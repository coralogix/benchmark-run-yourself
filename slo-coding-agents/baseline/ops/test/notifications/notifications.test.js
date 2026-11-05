'use strict';

var expect = require('chai').expect;
var sinon = require('sinon');
var Promise = require('bluebird');
var request = require('supertest');
var helpers = require('../bus/helpers');
var db = require('../../lib/db');
var fetchClient = require('../../lib/http/fetch-client');
var notifications = require('../../services/notifications/server');

var PAYLOAD = { order_ref: 'LD-N-1', email: 'n1@example.test', items: [{ sku: 'SKU-0001', qty: 1 }], total_pence: 649 };

describe('notifications', function () {
  var app;
  var fetchStub;

  beforeEach(function () {
    db.open(helpers.tmpDir('larder-notif-test-'), { notifications: 'id' });
    app = notifications.buildApp();
    fetchStub = sinon.stub(fetchClient, 'fetchJson');
  });

  afterEach(function () {
    sinon.restore();
  });

  function deliver(topic, payload) {
    return request(app).post('/internal/events').send({ topic: topic, key: payload.order_ref, payload: payload, delivery_id: 'd-1' });
  }

  it('sends order_confirmation with the order_ref', function () {
    fetchStub.returns(Promise.resolve({ status: 202, body: { message_id: 'm-1' } }));
    return deliver('order.confirmed', PAYLOAD).expect(200).then(function () {
      var args = fetchStub.firstCall.args;
      expect(args[0]).to.match(/\/v1\/messages$/);
      expect(args[1].body).to.deep.equal({ to: 'n1@example.test', template: 'order_confirmation', data: { order_ref: 'LD-N-1' } });
      return request(app).get('/internal/notifications?order_ref=LD-N-1').expect(200);
    }).then(function (res) {
      expect(res.body.notifications).to.have.length(1);
      expect(res.body.notifications[0].status).to.equal('sent');
      expect(res.body.notifications[0].message_id).to.equal('m-1');
    });
  });

  it('uses the order_dispatched template for order.dispatched', function () {
    fetchStub.returns(Promise.resolve({ status: 202, body: { message_id: 'm-2' } }));
    return deliver('order.dispatched', PAYLOAD).expect(200).then(function () {
      expect(fetchStub.firstCall.args[1].body.template).to.equal('order_dispatched');
    });
  });

  it('records a provider failure but still acks the bus', function () {
    fetchStub.returns(Promise.resolve({ status: 500, body: null, text: 'boom' }));
    return deliver('order.confirmed', PAYLOAD).expect(200).then(function (res) {
      expect(res.body.result.sent).to.equal(false);
      return request(app).get('/internal/notifications/stats');
    }).then(function (res) {
      expect(res.body.by_status.failed).to.equal(1);
    });
  });

  it('skips payloads without an email address', function () {
    return deliver('order.confirmed', { order_ref: 'LD-N-2' }).expect(200).then(function (res) {
      expect(res.body.result.skipped).to.equal(true);
      expect(fetchStub.called).to.equal(false);
    });
  });
});
