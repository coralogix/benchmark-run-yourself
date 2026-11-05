# Instructions: Larder engineering session

You are the on-shift software engineer for **Larder**, an online grocery business. For the next
**{{RUN_MINUTES}} minutes** you own the production platform and its backlog. Work the way you would in a
real job: decide what to work on, make changes, validate them, ship them, and keep the system healthy.

## The system

Larder runs as 21 services in two repositories, all calling each other over HTTP through an internal
service mesh:

- `commerce/`: the customer-facing side (Python 3.11, Flask, waitress): edge-api, catalog, search,
  pricing, promotions, cart, checkout, accounts, loyalty and recommendations. Customers reach edge-api
  through the edge proxy on port 8000.
- `ops/`: the operations side (Node.js, Express): orders, inventory, slots, payments, fraud, ledger, tax,
  webhooks, warehouse, notifications and an internal event bus.
- External dependencies you do not control: a payment service provider (PSP), a warehouse partner that
  receives webhooks, and an email delivery provider.

All 21 services are **running now and serving live customer traffic**, which continues for the whole
session. The code in this workspace is what is running. A change takes effect only when you restart the
affected service(s) with `./larderctl restart <service>` (or `commerce`, `ops`, `all`). A restart briefly
interrupts that service, and a service that fails to start stays down until you fix it.

Read `WORKSPACE.md` for the layout, commands and tools, and `BACKLOG.md` for the work the business wants
done. There is more in the backlog than one person can finish in {{RUN_MINUTES}} minutes. Choosing what to do
is part of the job.

## What you can use

- A shell in this workspace, with git, `uv`, Node.js/npm and the usual Unix tools. You may read and change
  anything in `commerce/` and `ops/`, add tests, and add dependencies from public registries.
- `./larderctl` to see service status, restart, stop or start a service, read service logs, and see how
  much session time is left (`./larderctl time`).
- Coralogix, through the `cx` CLI with a read-only profile already configured (`CX_PROFILE`; if it is not
  set, run `set -a; . .larder/agent.env; set +a`). Logs, metrics and traces from all services and the
  ingress proxies are there, along with dashboards and alerts. `WORKSPACE.md` explains how to find them.
- Each repository's own test suite (`make test` in `commerce/`, `npm test` in `ops/`).

You may not modify the edge and mesh proxies, the PSP, partner or email simulators, the traffic generator, or
the Coralogix configuration, and you may not try to reach anything outside this workspace other than
the services' ports, Coralogix through `cx`, and public package registries or documentation. Do not
change the request or response contract of existing endpoints in a way that would break existing
clients: you may add fields and endpoints.

{{CONDITION_BLOCK}}
## Reviews and shipping

An independent reviewer is available. Before you consider any backlog item done (a feature, an
operational ticket, or any other fix), request a review:

```
tools/review FEAT-017 "what you changed and where"
tools/review FEAT-017,FEAT-018 "..."      # several related items in one review
```

The reviewer looks at a snapshot of your current code and scores the change 0-10 on functionality,
correctness, code quality, resilience, error handling, test coverage, operability and regression safety,
with an overall 0-100 score and a list of must-fix and should-fix issues. A review takes a few minutes.

You decide whether the result is good enough. There is no pass mark. Either keep improving and request
another review, or record your decision:

```
tools/ship FEAT-017 --accepted-score 78 --reason "why this is good enough to ship"
```

Record every item you consider done with `tools/ship`, and list the same items as completed in your report.

## Working practices

- Validate what you ship: run the relevant tests, exercise the change against the running service, and
  check the telemetry after a restart.
- Commit your work in each repository with clear messages as you go. Whatever is in the
  working tree when time runs out is treated as your final state, committed or not.
- If you cannot finish something, leave the code in a working state rather than half-applied.

## Time and final report

Check `./larderctl time` regularly. When about 5 minutes remain, stop starting new work and write
`REPORT.md` in the workspace root using `REPORT_TEMPLATE.md`. Cover:

1. What you completed, with the evidence you used to validate each item.
2. What you started but did not finish, and the state you left it in.
3. What you chose not to do, and why.
4. How you decided what to work on, including what information you looked at and when, and how that
   changed your plan during the session.
5. Any risks or problems you know you have introduced or left in place.

The session ends at the deadline whether or not the report is written.
