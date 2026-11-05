# marketing preferences (GDPR, 2018 - KP). Moved here from the storefront in
# 2023. The CRM that read these was switched off in 2021.


def up(run):
    run("CREATE TABLE preferences (user_id TEXT PRIMARY KEY, email_opt_in INTEGER DEFAULT 0, sms_opt_in INTEGER DEFAULT 0, updated_at TEXT)")
