# Clean-room Quant Hackathon CTO Strategy Report

Research date: 30 August 2026
Scope: public sources plus the commissioning card only. No repository, project material, prior strategy, credentials, broker account, market order, or deployment was inspected or used. This is a design and due-diligence report, not trading advice or an implemented system.

## 1. Executive decision

Build one auditable paper-trading system around a liquid-US-equity event-reaction strategy. Treat options as a strictly gated satellite, not the foundation. Do not try to win by pretending a paper fill is a real fill, by downloading the entire options universe, or by competing at microsecond HFT latency.

The recommended core is:

- systematic ranking of liquid common stocks after timestamped, verifiable earnings/filing/news events;
- a short-horizon reaction/continuation signal using price, volume, volatility, sector/market context, and executable spread;
- a small diversified portfolio of the highest net-of-cost candidates;
- a broker-neutral execution port, initially implemented only against the contest-approved paper interface;
- immutable decision, order, fill, and reconciliation records; and
- two scoreboards: broker-paper PnL and conservative cost-adjusted PnL.

Why this is the CTO choice:

1. It can plausibly capture a short-hackathon opportunity without needing unavailable co-location or a huge historical alternatives dataset. Research does not make an event/earnings drift a free lunch: transaction costs materially reduce implementable PEAD profits, particularly in the less liquid names where headline returns can look strongest [14]. That is an argument for liquid names and explicit costs, not for discarding the family.
   A broad PEAD review documents the long research record and notes that the international evidence is not entirely conclusive [6].
2. Alpaca paper trading simulates fills from real-time quotes but explicitly does not model market impact, latency slippage, queue position, price improvement, dividends, or the available NBBO quantity [1]. A paper-only leaderboard therefore requires an internal capacity and cost model to remain credible.
3. Long options have the largest possible raw-PnL upside, but also amplify spread, volatility, expiry, and assignment complexity. The SEC notes that a standard contract generally represents 100 underlying shares and that option premium depends on price, time and volatility [5]. Use them only after their data and reconciliation gates pass.
4. Ultra-high-frequency return prediction is a different contest: NBER research finds value in millisecond timeliness and incoming-order-flow information [15]. A short build without the matching data, infrastructure, and broker routing should not fake that advantage.

This is deliberately aggressive about learning and disciplined about execution. It maximizes the probability of a defensible positive outcome rather than the maximum possible one-run paper PnL.

## 2. Immediate rule-reading gate — do this before writing the system

The competition rules are not included in this card. The following facts must be copied into a versioned `competition_contract.md` before strategy parameters are frozen:

1. Rank formula: final raw PnL, percentage return, Sharpe/Sortino, drawdown-adjusted score, daily consistency, or a judge assessment.
2. Start/end timestamp, mark-to-market time, whether unrealized PnL counts, overnight/weekend treatment, resets, and treatment of corporate actions.
3. Initial capital, margin/shorting limits, fractional shares, leverage, borrow/locate assumptions, PDT restrictions, and forbidden asset classes.
4. Broker and account type; whether equities, ETFs, options, spreads, and short positions are actually enabled in that paper environment.
5. Exact market-data entitlement and the source used to mark fills/PnL. A Paper Only Alpaca account is entitled to IEX data only [1]; that may not match a paid contest account or a separate research feed.
6. Allowed data vendors, news sources, LLMs, external APIs, automation, team members, and manual intervention.
7. Fees, commissions, exchange/regulatory charges, currency treatment, tax assumptions, and any paper-only fill rules.
8. Submission requirements: source/data disclosure, logs, reproducibility, order blotter, demo, and whether judges can inspect code.
9. Whether a strategy may hold options through expiry or exercise/assignment, and whether a multi-leg order is legal.
10. Failure policy for outages, rate limits, delayed data, an order submitted just before the cutoff, and duplicate/restarted workers.

Decision tree after the read:

