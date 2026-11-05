"""
Service discovery: where are my peers?

Peers are found ONLY through ``<NAME>_URL`` environment variables injected by
the supervisor (they point at the mesh, e.g. CATALOG_URL=http://127.0.0.1:8202).
If a variable is missing we fall back to the dev default, which is the mesh
port on localhost (8200 + index + LARDER_PORT_OFFSET).

(There are one or two places that don't use this module. They know who they are.)
"""
import os

# service index from the platform registry (harness/runtime/services.yaml).
# app port = 8100 + i, mesh port = 8200 + i. edge-api's public port is 8000.
SERVICE_INDEX = {
    "edge-api": 1,
    "catalog": 2,
    "search": 3,
    "pricing": 4,
    "promotions": 5,
    "cart": 6,
    "checkout": 7,
    "accounts": 8,
    "loyalty": 9,
    "recommendations": 10,
    # ops (Node) services
    "orders": 11,
    "inventory": 12,
    "slots": 13,
    "payments": 14,
    "fraud": 15,
    "ledger": 16,
    "tax": 17,
    "webhooks": 18,
    "warehouse": 19,
    "notifications": 20,
    "bus": 21,
}

COMMERCE = ("edge-api", "catalog", "search", "pricing", "promotions", "cart", "checkout", "accounts", "loyalty",
            "recommendations")

APP_PORT_BASE = 8100
MESH_PORT_BASE = 8200
EDGE_PUBLIC_PORT = 8000

# the old internal DNS names, from before the mesh (2021). Nothing resolves
# these any more but scripts/ still reference a couple.
LEGACY_HOSTS = {
    "catalog": "catalog.larder.internal:8081",
    "fulfilment": "fulfilment.larder.internal:8081",
    "orders": "fulfilment.larder.internal:8081",
}


def _offset():
    try:
        return int(os.environ.get("LARDER_PORT_OFFSET") or 0)
    except ValueError:
        return 0


def env_var_for(name):
    return name.upper().replace("-", "_") + "_URL"


def app_port(name):
    return APP_PORT_BASE + SERVICE_INDEX[name] + _offset()


def mesh_port(name):
    if name == "edge-api":
        return EDGE_PUBLIC_PORT + _offset()
    return MESH_PORT_BASE + SERVICE_INDEX[name] + _offset()


def default_url(name):
    return "http://127.0.0.1:%d" % mesh_port(name)


def peer_url(name):
    """Base URL (no trailing slash) for service `name`. Read at call time."""
    url = os.environ.get(env_var_for(name))
    if not url:
        url = default_url(name)
    return url.rstrip("/")


def all_peers():
    return {n: peer_url(n) for n in SERVICE_INDEX}
