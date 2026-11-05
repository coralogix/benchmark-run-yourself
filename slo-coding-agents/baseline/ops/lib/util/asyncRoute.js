'use strict';

// Express 4 does not catch rejected promises from handlers. Wrap them.
// (Some older routes don't use this and do their own try/catch.)
module.exports = function asyncRoute(fn) {
  return function (req, res, next) {
    var p;
    try {
      p = fn(req, res, next);
    } catch (e) {
      return next(e);
    }
    if (p && typeof p.catch === 'function') p.catch(next);
  };
};
