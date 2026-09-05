# Source rights and point-in-time feasibility contract

Issue #41 fixes the source-rights decision before a snapshot can be compiled. It is a local, deterministic contract boundary: it grants no collection, trading, publication, account, broker, MCP, or network authority.

## Canonical binding

The only capture matrix is the packaged `esscher.source_matrix/v1` resource:

- resource: `esscher/contracts/policies/source_matrix_v1.json`
- matrix SHA-256: `888447640aa705510bc0594abc9a78f22c988e961282ff82a6f44337181d04ca`
- accepted event-policy SHA-256: `afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca`
- Gate A programme-contract SHA-256: `40c2e780c684bdde671b028dbdd8c9b13268e659c24e98a2d452ff7c8692f955`
- matrix decision time: `2026-08-30T21:55:00Z`

Every rights evaluation authenticates the packaged matrix and then rebinds its stored policy and Gate A hashes to the currently authenticated packaged upstream bytes. A changed policy or Gate A resource is therefore rejected as `SOURCE_MATRIX_DRIFT` until the matrix is intentionally amended and re-reviewed.

There is no production `--source-matrix` option. The internal byte seam accepts only the exact frozen matrix digest for deterministic tests; it cannot select an alternative rights decision.

## Matrix scope and verdicts

The matrix records 21 sources across all nine required categories:

1. earnings-calendar revisions;
2. issuer and SEC bytes;
3. news;
4. consensus;
5. fundamentals;
6. equity, market, and sector observations;
7. macro vintages;
8. current options; and
9. historical option BBO.

Each record carries its endpoint description, identifiers, availability clock, timestamp precision, revision and adjustment policy, depth, completeness, entitlement, retention/redistribution status, rate limits, evidence, limitations, and conditions. It ends in exactly one verdict:

- `FEASIBLE`;
- `FEASIBLE_WITH_LIMITATIONS`; or
- `BLOCKED`.

Ambiguous rights always remain `BLOCKED`. A capture must satisfy every condition on its selected non-blocked source; unmet conditions fail closed as `SOURCE_RIGHTS_LIMITATION_UNMET`. A required class covered only by blocked records fails as `SOURCE_RIGHTS_BLOCKED`.

## Candidate-specific preflight

The capture command determines the accepted candidate before it evaluates rights. Earnings captures therefore use their five earnings source classes, while macro captures use the BLS calendar, release, revision-table, SPY-trade, and SPY-quote classes from `MACRO_SPY_CONTINUATION_CHALLENGER_V1`. No macro capture may reuse the earnings preflight.

All matrix timestamps must use an explicit zero-offset UTC form (`Z` or `+00:00`). Naive timestamps and non-zero offsets are rejected, so parsing never depends on the host timezone.

A paid record can be non-blocked only with a structured approval record: decision `APPROVED`, a stable uppercase approver identifier, and an approval timestamp at or before the matrix decision time. Any other decision, missing identity, or inverted chronology fails closed.

## Development evidence

Five committed golden bundles provide development-only, reproducible evidence. They are labelled `NOT_ALPHA_EVIDENCE`, `NO_BROKER_EXECUTION`, and `NOT_HISTORICAL_DATA`; licensed market-data bundles retain hash receipts rather than payload bytes. The bundle partition checks prevent their events from becoming untouched evaluation evidence.

Direct provider endpoints are not committed as a live capture path. The matrix does not contain credentials, direct provider hosts, account identifiers, or broker authority.

## Offline capture boundary

The command operates only with an explicit synthetic fixture path, an explicit UTC capture clock, explicit host authorization, and explicitly declared matrix conditions. The fixture is passed through to the compiler adapters; an installed wheel never looks for a repository test-fixture path.

```bash
ESSCHER_CAPTURE_AUTHORIZED=yes uv run python -m esscher.sourcedata.capture \
  --event-id KR-2026Q2-EARNINGS \
  --fixture tests/fixtures/sourcedata/synthetic_snapshot_inputs_v1.json \
  --capture-at 2026-09-11T13:35:10Z \
  --output-dir build/issue41-capture \
  --condition-satisfied HUMAN_VERIFIED_CAPTURE \
  --condition-satisfied PER_RECORD_PRIMARY_PROVENANCE \
  --condition-satisfied GATE_A_EQUITY_ENTITLEMENT_RECEIPT
```

This replays only committed synthetic adapters. `--live` remains rejected until a separate reviewed boundary pins the exact read-only server and schema surface. The command never opens a provider, broker, account, MCP, or trading session.

## Verification

```bash
uv run --extra dev pytest \
  tests/test_source_matrix_contract.py \
  tests/test_source_rights_gate.py \
  tests/test_issue41_capture_boundary.py -q
uv run --extra dev python scripts/check_repo_hygiene.py
```

The tests use only packaged resources, committed development bundles, explicit synthetic fixtures, and local temporary output directories.
