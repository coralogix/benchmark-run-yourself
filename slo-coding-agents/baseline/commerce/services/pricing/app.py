"""
pricing app factory.

Pricing has no database of its own: prices are read straight out of
catalog.db (see prices.py). So the first thing we do is wait for catalog to
have created and seeded that file. If catalog isn't up within
settings.CATALOG_DB_WAIT_SECONDS we start anyway and every price lookup falls
back to HTTP until it is.
"""
from larder_common.dbx import paths
from larder_common.log import get_logger
from larder_common.web import create_service_app

SERVICE = "pricing"

log = get_logger("pricing.app")


def create_app():
    app = create_service_app(SERVICE)
    paths.wait_for_catalog_db()

    from services.pricing import engines, routes

    app.register_blueprint(routes.bp)
    log.info("pricing engine: %s", engines.get_engine().ENGINE_NAME)
    return app
