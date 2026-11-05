'use strict';

var expect = require('chai').expect;
var fs = require('fs');
var path = require('path');
var jdb = require('../../vendor/jdb');
var poolLib = require('../../lib/pool');
var tmp = require('../support/tmp');

describe('vendor/jdb', function () {
  var dir;
  beforeEach(function () { dir = tmp.tmpDir('jdb'); });
  afterEach(function () { tmp.rmrf(dir); });

  it('puts, gets and flushes to disk', function () {
    var db = jdb(dir, { flushDelayMs: 1 });
    var col = db.collection('things', { key: 'k' });
    return col.put({ k: 'a', n: 1 }).then(function () {
      return col.get('a');
    }).then(function (doc) {
      expect(doc.n).to.equal(1);
      var onDisk = JSON.parse(fs.readFileSync(path.join(dir, 'things.json'), 'utf8'));
      expect(onDisk).to.have.length(1);
    });
  });
});

describe('lib/pool', function () {
  it('hands a released connection to the next waiter', function () {
    var pool = new poolLib.Pool({ collection: function () {} }, 1);
    return pool.acquire('first').then(function (c1) {
      var second = pool.acquire('second', 1000);
      expect(pool.waiting()).to.equal(1);
      c1.release();
      return second;
    }).then(function (c2) {
      expect(c2.label).to.equal('second');
      expect(pool.inUse()).to.equal(1);
      c2.release();
    });
  });

  it('times out an acquire when the pool is exhausted', function () {
    var pool = new poolLib.Pool({ collection: function () {} }, 1);
    return pool.acquire('hog').then(function () {
      return pool.acquire('late', 20);
    }).then(function () {
      throw new Error('should not resolve');
    }, function (err) {
      expect(err.code).to.equal('pool_timeout');
    });
  });
});
