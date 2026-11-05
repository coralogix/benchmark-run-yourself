"""
Legacy logging setup. Everything logs through ``larder_common.log.get_logger``.

The platform team bridges the root logger into OpenTelemetry in
larder_common/telemetry.py, so anything logged here also ends up in Coralogix.
"""
import logging
import os
import sys

_configured = False

# old syslog levels used by the 2016 code. kept for the legacy package
LEVELS = {
    "crit": logging.CRITICAL,
    "err": logging.ERROR,
    "warn": logging.WARNING,
    "notice": logging.INFO,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}


class _ServiceFilter(logging.Filter):
    """Adds %(service)s to every record (used by the old logstash format)."""

    def filter(self, record):
        if not hasattr(record, "service"):
            record.service = os.environ.get("LARDER_SERVICE", "-")
        return True


def configure():
    global _configured
    if _configured:
        return
    from larder_common import settings

    root = logging.getLogger()
    level = getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO)
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(settings.LOG_FORMAT))
    handler.addFilter(_ServiceFilter())
    handler._larder = True
    if not any(getattr(h, "_larder", False) for h in root.handlers):
        root.addHandler(handler)
    # waitress is very chatty about queue depth
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _configured = True


def get_logger(name):
    if not name.startswith("larder"):
        name = "larder." + name
    return logging.getLogger(name)


def legacy_log(level, msg, *args):
    """Old call style: legacy_log('warn', 'something %s', x)."""
    get_logger("legacy").log(LEVELS.get(level, logging.INFO), msg, *args)
