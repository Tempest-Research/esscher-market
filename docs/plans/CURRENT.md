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

## 3. What remains under active research

The firm-simulation reports and Ben's external research are being used to settle these decisions before policy constants are treated as final:

- the hackathon's actual scoring function, capital, horizon, mark source, costs, allowed assets, leverage, and intervention rules;
- whether the core should be scheduled-earnings residual direction, a broader event-reaction strategy, or another evidence-supported family;
- systematic universe selection, an ex-ante discretionary shortlist, or a hybrid;
- equities, options, or a gated combination as the trade expression;
- concentration required to maximize terminal PnL versus diversification required to maximize the probability of a strong finish;
- exact observation, entry, holding, exit, and flattening rules;
- exact historical data vendors and permissible retention/redistribution;
- the hosted LLM provider and operating parameters; and
- the evidence threshold required before strategy-generated PAPER mutation.

Until those questions are reconciled, existing numerical constants in issues or dated drafts are hypotheses and safety candidates—not proven optima.

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

The current default is systematic ex-ante selection because it gives an honest denominator and prevents selecting winners after observing returns. It does not guarantee alpha.

A hand-picked or hybrid universe remains legitimate only when:

- the shortlist is frozen before the relevant event or outcome;
- the selection rationale uses information available at freeze time;
- every considered and excluded candidate remains recorded;
- the same rule is applied in historical, prospective, and PAPER evaluation; and
- reported PnL includes the complete selected denominator rather than showcased winners.

The research comparison must measure systematic, discretionary-ex-ante, and hybrid methods under the same capital, latency, cost, and marking rules.

### 8.2 Equities versus options

This remains an empirical decision:

- Equities offer simpler data, tighter execution truth, easier historical validation, and lower variance.
- Long options or defined-risk spreads offer convexity and greater possible terminal PnL, but add IV, skew, spread, quote, multiplier, expiry, assignment, and fill-model risk.
- Historical underlying direction cannot be reported as historical option PnL.
- Options become the core only if legitimate quote data and prospective/PAPER results justify the additional complexity.
- If a hybrid wins, the underlying decision remains common while deterministic code selects the permitted expression under the frozen policy.

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

1. Capture the competition contract and unresolved rules.
2. Run source, entitlement, timestamp, feed, and marking feasibility checks.
3. Freeze the strategy hypothesis, universe method, information cutoff, target, baselines, partitions, amendment rule, and claim boundary.
4. Define direction-only evidence, feature, reasoner, decision, package, risk, permit, lifecycle, reconciliation, evaluation, and release contracts.
5. Build the security master, calendar, issuer/SEC/news, fundamental, market/sector, and option-data pipelines.
6. Build deterministic features and baselines.
7. Build the provider-neutral research/decision LLM adapter and strict validator.
8. Build the survivorship-safe historical panel, walk-forward evaluation, untouched manifest, and prospective signal ledger.
9. Reconcile the strategy choice using all independent reports and measured feasibility.
10. Build deterministic instrument/package selection for the approved expression.
11. Build account risk, durable reservations, idempotent permits, and entry/close controls.
12. Build the monitored order/position lifecycle and reconciliation worker.
13. Prove an offline causal slice from source bytes to a final-flat fake-broker receipt.
14. Run a full-stack prospective shadow ledger.
15. After explicit approval and every gate, record one strategy-generated Alpaca PAPER open-to-flat lifecycle.

Parallelism is allowed only across independent ownership boundaries. Shared contracts and hotspot files have one owner.

## 12. Relationship to GitHub issues

The live issues currently map approximately as follows:

- #26 — strategy policy;
- #27 — point-in-time data collector;
- #28 — generated direction decision;
- #29 — deterministic option package;
- #30 — account risk and durable reservations;
- #31 — monitored lifecycle and close;
- #3 — historical confirmation panel;
- #32 — prospective shadow ledger;
- #9 — final approved PAPER lifecycle;
- #33 — integrated tracker.

Their bodies preserve an earlier fixed scheduled-earnings/debit-vertical design and use “v1” terminology. They remain the live execution tracker, but any policy constant that conflicts with this current plan or the pending research reconciliation must be resolved explicitly rather than guessed. The dated issue-plan snapshot in `archive/` exists so changes can be reviewed rather than silently overwritten.

## 13. Acceptance proof

Esscher is not complete until one independently readable trace exists:

```text
permitted source bytes
  -> frozen point-in-time snapshot
  -> deterministic features and baselines
  -> LLM-generated UP/DOWN decision
  -> deterministic package and risk approval
  -> one-use PAPER permit
  -> broker order and fill observations
  -> monitored position and deterministic exit
  -> final-flat broker reconciliation
  -> attributable PAPER PnL or explicit PnL unavailable
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
