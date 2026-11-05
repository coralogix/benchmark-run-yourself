"""
Marketing preferences (KP 2018, GDPR).

The CRM that consumed these was switched off in 2021 (legacy/crm.py). The
account page used to show them; it stopped in 2023 when the page moved to
edge-api. get_preferences() is still called by summary.py, and its result is
thrown away. TODO(TH 2023-06-01): remove the call, it's a query per page view.
"""
from services.accounts.models import Preferences

DEFAULTS = {"email_opt_in": False, "sms_opt_in": False}


def get_preferences(user_id):
    p = Preferences.get(user_id)
    if p is None:
        return dict(DEFAULTS)
    return {"email_opt_in": bool(p.email_opt_in), "sms_opt_in": bool(p.sms_opt_in)}


def set_preferences(user_id, email_opt_in=None, sms_opt_in=None):  # pragma: no cover - unused
    p = Preferences.get(user_id) or Preferences(user_id=user_id)
    if email_opt_in is not None:
        p.email_opt_in = bool(email_opt_in)
    if sms_opt_in is not None:
        p.sms_opt_in = bool(sms_opt_in)
    p.save()
    return get_preferences(user_id)