- Raw-PnL-only, permissive paper rules: concentrate the validated equity core in the top three to five independent candidates. Do not relax the data, duplicate-order, or daily-loss controls.
- Return plus drawdown/Sharpe: hold six to eight lower-correlated candidates, cap net beta, and make the options satellite unavailable unless it improves the stress-cost simulation.
- No shorting/options or uncertain entitlement: run long-only liquid equities, keep the unallocated leg as cash, and use ETF market context only as a feature/hedge where rules permit.
- Options permitted and marks are based on credible option NBBO: run the satellite only after all option-specific gates below are green.
- Any rule ambiguity that changes legal exposure or score: stay in shadow mode and ask the organizer; do not infer a favorable interpretation.

## 3. Ranked strategy families

| Rank | Family | Maximum raw-PnL potential | Robust expected-value posture | Required edge and evidence | Decision |
|---|---|---:|---|---|---|
| 1 | Liquid equity event reaction / short continuation | High | Best of the candidates | Timestamped event plus an initially confirmed, liquid price reaction; selection is based on net-of-cost rank, not a hand-picked ticker | Build as the core |
| 2 | Cross-sectional intraday momentum/reversal with liquidity-regime gate | High | Medium | Needs matching trades/quotes, a realistic arrival-lag simulator, and walk-forward stability; do not conflate it with microsecond HFT [15] | Research as fallback/overlay only |
| 3 | Long-option or debit-spread event convexity | Very high, with bounded long-premium loss | Low until cost- and data-validated | Accurate contract chain, NBBO, multiplier, implied-volatility/expiry controls, and option lifecycle reconciliation [4][5] | Gated satellite only |
| 4 | LLM-led news/sentiment trading | Attractive demo value, uncertain PnL | Low | Text provenance, latency, stable structured extraction, and non-leaky validation | Never put an LLM in the order-authority path |

The core should not be a generic “news sentiment bot.” It should be an event-conditioned systematic process. The event determines when to inspect a symbol; observed market reaction and liquidity determine whether to trade it. This avoids relying on a model to infer a direction from untrusted free text alone.

The fallback family is deliberately conditional: a high-frequency event-study finds post-news drift over early days in its historical sample, while also noting that widely reported anomalies can weaken after publication. That supports a liquidity/regime gate and fresh cost validation, not an unconditional return factor or a performance promise [13].

## 4. Recommended core: liquid equity event-reaction portfolio

### 4.1 Universe and candidate eligibility

Start with US common stocks. ETFs may be used for market/sector context or a permitted hedge, not as arbitrary substitute alpha. Initial thresholds are deliberately conservative starting defaults, not claimed optimizations:

- price at the decision timestamp: at least $10;
- 20-trading-day median dollar ADV: at least $25m;
- median regular-session NBBO spread: no more than 15 bps;
- symbol status: normal, mapped, and not halted;
- no known split, dividend/ex-date, merger, or symbol-change ambiguity inside the proposed holding window;
- event timestamp and source must be persisted before the signal can be calculated;
- the candidate must have both a current quote and sufficient pre-event observations to calculate the features.

The system stores `source_ts`, `received_ts`, `as_of_ts`, data-feed identity, symbol mapping version, quote age, and any source sequence/event ID. If any mandatory field is absent, the candidate is rejected, not filled with a default.

### 4.2 Event sources and signal inputs

Approved input layers, subject to the competition contract:

- SEC EDGAR submissions/XBRL for public filing facts and durable timestamps. The SEC says the public JSON APIs are unauthenticated, are updated as filings disseminate, and provide submissions and XBRL data [7]. EDGAR is useful for facts and provenance, not a claim that it alone is an earnings-calendar feed.
- A licensed, timestamped earnings/event feed if rules permit. If no reliable schedule source is available, do not fabricate an earnings surprise; trade only event reactions that the permitted live source delivered.
- Alpaca’s real-time news stream can provide headline, article/source, symbol list, `created_at`, and `updated_at` fields [11]. Store the original payload and exact source identifier; do not trust an article’s wording as an instruction to the system.
- Price, quote, and volume data from a feed whose entitlement is verified. Databento US Equities offers live/historical US-equity venue data, including top-of-book and deeper schemas, while its catalog identifies the specific source coverage [12]. The actual deployment feed must be compared to the contest/broker mark source before it is allowed to make entry decisions.

