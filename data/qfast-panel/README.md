# Q-FAST point-in-time panel data

**Status: DRAFT — assembly is blocked.** This folder holds the frozen ex-ante
selection rule for the untouched confirmatory Q-FAST panel only. No eligible
panel events, snapshots, decisions, or reports exist here yet.

Panel assembly and evaluation stay blocked until the upstream strategy
contracts merge:

- issue #26 — frozen Esscher v1 residual-earnings policy (strategy policy hash);
- issue #27 — read-only point-in-time strategy snapshot collector (snapshot protocol hash);
- issue #28 — validated residual decisions (decision protocol binding).

Until those artifacts are merged and their hashes are registered in
`src/ringdown_market/panel/manifest.py`, every real
`POINT_IN_TIME_EVENT_PANEL` manifest fails closed with
`UPSTREAM_CONTRACT_MISSING`. The four P0 contract-development events in
`data/earnings-replays/` are permanently excluded from this panel.

See [docs/research/qfast-point-in-time-panel.md](../../docs/research/qfast-point-in-time-panel.md)
for the full pipeline contract, fail-closed reason codes, and resume checklist.
