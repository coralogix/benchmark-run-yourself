"""python -m services.cart"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.cart.app import create_app

# waitress worker threads (TH 2023-03)
THREADS = 6

if __name__ == "__main__":
    run(create_app, "cart", threads=THREADS)
