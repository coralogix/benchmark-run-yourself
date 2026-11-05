'use strict';

/*
 * `request` / `request-promise` client. DEPRECATED library (TD-2, PLAT-402,
 * "replace when we have time"), still used by payments (PSP + fraud) and the
 * orders -> inventory/slots/payments calls because that code was lifted
 * straight out of larder-fulfilment.
 *
 *   var client = requestClient('inventory');          // a peer name
 *   var client = requestClient(function () { return url; }); // or a base URL fn
 *   client.post('/internal/x', body, { timeout: 4000 })
 *     -> Promise<{ statusCode, body, headers }>
 *
 * Non-2xx responses RESOLVE (simple: false). Callers check statusCode.
 * Network errors / timeouts REJECT with request's error (err.cause.code is
 * ETIMEDOUT / ESOCKETTIMEDOUT / ECONNREFUSED ...).
 *
 * Default timeout is config http.timeoutMs (30s, "matches the mesh"). Pass
 * { timeout: 0 } for no timeout at all - request treats 0 as "never".
 */

var rp = require('request-promise');
var _ = require('lodash');
var config = require('../config');

function resolveBase(base) {
  if (typeof base === 'function') return String(base()).replace(/\/+$/, '');
  if (/^https?:\/\//.test(String(base))) return String(base).replace(/\/+$/, '');
  return config.peer(base);
}

function requestClient(base, defaults) {
  defaults = defaults || {};

  function call(method, path, body, opts) {
    opts = opts || {};
    var timeout = opts.timeout !== undefined ? opts.timeout
      : (defaults.timeout !== undefined ? defaults.timeout : config.get('http.timeoutMs', 30000));
    var req = {
      method: method,
      uri: resolveBase(base) + path,
      json: true,
      resolveWithFullResponse: true,
      simple: false,
      headers: _.assign({
        'User-Agent': config.get('http.userAgent', 'larder-ops/3') + ' (+request)'
      }, defaults.headers || {}, opts.headers || {})
    };
    if (timeout) req.timeout = timeout;
    if (body !== undefined && body !== null) req.body = body;
    if (opts.qs) req.qs = opts.qs;
    return rp(req).then(function (res) {
      return { statusCode: res.statusCode, body: res.body, headers: res.headers };
    });
  }

  return {
    get: function (path, opts) { return call('GET', path, null, opts); },
    post: function (path, body, opts) { return call('POST', path, body, opts); },
    patch: function (path, body, opts) { return call('PATCH', path, body, opts); },
    del: function (path, opts) { return call('DELETE', path, null, opts); },
    base: function () { return resolveBase(base); }
  };
}

// request's error codes for "the other side didn't answer / hung up"
function errCode(err) {
  if (!err) return null;
  if (err.cause && err.cause.code) return err.cause.code;
  if (err.error && err.error.code) return err.error.code;
  return err.code || null;
}

module.exports = requestClient;
module.exports.errCode = errCode;
module.exports.rp = rp;
