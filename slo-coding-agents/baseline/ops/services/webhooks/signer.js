'use strict';

/*
 * HMAC signing for partner webhooks (v2, unfinished - j.watts 2023-03).
 *
 * Partner expects:  X-Larder-Signature: sha256=<hex hmac of the raw body>
 *
 * The dispatcher does not call this yet (partner wasn't ready; partner-sim
 * accepts unsigned v1 bodies). The secret is
 * read from partner.webhook_secret in config/default.json.
 * (production.json has a webhooks.hmac_secret too - that one is not read by
 * anything, it was for the ops dashboard that never got built.)
 */

var crypto = require('crypto');
var config = require('../../lib/config');

var HEADER = 'X-Larder-Signature';

function secret() {
  return config.get('partner.webhook_secret');
}

function sign(rawBody, key) {
  key = key || secret();
  if (!key) return null;
  return 'sha256=' + crypto.createHmac('sha256', key).update(rawBody, 'utf8').digest('hex');
}

function verify(rawBody, header, key) {
  var expected = sign(rawBody, key);
  if (!expected || !header) return false;
  var a = Buffer.from(expected);
  var b = Buffer.from(String(header));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

module.exports = { HEADER: HEADER, sign: sign, verify: verify, secret: secret };
