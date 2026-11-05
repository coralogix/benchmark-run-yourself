'use strict';

var path = require('path');
var expect = require('chai').expect;
var request = require('supertest');
var helpers = require('../bus/helpers');
var config = require('../../lib/config');
var payloads = require('../../services/webhooks/payloads');
var dispatcher = require('../../services/webhooks/dispatcher');
var webhooks = require('../../services/webhooks/server');

var ORDER = {
  order_ref: 'LD-TEST-1',
  user_id: 'u-1',
  email: 'u1@example.test',
  status: 'confirmed',
  items: [{ sku: 'SKU-0001', qty: 2, unit_price_pence: 249, vat_band: 'zero' }],
  coupon: { code: 'SAVE10', percent_off: 10, free_delivery: false },
  subtotal_pence: 498,
  discount_pence: 50,
  delivery_fee_pence: 399,
  total_pence: 847,
  charge_id: 'ch_1',
  hold_id: 'H-1',
  slot: { slot_id: 'SL-0005', zone: 'Z1', date: '2026-10-21', window: '08:00-10:00' },
  created_at: '2026-10-20T10:00:00.000Z'
};

describe('webhooks', function () {
  var srv;
  var partnerBase;
  var hits;

  before(function (done) {
    config.set('data.dir', helpers.tmpDir('larder-webhooks-test-'));
    helpers.stubServer(function (req, res) {
      hits.push(req.body);
      res.status(200).json({ ok: true });
    }, function (err, s, url) {
      srv = s;
      partnerBase = url;
      done(err);
    });
  });

  after(function (done) {
    dispatcher.stop();
    srv.close(done);
  });

  beforeEach(function () {
    hits = [];
    config.set('partner.url', partnerBase + '/hooks');
    dispatcher._reset();
  });

  it('builds a schema v1 order.confirmed envelope from the bus payload', function () {
    var p = payloads.orderConfirmed(ORDER);
    expect(p.event).to.equal('order.confirmed');
    expect(p.schema_version).to.equal(1);
    expect(p.data.items[0].line_total_pence).to.equal(498);
    expect(p.data.coupon_code).to.equal('SAVE10');
    expect(p.data.delivery).to.deep.equal({ hold_id: 'H-1', slot_id: 'SL-0005', zone: 'Z1', date: '2026-10-21', window: '08:00-10:00' });
    expect(p.data.source).to.equal('fulfilment');
  });

  it('order.dispatched carries no line items', function () {
    var p = payloads.orderDispatched({ order_ref: 'LD-1', item_count: 3, carrier: 'van', dispatched_at: 'x' });
    expect(p.data.items).to.equal(undefined);
    expect(p.data.item_count).to.equal(3);
    expect(p.data.carrier).to.equal('van');
  });

  it('enqueues bus deliveries and acks with 200', function () {
    var app = webhooks.buildApp();
    return request(app).post('/internal/events')
      .send({ topic: 'order.confirmed', key: ORDER.order_ref, payload: ORDER, delivery_id: 'd1' })
      .expect(200)
      .then(function (res) {
        expect(res.body.ok).to.equal(true);
        expect(dispatcher.depth()).to.equal(1);
        expect(dispatcher.items()[0].event).to.equal('order.confirmed');
      });
  });

  it('delivers pending items to the partner on tick and drops them', function () {
    dispatcher.enqueue(payloads.orderConfirmed(ORDER));
    return dispatcher.tick().then(function () {
      expect(hits).to.have.length(1);
      expect(hits[0].order_ref).to.equal('LD-TEST-1');
      expect(dispatcher.depth()).to.equal(0);
    });
  });
});
