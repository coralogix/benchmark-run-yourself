'use strict';

/*
 * tax - canonical totals + VAT. Stateless.
 *
 *   node --require ./lib/telemetry/otel.js services/tax/server.js
 *
 * The cheapest service we run. It still boots the whole shared lib (config,
 * patches, jdb is skipped because there are no collections).
 */

var service = require('../../lib/service');

module.exports = service.create({
  name: 'tax',
  mount: function (app) {
    app.use('/internal', require('./routes/totals'));
    app.use('/internal', require('./routes/vat'));
  }
});

if (require.main === module) module.exports.main();
