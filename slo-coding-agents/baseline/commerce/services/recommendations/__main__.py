"""python -m services.recommendations"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.recommendations.app import create_app

# waitress worker threads. TH 2023-04: same as search
THREADS = 6

if __name__ == "__main__":
    run(create_app, "recommendations", threads=THREADS)
