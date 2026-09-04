# Q-FAST point-in-time panel data

**Status: DRAFT — data layer frozen, assembly still blocked.**

This folder holds the frozen data layer of the untouched confirmatory Q-FAST
panel:

- `universe-freeze-v1.json` — ex-ante candidate enumeration, committed before
  any evidence or price lookup (git timestamp is the ordering proof).
- `universe/` — the frozen eligible universe: selection rule, event list, and
  one historical evidence manifest per event, validated by
  `ringdown_market.panel.universe.validate_panel_universe`.
- `market-windows/` — synchronized issuer/SPY/sector one-minute window
  provenance per event, validated by
  `ringdown_market.panel.windows.validate_market_window_set`.

## Frozen universe (23 eligible events)

23 scheduled BMO/AMC earnings events between 2026-07-01 and 2026-08-28 passed
the frozen criteria with primary-source provenance:

- 15 BEFORE_OPEN events, 8 AFTER_CLOSE events;
- 7 GICS sectors with SPDR proxies, market proxy SPY;
- every event carries its issuer Form 8-K press release (EDGAR exhibit URL,
  content SHA-256, acceptance timestamp) and the SEC_OFFICIAL filing index;
- every publication bound precedes the event decision cutoff.

Preserved exclusions from the same candidate enumeration:

| Event | Reason |
| --- | --- |
| C-20260714 | scheduled call during the regular session |
| COP-20260806 | scheduled call during the regular session |
| GS-20260714 | scheduled call at the session-open boundary |
| LLY-20260805 | scheduled call during the regular session |
| PFE-20260804 | scheduled call during the regular session |
| AMD (first pass) | resolved on second-pass exhibit review (eligible) |

Candidates with no in-window primary source: AVGO, BA, CRM, CVX, DIS, INTC,
MS, NFLX, PEP, PG, WMT, XOM (no event created; not exclusions).

The four P0 contract-development events remain permanently excluded.

## Market windows

Each event has a synchronized 82-bar one-minute window (session open through
open+81 minutes, fully split- and dividend-adjusted) for issuer, SPY, and the
sector ETF. Raw bar bytes stay host-side under the market-data entitlement;
the repository carries metadata and SHA-256 bindings only
(`METADATA_AND_HASH_ONLY`).

## What remains blocked

Status update 2026-09-04 (issue #67/#91, owner MS-Mesh):

1. issue #26 is closed completed and the frozen V1 strategy-policy digest
   `afce93b5...` is now registered in `KNOWN_STRATEGY_POLICY_SHA256`;
2. issue #27 merged the point-in-time snapshot compiler, but no canonical
   snapshot-protocol artifact digest exists yet: `KNOWN_SNAPSHOT_PROTOCOL_SHA256`
   deliberately stays empty and real manifests remain fail-closed on that field
   until the protocol is canonicalized under owner review;
3. issue #28 is closed completed: the merged research-decision protocol is
   enforced (`decision_protocol_sha256` must equal
   `RESEARCH_DECISION_PROTOCOL_SHA256`). Validated residual decisions over the
   23 events additionally require the host-side raw evidence bytes and 82-bar
   price paths (`METADATA_AND_HASH_ONLY`), which are unavailable on the current
   host; route-bound receipt generation over the 23 events stays blocked on
   that data, and the live-route bridge is demonstrated separately on the
   excluded contract-development fixture event
   (`scripts/generate_route_bound_decision.py`);
4. the host has supplied a measured p95 execution-latency profile: the packaged
   profile is `HOST_MEASURED` (nearest-rank p95 500 ms over 28 valid warm
   observations on the V4 route, issue #91, 2026-09-04).

Until item 2 (and the item-3 data availability) resolve, `ringdown
assemble-panel` still rejects real manifests with `UPSTREAM_CONTRACT_MISSING`.
The synthetic-rehearsal lane (`scripts/run_qfast_panel_evidence.py`) runs the
complete evidence machinery over the frozen universe and permanently claims
`NOT_ALPHA_EVIDENCE`.

See [docs/research/qfast-point-in-time-panel.md](../../docs/research/qfast-point-in-time-panel.md)
for the full pipeline contract, reason codes, and resume checklist.
