import pytest


def _search(client, q, uid=None):
    url = "/internal/search?q=%s" % q
    if uid:
        url += "&uid=%s" % uid
    return client.get(url)


def test_search_basic(search):
    r = _search(search, "apples")
    data = r.get_json()
    assert r.status_code == 200
    assert data["query"] == "apples"
    assert data["count"] == len(data["results"])
    assert data["count"] > 0
    assert any("Apples" in p["name"] for p in data["results"])


def test_result_shape(search):
    item = _search(search, "milk").get_json()["results"][0]
    for key in ("sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags"):
        assert key in item
    assert "in_stock" not in item


def test_search_empty_query(search):
    assert _search(search, "").get_json()["count"] == 0


def test_search_no_results(search):
    assert _search(search, "qwertyuiop").get_json()["results"] == []


def test_search_is_cached(search):
    from services.search import engine

    _search(search, "bread")
    n = engine.cache_size()
    _search(search, "bread")
    assert engine.cache_size() == n


def test_search_synonym(search):
    # "prawns" -> "shrimp" via the legacy synonym table
    data = _search(search, "shrimp").get_json()
    assert any("Prawns" in p["name"] for p in data["results"])


def test_live_price_failure_is_ignored(search, mesh):
    mesh.down.add("pricing")
    r = _search(search, "cheese")
    assert r.status_code == 200
    assert r.get_json()["count"] > 0


@pytest.mark.skip(reason="flaky on CI - JW 2021")
def test_search_ranking_stable(search):
    a = _search(search, "organic").get_json()["results"]
    b = _search(search, "organic", uid="u-0002").get_json()["results"]
    assert [p["sku"] for p in a][:5] == [p["sku"] for p in b][:5]
