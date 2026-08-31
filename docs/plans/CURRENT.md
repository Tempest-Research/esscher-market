# Esscher end-state plan

**Status:** current team plan and decision boundary
**Updated:** 2026-08-30
**Repository basis:** `Tempest-Research/esscher-market` at `8e4b335e963511d9d333b6a4a4569c86aabe1125`
**Execution boundary:** Alpaca PAPER only

Read this file before the dated drafts, research memos, reviews, or issue-plan snapshot in this directory. Those documents preserve useful alternatives and evidence, but they do not independently authorize implementation choices.

## 1. Outcome

Build Esscher as an autonomous quantitative research and PAPER-trading system that:

1. constructs a point-in-time eligible universe before outcomes;
2. ingests permitted issuer, filing, news, market, sector, fundamental, and option evidence;
3. computes reproducible quantitative features and historical comparisons;
4. uses an LLM as the research and directional-decision brain;
5. emits exactly `UP`, `DOWN`, or `UNCERTAIN` for a candidate event;
6. converts an accepted direction into a deterministic, risk-bounded trade or an explicit rejection;
7. executes, monitors, closes, and reconciles the position through Alpaca PAPER;
8. retains every candidate, abstention, rejection, order, fill, position, result, and limitation in an attributable ledger; and
9. evaluates both strategy quality and implementation truth without presenting a single profitable run as proof of alpha.

This is the complete intended system, not a reduced product stage. The work is still delivered in dependency order because data, strategy, risk, and execution have real causal dependencies. Contract and release identifiers remain necessary for replay and auditability; they do not represent successive product scopes.

## 2. What is fixed

The following boundaries survive all current plan variants:

- Esscher is permanently PAPER-only.
- The system operates unattended inside a frozen policy and fails closed.
- Strategy proposes. Risk approves. Execution submits. Reconciliation verifies.
- The LLM cannot possess broker credentials or unrestricted order tools.
- Deterministic code owns timestamps, arithmetic, schemas, universe eligibility, data-health checks, trade construction, risk budgets, order idempotency, lifecycle transitions, exits, and reconciliation.
- Missing, stale, conflicting, unsupported, late, or malformed evidence produces abstention or rejection rather than an inferred value.
- All historical and prospective evaluation keeps abstentions and failures in the denominator.
- Underlying-direction evidence, option-expression economics, PAPER fills, and profitability claims are separate claims.
- Production cannot train, retune, rewrite prompts, change thresholds, or promote its own research policy.
- Demo and submission surfaces are proof outputs. They are not substitutes for a strategy or a working PAPER lifecycle.

## 3. Accepted strategy decision

Ben accepted the recommendation in [`reviews/2026-08-30-independent-quant-firm-synthesis.md`](reviews/2026-08-30-independent-quant-firm-synthesis.md) on 30 August 2026. The following decisions now govern implementation.

### 3.1 Primary strategy and challenger

- The primary research candidate is **systematic post-earnings residual continuation in liquid U.S. common stocks**. BMO and AMC events remain distinct cohorts with separate observation clocks, targets, and reporting.
- Discretionary ticker insertion is prohibited. Eligibility, rejection, ranking, and abstention come from a frozen point-in-time universe and deterministic policy. A human may veto corrupt data or invoke a kill switch, but may not insert a preferred ticker after observing an outcome.
- **Scheduled macro-release SPY continuation** remains a named challenger and operational proving lane, not approved alpha. It may replace earnings only if the earnings data contract is infeasible, a preregistered after-cost underlying study is positive, the LLM beats price-only and deterministic-parser baselines, and the result survives chronological holdout or prospective evidence.

### 3.2 Direction before expression

- Validate the directional policy first on signed underlying returns before attributing value to an option structure. This research control measures the economic direction; it does not authorize an executable uncovered short-share position.
- Compare cash/no trade, a bounded eligible share expression, one long option, and a defined-risk debit vertical on the same eligible events, decision timestamps, exit clock, and frozen operational-loss budget. If no bounded share expression exists for a direction, retain the signed underlying series as a research baseline and emit `NO_PACKAGE` for share execution.
- Promote an option expression only if legitimate quote data, current package geometry, liquidity, lifecycle controls, and after-cost evidence justify the extra model. A competition-required option fill proves eligibility and operation, not superior expectancy.

### 3.3 LLM authority

