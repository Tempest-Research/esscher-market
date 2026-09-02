# Autonomous host runner: synthetic composition over the frozen coordinator

This document covers issue #82. The scaffold in `runtime/autonomous_host.py` composes
`AutonomousSessionCoordinator` with typed host adapters so that release/arm validation,
deterministic windows, duplicate claims, state transitions, hard-flat handling, and sanitized
receipts are rehearsed offline. On top of that scaffold, `runtime/host_composition.py` now
composes the **real** application services behind the four host ports: the frozen source
compiler, the bounded decision engine, the deterministic synthetic confirmation bridge
(`application/autonomous_bridge.py`), the Gate D expression compiler, the canonical permit
bridge, the V2 risk kernel (`authorize_entry_v2`), `PaperStrategyApplication.prepare_v2` /
`open` / `close`, the monitored lifecycle worker, a hash-chained rehydration sidecar
(`runtime/host_persistence.py`), and the deterministic in-memory synthetic broker
(`runtime/host_fake_broker.py`).

Everything remains permanently `SYNTHETIC_FAKE` / `NOT_ALPHA_EVIDENCE` with zero
network/provider/broker/account calls. The composition is evidence that the accepted
source-to-flat path runs end-to-end under the coordinator with no hand-constructed decision,
permit, lifecycle, or flat result. It is not evidence that Kimi, an Alpaca account, an MCP
server, a real option fill, or a production deployment has been exercised; the guarded MCP
broker capability (`LifecycleMcpPaperBroker`) in particular is still not part of this path —
the composition drives the `lifecycle.broker.PaperBroker` protocol directly through the
synthetic broker.

## Architecture and data flow

`AutonomousSessionCoordinator` in `runtime/autonomous.py` remains the only candidate loop and the
only writer of autonomous session state. The host adapters validate correlation and translate
host-attested typed values; they do not independently obtain the facts asserted by those values.

```text
INPUT
  canonical StrategyRelease + ArmRecord + AutonomousSessionArm bytes
  host-supplied observation replay timeline and state directory
  trusted host-owned module:function
  SYNTHETIC_FAKE host plan returning typed observations and outcomes
    |
    v
EVENT
  parse the release and arm, then load and call the trusted host module
  -> validate release/arm/session identities, package hashes, clocks, and state paths
  -> lock the session store and construct the fake host adapters
  -> validate and translate a host-attested reconciliation observation
  -> validate and translate host-attested due-window candidates
  -> atomically claim each canonical opportunity once
  -> translate HostCandidateOutcome into CandidateProcessingResult
  -> at hard-flat, translate HostLifecycleOutcome into LifecycleCloseResult
  -> bind finalization to canonical structured synthetic broker truth
    |
    v
OUTPUT
  coordinator AutonomousRunResult and, after finalization, AutonomousSessionSummary
  one canonical AutonomousHostReceipt classified as a synthetic contract fixture
  terminal success only when both the coordinator summary and FINAL synthetic truth are flat
    |
    v
STATE
  AutonomousSessionStore: arm, windows, claims, dispositions, synthetic active identities,
    manual reasons, and summary
  host-owned backend state: outside this scaffold's validation and durability guarantees
```

In particular, the generic runner does not create or verify a `RiskLedger`, strategy decision,
expression, permit, application, order, fill, or real broker observation. A host backend can
perform additional work, but the adapter sees only its typed attestation and cannot upgrade that
attestation into evidence that the real application path ran. The composition layer below is the
host backend that actually performs that work with the real services, and its outputs remain
synthetic-labelled attestations at the adapter boundary.

## Composition layer: INPUT -> EVENT -> OUTPUT -> STATE

`runtime/host_composition.py` wires the four host backend protocols to real services. The
account fingerprint binding is checked first: `composition_plan_factory` raises
`AutonomousHostRejected("ACCOUNT_FINGERPRINT_MISMATCH")` unless
`authority.account_fingerprint_sha256` equals the SHA-256 of the synthetic broker's canonical
initial account truth (`runtime/host_fake_broker.py: account_state_sha256`), so no port or
adapter exists before the armed session is bound to the observed synthetic account.

