'use strict';

/*
 * Nightly stock feed import (v1). The warehouse used to drop a CSV into the
 * data dir:
 *
 *   sku,on_hand
 *   SKU-0001,120
 *
 * and we'd overwrite on_hand. The warehouse moved to the partner API in 2021
 * and stopped sending the file, but this still polls for it every 5 minutes.
 * Since the 2023 split it runs inside the inventory service.
 * If you drop a file in, it WILL be applied (and then renamed to .done).
 */

var fs = require('fs');
var path = require('path');
var async = require('async');
var config = require('../lib/config');
var db = require('../lib/db');
var logger = require('../lib/logger');

var timer = null;

function feedPath() {
  return path.join(config.get('data.dir'), config.get('legacy.stockFeedFile', 'stock_feed.csv'));
}

function parseCsv(raw) {
  return raw.split(/\r?\n/).slice(1).filter(Boolean).map(function (line) {
    var parts = line.split(',');
    return { sku: parts[0].trim(), on_hand: parseInt(parts[1], 10) };
  }).filter(function (r) { return r.sku && !isNaN(r.on_hand); });
}

function runOnce(cb) {
  cb = cb || function () {};
  var file = feedPath();
  async.waterfall([
    function (next) {
      fs.access(file, fs.constants.R_OK, function (err) {
        if (err) return next('nofile');
        next();
      });
    },
    function (next) {
      fs.readFile(file, 'utf8', next);
    },
    function (raw, next) {
      var rows = parseCsv(raw);
      var col = db.collection('inventory');
      async.eachLimit(rows, 5, function (row, done) {
        col.get(row.sku, function (err, rec) {
          if (err) return done(err);
          if (!rec) return done();
          rec.on_hand = row.on_hand;
          col.put(rec, function (e) { done(e); });
        });
      }, function (err) {
        next(err, rows.length);
      });
    },
    function (n, next) {
      fs.rename(file, file + '.done', function (err) { next(err, n); });
    }
  ], function (err, n) {
    if (err === 'nofile') return cb(null, 0);
    if (err) {
      logger.error('stock sync failed', { err: err.message || err });
      return cb(err);
    }
    logger.info('stock sync applied', { rows: n });
    cb(null, n);
  });
}

function start() {
  if (timer) return;
  timer = setInterval(function () { runOnce(); }, config.get('legacy.stockSyncIntervalMs', 300000));
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

module.exports = { start: start, stop: stop, runOnce: runOnce, parseCsv: parseCsv };
