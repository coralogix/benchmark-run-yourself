"""
larder_common - code shared by every commerce service.

Started life as the storefront monolith's ``larder`` package (2016); when the
storefront was split into services (2023, "Project Pantry") everything that
more than one service needed was moved here more or less unchanged.

Importing this package installs a few process-wide patches that the rest of
the code relies on. Do not reorder, and do not import flask/requests before
this module in a new entry point:

    _install_json_patch()     Decimal/date/set support for every json.dumps()
    _install_socket_patch()   resets the global socket timeout (see comment)
    _install_requests_patch() default headers on every requests.Session

"""
import datetime
import decimal
import json
import os
import socket

__version__ = "4.2.0"

# set by web.create_service_app(), handy for scripts that need the app
APP = None
# set by web.create_service_app() too. NB: last app created wins, so in the
# test suite (several apps per process) this is whatever was built last.
SERVICE = os.environ.get("LARDER_SERVICE", "unknown")


def _install_json_patch():
    # 2017-05 (MK): Decimal prices from the old MySQL days blow up json.dumps.
    # Patch the stdlib default encoder once so every json.dumps() in the
    # codebase (including Flask's jsonify) handles Decimal / dates / sets.
    _orig_default = json.JSONEncoder.default

    def _larder_default(self, o):
        if isinstance(o, decimal.Decimal):
            # float is fine here, prices are always 2dp
            return float(o)
        if isinstance(o, (datetime.datetime, datetime.date)):
            return o.isoformat()
        if isinstance(o, (set, frozenset)):
            return sorted(o)
        if hasattr(o, "to_dict"):
            return o.to_dict()
        return _orig_default(self, o)

    if getattr(json.JSONEncoder.default, "_larder_patched", False):
        return
    _larder_default._larder_patched = True
    json.JSONEncoder.default = _larder_default


def _install_socket_patch():
    # JW 2019-06-12: somebody's library set socket.setdefaulttimeout(30) at
    # import time and every slow PSP call started failing with "timed out"
    # (INC-402). We always want "wait as long as it takes" for our own calls,
    # so force it back to None for the whole process.
    socket.setdefaulttimeout(None)


def _install_requests_patch():
    # every outbound call identifies the calling service. The mesh logs this.
    try:
        import requests
    except ImportError:  # pragma: no cover
        return
    if getattr(requests.Session, "_larder_patched", False):
        return
    _orig_init = requests.Session.__init__

    def _init(self, *a, **kw):
        _orig_init(self, *a, **kw)
        self.headers["User-Agent"] = "larder-%s/%s" % (os.environ.get("LARDER_SERVICE", "commerce"), __version__)
        self.headers["X-Larder-Client"] = os.environ.get("LARDER_SERVICE", "commerce")

    requests.Session.__init__ = _init
    requests.Session._larder_patched = True


_install_json_patch()
_install_socket_patch()
_install_requests_patch()
