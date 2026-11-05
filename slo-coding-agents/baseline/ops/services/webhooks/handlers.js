'use strict';

/*
 * Bus -> partner webhook queue.
 *
 *   order.confirmed   -> payloads.orderConfirmed  -> dispatcher.enqueue
 *   order.dispatched  -> payloads.orderDispatched -> dispatcher.enqueue
 *
 * We ack the bus as soon as the item is in the in-memory queue. It is NOT on
 * disk until the next dispatcher tick persists it (up to webhooks.tickMs,
 * 2s). A crash in that window loses the event. (FIXME t.berg 2023-08-21)
 *
 * The bus is at-least-once, so the same delivery can arrive twice; the
 * dispatcher's uniqWith on persist catches exact duplicates, but occurred_at
 * differs between the two builds so it mostly doesn't. Partner de-dupes on
 * order_ref+event. We think.
 */

var logger = require('../../lib/logger');
var dispatcher = require('./dispatcher');
var payloads = require('./payloads');

var seen = { confirmed: 0, dispatched: 0, skipped: 0 };

function orderConfirmed(payload, msg) {
  if (!payload || !payload.order_ref) {
    seen.skipped++;
    logger.warn('webhooks: order.confirmed without order_ref, skipped', { delivery_id: msg && msg.delivery_id });
    return { skipped: true };
  }
  seen.confirmed++;
  var item = dispatcher.enqueue(payloads.orderConfirmed(payload));
  return { webhook_id: item.id };
}

function orderDispatched(payload, msg) {
  if (!payload || !payload.order_ref) {
    seen.skipped++;
    logger.warn('webhooks: order.dispatched without order_ref, skipped', { delivery_id: msg && msg.delivery_id });
    return { skipped: true };
  }
  seen.dispatched++;
  var item = dispatcher.enqueue(payloads.orderDispatched(payload));
  return { webhook_id: item.id };
}

function cartUpdated(payload, msg) {
  if (!payload || !payload.cart_id) {
    seen.skipped++;
    return { skipped: true };
  }
  seen.baskets = (seen.baskets || 0) + 1;
  var item = dispatcher.enqueue(payloads.basketUpdated(payload));
  return { webhook_id: item.id };
}

module.exports = {
  'cart.updated': cartUpdated,
  'order.confirmed': orderConfirmed,
  'order.dispatched': orderDispatched,
  stats: function () { return seen; }
};
