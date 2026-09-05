# Strategy data pipeline

This document is the read-only point-in-time data contract required by issue
#27. It describes how the collector turns permitted primary evidence and
synchronized market data into canonical strategy snapshots for both the
earnings primary candidate and the macro challenger, without rerunning
research, touching a broker, or hiding host state. The pipeline is
comparison/collection tooling, not research evidence; it never infers a
missing fact and never claims alpha, profitability, or executable fills.

## Boundary

The collector consumes the exact strategy input contract frozen by issue #26
(`accepted_event_policy_v1.json`, SHA-256
`afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca`) and emits
the canonical `esscher.strategy_snapshot/v1`, `esscher.feature_receipt/v1`,
source receipts, corporate-action receipts, and one
`esscher.data_feasibility_manifest/v1` per candidate, all of which
`build_strategy_input` joins under the frozen policy. It adds no features,
thresholds, clocks, or timing rules of its own.

- No network, subprocess, broker, or MCP call exists anywhere in
  `src/esscher/sourcedata/` (enforced by an AST-level test).
- No order, account, position, trading, or mutation method exists on any
  adapter interface (enforced by a surface test).
- Host credentials never appear in code, fixtures, logs, reports, or CLI
  history; the capture command requires explicit host authorization
  (`ESSCHER_CAPTURE_AUTHORIZED=yes`) and fails closed without it.

## Earnings sources and source classes

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

## Macro sources and source classes

The macro challenger (`MACRO_SPY_CONTINUATION_CHALLENGER_V1`) uses the
official BLS release families plus SPY market observations:

| Class | Use |
| --- | --- |
| `OFFICIAL_BLS_RELEASE_CALENDAR` | frozen official schedule per reference period |
| `OFFICIAL_BLS_RELEASE` | first-vintage release fields for the event reference period |
| `OFFICIAL_BLS_REVISION_TABLE` | official revisions to prior reference periods |
| `LICENSED_SIP_SPY_TRADES` | SPY event-window and normalization-session prints |
| `LICENSED_SIP_SPY_QUOTES` | SPY anchor and event-window NBBO quotes |

All five classes are required for every macro snapshot. The schedule is never
inferred from a normal release time: the frozen official schedule entry must
match the manifest `scheduled_at` exactly, or the event fails with
`SCHEDULE_NOT_FROZEN`.

## Source-rights preflight

Before loading an adapter, capture identifies the exact accepted candidate and
evaluates only that candidate's required source classes against the one
packaged `esscher.source_matrix/v1` resource. Its fixed SHA-256 is
`888447640aa705510bc0594abc9a78f22c988e961282ff82a6f44337181d04ca`; each
preflight rebinds its policy and Gate A digests before source selection.

Every covering source must be non-blocked and all of its recorded conditions
must be explicitly satisfied. A blocked class yields `SOURCE_RIGHTS_BLOCKED`;
an unmet condition yields `SOURCE_RIGHTS_LIMITATION_UNMET`; a changed packaged
matrix or upstream binding yields `SOURCE_MATRIX_DRIFT`. There is no
caller-selected matrix path. Consensus and news remain `BLOCKED` without
changing this slice because consensus is represented as `UNAVAILABLE` and no
v1 feature consumes news.

The capture command requires an explicit synthetic fixture. It passes that
loaded fixture into the compiler adapters, allowing an installed wheel to run
the same offline capture without a repository test-fixture fallback. Capture
clocks are explicit zero-offset UTC values only. The command never opens a
network, provider, broker, account, MCP, order, or trading path; `--live`
remains an explicit fail-closed boundary.

## Retrieval integrity

Pagination, partial retrieval, and duplicate source records are explicit and
never pass silently:

- each evidence entry carries `pages_retrieved` / `pages_total`; a partial
  retrieval fails closed with `PAGINATION_INCOMPLETE`;
- two evidence entries with identical content identity fail closed with
  `DUPLICATE_SOURCE_RECORD`;
- duplicate market observations at one timestamp fail closed with
  `DUPLICATE_OBSERVATION`.

## Earnings timing rules (BMO and AMC stay distinct)

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

BMO and AMC cohorts share the same clock values but remain separate cohorts:
BMO selects the same-day reaction session with a `BEFORE_OPEN` timing bucket,
while AMC selects the next full regular session after publication with an
`AFTER_CLOSE` timing bucket.

## Macro timing rules

| Cohort | Observation window | Evidence cutoff | Decision cutoff | Entry deadline |
| --- | --- | --- | --- | --- |
| `BLS_JOLTS` | 10:00:00–10:15:00 | 10:15:15 | 10:16:05 | 10:17:00 |
| `BLS_EMPLOYMENT_SITUATION` | 09:30:00–09:45:00 | 09:45:15 | 09:46:05 | 09:47:00 |

