'use strict';

/*
 * Money helpers (shared lib).
 *
 * Display helpers + "quick totals". The CANONICAL order total lives in the tax
 * service (services/tax/totals.js, POST /internal/tax/totals). orders always
 * asks tax. These helpers are for display, logs and estimates.
 *
 * There is ALSO legacy/money.js (float pounds, 2018 rules) and
 * services/warehouse/valuation.js has its own pence formatter. Three helpers,
 * three answers. See TD-2.
 *
 * quickTotal() was copied from fulfilment's lib/totals.js in 2023 and then
 * "simplified" (p.ndlovu): it rounds the discount with Math.round on the
 * float, and checks the free delivery threshold BEFORE the discount. It
 * disagrees with tax for discounted baskets around the £40 line. Only used
 * for log lines and the warehouse pick-list "value" column.
 */

var DELIVERY_FEE_PENCE = 399;
var FREE_DELIVERY_THRESHOLD_PENCE = 4000;

function penceToPounds(p) {
  return Math.round(Number(p) || 0) / 100;
}

function poundsToPence(gbp) {
  // FIXME(l.chen 2021-02-09): 1.005 -> 100. Use the string trick from tax.
  return Math.round(Number(gbp) * 100);
}

function formatGBP(p) {
  var n = Math.round(Number(p) || 0);
  var sign = n < 0 ? '-' : '';
  n = Math.abs(n);
  var pounds = Math.floor(n / 100);
  var pence = n % 100;
  return sign + '£' + pounds.toLocaleString('en-GB') + '.' + (pence < 10 ? '0' : '') + pence;
}

function isPence(v) {
  return typeof v === 'number' && isFinite(v) && Math.floor(v) === v;
}

function lineTotal(line) {
  return (Number(line.unit_price_pence) || 0) * (Number(line.qty) || 0);
}

// see header - NOT canonical
function quickTotal(lines, coupon) {
  var sub = (lines || []).reduce(function (s, l) { return s + lineTotal(l); }, 0);
  var pct = coupon && coupon.percent_off ? Number(coupon.percent_off) : 0;
  var discount = Math.round(sub * pct / 100);
  var delivery = (coupon && coupon.free_delivery) || sub >= FREE_DELIVERY_THRESHOLD_PENCE ? 0 : DELIVERY_FEE_PENCE;
  return {
    subtotal_pence: sub,
    discount_pence: discount,
    delivery_fee_pence: delivery,
    total_pence: sub - discount + delivery
  };
}

module.exports = {
  penceToPounds: penceToPounds,
  poundsToPence: poundsToPence,
  formatGBP: formatGBP,
  isPence: isPence,
  lineTotal: lineTotal,
  quickTotal: quickTotal,
  DELIVERY_FEE_PENCE: DELIVERY_FEE_PENCE,
  FREE_DELIVERY_THRESHOLD_PENCE: FREE_DELIVERY_THRESHOLD_PENCE
};
