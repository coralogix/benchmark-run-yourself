'use strict';

/*
 * Delivery slots + holds.
 *
 *   GET  /internal/slots?zone=Z1   (or ?postcode=LD1 1AA, or ?all=1 for past)
 *   POST /internal/slot-holds      { slot_id, cart_ref }
 *   GET  /internal/slot-holds/:id
 *   DELETE /internal/slot-holds/:id
 *   POST /internal/slot-holds/:id/confirm  { order_ref }      (orders, 2023)
 *   POST /internal/slot-holds/:id/release  { reason }         (orders, 2023)
 *   GET  /internal/debug/pool
 *
 * Copied from larder-fulfilment routes/slots.js in the 2023 split; the
 * confirm/release routes are new and replace the in-process calls orders
 * used to make.
 */

var express = require('express');
var slots = require('../slots');
var pool = require('../../../lib/pool');
var logger = require('../../../lib/logger');
var globals = require('../../../lib/globals');
var errors = require('../../../lib/errors');
var telemetry = require('../../../lib/telemetry');
var asyncRoute = require('../../../lib/util/asyncRoute');

var router = express.Router();

router.get('/slots', asyncRoute(async function (req, res) {
  var zone = req.query.zone;
  if (!zone && req.query.postcode) {
    zone = await slots.zoneForPostcode(req.query.postcode);
    if (!zone) return res.status(404).json({ error: 'unknown_postcode' });
  }
  var list = await slots.listSlots(zone, { includePast: req.query.all === '1' });
  res.json({ slots: list });
}));

// Hold a slot for a cart. Written before pool.withConnection() existed, so it
// manages the connection by hand.
router.post('/slot-holds', function (req, res, next) {
  var body = req.body || {};
  if (!body.slot_id) return res.status(400).json({ error: 'slot_id required' });

  pool.get().acquire('slot-hold').then(function (conn) {
    return slots.getSlot(conn, body.slot_id).then(function (slot) {
      if (!slot) {
        conn.release();
        return res.status(404).json({ error: 'slot_not_found' });
      }

      if (slot.booked >= slot.capacity) {
        globals.counters.holdsRejected++;
        logger.info('slot full', { slot_id: slot.slot_id, booked: slot.booked, capacity: slot.capacity });
        return res.status(409).json({ error: 'slot_full' });
      }

      return slots.createHold(conn, slot, body.cart_ref).then(function (hold) {
        conn.release();
        res.status(201).json({ hold_id: hold.hold_id, expires_at: hold.expires_at });
      });
    }).catch(function (err) {
      conn.release();
      throw err;
    });
  }).catch(next);
});

router.get('/slot-holds/:id', asyncRoute(async function (req, res) {
  var hold = await slots.getHold(null, req.params.id);
  if (!hold) return res.status(404).json({ error: 'hold_not_found' });
  res.json(hold);
}));

// Explicit release (storefront "change slot" button). Uses the newer helper.
router.delete('/slot-holds/:id', asyncRoute(async function (req, res) {
  var hold = await pool.get().withConnection('slot-release', function (conn) {
    return slots.releaseHold(conn, req.params.id, 'released');
  });
  if (!hold) return res.status(404).json({ error: 'hold_not_found' });
  res.json({ hold_id: hold.hold_id, status: hold.status });
}));

// orders: turn the hold into a booking. Shares the pool with the hold route
// above ("so the hold + order write are one transaction", which stopped being
// true when the order write moved to another process).
router.post('/slot-holds/:id/confirm', asyncRoute(async function (req, res) {
  var body = req.body || {};
  var holdId = req.params.id;
  if (!body.order_ref) throw new errors.BadRequest('order_ref required');
  telemetry.setAttr('larder.order_ref', body.order_ref);
  var out = await pool.get().withConnection('order-confirm:' + body.order_ref, function (conn) {
    return slots.confirmHold(conn, holdId, body.order_ref).then(function (hold) {
      return slots.getSlot(conn, hold.slot_id).then(function (slot) {
        return { hold: hold, slot: slot };
      });
    });
  });
  res.json({
    hold_id: out.hold.hold_id,
    status: out.hold.status,
    slot: slots.slotSummary(out.slot)
  });
}));

// orders: payment failed, give the unit back.
router.post('/slot-holds/:id/release', asyncRoute(async function (req, res) {
  var reason = (req.body && req.body.reason) || 'released';
  var hold = await pool.get().withConnection('slot-release:' + reason, function (conn) {
    return slots.releaseHold(conn, req.params.id, reason);
  });
  if (!hold) return res.status(404).json({ error: 'hold_not_found' });
  res.json({ hold_id: hold.hold_id, status: hold.status });
}));

router.get('/debug/pool', function (req, res) {
  res.json(pool.get().stats());
});

module.exports = router;
