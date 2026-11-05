'use strict';

var http = require('http');
var expect = require('chai').expect;
var config = require('../../lib/config');
var errors = require('../../lib/errors');
var adapter = require('../../services/payments/psp/adapter');

// A tiny in-process PSP. Answers immediately.
describe('payments: PSP adapter', function () {
  var server, seen = [], oldUrl;

  before(function (done) {
    server = http.createServer(function (req, res) {
      var raw = '';
      req.on('data', function (c) { raw += c; });
      req.on('end', function () {
        var body = JSON.parse(raw);
        seen.push(body);
        if (body.token === 'tok_decline') {
          res.writeHead(402, { 'Content-Type': 'application/json' });
          return res.end('{"error":"card_declined"}');
        }
        if (body.token === 'tok_boom') {
          res.writeHead(500, { 'Content-Type': 'application/json' });
          return res.end('{"error":"psp_internal_error"}');
        }
        res.writeHead(201, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ charge_id: 'ch_' + seen.length, status: 'captured', amount_pence: body.amount_pence }));
      });
    }).listen(0, '127.0.0.1', function () {
      oldUrl = config.get('psp.url');
      config.set('psp.url', 'http://127.0.0.1:' + server.address().port);
      done();
    });
  });

  after(function (done) {
    config.set('psp.url', oldUrl);
    server.close(done);
  });

  it('captures a charge and sends GBP', function () {
    return adapter.charge({ amount_pence: 1050, order_ref: 'LDR-A1', token: 'tok_ok' }).then(function (r) {
      expect(r.status).to.equal('captured');
      expect(r.attempts).to.equal(1);
      expect(seen[seen.length - 1].currency).to.equal('GBP');
    });
  });

  it('throws PaymentDeclined on 402', function () {
    return adapter.charge({ amount_pence: 1050, order_ref: 'LDR-A2', token: 'tok_decline' }).then(function () {
      throw new Error('should not resolve');
    }, function (e) {
      expect(e).to.be.instanceOf(errors.PaymentDeclined);
    });
  });

  it('does not retry a PSP 500', function () {
    var before = seen.length;
    return adapter.charge({ amount_pence: 1050, order_ref: 'LDR-A3', token: 'tok_boom' }).then(function () {
      throw new Error('should not resolve');
    }, function (e) {
      expect(e).to.be.instanceOf(errors.PaymentError);
      expect(seen.length - before).to.equal(1);
    });
  });
});
