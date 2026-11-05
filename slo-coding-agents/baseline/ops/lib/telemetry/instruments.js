// OWNED BY PLATFORM TEAM - do not modify without #platform approval
//
// Application metric instruments for ops services. Names are part of the
// platform contract (BUILD_SPEC section 2; dashboards + SLO alerts depend on
// them). Do NOT add `unit` - Coralogix appends it to the name.
//
// Every service gets every instrument (it's cheap; an unused counter exports
// nothing). Services identify themselves via the service.name resource
// attribute, NOT via a label.
//
//   counters: ops_orders{outcome} ops_reservations{outcome}
//             ops_payment_attempts{outcome,attempt} ops_fraud_decisions{decision}
//             ops_webhook_deliveries{outcome} ops_bus_deliveries{subscriber,outcome}
//   gauges:   ops_webhook_queue_depth ops_db_pool_in_use ops_db_pool_waiting
//             ops_event_loop_lag_seconds ops_bus_queue_depth{topic}
'use strict';

var api = require('@opentelemetry/api');

var otel = global.__LARDER_OTEL__;
var scope = 'larder-ops/' + (otel && otel.serviceName ? otel.serviceName() : (process.env.LARDER_SERVICE || 'ops'));

var meter = api.metrics.getMeter(scope, '1.0.0');

var counters = {
  orders: meter.createCounter('ops_orders', {
    description: 'Order create requests by outcome'
  }),
  reservations: meter.createCounter('ops_reservations', {
    description: 'Inventory reservation operations by outcome'
  }),
  paymentAttempts: meter.createCounter('ops_payment_attempts', {
    description: 'PSP charge attempts by outcome and attempt number'
  }),
  fraudDecisions: meter.createCounter('ops_fraud_decisions', {
    description: 'Fraud scoring decisions'
  }),
  webhookDeliveries: meter.createCounter('ops_webhook_deliveries', {
    description: 'Partner webhook delivery attempts by outcome'
  }),
  busDeliveries: meter.createCounter('ops_bus_deliveries', {
    description: 'Bus deliveries to subscribers by outcome'
  })
};

var gauges = {
  webhookQueueDepth: meter.createObservableGauge('ops_webhook_queue_depth', {
    description: 'Items currently in the partner webhook queue'
  }),
  poolInUse: meter.createObservableGauge('ops_db_pool_in_use', {
    description: 'Connections checked out of the slots pool'
  }),
  poolWaiting: meter.createObservableGauge('ops_db_pool_waiting', {
    description: 'Callers waiting to acquire a pool connection'
  }),
  eventLoopLag: meter.createObservableGauge('ops_event_loop_lag_seconds', {
    description: 'Max event loop delay observed since the previous collection'
  }),
  busQueueDepth: meter.createObservableGauge('ops_bus_queue_depth', {
    description: 'Undelivered messages per bus topic'
  })
};

// name -> fn() returning a number, or (for labelled gauges) an array of
// [value, attrs] pairs
var sources = {};

function observe(name, fn) {
  sources[name] = fn;
}

function safe(fn) {
  try {
    var v = fn();
    return typeof v === 'number' && isFinite(v) ? v : 0;
  } catch (e) {
    return 0;
  }
}

['webhookQueueDepth', 'poolInUse', 'poolWaiting', 'eventLoopLag'].forEach(function (name) {
  gauges[name].addCallback(function (r) {
    if (sources[name]) r.observe(safe(sources[name]));
  });
});

gauges.busQueueDepth.addCallback(function (r) {
  if (!sources.busQueueDepth) return;
  var pairs;
  try {
    pairs = sources.busQueueDepth() || [];
  } catch (e) {
    return;
  }
  pairs.forEach(function (p) {
    if (p && typeof p[0] === 'number') r.observe(p[0], p[1] || {});
  });
});

function inc(counter, attrs, n) {
  try {
    counters[counter].add(n || 1, attrs || {});
  } catch (e) {
    // never let metrics break a request
  }
}

module.exports = {
  meter: meter,
  tracer: api.trace.getTracer(scope),
  counters: counters,
  gauges: gauges,
  observe: observe,
  inc: inc,
  _sources: sources
};
