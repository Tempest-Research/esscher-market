# Autonomous host runner: synthetic coordinator rehearsal scaffold

This is an incomplete draft for issue #82. The code in
`runtime/autonomous_host.py` composes `AutonomousSessionCoordinator` with typed fake-host
adapters so that release/arm validation, deterministic windows, duplicate claims, state
transitions, hard-flat handling, and sanitized receipts can be rehearsed offline. It does **not**
compose the accepted V2 strategy path, `PaperStrategyApplication`, a monitored
`ActivePaperLifecycle`, or the guarded MCP broker lifecycle. It therefore is not the requested
end-to-end acceptance path, and issue #82 remains open.

The scaffold is evidence about coordinator contracts only. It is not evidence that Kimi, an
Alpaca account, an MCP server, an option fill, a durable close after process loss, or a production
deployment has been exercised.

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
attestation into evidence that the real application path ran.

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
replay. It does **not** persist or reconstruct the application's `ActivePaperLifecycle`, canonical
permit bytes, lifecycle clocks, close-critical binding, MCP request/order correlation, or broker
intent state. Consequently, a restart test that replays `ActiveLifecycleIdentity` through a fake
`LifecycleCloserPort` is a synthetic coordinator restart test, not durable application-lifecycle
rehydration and not proof that a new process can safely close real exposure.

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

## Exact blockers to completing issue #82

1. **The host ports stop before the accepted source-to-application path.** The collector accepts
   host-created candidate observations and the candidate adapter accepts a host-created outcome.
   Neither adapter consumes the accepted fake's exact source bytes or invokes V2 context,
   reasoner, confirmation, expression, risk, application, or monitored lifecycle code.
2. **V2 has no execution authority.** `StrategyV2DirectionDecision` is deliberately
   `DIRECTION_ONLY_UNCONFIRMED`, and `accepted_event_policy_v2.json` explicitly says it defines no
   validated alpha threshold. Creating a confirmed decision from that proposal would invent
   authority unless an owner-approved deterministic confirmation contract is added.
3. **The downstream execution seams are still legacy-shaped.** The expression compiler and
   compiled-to-permit bridge accept the legacy `StrategyDecision`; `PaperStrategyApplication`
   calls legacy `RiskKernel.authorize_entry`, while the autonomous release binds V2 risk policy.
   There is also no authority-bearing compiler that deterministically derives the risk tier
   instead of accepting caller-supplied `decision_ready`/tier values.
4. **The host account is not attested before mutation.** `HostMcpPaperSessionFactory` validates the
   pinned PAPER capability and sanitizes account status, but it does not derive and bind the
   observed host account fingerprint to `AutonomousSessionArm.account_fingerprint_sha256`.
5. **A real active lifecycle cannot be durably rehydrated.** `AutonomousSessionStore` persists only
   `ActiveLifecycleIdentity`; `RiskLedger` retains permit identity/hash rather than canonical
   permit bytes; and `LifecycleMcpPaperBroker` keeps request/order correlation in process-local
   memory. A fresh process therefore lacks the exact permit, clocks, close-critical binding, and
   correlation needed for safe close-only authority.
6. **Claim recovery deliberately stops instead of reconstructing mutation truth.** A persisted
   `CLAIMED` opportunity becomes `CLAIM_RECOVERY_UNKNOWN`. This avoids duplicate submission but
   does not reconcile a crash between external mutation and recording an active lifecycle.
7. **Real final reconciliation is absent.** `SyntheticBrokerTruth` makes the fake receipt
   structurally explicit, but no concrete MCP reconciler produces canonical account/order/position
   bytes and proves their session/account attribution and flatness.
8. **Dynamic host authority is not release-bound.** `module:function` code is trusted and
   unsandboxed, and its bytes are not bound to the selected release build. A production-capable
   host needs an authenticated, release-bound composition instead of an arbitrary import.
9. **The timeline is replay input, not a production scheduler.** The caller supplies the complete
   observation timeline up front. The scaffold validates ordering and arm containment, but it does
   not read a trusted current clock, wait for due windows, bind collection to actual wall time, or
   drive expiry and hard-flat transitions after process restarts. A production runner needs an
   owned clock/scheduler loop whose observations cannot be backdated or selected by the host.

Until those interfaces exist and an acceptance test traverses them without hand-constructing the
decision, opportunity authority, permit, active lifecycle, close result, or flat result, the
runner must remain named and documented as a synthetic coordinator rehearsal scaffold. A pull
request for this slice may reference issue #82, but it must not claim to close it.

## Stacked dependency

Issue #82 starts from PR #81's `feat/strategy-release` head. PR #84 owns the corrected Alpaca MCP
2.3.1 provenance and protocol constants. Do not duplicate its changes in execution-policy,
Host-MCP, or #77 fixture files. After #84 merges into `feat/strategy-release`, restack this runner
once on the corrected head and rerun the focused fake scenarios, full suite, Ruff checks, package
build, and clean-wheel operator smoke. No production capability receipt may be minted from the
superseded constants.
