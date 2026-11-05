"""
Config file plumbing for larder_common.settings.

Reads ``config/defaults.ini`` from the repo root (two levels up from libs/).
Nothing else should import configparser; use ``larder_common.settings``.

If you are looking for "where does setting X come from", the answer is, in
increasing order of precedence:

    1. config/defaults.ini            (this module)
    2. environment variables          (settings.py, *some* settings only)
    3. constants in settings.py       (a few are re-assigned after the env read)
    4. settings_local.py              (applied last, skipped when LARDER_ENV=test)

README.md says "environment always wins". It doesn't, see 3 and 4.
"""
import configparser
import os

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(LIB_DIR))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")
CONFIG_FILE = os.environ.get("LARDER_CONFIG_FILE") or os.path.join(CONFIG_DIR, "defaults.ini")

_ini = configparser.ConfigParser(inline_comment_prefixes=(";",))
_ini_loaded = _ini.read(CONFIG_FILE)


def ini_get(key, fallback=None, section="larder"):
    try:
        val = _ini.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback
    if val == "":
        # empty in the ini means "use the fallback", not "empty string"
        return fallback
    return val


def ini_sections():
    return list(_ini.sections())


def ini_dump(section):
    try:
        return dict(_ini.items(section))
    except configparser.NoSectionError:
        return {}


def env(name, fallback=None):
    """LARDER_<NAME> from the environment. Empty string counts as unset."""
    v = os.environ.get("LARDER_" + name)
    if v is None or v == "":
        return fallback
    return v


def to_bool(v):
    # FIXME: duplicated in utils.to_bool and helpers.strings.truthy (TH 2022-01-19)
    # and they disagree about "t" and "on". Nobody has dared to merge them.
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def to_float_or_none(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def loaded_files():
    return list(_ini_loaded)
