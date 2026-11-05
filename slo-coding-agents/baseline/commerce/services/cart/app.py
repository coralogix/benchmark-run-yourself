"""cart app factory: migrations -> warm cache (PM-1307) -> gauges -> blueprints."""
from larder_common import dbx, telemetry
from larder_common.dbx import migrate
from larder_common.web import create_service_app

SERVICE = "cart"


def create_app():
    app = create_service_app(SERVICE)
    from services.cart.migrations import MIGRATIONS

    migrate.run(dbx.register("cart"), "services.cart.migrations", MIGRATIONS)

    from services.cart import routes, state

    state.warm()
    # "active" carts: see state.active_count()
    telemetry.register_gauge("active_carts", state.active_count)
    app.register_blueprint(routes.bp)
    return app
