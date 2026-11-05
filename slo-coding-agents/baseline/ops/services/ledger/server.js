'use strict';

/*
 * ledger - accounting journal.
 *
 *   node --require ./lib/telemetry/otel.js services/ledger/server.js
 *
 * Data: $LARDER_DATA_DIR/ledger.json (plain JSON array, see journal.js).
 * Written by: orders (POST /internal/ledger/entries) and the bus
 * (order.confirmed backfill, see events.js).
 */

var fs = require('fs');
var service = require('../../lib/service');
var config = require('../../lib/config');
var busClient = require('../../lib/bus-client');
var journal = require('./journal');
var events = require('./events');

module.exports = service.create({
  name: 'ledger',
  beforeListen: function () {
    fs.mkdirSync(config.get('data.dir'), { recursive: true });
    journal.load();
  },
  mount: function (app) {
    app.use(busClient.subscriber(events, { name: 'ledger' }));
    app.use('/internal', require('./routes/entries'));
  },
  debug: function () {
    return { ledger: journal.balance() };
  }
});

if (require.main === module) module.exports.main();
