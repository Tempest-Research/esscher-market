# Accepted event-strategy research contract V1

**Status:** preregistered research policy; no alpha or execution authority
**Canonical policy:** `src/ringdown_market/strategy/policies/accepted_event_policy_v1.json`
**Registry digest:** `afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca`
**Execution boundary:** Alpaca PAPER only, with entry currently disabled

## 1. What V1 does

Esscher freezes a candidate universe before outcomes, builds one immutable point-in-time evidence packet, asks a bounded reasoner for exactly `UP`, `DOWN`, or `UNCERTAIN`, and lets deterministic code validate or veto that direction. The policy covers two distinct research candidates:

1. `EARNINGS_RESIDUAL_CONTINUATION_V1` — the primary candidate, with BMO and AMC as separate cohorts.
2. `MACRO_SPY_CONTINUATION_CHALLENGER_V1` — an inactive SPY challenger, with BLS JOLTS and Employment Situation as separate cohorts.

The policy does **not** approve a trade expression, DTE, width, quantity, price, risk percentage, holding period, exit winner, account, permit, order, or broker mutation. Those values require Gate A facts and Gate C/D results. The machine-readable policy therefore has an empty `production_allowed` array and `UNSELECTED` expression, exit, and risk states for both candidates.

The typed boundary consists of `esscher.candidate_manifest/v1`,
`esscher.strategy_snapshot/v1`, `esscher.feature_receipt/v1`,
`esscher.reasoner_exchange/v1`, and `esscher.validated_decision/v1`. A strategy input is valid only
when exact candidate-manifest bytes contain the snapshot event and agree on issuer, permanent
security ID, ticker, cohort, eligibility, exclusion reasons, policy, and freeze time. This is the
machine-enforced barrier against discretionary symbol insertion.

## 2. Authority boundary

The LLM controls only:

- `UP`, `DOWN`, or `UNCERTAIN`;
- a bounded summary;
- evidence references;
- contradictions and unknowns; and
- its strongest evidence-backed falsifier.

Deterministic code controls the universe, eligibility, timestamps, source validity, arithmetic, features, confirmation, instruments, packages, quantity, price, risk, permits, execution, exits, and reconciliation. The LLM receives no credentials or broker tools and cannot return any executable parameter.

`UNCERTAIN` always means `NO_TRADE`. A confirmation rule may preserve or veto `UP` or `DOWN`; it cannot reverse a direction or create one from `UNCERTAIN`.

## 3. Gate A is deliberately unresolved

