"""
Delete local sqlite files so every service re-seeds on next start.

    uv run python -m scripts.reset_data            # var/*/*.db
    uv run python -m scripts.reset_data cart       # just var/cart

NB: cart keeps carts in memory too; restart it after this or it will happily
serve carts whose rows no longer exist (and write them back).
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    pattern = os.path.join(ROOT, "var", "%s", "*.db*")
    dirs = argv or ["*"]
    n = 0
    for d in dirs:
        for path in glob.glob(pattern % d):
            os.remove(path)
            print("removed", os.path.relpath(path, ROOT))
            n += 1
    print("%d files removed" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
