'use strict';

/*
 * bus - the homegrown pub/sub broker (2023, p.ndlovu). Started first by the
 * supervisor (stage 1) because everything else publishes to it.
 *
 *   node --require ./lib/telemetry/otel.js services/bus/server.js
 *
 * Publishers: orders (order.confirmed), warehouse (order.dispatched).
 * Subscribers + delivery order: config/bus-subscriptions.json.
 * Delivery: services/bus/worker.js (one sequential worker per topic).
 * State: $LARDER_DATA_DIR/bus.json (services/bus/store.js).
 *
 * Why not Kafka/Rabbit/Redis: "we'd need to run it" (2023 design review).
 */

var service = require('../../lib/service');
var store = require('./store');
var subscriptions = require('./subscriptions');
var worker = require('./worker');

module.exports = service.create({
  name: 'bus',
  // no jdb collections - bus.json is managed by ./store
  mount: function (app) {
    app.use(require('./routes/publish'));
    app.use('/internal', require('./routes/admin'));
  },
  beforeListen: function () {
    subscriptions.load();
    store.load();
  },
  jobs: [worker.job],
  onShutdown: function () {
    worker.stopAll();
    store.save();
  },
  debug: function () {
    return { bus: store.snapshot(), workers: worker.statusAll() };
  }
});

if (require.main === module) module.exports.main();
