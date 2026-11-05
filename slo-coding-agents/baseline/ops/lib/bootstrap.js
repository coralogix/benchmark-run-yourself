'use strict';

/*
 * First-boot seeding (shared). If the service's data dir has no *.json files
 * we load seed/<name>.json for each collection the service asks for.
 *
 *   bootstrap.seedIfEmpty(db, {
 *     inventory: { file: 'inventory', normalise: fn },
 *     holds: { truncate: true }
 *   }, cb)
 *
 * Callback style - services call it through bluebird.promisify.
 *
 * Slot dates are stored as concrete YYYY-MM-DD dates computed from the seed's
 * day_offset at seed time. Restarting does not re-date slots (by design? the
 * comment in PLAT-201 says "slots roll over at midnight via cron" - there is no
 * cron).
 */

var fs = require('fs');
var path = require('path');
var async = require('async');
var _ = require('lodash');
var logger = require('./logger');

var SEED_DIR = path.join(__dirname, '..', 'seed');

function readSeed(name, cb) {
  fs.readFile(path.join(SEED_DIR, name + '.json'), 'utf8', function (err, raw) {
    if (err) return cb(err);
    try {
      cb(null, JSON.parse(raw));
    } catch (e) {
      cb(e);
    }
  });
}

function seedIfEmpty(db, spec, cb) {
  if (!db.isEmpty()) {
    logger.info('bootstrap: data dir not empty, skipping seed', { dir: db.dir });
    return cb(null, false);
  }
  logger.info('bootstrap: seeding empty data dir', { dir: db.dir, seed: SEED_DIR, collections: Object.keys(spec) });

  var counts = {};
  async.eachSeries(Object.keys(spec), function (name, done) {
    var s = spec[name] || {};
    var col = db.collection(name, s.key ? { key: s.key } : undefined);
    if (s.truncate || !s.file) {
      return col.truncate().then(function () { done(); }, done);
    }
    readSeed(s.file, function (err, rows) {
      if (err) return done(err);
      var docs = typeof s.normalise === 'function' ? s.normalise(rows) : rows;
      counts[name] = docs.length;
      col.putMany(docs).then(function () { done(); }, done);
    });
  }, function (err) {
    if (err) return cb(err);
    db.collection('meta', { key: 'key' }).put({
      key: 'seed',
      seeded_at: new Date().toISOString(),
      counts: counts
    }).then(function () {
      logger.info('bootstrap: seed complete', _.assign({ dir: db.dir }, counts));
      cb(null, true);
    }, cb);
  });
}

module.exports = {
  seedIfEmpty: seedIfEmpty,
  readSeed: readSeed,
  SEED_DIR: SEED_DIR
};
