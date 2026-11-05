'use strict';

// Shared test helpers. Not a test file (mocha --recursive loads it anyway;
// it has no describe() so nothing runs).

var fs = require('fs');
var os = require('os');
var path = require('path');

function tmpDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'larder-ops-' + (prefix || 'test') + '-'));
}

function rmrf(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch (e) {
    // node < 14 fallback was here (2021). Gone.
  }
}

module.exports = { tmpDir: tmpDir, rmrf: rmrf };
