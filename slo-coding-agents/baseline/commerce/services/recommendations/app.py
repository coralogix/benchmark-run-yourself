"""recommendations app factory. No database, no cache (yet - see engine.py)."""
from larder_common.web import create_service_app

SERVICE = "recommendations"


def create_app():
    app = create_service_app(SERVICE)

    from services.recommendations import explain, routes

    app.register_blueprint(routes.bp)
    app.register_blueprint(explain.bp)
    return app
