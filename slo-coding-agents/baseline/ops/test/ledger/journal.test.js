'use strict';

var expect = require('chai').expect;
var request = require('supertest');
var tmp = require('../support/tmp');
var config = require('../../lib/config');
var ledger = require('../../services/ledger/server');
var journal = require('../../services/ledger/journal');
var events = require('../../services/ledger/events');

describe('ledger', function () {
  var dir, app;

  before(function () {
    dir = tmp.tmpDir('ledger');
    config.set('data.dir', dir);
    journal._reset();
    app = ledger.buildApp();
  });
  after(function () {
    journal._reset();
    tmp.rmrf(dir);
  });
  beforeEach(function () { config.set('data.dir', dir); });

  it('appends entries with increasing seq', function () {
    return request(app).post('/internal/ledger/entries')
      .send({ order_ref: 'LDR-L1', kind: 'charge', amount_pence: 1500, charge_id: 'ch_1' })
      .expect(201).then(function (r1) {
        return request(app).post('/internal/ledger/entries')
          .send({ order_ref: 'LDR-L2', kind: 'charge', amount_pence: 700 })
          .expect(201).then(function (r2) {
            expect(r2.body.seq).to.equal(r1.body.seq + 1);
          });
      });
  });

  it('signs refunds negative in the balance', function (done) {
    journal.append({ order_ref: 'LDR-L1', kind: 'refund', amount_pence: 500 }, function (err) {
      if (err) return done(err);
      var b = journal.balance();
      expect(b.by_kind.refund.amount_pence).to.equal(-500);
      expect(b.balance_pence).to.equal(1500 + 700 - 500);
      done();
    });
  });

  it('bus backfill skips orders that already have a charge entry', function () {
    return events['order.confirmed']({ order_ref: 'LDR-L1', total_pence: 1500 }).then(function (r) {
      expect(r.skipped).to.equal('exists');
    });
  });

  it('bus backfill writes a charge entry when missing', function () {
    return request(app).post('/internal/events')
      .send({ topic: 'order.confirmed', key: 'LDR-L9', payload: { order_ref: 'LDR-L9', total_pence: 4200, charge_id: 'ch_9' }, delivery_id: 'd-1' })
      .expect(200).then(function () {
        var list = journal.find({ order_ref: 'LDR-L9' });
        expect(list).to.have.length(1);
        expect(list[0].source).to.equal('bus');
      });
  });
});