```text
INPUT
  CompositionFeed of CompositionFeedEvent values, each carrying:
    window_id + V2 lane candidate_id from the armed session windows
    evidence_manifest_bytes / market_window_bytes (sourcedata fixture sections)
    capture_at, market_publisher, market_entitlement, market_redistribution
  one RiskLedger, one SyntheticPaperBroker, the validated host authority
    |
    v
EVENT
  DueWindow backend: emits each not-yet-emitted feed event for the due window as a
    HostCandidateObservation with a content-addressed strategy context
  Candidate backend: rejoins the fixture bytes -> CaptureConfiguration + fixture sources
    -> compile_strategy_snapshot -> engine decide (SyntheticRehearsalRoute, deterministic
    confirmation-sign direction, COMPLETED exchange bound to its raw hash)
    -> autonomous_bridge.confirm_engine_outcome (epsilon from the frozen V1 policy)
    -> PaperStrategyApplication.prepare_v2 (Gate D compile -> canonical permit ->
    freeze_candidate -> derived_opportunity -> RiskKernel.authorize_entry_v2)
    -> application.open through the synthetic broker under the rehearsal mutation gate
    -> decision episode + hash-chained sidecar ACTIVE bundle -> HostCandidateOutcome.active
  Lifecycle backend: rehydrates the sidecar ACTIVE bundle (canonical permit bytes, exit-plan
    clock bytes, correlation, open order id) -> rebuilds the close-critical binding
    -> application.close with a deterministic close permit -> terminal-flat proof sha
    -> sidecar TERMINAL entry + outcome episode -> HostLifecycleOutcome.terminal_flat
  Reconciliation backend: canonical account/orders/positions digests and open counts from the
    synthetic broker -> SyntheticBrokerTruth.for_request -> complete/ambiguous/incomplete
    observation; orphaned working orders are resolved through the risk-reducing cancel path only
    |
    v
OUTPUT
  typed host observations/outcomes consumed by the unchanged adapters and coordinator
  AutonomousHostReceipt: TERMINAL only with a final flat summary plus FINAL flat synthetic truth
  reason-code mapping: confirmation abstention -> ABSTAINED/PORT_OUTPUT_INVALID,
    V2 risk abstention -> ABSTAINED/RISK_FREEZE, pipeline rejection ->
    REJECTED_BEFORE_MUTATION/PORT_OUTPUT_INVALID (freeze=False), ambiguous broker mutation ->
    MANUAL_RECONCILIATION_REQUIRED with UNKNOWN/PARTIAL + UNKNOWN_BROKER_STATE
    |
    v
STATE
  AutonomousSessionStore (unchanged sole writer of session state)
  RiskLedger: candidate freeze, V2 reservation/permit, intents, submissions, passport,
    decision + outcome episodes
  SyntheticPaperBroker: in-memory orders/positions with canonical state hashes
  host_persistence.jsonl: hash-chained ACTIVE/TERMINAL entries enabling close-only restart
```

All clocks are injected: the composition derives its decision/authorization/open/close instants
from the frozen fixture snapshot deadlines and advances one monotonic rehearsal clock that also
drives the synthetic broker. No wall time is read.

## Three distinct identities

The following hashes are not interchangeable:

1. `StrategyRelease.release_sha256` identifies the canonical promoted release record.
2. `ArmRecord.arm_sha256` identifies release, capability, source, ledger, process, and authority
   bindings from `contracts/strategy_release.py`.
3. `AutonomousSessionArm.arm_sha256` identifies the concrete PAPER session, runtime code hash,
   account fingerprint, policy hashes, route/model hashes, and due windows from
   `runtime/autonomous.py`.

Composition preserves all three. `ArmRecord.release_sha256` selects the exact release, while
`AutonomousSessionArm.release_code_sha256` is checked against the selected release's declared
build identity and a host-supplied runtime identity. The scaffold does not independently measure
the installed wheel or source tree, so that runtime identity remains an attestation rather than
build provenance. The account capability ID in `ArmRecord` is not the account fingerprint in
`AutonomousSessionArm`. Session/arm IDs and clock containment also require explicit validation;
matching one hash does not imply the others.

The autonomous arm is compared with a deterministically rebuilt
`AutonomousSessionArm.for_trading_date(...)`. The lower-level validator authenticates current
package hashes and opening times, but it is not by itself an assertion that a host account was
observed or that arbitrary dynamically imported host code belongs to the release build.

## Current adapter boundaries

