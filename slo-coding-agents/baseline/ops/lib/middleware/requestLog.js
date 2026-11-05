'use strict';

var logger = require('../logger');
var ids = require('../util/ids');

// Access log. Health checks are skipped (they're 1/s from the mesh).
var SKIP = /^\/(healthz|internal\/health)/;
var SLOW_MS = 2000;

module.exports = function requestLog() {
  return function (req, res, next) {
    var started = process.hrtime();
    req.id = req.headers['x-request-id'] || ids.requestId();
    res.setHeader('X-Request-Id', req.id);
    res.on('finish', function () {
      if (SKIP.test(req.path)) return;
      var d = process.hrtime(started);
      var ms = Math.round(d[0] * 1e3 + d[1] / 1e6);
      var meta = { method: req.method, path: req.originalUrl, status: res.statusCode, ms: ms, req_id: req.id };
      if (res.statusCode >= 500) logger.warn('request failed', meta);
      else if (ms > SLOW_MS) logger.warn('slow request', meta);
      else logger.verbose('request', meta);
    });
    next();
  };
};
