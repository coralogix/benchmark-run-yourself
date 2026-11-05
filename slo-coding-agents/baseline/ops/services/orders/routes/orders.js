'use strict';

/*
 *   POST  /internal/orders                 create (see ../create.js)
 *   GET   /internal/orders/:ref            one order
 *   GET   /internal/orders?user_id=&page=&per_page=
 *   PATCH /internal/orders/:ref/status     warehouse -> dispatched
 *
 * Listing has two modes, decided by whether `page` is present AT ALL:
 *
 *   ?page=N            the paginated API (2022, ACC-91): filter by user_id,
 *                      newest first, {orders,page,per_page,total}
 *   no page param      "v1 compat" (2021, ACC-33): the accounts app of the
 *                      time fetched everything and filtered client-side, so
 *                      this branch returns EVERY order in the system and
 *                      ignores user_id entirely. The response is shaped like
 *                      a single page so the new client code "just works".
 *                      orders.legacyListLimit caps it if > 0 (it is 0).
 *
 * TODO(l.chen 2022-05-17): remove v1 compat once accounts passes page=1.
 * TODO(l.chen 2023-03-02): accounts still doesn't. Asked in #commerce.
 */

var express = require('express');
var _ = require('lodash');
var config = require('../../../lib/config');
var asyncRoute = require('../../../lib/util/asyncRoute');
var collections = require('../../../lib/util/collections');
var errors = require('../../../lib/errors');
var telemetry = require('../../../lib/telemetry');
var logger = require('../../../lib/logger');
var create = require('../create');
var store = require('../store');
var views = require('../views');

var router = express.Router();

router.post('/orders', asyncRoute(async function (req, res) {
  await telemetry.withSpan('orders.create', { 'larder.order_ref': String((req.body && req.body.order_ref) || '') }, async function (span) {
    try {
      var result = await create.createOrder(req.body);
      span.setAttribute('http.response.status_code', result.status);
      res.status(result.status).json(result.body);
    } catch (e) {
      if (!(e && e.status && e.status < 500)) telemetry.inc('orders', { outcome: 'error' });
      throw e;
    }
  });
}));

router.get('/orders', asyncRoute(async function (req, res) {
  var q = req.query || {};
  var hasPage = Object.prototype.hasOwnProperty.call(q, 'page');

  if (!hasPage) {
    // v1 compat - see header. user_id is NOT applied here.
    var everything = await new Promise(function (resolve, reject) {
      store.listAllCb(function (err, list) { return err ? reject(err) : resolve(list); });
    });
    var cap = config.get('orders.legacyListLimit', 0);
    if (cap > 0) everything = everything.slice(0, cap);
    telemetry.setAttr('larder.orders.legacy_list', everything.length);
    if (q.user_id) logger.debug('orders: v1 compat list called with user_id', { user_id: q.user_id, returned: everything.length });
    return res.json({
      orders: everything.map(views.compactView),
      page: 1,
      per_page: everything.length,
      total: everything.length
    });
  }

  var list = q.user_id ? await store.byUser(q.user_id) : store.sortNewestFirst(await store.all());
  var p = collections.paginate(list, q.page, q.per_page || config.get('orders.defaultPerPage', 20), config.get('orders.maxPerPage', 100));
  res.json({
    orders: p.items.map(views.publicView),
    page: p.page,
    per_page: p.per_page,
    total: p.total
  });
}));

router.get('/orders/:ref', asyncRoute(async function (req, res) {
  var o = await store.findByRef(req.params.ref);
  if (!o) throw new errors.NotFound('order not found');
  res.json(views.publicView(o));
}));

router.patch('/orders/:ref/status', asyncRoute(async function (req, res) {
  var body = req.body || {};
  if (!body.status) throw new errors.BadRequest('status required');
  var o = await create.setStatus(req.params.ref, _.pick(body, ['status', 'dispatched_at', 'carrier']));
  res.json({ order_ref: o.order_ref, status: o.status, dispatched_at: o.dispatched_at || null });
}));

// Fulfilment-era alias: POST /internal/orders/:ref/dispatch used to do the
// whole dispatch in-process. Now it only flips the status - stock, the
// pick list and order.dispatched are warehouse's job (POST
// /internal/dispatch/:ref on warehouse). Kept for the ops runbook RB-4.
router.post('/orders/:ref/dispatch', asyncRoute(async function (req, res) {
  var o = await create.setStatus(req.params.ref, { status: 'dispatched', carrier: req.body && req.body.carrier });
  logger.warn('orders: legacy dispatch alias used; stock and events were NOT updated', { order_ref: o.order_ref });
  res.json({ status: o.status, order_ref: o.order_ref, dispatched_at: o.dispatched_at });
}));

module.exports = router;
