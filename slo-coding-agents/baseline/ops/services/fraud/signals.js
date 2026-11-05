'use strict';

/*
 * Feature extraction for the rules engine. "Signals" in the 2021 fraud
 * vendor's vocabulary (we ran their SDK for four months, FRAUD-12; this file
 * is what was left when we stopped paying for it).
 *
 * Every signal ends up in the scored record that the velocity check persists
 * (history.js), because the vendor wanted the "full feature vector" for model
 * training. Nobody trains anything any more.
 */

var crypto = require('crypto');
var moment = require('moment');
var _ = require('lodash');

// free-mail providers. Only used for the `freemail` signal, which no rule
// reads since the 2022 rules rewrite.
var FREEMAIL = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com', 'icloud.com', 'proton.me'];

function normaliseEmail(email) {
  if (!email) return '';
  var e = String(email).trim().toLowerCase();
  var at = e.lastIndexOf('@');
  if (at < 1) return e;
  var local = e.slice(0, at);
  var domain = e.slice(at + 1);
  // gmail-style "+tag" addresses count as the same person
  local = local.split('+')[0];
  if (domain === 'gmail.com' || domain === 'googlemail.com') local = local.replace(/\./g, '');
  return local + '@' + domain;
}

function emailDomain(email) {
  var e = normaliseEmail(email);
  var at = e.lastIndexOf('@');
  return at === -1 ? '' : e.slice(at + 1);
}

function digitsRatio(s) {
  if (!s) return 0;
  var d = String(s).replace(/[^0-9]/g, '').length;
  return Math.round((d / String(s).length) * 1000) / 1000;
}

function amountBand(pence) {
  var p = Number(pence) || 0;
  if (p < 1000) return 'micro';
  if (p < 4000) return 'small';
  if (p < 10000) return 'medium';
  if (p < 25000) return 'large';
  return 'xl';
}

// The vendor's model input: 64 floats in [0,1). We derive them from a hash of
// the signals so the shape stays the same for the (never started) in-house
// model. Persisted with every record "for training" (FRAUD-12).
var FEATURE_VECTOR_SIZE = 64;

function featureVector(seed) {
  var out = [];
  var h = crypto.createHash('sha512').update(String(seed)).digest();
  for (var i = 0; i < FEATURE_VECTOR_SIZE; i++) {
    var a = h[i % h.length];
    var b = h[(i * 7 + 3) % h.length];
    out.push((a * 256 + b) / 65536 + i / 1e9);
  }
  return out;
}

function fingerprint(parts) {
  return crypto.createHash('sha1').update(parts.join('|')).digest('hex');
}

/**
 * extract(body, now) -> signals object (flat-ish, JSON-friendly)
 */
function extract(body, now) {
  body = body || {};
  var m = moment(now || undefined);
  var email = normaliseEmail(body.email);
  var local = email.split('@')[0] || '';
  var domain = emailDomain(email);
  return {
    email_normalised: email,
    email_domain: domain,
    email_local_length: local.length,
    email_digits_ratio: digitsRatio(local),
    email_has_plus: String(body.email || '').indexOf('+') !== -1,
    freemail: FREEMAIL.indexOf(domain) !== -1,
    user_id: body.user_id || null,
    user_key: email || (body.user_id ? 'uid:' + body.user_id : 'anon'),
    amount_pence: Number(body.amount_pence) || 0,
    amount_band: amountBand(body.amount_pence),
    amount_round_pounds: (Number(body.amount_pence) || 0) % 100 === 0,
    token_prefix: body.token ? String(body.token).slice(0, 4) : null,
    token_is_test: body.token ? /^tok_(fraud|test)/.test(String(body.token)) : false,
    hour_local: m.hour(),
    weekday: m.isoWeekday(),
    weekend: m.isoWeekday() >= 6,
    month: m.format('YYYY-MM'),
    order_ref: body.order_ref ? String(body.order_ref) : null,
    order_ref_length: body.order_ref ? String(body.order_ref).length : 0,
    device_fingerprint: fingerprint([email, body.user_id || '', body.token ? String(body.token).slice(0, 4) : '']),
    feature_vector: featureVector([email, body.user_id, body.amount_pence, body.order_ref].join('|')),
    // vendor SDK fields, always the same now
    sdk_version: 'none',
    ip_country: 'GB',
    ip_risk: 0,
    extracted_at: m.toISOString()
  };
}

module.exports = {
  extract: extract,
  normaliseEmail: normaliseEmail,
  emailDomain: emailDomain,
  digitsRatio: digitsRatio,
  amountBand: amountBand,
  featureVector: featureVector,
  FREEMAIL: FREEMAIL,
  _pick: _.pick
};
