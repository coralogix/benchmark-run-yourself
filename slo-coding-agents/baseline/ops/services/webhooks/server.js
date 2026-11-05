'use strict';

/*
 * webhooks - partner webhook dispatcher (bus subscriber).
 *
 *   node --require ./lib/telemetry/otel.js services/webhooks/server.js
 *
 * In:  bus deliveries on POST /internal/events (order.confirmed, order.dispatched)
 * Out: POST PARTNER_WEBHOOK_URL (schema v1, unsigned) from dispatcher.js
 * State: $LARDER_DATA_DIR/webhook_queue.json
 *
 * Secrets: partner.webhook_secret (default.json) is what signer.js would use.
 * webhooks.hmac_secret (production.json) is a second copy that nothing reads.
 * Neither is used at the moment because nothing signs.
 */

var service = require('../../lib/service');
var busClient = require('../../lib/bus-client');
var dispatcher = require('./dispatcher');
var handlers = require('./handlers');

module.exports = service.create({
  name: 'webhooks',
  mount: function (app) {
    app.use(busClient.subscriber({
      'cart.updated': handlers['cart.updated'],
      'order.confirmed': handlers['order.confirmed'],
      'order.dispatched': handlers['order.dispatched']
    }, { name: 'webhooks' }));
    app.use('/internal', require('./routes/queue'));
  },
  // dispatcher.start() loads the persisted queue and starts the tick timer
  jobs: [dispatcher],
  onShutdown: function () {
    dispatcher.persist();
  },
  debug: function () {
    return { webhooks: dispatcher.snapshot(), handled: handlers.stats() };
  }
});

if (require.main === module) module.exports.main();