Feature set for each candidate at a fixed decision time:

```text
event_type, event_source, event_timestamp_confidence,
return_1m/5m/15m, signed_gap, realized_volatility,
relative_volume, spread_bps, quote_age, quote_size,
market_return, sector_return, prior-day return,
optional licensed earnings-surprise feature,
market-status/halt flag, and data-quality flags.
```

### 4.3 Direction, sizing, and exits

Use a transparent score first. A suitable initial form is:

```text
net_score = reaction_strength
          + relative_volume_confirmation
          + sector/market-neutral residual
          - spread_and_expected_impact_penalty
          - event_timestamp_uncertainty_penalty
          - volatility_and_correlation_penalty
```

A calibrated LightGBM ranker may replace the hand-weighted score only after it beats the baseline in genuinely walk-forward, cost-adjusted tests. The model predicts a rank, not certainty and not a magic return. Its input schema, training window, feature code hash, hyperparameters, and output score are saved beside every decision.

Initial trading rules:

- Wait for an explicit reaction-confirmation interval rather than trading the first print. Start with 10 minutes after a valid event/reaction observation; vary this only through pre-registered sensitivity tests.
- Enter only if estimated edge exceeds the round-trip base cost plus a safety margin. An appealing gross signal that does not clear this gate is no trade.
- Use marketable limit orders for equity entry/exit; do not require undocumented broker stop-order support. Public Alpaca option documentation and current OpenAPI descriptions differ on the exact supported option order types [2][3], reinforcing the need for an adapter capability test rather than an assumption.
- Hold until the earliest of: profit target, volatility-scaled stop, time stop, invalidated data/risk condition, or pre-registered session/overnight cutoff. Start with a short intraday-to-next-session horizon; then validate multi-day continuation separately.
- Use six to eight top candidates for a risk-adjusted contest, or three to five only when raw PnL is demonstrably the score and correlations remain below the configured ceiling.
- No manual direction override after the daily universe and parameters are frozen. A human can trigger a documented emergency kill, not select a favorite ticker.

### 4.4 Options satellite — enable only under hard gates

Alpaca documents paper options as enabled by default and offers contract lookup plus real-time/historical option data [2]. Databento’s OPRA.PILLAR catalog describes consolidated last sale and national BBO across US equity-options venues, but warns that full highest-granularity coverage can be several terabytes per day [4]. Therefore:

- subscribe/query only the shortlisted underlyings and nearby contracts; never ingest “all options” for a hackathon;
- use long calls/puts or defined-risk debit spreads only; never sell naked options;
- require expiry at least 10 calendar days away unless a separate expiry experiment passes; do not use 0DTE as a shortcut to high PnL;
- require a fresh NBBO, minimum quote-size threshold, spread-to-mid threshold, contract multiplier, and fee schedule in the decision record;
- mark an open long option at bid and a short option at ask; do not use mid-price as realized PnL;
- force flat before the pre-registered expiry boundary. Alpaca says option assignments are not delivered through the WebSocket and need REST activity polling [2]; expiry/assignment is not a tolerable hidden state in a short contest;
- enable only when its cost-stress simulator beats the equity-only core on a rule-appropriate metric.

The SEC warning matters operationally: option premium, time and volatility are material, and long premium can be lost entirely [5]. “Options allowed” is not evidence that options improve expected value.

## 5. Technical architecture

### 5.1 Design principle

One small, observable system beats an impressive distributed diagram. For a short contest, keep all trade-critical processes in one container group with an append-only durable ledger and an independently restartable reconciler. No strategy service may possess an unbounded direct broker client.

The market-data stream itself can reject an unauthorised feed, exceed connection/subscription limits, or disconnect a slow client; track those states explicitly instead of treating a connected socket as healthy [9].

