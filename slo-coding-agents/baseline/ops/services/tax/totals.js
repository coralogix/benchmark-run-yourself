'use strict';

/*
 * THE canonical order total. Integer pence throughout.
 *
 *   subtotal  = sum(unit_price_pence * qty)          (server-side prices)
 *   discount  = round_half_up(subtotal * percent_off / 100)
 *   delivery  = 399 if (subtotal - discount) < 4000 and not coupon.free_delivery
 *               else 0
 *   total     = subtotal - discount + delivery
 *
 * Agreed with storefront + finance in FIN-102 (2021). If you change this,
 * commerce pricing must change in the same release or every checkout 409s.
 *
 * Moved here from larder-fulfilment lib/totals.js in the 2023 split. orders
 * calls POST /internal/tax/totals with INVENTORY unit prices.
 *
 * (old text:) If you change this,
 * storefront must change in the same release or every checkout 409s.
 */

var DELIVERY_FEE_PENCE = 399;
var FREE_DELIVERY_THRESHOLD_PENCE = 4000;

// Half-up rounding of n / d for non-negative integers.
function roundHalfUpDiv(n, d) {
  return Math.floor((2 * n + d) / (2 * d));
}

function roundHalfUp(x) {
  return Math.floor(x + 0.5);
}

function subtotal(lines) {
  var s = 0;
  for (var i = 0; i < lines.length; i++) {
    s += (lines[i].unit_price_pence | 0) * (lines[i].qty | 0);
  }
  return s;
}

function discountFor(sub, coupon) {
  if (!coupon || coupon.percent_off == null) return 0;
  var pct = Number(coupon.percent_off);
  if (!(pct > 0)) return 0;
  if (Math.floor(pct) === pct) return roundHalfUpDiv(sub * pct, 100);
  // fractional percentages (only VIP7.5 ever used this)
  return roundHalfUp(sub * pct / 100);
}

function deliveryFor(afterDiscount, coupon) {
  if (coupon && coupon.free_delivery) return 0;
  return afterDiscount < FREE_DELIVERY_THRESHOLD_PENCE ? DELIVERY_FEE_PENCE : 0;
}

/**
 * computeTotals(lines, coupon)
 *   lines:  [{ sku, qty, unit_price_pence }]  - MUST be server-side prices
 *   coupon: { code, percent_off, free_delivery } | null
 */
function computeTotals(lines, coupon) {
  var sub = subtotal(lines || []);
  var discount = discountFor(sub, coupon);
  var delivery = deliveryFor(sub - discount, coupon);
  return {
    subtotal_pence: sub,
    discount_pence: discount,
    delivery_fee_pence: delivery,
    total_pence: sub - discount + delivery
  };
}

/**
 * validate(lines, coupon, clientTotalPence) -> { ok, expected, totals }
 */
function validate(lines, coupon, clientTotalPence) {
  var totals = computeTotals(lines, coupon);
  return {
    ok: Number(clientTotalPence) === totals.total_pence,
    expected: totals.total_pence,
    totals: totals
  };
}

module.exports = {
  computeTotals: computeTotals,
  validate: validate,
  subtotal: subtotal,
  discountFor: discountFor,
  deliveryFor: deliveryFor,
  roundHalfUpDiv: roundHalfUpDiv,
  DELIVERY_FEE_PENCE: DELIVERY_FEE_PENCE,
  FREE_DELIVERY_THRESHOLD_PENCE: FREE_DELIVERY_THRESHOLD_PENCE
};
