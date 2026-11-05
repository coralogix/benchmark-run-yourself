'use strict';

/*
 * Dispatch an order (the van is leaving).
 *
 *   POST /internal/dispatch/:ref
 *
 *   1. GET  ORDERS_URL/internal/orders/:ref           (404 -> 404)
 *        already dispatched -> 200, nothing else happens
 *        not confirmed      -> 409 not_confirmed
 *   2. make sure we have a pick list (the bus may not have delivered
 *      order.confirmed to us yet)
 *   3. POST INVENTORY_URL/internal/inventory/dispatches  (stock leaves:
 *      on_hand -= qty, reserved -= qty). Failure is LOGGED AND IGNORED -
 *      "the nightly stock-take will catch it" (there is no nightly stock-take
 *      since 2021).
 *   4. PATCH ORDERS_URL/internal/orders/:ref/status {status: dispatched}
 *   5. mark the pick list dispatched
 *   6. publish order.dispatched on the bus (lib/bus-client - never fails;
 *      a failed publish just means no email and no partner webhook)
 *
 * Timeouts: the orders calls have NONE (fetch-client default 0, "let the mesh
 * time it out"); inventory gets INVENTORY_TIMEOUT_MS.
 *
 * Written callback-style with async.waterfall because the scanner gateway
 * this was ported from (2020) was. Wrapped in a bluebird promise for the route.
 */

var Promise = require('bluebird');
var async = require('async');
var _ = require('lodash');
var config = require('../../lib/config');
var logger = require('../../lib/logger');
var errors = require('../../lib/errors');
var fetchClient = require('../../lib/http/fetch-client');
var busClient = require('../../lib/bus-client');
var picklists = require('./picklists');

var INVENTORY_TIMEOUT_MS = 5000;

// one dispatch per order at a time; a second concurrent request for the same
// ref gets the first one's result (scanner double-taps, 2021)
var inFlight = {};

function notConfirmed(status) {
  var e = new errors.Conflict('order is ' + status);
  e.code = 'not_confirmed';
  return e;
}

function ordersUrl(path) {
  return fetchClient.peerUrl('orders', path);
}

function fetchOrder(ref, cb) {
  fetchClient.fetchJson(ordersUrl('/internal/orders/' + encodeURIComponent(ref))).then(function (res) {
    if (res.status === 404) return cb(new errors.NotFound('order not found'));
    if (res.status !== 200 || !res.body) {
      return cb(new errors.UpstreamError('orders returned ' + res.status));
    }
    cb(null, res.body);
  }, function (err) {
    cb(new errors.UpstreamError('orders unreachable: ' + err.message));
  });
}

function commitStock(order, cb) {
  var items = (order.items || []).map(function (l) { return { sku: l.sku, qty: l.qty }; });
  fetchClient.fetchJson(fetchClient.peerUrl('inventory', '/internal/inventory/dispatches'), {
    method: 'POST',
    body: { order_ref: order.order_ref, items: items },
    timeout: INVENTORY_TIMEOUT_MS
  }).then(function (res) {
    if (res.status >= 300) {
      logger.error('warehouse: inventory dispatch commit failed, continuing', { order_ref: order.order_ref, status: res.status });
    }
    cb(null);
  }, function (err) {
    logger.error('warehouse: inventory unreachable, stock not committed', { order_ref: order.order_ref, err: err.message });
    cb(null);
  });
}

function patchStatus(ref, at, carrier, cb) {
  fetchClient.fetchJson(ordersUrl('/internal/orders/' + encodeURIComponent(ref) + '/status'), {
    method: 'PATCH',
    body: { status: 'dispatched', dispatched_at: at, carrier: carrier }
  }).then(function (res) {
    if (res.status === 404) return cb(new errors.NotFound('order not found'));
    if (res.status === 409) return cb(notConfirmed(res.body && res.body.status || 'not confirmed'));
    if (res.status >= 300) return cb(new errors.UpstreamError('orders status update returned ' + res.status));
    cb(null, res.body);
  }, function (err) {
    cb(new errors.UpstreamError('orders unreachable: ' + err.message));
  });
}

function dispatchedPayload(order, pl, at, carrier) {
  return {
    order_ref: order.order_ref,
    user_id: order.user_id || null,
    email: order.email || null,
    status: 'dispatched',
    dispatched_at: at,
    carrier: carrier,
    hold_id: order.hold_id || null,
    item_count: pl ? pl.item_count : _.sumBy(order.items || [], 'qty'),
    total_pence: order.total_pence,
    pick_list_id: pl ? pl.pick_list_id : null
  };
}

function run(ref, opts, cb) {
  var carrier = (opts && opts.carrier) || config.get('warehouse.defaultCarrier', 'larder-vans');
  var at = new Date().toISOString();
  var ctx = { order: null, pl: null, already: false };

  async.waterfall([
    function (next) {
      fetchOrder(ref, next);
    },
    function (order, next) {
      ctx.order = order;
      if (order.status === 'dispatched') {
        ctx.already = true;
        return next('already');
      }
      if (order.status !== 'confirmed') return next(notConfirmed(order.status));
      picklists.ensure(order, 'dispatch').asCallback(next);
    },
    function (pl, next) {
      ctx.pl = pl;
      commitStock(ctx.order, next);
    },
    function (next) {
      patchStatus(ref, at, carrier, next);
    },
    function (body, next) {
      picklists.markDispatched(ctx.pl, at, carrier).asCallback(function (err) {
        // the order IS dispatched at this point; a pick list write failure
        // is cosmetic
        if (err) logger.warn('warehouse: pick list update failed', { order_ref: ref, err: err.message });
        next(null);
      });
    },
    function (next) {
      busClient.publish('order.dispatched', ref, dispatchedPayload(ctx.order, ctx.pl, at, carrier)).then(function (ok) {
        if (!ok) logger.error('warehouse: order.dispatched not published', { order_ref: ref });
        next(null);
      });
    }
  ], function (err) {
    if (err === 'already') {
      // make the pick list agree, best effort, don't wait
      picklists.get(ref).then(function (pl) {
        if (pl && pl.status !== picklists.STATUS.DISPATCHED) {
          return picklists.markDispatched(pl, ctx.order.dispatched_at || at, carrier);
        }
        return null;
      }).catch(function () {});
      return cb(null, { order_ref: ref, status: 'dispatched', already: true });
    }
    if (err) return cb(err);
    logger.info('warehouse: dispatched', { order_ref: ref, carrier: carrier, items: ctx.pl && ctx.pl.item_count });
    cb(null, { order_ref: ref, status: 'dispatched' });
  });
}

function dispatch(ref, opts) {
  if (inFlight[ref]) return inFlight[ref];
  var p = Promise.fromCallback(function (cb) { run(ref, opts, cb); }).finally(function () {
    delete inFlight[ref];
  });
  inFlight[ref] = p;
  return p;
}

module.exports = {
  dispatch: dispatch,
  dispatchedPayload: dispatchedPayload,
  INVENTORY_TIMEOUT_MS: INVENTORY_TIMEOUT_MS
};
