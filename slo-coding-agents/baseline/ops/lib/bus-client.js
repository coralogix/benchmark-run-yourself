'use strict';

/*
 * Bus client (shared). Two halves:
 *
 * 1. publish(topic, key, payload) -> Promise<boolean>
 *    POSTs {topic,key,payload} to BUS_URL/publish. Resolves true on 202.
 *    NEVER rejects: a failed publish is logged and resolves false, because
 *    "the order is already charged, we can't fail it now" (p.ndlovu 2023).
 *    There is no outbox. A failed publish is a lost event.
 *
 * 2. subscriber(handlers) -> express.Router
 *    Mounts POST /internal/events. The bus delivers
 *    {topic,key,payload,delivery_id}; we call handlers[topic](payload, msg)
 *    and answer 200 when it resolves. Unknown topics are acked (200) so the
 *    bus doesn't spin on them. A handler that throws -> 500 -> the bus
 *    retries the SAME message forever (see services/bus/worker.js), so
 *    handlers should not throw for "bad" data. Most of them don't.
 */

var express = require('express');
var axiosClient = require('./http/axios-client');
var logger = require('./logger');
var globals = require('./globals');
var telemetry = require('./telemetry');

var PUBLISH_TIMEOUT_MS = 5000;
var client = null;

function getClient() {
  if (!client) client = axiosClient('bus', { timeout: PUBLISH_TIMEOUT_MS });
  return client;
}

function publish(topic, key, payload) {
  return getClient().post('/publish', { topic: topic, key: key, payload: payload }).then(function (res) {
    if (res.status === 202 || res.status === 200) {
      globals.counters.busPublished++;
      return true;
    }
    logger.error('bus: publish rejected', { topic: topic, key: key, status: res.status, body: res.data });
    return false;
  }, function (err) {
    logger.error('bus: publish failed', { topic: topic, key: key, err: err.message, code: err.code });
    return false;
  });
}

function subscriber(handlers, opts) {
  opts = opts || {};
  var router = express.Router();
  var seen = 0;
  router.post('/internal/events', function (req, res, next) {
    var msg = req.body || {};
    seen++;
    var handler = handlers[msg.topic];
    if (!handler) {
      logger.debug('bus subscriber: ignoring topic', { topic: msg.topic, delivery_id: msg.delivery_id });
      return res.json({ ok: true, ignored: true });
    }
    telemetry.setAttr('larder.bus.topic', msg.topic);
    telemetry.setAttr('larder.bus.delivery_id', msg.delivery_id);
    Promise.resolve().then(function () {
      return handler(msg.payload || {}, msg);
    }).then(function (result) {
      res.json({ ok: true, result: result === undefined ? null : result });
    }, function (err) {
      logger.error('bus subscriber: handler failed', {
        topic: msg.topic, key: msg.key, delivery_id: msg.delivery_id, err: err.message, service: opts.name
      });
      if (err && err.status && err.status < 500) {
        // "poison" message: ack it so the topic keeps moving
        return res.status(200).json({ ok: false, error: err.code || 'rejected' });
      }
      next(err);
    });
  });
  router.get('/internal/events/stats', function (req, res) {
    res.json({ received: seen, topics: Object.keys(handlers) });
  });
  return router;
}

module.exports = {
  publish: publish,
  subscriber: subscriber,
  PUBLISH_TIMEOUT_MS: PUBLISH_TIMEOUT_MS,
  _setClient: function (c) { client = c; }
};
