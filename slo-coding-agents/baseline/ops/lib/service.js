'use strict';

/*
 * Shared service scaffolding. Every services/<name>/server.js does:
 *
 *   var service = require('../../lib/service');
 *   module.exports = service.create({
 *     name: 'inventory',
 *     collections: { inventory: 'sku' },     // jdb collections + keys
 *     seed: { inventory: { file: 'inventory', normalise: fn } },
 *     mount: function (app) { app.use('/internal', routes) },
 *     jobs: [sweeper],                       // { start(), stop() }
 *     beforeListen: function () { ... },     // may return a promise
 *     onShutdown: function () { ... },       // sync
 *     debug: function () { return {...} }    // merged into /internal/debug/vars
 *   });
 *   if (require.main === module) module.exports.main();
 *
 * Things that happen here that services don't mention:
 *   - lib/patches.js is applied (Date.toJSON, JSON.stringify, keepAlive)
 *   - GET /healthz, /internal/health and /internal/debug/vars are mounted
 *     BEFORE the service's routes (and before internalAuth)
 *   - the maintenance flag blocks non-GET requests with 503 when set
 *     (only reachable through the orders v1 admin route)
 *   - body limit is config app.bodyLimit (1mb). The bus can deliver bigger
 *     order payloads than that in theory. In practice no.
 *   - unknown routes -> 404 {"error":"not_found"}
 *   - unhandled errors -> Express's default HTML 500 (see middleware/errors)
 */

var Promise = require('bluebird');
var express = require('express');
var bodyParser = require('body-parser');
var _ = require('lodash');
var config = require('./config');
var logger = require('./logger');
var globals = require('./globals');
var patches = require('./patches');
var db = require('./db');
var bootstrap = require('./bootstrap');
var requestLog = require('./middleware/requestLog');
var internalAuth = require('./middleware/internalAuth');
var errorMw = require('./middleware/errors');
var runtime = require('./telemetry/runtime');

var seedIfEmpty = Promise.promisify(bootstrap.seedIfEmpty);

function create(spec) {
  if (!spec || !spec.name) throw new Error('service.create: name required');
  var name = spec.name;
  var server = null;
  var jobs = spec.jobs || [];

  // must happen before anything calls config.get() so {service} resolves
  config.setService(name);

  function buildApp() {
    patches.apply();
    var app = express();
    app.disable('x-powered-by');
    app.set('trust proxy', config.get('app.trustProxy'));
    app.set('json spaces', 0);

    app.use(requestLog());
    app.use(bodyParser.json({ limit: config.get('app.bodyLimit', '1mb') }));

    app.get('/healthz', function (req, res) {
      res.json({ ok: true, service: name });
    });
    // k8s-style alias from the 2021 migration that never happened
    app.get('/internal/health', function (req, res) {
      res.json({ ok: true, service: name, version: config.get('app.version'), ready: !!globals.ready });
    });

    app.get('/internal/debug/vars', function (req, res) {
      var extra = {};
      try {
        extra = spec.debug ? spec.debug() : {};
      } catch (e) {
        extra = { debug_error: e.message };
      }
      res.json(_.assign({
        service: name,
        started_at: globals.startedAt,
        uptime_s: Math.round(process.uptime()),
        counters: globals.counters,
        last_error: globals.lastError,
        flags: globals.flags,
        event_loop: runtime.snapshot(),
        memory: process.memoryUsage()
      }, extra));
    });

    // maintenance flag: set by orders' /v1/admin/maintenance. Only blocks writes.
    app.use(function (req, res, next) {
      if (globals.flags.maintenance && req.method !== 'GET' && req.path.indexOf('/v1/admin') !== 0) {
        return res.status(503).json({ error: 'maintenance' });
      }
      next();
    });

    app.use('/internal', internalAuth());

    if (typeof spec.mount === 'function') spec.mount(app);

    app.get('/', function (req, res) {
      res.type('text').send('larder ' + name + ' ' + config.get('app.version'));
    });

    app.use(function (req, res) {
      res.status(404).json({ error: 'not_found' });
    });

    app.use(errorMw.knownErrors());
    app.use(errorMw.logUnhandled());
    // no final handler: Express's default one renders the 500
    return app;
  }

  function openData() {
    if (!spec.collections) return Promise.resolve(null);
    var database = db.open(config.get('data.dir'), spec.collections);
    var seeding = spec.seed ? seedIfEmpty(database, spec.seed) : Promise.resolve(false);
    return seeding.then(function () { return db.loadAll(); }).then(function () { return database; });
  }

  function boot() {
    var cfg = config.get();
    globals.service = name;
    globals.version = cfg.app.version;
    globals.runId = cfg.app.runId || process.env.LARDER_RUN_ID || null;
    runtime.start();

    return openData()
      .then(function () {
        return spec.beforeListen ? spec.beforeListen() : null;
      })
      .then(function () {
        jobs.forEach(function (j) { j.start(); });
        var app = buildApp();
        return new Promise(function (resolve, reject) {
          server = app.listen(cfg.app.port, function () {
            globals.ready = true;
            logger.info(name + ' listening', {
              service: name,
              port: cfg.app.port,
              data_dir: cfg.data.dir,
              env: cfg.env,
              version: cfg.app.version,
              run_id: globals.runId
            });
            resolve(server);
          });
          server.on('error', reject);
          // mesh keeps connections alive for 60s; be longer than that
          server.keepAliveTimeout = cfg.app.keepAliveTimeoutMs || 65000;
          server.headersTimeout = server.keepAliveTimeout + 1000;
        });
      });
  }

  function shutdown(signal) {
    if (globals.shuttingDown) return;
    globals.shuttingDown = true;
    logger.info(name + ' shutting down', { signal: signal });
    jobs.forEach(function (j) {
      try { j.stop(); } catch (e) { /* ignore */ }
    });
    runtime.stop();
    if (spec.onShutdown) {
      try {
        spec.onShutdown();
      } catch (e) {
        logger.error('shutdown hook failed', { err: e.message });
      }
    }
    try {
      if (globals.db) globals.db.flushAllSync();
    } catch (e) {
      logger.error('shutdown: db flush failed', { err: e.message });
    }
    if (server) server.close();
    var otel = global.__LARDER_OTEL__;
    var flush = otel ? otel.shutdown() : Promise.resolve();
    Promise.resolve(flush).timeout(6000).catch(function () {}).then(function () {
      process.exit(0);
    });
  }

  function main() {
    process.on('SIGTERM', function () { shutdown('SIGTERM'); });
    process.on('SIGINT', function () { shutdown('SIGINT'); });
    process.on('unhandledRejection', function (reason) {
      logger.error('unhandled rejection', { err: reason && reason.message ? reason.message : String(reason) });
    });
    return boot().catch(function (err) {
      logger.error(name + ' failed to start', { err: err.message, stack: err.stack });
      setTimeout(function () { process.exit(1); }, 200);
    });
  }

  return {
    name: name,
    buildApp: buildApp,
    boot: boot,
    shutdown: shutdown,
    main: main,
    server: function () { return server; }
  };
}

module.exports = { create: create };
