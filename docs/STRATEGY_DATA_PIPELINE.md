# Strategy data pipeline

This document is the read-only point-in-time data contract required by issue
#27. It describes how the collector turns permitted primary evidence and
synchronized equity market data into canonical strategy snapshots without
rerunning research, touching a broker, or hiding host state. The pipeline is
comparison/collection tooling, not research evidence; it never infers a
missing fact and never claims alpha, profitability, or executable fills.

## Boundary

The collector consumes the exact strategy input contract frozen by issue #26
(`accepted_event_policy_v1.json`, SHA-256
`3234017de2fec6c33dce20508f483d649d4614130e76cdc6f57af8185e05d05e`) and emits
the canonical `esscher.strategy_snapshot/v1`,
`esscher.feature_receipt/v1`, source receipts, and corporate-action receipts
that `build_strategy_input` joins under the frozen policy. It adds no
features, thresholds, or timing rules of its own.

- No network, subprocess, broker, or MCP call exists anywhere in
  `src/ringdown_market/sourcedata/` (enforced by an AST-level test).
- No order, account, position, trading, or mutation method exists on any
  adapter interface (enforced by a surface test).
- Host credentials never appear in code, fixtures, logs, reports, or CLI
  history; the capture command requires explicit host authorization
  (`ESSCHER_CAPTURE_AUTHORIZED=yes`) and fails closed without it.

## Sources and source classes

Permitted source classes come from the frozen policy evidence registry:

| Class | Use |
| --- | --- |
| `OFFICIAL_EXCHANGE_CALENDAR` | session calendar, reaction session identity |
| `POINT_IN_TIME_SECURITY_MASTER` | issuer identity, sector, exchange, price floor, optionability at freeze |
| `ISSUER_INVESTOR_RELATIONS` | primary earnings/guidance facts |
| `SEC_EDGAR` | optional corroborating filings (not a publication-time substitute) |
| `LICENSED_POINT_IN_TIME_CONSENSUS` | consensus surprises (absent in this slice; reported `UNAVAILABLE`, never imputed) |
| `LICENSED_PERMITTED_NEWS` | unused by v1 features |
| `LICENSED_SIP_EQUITY_TRADES` | opening-window prints, daily bars, window volumes |
| `LICENSED_SIP_EQUITY_QUOTES` | NBBO spread and quote-age features |
| `CORPORATE_ACTION_RECORD` | splits, dividends, symbol changes |

Required classes for every earnings snapshot: `OFFICIAL_EXCHANGE_CALENDAR`,
`POINT_IN_TIME_SECURITY_MASTER`, `ISSUER_INVESTOR_RELATIONS`,
`LICENSED_SIP_EQUITY_TRADES`, `CORPORATE_ACTION_RECORD`. A missing required
class fails closed with `FEATURE_DEPENDENCY_MISSING`; an unregistered class
fails with `UNPERMITTED_SOURCE_CLASS`; unverified entitlement fails with
`SOURCE_RIGHTS_UNVERIFIED`.

Provenance fields stay separate: publisher time (`published_at`) and its
precision, retrieval time (`retrieved_at`), content identity
(`content_sha256`), entitlement, and redistribution status. SEC acceptance
time never substitutes for publisher time.

## Timing rules

All clocks realize the frozen policy clock for the event cohort
(America/New_York wall time converted to UTC):

| Clock | BMO and AMC value |
| --- | --- |
| Observation window | 09:30:00–09:35:00 |
| Evidence cutoff | 09:35:15 |
| Decision cutoff | 09:36:05 |
| Candidate entry deadline | 09:37:00 |
| Universe freeze | 16:15 on the prior eligible regular session |

Enforced gates:

- the capture clock (`capture_at`) and every retrieval time must be at or
  before the evidence cutoff (`RETRIEVED_AFTER_CUTOFF`);
- primary publication must precede the reaction session open
  (`PRIMARY_RELEASE_LATE`), BMO publication must fall on the reaction date,
  and AMC publication must follow the prior session close;
- missing publisher time on primary evidence fails with
  `PUBLICATION_TIME_UNKNOWN`;
- the reaction session must be a full regular 09:30–16:00 session
  (`CLOCK_MISMATCH`).

Synchronized-window data-health bounds (frozen policy `data_health` rules):