The LLM emits `UP`, `DOWN`, or `UNCERTAIN` from one immutable evidence packet. A deterministic market-confirmation rule may veto or reject `UP` or `DOWN`; it may not convert `UNCERTAIN` into a trade. Deterministic code retains universe, timestamp, arithmetic, data-health, expression, quantity, risk, permit, lifecycle, exit, and reconciliation authority.

### 3.4 System-wide evidence controls

Every strategy lane must use:

- a hash-linked **Trade Passport** covering candidates, abstentions, rejections, decisions, reservations, orders, fills, closes, and reconciliation;
- explicit evidence modes—live paper, recorded paper, historical replay, and synthetic mock—with separate data-quality labels;
- broker PAPER PnL and conservative quote-side shadow PnL as separate truth surfaces;
- an exposure-aware order reducer that preserves unknown, partial-fill, incident, and non-flat states;
- risk reservation before submission and release only after fill/cancel reconciliation;
- broker truth at startup, after disconnect, after every fill, and before declaring flat;
- an explicit `NOT RUN` ledger; and
- a deterministic Passport verifier over an append-only evidence chain.

### 3.5 Proof path

The dependency gates are:

1. **Gate A — competition and account contract:** verify scoring, horizon, costs, permitted instruments, drawdown rules, data entitlement, PAPER-only account state, and required broker capabilities.
2. **Gate B — data feasibility:** prove legitimate point-in-time manifests for earnings and macro candidates before comparing them.
3. **Gate C — underlying signal tournament:** compare cash, price-only, deterministic-parser, LLM, and placebo controls under frozen clocks, costs, and chronological partitions.
4. **Gate D — expression comparison:** compare shares, one long option, and a debit vertical for the winning direction under the same events and operational budget.
5. **Gate E — autonomous PAPER lifecycle:** after every prior gate and explicit approval, produce one strategy-generated open-to-flat Alpaca PAPER Passport with broker-confirmed flatness.

### 3.6 Parameters still requiring evidence

Issue #26 freezes owner-selected research observation clocks, feature definitions, deterministic
confirmation thresholds, sample floors, baselines, and chronological partitions in the accepted V1
policy. These are preregistered hypotheses, not evidence of alpha, and may change only through a new
policy identity and prospective reset.

The exact competition constants, source vendors and rights, promoted observation and exit clocks, LLM
provider/configuration, activated risk budgets, option DTE/delta/width rules, and evidence thresholds
remain unresolved. Numerical values in issues, dated drafts, or section 9.3 are hypotheses or
non-enabling upper-ceiling candidates until Gate A, the owning contract, unit tests, and frozen
validation support an approved policy receipt. Missing, contradictory, or less permissive
organizer/account facts override those candidates and keep entries disabled. Gate A facts are
recorded as `UNVERIFIED`; expression, exit, and risk remain `UNSELECTED`. No implementation may fill
those values from a dated draft or legacy demo constant.

## 4. The LLM's task

The hosted LLM performs two isolated jobs. They may use the same provider, but they have different tools, data, and authority.

### 4.1 Research job

The research job operates outside the broker path. It may:

- inspect point-in-time historical panels through read-only analytics tools;
- formulate economically coherent signal hypotheses;
- request deterministic feature calculations, event studies, backtests, ablations, sensitivity tests, and regime comparisons;
- compare simple rules, statistical models, and LLM-assisted hypotheses;
- identify leakage, contradictions, data gaps, and likely falsifiers;
- propose a new frozen strategy policy for review.

It does not calculate portfolio arithmetic in prose. Python or another deterministic analytics engine calculates returns, betas, volatility, costs, uncertainty, fills, and PnL. The LLM interprets those results.

It also cannot publish its own proposal into production. A proposed policy must pass the predefined historical, untouched, prospective, operational, and review gates before it becomes an approved strategy release.

### 4.2 Event-decision job

For each eligible event, the decision job receives one immutable point-in-time evidence packet plus bounded read-only retrieval over cited source material. It interprets:

- issuer releases, filings, guidance, and amendments;
- permitted news published before the cutoff;
- point-in-time consensus where legitimately available;
- historical company and event context;
- stock, market, and sector reaction;
- volatility, liquidity, regime, and data-health features;
- outputs from deterministic baselines and approved historical-analogue queries; and
- explicit unknowns and contradictions.

Its output is a strict decision record:

