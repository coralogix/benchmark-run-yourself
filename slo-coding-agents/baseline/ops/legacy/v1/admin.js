'use strict';

/*
 * v1 admin. Used by ops runbooks (RB-7 "partner missing events", RB-9
 * "legacy date format"). No auth - it's "internal". Mounted in the orders
 * service only, but the flags it flips are process globals, so they only
 * affect the orders process. The runbook still says "this fixes dates for
 * the partner" - partner payloads are built in the webhooks service now.
 */

var express = require('express');
var globals = require('../../lib/globals');
var logger = require('../../lib/logger');
var config = require('../../lib/config');

var router = express.Router();

// RB-9: the v1 partner wants "YYYY-MM-DD HH:mm:ss" dates. Flips the global
// Date.prototype.toJSON patch (lib/patches.js). Affects EVERY response from
// this process, and every order written while it's on (see store.js sort).
router.post('/legacy-dates', function (req, res) {
  globals.flags.legacyDateFormat = !!(req.body && req.body.enabled);
  logger.warn('v1 admin: legacy date format toggled', { enabled: globals.flags.legacyDateFormat });
  res.json({ legacyDateFormat: globals.flags.legacyDateFormat });
});

router.post('/maintenance', function (req, res) {
  globals.flags.maintenance = !!(req.body && req.body.enabled);
  res.json({ maintenance: globals.flags.maintenance });
});

// RB-7: used to compact the in-process webhook queue. The queue lives in the
// webhooks service since 2023; this just points you there.
router.post('/webhooks/compact', function (req, res) {
  res.status(410).json({ error: 'moved', use: config.peer('webhooks') + '/internal/webhooks/queue' });
});

router.get('/webhooks', function (req, res) {
  res.redirect(302, config.peer('webhooks') + '/internal/webhooks/queue');
});

module.exports = router;
