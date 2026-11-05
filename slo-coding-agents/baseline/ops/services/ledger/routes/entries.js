'use strict';

/*
 *   POST /internal/ledger/entries
 *     { order_ref, kind, amount_pence, charge_id, payment_id, user_id, meta }
 *     201 { entry_id, seq }   400 { error: "bad_request", message }
 *
 *   GET  /internal/ledger/entries?order_ref=&kind=&limit=    (oldest first)
 *   GET  /internal/ledger/balance
 *
 *   GET  /internal/ledger/export.csv   (finance; added 2021, still used monthly)
 */

var express = require('express');
var journal = require('../journal');
var money = require('../../../lib/money');

var router = express.Router();

router.post('/ledger/entries', function (req, res, next) {
  journal.append(req.body || {}, function (err, entry) {
    if (err) {
      if (err.status === 400) return res.status(400).json({ error: 'bad_request', message: err.message });
      return next(err);
    }
    res.status(201).json({ entry_id: entry.entry_id, seq: entry.seq });
  });
});

router.get('/ledger/entries', function (req, res) {
  var limit = parseInt(req.query.limit, 10);
  if (!(limit > 0)) limit = 100;
  if (limit > 1000) limit = 1000;
  var list = journal.find({ order_ref: req.query.order_ref, kind: req.query.kind }, limit);
  res.json({ entries: list, count: list.length });
});

router.get('/ledger/balance', function (req, res) {
  var b = journal.balance();
  b.balance_display = money.formatGBP(b.balance_pence);
  res.json(b);
});

router.get('/ledger/export.csv', function (req, res) {
  var rows = journal.find({}, 0).map(function (e) {
    // pounds with 2dp - finance's Excel template wants pounds
    return [e.seq, e.created_at, e.kind, e.order_ref || '', (e.amount_pence / 100).toFixed(2), e.charge_id || '', e.source].join(',');
  });
  res.type('text/csv').send(['seq,created_at,kind,order_ref,amount_gbp,charge_id,source'].concat(rows).join('\n'));
});

module.exports = router;
