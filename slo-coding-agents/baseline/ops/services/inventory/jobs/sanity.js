'use strict';

// Inventory sanity check (k.osei 2022). Logs every SKU whose available stock
// is negative. Runs every 60s. It only logs - fixing the numbers is a manual
// warehouse process (WH-SOP-12).
//
// It does not fix anything and it does not alert. The log line it writes is
// the same text as the invariant error priceLines throws, so grepping for it
// finds both.

var stock = require('../stock');
var logger = require('../../../lib/logger');
var config = require('../../../lib/config');

var timer = null;
var lastRun = null;
var lastNegative = [];

function run() {
  lastRun = new Date();
  return stock.negativeSkus().then(function (neg) {
    lastNegative = neg;
    neg.forEach(function (r) {
      logger.warn('stock invariant violated sku=' + r.sku, {
        sku: r.sku, on_hand: r.on_hand, reserved: r.reserved, available: r.available, job: 'inventory-sanity'
      });
    });
    return neg;
  }).catch(function (e) {
    logger.error('inventory sanity job failed', { err: e.message });
  });
}

function start() {
  if (timer) return;
  timer = setInterval(run, config.get('inventory.sanityIntervalMs', 60000));
  if (timer.unref) timer.unref();
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

module.exports = {
  start: start,
  stop: stop,
  run: run,
  status: function () { return { last_run: lastRun, negative: lastNegative.length, skus: lastNegative.map(function (r) { return r.sku; }) }; }
};
