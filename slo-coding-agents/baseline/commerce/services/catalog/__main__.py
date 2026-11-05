"""python -m services.catalog"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.catalog.app import create_app

# waitress worker threads. RB 2023-03: catalog is "just sqlite reads", 4 is
# loads. (Every product page also costs 13 catalog calls via recommendations.)
THREADS = 4

if __name__ == "__main__":
    run(create_app, "catalog", threads=THREADS)
