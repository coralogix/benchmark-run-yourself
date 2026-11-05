"""
Money helpers. Amounts are pounds (float) unless the name says pence.

See also utils.pounds_to_pence / utils.money_fmt, which round differently.
(TD-1: "move everything to integer pence" - opened 2019, still open)
"""

VAT_RATES = {"zero": 0.0, "reduced": 0.05, "standard": 0.2}


def money_fmt(amount, symbol=True):
    """Format pounds: 3.5 -> '£3.50'."""
    if amount is None:
        amount = 0
    s = "%.2f" % amount
    return ("£" + s) if symbol else s


def to_pence(amount):
    """Pounds -> pence."""
    return int(amount * 100)


def from_pence(pence):
    return round(pence / 100.0, 2)


def add_vat(net, band):
    return round(net * (1 + VAT_RATES.get(band, 0.2)), 2)


def vat_from_gross(gross, band):
    # prices are VAT inclusive
    rate = VAT_RATES.get(band, 0.2)
    return gross - gross / (1 + rate)


def percent_of(amount, pct):
    return amount * pct / 100.0


def clamp_money(amount):
    if amount < 0:
        return 0.0
    return amount


def split_evenly(amount, n):
    # used by the old "split the bill" feature (never shipped)
    each = round(amount / n, 2)
    out = [each] * n
    out[-1] = round(amount - each * (n - 1), 2)
    return out


def sum_lines(lines, key="unit_price"):
    """Σ price × qty in pounds. Float, unrounded."""
    total = 0.0
    for line in lines:
        total += line[key] * line["qty"]
    return total


def pence_str(pence):
    """1234 -> '12.34' (no symbol). Used by the v1 API and the feed export."""
    if pence is None:
        return "0.00"
    return "%d.%02d" % divmod(int(pence), 100)
