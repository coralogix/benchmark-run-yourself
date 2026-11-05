'use strict';

/*
 * axios@0.21 client factory. Used by the bus (deliveries), webhooks
 * (partner) and orders (tax, ledger, bus publish).
 *
 *   var client = axiosClient('tax');   // peer name, resolved per request
 *   client.post('/internal/tax/totals', body).then(function (res) { ... })
 *
 * Things this does that you might not expect:
 *   - validateStatus accepts EVERYTHING below 500. A 404 or 409 resolves;
 *     callers must look at res.status. 5xx rejects (err.response is set).
 *   - GET requests that fail with a network error are retried
 *     config http.getRetries times (2) via vendor/retry-it. POSTs are not.
 *     ECONNRESET is retried regardless (retry-it LARDER patch, INC-1187).
 *   - default timeout is config http.timeoutMs (30s) unless opts.timeout.
 *
 * (t.berg 2021, moved to lib/ in the 2023 split)
 */

var axios = require('axios');
var _ = require('lodash');
var config = require('../config');
var retryIt = require('../../vendor/retry-it');

function resolveBase(base) {
  if (typeof base === 'function') return String(base()).replace(/\/+$/, '');
  if (/^https?:\/\//.test(String(base))) return String(base).replace(/\/+$/, '');
  return config.peer(base);
}

function axiosClient(base, opts) {
  opts = opts || {};
  var instance = axios.create({
    timeout: opts.timeout !== undefined ? opts.timeout : config.get('http.timeoutMs', 30000),
    headers: _.assign({
      'Content-Type': 'application/json',
      'User-Agent': config.get('http.userAgent', 'larder-ops/3') + ' (+axios)'
    }, opts.headers || {}),
    validateStatus: opts.validateStatus || function (s) { return s < 500; },
    maxContentLength: 50 * 1024 * 1024
  });

  function url(path) {
    return resolveBase(base) + path;
  }

  return {
    instance: instance,
    get: function (path, cfg) {
      return retryIt(function () {
        return instance.get(url(path), cfg);
      }, {
        retries: config.get('http.getRetries', 2),
        minTimeout: 50,
        retryOn: function (err) { return !err.response; }
      });
    },
    post: function (path, body, cfg) { return instance.post(url(path), body, cfg); },
    patch: function (path, body, cfg) { return instance.patch(url(path), body, cfg); },
    // for callers that already have an absolute URL (bus subscribers)
    postUrl: function (absUrl, body, cfg) { return instance.post(absUrl, body, cfg); },
    base: function () { return resolveBase(base); }
  };
}

function isTimeout(err) {
  return !!(err && (err.code === 'ECONNABORTED' || /timeout/i.test(String(err.message))));
}

module.exports = axiosClient;
module.exports.isTimeout = isTimeout;
