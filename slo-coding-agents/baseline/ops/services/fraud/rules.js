'use strict';

/*
 * Fraud rules engine (k.osei 2022, after the vendor contract ended).
 *
 * Each rule looks at (signals, velocity, cfg) and returns null (no match) or
 * { weight, block?, detail }. Score = sum of weights. Decision:
 *
 *   block   if any matching rule has block: true, or score >= BLOCK_SCORE
 *   review  if score >= REVIEW_SCORE
 *   allow   otherwise
 *
 * payments only acts on "block". "review" goes through like "allow" (there is
 * no review queue, see payments/fraudClient.js).
 *
 * Rule order matters only for the `reasons` array (and the trace that ends up
 * in fraud_history.json).
 */

var _ = require('lodash');
var config = require('../../lib/config');
var signals = require('./signals');
var history = require('./history');

var REVIEW_SCORE = 50;
var BLOCK_SCORE = 100;
var RULESET_VERSION = '2022.11-r7';

function cfg() {
  return {
    blockedDomains: config.get('fraud.blockedDomains', []),
    reviewAmountPence: config.get('fraud.reviewAmountPence', 25000),
    blockAmountPence: config.get('fraud.blockAmountPence', 500000),
    velocityReviewAt: config.get('fraud.velocityReviewAt', 12),
    velocityBlockAt: config.get('fraud.velocityBlockAt', 40),
    velocityWindowMinutes: config.get('fraud.velocityWindowMinutes', 60)
  };
}

var RULES = [
  {
    id: 'blocked_domain',
    description: 'email domain on the block list',
    run: function (s, v, c) {
      if (s.email_domain && c.blockedDomains.indexOf(s.email_domain) !== -1) {
        return { weight: 100, block: true, detail: s.email_domain };
      }
      return null;
    }
  },
  {
    id: 'test_token',
    description: 'PSP test token that means "treat as fraud"',
    run: function (s) {
      // tok_fraud is what QA uses; tok_test_* was the vendor's sandbox
      if (s.token_is_test) return { weight: 100, block: true, detail: s.token_prefix };
      return null;
    }
  },
  {
    id: 'amount_xl',
    description: 'amount above the hard limit',
    run: function (s, v, c) {
      if (s.amount_pence > c.blockAmountPence) return { weight: 100, block: true, detail: s.amount_pence };
      return null;
    }
  },
  {
    id: 'amount_large',
    description: 'large basket',
    run: function (s, v, c) {
      if (s.amount_pence >= c.reviewAmountPence) return { weight: 30, detail: s.amount_pence };
      return null;
    }
  },
  {
    id: 'velocity_review',
    description: 'many payments from the same customer in the window',
    run: function (s, v, c) {
      if (v.count >= c.velocityReviewAt) return { weight: 40, detail: v.count + ' in ' + c.velocityWindowMinutes + 'm' };
      return null;
    }
  },
  {
    id: 'velocity_block',
    description: 'card-testing levels of velocity',
    run: function (s, v, c) {
      if (v.count >= c.velocityBlockAt) return { weight: 100, block: true, detail: v.count };
      return null;
    }
  },
  {
    id: 'repeat_offender',
    description: 'blocked before',
    run: function (s, v) {
      // 2023-01: was block:true, which locked out a customer forever after one
      // bad afternoon (CS-4410). Now just a nudge.
      if (v.blocked_before > 0) return { weight: 20, detail: v.blocked_before };
      return null;
    }
  },
  {
    id: 'missing_email',
    description: 'no email supplied',
    run: function (s) {
      if (!s.email_normalised) return { weight: 10, detail: null };
      return null;
    }
  },
  {
    id: 'numeric_local_part',
    description: 'email local part is mostly digits',
    run: function (s) {
      if (s.email_digits_ratio > 0.6) return { weight: 5, detail: s.email_digits_ratio };
      return null;
    }
  },
  {
    id: 'night_owl',
    description: '01:00-05:00 local',
    run: function (s) {
      if (s.hour_local >= 1 && s.hour_local < 5) return { weight: 5, detail: s.hour_local };
      return null;
    }
  }
  // { id: 'ip_country', ... }  removed with the vendor SDK (no IPs any more)
];

function decide(score, blocked) {
  if (blocked || score >= BLOCK_SCORE) return 'block';
  if (score >= REVIEW_SCORE) return 'review';
  return 'allow';
}

/**
 * evaluate(sig, velocity) -> { decision, score, reasons, trace }
 * Pure; no I/O. score() below does the history part.
 */
function evaluate(sig, velocity) {
  var c = cfg();
  var score = 0;
  var blocked = false;
  var reasons = [];
  var trace = RULES.map(function (rule) {
    var started = process.hrtime();
    var hit = null;
    try {
      hit = rule.run(sig, velocity, c);
    } catch (e) {
      hit = null;
    }
    var d = process.hrtime(started);
    if (hit) {
      score += hit.weight;
      if (hit.block) blocked = true;
      reasons.push(rule.id);
    }
    return {
      rule: rule.id,
      description: rule.description,
      matched: !!hit,
      weight: hit ? hit.weight : 0,
      block: !!(hit && hit.block),
      detail: hit ? hit.detail : null,
      config: _.pick(c, ['velocityReviewAt', 'velocityBlockAt', 'velocityWindowMinutes', 'reviewAmountPence']),
      eval_ns: d[0] * 1e9 + d[1]
    };
  });
  return { decision: decide(score, blocked), score: score, reasons: reasons, trace: trace };
}

/**
 * score(body) -> { decision, score, reasons, velocity }
 *
 * Synchronous on purpose (see history.js header).
 */
function score(body, meta) {
  meta = meta || {};
  var now = new Date();
  var sig = signals.extract(body, now);
  var hist = history.load();
  var velocity = history.count(hist, sig, now);
  var result = evaluate(sig, velocity);

  history.append(hist, {
    id: hist.length + 1,
    scored_at: now.toISOString(),
    email: body.email || null,
    order_ref: sig.order_ref,
    decision: result.decision,
    score: result.score,
    reasons: result.reasons,
    ruleset: RULESET_VERSION,
    // full snapshot for the dispute process (FRAUD-31)
    request: _.pick(body, ['order_ref', 'email', 'user_id', 'amount_pence']),
    request_meta: {
      req_id: meta.req_id || null,
      user_agent: meta.user_agent || null,
      traceparent: meta.traceparent || null,
      received_at: now.toISOString()
    },
    signals: sig,
    velocity: velocity,
    // FRAUD-49 (2025-02): disputes wanted the customer's recent history next
    // to each decision without a second lookup
    recent: history.recentFor(hist, sig.user_key, 10),
    trace: result.trace
  });

  return {
    decision: result.decision,
    score: result.score,
    reasons: result.reasons,
    velocity: { count: velocity.count, window_minutes: cfg().velocityWindowMinutes },
    ruleset: RULESET_VERSION
  };
}

module.exports = {
  score: score,
  evaluate: evaluate,
  decide: decide,
  RULES: RULES,
  REVIEW_SCORE: REVIEW_SCORE,
  BLOCK_SCORE: BLOCK_SCORE,
  RULESET_VERSION: RULESET_VERSION
};
