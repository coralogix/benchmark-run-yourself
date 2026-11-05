'use strict';

// Seed normalisation for inventory (was lib/bootstrap.js normaliseInventory
// in fulfilment). seed/inventory.json is the 2019 stock-take export.

function normaliseInventory(list) {
  return list.map(function (r) {
    return {
      sku: r.sku,
      on_hand: r.on_hand | 0,
      reserved: r.reserved | 0,
      unit_price_pence: r.unit_price_pence | 0,
      vat_band: r.vat_band || 'standard',
      tags: r.tags || [],
      updated_at: null
    };
  });
}

module.exports = {
  normaliseInventory: normaliseInventory,
  spec: {
    inventory: { file: 'inventory', key: 'sku', normalise: normaliseInventory }
  }
};