| Rule | Bound | Failure code |
| --- | --- | --- |
| start observation delay | ≤ 15 seconds | `MARKET_OBSERVATION_MISSING` |
| end observation age | ≤ 15 seconds | `MARKET_OBSERVATION_STALE` |
| cross-instrument endpoint skew | ≤ 5 seconds | `MARKET_OBSERVATION_ASYNCHRONOUS` |
| quote age at window end | ≤ 1000 milliseconds | `MARKET_OBSERVATION_STALE` |
| forward fill | forbidden | gaps fail as missing |
| numeric finiteness | required | `NON_FINITE_FEATURE` |

Only `REGULAR_CONTINUOUS` prints enter window statistics; opening auctions
and non-regular conditions are excluded. Duplicate observations at one
timestamp fail with `DUPLICATE_OBSERVATION`. Every window record keeps its
raw observation timestamp.

## Corporate actions and adjustments

- Splits are the only price-adjusting action. Each split applies the ratio
  `denominator/numerator` to all closes strictly before its ex-date so the
  series is expressed on the latest basis; the disclosure is the applied
  corporate-action receipt identity.
- Cash dividends follow the frozen price-only policy: they are recorded as
  receipts and never alter returns.
- Symbol changes are not resolved by this collector version and fail closed
  with `CORPORATE_ACTION_UNRESOLVED`.
- Conflicting splits sharing one ex-date fail with
  `MATERIAL_SOURCE_CONFLICT`; a split without a provenance receipt fails
  with `CORPORATE_ACTION_UNRESOLVED`.

## Beta estimation

One OLS regression of split-adjusted price-only daily log returns:

```text
r_stock = alpha + beta_market * r_SPY + beta_sector * (r_sector - r_SPY) + error
```

- window: exactly 252 sessions ending 21 sessions before the reaction
  session;
- at least 200 aligned observations (`BETA_INSUFFICIENT_OBSERVATIONS`);
- no winsorization, no forward fill;
- regressor condition number at most 30, computed as the square root of the
  eigenvalue ratio of the centered regressor cross-product matrix
  (`BETA_ILL_CONDITIONED`);
- bounds `beta_market ∈ [-1, 3]`, `beta_sector ∈ [-2, 2]`
  (`BETA_OUT_OF_BOUNDS`).

All arithmetic runs under one fixed decimal context (50 digits,
round-half-even); natural logarithms use the atanh series and square roots
use Newton iteration, so identical inputs produce identical outputs without
binary floating point.

## Features

All thirteen frozen earnings-candidate features are emitted with policy-exact
units, value types, and statuses. Missing consensus is reported
`UNAVAILABLE` and never imputed; when consensus is unavailable the frozen
time-series SUE path is required and built from issuer quarter history.
Required market features depend on synchronized windows, frozen betas, and
verified SIP quote entitlement; a missing dependency fails closed with
`FEATURE_DEPENDENCY_MISSING`.

## Determinism and receipts

Identical source bytes, policy, and capture clock produce byte-identical
snapshot, receipt, and packet bytes. The evidence packet hash binds the exact
serialized source receipts; the feature receipt binds the snapshot hash; the
snapshot binds the manifest hash and the policy hash. The producer identity
is the frozen constant
`sha256(canonical({"producer": "esscher.sourcedata.snapshot_compiler", "contract": "esscher.strategy_snapshot", "version": 1}))`;
no wall-clock value is generated inside the library.

## Recovery

Capture is a pure function of explicit inputs: re-running the capture with
the same fixture, manifest bytes, and capture clock re-derives identical
artifacts. There is no hidden state, cache, or incremental store to repair;
recovery is recomputation, and divergence is detectable through the bound
SHA-256 lineage.

## Live boundary

The preferred live market-data boundary is the official Alpaca MCP
read-only stock-data surface. It is not pinned in this slice: the capture
command rejects `--live` with `LIVE_BOUNDARY_NOT_PINNED` until a separate
recorded gate pins the exact server version and tool schemas and confirms the
required historical windows are available. Fake adapters replay one frozen
synthetic fixture so every test stays deterministic and offline.

## Verification

Exact commands observed during implementation:

```text
command: uv run pytest tests/test_strategy_snapshot_collector.py -q
result: 35 passed in 6.04s

command: uv run pytest -q
result: 323 passed in 10.12s

command: uv run ruff check .
result: All checks passed!

command: uv run ruff format --check .
result: 82 files already formatted

command: uv run python scripts/check_repo_hygiene.py
result: repository hygiene: PASS (114 visible files checked)

command: uv build
result: Successfully built dist/ringdown_market-0.2.0.tar.gz and dist/ringdown_market-0.2.0-py3-none-any.whl
```
