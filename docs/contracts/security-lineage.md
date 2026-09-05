# Point-in-time security master and corporate-action lineage contract

Issue #42 makes issuer, security, listing, and event identity reconstructible
at every decision cutoff without depending on the current ticker. The canonical
lineage is packaged as
`esscher/contracts/policies/security_lineage_v1.json` with SHA-256
`b400453a62ced05dacaa338dd59b90bceeba04853d9aef572ebfbcd16cb97ff5` and binds
the accepted event policy digest `afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca`
and the packaged source-matrix digest
`888447640aa705510bc0594abc9a78f22c988e961282ff82a6f44337181d04ca`. The
contract grants no collection, trading, or publication authority.

## Identity rule

Identity is CIK-rooted (`CIK_ROOTED_IDENTITY`). An EDGAR Central Index Key is
permanent and never reused, so issuer identity is the CIK; securities bind to
issuers; listings bind to securities with explicit periods; tickers are
attributes with validity windows. A trading-symbol change never changes
identity, and a ticker at cutoff that belongs to another issuer fails closed
as `SYMBOL_REUSE_DETECTED` rather than falling back to current survivors.

## Records

- Issuers: CIK, name history, and ticker validity windows, each with
  provenance.
- Securities: stable security identity bound to one issuer and security type.
- Listings: security, exchange MIC, listing period, and delisting reason;
  terminated listings require a reason, active listings forbid one, and
  overlapping periods for one security fail closed as `LINEAGE_CONFLICT`.
- Actions: `SPLIT`, `CASH_DIVIDEND`, `SYMBOL_CHANGE`, `MERGER`, `SPINOFF`,
  and `OPTION_ADJUSTMENT`. Splits require ratio terms; symbol changes require
  both symbols; mergers and spinoffs require a successor issuer; option
  adjustments require the OCC info-memo identifier. Conflicting split records
  fail as `LINEAGE_CONFLICT`; conflicting option adjustments fail as
  `OPTION_ADJUSTMENT_CONFLICT`.
- Chains: one authoritative chain per event binding issuer, security,
  listing, ticker at cutoff, option-listing state, and the as-of instant,
  with provenance links.

## Adjustment policy

Exactly one policy is frozen: splits are the only price-adjusting action;
cash dividends are recorded and never adjust prices; option adjustments are
authoritative only via OCC info memos; issuer actions never adjust market or
sector series. A listed-option chain with a split and no matching OCC option
adjustment fails closed as `OPTION_ADJUSTMENT_UNRESOLVED`.

## OCC access boundary

The OCC info-memo program is the authoritative public record of option
adjustments, but its web and API surfaces reject automated retrieval
(HTTP 403, receipt `LR4_OCC_ACCESS_BOUNDARY`). Capture therefore requires a
human-initiated session under the `HUMAN_VERIFIED_CAPTURE` condition, exactly
as the NYSE session-calendar source does in the issue #41 matrix. Synthetic
fixtures model the memo shape meanwhile.

## Evidence receipts

`data/security-lineage/evidence/` holds byte-pinned development-partition
receipts: LR1 a four-for-one split 8-K, LR2 an exchange-filed Form 25-NSE
delisting, LR3 a trading-symbol change under one stable CIK, and LR4 the OCC
access-boundary receipt. All committed bytes are public-domain EDGAR
accession artifacts pinned byte-exact by `.gitattributes`.

## Capture boundary

The capture command evaluates the lineage after the source-rights gate with
the same authenticated packaged source-matrix bytes used for rights preflight
and capture identity. It accepts no caller-selected matrix or lineage path: an
alternate matrix is rejected before resolution. The event chain must resolve
as-of the cutoff, the listing must be active at the cutoff (no
current-survivor fallback), and every binding must be consistent. Capture
writes `lineage_receipts.jsonl` through the same symlink-safe output boundary
as every other canonical artifact and binds the lineage digest into
`capture_identity.json`. Missing chains, delisted listings, reused symbols,
conflicted records, and drifted upstream contracts all fail closed with stable
reason codes before any snapshot exists.

## Reproduction

```bash
uv run --extra dev pytest tests/test_security_lineage_contract.py tests/test_lineage_gate.py -q
uv run --extra dev python scripts/check_repo_hygiene.py
```

Tests use the packaged lineage, canonical fixtures, and committed evidence
receipts; they make no network, account, MCP, or broker call.
