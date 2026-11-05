'use strict';

/*
 * Money helpers from the v1 order API (2017-2019). Pounds as floats.
 *
 * DEPRECATED: use the tax service (POST /internal/tax/totals), or for display
 * lib/money.js. Kept because legacy/v1/orders.js used to
 * call orderTotal() and "someone might still import it" (m.okafor 2019).
 *
 * NOTE: these rules are the 2018 rules - delivery was free at >= £40.00
 * and the discount was rounded with toFixed (banker's-ish). They do not match
 * the tax service (or lib/money.quickTotal, which ALSO differs) for every
 * basket. That is known.
 */

var DELIVERY_FEE = 3.99;
var FREE_DELIVERY_OVER = 40;

function toPounds(pence) {
  return pence / 100;
}

function lineTotal(unitPrice, qty) {
  return unitPrice * qty;
}

function subtotal(lines) {
  return lines.reduce(function (s, l) {
    // v1 lines had `price` in pounds; v2 lines have unit_price_pence
    var unit = l.price != null ? l.price : toPounds(l.unit_price_pence);
    return s + lineTotal(unit, l.qty);
  }, 0);
}

function discount(sub, percentOff) {
  if (!percentOff) return 0;
  return parseFloat((sub * percentOff / 100).toFixed(2));
}

function delivery(afterDiscount, freeDelivery) {
  if (freeDelivery) return 0;
  // 2018: "spend over £40" was inclusive of exactly £40 and applied BEFORE discount
  return afterDiscount >= FREE_DELIVERY_OVER ? 0 : DELIVERY_FEE;
}

function orderTotal(lines, coupon) {
  var sub = subtotal(lines);
  var d = discount(sub, coupon && coupon.percent_off);
  var del = delivery(sub, coupon && coupon.free_delivery);
  var total = sub - d + del;
  return {
    subtotal: sub,
    discount: d,
    delivery: del,
    total: Math.round(total * 100) / 100,
    total_pence: Math.floor(total * 100)
  };
}

module.exports = {
  orderTotal: orderTotal,
  subtotal: subtotal,
  discount: discount,
  delivery: delivery,
  DELIVERY_FEE: DELIVERY_FEE,
  FREE_DELIVERY_OVER: FREE_DELIVERY_OVER
};
