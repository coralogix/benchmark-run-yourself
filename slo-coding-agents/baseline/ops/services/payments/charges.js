'use strict';

/*
 * Charge orchestration:
 *
 *   validate -> payment record (pending) -> fraud score -> PSP charge
 *            -> payment record (captured | declined | failed | blocked)
 *
 * Returns { status, body } like the old fulfilment orders.createOrder did, so
 * the route is a one-liner. Known failures are returned; anything unexpected
 * throws and becomes an HTML 500 (see lib/middleware/errors.js).
 *
 * There is NO de-duplication on order_ref here. "orders makes sure it only
 * charges once per order" (it doesn't, see services/orders). Two calls with
 * the same order_ref create two payment records and two PSP charges.
 */

var _ = require('lodash');
var errors = require('../../lib/errors');
var logger = require('../../lib/logger');
var config = require('../../lib/config');
var telemetry = require('../../lib/telemetry');
var psp = require('./psp');
var fraudClient = require('./fraudClient');
var store = require('./store');

// PSP minimum charge is 30p (their docs). Our minimum basket is way above
// that, so this only catches bugs.
var MIN_CHARGE_PENCE = 30;
// £5,000. Above that the PSP wants 3DS, which we never built.
var MAX_CHARGE_PENCE = 500000;

function validate(body) {
  if (!body || typeof body !== 'object') throw new errors.BadRequest('body required');
  if (!body.order_ref) throw new errors.BadRequest('order_ref required');
  var amount = Number(body.amount_pence);
  if (!(amount > 0) || Math.floor(amount) !== amount) throw new errors.BadRequest('amount_pence must be a positive integer');
  if (amount < MIN_CHARGE_PENCE) throw new errors.BadRequest('amount below PSP minimum');
  if (amount > MAX_CHARGE_PENCE) throw new errors.BadRequest('amount above PSP maximum');
  if (!body.token) throw new errors.BadRequest('token required');
}

async function charge(body) {
  validate(body);
  var orderRef = String(body.order_ref);
  telemetry.setAttr('larder.order_ref', orderRef);

  var rec = await store.create({
    order_ref: orderRef,
    user_id: body.user_id,
    email: body.email,
    amount_pence: body.amount_pence,
    currency: config.get('psp.currency', 'GBP'),
    token: body.token
  });

  // 1. fraud
  var verdict;
  try {
    verdict = await fraudClient.score({
      order_ref: orderRef,
      email: body.email,
      user_id: body.user_id,
      amount_pence: body.amount_pence,
      token: body.token
    });
  } catch (e) {
    // only when fraud.failOpen=false
    await store.update(rec, { status: store.STATUS.FAILED, failure: 'fraud_unavailable' });
    return { status: 502, body: { error: 'payment_error', reason: 'fraud_unavailable' } };
  }
  rec.fraud = _.pick(verdict, ['decision', 'score', 'reasons', 'fail_open']);
  telemetry.setAttr('larder.fraud.decision', verdict.decision);

  if (verdict.decision === 'block') {
    await store.update(rec, { status: store.STATUS.BLOCKED, failure: 'fraud' });
    logger.info('payments: blocked by fraud', { order_ref: orderRef, reasons: verdict.reasons, score: verdict.score });
    return { status: 402, body: { error: 'payment_declined', reason: 'fraud' } };
  }

  // 2. PSP (retries live inside the adapter)
  var result;
  try {
    result = await psp.charge({
      amount_pence: body.amount_pence,
      order_ref: orderRef,
      token: body.token
    });
  } catch (e) {
    if (e instanceof errors.PaymentDeclined) {
      await store.update(rec, { status: store.STATUS.DECLINED, failure: 'card_declined', attempts: 1 });
      return { status: 402, body: { error: 'payment_declined', reason: 'card_declined' } };
    }
    if (e instanceof errors.PaymentError) {
      await store.update(rec, {
        status: store.STATUS.FAILED,
        failure: e.cause || e.message,
        attempts: e.attempts || null
      });
      return { status: 502, body: { error: 'payment_error' } };
    }
    await store.update(rec, { status: store.STATUS.FAILED, failure: e.message });
    throw e;
  }

  await store.update(rec, {
    status: store.STATUS.CAPTURED,
    charge_id: result.charge_id,
    attempts: result.attempts
  });

  return {
    status: 201,
    body: {
      payment_id: rec.payment_id,
      charge_id: result.charge_id,
      status: 'captured',
      amount_pence: result.amount_pence != null ? result.amount_pence : rec.amount_pence,
      attempts: result.attempts
    }
  };
}

module.exports = {
  charge: charge,
  validate: validate,
  MIN_CHARGE_PENCE: MIN_CHARGE_PENCE,
  MAX_CHARGE_PENCE: MAX_CHARGE_PENCE
};
