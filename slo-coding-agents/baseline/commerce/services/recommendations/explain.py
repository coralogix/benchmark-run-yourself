"""
Debug view for recommendations (TH 2023-04): why did we recommend X?

    GET /internal/recommendations/<sku>/_explain

Runs the same pipeline as engine.also_bought and returns the intermediate
steps. NB: it makes all the same calls (1 + 1 + 12) and then some, because
it also fetches the products that didn't make the cut "for comparison".
"""
from flask import Blueprint

from larder_common import settings
from larder_common.errors import NotFound
from larder_common.helpers.strings import category_terms
from larder_common.web import ok
from services.recommendations import engine, scoring
from services.recommendations.sources import catalog_client, search_client

bp = Blueprint("recommendations_explain", __name__)


@bp.route("/internal/recommendations/<sku>/_explain")
def explain(sku):
    base = engine._product(sku)
    if base is None:
        raise NotFound("not_found")
    q = category_terms(base.get("category"))
    res = search_client().get("/internal/search", params={"q": q})
    results = res.body.get("results", []) if res.ok else []
    candidates = [r["sku"] for r in results if r.get("sku") != sku]
    scored = []
    for cand in candidates[: settings.RECS_CANDIDATES * 2]:
        p = engine._product(cand)
        if p is None:
            continue
        scored.append({"sku": cand, "score": scoring._score(base, p), "kept": cand in candidates[: settings.RECS_CANDIDATES]})
    return ok(
        {
            "sku": sku,
            "query": q,
            "search_status": res.status,
            "search_results": len(results),
            "catalog_base_url": catalog_client().base_url,
            "candidates": scored,
            "limit": settings.RECS_LIMIT,
        }
    )
