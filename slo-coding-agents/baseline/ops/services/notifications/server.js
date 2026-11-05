'use strict';

/*
 * notifications - customer emails (bus subscriber).
 *
 *   node --require ./lib/telemetry/otel.js services/notifications/server.js
 *
 * In:  bus deliveries on POST /internal/events
 * Out: EMAIL_URL/v1/messages (mailer.js, 10s timeout, no retry)
 * State: $LARDER_DATA_DIR/notifications.json
 */

var service = require('../../lib/service');
var busClient = require('../../lib/bus-client');
var handlers = require('./handlers');
var store = require('./store');

var collections = {};
collections[store.COLLECTION] = 'id';

module.exports = service.create({
  name: 'notifications',
  collections: collections,
  mount: function (app) {
    app.use(busClient.subscriber({
      'order.confirmed': handlers['order.confirmed'],
      'order.dispatched': handlers['order.dispatched']
    }, { name: 'notifications' }));
    app.use('/internal', require('./routes/notifications'));
  },
  debug: function () {
    return { notifications: store.stats() };
  }
});

if (require.main === module) module.exports.main();