```json
{
  "decision": "UP | DOWN | UNCERTAIN",
  "evidence_ids": ["source-id"],
  "contradictions": [
    {
      "evidence_ids": ["source-id-a", "source-id-b"],
      "summary": "bounded contradiction"
    }
  ],
  "unknowns": ["stable reason code"],
  "strongest_falsifier": {
    "evidence_id": "source-id",
    "summary": "bounded falsifier"
  },
  "summary": "bounded rationale"
}
```

The LLM chooses direction or abstention. It does not choose an arbitrary symbol, contract, strike, expiry, quantity, limit price, account, risk budget, or exit.

The accepted policy derives stable hashes for the provider-neutral route, prompt contract, and
output schema. Each exchange additionally binds the exact evidence packet, request/response bytes,
provider/model identity, decoding configuration, and eight-second deadline. Issue #28 may integrate
a provider, but it may not alter those policy-owned semantics in place.

## 5. What “the LLM makes a trade” means

The LLM produces the strategy's trade intent. Esscher turns that intent into a safe order:

```text
UP
  -> deterministic bullish instrument/package search
  -> PACKAGE or NO_PACKAGE
  -> risk approval or rejection
  -> one-use PAPER permit
  -> Alpaca PAPER execution

DOWN
  -> deterministic bearish instrument/package search
  -> PACKAGE or NO_PACKAGE
  -> risk approval or rejection
  -> one-use PAPER permit
  -> Alpaca PAPER execution

UNCERTAIN
  -> NO_TRADE
```

An `UP` or `DOWN` result is necessary but not sufficient for an order. Stale market data, weak liquidity, an unavailable legal instrument, excessive spread, account exposure, exhausted loss budget, duplicate event, market closure, or uncertain broker state must still produce `NO_TRADE` with a stable reason.

This keeps the strategy genuinely model-generated without allowing a stochastic component to bypass risk or mutate broker state directly.

## 6. End-to-end architecture

```text
COMPETITION CONTRACT + APPROVED STRATEGY RELEASE
                         |
                         v
Calendar / security master / ex-ante candidate freeze
                         |
                         v
Permitted ingestion pipelines
  issuer IR | SEC | news | fundamentals | stock | market | sector | options
                         |
                         v
Immutable raw evidence and market observations
                         |
                         v
Point-in-time snapshot and deterministic feature compiler
                         |
            +------------+------------+
            |                         |
            v                         v
Deterministic baselines       Bounded LLM decision
            |                         |
            +------------+------------+
                         v
Deterministic validator: UP | DOWN | UNCERTAIN
                         |
             +-----------+-----------+
             |                       |
             v                       v
Research/evaluation ledger   Instrument/package compiler
                                     |
                                     v
                              Risk and reservation
                                     |
                                     v
                              One-use PAPER permit
                                     |
                                     v
                        Narrow Alpaca PAPER adapter
                                     |
                                     v
                       Order/fill/position state machine
                                     |
                                     v
                      Deterministic monitor and exit
                                     |
                                     v
                       Broker reconciliation and PnL
                                     |
                                     v
                  Prospective evidence and research feedback
```

The research and production planes share immutable contracts and approved releases. They do not share unrestricted tools or policy-write authority.

## 7. Data and evidence plane

Every decision-relevant input records, where applicable:

- issuer, security, and event identity;
- source and retrieval endpoint;
- publisher timestamp, timestamp precision, retrieval time, and timezone;
- raw content or a rights-compatible content hash;
- feed, adjustment, pagination, and entitlement policy;
- schema/parser/compiler identity;
- point-in-time cutoff and availability result;
- explicit stale, missing, conflict, and exclusion codes.

The system needs these durable layers:

1. competition contract and scoring rules;
2. historical security master and corporate actions;
3. earnings/event calendar observations and revisions;
4. issuer and SEC evidence;
5. permitted news evidence;
6. fundamentals and point-in-time consensus where licensed;
7. synchronized stock, market, and sector observations;
8. current/prospective option chains and, only if legitimately sourced, historical option BBO;
9. deterministic feature receipts;
10. reasoner request/response receipts;
11. decisions, packages, risk reservations, orders, fills, positions, exits, and terminal reconciliation.

Raw licensed payloads must not be committed when rights prohibit redistribution. Schemas, hashes, source receipts, derived features, and synthetic test fixtures remain commit-safe when correctly labelled.

## 8. Universe and instrument policy

### 8.1 Universe selection

