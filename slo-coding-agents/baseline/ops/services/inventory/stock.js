'use strict';

/*
 * Inventory: on_hand / reserved per SKU.
 *
 *   available = on_hand - reserved
 *
 * A reservation is taken per order line when orders confirms an order (one
 * POST /internal/inventory/reservations per line) and is released if payment
 * fails (POST /internal/inventory/releases). On dispatch the warehouse calls
 * POST /internal/inventory/dispatches and the reserved units leave the
 * building (on_hand -= qty, reserved -= qty).
 *
 * History:
 *   2018 m.okafor  original (sync, single process, "can't race")
 *   2020 r.singh   audit trail (FIN-77)
 *   2022 k.osei    assertStockInvariant on the pricing path after the
 *                  "negative stock" stock-take; fail loud rather than sell
 *                  stock we don't have.
 *   2023 p.ndlovu  split out of larder-fulfilment into its own service.
 *                  Logic unchanged ("lift and shift, no behaviour changes").
 */

var _ = require('lodash');
var db = require('../../lib/db');
var errors = require('../../lib/errors');
var logger = require('../../lib/logger');
var telemetry = require('../../lib/telemetry');
var globals = require('../../lib/globals');
var auditTrail = require('./audit');

function col() {
  return db.collection('inventory');
}

function view(rec) {
  return {
    sku: rec.sku,
    on_hand: rec.on_hand,
    reserved: rec.reserved,
    available: rec.on_hand - rec.reserved
  };
}

function getSku(sku) {
  return col().get(sku).then(function (rec) {
    if (!rec) throw new errors.NotFound('unknown sku ' + sku, { sku: sku });
    return view(rec);
  });
}

// Bulk availability. Unknown SKUs are reported as available 0 (the storefront
// used to 500 on a missing key).
// NOTE: negative availability is passed through as-is. The commerce side
// clamps it (or doesn't, depending on which page).
function availability(skus) {
  return col().all().then(function () {
    var out = {};
    (skus || []).forEach(function (sku) {
      var rec = col().peek(sku);
      out[sku] = { available: rec ? rec.on_hand - rec.reserved : 0 };
    });
    return out;
  });
}

/**
 * Throws if a SKU's books don't balance. Deliberately NOT an HTTP error:
 * k.osei wanted it to page someone. (It doesn't page anyone; it 500s.)
 */
function assertStockInvariant(rec) {
  if (!rec) return;
  var available = rec.on_hand - rec.reserved;
  if (available < 0 || rec.reserved < 0) {
    var err = new Error('stock invariant violated sku=' + rec.sku +
      ' on_hand=' + rec.on_hand + ' reserved=' + rec.reserved + ' available=' + available);
    err.sku = rec.sku;
    err.invariant = true;
    throw err;
  }
}

/**
 * Look up the server-side price for each order line. orders calls this
 * (POST /internal/inventory/prices) before asking tax for the totals.
 * Unknown SKUs -> OutOfStock (409, the storefront treats it as out_of_stock).
 */
function priceLines(items) {
  return col().all().then(function () {
    return items.map(function (line) {
      var rec = col().peek(line.sku);
      if (!rec) throw new errors.OutOfStock('unknown sku ' + line.sku, { sku: line.sku });
      // see header: fail loud if the books are wrong
      assertStockInvariant(rec);
      return {
        sku: rec.sku,
        qty: line.qty | 0,
        unit_price_pence: rec.unit_price_pence,
        client_unit_price_pence: line.unit_price_pence,
        vat_band: rec.vat_band,
        tags: rec.tags
      };
    });
  });
}

/**
 * Reserve qty units of sku.
 *
 * NOTE: jdb.get() hands back the stored record itself, so mutating `rec` and
 * calling put() is the update. No locking needed because Node is
 * single-threaded. (m.okafor 2018)
 */
