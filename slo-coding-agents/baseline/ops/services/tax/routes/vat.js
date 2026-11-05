'use strict';

/*
 *   POST /internal/tax/vat
 *     { items: [{ sku, qty, unit_price_pence, vat_band }] }
 *   -> { lines: [{sku,qty,vat_band,rate,gross_pence,vat_pence,net_pence}],
 *        by_band: {...}, vat_pence, gross_pence }
 *
 *   GET /internal/tax/rates -> { zero: 0, reduced: 5, standard: 20 }
 *
 * vat_band defaults to "standard" when missing (finance: "over-declare, never
 * under-declare"). The inventory records all have a band, so this only bites
 * callers that forget to send it.
 */

var express = require('express');
var vat = require('../vat');
var errors = require('../../../lib/errors');

var router = express.Router();

router.post('/tax/vat', function (req, res, next) {
  var body = req.body || {};
  if (!Array.isArray(body.items)) return next(new errors.BadRequest('items must be an array'));
  try {
    res.json(vat.basketVat(body.items));
  } catch (e) {
    if (e.status === 400) return res.status(400).json({ error: 'unknown_vat_band', message: e.message });
    next(e);
  }
});

router.get('/tax/rates', function (req, res) {
  res.json(vat.rates());
});

module.exports = router;
