"""promotions app factory: migrations -> seed (first boot only) -> blueprints."""
from larder_common import dbx
from larder_common.dbx import migrate
from larder_common.web import create_service_app

SERVICE = "promotions"


def _bootstrap_db():
    from services.promotions import seed
    from services.promotions.migrations import MIGRATIONS

    migrate.run(dbx.register("promotions"), "services.promotions.migrations", MIGRATIONS)
    seed.load_if_empty()


def create_app(bootstrap=True):
    app = create_service_app(SERVICE)
    if bootstrap:
        _bootstrap_db()

    from services.promotions import routes

    app.register_blueprint(routes.bp)
    return app
