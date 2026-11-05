'use strict';

/*
 * Order creation (POST /internal/orders).
 *
 *   1. price lines at inventory (SERVER prices; client unit_price_pence is
 *      only logged)                                   -> 409 out_of_stock
 *   2. totals at tax, compare with client total_pence  -> 409 price_mismatch
 *   3. reserve each line at inventory, one at a time  -> 409 out_of_stock
 *   4. confirm the slot hold at slots                  -> 409 hold_expired
 *   5. charge via payments (fraud is payments' job)   -> 402 / 502
 *   6. ledger entry (best effort) + persist the order
 *   7. publish order.confirmed on the bus (best effort), then 201
 *
 * On payment failure the reservations and the hold are released and a
 * payment_failed order is persisted (so ops can see it).
 *
 * History:
 *   2018 m.okafor   fulfilment lib/orders.js (in-process everything)
 *   2021 r.singh    replay protection: a second POST with a confirmed
 *                   order_ref returned the first order instead of charging
 *                   again
 *   2023 p.ndlovu   split into services. Replay protection REMOVED from here:
 *                   "checkout owns idempotency now, it has the cart lock"
 *                   (ARCH-12). The storage is keyed on an internal id, so a
 *                   retried POST with the same order_ref is simply a second
 *                   order: second reservation, second charge, second ledger
 *                   entry, second order.confirmed.
 *   2024 l.chen     bus publish instead of the in-process webhook enqueue
 */

var _ = require('lodash');
var Promise = require('bluebird');
var config = require('../../lib/config');
var errors = require('../../lib/errors');
var logger = require('../../lib/logger');
var globals = require('../../lib/globals');
var money = require('../../lib/money');
var telemetry = require('../../lib/telemetry');
var busClient = require('../../lib/bus-client');
var store = require('./store');
var peers = require('./peers');

var STATUS = store.STATUS;

function count(outcome) {
  telemetry.inc('orders', { outcome: outcome });
}

function validateBody(body) {
  if (!body || typeof body !== 'object') throw new errors.BadRequest('body required');
  if (!body.order_ref) throw new errors.BadRequest('order_ref required');
  if (!Array.isArray(body.items) || !body.items.length) throw new errors.BadRequest('items required');
  if (body.items.length > config.get('orders.maxItems', 100)) throw new errors.BadRequest('too many items');
  body.items.forEach(function (l) {
    if (!l || !l.sku) throw new errors.BadRequest('item sku required');
    if (!(Number(l.qty) > 0)) throw new errors.BadRequest('item qty must be > 0');
  });
  if (body.total_pence == null) throw new errors.BadRequest('total_pence required');
  // payment_token is NOT validated here; the PSP says 400 missing_token and
  // payments turns that into a 502. (FIXME a.mensah 2023-09-11)
}

// Merge duplicate SKU lines (storefront sends them separately if the
// customer adds the same item twice from different pages)
function mergeLines(items) {
  var bySku = {};
  var order = [];
  items.forEach(function (l) {
    if (!bySku[l.sku]) {
      bySku[l.sku] = { sku: l.sku, qty: 0, unit_price_pence: l.unit_price_pence };
      order.push(l.sku);
    }
    bySku[l.sku].qty += Number(l.qty) | 0;
  });
  return order.map(function (s) { return bySku[s]; });
}

function buildOrder(body, priced, t, coupon) {
  return {
    order_ref: String(body.order_ref),
    user_id: body.user_id || null,
    email: body.email || null,
    items: priced.map(function (l) {
      return { sku: l.sku, qty: l.qty, unit_price_pence: l.unit_price_pence, vat_band: l.vat_band };
    }),
    coupon: coupon,
    subtotal_pence: t.subtotal_pence,
    discount_pence: t.discount_pence,
    delivery_fee_pence: t.delivery_fee_pence,
    total_pence: t.total_pence,
    client_total_pence: body.total_pence,
    client_subtotal_pence: body.subtotal_pence,
    client_delivery_fee_pence: body.delivery_fee_pence,
    hold_id: body.hold_id || null,
    slot: null,
    charge_id: null,
    payment_id: null,
    created_at: new Date().toISOString()
  };
}

