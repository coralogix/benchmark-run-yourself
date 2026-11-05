'use strict';

/*
 * slots - delivery zones, slots, holds.
 *
 *   node --require ./lib/telemetry/otel.js services/slots/server.js
 *
 * Data: $LARDER_DATA_DIR/{slots,holds,zones}.json (jdb).
 * Writes go through the "connection pool" (lib/pool.js, 16 connections,
 * 30s acquire timeout). The pool gauges (ops_db_pool_in_use /
 * ops_db_pool_waiting) are only meaningful here.
 *
 * Restarting this service resets the pool. (Ops runbook RB-14: "if slot holds
 * start timing out, restart slots". Nobody wrote down why it works.)
 */

var service = require('../../lib/service');
var db = require('../../lib/db');
var pool = require('../../lib/pool');
var seed = require('./seed');
var slots = require('./slots');
var holdSweeper = require('./jobs/holdSweeper');

var _ = require('lodash');

module.exports = service.create({
  name: 'slots',
  collections: { slots: 'slot_id', holds: 'hold_id', zones: 'zone' },
  seed: seed.spec,
  jobs: [holdSweeper],
  beforeListen: function () {
    pool.init(db.get());
  },
  mount: function (app) {
    app.use('/internal', require('./routes/zones'));
    app.use('/internal', require('./routes/slots'));
  },
  debug: function () {
    return {
      pool: _.omit(pool.get().stats(), 'connections'),
      holds: slots.holdStats(),
      sweeper: holdSweeper.status()
    };
  }
});

if (require.main === module) module.exports.main();
