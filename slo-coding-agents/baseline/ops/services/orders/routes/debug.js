'use strict';

// Debug endpoints. "Temporary" (2020). Not authenticated.

var express = require('express');
var _ = require('lodash');
var store = require('../store');
var asyncRoute = require('../../../lib/util/asyncRoute');

var router = express.Router();

router.get('/debug/orders/stats', asyncRoute(async function (req, res) {
  var all = await store.all();
  var refs = _.countBy(all, 'order_ref');
  var dupes = Object.keys(refs).filter(function (k) { return refs[k] > 1; });
  res.json({
    total: all.length,
    by_status: _.countBy(all, 'status'),
    // added during INC-2410 (2024-03); nobody alerts on it
    duplicate_refs: dupes.length,
    duplicate_examples: dupes.slice(0, 10)
  });
}));

// all copies of a ref, oldest first (GET /internal/orders/:ref only shows the first)
router.get('/debug/orders/:ref/all', asyncRoute(async function (req, res) {
  res.json({ orders: await store.findAllByRef(req.params.ref) });
}));

module.exports = router;
