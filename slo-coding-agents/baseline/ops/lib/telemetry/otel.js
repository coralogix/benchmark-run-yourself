// OWNED BY PLATFORM TEAM - do not modify without #platform approval
//
// OpenTelemetry bootstrap for every ops service. Loaded with `node --require`
// BEFORE any application code so the http/express patches apply:
//
//   node --require ./lib/telemetry/otel.js services/<name>/server.js
//
// Configuration comes ONLY from the standard OTEL_* environment variables:
//   OTEL_EXPORTER_OTLP_ENDPOINT   (unset => telemetry disabled, app runs normally)
//   OTEL_EXPORTER_OTLP_HEADERS
//   OTEL_EXPORTER_OTLP_PROTOCOL   (we only ship http/protobuf exporters)
//   OTEL_SERVICE_NAME
//   OTEL_RESOURCE_ATTRIBUTES
//   OTEL_METRIC_EXPORT_INTERVAL   (ms, default 10000)
//
// Resource attributes required by the Coralogix contract (service.namespace,
// cx.application.name, cx.subsystem.name, larder.repo, bench.run_id) are
// filled in below when the supervisor did not already send them in
// OTEL_RESOURCE_ATTRIBUTES. Env wins.
//
// Propagation: NodeSDK defaults to W3C tracecontext + baggage. Do not add B3,
// the commerce repo (Python) only speaks W3C.
//
// Instruments must NEVER set `unit` (Coralogix appends it to the metric name).
//
// History:
//   2023-06 platform  copied from larder-fulfilment/telemetry/otel.js for the split
//   2024-02 platform  resource defaults (PLAT-512)
'use strict';

var state = {
  enabled: false,
  sdk: null,
  shutdownPromise: null,
  serviceName: null
};

function shutdown() {
  if (state.shutdownPromise) return state.shutdownPromise;
  if (!state.sdk) {
    state.shutdownPromise = Promise.resolve();
    return state.shutdownPromise;
  }
  var guard = new Promise(function (resolve) {
    var t = setTimeout(resolve, 5000);
    if (t.unref) t.unref();
  });
  state.shutdownPromise = Promise.race([
    state.sdk.shutdown().catch(function (e) {
      // eslint-disable-next-line no-console
      console.error('[otel] shutdown error', e && e.message);
    }),
    guard
  ]);
  return state.shutdownPromise;
}

function guessServiceName() {
  if (process.env.OTEL_SERVICE_NAME) return process.env.OTEL_SERVICE_NAME;
  if (process.env.LARDER_SERVICE) return process.env.LARDER_SERVICE;
  // services/<name>/server.js
  var main = process.argv[1] || '';
  var m = main.replace(/\\/g, '/').match(/services\/([^/]+)\/server(\.js)?$/);
  return m ? m[1] : 'ops-unknown';
}

state.serviceName = guessServiceName();

module.exports = {
  isEnabled: function () { return state.enabled; },
  serviceName: function () { return state.serviceName; },
  shutdown: shutdown
};
global.__LARDER_OTEL__ = module.exports;

if (!process.env.OTEL_EXPORTER_OTLP_ENDPOINT) {
  // No collector configured: the @opentelemetry/api globals stay as no-ops.
  return;
}

var NodeSDK = require('@opentelemetry/sdk-node').NodeSDK;
var resources = require('@opentelemetry/resources');
var OTLPTraceExporter = require('@opentelemetry/exporter-trace-otlp-proto').OTLPTraceExporter;
var OTLPMetricExporter = require('@opentelemetry/exporter-metrics-otlp-proto').OTLPMetricExporter;
var OTLPLogExporter = require('@opentelemetry/exporter-logs-otlp-proto').OTLPLogExporter;
var PeriodicExportingMetricReader = require('@opentelemetry/sdk-metrics').PeriodicExportingMetricReader;
var BatchLogRecordProcessor = require('@opentelemetry/sdk-logs').BatchLogRecordProcessor;
var HttpInstrumentation = require('@opentelemetry/instrumentation-http').HttpInstrumentation;
var ExpressInstrumentation = require('@opentelemetry/instrumentation-express').ExpressInstrumentation;
var otelApi = require('@opentelemetry/api');
var diag = otelApi.diag;
var DiagConsoleLogger = otelApi.DiagConsoleLogger;
var DiagLogLevel = otelApi.DiagLogLevel;

if (process.env.OTEL_LOG_LEVEL && DiagLogLevel[process.env.OTEL_LOG_LEVEL.toUpperCase()] !== undefined) {
  diag.setLogger(new DiagConsoleLogger(), DiagLogLevel[process.env.OTEL_LOG_LEVEL.toUpperCase()]);
}

var exportInterval = parseInt(process.env.OTEL_METRIC_EXPORT_INTERVAL || '10000', 10);
if (!(exportInterval > 0)) exportInterval = 10000;

function parseResourceEnv(raw) {
  var out = {};
  String(raw || '').split(',').forEach(function (pair) {
    var idx = pair.indexOf('=');
    if (idx <= 0) return;
    var k = pair.slice(0, idx).trim();
    var v = pair.slice(idx + 1).trim();
    try { v = decodeURIComponent(v); } catch (e) { /* keep raw */ }
    if (k) out[k] = v;
  });
  return out;
}

// Defaults only. Anything in OTEL_RESOURCE_ATTRIBUTES / OTEL_SERVICE_NAME wins
// because NodeSDK merges its env detector over this.
var fromEnv = parseResourceEnv(process.env.OTEL_RESOURCE_ATTRIBUTES);
var defaults = {
  'service.name': state.serviceName,
  'service.namespace': 'larder',
  'deployment.environment': 'bench',
  'cx.application.name': 'larder',
  'cx.subsystem.name': state.serviceName,
  'larder.repo': 'ops'
};
if (process.env.LARDER_RUN_ID) defaults['bench.run_id'] = process.env.LARDER_RUN_ID;
Object.keys(fromEnv).forEach(function (k) { delete defaults[k]; });

var sdk = new NodeSDK({
  resource: resources.resourceFromAttributes(defaults),
  traceExporter: new OTLPTraceExporter(),
  metricReader: new PeriodicExportingMetricReader({
    exporter: new OTLPMetricExporter(),
    exportIntervalMillis: exportInterval,
    exportTimeoutMillis: Math.min(exportInterval, 30000)
  }),
  // sdk-logs >= 0.200 takes an options object, not the exporter (platform 2025-11)
  logRecordProcessors: [new BatchLogRecordProcessor({ exporter: new OTLPLogExporter() })],
  instrumentations: [
    new HttpInstrumentation({
      // the mesh health-checks every service once a second
      ignoreIncomingRequestHook: function (req) {
        return req.url === '/healthz' || req.url === '/internal/health';
      }
    }),
    new ExpressInstrumentation()
  ]
});

try {
  sdk.start();
  state.sdk = sdk;
  state.enabled = true;
} catch (e) {
  // eslint-disable-next-line no-console
  console.error('[otel] failed to start, continuing without telemetry:', e && e.message);
}

process.once('SIGTERM', function () {
  shutdown().then(function () { process.exit(0); });
});
