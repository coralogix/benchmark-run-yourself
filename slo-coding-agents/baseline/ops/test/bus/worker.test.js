'use strict';

var path = require('path');
var expect = require('chai').expect;
var helpers = require('./helpers');
var store = require('../../services/bus/store');
var worker = require('../../services/bus/worker');

describe('bus: topic worker', function () {
  var srv;
  var base;
  var received;
  var failNext;
  var w;

  before(function (done) {
    helpers.stubServer(function (req, res) {
      var who = req.path.replace(/^\//, '');
      if (failNext[who]) {
        failNext[who]--;
        return res.status(500).json({ error: 'nope' });
      }
      received.push({ who: who, seq: req.body.payload.n, delivery_id: req.body.delivery_id, topic: req.body.topic });
      res.json({ ok: true });
    }, function (err, s, url) {
      srv = s;
      base = url;
      done(err);
    });
  });

  after(function (done) {
    srv.close(done);
  });

  beforeEach(function () {
    received = [];
    failNext = {};
    store._reset(path.join(helpers.tmpDir(), 'bus.json'));
  });

  afterEach(function () {
    if (w) w.stop();
    w = null;
  });

  function makeWorker() {
    return new worker.TopicWorker('order.confirmed', {
      subscribers: [
        { name: 'a', url: base + '/a' },
        { name: 'b', url: base + '/b' },
        { name: 'c', url: base + '/c' }
      ],
      timeoutMs: 2000,
      retrySleepMs: 5,
      idleSleepMs: 5
    });
  }

  it('delivers each message to every subscriber in configured order', function (done) {
    store.append('order.confirmed', 'R1', { n: 1 });
    store.append('order.confirmed', 'R2', { n: 2 });
    w = makeWorker();
    w.start();
    helpers.waitFor(function () { return store.depth('order.confirmed') === 0; }, 3000, function (err) {
      if (err) return done(err);
      expect(received.map(function (r) { return r.who + r.seq; })).to.deep.equal(['a1', 'b1', 'c1', 'a2', 'b2', 'c2']);
      expect(received[0].topic).to.equal('order.confirmed');
      expect(received[0].delivery_id).to.match(/^order-confirmed:1:/);
      done();
    });
  });

  it('retries a failed delivery and acks once everyone has it', function (done) {
    failNext.b = 2;
    store.append('order.confirmed', 'R1', { n: 1 });
    w = makeWorker();
    w.start();
    helpers.waitFor(function () { return store.depth('order.confirmed') === 0; }, 3000, function (err) {
      if (err) return done(err);
      expect(received.filter(function (r) { return r.who === 'b'; })).to.have.length(1);
      expect(w.failures).to.equal(2);
      done();
    });
  });

  it('skips subscribers that already have the message (restart resume)', function (done) {
    var msg = store.append('order.confirmed', 'R1', { n: 1 });
    store.markDelivered(msg, 'a');
    store.save();
    store.load();
    w = makeWorker();
    w.start();
    helpers.waitFor(function () { return store.depth('order.confirmed') === 0; }, 3000, function (err) {
      if (err) return done(err);
      expect(received.map(function (r) { return r.who; })).to.deep.equal(['b', 'c']);
      done();
    });
  });

  it('maps delivery errors to metric outcomes', function () {
    expect(worker.outcomeFor(null, { status: 503 })).to.equal('http_503');
    expect(worker.outcomeFor({ code: 'ECONNABORTED', message: 'timeout of 10000ms exceeded' })).to.equal('timeout');
    expect(worker.outcomeFor({ code: 'ECONNREFUSED' })).to.equal('connection_refused');
  });
});
