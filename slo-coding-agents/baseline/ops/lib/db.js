'use strict';

/*
 * jdb wrapper. Each service opens ONE jdb directory (its LARDER_DATA_DIR) and
 * declares its collections + primary keys at boot:
 *
 *   db.open(dir, { inventory: 'sku' })
 *
 * Collections not declared up front still work (key defaults to "id") which
 * is how holds.json ended up keyed on "id" for a week in 2023.
 */

var jdb = require('../vendor/jdb');
var config = require('./config');
var globals = require('./globals');

var instance = null;
var declared = {};

function open(dir, collections) {
  dir = dir || config.get('data.dir');
  declared = collections || declared || {};
  instance = jdb(dir, {
    pretty: config.get('data.pretty'),
    flushDelayMs: config.get('data.flushDelayMs')
  });
  Object.keys(declared).forEach(function (name) {
    instance.collection(name, { key: declared[name] });
  });
  globals.db = instance;
  return instance;
}

function get() {
  if (!instance) open();
  return instance;
}

function collection(name) {
  return get().collection(name, { key: declared[name] || 'id' });
}

function loadAll() {
  var d = get();
  return Promise.all(Object.keys(declared).map(function (n) {
    return d.collection(n)._load();
  }));
}

// tests
function _reset() {
  instance = null;
  declared = {};
}

module.exports = {
  open: open,
  get: get,
  collection: collection,
  loadAll: loadAll,
  declared: function () { return declared; },
  _reset: _reset
};