Issue [#40](https://github.com/Tempest-Research/esscher-market/issues/40) owns the changing external facts. V1 records all of these as `UNVERIFIED` with `null` values:

- scoring objective, base capital, horizon/deadline, official mark, and costs;
- allowed instruments and leverage;
- drawdown, flattening, and intervention rules;
- dedicated PAPER account state and reset assumptions;
- equity/options entitlements; and
- option approval level and atomic multi-leg PAPER capability.

An unverified Gate A fact cannot be replaced with a default. `gate_a.overall_status` is `UNVERIFIED`, so the policy mandates `ENTRY_DISABLED`. Research code may implement and test the frozen contracts without pretending the external facts have been verified.

## 4. Earnings primary candidate

### Universe and freeze

The candidate list freezes at 16:15 America/New_York on the immediately preceding eligible regular session. It includes only active NYSE/Nasdaq U.S. common stocks, excludes ADRs, ETFs, funds, OTC securities, preferreds, and warrants, and requires:

- prior regular-session close at least $10;
- at least 18 valid sessions in the preceding 20;
- 20-session median dollar volume at least $50 million;
- a listed option observed before the freeze; and
- an exact, non-conflicting BMO or AMC classification.

Every considered event and exclusion remains recorded. Capacity ranking is descending frozen median dollar volume, then ticker and event ID in ASCII order. Ranking never changes denominator membership and no human may insert a preferred ticker.

The market proxy is SPY. The point-in-time GICS sector map is fixed to XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLK, XLB, XLRE, or XLU. An unknown sector mapping is not guessed.

### Separate clocks

| Cohort | Reaction session | Observation | Evidence freeze | Decision cutoff | Research entry deadline |
|---|---|---:|---:|---:|---:|
| BMO | Same eligible full session after a release proven public before the open | 09:30–09:35 ET | 09:35:15 | 09:36:05 | 09:37:00 |
| AMC | Next eligible full session after a release proven public at/after the prior close | 09:30–09:35 ET | 09:35:15 | 09:36:05 | 09:37:00 |

An AMC snapshot binds `prior_eligible_session_close_at` as the preceding 16:00 ET
regular-session boundary. Its declared publication time must be at or after that bound and strictly
before the next reaction-session open; the contract does not infer an eligible prior close from a
calendar date.

The opening auction, non-regular conditions, corrections, extended hours, forward-filled observations, and post-cutoff dependencies are inadmissible. The start observation may be no more than 15 seconds late, the end observation no more than 15 seconds old, and issuer/SPY/sector endpoints no more than five seconds apart.

### Beta and deterministic confirmation

V1 fits the transparent two-factor model

```text
r_stock = alpha + beta_market * r_SPY + beta_sector * (r_sector - r_SPY) + error
```

using split-adjusted, price-only daily log returns for 252 sessions ending 21 sessions before the reaction session. It requires at least 200 aligned observations, no winsorization or forward fill, design condition number at most 30, `beta_market` in `[-1, 3]`, and `beta_sector` in `[-2, 2]`.

The same formula produces `market.opening_residual_log_return.v1` over the synchronized opening window. The confirmation epsilon is `0.0025` log return (25 basis points):

- LLM `UP` plus residual at least `+0.0025` is `CONTINUE`;
- LLM `DOWN` plus residual at most `-0.0025` is `CONTINUE`;
- a qualifying opposite residual is `REVERSE`;
- absolute residual below the threshold is `NONE`; and
- LLM `UNCERTAIN` is `NOT_APPLICABLE`.

Only `CONTINUE` preserves a directional result. The threshold and beta choices are owner-selected preregistration hypotheses, not observed evidence.

### Frozen earnings features

The exact 13 feature IDs are:

1. `earnings.eps_consensus_surprise_pct.v1`
2. `earnings.revenue_consensus_surprise_pct.v1`
3. `earnings.eps_timeseries_sue.v1`
4. `earnings.revenue_yoy_pct.v1`
5. `earnings.guidance_direction.v1`
6. `market.event_gap_residual.v1`
7. `market.opening_residual_log_return.v1`
8. `market.opening_relative_volume_20d.v1`
9. `market.opening_nbbo_spread_bps.v1`
10. `market.quote_age_ms.v1`
11. `market.realized_volatility_20d.v1`
12. `market.pre_event_residual_momentum_20d.v1`
13. `market.distance_from_opening_vwap_bps.v1`

Every feature record exists with `PRESENT`, `UNAVAILABLE`, `NOT_APPLICABLE`, or `CONFLICTING` status. Decimal values are canonical decimal strings. Every `PRESENT` feature and every `PRESENT` vector component must cite at least one `source_refs` evidence ID; blank provenance is rejected. Consensus features are optional only when explicitly unavailable; at least one frozen EPS surprise path is required. `NOT_GIVEN` is a valid guidance value, not an imputation. Required market features, dependencies, quote entitlement, numeric finiteness, and corporate-action state fail closed.

## 5. Macro SPY challenger

The macro candidate remains inactive unless earnings Gate B is formally infeasible and macro Gates B/C pass. It does not activate automatically and is not approved alpha.

The evidence-backed boundary is SPY-only, post-release, official BLS evidence, no invented consensus, and separate JOLTS/Employment clocks and reports. The exact windows and thresholds below are owner-selected V1 hypotheses.

| Cohort | Official event | Regular-session observation | Evidence freeze | Decision cutoff | Research entry deadline |
|---|---|---:|---:|---:|---:|
| BLS JOLTS | Exact BLS schedule, normally 10:00 ET | 10:00–10:15; anchor is median SPY SIP midpoint from 09:55–10:00 | 10:15:15 | 10:16:05 | 10:17:00 |
| BLS Employment Situation | Exact BLS schedule, normally 08:30 ET | 09:30–09:45 only; premarket price is forbidden | 09:45:15 | 09:46:05 | 09:47:00 |

The policy binds BLS JOLTS only to `BLS_JOLTS` and Employment Situation only to `BLS_EMPLOYMENT_SITUATION`. The official public time must be known and no more than 60 seconds from the retained frozen schedule. Exactly one `MACRO_PRIMARY` `OFFICIAL_BLS_RELEASE` citation must bind the declared event time through its publisher timestamp. A late/off-schedule release, a release-family mismatch, an unbound official timestamp, a missing required component or revision, a non-full session, or a stale/missing market window is retained with an exclusion reason.

JOLTS requires openings, hires, quits, layoffs/discharges, total separations, and published revisions. Employment requires nonfarm payrolls, unemployment rate, average hourly earnings, participation, and payroll revisions. Consensus is optional and never inferred.

For each cohort separately:

```text
r_event = log(P_end / P_anchor)
location = median(same-clock returns over prior 60 full non-event sessions)
scale = max(1.4826 * MAD, 0.0005)
z = (r_event - location) / scale
```

At least 45 normalization observations and 15 of 20 matching volume observations are required. Confirmation needs `abs(z) >= 1.0`, event-window volume ratio at least `1.25`, and the end midpoint on the matching side of event-window VWAP. The rule can only preserve or veto the LLM direction.

The macro feature set contains the exact cohort component records, optional consensus and revision vectors, plus SPY event return, robust z-score, volume ratio, VWAP distance, range, reversal, spread, quote age, and 20-day realized volatility. The canonical JSON contains their exact IDs, types, units, status rules, and definitions.

## 6. Reasoner contract and abstention

There is one provider-neutral call, no retry, no tools, temperature `0.0`, at most 512 output tokens, and an eight-second hard timeout. The decision binds policy, evidence, snapshot, route, prompt, schema, and model-configuration hashes.

The route, prompt-contract, and output-schema hashes are deterministically derived from the accepted
policy. The model-configuration hash binds provider, model, advertised revision, and decoding
parameters without choosing the provider in this issue. A response after the earlier of the
eight-second call deadline or decision cutoff is recorded as rejected `UNCERTAIN`.

| Candidate | Route SHA-256 | Prompt-contract SHA-256 | Output-schema SHA-256 |
|---|---|---|---|
| Earnings | `af801a9baf24cff5b1f093e3802834855e8b82d56491b7244bba59ba357b30e3` | `617897661b723c2315f3cb60fbb15b6e57dfc571098a4be4563b324cd6a0354f` | `08dd5302e8e03e01a7012acb59048329516e6a801f8b24827066f43430c04fa4` |
| Macro | `c2dd3668be1595f6658506f830ccad06b92b532c36732fff667f7f59ce641dd2` | `52f7b1c152128414363225aa441bf40e3b099ff045952891d9b2743bb3bccfec` | `08dd5302e8e03e01a7012acb59048329516e6a801f8b24827066f43430c04fa4` |

The reasoner returns exactly these fields, with no additional properties:

```json
{
  "decision": "UP | DOWN | UNCERTAIN",
  "evidence_ids": ["known-evidence-id"],
  "contradictions": [
    {
      "evidence_ids": ["known-id-a", "known-id-b"],
      "summary": "At most 400 Unicode code points."
    }
  ],
  "unknowns": ["FROZEN_UNKNOWN_CODE"],
  "strongest_falsifier": {
    "evidence_id": "known-evidence-id",
    "summary": "At most 400 Unicode code points."
  },
  "summary": "At most 800 Unicode code points."
}
```

Directional output requires a candidate-primary citation, a market-confirmation citation, and a non-null strongest falsifier. Unknown evidence IDs, forbidden/extra fields, duplicate codes, malformed JSON, provider failure, a late response, policy/hash drift, or a critical unknown yields deterministic `UNCERTAIN`.

The complete tolerated and critical unknown-code enums are frozen in the canonical policy rather than accepted as free text.

## 7. Baselines, partitions, and evidence gates

Every arm consumes the same event, snapshot, cutoff, target, latency, and denominator:

- cash/always uncertain;
- price continuation and price reversal;
- deterministic parser;
- bounded LLM;
- no-text ablation;
- opposite-LLM placebo; and
- 256 SHA-256-seeded, coverage-matched random placebos.

Placebos never have permit authority. Existing hand-authored `candidate_signal` fields remain supplied synthetic test inputs, not generated strategy output.

### Earnings partitions and floors

| Partition | Dates | Total minimum | BMO minimum | AMC minimum |
|---|---|---:|---:|---:|
| Development | 2020–2023 | 200 | 75 | 75 |
| Validation | 2024 | 100 | 40 | 40 |
| Untouched | 2025 through 2026-06-30 | 100 | 40 | 40 |
| Prospective | Strictly after policy freeze | 30 | 10 | 10 |

Validation and untouched partitions also require at least eight sectors and 50 issuers. BMO and AMC metrics are reported separately.

### Macro partitions and floors

| Partition | Dates | JOLTS minimum | Employment minimum |
|---|---|---:|---:|
| Development | 2016–2021 | 60 | 60 |
| Validation | 2022–2023 | 20 | 20 |
| Untouched | 2024 through 2026-06-30 | 20 | 20 |
| Prospective | Strictly after policy freeze | 6 | 6 |

Release families never pool to manufacture a result.

The existing 20–30 event Q-FAST screen remains a reject-only engineering gate. `NOT_REJECTED_SMALL_SAMPLE` is never an alpha claim. Promotion additionally requires at least 30% directional coverage per cohort, positive p95-latency after-cost `mean_all`, a non-negative 95% clustered-bootstrap lower bound, positive incremental value over price and parser controls, non-negative leave-one-group-out results, and performance above the 95th percentile of the 256 random arms. Macro must also preserve sign across the frozen neighboring-window and delayed-entry sensitivities.

Official costs remain `UNVERIFIED`, so after-cost metrics are presently `NOT_RUN`; no candidate can pass the promotion or PAPER-mutation gate yet.

## 8. Expression and exit boundary

V1 preregisters research grids without selecting a winner:

- earnings expression candidates: cash, shares, one long option, debit vertical;
- macro expression candidates: cash, SPY shares, one long SPY option, SPY debit vertical;
- earnings entry grid: 09:35, 09:45, 10:00, session close;
- earnings exit grid: same-session close, D+1, D+5, D+10, D+20 closes;
- JOLTS entry grid: 10:15, 10:20, 10:30;
- Employment entry grid: 09:45, 10:00;
- macro exit grid: 10:30, 11:00, 12:00, 15:30, rejecting exit-at-or-before-entry pairs.

Gate C chooses a directional clock/horizon only through the frozen chronological protocol. Gate D then compares expressions on the same events, direction, time, costs, and operational-loss budget. If nothing passes, the outcome is no expression and no trade.

The existing debit-vertical bridge is inert infrastructure, not a promoted package. Its quantity-one, $500 maximum-loss, 60-second permit TTL, and the demo's 60-minute hold are not V1 strategy constants. Issue #29 owns expression evidence, #30 owns risk/reservation, and #31 owns the eventual frozen lifecycle exit.

## 9. Claims and change control

This merge may claim only:

- a preregistered research policy;
- deterministic engineering evidence;
- `NOT_ALPHA_EVIDENCE`; and
- `NO_EXECUTION_AUTHORITY`.

Historical direction, expression economics, quote-side shadow PnL, Alpaca PAPER PnL, and broker-flat operational proof remain separate claims. Underlying returns are never option PnL; indicative data is never NBBO/executable evidence; a profitable PAPER lifecycle is never repeatable-alpha evidence.

The canonical JSON contains no self-hash. `policy.py` owns the immutable golden digest and authenticates the packaged resource before returning a deeply immutable view. Any semantic change requires a new policy identity and digest, review, and fresh untouched/prospective evidence. Production cannot train, retune, self-amend, rewrite prompts, promote an expression, or relax a gate.
