# Esscher end-state plan

**Status:** current team plan and decision boundary
**Updated:** 2026-08-30
**Repository basis:** `Tempest-Research/esscher-market` at `7fca3946f0730e96b6754c463d3aff22b52ccd06`
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

- Validate the directional policy on the underlying with shares before attributing value to an option structure.
- Compare cash/no trade, shares, one long option, and a defined-risk debit vertical on the same eligible events, decision timestamps, exit clock, and frozen operational-loss budget.
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

The exact competition constants, source vendors and rights, observation and exit clocks, LLM provider/configuration, risk budgets, option DTE/delta/width rules, and evidence thresholds remain unresolved. Numerical values in issues or dated drafts are hypotheses until Gate A, unit tests, and frozen validation support them.

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
  "strongest_falsifier_evidence_id": "source-id | null",
  "unknowns": ["stable reason code"],
  "summary": "bounded rationale"
}
```

The LLM chooses direction or abstention. It does not choose an arbitrary symbol, contract, strike, expiry, quantity, limit price, account, risk budget, or exit.

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
3. Freeze each candidate's universe, information cutoff, target, clocks, baselines, chronological partitions, amendment rule, and claim boundary.
4. Define evidence, feature, reasoner, decision, expression, risk, permit, lifecycle, Passport, reconciliation, evaluation, and release contracts.
5. Build the security master, earnings calendar, issuer/SEC/news, macro-release, fundamental, market/sector, and option-observation pipelines required by those manifests.
6. Build deterministic features, price-only and parser baselines, placebo controls, the provider-neutral LLM adapter, and strict validator.
7. Complete Gate C using survivorship-safe historical panels, untouched manifests, and prospective signal ledgers; promote one frozen directional policy or report no winner.
8. Complete Gate D for the winning policy; promote one expression only if its executable after-cost evidence supports it.
9. Build deterministic selection for the promoted expression, including explicit `NO_PACKAGE` outcomes.
10. Build account risk, durable reservations, idempotent permits, entry/close controls, and the append-only Trade Passport.
11. Build the monitored order/position lifecycle, exposure-aware reducer, and reconciliation worker using the promoted policy's frozen exit clock.
12. Prove an offline causal slice from source bytes to a final-flat fake-broker Passport.
13. Run the full-stack prospective shadow ledger with explicit evidence modes, `NOT RUN`, broker/shadow PnL separation, and complete-denominator reporting.
14. Complete Gate E only after explicit approval: record one strategy-generated Alpaca PAPER open-to-flat lifecycle and broker-confirmed flatness.

Parallelism is allowed only across independent ownership boundaries. Shared contracts and hotspot files have one owner.

## 12. Relationship to GitHub issues

The live issues currently map approximately as follows:

- #26 — strategy policy;
- #27 — point-in-time data collector;
- #28 — generated direction decision;
- #29 — expression comparison and deterministic compiler;
- #30 — account risk and durable reservations;
- #31 — monitored lifecycle and frozen-policy close;
- #3 — historical confirmation panel;
- #32 — prospective shadow ledger;
- #9 — final approved PAPER lifecycle;
- #33 — integrated tracker.

Issues #26–#33 were reconciled with this accepted decision on 30 August 2026. The older issue-plan copy in `archive/` remains unchanged as the historical snapshot. Linked legacy gates such as #3 and #9 must consume the promoted policy and may not revive a hard-coded instrument, hold time, or illustrative risk constant that conflicts with this plan.

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
