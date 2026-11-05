'use strict';

/*
 * Order JSON shapes.
 *
 *   publicView  - GET /internal/orders/:ref and the paginated list. Contract
 *                 with commerce (BUILD_SPEC 4): {order_ref,user_id,status,items,
 *                 subtotal_pence,discount_pence,delivery_fee_pence,total_pence,
 *                 charge_id,hold_id,created_at} + extras below.
 *   compactView - what the "v1 compat" list returned in 2021 (it was meant to
 *                 be smaller. It isn't: items are still in there because the
 *                 accounts page showed item counts).
 *   v1View      - /v1/orders (pounds, camelCase). See legacy/v1/orders.js.
 */

var _ = require('lodash');
var moment = require('moment');

function itemView(l) {
  return { sku: l.sku, qty: l.qty, unit_price_pence: l.unit_price_pence };
}

function publicView(o) {
  return {
    order_ref: o.order_ref,
    user_id: o.user_id,
    email: o.email || null,
    status: o.status,
    items: (o.items || []).map(itemView),
    subtotal_pence: o.subtotal_pence,
    discount_pence: o.discount_pence,
    delivery_fee_pence: o.delivery_fee_pence,
    total_pence: o.total_pence,
    coupon: o.coupon || null,
    charge_id: o.charge_id || null,
    hold_id: o.hold_id || null,
    slot: o.slot || null,
    created_at: o.created_at,
    dispatched_at: o.dispatched_at || null
  };
}

// 2021 (ACC-33): "compact" view for the accounts page. Same as publicView
// minus email/slot. The comment in the accounts repo says this is ~10x
// smaller. It is about 10% smaller.
function compactView(o) {
  return _.omit(publicView(o), ['email', 'slot']);
}

// v1 order shape: pounds, camelCase, "placed" instead of created_at
function v1View(o) {
  return {
    ref: o.order_ref,
    customer: o.user_id,
    state: o.status === 'confirmed' ? 'PLACED' : String(o.status || '').toUpperCase(),
    lines: (o.items || []).map(function (l) {
      return { sku: l.sku, quantity: l.qty, price: l.unit_price_pence / 100 };
    }),
    total: o.total_pence / 100,
    placed: moment(o.created_at).format('DD/MM/YYYY HH:mm'),
    paymentRef: o.charge_id
  };
}

module.exports = {
  publicView: publicView,
  compactView: compactView,
  v1View: v1View,
  itemView: itemView
};
