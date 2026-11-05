'use strict';

/*
 * Bus subscriptions. Source of truth: config/bus-subscriptions.json
 *
 *   { "<topic>": [ { name, url_env, peer, path }, ... ] }
 *
 * The ORDER of the array is the delivery order (see worker.js). The webhooks
 * team asked to be first in 2023 "because the partner SLA is tightest"; the
 * rest is the order people added themselves in.
 *
 * URL resolution, per delivery (so a changed env var needs a restart anyway):
 *   process.env[url_env]  ->  config peers.<peer>  ->  throw
 *
 * Keys starting with "_" are comments and are skipped.
 *
 * (p.ndlovu 2023-07, from the old hard-coded list in fulfilment's jobs/index.js)
 */

var fs = require('fs');
var path = require('path');
var _ = require('lodash');
var config = require('../../lib/config');
var logger = require('../../lib/logger');

var cached = null;

function file() {
  return path.join(config.CONFIG_DIR, config.get('bus.subscriptionsFile', 'bus-subscriptions.json'));
}

function normalise(raw) {
  var out = {};
  Object.keys(raw || {}).forEach(function (topic) {
    if (topic.charAt(0) === '_') return;
    var subs = raw[topic];
    if (!Array.isArray(subs)) {
      logger.warn('bus: subscriptions for topic are not a list, ignoring', { topic: topic });
      return;
    }
    out[topic] = subs.filter(function (s) { return s && s.name; }).map(function (s) {
      return {
        name: String(s.name),
        url_env: s.url_env || (String(s.name).toUpperCase().replace(/-/g, '_') + '_URL'),
        peer: s.peer || s.name,
        path: s.path || '/internal/events'
      };
    });
  });
  return out;
}

function load(fromFile) {
  var f = fromFile || file();
  var raw = JSON.parse(fs.readFileSync(f, 'utf8'));
  cached = normalise(raw);
  logger.info('bus: subscriptions loaded', {
    file: f,
    topics: _.mapValues(cached, function (subs) { return _.map(subs, 'name').join(','); })
  });
  return cached;
}

function all() {
  if (!cached) load();
  return cached;
}

function topics() {
  return Object.keys(all());
}

function forTopic(topic) {
  return all()[topic] || [];
}

// Base URL + path for a subscriber. Env first, then config peers.
function urlFor(sub) {
  var base = process.env[sub.url_env];
  if (!base) base = config.peer(sub.peer);
  return String(base).replace(/\/+$/, '') + sub.path;
}

// tests
function _set(map) {
  cached = normalise(map);
  return cached;
}

module.exports = {
  load: load,
  all: all,
  topics: topics,
  forTopic: forTopic,
  urlFor: urlFor,
  normalise: normalise,
  _set: _set
};
