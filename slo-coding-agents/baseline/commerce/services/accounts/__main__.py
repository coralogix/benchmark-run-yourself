"""python -m services.accounts"""
import services  # noqa: F401  (sys.path + larder_common patches; must be first)
from larder_common.serve import run
from services.accounts.app import create_app

# waitress worker threads. TH 2023-03: the account page is "a couple of
# lookups", 6 threads is generous.
THREADS = 6

if __name__ == "__main__":
    run(create_app, "accounts", threads=THREADS)
