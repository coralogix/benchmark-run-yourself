# "Remember my card" (SB 2021-09, PAY-77). The PSP never enabled token vaulting
# for us, so nothing is ever written here. See tokens.py.


def up(run):
    run("CREATE TABLE saved_tokens (user_id TEXT, token TEXT, brand TEXT, last4 TEXT, created_at TEXT)")
