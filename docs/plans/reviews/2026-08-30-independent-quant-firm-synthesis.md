# Independent quant-firm synthesis

**Status:** accepted research synthesis; its promotion decisions were incorporated into [`../CURRENT.md`](../CURRENT.md) after Ben's explicit approval on 30 August 2026
**Date:** 30 August 2026
**Execution boundary:** Alpaca PAPER only
**Repository basis before this synthesis:** `468d11f423ab710632595a586312e121f00430e9`

## 1. Inputs and provenance

This memo reconciles four independent blank-slate strategy reports, then checks the proposed decision against the project-specific data and architecture reviews already in this archive.

| Input | Provenance | Preferred strategy | Stored SHA-256 |
|---|---|---|---|
| [`2026-08-30-clean-room-quant-firm-cto-report.md`](../research/2026-08-30-clean-room-quant-firm-cto-report.md) | Ciel clean-room small-firm CTO investigation; 15 cited sources | Liquid U.S.-equity event-reaction core; options as a gated satellite | `dc33cdc3a740ae84b539d2e39354db83405d814e881bd68af72888b8cfdf3e2b` |
| [`2026-08-30-clean-room-quant-researcher-report.md`](../research/2026-08-30-clean-room-quant-researcher-report.md) | Sol clean-room chief-quant investigation; 19 cited sources | Systematic post-earnings underreaction in liquid shares; conditional long options | `7b9228ce8b064376f1ae7eb47bbccf39ecdfecb0a37d1c8548104f3e1e3cfccb` |
| [`2026-08-30-clean-room-tournament-portfolio-report.md`](../research/2026-08-30-clean-room-tournament-portfolio-report.md) | Sol clean-room tournament-PM/risk investigation; 9 cited sources | Equity event/momentum core plus a bounded option sleeve; convex concentration only when scoring rewards it | `c76a32aec8342ac2773a00f3d4158fccab30638858b9d57f60be76576eb3e51b` |
| [`2026-08-30-external-chatgpt-aegis-macro-desk-plan.md`](../research/2026-08-30-external-chatgpt-aegis-macro-desk-plan.md) | Ben-supplied online ChatGPT deep-research brief; 30 distinct external URLs | Scheduled macro-release interpretation, delayed SPY confirmation, and deterministic SPY debit spreads | `d24cb46164b4c5642140b0312e6f679d46bbc3d038965861638e708dedf104b7` |

The supplied Aegis source was 130,991 bytes with SHA-256 `864d353cb4d3a768e92843534ae4bf18cd57a51c29827e9945e2c9207fb8d566`. The repository copy normalizes UTF-8 line endings, trailing Markdown whitespace, and fenced Python layout where required by the repository formatter. No strategy claim, recommendation, or operational instruction was substantively rewritten.

Project-specific constraints come from [`2026-08-30-quant-data-ai-evidence-memo.md`](../research/2026-08-30-quant-data-ai-evidence-memo.md) and [`../reviews/2026-08-30-research-to-paper-architecture-challenge.md`](../reviews/2026-08-30-research-to-paper-architecture-challenge.md). Those reviews are not additional clean-room votes; they identify data, authority, and verification requirements.

## 2. Executive decision

### Strategy

Keep **systematic post-earnings residual continuation in liquid U.S. common stocks** as the primary research candidate. Do not permit discretionary ticker selection. Every eligible event, rejection, score, and abstention must come from a point-in-time universe and frozen ranking rule.

Preserve **scheduled macro-release SPY continuation** as a named challenger and near-term operational proving lane, not as approved alpha. It can become the primary candidate only if:

1. the earnings data contract cannot be satisfied legally and point-in-time;
2. a pre-registered macro event study shows positive after-cost continuation evidence on the underlying;
3. the LLM adds measurable value beyond a price-only continuation rule and deterministic release parser; and
4. the result survives chronological holdout or prospective evidence rather than one competition event.

### Instrument

Separate the **directional strategy** from its **trade expression**.

- Validate the underlying directional signal with shares first because that isolates signal quality from option volatility, spread, strike, expiry, assignment, and simulator effects.
- Permit a deterministic defined-risk debit vertical only when current quotes, contract geometry, expiry, liquidity, maximum-debit, lifecycle, and risk gates all pass.
- Treat options as mandatory only if verified competition rules require an option fill. That would establish eligibility and operational evidence, not prove that options improve expectancy.
- Compare the same frozen direction and operational budget through cash, shares, a single long option, and a debit spread. The option expression is promoted only if it beats shares on the declared objective after executable costs.

