"""
Catalog models (see larder_common/dbx/model.py for the ORM).

Relationship helpers are plain methods and each one is a query. Callers in
loops beware.
"""
import datetime

from larder_common.dbx.model import Field, Model


def _now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class Product(Model):
    __table__ = "products"
    __database__ = "catalog"
    id = Field(int, pk=True)
    sku = Field(str)
    name = Field(str)
    category = Field(str)
    price = Field(float)  # pounds!
    vat_band = Field(str, default="standard")
    description = Field(str)
    active = Field(int, default=1)
    created_at = Field(str, default=_now)

    def tags(self):
        return [r["tag"] for r in self._db().query("SELECT tag FROM product_tags WHERE product_id = ?", (self.id,))]

    def diet(self):
        return [r["diet"] for r in self._db().query("SELECT diet FROM product_diet WHERE product_id = ?", (self.id,))]

    @classmethod
    def by_sku(cls, sku):
        return cls.get_by(sku=sku)


class SearchBoost(Model):
    __table__ = "search_boosts"
    __database__ = "catalog"
    sku = Field(str, pk=True)
    boost = Field(float, default=1.0)
    reason = Field(str)


class SalesRank(Model):
    __table__ = "sales_rank"
    __database__ = "catalog"
    sku = Field(str, pk=True)
    orders = Field(int, default=0)
    updated_at = Field(str, default=_now)
