"""loyalty app factory."""
from larder_common import dbx
from larder_common.dbx import migrate
from larder_common.web import create_service_app

SERVICE = "loyalty"


def create_app():
    app = create_service_app(SERVICE)
    from services.loyalty.migrations import MIGRATIONS

    migrate.run(dbx.register("loyalty"), "services.loyalty.migrations", MIGRATIONS)

    from services.loyalty import routes

    app.register_blueprint(routes.bp)
    return app
