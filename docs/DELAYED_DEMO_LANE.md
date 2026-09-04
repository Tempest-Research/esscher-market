# Delayed-capture demo lane (V3) — owner-approved, loudly labeled

**Status: owner-approved 2026-09-04 (MS-Mesh) under issues #68/#101. This lane
exists because the host declined the Alpaca Algo Trader Plus purchase and the
Basic data plan cannot serve the validated lane's live-capture window.**

## What this lane is — and is not

The V3 lane runs the **identical frozen science input** as the validated V1
lane: the same 09:30:00–09:35:00 ET opening-reaction signal window, the same
features, betas, evidence rules, window-density gates, one-call/no-retry V5
reasoner route, risk kernel, permits, and broker-truth reconciliation. Only the
**capture/decision/entry clocks** move, because the Basic plan serves SIP data
only once it is older than fifteen minutes:

| Clock | V1 (validated, needs #101) | V3 (demo) |
| --- | --- | --- |
| Signal window | 09:30:00–09:35:00 ET | **identical** |
| Capture instant | 09:35:00–09:35:15 (data 15s old) | 09:50:05–09:51:00 (data ≥15min old = legal historical SIP, full density) |
| Evidence cutoff | 09:35:15 | 09:51:00 |
| Decision cutoff | 09:36:05 | 09:51:50 |
| Entry deadline | 09:37:00 | 10:01:00 (just after the first armed 10:00 scan window; satisfies the 30s risk-truth freshness bound under wall-clock processing) |
| Option quotes | OPRA executable data | **indicative feed** (`allows_indicative_data` demo policy; `INDICATIVE_DATA` labels) |

**Disclosed risks (also frozen inside the policy bytes):**
- Entry occurs ~25 minutes after the signal window instead of ~2. The
  continuation edge's decay over that gap is **unmeasured**; results from this
  lane are `NOT_THE_VALIDATED_LANE` and can never substantiate the V1
  hypothesis.
- Orders are priced from indicative (IEX equity / indicative option) quotes;
  paper fills may be optimistic versus real executable spreads.
- Every artifact, receipt, runbook step, and website surface touching this lane
  carries `DELAYED_EXECUTION_DEMO` / `NOT_THE_VALIDATED_LANE` /
  `INDICATIVE_OPTION_PRICING` labels.

Everything else — PAPER-only, no broker authority outside the armed session,
one call/no retry/no fallback, fail-closed abstentions, hard flat 15:30 ET,
broker-confirmed flatness — is unchanged.

## Policy identity

- `accepted_event_policy_v3.json` — `ESSCHER_ACCEPTED_EVENT_POLICY_V3`, digest
  `c3425c8dc259970966addcff9f21949b8fd71a006443a70392cacc868054ed7b`,
  candidate `EARNINGS_RESIDUAL_CONTINUATION_V3`, cohorts BMO/AMC.
- Reasoner registry triple (route/prompt/output-schema) registered; the
  output-schema hash is identical to V1 by construction.
- The V1/V2 packages, digests, and clocks are untouched; the latency profile
  stays bound to V1 and the current V5 route measurement.
- Expression: `demo_delayed_promoted_expression_policy()`
  (`DELAYED_DEMO_PROMOTED_EXPRESSION_V1`, `allows_indicative_data=true`).

## Session-day runbook (demo lane)

Prior session (Monday) 16:10 ET — universe freeze (ex-ante):

```
uv run python scripts/capture_lane.py discover --mode both --session-date <D> ...
uv run python scripts/capture_lane.py screen ...
uv run python scripts/capture_lane.py freeze-manifest --policy-version 3 ...
uv run python scripts/capture_lane.py prefetch ...        # historical, legal on Basic
```

Session day (Tuesday) 09:25 ET — release capture; 09:50:05–09:51:00 ET —
delayed window capture (the command refuses to run before the 15-minute
recency boundary clears the window end and after the 09:51:00 cutoff):

```
uv run python scripts/capture_lane.py capture-release --session-date <D> ...
uv run python scripts/capture_lane.py capture-window --delayed-demo --session-date <D> ...
uv run python scripts/capture_lane.py serialize --event-id <EVENT> ...
```

Then: fresh `ringdown paper-preflight` on the final build, session-arm mint for
the date, owner review of the exact session manifest, the **second explicit
owner authorization immediately before broker mutation** (`ESSCHER_MUTATION_AUTHORIZED=yes`
in the host selector environment — the mutation gate is closed without it), and
`ringdown paper-run` with the host selector configured for the demo lane
(`ESSCHER_DEMO_LANE=1`: demo expression policy + delayed captures; the selector
refuses any mix of the flag and capture claims in either direction). Windows
10:00–15:00 ET, hard flat 15:30 ET, broker-confirmed flatness, full receipt
set.

## Website live tracker (real profit/loss)

Beside `paper-run`, the host runs the publisher in watch mode:

```
uv run python scripts/publish_session_state.py --repo . --env-file <ringdown-market>/.env \
    --mint-dir <packet> --preflight <receipt.json> --rehearsal <dir> \
    --measurement <report.json> --lane DELAYED_EXECUTION_DEMO --session-status RUNNING --watch
```

It polls the read-only Alpaca REST endpoints every 60s and republishes
`session-state.json` to the `live-data` branch **whenever the account state
changes** (new position, P&L move, order event; timestamp-insensitive change
hash) plus a 15-minute heartbeat. The website panel (Live Desk page,
Tempest-Research/website) fetches through the GitHub contents API with
raw/jsdelivr fallbacks, auto-refreshes every 30s, and shows live equity, P&L
versus the $100k start, per-position unrealized P&L, open orders, gate
receipts, and the measured reasoner route — always under PAPER-only and
`DELAYED_EXECUTION_DEMO` / `NOT_THE_VALIDATED_LANE` labels. Redaction is
absolute: account id as sha256 digest only, no credentials anywhere in the
payload, and the publisher refuses any base URL whose host is not the Alpaca
PAPER API (`paper-api.` prefix on the Alpaca markets domain).

