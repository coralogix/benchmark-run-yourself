'use strict';

var express = require('express');
var async = require('async');
var _ = require('lodash');
var store = require('../../services/orders/store');
var views = require('../../services/orders/views');
var logger = require('../../lib/logger');
var notify = require('../notify');

var router = express.Router();

router.get('/:ref', function (req, res, next) {
  store.findByRef(req.params.ref).then(function (o) {
    if (!o) return res.status(404).send('Not found');
    res.json(views.v1View(o));
  }, next);
});

// list by customer - ops tool. No pagination, capped at 200.
// (FIXME l.chen 2019-08-12)
router.get('/', function (req, res, next) {
  store.listAllCb(function (err, list) {
    if (err) return next(err);
    var mine = list.filter(function (o) {
      return !req.query.customer || o.user_id === req.query.customer;
    });
    res.json(mine.slice(0, 200).map(views.v1View));
  });
});

// Turned off 2020-03 (m.okafor). Kept the old body below for reference.
router.post('/', function (req, res) {
  logger.warn('v1: POST /v1/orders called', { ua: req.headers['user-agent'] });
  res.status(410).json({ error: 'gone', use: 'POST /internal/orders' });
  /*
  var money = require('../money');
  var t = money.orderTotal(req.body.lines, req.body.coupon);
  if (Math.abs(t.total - req.body.total) > 0.01) return res.status(409)...
  ...
  */
});

// Re-send the confirmation to the partner, synchronously, bypassing the
// webhooks service and its queue (see legacy/notify.js).
router.post('/:ref/resend', function (req, res, next) {
  async.waterfall([
    function (done) {
      store.findByRef(req.params.ref).then(function (o) { done(null, o); }, done);
    },
    function (o, done) {
      if (!o) return done({ notFound: true });
      notify.notifyPartner(notify.payloadFor(o), done);
    }
  ], function (err, result) {
    if (err && err.notFound) return res.status(404).json({ error: 'not_found' });
    if (err) return res.status(502).json({ error: 'partner_failed', message: err.message });
    res.json({ ok: true, attempts: result.attempts });
  });
});

// totals summary for the ops "day sheet" spreadsheet. Pounds, floats.
router.get('/summary/today', function (req, res, next) {
  store.listAllCb(function (err, list) {
    if (err) return next(err);
    var today = new Date().toISOString().slice(0, 10);
    var todays = list.filter(function (o) { return String(o.created_at).slice(0, 10) === today; });
    res.json({
      date: today,
      count: todays.length,
      total: _.sumBy(todays, function (o) { return o.total_pence / 100; }),
      by_state: _.countBy(todays.map(views.v1View), 'state')
    });
  });
});

module.exports = router;
