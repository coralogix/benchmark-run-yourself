"""
Plain-text receipts (DP 2018). Emailed by the storefront until notifications
(ops) took over customer emails in 2022. Nothing calls this now; FEAT-7 (VAT
receipts) will probably want it back. It works in pounds and uses the
"other" money_fmt (utils, half-up) so it can disagree with the order total
by a penny on discounted orders.
"""
from larder_common.utils import money_fmt

WIDTH = 40
FOOTER = "Thank you for shopping at Larder!"


def _row(left, right):
    space = max(1, WIDTH - len(left) - len(right))
    return left + " " * space + right


def render(order_ref, breakdown):
    out = ["LARDER".center(WIDTH), "Order %s" % order_ref, "-" * WIDTH]
    for l in breakdown.get("lines", []):
        out.append(_row("%dx %s" % (l["qty"], (l.get("name") or l["sku"])[:28]),
                        money_fmt(l["unit_price"] * l["qty"])))
    out.append("-" * WIDTH)
    out.append(_row("Subtotal", money_fmt(breakdown.get("subtotal"))))
    if breakdown.get("discount"):
        out.append(_row("Discount", "-" + money_fmt(breakdown["discount"])))
    out.append(_row("Delivery", money_fmt(breakdown.get("delivery_fee"))))
    out.append(_row("TOTAL", money_fmt(breakdown.get("total"))))
    out.append("")
    out.append(FOOTER)
    return "\n".join(out)
