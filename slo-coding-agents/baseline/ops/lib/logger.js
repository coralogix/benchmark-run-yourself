'use strict';

/*
 * Legacy logger. winston 2.x because the 3.x upgrade broke the format our old
 * log shipper parsed (PLAT-88, 2019). Do not upgrade without talking to
 * platform.
 *
 * Every log line is ALSO emitted as an OpenTelemetry log record (platform
 * requirement, 2024) through the OtelTransport below. When telemetry is
 * disabled the OTel logger is a no-op.
 *
 * The service name is prefixed onto every console line ("[orders] ...") so
 * the old all-services tail in the bench box still makes sense.
 */

var util = require('util');
var winston = require('winston');
var apiLogs = require('@opentelemetry/api-logs');
var _ = require('lodash');
var moment = require('moment');

var SeverityNumber = apiLogs.SeverityNumber;

// winston level -> OTel severity
var SEVERITY = {
  error: { n: SeverityNumber.ERROR, t: 'ERROR' },
  warn: { n: SeverityNumber.WARN, t: 'WARN' },
  info: { n: SeverityNumber.INFO, t: 'INFO' },
  verbose: { n: SeverityNumber.DEBUG2, t: 'DEBUG' },
  debug: { n: SeverityNumber.DEBUG, t: 'DEBUG' },
  silly: { n: SeverityNumber.TRACE, t: 'TRACE' }
};

function serviceName() {
  var o = global.__LARDER_OTEL__;
  if (o && o.serviceName) return o.serviceName();
  return process.env.LARDER_SERVICE || 'ops';
}

function flattenMeta(meta, prefix, out) {
  out = out || {};
  if (!meta || typeof meta !== 'object') return out;
  Object.keys(meta).forEach(function (k) {
    var v = meta[k];
    var key = prefix ? prefix + '.' + k : k;
    if (v === null || v === undefined) return;
    if (v instanceof Error) {
      out[key + '.message'] = v.message;
      out[key + '.stack'] = String(v.stack || '').slice(0, 4000);
    } else if (Array.isArray(v)) {
      out[key] = JSON.stringify(v).slice(0, 2000);
    } else if (typeof v === 'object') {
      flattenMeta(v, key, out);
    } else {
      out[key] = v;
    }
  });
  return out;
}

function OtelTransport(options) {
  options = options || {};
  winston.Transport.call(this, options);
  this.name = 'otel';
  this.level = options.level || 'debug';
}
util.inherits(OtelTransport, winston.Transport);

OtelTransport.prototype.log = function (level, msg, meta, callback) {
  try {
    var sev = SEVERITY[level] || SEVERITY.info;
    apiLogs.logs.getLogger('larder-ops.legacy').emit({
      severityNumber: sev.n,
      severityText: sev.t,
      body: msg,
      attributes: flattenMeta(meta)
    });
  } catch (e) {
    // swallow - logging must never throw
  }
  callback(null, true);
};

var level = process.env.LOG_LEVEL || 'info';
// see config/local.js: an unknown level silently becomes info there, but the
// logger reads the env var directly and winston 2 then logs nothing below
// error. (FIXME j.watts 2022-11-03: make this read config.)
if (!SEVERITY[level]) level = 'error';
var silent = process.env.NODE_ENV === 'test' && !process.env.LOG_IN_TESTS;

var logger = new winston.Logger({
  levels: winston.config.npm.levels,
  transports: [
    new winston.transports.Console({
      level: level,
      silent: silent,
      timestamp: function () { return moment().format('YYYY-MM-DD HH:mm:ss.SSS'); },
      label: serviceName(),
      colorize: false,
      stderrLevels: ['error']
    }),
    new OtelTransport({ level: 'debug' })
  ],
  exitOnError: false
});

// Child-logger-ish helper used by newer code (winston 2 has no child()).
logger.withContext = function (ctx) {
  var base = _.clone(ctx || {});
  var child = {};
  ['error', 'warn', 'info', 'verbose', 'debug'].forEach(function (lvl) {
    child[lvl] = function (msg, meta) {
      logger.log(lvl, msg, _.assign({}, base, meta || {}));
    };
  });
  child.warning = child.warn;
  return child;
};

// a few modules still call logger.warning (log4js habit)
logger.warning = logger.warn;

module.exports = logger;
module.exports.OtelTransport = OtelTransport;
module.exports.SEVERITY = SEVERITY;
module.exports.flattenMeta = flattenMeta;
