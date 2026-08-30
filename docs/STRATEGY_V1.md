# Esscher v1 Strategy Policy (frozen)

This document is the preregistered Esscher v1 strategy contract required by issue #26. It freezes the hypothesis, admissible information set, feature definitions, abstention rules, trade expression, exit, falsifiers, evidence thresholds, and claim boundary. The machine-readable frozen policy lives at [`configs/strategy_v1.json`](../configs/strategy_v1.json); its exact bytes carry SHA-256 `fb3eb4dc0e8898a6cea1ad159611623c8cad16143a6dc71ad4179c610a72ac10` and are parsed only by the strict contract in `src/ringdown_market/strategy/policy.py`. Any byte change after freeze is a policy-hash mismatch and fails closed.

Nothing in this policy can call a model, a broker, an MCP tool, or the network. The config is inert bytes; only deterministic code in this repository interprets it.

## 1. Hypothesis

Esscher v1 predicts the **residual underlying direction** of an issuer after a scheduled earnings event, over a bounded opening-window hold. It does not predict option prices, fill quality, or profitability.

```text
residual_return = r_stock - beta_market * r_market - beta_sector * r_sector
```

The strategy may emit only `UP`, `DOWN`, or `UNCERTAIN`. `UNCERTAIN` means no trade. Self-reported confidence never authorizes a trade. Prose never controls market arithmetic: `reaction_relation` is derived by deterministic code from the accepted residual direction and the frozen opening residual.

## 2. Universe and timing

- Universe: scheduled BMO/AMC earnings for supported US-listed, optionable common equities priced at least `$10.00`.
- Timing clock: `America/New_York`.
- Observation window: `09:30:00`–`09:35:00` of the reaction session.
- Decision cutoff rule: `OBSERVATION_WINDOW_END` (`09:35:00`).
- Valid signal by: `09:36:05`.
- No opening submission after: `09:37:00`.
- All packages close by `15:30:00` ET and before expiry.

## 3. Admissible information set

Permitted source kinds: `ISSUER_PRIMARY`, `SEC_OFFICIAL`, `OFFICIAL_MARKET_DATA`. Prohibited: competitor submissions, secondary summaries, unofficial aggregators, unsourced prose. Missing or conflicting facts remain explicit and make the snapshot ineligible; they are never imputed.

Timestamped primary earnings/guidance evidence follows the provenance contract in [point-in-time evidence gate](research/point-in-time-evidence-gate.md): typed `published_at`, publisher, precision, retrieval time, content hash, entitlement, and redistribution status.

### Feature definitions

Every feature names its point-in-time availability rule and permitted source classes. No other feature may enter the decision without a policy version change.

| Feature | Definition | Availability rule | Source classes |
| --- | --- | --- | --- |
| `earnings_numeric/v1` | Timestamped numeric earnings facts (revenue, EPS, segments) and guidance figures | `PUBLISHED_AT_OR_BEFORE_CUTOFF` | `ISSUER_PRIMARY`, `SEC_OFFICIAL` |
| `guidance_statement/v1` | Forward guidance statements and changes, explicit unknowns preserved | `PUBLISHED_AT_OR_BEFORE_CUTOFF` | `ISSUER_PRIMARY`, `SEC_OFFICIAL` |
| `opening_return/v1` | Log return of the underlying over the registered 09:30:00–09:35:00 opening window | `OBSERVED_WITHIN_REGISTERED_OPENING_WINDOW` | `OFFICIAL_MARKET_DATA` |
| `market_opening_return/v1` | Log return of the SPY proxy over the identical window | `OBSERVED_WITHIN_REGISTERED_OPENING_WINDOW` | `OFFICIAL_MARKET_DATA` |
| `sector_opening_return/v1` | Log return of the frozen sector proxy over the identical window | `OBSERVED_WITHIN_REGISTERED_OPENING_WINDOW` | `OFFICIAL_MARKET_DATA` |
| `market_beta/v1` | Underlying-to-market beta under the frozen beta policy | `FROZEN_BETA_POLICY_PRE_CUTOFF` | `OFFICIAL_MARKET_DATA` |
| `sector_beta/v1` | Underlying-to-sector beta under the frozen beta policy | `FROZEN_BETA_POLICY_PRE_CUTOFF` | `OFFICIAL_MARKET_DATA` |

