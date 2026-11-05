'use strict';

/*
 * "Connection pool".
 *
 * jdb has no connections, obviously. This exists because in 2019 slot holds
 * were moved to Postgres for six months (PLAT-140) and then moved back, and the
 * pool abstraction stayed because it also gives us a crude concurrency limit on
 * the slot/order write path ("only N writers at a time").
 *
 * After the 2023 split only the slots service creates a pool. orders used to
 * share it in-process; now orders calls POST /internal/slot-holds/:id/confirm
 * and that handler takes a connection from the SAME pool as the hold route.
 *
 * Usage:
 *   var conn = await pool.acquire('label');
 *   try { ... conn.collection('slots') ... } finally { conn.release(); }
 *
 * acquire() waits up to ACQUIRE_TIMEOUT_MS for a free connection then throws
 * PoolTimeout.
 */

var EventEmitter = require('events').EventEmitter;
var util = require('util');
var errors = require('./errors');
var logger = require('./logger');
var instruments = require('./telemetry/instruments');

// NOTE: config has db.poolSize (32 / 64 in prod) but nothing reads it - the
// real limit is this constant. Bumped 12 -> 16 after INC-2291 (2023-09), 16 -> 36 for peak 2024.
var SLOT_POOL_SIZE = 36;
var ACQUIRE_TIMEOUT_MS = 5000; // was 30s; cut after the 2023 "slot picker spinner" incident (KP)
var LONG_HOLD_WARN_MS = 15000;

var nextConnId = 1;

function Connection(pool) {
  this.id = nextConnId++;
  this.pool = pool;
  this.inUse = false;
  this.label = null;
  this.acquiredAt = 0;
  this.uses = 0;
}

Connection.prototype.collection = function (name) {
  return this.pool.db.collection(name);
};

Connection.prototype.release = function () {
  if (!this.inUse) {
    logger.debug('pool: double release ignored', { conn: this.id });
    return;
  }
  var held = Date.now() - this.acquiredAt;
  if (held > LONG_HOLD_WARN_MS) {
    logger.warn('pool: connection held for a long time', { conn: this.id, label: this.label, held_ms: held });
  }
  this.inUse = false;
  this.label = null;
  this.acquiredAt = 0;
  this.pool._onRelease(this);
};

function Pool(db, size) {
  EventEmitter.call(this);
  this.db = db;
  this.size = size || SLOT_POOL_SIZE;
  this.conns = [];
  this.waiters = [];
  this.timeouts = 0;
  this.acquired = 0;
  for (var i = 0; i < this.size; i++) this.conns.push(new Connection(this));
}
util.inherits(Pool, EventEmitter);

Pool.prototype._free = function () {
  for (var i = 0; i < this.conns.length; i++) {
    if (!this.conns[i].inUse) return this.conns[i];
  }
  return null;
};

Pool.prototype._checkout = function (conn, label) {
  conn.inUse = true;
  conn.label = label || 'anon';
  conn.acquiredAt = Date.now();
  conn.uses++;
  this.acquired++;
  return conn;
};

Pool.prototype.acquire = function (label, timeoutMs) {
  var self = this;
  var conn = self._free();
  if (conn) return Promise.resolve(self._checkout(conn, label));

  timeoutMs = timeoutMs || ACQUIRE_TIMEOUT_MS;
  return new Promise(function (resolve, reject) {
    var waiter = {
      label: label,
      since: Date.now(),
      resolve: resolve,
      reject: reject,
      timer: null
    };
    waiter.timer = setTimeout(function () {
      var idx = self.waiters.indexOf(waiter);
      if (idx !== -1) self.waiters.splice(idx, 1);
      self.timeouts++;
      logger.error('pool: acquire timed out', {
        label: label,
        waited_ms: Date.now() - waiter.since,
        in_use: self.inUse(),
        waiting: self.waiters.length
      });
      reject(new errors.PoolTimeout('timed out after ' + timeoutMs + 'ms waiting for pool connection (' + label + ')'));
    }, timeoutMs);
    self.waiters.push(waiter);
  });
};

Pool.prototype._onRelease = function (conn) {
  var waiter = this.waiters.shift();
  if (waiter) {
    clearTimeout(waiter.timer);
    waiter.resolve(this._checkout(conn, waiter.label));
  }
  this.emit('release', conn.id);
};

Pool.prototype.inUse = function () {
  return this.conns.filter(function (c) { return c.inUse; }).length;
};

Pool.prototype.waiting = function () {
  return this.waiters.length;
};

Pool.prototype.stats = function () {
  var now = Date.now();
  return {
    size: this.size,
    in_use: this.inUse(),
    waiting: this.waiters.length,
    acquired_total: this.acquired,
    timeouts_total: this.timeouts,
    connections: this.conns.map(function (c) {
      return {
        id: c.id,
        in_use: c.inUse,
        label: c.label,
        held_ms: c.inUse ? now - c.acquiredAt : 0,
        uses: c.uses
      };
    })
  };
};

// Helper for code that wants the try/finally done for it. Newer code uses
// this; the slot hold route predates it.
Pool.prototype.withConnection = function (label, fn) {
  var self = this;
  return self.acquire(label).then(function (conn) {
    var p;
    try {
      p = Promise.resolve(fn(conn));
    } catch (e) {
      conn.release();
      return Promise.reject(e);
    }
    return p.then(function (r) {
      conn.release();
      return r;
    }, function (e) {
      conn.release();
      throw e;
    });
  });
};

var shared = null;

function init(db, size) {
  shared = new Pool(db, size);
  instruments.observe('poolInUse', function () { return shared ? shared.inUse() : 0; });
  instruments.observe('poolWaiting', function () { return shared ? shared.waiting() : 0; });
  return shared;
}

function get() {
  if (!shared) throw new Error('pool not initialised');
  return shared;
}

module.exports = {
  init: init,
  get: get,
  Pool: Pool,
  SLOT_POOL_SIZE: SLOT_POOL_SIZE,
  ACQUIRE_TIMEOUT_MS: ACQUIRE_TIMEOUT_MS
};