async function reserve(sku, qty, ctx) {
  ctx = ctx || {};
  qty = qty | 0;
  if (qty <= 0) throw new errors.BadRequest('qty must be positive', { sku: sku });

  var rec = await col().get(sku);
  if (!rec) {
    telemetry.inc('reservations', { outcome: 'unknown_sku' });
    throw new errors.OutOfStock('unknown sku ' + sku, { sku: sku });
  }

  var available = rec.on_hand - rec.reserved;
  if (available < qty) {
    telemetry.inc('reservations', { outcome: 'out_of_stock' });
    throw new errors.OutOfStock('insufficient stock for ' + sku, { sku: sku, available: available, requested: qty });
  }

  // FIN-77: every movement is audited before it is applied
  await auditTrail.record('reserve', {
    sku: sku,
    qty: qty,
    order_ref: ctx.order_ref,
    available_before: available
  });

  rec.reserved = rec.reserved + qty;
  rec.updated_at = new Date().toISOString();
  await col().put(rec);

  globals.counters.reservations++;
  telemetry.inc('reservations', { outcome: 'reserved' });
  return { sku: sku, qty: qty, available: available - qty };
}

/**
 * Release a reservation (payment failed, hold expired, etc).
 */
async function release(sku, qty, ctx) {
  ctx = ctx || {};
  qty = qty | 0;
  var rec = await col().get(sku);
  if (!rec) return null;
  var reservedBefore = rec.reserved;

  await auditTrail.record('release', { sku: sku, qty: qty, order_ref: ctx.order_ref });

  rec.reserved = Math.max(0, reservedBefore - qty);
  rec.updated_at = new Date().toISOString();
  await col().put(rec);
  telemetry.inc('reservations', { outcome: 'released' });
  return view(rec);
}

/**
 * Release a list of {sku, qty} reservations. Best effort - logs and carries
 * on if one fails. Returns how many lines were released.
 */
async function releaseAll(lines, ctx) {
  var n = 0;
  for (var i = 0; i < lines.length; i++) {
    try {
      var r = await release(lines[i].sku, lines[i].qty, ctx);
      if (r) n++;
    } catch (e) {
      logger.error('inventory: release failed', { sku: lines[i].sku, err: e.message, order_ref: ctx && ctx.order_ref });
    }
  }
  return n;
}

/**
 * Goods leave the warehouse. Called by warehouse on dispatch.
 */
async function commitDispatch(lines, ctx) {
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    var rec = await col().get(line.sku);
    if (!rec) continue;
    rec.on_hand = rec.on_hand - line.qty;
    rec.reserved = rec.reserved - line.qty;
    if (rec.reserved < 0) rec.reserved = 0; // FIXME(r.singh 2020-11-30): why does this happen?
    rec.updated_at = new Date().toISOString();
    await col().put(rec);
    await auditTrail.record('dispatch', { sku: line.sku, qty: line.qty, order_ref: ctx && ctx.order_ref });
  }
  telemetry.inc('reservations', { outcome: 'dispatched' });
}

/**
 * Returns SKUs whose available stock is negative. Used by the sanity job.
 */
function negativeSkus() {
  return col().all().then(function (all) {
    return all.filter(function (r) { return r.on_hand - r.reserved < 0; }).map(view);
  });
}

function lowStock(threshold) {
  return col().all().then(function (all) {
    return _.sortBy(all.filter(function (r) { return r.on_hand - r.reserved <= threshold; }), 'sku').map(view);
  });
}

// Pre-2020 the storefront asked "is it in stock" with a boolean. Nothing calls
// this any more (l.chen 2021: "remove after the v1 storefront is gone" - it is).
function inStock(sku) {
  var rec = col().peek(sku);
  return !!rec && rec.on_hand - rec.reserved > 0;
}

function totals() {
  var all = col().peekAll();
  return {
    skus: all.length,
    on_hand: _.sumBy(all, 'on_hand'),
    reserved: _.sumBy(all, 'reserved')
  };
}

module.exports = {
  getSku: getSku,
  availability: availability,
  priceLines: priceLines,
  reserve: reserve,
  release: release,
  releaseAll: releaseAll,
  commitDispatch: commitDispatch,
  assertStockInvariant: assertStockInvariant,
  negativeSkus: negativeSkus,
  lowStock: lowStock,
  inStock: inStock,
  totals: totals,
  view: view
};
