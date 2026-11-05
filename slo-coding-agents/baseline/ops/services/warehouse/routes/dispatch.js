'use strict';

/*
 *   POST /internal/dispatch/:ref   { carrier? }  -> 200 { order_ref, status: "dispatched" }
 *                                  404 {"error":"not_found"} / 409 {"error":"not_confirmed"} / 502
 *
 * Called by the warehouse floor (loadgen in the bench) ~3 minutes after
 * confirmation.
 */

var express = require('express');
var dispatch = require('../dispatch');
var telemetry = require('../../../lib/telemetry');

var router = express.Router();

router.post('/dispatch/:ref', function (req, res, next) {
  var ref = req.params.ref;
  telemetry.setAttr('larder.order_ref', ref);
  dispatch.dispatch(ref, { carrier: req.body && req.body.carrier }).then(function (r) {
    res.json({ order_ref: r.order_ref, status: r.status });
  }, next);
});

module.exports = router;
