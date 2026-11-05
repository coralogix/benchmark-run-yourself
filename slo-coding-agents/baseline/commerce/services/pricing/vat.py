"""
VAT breakdown for receipts (FEAT-7 spike, SB 2023-09). Not wired to any route.

Prices are VAT inclusive. The canonical VAT numbers live in ops (tax
service); this is a local copy of the same idea that rounds per line instead
of per band, so it will disagree with tax by a penny on some baskets.
"""
from larder_common.money import VAT_RATES


def line_vat_pence(unit_price_pence, qty, band):
    rate = VAT_RATES.get(band, 0.2)
    gross = unit_price_pence * qty
    return int(round(gross - gross / (1 + rate)))


def vat_summary(lines):
    by_band = {}
    for l in lines:
        band = l.get("vat_band", "standard")
        by_band[band] = by_band.get(band, 0) + line_vat_pence(l["unit_price_pence"], l["qty"], band)
    return by_band