| Port | What the scaffold actually does | What it does not prove |
| --- | --- | --- |
| `ReconciliationPort` | Calls the host backend, checks the typed observation against the exact session/account/protocol/phase/request identities, and translates a complete attestation. | It does not query an account, orders, positions, MCP, or a broker. |
| `DueWindowCollectorPort` | Validates host-attested candidate observations and translates them into canonical `AutonomousOpportunity` values for the requested window. | It does not collect source bytes, build V2 strategy context, or call the reasoner. |
| `CandidateProcessorPort` | Validates a host-attested `HostCandidateOutcome` and translates it into `CandidateProcessingResult`. | It does not invoke confirmation, expression, risk, `PaperStrategyApplication`, order submission, or monitored lifecycle code. |
| `LifecycleCloserPort` | Validates a host-attested `HostLifecycleOutcome` for the stored synthetic lifecycle identity and translates it into `LifecycleCloseResult`. | It does not rehydrate or close a real `ActivePaperLifecycle`, nor reconcile an MCP close intent. |

Known typed pre-mutation outcomes may become abstention/rejection. Any exception, invalid
attestation, partial mutation, contradictory identity, or incomplete truth fails closed into a
non-terminal or manual-required state. That validates coordinator behavior; it does not validate
the backend's underlying assertion.

## Trusted dynamic host code

The `--host-plan module:function` selector imports and executes ordinary host Python in the CLI
process. It is trusted, operator-attested code, not data. It is not sandboxed, capability-limited,
or automatically content-addressed into the selected release. The invocation factory runs after
the CLI has parsed the release and arm bytes but before the runner performs its complete authority
validation; the returned plan factory runs after that validation and inside the state-directory
lock.

Suppressing host stdout/stderr only sanitizes the CLI result. It does not prevent the imported
module from reading files, using credentials, opening a network connection, mutating external
state, or leaking through another channel. Operators must audit and separately attest the module
and must use a credential-free offline fake for this scaffold. A host plan's
`HostExecutionClass.SYNTHETIC_FAKE` value is an explicit attestation accepted by the runner; it is
not a technical proof that arbitrary host code performed no broker execution.

## Structured synthetic broker truth

The retained fake contract uses a canonical `SyntheticBrokerTruth`, rather than allowing the
backend to supply an unexplained aggregate digest. It contains the exact request bindings
`session_id`, `session_arm_sha256`, `account_fingerprint_sha256`,
`execution_protocol_sha256`, `observed_at`, `phase`, and sorted `active_lifecycle_ids`, plus the
synthetic components `account_state_sha256`, `orders_state_sha256`,
`positions_state_sha256`, `open_order_count`, `open_position_count`, and `is_flat`.

`synthetic_broker_truth_bytes()` defines canonical bytes and
`synthetic_broker_truth_sha256()` derives the aggregate digest. The backend returns the typed
object, never a free-standing aggregate digest. Flatness must agree with no active lifecycle IDs
and zero open-order and open-position counts; active lifecycle IDs require a non-zero position
count. A `CHECKPOINT` observation may support a non-final invocation. `FINAL` is valid only after
coordinator finalization and must contain no active IDs, zero open counts, and `is_flat=True`.

The receipt binds `reconciliation_phase` and `reconciliation_broker_truth_sha256` and remains
classified as `SYNTHETIC_CONTRACT_FIXTURE` with `claim_basis=HOST_PLAN_ATTESTATION`. Its claims are
`SYNTHETIC_FAKE`, `NOT_HISTORICAL_DATA`, `NOT_ALPHA_EVIDENCE`, and
`HOST_PLAN_ATTESTS_NO_BROKER_EXECUTION`. The last claim is deliberately attributable rather than
categorical: the runner accepts it from the admitted offline fake plan but cannot enforce it
against arbitrary dynamic code. Even structured synthetic account/order/position hashes and zero
counts remain host assertions; they are not source-attested broker truth and must never be cited
as operational flatness or connectivity evidence.

## Operator semantics

These semantics do not depend on a particular flag spelling:

- Parse the release and arm bytes before loading the host selector, then authenticate the complete
  authority and session before constructing the four port adapters.
- Accept canonical artifacts and hashes, never credentials or raw secret-bearing account
  configuration.
- Admit only the explicit `SYNTHETIC_FAKE` execution class in this draft.
- For a cooperative, audited host module that writes through Python's standard streams, redirect
  host output and write one sanitized, machine-readable result to standard output. Raw
  provider/broker payloads and arbitrary exception text do not belong in that result. Because the
  host is unsandboxed, native writes or child processes can bypass this redirection; the scaffold
  does not claim OS-level output isolation.
