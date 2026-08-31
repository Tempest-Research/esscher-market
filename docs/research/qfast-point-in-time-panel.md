# Q-FAST point-in-time panel pipeline

**Status: DRAFT — data layer frozen, assembly blocked.** The panel compiler,
manifest contracts, validation tests, synthetic fixtures, the frozen 23-event
universe with primary-source provenance, and the synchronized market-window
provenance are in place. Panel assembly and evaluation remain fail-closed
until issues #26, #27, and #28 merge and a host-measured p95 latency profile
exists. This note is the lane contract for that assembly step.

## Data layer state

- Universe frozen ex-ante: candidate enumeration committed
  (`data/qfast-panel/universe-freeze-v1.json`) before any evidence or price
  lookup; the git timestamp is the ordering proof.
- 23 eligible events (15 BEFORE_OPEN, 8 AFTER_CLOSE, 7 sectors) with EDGAR
  primary provenance; validated by `validate_panel_universe`.
- 5 preserved exclusions with reasons (in-session or session-open-boundary
  calls); 12 candidates produced no in-window primary source; the four P0
  contract-development events remain permanently excluded.
- Synchronized 82-bar one-minute windows (issuer/SPY/sector, fully adjusted)
  for every eligible event; raw bars remain host-side
  (`METADATA_AND_HASH_ONLY`); validated by `validate_market_window_set`.
- Historical evidence manifests use schema
  `ringdown.historical_evidence_manifest` v1: retrieval legitimately
  postdates the cutoff because EDGAR accession preserves the exact historical
  version; every publication bound still precedes the decision cutoff.

## Purpose

Issue #3 requires an untouched 20–30 event point-in-time panel for the
reject-only Q-FAST screen. "Untouched" means the eligible universe, exclusion
reasons, latency profiles, baselines, and abstention treatment freeze before
any candidate return is inspected, and no event is ever added or removed
because its realized outcome helps the candidate.

## Frozen panel rules

- Exclude the four P0 contract-development events
  (`KR-2026Q2-EARNINGS`, `GIS-2027Q1-EARNINGS`, `MU-2026Q4-EARNINGS`,
  `NKE-2027Q1-EARNINGS`) and preserve every excluded event with its reason.
- Freeze the eligible universe and exclusion reasons before evaluating
  candidate returns; never tune on the confirmatory panel.
- Retain abstentions in the eligible-event denominator as zero signed return.
- Measure issuer, market, and sector prices over the same achievable entry and
  fill-relative exit window (`evaluation.evaluate_event` semantics).
- Run all frozen baselines at equal risk, including `ALWAYS_ABSTAIN`.
- Keep zero-latency and p95-latency results separated; the preregistered
  latency gate runs on `p95`.
- Eligible panel size is 20–30 events; the floor and ceiling are frozen in the
  selection rule and cannot move.

## Artifacts

| Artifact | Schema | Location |
| --- | --- | --- |
| Selection rule | `ringdown.earnings_replay_selection_rule` v1 | [data/qfast-panel/universe/selection-rule-v1.json](../../data/qfast-panel/universe/selection-rule-v1.json) |
| Frozen event list | `ringdown.frozen_earnings_event_list` v1 | [data/qfast-panel/universe/event-list-v1.json](../../data/qfast-panel/universe/event-list-v1.json) |
| Historical evidence manifests | `ringdown.historical_evidence_manifest` v1 | [data/qfast-panel/universe/events/](../../data/qfast-panel/universe/events/) |
| Market-window provenance | `ringdown.panel_market_window_provenance` v1 | [data/qfast-panel/market-windows/](../../data/qfast-panel/market-windows/) |
| Panel manifest | `ringdown.qfast_panel_manifest` v1 | produced when assembly unblocks |
| Panel bundle | `ringdown.qfast_panel_bundle` v1 | produced when assembly unblocks |
| Panel report | `ringdown.qfast_panel_report` v1 | produced by `ringdown assemble-panel` |
| Synthetic fixtures | panel schemas | [tests/fixtures/](../../tests/fixtures/) |

The selection rule carries only ex-ante criteria: source requirements,
point-in-time retrieval rules, synchronized-window requirement, abstention
denominator rule, the frozen 20/30 bounds, the required P0 exclusions, and the
claim boundary. It carries no event outcomes and no post-cutoff paths.

The panel manifest binds by hash:

- `selection_rule_sha256` — exact selection-rule bytes;
- `strategy_policy_sha256` — the frozen strategy policy from issue #26;
- `snapshot_protocol_sha256` — the snapshot protocol from issue #27;
- `decision_protocol_sha256` — must equal the merged
  `RESEARCH_DECISION_PROTOCOL_SHA256` from
  `src/ringdown_market/contracts/execution_policy.py`;
