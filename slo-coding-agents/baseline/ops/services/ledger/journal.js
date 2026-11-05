'use strict';

/*
 * The ledger journal. Append-only list of accounting entries in
 * $LARDER_DATA_DIR/ledger.json (config ledger.file).
 *
 * NOT jdb: finance wanted "a plain file we can open in Excel" (FIN-102,
 * 2021), so this is a JSON array, rewritten in full on every append with
 * fs.writeFileSync. It's append-only in the accounting sense: entries are
 * never edited or removed; corrections are new entries with kind
 * "adjustment".
 *
 * Entry:
 *   { entry_id, seq, kind, order_ref, amount_pence, currency, charge_id,
 *     payment_id, user_id, source, meta, created_at }
 *
 * kinds: charge | refund | adjustment | fee
 *   (refund: nothing writes these yet - cancellations backlog)
 *
 * Amounts are signed from the merchant's point of view: charge +, refund -.
 * The caller sends a positive amount and we apply the sign. (The 2021 import
 * script sent signed refunds, so there are a few double-negative refunds in
 * the old prod file. Not here.)
 *
 * TODO(l.chen 2021-06-30): write-ahead + periodic snapshot instead of full rewrite
 */

var fs = require('fs');
var path = require('path');
var _ = require('lodash');
var config = require('../../lib/config');
var logger = require('../../lib/logger');
var ids = require('../../lib/util/ids');
var globals = require('../../lib/globals');

var KINDS = ['charge', 'refund', 'adjustment', 'fee'];
var SIGN = { charge: 1, refund: -1, adjustment: 1, fee: -1 };

var entries = null; // in memory, loaded once
var lastWriteMs = 0;

function file() {
  return path.join(config.get('data.dir'), config.get('ledger.file', 'ledger.json'));
}

function load() {
  if (entries) return entries;
  var raw = null;
  try {
    raw = fs.readFileSync(file(), 'utf8');
  } catch (e) {
    if (e.code !== 'ENOENT') throw e;
  }
  entries = raw && raw.trim() ? JSON.parse(raw) : [];
  if (!Array.isArray(entries)) entries = [];
  logger.info('ledger: journal loaded', { entries: entries.length, file: file() });
  return entries;
}

function persist() {
  var started = Date.now();
  fs.mkdirSync(path.dirname(file()), { recursive: true });
  fs.writeFileSync(file(), JSON.stringify(entries, null, 1), 'utf8');
  lastWriteMs = Date.now() - started;
}

function validate(e) {
  var err;
  if (!e || typeof e !== 'object') err = 'entry required';
  else if (KINDS.indexOf(e.kind) === -1) err = 'kind must be one of ' + KINDS.join(',');
  else if (!(Number(e.amount_pence) >= 0) || Math.floor(Number(e.amount_pence)) !== Number(e.amount_pence)) err = 'amount_pence must be a non-negative integer';
  else if (e.kind !== 'adjustment' && !e.order_ref) err = 'order_ref required';
  if (err) {
    var x = new Error(err);
    x.status = 400;
    x.code = 'bad_request';
    throw x;
  }
}

/**
 * append(entry, cb) - node-style callback, because the original ledger
 * module (2021) was callback-only and the route still uses it that way.
 */
function append(input, cb) {
  try {
    validate(input);
  } catch (e) {
    return process.nextTick(function () { cb(e); });
  }
  load();
  var seq = entries.length + 1;
  var entry = {
    entry_id: ids.ledgerEntryId(seq),
    seq: seq,
    kind: input.kind,
    order_ref: input.order_ref ? String(input.order_ref) : null,
    amount_pence: SIGN[input.kind] * Number(input.amount_pence),
    currency: config.get('ledger.currency', 'GBP'),
    charge_id: input.charge_id || null,
    payment_id: input.payment_id || null,
    user_id: input.user_id || null,
    source: input.source || 'api',
    meta: input.meta || {},
    created_at: new Date().toISOString()
  };
  entries.push(entry);
  try {
    persist();
  } catch (e) {
    entries.pop();
    return process.nextTick(function () { cb(e); });
  }
  globals.counters.ledgerAppends++;
  process.nextTick(function () { cb(null, entry); });
}

function find(filter, limit) {
  load();
  var out = entries;
  if (filter && filter.order_ref) {
    out = out.filter(function (e) { return e.order_ref === String(filter.order_ref); });
  }
  if (filter && filter.kind) {
    out = out.filter(function (e) { return e.kind === filter.kind; });
  }
  if (limit) out = out.slice(-1 * limit);
  return out;
}

function hasCharge(orderRef) {
  load();
  // linear scan from the end, most lookups are for recent orders
  for (var i = entries.length - 1; i >= 0; i--) {
    if (entries[i].order_ref === orderRef && entries[i].kind === 'charge') return true;
  }
  return false;
}

function balance() {
  load();
  var byKind = _.mapValues(_.groupBy(entries, 'kind'), function (list) {
    return { count: list.length, amount_pence: _.sumBy(list, 'amount_pence') };
  });
  return {
    currency: config.get('ledger.currency', 'GBP'),
    entries: entries.length,
    balance_pence: _.sumBy(entries, 'amount_pence'),
    by_kind: byKind,
    last_write_ms: lastWriteMs
  };
}

// tests
function _reset() {
  entries = null;
  lastWriteMs = 0;
}

module.exports = {
  append: append,
  find: find,
  hasCharge: hasCharge,
  balance: balance,
  load: load,
  file: file,
  KINDS: KINDS,
  _reset: _reset
};
