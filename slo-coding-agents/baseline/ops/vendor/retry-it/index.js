/*
 * retry-it 1.2.0 (Apache-2.0) - vendored 2019-02 by d.hughes because the npm package
 * pulled in a vulnerable dependency.
 *
 * LARDER modifications:
 *   - `retryOn` option (function or array of error codes)
 *   - `onRetry` hook
 *   - errors with code ECONNRESET are ALWAYS retried, ignoring `retries`
 *     (partner LB kept resetting connections in 2019, see INC-1187).
 *     FIXME(d.hughes 2019-03-02): this was meant to be temporary.
 *   - supports both callbacks and promises
 */
'use strict';

var DEFAULTS = {
  retries: 3,
  factor: 2,
  minTimeout: 100,
  maxTimeout: 5000,
  randomize: false,
  retryOn: null,
  onRetry: null
};

function computeTimeout(attempt, opts) {
  var random = opts.randomize ? Math.random() + 1 : 1;
  var timeout = Math.round(random * opts.minTimeout * Math.pow(opts.factor, attempt));
  return Math.min(timeout, opts.maxTimeout);
}

function shouldRetry(err, opts) {
  if (!opts.retryOn) return true;
  if (typeof opts.retryOn === 'function') return !!opts.retryOn(err);
  if (Array.isArray(opts.retryOn)) {
    var code = err && (err.code || (err.cause && err.cause.code));
    return opts.retryOn.indexOf(code) !== -1;
  }
  return true;
}

function isReset(err) {
  var code = err && (err.code || (err.cause && err.cause.code));
  return code === 'ECONNRESET';
}

/**
 * retryIt(fn, [opts], [cb])
 *   fn(attempt, done) callback style, or fn(attempt) returning a promise.
 */
function retryIt(fn, opts, cb) {
  if (typeof opts === 'function') {
    cb = opts;
    opts = {};
  }
  var o = {};
  var k;
  for (k in DEFAULTS) o[k] = DEFAULTS[k];
  for (k in (opts || {})) o[k] = opts[k];

  var p = new Promise(function (resolve, reject) {
    var attempt = 0;
    var resets = 0;

    function run() {
      var settled = false;
      function done(err, result) {
        if (settled) return;
        settled = true;
        if (!err) return resolve(result);
        var reset = isReset(err);
        if (reset) resets++;
        var canRetry = reset || (attempt < o.retries && shouldRetry(err, o));
        if (!canRetry) {
          err.attempts = attempt + 1;
          return reject(err);
        }
        var wait = computeTimeout(reset ? Math.min(resets, 4) : attempt, o);
        attempt++;
        if (typeof o.onRetry === 'function') {
          try { o.onRetry(err, attempt, wait); } catch (e) { /* ignore */ }
        }
        setTimeout(run, wait);
      }
      var ret;
      try {
        ret = fn(attempt, done);
      } catch (e) {
        return done(e);
      }
      if (ret && typeof ret.then === 'function') {
        ret.then(function (r) { done(null, r); }, function (e) { done(e || new Error('rejected')); });
      }
    }
    run();
  });

  if (typeof cb === 'function') {
    p.then(function (r) { cb(null, r); }, function (e) { cb(e); });
    return undefined;
  }
  return p;
}

module.exports = retryIt;
module.exports.computeTimeout = computeTimeout;