function confirmedPayload(order) {
  return {
    order_ref: order.order_ref,
    order_id: order.id,
    user_id: order.user_id,
    email: order.email,
    status: STATUS.CONFIRMED,
    items: order.items.map(function (l) {
      return { sku: l.sku, qty: l.qty, unit_price_pence: l.unit_price_pence, vat_band: l.vat_band };
    }),
    coupon: order.coupon,
    subtotal_pence: order.subtotal_pence,
    discount_pence: order.discount_pence,
    delivery_fee_pence: order.delivery_fee_pence,
    total_pence: order.total_pence,
    charge_id: order.charge_id,
    payment_id: order.payment_id,
    hold_id: order.hold_id,
    slot: order.slot,
    created_at: order.created_at
  };
}

/**
 * createOrder(body) -> { status, body }  (HTTP status + response body)
 * Known failures are returned; anything unexpected throws (-> 500).
 */
async function createOrder(body) {
  validateBody(body);
  var orderRef = String(body.order_ref);
  telemetry.setAttr('larder.order_ref', orderRef);
  var log = logger.withContext({ order_ref: orderRef });

  var lines = mergeLines(body.items);
  var coupon = body.coupon || null;

  // 1. server prices
  var priced = await peers.priceLines(lines);
  if (priced.status === 409) {
    count('out_of_stock');
    return { status: 409, body: { error: 'out_of_stock', sku: priced.sku } };
  }
  var pricedLines = priced.items;

  // 2. totals (tax is canonical; lib/money.quickTotal is only for the log line)
  var t = await peers.totals(pricedLines, coupon, body.total_pence);
  if (!t.ok) {
    count('price_mismatch');
    log.info('orders: price mismatch', {
      client_total: body.total_pence,
      expected: t.expected_total_pence,
      quick_total: money.quickTotal(pricedLines, coupon).total_pence,
      coupon: coupon && coupon.code
    });
    return { status: 409, body: { error: 'price_mismatch', expected_total_pence: t.expected_total_pence } };
  }

  // 3. reserve, line by line (sequential on purpose: "so the audit log reads
  // in order" - r.singh 2020)
  var reserved = [];
  for (var i = 0; i < pricedLines.length; i++) {
    var r;
    try {
      r = await peers.reserve(pricedLines[i].sku, pricedLines[i].qty, orderRef);
    } catch (e) {
      await peers.releaseReservations(reserved, orderRef);
      count('error');
      throw e;
    }
    if (r.status === 409) {
      await peers.releaseReservations(reserved, orderRef);
      count('out_of_stock');
      return { status: 409, body: { error: 'out_of_stock', sku: r.sku } };
    }
    reserved.push({ sku: pricedLines[i].sku, qty: pricedLines[i].qty });
  }

  var order = buildOrder(body, pricedLines, t, coupon);

  // 4. slot hold
  var holdConfirmed = false;
  if (body.hold_id) {
    var h;
    try {
      h = await peers.confirmHold(body.hold_id, orderRef);
    } catch (e) {
      await peers.releaseReservations(reserved, orderRef);
      count('error');
      throw e;
    }
    if (h.status !== 200) {
      await peers.releaseReservations(reserved, orderRef);
      count('hold_expired');
      return { status: 409, body: { error: 'hold_expired' } };
    }
    holdConfirmed = true;
    order.slot = h.slot || null;
  }

  // 5. charge
  var c = await peers.charge({
    order_ref: orderRef,
    amount_pence: t.total_pence,
    token: body.payment_token,
    user_id: order.user_id,
    email: order.email
  });

  if (c.status !== 201) {
    await peers.releaseReservations(reserved, orderRef);
    if (holdConfirmed) await peers.releaseHold(body.hold_id, 'payment_failed');
    order.status = STATUS.PAYMENT_FAILED;
    order.failure = (c.body && (c.body.reason || c.body.error)) || 'payment_error';
    await store.insert(order);
    globals.counters.ordersFailed++;
    if (c.status === 402) {
      count('declined');
      log.info('orders: payment declined', { reason: order.failure });
      return { status: 402, body: { error: 'payment_declined' } };
    }
    count('payment_error');
    log.warn('orders: payment error', { failure: order.failure });
    return { status: 502, body: { error: 'payment_error' } };
  }

  order.status = STATUS.CONFIRMED;
  order.charge_id = c.body.charge_id;
  order.payment_id = c.body.payment_id || null;
  order.payment_attempts = c.body.attempts || 1;

  // 6. ledger + persist. Ledger first: "finance would rather have an entry
  // without an order than an order without an entry" (FIN-140).
  if (config.get('orders.ledgerOnConfirm', true)) {
    await peers.ledgerEntry({
      order_ref: orderRef,
      kind: 'charge',
      amount_pence: order.total_pence,
      charge_id: order.charge_id,
      payment_id: order.payment_id,
      user_id: order.user_id,
      meta: { source: 'orders', discount_pence: order.discount_pence, delivery_fee_pence: order.delivery_fee_pence }
    });
  }
  await store.insert(order);

  // 7. publish. Awaited so the event is "on the bus" before we answer.
  // INC-2291 (2025-02): 41 orders were charged and confirmed while the bus was
  // down; the warehouse never picked them and the partner never heard. Since
  // then an order whose event can't be published is NOT confirmed: we answer
  // 503 and the customer retries (same order_ref). The charge stays on the
  // PSP side until finance's reversal job runs (FIN-88, not built yet).
  if (config.get('orders.publishOnConfirm', true)) {
    var published = await busClient.publish('order.confirmed', orderRef, confirmedPayload(order));
    if (!published) {
      log.error('orders: order.confirmed NOT published, refusing to confirm (INC-2291)', { charge_id: order.charge_id });
      telemetry.inc('orders', { outcome: 'publish_failed' });
      return { status: 503, body: { error: 'order_not_confirmed', reason: 'event_bus_unavailable' } };
    }
  }

  globals.counters.ordersCreated++;
  globals.lastOrderRef = orderRef;
  count('created');
  log.info('orders: confirmed', { total_pence: order.total_pence, charge_id: order.charge_id, id: order.id });
  return {
    status: 201,
    body: { order_ref: orderRef, status: 'confirmed', charge_id: order.charge_id, total_pence: order.total_pence }
  };
}