### LLM authority

Retain the existing project decision boundary: the LLM emits `UP`, `DOWN`, or `UNCERTAIN` from an immutable evidence packet. A deterministic market-confirmation rule may **veto** or reject that direction but may not convert an LLM `UNCERTAIN` into a trade.

This preserves Ben's intended LLM role without granting capital authority:

- the LLM interprets evidence, weighs contradictions, chooses direction or abstention, and names a falsifier;
- deterministic code owns the universe, timestamps, arithmetic, data health, contract/package selection, quantity, debit, risk, permit, order lifecycle, exit, flattening, and reconciliation;
- malformed, late, stale, conflicting, or unsupported evidence fails closed;
- the model cannot call generic broker or trading tools.

### Architecture

Adopt the strongest Aegis engineering ideas regardless of which strategy wins:

1. a hash-linked **Trade Passport** for every candidate, abstention, rejection, order, fill, close, and reconciliation result;
2. explicit evidence modes such as live paper, recorded paper, historical replay, and synthetic mock, with orthogonal data-quality labels;
3. official broker PAPER P&L and conservative quote-side shadow P&L reported separately;
4. an order reducer that preserves `UNKNOWN`, partial-fill, incident, and exposure-bearing states rather than inferring flatness from a terminal parent order;
5. risk reservation before submission and release only after cancellation/fill reconciliation;
6. broker truth at startup, after disconnect, after every fill, and before declaring flat;
7. an explicit `NOT RUN` ledger so missing evidence cannot quietly become a performance claim; and
8. a deterministic Passport verifier and append-only evidence chain.

These controls improve either strategy. They are not evidence that either strategy has alpha.

## 3. Where the independent reports converge

| Question | Convergence |
|---|---|
| Hand-picked stocks or systematic selection? | Systematic, point-in-time eligibility and ranking. A human may veto corrupt data or trigger a kill switch, not insert a preferred ticker after seeing an outcome. |
| Broad scanner or bounded event family? | A bounded event-driven strategy with known information clocks. |
| LLM or deterministic trading authority? | LLM for interpretation/reasoning; deterministic code for money, contracts, risk, execution, and broker access. |
| Force activity or abstain? | Cash and explicit no-trade outcomes are first-class states. |
| Trust broker PAPER P&L? | No. Preserve broker PAPER observations and a conservative executable shadow ledger separately. |
| Options posture? | Defined-loss structures only; no naked options, 0DTE shortcut, midpoint-only P&L, or inferred liquidity. |
| Historical validity? | Point-in-time inputs, chronological testing, realistic delays/costs, complete candidate/rejection records, and untouched evidence. |
| Operational proof? | Immutable decision/order/fill records, idempotent submission, state reduction, reconciliation, and broker-confirmed flatness. |
| Can one profitable run prove the strategy? | No. A profitable PAPER lifecycle proves the path operated; it does not establish repeatable alpha. |

This convergence is strong enough to settle architecture and governance without waiting for a final alpha choice.

## 4. Where the reports disagree

| Decision | Earnings/equity view | Aegis macro/SPY view | Resolution |
|---|---|---|---|
| Event family | Earnings, filings, guidance, calls, revisions, and market residuals across many issuers | Scheduled government macro releases interpreted against SPY reaction | Earnings remains primary research candidate; macro is the explicit feasibility challenger |
| Sample structure | Cross-sectional and recurring, potentially hundreds of events | One underlying and sparse monthly releases | Earnings is scientifically stronger if its data contract is available; macro is operationally simpler |
| Main instrument | Shares by default; options only conditionally | SPY debit spreads as the central sponsor-aligned expression | Validate direction in shares; promote options only after the expression comparison |
| Holding horizon | Roughly intraday to 5–20 sessions depending on event/variant | Same-day post-release continuation with a mandatory time exit | Freeze separate strategy clocks; never blend outcomes across them |
| LLM contribution | Multidimensional issuer interpretation and directional decision | Release interpretation plus a deterministic price-confirmation table | Keep LLM direction; use deterministic confirmation as a veto and measure incremental value |
| Diversification | Several issuer events with beta/sector controls | One SPY spread and cash | Diversification is possible only in the earnings lane; Aegis must not claim it from one underlying |
| Primary optimization | Expected after-cost residual return or contest-rank utility | Auditable seven-day sponsor-native vertical slice | Project completion prioritizes validated strategy plus working PAPER operation, not demo convenience |

