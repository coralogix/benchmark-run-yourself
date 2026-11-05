"""
Read access to catalog's database from other services (search, pricing).

    from larder_common.dbx.catalog_db import catalog_db
    catalog_db().query("SELECT ... FROM products ...")

This is the "shared database" the 2023 split was supposed to get rid of.
TODO(TH 2023-03-02): replace with calls to catalog's /internal/products/bulk
once it exists.  (it exists now - nobody has done the switch. SB 2024-01)
"""
from larder_common.dbx import connection
from larder_common.dbx import paths

NAME = "catalog"


def catalog_db():
    # NB: registered under the name "catalog" with the shared-path resolver,
    # so in the catalog service itself this is the same file as its own db.
    d = connection._REGISTRY.get(NAME)
    if d is None:
        d = connection.register(NAME, paths.catalog_db_path)
    return d
