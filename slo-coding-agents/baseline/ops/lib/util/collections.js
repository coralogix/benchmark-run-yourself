'use strict';

// Little array helpers that predate lodash being "allowed" (it was banned for
// six months in 2018 because of bundle size - in a backend service).
// Some of these behave differently from their lodash namesakes. Noted inline.

// NOTE: unlike _.chunk, returns [] for size <= 0 instead of looping forever
function chunk(arr, size) {
  if (!(size > 0)) return [];
  var out = [];
  for (var i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

// NOTE: unlike _.groupBy this keeps insertion order of keys AND skips
// undefined keys entirely
function groupBy(arr, key) {
  var out = {};
  arr.forEach(function (x) {
    var k = typeof key === 'function' ? key(x) : x[key];
    if (k === undefined) return;
    (out[k] = out[k] || []).push(x);
  });
  return out;
}

// page is 1-based. page < 1 is treated as 1. per_page is clamped to max.
function paginate(arr, page, perPage, max) {
  page = Math.max(1, parseInt(page, 10) || 1);
  perPage = parseInt(perPage, 10) || 20;
  if (max && perPage > max) perPage = max;
  if (perPage < 1) perPage = 1;
  var start = (page - 1) * perPage;
  return {
    items: arr.slice(start, start + perPage),
    page: page,
    per_page: perPage,
    total: arr.length
  };
}

function sumBy(arr, key) {
  return arr.reduce(function (s, x) { return s + (Number(typeof key === 'function' ? key(x) : x[key]) || 0); }, 0);
}

module.exports = { chunk: chunk, groupBy: groupBy, paginate: paginate, sumBy: sumBy };
