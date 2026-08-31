# Deterministic feature-receipt contract

Issue #43 owns the canonical deterministic arithmetic boundary between
immutable evidence and every baseline or LLM decision arm. Numeric arithmetic
and data-health decisions are never delegated to model prose. The receipt
schema is `esscher.feature_receipt/v1`, serialized by
`ringdown_market/strategy/contracts.py` and produced by
`ringdown_market/sourcedata/compiler.py`.

## Bound fields

Every receipt binds:

- `strategy_snapshot_sha256`, `policy_sha256`, `producer_build_sha256` — the
  exact snapshot bytes, frozen policy, and compiler/build identity;
- ordered `features` with versioned IDs, exact `Decimal` values, units,
  statuses, observation times, and source references;
- `decision_cutoff_at` and `feature_snapshot_at` — the frozen candidate
  clocks; `feature_snapshot_at` can never exceed the decision cutoff;
- `maximum_public_timestamp` — the latest publisher timestamp across all
  packet evidence; it can never exceed the decision cutoff, otherwise
  compilation fails closed with `MAXIMUM_PUBLIC_TIMESTAMP_AFTER_CUTOFF`;
- `data_health` and `health_reason_codes` mirrored from the bound snapshot;
- `evidence_ids` — the sorted packet evidence IDs plus, when the issue #42
  lineage gate runs, a `LINEAGE_RECEIPT:<sha256>` identity;
- `lineage_receipt_sha256` — the SHA-256 of the exact canonical issue #42
  lineage receipt bytes, computed and supplied by the capture boundary.

## Preregistration

The compiler enforces that the compiled feature IDs exactly match the
preregistered policy registry for the candidate: 13 earnings features and 20
macro features, all `.v1`, disjoint across candidates. Any extra or missing
feature fails closed with `FEATURE_REGISTRY_MISMATCH`. Unavailable features
stay `UNAVAILABLE` with null values; nothing is imputed or selected based on
observed data.

## Clock separation

BMO, AMC, and macro cohorts each derive from their own frozen policy clocks.
A cohort outside the candidate's frozen vocabulary fails with
`TIMING_BUCKET_UNKNOWN`; a cohort flip that contradicts the frozen clocks
fails with `CLOCK_MISMATCH`. Receipts always carry the manifest cohort.

## Determinism and negative capability

Identical manifest, policy, compiler, and lineage bytes produce
byte-identical receipts; any binding change changes the bytes. The compiler
boundary imports no network, LLM-provider, account, order, position, or
broker surface — proven by AST import scans and socket-disabled runtime
compilation — and receipt payloads carry no execution-authority fields.

## Reproduction

```bash
uv run --extra dev pytest tests/test_feature_receipt_contract.py -q
uv run --extra dev pytest tests/test_capture_lineage_receipt.py -q
uv run --extra dev python scripts/check_repo_hygiene.py
```

Tests use synthetic development fixtures only; they make no network, account,
MCP, or broker call.
