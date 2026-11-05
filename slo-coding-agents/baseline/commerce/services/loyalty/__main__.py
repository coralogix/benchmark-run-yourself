"""python -m services.loyalty"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.loyalty.app import create_app

# waitress worker threads. Loyalty does nothing yet, 4 is plenty (SB 2022)
THREADS = 4

if __name__ == "__main__":
    run(create_app, "loyalty", threads=THREADS)
