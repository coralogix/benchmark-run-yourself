'use strict';

// Process-wide mutable state. Yes, really. Several modules read/write this
// directly; the debug routes dump it. (r.singh 2019: "temporary")
//
// After the 2023 split every service process has its own copy, but most of
// the counters only make sense in one service. Nobody cleaned them up.
var g = global.__larder = global.__larder || {
  startedAt: new Date(),
  service: null,
  version: null,
  runId: null,
  db: null,
  ready: false,
  shuttingDown: false,
  counters: {
    ordersCreated: 0,
    ordersFailed: 0,
    holdsCreated: 0,
    holdsRejected: 0,
    reservations: 0,
    webhooksSent: 0,
    webhooksFailed: 0,
    busPublished: 0,
    busDelivered: 0,
    busRetries: 0,
    emailsSent: 0,
    emailsFailed: 0,
    fraudScored: 0,
    ledgerAppends: 0
  },
  lastOrderRef: null,
  lastError: null,
  timers: [],
  // service-specific scratch space; see each service's server.js
  extra: {},
  flags: {
    // flipped by the legacy admin route; only the orders app checks it
    maintenance: false,
    // see lib/patches.js
    legacyDateFormat: false
  }
};

module.exports = g;
