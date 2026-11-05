"""accounts app factory: migrations -> seed (first boot) -> blueprints."""
from larder_common import dbx
from larder_common.dbx import migrate
from larder_common.web import create_service_app

SERVICE = "accounts"


def _bootstrap_db():
    from services.accounts import seed
    from services.accounts.migrations import MIGRATIONS

    migrate.run(dbx.register("accounts"), "services.accounts.migrations", MIGRATIONS)
    seed.load_if_empty()


def create_app(bootstrap=True):
    app = create_service_app(SERVICE)
    if bootstrap:
        _bootstrap_db()

    from services.accounts import routes

    app.register_blueprint(routes.bp)
    return app
