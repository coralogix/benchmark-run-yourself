"""python -m services.pricing"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.pricing.app import create_app

# waitress worker threads. Search calls us once per result, so a few more
# than the default (AS 2023-06)
THREADS = 6

if __name__ == "__main__":
    run(create_app, "pricing", threads=THREADS)