### Frozen beta policy

- Version: `beta-freeze/v1`; market proxy `SPY`; sector proxy frozen per event sector.
- Estimation uses only data available at or before `feature_snapshot_at`.
- Post-event data is forbidden; re-estimation after observing outcomes is forbidden.
- Stock, market, and sector series use one adjusted-bar policy and synchronized timestamps.

## 4. Reasoner boundary

The reasoner may classify the residual direction and cite evidence. It cannot choose contracts, size, entries, or exits (`CONTRACT_SELECTION`, `SIZING`, `ENTRY`, `EXIT` are frozen prohibited authorities). There is exactly one route, bound by prompt hash, output-schema hash, and policy hash, with no transparent fallback. Invalid, late, canceled, incomplete, stale, conflicting, or unsupported output becomes `UNCERTAIN` and cannot reach execution.

## 5. Abstention rules

The policy abstains (`UNCERTAIN`, no trade) under exactly these stable codes. No fallback signal exists.

| Code | Condition |
| --- | --- |
| `MISSING_EVIDENCE` | A required earnings or guidance fact is absent from the admissible information set. |
| `CONFLICTING_EVIDENCE` | Independent admissible sources disagree and the source hierarchy cannot resolve the conflict. |
| `STALE_INPUT` | A snapshot, feature, or evidence timestamp is stale relative to the cutoff or registered window. |
| `CUTOFF_VIOLATION` | Any input carries information published or observed after the decision cutoff. |
| `INELIGIBLE_UNIVERSE` | The event fails listing, optionability, price floor, or scheduled BMO/AMC rules. |
| `INELIGIBLE_TIMING` | The event timing bucket or session calendar cannot be established safely. |
| `LATE_REASONER_OUTPUT` | The reasoner result arrives after `valid_signal_by`. |
| `INVALID_REASONER_OUTPUT` | The reasoner output is invalid, incomplete, canceled, or violates the frozen schema. |
| `UNBOUNDED_FALSIFIER` | A falsifier is not bounded by admissible evidence or contradicts the accepted direction unresolved. |
| `POLICY_HASH_MISMATCH` | The bound strategy policy hash differs from the frozen policy hash. |
| `SNAPSHOT_HASH_MISMATCH` | The strategy snapshot hash differs from the snapshot bytes consumed. |
| `ROUTE_MISMATCH` | The reasoner route identity differs from the single frozen route. |
| `DUPLICATE_DECISION` | A decision already exists for the same event and policy identity. |
| `NO_FALLBACK` | No route or replacement signal may substitute for a failed decision path. |

## 6. Expression and exit

