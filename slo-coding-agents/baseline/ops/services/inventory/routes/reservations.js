'use strict';

/*
 * Write side of inventory. Called by orders (reserve / release) and
 * warehouse (dispatch).
 *
 *   POST /internal/inventory/reservations  { sku, qty, order_ref }
 *        201 { sku, qty, available } | 409 { error: "out_of_stock", sku }
 *   POST /internal/inventory/releases      { order_ref, items: [{sku, qty}] }
 *        200 { released: n }
 *   POST /internal/inventory/dispatches    { order_ref, items: [{sku, qty}] }
 *        200 { ok: true }
 *
 * One reservation per request, one line at a time. A batch endpoint was
 * written in 2023 ("reserveMany") and reverted the same week because orders
 * relied on knowing WHICH line failed. See git log, not here.
 *
 * There is no record of which order holds which reservation. Releases trust
 * the caller's quantities. (FIXME(k.osei 2022-05-17): reservations table)
 */

var express = require('express');
var stock = require('../stock');
var asyncRoute = require('../../../lib/util/asyncRoute');
var errors = require('../../../lib/errors');
var logger = require('../../../lib/logger');
var telemetry = require('../../../lib/telemetry');

var router = express.Router();

function lines(body) {
  var items = body && body.items;
  if (!Array.isArray(items)) throw new errors.BadRequest('items must be an array');
  return items.filter(function (l) { return l && l.sku; }).map(function (l) {
    return { sku: l.sku, qty: Number(l.qty) | 0 };
  });
}

router.post('/inventory/reservations', asyncRoute(async function (req, res) {
  var body = req.body || {};
  if (!body.sku) throw new errors.BadRequest('sku required');
  telemetry.setAttr('larder.order_ref', body.order_ref);
  telemetry.setAttr('larder.sku', body.sku);
  var r = await stock.reserve(body.sku, Number(body.qty), { order_ref: body.order_ref });
  res.status(201).json(r);
}));

router.post('/inventory/releases', asyncRoute(async function (req, res) {
  var body = req.body || {};
  var l = lines(body);
  var n = await stock.releaseAll(l, { order_ref: body.order_ref });
  if (n !== l.length) {
    logger.warn('inventory: partial release', { order_ref: body.order_ref, requested: l.length, released: n });
  }
  res.json({ released: n });
}));

router.post('/inventory/dispatches', asyncRoute(async function (req, res) {
  var body = req.body || {};
  await stock.commitDispatch(lines(body), { order_ref: body.order_ref });
  res.json({ ok: true });
}));

module.exports = router;
