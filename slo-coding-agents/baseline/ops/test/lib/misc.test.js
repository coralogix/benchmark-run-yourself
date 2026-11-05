'use strict';

var expect = require('chai').expect;
var sinon = require('sinon');
var express = require('express');
var bodyParser = require('body-parser');
var request = require('supertest');
var retryIt = require('../../vendor/retry-it');
var busClient = require('../../lib/bus-client');

describe('vendor/retry-it', function () {
  it('retries until the function succeeds', function () {
    var calls = 0;
    return retryIt(function () {
      calls++;
      return calls < 3 ? Promise.reject(new Error('nope')) : Promise.resolve('ok');
    }, { retries: 3, minTimeout: 1 }).then(function (r) {
      expect(r).to.equal('ok');
      expect(calls).to.equal(3);
    });
  });
});

describe('lib/bus-client', function () {
  function appWith(handlers) {
    var app = express();
    app.use(bodyParser.json());
    app.use(busClient.subscriber(handlers));
    return app;
  }

  it('acks topics it has no handler for', function () {
    return request(appWith({})).post('/internal/events').send({ topic: 'x.y', payload: {} }).expect(200).then(function (res) {
      expect(res.body.ignored).to.equal(true);
    });
  });

  it('passes the payload to the topic handler', function () {
    var seen = null;
    var app = appWith({ 'order.confirmed': function (p) { seen = p; return 'done'; } });
    return request(app).post('/internal/events')
      .send({ topic: 'order.confirmed', key: 'R1', payload: { order_ref: 'R1' }, delivery_id: 'd1' })
      .expect(200).then(function () {
        expect(seen.order_ref).to.equal('R1');
      });
  });

  it('publish resolves false instead of throwing when the bus is down', function () {
    var fake = { post: sinon.stub().rejects(new Error('ECONNREFUSED')) };
    busClient._setClient(fake);
    return busClient.publish('order.confirmed', 'R1', {}).then(function (ok) {
      expect(ok).to.equal(false);
      busClient._setClient(null);
    });
  });
});
