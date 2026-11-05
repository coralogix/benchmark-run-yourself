'use strict';

var expect = require('chai').expect;
var request = require('supertest');
var totals = require('../../services/tax/totals');
var vat = require('../../services/tax/vat');
var tax = require('../../services/tax/server');

describe('tax: canonical totals', function () {
  it('adds delivery under 4000p', function () {
    expect(totals.computeTotals([{ sku: 'A', qty: 2, unit_price_pence: 249 }], null).total_pence).to.equal(897);
  });

  it('applies the threshold after the discount', function () {
    var t = totals.computeTotals([{ sku: 'A', qty: 1, unit_price_pence: 4200 }], { code: 'X', percent_off: 10, free_delivery: false });
    expect(t.discount_pence).to.equal(420);
    expect(t.delivery_fee_pence).to.equal(399);
  });

  it('rounds the discount half-up', function () {
    expect(totals.discountFor(150, { percent_off: 1 })).to.equal(2);
  });
});

describe('tax: VAT', function () {
  it('extracts VAT from a VAT-inclusive standard-rated line', function () {
    var l = vat.lineVat({ sku: 'S', qty: 1, unit_price_pence: 120, vat_band: 'standard' });
    expect(l.vat_pence).to.equal(20);
    expect(l.net_pence).to.equal(100);
  });

  it('zero-rated lines carry no VAT and aliases are understood', function () {
    expect(vat.lineVat({ sku: 'Z', qty: 3, unit_price_pence: 99, vat_band: 'Z' }).vat_pence).to.equal(0);
  });
});

describe('tax: routes', function () {
  var app = tax.buildApp();

  it('POST /internal/tax/totals reports expected total on mismatch', function () {
    return request(app).post('/internal/tax/totals')
      .send({ items: [{ sku: 'A', qty: 1, unit_price_pence: 100 }], coupon: null, client_total_pence: 500 })
      .expect(200).then(function (res) {
        expect(res.body.ok).to.equal(false);
        expect(res.body.expected_total_pence).to.equal(499);
      });
  });

  it('POST /internal/tax/vat rejects an unknown band', function () {
    return request(app).post('/internal/tax/vat')
      .send({ items: [{ sku: 'A', qty: 1, unit_price_pence: 100, vat_band: 'luxury' }] })
      .expect(400);
  });
});
