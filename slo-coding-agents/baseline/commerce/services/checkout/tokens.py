"""
Payment token handling.

The customer's browser tokenises the card with the PSP's JS and we get a
`payment_token` like "tok_visa". We pass it straight to ops (payments owns
the PSP). These helpers are from PAY-77 ("remember my card", SB 2021-09),
which never went live; only looks_valid() is used.
"""
import re

from larder_common import dbx

# PSP test tokens + live ones. Live tokens are 24+ chars. (SB)
_TOKEN = re.compile(r"^tok_[A-Za-z0-9_]{2,64}$")

TEST_TOKENS = ("tok_visa", "tok_mastercard", "tok_amex", "tok_decline", "tok_slow")


def looks_valid(token):
    # NB: only logged, never enforced. The mobile app sent "pm_" tokens for a
    # while in 2022 and we didn't want to block those.
    return bool(token and _TOKEN.match(str(token)))


def is_test_token(token):
    return token in TEST_TOKENS


def saved_tokens(user_id):  # pragma: no cover - PAY-77, unused
    rows = dbx.db("checkout").query("SELECT token, brand, last4 FROM saved_tokens WHERE user_id = ?", (user_id,))
    return [dict(r) for r in rows]


def mask(token):
    if not token:
        return token
    return token[:4] + "..." + token[-2:] if len(token) > 8 else "tok_***"
