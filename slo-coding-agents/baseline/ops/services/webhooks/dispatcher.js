'use strict';

/*
 * Partner webhook dispatcher.
 *
 * In-memory queue, persisted to $LARDER_DATA_DIR/webhook_queue.json so events
 * survive a restart. A timer ticks every config webhooks.tickMs (2000 via
 * config/local.js). Each tick:
 *
 *   1. sends up to BATCH_SIZE (40) pending items to PARTNER_WEBHOOK_URL
 *   2. retries everything that previously failed (retryFailed, added 2021)
 *   3. persists the queue
 *
 * Delivered items are removed. Failed items are kept and retried - we never
 * drop a partner event (warehouse contract, schedule 4).
 *
 * History:
 *   2019 d.hughes  original (node-fetch, fire-and-forget)
 *   2020 d.hughes  persisted queue after events were lost on deploy
 *   2021 t.berg    axios + retries for failed items, attempt history for the
 *                  partner dispute process (they wanted the exact bodies we sent)
 *   2022 t.berg    de-dupe on persist after the double-dispatch incident
 *   2023 p.ndlovu  moved out of fulfilment into the webhooks service for the
 *                  split; items now arrive from the bus (handlers.js) instead
 *                  of being enqueued in-process by the order flow. Otherwise
 *                  unchanged ("don't touch it, partner disputes depend on the
 *                  attempt history").
 *
 * Every item that ever fails stays in `queue` until it is delivered, and
 * every tick re-sends ALL of them (retryFailed has no batch limit) and then
 * JSON.stringify()s + writeFileSync()s the whole queue, attempt history
 * included. With the partner failing a third of requests that file gets big.
 * TODO(t.berg 2022-03-08): cap attempts[] at 20. Never done.
 */

var fs = require('fs');
var path = require('path');
var axios = require('axios');
var _ = require('lodash');
var config = require('../../lib/config');
var logger = require('../../lib/logger');
var ids = require('../../lib/util/ids');
var instruments = require('../../lib/telemetry/instruments');
var globals = require('../../lib/globals');

var BATCH_SIZE = 40;
var DEFAULT_TICK_MS = 2000;
var STATUS = { PENDING: 'pending', RETRY: 'retry', DELIVERED: 'delivered' };

var queue = [];
var timer = null;
var ticking = false;
var lastTick = null;
var lastPersistMs = 0;
var lastPersistBytes = 0;
var stats = { ticks: 0, sent: 0, delivered: 0, failed: 0, persisted: 0 };

function queueFile() {
  return path.join(config.get('data.dir'), config.get('webhooks.queueFile', 'webhook_queue.json'));
}

function targetUrl() {
  return config.get('partner.url');
}

function http() {
  return axios.create({
    timeout: config.get('webhooks.timeoutMs', 3000),
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': 'larder-ops-webhooks/1'
    },
    // treat every non-2xx as a failure
    validateStatus: function (s) { return s >= 200 && s < 300; }
  });
}

var client = null;

/**
 * enqueue(payload) - payload is a schema v1 envelope (see payloads.js)
 */
function enqueue(payload) {
  var item = {
    id: ids.webhookId(),
    event: payload.event,
    order_ref: payload.order_ref,
    status: STATUS.PENDING,
    enqueued_at: new Date().toISOString(),
    payload: payload,
    attempts: []
  };
  queue.push(item);
  return item;
}

function recordFailure(item, err) {
  var res = err && err.response;
  item.attempts.push({
    at: new Date().toISOString(),
    status: res ? res.status : null,
    error: err ? (err.code || err.message) : 'unknown',
    message: err ? err.message : null,
    responseHeaders: res ? res.headers : null,
    responseBody: res ? res.data : null,
    // partner dispute process needs the exact body we sent each time
    requestBody: _.cloneDeep(item.payload),
    stack: err && err.stack ? String(err.stack) : null
  });
}

function send(item) {
  stats.sent++;
  return client.post(targetUrl(), item.payload).then(function (res) {
    return { ok: true, status: res.status };
  }, function (err) {
    return { ok: false, err: err };
  });
}

// --- 1. fresh items --------------------------------------------------------

function deliverPending() {
  var batch = queue.filter(function (i) { return i.status === STATUS.PENDING; }).slice(0, BATCH_SIZE);
  if (!batch.length) return Promise.resolve();
  return Promise.all(batch.map(function (item) {
    return send(item).then(function (r) {
      if (r.ok) {
        stats.delivered++;
        globals.counters.webhooksSent++;
        instruments.inc('webhookDeliveries', { outcome: 'delivered' });
        return item.id;
      }
      stats.failed++;
      globals.counters.webhooksFailed++;
      instruments.inc('webhookDeliveries', { outcome: 'failed' });
      item.status = STATUS.RETRY;
      recordFailure(item, r.err);
      logger.warn('webhooks: delivery failed, will retry', {
        id: item.id, event: item.event, order_ref: item.order_ref, err: r.err && r.err.message
      });
      return null;
    });
  })).then(function (deliveredIds) {
    var done = _.compact(deliveredIds);
    if (done.length) {
      queue = queue.filter(function (i) { return done.indexOf(i.id) === -1; });
    }
  });
}

