'use strict';

/*
 * Topic workers.
 *
 * One TopicWorker per topic. Each worker runs a single loop:
 *
 *   take the HEAD message of the topic
 *     for each subscriber, in config/bus-subscriptions.json order:
 *       skip if message.delivered[subscriber]
 *       POST {topic,key,payload,delivery_id} with a bus.deliveryTimeoutMs timeout
 *       on 2xx: mark delivered, persist, next subscriber
 *       otherwise: sleep bus.retrySleepMs and try the SAME subscriber again,
 *                  forever
 *     ack (remove) the message, persist
 *   repeat
 *
 * Guarantees we give subscribers (ARCHITECTURE.md "bus"):
 *   - per-topic ordering: a subscriber never sees message N+1 before N
 *   - at-least-once: a delivery that timed out on our side may well have
 *     been processed. Subscribers must tolerate duplicates.
 *
 * Why sequential: the 2023-06 version fanned out with Promise.all and the
 * warehouse saw order.dispatched before order.confirmed for the same order
 * (INC-2402). deliverParallel() below is that version, kept "in case we need
 * it back". The fix made ordering global to the topic, i.e. every subscriber
 * waits for every other subscriber. Nobody objected at the time.
 *
 * Retry: no limit, no backoff, no dead-letter queue. "Events must never be
 * lost" (warehouse contract, schedule 4 - same wording as the webhooks
 * dispatcher). A subscriber that is down or slow holds the whole topic.
 *
 * Timeout: config bus.deliveryTimeoutMs. default.json says 30000,
 * production.json 5000, config/local.js forces 10000 everywhere (and so
 * BUS_DELIVERY_TIMEOUT_MS has no effect).
 */

var Promise = require('bluebird');
var _ = require('lodash');
var config = require('../../lib/config');
var logger = require('../../lib/logger');
var globals = require('../../lib/globals');
var telemetry = require('../../lib/telemetry');
var axiosClient = require('../../lib/http/axios-client');
var store = require('./store');
var subscriptions = require('./subscriptions');

var DEFAULT_TIMEOUT_MS = 10000;
var DEFAULT_RETRY_SLEEP_MS = 1000;
var DEFAULT_IDLE_SLEEP_MS = 200;
var SLOW_DELIVERY_WARN_MS = 2000;
var LOG_EVERY_N_FAILURES = 10;

function outcomeFor(err, res) {
  if (res) return 'http_' + res.status;
  if (axiosClient.isTimeout(err)) return 'timeout';
  if (err && err.code === 'ECONNREFUSED') return 'connection_refused';
  if (err && err.code === 'ECONNRESET') return 'connection_reset';
  return 'error';
}

/**
 * new TopicWorker(topic, opts)
 *   opts.subscribers  [{name, url: fn|string}]  (default: from subscriptions.js)
 *   opts.timeoutMs, opts.retrySleepMs, opts.idleSleepMs  (default: config)
 *   opts.store        (default: ./store)
 */
function TopicWorker(topic, opts) {
  opts = opts || {};
  this.topic = topic;
  this.store = opts.store || store;
  this.subscribers = opts.subscribers || subscriptions.forTopic(topic).map(function (s) {
    return { name: s.name, url: function () { return subscriptions.urlFor(s); } };
  });
  this.timeoutMs = opts.timeoutMs || config.get('bus.deliveryTimeoutMs') || DEFAULT_TIMEOUT_MS;
  this.retrySleepMs = opts.retrySleepMs != null ? opts.retrySleepMs : (config.get('bus.retrySleepMs') || DEFAULT_RETRY_SLEEP_MS);
  this.idleSleepMs = opts.idleSleepMs || config.get('bus.idleSleepMs') || DEFAULT_IDLE_SLEEP_MS;
  this.client = axiosClient(function () { return 'http://unused'; }, {
    timeout: this.timeoutMs,
    // anything but 2xx is a failed delivery (axios-client's default lets 4xx through)
    validateStatus: function (s) { return s >= 200 && s < 300; }
  });
  this.running = false;
  this.stopped = false;
  this.current = null; // { message_id, subscriber, since, attempts }
  this.delivered = 0;
  this.failures = 0;
  this.lastDeliveryMs = 0;
  this._wake = null;
  this._loop = null;
}

TopicWorker.prototype._sleep = function (ms) {
  var self = this;
  return new Promise(function (resolve) {
    var t = setTimeout(function () {
      self._wake = null;
      resolve();
    }, ms);
    self._wake = function () {
      clearTimeout(t);
      self._wake = null;
      resolve();
    };
  });
};

// publish() calls this so an idle worker doesn't wait out idleSleepMs.
// It does NOT interrupt a retry sleep (the wake only fires when idle).
TopicWorker.prototype.kick = function () {
  if (this._wake && !this.current) this._wake();
};

TopicWorker.prototype.deliver = function (sub, msg) {
  var self = this;
  var url = typeof sub.url === 'function' ? sub.url() : sub.url;
  var body = { topic: msg.topic, key: msg.key, payload: msg.payload, delivery_id: msg.id };
  var started = Date.now();
  return self.client.postUrl(url, body, {
    headers: { 'X-Larder-Delivery': msg.id, 'X-Larder-Topic': msg.topic }
  }).then(function (res) {
    var took = Date.now() - started;
    self.lastDeliveryMs = took;
    if (took > SLOW_DELIVERY_WARN_MS) {
      logger.warn('bus: slow delivery', { topic: msg.topic, subscriber: sub.name, took_ms: took, delivery_id: msg.id });
    }
    return { ok: true, status: res.status, took_ms: took };
  }, function (err) {
    return { ok: false, err: err, res: err && err.response, took_ms: Date.now() - started };
  });
};