```text
Allowed event/data sources
  ├─ Databento equities / optional OPRA
  ├─ SEC EDGAR filings
  ├─ Permitted live-news stream
  └─ Contest broker market/trade streams
              │
              v
  Normalizer + clock/quality guard ──> immutable raw Parquet snapshots
              │                                  │
              v                                  v
  Feature builder + baseline/ranker ──> DuckDB research/replay views
              │
              v
  Risk engine / position allocator
              │  (creates immutable OrderIntent, never an order directly)
              v
  BrokerPort: AlpacaPaperAdapter or contest-specific adapter
              │
              ├─ submission result / REST snapshots
              └─ trade_updates stream
                         │
                         v
  Order state machine + double-entry position/PnL ledger
                         │
                         v
  Reconciliation service + circuit breaker + proof exporter
                         │
                         v
  OpenTelemetry metrics/traces/logs -> Collector -> Prometheus/Grafana
```

OpenTelemetry’s Python documentation recommends a Collector for production telemetry and supports OTLP export to systems such as Prometheus and Jaeger [10]. That is sufficient observability plumbing; it is not a reason to add a large platform.

### 5.2 Exact components and ownership

| Component | Product / implementation | Owner | Non-negotiable behavior |
|---|---|---|---|
| Research store | Parquet + PyArrow, queried through DuckDB | Research/data engineer | Raw snapshots are immutable and have a manifest hash |
| Operational ledger | PostgreSQL tables for intents, broker events, fills, positions, reconciliation breaks | Execution engineer | Append-only records; corrections are compensating events |
| Feature/model service | Python, Polars, scikit-learn baseline; LightGBM only after baseline gate | Research/data engineer | Every prediction carries feature/model/config hashes |
| Backtest/replay | Custom discrete-event simulator plus deterministic fixtures | Research/data engineer | Uses only information available at each `as_of_ts` |
| Execution | Python `asyncio`, typed `BrokerPort`, one contest-paper adapter | Execution engineer | Idempotent client order IDs; no credential in log/config/artifact |
| Reconciliation/risk | Separate process with read-only broker state plus kill/freeze switch | Risk/proof engineer | A discrepancy freezes new entries immediately |
| Deployment | Docker Compose on one approved Linux runner with restart policy | Execution engineer | One release manifest, pinned dependencies, health endpoint |
| Observability | OpenTelemetry Collector + Prometheus/Grafana | Risk/proof engineer | Emits data freshness, order lifecycle, reconciliation, and PnL metrics |
| LLM use | Offline, schema-constrained metadata classifier/explainer only | Research/data engineer | No broker permission, no autonomous order authority, prompt/output persisted |

The LLM lane is deliberately narrow. It may normalize an allowed article into an enum such as `earnings`, `guidance`, `M&A`, `halt`, `other`, with a confidence and model/prompt hash. The risk engine accepts only a fixed typed field after deterministic validation; it never accepts free text, an LLM trade recommendation, or a tool call. This also removes an entire class of data-feed prompt-injection and nondeterminism risk.

### 5.3 Data contracts and storage schema

Minimum durable tables:

```text
market_event(event_id, source, source_ts, received_ts, symbol, raw_payload_hash, quality)
quote_snapshot(quote_id, source, source_ts, received_ts, symbol, bid, ask, bid_size, ask_size, condition)
feature_snapshot(feature_id, as_of_ts, symbol, schema_hash, values_hash, model_input_json)
signal(signal_id, feature_id, model_hash, config_hash, direction, rank, expected_edge, cost_estimate)
order_intent(intent_id, signal_id, client_order_id, side, qty, limit, risk_decision, created_ts)
broker_event(event_id, broker_order_id, client_order_id, event_type, broker_ts, received_ts, raw_payload_hash)
fill(fill_id, broker_order_id, execution_id, qty, price, broker_ts, received_ts)
position_snapshot(snapshot_id, broker_ts, ledger_qty, broker_qty, ledger_cash, broker_cash, status)
recon_break(break_id, detected_ts, invariant, expected, observed, disposition)
```

Persist raw events in partitioned Parquet by date/source/symbol bucket; keep the ordered operational state in PostgreSQL. This makes a jury replay possible without pretending that a notebook output is a trade ledger.

## 6. Backtesting, replay, and anti-leakage protocol

### 6.1 Research sequence

