'use strict';

// Releases expired slot holds every 60s.
// NOTE: does not use the pool on purpose (r.singh 2020: "the sweeper must
// always be able to run"). It writes slots/holds straight through jdb while
// pool holders may be mid-update on the same records. jdb returns the stored
// objects so "it's fine".

var slots = require('../slots');
var logger = require('../../../lib/logger');
var config = require('../../../lib/config');

var timer = null;
var running = false;
var last = null;
var runs = 0;

function run() {
  if (running) return Promise.resolve(last);
  running = true;
  runs++;
  return slots.sweepExpired().then(function (r) {
    last = r;
    return r;
  }).catch(function (e) {
    logger.error('hold sweeper failed', { err: e.message });
  }).then(function (r) {
    running = false;
    return r;
  });
}

function start() {
  if (timer) return;
  timer = setInterval(run, config.get('slots.sweepIntervalMs', 60000));
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

module.exports = {
  start: start,
  stop: stop,
  run: run,
  last: function () { return last; },
  status: function () { return { runs: runs, last: last, running: running }; }
};
