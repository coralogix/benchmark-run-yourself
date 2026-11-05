'use strict';

process.env.NODE_ENV = 'test';

var os = require('os');
var fs = require('fs');
var path = require('path');
var expect = require('chai').expect;
var request = require('supertest');

var config = require('../../lib/config');
var db = require('../../lib/db');
var svc = require('../../services/inventory/server');
var stock = require('../../services/inventory/stock');
var seed = require('../../services/inventory/seed');

function fixtures() {
  return seed.normaliseInventory([
    { sku: 'SKU-T1', on_hand: 10, reserved: 0, unit_price_pence: 249, vat_band: 'zero' },
    { sku: 'SKU-T2', on_hand: 3, reserved: 1, unit_price_pence: 1099, vat_band: 'standard', tags: ['3for2'] },
    { sku: 'SKU-T3', on_hand: 100, reserved: 0, unit_price_pence: 50 }
  ]);
}

describe('inventory service', function () {
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
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-inv-'));
    dirs.push(tmp);
    config.set('data.dir', tmp);
    var d = db.open(tmp, { inventory: 'sku' });
    return d.collection('inventory').putMany(fixtures()).then(function () {
      app = svc.buildApp();
    });
  });

  it('serves /healthz with the service name', function () {
    return request(app).get('/healthz').expect(200, { ok: true, service: 'inventory' });
  });

  it('reports availability, unknown skus as 0', function () {
    return request(app).post('/internal/inventory/availability')
      .send({ skus: ['SKU-T1', 'SKU-T2', 'NOPE'] })
      .expect(200)
      .then(function (res) {
        expect(res.body).to.deep.equal({
          'SKU-T1': { available: 10 },
          'SKU-T2': { available: 2 },
          NOPE: { available: 0 }
        });
      });
  });

  it('returns a single sku view and 404s unknown skus', function () {
    return request(app).get('/internal/inventory/SKU-T2').expect(200).then(function (res) {
      expect(res.body).to.deep.equal({ sku: 'SKU-T2', on_hand: 3, reserved: 1, available: 2 });
      return request(app).get('/internal/inventory/NOPE').expect(404);
    }).then(function (res) {
      expect(res.body.error).to.equal('not_found');
    });
  });

  it('prices lines with server-side unit prices', function () {
    return request(app).post('/internal/inventory/prices')
      .send({ items: [{ sku: 'SKU-T2', qty: 2, unit_price_pence: 1 }] })
      .expect(200)
      .then(function (res) {
        expect(res.body.items).to.deep.equal([
          { sku: 'SKU-T2', qty: 2, unit_price_pence: 1099, vat_band: 'standard', tags: ['3for2'] }
        ]);
      });
  });

  it('treats an unknown sku in pricing as out_of_stock', function () {
    return request(app).post('/internal/inventory/prices')
      .send({ items: [{ sku: 'GONE', qty: 1 }] })
      .expect(409, { error: 'out_of_stock', sku: 'GONE' });
  });

  it('reserves stock and reduces availability', function () {
    return request(app).post('/internal/inventory/reservations')
      .send({ sku: 'SKU-T1', qty: 4, order_ref: 'ORD-1' })
      .expect(201)
      .then(function (res) {
        expect(res.body).to.deep.equal({ sku: 'SKU-T1', qty: 4, available: 6 });
        return stock.getSku('SKU-T1');
      })
      .then(function (v) {
        expect(v.reserved).to.equal(4);
      });
  });

  it('rejects a reservation larger than available', function () {
    return request(app).post('/internal/inventory/reservations')
      .send({ sku: 'SKU-T2', qty: 3, order_ref: 'ORD-2' })
      .expect(409, { error: 'out_of_stock', sku: 'SKU-T2' });
  });

  it('releases and dispatches reservations', function () {
    return request(app).post('/internal/inventory/releases')
      .send({ order_ref: 'ORD-3', items: [{ sku: 'SKU-T2', qty: 1 }, { sku: 'NOPE', qty: 1 }] })
      .expect(200, { released: 1 })
      .then(function () {
        return stock.reserve('SKU-T3', 5, { order_ref: 'ORD-4' });
      })
      .then(function () {
        return request(app).post('/internal/inventory/dispatches')
          .send({ order_ref: 'ORD-4', items: [{ sku: 'SKU-T3', qty: 5 }] })
          .expect(200, { ok: true });
      })
      .then(function () {
        return stock.getSku('SKU-T3');
      })
      .then(function (v) {
        expect(v).to.deep.equal({ sku: 'SKU-T3', on_hand: 95, reserved: 0, available: 95 });
      });
  });

  it('lists low stock under a threshold', function () {
    return request(app).get('/internal/inventory/low-stock?threshold=5').expect(200).then(function (res) {
      expect(res.body.items.map(function (i) { return i.sku; })).to.deep.equal(['SKU-T2']);
    });
  });

  it('serves the v1 scanner format (on_hand, plain text)', function () {
    return request(app).get('/v1/stock/SKU-T2').expect(200).then(function (res) {
      expect(res.text).to.equal('SKU-T2:3');
      expect(res.headers.deprecation).to.equal('true');
    });
  });

  it('assertStockInvariant accepts balanced books', function () {
    expect(function () { stock.assertStockInvariant({ sku: 'X', on_hand: 2, reserved: 2 }); }).to.not.throw();
  });
});
