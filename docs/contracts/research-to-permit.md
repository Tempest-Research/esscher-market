# Frozen research decision to PAPER permit contract

This contract is the only bridge from a frozen research verdict to the existing paper execution plane. It is pure: it receives immutable bytes, validates them, and returns one immutable `DebitVerticalPermit` or a deterministic rejection. It cannot read market data, broker state, credentials, outcomes, or an MCP session.

```text
exact frozen decision bytes
+ exact point-in-time evidence-manifest bytes
+ exact feature-input-snapshot bytes
+ registered policy version
        |
        v
strict schema, hash, provenance, cutoff, eligibility, claim, shape, and risk checks
        |
        +--> deterministic rejection
        |
        v
immutable PAPER debit-vertical permit
        |
        v
existing single official Alpaca MCP boundary
```

The implementation is in `src/ringdown_market/contracts/research_to_permit.py`. The frozen research-decision protocol, Alpaca MCP execution protocol, and paper-risk registry entry are defined once in `src/ringdown_market/contracts/execution_policy.py`. The downstream adapter remains `src/ringdown_market/execution/mcp.py`; this bridge adds no order, REST, CLI, network, or alternate-adapter path.

## Versioned inputs

All three inputs are UTF-8 JSON objects with `schema_version: 1`. Duplicate keys, missing fields, unknown fields, unknown states, non-finite numbers, mutable `bytearray` inputs, and unsupported versions fail closed.

### `ringdown.frozen_research_decision`

The decision records:

- event identity: `event_id`, `issuer`;
- clocks: `decision_cutoff`, `latest_evidence_at`, `feature_snapshot_at`, `frozen_at`;
- verdicts: `decision_state`, `direction`, `eligibility`, `qfast_status`, `qlatency_status`;
- claim boundary: `claim`, `data_class`, `data_qualifiers`;
- immutable identities: `evidence_manifest_sha256`, `input_snapshot_sha256`, `protocol_sha256`, `policy_version`, `policy_sha256`;
- the already-chosen strategy: one exact debit vertical with underlying, type, expiry, quantity, limit debit, and both OCC contracts.

The runtime does not score, tune, select an event, choose a direction, choose a different spread, or inspect outcomes. `UP` must already specify a bull call; `DOWN` must already specify a bear put. `ABSTAIN` and `UNCERTAIN` are terminal non-execution states.

### `ringdown.point_in_time_evidence_manifest`

This is the frozen provenance artifact aligned with the accepted point-in-time rules in `docs/research/point-in-time-evidence-gate.md`. Each record retains event and issuer identity, a registered official/primary/licensed source kind, public URL, publisher, typed and precise `published_at`, retrieval and collector times, cutoff and snapshot times, raw-byte SHA-256, data class and qualifiers, entitlement and redistribution notes, and explicit field status. `field_source_refs` binds material research fields to exact evidence IDs.

Version 1 admits only:

- `POINT_IN_TIME_EVENT_PANEL`;
- sorted qualifiers `INDICATIVE_DATA` and `NOT_ALPHA_EVIDENCE`;
- an exact second- or minute-precision publication instant;
- `raw_bytes` hashing;
- `PRESENT` evidence with a permitted metadata/bytes boundary;
- evidence published, accepted (when present), retrieved, and first observed at or before `decision_cutoff`.

Generated, synthetic, missing, revised, conflicting, date-only, unknown-time, post-cutoff, unavailable, unpermitted, or unregistered evidence cannot produce a permit. The bridge validates declared provenance, exact artifact bytes, and the frozen research-protocol identity. It never generates, fills, rewrites, or falls back to evidence, and explicit generated/synthetic input states are not admitted. SHA-256 is an integrity commitment, not source authentication: an already-compromised trusted research producer could still lie inside a self-consistent manifest. Version 1 intentionally adds no signing key or credential. If the producer/host becomes an untrusted security boundary, execution must remain disabled until a separately reviewed signed-custody protocol is added; this PR does not claim to solve that threat model.

### In-process authority boundary

`DebitVerticalPermit` has no supported public constructor: the validated bridge supplies a private in-process authorization marker, and the MCP compiler then recomputes the permit ID across every serialized term. This blocks ordinary alternate construction and post-mapping mutation inside the application. Python module privacy is not a cryptographic sandbox; malicious code already executing inside the trusted host process could import private implementation details. That threat likewise requires process isolation and signed custody, not another self-declared hash.

### `ringdown.feature_input_snapshot`

Every frozen feature records `feature_id`, exact `source_refs`, the derived `source_max_public_at`, `feature_computed_at`, `definition_version`, `field_status`, `dependency_check`, and `value_sha256`.

The bridge proves dependency closure before issuing a permit:

- every source reference exists in the exact evidence manifest;
- `source_max_public_at` equals the maximum publication time of those exact records;
- no dependency is post-cutoff;
- no feature is computed after `feature_snapshot_at`;
- only `PRESENT` plus `ELIGIBLE` dependency state passes.

