'use strict';

// Indirection so tests can stub the adapter. (Also "so we can swap PSPs" -
// we have had one PSP since 2017.)
var adapter = require('./adapter');

module.exports = {
  charge: function (p) { return adapter.charge(p); },
  refund: function (id, amount, ctx) { return adapter.refund(id, amount, ctx); },
  adapter: adapter
};
