'use strict';

var expect = require('chai').expect;
var sinon = require('sinon');
var request = require('supertest');
var tmp = require('../support/tmp');
var config = require('../../lib/config');
var db = require('../../lib/db');
var errors = require('../../lib/errors');
var payments = require('../../services/payments/server');
var psp = require('../../services/payments/psp');
var fraudClient = require('../../services/payments/fraudClient');

function fakeFraud(decision, fail) {
  return {
    post: function () {
      if (fail) return Promise.reject(new Error('ECONNREFUSED'));
      return Promise.resolve({ statusCode: 200, body: { decision: decision, score: 0, reasons: [] } });
    }
  };
}

describe('payments: POST /internal/payments/charge', function () {
  var dir, app, sandbox;
  var body = { order_ref: 'LDR-T1', amount_pence: 2599, token: 'tok_ok', user_id: 'u-0001', email: 'dev.murphy1@example.test' };

  before(function () {
    dir = tmp.tmpDir('payments');
    config.set('data.dir', dir);
    db.open(dir, { payments: 'payment_id' });
    app = payments.buildApp();
  });
  after(function () { tmp.rmrf(dir); });
  beforeEach(function () { sandbox = sinon.createSandbox(); });
  afterEach(function () {
    sandbox.restore();
    fraudClient._setClient(null);
  });

  it('captures and returns the charge', function () {
    fraudClient._setClient(fakeFraud('allow'));
    sandbox.stub(psp.adapter, 'charge').resolves({ charge_id: 'ch_00000001', status: 'captured', amount_pence: 2599, attempts: 1 });
    return request(app).post('/internal/payments/charge').send(body).expect(201).then(function (res) {
      expect(res.body.charge_id).to.equal('ch_00000001');
      expect(res.body.status).to.equal('captured');
      expect(res.body.payment_id).to.match(/^pay_/);
    });
  });

  it('declines with reason fraud when fraud blocks, without calling the PSP', function () {
    fraudClient._setClient(fakeFraud('block'));
    var charge = sandbox.stub(psp.adapter, 'charge');
    return request(app).post('/internal/payments/charge').send(body).expect(402).then(function (res) {
      expect(res.body).to.deep.equal({ error: 'payment_declined', reason: 'fraud' });
      expect(charge.called).to.equal(false);
    });
  });

  it('maps a card decline to 402 card_declined', function () {
    fraudClient._setClient(fakeFraud('allow'));
    sandbox.stub(psp.adapter, 'charge').rejects(new errors.PaymentDeclined('card declined'));
    return request(app).post('/internal/payments/charge').send(body).expect(402).then(function (res) {
      expect(res.body.reason).to.equal('card_declined');
    });
  });

  it('maps a PSP failure to 502 payment_error', function () {
    fraudClient._setClient(fakeFraud('allow'));
    sandbox.stub(psp.adapter, 'charge').rejects(new errors.PaymentError('psp returned 500'));
    return request(app).post('/internal/payments/charge').send(body).expect(502).then(function (res) {
      expect(res.body.error).to.equal('payment_error');
    });
  });

  it('fails closed when fraud is unreachable (FRAUD-52)', function () {
    fraudClient._setClient(fakeFraud(null, true));
    sandbox.stub(psp.adapter, 'charge').resolves({ charge_id: 'ch_3', amount_pence: 2599, attempts: 1 });
    return request(app).post('/internal/payments/charge').send(body).expect(502).then(function (res) {
      expect(res.body.reason).to.equal('fraud_unavailable');
    });
  });

  it('rejects a missing token with 400', function () {
    return request(app).post('/internal/payments/charge').send({ order_ref: 'X', amount_pence: 100 }).expect(400);
  });

  it('lists payments for an order_ref', function () {
    return request(app).get('/internal/payments?order_ref=LDR-T1').expect(200).then(function (res) {
      expect(res.body.payments.length).to.be.at.least(1);
      res.body.payments.forEach(function (p) { expect(p.order_ref).to.equal('LDR-T1'); });
    });
  });
});
