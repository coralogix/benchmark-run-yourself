'use strict';

var logger = require('../logger');
var globals = require('../globals');

/*
 * Error handling.
 *
 * Known (HTTP-ish) errors with a .status < 500 are turned into JSON. Anything
 * else is logged and passed on to Express's default handler, which renders
 * the HTML 500 page. (We tried a JSON 500 handler in 2020; the storefront's
 * error parsing depended on the HTML. Don't ask.) Upstream errors (502) get
 * JSON because the commerce checkout parses `error`.
 */
function knownErrors() {
  // eslint-disable-next-line no-unused-vars
  return function (err, req, res, next) {
    if (err && err.status && err.code && (err.status < 500 || err.status === 502)) {
      var body = { error: err.code };
      if (err.sku) body.sku = err.sku;
      if (err.status >= 400 && err.code === 'bad_request') body.message = err.message;
      return res.status(err.status).json(body);
    }
    if (err && err.type === 'entity.parse.failed') {
      return res.status(400).json({ error: 'bad_json' });
    }
    next(err);
  };
}

function logUnhandled() {
  return function (err, req, res, next) {
    globals.lastError = { at: new Date().toISOString(), message: err && err.message, path: req.originalUrl };
    logger.error(err && err.message ? err.message : String(err), {
      path: req.originalUrl,
      method: req.method,
      req_id: req.id,
      err: err
    });
    next(err);
  };
}

module.exports = { knownErrors: knownErrors, logUnhandled: logUnhandled };
