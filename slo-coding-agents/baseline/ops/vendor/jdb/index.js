/*!
 * jdb - a tiny JSON-file document store
 * (c) 2016 hollowtree, Apache License 2.0
 *
 * Vendored into larder-fulfilment 2018-03 (m.okafor). Upstream is gone, so this
 * file IS the source of truth now. Local modifications are marked "LARDER:".
 */
'use strict';

var fs = require('fs');
var path = require('path');
var EventEmitter = require('events').EventEmitter;
var util = require('util');

var DEFAULT_FLUSH_DELAY_MS = 25; // LARDER: was 0 (sync) upstream
var tmpCounter = 0;

function noop() {}

/**
 * Collection - one JSON file holding an array of documents.
 *
 * Documents are kept in memory keyed by `key` (default "id"). Reads are served
 * from memory. Writes mark the collection dirty and are flushed to disk
 * shortly after (write-behind).
 */
function Collection(db, name, opts) {
  EventEmitter.call(this);
  opts = opts || {};
  this.db = db;
  this.name = name;
  this.key = opts.key || 'id';
  this.file = path.join(db.dir, name + '.json');
  this.flushDelayMs = opts.flushDelayMs != null ? opts.flushDelayMs : db.flushDelayMs;
  this._docs = null; // lazily loaded
  this._order = [];
  this._loading = null;
  this._flushTimer = null;
  this._waiters = [];
  this.writes = 0;
  this.flushes = 0;
}
util.inherits(Collection, EventEmitter);

Collection.prototype._load = function () {
  var self = this;
  if (self._docs) return Promise.resolve(self);
  if (self._loading) return self._loading;
  self._loading = new Promise(function (resolve, reject) {
    fs.readFile(self.file, 'utf8', function (err, raw) {
      if (err && err.code !== 'ENOENT') {
        self._loading = null;
        return reject(err);
      }
      var list = [];
      if (raw && raw.trim().length) {
        try {
          list = JSON.parse(raw);
        } catch (e) {
          self._loading = null;
          e.message = 'jdb: corrupt collection ' + self.name + ': ' + e.message;
          return reject(e);
        }
      }
      if (!Array.isArray(list)) {
        // upstream supported {key: doc} maps as well
        list = Object.keys(list).map(function (k) { return list[k]; });
      }
      self._docs = Object.create(null);
      self._order = [];
      list.forEach(function (doc) {
        var id = doc[self.key];
        if (id === undefined || id === null) return;
        if (!(id in self._docs)) self._order.push(id);
        self._docs[id] = doc;
      });
      resolve(self);
    });
  });
  return self._loading;
};

// Synchronous accessors. Only valid after the collection has been loaded.
Collection.prototype.isLoaded = function () { return !!this._docs; };

Collection.prototype.peek = function (id) {
  if (!this._docs) return undefined;
  return this._docs[id];
};

Collection.prototype.peekAll = function () {
  var self = this;
  if (!self._docs) return [];
  return self._order.filter(function (id) { return id in self._docs; }).map(function (id) { return self._docs[id]; });
};

/**
 * get(id) -> Promise<doc|null>
 *
 * LARDER: returns the stored object itself, NOT a copy (deep copy was 30% of
 * CPU on the stock page in 2018). Callers must call put() after mutating so the
 * change gets flushed.
 */
Collection.prototype.get = function (id, cb) {
  var self = this;
  var p = self._load().then(function () {
    var doc = self._docs[id];
    return doc === undefined ? null : doc;
  });
  if (typeof cb === 'function') {
    p.then(function (d) { cb(null, d); }, cb);
    return undefined;
  }
  return p;
};

Collection.prototype.all = function (cb) {
  var self = this;
  var p = self._load().then(function () { return self.peekAll(); });
  if (typeof cb === 'function') {
    p.then(function (d) { cb(null, d); }, cb);
    return undefined;
  }
  return p;
};

Collection.prototype.find = function (predicate, cb) {
  var p = this.all().then(function (docs) { return docs.filter(predicate); });
  if (typeof cb === 'function') {
    p.then(function (d) { cb(null, d); }, cb);
    return undefined;
  }
  return p;
};

Collection.prototype.count = function () {
  return this._docs ? Object.keys(this._docs).length : 0;
};

/**
 * put(doc) -> Promise<doc>, resolves once the doc has been flushed.
 */
