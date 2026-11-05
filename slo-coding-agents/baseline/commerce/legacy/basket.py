"""
Pre-2018 basket model. Replaced by carts (now the cart service).

The `baskets` table was dropped in the sqlite move, so load_basket() can't
work any more. /v1/basket/<id> still routes here.
"""
from larder_common.dbx.catalog_db import catalog_db
from larder_common.money import money_fmt


class Basket(object):
    def __init__(self, basket_id, user_id, lines, voucher=None):
        self.basket_id = basket_id
        self.user_id = user_id
        self.lines = lines
        self.voucher = voucher

    def subtotal(self):
        return sum(l["price"] * l["qty"] for l in self.lines)

    def to_v1(self):
        return {
            "id": self.basket_id,
            "user": self.user_id,
            "lines": self.lines,
            "subtotal": self.subtotal(),
            "subtotal_display": money_fmt(self.subtotal()),
            "voucher": self.voucher,
        }


def load_basket(basket_id):
    row = catalog_db().query_one("SELECT * FROM baskets WHERE id = ?", (basket_id,))
    if row is None:
        raise KeyError(basket_id)
    lines = [
        dict(r) for r in catalog_db().query("SELECT sku, price, qty FROM basket_lines WHERE basket_id = ?", (basket_id,))
    ]
    return Basket(row["id"], row["user_id"], lines, row["voucher"])


def merge_baskets(a, b):
    """Merge guest basket b into a. (2017, login flow)"""
    by_sku = {l["sku"]: dict(l) for l in a.lines}
    for l in b.lines:
        if l["sku"] in by_sku:
            by_sku[l["sku"]]["qty"] += l["qty"]
        else:
            by_sku[l["sku"]] = dict(l)
    a.lines = list(by_sku.values())
    a.voucher = a.voucher or b.voucher
    return a
