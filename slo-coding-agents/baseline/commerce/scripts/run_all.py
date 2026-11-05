"""
Run every commerce service locally, for development (make run-all).

    uv run python -m scripts.run_all            # all ten
    uv run python -m scripts.run_all catalog search

There is no mesh locally, so this points every <NAME>_URL at the peer's APP
port (8100 + i) instead of the mesh port (8200 + i). edge-api listens on its
app port (8101), not 8000. Ops services are NOT started; point ORDERS_URL /
INVENTORY_URL / SLOTS_URL at something yourself (they default to the mesh
ports, which won't be there).

Start order: catalog first, because search and pricing wait for catalog.db.
(they'd wait anyway, but the logs are less scary this way)

Ctrl-C stops everything. (TH 2023-03, replaced the old Procfile + honcho)
"""
import os
import signal
import subprocess
import sys
import time

import services  # noqa: F401
from larder_common import discovery

ORDER = ["catalog", "promotions", "pricing", "search", "cart", "accounts", "loyalty", "checkout",
         "recommendations", "edge-api"]

# seconds to give catalog before starting the rest
CATALOG_HEAD_START = 2.0


def module_for(name):
    return "services." + name.replace("-", "_")


def env_for(name, root):
    env = dict(os.environ)
    env["LARDER_SERVICE"] = name
    env["PORT"] = str(discovery.app_port(name))
    env["LARDER_DATA_DIR"] = os.path.join(root, "var", name)
    env.setdefault("LARDER_ENV", "dev")
    for peer in discovery.COMMERCE:
        env.setdefault(discovery.env_var_for(peer), "http://127.0.0.1:%d" % discovery.app_port(peer))
    return env


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    names = [n for n in ORDER if not argv or n in argv]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    procs = []
    for name in names:
        env = env_for(name, root)
        os.makedirs(env["LARDER_DATA_DIR"], exist_ok=True)
        p = subprocess.Popen([sys.executable, "-m", module_for(name)], cwd=root, env=env)
        procs.append((name, p))
        print("started %-16s pid=%-6d port=%s" % (name, p.pid, env["PORT"]))
        if name == "catalog":
            time.sleep(CATALOG_HEAD_START)

    def _stop(*_):
        for name, p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for name, p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while True:
        for name, p in procs:
            if p.poll() is not None:
                print("%s exited with %s" % (name, p.returncode))
                procs.remove((name, p))
                break
        if not procs:
            return 1
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
