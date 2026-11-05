'use strict';

var expect = require('chai').expect;
var sinon = require('sinon');
var request = require('supertest');
var db = require('../../lib/db');
var config = require('../../lib/config');
var busClient = require('../../lib/bus-client');
var tmp = require('../support/tmp');

var svc = require('../../services/orders/server');
var peers = require('../../services/orders/peers');
var store = require('../../services/orders/store');

function body(overrides) {
  return Object.assign({
    order_ref: 'LD-TEST-1',
    user_id: 'u-1',
    email: 'u1@example.test',
    items: [{ sku: 'SKU-0001', qty: 2, unit_price_pence: 249 }],
    coupon: null,
    subtotal_pence: 498,
    discount_pence: 0,
    delivery_fee_pence: 399,
    total_pence: 897,
    hold_id: 'H-abc',
    payment_token: 'tok_visa'
  }, overrides || {});
}

describe('orders service', function () {
  var dir, app, sandbox;

  beforeEach(function () {
    dir = tmp.tmpDir('orders');
    config.set('data.dir', dir);
    db.open(dir, { orders: 'id' });
    app = svc.buildApp();
    sandbox = sinon.createSandbox();
    sandbox.stub(peers, 'priceLines').resolves({ status: 200, items: [{ sku: 'SKU-0001', qty: 2, unit_price_pence: 249, vat_band: 'zero' }] });
    sandbox.stub(peers, 'totals').resolves({ subtotal_pence: 498, discount_pence: 0, delivery_fee_pence: 399, total_pence: 897, ok: true, expected_total_pence: 897 });
    sandbox.stub(peers, 'reserve').resolves({ status: 201 });
    sandbox.stub(peers, 'releaseReservations').resolves({});
    sandbox.stub(peers, 'confirmHold').resolves({ status: 200, slot: { slot_id: 'SL-0005', zone: 'Z1', date: '2026-01-01', window: '08:00-10:00' } });
    sandbox.stub(peers, 'releaseHold').resolves({});
    sandbox.stub(peers, 'charge').resolves({ status: 201, body: { charge_id: 'ch_1', payment_id: 'pay_1', attempts: 1 } });
    sandbox.stub(peers, 'ledgerEntry').resolves({ entry_id: 'le_1' });
    sandbox.stub(busClient, 'publish').resolves(true);
  });

  afterEach(function () {
    sandbox.restore();
    tmp.rmrf(dir);
  });

  it('creates an order and publishes order.confirmed', function () {
    return request(app).post('/internal/orders').send(body()).expect(201).then(function (res) {
      expect(res.body).to.deep.equal({ order_ref: 'LD-TEST-1', status: 'confirmed', charge_id: 'ch_1', total_pence: 897 });
      expect(peers.ledgerEntry.calledOnce).to.equal(true);
      var call = busClient.publish.firstCall;
      expect(call.args[0]).to.equal('order.confirmed');
      expect(call.args[2].email).to.equal('u1@example.test');
      expect(call.args[2].slot.zone).to.equal('Z1');
    });
  });

  it('409s price_mismatch with the expected total', function () {
    peers.totals.resolves({ total_pence: 897, ok: false, expected_total_pence: 897 });
    return request(app).post('/internal/orders').send(body({ total_pence: 900 })).expect(409).then(function (res) {
      expect(res.body).to.deep.equal({ error: 'price_mismatch', expected_total_pence: 897 });
      expect(peers.reserve.called).to.equal(false);
    });
  });

  it('409s out_of_stock and releases earlier reservations', function () {
    peers.priceLines.resolves({ status: 200, items: [
      { sku: 'A', qty: 1, unit_price_pence: 100 }, { sku: 'B', qty: 1, unit_price_pence: 100 }
    ] });
    peers.reserve.onSecondCall().resolves({ status: 409, sku: 'B' });
    return request(app).post('/internal/orders').send(body()).expect(409).then(function (res) {
      expect(res.body).to.deep.equal({ error: 'out_of_stock', sku: 'B' });
      expect(peers.releaseReservations.firstCall.args[0]).to.deep.equal([{ sku: 'A', qty: 1 }]);
    });
  });

  it('402s on decline and gives back stock and the hold', function () {
    peers.charge.resolves({ status: 402, body: { error: 'payment_declined', reason: 'card_declined' } });
    return request(app).post('/internal/orders').send(body({ payment_token: 'tok_decline' })).expect(402).then(function (res) {
      expect(res.body).to.deep.equal({ error: 'payment_declined' });
      expect(peers.releaseReservations.calledOnce).to.equal(true);
      expect(peers.releaseHold.calledWith('H-abc', 'payment_failed')).to.equal(true);
      expect(busClient.publish.called).to.equal(false);
    });
  });

  it('502s payment_error when payments fails', function () {
    peers.charge.resolves({ status: 502, body: { error: 'payment_error' } });
    return request(app).post('/internal/orders').send(body()).expect(502, { error: 'payment_error' });
  });

  it('409s hold_expired when slots refuses the hold', function () {
    peers.confirmHold.resolves({ status: 409, error: 'hold_expired' });
    return request(app).post('/internal/orders').send(body()).expect(409, { error: 'hold_expired' });
  });

  it('returns an order by ref', function () {
    return request(app).post('/internal/orders').send(body()).expect(201).then(function () {
      return request(app).get('/internal/orders/LD-TEST-1').expect(200);
    }).then(function (res) {
      expect(res.body.status).to.equal('confirmed');
      expect(res.body.items).to.deep.equal([{ sku: 'SKU-0001', qty: 2, unit_price_pence: 249 }]);
      expect(res.body).to.include.keys('subtotal_pence', 'discount_pence', 'delivery_fee_pence', 'charge_id', 'hold_id', 'created_at');
    });
  });

  it('paginates a user\'s orders newest first', function () {
    var base = Date.parse('2025-01-01T00:00:00Z');
    var docs = [];
    for (var i = 0; i < 5; i++) {
      docs.push({ order_ref: 'R' + i, user_id: i % 2 ? 'u-2' : 'u-1', status: 'confirmed', items: [], created_at: new Date(base + i * 1000).toISOString() });
    }
    return Promise.all(docs.map(store.insert)).then(function () {
      return request(app).get('/internal/orders?user_id=u-1&page=1&per_page=2').expect(200);
    }).then(function (res) {
      expect(res.body.total).to.equal(3);
      expect(res.body.per_page).to.equal(2);
      expect(res.body.orders.map(function (o) { return o.order_ref; })).to.deep.equal(['R4', 'R2']);
    });
  });

  it('marks an order dispatched via PATCH status', function () {
    return request(app).post('/internal/orders').send(body()).expect(201).then(function () {
      return request(app).patch('/internal/orders/LD-TEST-1/status').send({ status: 'dispatched', carrier: 'van-3' }).expect(200);
    }).then(function (res) {
      expect(res.body.status).to.equal('dispatched');
    });
  });

  it('refuses to dispatch a payment_failed order', function () {
    peers.charge.resolves({ status: 402, body: { error: 'payment_declined' } });
    return request(app).post('/internal/orders').send(body()).expect(402).then(function () {
      return request(app).patch('/internal/orders/LD-TEST-1/status').send({ status: 'dispatched' }).expect(409, { error: 'not_confirmed' });
    });
  });

  it.skip('publishes before responding even when the bus is slow (flaky, INC-2371)', function () {
    // relied on wall-clock ordering between supertest and the stubbed publish
    // delay; failed ~1 in 20 on the shared runner. (l.chen 2024-01-18)
  });
});

describe('legacy /v1/orders', function () {
  var dir, app;

  beforeEach(function () {
    dir = tmp.tmpDir('orders-v1');
    config.set('data.dir', dir);
    db.open(dir, { orders: 'id' });
    app = svc.buildApp();
    return store.insert({ order_ref: 'V1-1', user_id: 'u-9', status: 'confirmed', items: [{ sku: 'A', qty: 1, unit_price_pence: 250 }], total_pence: 649, charge_id: 'ch_9', created_at: '2024-02-01T10:00:00.000Z' });
  });

  afterEach(function () { tmp.rmrf(dir); });

  it('renders the v1 view in pounds', function () {
    return request(app).get('/v1/orders/V1-1').expect(200).then(function (res) {
      expect(res.headers.deprecation).to.equal('true');
      expect(res.body.state).to.equal('PLACED');
      expect(res.body.total).to.equal(6.49);
      expect(res.body.lines[0].price).to.equal(2.5);
    });
  });
});