The macro reaction session is the session containing the official release and
must be a full regular session (`NON_FULL_REGULAR_SESSION`). The JOLTS anchor
uses SPY NBBO midpoints over the five minutes before the observation window;
the Employment Situation anchor is the first in-window SPY midpoint. Macro
snapshots use the `SCHEDULED_RELEASE` timing bucket.

## Macro vintages and revisions

Macro release vintages cannot silently use revised values:

- base macro fields always come from the first published vintage
  (`vintage_index = 1`) of the event reference period;
- official revisions published at or before the evidence cutoff are recorded
  separately in the `macro.revision_vector.v1` feature (one component per
  revised field, value = revised − initial) and in the revision-table receipt;
- a missing first vintage fails with `OFFICIAL_RELEASE_MISSING`; publication
  after the evidence cutoff fails with `OFFICIAL_RELEASE_LATE`;
- conflicting revisions for one field fail with
  `REVISION_FIELD_CONFLICTING`; an absent official revision table fails with
  `REVISION_FIELD_MISSING`.

## Synchronized-window data-health bounds

Frozen policy `data_health` rules applied to both candidates:

| Rule | Bound | Failure code |
| --- | --- | --- |
| start observation delay | ≤ 15 seconds | `MARKET_OBSERVATION_MISSING` |
| end observation age | ≤ 15 seconds | `MARKET_OBSERVATION_STALE` |
| cross-instrument endpoint skew | ≤ 5 seconds | `MARKET_OBSERVATION_ASYNCHRONOUS` |
| quote age at window end | ≤ 1000 milliseconds | `MARKET_OBSERVATION_STALE` |
| forward fill | forbidden | gaps fail as missing |
| numeric finiteness | required | `NON_FINITE_FEATURE` |

Only `REGULAR_CONTINUOUS` prints enter window statistics; opening auctions
and non-regular conditions are excluded. Every window record keeps its raw
observation timestamp.

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

## Beta estimation (earnings only)

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

All thirteen frozen earnings-candidate features and all twenty frozen
macro-challenger features are emitted with policy-exact units, value types,
and statuses. Missing consensus is reported `UNAVAILABLE` and never imputed;
when consensus is unavailable the frozen time-series SUE path is required and
built from issuer quarter history. Macro cohort fields are `PRESENT` only for
the event cohort and `NOT_APPLICABLE` otherwise. Required market features
depend on synchronized windows, frozen betas (earnings) or normalization
history (macro), and verified SIP quote entitlement; a missing dependency
fails closed with a stable reason code.

## Gate B data-feasibility manifests

Each candidate gets one `esscher.data_feasibility_manifest/v1` recording, for
every declared source family: source, endpoint, publisher clock, timestamp
precision, retrieval semantics, revision behavior, identifiers, entitlement,
retention/redistribution rights, feed/adjustment policy, historical coverage,
known gaps, and one reproducible sample-receipt hash. The manifest verdict is
fail-closed:

- `FEASIBLE` only when every required source class has a declaration and a
  bound sample receipt with verified rights;
- otherwise `INFEASIBLE` with stable reasons
  (`MISSING_REQUIRED_SOURCE`, `SOURCE_RIGHTS_UNVERIFIED`).

An infeasible earnings verdict records that the macro challenger evaluation is
triggered (`fallback_candidate_id`) and carries the
`NO_TRADE_AUTHORIZATION` claim: it never authorizes a trade.

## Determinism and receipts

Identical source bytes, policy, and capture clock produce byte-identical
snapshot, receipt, packet, and feasibility-manifest bytes. The evidence packet
hash binds the exact serialized source receipts; the feature receipt binds the
snapshot hash; the snapshot binds the manifest hash and the policy hash. The
producer identity is the frozen constant
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
required historical windows are available. Generic Alpaca account, position,
order, or trading tools are prohibited. Fake adapters replay one frozen
synthetic fixture so every test stays deterministic and offline.

## Verification

Exact commands observed during the corrected local forward-port:

```text
command: uv run pytest tests/test_strategy_snapshot_collector.py -q
result: 60 passed

command: uv run pytest -q
result: 362 passed

command: uv run ruff check .
result: All checks passed!

command: uv run ruff format --check .
result: 85 files already formatted

command: uv run python scripts/check_repo_hygiene.py
result: repository hygiene: PASS (117 visible files checked)

command: uv build
result: Successfully built dist/ringdown_market-0.2.0.tar.gz and dist/ringdown_market-0.2.0-py3-none-any.whl
```
