'use strict';

/*
 * inventory - stock levels, reservations, server-side unit prices.
 *
 *   node --require ./lib/telemetry/otel.js services/inventory/server.js
 *
 * Data: $LARDER_DATA_DIR/inventory.json (jdb), audit.log (append only).
 * Jobs: sanity check (60s), legacy stock feed import (5 min, v1 only).
 * Also mounts the v1 warehouse scanner routes at /v1/stock.
 *
 * Unit prices live HERE and not in catalog because in 2018 the stock-take
 * spreadsheet was the price list. Commerce's catalog has its own copy of the
 * prices; orders validates totals against THESE (via tax).
 */

var service = require('../../lib/service');
var config = require('../../lib/config');
var seed = require('./seed');
var stock = require('./stock');
var audit = require('./audit');
var sanity = require('./jobs/sanity');
var stockSync = require('../../legacy/stockSync');

var legacyJob = {
  start: function () { if (config.get('legacy.v1Enabled')) stockSync.start(); },
  stop: function () { stockSync.stop(); }
};

module.exports = service.create({
  name: 'inventory',
  collections: { inventory: 'sku' },
  seed: seed.spec,
  jobs: [sanity, legacyJob],
  mount: function (app) {
    app.use('/internal', require('./routes/stock'));
    app.use('/internal', require('./routes/reservations'));
    if (config.get('legacy.v1Enabled')) {
      app.use('/v1/stock', function (req, res, next) {
        res.setHeader('Deprecation', 'true');
        res.setHeader('Sunset', 'Sat, 31 Dec 2022 23:59:59 GMT');
        next();
      }, require('../../legacy/v1/stock'));
    }
  },
  debug: function () {
    return {
      audit: audit.stats(),
      inventory_sanity: sanity.status(),
      totals: stock.totals()
    };
  }
});

if (require.main === module) module.exports.main();
