'use strict';

var uuidv4 = require('uuid/v4');
var uuidv1 = require('uuid/v1');
var crypto = require('crypto');

// hold ids used to be sequential ints (pre-2019); the H- prefix stops the
// storefront from parseInt()ing them.
function holdId() {
  return 'H-' + uuidv4().replace(/-/g, '').slice(0, 16);
}

function chargeRef() {
  return 'ch_local_' + crypto.randomBytes(6).toString('hex');
}

function requestId() {
  return uuidv4();
}

function webhookId() {
  return 'wh_' + uuidv4();
}

// Time-ordered ids (uuid v1) for things we list newest-first. The v1 node id
// is random per process so two services can't collide. Probably.
function orderId() {
  return 'ord_' + uuidv1().replace(/-/g, '');
}

function paymentId() {
  return 'pay_' + uuidv1().replace(/-/g, '').slice(0, 20);
}

function ledgerEntryId(seq) {
  // seq is the line number in ledger.json; the uuid tail is for "uniqueness
  // across shards" (there are no shards)
  return 'le_' + String(seq).padStart(8, '0') + '_' + uuidv4().slice(0, 8);
}

function messageId() {
  return 'msg_' + uuidv4().replace(/-/g, '');
}

function deliveryId(topic, seq) {
  return String(topic).replace(/\./g, '-') + ':' + seq + ':' + uuidv4().slice(0, 8);
}

function pickListId() {
  return 'PL-' + Date.now().toString(36).toUpperCase() + '-' + crypto.randomBytes(2).toString('hex').toUpperCase();
}

module.exports = {
  holdId: holdId,
  chargeRef: chargeRef,
  requestId: requestId,
  webhookId: webhookId,
  orderId: orderId,
  paymentId: paymentId,
  ledgerEntryId: ledgerEntryId,
  messageId: messageId,
  deliveryId: deliveryId,
  pickListId: pickListId,
  uuid: uuidv4
};
