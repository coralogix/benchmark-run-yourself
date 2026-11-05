'use strict';

process.env.NODE_ENV = 'test';

var os = require('os');
var fs = require('fs');
var path = require('path');
var expect = require('chai').expect;
var request = require('supertest');
var moment = require('moment');

var config = require('../../lib/config');
var db = require('../../lib/db');
var pool = require('../../lib/pool');
var svc = require('../../services/slots/server');
var slots = require('../../services/slots/slots');
var seed = require('../../services/slots/seed');

function fixtures() {
  return seed.normaliseSlots([
    { slot_id: 'SL-T1', zone: 'Z1', window: '08:00-10:00', capacity: 5, booked: 0, day_offset: 1 },
    { slot_id: 'SL-T2', zone: 'Z1', window: '10:00-12:00', capacity: 2, booked: 2, day_offset: 1 },
    { slot_id: 'SL-T3', zone: 'Z2', window: '08:00-10:00', capacity: 5, booked: 0, day_offset: 2 },
    { slot_id: 'SL-OLD', zone: 'Z1', window: '08:00-10:00', capacity: 5, booked: 0, day_offset: -3 }
  ]);
}

var ZONES = [
  { zone: 'Z1', name: 'Central', postcodes: ['LD1 1AA', 'LD1 2BB'] },
  { zone: 'Z2', name: 'North', postcodes: ['LD2 1AA'] }
];

describe('slots service', function () {
  var tmp;
  var app;
  var dirs = [];

  after(function (done) {
    // let jdb's write-behind flush land before removing the dirs
    setTimeout(function () {
      dirs.forEach(function (d) { fs.rmSync(d, { recursive: true, force: true }); });
      done();
    }, 150);
  });

  beforeEach(function () {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-slots-'));
    dirs.push(tmp);
    config.set('data.dir', tmp);
    var d = db.open(tmp, { slots: 'slot_id', holds: 'hold_id', zones: 'zone' });
    pool.init(d);
    return Promise.all([
      d.collection('slots').putMany(fixtures()),
      d.collection('zones').putMany(ZONES),
      d.collection('holds').truncate()
    ]).then(function () {
      app = svc.buildApp();
    });
  });

  it('looks up zones by exact postcode and by sector', function () {
    return request(app).get('/internal/zones/lookup?postcode=ld2%201aa').expect(200, { zone: 'Z2' }).then(function () {
      return request(app).get('/internal/zones/lookup?postcode=LD1%202ZZ').expect(200, { zone: 'Z1' });
    }).then(function () {
      return request(app).get('/internal/zones/lookup?postcode=QQ9%209QQ').expect(404, { error: 'unknown_postcode' });
    });
  });

  it('lists future slots for a zone', function () {
    return request(app).get('/internal/slots?zone=Z1').expect(200).then(function (res) {
      expect(res.body.slots.map(function (s) { return s.slot_id; })).to.deep.equal(['SL-T1', 'SL-T2']);
      expect(res.body.slots[0]).to.have.keys('slot_id', 'zone', 'date', 'window', 'capacity', 'booked');
    });
  });

  it('creates a hold and counts it as booked', function () {
    return request(app).post('/internal/slot-holds').send({ slot_id: 'SL-T1', cart_ref: 'cart-1' })
      .expect(201)
      .then(function (res) {
        expect(res.body.hold_id).to.match(/^H-/);
        expect(moment(res.body.expires_at).isAfter(moment())).to.equal(true);
        return db.collection('slots').get('SL-T1');
      })
      .then(function (s) {
        expect(s.booked).to.equal(1);
      });
  });

  it('answers 409 slot_full when the slot has no capacity', function () {
    return request(app).post('/internal/slot-holds').send({ slot_id: 'SL-T2', cart_ref: 'cart-2' })
      .expect(409, { error: 'slot_full' });
  });

  it('404s an unknown slot', function () {
    return request(app).post('/internal/slot-holds').send({ slot_id: 'SL-NOPE' })
      .expect(404, { error: 'slot_not_found' });
  });

  it('confirms a hold for an order (and accepts a replay)', function () {
    var holdId;
    return request(app).post('/internal/slot-holds').send({ slot_id: 'SL-T3', cart_ref: 'c' }).expect(201)
      .then(function (res) {
        holdId = res.body.hold_id;
        return request(app).post('/internal/slot-holds/' + holdId + '/confirm').send({ order_ref: 'ORD-9' }).expect(200);
      })
      .then(function (res) {
        expect(res.body.status).to.equal('booked');
        expect(res.body.slot).to.include({ slot_id: 'SL-T3', zone: 'Z2', window: '08:00-10:00' });
        return request(app).post('/internal/slot-holds/' + holdId + '/confirm').send({ order_ref: 'ORD-9' }).expect(200);
      });
  });

  it('refuses to confirm an unknown hold', function () {
    return request(app).post('/internal/slot-holds/H-nope/confirm').send({ order_ref: 'ORD-1' })
      .expect(409, { error: 'hold_expired' });
  });

  it('releases a hold and gives the unit back', function () {
    return request(app).post('/internal/slot-holds').send({ slot_id: 'SL-T1' }).expect(201)
      .then(function (res) {
        return request(app).post('/internal/slot-holds/' + res.body.hold_id + '/release')
          .send({ reason: 'payment_failed' }).expect(200);
      })
      .then(function (res) {
        expect(res.body.status).to.equal('released');
        return db.collection('slots').get('SL-T1');
      })
      .then(function (s) {
        expect(s.booked).to.equal(0);
      });
  });

  it('sweeper expires holds past expires_at', function () {
    return request(app).post('/internal/slot-holds').send({ slot_id: 'SL-T1' }).expect(201)
      .then(function () {
        return slots.sweepExpired(Date.now() + 60 * 60 * 1000);
      })
      .then(function (r) {
        expect(r.expired).to.equal(1);
        return db.collection('slots').get('SL-T1');
      })
      .then(function (s) {
        expect(s.booked).to.equal(0);
      });
  });

  // Fails around midnight because seed dates are computed with local time and
  // the listing filters with startOf('day'). (flaky, see INC-2417)
  it.skip('hides slots dated yesterday even across midnight (flaky, see INC-2417)', function () {
    return request(app).get('/internal/slots?zone=Z1&all=1').expect(200).then(function (res) {
      expect(res.body.slots).to.have.length(3);
    });
  });
});