1. Build a point-in-time event table. Do not use a revised filing, revised calendar time, or later symbol mapping as if it were known at the trade timestamp.
2. Form the universe using only previous-day liquidity statistics. Keep delisted/inactive names where historical source licensing permits; do not let today’s universe select history.
3. Use chronological walk-forward folds with embargo windows around event clusters. Train on the past, validate on a later block, and hold the final period untouched until the rule choice is frozen.
4. Compare the transparent score, a simple linear/logistic ranking baseline, and a constrained LightGBM ranker. If the complex model does not win after costs in the same out-of-sample windows, keep the baseline.
5. Run parameter sensitivity across event wait, holding time, universe threshold, cost multiplier, and portfolio count. Reject a result that depends on one precise setting or one calendar week.
6. Replay every planned live decision from the raw data snapshot and verify that feature, signal, intent, and order are byte-for-byte reproducible from the manifest.

### 6.2 Event-driven simulator

The simulator must model order arrival after the decision, not a fill at the contemporaneous mid:

```text
arrival_ts = decision_ts + sampled_or_configured_latency
buy fill  = observed eligible ask + fees + impact
sell fill = observed eligible bid - fees - impact
fill qty  = min(requested_qty, configured participation cap × observed eligible volume)
```

Use base and stress scenarios:

- Base: actual bid/ask at valid arrival, half-spread/liquidity penalty consistent with order style, known fees, and measured quote age.
- Stress: 1.5x observed spread, doubled impact coefficient, P95 decision-to-arrival lag, plus adverse 1-minute move where applicable.
- Catastrophe: source disconnect before exit, single price-gap beyond the intended stop, partial fills, duplicate stream delivery, and delayed broker acknowledgement.

The basic impact term may be:

```text
impact = c × mid × sigma_5m × sqrt(order_notional / max(eligible_5m_dollar_volume, 1))
```

Calibrate `c` only from data that pre-dates the evaluated window. Store its value in the run manifest. Apply broker/contest fees separately. The peer-reviewed PEAD study reports that transaction costs significantly reduce strategy profits [14]; the simulator should therefore be allowed to veto an attractive gross signal.

### 6.3 Required proof gates before any paper order

- No look-ahead assertion: every source timestamp precedes every feature and decision timestamp.
- Cost assertion: the selected strategy remains positive in the base scenario and is not catastrophically dependent on midpoint/Paper fills in stress.
- Stability assertion: positive or acceptably flat results across at least three chronological validation blocks; no single symbol, event day, or parameter setting supplies most reported PnL.
- Capacity assertion: every simulated order passes the same participation/spread cap used later.
- Operational assertion: 20 representative replays reproduce identical intents and PnL from immutable inputs.
- Failure assertion: stale data, duplicated events, out-of-order events, rate-limit failure, and broker rejection tests fail closed.

A green in-sample chart, a single good trade, or a broker-paper fill is not a gate.

## 7. Paper-trading reconciliation and transaction-cost discipline

### 7.1 Order lifecycle

Use a monotonic state machine keyed by `client_order_id` and broker order ID:

```text
DRAFT -> RISK_APPROVED -> SUBMITTED -> ACKNOWLEDGED
     -> ACCEPTED -> PARTIALLY_FILLED* -> FILLED
                           └-> CANCEL_PENDING -> CANCELED
     -> REJECTED | EXPIRED | REPLACE_REJECTED | CANCEL_REJECTED
```

Alpaca’s `trade_updates` stream exposes fills, partial fills, cancellations, rejections and other lifecycle events [8]. On reconnect, fetch broker orders, activities, positions and account snapshot; deduplicate by event/execution ID; replay the state machine; then reconcile. A WebSocket message is evidence of an event, not permission to skip REST reconciliation.

Reconciliation cadence: on process start, after every reconnect, every 30 seconds during the contest, after each final state, and at the official PnL cutoff. A break means new entries stop immediately.

Invariants:

```text
ledger position quantity == broker position quantity
sum(fill quantities) == broker filled quantity
no finalized broker order lacks an OrderIntent
cash/realized PnL movements reconcile to broker activity within declared paper limitations
no open order has an unknown client_order_id
all PnL marks state their price source and timestamp
```

