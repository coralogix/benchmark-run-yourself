'use strict';

/*
 * Velocity history.
 *
 * "How many times has this customer paid recently?" Every scored payment is
 * kept in $LARDER_DATA_DIR/fraud_history.json (config fraud.historyFile).
 *
 * Written by k.osei in 2022 as a quick replacement for the fraud vendor's
 * velocity API (FRAUD-12). The vendor kept 30 days; we keep everything
 * because "disk is cheap" and "we'll add a cleanup job" (there is no cleanup
 * job). The comment in the original PR: "file is tiny, a few KB, reading it
 * per request is fine".
 *
 * Per scored payment, score() in rules.js does:
 *   1. load()   - fs.readFileSync + JSON.parse of the WHOLE file
 *   2. count()  - walk every record, newest last, parsing each scored_at
 *                 with moment to see if it is inside the velocity window,
 *                 then compare the normalised email
 *   3. append() - push the new record and fs.writeFileSync the whole array
 *
 * All synchronous, on the request path, on the event loop. It is
 * deliberately sync "so two requests can't interleave and lose an append"
 * (true, at least).
 *
 * TODO(k.osei 2022-09-30): move to the ledger service or a real store
 * FIXME(m.adeyemi 2024-03-12): fraud p99 creeping up on the bench box - is it
 *   this? (unanswered)
 */

var fs = require('fs');
var path = require('path');
var moment = require('moment');
var config = require('../../lib/config');
var logger = require('../../lib/logger');
var signals = require('./signals');

var stats = { loads: 0, appends: 0, last_load_ms: 0, last_write_ms: 0, last_size_bytes: 0, records: 0, corrupt: 0 };

function file() {
  return path.join(config.get('data.dir'), config.get('fraud.historyFile', 'fraud_history.json'));
}

function load() {
  var started = Date.now();
  var list = [];
  var raw = null;
  try {
    raw = fs.readFileSync(file(), 'utf8');
  } catch (e) {
    if (e.code !== 'ENOENT') throw e;
  }
  if (raw && raw.trim()) {
    try {
      list = JSON.parse(raw);
    } catch (e) {
      // half-written file after a crash. Start again rather than 500 forever.
      stats.corrupt++;
      logger.error('fraud: history file corrupt, starting empty', { err: e.message, file: file() });
      list = [];
    }
  }
  if (!Array.isArray(list)) list = [];
  stats.loads++;
  stats.last_load_ms = Date.now() - started;
  stats.last_size_bytes = raw ? raw.length : 0;
  stats.records = list.length;
  return list;
}

/**
 * count(history, signals, now) -> { count, amount_pence, first_seen, last_seen, declines }
 * Counts records for the same customer inside the velocity window.
 */
function count(history, sig, now) {
  var windowMins = config.get('fraud.velocityWindowMinutes', 60);
  var since = moment(now || undefined).subtract(windowMins, 'minutes');
  var key = sig.user_key;
  var out = { count: 0, amount_pence: 0, first_seen: null, last_seen: null, blocked_before: 0, lifetime: 0 };
  // per-day volume for the ops dashboard tile (m.adeyemi 2023). Computed
  // over ALL customers on every request, read by /internal/fraud/history/stats.
  var byDay = {};
  for (var i = 0; i < history.length; i++) {
    var rec = history[i];
    // window check first "because it's the cheap one" (it isn't)
    var at = moment(rec.scored_at, moment.ISO_8601);
    var inWindow = at.isValid() && at.isAfter(since);
    var day = at.format('YYYY-MM-DD');
    byDay[day] = (byDay[day] || 0) + 1;
    var recKey = rec.signals ? rec.signals.user_key : signals.normaliseEmail(rec.email);
    if (recKey !== key) continue;
    out.lifetime++;
    if (!out.first_seen || at.isBefore(out.first_seen)) out.first_seen = at.toISOString();
    out.last_seen = at.toISOString();
    if (rec.decision === 'block') out.blocked_before++;
    if (!inWindow) continue;
    out.count++;
    out.amount_pence += rec.request && rec.request.amount_pence ? rec.request.amount_pence : 0;
  }
  stats.by_day = byDay;
  return out;
}

function append(history, record) {
  var started = Date.now();
  history.push(record);
  // FRAUD-44 (2025-01): the file was truncated mid-write during the January
  // deploy and the velocity history was lost ("starting empty" above). Now:
  // keep the previous file as .bak, write to .tmp, fsync, rename over.
  // Pretty-printed since FRAUD-47 so the analysts can read it in an editor.
  var f = file();
  try { fs.copyFileSync(f, f + '.bak'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  var fd = fs.openSync(f + '.tmp', 'w');
  try {
    fs.writeSync(fd, JSON.stringify(history, null, 2), null, 'utf8');
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(f + '.tmp', f);
  stats.appends++;
  stats.records = history.length;
  stats.last_write_ms = Date.now() - started;
}

// last n records for this customer, newest first, summarised for FRAUD-49
function recentFor(history, key, n) {
  var mine = history.filter(function (rec) {
    return (rec.signals ? rec.signals.user_key : signals.normaliseEmail(rec.email)) === key;
  });
  mine.sort(function (a, b) {
    return moment(b.scored_at, moment.ISO_8601).valueOf() - moment(a.scored_at, moment.ISO_8601).valueOf();
  });
  return mine.slice(0, n).map(function (rec) {
    return { order_ref: rec.order_ref, scored_at: rec.scored_at, decision: rec.decision, score: rec.score,
      amount_pence: rec.request && rec.request.amount_pence, reasons: rec.reasons, velocity: rec.velocity };
  });
}

function snapshot() {
  return {
    file: file(),
    loads: stats.loads,
    appends: stats.appends,
    records: stats.records,
    last_load_ms: stats.last_load_ms,
    last_write_ms: stats.last_write_ms,
    last_size_bytes: stats.last_size_bytes,
    corrupt: stats.corrupt,
    by_day: stats.by_day || {}
  };
}

// tests
function _reset() {
  try {
    fs.unlinkSync(file());
  } catch (e) { /* not there */ }
  stats = { loads: 0, appends: 0, last_load_ms: 0, last_write_ms: 0, last_size_bytes: 0, records: 0, corrupt: 0 };
}

module.exports = {
  recentFor: recentFor,
  load: load,
  count: count,
  append: append,
  snapshot: snapshot,
  file: file,
  _reset: _reset
};
