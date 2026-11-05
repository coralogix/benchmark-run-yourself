'use strict';

/*
 *   GET /internal/pick-lists/:ref           one pick list
 *   GET /internal/pick-lists?status=open    list (last 100)
 *   GET /internal/pick-lists/:ref/sheet     plain-text pick sheet for the Zebra printer
 */

var express = require('express');
var picklists = require('../picklists');

var router = express.Router();

router.get('/pick-lists', function (req, res, next) {
  var limit = Math.min(parseInt(req.query.limit, 10) || 100, 1000);
  picklists.list(req.query.status, limit).then(function (list) {
    res.json({ pick_lists: list, total: list.length });
  }, next);
});

router.get('/pick-lists/:ref', function (req, res, next) {
  picklists.get(req.params.ref).then(function (pl) {
    if (!pl) return res.status(404).json({ error: 'not_found' });
    res.json(pl);
  }, next);
});

// 2017 Zebra firmware: 32 columns, ASCII only, no commas (see valuation.js)
router.get('/pick-lists/:ref/sheet', function (req, res, next) {
  picklists.get(req.params.ref).then(function (pl) {
    if (!pl) return res.status(404).type('text').send('UNKNOWN');
    var out = [];
    out.push('ORDER ' + pl.order_ref);
    out.push('ZONE ' + (pl.zone || '-') + ' BAGS ' + pl.bags + (pl.high_value ? ' **CHECK**' : ''));
    out.push('VALUE ' + pl.value_label.replace('£', 'GBP '));
    pl.lines.forEach(function (l) {
      out.push((l.bin + '            ').slice(0, 12) + (l.sku + '          ').slice(0, 10) + ' x' + l.qty);
    });
    res.type('text').send(out.join('\n') + '\n');
  }, next);
});

module.exports = router;
