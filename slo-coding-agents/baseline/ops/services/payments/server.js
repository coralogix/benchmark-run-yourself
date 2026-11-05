'use strict';

/*
 * payments - PSP adapter + fraud pre-check.
 *
 *   node --require ./lib/telemetry/otel.js services/payments/server.js
 *
 * Data: $LARDER_DATA_DIR/payments.json (jdb).
 * Peers: FRAUD_URL (fraud), PSP_URL (psp-sim). Nothing calls back into us
 * except orders.
 */

var service = require('../../lib/service');
var config = require('../../lib/config');
var store = require('./store');
var psp = require('./psp');

module.exports = service.create({
  name: 'payments',
  collections: { payments: 'payment_id' },
  mount: function (app) {
    app.use('/internal', require('./routes/charges'));
  },
  debug: function () {
    return {
      payments: store.stats(),
      psp: {
        url: config.get('psp.url'),
        settings: psp.adapter._settings()
      }
    };
  }
});

if (require.main === module) module.exports.main();
