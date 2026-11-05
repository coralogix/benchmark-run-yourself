// Local overrides.
//
// NOTE: despite the name this file is committed and is loaded in EVERY
// environment, and it is applied LAST (after env vars), see lib/config.js.
// It started life as a dev convenience in larder-fulfilment (r.singh 2019),
// then the bench / staging boxes came to depend on these values, and the 2023
// split copied it into larder-ops unchanged plus the bus bits. Don't delete it.
//
// TODO(j.watts 2022-11-03): move these into default.json and kill this file.
// TODO(p.ndlovu 2023-07-21): ^ still true after the split.
'use strict';

module.exports = function (cfg, env) {
  // PSP: the 10s timeout in default.json made checkout feel hung (INC-2034).
  // 4s matches the PSP's documented p99 (it doesn't any more).
  cfg.psp.timeoutMs = 4000;
  cfg.psp.retries = 3; // "3 retries is the industry standard" - see PR #412
  cfg.psp.retryDelayMs = 250;

  // Webhooks: partner asked for faster delivery (2021-04)
  cfg.webhooks.tickMs = 2000;

  // Holds were 15 minutes until the 2020 Christmas capacity crunch.
  cfg.slots.holdTtlMinutes = 10;

  // Bus: 30s (default.json) let one stuck subscriber hold a topic for half a
  // minute per message. 10s "is plenty for an internal call" (p.ndlovu 2023).
  // BUS_DELIVERY_TIMEOUT_MS in the env is therefore ignored. Sorry.
  cfg.bus.deliveryTimeoutMs = 10000;
  cfg.bus.retrySleepMs = 1000;

  // Notifications: email provider SLA is "under 10s"
  cfg.email.timeoutMs = 10000;

  // Fraud (FRAUD-52, 2025-03): after the chargeback spike finance asked for
  // fail CLOSED - an unscored payment is not taken. The vendor's SLA was
  // 300 ms; we give our own scorer 500 ms so a checkout never waits on it.
  cfg.fraud.failOpen = false;
  cfg.fraud.timeoutMs = 500;

  // Only honour the env log level if it's a real winston level
  if (env.LOG_LEVEL && ['error', 'warn', 'info', 'verbose', 'debug', 'silly'].indexOf(env.LOG_LEVEL) === -1) {
    cfg.log.level = 'info';
  }

  // production.json turns on internal auth, but the mesh doesn't send the
  // header yet. FIXME(a.mensah 2023-02-14): remove once mesh injects it.
  cfg.internalAuth.enabled = false;

  return cfg;
};
