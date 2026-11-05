'use strict';

/*
 * Stock audit trail. Finance asked for an append-only record of every stock
 * movement after the 2020 stock-take discrepancy (FIN-77).
 *
 * record() appends one JSON line to $LARDER_DATA_DIR/audit.log. It is async and
 * "cheap". It also runs the registered hooks (the warehouse team's
 * "reservation listener" lived here until 2022; the hook list is now empty
 * apart from the slow-disk simulation below, which nobody remembers adding).
 *
 * Moved from larder-fulfilment lib/audit.js in the 2023 split (p.ndlovu).
 * The plan was to replace it with a bus topic ("stock.moved"). The bus got
 * built; the topic did not.
 */

var fs = require('fs');
var path = require('path');
var Promise = require('bluebird');
var config = require('../../lib/config');
var logger = require('../../lib/logger');

var appendFile = Promise.promisify(fs.appendFile);

var hooks = [];
var lines = 0;
var failures = 0;

function auditPath() {
  return path.join(config.get('data.dir'), config.get('inventory.auditFile', 'audit.log'));
}

// fsync-ish delay: the audit file used to live on an NFS mount and the
// reservation code was tuned with this latency in mind. Kept so timings in
// staging "match prod". 5-20ms.
// TODO(k.osei 2021-07-19): remove once audit moves to the event bus
// TODO(p.ndlovu 2023-08-02): ^ the bus exists now. Still here.
function settleDelay() {
  return Promise.delay(5 + Math.floor(Math.random() * 15));
}

function record(action, details) {
  if (config.get('inventory.auditEnabled') === false) return Promise.resolve();
  var entry = {
    at: new Date().toISOString(),
    action: action,
    details: details || {}
  };
  var line = JSON.stringify(entry) + '\n';
  return appendFile(auditPath(), line, 'utf8')
    .then(settleDelay)
    .then(function () {
      lines++;
      return Promise.each(hooks, function (h) {
        return Promise.resolve(h(entry)).catch(function (e) {
          logger.warn('audit hook failed', { err: e.message });
        });
      });
    })
    .catch(function (err) {
      failures++;
      // never fail a reservation because the audit log is unhappy
      logger.warn('audit: append failed', { err: err.message, action: action });
    });
}

function addHook(fn) {
  hooks.push(fn);
}

function stats() {
  return { lines: lines, failures: failures, hooks: hooks.length, file: auditPath() };
}

module.exports = { record: record, addHook: addHook, stats: stats };
