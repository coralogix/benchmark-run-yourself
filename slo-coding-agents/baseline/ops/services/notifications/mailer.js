'use strict';

/*
 * Email provider client.
 *
 *   POST EMAIL_URL/v1/messages { to, template, data } -> 202 { message_id }
 *
 * - timeout: config email.timeoutMs (10s; default.json says 10000 and
 *   config/local.js sets it again "in case").
 * - NO retry. The provider "guarantees delivery once accepted" and a retry
 *   after a timeout sends the customer two emails (INC-2188, 2022). So a
 *   timeout = no email, and the failure is only recorded in
 *   notifications.json.
 * - never rejects: resolves { ok, status, message_id, error, took_ms }.
 *
 * The caller (handlers.js) waits for this before acking the bus, so the
 * provider's latency is the bus's latency for every topic we subscribe to.
 */

var config = require('../../lib/config');
var fetchClient = require('../../lib/http/fetch-client');
var logger = require('../../lib/logger');
var globals = require('../../lib/globals');

var DEFAULT_TIMEOUT_MS = 10000;
var SLOW_WARN_MS = 3000;

function baseUrl() {
  return String(config.get('email.url') || 'http://127.0.0.1:8092').replace(/\/+$/, '');
}

function send(msg) {
  var started = Date.now();
  var timeout = config.get('email.timeoutMs') || DEFAULT_TIMEOUT_MS;
  var body = { to: msg.to, template: msg.template, data: msg.data || {} };
  return fetchClient.fetchJson(baseUrl() + '/v1/messages', {
    method: 'POST',
    body: body,
    timeout: timeout,
    headers: { 'X-Larder-From': config.get('email.from', 'orders@larder.example') }
  }).then(function (res) {
    var took = Date.now() - started;
    if (took > SLOW_WARN_MS) logger.warn('email: slow provider', { took_ms: took, template: msg.template });
    if (res.status >= 200 && res.status < 300) {
      globals.counters.emailsSent++;
      return {
        ok: true,
        status: res.status,
        message_id: res.body && res.body.message_id ? res.body.message_id : null,
        took_ms: took
      };
    }
    globals.counters.emailsFailed++;
    logger.warn('email: provider rejected message', { status: res.status, template: msg.template, body: res.text && res.text.slice(0, 200) });
    return { ok: false, status: res.status, error: 'http_' + res.status, took_ms: took };
  }, function (err) {
    globals.counters.emailsFailed++;
    var code = err.type === 'request-timeout' ? 'timeout' : (err.code || err.type || 'error');
    logger.error('email: send failed', { err: err.message, code: code, template: msg.template, took_ms: Date.now() - started });
    return { ok: false, status: null, error: code, took_ms: Date.now() - started };
  });
}

module.exports = { send: send, baseUrl: baseUrl };