Collection.prototype.put = function (doc, cb) {
  var self = this;
  var p = self._load().then(function () {
    var id = doc[self.key];
    if (id === undefined || id === null) {
      throw new Error('jdb: document missing key "' + self.key + '" in ' + self.name);
    }
    if (!(id in self._docs)) self._order.push(id);
    self._docs[id] = doc;
    self.writes++;
    return self._scheduleFlush().then(function () { return doc; });
  });
  if (typeof cb === 'function') {
    p.then(function (d) { cb(null, d); }, cb);
    return undefined;
  }
  return p;
};

Collection.prototype.putMany = function (docs) {
  var self = this;
  return self._load().then(function () {
    docs.forEach(function (doc) {
      var id = doc[self.key];
      if (!(id in self._docs)) self._order.push(id);
      self._docs[id] = doc;
    });
    self.writes += docs.length;
    return self._scheduleFlush();
  });
};

Collection.prototype.remove = function (id) {
  var self = this;
  return self._load().then(function () {
    var existed = id in self._docs;
    delete self._docs[id];
    if (existed) {
      self._order = self._order.filter(function (x) { return x !== id; });
      return self._scheduleFlush().then(function () { return true; });
    }
    return false;
  });
};

Collection.prototype.truncate = function () {
  this._docs = Object.create(null);
  this._order = [];
  return this._scheduleFlush();
};

// LARDER: write-behind. Every put within flushDelayMs shares a single write.
Collection.prototype._scheduleFlush = function () {
  var self = this;
  return new Promise(function (resolve, reject) {
    self._waiters.push({ resolve: resolve, reject: reject });
    if (self._flushTimer) return;
    self._flushTimer = setTimeout(function () {
      self._flushTimer = null;
      var waiters = self._waiters;
      self._waiters = [];
      self._flush(function (err) {
        waiters.forEach(function (w) { err ? w.reject(err) : w.resolve(); });
      });
    }, self.flushDelayMs);
  });
};

// LARDER: tmp file + rename. Two flushes can be in flight at once; whichever
// rename lands last wins. (Upstream wrote the target file directly and we got
// truncated files on deploys.)
Collection.prototype._flush = function (cb) {
  var self = this;
  cb = cb || noop;
  var body;
  try {
    body = JSON.stringify(self.peekAll(), null, self.db.pretty ? 1 : 0);
  } catch (e) {
    return cb(e);
  }
  var tmp = self.file + '.' + process.pid + '.' + (++tmpCounter) + '.tmp';
  fs.writeFile(tmp, body, 'utf8', function (err) {
    if (err) return cb(err);
    fs.rename(tmp, self.file, function (err2) {
      if (err2) return cb(err2);
      self.flushes++;
      self.emit('flush', body.length);
      cb(null);
    });
  });
};

Collection.prototype.flushSync = function () {
  if (!this._docs) return;
  fs.writeFileSync(this.file, JSON.stringify(this.peekAll(), null, this.db.pretty ? 1 : 0), 'utf8');
};

/**
 * Db - a directory of collections.
 */
function Db(dir, opts) {
  opts = opts || {};
  this.dir = dir;
  this.pretty = !!opts.pretty;
  this.flushDelayMs = opts.flushDelayMs != null ? opts.flushDelayMs : DEFAULT_FLUSH_DELAY_MS;
  this.collections = {};
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch (e) {
    if (e.code !== 'EEXIST') throw e;
  }
}

Db.prototype.collection = function (name, opts) {
  if (!this.collections[name]) {
    this.collections[name] = new Collection(this, name, opts);
  }
  return this.collections[name];
};

Db.prototype.exists = function (name) {
  return fs.existsSync(path.join(this.dir, name + '.json'));
};

Db.prototype.isEmpty = function () {
  var files;
  try {
    files = fs.readdirSync(this.dir);
  } catch (e) {
    return true;
  }
  return files.filter(function (f) { return /\.json$/.test(f); }).length === 0;
};

Db.prototype.flushAllSync = function () {
  var self = this;
  Object.keys(self.collections).forEach(function (n) {
    try { self.collections[n].flushSync(); } catch (e) { /* best effort */ }
  });
};

module.exports = function jdb(dir, opts) {
  return new Db(dir, opts);
};
module.exports.Db = Db;
module.exports.Collection = Collection;

/*
 * CHANGES (LARDER)
 * 0.4.2-larder.3  2020-06-02 r.singh   write-behind flush, flushDelayMs option
 * 0.4.2-larder.2  2019-01-22 m.okafor  tmp+rename writes
 * 0.4.2-larder.1  2018-05-09 m.okafor  get() returns stored object (no clone)
 */
