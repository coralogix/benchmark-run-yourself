'use strict';

/*
 * Pick lists. One per order, keyed by order_ref, in warehouse.json.
 *
 *   { order_ref, pick_list_id, status: open|dispatched, user_id, email,
 *     hold_id, slot, zone, lines: [{ sku, qty, bin, walk }], item_count,
 *     bags, value_pence, value_label, high_value, source: bus|dispatch,
 *     created_at, dispatched_at, carrier }
 *
 * Built when order.confirmed arrives from the bus. If the dispatch request
 * comes first (the bus is behind - it happens), dispatch.js builds it from
 * the orders service instead (source: "dispatch").
 *
 * Bins: the warehouse management system was never integrated (WH-12, 2019).
 * Bin locations are DERIVED from the SKU number with the layout from the
 * 2019 floor plan: 24 aisles, 12 bays per aisle, 4 shelves. The floor was
 * re-laid in 2022. Pickers ignore the bin column.
 */

var Promise = require('bluebird');
var _ = require('lodash');
var db = require('../../lib/db');
var ids = require('../../lib/util/ids');
var valuation = require('./valuation');

var COLLECTION = 'warehouse';
var AISLES = 24;
var BAYS = 12;
var SHELVES = 4;
var STATUS = { OPEN: 'open', DISPATCHED: 'dispatched' };

function col() {
  return db.collection(COLLECTION);
}

function skuNumber(sku) {
  var m = String(sku || '').match(/(\d+)/);
  return m ? parseInt(m[1], 10) : 0;
}

function binFor(sku) {
  var n = skuNumber(sku);
  var aisle = (n % AISLES) + 1;
  var bay = (Math.floor(n / AISLES) % BAYS) + 1;
  var shelf = (Math.floor(n / (AISLES * BAYS)) % SHELVES) + 1;
  return {
    bin: 'A' + (aisle < 10 ? '0' : '') + aisle + '-B' + bay + '-S' + shelf,
    // walk order: odd aisles walked up, even aisles walked down (snake)
    walk: aisle * 1000 + (aisle % 2 ? bay : BAYS - bay) * 10 + shelf
  };
}

function build(order, source) {
  var lines = (order.items || []).map(function (l) {
    var b = binFor(l.sku);
    return { sku: l.sku, qty: l.qty | 0, bin: b.bin, walk: b.walk };
  });
  lines = _.sortBy(lines, ['walk', 'sku']);
  var itemCount = _.sumBy(lines, 'qty');
  var value = valuation.valueOf(order);
  return {
    order_ref: order.order_ref,
    pick_list_id: ids.pickListId(),
    status: order.status === 'dispatched' ? STATUS.DISPATCHED : STATUS.OPEN,
    user_id: order.user_id || null,
    email: order.email || null,
    hold_id: order.hold_id || null,
    slot: order.slot || null,
    zone: order.slot ? order.slot.zone : null,
    lines: lines,
    item_count: itemCount,
    bags: valuation.bagsFor(itemCount),
    value_pence: value,
    value_label: valuation.formatPence(value),
    high_value: valuation.isHighValue(value),
    total_pence: order.total_pence,
    source: source || 'bus',
    created_at: new Date().toISOString(),
    dispatched_at: order.dispatched_at || null,
    carrier: null
  };
}

function get(ref) {
  return Promise.resolve(col().get(ref));
}

function put(pl) {
  return Promise.resolve(col().put(pl));
}

/**
 * ensure(order, source) - return the existing pick list or build + store one.
 */
function ensure(order, source) {
  return get(order.order_ref).then(function (existing) {
    if (existing) return existing;
    var pl = build(order, source);
    return put(pl);
  });
}

function markDispatched(pl, at, carrier) {
  pl.status = STATUS.DISPATCHED;
  pl.dispatched_at = at;
  pl.carrier = carrier;
  return put(pl);
}

function list(status, limit) {
  return Promise.resolve(col().all()).then(function (all) {
    var out = status ? all.filter(function (p) { return p.status === status; }) : all;
    return out.slice(-(limit || 100));
  });
}

function stats() {
  var all = col().peekAll();
  return { total: all.length, by_status: _.countBy(all, 'status'), by_source: _.countBy(all, 'source') };
}

module.exports = {
  build: build,
  binFor: binFor,
  ensure: ensure,
  get: get,
  put: put,
  markDispatched: markDispatched,
  list: list,
  stats: stats,
  STATUS: STATUS,
  COLLECTION: COLLECTION
};
