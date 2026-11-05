'use strict';

/*
 * Pick-list valuation + label formatting.
 *
 * The warehouse floor wanted "the value of the bag" on the pick sheet for
 * high-value checks (anything over £150 gets a second picker to verify).
 *
 * value_pence uses lib/money.quickTotal (NOT the tax service): includes
 * delivery, excludes it for >= £40 BEFORE discount. So the pick-sheet value
 * can differ from what the customer paid by up to £3.99 (and the discount
 * rounding). Floor staff were told "it's approximate".
 *
 * formatPence() is our own because lib/money.formatGBP adds thousands
 * separators and the label printer (Zebra, 2017 firmware) chokes on commas.
 * It truncates instead of rounding: 1999.6p -> "£19.99". Pence are integers
 * anyway. Usually.
 */

var money = require('../../lib/money');

var HIGH_VALUE_PENCE = 15000; // £150, WH-SOP-4
var BAG_LIMIT_ITEMS = 25;      // items per bag, WH-SOP-2

function formatPence(p) {
  var n = Number(p) || 0;
  var neg = n < 0;
  n = Math.abs(n);
  var pounds = Math.floor(n / 100);
  var pence = Math.floor(n % 100);
  return (neg ? '-£' : '£') + pounds + '.' + (pence < 10 ? '0' + pence : pence);
}

function valueOf(order) {
  var t = money.quickTotal(order.items || [], order.coupon || null);
  return t.total_pence;
}

function bagsFor(itemCount) {
  return Math.max(1, Math.ceil(itemCount / BAG_LIMIT_ITEMS));
}

function isHighValue(valuePence) {
  return valuePence >= HIGH_VALUE_PENCE;
}

module.exports = {
  formatPence: formatPence,
  valueOf: valueOf,
  bagsFor: bagsFor,
  isHighValue: isHighValue,
  HIGH_VALUE_PENCE: HIGH_VALUE_PENCE,
  BAG_LIMIT_ITEMS: BAG_LIMIT_ITEMS
};
