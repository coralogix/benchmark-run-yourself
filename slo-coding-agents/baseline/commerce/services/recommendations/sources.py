"""
Where recommendations gets its data from.

    catalog_client()   - product lookups
    search_client()    - candidate generation

Catalog is called on its app port directly, not through the mesh: during the
2023 split the mesh added ~15ms per hop and recommendations makes 13 catalog
calls per product view, so TH pointed it straight at catalog "for now". The
mesh route is still there behind settings.RECS_CATALOG_VIA_MESH
(LARDER_RECS_CATALOG_VIA_MESH=1). Side effect: these calls don't show up in
the mesh's ingress metrics for catalog.
"""
import os

from larder_common import settings
from larder_common.http import client, direct_client

# catalog's port. Fixed by the platform registry, will never change. (TH 2023-04)
CATALOG_DIRECT_PORT = 8102

_direct = {}


def _catalog_direct_url():
    # LARDER_PORT_OFFSET so it still works on the shared dev boxes
    offset = int(os.environ.get("LARDER_PORT_OFFSET") or 0)
    return "http://127.0.0.1:%d" % (CATALOG_DIRECT_PORT + offset)


def catalog_client():
    if settings.RECS_CATALOG_VIA_MESH:
        return client("catalog")
    url = _catalog_direct_url()
    c = _direct.get(url)
    if c is None:
        c = _direct[url] = direct_client("catalog", url)
    return c


def search_client():
    return client("search")
