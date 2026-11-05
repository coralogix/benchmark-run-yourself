'use strict';

/*
 * Email templates. The templates themselves live at the email provider
 * (email-sim in the bench); we only send a template name + data.
 *
 *   order.confirmed   -> "order_confirmation"   data: { order_ref }
 *   order.dispatched  -> "order_dispatched"     data: { order_ref }
 *
 * Until 2023 the confirmation email also carried the line items and the
 * delivery slot (see richConfirmationData below). It was cut to order_ref
 * only during the split "because the provider rejects payloads over 8KB"
 * (h.kaur 2023-07-03). The provider does not reject payloads over 8KB; it
 * was a 413 from the old nginx in front of fulfilment. The customer-facing
 * template still has the {{items}} and {{slot}} blocks; they render empty.
 */

var _ = require('lodash');
var money = require('../../lib/money');

var BY_TOPIC = {
  'order.confirmed': 'order_confirmation',
  'order.dispatched': 'order_dispatched'
};

// template -> data builder
var BUILDERS = {
  order_confirmation: function (p) {
    return { order_ref: p.order_ref };
  },
  order_dispatched: function (p) {
    return { order_ref: p.order_ref };
  }
};

function templateFor(topic) {
  return BY_TOPIC[topic] || null;
}

function dataFor(template, payload) {
  var b = BUILDERS[template];
  if (!b) throw new Error('no data builder for template ' + template);
  return b(payload || {});
}

/*
 * Pre-2023 confirmation data. NOT USED. Kept for when "the provider limit is
 * raised" (see header).
 * TODO(h.kaur 2023-07-03): put back once ops confirm the 8KB limit is gone.
 */
// eslint-disable-next-line no-unused-vars
function richConfirmationData(p) {
  return {
    order_ref: p.order_ref,
    items: _.map(p.items || [], function (l) {
      return { sku: l.sku, qty: l.qty, line_total: money.formatGBP((l.unit_price_pence | 0) * (l.qty | 0)) };
    }),
    total: money.formatGBP(p.total_pence),
    slot: p.slot ? { date: p.slot.date, window: p.slot.window } : null
  };
}

module.exports = {
  templateFor: templateFor,
  dataFor: dataFor,
  BY_TOPIC: BY_TOPIC,
  BUILDERS: BUILDERS
};