Systematic ex-ante selection is mandatory. It provides the honest denominator needed to measure eligibility, abstention, data failure, package failure, and strategy performance; it does not guarantee alpha.

- Freeze the eligible universe and all exclusion reasons before outcomes.
- Apply the same eligibility and ranking rule in historical, untouched, prospective, and PAPER evaluation.
- Preserve every considered, excluded, rejected, and abstained candidate.
- Permit human intervention only to reject corrupt evidence, enter close-only recovery, or invoke a kill switch—not to add a preferred symbol after observing an outcome.
- Report complete-denominator results rather than showcased winners.

### 8.2 Direction before trade expression

- Shares are the first validation surface because they isolate directional quality from volatility, strike, expiry, spread, assignment, and fill-model effects.
- Cash/no trade, shares, one long option, and a defined-risk debit vertical are compared only after the underlying policy is frozen.
- Historical underlying direction is never reported as historical option PnL.
- An option expression is promoted only when legitimate quote data and after-cost historical/prospective evidence justify the additional model.
- Production uses one approved frozen expression policy; deterministic code selects the permitted instrument/package or emits `NO_PACKAGE`.

## 9. Quantitative validation

A hosted LLM removes the need to train a proprietary model immediately. It does not remove research discipline.

Required evidence surfaces:

1. **Development panel:** build pipelines, establish baselines, and falsify weak ideas.
2. **Walk-forward evaluation:** train or choose only on earlier data; test on later chronological blocks with appropriate purge/embargo treatment.
3. **Untouched confirmation:** no feature, prompt, provider, threshold, universe, or policy choice may use its outcomes.
4. **Prospective shadow ledger:** record future events after policy freeze, including every abstention and failure.
5. **PAPER evidence:** attribute actual broker observations to strategy decisions while retaining PAPER limitations.

Historical hosted-LLM decisions may be contaminated by knowledge acquired during model pretraining. Point-in-time prompts do not erase that knowledge. Prospective post-freeze events are therefore the strongest evidence for the LLM's incremental contribution.

Compare at minimum:

- always abstain and random controls;
- simple direction/sign rules;
- market/sector-only baselines;
- transparent statistical models;
- the bounded LLM;
- ablations without text, without historical analogues, and without selected feature groups.

Report coverage, abstention, rejection, data failures, package failures, costs, drawdown, concentration, sensitivity, uncertainty, and full PnL attribution. Directional accuracy alone is not a trading result.

### 9.1 Close the strategy family before implementation

The current research inputs describe materially different strategies: an intraday reaction lane and a multi-day post-earnings continuation lane. They may share evidence contracts, but they do not share one target, execution budget, or risk model. Gate C must preregister and compare them rather than silently blend them.

- BMO and AMC remain separate cohorts with independent manifests, partitions, ledgers, metrics, and promotion decisions. Results are never pooled to rescue a failing cohort.
- `S0` is defined from the primary listing's pinned exchange calendar. An immutable pre-outcome calendar record assigns the cohort, and its label must agree with `max_public_at`: BMO is after 00:00 exchange-local on the `S0` date and before the `S0` opening auction; AMC is after the regular close of `S-1` and before 00:00 exchange-local on the `S0` date, with `S0` the next regular session. A missing or contradictory label, a timestamp outside those mutually exclusive windows, holiday ambiguity, or evidence not finalized before a candidate entry produces `MISSED_CLOCK` or exclusion under the frozen amendment rule; it never silently relabels or rolls cohorts.
- `S+k` means the kth subsequent full regular session after `S0`, not a calendar day. Every timestamp and daylight-saving conversion uses the pinned exchange calendar and timezone rules.
- The intraday reaction lane permits only `S0` auction, 09:35, 09:45, or 10:00 entries paired with the `S0` regular-session close. The `S0` close cannot pair with itself.
- The multi-day continuation lane permits only an `S0` regular-session-close entry paired with the `S+1`, `S+5`, `S+10`, or `S+20` regular-session close.
- Gate C preregisters the allowed matrix above separately for BMO and AMC and rejects all other entry/exit cross-products. Each candidate uses only evidence public and finalized before its entry clock.
- The trial budget, selection metric, chronological partitions, transaction-cost model, and untouched confirmation set are frozen before outcome inspection.
- Promote one target/clock policy only when it is stable across neighbouring clocks and survives after-cost chronological evaluation. Otherwise report no winner.
- If the edge requires sub-second reaction to public news, reject that lane. A hosted LLM and Alpaca PAPER are not a high-frequency execution stack.