### 7.2 Cost and marking rules

Maintain these columns per candidate and per portfolio:

```text
paper_gross_pnl
paper_net_pnl
conservative_mark_to_exit_pnl
base_cost_adjusted_pnl
stress_cost_adjusted_pnl
unrealized_bid_ask_mark_pnl
```

- Long positions are marked at bid; shorts at ask. Long options are marked at bid; short options at ask.
- Actual paper fills are recorded verbatim, but are never silently substituted for a conservative execution model.
- Add spread, fee, latency, and impact costs on entry and exit. Do not count price improvement unless the official broker record shows it.
- Exclude or separately account for dividends, borrow fees, corporate-action effects, and option exercise/assignment if the paper environment does not simulate them. Alpaca explicitly lists major paper/live differences, including no dividend simulation and no NBBO-quantity check [1].
- If broker-paper PnL is positive but stress-cost PnL is negative, publish both and classify the trade as paper-dependent, not validated alpha.

### 7.3 Circuit breakers

Starting defaults, to be confirmed by the competition contract and stored as configuration:

- per-name gross exposure: at most 12.5% of NAV;
- sector gross exposure: at most 30% of NAV;
- gross exposure: at most 100% of NAV unless contest margin explicitly allows more;
- risk budget per new trade: at most 0.75% of NAV under the defined adverse move;
- daily realized-plus-conservative-unrealized loss: -3% of NAV freezes new entries;
- peak-to-trough contest drawdown: -6% of NAV requires a documented operator review before any new entry;
- stale quote, missing source sequence, halt, or reconciliation discrepancy: no new order;
- manual kill: cancel known open entries and prevent fresh risk; exit handling follows the pre-approved broker capability and data-health policy.

These are competition defaults, not financial recommendations. Tighten them if the rules reward risk-adjusted performance; do not loosen them after a losing trade.

## 8. Team operating process

A capable small team has three clear lanes:

1. Research/data lead: source contracts, point-in-time data, baseline/ranker, simulation, and model evidence.
2. Execution lead: broker adapter, idempotency, WebSocket/REST recovery, deployment, and health behavior.
3. Risk/proof lead: configuration freeze, independent reconciliation, cost scoreboards, logs, jury pack, and emergency control.

Process rules:

- Parameter changes require a short record: rationale, before/after value, expected effect, simulation receipt, approver, and effective timestamp. No silent intraday tuning.
- The risk/proof lead does not author the signal model; the signal author does not approve their own reconciliation exception.
- One person owns each hotspot at a time. A change to order state, PnL marking, or risk limits requires a second reader before it reaches paper mode.
- Hold a 15-minute pre-open readiness check, two intraday 5-minute health checks, and a post-close reconciliation/proof check. This is operations, not status theatre.
- Any source, entitlement, broker, or scoring mismatch becomes a documented “shadow only” state, not a workaround.

## 9. Hour-by-hour short-hackathon build plan (32 hours)

This is a critical path, not evidence that the work has already occurred. Parallelize the distinct lanes only after the hour-1 rules gate.

