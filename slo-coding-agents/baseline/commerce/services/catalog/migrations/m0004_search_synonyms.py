# synonyms used by the search index (services/search/index.py reads this
# table straight out of catalog.db)
# TODO(RB 2019-06-02): index on term once we know the query shape


def up(run):
    run("CREATE TABLE search_synonyms (id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT, expansion TEXT, source TEXT)")