Research basis for this contest rather than assuming the classic anomaly persists unchanged:

- transaction costs materially reduce implementable PEAD profits: <https://doi.org/10.1111/j.1475-679X.2008.00290.x>;
- one modern study reports classic PEAD largely absent in large stocks since 2006: <https://doi.org/10.31235/osf.io/z7k3p>;
- text-derived earnings-call surprise reports stronger recent drift than classic numeric PEAD: <https://doi.org/10.1017/S0022109022001181>.

### 9.2 Parameter ownership and change control

Every parameter belongs to exactly one class:

1. **Hard safety policy:** PAPER-only, authority boundaries, stale-data lockout, permitted instruments, account/exposure ceilings, idempotency, reservation, close-only recovery, reconciliation, and flatness. These values are conservative and cannot be tuned against returns.
2. **Research parameter:** entry clock, holding horizon, feature inclusion, deterministic thresholds, stop/take-profit overlay, and expression. These are chosen only through the frozen Gate C/D protocol.
3. **Commissioning limit:** deliberately restrictive PAPER limits used to prove safe unattended operation. They are not alpha claims and may only be relaxed by an approved policy release with evidence.
4. **Runtime measurement:** source delay, model latency, quote age, broker acknowledgement/fill time, slippage, rejection, and reconciliation duration. Measurements never silently rewrite policy.

Each release binds the strategy, data, feature, prompt/model, expression, risk, exit, latency, and execution-policy identities. Production cannot self-train, retune, promote, or amend them.

### 9.3 PAPER commissioning risk envelope

Gate A and #30 own activation of every risk value. Until verified competition/account facts and an approved risk-policy receipt exist, the only valid runtime state is `ENTRY_DISABLED`; missing values are not interpreted as zero or as permission. The following values are provisional upper-ceiling candidates for design, simulation, and tests. They cannot enable entry. An activated value is always the minimum of the approved Gate A/risk-policy value, the applicable verified organizer/account limit, and the candidate ceiling below:

- event loss ceiling candidate: at most `0.25%` of reconciled net liquidation value;
- aggregate open bounded-loss ceiling candidate: at most `1.00%`;
- one-sector or correlated event-bucket ceiling candidate: at most `0.50%`;
- pending entry orders reserve their full bounded potential loss before submission;
- concurrent-position ceiling candidate: one during broker commissioning, and at most four only after lifecycle/fault tests pass and a later approved receipt activates that value within the aggregate cap;
- no leverage, averaging down, discretionary size increase, or risk-limit change during an active release;
- session-loss lockout candidate: at most `1.00%`, after which new entries are disabled and pending entries are cancelled after reconciliation;
- high-water drawdown freeze candidate: at most `3.00%`, activated only after positions and orders are reconciled; and
- an operational discrepancy enters `ENTRY_DISABLED`/`CLOSE_ONLY` independently of strategy PnL.

Sizing requires an activated bounded-loss value rather than cash outlay or a stress scenario alone:

```text
R_event = min(
  activated_event_loss_budget,
  remaining_activated_portfolio_budget,
  remaining_activated_bucket_budget,
  liquidity_capacity,
)

quantity = floor(R_event / worst_case_loss_per_unit)
```

For a long option or ordinary long debit vertical, worst-case package loss is the executable debit times the contract multiplier and quantity, plus modelled fees and operational reserves. A long-share PAPER expression treats the full executable purchase notional plus fees as the bounded maximum loss; adverse event-move and overnight-gap stresses remain additional reporting and concentration controls, not substitutes for that bound. Historical direction testing may score `DOWN` as the negative underlying return, but that signed research series grants no short-sale authority. Uncovered short shares are prohibited during commissioning because their loss is unbounded; a `DOWN` decision therefore emits `NO_PACKAGE` unless Gate D has promoted a separately validated bounded-loss long put or debit put spread and all capability gates pass. If one indivisible package exceeds the activated budget, emit `NO_PACKAGE`; never enlarge the budget to force a trade. Kelly sizing is prohibited until a sufficiently large untouched/prospective sample supports a conservative estimate and a separately approved cap.

### 9.4 Exit, stop, and emergency policy

The lifecycle distinguishes four concepts:

1. **Strategy exit:** the frozen alpha hypothesis, initially benchmarked against a time-only exit.
2. **Hard time exit:** an event-clock deadline that late fills cannot extend.
3. **Safety/emergency exit:** deterministic containment for data, policy, monitor, broker, expiry, assignment, or exposure failures.
4. **Reconciliation:** independent proof that orders, fills, positions, reservations, and the broker agree and the account is flat when required.

Stop-loss and take-profit rules are research overlays, not presumed safety. Compare a small preregistered family—time-only, volatility/residual invalidation, deterministic signal-decay, and fixed-risk overlays—and promote an overlay only if it improves untouched after-cost results and expected shortfall across neighbouring values.

Equity bracket/OCO orders may provide a secondary defence, but stop-market execution price is not guaranteed, stop-limit orders may not fill, and cancellation races require immediate reconciliation. Position size must remain safe if the stop slips or fails.

Current Alpaca US documentation states that single-leg options support `market`, `limit`, `stop`, and `stop_limit`, while multi-leg option orders support `market` and `limit` only. The order schema permits equity `bracket`/`OCO`/`OTO` classes but options only `simple` or `mleg`. Therefore:

- do not claim broker-native bracket/OCO protection for options;
- do not use native single-option stops until the pinned adapter and PAPER contract tests prove exact behaviour;
- manage debit-vertical exits deterministically from fresh two-sided leg quotes;
- compute conservative closing value from executable quote sides, not midpoint;
- close verticals as atomic multi-leg packages with bounded repricing only if the pinned adapter capability receipt and PAPER lifecycle tests prove the required submit, cancel, partial-fill, readback, and reconciliation semantics; otherwise the expression is ineligible and produces `NO_PACKAGE`;
- never leg out except through an explicit reconciled emergency procedure;
- close before the frozen expiry/assignment boundary and poll REST state where streaming does not cover assignment.

Emergency flattening has one owner and one state machine with explicit non-flat incident outcomes:

```text
ENTRY_DISABLED
  -> CANCEL_PENDING_ENTRIES
  -> RECONCILE
      -> EXPOSURE_KNOWN
          -> CLOSE_ONLY
          -> FLATTENING
          -> RECONCILE_CLOSE
              -> BROKER_CONFIRMED_FLAT
              -> RETRYABLE_CLOSE_FAILURE -> RECONCILE
              -> MANUAL_REQUIRED_NON_FLAT
      -> BROKER_STATE_UNKNOWN
          -> RETRYABLE_RECONCILIATION -> RECONCILE
          -> MANUAL_REQUIRED_UNKNOWN
```

Retry counts, deadlines, and idempotency keys are frozen policy. Rejected or partial closes, broker outage, contradictory exposure, assignment/expiry events, and exhausted retries enter the appropriate `MANUAL_REQUIRED` state with entries disabled and close/manual authority retained. `MANUAL_REQUIRED` is an incident outcome, not flatness or lifecycle success: the Passport remains explicitly non-flat or unknown, reservations remain conservative, and final PnL/terminal-success receipts are prohibited. Only a fresh broker readback proving no applicable open orders or exposure may enter `BROKER_CONFIRMED_FLAT`, release reservations, and finalize PnL. Unknown broker state is reconciled before a new close is submitted; blind flattening may invert a position.

### 9.5 Feature and RSI policy

Deterministic code computes every numeric feature. The initial hypothesis set is deliberately small: permitted numeric/guidance/text surprise, market/sector residual reaction, relative volume, spread and quote age, realised volatility, pre-event residual momentum, and distance from session VWAP.

RSI is an optional ablation for the specific continuation-versus-reversal hypothesis. Its lookback, input bars, adjustment policy, and cutoff are frozen before evaluation. It cannot independently authorize a trade, and it is removed if it adds no untouched after-cost value over simpler price features. Do not create an indicator zoo of correlated RSI/MACD/moving-average/Bollinger transformations.

The LLM consumes immutable evidence and precomputed features. It does not calculate indicators, select feature windows, or use self-reported confidence as a sizing input. Its incremental contribution must beat price-only, parser, transparent statistical, and deterministic text baselines prospectively.

### 9.6 Decision, quote, and execution latency

Optimise latency around the promoted observation clock, not as a vanity metric. Precompute source retrieval, parsing, historical context, and model/provider readiness before the clock; at the clock add only fresh market/sector/volume observations and the bounded decision.