- Return success only for an existing final summary whose `terminal_flat_proven` value is true and
  matching canonical `FINAL` synthetic truth that is also flat. Rejection, manual-required state,
  `CHECKPOINT` truth, or an incomplete/non-flat session returns non-zero.
- Replay an already-final session without repeating candidate or lifecycle transitions. A replay
  still performs one `FINAL` synthetic reconciliation at the stored finalization time; uncertainty
  there durably and irreversibly escalates the session to manual reconciliation.

A simulated broker-shaped fill demonstrates software flow only. It does not demonstrate broker
connectivity, execution quality, profitability, or judged-account P&L.

## Restart, duplicates, and hard-flat

`AutonomousSessionStore.ensure_arm` makes a session ID byte-identical on replay.
`claim_opportunity` is the atomic duplicate fence: an identical recorded opportunity is skipped,
a conflicting identity is rejected, and an in-progress claim found after restart freezes into
`CLAIM_RECOVERY_UNKNOWN` instead of being submitted again.

The store persists `ActiveLifecycleIdentity`: a sanitized synthetic identity used by coordinator
replay. It deliberately does **not** persist the application's `ActivePaperLifecycle`, canonical
permit bytes, lifecycle clocks, close-critical binding, or correlation. The composition layer
carries those in the hash-chained `host_persistence.jsonl` sidecar
(`runtime/host_persistence.py`), so a fresh process can rebuild close-only authority from durable
bytes and close real (synthetic-broker) exposure through `PaperStrategyApplication.close`. The
store identity and the sidecar bundle are joined by the lifecycle id; a missing, tampered, or
non-terminal sidecar bundle fails closed into manual reconciliation instead of guessing.

At hard-flat, the coordinator visits known synthetic active identities in deterministic order,
records only translated terminal-flat attestations, marks unresolved closes manual-required, and
freezes a final summary. The host runner then requires canonical `FINAL` synthetic broker truth.
This closes the fake scenario's local contract but does not fill the missing real-lifecycle
checkpoint or broker-reconciliation path.

## Safety invariants of this scaffold

- The admitted execution class is `SYNTHETIC_FAKE`; all arms remain `PAPER`.
- Identity or deadline failure blocks adapter construction or fails the coordinator closed.
- Unknown or contradictory mutation state is never translated into a second order.
- Acknowledgement is not fill proof, an empty local identity list is not broker-flat proof, and
  synthetic final truth is not real broker truth.
- Duplicate opportunity and terminal-episode identities are idempotent or rejected; they are not
  silently replaced.
- Fake fixtures, structured fake truth, green tests, and synthetic receipts are engineering
  evidence only.

## Exact blockers: resolution status

1. **RESOLVED — the host ports now drive the accepted source-to-application path.** The
   collector backend consumes the exact synthetic source bytes of a feed event and the candidate
   backend invokes the real pipeline: `compile_strategy_snapshot`/`compiled_strategy_input`
   (`sourcedata/compiler.py`), `BoundedDecisionEngine.decide` with the deterministic
   `SyntheticRehearsalRoute`, confirmation, Gate D `compile_or_no_package`, the canonical permit
   bridge, `RiskKernel.freeze_candidate`, and `PaperStrategyApplication.prepare_v2`/`open`/`close`
   (`runtime/host_composition.py`, `application/paper_pipeline.py`). No decision, permit,
   lifecycle, or flat result is hand-constructed; `tests/test_autonomous_host_composition.py`
   traverses the path end-to-end through `run_autonomous_host_command` and the CLI.
2. **RESOLVED — an owner-visible deterministic confirmation contract now exists.**
   `application/autonomous_bridge.py` defines `SYNTHETIC_CONFIRMATION_RULE_ID`, a
   content-addressed rule (`confirmation_rule_sha256`) over the frozen V1 policy epsilons, and
   `confirm_engine_outcome`, which confirms only when the candidate's frozen confirmation
   feature clears its epsilon with the decision's sign. `StrategyV2DirectionDecision` remains
   `DIRECTION_ONLY_UNCONFIRMED`; the rehearsal never promotes it and the rule is permanently
   labelled synthetic (`NOT_ALPHA_EVIDENCE`), so no validated-alpha authority is invented.
