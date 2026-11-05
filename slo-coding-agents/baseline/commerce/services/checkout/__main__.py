"""python -m services.checkout"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.checkout.app import create_app

# waitress worker threads. Kept at the storefront's old per-node figure when
# checkout was split out (TH 2023-03): "checkout is low volume, and fewer
# threads means fewer concurrent payments if something goes wrong".
# Every request holds a thread for the whole orders/PSP round trip.
THREADS = 4

if __name__ == "__main__":
    run(create_app, "checkout", threads=THREADS)
