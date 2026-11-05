"""
Larder commerce services. One package per service, run with

    python -m services.<module>

from the repo root (the supervisor does this with cwd = repo root).

NB: importing this package puts ``libs/`` on sys.path so that
``import larder_common`` works without installing anything. The tests rely on
pytest's ``pythonpath`` setting for the same thing. (TH 2023-02: "temporary
until we publish larder_common to the internal index")
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_LIBS = os.path.join(_ROOT, "libs")

for _p in (_LIBS, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# importing larder_common installs the process-wide patches (json, sockets).
# It has to happen before flask/requests are used anywhere.
import larder_common  # noqa: E402,F401