Initial commissioning service-level objectives, measured rather than assumed, are:

- deterministic feature refresh: p95 below `250 ms`;
- LLM response: p95 below `5 s`, hard timeout by `8 s`, no blind retry;
- validation, package, reservation, and permit: p95 below `250 ms`;
- broker acknowledgement: measured continuously, initial p95 target below `2 s`;
- complete decision-to-submit path: p95 below `10 s`.

A model timeout is `UNCERTAIN`/`NO_TRADE`. Separate evidence validity, decision validity, package quote validity, and order deadline. The existing 60-second permit TTL cannot substitute for quote freshness. Provisional quote-age ceilings are one second for equities and two seconds per option leg; any expired quote requires package refresh and complete revalidation.

Every Passport records publisher, receipt, evidence-ready, observation-clock, model request/response, quote, permit, submit, acknowledgement, first-fill, final-fill, close, and reconciliation timestamps.

### 9.7 Market-data and PAPER realism gate

Alpaca PAPER does not model market impact, latency slippage, queue position, price improvement, or displayed NBBO quantity. Broker PAPER PnL therefore remains separate from conservative executable shadow PnL.

The Basic options feed is indicative; OPRA is the consolidated options feed. Autonomous option entry and exit stay disabled unless the runtime proves its current entitlement and quote source. A fast decision against indicative option prices is not executable evidence.

Sources reviewed for these broker capabilities and limitations:

- <https://docs.alpaca.markets/us/docs/options-trading>
- <https://docs.alpaca.markets/us/docs/options-level-3-trading>
- <https://docs.alpaca.markets/us/reference/postorder>
- <https://docs.alpaca.markets/us/docs/orders-at-alpaca>
- <https://docs.alpaca.markets/us/docs/paper-trading>
- <https://docs.alpaca.markets/us/docs/real-time-option-data>
- <https://docs.alpaca.markets/changelog/options-stop-limit-orders>

## 10. PnL objective

Before strategy freeze, copy the organizer's rules into a versioned competition contract. The optimal policy changes materially between:

- maximum terminal raw PnL;
- maximum percentage return;
- highest probability of finishing positive or near the top;
- risk-adjusted scoring;
- judge-weighted engineering and reproducibility; and
- rules that include or exclude unrealized PnL, options, leverage, costs, or drawdown penalties.

Esscher should maintain separate truth surfaces:

- broker PAPER gross and net PnL;
- conservative bid/ask-marked PnL;
- base cost-adjusted PnL;
- stress cost-adjusted PnL; and
- strategy-level results including abstentions and rejected packages.

A high paper-fill PnL that disappears under conservative marks is labelled paper-dependent, not validated alpha.

## 11. Implementation work order

This is dependency order for the complete architecture, not a sequence of reduced products.

1. Complete Gate A and freeze the competition/account contract without inventing inaccessible rules.
2. Complete Gate B for both earnings and macro candidates; block any lane without a legitimate point-in-time data manifest.
3. Freeze each candidate's universe, information cutoff, target, clock grid, feature hypothesis set, parameter-selection protocol, baselines, chronological partitions, cost model, amendment rule, and claim boundary.
4. Define evidence, feature, reasoner, decision, expression, portfolio-risk, reservation, exit, latency, permit, lifecycle, Passport, reconciliation, evaluation, and release contracts.
5. Build the security master, earnings calendar, issuer/SEC/news, macro-release, fundamental, market/sector, and option-observation pipelines required by those manifests.
6. Build deterministic features, price-only and parser baselines, placebo controls, the provider-neutral LLM adapter, and strict validator.
7. Complete Gate C using survivorship-safe historical panels, bounded parameter trials, untouched manifests, and prospective signal ledgers; promote one frozen directional/clock/exit policy or report no winner.
8. Complete Gate D for the winning policy; promote one expression only if its executable after-cost evidence supports it.
9. Build deterministic selection for the promoted expression, including explicit `NO_PACKAGE` outcomes.
10. Build account-relative risk, durable reservations, correlation buckets, drawdown states, quote-bound idempotent permits, entry/close controls, and the append-only Trade Passport.
11. Build the monitored order/position lifecycle, validated strategy exit, hard time exit, synthetic option-package exit, emergency flattening, exposure-aware reducer, and reconciliation worker.
12. Prove an offline causal slice from source bytes to a final-flat fake-broker Passport, including restart, duplicate, stale-quote, partial-fill, lost-monitor, assignment/expiry, and ambiguous-timeout faults.
13. Run the full-stack prospective shadow ledger with explicit evidence modes, measured latency, quote-side execution economics, `NOT RUN`, broker/shadow PnL separation, and complete-denominator reporting.
14. Complete Gate E only after explicit approval: record one strategy-generated Alpaca PAPER open-to-flat lifecycle and broker-confirmed flatness.

