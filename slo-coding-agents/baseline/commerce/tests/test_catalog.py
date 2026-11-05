def test_healthz(catalog):
    r = catalog.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "service": "catalog"}


def test_products_first_page(catalog):
    data = catalog.get("/internal/products").get_json()
    assert data["page"] == 1
    assert data["per_page"] == 20
    assert len(data["items"]) == 20
    assert data["total"] >= 200


def test_product_shape(catalog):
    item = catalog.get("/internal/products?per_page=1").get_json()["items"][0]
    for key in ("sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags"):
        assert key in item
    assert isinstance(item["price_pence"], int)


def test_price_pence_matches_price(catalog):
    for item in catalog.get("/internal/products?per_page=50").get_json()["items"]:
        assert item["price_pence"] == round(item["price"] * 100)


def test_category_filter(catalog):
    data = catalog.get("/internal/products?category=dairy&per_page=100").get_json()
    assert data["items"]
    assert all(i["category"] == "dairy" for i in data["items"])


def test_detail_and_404(catalog):
    data = catalog.get("/internal/products/SKU-0050").get_json()
    assert data["sku"] == "SKU-0050"
    assert data["price_pence"] == 249
    assert "description" in data
    assert catalog.get("/internal/products/SKU-9999").status_code == 404


def test_bulk_preserves_order(catalog):
    r = catalog.post("/internal/products/bulk", json={"skus": ["SKU-0003", "SKU-0050", "NOPE"]})
    assert [i["sku"] for i in r.get_json()["items"]] == ["SKU-0003", "SKU-0050"]


def test_categories(catalog):
    cats = catalog.get("/internal/categories").get_json()["categories"]
    assert "dairy" in cats and "produce" in cats
