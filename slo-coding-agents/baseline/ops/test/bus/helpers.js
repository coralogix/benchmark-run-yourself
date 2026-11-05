'use strict';

var os = require('os');
var fs = require('fs');
var path = require('path');
var http = require('http');
var express = require('express');
var bodyParser = require('body-parser');

function tmpDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix || 'larder-bus-test-'));
}

// stub subscriber server; handler(req, res) decides the answer
function stubServer(handler, cb) {
  var app = express();
  app.use(bodyParser.json());
  app.post('*', handler);
  var srv = http.createServer(app);
  srv.listen(0, '127.0.0.1', function () {
    cb(null, srv, 'http://127.0.0.1:' + srv.address().port);
  });
}

function waitFor(fn, timeoutMs, cb) {
  var started = Date.now();
  (function poll() {
    if (fn()) return cb();
    if (Date.now() - started > timeoutMs) return cb(new Error('timed out waiting'));
    setTimeout(poll, 10);
  })();
}

module.exports = { tmpDir: tmpDir, stubServer: stubServer, waitFor: waitFor };
