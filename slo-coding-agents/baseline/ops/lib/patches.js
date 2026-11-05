'use strict';

/*
 * Runtime patches. Applied by lib/service.js before any routes are built, so
 * EVERY ops service gets them (they started in fulfilment; the split copied
 * them everywhere "to keep behaviour identical").
 *
 * 1. Date.prototype.toJSON
 *    The v1 partner integration wanted "YYYY-MM-DD HH:mm:ss" timestamps.
 *    Rather than fix the serializer we patched Date globally (2018, m.okafor).
 *    It is gated on globals.flags.legacyDateFormat, which the v1 admin route
 *    (orders service only) can flip at runtime. Everything else expects
 *    ISO-8601.
 *
 * 2. JSON.stringify
 *    BigInt support for the 2021 ledger experiment. The experiment is gone,
 *    the patch isn't. NOTE: this makes every JSON.stringify call without a
 *    replacer go through a JS function per key. (Noticed by t.berg 2022 in a
 *    flame graph, "not worth fixing".)
 *
 * 3. http.globalAgent keepAlive
 *    "Fixes" ECONNRESET from the PSP load balancer. Probably doesn't.
 *
 * 4. Promise
 *    global.Promise is NOT replaced with bluebird (it was, 2018-2020; removed
 *    because of the async_hooks / OTel context loss). Some files still
 *    `var Promise = require('bluebird')` locally - those are bluebird.
 */

var http = require('http');
var moment = require('moment');
var globals = require('./globals');

var applied = false;

function apply() {
  if (applied) return;
  applied = true;

  var originalToJSON = Date.prototype.toJSON;
  // eslint-disable-next-line no-extend-native
  Date.prototype.toJSON = function () {
    if (globals.flags.legacyDateFormat) {
      if (isNaN(this.getTime())) return null;
      return moment.utc(this).format('YYYY-MM-DD HH:mm:ss');
    }
    return originalToJSON.apply(this, arguments);
  };
  Date.prototype.toJSON.__larderPatched = true;

  var originalStringify = JSON.stringify;
  JSON.stringify = function (value, replacer, space) {
    if (replacer == null) {
      replacer = function (k, v) {
        return typeof v === 'bigint' ? v.toString() : v;
      };
      return originalStringify.call(JSON, value, replacer, space);
    }
    return originalStringify.apply(JSON, arguments);
  };
  JSON.stringify.__larderPatched = true;

  http.globalAgent.keepAlive = true;
}

function isApplied() {
  return applied;
}

module.exports = { apply: apply, isApplied: isApplied };
