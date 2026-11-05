'use strict';

/*
 *   POST /internal/payments/charge   { order_ref, amount_pence, token, user_id, email }
 *     201 { payment_id, charge_id, status: "captured", amount_pence, attempts }
 *     402 { error: "payment_declined", reason: "card_declined" | "fraud" }
 *     502 { error: "payment_error" }
 *
 *   GET  /internal/payments/:payment_id
 *   GET  /internal/payments?order_ref=     (newest first)
 *
 * No refund route. psp.refund() exists (see psp/adapter.js) but nothing calls
 * it - cancellations have been "next quarter" since 2022.
 */

var express = require('express');
var charges = require('../charges');
var store = require('../store');
var asyncRoute = require('../../../lib/util/asyncRoute');
var telemetry = require('../../../lib/telemetry');

var router = express.Router();

router.post('/payments/charge', asyncRoute(async function (req, res) {
  var result = await telemetry.withSpan('payments.charge', { 'larder.order_ref': String((req.body || {}).order_ref) }, function () {
    return charges.charge(req.body);
  });
  res.status(result.status).json(result.body);
}));

router.get('/payments', asyncRoute(async function (req, res) {
  if (req.query.order_ref) {
    var list = await store.byOrderRef(req.query.order_ref);
    return res.json({ payments: list.map(store.view) });
  }
  var recent = await store.recent(Math.min(parseInt(req.query.limit, 10) || 50, 500));
  res.json({ payments: recent.map(store.view) });
}));

router.get('/payments/:payment_id', asyncRoute(async function (req, res) {
  var rec = await store.get(req.params.payment_id);
  if (!rec) return res.status(404).json({ error: 'payment_not_found' });
  res.json(store.view(rec));
}));

module.exports = router;
