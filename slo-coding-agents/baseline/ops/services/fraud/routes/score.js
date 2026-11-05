'use strict';

/*
 *   POST /internal/fraud/score  { order_ref, email, user_id, amount_pence, token }
 *     200 { decision: "allow"|"review"|"block", score, reasons, velocity, ruleset }
 *
 *   GET  /internal/fraud/rules     the rule list (for the ops wiki page)
 *   GET  /internal/fraud/history/stats
 *
 * Missing fields are scored, not rejected (a 400 from fraud would fail the
 * payment, and payments fails OPEN on errors anyway, so why bother).
 */

var express = require('express');
var rules = require('../rules');
var history = require('../history');
var telemetry = require('../../../lib/telemetry');
var globals = require('../../../lib/globals');
var logger = require('../../../lib/logger');

var router = express.Router();

router.post('/fraud/score', function (req, res, next) {
  var body = req.body || {};
  var result;
  try {
    result = rules.score(body, {
      req_id: req.id,
      user_agent: req.headers['user-agent'],
      traceparent: req.headers.traceparent
    });
  } catch (e) {
    return next(e);
  }
  globals.counters.fraudScored++;
  telemetry.inc('fraudDecisions', { decision: result.decision });
  telemetry.setAttr('larder.fraud.decision', result.decision);
  if (result.decision !== 'allow') {
    logger.info('fraud: ' + result.decision, { order_ref: body.order_ref, score: result.score, reasons: result.reasons });
  }
  res.json(result);
});

router.get('/fraud/rules', function (req, res) {
  res.json({
    ruleset: rules.RULESET_VERSION,
    review_score: rules.REVIEW_SCORE,
    block_score: rules.BLOCK_SCORE,
    rules: rules.RULES.map(function (r) { return { id: r.id, description: r.description }; })
  });
});

router.get('/fraud/history/stats', function (req, res) {
  res.json(history.snapshot());
});

module.exports = router;
