'use strict';

var Promise = require('bluebird');

module.exports = function sleep(ms) {
  return Promise.delay(ms);
};
