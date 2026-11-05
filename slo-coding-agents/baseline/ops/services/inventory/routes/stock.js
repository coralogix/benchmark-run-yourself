'use strict';

/*
 * Read side of inventory.
 *
 *   POST /internal/inventory/availability  { skus: [..] }  -> { "<sku>": { available } }
 *   POST /internal/inventory/prices        { items: [{sku, qty}] } -> { items: [...] }
 *   GET  /internal/inventory/low-stock?threshold=10
 *   GET  /internal/inventory/:sku          -> { sku, on_hand, reserved, available }
 *
 * Route order matters: /low-stock must be registered before /:sku or it gets
 * looked up as a SKU called "low-stock" (that was a real bug, 2021-03).
 */

var express = require('express');
var _ = require('lodash');
var stock = require('../stock');
var asyncRoute = require('../../../lib/util/asyncRoute');
var errors = require('../../../lib/errors');
var config = require('../../../lib/config');
var telemetry = require('../../../lib/telemetry');

var router = express.Router();

// 500 is the storefront's max page size x 2.5, "plenty" (2019)
var MAX_AVAILABILITY_SKUS = 500;

// commerce calls this once per product page with every SKU on the page
router.post('/inventory/availability', asyncRoute(async function (req, res) {
  var skus = req.body && req.body.skus;
  if (!Array.isArray(skus)) throw new errors.BadRequest('skus must be an array');
  var result = await stock.availability(_.uniq(skus).slice(0, MAX_AVAILABILITY_SKUS));
  res.json(result);
}));

// orders calls this with the order lines before validating totals at tax.
// The invariant check lives in priceLines - a SKU with negative availability
// throws a plain Error here, which is NOT a known error, so it falls through
// to Express's HTML 500. On purpose (k.osei 2022, "fail loud").
router.post('/inventory/prices', asyncRoute(async function (req, res) {
  var items = req.body && req.body.items;
  if (!Array.isArray(items) || !items.length) throw new errors.BadRequest('items must be a non-empty array');
  items.forEach(function (l) {
    if (!l || !l.sku) throw new errors.BadRequest('item sku required');
  });
  telemetry.setAttr('larder.inventory.lines', items.length);
  var priced = await stock.priceLines(items);
  res.json({
    items: priced.map(function (p) {
      return { sku: p.sku, qty: p.qty, unit_price_pence: p.unit_price_pence, vat_band: p.vat_band, tags: p.tags };
    })
  });
}));

router.get('/inventory/low-stock', asyncRoute(async function (req, res) {
  var threshold = parseInt(req.query.threshold, 10);
  if (isNaN(threshold)) threshold = config.get('inventory.lowStockThreshold', 10);
  res.json({ items: await stock.lowStock(threshold) });
}));

router.get('/inventory/:sku', asyncRoute(async function (req, res) {
  res.json(await stock.getSku(req.params.sku));
}));

module.exports = router;