| Hour | Owner | Deliverable / hard gate |
|---:|---|---|
| 0 | CTO + all | Copy rules into `competition_contract.md`; record unknowns and prohibited actions |
| 1 | Risk/proof | Decide raw-PnL versus risk-adjusted deployment branch; freeze initial capital and asset permissions |
| 2 | Data | Verify data entitlements, mark source, licensing, timestamps, and sample payloads; no secrets in artifacts |
| 3 | Execution | Write typed broker/data contracts and event schemas; create deterministic fixtures |
| 4 | Data | Build the liquid point-in-time universe and source manifest |
| 5 | Data | Ingest a small historical/event sample into Parquet and verify timestamps/symbol mapping |
| 6 | Research | Implement benchmark portfolios and transparent event-reaction score |
| 7 | Research | Implement cost model v1 and bid/ask marks; reject midpoint shortcut |
| 8 | Research | Build event-driven replay with configurable arrival latency |
| 9 | Research | Run first chronological walk-forward and inspect failures, not only returns |
| 10 | Data | Add data-quality, staleness, halt, and corporate-action rejection tests |
| 11 | Execution | Implement `OrderIntent` ledger and idempotent client-order-ID generator |
| 12 | Execution | Implement broker adapter against mock responses; test duplicate/late/rejected events |
| 13 | Risk/proof | Implement order-state machine and invariant checks |
| 14 | Execution | Add trade-update listener plus reconnect/replay path |
| 15 | Risk/proof | Add REST snapshot reconciliation interface and circuit-breaker behavior |
| 16 | Observability | Emit quote age, stream disconnects, decisions, order states, risk, reconciliation and both PnLs |
| 17 | Research | Run base/stress/catastrophe scenario grid; document parameter sensitivity |
| 18 | CTO | Strategy checkpoint: keep baseline, gate complex model, or abandon family on evidence |
| 19 | Research | If justified, train constrained ranker and compare against baseline only on held-out periods |
| 20 | Risk/proof | Replay 20 sampled decisions from raw manifest; require identical outputs |
| 21 | Execution | Containerize, pin dependencies, and run clean start/restart/recovery smoke test |
| 22 | All | Independent local review of data leakage, duplicate-order, cost, and secret/logging risks |
| 23 | CTO | Freeze v1 strategy/config; create release manifest and known-limitations note |
| 24 | Execution + risk | Connect only to authorized paper endpoint, run read-only health check, then shadow decision flow |
| 25 | Data | Compare research feed versus broker/contest mark feed; resolve or remain shadow-only |
| 26 | Risk/proof | Validate reconciliation zero-break condition under mock/authorized paper test events |
| 27 | CTO | Go/no-go: paper execution requires all proof gates, otherwise continue shadow replay |
| 28 | Operations | First controlled paper window with exposure caps; record every candidate, including rejects |
| 29 | Operations | Intraday health review; no parameter edits unless a documented safety exception is needed |
| 30 | Risk/proof | Cutoff reconciliation, PnL waterfall, and data/order-log export |
| 31 | CTO + all | Assemble judge pack; state result, limitations, and what did not work honestly |

If the contest is shorter, remove the complex model and options satellite first, not the ledger, cost model, or reconciliation process.

## 10. Transparency and proof package for judges

Deliver a compact, reproducible package:

1. Strategy specification: universe, signal, parameters, risk rules, intended holding period, prohibited manual actions, and exact score branch selected from the competition contract.
2. Environment manifest: source code revision, dependency lock/hash, container image, configuration hash, model hash, and UTC clock policy.
3. Data manifest: source URLs/vendors, entitlement note, pull timestamp, schema version, raw payload/Parquet checksums, and any unavailable/rejected data.
4. Candidate ledger: every eligible candidate, score, rejection reason, selected rank, and decision time. This proves the team did not show only winners.
5. Order blotter: intent, broker response, lifecycle events, fills, cancel/reject/reconnect events, and broker IDs redacted only where necessary.
6. Reconciliation receipt: cutoff snapshot, invariant results, any break and its resolution, plus broker-paper versus internal position/cash comparison.
7. PnL waterfall: gross paper PnL, broker fees, base cost-adjusted PnL, stress cost-adjusted PnL, and bid/ask-marked unrealized PnL.
8. Validation packet: walk-forward plots/tables, benchmark comparison, parameter sensitivity, failure injections, and the 20-decision deterministic replay receipt.
9. Limitations page: paper-fill disparities, feed mismatch risks, non-modeled costs, options constraints, and the fact that no simulation guarantees future or real-money performance.
10. A five-minute demo: data event -> feature snapshot -> signal -> risk decision -> order intent -> broker event -> reconciliation -> proof artifact. Include one rejected candidate and one failure-mode test.

The clearest competitive advantage is not a vague claim of “AI alpha.” It is a fast, transparent system that can show exactly why it traded, what it really paid under conservative assumptions, and that its paper account agrees with its own ledger.

## 11. Material risks and escalation triggers

