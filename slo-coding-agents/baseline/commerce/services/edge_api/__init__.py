"""
edge-api - the customer-facing BFF. Every public /api/* route lives here and
fans out to the commerce services and to ops (inventory, slots, orders).

Also mounts the 2016 /v1 API (legacy/v1_routes.py).
"""