## 5. Why Aegis is not automatically the strategy winner

The Aegis report is the most complete implementation specification, but it correctly labels its own strategy decision **INVESTIGATE**. Its mandatory ledger says the continuation backtest, point-in-time option backtest, walk-forward validation, AI ablation, shares-versus-spreads comparison, parameter stability, execution tests, and positive-expectancy claim are all `NOT RUN`.

Three technical objections remain:

1. **Sparse independent observations.** A JOLTS/Employment-only design supplies too few release events for a convincing modern holdout. More release types can increase the sample, but each requires its own point-in-time field/revision mapping and may have a different market mechanism.
2. **Price confirmation may subsume the purported language edge.** If the system waits fifteen minutes and then follows the signed SPY residual only when release language does not object, the economic signal may reduce to post-release price continuation. That is a legitimate hypothesis, but the LLM's incremental value must beat the price-only and deterministic-parser baselines.
3. **Options add an unproven second hypothesis.** Correct direction does not imply a debit spread beats shares after bid/ask, volatility, theta, entry delay, and forced-exit costs. The report explicitly has not run that comparison.

Aegis remains valuable because its feasible universe is tiny, its source can be official, and SPY is operationally liquid. If the earnings point-in-time data gate fails, macro becomes the cleaner honest research route. That is a feasibility decision, not a rhetorical victory.

## 6. Why earnings remains the primary candidate

Three independent reports converge on a liquid-equity event-reaction or post-earnings family. The chief-quant report gives the most specific economic hypothesis: simple headline surprise may be absorbed quickly, while coherent multidimensional information across numeric results, guidance, text, later revisions, and residual market reaction may diffuse more slowly.

The family has four project advantages:

1. issuer events create a larger cross-section and therefore more falsifiable observations than one macro underlying;
2. the LLM has a substantive bounded job—resolving multidimensional evidence and contradiction rather than paraphrasing one number;
3. shares provide a cleaner test of directional edge before an option expression adds another model; and
4. a systematic universe can diversify issuer-specific errors and make candidate selection measurable.

Its decisive weakness is data feasibility. The project must not use revised consensus, current constituents, backfilled calendars, or reconstructed text as if they were observable at decision time. If licensed/permitted point-in-time expectations, event timing, identifiers, and source text are unavailable, the strategy remains blocked regardless of how strong its academic story sounds.

## 7. Direct source audit of the Aegis brief

A sampled audit of load-bearing Alpaca claims found:

- Alpaca's Basic plan currently documents IEX-only equity coverage and an indicative options feed, while its paid plan documents wider equity coverage and OPRA options data.[1]
- Alpaca states that PAPER trading omits market impact, information leakage, latency slippage, queue position, price improvement, regulatory fees, and dividends; it also says PAPER quantity is not checked against available NBBO size.[2]
- Alpaca documents historical option data only from February 2024 and distinguishes indicative derivatives from OPRA consolidated BBO data.[3]
- Alpaca documents multi-leg options and Level 3 trading, but the retrieved page specifically announces live multi-leg availability. A controlled competition-account PAPER MLeg contract test therefore remains mandatory rather than inferred from the page.[4]
- Alpaca's MCP documentation exposes toolset filtering and defaults its own paper flag to true, while still making account and trading toolsets available when enabled. The application must omit those toolsets and enforce its own exact getter allowlist.[5]
- `alpaca-py` is the official Python SDK and exposes the trading and market-data clients the brief references.[6]

The lablab event page and BLS schedule rejected direct automated retrieval during this audit, and the configured search backend was unavailable. Therefore the exact enrollment state, scoring formula, deadline conversion, option-activity requirement, and cited September release dates remain **unverified here**. The Aegis report itself already marks key rule/enrollment facts unknown or conflicting. They remain a hard external gate, not an inferred assumption.

