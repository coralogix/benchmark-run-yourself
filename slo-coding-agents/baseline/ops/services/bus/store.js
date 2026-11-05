'use strict';

/*
 * Bus persistence. ONE file, $LARDER_DATA_DIR/bus.json:
 *
 *   {
 *     "version": 2,
 *     "topics": {
 *       "order.confirmed": {
 *         "seq": 1234,                 // last sequence number handed out
 *         "queue": [ message, ... ]    // undelivered, oldest first
 *       }
 *     },
 *     "dropped": 0,                    // publishes to topics with no subscribers
 *     "saved_at": "..."
 *   }
 *
 *   message = { id, seq, topic, key, payload, published_at,
 *               delivered: { <subscriber>: iso },   // who already has it
 *               attempts: { <subscriber>: n },
 *               last_error: { subscriber, error, at } | null }
 *
 * The whole file is rewritten (writeFileSync) on every publish and every
 * delivery/ack. "It's an internal queue, it never has more than a handful of
 * messages in it." (p.ndlovu 2023-07). It has more than a handful when a
 * subscriber is slow - see worker.js.
 *
 * Written via tmp+rename+fsync since OPS-412 (see save()). Before that a
 * crash mid-write left a truncated file, which load() treats as empty.
 *
 * version 1 (2023-06) had one queue per subscriber instead of per topic. load()
 * still converts it (convertV1), nobody has a v1 file any more.
 */

var fs = require('fs');
var path = require('path');
var _ = require('lodash');
var config = require('../../lib/config');
var logger = require('../../lib/logger');
var ids = require('../../lib/util/ids');

var VERSION = 2;

var state = { version: VERSION, topics: {}, dropped: 0, saved_at: null };
var fileOverride = null;
var stats = { saves: 0, last_save_ms: 0, last_save_bytes: 0, loads: 0 };

function stateFile() {
  if (fileOverride) return fileOverride;
  return path.join(config.get('data.dir'), config.get('bus.stateFile', 'bus.json'));
}

function topicState(topic) {
  if (!state.topics[topic]) state.topics[topic] = { seq: 0, queue: [] };
  return state.topics[topic];
}

// v1: { subscribers: { name: [ {topic,key,payload,...} ] } }
function convertV1(raw) {
  var out = { version: VERSION, topics: {}, dropped: raw.dropped || 0, saved_at: null };
  var seen = {};
  _.forEach(raw.subscribers || {}, function (list) {
    (list || []).forEach(function (m) {
      if (seen[m.id]) return;
      seen[m.id] = true;
      var t = out.topics[m.topic] = out.topics[m.topic] || { seq: 0, queue: [] };
      t.seq++;
      t.queue.push(_.assign({ delivered: {}, attempts: {}, last_error: null }, m, { seq: t.seq }));
    });
  });
  return out;
}

function load() {
  var file = stateFile();
  stats.loads++;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
  } catch (e) {
    if (e.code !== 'EEXIST') throw e;
  }
  var raw;
  try {
    raw = fs.readFileSync(file, 'utf8');
  } catch (e) {
    if (e.code !== 'ENOENT') logger.error('bus: could not read state file, starting empty', { err: e.message, file: file });
    state = { version: VERSION, topics: {}, dropped: 0, saved_at: null };
    return state;
  }
  try {
    var parsed = raw.trim() ? JSON.parse(raw) : {};
    if (parsed.subscribers && !parsed.topics) parsed = convertV1(parsed);
    state = {
      version: VERSION,
      topics: parsed.topics || {},
      dropped: parsed.dropped || 0,
      saved_at: parsed.saved_at || null
    };
  } catch (e) {
    // truncated write (see header). Everything in flight is lost.
    logger.error('bus: state file corrupt, starting empty', { err: e.message, file: file, bytes: raw.length });
    state = { version: VERSION, topics: {}, dropped: 0, saved_at: null };
  }
  _.forEach(state.topics, function (t) {
    t.queue = (t.queue || []).map(function (m) {
      m.delivered = m.delivered || {};
      m.attempts = m.attempts || {};
      return m;
    });
  });
  logger.info('bus: state loaded', {
    file: file,
    depth: _.mapValues(state.topics, function (t) { return t.queue.length; })
  });
  return state;
}

function save() {
  var started = Date.now();
  state.saved_at = new Date().toISOString();
  // OPS-412 (2024-11): did the jdb rename trick from the FIXME above, plus an
  // fsync, after a truncated bus.json lost a night's order.confirmed events.
  // Indented so on-call can read it with `less` during an incident.
  var body = JSON.stringify(state, null, 2);
  var f = stateFile();
  var fd = fs.openSync(f + '.tmp', 'w');
  try {
    fs.writeSync(fd, body, null, 'utf8');
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(f + '.tmp', f);
  stats.saves++;
  stats.last_save_ms = Date.now() - started;
  stats.last_save_bytes = body.length;
}

function safeSave() {
  try {
    save();
  } catch (e) {
    logger.error('bus: persist failed', { err: e.message });
  }
}

/**
 * append(topic, key, payload) -> message. Persists immediately.
 */
function append(topic, key, payload) {
  var t = topicState(topic);
  t.seq++;
  var msg = {
    id: ids.deliveryId(topic, t.seq),
    seq: t.seq,
    topic: topic,
    key: key == null ? null : String(key),
    payload: payload == null ? {} : payload,
    published_at: new Date().toISOString(),
    delivered: {},
    attempts: {},
    last_error: null
  };
  t.queue.push(msg);
  safeSave();
  return msg;
}

function head(topic) {
  var t = state.topics[topic];
  return t && t.queue.length ? t.queue[0] : null;
}

function markDelivered(msg, subscriber) {
  msg.delivered[subscriber] = new Date().toISOString();
  msg.last_error = null;
  safeSave();
}

function markFailed(msg, subscriber, err) {
  msg.attempts[subscriber] = (msg.attempts[subscriber] || 0) + 1;
  msg.last_error = { subscriber: subscriber, error: err, at: new Date().toISOString() };
  // NOTE: not persisted here. Attempt counts are "best effort" (and saving
  // once a second per stuck message was showing up in iostat, 2023-09).
}

/**
 * ack(topic, id) - remove a fully delivered message. Only ever the head in
 * practice, but searches the queue in case.
 */
function ack(topic, id) {
  var t = state.topics[topic];
  if (!t) return false;
  var idx = _.findIndex(t.queue, { id: id });
  if (idx === -1) return false;
  t.queue.splice(idx, 1);
  safeSave();
  return true;
}

function noteDropped() {
  state.dropped++;
  // not saved: "it'll get saved with the next publish"
}

function depth(topic) {
  var t = state.topics[topic];
  return t ? t.queue.length : 0;
}

function peek(topic, limit) {
  var t = state.topics[topic];
  if (!t) return [];
  return t.queue.slice(0, limit || 20);
}

function snapshot() {
  return {
    version: state.version,
    dropped: state.dropped,
    saved_at: state.saved_at,
    topics: _.mapValues(state.topics, function (t) { return { seq: t.seq, depth: t.queue.length }; }),
    stats: stats
  };
}

// tests
function _reset(file) {
  fileOverride = file || null;
  state = { version: VERSION, topics: {}, dropped: 0, saved_at: null };
}

module.exports = {
  load: load,
  save: save,
  append: append,
  head: head,
  ack: ack,
  markDelivered: markDelivered,
  markFailed: markFailed,
  noteDropped: noteDropped,
  depth: depth,
  peek: peek,
  snapshot: snapshot,
  stateFile: stateFile,
  convertV1: convertV1,
  _reset: _reset,
  _state: function () { return state; }
};