- per eligible event, `evidence_manifest_sha256` identity of its point-in-time
  evidence manifest.

The panel bundle carries the per-event decision snapshots and synchronized
price paths in the exact machine shape already consumed by
`ringdown evaluate`, and it re-declares the manifest's data class and
limitations so hygiene and claim checks stay visible.

## Fail-closed reason codes

`PanelRejectionReason` values are stable machine-readable receipts:

| Reason | Meaning |
| --- | --- |
| `INVALID_DOCUMENT` | malformed bytes, types, or values |
| `DUPLICATE_FIELD` | duplicate JSON key in any artifact |
| `MISSING_FIELD` / `UNKNOWN_FIELD` | strict schema drift |
| `UNSUPPORTED_SCHEMA` | wrong schema id or version |
| `HASH_MISMATCH` | artifact not bound to the supplied bytes |
| `DUPLICATE_EVENT_ID` | repeated event or evidence identity |
| `IDENTITY_MISMATCH` | bundle order/limitations differ from the frozen manifest |
| `PANEL_SIZE_VIOLATION` | eligible universe outside the frozen bounds |
| `P0_EVENT_IN_PANEL` | a P0 contract-development event entered the panel |
| `POINT_IN_TIME_VIOLATION` | any timestamp after the decision cutoff or freeze |
| `LATENCY_PROFILE_NOT_MEASURED` | p95 profile without a host-measured record |
| `UPSTREAM_CONTRACT_MISSING` | #26/#27/#28 artifacts not merged and registered |
| `CLAIM_BOUNDARY_MISMATCH` | weakened claim boundary or missing qualifiers |
| `SELECTION_RULE_VIOLATION` | frozen rule criteria, bounds, or profiles changed |
| `MISSING_PRICE_POINT` | path cannot supply achievable entry or exit |

## Leak gates

- The manifest freeze must equal the selection-rule freeze
  (ex-ante ordering).
- Bundle decision snapshots are rejected by `DecisionSnapshot` itself when
  `latest_evidence_at` or `feature_snapshot_at` postdate the decision cutoff.
- Latency measurements postdating the panel freeze are rejected.
- Evidence manifest identities are bound per event; real manifests require
  unique non-null evidence hashes.

## Determinism

Identical manifest, rule, and bundle bytes produce byte-identical reports:
canonical JSON (`sort_keys`, compact separators, `allow_nan=False`), sorted
report keys, two-space indent, LF, trailing newline. The report carries
`input_sha256` (exact bundle bytes), `protocol_sha256` (canonical panel
protocol), and the manifest/rule/policy lineage hashes.

## Claim boundary

Every report carries `claims: ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"]`,
the Q-FAST `claim` stays `NOT_ALPHA_EVIDENCE` regardless of outcome, and
`NOT_REJECTED_SMALL_SAMPLE` never promotes to proven alpha. Option prices, if
ever attached by other lanes, remain `INDICATIVE_DATA`. Q-LATENCY failure
forces `SHADOW_ONLY` through the existing latency gate.

## Dependencies and resume checklist

Assembly unblocks only when all of the following hold:

1. Issue #26 merged: register its strategy-policy hash in
   `KNOWN_STRATEGY_POLICY_SHA256` (`src/ringdown_market/panel/manifest.py`).
2. Issue #27 merged: register its snapshot-protocol hash in
   `KNOWN_SNAPSHOT_PROTOCOL_SHA256`.
3. Issue #28 merged: candidate decisions arrive through the frozen research
   decision protocol (hash already enforced).
4. A host-measured p95 latency profile with provenance exists (synthetic
   profiles are rejected for real panels).
5. Permitted timestamped market-data entitlement and redistribution terms are
   established for the event universe.

Until then `ringdown assemble-panel` rejects real manifests before any
evaluation runs; synthetic fixtures remain the only exercisable path.

## Limitations

- No confirmatory panel exists yet; nothing here is historical evidence.
- Synthetic fixtures are software-construction artifacts, not alpha, fill,
  or profitability evidence.
- The pipeline evaluates synchronized equity prices only; it never touches
  option chains, broker state, or account data.
- The selection rule encodes no market assumptions; universe thresholds come
  from the frozen strategy policy once issue #26 merges.

See also [point-in-time evidence gate](./point-in-time-evidence-gate.md) and
the [source and claim policy](../SOURCE_AND_CLAIM_POLICY.md).