3. **RESOLVED — the V2 execution seam is joined.** `prepare_v2` authorizes through
   `RiskKernel.authorize_entry_v2` with `autonomous_bridge.derived_opportunity`, whose risk tier
   is always derived from the packaged V2 policy's first owner-approved tier
   (`derived_risk_tier` → `RiskTier.FIVE_PERCENT`) and whose readiness is the deterministic
   confirmation result — never caller-supplied. Documented seam behavior: Gate D stamps
   `CompiledExpression.policy_sha256` with the promoted expression-policy digest, while the V2
   kernel binds that field to the active V2 risk policy; `prepare_v2` deterministically rebinds
   the compiled expression to the V2 risk-policy digest for the freeze/allocation/authorization
   join while the prepared lifecycle retains the exact Gate-D expression that `open` revalidates.
4. **RESOLVED — the host account is attested before mutation.** `composition_plan_factory`
   compares `authority.account_fingerprint_sha256` against the SHA-256 of the synthetic broker's
   canonical initial account truth and raises
   `AutonomousHostRejected("ACCOUNT_FINGERPRINT_MISMATCH")` before any backend exists
   (`runtime/host_composition.py`, `runtime/host_fake_broker.py`). Against the synthetic broker
   this is an observed binding; against a real account it would remain an attestation until #68.
5. **RESOLVED — a real active lifecycle is durably rehydrated.**
   `runtime/host_persistence.py` writes a hash-chained JSONL sidecar
   (`host_persistence.jsonl`) whose ACTIVE entries carry the canonical permit bytes, exit-plan
   clock bytes, correlation payload/sha, open order id, account id, application identity, and
   opened_at; TERMINAL entries carry the terminal-flat proof. `CompositionLifecycleBackend`
   rehydrates a `RehydratedActiveBundle` in a fresh process, rebuilds the close-critical
   binding, and closes through `PaperStrategyApplication.close` (restart scenario S3 in
   `tests/test_autonomous_host_composition.py`).
6. **OPEN (#68) — claim recovery still stops instead of reconstructing mutation truth.** A
   persisted `CLAIMED` opportunity still becomes `CLAIM_RECOVERY_UNKNOWN`
   (`runtime/autonomous.py`, deliberately unmodified). The sidecar narrows the crash window —
   an opening that completed but was never journaled still leaves broker state that only the
   reconciliation cancel path (working orders) or manual intervention (filled orphans) can
   resolve. A production runner needs broker-side reconstruction of unjournaled mutations.
7. **RESOLVED — a concrete final reconciler exists for the synthetic boundary.**
   `CompositionReconciliationBackend` produces canonical account/order/position digests, open
   counts, and flatness from the synthetic broker's state and attests them through
   `SyntheticBrokerTruth.for_request`, proving session/account/phase attribution and flatness at
   CHECKPOINT and FINAL. This remains synthetic truth: no MCP reconciler observes a real
   broker, which stays scoped to #68.
8. **OPEN (#68) — dynamic host authority is not release-bound.** The `module:function`
   selector is unchanged (`cli.py`): trusted, unsandboxed host Python whose bytes are not bound
   to the selected release build. A production-capable host needs an authenticated,
   release-bound composition instead of an arbitrary import.
9. **OPEN (#68) — the timeline is replay input, not a production scheduler.** The caller still
   supplies the complete observation timeline up front; the runner validates ordering and arm
   containment but never reads a trusted current clock, waits for due windows, or drives expiry
   and hard-flat transitions after restarts. All composition clocks are injected
   fixture-derived rehearsal clocks by design.

With blockers 1–5 and 7 resolved for the synthetic execution class, the acceptance path for
issue #82 runs end-to-end without hand-constructed decisions, opportunity authority, permits,
active lifecycles, close results, or flat results — while every artifact remains labelled
`SYNTHETIC_FAKE` / `NOT_ALPHA_EVIDENCE`. Blockers 6, 8, and 9 keep the runner honestly named a
synthetic rehearsal: productionizing claim recovery, release-bound host authority, and an owned
clock/scheduler loop belongs to #68.

## Stacked dependency

Issue #82 starts from PR #81's `feat/strategy-release` head. PR #84 owns the corrected Alpaca MCP
2.3.1 provenance and protocol constants. Do not duplicate its changes in execution-policy,
Host-MCP, or #77 fixture files. After #84 merges into `feat/strategy-release`, restack this runner
once on the corrected head and rerun the focused fake scenarios, full suite, Ruff checks, package
build, and clean-wheel operator smoke. No production capability receipt may be minted from the
superseded constants.
