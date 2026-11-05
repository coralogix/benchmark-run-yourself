"""python -m services.search"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.search.app import create_app

# waitress worker threads. DP 2019: 6 is plenty, search is mostly cache hits
THREADS = 6


def _warm(app):
    # build the search index before taking traffic (first search is slow otherwise)
    from services.search import index

    index.ensure_index()


if __name__ == "__main__":
    run(create_app, "search", threads=THREADS, before_serve=_warm)
