'use strict';

/*
 * Payment records (jdb collection "payments", keyed payment_id, file
 * $LARDER_DATA_DIR/payments.json).
 *
 * One record per POST /internal/payments/charge, NOT per PSP attempt - the
 * attempts are an array inside the record. (Finance asked for one row per
 * PSP call in 2022, FIN-151; "later".)
 *
 * Records are never deleted. payments.json is rewritten by jdb's write-behind
 * flush on every put, so it grows with the run. It has never been big enough
 * to matter (famous last words, r.singh 2020 about holds.json).
 */

var _ = require('lodash');
var db = require('../../lib/db');
var ids = require('../../lib/util/ids');

var STATUS = {
  PENDING: 'pending',
  CAPTURED: 'captured',
  DECLINED: 'declined',
  FAILED: 'failed',
  BLOCKED: 'blocked',
  REFUNDED: 'refunded' // FEAT "refunds" (not built)
};

function col() {
  return db.collection('payments');
}

// masks "tok_1234567890" -> "tok_******7890". Tokens are not card numbers
// but PCI audit 2019 said "treat them like they are".
function maskToken(token) {
  if (!token) return null;
  var s = String(token);
  if (s.length <= 8) return s.slice(0, 4) + '****';
  return s.slice(0, 4) + new Array(s.length - 7).join('*') + s.slice(-4);
}

function create(params) {
  var rec = {
    payment_id: ids.paymentId(),
    order_ref: params.order_ref ? String(params.order_ref) : null,
    user_id: params.user_id || null,
    email: params.email || null,
    amount_pence: params.amount_pence | 0,
    currency: params.currency || 'GBP',
    token_masked: maskToken(params.token),
    status: STATUS.PENDING,
    fraud: null,
    charge_id: null,
    attempts: 0,
    failure: null,
    created_at: new Date().toISOString(),
    updated_at: null
  };
  return col().put(rec);
}

function update(rec, patch) {
  _.assign(rec, patch || {});
  rec.updated_at = new Date().toISOString();
  return col().put(rec);
}

function get(paymentId) {
  return col().get(paymentId);
}

// linear scan, there is no index. Newest first.
function byOrderRef(orderRef) {
  return col().find(function (p) { return p.order_ref === String(orderRef); }).then(function (list) {
    return _.sortBy(list, 'created_at').reverse();
  });
}

function recent(limit) {
  return col().all().then(function (all) {
    return all.slice(-1 * (limit || 50)).reverse();
  });
}

function stats() {
  var all = col().peekAll();
  return {
    total: all.length,
    by_status: _.countBy(all, 'status'),
    multi_attempt: all.filter(function (p) { return p.attempts > 1; }).length
  };
}

function view(rec) {
  if (!rec) return null;
  return _.pick(rec, [
    'payment_id', 'order_ref', 'user_id', 'amount_pence', 'currency', 'status', 'charge_id',
    'attempts', 'failure', 'fraud', 'token_masked', 'created_at', 'updated_at'
  ]);
}

module.exports = {
  STATUS: STATUS,
  create: create,
  update: update,
  get: get,
  byOrderRef: byOrderRef,
  recent: recent,
  stats: stats,
  view: view,
  maskToken: maskToken
};
