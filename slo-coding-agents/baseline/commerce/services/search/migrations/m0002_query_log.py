# AS 2023-06: log zero-result queries for merch. Nobody reads the table.


def up(run):
    run("CREATE TABLE query_log (id INTEGER PRIMARY KEY AUTOINCREMENT, q TEXT, uid TEXT, results INTEGER, at TEXT)")
