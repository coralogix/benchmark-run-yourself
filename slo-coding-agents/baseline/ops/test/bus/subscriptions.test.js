'use strict';

var path = require('path');
var expect = require('chai').expect;
var subscriptions = require('../../services/bus/subscriptions');
var config = require('../../lib/config');

describe('bus: subscriptions', function () {
  afterEach(function () {
    delete process.env.TEST_SUB_URL;
    subscriptions.load();
  });

  it('loads the committed subscription file in the documented order', function () {
    var subs = subscriptions.load(path.join(config.CONFIG_DIR, 'bus-subscriptions.json'));
    expect(subs['order.confirmed'].map(function (s) { return s.name; }))
      .to.deep.equal(['webhooks', 'notifications', 'warehouse', 'ledger', 'loyalty']);
    expect(subs['order.dispatched'].map(function (s) { return s.name; }))
      .to.deep.equal(['webhooks', 'notifications']);
    expect(subs._comment).to.equal(undefined);
  });

  it('prefers the url_env variable over config peers', function () {
    subscriptions._set({ 't.x': [{ name: 'test-sub', url_env: 'TEST_SUB_URL', peer: 'ledger', path: '/internal/events' }] });
    var sub = subscriptions.forTopic('t.x')[0];
    expect(subscriptions.urlFor(sub)).to.equal(config.peer('ledger') + '/internal/events');
    process.env.TEST_SUB_URL = 'http://127.0.0.1:9999/';
    expect(subscriptions.urlFor(sub)).to.equal('http://127.0.0.1:9999/internal/events');
  });
});
