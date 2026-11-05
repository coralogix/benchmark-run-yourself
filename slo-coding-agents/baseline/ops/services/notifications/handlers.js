'use strict';

/*
 * Bus handlers. One email per event, sent INLINE: we only answer the bus
 * once the provider has answered (or timed out). That way "the bus knows the
 * email went" (h.kaur 2023). It also means the bus waits for the provider.
 *
 * We always ack (resolve), even when the email failed: a failed email is
 * recorded as status=failed and nobody retries it. The one exception is a
 * bug in here (throws -> 500 -> bus retries forever, see lib/bus-client).
 *
 * No email on the payload (old orders, guest checkouts in 2021) -> skipped.
 */

var Promise = require('bluebird');
var config = require('../../lib/config');
var logger = require('../../lib/logger');
var templates = require('./templates');
var mailer = require('./mailer');
var store = require('./store');

function handlerFor(topic) {
  return function (payload, msg) {
    payload = payload || {};
    var template = templates.templateFor(topic);
    var base = {
      order_ref: payload.order_ref || null,
      topic: topic,
      template: template,
      to: payload.email || null,
      delivery_id: msg && msg.delivery_id
    };

    if (config.get('email.enabled') === false) {
      return store.record(Object.assign(base, { status: 'skipped', error: 'email_disabled' })).return({ skipped: true });
    }
    if (!payload.email) {
      logger.info('notifications: no email on payload, skipping', { order_ref: payload.order_ref, topic: topic });
      return store.record(Object.assign(base, { status: 'skipped', error: 'no_email' })).return({ skipped: true });
    }

    var data = templates.dataFor(template, payload);
    return Promise.resolve(mailer.send({ to: payload.email, template: template, data: data })).then(function (r) {
      return store.record(Object.assign(base, {
        status: r.ok ? 'sent' : 'failed',
        message_id: r.message_id || null,
        error: r.error || null,
        took_ms: r.took_ms
      })).then(function () {
        return { sent: r.ok, message_id: r.message_id || null };
      });
    });
  };
}

module.exports = {
  'order.confirmed': handlerFor('order.confirmed'),
  'order.dispatched': handlerFor('order.dispatched'),
  handlerFor: handlerFor
};
