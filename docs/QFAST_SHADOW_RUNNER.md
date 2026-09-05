# Q-FAST shadow runner and full-stack shadow ledger

Deterministic evidence-execution slice for issue #67 (issue #83 slice): validate the frozen 23-event / 7-sector Q-FAST inputs, consume immutable V2 direction receipts, generate byte-stable zero-latency and p95 reports, and append a full-stack shadow ledger — without provider, account, or broker mutation. Stacked on PR #81 (`feat/strategy-release`); the final #82 integration consumes the same decisions through the shadow replay plan.

A green run of this slice is **engineering evidence only**. Every report carries `claim: NOT_ALPHA_EVIDENCE`; synthetic receipts are labelled `SYNTHETIC_RECEIPTS` and can never become candidate evidence; promotion requires a HOST_MEASURED latency profile (#64), route-bound receipts, and a surviving p95 latency gate.

## Inputs (all frozen, all validated before outcome access)

- Panel manifest + selection rule (`ringdown.qfast_panel_manifest` / `..._selection_rule`) — event count, sector count, hold, latency profiles, rights limitations.
- Frozen universe (event list + per-event evidence manifests) and synchronized market windows — source and clock status.
- Immutable V2 direction receipts (`esscher.direction_receipt/v2`, `alpha/direction_receipts.py`) — one bounded `UP`/`DOWN`/`UNCERTAIN` decision per event with cutoff-safe evidence/feature timestamps and producer attribution (`ROUTE_BOUND` hashes or `SYNTHETIC` + `NOT_ALPHA_EVIDENCE`).
- Latency profile (`esscher.latency_profile`) — the packaged PREREGISTERED p95 bound until #64 supplies a HOST_MEASURED replacement.
- Panel bundle (`ringdown.qfast_panel_bundle`) — decision snapshots and post-decision price paths; receipts are cross-checked against bundle decisions before any evaluation.

`alpha/evidence_validator.py` reports exact event count, sector count, manifest identity, source/clock/rights status, and every rejection reason. Validation failure blocks evaluation entirely: no rows, no reports, no ledger records.

## Trace: INPUT → EVENT → OUTPUT → STATE

1. **INPUT** — validator binds manifest/selection/universe/windows/receipts/profile/policy hashes; any mismatch is a stable rejection code (`MANIFEST_INVALID`, `DECISION_SET_INCOMPLETE`, `POLICY_MISMATCH`, `LATENCY_PROFILE_MISMATCH`, `EVENT_COUNT_MISMATCH`, `SECTOR_COUNT_MISMATCH`, `RIGHTS_STATUS_UNVERIFIED`, `SOURCE_STATUS_UNVERIFIED`, `CLOCK_STATUS_INVALID`).
2. **EVENT** — per event, the receipt direction and ex-ante scores configure the candidate and baseline methods (`price_only_continuation`, `numeric_score_continuation`, `cash_always_abstain`); baselines never observe candidate outcomes. `alpha/evaluation.py` evaluates each method from the first achievable entry after the latency arm (0 ms or p95 ms) through the fill-relative hold; abstentions keep zero signed return and stay in the denominator.
3. **OUTPUT** — `alpha/shadow_runner.py` emits one canonical `esscher.qfast_shadow_report/v1`: zero and p95 arms over the identical denominator, reject-only Q-FAST metrics, latency gate status, perturbation/stability deltas (hold ×2, latency ÷2), promotion recommendation with explicit reasons, and the run SHA-256.
4. **STATE** — `alpha/shadow_ledger.py` appends append-only decision+outcome episode pairs through the existing `RiskLedger` episode API (no schema change): signal, theoretical expression, fake lifecycle `SHADOW_THEORETICAL_FLAT`, costs, clocks, `SHADOW_THEORETICAL` P&L class, and final-flat state. Identical replays are idempotent; restarts re-read rows; broker truth and shadow P&L stay in separate classifications.
5. **HANDOFF** — `alpha/fullstack_adapter.py` builds the strict `esscher.shadow_replay_plan/v1` (terminal-flat by construction, labels `NO_BROKER_MUTATION`, `NO_CREDENTIALS`, `SHADOW_ONLY`, `SOURCE_GROUNDED`) so the #82 fake-proven runner consumes the same decisions without reinterpretation.

## Reproduce

```text
uv run python scripts/run_qfast_shadow_evidence.py \
  --manifest <panel manifest> --selection-rule <rule> --bundle <bundle> \
  --receipts-dir <receipts> --event-list <event list> \
  [--universe-dir <universe>] [--windows-dir <windows>] \
  [--latency-profile <profile>] [--policy-sha <sha>] [--out out/qfast-shadow]
```

The command writes `qfast_shadow_report.json`, `evidence_validation.json`, and (when accepted) `shadow_replay_plan.json` under the ignored `out/` directory and prints their SHA-256 digests. Identical inputs reproduce byte-identical artifacts.

## Boundary

No Kimi or provider call, no latency fabrication, no account query, no Alpaca MCP invocation, no PAPER order, cancellation, position mutation, deployment, merge, tag, or real-money path. Owned surface: `src/esscher/alpha/` evaluation/reporting modules, `scripts/run_qfast_shadow_evidence.py`, focused tests and synthetic fixtures, and this document. `runtime/autonomous.py`, `cli.py`, `risk/ledger.py`, execution/MCP code, policy JSON, and release contracts are consumed, never edited.
