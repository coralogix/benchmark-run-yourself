'use strict';

var util = require('util');

function makeError(name, defaults) {
  function E(message, extra) {
    Error.captureStackTrace(this, E);
    this.name = name;
    this.message = message || defaults.message;
    this.status = defaults.status;
    this.code = defaults.code;
    if (extra) {
      for (var k in extra) this[k] = extra[k];
    }
  }
  util.inherits(E, Error);
  return E;
}

module.exports = {
  makeError: makeError,
  NotFound: makeError('NotFound', { status: 404, code: 'not_found', message: 'not found' }),
  BadRequest: makeError('BadRequest', { status: 400, code: 'bad_request', message: 'bad request' }),
  Conflict: makeError('Conflict', { status: 409, code: 'conflict', message: 'conflict' }),
  OutOfStock: makeError('OutOfStock', { status: 409, code: 'out_of_stock', message: 'out of stock' }),
  PriceMismatch: makeError('PriceMismatch', { status: 409, code: 'price_mismatch', message: 'price mismatch' }),
  SlotFull: makeError('SlotFull', { status: 409, code: 'slot_full', message: 'slot full' }),
  HoldExpired: makeError('HoldExpired', { status: 409, code: 'hold_expired', message: 'hold expired or unknown' }),
  PaymentDeclined: makeError('PaymentDeclined', { status: 402, code: 'payment_declined', message: 'payment declined' }),
  PaymentError: makeError('PaymentError', { status: 502, code: 'payment_error', message: 'payment provider error' }),
  UpstreamError: makeError('UpstreamError', { status: 502, code: 'upstream_error', message: 'upstream service error' }),
  PoolTimeout: makeError('PoolTimeout', { status: 500, code: 'pool_timeout', message: 'timed out waiting for a pool connection' })
};