This implements the feature-level cutoff check required by the Lane B contract without changing or inventing Lane B source data.

## Frozen policy and protocol

The only registered policy is `paper-debit-vertical/v1`:

- mode: `PAPER`;
- data qualifier: `INDICATIVE_DATA`;
- strategy: one 1:1 debit vertical;
- quantity: one spread package;
- maximum opening debit: USD 500;
- permit lifetime: 60 seconds from the exact decision-cutoff freeze;
- research protocol: the frozen decision/evidence/input contract identity;
- execution protocol: the pinned official Alpaca MCP identity.

The policy binds two distinct protocol identities. The research-protocol hash freezes the three input schemas, admitted verdict/gate states, claim/data boundary, raw-byte representation, feature dependency gate, and cutoff freeze rule. The execution-protocol hash binds Alpaca MCP `2.3.0`, commit `872abbf28dab6cdde7d341fc13ac139b8002d1d9`, PAPER/indicative boundaries, and the five official lifecycle tools already used by the adapter. There is no `latest` alias, mutable registry fallback, alternate mode, direct REST/CLI path, or future live-money value.

## Field mapping

| Permit field | Rule |
| --- | --- |
| `decision_sha256` | SHA-256 of the exact decision bytes, including whitespace and ordering. |
| `evidence_sha256` | Verified SHA-256 of the exact evidence-manifest bytes. |
| `input_snapshot_sha256` | Verified SHA-256 of the exact feature-input bytes. |
| `protocol_sha256` | Copied from the immutable registered research-decision protocol after exact comparison. |
| `execution_protocol_sha256` | Derived from the immutable registered official Alpaca MCP protocol. |
| `policy_sha256` | Copied from the immutable policy registry entry after exact version/hash comparison. |
| `permit_id` | Derived from every serialized authorization term: lineage hashes, timing, risk, mode, data class, strategy, and legs. |
| `event_run_id` | Derived from the exact decision-byte hash. |
| `issued_at` | Derived as the exact `frozen_at`, which v1 requires to equal `decision_cutoff`. |
| `expires_at` | Derived as `issued_at + 60 seconds`. |
| vertical type, quantity, price, expiry, symbols, strikes | Copied without interpretation from the already-approved strategy. |
| sides and position intents | Derived mechanically as long buy-to-open then short sell-to-open. |
| `run_mode` | Fixed to `PAPER`; not caller-selectable. |
| execution `data_class` | Fixed to `INDICATIVE_DATA`; not caller-selectable. |

The official MCP compiler recomputes the registered permit ID before it can form an order call. Changing a lineage hash, timing value, risk term, mode, instrument, or leg after mapping invalidates the permit before tool use. One `McpPaperBroker` instance consumes each opening permit ID before its first awaited tool call and rejects a second attempt without tool use; across process/session restarts, the deterministic Alpaca `client_order_id` remains the reconciliation/idempotency boundary.

## Eligibility and deterministic rejection

A permit requires all of the following:

- `APPROVED`, directional, and `ELIGIBLE`;
- Q-FAST `NOT_REJECTED_SMALL_SAMPLE`;
- Q-LATENCY `NOT_REJECTED_SMALL_SAMPLE`;
- exact cutoff/freeze identity and dependency-closed evidence;
- `NOT_ALPHA_EVIDENCE` plus `INDICATIVE_DATA` claim boundaries;
- exact evidence, input, protocol, and policy hashes;
- supported direction/vertical shape and the frozen paper-risk cap.

Failures raise `DecisionPermitRejected` with a stable `PermitRejectionReason`, path, and detail. Reasons distinguish invalid/unknown schema, hash, policy, protocol, time, stale/missing evidence, provenance, eligibility, Q-FAST, Q-LATENCY, claim, strategy, and risk failures. Validation order is fixed, so identical invalid bytes return the same reason.

## Replay and fixture boundary

Identical decision bytes, exact evidence/input bytes, and policy version produce identical permit objects, bytes, and IDs. Semantically equal decision JSON with different bytes deliberately has a different `decision_sha256` and permit identity.

`tests/contract_fixtures/frozen_research_decision_v1.json` is an explicitly labeled `SYNTHETIC_CONTRACT_FIXTURE` envelope with `NOT_HISTORICAL_DATA`, `NOT_ALPHA_EVIDENCE`, and `NO_BROKER_EXECUTION`. Its embedded production-shaped documents exercise software contracts only. Tests never open an MCP session or make a broker call, and the fixture is not Lane B source data or evidence of alpha, pricing, fills, or profitability.

## Version and rollback

Version impact is `minor`. The package remains `0.2.0`; issue #11 owns the release-train consolidation to `0.3.0`. Rollback is a code revert: there is no schema migration, credential change, broker call, order, position, release, or external state to unwind.