/**
 * setStatus(ref, { status, dispatched_at, carrier }) - warehouse calls this.
 * Only confirmed -> dispatched is allowed. dispatched -> dispatched is a
 * no-op. Applies to ALL orders with that ref (duplicates included), because
 * "the van takes whatever is on the shelf" (warehouse, 2024).
 */
function setStatus(ref, change) {
  change = change || {};
  return store.findAllByRef(ref).then(function (list) {
    if (!list.length) throw new errors.NotFound('order not found');
    var target = change.status;
    if (target !== STATUS.DISPATCHED) {
      throw new errors.BadRequest('unsupported status ' + target);
    }
    var updatable = list.filter(function (o) { return o.status === STATUS.CONFIRMED || o.status === STATUS.DISPATCHED; });
    if (!updatable.length) {
      var err = new errors.Conflict('order is ' + list[0].status);
      err.code = 'not_confirmed';
      throw err;
    }
    return Promise.mapSeries(updatable, function (o) {
      if (o.status === STATUS.DISPATCHED) return o;
      o.status = STATUS.DISPATCHED;
      o.dispatched_at = change.dispatched_at || new Date().toISOString();
      o.carrier = change.carrier || null;
      return store.save(o);
    }).then(function (saved) {
      return _.first(saved);
    });
  });
}

module.exports = {
  createOrder: createOrder,
  setStatus: setStatus,
  mergeLines: mergeLines,
  validateBody: validateBody,
  buildOrder: buildOrder,
  confirmedPayload: confirmedPayload
};
