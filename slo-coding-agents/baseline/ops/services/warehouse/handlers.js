'use strict';

/*
 * Bus handlers. order.confirmed -> build the pick list.
 *
 * Idempotent: if the pick list exists already (bus re-delivery, or dispatch
 * built it first) we leave it alone.
 */

var logger = require('../../lib/logger');
var picklists = require('./picklists');

function orderConfirmed(payload) {
  if (!payload || !payload.order_ref) {
    logger.warn('warehouse: order.confirmed without order_ref, ignored');
    return { ignored: true };
  }
  if (!Array.isArray(payload.items) || !payload.items.length) {
    // FIXME(s.aziz 2023-11-20): happened once, order with no lines. Ack it.
    logger.warn('warehouse: order.confirmed with no items', { order_ref: payload.order_ref });
    return { ignored: true };
  }
  return picklists.ensure(payload, 'bus').then(function (pl) {
    return { pick_list_id: pl.pick_list_id, bins: pl.lines.length };
  });
}

module.exports = {
  'order.confirmed': orderConfirmed
};
