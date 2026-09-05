# Production PAPER_MCP host, read-only preflight, and broker reconciliation

This document covers issue #90 (PRD PR-2/PR-3, with PR-5 rehearsal support).
It describes the production-only `PAPER_MCP` composition that connects Esscher's
autonomous application path to the official Alpaca MCP boundary, the read-only
preflight receipt, the account-activity ingestion contract, the wall-clock
scheduler, and the durable blocked-state/recovery semantics. Everything here is
repository readiness for **authorized** PAPER use: it is not evidence that a
broker session, order, fill, account flatness, release approval, or P&L ever
occurred. Those gates belong to #91 and #68.

The frozen `SYNTHETIC_FAKE` rehearsal runner and composition are unchanged; the
production path never falls back to them, and they never carry production state.

## Data flow

```text
source
  host-captured evidence/market bytes per armed window (PaperMcpFeedEvent)
  -> CaptureSourceDoor -> compile_strategy_snapshot -> compiled_strategy_input
    |
    v
decision
  BoundedDecisionEngine over the owner-approved direct route adapter
  (current: qwen3.8-max-0902 V5 via the official Alibaba DashScope endpoint;
  the deepseek V4 furry.vg gateway, MiniMax-M3 V3, and direct-Kimi V1/V2
  packages remain dormant),
  under the independently enforced approval binding
  (approved_route.validated_route hashes == armed session arm hashes ==
  packaged approval) -> deterministic confirmation bridge
    |
    v
risk
  RiskKernel V2 over PaperMcpAccountTruthSource (read-only MCP account,
  orders, positions) -> freeze_candidate -> authorize_entry_v2 ->
  Gate D expression compile -> canonical debit-vertical permit
    |
    v
claim
  coordinator claim fence (unchanged): one atomic claim per canonical
  opportunity; restart of a claimed opportunity freezes into
  CLAIM_RECOVERY_UNKNOWN instead of resubmitting
    |
    v
MCP
  PaperStrategyApplication.open through the factory-issued
  LifecycleMcpPaperBroker on the guarded prepared session:
  place_option_order with the deterministic client_order_id, under
  PaperMcpMutationGate (closed in rehearsal; open only for an armed session)
    |
    v
broker readback
  timeout is never retried: get_order_by_client_id readback decides;
  ambiguous outcomes freeze into UNKNOWN_BROKER_STATE manual reconciliation;
  every non-terminal failure appends one durable blocked-state entry with a
  bounded retry budget (BLOCKED_RETRY_BUDGET_EXHAUSTED escalation)
    |
    v
lifecycle
  hash-chained sidecar ACTIVE bundle -> MonitoredPaperLifecycle close at the
  hard-flat boundary -> close permit -> monitored close submission ->
  account-activity acquisition (cursor-durable, paginated, deduplicated) ->
  hash-bound typed option-event mapping; unknown/unmappable/contradictory
  activities route to ACTIVITY_MANUAL_ROUTE, never a guess
    |
    v
reconciliation
  read-only account/orders/positions truth -> PaperMcpBrokerTruth
  (esscher.paper_mcp_broker_truth, structurally separated from the synthetic
  truth schema) -> orphaned working orders resolved exclusively through the
  risk-reducing cancel door -> terminal flat proof
  (esscher.paper_mcp_terminal_flat_proof) only from observed flat broker truth
```

## Components

| Piece | Location |
| --- | --- |
| Read-only tool extension (`get_orders`, `get_account_activities`) over the identical pinned 2.3.1 artifact, hashed selection + self-consistent receipt | `contracts/execution_policy.py`, `execution/host_mcp.py`, `tests/contract_fixtures/alpaca_mcp_v2_3_1_readonly_extension.json` |
| Guarded doors: `readonly_call` (mutation-impossible) and `risk_reducing_cancel` (flatten-authority only) | `execution/host_mcp.py` |
| `PAPER_MCP` execution class, production plan admission via one shared `production_binding_sha256`, class-separated broker truth and receipt claims | `runtime/autonomous_host.py` |
| Activity acquisition: paginated, cursor-durable (hash-chained journal), cycle/budget fail-closed; raw-to-typed mapping under the content-addressed `esscher.alpaca_activity_mapping/v1` contract | `sourcedata/alpaca_option_events.py` |
| Production composition: doors, plan factory, four backends, blocked-state journal, terminal flat proof | `runtime/paper_mcp_composition.py` |
| Read-only preflight and `NO_BROKER_MUTATION` receipt | `runtime/paper_preflight.py`, `contracts/broker_preflight.py` |
| Wall-clock scheduler (injected clock/sleep, one sleep per point, deterministic manual/terminal stops) | `runtime/paper_scheduler.py` |
| CLI: `paper-preflight`, `paper-run` | `cli.py` |

## Execution-class separation

`HostExecutionClass` admits exactly `SYNTHETIC_FAKE` and `PAPER_MCP`. A plan is
rejected when:

- any backend of a `SYNTHETIC_FAKE` plan carries a production binding;
- any backend of a `PAPER_MCP` plan lacks the production binding, or the four
  bindings are not byte-identical;
- the receipt class and its claims do not match: synthetic receipts keep
  `SYNTHETIC_FAKE`/`HOST_PLAN_ATTESTATION`; production receipts carry
  `PAPER_OPERATIONAL_RESULT`, `NOT_ALPHA_EVIDENCE`, `NO_CREDENTIALS_RECORDED`
  under `PRODUCTION_COMPOSITION` and data class `PAPER_MCP_HOST_OBSERVATION`.