- Feed/mark mismatch: research prices, paper fills, and contest marking can differ. Escalate before placing orders; do not compare incommensurable PnL.
- Paper simulation distortion: Alpaca’s documented assumptions can overstate capacity/fills [1]. Preserve the stress scoreboard and participation caps even if they depress leaderboard PnL.
- Options lifecycle: data scale, stale chains, spreads, contract multipliers, early exercise/assignment, and API-state gaps make the satellite opt-in only [2][4][5].
- Model overfit: model complexity is not an edge if it loses to a simple baseline or only wins in one regime. Keep baseline by default.
- Operational recovery: a restarted worker must discover state, not submit a duplicate intent. A reconciliation break is a freeze, not an alert to ignore.
- Rule ambiguity: contest terms override this report. Any uncertain action with scoring, legal, or account implications is paused for organizer clarification.

## 12. Source register

Sources:
All URLs were independently located in public documentation/research during this clean-room task. “Accessed” is 30 August 2026. Source dates are supplied where visible; “undated” means no publication/update date was visible in the retrieved page. Citation IDs preserve the research ledger.

[1] Alpaca, “Paper Trading.” Updated 7 July 2026. https://docs.alpaca.markets/docs/paper-trading

[2] Alpaca, “Options Trading.” Updated 2 April 2026. https://docs.alpaca.markets/us/docs/options-trading

[3] Alpaca, “Replace Order by ID” OpenAPI reference. Undated. https://docs.alpaca.markets/us/reference/patchorderbyorderid-1

[4] Databento, “OPRA.PILLAR: Real-time and historical equity options price data API.” Undated. https://databento.com/catalog/opra/OPRA.PILLAR

[5] U.S. Securities and Exchange Commission, “Investor Bulletin: An Introduction to Options.” Undated in retrieved page. https://www.sec.gov/oiea/investor-alerts-bulletins/ib_introductionoptions

[6] ScienceDirect, “A Review of the Post-Earnings-Announcement Drift.” Undated in retrieved public result. https://www.sciencedirect.com/science/article/pii/S2214635020303750

[7] U.S. Securities and Exchange Commission, “EDGAR Application Programming Interfaces (APIs).” Last reviewed/updated 8 April 2025. https://sec.gov/edgar/sec-api-documentation

[8] Alpaca, “Websocket Streaming.” Page stated “Updated 11 months ago” at retrieval; no absolute date visible. https://docs.alpaca.markets/us/docs/websocket-streaming

[9] Alpaca, “WebSocket Stream.” Page stated “Updated about 7 hours ago” at retrieval; no absolute date visible. https://docs.alpaca.markets/us/docs/streaming-market-data

[10] OpenTelemetry, “Exporters.” Last modified 14 January 2026. https://opentelemetry.io/docs/languages/python/exporters/

[11] Alpaca, “Real-time News.” Page stated “Updated 11 months ago” at retrieval; no absolute date visible. https://docs.alpaca.markets/us/docs/streaming-real-time-news

[12] Databento, “Databento US Equities.” Undated. https://databento.com/catalog/us-equities

[13] ScienceDirect, “Pervasive Underreaction: Evidence from High-Frequency Data.” Undated in retrieved public result. https://www.sciencedirect.com/science/article/pii/S0304405X21001306

[14] SSRN record, “Implications of Transaction Costs for the Post-Earnings-Announcement Drift.” Journal of Accounting Research, Vol. 46 No. 3, June 2008; SSRN page last revised 18 April 2013. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1024185

[15] Aït-Sahalia, Fan, Xue & Zhou, “How and When are High-Frequency Stock Returns Predictable?”, NBER Working Paper 30366, August 2022. https://nber.org/papers/w30366

## 13. Handoff

Decision: Build the event-reaction equity core first; options and complex models are conditional additions, not minimum scope.

Implemented in this task: research and a clean-room CTO report only. No broker connection, credentials, orders, code deployment, repository mutation, or claim of live/tested performance occurred.

Next gate: obtain the actual competition contract, then run the hour-0 to hour-2 rules/entitlement/mark-source audit before authorizing any implementation lane.