// --- 2. retries (t.berg 2021) -----------------------------------------------
// No backoff on purpose: partner outages are short and they want events ASAP.

function retryFailed() {
  var retrying = queue.filter(function (i) { return i.status === STATUS.RETRY; });
  if (!retrying.length) return Promise.resolve();
  return Promise.all(retrying.map(function (item) {
    return send(item).then(function (r) {
      if (r.ok) {
        stats.delivered++;
        globals.counters.webhooksSent++;
        instruments.inc('webhookDeliveries', { outcome: 'delivered_retry' });
        // mark it; compactQueue() takes delivered items out
        item.status = STATUS.DELIVERED;
        item.delivered_at = new Date().toISOString();
        return;
      }
      stats.failed++;
      globals.counters.webhooksFailed++;
      instruments.inc('webhookDeliveries', { outcome: 'failed' });
      recordFailure(item, r.err);
    });
  }));
}

// Removes delivered items. Runs on load() so a restart starts clean.
function compactQueue() {
  var before = queue.length;
  queue = queue.filter(function (i) { return i.status !== STATUS.DELIVERED; });
  return before - queue.length;
}

// --- 3. persistence ---------------------------------------------------------

var DEDUPE_IGNORE = ['id', 'enqueued_at'];

function sameEvent(a, b) {
  return _.isEqual(_.omit(a, DEDUPE_IGNORE), _.omit(b, DEDUPE_IGNORE));
}

function persist() {
  if (config.get('webhooks.persist') === false) return;
  var started = Date.now();
  // de-dupe guard (2022 double-dispatch incident): the same event got
  // enqueued twice with different ids, so compare everything except those.
  var unique = _.uniqWith(queue, sameEvent);
  if (unique.length !== queue.length) {
    logger.warn('webhooks: duplicate queue items dropped', { dropped: queue.length - unique.length });
    queue = unique;
  }
  var body = JSON.stringify(queue);
  fs.writeFileSync(queueFile(), body, 'utf8');
  stats.persisted++;
  lastPersistMs = Date.now() - started;
  lastPersistBytes = body.length;
}

function load() {
  var file = queueFile();
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
  } catch (e) {
    // EEXIST on old node; ignore
  }
  try {
    var raw = fs.readFileSync(file, 'utf8');
    var parsed = raw.trim() ? JSON.parse(raw) : [];
    queue = Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    if (e.code !== 'ENOENT') {
      logger.error('webhooks: could not load persisted queue, starting empty', { err: e.message, file: file });
    }
    queue = [];
  }
  var removed = compactQueue();
  logger.info('webhooks: queue loaded', { items: queue.length, compacted: removed });
  return queue.length;
}

function tick() {
  if (ticking) return Promise.resolve();
  ticking = true;
  stats.ticks++;
  lastTick = new Date();
  return deliverPending()
    .then(retryFailed)
    .catch(function (e) {
      logger.error('webhooks: tick failed', { err: e.message });
    })
    .then(function () {
      try {
        persist();
      } catch (e) {
        logger.error('webhooks: persist failed', { err: e.message });
      }
      ticking = false;
    });
}

function start() {
  if (timer) return;
  client = http();
  load();
  instruments.observe('webhookQueueDepth', function () { return queue.length; });
  var every = config.get('webhooks.tickMs') || DEFAULT_TICK_MS;
  timer = setInterval(tick, every);
  logger.info('webhooks: dispatcher started', { tick_ms: every, url: targetUrl(), batch: BATCH_SIZE });
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

// tests + legacy admin
function _reset() {
  queue = [];
  ticking = false;
  stats = { ticks: 0, sent: 0, delivered: 0, failed: 0, persisted: 0 };
  if (!client) client = http();
}

function snapshot() {
  return {
    depth: queue.length,
    by_status: _.countBy(queue, 'status'),
    by_event: _.countBy(queue, 'event'),
    last_tick: lastTick,
    last_persist_ms: lastPersistMs,
    last_persist_bytes: lastPersistBytes,
    stats: stats
  };
}

module.exports = {
  enqueue: enqueue,
  tick: tick,
  start: start,
  stop: stop,
  load: load,
  persist: persist,
  compactQueue: compactQueue,
  snapshot: snapshot,
  depth: function () { return queue.length; },
  items: function () { return queue; },
  STATUS: STATUS,
  BATCH_SIZE: BATCH_SIZE,
  _reset: _reset
};
