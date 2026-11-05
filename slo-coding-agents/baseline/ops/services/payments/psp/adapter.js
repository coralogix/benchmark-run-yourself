'use strict';

/*
 * PSP adapter - talks to the payment service provider.
 *
 * Uses `request-promise` (request is deprecated, see TD-2 / PLAT-402, "replace
 * when we have time"). The PSP API:
 *
 *   POST /v1/charges  { amount_pence, currency, order_ref, token }
 *     201 { charge_id, status: "captured", amount_pence }
 *     402 { error: "card_declined" }
 *   POST /v1/refunds  { charge_id, amount_pence }
 *     201 { refund_id }
 *
 * Retry policy: the PSP's load balancer drops idle connections and sometimes
 * just doesn't answer, so we retry timeouts / resets a few times. Charges are
 * safe to retry because the PSP de-duplicates on order_ref.
 *   ^ NOTE(a.mensah 2023-05): is that true? Their docs mention an
 *     Idempotency-Key header, not order_ref. Need to check with them.
 *
 * Timeout comes from config psp.timeoutMs (config/local.js sets 4000, which
 * wins over PSP_TIMEOUT_MS in the env because local.js is applied last).
 *
 * Moved from larder-fulfilment lib/psp/ in the 2023 split, unchanged apart
 * from require paths (p.ndlovu: "don't touch the money code during a split").
 */

var rp = require('request-promise');
var _ = require('lodash');
var config = require('../../../lib/config');
var logger = require('../../../lib/logger');
var sleep = require('../../../lib/util/sleep');
var errors = require('../../../lib/errors');
var instruments = require('../../../lib/telemetry').instruments;

var DEFAULT_TIMEOUT_MS = 4000;
var RETRY_DELAY_MS = 250;

// request's error codes for "the other side didn't answer / hung up"
var RETRYABLE = ['ETIMEDOUT', 'ESOCKETTIMEDOUT', 'ECONNRESET', 'ECONNREFUSED', 'EPIPE'];

function baseUrl() {
  return String(config.get('psp.url')).replace(/\/+$/, '');
}

function settings() {
  return {
    timeout: config.get('psp.timeoutMs') || DEFAULT_TIMEOUT_MS,
    retries: config.get('psp.retries') != null ? config.get('psp.retries') : 3,
    delay: config.get('psp.retryDelayMs') || RETRY_DELAY_MS
  };
}

function errCode(err) {
  if (!err) return null;
  if (err.cause && err.cause.code) return err.cause.code;
  if (err.error && err.error.code) return err.error.code;
  return err.code || null;
}

function outcomeFor(err) {
  var code = errCode(err);
  if (code === 'ETIMEDOUT' || code === 'ESOCKETTIMEDOUT') return 'timeout';
  if (code === 'ECONNRESET' || code === 'EPIPE') return 'connection_reset';
  if (code === 'ECONNREFUSED') return 'connection_refused';
  return 'error';
}

function post(path, body, timeout) {
  return rp({
    method: 'POST',
    uri: baseUrl() + path,
    body: body,
    json: true,
    timeout: timeout,
    resolveWithFullResponse: true,
    simple: false,
    headers: {
      'User-Agent': 'larder-payments/3 (+request)',
      'X-Merchant-Id': config.get('psp.merchantId')
    }
  });
}

/**
 * charge({ amount_pence, order_ref, token }) -> { charge_id, status, amount_pence, attempts }
 * Throws PaymentDeclined (402) or PaymentError (502).
 */
async function charge(params) {
  var s = settings();
  var body = {
    amount_pence: params.amount_pence,
    currency: config.get('psp.currency', 'GBP'),
    order_ref: params.order_ref,
    token: params.token
  };
  var maxAttempts = 1 + s.retries;
  var lastErr = null;

  for (var attempt = 1; attempt <= maxAttempts; attempt++) {
    var started = Date.now();
    try {
      var res = await post('/v1/charges', body, s.timeout);
      var took = Date.now() - started;

      if (res.statusCode === 201 || res.statusCode === 200) {
        instruments.inc('paymentAttempts', { outcome: 'captured', attempt: String(attempt) });
        logger.info('psp: charge captured', {
          order_ref: params.order_ref, charge_id: res.body && res.body.charge_id, attempt: attempt, took_ms: took
        });
        return {
          charge_id: res.body.charge_id,
          status: res.body.status || 'captured',
          amount_pence: res.body.amount_pence,
          attempts: attempt
        };
      }

      if (res.statusCode === 402) {
        instruments.inc('paymentAttempts', { outcome: 'declined', attempt: String(attempt) });
        logger.info('psp: charge declined', { order_ref: params.order_ref, attempt: attempt });
        throw new errors.PaymentDeclined('card declined', { pspBody: res.body });
      }

      // 4xx/5xx from the PSP: don't retry, they told us no
      instruments.inc('paymentAttempts', { outcome: 'http_' + res.statusCode, attempt: String(attempt) });
      logger.error('psp: unexpected status', {
        order_ref: params.order_ref, status: res.statusCode, body: _.truncate(JSON.stringify(res.body), { length: 300 }), attempt: attempt
      });
      throw new errors.PaymentError('psp returned ' + res.statusCode, { pspStatus: res.statusCode });
    } catch (err) {
      if (err instanceof errors.PaymentDeclined || err instanceof errors.PaymentError) throw err;
      lastErr = err;
      var code = errCode(err);
      instruments.inc('paymentAttempts', { outcome: outcomeFor(err), attempt: String(attempt) });
      if (RETRYABLE.indexOf(code) !== -1 && attempt < maxAttempts) {
        logger.warn('psp: charge attempt failed, retrying', {
          order_ref: params.order_ref, attempt: attempt, code: code, took_ms: Date.now() - started
        });
        await sleep(s.delay);
        continue;
      }
      logger.error('psp: charge failed', {
        order_ref: params.order_ref, attempt: attempt, code: code, err: err.message
      });
      break;
    }
  }

  throw new errors.PaymentError('payment failed after retries', {
    cause: lastErr && lastErr.message,
    attempts: maxAttempts
  });
}

/**
 * refund(charge_id, amount_pence) - not wired to any route yet (cancellations
 * are "next quarter" since 2022). Same retry policy as charge.
 */
async function refund(chargeId, amountPence, ctx) {
  var s = settings();
  var maxAttempts = 1 + s.retries;
  var lastErr;
  for (var attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      var res = await post('/v1/refunds', { charge_id: chargeId, amount_pence: amountPence }, s.timeout);
      if (res.statusCode === 201 || res.statusCode === 200) {
        return { refund_id: res.body.refund_id, attempts: attempt };
      }
      throw new errors.PaymentError('refund failed with ' + res.statusCode, { pspStatus: res.statusCode });
    } catch (err) {
      if (err instanceof errors.PaymentError) throw err;
      lastErr = err;
      if (RETRYABLE.indexOf(errCode(err)) !== -1 && attempt < maxAttempts) {
        await sleep(s.delay);
        continue;
      }
      break;
    }
  }
  logger.error('psp: refund failed', { charge_id: chargeId, err: lastErr && lastErr.message, order_ref: ctx && ctx.order_ref });
  throw new errors.PaymentError('refund failed after retries');
}

module.exports = {
  charge: charge,
  refund: refund,
  RETRYABLE: RETRYABLE,
  _settings: settings
};
