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

Panel assembly and Q-FAST evaluation stay fail-closed until:

1. issue #26 merges and its strategy-policy hash is registered in
   `KNOWN_STRATEGY_POLICY_SHA256`;
2. issue #27 merges and its snapshot-protocol hash is registered in
   `KNOWN_SNAPSHOT_PROTOCOL_SHA256`;
3. issue #28 produces validated residual decisions through the merged
   research-decision protocol;
4. the host supplies a measured p95 execution-latency profile.

Until then `ringdown assemble-panel` rejects real manifests with
`UPSTREAM_CONTRACT_MISSING` / `LATENCY_PROFILE_NOT_MEASURED`.

See [docs/research/qfast-point-in-time-panel.md](../../docs/research/qfast-point-in-time-panel.md)
for the full pipeline contract, reason codes, and resume checklist.
