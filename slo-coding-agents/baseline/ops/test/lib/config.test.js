'use strict';

var expect = require('chai').expect;
var path = require('path');
var config = require('../../lib/config');

describe('lib/config', function () {
  after(function () {
    config.reload(process.env);
  });

  it('maps <NAME>_URL env vars onto peers', function () {
    var cfg = config.load({ INVENTORY_URL: 'http://127.0.0.1:9999/', EDGE_API_URL: 'http://127.0.0.1:8000' }, { service: 'orders' });
    expect(cfg.peers.inventory).to.equal('http://127.0.0.1:9999');
    expect(cfg.peers.edge_api).to.equal('http://127.0.0.1:8000');
  });

  it('substitutes {service} in the default data dir', function () {
    var cfg = config.load({}, { service: 'ledger' });
    expect(cfg.data.dir).to.equal(path.resolve(process.cwd(), 'data/ledger'));
  });

  it('applies config/local.js after env vars', function () {
    // documented in lib/config.js header - local.js wins for psp.*
    var cfg = config.load({ PSP_TIMEOUT_MS: '9000' }, { service: 'payments' });
    expect(cfg.psp.timeoutMs).to.equal(4000);
  });
});
