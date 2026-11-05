'use strict';

var moment = require('moment');

// All slot dates are local (Europe/London in prod, whatever the box is in
// dev). moment() without tz on purpose - see INC-1502 for why we stopped
// using moment-timezone.
function today() {
  return moment().format('YYYY-MM-DD');
}

function dateFromOffset(dayOffset, base) {
  return moment(base || undefined).startOf('day').add(dayOffset || 0, 'days').format('YYYY-MM-DD');
}

function nowIso() {
  return new Date().toISOString();
}

function addMinutesIso(minutes, from) {
  return moment(from || undefined).add(minutes, 'minutes').toISOString();
}

function isPast(iso) {
  return moment(iso).isBefore(moment());
}

// 600000 = 10 minutes, kept for the v1 routes which pass ms around
var TEN_MINUTES = 600000;

module.exports = {
  today: today,
  dateFromOffset: dateFromOffset,
  nowIso: nowIso,
  addMinutesIso: addMinutesIso,
  isPast: isPast,
  TEN_MINUTES: TEN_MINUTES
};
