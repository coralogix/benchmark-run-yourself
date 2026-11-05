'use strict';

/*
 * v1 partner notification. Before the webhook dispatcher existed (2019) we
 * POSTed to the partner directly from the request handler. Still used by the
 * v1 "resend" admin route (orders service), which ops use when the partner
 * says they missed an order.
 *
 * Uses node-fetch + the vendored retry-it (the webhooks dispatcher uses axios
 * and its own retry loop; the PSP adapter uses request + its own loop. Yes.)
 *
 * NOTE: this goes straight to the partner, NOT through the webhooks service
 * or its queue, and with the v1 payload builder below rather than the one in
 * services/webhooks. The fallback URL is the partner box from the 2019 bench
 * rack; it's only used if partner.url is empty, which "can't happen".
 */

var fetch = require('node-fetch');
var _ = require('lodash');
var retryIt = require('../vendor/retry-it');
var config = require('../lib/config');
var logger = require('../lib/logger');

var FALLBACK_PARTNER_URL = 'http://127.0.0.1:8091/hooks';

function partnerUrl() {
  return String(config.get('partner.url') || FALLBACK_PARTNER_URL);
}

// v1 payload (schema_version 1, same envelope as services/webhooks/payloads)
// built from a stored order. Dispatched orders get order.dispatched.
function payloadFor(order) {
  var dispatched = order.status === 'dispatched';
  return {
    event: dispatched ? 'order.dispatched' : 'order.confirmed',
    schema_version: 1,
    order_ref: order.order_ref,
    occurred_at: new Date().toISOString(),
    data: dispatched ? {
      user_id: order.user_id,
      status: 'dispatched',
      dispatched_at: order.dispatched_at,
      hold_id: order.hold_id,
      item_count: _.sumBy(order.items || [], 'qty'),
      total_pence: order.total_pence,
      carrier: order.carrier || 'larder-vans',
      source: 'fulfilment'
    } : {
      user_id: order.user_id,
      status: order.status,
      items: (order.items || []).map(function (l) {
        return {
          sku: l.sku,
          qty: l.qty,
          unit_price_pence: l.unit_price_pence,
          line_total_pence: l.unit_price_pence * l.qty,
          vat_band: l.vat_band
        };
      }),
      subtotal_pence: order.subtotal_pence,
      discount_pence: order.discount_pence,
      delivery_fee_pence: order.delivery_fee_pence,
      total_pence: order.total_pence,
      coupon_code: order.coupon ? order.coupon.code : null,
      charge_id: order.charge_id,
      delivery: order.slot ? _.assign({ hold_id: order.hold_id }, order.slot) : null,
      confirmed_at: order.created_at,
      // partner keys off this; "fulfilment" even after the split (PARTNER-7)
      source: 'fulfilment'
    }
  };
}

function notifyPartner(payload, cb) {
  var url = partnerUrl();
  retryIt(function (attempt, done) {
    fetch(url, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: { 'Content-Type': 'application/json', 'X-Larder-Resend': '1' },
      timeout: 5000
    }).then(function (res) {
      if (res.status >= 200 && res.status < 300) return done(null, { status: res.status, attempts: attempt + 1 });
      var e = new Error('partner returned ' + res.status);
      e.status = res.status;
      done(e);
    }, done);
  }, {
    retries: 2,
    minTimeout: 200,
    onRetry: function (err, n) {
      logger.warn('legacy notify: retrying', { attempt: n, err: err.message, order_ref: payload.order_ref });
    }
  }, cb);
}

module.exports = { notifyPartner: notifyPartner, payloadFor: payloadFor, FALLBACK_PARTNER_URL: FALLBACK_PARTNER_URL };
