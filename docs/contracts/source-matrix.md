# Source rights and point-in-time feasibility contract

Issue #41 decides, before any collector implementation, whether every required
source is lawful, point-in-time reproducible, and operationally usable. The
canonical matrix is packaged as
`ringdown_market/contracts/policies/source_matrix_v1.json` with SHA-256
`87d552d96599818f4a996748318e75944286f17166a58e4b596fee35d5b518c7` and binds
the accepted event policy digest `3234017de2fec6c33dce20508f483d649d4614130e76cdc6f57af8185e05d05e`
and the Gate A contract digest `40c2e780c684bdde671b028dbdd8c9b13268e659c24e98a2d452ff7c8692f955`.
The contract grants no collection, trading, or publication authority.

## Verdicts

Every source ends in exactly one of:

- `FEASIBLE`: lawful, reproducible, and operationally usable within the
  recorded limits;
- `FEASIBLE_WITH_LIMITATIONS`: usable only with the recorded limitations and
  conditions, each of which fails closed when unmet;
- `BLOCKED`: ambiguous rights, unverified entitlement, or a paid plan without
  recorded human approval.

Rights ambiguity is always `BLOCKED`, and no paid plan is ever selected
without a recorded human approval. A `BLOCKED` verdict is a terminal decision,
not a failure of this contract: it blocks the dependent lane before
implementation cost is incurred.

## Category verdicts

| Category | Verdict summary | Lane effect |
| --- | --- | --- |
| Earnings-calendar revisions | FEASIBLE (issuer IR + EDGAR 8-K); FEASIBLE_WITH_LIMITATIONS (NYSE session calendar) | earnings lane open with documented capture limits |
| Issuer/SEC bytes | FEASIBLE | immutable accession bytes with acceptance-time clocks |
| News | BLOCKED | lane-safe: v1 features never consume news; consensus-free citations stand |
| Consensus | BLOCKED | lane-safe: features report UNAVAILABLE and the frozen SUE path substitutes |
| Fundamentals | FEASIBLE | EDGAR XBRL with acceptance-time point-in-time clocks |
| Stock/market/sector observations | FEASIBLE_WITH_LIMITATIONS | historical SIP verified on the development account; real-time SIP requires a paid plan that stays unapproved; competition-account entitlement unverified per Gate A |
| Macro vintages | FEASIBLE | BLS schedules, releases, and revision evidence are public domain |
| Current options | FEASIBLE (contract master); FEASIBLE_WITH_LIMITATIONS (indicative snapshots); BLOCKED (OPRA) | indicative data only; never OPRA/NBBO evidence |
| Historical option BBO | BLOCKED | blocks historical option-expression evidence only; the underlying-direction lane does not depend on it |

## Records

Each source records endpoint, identifiers, publisher/availability clock,
timestamp precision, revision policy, depth, adjustment policy, completeness,
entitlement, retention/redistribution rights, rate limits, the paid-plan flag
and any human approval, the verdict, limitations, conditions, and at least one
evidence record. Evidence is either a `DOCUMENT` receipt (URL, retrieval time,
content hash, exact quote) or a `BUNDLE` reference into the golden-bundle
registry. Direct Alpaca hosts are rejected from committed endpoint strings.

The strict parser rejects unknown, missing, and duplicate fields, unknown
verdicts or conditions, malformed digests, and evidence without retrieval
times. Ambiguous entitlement or redistribution rights with a non-blocked
verdict fail as `RIGHTS_AMBIGUOUS`; paid plans without approval fail as
`PAID_PLAN_UNAPPROVED`; uncovered categories fail as `MATRIX_INCOMPLETE`.

## Golden bundles

Three to five development-only golden bundles under
`data/source-feasibility/golden-bundles/` prove reproducible capture without
consuming the untouched panel:

- `GB1_EDGAR_AAPL_20230202` — immutable EDGAR accession bytes for one
  development-partition AMC earnings event, including the full-text-search
  discovery receipt;
- `GB2_BLS_JOLTS_202110` — archived JOLTS release, revision narrative, and
  official 2021 release schedule;
- `GB3_NYSE_SESSION_CALENDAR` — official NYSE Trading Days document bytes;
- `GB4_ALPACA_EQUITY_OBSERVATIONS` — hash-only receipts proving historical SIP
  bars and quotes succeed while real-time SIP fails closed with the
  subscription message on the Basic plan;
- `GB5_ALPACA_CURRENT_OPTIONS` — hash-only receipts proving the option
  contract master and indicative snapshots on the Basic plan.

Bundle events fall only in development partitions; tests assert they never
fall inside any frozen untouched partition and never reuse the four P0
contract-development events. Licensed market-data bytes are never committed:
Alpaca probes retain only endpoint paths, parameters, statuses, byte counts,
and SHA-256 receipts.

## Capture boundary

The snapshot collector consumes the matrix at its capture command: every
required earnings source class must map to a non-blocked source, and every
condition on the chosen source must be declared satisfied by the host. The
capture identity binds the matrix digest. Unknown conditions, missing matrix
files, drifted upstream contracts, blocked classes, and unmet limitations all
fail closed before any snapshot exists.

## Reproduction

```bash
uv run --extra dev pytest tests/test_source_matrix_contract.py tests/test_source_rights_gate.py -q
uv run --extra dev python scripts/check_repo_hygiene.py
```

Tests use the packaged matrix, the committed golden bundles, and canonical
fixtures; they make no network, account, MCP, or broker call.
