"""python -m services.promotions"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.promotions.app import create_app

# waitress worker threads. Coupons are applied a few times a minute (SB 2023)
THREADS = 4

if __name__ == "__main__":
    run(create_app, "promotions", threads=THREADS)
