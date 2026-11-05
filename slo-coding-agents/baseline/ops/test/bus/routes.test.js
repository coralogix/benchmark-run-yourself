'use strict';

var path = require('path');
var expect = require('chai').expect;
var request = require('supertest');
var helpers = require('./helpers');
var store = require('../../services/bus/store');
var subscriptions = require('../../services/bus/subscriptions');
var bus = require('../../services/bus/server');

describe('bus: routes', function () {
  var app;

  before(function () {
    subscriptions._set({
      'order.confirmed': [{ name: 'webhooks', url_env: 'NOPE_URL', peer: 'webhooks', path: '/internal/events' }]
    });
    app = bus.buildApp();
  });

  after(function () {
    subscriptions.load();
  });

  beforeEach(function () {
    store._reset(path.join(helpers.tmpDir(), 'bus.json'));
  });

  it('accepts a publish with 202 and a sequence number', function () {
    return request(app).post('/publish').send({ topic: 'order.confirmed', key: 'R1', payload: { a: 1 } })
      .expect(202)
      .then(function (res) {
        expect(res.body.topic).to.equal('order.confirmed');
        expect(res.body.seq).to.equal(1);
        expect(store.depth('order.confirmed')).to.equal(1);
      });
  });

  it('rejects a publish without a topic', function () {
    return request(app).post('/publish').send({ key: 'x' }).expect(400)
      .then(function (res) { expect(res.body.error).to.equal('topic_required'); });
  });

  it('accepts and drops topics nobody subscribes to', function () {
    return request(app).post('/publish').send({ topic: 'order.cancelled', key: 'R1', payload: {} })
      .expect(202)
      .then(function (res) {
        expect(res.body.dropped).to.equal(true);
        expect(store.depth('order.cancelled')).to.equal(0);
      });
  });

  it('serves healthz', function () {
    return request(app).get('/healthz').expect(200, { ok: true, service: 'bus' });
  });
});
