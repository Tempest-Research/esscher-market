# Autonomous application service (deadline-aware, identity-bound)

This note scopes the deadline-aware application service slice of issue #66 implemented by
`runtime.autonomous_application_service`, together with its two supporting contracts,
`runtime.stage_budgets` and `runtime.health_receipts`. The service composes the real application
path (frozen source compiler, bounded decision engine, synthetic confirmation bridge, Gate D
expression compiler, V2 risk kernel, monitored PAPER lifecycle, synthetic in-memory broker) and
the frozen option-event reconciliation journal from `runtime.option_events` behind one fail-closed,
content-addressed stage chain per armed window.

The slice is synthetic-only: feeds are fixtures, the broker is in-memory, every clock is injected,
and all artifacts are labelled `SYNTHETIC_FAKE` / `NOT_ALPHA_EVIDENCE` / `NOT_HISTORICAL_DATA`.
It supplies no alpha, broker-connectivity, fill, external position, external flatness, or
profitability evidence, and it creates no provider, credential, account, or network capability.

## Chain data flow

```text
INPUT
  validated host authority graph (release + arm record + frozen session arm)
  + one composition feed event for the due window (evidence + market bytes)
  + derived stage budgets (frozen p95 profile x armed window schedule)
  + optional feed-provided option activity bundle (positions, coverage, events)
  + injected deterministic stage clock
        |
        v
EVENT  (one run_window per armed due window)
  deadline gate     observed_at vs window closes_at / arm hard_flat_at
  sticky gate       durable journal manual state blocks all new exposure
  durable claim     autonomous session store claim => duplicate suppression
        |
        v
STAGE CHAIN (each receipt binds the prior receipt SHA-256; first binds the arm SHA-256)
  1 EVIDENCE_CAPTURE       canonical capture artifacts via the public sourcedata
                           capture entry point + compile_strategy_snapshot
  2 FEATURE_RECEIPT        compiled_strategy_input feature receipt identity
  3 DECISION               application.prepare_v2 (engine decide + synthetic
                           confirmation + permit bridge + V2 risk approval)
  4 EXPRESSION             Gate D compiled-expression identity bound to the decision
  5 RISK                   risk approval bound to the exact canonical permit
  6 LIFECYCLE_OPEN         the sole broker mutation: application.open with the
                           injected broker clock + decision episode + sidecar bundle
  7 MONITORED_EXECUTION    open-fill state attestation + durable lifecycle identity
  8 RECONCILIATION         synthetic broker truth + reconcile_option_events through
                           the atomic journal claim + deterministic exposure recompute
  9 TERMINAL               binds every prior stage receipt SHA-256
        |
        v
OUTPUT
  WindowRunResult (stage receipts, health receipt, option receipt shas, exposure sha)
  or ApplicationServiceStopped (failing receipt + full chain + health receipt);
  CloseAuthorityResult from the bounded close authority (lifecycle closer + reconciler)
        |
        v
STATE
  autonomous session store claims (retry-safe, non-duplicable)
  hash-chained host persistence sidecar (ACTIVE / EXPOSURE / TERMINAL entries)
  append-only option-event journal (sticky manual state, atomic activity claims)
  episodic risk ledger (decision + outcome episodes, terminal-flat proof)
  service terminal receipt binding every stage, health, close, and option sha
```

A stage runs only after its prerequisite receipt identity verifies; any stage failure marks every
downstream stage `SKIPPED` and raises `ApplicationServiceStopped`, so no downstream stage executes
after its identity-bound prerequisite fails. Deadline exhaustion before the mutation stage fails
closed with an all-`SKIPPED` chain while `close_authority(...)` (bounded to the lifecycle closer
plus reconciler) remains callable, preserving minimum bounded exit authority. A budget violation
(`finished - started > budget_ms`) marks the stage `FAILED` with reason `BUDGET_VIOLATION` and
stops the chain; a violation at or after `LIFECYCLE_OPEN` escalates the circuit to
`MANUAL_REQUIRED` because exposure may already exist.

## Budget derivation

`derive_stage_budgets(profile=..., arm=...)` reads only frozen package contracts:

