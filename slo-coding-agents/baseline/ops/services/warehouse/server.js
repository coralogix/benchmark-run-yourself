'use strict';

/*
 * warehouse - pick lists + dispatch (bus subscriber).
 *
 *   node --require ./lib/telemetry/otel.js services/warehouse/server.js
 *
 * In:  bus order.confirmed on POST /internal/events; POST /internal/dispatch/:ref
 * Out: orders (GET + PATCH status), inventory (dispatches), bus (order.dispatched)
 * State: $LARDER_DATA_DIR/warehouse.json
 */

var service = require('../../lib/service');
var busClient = require('../../lib/bus-client');
var handlers = require('./handlers');
var picklists = require('./picklists');

var collections = {};
collections[picklists.COLLECTION] = 'order_ref';

module.exports = service.create({
  name: 'warehouse',
  collections: collections,
  mount: function (app) {
    app.use(busClient.subscriber({
      'order.confirmed': handlers['order.confirmed']
    }, { name: 'warehouse' }));
    app.use('/internal', require('./routes/dispatch'));
    app.use('/internal', require('./routes/pickLists'));
  },
  debug: function () {
    return { pick_lists: picklists.stats() };
  }
});

if (require.main === module) module.exports.main();
