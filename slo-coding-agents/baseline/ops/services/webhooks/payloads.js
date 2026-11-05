'use strict';

/*
 * Partner webhook payloads, schema_version 1.
 *
 *   { event, schema_version, order_ref, occurred_at, data: {...} }
 *
 * v1 order.dispatched does NOT include line items (partner said they'd look
 * them up from order.confirmed; they don't). v2 was specced in 2023 and never
 * shipped - see signer.js.
 *
 * Since the 2023 split the input is the BUS payload (see orders'
 * order.confirmed and warehouse's order.dispatched), not the fulfilment order
 * record. Field names mostly line up. `source` still says "fulfilment"
 * because the partner filters on it (j.watts: "do NOT change").
 *
 * occurred_at is the time we BUILT the payload (i.e. when the bus got round
 * to delivering to us), not when the order was confirmed. confirmed_at is
 * the order time. Partner has complained about this twice (2023-10, 2024-02).
 */

var _ = require('lodash');

var SCHEMA_VERSION = 1;
var SOURCE = 'fulfilment';

function envelope(event, orderRef, data) {
  return {
    event: event,
    schema_version: SCHEMA_VERSION,
    order_ref: orderRef,
    occurred_at: new Date().toISOString(),
    data: data
  };
}

function lineView(l) {
  var unit = l.unit_price_pence | 0;
  return {
    sku: l.sku,
    qty: l.qty,
    unit_price_pence: unit,
    line_total_pence: unit * (l.qty | 0),
    vat_band: l.vat_band || null
  };
}

function deliveryView(order) {
  var slot = order.slot;
  if (!slot) return null;
  return {
    hold_id: order.hold_id || null,
    slot_id: slot.slot_id,
    zone: slot.zone,
    date: slot.date,
    window: slot.window
  };
}

/**
 * orderConfirmed(busPayload) - busPayload is orders' order.confirmed payload
 */
function orderConfirmed(order) {
  order = order || {};
  return envelope('order.confirmed', order.order_ref, {
    user_id: order.user_id,
    status: order.status || 'confirmed',
    items: (order.items || []).map(lineView),
    subtotal_pence: order.subtotal_pence,
    discount_pence: order.discount_pence,
    delivery_fee_pence: order.delivery_fee_pence,
    total_pence: order.total_pence,
    coupon_code: order.coupon ? order.coupon.code : null,
    charge_id: order.charge_id || null,
    delivery: deliveryView(order),
    confirmed_at: order.created_at || null,
    source: SOURCE
  });
}

/**
 * orderDispatched(busPayload) - warehouse's order.dispatched payload
 */
// PARTNER-77 (2024-10): the partner's CRM wants basket changes for its
// abandoned-basket journeys. commerce doesn't publish cart.updated yet
// (PM-1290), so nothing arrives here today.
function basketUpdated(cart) {
  return envelope('basket.updated', null, {
    cart_id: cart.cart_id,
    customer_id: cart.user_id || null,
    lines: (cart.lines || []).map(function (l) { return { sku: l.sku, qty: l.qty }; }),
    total_pence: cart.total_pence || null
  });
}

function orderDispatched(order, extra) {
  order = order || {};
  extra = extra || {};
  return envelope('order.dispatched', order.order_ref, {
    user_id: order.user_id,
    status: 'dispatched',
    dispatched_at: order.dispatched_at || null,
    hold_id: order.hold_id || null,
    item_count: order.item_count != null ? order.item_count : _.sumBy(order.items || [], 'qty'),
    total_pence: order.total_pence,
    carrier: order.carrier || extra.carrier || 'larder-vans',
    source: SOURCE
  });
}

module.exports = {
  basketUpdated: basketUpdated,
  SCHEMA_VERSION: SCHEMA_VERSION,
  orderConfirmed: orderConfirmed,
  orderDispatched: orderDispatched,
  envelope: envelope
};
