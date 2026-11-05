'use strict';

var fs = require('fs');
var expect = require('chai').expect;
var request = require('supertest');
var tmp = require('../support/tmp');
var config = require('../../lib/config');
var fraud = require('../../services/fraud/server');
var rules = require('../../services/fraud/rules');
var history = require('../../services/fraud/history');

describe('fraud: scoring', function () {
  var dir, app, oldReviewAt;

  before(function () {
    dir = tmp.tmpDir('fraud');
    config.set('data.dir', dir);
    oldReviewAt = config.get('fraud.velocityReviewAt');
    app = fraud.buildApp();
  });
  after(function () {
    config.set('fraud.velocityReviewAt', oldReviewAt);
    tmp.rmrf(dir);
  });
  beforeEach(function () {
    config.set('data.dir', dir);
    history._reset();
  });

  it('allows an ordinary payment', function () {
    return request(app).post('/internal/fraud/score')
      .send({ order_ref: 'LDR-1', email: 'ravi.khan3@example.test', user_id: 'u-0003', amount_pence: 4599, token: 'tok_ok' })
      .expect(200).then(function (res) {
        expect(res.body.decision).to.equal('allow');
        expect(res.body.reasons).to.deep.equal([]);
      });
  });

  it('blocks a blocked email domain', function () {
    var r = rules.score({ order_ref: 'LDR-2', email: 'someone@fraud.example', amount_pence: 1000, token: 'tok_ok' });
    expect(r.decision).to.equal('block');
    expect(r.reasons).to.include('blocked_domain');
  });

  it('blocks the QA fraud token', function () {
    var r = rules.score({ order_ref: 'LDR-3', email: 'qa@example.test', amount_pence: 1000, token: 'tok_fraud' });
    expect(r.decision).to.equal('block');
  });

  it('flags velocity for review', function () {
    config.set('fraud.velocityReviewAt', 2);
    var last;
    for (var i = 0; i < 3; i++) {
      last = rules.score({ order_ref: 'LDR-V' + i, email: 'theo.fofana2@example.test', amount_pence: 2000, token: 'tok_ok' });
    }
    config.set('fraud.velocityReviewAt', oldReviewAt);
    expect(last.reasons).to.include('velocity_review');
  });

  it('records each scored payment in the history file', function () {
    rules.score({ order_ref: 'LDR-H1', email: 'a@example.test', amount_pence: 2000, token: 'tok_ok' });
    rules.score({ order_ref: 'LDR-H2', email: 'b@example.test', amount_pence: 2000, token: 'tok_ok' });
    var saved = JSON.parse(fs.readFileSync(history.file(), 'utf8'));
    expect(saved.map(function (r) { return r.order_ref; })).to.deep.equal(['LDR-H1', 'LDR-H2']);
  });

  it.skip('velocity window excludes records older than the window (flaky: wall clock, j.watts 2023-08-02)', function () {
    // relied on faking Date with sinon timers, which also froze moment() in
    // signals.js and made it time out on CI half the time
  });
});
