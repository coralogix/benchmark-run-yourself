'use strict';

// Seed normalisation for slots (was lib/bootstrap.js in fulfilment).
// seed/slots.json has day_offset, not dates; dates are fixed at seed time.

var dates = require('../../lib/util/dates');

function normaliseSlots(list, base) {
  base = base || new Date();
  return list.map(function (s) {
    return {
      slot_id: s.slot_id,
      zone: s.zone,
      date: s.date || dates.dateFromOffset(s.day_offset, base),
      window: s.window,
      capacity: s.capacity | 0,
      booked: s.booked | 0,
      day_offset: s.day_offset
    };
  });
}

module.exports = {
  normaliseSlots: normaliseSlots,
  spec: {
    slots: { file: 'slots', key: 'slot_id', normalise: function (l) { return normaliseSlots(l); } },
    zones: { file: 'zones', key: 'zone' },
    holds: { key: 'hold_id', truncate: true }
  }
};
