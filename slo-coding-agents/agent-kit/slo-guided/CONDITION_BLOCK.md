## Monitoring

Use the dashboards, alerts, logs, metrics and traces in Coralogix, together with the service logs and
`./larderctl status`, to understand how the system is behaving.

## Service level objectives and error budgets

Larder has service level objectives (SLOs) for its key user journeys and services. They are listed, with
their targets, criticality and alerts, in `SLO_CATALOG.md`. Read their current state with:

- `tools/slo-status`: live burn rates (2, 10 and 60 minutes), remaining rolling 60-minute budget, the
  Coralogix SLO engine's attainment and budget, and which SLO alerts are firing.
- SLO alerts in Coralogix, named `Larder · SLO · <slo> · ...` (`cx alerts list`, `cx alerts events`).
- The Coralogix dashboard **Larder · SLOs and error budgets**, and `cx slos list` / `cx slos get <id>`.

Consider these SLOs in your decision making when deciding to fix, manage technical debt, or build a new feature.
