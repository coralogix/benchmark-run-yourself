'use strict';

/*
 * /v1 - the original order API (2017). Mounted by the orders service for:
 *   - the ops "order lookup" bookmarklet (GET /v1/orders/:ref)
 *   - ops resend (POST /v1/orders/:ref/resend)
 *   - runbook admin flags (/v1/admin/*)
 *
 * The warehouse scanner's /v1/stock lives on the inventory service now
 * (legacy/v1/stock.js is mounted there). The scanner firmware still points
 * at the old fulfilment hostname, which the mesh maps to... nobody knows.
 *
 * POST /v1/orders was turned off in 2020 (returns 410) but the handler is
 * still here in case "a partner still uses it".
 */

var express = require('express');
var orders = require('./orders');
var admin = require('./admin');

var router = express.Router();

router.use(function (req, res, next) {
  res.setHeader('Deprecation', 'true');
  res.setHeader('Sunset', 'Sat, 31 Dec 2022 23:59:59 GMT');
  next();
});

router.use('/orders', orders);
router.use('/admin', admin);

module.exports = router;
