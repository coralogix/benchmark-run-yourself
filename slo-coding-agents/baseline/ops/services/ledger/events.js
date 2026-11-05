'use strict';

/*
 * Bus subscriber: order.confirmed.
 *
 * orders writes the charge entry itself (POST /internal/ledger/entries) right
 * after the payment. This handler is the BACKFILL path from 2023, when the
 * direct write was briefly turned off (config orders.ledgerOnConfirm) and we
 * wanted the bus to cover it. If the order already has a charge entry, it's a
 * no-op; otherwise we write one with source "bus".
 *
 * NOTE: "already has a charge entry" is per order_ref. orders does not
 * dedupe order_ref, so a retried checkout produces two confirmed orders with
 * the same ref and two direct entries; this handler sees the first one and
 * skips both bus deliveries.
 */

var Promise = require('bluebird');
var journal = require('./journal');
var logger = require('../../lib/logger');

var appendAsync = Promise.promisify(journal.append);

function onOrderConfirmed(payload) {
  if (!payload || !payload.order_ref) {
    logger.warn('ledger: order.confirmed without order_ref, ignoring');
    return Promise.resolve({ skipped: 'no_order_ref' });
  }
  var ref = String(payload.order_ref);
  if (journal.hasCharge(ref)) {
    return Promise.resolve({ skipped: 'exists' });
  }
  if (!(payload.total_pence > 0)) {
    // zero-value orders (100% coupon, staff test) - nothing to book
    return Promise.resolve({ skipped: 'zero' });
  }
  logger.info('ledger: backfilling charge from bus', { order_ref: ref });
  return appendAsync({
    kind: 'charge',
    order_ref: ref,
    amount_pence: payload.total_pence,
    charge_id: payload.charge_id,
    payment_id: payload.payment_id,
    user_id: payload.user_id,
    source: 'bus',
    meta: { order_id: payload.order_id || null, discount_pence: payload.discount_pence || 0 }
  }).then(function (e) {
    return { entry_id: e.entry_id };
  });
}

module.exports = {
  'order.confirmed': onOrderConfirmed
};
