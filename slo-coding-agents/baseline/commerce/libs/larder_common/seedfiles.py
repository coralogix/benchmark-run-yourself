"""Seed data loading helpers (seed/*.json at the repo root)."""
import json
import os

from larder_common.log import get_logger

log = get_logger("seed")


def seed_path(name):
    from larder_common import settings

    return os.path.join(settings.SEED_DIR, name)


def load_json(name):
    path = seed_path(name)
    if not os.path.exists(path):
        log.warning("seed file missing: %s", path)
        return []
    with open(path) as fh:
        return json.load(fh)