Parallelism is allowed only across independent ownership boundaries. Shared contracts and hotspot files have one owner.

## 12. Relationship to GitHub issues

The live issues currently map as follows. Issues #26–#33 own parent outcomes; #40–#50 split implementation-critical contracts and sequencing beneath them:

- #26 — strategy policy;
- #27 — point-in-time data collector;
- #28 — generated direction decision;
- #29 — expression comparison and deterministic compiler;
- #30 — account risk and durable reservations;
- #31 — monitored lifecycle and frozen-policy close;
- #32 — prospective shadow ledger;
- #33 — integrated tracker and frozen-release programme;
- #40 — changing competition/account/entitlement/capability facts for Gate A; unknown exposure or eligibility facts keep entry disabled;
- #41 — source rights and point-in-time feasibility before collector implementation;
- #42 — point-in-time security-master and corporate-action lineage;
- #43 — deterministic feature receipts between frozen snapshots and every decision arm;
- #44 — the early post-freeze prospective signal ledger, deliberately started once the decision pipeline exists and without waiting for broker infrastructure;
- #45 — immutable strategy promotion, rejection, revocation, and production loading;
- #46 — PAPER-only least-privilege and hostile-input attestation;
- #47 — end-to-end deadline budgets and operational-health receipts;
- #48 — restart-safe frozen-release orchestration from due candidate to reconciled outcome;
- #49 — option assignment, exercise, expiry, and broker-driven position events, or `NOT_APPLICABLE` if shares win Gate D;
- #50 — later full-stack shadow evaluation through expression, risk, hypothetical lifecycle, and conservative PnL. It consumes #44 and does not replace the early signal ledger;
- #3 — historical confirmation panel consuming the frozen policy; and
- #9 — final explicitly approved PAPER lifecycle after every prerequisite gate.

The accepted earnings-primary, macro-challenger, direction-before-expression, bounded-LLM-authority, and PAPER-only decisions are reconciled across #26–#33. Their data feasibility, promoted clocks, expression, activated risk values, lifecycle, and evidence thresholds remain blocked until the owning #40–#50 contracts close; this plan does not mark those assumptions complete. The older issue-plan copy in `archive/` remains unchanged as the historical snapshot. Linked legacy gates such as #3 and #9 must consume the promoted policy and may not revive a hard-coded instrument, hold time, or illustrative risk constant that conflicts with this plan.

## 13. Acceptance proof

Esscher is not complete until one independently readable trace exists:

```text
permitted source bytes
  -> frozen point-in-time snapshot
  -> deterministic features and baselines
  -> LLM-generated UP/DOWN decision (UNCERTAIN ends as NO_TRADE)
  -> deterministic promoted expression and risk approval
  -> one-use PAPER permit
  -> broker order and fill observations
  -> monitored position and deterministic exit
  -> final-flat broker reconciliation
  -> attributable Trade Passport
  -> PAPER PnL and quote-side shadow PnL, or explicit PnL unavailable
```

The trace must prove:

- no hand-authored signal, package, permit, or exit entered the production path;
- no unavailable source or invented observation was substituted;
- every model claim cites supplied evidence;
- every deterministic artifact binds its parent inputs;
- every order-affecting action passed through risk and idempotency controls;
- restart, duplicate event, stale data, partial fill, ambiguous timeout, and non-flat exit paths fail safely;
- the broker confirms the final position state; and
- reporting distinguishes engineering proof, historical evidence, prospective evidence, PAPER performance, and profitability claims.

## 14. Reading and change control

- Read `docs/plans/README.md` for the complete archive and status labels.
- Research and review documents are inputs, not implementation authority.
- Never overwrite a dated source report. Add a new dated document and update the index.
- Change this current plan only when a decision is intentionally reconciled; record what changed and which evidence caused it.
- If this file conflicts with live broker state, current user authority, data entitlement, or competition rules, the live source wins and this file must be corrected before implementation continues.
