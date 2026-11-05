'use strict';

/*
 * Calls from orders to other ops services. Everything goes through the
 * <NAME>_URL env vars (config peers.*), i.e. through the mesh.
 *
 * Two HTTP clients in one file, because:
 *   - inventory / slots / payments calls were lifted from fulfilment's
 *     in-process calls in the 2023 split and wrapped with the old `request`
 *     client (lib/http/request-client) - default timeout 30s.
 *   - tax / ledger were added later with axios (lib/http/axios-client),
 *     which RESOLVES on 4xx (validateStatus < 500) and REJECTS on 5xx.
 * The two clients report errors differently; each function below normalises
 * to { status, body } or throws. Read them before you change one.
 *
 * Tests replace these functions with sinon stubs (create.js always calls
 * them as peers.x(), never destructured).
 */

var requestClient = require('../../lib/http/request-client');
var axiosClient = require('../../lib/http/axios-client');
var errors = require('../../lib/errors');
var logger = require('../../lib/logger');

var clients = {};

function rq(name) {
  if (!clients['rq:' + name]) clients['rq:' + name] = requestClient(name);
  return clients['rq:' + name];
}

function ax(name) {
  if (!clients['ax:' + name]) clients['ax:' + name] = axiosClient(name);
  return clients['ax:' + name];
}

function upstream(service, err) {
  var e = new errors.UpstreamError(service + ' call failed: ' + (err && err.message));
  e.service = service;
  e.cause = err;
  return e;
}

// ---- inventory -------------------------------------------------------------

/**
 * priceLines([{sku, qty}]) -> { status: 200, items } | { status: 409, sku }
 * Inventory answers 500 (HTML) when a SKU's books don't balance
 * (assertStockInvariant). That is NOT mapped: we throw a plain Error so this
 * service 500s too. k.osei: "fail loud".
 */
function priceLines(lines) {
  return rq('inventory').post('/internal/inventory/prices', {
    items: lines.map(function (l) { return { sku: l.sku, qty: l.qty }; })
  }).then(function (res) {
    if (res.statusCode === 200) return { status: 200, items: res.body.items };
    if (res.statusCode === 409) return { status: 409, sku: res.body && res.body.sku, error: res.body && res.body.error };
    throw new Error('inventory /prices returned ' + res.statusCode);
  });
}

function reserve(sku, qty, orderRef) {
  return rq('inventory').post('/internal/inventory/reservations', {
    sku: sku, qty: qty, order_ref: orderRef
  }).then(function (res) {
    if (res.statusCode === 201 || res.statusCode === 200) return { status: 201, body: res.body };
    if (res.statusCode === 409) return { status: 409, sku: (res.body && res.body.sku) || sku };
    throw new Error('inventory /reservations returned ' + res.statusCode + ' for ' + sku);
  });
}

// best effort, never throws
function releaseReservations(lines, orderRef) {
  if (!lines || !lines.length) return Promise.resolve(null);
  return rq('inventory').post('/internal/inventory/releases', {
    order_ref: orderRef, items: lines
  }).then(function (res) {
    if (res.statusCode !== 200) logger.error('orders: release reservations failed', { order_ref: orderRef, status: res.statusCode });
    return res.body;
  }, function (err) {
    logger.error('orders: release reservations failed', { order_ref: orderRef, err: err.message });
    return null;
  });
}

// ---- slots -----------------------------------------------------------------

/**
 * confirmHold(holdId, orderRef) -> { status: 200, slot } | { status: 409 }
 * Slots takes a pool connection for this. If the pool is exhausted slots
 * waits up to 30s for one, and so do we (request default timeout 30s).
 */
function confirmHold(holdId, orderRef) {
  return rq('slots').post('/internal/slot-holds/' + encodeURIComponent(holdId) + '/confirm', {
    order_ref: orderRef
  }).then(function (res) {
    if (res.statusCode === 200) return { status: 200, slot: res.body && res.body.slot, hold: res.body };
    if (res.statusCode === 409 || res.statusCode === 404) return { status: 409, error: 'hold_expired' };
    throw new Error('slots confirm returned ' + res.statusCode);
  });
}

function releaseHold(holdId, reason) {
  if (!holdId) return Promise.resolve(null);
  return rq('slots').post('/internal/slot-holds/' + encodeURIComponent(holdId) + '/release', {
    reason: reason || 'released'
  }).then(function (res) {
    return res.body;
  }, function (err) {
    logger.error('orders: release hold failed', { hold_id: holdId, err: err.message });
    return null;
  });
}

// ---- payments ----------------------------------------------------------------

/**
 * charge({...}) -> { status: 201, body } | { status: 402, body } | { status: 502, body }
 * Network errors / timeouts -> { status: 502, body: {error:'payment_error'} }.
 *
 * No Idempotency-Key, no order-level dedupe: if this times out the payment
 * may still have been taken. (payments retries the PSP itself, see
 * services/payments/psp/adapter.js)
 */
function charge(params) {
  return rq('payments').post('/internal/payments/charge', params).then(function (res) {
    if (res.statusCode === 201 || res.statusCode === 200) return { status: 201, body: res.body };
    if (res.statusCode === 402) return { status: 402, body: res.body };
    return { status: 502, body: res.body || { error: 'payment_error' } };
  }, function (err) {
    logger.error('orders: payments call failed', { order_ref: params.order_ref, err: err.message, code: requestClient.errCode(err) });
    return { status: 502, body: { error: 'payment_error' }, err: err };
  });
}

// ---- tax -----------------------------------------------------------------------

/**
 * totals(items, coupon, clientTotal) -> { subtotal_pence, ..., total_pence, ok, expected_total_pence }
 * Throws UpstreamError (502) if tax is unhappy.
 */
function totals(items, coupon, clientTotal) {
  return ax('tax').post('/internal/tax/totals', {
    items: items.map(function (l) { return { sku: l.sku, qty: l.qty, unit_price_pence: l.unit_price_pence }; }),
    coupon: coupon || null,
    client_total_pence: clientTotal
  }).then(function (res) {
    if (res.status !== 200) throw upstream('tax', new Error('status ' + res.status));
    return res.data;
  }, function (err) {
    throw upstream('tax', err);
  });
}

// ---- ledger --------------------------------------------------------------------

// best effort; the ledger also backfills from the bus (order.confirmed)
function ledgerEntry(entry) {
  return ax('ledger').post('/internal/ledger/entries', entry).then(function (res) {
    if (res.status !== 201 && res.status !== 200) {
      logger.error('orders: ledger rejected entry', { order_ref: entry.order_ref, status: res.status });
      return null;
    }
    return res.data;
  }, function (err) {
    logger.error('orders: ledger write failed', { order_ref: entry.order_ref, err: err.message });
    return null;
  });
}

module.exports = {
  priceLines: priceLines,
  reserve: reserve,
  releaseReservations: releaseReservations,
  confirmHold: confirmHold,
  releaseHold: releaseHold,
  charge: charge,
  totals: totals,
  ledgerEntry: ledgerEntry,
  _clients: clients
};
