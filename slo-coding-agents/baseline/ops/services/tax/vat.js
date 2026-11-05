'use strict';

/*
 * Line VAT. UK bands:
 *
 *   zero      0%   (most food)
 *   reduced   5%   (nothing we sell, as far as anyone knows)
 *   standard  20%  (confectionery, soft drinks, alcohol, household)
 *
 * Shelf prices INCLUDE VAT (config tax.pricesIncludeVat, always true), so the
 * VAT in a gross line total is   gross * rate / (100 + rate),  rounded half-up
 * per LINE (HMRC allows per-line or per-invoice; finance picked per-line in
 * FIN-140, 2022, and then never used it because receipts don't show VAT yet -
 * see the backlog).
 *
 * Not called by anything at baseline. POST /internal/tax/vat exists for the
 * "VAT receipts" project.
 *
 * NOTE: discounts are NOT apportioned across lines here. If you need VAT on a
 * discounted basket you have to work out the apportionment yourself (FIN-140
 * says "pro rata by gross line value").
 */

var config = require('../../lib/config');

var DEFAULT_RATES = { zero: 0, reduced: 5, standard: 20 };
// 2019 data import used these spellings; still in some inventory records
var BAND_ALIASES = { Z: 'zero', R: 'reduced', S: 'standard', exempt: 'zero', std: 'standard' };

function rates() {
  return config.get('tax.rates') || DEFAULT_RATES;
}

function normaliseBand(band) {
  if (!band) return 'standard';
  var b = String(band);
  if (BAND_ALIASES[b]) return BAND_ALIASES[b];
  b = b.toLowerCase();
  return rates()[b] !== undefined ? b : null;
}

// half-up of n / d for non-negative integers (same trick as totals.js)
function roundHalfUpDiv(n, d) {
  return Math.floor((2 * n + d) / (2 * d));
}

function vatForGross(grossPence, ratePct) {
  if (!ratePct) return 0;
  return roundHalfUpDiv(grossPence * ratePct, 100 + ratePct);
}

/**
 * lineVat({ sku, qty, unit_price_pence, vat_band })
 *   -> { sku, qty, vat_band, rate, gross_pence, vat_pence, net_pence }
 * Throws (BadRequest-ish plain Error with .status=400) on an unknown band.
 */
function lineVat(line) {
  var band = normaliseBand(line.vat_band);
  if (band === null) {
    var e = new Error('unknown vat_band ' + line.vat_band);
    e.status = 400;
    e.code = 'bad_request';
    throw e;
  }
  var rate = rates()[band];
  var gross = (line.unit_price_pence | 0) * (line.qty | 0);
  var vat = vatForGross(gross, rate);
  return {
    sku: line.sku,
    qty: line.qty | 0,
    vat_band: band,
    rate: rate,
    gross_pence: gross,
    vat_pence: vat,
    net_pence: gross - vat
  };
}

function basketVat(lines) {
  var out = (lines || []).map(lineVat);
  var byBand = {};
  out.forEach(function (l) {
    byBand[l.vat_band] = byBand[l.vat_band] || { gross_pence: 0, vat_pence: 0, net_pence: 0 };
    byBand[l.vat_band].gross_pence += l.gross_pence;
    byBand[l.vat_band].vat_pence += l.vat_pence;
    byBand[l.vat_band].net_pence += l.net_pence;
  });
  return {
    lines: out,
    by_band: byBand,
    vat_pence: out.reduce(function (s, l) { return s + l.vat_pence; }, 0),
    gross_pence: out.reduce(function (s, l) { return s + l.gross_pence; }, 0)
  };
}

module.exports = {
  lineVat: lineVat,
  basketVat: basketVat,
  vatForGross: vatForGross,
  normaliseBand: normaliseBand,
  rates: rates,
  DEFAULT_RATES: DEFAULT_RATES
};