| Budget field          | Value                                    | Source                                                    |
| --------------------- | ---------------------------------------- | --------------------------------------------------------- |
| `reasoner_ms`         | 30000                                    | packaged p95 latency profile (`PREREGISTERED`, v1)          |
| `market_data_ms`      | 30000                                    | same p95 bound                                             |
| `broker_ms`           | 30000                                    | same p95 bound                                             |
| `retry_backoff_ms`    | 0                                        | frozen route call policy `retry_count=0` (one-call/no-retry) |
| `shutdown_reserve_ms` | 30000                                    | same p95 bound                                             |
| `profile_sha256`      | profile content hash                     | `ValidatedLatencyProfile.content_sha256`                   |
| `arm_window_sha256`   | arm + window schedule hash               | canonical `esscher.arm_window_set` v1 payload              |

Stage-to-budget mapping: `EVIDENCE_CAPTURE`/`FEATURE_RECEIPT` use `market_data_ms`;
`DECISION`/`EXPRESSION`/`RISK` use `reasoner_ms`; `LIFECYCLE_OPEN`/`MONITORED_EXECUTION`/
`RECONCILIATION` use `broker_ms`; `TERMINAL` (and the bounded close stage) uses
`shutdown_reserve_ms`. `validate_stage_budgets_within_window` fails closed when the per-window
budget total exceeds the shortest armed window length, and the service constructor rejects budgets
not derived from its own arm.

## Operational health receipt

`esscher.operational_health_receipt` v1 fields:

- `run_id`, `arm_sha256`, `observed_at`, `budget_sha256`;
- `stage_latencies`: observed whole milliseconds per attempted stage from the injected clock;
- `budget_violations`: stage names whose observed latency exceeded their budget;
- `staleness`: per-source `{source_id, age_seconds, max_age_seconds, stale}` computed against the
  frozen V2 risk-policy `truth_max_age_seconds` (30 s) for `FEED_CAPTURE`,
  `OPTION_OBSERVATION` (when a bundle is present), and `BROKER_TRUTH` (synthetic, always fresh);
- `dependency_degradation`: sorted reason codes (for example the option receipt reasons of a
  diverged reconciliation);
- `reconciliation_lag_ms`: journal claim instant to receipt `observed_at`, `None` without a claim;
- `duplicate_suppressions`: cumulative suppressed duplicate window runs;
- `circuit_state`: `NOMINAL`, `MANUAL_REQUIRED` (reconciliation divergence, unknown broker
  mutation state, or the durable sticky journal manual gate), or `FROZEN` (risk freeze);
- `claims`: `("SYNTHETIC_FAKE", "NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA")`.

## Acceptance-criteria map

| #66 criterion | Test (`tests/test_autonomous_application_service.py`)                                   |
| ------------- | --------------------------------------------------------------------------------------- |
| C1 no stage after failed prerequisite | `test_prerequisite_failure_stops_downstream`                              |
| C2 deadline fails closed, exits retained | `test_deadline_exhaustion_fails_closed_but_close_authority_retained`, `test_budget_violation_stops_before_mutation` |
| C3 retries cannot duplicate | `test_duplicate_window_run_suppresses_without_duplicate_episodes`, `test_assignment_exercise_expiry_recompute_exposure` (replay), `test_divergent_broker_truth_forces_sticky_manual` (no retry) |
| C4 deterministic event transitions + exposure | `test_assignment_exercise_expiry_recompute_exposure`            |
| C5 broker truth overrides stale assumptions | `test_divergent_broker_truth_forces_sticky_manual`                    |
| C6 end-to-end causal identity | `test_causal_identity_chain_across_all_stages`, `test_health_receipt_contents`, `test_no_network_in_new_modules`, `test_full_accepted_run_with_socket_surface_disabled` |

## Remaining #68 blockers

- Live acquisition: the feed is a frozen fixture split; no live source, entitlement, or
  pagination acquisition path exists.
- Real MCP: the broker is the in-memory synthetic PAPER fake; the official Alpaca MCP capability
  path remains sealed and unpinned for live use.
- Production clock: every clock is injected and deterministic; no wall-clock scheduling,
  monitoring, or latency measurement exists, so the p95 budgets remain preregistered bounds
  rather than host measurements.
