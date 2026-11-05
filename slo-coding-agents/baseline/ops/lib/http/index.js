'use strict';

// Three HTTP clients, one per era. See each file for its quirks.
//   request-client  2017-2019  (payments, orders legacy calls)
//   axios-client    2020-2021  (bus, webhooks, orders newer calls)
//   fetch-client    2022-2023  (warehouse, notifications)
// TODO(TD-2): pick one. (r.singh 2021-01-05, p.ndlovu 2023-08-30, "+1")

module.exports = {
  requestClient: require('./request-client'),
  axiosClient: require('./axios-client'),
  fetchClient: require('./fetch-client')
};