Broker truth is cryptographically class-separated: `esscher.synthetic_broker_truth`
and `esscher.paper_mcp_broker_truth` hash differently for identical field
values, and the reconciliation adapter rejects a truth record of the wrong
class for its plan. The production composition module never imports the
synthetic broker, synthetic clocks, or the sourcedata fixture loaders
(AST-enforced by `tests/test_paper_mcp_composition.py`).

## Read-only preflight

`esscher paper-preflight` connects one host-owned MCP session through the
packaged factory (tool/schema provenance and account eligibility preflight),
then verifies, exclusively through the read-only door:

- PAPER endpoint/account class, expected account identity (bound as a digest,
  never raw), unblocked ACTIVE status, options capability, and the configured
  starting-equity contract;
- read-only account, open-order, position, and activity queries succeed, with
  the activities probe paginating to completion inside a bounded page budget;
- a flat starting state (zero open orders and zero non-zero positions),
  otherwise `NON_FLAT_START`;
- the approved direct route configuration hash (current: the V5 DashScope
  qwen package) and the packaged host-measured latency-profile hash
  (mismatches: `ROUTE_MISMATCH`, `LATENCY_PROFILE_MISMATCH`).

The outcome is one canonical `esscher.broker_preflight_receipt/v1` artifact:
content-addressed, redacted (no credentials, no raw account identifier),
claiming `NO_BROKER_MUTATION`, and either `PASSED` with no reason codes or
`REJECTED` with stable reason codes. Exit codes: 0 passed, 2 rejected/invalid
expectations, 3 host/structural failure. A mutation attempt through the
preflight path is impossible by construction (the read-only door rejects the
mutating selection) and is asserted by tests. Owner review of the receipt, the
host-measured route profile, and the GO/NO-GO decision remain #91 (owner
MS-Mesh).

## paper-run and scheduling

`esscher paper-run --release --arm --state-dir --ledger --output-dir
--host-invocation module:function` requires the explicit authority paths, and
the selector's `PaperSessionInvocation` must bind byte-identical release/arm
bytes, the same state directory, and the declared ledger path, or the CLI
rejects it. The scheduler derives the observation timeline from the armed
session (window openings plus hard-flat), waits for each instant with exactly
one injected sleep per point (never a busy loop), and re-runs the unchanged
host runner over each timeline prefix; durable state makes replays idempotent.
A `MANUAL_RECONCILIATION_REQUIRED` receipt stops the schedule before any later
window; a `TERMINAL` receipt ends it. The final receipt is written to
`<output-dir>/paper-run-receipt.json`.

Restart recovery is broker-truth-first: candidate processing is refused with
`RECONCILIATION_INCOMPLETE` until a STARTUP reconciliation has observed the
account, orders, and positions through the read-only door; orphaned working
orders are resolved only through the risk-reducing cancel door before truth is
attested; unresolvable states escalate to manual reconciliation and durable
blocked entries instead of a second mutation.

## No-mutation rehearsal (PR-5 support)

Composing the doors with `mutation_permitted=False` runs the identical
production path with `PaperMcpMutationGate` closed: capture, decision,
confirmation, risk, and permit compilation all execute, the would-be permit is
recorded through a `MUTATION_GATE_CLOSED` rejected-before-mutation outcome, and
no order tool is ever called. This is the rehearsal surface #91 uses; the gate
opens only for an explicitly armed session under the owner's #91/#68 approvals.

## Delegated seams (honest boundaries)

- `AutonomousApplicationService`'s nine-stage receipt chain remains typed to
  the synthetic broker; the production backends drive the same underlying real
  services (`PaperStrategyApplication`, `MonitoredPaperLifecycle`, `RiskKernel`,
  episode ledger, sidecar) directly. Integrating the stage-receipt chain with
  the `PAPER_MCP` class is a follow-up under owner review.
- Route pivot (#68 governance, 2026-09-04, owner MS-Mesh): the current
  approved route is the V5 DashScope package (`dashscope_qwen` @
  `https://dashscope.aliyuncs.com/compatible-mode/v1`, date-pinned model
  `qwen3.8-max-0902`, NO wire response_format - DashScope json_object demands a
  literal "json" token the immutable frozen prompt never contains, so the
  strict schema is prompt-directed and client-validated - temperature 0,
  top_p 1.0, enable_thinking=false, 1024-token cap, 8s one-call/no-retry
  policy) after gate measurement: 30/30 warm observations strict-schema-valid
  at nearest-rank p95 5578 ms through the real adapter, and the free furry.vg
  gateway (V4) proved evening concurrency-capped (429 "at concurrency limit")
  which would abstain live decisions. Its adapter (`QwenDashScopeReasonerRoute`,
  sharing the `DirectEnvelopeReasonerRoute` lane with the V4/V3 adapters) is
  wired for the assembled engine: exchanges carry the frozen policy-registry
  identities and the configured `RouteIdentity` model-config hash, and the
  composition factory enforces `reasoner is approved_route` so no double can
  front it. The deepseek V4 furry.vg gateway, MiniMax-M3 V3, and direct Kimi
  V1/V2 packages remain approved-but-dormant and can be re-activated by a
  future owner gate. The composition fails closed with `ROUTE_NOT_APPROVED` on
  any unapproved or drifting route; no provider, model, or synthetic fallback
  exists.
- Activity/position/order fixtures in tests are synthetic broker-shaped records
  labelled `SYNTHETIC_BROKER_SHAPED`; no real account was queried. Host
  normalization proves structure and correlation, never that Alpaca supplied
  the bytes.

## Safety boundary

No credential value enters the repository, CLI arguments, receipts, or logs.
No real broker mutation occurred in producing or testing this slice; every
test is offline against fake sessions and fixture captures. Green tests here
are engineering evidence of repository readiness for authorized PAPER use —
not broker connectivity, fill, flatness-at-close, release, or P&L proof.