// Deliver msg to one subscriber, retrying in place until it works or the
// worker is stopped. Resolves true when delivered, false when stopped.
TopicWorker.prototype.deliverUntilDone = function (sub, msg) {
  var self = this;
  self.current = { message_id: msg.id, seq: msg.seq, subscriber: sub.name, since: Date.now(), attempts: 0 };

  function attempt() {
    if (self.stopped) return Promise.resolve(false);
    self.current.attempts++;
    return self.deliver(sub, msg).then(function (r) {
      if (r.ok) {
        telemetry.inc('busDeliveries', { subscriber: sub.name, outcome: 'delivered' });
        self.store.markDelivered(msg, sub.name);
        self.delivered++;
        globals.counters.busDelivered++;
        return true;
      }
      var outcome = outcomeFor(r.err, r.res);
      telemetry.inc('busDeliveries', { subscriber: sub.name, outcome: outcome });
      self.store.markFailed(msg, sub.name, outcome);
      self.failures++;
      globals.counters.busRetries++;
      var n = msg.attempts[sub.name] || 0;
      if (n === 1 || n % LOG_EVERY_N_FAILURES === 0) {
        logger.warn('bus: delivery failed, retrying', {
          topic: msg.topic,
          subscriber: sub.name,
          delivery_id: msg.id,
          attempts: n,
          outcome: outcome,
          took_ms: r.took_ms,
          err: r.err && r.err.message
        });
      }
      return self._sleep(self.retrySleepMs).then(attempt);
    });
  }

  return attempt();
};

TopicWorker.prototype.processHead = function () {
  var self = this;
  var msg = self.store.head(self.topic);
  if (!msg) return Promise.resolve(false);

  return Promise.each(self.subscribers, function (sub) {
    if (self.stopped) return null;
    if (msg.delivered[sub.name]) return null;
    return self.deliverUntilDone(sub, msg);
  }).then(function () {
    self.current = null;
    if (self.stopped) return false;
    self.store.ack(self.topic, msg.id);
    return true;
  });
};

TopicWorker.prototype.loop = function () {
  var self = this;
  if (self.stopped) return Promise.resolve();
  return self.processHead().then(function (didWork) {
    if (self.stopped) return null;
    if (!didWork) return self._sleep(self.idleSleepMs);
    return null;
  }).catch(function (e) {
    // a bug in here would otherwise kill the topic silently
    logger.error('bus: worker loop error', { topic: self.topic, err: e.message, stack: e.stack });
    return self._sleep(self.retrySleepMs);
  }).then(function () {
    return self.loop();
  });
};

TopicWorker.prototype.start = function () {
  if (this.running) return;
  this.running = true;
  this.stopped = false;
  logger.info('bus: worker started', {
    topic: this.topic,
    subscribers: _.map(this.subscribers, 'name').join(','),
    timeout_ms: this.timeoutMs,
    retry_sleep_ms: this.retrySleepMs
  });
  this._loop = this.loop();
};

TopicWorker.prototype.stop = function () {
  this.stopped = true;
  this.running = false;
  if (this._wake) this._wake();
};

TopicWorker.prototype.status = function () {
  var head = this.store.head(this.topic);
  return {
    topic: this.topic,
    depth: this.store.depth(this.topic),
    head_seq: head ? head.seq : null,
    head_age_ms: head ? Date.now() - new Date(head.published_at).getTime() : 0,
    current: this.current ? _.assign({}, this.current, { for_ms: Date.now() - this.current.since }) : null,
    head_attempts: head ? head.attempts : {},
    head_delivered: head ? Object.keys(head.delivered) : [],
    last_error: head ? head.last_error : null,
    delivered_total: this.delivered,
    failures_total: this.failures,
    last_delivery_ms: this.lastDeliveryMs,
    subscribers: _.map(this.subscribers, 'name')
  };
};

/*
 * The 2023-06 fan-out version. NOT USED (see header, INC-2402). Left here
 * because "we'll want it back once subscribers are idempotent".
 * TODO(p.ndlovu 2023-07-14): delete or bring back behind bus.parallel.
 */
// eslint-disable-next-line no-unused-vars
function deliverParallel(worker, msg) {
  return Promise.all(worker.subscribers.map(function (sub) {
    if (msg.delivered[sub.name]) return null;
    return worker.deliver(sub, msg).then(function (r) {
      if (r.ok) worker.store.markDelivered(msg, sub.name);
      return r.ok;
    });
  })).then(function (results) {
    return _.every(results, function (r) { return r !== false; });
  });
}

// --- registry -----------------------------------------------------------------

var workers = {};

function startAll() {
  subscriptions.topics().forEach(function (topic) {
    if (!workers[topic]) workers[topic] = new TopicWorker(topic);
    workers[topic].start();
  });
  telemetry.observe('busQueueDepth', function () {
    return subscriptions.topics().map(function (topic) {
      return [store.depth(topic), { topic: topic }];
    });
  });
}

function stopAll() {
  _.forEach(workers, function (w) { w.stop(); });
}

function get(topic) {
  return workers[topic] || null;
}

function kick(topic) {
  if (workers[topic]) workers[topic].kick();
}

function statusAll() {
  return subscriptions.topics().map(function (topic) {
    return workers[topic] ? workers[topic].status() : { topic: topic, depth: store.depth(topic), running: false };
  });
}

module.exports = {
  TopicWorker: TopicWorker,
  startAll: startAll,
  stopAll: stopAll,
  get: get,
  kick: kick,
  statusAll: statusAll,
  outcomeFor: outcomeFor,
  // lib/service.js job interface
  job: { start: startAll, stop: stopAll },
  _workers: workers
};
