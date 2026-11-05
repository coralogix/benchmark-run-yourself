'use strict';

/*
 *   GET /internal/bus/topics          per-topic depth, head age, who we're stuck on
 *   GET /internal/bus/subscriptions   resolved subscriber URLs
 *   GET /internal/bus/messages?topic=&limit=   peek at a topic's queue
 *   GET /internal/bus/state           store snapshot (file size, saves)
 *
 * No auth ("internal"). RB-14 "bus backed up" says: look at topics, find
 * `current.subscriber`, go and fix that service. There is no skip/purge
 * endpoint; RB-14 says to stop the bus, edit bus.json by hand, start it.
 */

var express = require('express');
var _ = require('lodash');
var store = require('../store');
var subscriptions = require('../subscriptions');
var worker = require('../worker');

var router = express.Router();

router.get('/bus/topics', function (req, res) {
  res.json({ topics: worker.statusAll() });
});

router.get('/bus/subscriptions', function (req, res) {
  var out = _.mapValues(subscriptions.all(), function (subs) {
    return subs.map(function (s) {
      var url;
      try {
        url = subscriptions.urlFor(s);
      } catch (e) {
        url = null;
      }
      return { name: s.name, url_env: s.url_env, url: url };
    });
  });
  res.json(out);
});

router.get('/bus/messages', function (req, res) {
  var topic = req.query.topic;
  if (!topic) return res.status(400).json({ error: 'topic_required' });
  var limit = Math.min(parseInt(req.query.limit, 10) || 20, 200);
  res.json({ topic: topic, depth: store.depth(topic), messages: store.peek(topic, limit) });
});

router.get('/bus/state', function (req, res) {
  res.json(store.snapshot());
});

module.exports = router;
