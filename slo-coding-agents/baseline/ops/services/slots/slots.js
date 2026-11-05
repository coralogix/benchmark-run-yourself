'use strict';

/*
 * Delivery slots and slot holds.
 *
 * A slot has `capacity` and `booked`. `booked` counts confirmed bookings AND
 * live holds: creating a hold does booked += 1; an expired or released hold
 * gives the unit back; an order consumes the hold (status -> booked) and the
 * unit stays counted.
 *
 * Holds live for config slots.holdTtlMinutes (15 in default.json, 10 via
 * config/local.js, which wins). The sweeper (jobs/holdSweeper.js)
 * expires them once a minute, so in practice a hold lives 10-11 minutes.
 *
 * Writes go through a pool connection (see lib/pool.js); reads for the slot
 * listing go straight to the db.
 *
 * 2023 split (p.ndlovu): this file moved from larder-fulfilment lib/slots.js
 * unchanged. Before the split orders called confirmHold() in-process with its
 * own pool connection; now orders calls POST /internal/slot-holds/:id/confirm
 * over HTTP and the route takes the connection. Same pool, same 16 slots.
 */

var _ = require('lodash');
var moment = require('moment');
var db = require('../../lib/db');
var errors = require('../../lib/errors');
var logger = require('../../lib/logger');
var ids = require('../../lib/util/ids');
var config = require('../../lib/config');
var globals = require('../../lib/globals');

var HOLD_STATUS = {
  HELD: 'held',
  BOOKED: 'booked',
  EXPIRED: 'expired',
  RELEASED: 'released'
};

// slots in the past are hidden from the listing unless ?all=1
function isPastDate(date) {
  return moment(date, 'YYYY-MM-DD').isBefore(moment().startOf('day'));
}

function slotView(s) {
  return {
    slot_id: s.slot_id,
    zone: s.zone,
    date: s.date,
    window: s.window,
    capacity: s.capacity,
    booked: s.booked
  };
}

function listSlots(zone, opts) {
  opts = opts || {};
  return db.collection('slots').all().then(function (all) {
    var list = all.filter(function (s) {
      if (zone && s.zone !== zone) return false;
      if (!opts.includePast && isPastDate(s.date)) return false;
      return true;
    });
    return _.sortBy(list, ['date', 'window', 'slot_id']).map(slotView);
  });
}

function zoneForPostcode(postcode) {
  var pc = String(postcode || '').toUpperCase().replace(/\s+/g, ' ').trim();
  return db.collection('zones').all().then(function (zones) {
    var hit = _.find(zones, function (z) { return (z.postcodes || []).indexOf(pc) !== -1; });
    if (hit) return hit.zone;
    // fall back to the postcode sector ("LD1 9ZZ" -> "LD1 9" -> Z1). It was the
    // outward code until OPS-311 (2022): LD6 is split between two depots.
    if (pc.indexOf(' ') < 1) return null;
    var sector = pc.slice(0, pc.indexOf(' ') + 2);
    hit = _.find(zones, function (z) {
      return (z.postcodes || []).some(function (p) { return p.slice(0, p.indexOf(' ') + 2) === sector; });
    });
    return hit ? hit.zone : null;
  });
}

function getSlot(conn, slotId) {
  return conn.collection('slots').get(slotId);
}

function getHold(conn, holdId) {
  var c = conn ? conn.collection('holds') : db.collection('holds');
  return c.get(holdId);
}

/**
 * Create a hold on a slot the caller has already checked has room.
 * Returns the hold record.
 */
function createHold(conn, slot, cartRef) {
  var ttl = config.get('slots.holdTtlMinutes', 10);
  var now = moment();
  var hold = {
    hold_id: ids.holdId(),
    slot_id: slot.slot_id,
    cart_ref: cartRef || null,
    status: HOLD_STATUS.HELD,
    created_at: now.toISOString(),
    expires_at: now.clone().add(ttl, 'minutes').toISOString(),
    order_ref: null
  };
  slot.booked = slot.booked + 1;
  globals.counters.holdsCreated++;
  return Promise.all([
    conn.collection('slots').put(slot),
    conn.collection('holds').put(hold)
  ]).then(function () { return hold; });
}

