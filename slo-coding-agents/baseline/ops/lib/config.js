'use strict';

/*
 * Config loader (shared by every ops service).
 *
 * Precedence (lowest -> highest), as actually implemented below:
 *   1. config/default.json
 *   2. config/production.json   (only when NODE_ENV=production)
 *   3. environment variables     (only the ones mapped in ENV_MAP / URL_ENV)
 *   4. config/local.js           (always, if present - yes, after env)
 *
 * The README says env vars win. They mostly do, unless local.js touches the
 * same key (psp.*, bus.deliveryTimeoutMs, webhooks.tickMs, ...).
 *
 * Service discovery: every peer is `<NAME>_URL` in the env (upper case, '-'
 * -> '_'), mapped onto peers.<name> with '-' -> '_'. default.json has dev
 * defaults pointing at the mesh ports.
 *
 * "{service}" in data.dir is replaced with the service name (LARDER_SERVICE,
 * or whatever lib/service.js was told). LARDER_DATA_DIR replaces the whole
 * thing.
 */

var fs = require('fs');
var path = require('path');
var _ = require('lodash');

var ROOT = path.join(__dirname, '..');
var CONFIG_DIR = path.join(ROOT, 'config');

function bool(v) {
  return v !== '0' && v !== 'false' && v !== 'no' && v !== '';
}

var ENV_MAP = {
  PORT: ['app.port', Number],
  LARDER_SERVICE: ['app.service', String],
  LARDER_DATA_DIR: ['data.dir', String],
  LARDER_RUN_ID: ['app.runId', String],
  PSP_URL: ['psp.url', String],
  PSP_TIMEOUT_MS: ['psp.timeoutMs', Number],
  PARTNER_WEBHOOK_URL: ['partner.url', String],
  EMAIL_URL: ['email.url', String],
  EMAIL_TIMEOUT_MS: ['email.timeoutMs', Number],
  LOG_LEVEL: ['log.level', String],
  WEBHOOK_TICK_MS: ['webhooks.tickMs', Number],
  BUS_DELIVERY_TIMEOUT_MS: ['bus.deliveryTimeoutMs', Number],
  FRAUD_FAIL_OPEN: ['fraud.failOpen', bool],
  // PARTNER_WEBHOOK_SECRET was used by the old ansible deploy. Not mapped any
  // more; the secret comes from partner.webhook_secret. (j.watts 2022)
  LEGACY_V1: ['legacy.v1Enabled', bool]
};

// Any env var ending in _URL that isn't in ENV_MAP is a peer.
var URL_SUFFIX = /_URL$/;
var NOT_PEERS = ['PSP_URL', 'PARTNER_WEBHOOK_URL', 'EMAIL_URL'];

var cached = null;
var serviceHint = null;

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    if (e.code === 'ENOENT') return {};
    throw e;
  }
}

function load(env, opts) {
  env = env || process.env;
  opts = opts || {};
  var dir = opts.configDir || CONFIG_DIR;
  var cfg = readJson(path.join(dir, 'default.json'));

  if (env.NODE_ENV === 'production') {
    cfg = _.merge(cfg, readJson(path.join(dir, 'production.json')));
  }

  Object.keys(ENV_MAP).forEach(function (k) {
    if (env[k] !== undefined && env[k] !== '') {
      _.set(cfg, ENV_MAP[k][0], ENV_MAP[k][1](env[k]));
    }
  });

  cfg.peers = cfg.peers || {};
  Object.keys(env).forEach(function (k) {
    if (!URL_SUFFIX.test(k) || NOT_PEERS.indexOf(k) !== -1) return;
    if (!env[k]) return;
    var name = k.replace(URL_SUFFIX, '').toLowerCase();
    cfg.peers[name] = String(env[k]).replace(/\/+$/, '');
  });

  var localPath = path.join(dir, 'local.js');
  if (!opts.skipLocal && fs.existsSync(localPath)) {
    var local = require(localPath);
    if (typeof local === 'function') cfg = local(cfg, env) || cfg;
    else cfg = _.merge(cfg, local);
  }

  var svc = cfg.app.service || opts.service || serviceHint || 'ops';
  cfg.app.service = svc;
  if (!cfg.app.port && cfg.ports && cfg.ports[svc]) cfg.app.port = cfg.ports[svc];
  cfg.data.dir = path.resolve(process.cwd(), String(cfg.data.dir).replace('{service}', svc));
  cfg.env = env.NODE_ENV || 'development';
  return cfg;
}

function get(keyPath, dflt) {
  if (!cached) cached = load();
  if (!keyPath) return cached;
  var v = _.get(cached, keyPath);
  return v === undefined ? dflt : v;
}

// Base URL for a peer service (no trailing slash). Names use '_' or '-'.
function peer(name) {
  var key = String(name).replace(/-/g, '_').toLowerCase();
  var url = get('peers.' + key);
  if (!url) throw new Error('no URL configured for peer ' + name + ' (set ' + key.toUpperCase() + '_URL)');
  return String(url).replace(/\/+$/, '');
}

// lib/service.js calls this before anything reads config so "{service}"
// resolves even when LARDER_SERVICE isn't set (dev).
function setService(name) {
  serviceHint = name;
  if (cached && (!process.env.LARDER_SERVICE)) cached = null;
}

// tests poke at this
function reload(env, opts) {
  cached = load(env, opts);
  return cached;
}

function set(keyPath, value) {
  if (!cached) cached = load();
  _.set(cached, keyPath, value);
}

module.exports = get;
module.exports.get = get;
module.exports.peer = peer;
module.exports.set = set;
module.exports.reload = reload;
module.exports.load = load;
module.exports.setService = setService;
module.exports.ENV_MAP = ENV_MAP;
module.exports.ROOT = ROOT;
module.exports.CONFIG_DIR = CONFIG_DIR;
