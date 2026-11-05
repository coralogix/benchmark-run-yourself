# delivery slot holds (2019). slot_id is informational only.


def up(run):
    run("ALTER TABLE carts ADD COLUMN hold_id TEXT")
    run("ALTER TABLE carts ADD COLUMN slot_id TEXT")
    run("ALTER TABLE carts ADD COLUMN hold_expires_at TEXT")
