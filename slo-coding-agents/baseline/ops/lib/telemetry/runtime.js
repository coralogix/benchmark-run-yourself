// OWNED BY PLATFORM TEAM - do not modify without #platform approval
//
// Runtime health: event loop delay via perf_hooks.monitorEventLoopDelay,
// exported as ops_event_loop_lag_seconds by every ops service.
'use strict';

var perfHooks = require('perf_hooks');
var instruments = require('./instruments');

var histogram = null;
var lastMaxSeconds = 0;
var lastMeanSeconds = 0;
var lastP99Seconds = 0;
var sampleTimer = null;

// The gauge reports the max delay seen since the last metric collection.
// We also keep a rolling snapshot for /internal/debug/vars.
function start() {
  if (histogram) return;
  histogram = perfHooks.monitorEventLoopDelay({ resolution: 20 });
  histogram.enable();
  instruments.observe('eventLoopLag', function () {
    var v = histogram.max / 1e9;
    lastMaxSeconds = v;
    lastMeanSeconds = histogram.mean / 1e9;
    lastP99Seconds = histogram.percentile(99) / 1e9;
    histogram.reset();
    return v;
  });
  // also refresh the snapshot every 10s even when no exporter is collecting
  sampleTimer = setInterval(function () {
    if (!histogram) return;
    lastMaxSeconds = Math.max(lastMaxSeconds * 0.5, histogram.max / 1e9);
    lastMeanSeconds = histogram.mean / 1e9;
    lastP99Seconds = histogram.percentile(99) / 1e9;
  }, 10000);
  if (sampleTimer.unref) sampleTimer.unref();
}

function stop() {
  if (histogram) histogram.disable();
  if (sampleTimer) clearInterval(sampleTimer);
  histogram = null;
}

function snapshot() {
  return {
    max_seconds: histogram ? Math.max(lastMaxSeconds, histogram.max / 1e9) : 0,
    mean_seconds: histogram ? (histogram.mean / 1e9 || lastMeanSeconds) : 0,
    p99_seconds: histogram ? (histogram.percentile(99) / 1e9 || lastP99Seconds) : 0
  };
}

module.exports = { start: start, stop: stop, snapshot: snapshot };
