"""
The 2018 environment loader (before settings.py learned about the ini file).

Still imported by legacy/stock_sync.py? No - it was, until 2021. Nothing
imports it now. It reads STOREFRONT_* variables, which is why you'll still see
STOREFRONT_ENV in some old runbooks; setting it does nothing.
"""
import os

PREFIX = "STOREFRONT_"

DEFAULTS = {
    "ENV": "production",
    "PORT": "5000",
    "DB_URL": "mysql://larder:larder@larder-db-01/larder",
    "REDIS_URL": "redis://larder-redis-01:6379/3",
    "FULFILMENT_URL": "http://fulfilment.larder.internal:8081",
    "SEARCH_CACHE_TTL": "600",
}


def get(name, default=None):
    return os.environ.get(PREFIX + name, DEFAULTS.get(name, default))


def as_dict():
    return {k: get(k) for k in DEFAULTS}