/**
 * Turn a hold into a booking. Throws HoldExpired if the hold is unknown or
 * no longer held. A hold past expires_at that the sweeper hasn't reached yet
 * is still accepted (grace period, intentional per PLAT-233).
 */
function confirmHold(conn, holdId, orderRef) {
  return conn.collection('holds').get(holdId).then(function (hold) {
    if (!hold) throw new errors.HoldExpired('unknown hold ' + holdId);
    if (hold.status === HOLD_STATUS.BOOKED && hold.order_ref === orderRef) return hold; // replay
    if (hold.status !== HOLD_STATUS.HELD) {
      throw new errors.HoldExpired('hold ' + holdId + ' is ' + hold.status);
    }
    hold.status = HOLD_STATUS.BOOKED;
    hold.order_ref = orderRef;
    hold.booked_at = new Date().toISOString();
    return conn.collection('holds').put(hold);
  });
}

/**
 * Give a hold's unit back to the slot. Used when payment fails and by the
 * sweeper.
 */
function releaseHold(conn, holdId, reason) {
  var holds = conn ? conn.collection('holds') : db.collection('holds');
  var slots = conn ? conn.collection('slots') : db.collection('slots');
  return holds.get(holdId).then(function (hold) {
    if (!hold) return null;
    if (hold.status !== HOLD_STATUS.HELD && hold.status !== HOLD_STATUS.BOOKED) return hold;
    return slots.get(hold.slot_id).then(function (slot) {
      if (slot) slot.booked = Math.max(0, slot.booked - 1);
      hold.status = reason === 'expired' ? HOLD_STATUS.EXPIRED : HOLD_STATUS.RELEASED;
      hold.released_at = new Date().toISOString();
      hold.release_reason = reason || 'released';
      return Promise.all([slot ? slots.put(slot) : null, holds.put(hold)]).then(function () { return hold; });
    });
  });
}

/**
 * Expire held holds whose expires_at is in the past. Also purges old
 * non-live holds so holds.json doesn't grow forever (6h, magic number from
 * the 2021 disk-full incident).
 */
function sweepExpired(now) {
  now = now || Date.now();
  var holds = db.collection('holds');
  return holds.all().then(function (all) {
    var expired = all.filter(function (h) {
      return h.status === HOLD_STATUS.HELD && new Date(h.expires_at).getTime() < now;
    });
    var purge = all.filter(function (h) {
      return h.status !== HOLD_STATUS.HELD && h.status !== HOLD_STATUS.BOOKED &&
        new Date(h.created_at).getTime() < now - 6 * 3600 * 1000;
    });
    return expired.reduce(function (p, h) {
      return p.then(function () { return releaseHold(null, h.hold_id, 'expired'); });
    }, Promise.resolve()).then(function () {
      return Promise.all(purge.map(function (h) { return holds.remove(h.hold_id); }));
    }).then(function () {
      if (expired.length) logger.info('slots: expired holds released', { count: expired.length });
      return { expired: expired.length, purged: purge.length };
    });
  });
}

// Compact slot description that goes into the order and the order.confirmed
// event (warehouse + notifications read it from there).
function slotSummary(s) {
  if (!s) return null;
  return { slot_id: s.slot_id, zone: s.zone, date: s.date, window: s.window };
}

// Holds per cart. config slots.maxHoldsPerCart (3) was supposed to be
// enforced in the hold route. It isn't; this is only used by debug.
// TODO(l.chen 2020-02-11): enforce
function holdsForCart(cartRef) {
  return db.collection('holds').peekAll().filter(function (h) {
    return h.cart_ref === cartRef && h.status === HOLD_STATUS.HELD;
  });
}

function holdStats() {
  var all = db.collection('holds').peekAll();
  return _.countBy(all, 'status');
}

module.exports = {
  HOLD_STATUS: HOLD_STATUS,
  listSlots: listSlots,
  zoneForPostcode: zoneForPostcode,
  getSlot: getSlot,
  getHold: getHold,
  createHold: createHold,
  confirmHold: confirmHold,
  releaseHold: releaseHold,
  sweepExpired: sweepExpired,
  holdStats: holdStats,
  holdsForCart: holdsForCart,
  slotSummary: slotSummary,
  isPastDate: isPastDate,
  slotView: slotView
};
