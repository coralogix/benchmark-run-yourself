"""python -m services.edge_api"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.edge_api.app import create_app

# waitress worker threads. DP 2023-03: edge is pure IO (fan-out), so it gets
# the most threads of anything. If you raise this, raise CONNECTION_LIMIT too.
THREADS = 8
CONNECTION_LIMIT = 400

if __name__ == "__main__":
    run(create_app, "edge-api", threads=THREADS, connection_limit=CONNECTION_LIMIT)
