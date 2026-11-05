'use strict';

/*
 * Calls the fraud service before every charge.
 *
 *   POST <FRAUD_URL>/internal/fraud/score
 *     { order_ref, email, user_id, amount_pence, token }
 *   -> { decision: "allow" | "review" | "block", score, reasons }
 *
 * Uses the shared request client WITHOUT a timeout of its own, so it gets
 * config http.timeoutMs (30s). Fraud "always answers in a few ms" (k.osei
 * 2022, when it was a function call inside fulfilment).
 *
 * Fail OPEN: if fraud is down / errors / returns garbage we treat the payment
 * as allowed (config fraud.failOpen, default true). Finance would rather eat
 * a chargeback than lose a basket. Decision "review" is also allowed through
 * - there is no review queue. (TODO(k.osei 2022-04-11): manual review queue)
 */

var requestClient = require('../../lib/http/request-client');
var config = require('../../lib/config');
var logger = require('../../lib/logger');

var client = null;

function getClient() {
  if (!client) client = requestClient('fraud', { timeout: config.get('fraud.timeoutMs', 30000) });
  return client;
}

function failOpenResult(reason) {
  return { decision: 'allow', score: 0, reasons: ['fail_open:' + reason], fail_open: true };
}

function score(params) {
  var body = {
    order_ref: params.order_ref,
    email: params.email || null,
    user_id: params.user_id || null,
    amount_pence: params.amount_pence,
    token: params.token
  };
  var started = Date.now();
  return getClient().post('/internal/fraud/score', body).then(function (res) {
    var took = Date.now() - started;
    if (res.statusCode !== 200 || !res.body || !res.body.decision) {
      logger.warn('fraud: unexpected response', { status: res.statusCode, order_ref: params.order_ref, took_ms: took });
      if (config.get('fraud.failOpen', true)) return failOpenResult('status_' + res.statusCode);
      var e = new Error('fraud returned ' + res.statusCode);
      e.fraudUnavailable = true;
      throw e;
    }
    if (took > 1000) {
      // started showing up in 2024; nobody has looked
      logger.info('fraud: slow score', { order_ref: params.order_ref, took_ms: took });
    }
    res.body.took_ms = took;
    return res.body;
  }, function (err) {
    logger.error('fraud: score call failed', { order_ref: params.order_ref, err: err.message });
    if (config.get('fraud.failOpen', true)) return failOpenResult('error');
    err.fraudUnavailable = true;
    throw err;
  });
}

module.exports = {
  score: score,
  _setClient: function (c) { client = c; }
};
