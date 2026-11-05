'use strict';

var config = require('../config');

// Shared-token auth for /internal. Disabled everywhere (see config/local.js).
module.exports = function internalAuth() {
  return function (req, res, next) {
    if (!config.get('internalAuth.enabled')) return next();
    var hdr = config.get('internalAuth.header');
    if (req.headers[hdr] === config.get('internalAuth.token')) return next();
    res.status(401).json({ error: 'unauthorised' });
  };
};
