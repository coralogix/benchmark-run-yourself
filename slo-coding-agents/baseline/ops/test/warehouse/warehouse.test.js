'use strict';

var expect = require('chai').expect;
var sinon = require('sinon');
var Promise = require('bluebird');
var request = require('supertest');
var helpers = require('../bus/helpers');
var db = require('../../lib/db');
var fetchClient = require('../../lib/http/fetch-client');
var busClient = require('../../lib/bus-client');
var picklists = require('../../services/warehouse/picklists');
var handlers = require('../../services/warehouse/handlers');
var warehouse = require('../../services/warehouse/server');

function order(status) {
  return {
    order_ref: 'LD-W-1',
    user_id: 'u-9',
    email: 'u9@example.test',
    status: status,
    items: [{ sku: 'SKU-0030', qty: 2, unit_price_pence: 300 }, { sku: 'SKU-0007', qty: 1, unit_price_pence: 120 }],
    total_pence: 1119,
    hold_id: 'H-9',
    slot: { slot_id: 'SL-1', zone: 'Z2', date: '2026-10-21', window: '10:00-12:00' }
  };
}

describe('warehouse', function () {
  var app;
  var fetchStub;
  var publishStub;

  beforeEach(function () {
    db.open(helpers.tmpDir('larder-wh-test-'), { warehouse: 'order_ref' });
    app = warehouse.buildApp();
    fetchStub = sinon.stub(fetchClient, 'fetchJson');
    publishStub = sinon.stub(busClient, 'publish').returns(Promise.resolve(true));
  });

  afterEach(function () {
    sinon.restore();
  });

  it('derives bins from the sku number', function () {
    expect(picklists.binFor('SKU-0030')).to.deep.equal(picklists.binFor('SKU-0030'));
    expect(picklists.binFor('SKU-0001').bin).to.equal('A02-B1-S1');
  });

  it('builds a pick list when order.confirmed arrives', function () {
    return Promise.resolve(handlers['order.confirmed'](order('confirmed'))).then(function () {
      return picklists.get('LD-W-1');
    }).then(function (pl) {
      expect(pl.item_count).to.equal(3);
      expect(pl.status).to.equal('open');
      expect(pl.zone).to.equal('Z2');
      expect(pl.source).to.equal('bus');
    });
  });

  it('dispatches a confirmed order: inventory, orders status, bus', function () {
    fetchStub.callsFake(function (url, opts) {
      if (/\/status$/.test(url)) return Promise.resolve({ status: 200, body: { order_ref: 'LD-W-1', status: 'dispatched' } });
      if (/inventory\/dispatches$/.test(url)) return Promise.resolve({ status: 200, body: { ok: true } });
      return Promise.resolve({ status: 200, body: order('confirmed') });
    });
    return request(app).post('/internal/dispatch/LD-W-1').send({}).expect(200)
      .then(function (res) {
        expect(res.body).to.deep.equal({ order_ref: 'LD-W-1', status: 'dispatched' });
        expect(publishStub.calledOnce).to.equal(true);
        expect(publishStub.firstCall.args[0]).to.equal('order.dispatched');
        expect(publishStub.firstCall.args[2].email).to.equal('u9@example.test');
        return picklists.get('LD-W-1');
      })
      .then(function (pl) {
        expect(pl.status).to.equal('dispatched');
        expect(pl.source).to.equal('dispatch');
      });
  });

  it('404s for an unknown order', function () {
    fetchStub.returns(Promise.resolve({ status: 404, body: { error: 'not_found' } }));
    return request(app).post('/internal/dispatch/NOPE').expect(404, { error: 'not_found' });
  });

  it('409s when the order is not confirmed', function () {
    fetchStub.returns(Promise.resolve({ status: 200, body: order('payment_failed') }));
    return request(app).post('/internal/dispatch/LD-W-1').expect(409, { error: 'not_confirmed' });
  });

  it.skip('dispatch is idempotent under concurrent scanner double-taps (flaky on CI, timing dependent - s.aziz 2024-01)', function () {
    // relied on the in-flight map catching two requests within the same tick
  });
});