## 8. Decision gates before strategy-generated PAPER mutation

The following gates apply in dependency order to the complete system; they are not separate product versions.

### Gate A — competition and account contract

Verify the official scoring objective, horizon, mark, costs, permitted instruments, required option activity, drawdown rules, account balance, data entitlement, and MLeg PAPER capability. Unknowns that change exposure keep entries disabled.

### Gate B — data feasibility

Produce a manifest for each strategy candidate:

- **earnings:** event calendar, official publication clock, consensus vintage, issuer documents, revisions, identifiers/corporate actions, underlying bars/quotes, and option observations;
- **macro:** official release archive, revision history, field mapping, optional consensus provenance, SPY bars/quotes, and option observations.

A strategy without a legitimate point-in-time panel cannot enter the comparison.

### Gate C — underlying signal tournament

Evaluate earnings residual continuation and macro SPY continuation separately under frozen clocks. Both must retain rejected/no-trade events and run:

- cash/no trade;
- price-only continuation;
- deterministic parser/rules;
- LLM direction with the same evidence cutoff;
- opposite/random-direction placebo;
- delayed-entry and stressed-cost sensitivities;
- chronological development, validation, untouched, and prospective slices where sample size permits.

Do not compare the two using unlike horizons or cherry-picked metrics. Report event coverage, abstention, expectancy interval, drawdown, adverse excursion, regime sensitivity, and LLM incremental value.

### Gate D — expression comparison

For the winning directional policy, compare shares, one long option, and a defined-risk debit spread using the same eligible events, direction, decision timestamp, exit clock, and frozen operational-loss budget. Use executable quote-side assumptions and preserve cases where no compliant option package exists.

### Gate E — autonomous PAPER lifecycle

Only after policy and package gates pass may the frozen strategy create a one-use PAPER permit. Completion requires a strategy-generated candidate through decision, risk reservation, Alpaca PAPER order, broker readback, monitored exit, both legs/positions confirmed flat, and an attributable Passport. A manual smoke order proves infrastructure only.

## 9. Promotion record

Ben accepted the strategy recommendation on 30 August 2026. The following were promoted into `CURRENT.md`:

1. no discretionary hand-picking;
2. earnings residual continuation as primary research candidate;
3. macro SPY continuation as a named challenger and operational proving lane, not approved alpha;
4. underlying-signal validation before instrument promotion;
5. options as a deterministic, gated expression rather than an assumed core advantage;
6. LLM `UP`/`DOWN`/`UNCERTAIN` authority with deterministic confirmation as a veto;
7. Trade Passport, evidence modes, shadow P&L, explicit `NOT RUN`, and exposure-aware reconciliation as system-wide requirements; and
8. Gate A through Gate E as the proof path to completion.

Do not import Aegis's illustrative 0.50% trade risk, 1.50% aggregate risk, 2.50% drawdown kill, DTE, delta, width, or timing constants as proven parameters. They are sensible policy candidates that still require account/rules verification, unit tests, and frozen validation.

## 10. Bottom line

The reports do not support selecting several fashionable stocks by hand. They support a systematic, event-driven research program with auditable abstention and a deterministic money path.

The strongest current strategic candidate is **liquid-equity post-earnings residual continuation**. The strongest immediate operational challenger is **Aegis-style macro-release SPY continuation**. The strongest architecture is the Aegis control system wrapped around the project's existing LLM directional-decision boundary.

That combination avoids both obvious errors: building a beautiful SPY options demo with no demonstrated edge, and spending weeks researching earnings while never proving that the autonomous PAPER lifecycle can open, monitor, close, reconcile, and explain a real strategy-generated position.

## Sources

[1] https://docs.alpaca.markets/us/docs/about-market-data-api — Alpaca: About Market Data API
[2] https://docs.alpaca.markets/us/docs/paper-trading — Alpaca: Paper Trading
[3] https://docs.alpaca.markets/us/docs/historical-option-data — Alpaca: Historical Option Data
[4] https://docs.alpaca.markets/us/docs/options-level-3-trading — Alpaca: Options Level 3 Trading
[5] https://docs.alpaca.markets/us/docs/alpaca-mcp-server — Alpaca: Trading MCP Server
[6] https://github.com/alpacahq/alpaca-py — Alpaca official Python SDK
