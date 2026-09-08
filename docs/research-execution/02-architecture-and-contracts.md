# Architecture and contract discussion

Draft 0.1 · 8 September 2026 · Proposal for Ben, Alex and Yaroslav

This document expands [the group brief](01-project-brief.md). All record names and semantics here are proposed, not implemented APIs. Alex's repository has not been inspected for this pack. Do not interpret this as permission to change code or connect an account.

## 1. System ownership

### Research laboratory

Own hypotheses, source provenance, datasets, features, model artifacts, evaluation splits, portfolio policy, target selection and experiment conclusions. Run models here and emit reviewed targets. Keep training labels and historical reports immutable with versioned corrections.

### Execution pipeline

Own account state, outstanding orders, reservations, operational instrument rules, current valuation inputs, sizing, venue/simulator commands, lifecycle, persistence, risk enforcement and reconciliation. When broker-paper mode is later approved, venue reports must be reconciled rather than treating the internal cache as independent proof.

### Shared boundary

Agree one canonical, versioned contract with one file maintainer and review by both consumers. No shared mutable database and no research-side broker credentials. One strategy, one isolated account, one writer and no multi-strategy netting in the first profile.

Yaroslav could contribute to either implementation, contract tests or the comparison layer. Cross-project contribution does not confer implicit account authority or make him the sole reviewer of his own work.

## 2. Proposed records

### StrategyBundle

Meaning: the identity and evidence of an evaluated strategy version.

Suggested required fields:

- `schema_version`, `research_project_id`, `strategy_version`, `bundle_digest`.
- Dataset snapshot/content digest; feature-pipeline version; model artifact identity; portfolio-policy identity.
- Universe version and instrument-map version; currencies and calendar conventions.
- Training/validation/test boundaries, data-availability rules and evidence-report identity.
- Rebalance schedule, intended execution policy, cost assumptions, limitations and requested operating envelope.

The digest binds canonical serialized content and referenced immutable artifact identities; choose the algorithm and serialization rules during contract ratification. An artifact path on one person's machine is not a portable identity. Execution must not deserialize arbitrary model/code payloads from a request. Initially it consumes targets produced by the research-side model.

### PortfolioIntent

Meaning: the desired portfolio for one decision time, not a list of guaranteed executable orders.

Suggested required fields:

- `schema_version`, `intent_id`, `bundle_digest`.
- `environment`: `historical_simulation`, or `broker_paper` only after separate approval. No live-money profile.
- Opaque account/sleeve reference; `decision_at`, `data_cutoff_at`, `valid_from`, `expires_at`.
- `targets`: complete weights for the explicitly managed universe, with explicit residual cash.
- Instrument-map version, execution window, reviewed execution-policy ID.
- Reference-price/valuation identity, cost assumptions, requested sizing and price/slippage constraints.

Proposed initial constraints: daily decisions, long-only, no leverage, one currency and one isolated simulated account. These depend on compatibility with Alex's actual project; revise the profile rather than forcing an incompatible market into it.

Missing instruments are not an implicit instruction to liquidate. Define the full snapshot and zeros explicitly. Out-of-universe holdings or unresolved overlapping intents cause a mismatch/abstention in the first profile. Do not silently reinterpret a weight as a quantity.

### ExecutionEvent

Meaning: an observation or recorded transition, linked to its cause.

- `schema_version`, `event_id`, `event_kind`, `intent_id`, strategy/bundle identity.
- Client order ID and venue order ID where available; correlation and causation IDs.
- Source timestamp, local received timestamp, available source sequence and local journal position.
- Status/cause code, raw evidence reference and normalized payload.
- Fill quantity/price, fee amount/currency, remaining order quantity when applicable.

Event kinds distinguish intent accepted/rejected, submission attempted, acknowledgement, rejection, partial/full fill, cancellation requested/confirmed, unknown outcome and reconciliation. Never manufacture missing venue identifiers or timestamps. Arrival order need not equal venue event order.

### ReconciliationSnapshot

Meaning: account state and the degree to which it agrees with authoritative execution records.

