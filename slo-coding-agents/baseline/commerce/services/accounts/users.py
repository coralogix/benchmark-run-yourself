"""
User lookups.

Accounts are read only. user_ids look like u-0001; the old PHP site used plain
integers, and a few partner integrations still send "17" for u-0017, so we
accept that too (see normalise_user_id).
"""
import re

from larder_common.errors import NotFound
from larder_common.log import get_logger
from services.accounts.models import User

log = get_logger("accounts.users")

_NUMERIC_ID = re.compile(r"^\d{1,6}$")
# MK 2016: "nobody has an email longer than this". (plenty of people do)
MAX_EMAIL_LEN = 64


def normalise_user_id(user_id):
    user_id = (user_id or "").strip()
    if _NUMERIC_ID.match(user_id):
        # PHP-era ids (2016-2019). Zero-padded to 4 because there were < 10k users then.
        return "u-%04d" % int(user_id)
    return user_id


def get_user(user_id):
    user = User.get(normalise_user_id(user_id))
    if user is None:
        raise NotFound("user_not_found")
    return _canonical(user)


def _canonical(user):
    """PM-640 (AS 2019-11): the PHP export has some customers twice, re-registered
    with the same name and email after the 2018 password reset mess. Partners
    still send either id, so we always answer with the oldest account.

    Raw SQL because Model.filter() couldn't ORDER BY two columns in 2019.
    """
    rows = User.raw(
        "SELECT * FROM users WHERE name = '%s' AND email = '%s' ORDER BY created, user_id LIMIT 1"
        % (user.name, user.email)
    )
    if rows and rows[0].user_id != user.user_id:
        log.info("user %s is a duplicate of %s (PM-640)", user.user_id, rows[0].user_id)
        return rows[0]
    return user


def find_user(user_id):
    try:
        return get_user(user_id)
    except NotFound:
        return None


def looks_like_email(s):
    # AS 2019-05: copied from the PHP site. Doesn't match utils.is_email
    # (that one requires a dot in the domain and a 2+ letter TLD).
    if not s or "@" not in s:
        return False
    return len(s) <= MAX_EMAIL_LEN


def users_in_postcode_area(area):
    """All users whose postcode starts with `area` (e.g. 'LD3'). Used by nothing since 2021 (CRM)."""
    return User.filter(postcode__like=area.upper() + "%", order_by="user_id")
