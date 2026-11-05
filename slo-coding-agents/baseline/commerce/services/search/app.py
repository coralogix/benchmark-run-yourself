"""
search app factory.

NB: blocks until catalog.db exists and has products (search builds its index
from catalog's database file, not from catalog's API). If catalog isn't up
within settings.CATALOG_DB_WAIT_SECONDS we start anyway and every search 500s
until the index can be built.
"""
from larder_common import dbx, telemetry
from larder_common.dbx import migrate, paths
from larder_common.web import create_service_app

SERVICE = "search"


def create_app():
    app = create_service_app(SERVICE)
    paths.wait_for_catalog_db()

    from services.search.migrations import MIGRATIONS

    migrate.run(dbx.register("search"), "services.search.migrations", MIGRATIONS)

    from services.search import admin, engine, routes

    telemetry.register_gauge("search_cache_entries", engine.cache_size)
    telemetry.touch_search_flushes()

    app.register_blueprint(routes.bp)
    app.register_blueprint(admin.bp)
    return app
