'use strict';

/*
 * Order storage. jdb collection "orders" in $LARDER_DATA_DIR/orders.json.
 *
 * Keyed on an internal `id` (ord_<uuid v1>), NOT on order_ref. In fulfilment
 * the collection was keyed on order_ref; the split (p.ndlovu 2023-07) moved
 * to internal ids "so we can support re-orders with the same ref" (we never
 * did). Lookups by ref are a linear scan. There is no unique index on
 * order_ref - see services/orders/create.js for what that means for retries.
 *
 * All functions return native promises except listAllCb (async@2 callback,
 * used by the v1 routes).
 */

var _ = require('lodash');
var moment = require('moment');
var db = require('../../lib/db');
var ids = require('../../lib/util/ids');

var COLLECTION = 'orders';

var STATUS = {
  PENDING: 'pending',
  CONFIRMED: 'confirmed',
  DISPATCHED: 'dispatched',
  PAYMENT_FAILED: 'payment_failed',
  CANCELLED: 'cancelled' // reserved for FEAT "cancellations" (2022, not built)
};

function col() {
  return db.collection(COLLECTION);
}

function insert(order) {
  if (!order.id) order.id = ids.orderId();
  return col().put(order);
}

function save(order) {
  // same thing as insert, jdb put() is an upsert. Two names because the v1
  // code called it save().
  return col().put(order);
}

/**
 * findByRef(ref) -> first order with that ref, in insertion order. If a ref
 * was created twice (retried POST) this returns the OLDEST one, which is
 * usually not the one the customer is looking at. (FIXME l.chen 2023-10-02)
 */
function findByRef(ref) {
  ref = String(ref);
  return col().all().then(function (all) {
    for (var i = 0; i < all.length; i++) {
      if (all[i].order_ref === ref) return all[i];
    }
    return null;
  });
}

function findAllByRef(ref) {
  ref = String(ref);
  return col().find(function (o) { return o.order_ref === ref; });
}

function all() {
  return col().all();
}

// newest first. created_at is ISO so string compare is fine (unless
// legacyDateFormat is on while an order is written - then created_at is
// "YYYY-MM-DD HH:mm:ss" and sorts BEFORE every ISO date. See lib/patches.js)
function sortNewestFirst(list) {
  return list.slice().sort(function (a, b) {
    if (a.created_at === b.created_at) return 0;
    return a.created_at < b.created_at ? 1 : -1;
  });
}

function byUser(userId) {
  return col().find(function (o) { return String(o.user_id) === String(userId); }).then(sortNewestFirst);
}

// v1 compat order: the old Rails account page sorted on the "placed" time it
// displayed (local time, to the minute) and then on the ref, and the mobile
// app (2021) still depends on that exact order (r.singh, APP-311). Same
// comparator, so the v1 list matches what customers saw.
function v1Placed(o) {
  return moment(o.created_at, moment.ISO_8601).local().format('YYYY-MM-DD HH:mm');
}

function sortV1(list) {
  return list.slice().sort(function (a, b) {
    var pa = v1Placed(a);
    var pb = v1Placed(b);
    if (pa !== pb) return pa < pb ? 1 : -1;
    return String(b.order_ref).localeCompare(String(a.order_ref));
  });
}

// "Your previous order" link on the v1 account page (APP-311 again): the
// customer's order placed before this one, from the same sorted list.
function withPrevious(sorted) {
  return sorted.map(function (o, i) {
    var prev = _.find(sorted.slice(i + 1), function (p) { return String(p.user_id) === String(o.user_id); });
    return _.assign({}, o, { previous_order_ref: prev ? prev.order_ref : null });
  });
}

function listAllCb(cb) {
  col().all(function (err, list) {
    if (err) return cb(err);
    cb(null, withPrevious(sortV1(list)));
  });
}

function counts() {
  return _.countBy(col().peekAll(), 'status');
}

module.exports = {
  STATUS: STATUS,
  COLLECTION: COLLECTION,
  insert: insert,
  save: save,
  findByRef: findByRef,
  findAllByRef: findAllByRef,
  all: all,
  byUser: byUser,
  listAllCb: listAllCb,
  sortNewestFirst: sortNewestFirst,
  counts: counts
};