- Expression: exactly one bounded debit vertical, quantity one. `UP` expresses a bull call vertical; `DOWN` expresses a bear put vertical. Expiry 7–21 calendar days at entry; widths from the frozen set `$2.50` or `$5.00` where available. Contract selection belongs exclusively to the deterministic option compiler (issue #29); the strategy never selects contracts.
- Exit: the hold lasts 60 minutes from the **reconciled opening fill**. There is no model exit, profit-take, or stop-loss in v1. Close is one atomic multi-leg package, completed by `15:30:00` ET and before expiry.
- Execution boundary: permanently `PAPER_ONLY` through the official Alpaca MCP boundary only; no real-money mode, no direct REST/CLI fallback.

## 7. Decision output contract

Strategy decisions serialize under schema `esscher.strategy_decision` (version 1) with deterministic canonical bytes. A decision carries:

- event identity (`event_id`, `issuer`, `ticker`) and the four clocks (`decision_cutoff`, `feature_snapshot_at`, `decided_at`, `decision_deadline`);
- `direction` (`UP`/`DOWN`/`UNCERTAIN`), `decision_state` (`APPROVED`/`ABSTAIN`), and stable `abstention_reasons`;
- `reaction_relation` (`CONTINUE`/`REVERSE`/`NONE`) computed by code;
- the frozen opening residual and evidence citations binding every accepted claim to input evidence, plus the strongest falsifier;
- lineage hashes: snapshot, policy version/hash, route, and reasoner output;
- the exact trace stages `INPUT -> FEATURE -> REASONER -> VALIDATOR -> OUTPUT`;
- claim boundary `NOT_ALPHA_EVIDENCE` with qualifiers `INDICATIVE_DATA`, `NOT_ALPHA_EVIDENCE`.

A strategy decision can never carry order, permit, account, contract, leg, strike, expiry, quantity, limit-price, or symbol fields. The parser rejects such fields as `EXECUTION_FIELD_FORBIDDEN`.

Hand-authored `candidate_signal` fixtures (for example `tests/contract_fixtures/frozen_research_decision_v1.json`) are supplied test inputs for the permit contract. They are not strategy output and cannot enter the production decision path.

## 8. Baselines and residual convention

Frozen no-LLM baselines, identical to `src/ringdown_market/alpha/baselines.py`: `always_abstain`, `gap_continue`, `gap_reverse`, `price_only`, `fundamental_rule`, `no_text_ablation`. Candidate and baselines share the same panel, timing, latency profile, and the common unit of signed residual log-return. Abstentions remain in the eligible-event denominator with zero signed return.

## 9. Evidence thresholds (preregistered)

- Confirmation panel: 20–30 untouched eligible events (issue #3), excluding the four contract-development events below.
- Q-FAST minimum events: 20. Reject reasons: `non_positive_mean`, `negative_median`, `loses_to_strongest_baseline`, `best_event_fragility`. Q-FAST remains reject-only; `NOT_REJECTED_SMALL_SAMPLE` is never promoted to proven alpha.
- Latency: the `p95` profile is required; zero-latency and p95 results are reported separately. Q-LATENCY failure forces `SHADOW_ONLY`.
- Historical confirmation (#3) and the prospective shadow ledger (#32) are both required before any PAPER mutation. If a threshold is unmet, the result is honestly reported as not met; no success language is inferred.

## 10. Event-set separation

Separated before any outcome is inspected:

- Development/tuning: `KR-2026Q2-EARNINGS`, `GIS-2027Q1-EARNINGS`, `MU-2026Q4-EARNINGS`, `NKE-2027Q1-EARNINGS` (the four P0 contract-development events). They are excluded from the confirmation panel.
- Confirmation: the untouched point-in-time panel built by issue #3.
- Prospective: post-freeze shadow events recorded by issue #32 only.

Events are never added or removed because a realized result helps the candidate.

## 11. Versioning and identity

- Policy schema: `esscher.strategy_policy` version 1.
- Policy version: `esscher-strategy-v1`; frozen at `2026-08-30T14:12:39Z`.
- Policy identity: SHA-256 of the exact config bytes, pinned in `src/ringdown_market/strategy/policy.py` as `STRATEGY_POLICY_V1_SHA256`.
- Decision schema: `esscher.strategy_decision` version 1.
- Strategy policy hash, snapshot hash, route hash, and reasoner output hash are bound through every decision and must reappear in any downstream package, reservation, permit, and trace for issue #33's end-to-end lineage.

## 12. Claim boundary

This policy supports engineering evidence only. It makes no alpha, profitability, or executable-fill claim. All outputs remain `NOT_ALPHA_EVIDENCE`; market observations remain `INDICATIVE_DATA`; execution remains `PAPER`. The local long-only equity-factor lab is experimental and is not part of this path.
