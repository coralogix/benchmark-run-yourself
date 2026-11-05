'use strict';

/*
 * notifications.json - one record per email we tried to send.
 *
 *   { id, order_ref, topic, template, to, status: sent|failed|skipped,
 *     message_id, error, took_ms, delivery_id, created_at }
 *
 * jdb collection keyed by id. Grows forever (no purge). Lookups by order_ref
 * scan the whole collection. (TODO(h.kaur 2023-09-11): index by order_ref)
 */

var Promise = require('bluebird');
var _ = require('lodash');
var db = require('../../lib/db');
var ids = require('../../lib/util/ids');

var COLLECTION = 'notifications';

function col() {
  return db.collection(COLLECTION);
}

function record(entry) {
  var doc = _.assign({
    id: ids.messageId(),
    created_at: new Date().toISOString()
  }, entry);
  return Promise.resolve(col().put(doc));
}

function byOrderRef(ref) {
  return Promise.resolve(col().find(function (d) { return d.order_ref === ref; }));
}

function recent(limit) {
  return Promise.resolve(col().all()).then(function (all) {
    return all.slice(-(limit || 50)).reverse();
  });
}

function stats() {
  var all = col().peekAll();
  return {
    total: all.length,
    by_status: _.countBy(all, 'status'),
    by_template: _.countBy(all, 'template')
  };
}

module.exports = { record: record, byOrderRef: byOrderRef, recent: recent, stats: stats, COLLECTION: COLLECTION };
