"""
catalog app factory.

Startup: migrations -> seed (first boot only) -> blueprints. search and
pricing poll for catalog.db to have products in it before they start serving,
so if catalog is slow to boot, so are they.
"""
from larder_common.dbx import migrate
from larder_common.dbx.catalog_db import catalog_db
from larder_common.web import create_service_app

SERVICE = "catalog"


def _bootstrap_db():
    from services.catalog import seed
    from services.catalog.migrations import MIGRATIONS

    migrate.run(catalog_db(), "services.catalog.migrations", MIGRATIONS)
    seed.load_if_empty()


def create_app(bootstrap=True):
    app = create_service_app(SERVICE)
    if bootstrap:
        _bootstrap_db()

    from services.catalog import admin, routes

    app.register_blueprint(routes.bp)
    app.register_blueprint(admin.bp)
    return app
