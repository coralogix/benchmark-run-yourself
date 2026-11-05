'use strict';

var express = require('express');
var async = require('async');
var db = require('../../lib/db');

var router = express.Router();

// Warehouse scanner app. Returns plain text "SKU-0001:120" (on_hand, NOT
// available - the scanner shows shelf stock).
router.get('/:sku', function (req, res, next) {
  db.collection('inventory').get(req.params.sku, function (err, rec) {
    if (err) return next(err);
    if (!rec) return res.status(404).type('text').send('UNKNOWN');
    res.type('text').send(rec.sku + ':' + rec.on_hand);
  });
});

// batch: /v1/stock?skus=A,B,C
router.get('/', function (req, res, next) {
  var skus = String(req.query.skus || '').split(',').filter(Boolean).slice(0, 50);
  var col = db.collection('inventory');
  async.mapSeries(skus, function (sku, done) {
    col.get(sku, function (err, rec) {
      done(err, rec ? rec.sku + ':' + rec.on_hand : sku + ':UNKNOWN');
    });
  }, function (err, lines) {
    if (err) return next(err);
    res.type('text').send(lines.join('\n'));
  });
});

module.exports = router;
