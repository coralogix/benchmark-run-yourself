"""
checkout app factory.

Startup order: migrations -> post-checkout hooks (settings.CHECKOUT_HOOKS) ->
blueprints.
"""
from larder_common import dbx
from larder_common.dbx import migrate
from larder_common.web import create_service_app

SERVICE = "checkout"


def _bootstrap_db():
    from services.checkout.migrations import MIGRATIONS

    migrate.run(dbx.register("checkout"), "services.checkout.migrations", MIGRATIONS)


def create_app(bootstrap=True):
    app = create_service_app(SERVICE)
    if bootstrap:
        _bootstrap_db()

    from services.checkout import hooks, routes

    hooks.load_default_hooks()
    app.register_blueprint(routes.bp)
    return app
