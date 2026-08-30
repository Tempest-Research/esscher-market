# Strategy data pipeline (read-only)

This document specifies the point-in-time strategy snapshot collector required by issue #27. The pipeline is permanently read-only: it never mutates a broker, never loads credentials, and never reaches the network from packaged code. Live read-only capture is a separately recorded gate; the in-package registry ships fake deterministic adapters only (`src/ringdown_market/data/adapters.py`).

Snapshot schema: `esscher.strategy_snapshot` version 1. Every snapshot is bound to the frozen strategy policy hash (`STRATEGY_POLICY_V1_SHA256`) and to the exact identities of the raw sources it consumed.

## 1. Sources

### Primary earnings/guidance evidence

Permitted source kinds: `ISSUER_PRIMARY`, `SEC_OFFICIAL`. Every evidence record carries:

- publisher and exact public source URL;
- `published_at` as a typed publication instant (`issuer_release_timestamp` or `official_dissemination_timestamp`) with second/minute precision;
- `accepted_at` separately when SEC acceptance metadata exists (never substituted for publication time);
- `retrieved_at` when our process captured the bytes;
- `content_sha256` over the frozen representation (`raw_bytes`);
- entitlement note and explicit redistribution status.

Rules follow the [point-in-time evidence gate](research/point-in-time-evidence-gate.md): no date midpoints, no acceptance-to-web lag estimates, no retrieval time masquerading as publication time. Conflicting independent publication times make the snapshot ineligible rather than silently arbitrated.

### Market data

Stock (event ticker), market proxy (`SPY`), and the frozen sector proxy use one adjusted-bar policy (`SPLIT_AND_DIVIDEND_ADJUSTED_V1`) and synchronized timestamps. The preferred production boundary is the official Alpaca MCP read-only stock-data tools behind a host-injected session; the exact server version and tool schemas must be pinned before a live adapter is implemented, and collection stops if the required historical windows are unavailable. The packaged collector consumes only injected adapter results and never instantiates a transport.

Each bar observation retains its raw observation timestamp (`raw_observed_at`), source identity, and adjustment policy. An observation whose raw time postdates the decision cutoff fails closed as `STALE_OBSERVATION`; an observation dated after the cutoff fails closed as `POST_CUTOFF_EVIDENCE`.

## 2. Timing rules

- The observation window is the registered 09:30:00–09:35:00 `America/New_York` opening window expressed in UTC on the reaction session.
- The decision cutoff is frozen to the observation window end (`OBSERVATION_WINDOW_END`). Any event whose `decision_cutoff` differs from `window_end_at` is rejected at event construction.
- No evidence or market value after the decision cutoff can enter a snapshot. Evidence publication, bar timestamps, and raw observation times are each checked against the cutoff.
- Opening features are computed only from bars present at both window endpoints for all three synchronized series; otherwise the snapshot records `UNSYNCHRONIZED_WINDOW` or `MISSING_BARS` and becomes ineligible.

## 3. Adjustment and corporate actions

- One adjusted-bar policy applies to issuer, market, and sector series; mixed adjustment policies fail closed.
- Splits, dividends, and symbol changes remain explicit receipts (`SPLIT`, `DIVIDEND`, `SYMBOL_CHANGE`). Split receipts require a positive factor; symbol-change receipts require the destination symbol; violations fail closed as unresolved corporate actions.
- Beta estimation uses only pre-cutoff synchronized return triples (`estimate_frozen_betas`, minimum two points, positive proxy variance). Re-estimation after outcomes is forbidden by the frozen beta policy.

## 4. Feature construction

Features match the frozen strategy information set exactly:

- `earnings_numeric/v1`, `guidance_statement/v1`: evidence-bound; their value hash binds the sorted content identities of the admitted evidence records.
- `opening_return/v1`, `market_opening_return/v1`, `sector_opening_return/v1`: log returns between the synchronized window endpoint prices, serialized with fixed 12-decimal text.
- `market_beta/v1`, `sector_beta/v1`: frozen-policy betas from the pre-cutoff estimation series.

Every feature records `source_max_public_at`, `feature_computed_at` (the cutoff freeze), `dependency_check: ELIGIBLE`, and a value hash. Missing or contradictory facts remain explicit and make the snapshot ineligible; they are never imputed.

## 5. Entitlement and redistribution

- `PUBLIC_BYTES_ALLOWED`: raw bytes may accompany the record; they are hashed before any parsing.
- `METADATA_AND_HASH_ONLY`: only URL, metadata, and hash may be retained; raw bytes fail closed as `REDISTRIBUTION_VIOLATION`.
- `UNAVAILABLE_NOT_PERMITTED`: metadata only; any raw bytes fail closed at the provenance contract.

No raw licensed payload is committed to the repository. Committed fixtures carry hashes, URLs, and metadata only, and are labelled `SYNTHETIC_CONTRACT_FIXTURE` with explicit limitations.

## 6. Failure and recovery rules

Eligibility failures are recorded as stable reason codes inside the snapshot (`rejection_reasons`) instead of raising, so downstream consumers see explicit ineligibility: `MISSING_EVIDENCE`, `POST_CUTOFF_EVIDENCE`, `STALE_OBSERVATION`, `MISSING_BARS`, `UNSYNCHRONIZED_WINDOW`, `NON_FINITE_VALUE`, `INELIGIBLE_UNIVERSE`, `CORPORATE_ACTION_UNRESOLVED`, `REDISTRIBUTION_VIOLATION`, `BETA_ESTIMATION_FAILED`. Structural violations of the contract (malformed documents, unknown fields, policy drift) fail closed by raising typed rejections.

Recovery after an interrupted collection is re-collection from sources: identical source bytes and policy produce byte-identical snapshots, so a re-run either reproduces the snapshot exactly or fails closed. There is no incremental repair path.

## 7. Capture command

`ringdown capture-snapshot --host-config <json> --policy <json> --input <json> --output <file>`:

- requires an explicit host configuration (`adapter_registry` limited to the in-package read-only registries; credential-like keys rejected);
- requires the exact frozen policy bytes (hash-checked before compilation);
- performs no broker mutation and no network access (verified by socket-disabled tests);
- writes canonical snapshot bytes and prints a sanitized summary with the snapshot SHA-256.

The command never contains credentials, account identifiers, or raw broker payloads.
