'use strict';

/*
 *   GET  /internal/webhooks/queue           dispatcher snapshot (depth, statuses, persist timings)
 *   GET  /internal/webhooks/queue/items     first N items (?limit=, default 20) - attempt history included
 *   POST /internal/webhooks/compact         RB-7: drop delivered items + persist (used to be /v1/admin/webhooks/compact)
 *   POST /internal/webhooks/tick            run one dispatcher tick now (debug)
 *   POST /internal/webhooks/resend          { payload } enqueue a raw v1 envelope (ops, RB-7)
 */

var express = require('express');
var _ = require('lodash');
var dispatcher = require('../dispatcher');
var handlers = require('../handlers');

var router = express.Router();

router.get('/webhooks/queue', function (req, res) {
  res.json(_.assign(dispatcher.snapshot(), { handled: handlers.stats() }));
});

router.get('/webhooks/queue/items', function (req, res) {
  var limit = Math.min(parseInt(req.query.limit, 10) || 20, 500);
  res.json({ depth: dispatcher.depth(), items: dispatcher.items().slice(0, limit) });
});

router.post('/webhooks/compact', function (req, res) {
  var removed = dispatcher.compactQueue();
  dispatcher.persist();
  res.json({ removed: removed, depth: dispatcher.depth() });
});

router.post('/webhooks/tick', function (req, res, next) {
  dispatcher.tick().then(function () {
    res.json(dispatcher.snapshot());
  }, next);
});

router.post('/webhooks/resend', function (req, res) {
  var payload = req.body && req.body.payload;
  if (!payload || !payload.event || !payload.order_ref) {
    return res.status(400).json({ error: 'payload with event and order_ref required' });
  }
  var item = dispatcher.enqueue(payload);
  res.status(202).json({ id: item.id, depth: dispatcher.depth() });
});

module.exports = router;
