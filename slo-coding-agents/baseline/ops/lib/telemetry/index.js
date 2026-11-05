// OWNED BY PLATFORM TEAM - do not modify without #platform approval
//
// Convenience re-export. Application code should require this, not the
// individual files (some older services still reach into ./instruments).
'use strict';

var api = require('@opentelemetry/api');
var instruments = require('./instruments');
var runtime = require('./runtime');

// Wraps fn in an active span. fn receives the span. Errors are recorded and
// rethrown. Works with sync and promise-returning fns.
function withSpan(name, attrs, fn) {
  if (typeof attrs === 'function') {
    fn = attrs;
    attrs = {};
  }
  return instruments.tracer.startActiveSpan(name, { attributes: attrs || {} }, function (span) {
    var out;
    try {
      out = fn(span);
    } catch (e) {
      span.recordException(e);
      span.setStatus({ code: api.SpanStatusCode.ERROR, message: e && e.message });
      span.end();
      throw e;
    }
    if (out && typeof out.then === 'function') {
      return out.then(function (v) {
        span.end();
        return v;
      }, function (e) {
        span.recordException(e);
        span.setStatus({ code: api.SpanStatusCode.ERROR, message: e && e.message });
        span.end();
        throw e;
      });
    }
    span.end();
    return out;
  });
}

function setAttr(key, value) {
  var span = api.trace.getActiveSpan();
  if (span && value !== undefined && value !== null) span.setAttribute(key, value);
}

module.exports = {
  api: api,
  instruments: instruments,
  runtime: runtime,
  inc: instruments.inc,
  observe: instruments.observe,
  tracer: instruments.tracer,
  withSpan: withSpan,
  setAttr: setAttr,
  enabled: function () {
    var o = global.__LARDER_OTEL__;
    return !!(o && o.isEnabled());
  }
};
