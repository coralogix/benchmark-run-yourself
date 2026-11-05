'use strict';

/*
 *   POST /internal/tax/totals
 *     { items: [{ sku, qty, unit_price_pence }], coupon: {code,percent_off,free_delivery}|null,
 *       client_total_pence? }
 *   -> { subtotal_pence, discount_pence, delivery_fee_pence, total_pence,
 *        ok?, expected_total_pence? }       (ok/expected only when client_total_pence sent)
 *
 * The caller is responsible for sending SERVER prices (orders sends
 * inventory's). We don't look anything up.
 *
 *   POST /internal/tax/validate  - v0 name for the same thing (2023-06, orders
 *   used it for two weeks). Kept.
 */

var express = require('express');
var totals = require('../totals');
var errors = require('../../../lib/errors');
var telemetry = require('../../../lib/telemetry');

var router = express.Router();

function parse(body) {
  if (!body || !Array.isArray(body.items)) throw new errors.BadRequest('items must be an array');
  var lines = body.items.map(function (l) {
    if (!l || !l.sku) throw new errors.BadRequest('item sku required');
    return { sku: l.sku, qty: Number(l.qty) | 0, unit_price_pence: Number(l.unit_price_pence) | 0 };
  });
  return { lines: lines, coupon: body.coupon || null };
}

function handle(req, res) {
  var p = parse(req.body);
  var t = totals.computeTotals(p.lines, p.coupon);
  telemetry.setAttr('larder.total_pence', t.total_pence);
  var body = {
    subtotal_pence: t.subtotal_pence,
    discount_pence: t.discount_pence,
    delivery_fee_pence: t.delivery_fee_pence,
    total_pence: t.total_pence
  };
  if (req.body.client_total_pence !== undefined && req.body.client_total_pence !== null) {
    body.ok = Number(req.body.client_total_pence) === t.total_pence;
    body.expected_total_pence = t.total_pence;
  }
  res.json(body);
}

router.post('/tax/totals', function (req, res, next) {
  try {
    handle(req, res);
  } catch (e) {
    next(e);
  }
});

router.post('/tax/validate', function (req, res, next) {
  try {
    if (req.body && req.body.total_pence !== undefined && req.body.client_total_pence === undefined) {
      req.body.client_total_pence = req.body.total_pence;
    }
    handle(req, res);
  } catch (e) {
    next(e);
  }
});

module.exports = router;