- Snapshot ID, environment, opaque account reference, as-of timestamp, journal position and provenance.
- Positions, cash/balances, open orders and reservations.
- Match/mismatch/unresolved status; discrepancy details; permitted next action.

No new exposure while an unresolved mismatch invalidates sizing or ownership assumptions. Corrections are appended, not used to erase the original observations.

## 3. Time, prices and quantities

Use UTC timestamps with explicit precision/units. Record publication/availability time separately from economic event time. Version security mappings, calendar rules and corporate actions; distinguish adjusted research prices from executable unadjusted prices. Decimal monetary and quantity representations should not silently lose precision.

Validate tick/lot requirements and define rounding policies rather than allowing a library default to change an order. Reject unsupported sizes in the first profile. Keep cash residual and fees explicit.

Historical simulation uses a controlled historical clock. Exporting historical predictions today does not refresh the original data cutoff or make them eligible for broker-paper use. Different environments must have distinct validation and routing, not merely different labels in a report.

## 4. Intake, transport and authority

Start with one immutable JSON request file and JSON Lines event exports, plus a reconciliation report. Publish a completed request atomically; validate it and persist acceptance before effects. The export is not the durable journal itself. If Alex already has a suitable API/IPC boundary, keep it and map these semantics onto it.

On repeated delivery:

- Same intent ID, same canonical payload: return recorded progress/outcome without generating another economic effect.
- Same ID, different payload: reject the conflict.
- Concurrent rebalance intents: reject overlap initially; do not invent implicit priority.
- Crash between venue submission and local acknowledgement: recover by querying/reconciling before retrying.

Execution checks its own allowlist/approval binding strategy digest, environment and limits. Request fields cannot self-authorize. The stricter applicable limit wins. Approved targets may still be rejected or remain unfilled; report why. If execution clips or changes a target under an agreed policy, record the constraint and resulting decision explicitly.

There is no unconditional exactly-once broker guarantee. The bounded goal is durable intent handling, idempotency where the venue supports it, reconciliation and abstention when outcome is unknown.

## 5. Proposed acceptance corpus

Each case needs inputs, initial state, fault schedule, expected events/state invariants and a replay command learned from the actual implementation. These are required scenarios, not passing tests:

1. Valid baseline target reaches a reconciled simulated state.
2. Duplicate intent before and after restart creates no duplicate order/effect.
3. Reused ID with changed content is rejected.
4. Lost acknowledgement is resolved without blind resubmission.
5. Partial fill plus cancellation preserves the executed quantity.
6. Fill/cancel race and delayed/duplicate fill reports do not double-count.
7. Expired data, wrong environment, unknown symbol or invalid lot/tick fails before submission.
8. Insufficient cash, open-order reservations or independent risk limits constrain/reject safely.
9. Restart with journal/venue mismatch blocks new exposure until resolved.
10. A risk stop prevents new submissions and separately tracks working-order cancellation; it does not imply liquidation or cancellation success.
11. Historical signals cannot be routed as fresh broker-paper intents.
12. Changed execution assumptions yield an explained comparison difference rather than overwritten research output.

Keep synthetic lifecycle fixtures separate from market-performance experiments. Test evidence should include real assertions and failure traces, not just a green process exit.

## 6. Integration files — only after inspection

Possible logical modules: contract schemas, research export adapter, execution intake adapter, normalized event importer, discrepancy analysis and shared test fixtures. Actual paths and native test commands must follow both repositories. Do not invent runnable commands for files that do not exist.

Contract changes need compatibility tests for both producers and consumers. Unsupported schema versions fail closed rather than being guessed. Preserve supported older fixtures when a compatible change is introduced; explicitly version breaking meaning changes.

## Open decisions

- Actual repositories/branches, languages, venue, common market and horizon.
- Whether target weights fit the pipeline better than quantities/orders.
- Artifact storage, canonical serialization, schema tooling and durable execution store.
- Price benchmark and instrument/lot conventions.
- Chosen reuse components, their licence obligations and data rights.
- Shared-contract maintainer and first bounded contribution for each person.

See [collaboration and delivery](03-collaboration-and-roadmap.md) and [agent review instructions](04-agent-research-brief.md) before turning this proposal into implementation tasks.
