"""
dbx - the tiny persistence layer.

Written in 2016 for the storefront when we moved off MySQL ("we don't need an
ORM for 6 tables"). It's grown since, and in the 2023 split it learned about
more than one database file. Pieces:

    connection.py  - per-thread sqlite3 connections, one per database name
    paths.py       - where each database file lives (incl. the catalog.db trick)
    model.py       - Model/Field mini ORM
    migrate.py     - schema migration runner (each service has a migrations/ package)

Usage:

    from larder_common import dbx
    rows = dbx.db("cart").query("SELECT * FROM carts WHERE user_id = ?", (uid,))
"""
from larder_common.dbx.connection import Database, close_all, db, register  # noqa: F401
