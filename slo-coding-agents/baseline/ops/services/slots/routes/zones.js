'use strict';

/*
 *   GET /internal/zones/lookup?postcode=LD1 1AA  -> { zone } | 404 unknown_postcode
 *   GET /internal/zones                          -> { zones: [...] }  (ops tool)
 *
 * Postcode match is exact first, then on the sector ("LD1 9ZZ" -> Z1 because
 * LD1 9AB is in Z1). Unknown sectors 404.
 */

var express = require('express');
var slots = require('../slots');
var db = require('../../../lib/db');
var asyncRoute = require('../../../lib/util/asyncRoute');

var router = express.Router();

router.get('/zones/lookup', asyncRoute(async function (req, res) {
  var pc = req.query.postcode;
  if (!pc) return res.status(400).json({ error: 'postcode required' });
  var zone = await slots.zoneForPostcode(pc);
  if (!zone) return res.status(404).json({ error: 'unknown_postcode' });
  res.json({ zone: zone });
}));

router.get('/zones', function (req, res, next) {
  db.collection('zones').all(function (err, zones) {
    if (err) return next(err);
    res.json({ zones: zones });
  });
});

module.exports = router;
