"""
edge-api app factory.

Blueprint order matters: /api first, then /v1 ("registered last so /api wins
on conflicts" - there are no conflicts, the prefixes differ, but don't move it).
"""
from larder_common import featureflags
from larder_common.log import get_logger
from larder_common.web import create_service_app

log = get_logger("edge")

SERVICE = "edge-api"


def create_app():
    app = create_service_app(SERVICE)

    from services.edge_api import headers

    headers.install(app)

    from services.edge_api.views import all_blueprints

    for bp in all_blueprints():
        app.register_blueprint(bp)

    if featureflags.enabled("edge_v1_routes"):
        # legacy /v1 API (old mobile app + partners)
        from legacy.v1_routes import bp as v1_bp

        app.register_blueprint(v1_bp, url_prefix="/v1")

    from services.edge_api import upstreams

    log.info("edge-api upstreams: %s", upstreams.describe())
    return app
