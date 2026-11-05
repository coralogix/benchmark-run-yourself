'use strict';

/*
 * node-fetch@2 helpers. Used by warehouse and notifications (the "newer"
 * services, 2022-2023, which were written by people who liked fetch).
 *
 *   fetchJson(url, { method, body, timeout }) -> Promise<{ status, body }>
 *
 * - body objects are JSON encoded.
 * - non-JSON responses come back as { status, body: null, text }.
 * - timeout uses node-fetch's own `timeout` option (ms). Default 0 = NONE.
 *   That default is on purpose ("let the mesh time it out", 2022). It
 *   means a caller that forgets to pass one waits as long as the mesh lets it.
 * - never retries.
 */

var fetch = require('node-fetch');
var _ = require('lodash');
var config = require('../config');

function fetchJson(url, opts) {
  opts = opts || {};
  var init = {
    method: opts.method || 'GET',
    headers: _.assign({
      Accept: 'application/json',
      'User-Agent': config.get('http.userAgent', 'larder-ops/3') + ' (+fetch)'
    }, opts.headers || {}),
    timeout: opts.timeout || 0
  };
  if (opts.body !== undefined) {
    init.body = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
    init.headers['Content-Type'] = 'application/json';
  }
  return fetch(url, init).then(function (res) {
    return res.text().then(function (text) {
      var body = null;
      if (text) {
        try {
          body = JSON.parse(text);
        } catch (e) {
          body = null;
        }
      }
      return { status: res.status, body: body, text: text };
    });
  });
}

function peerUrl(name, path) {
  return config.peer(name) + (path || '');
}

module.exports = {
  fetchJson: fetchJson,
  peerUrl: peerUrl,
  fetch: fetch
};
