'use strict';

/*
 * fraud - rules-based risk scoring for payments.
 *
 *   node --require ./lib/telemetry/otel.js services/fraud/server.js
 *
 * "Stateless" (ARCHITECTURE.md). Except for the velocity history file,
 * $LARDER_DATA_DIR/fraud_history.json, see history.js.
 */

var fs = require('fs');
var service = require('../../lib/service');
var config = require('../../lib/config');
var history = require('./history');

module.exports = service.create({
  name: 'fraud',
  beforeListen: function () {
    // no jdb collections here, so nobody else creates the data dir
    fs.mkdirSync(config.get('data.dir'), { recursive: true });
  },
  mount: function (app) {
    app.use('/internal', require('./routes/score'));
  },
  debug: function () {
    return { history: history.snapshot() };
  }
});

if (require.main === module) module.exports.main();
