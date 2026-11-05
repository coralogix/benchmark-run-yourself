'use strict';

/*
 * orders - order lifecycle and total validation.
 *
 *   node --require ./lib/telemetry/otel.js services/orders/server.js
 *
 * Calls (via <NAME>_URL): inventory, tax, slots, payments, ledger, bus.
 * Data: $LARDER_DATA_DIR/orders.json (jdb, keyed on internal id).
 *
 * Also mounts the v1 order API from legacy/v1 (ops bookmarklet, resend,
 * admin flags) when config legacy.v1Enabled (it is).
 */

var config = require('../../lib/config');
var service = require('../../lib/service');
var store = require('./store');

module.exports = service.create({
  name: 'orders',
  collections: { orders: 'id' },
  mount: function (app) {
    app.use('/internal', require('./routes/orders'));
    app.use('/internal', require('./routes/debug'));
    if (config.get('legacy.v1Enabled')) {
      app.use('/v1', require('../../legacy/v1'));
    }
  },
  debug: function () {
    return { orders_by_status: store.counts() };
  }
});

if (require.main === module) module.exports.main();
