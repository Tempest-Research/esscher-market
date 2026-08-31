# Monitored PAPER lifecycle (issue #31)

This document describes the autonomous PAPER lifecycle that drives one
risk-approved promoted expression through its frozen policy exit, restart
recovery, deterministic close, and broker-confirmed flatness. It reuses the risk
ledger, Trade Passport, and the constrained Alpaca PAPER adapter. Actual PAPER
mutation stays blocked behind the later #9 approval gate; every test uses a
deterministic fake broker and makes zero real MCP/broker calls.

## What is NOT preserved

- The `run_paper_demo` immediate open→close sequencing (open and resolve-to-flat
  in one call with no holding window) is **not** production policy.
- The earlier fixed 60-minute fill-relative hold is **not** production policy.
  It remains declared `INERT_NOT_STRATEGY_PROMOTED` in the frozen policy and
  cannot authorize PAPER mutation.

## Frozen exit boundary

- Strategy clocks are read from a frozen exit plan
  (`esscher.lifecycle_exit_plan` v1) and cannot be altered by model prose or a
  hard-coded fallback. When the exit plan is absent or its source is unverified
  (all-zero source digest) the lifecycle fails closed with
  `EXIT_PLAN_UNVERIFIED`; there is no fallback.
- Earnings BMO/AMC and macro clocks are carried per-cohort and cannot be
  blended.
- The LLM cannot initiate, delay, optimize, or cancel an exit. The worker is
  deterministic; exits fire on the frozen time-exit clock.
- Safety/manual controls may enter close-only recovery but cannot fabricate
  normal-policy evidence.
- All supported positions close before the verified flattening deadline; a
  failure to prove flat yields `MANUAL_REQUIRED` and no terminal success or PnL
  receipt is fabricated.

## State contract

```text
APPROVED -> OPEN_SUBMITTED
        -> OPEN_PARTIAL | OPEN_FILLED | OPEN_CANCELED | OPEN_UNKNOWN
        -> HOLDING
        -> CLOSE_DUE
        -> CLOSE_SUBMITTED
        -> CLOSE_PARTIAL | CLOSED_FLAT | MANUAL_REQUIRED
```

Every transition and side-effect intent is persisted before the next mutation.
Broker acknowledgement is never fill proof: the worker always reads back order
and position truth. Unknown, partial, incident, or exposure-bearing states stop
new entries while preserving reconciliation and close authority.

## Deterministic behaviors

- **Duplicate ticks:** a permit can be submitted once; a second tick with the
  same permit fails with `DUPLICATE_TICK`.
- **Restart recovery:** `recover_open_state` and `recover_flatness` resume from
  broker/ledger truth rather than replaying intent.
- **Broker outage:** a submission or readback outage fails closed with
  `BROKER_OUTAGE`; ambiguity is never retried.
- **Partial fills:** partial opening and partial closing cannot be repaired by
  sequential option legging; they stop for manual reconciliation.
- **Non-flat close:** a close whose position truth still carries a leg is
  `MANUAL_REQUIRED`, never declared flat.
- **Stale truth:** flatness is never confirmed from position truth older than
  the truth-max-age bound (`STALE_QUOTE`); future-dated truth fails with
  `CLOCK_JUMP`.
- **Clock jump past flattening:** a close attempted after the frozen
  flattening deadline fails with `FLATTENING_DEADLINE_PASSED`.
- **Mutation gate:** `ClosedMutationGate` keeps actual PAPER mutation blocked;
  `open()`/`close()` fail with `MUTATION_GATE_CLOSED` until the later approval
  gate opens.

## Final PnL

Final PnL uses exact matched broker fill economics or remains unavailable;
conservative shadow PnL is reported as a separate field and a separate claim.
The two are never conflated.

## Testing

All tests use `FakePaperBroker` and make zero real MCP/broker calls. Coverage
spans every transition, timeout, restart point, duplicate tick, partial fill,
broker outage, stale/future-dated truth, clock jump past flattening, non-flat
close, and the mutation gate.
