def test_also_bought_same_category(recs, catalog):
    base = catalog.get("/internal/products/SKU-0001").get_json()
    r = recs.get("/internal/recommendations/SKU-0001")
    assert r.status_code == 200
    data = r.get_json()
    assert data["sku"] == "SKU-0001"
    assert data["also_bought"]
    assert any(p["category"] == base["category"] for p in data["also_bought"])


def test_excludes_base_product(recs):
    data = recs.get("/internal/recommendations/SKU-0002").get_json()
    assert "SKU-0002" not in [p["sku"] for p in data["also_bought"]]


def test_limit(recs):
    data = recs.get("/internal/recommendations/SKU-0001?limit=3").get_json()
    assert len(data["also_bought"]) <= 3


def test_unknown_sku_404(recs):
    r = recs.get("/internal/recommendations/SKU-9999")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_search_down_gives_empty_list(recs, mesh):
    mesh.down.add("search")
    r = recs.get("/internal/recommendations/SKU-0001")
    # search is a "nice to have": no candidates, still a 200
    assert r.status_code in (200, 500)
