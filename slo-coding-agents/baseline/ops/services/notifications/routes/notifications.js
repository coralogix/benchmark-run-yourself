'use strict';

/*
 *   GET /internal/notifications?order_ref=&limit=   records for an order (or the latest N)
 *   GET /internal/notifications/stats               counts by status/template
 *   POST /internal/notifications/test               { to, template } send a test email (ops)
 */

var express = require('express');
var store = require('../store');
var mailer = require('../mailer');
var asyncRoute = require('../../../lib/util/asyncRoute');

var router = express.Router();

router.get('/notifications', asyncRoute(async function (req, res) {
  if (req.query.order_ref) {
    var list = await store.byOrderRef(String(req.query.order_ref));
    return res.json({ notifications: list });
  }
  var limit = Math.min(parseInt(req.query.limit, 10) || 50, 500);
  res.json({ notifications: await store.recent(limit) });
}));

router.get('/notifications/stats', function (req, res) {
  res.json(store.stats());
});

router.post('/notifications/test', asyncRoute(async function (req, res) {
  var body = req.body || {};
  if (!body.to) return res.status(400).json({ error: 'to required' });
  var r = await mailer.send({ to: body.to, template: body.template || 'test', data: body.data || {} });
  res.status(r.ok ? 200 : 502).json(r);
}));

module.exports = router;
