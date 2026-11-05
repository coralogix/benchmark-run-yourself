# Review one incident investigation

You are reviewing one AI agent's investigation of an incident in an online
shop. The agent queried an observability platform through a command-line tool
and answered the questions below one at a time. Every platform name, product
name, tool name and command has been replaced by a neutral label such as
"the platform", "the CLI" or "[query 3]". Do not try to work out which
platform or tool it was; judge only what the agent found and claimed.

Mark each dimension in the rubric for this one agent against the reference
measurements, which were taken from complete, unsampled data:

- 1, correct: identifies the right mechanism, or gives a supported impact
  measurement with the correct scope, time window and denominator.
- 0.5, partial: gets part of the answer right, but leaves the mechanism
  unproven or gives an incomplete or approximate impact estimate.
- 0, wrong or missed: misses the finding, gives the wrong explanation, or
  makes an unsupported impact claim.

A finding counts only if the evidence the agent retrieved, shown under each
question, supports it. A cause asserted without retrieved evidence is wrong.
A count or rate from sampled data presented as the whole is at most partial.
Where a dimension is a count, give a whole number.

List every false claim: a statement the reference contradicts, such as an
inflated failure rate, a wrong amount, or a claim that telemetry does not
exist when the agent's own evidence or the reference shows it does. Missing a
finding is not a false claim.

Reply with one JSON object and nothing else:

```json
{"marks": {"<dimension id>": <mark>, ...},
 "false_claims": ["<quoted or summarised claim, and what the reference shows>", ...],
 "reasons": {"<dimension id>": "<one sentence>", ...}}
```
