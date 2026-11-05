"""Public /api blueprints. Registered in services/edge_api/app.py."""


def all_blueprints():
    # function-level imports: views import upstreams which import
    # larder_common.http which imports telemetry... (circular at module level
    # back when this was larder/views, TH kept the pattern)
    from services.edge_api.views.account import bp as account_bp
    from services.edge_api.views.cart import bp as cart_bp
    from services.edge_api.views.catalogue import bp as catalogue_bp
    from services.edge_api.views.checkout import bp as checkout_bp
    from services.edge_api.views.delivery import bp as delivery_bp
    from services.edge_api.views.internal import bp as internal_bp

    return [catalogue_bp, cart_bp, delivery_bp, checkout_bp, account_bp, internal_bp]
