'use strict';

/*
 *   POST /publish   { topic, key, payload }  -> 202 { id, topic, seq }
 *
 * Mounted at the ROOT, not under /internal (BUILD_SPEC section 4; the 2023
 * design doc had /internal/bus/publish and the first clients used /publish,
 * so /publish won). /internal/bus/publish is an alias.
 *
 * - topic is required (400 {"error":"topic_required"}).
 * - A topic with no subscribers is accepted (202) and DROPPED. Callers can't
 *   tell. (Typos in topic names have bitten us: "order.confirm", 2023-09.)
 * - The message is persisted (bus.json rewrite) before we answer.
 * - payload is stored as-is. No size limit beyond the 1mb body limit.
 */

var express = require('express');
var logger = require('../../../lib/logger');
var store = require('../store');
var subscriptions = require('../subscriptions');
var worker = require('../worker');

var router = express.Router();

// topic names: lower-case words separated by dots. "order.confirmed"
var TOPIC_RE = /^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)*$/;

function publish(req, res) {
  var body = req.body || {};
  var topic = body.topic;
  if (!topic || typeof topic !== 'string') {
    return res.status(400).json({ error: 'topic_required' });
  }
  if (!TOPIC_RE.test(topic)) {
    return res.status(400).json({ error: 'bad_topic' });
  }
  if (!subscriptions.forTopic(topic).length) {
    store.noteDropped();
    logger.warn('bus: publish to topic with no subscribers, dropped', { topic: topic, key: body.key });
    return res.status(202).json({ id: null, topic: topic, seq: null, dropped: true });
  }
  var msg = store.append(topic, body.key, body.payload);
  worker.kick(topic);
  logger.verbose('bus: published', { topic: topic, key: msg.key, seq: msg.seq, id: msg.id });
  res.status(202).json({ id: msg.id, topic: topic, seq: msg.seq });
}

router.post('/publish', publish);
router.post('/internal/bus/publish', publish);

module.exports = router;
