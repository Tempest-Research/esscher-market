# Clean-room quantitative firm analysis

**Status:** independent research input; not implementation authority
**Role simulation:** Tournament portfolio manager, quant developer, and risk lead
**Context boundary:** neutral autonomous U.S. equities/options PAPER-trading hackathon brief only; no Esscher repository, current plan, issue graph, or prior worker conclusions supplied

## Preferred approach

> Under the stated baseline, use a pre-registered, liquidity-gated event-driven barbell: a six-to-ten-name liquid equity event/momentum core plus a two-to-three-position defined-loss long-option/debit-spread sleeve, with an independent risk gate, idempotent OMS, broker reconciliation, conservative executable shadow PnL, and an internal drawdown threshold inside the official limit. Use the raw-upside convex catalyst playbook instead if scoring is almost purely first-place PnL with no binding drawdown; use the diversified equity playbook if risk-adjusted return, survival, or an unknown horizon dominates.

## Explicit assumptions

- All teams start with equal capital; deposits, withdrawals, and account resets are prohibited.
- The competition has a fixed 20-trading-day horizon and permits every position to be liquidated before the final scoring snapshot.
- Final raw PnL or percentage return contributes roughly two-thirds of the score and technology/judging roughly one-third.
- U.S. listed options are allowed, current OPRA data is available, and the broker supports native atomic net-debit multi-leg orders.
- A 25% high-water drawdown is disqualifying, and the organizer defines whether it is intraday and which marks are used.
- Official paper fills are NBBO-based but size and queue realism are uncertain, so the team may maintain and disclose a conservative executable shadow-PnL ledger.
- Naked short options, post-launch discretionary candidate selection, prohibited leaderboard use, deposits, and resets are excluded.
- Fees, borrow, dividends, corporate actions, assignment, exercise, and expiry treatment will be specified and applied consistently by the organizer.

---

# Tournament portfolio design under unknown rules

> **Scope.** This is an independent design for an autonomous U.S. equities/options paper-trading competition, not investment advice or a claim of guaranteed profitability. No competition rules were supplied. **Fact** denotes a public-source constraint, **hypothesis** an assumption requiring confirmation, and **tournament tactic** a rule-contingent response rather than evidence of alpha. Citations [S1]–[S9] correspond to the ordered `sources` array.

## Executive verdict

The objective is not simply to maximize expected return. It is:

`maximize P(S_i(T) > max_j≠i S_j(T))`

subject to leverage, drawdown, instrument, execution, autonomy, audit and disqualification constraints.

That rank utility is discontinuous. In a winner-take-all raw-PnL sprint, a respectable median finish has nearly the same prize value as last place, so a conventionally diversified low-volatility book can be strategically dominated by a legitimate positively skewed book. If Sharpe, Sortino, drawdown, survival or judging quality matters, that conclusion reverses. Evidence from mutual-fund tournaments is only an analogy, not a hackathon effect estimate, but it documents the incentive direction: relative-performance laggards increased later-period volatility more than leaders.[S9]

The best default under the explicit assumptions below is an **auditable catalyst barbell**: liquid equities provide repeatable, defensible exposure, while a strictly limited long-option/debit-spread sleeve preserves a credible first-place tail. The system should optimize probability of beating an uncertain rival hurdle, not expected PnL in isolation.

## 1. Public facts and unresolved hypotheses

### Public facts

- Paper PnL is not live PnL. Alpaca provides one concrete simulator example: its documentation says paper trading omits market impact, latency slippage, queue position, price improvement, regulatory fees and dividends, and may fill quantities larger than displayed NBBO liquidity.[S1] The hackathon platform may behave differently; its own rules must be inspected.
- Account mechanics depend on account and broker rules. FINRA's June 4, 2026 explainer distinguishes settled-cash constraints from intraday margin and broker house requirements.[S2] A paper contest can override them.
- A purchased option can lose its entire premium, while certain written options can expose the writer to unlimited losses.[S3] Defined loss is therefore a structural control.
- OPRA disseminates consolidated last-sale and quotation information for U.S. listed options and distinguishes current from delayed information.[S4] Delayed options data is unsuitable for short-lived execution decisions.
- Catalyst names can become untradeable at the critical moment. The LULD plan applies price bands and can create limit states or pauses in NMS stocks.[S5]
- SEC EDGAR provides unauthenticated JSON submissions/XBRL APIs and says submission structures update throughout the day in real time.[S6] This helps detect filings but is not a complete forward event calendar.
- Selecting the best result from many backtests creates material overfitting and multiple-testing risk. PBO formalizes the problem, while the Deflated Sharpe Ratio addresses selection bias and non-normal returns.[S7][S8]

### Unknowns that dominate the strategy

Starting capital; equal versus unequal accounts; exact start/end; fixed versus surprise termination; raw dollars versus percentage return; realized versus marked PnL; the official mark; commissions and fees; borrow availability/cost; leverage and intraday margin; option permissions; native multi-leg support; assignment/expiry treatment; market-data entitlement and latency; partial-fill and displayed-size modeling; maximum drawdown; daily loss; disqualification; account resets; leaderboard visibility; permitted manual intervention; number and aggressiveness of rivals; prize distribution; and technology/judging weight.

These must become versioned configuration. Until then, entries should remain disabled.

## 2. Objective-function and rule-sensitivity audit

Let `E0` be starting equity, `ET` terminal reconciled equity, `D` maximum path drawdown, `Q` technology/judging quality and `P` penalties. A generic score is:

`S = a(ET−E0) + b((ET/E0)−1) + c·RISK_ADJUSTED(returns) + dQ − P`

The coefficients and penalty function determine the correct portfolio.

| Rule | Consequence | Rational response |
|---|---|---|
| **Terminal raw PnL** | With equal capital and no flows, raw PnL and percentage return rank teams identically. With unequal capital, financing or resets, they do not. | Concentrate where ex-ante edge and payout convexity are strongest, while retaining enough loss buffer to survive. |
| **Percentage return** | Capital efficiency matters; leverage/options magnify both tails, while idle cash lowers return. | Size by equity and premium-at-risk, never by contract count. Ban deposits and resets. |
| **Sharpe/Sortino** | A few event jumps and many zero-return days can score poorly or become statistically unstable. Sampling interval, serial correlation and downside definition can reverse rankings. Multiple trials inflate selected Sharpe.[S8] | Prefer repeated liquid-equity opportunities, smoother exposure and lower turnover. Publish raw return and drawdown beside the official metric. |
| **Judging quality** | A lucky binary option may win PnL but lose credibility. A polished platform with no attributable alpha can win a demo and lose the trading contest. | Demonstrate deterministic signal-to-fill lineage, independent risk, broker reconciliation, ablations and conservative shadow PnL. |
| **Fixed horizon** | Information resolving after `T` has no tournament value; an open terminal option creates mark and expiry ambiguity. | Admit events expected to resolve before `T−buffer`; liquidate before the scoring snapshot. |
| **Unknown horizon** | Near-expiry options and deadline-specific tactics become fragile; carry and survival dominate. | Prefer equities or longer-duration defined-risk positions, smaller gross exposure and rolling eligibility. |
| **Leverage/margin** | Buying power is not risk capacity. Exercise, assignment, open orders and gaps can force liquidation. | Include open orders and worst-case assignment in exposure; maintain a buying-power reserve and use broker-reported margin truth. |
| **Drawdown/disqualification** | A hard barrier is an absorbing state: all later upside becomes worthless after breach. Spread widening can trigger marked drawdown before economic liquidation. | Put internal stops materially inside the official boundary and track both official-mark and executable-liquidation drawdown. |
| **Visible leaderboard** | Relative rank changes the value of variance; laggards have an incentive to seek more tail.[S9] | Use only a pre-registered state machine. Leaders may reduce risk; laggards may spend unused original risk budget but never lift limits or reset. |
| **Terminal marking** | Midpoint or stale-last marks can reward positions that could not be liquidated. | Close before cutoff and maintain a bid-for-long/ask-for-short shadow valuation even if official scoring differs. |

### Opponent model

Let `K` be team count and `H` the uncertain best-rival return. Build coarse preregistered scenarios for diversified, leveraged-equity and convex-option rivals. For each candidate portfolio, simulate joint terminal return, drawdown breach, fill stress and `H`, and compare:

`P(portfolio return > H and no disqualification)`

More teams or winner-take-all prizes raise the relevant upper-tail hurdle. A top-quartile prize or binding risk penalty shifts preference toward smoother playbook B. Do not refit the rival distribution to a leaderboard path after observing it.

## 3. Hand-picking versus a systematic universe

### When a few stocks or contracts are rational

A two-to-five-position book is rational when the horizon is short and fixed, raw/percentage PnL dominates, only a few scheduled events resolve in time, current option quotes and atomic orders exist, maximum loss is known, and complete loss of the sleeve cannot trigger disqualification. Concentration is then a tournament allocation choice—not evidence that discretionary forecasting is superior.

### When breadth is superior

Use a point-in-time systematic universe when risk-adjusted performance or judging matters, the horizon is unknown, opportunities repeat, breadth improves estimation, or manual selection would invite hindsight. A systematic universe need not produce hundreds of holdings: deterministic ranking can select five names.

### Ex-ante selection protocol

1. Freeze the point-in-time universe, data cutoff, event calendar, eligibility gates and feature signs before the first order.
2. Save every eligible and rejected candidate with raw inputs, normalized features, rank, proposed instrument and rejection reason.
3. Hash the code/configuration and candidate manifest before launch; provide it to organizers if preregistration is supported.
4. Permit manual veto only for predefined operational reasons—stale data, symbol error, corporate action, halt or rule ineligibility—not because a thesis feels weak.
5. Preserve delisted symbols and historical membership. Never replace a selected loser with a historically winning unselected candidate.
6. Inventory every tested universe, feature, window, event definition, strike, expiry and fill assumption. PBO and DSR exist because the displayed winning backtest conceals this search.[S7][S8]
7. Report organizer PnL and conservative executable PnL side by side.

## 4. Instrument and strategy comparison

| Approach | Tournament merit | Principal failure | Rational use |
|---|---|---|---|
| **Concentrated equities** | Transparent linear exposure, generally tighter spreads and easier reconciliation; margin can provide large dollar exposure. | Direction must be correct; gaps jump stops; shorts add borrow/recall risk; payoff is less convex than options. | Options are banned, delayed, illiquid or unrealistically simulated, or the directional signal is strong and liquid. |
| **Long calls/puts** | Defined premium loss and large convex gap payoff; capital-efficient upper tail. | Theta, IV crush, wrong strike/expiry, wide spread, zero bid and total-premium loss.[S3] Paper fills may be impossible live.[S1] | Fixed horizon, current OPRA, liquid chain, strong directional edge and an event before expiry. |
| **Debit spreads** | Defined risk, lower premium and vega than an outright option, often better break-even. | Caps the extreme tail; two-leg spread and assignment complexity; stale combo marks. | Forecast has a bounded target and native atomic net-debit orders exist. |
| **Long volatility** | Captures a large move without choosing direction. | Implied move may already price the event; two spreads, theta and post-event IV crush make close-to-close tests deceptive. | Forecast absolute move exceeds executable implied break-even plus both-leg costs. |
| **Short volatility** | Frequent small wins can look excellent in a positive-return or Sharpe contest. | Negative skew, margin/assignment risk and potentially unlimited writer loss.[S3] One gap can erase the run. | Only fully defined-risk credit structures, explicitly permitted, whose worst case fits well inside the drawdown buffer. Naked writers are excluded. |
| **Catalyst portfolio** | Concentrates information resolution inside the contest and supports auditable ex-ante selection. | Event postponement, correlated shocks, expensive implied volatility and LULD/halts.[S5] | Known horizon, reliable timestamps and strict liquidity gates. |
| **Adaptive strategy** | Can preserve a lead, react to volatility/data health and retain a chance when behind. | Easy to overfit or disguise as leaderboard gaming; may amplify noise and correlated rival behavior. | State transitions and maximum risk are fixed before launch and every transition is logged. |

### Execution-realism gates

- Require current consolidated equities data and current OPRA for options.[S4] Reject crossed, locked, zero-bid, stale or out-of-session quotes.
- Screen normalized spread, displayed size, underlying dollar volume, option volume/open interest and chain continuity. Open interest is only a coarse filter, not proof of executable size.
- Use bounded limit or marketable-limit orders. Use native complex orders for spreads; if atomic combos are unavailable, either model leg risk explicitly or disable spreads.
- Cap equity participation relative to ADV and option quantity relative to displayed NBBO size. A paper simulator's willingness to fill more does not make it legitimate.[S1]
- For risk, value long options at executable bid and short options at executable ask, with stale-quote haircuts.
- Close options before expiry/cutoff; model assignment and corporate actions; block entries during halts, limit states and stale-data conditions.[S5]

## 5. Common autonomous system boundary

`market/event ingest → point-in-time feature store → signal proposal → independent risk gate → OMS/state machine → paper-broker adapter → fills/positions → reconciliation → official and shadow PnL → dashboard/audit`

Strategy proposes. Risk approves. Execution submits. Reconciliation verifies. Reporting uses reconciled truth.

Minimum controls include instrument/session eligibility, quote age, duplicate client-order ID, order/notional/premium caps, gross/net/beta/sector exposure, worst-case option loss and assignment, open-order exposure, buying-power reserve, daily loss, high-water drawdown, rate/cancel limits, stale-data lockout, halt awareness, and distinct entry-disable, cancel-all and flatten states. Startup, streaming, periodic and end-of-day reconciliation are required. Every signal-to-fill path carries a correlation ID, code/config version and reason code. Missing configuration, unknown rules or stale position truth blocks new entries.

## 6. Playbook A — maximum raw upside

### Mandate

**Convex catalyst sprint.** Use only if final raw/percentage PnL overwhelmingly dominates, `T` is fixed, defined-risk options and current quotes exist, and total sleeve loss cannot breach disqualification. This playbook intentionally accepts a high probability of losing money in exchange for a larger first-place tail.

### Candidate-selection logic

1. Build a preregistered registry of earnings, issuer-announced events and objectively timed regulatory/court decisions expected to resolve at least two sessions before `T`.
2. Begin with liquid underlyings/chains; reject uncertain dates, corporate actions, stale or zero-bid contracts, excessive spreads and overlapping sector/factor shocks.
3. Estimate direction and absolute-move distributions using only information known before each historical event. Compare forecast quantiles with the contemporaneous ATM straddle-implied move and executable round-trip cost.
4. Estimate each candidate's contribution to `P(return > H)` rather than ranking solely by expected return. Penalize spread, quote age, event uncertainty, jump-to-zero risk and correlation.
5. Select two to four independent top ranks. A single-name exception is permitted only if the frozen model—not discretion—estimates a higher tournament-win probability and maximum loss remains compliant.

### Instrument choice

- Strong directional confidence and open-ended tail: liquid call or put expiring one to four weeks after the event; avoid ultra-far OTM contracts without a reliable bid.
- Moderate directional confidence and bounded target: call/put debit spread, short strike near a preregistered forecast quantile.
- Weak direction but strong move forecast: straddle/strangle only when expected move clears implied break-even plus executable costs.
- No naked short option, expiry-day entry or unmanaged legged spread.

### Portfolio and risk policy

Let `E` be equity and `D*` the organizer's allowed drawdown in dollars. A provisional maximum premium budget is `min(0.25E, 0.50D*)`, tightened after fill and mark rules are known. Allocate across at least two catalyst/correlation buckets unless the documented single-name exception fires. Hold a material buying-power and spread-widening buffer. No averaging down and no limit increase after a loss. The internal liquidation threshold should be materially inside `D*`.

Pre-register the endgame: if comfortably ahead during the final quarter, realize gains and reduce tail exposure; if behind, deploy only unused original premium budget.

### Implementation stack

Python 3.12; Polars/Arrow/Parquet for point-in-time event panels; NumPy/SciPy for empirical distributions; option-chain normalization and Greeks; PostgreSQL for configurations, orders and fills; async market-data/broker adapters; FastAPI dashboard; containers; deterministic replay CLI.

### Data requirements

Point-in-time underlying trades/quotes, corporate actions and event timestamps; current OPRA; historical option surfaces at every historical decision time rather than today's surviving chain; earnings/filing data; and broker buying power, orders, fills and positions.

### Execution and monitoring

Submit limit/net-debit orders, begin inside the spread, reprice only within a maximum debit and timeout, and cancel on quote staleness or event-time change. Monitor per-leg spread/size, implied move, Greeks, premium-at-risk, event clock, halt state, fill divergence and official versus shadow PnL. Close with a cutoff buffer.

### Expected failure modes

Implied volatility already prices the event; direction is wrong; IV collapses; the event moves; a halt prevents exit; option bid disappears; the simulator awards non-executable size; the historically best strike was unknowable ex ante; or supposedly independent positions share one market factor.

### Anti-gaming proof

Retain the complete candidate and rejection tables, contract rule, chain snapshots, manifest hash and bid/ask shadow ledger. Freeze strikes/expiries before outcomes. Disclose every option-selection trial and show official and executable liquidation PnL together.

## 7. Playbook B — maximum probability of a positive/high finish

### Mandate

**Liquidity-first diversified adaptive alpha.** Use when positive/top-quartile finish probability, drawdown or survival matters more than a jackpot; when `T` is unknown; or when option execution is weak. It sacrifices the most extreme upside but must not become a closet index fund.

### Candidate-selection logic

1. Freeze a point-in-time universe of roughly the most liquid 300–500 U.S. common stocks, with price, dollar-volume, spread, corporate-action and trading-status gates.
2. Combine a small fixed ensemble: residual medium-term momentum, post-event drift and liquidity-conditioned short-horizon reversal. Use lagged data and freeze signs/weights.
3. Sector/beta-neutralize ranks. Select about eight to twelve longs and, only when borrow is observable and modeled, eight to twelve shorts. Otherwise hedge with liquid index/sector ETFs.
4. Enforce a no-trade threshold rather than forcing full investment. Permit a small debit-spread sleeve only if its budget was fixed at launch.

### Instrument choice

Equities form the core because quotes, fills and marks are easier to defend. Use liquid ETFs for broad hedges when single-stock short data is unreliable. Optional debit spreads may express the top catalyst signals; lottery options and naked writers are excluded.

### Portfolio and risk policy

Use inverse-volatility weights with position, sector, beta, gross and net caps. Target daily volatility only after the official drawdown rule is known; reduce gross when realized volatility, cross-sectional correlation or spreads jump. Maintain buying-power reserve. A daily-loss threshold disables entries; a second threshold cancels and flattens. In the final quarter a positive book scales down, while a negative book can spend only a small preregistered convex reserve.

A provisional option-premium cap is `min(0.05E, 0.20D*)`; equities receive most of the risk budget. Gross exposure should be reduced automatically when estimated stress loss approaches the internal drawdown limit.

### Implementation stack

Python/Polars for daily/intraday features; point-in-time Parquet; a lightweight optimizer with explicit turnover, sector and beta constraints; PostgreSQL audit ledger; async quote/order adapter; independent risk/reconciliation services; metrics and dashboard. Add machine learning only if it beats a linear baseline out of sample.

### Data requirements

Adjusted and unadjusted point-in-time prices, quotes/volume, corporate actions, historical membership, sector classifications, market factors, event timestamps and broker truth. Shorts additionally require borrow availability and fee snapshots; unavailable borrow means exclusion, not a free-locate assumption.

### Execution and monitoring

Trade during liquid windows, avoid the open unless the signal requires it, use bounded marketable limits, cap turnover/participation and pair hedge legs with timeouts. Monitor realized versus target volatility, beta/sector residuals, correlation, spread/slippage, turnover, borrow state, stale data, rejects and PnL attribution by feature.

### Expected failure modes

The ensemble is data-mined; factor crowding or regime reversal; lagging beta estimates; missing borrow/fees; hedge fills without the alpha leg; turnover consumes a thin edge; or de-risking protects a modest gain while aggressive rivals pass it.

### Anti-gaming proof

Freeze universe construction, feature signs/weights and optimizer objective. Retain point-in-time membership, delisted names, borrow snapshots, turnover budget, baselines, ablations and trial counts. Apply commissions, spread and rejected/partial orders in shadow PnL.

## 8. Playbook C — best judge-defensible system

### Mandate

**Pre-registered event-driven allocator.** Use when technology/judging has material weight, rules are ambiguous or organizers expect a convincing autonomous implementation. Build the smallest system that proves causal signal-to-PnL, risk enforcement and legitimate execution—not architecture for its own sake.

### Candidate-selection logic

Freeze a liquid point-in-time universe and event registry. Rank events using four interpretable preregistered components: event-time certainty, residual price/volume trend, prior-event drift or surprise, and executable-cost-adjusted forecast versus implied move. Select five to ten names with correlation buckets and a no-trade threshold. EDGAR can supply real-time filing events, but issuer/event-calendar data remains necessary for forward scheduling.[S6]

### Instrument choice

Equities are the default. Enable options only after proving current OPRA, historical chain integrity, atomic combo orders and assignment handling. Permit only long options and debit spreads whose worst-case loss is known before submission.

### Portfolio and risk policy

Allocate by expected edge divided by forecast risk, then impose hard position, event, sector, beta, gross, net, premium, daily-loss and high-water-drawdown caps. Open orders count toward exposure. Missing configuration or stale broker truth blocks entries. Entry-disable, cancel-all and flatten are distinct states; re-enabling requires fresh data and successful broker reconciliation.

### Implementation stack

- **Research/data:** Python 3.12, Polars/Arrow, partitioned Parquet and point-in-time symbol/corporate-action maps.
- **Runtime:** typed Python services, PostgreSQL append-only audit tables, async adapters, deterministic client-order IDs and an explicit order-state machine.
- **Risk/reconciliation:** independent synchronous pre-trade gate; startup, streaming, periodic and end-of-day broker reconciliation; official and conservative shadow PnL.
- **Observability:** structured logs with correlation IDs, metrics/alerts, dashboard for data age/exposure/orders/fills/PnL/config version, one-command replay and incident drills.
- **Deployment:** locked dependencies, containers, UTC clocks, CI tests and reproducible manifests.

### Data requirements

All equity data required by playbook B; event calendars and EDGAR/issuer timestamps; official quote/mark definitions; current OPRA and historical chains only if options are enabled; broker account/order/fill streams; trading calendar, halts, LULD, splits, dividends and symbol changes.

### Execution and monitoring

Strategy can only propose. Risk validates quote age, market state, buying power and post-fill worst case; OMS submits idempotently; reconciliation treats broker state as authoritative; reporting reads reconciled positions. Monitor feed heartbeat, signal age, acknowledgement/fill/cancel latency, rejects, partial fills, cancel-fill races, slippage, exposure, Greeks, drawdown, official-shadow divergence and configuration changes. Drill stale feed, duplicate event, disconnect, partial fill, halt and restart with an open position.

### Expected failure modes

The system is reliable but has no alpha; event joins leak future timestamps; symbol mapping is wrong; complexity delays launch; dashboard state diverges from broker truth; the options module expands scope before data quality is proven; or a configured risk control is not wired into submission.

### Anti-gaming proof

Provide the preregistration manifest, source-data lineage, complete candidate/rejection ledger, code/config hashes on every decision, immutable signal→risk→order→fill events, broker reconciliation receipts, no-reset proof, exact score reconstruction, trial inventory, walk-forward/purged event tests, baselines, ablations and deterministic replay. Prove every money-path gate is load-bearing by disconnecting it and requiring its integration test to fail.

## 9. Red-team analysis

| Apparent brilliance | Likely live failure | Required falsification |
|---|---|---|
| **A selects historical events/strikes after seeing gaps and fills at midpoint.** | Actual option is wide, overpriced, halted or has no exit bid; IV crush overwhelms correct direction. | Point-in-time chains, frozen strike rule, executable bid/ask fills and delisted-chain retention. |
| **A shows only the lucky terminal return.** | Most launches lose the sleeve or breach drawdown before the jackpot. | Distribution of contest-length blocks, loss frequency and barrier-hit rate—not one backtest. |
| **B tunes windows/weights on the same history.** | Factor regime flips; costs and borrow consume a thin edge. | Trial inventory, walk-forward tests, PBO/DSR, simple baseline and cost stress.[S7][S8] |
| **B assumes independent returns and perfect hedges.** | Correlations jump, hedge fills first and short borrow disappears. | Event-block bootstrap, crisis stress, paired-order timeout tests and historical borrow snapshots. |
| **C presents impressive architecture and synthetic replay.** | Runtime works but expectancy is zero, or extra services create more failure points than they control. | Compare with cash, SPY and simple momentum/event baselines; measure uptime and alpha separately. |
| **C has green tests for decorative controls.** | A stale-data or exposure setting is never read by the submission path. | Disconnect each gate and require the integration test to fail. |
| **All treat official paper fills as proof of executability.** | Simulator ignores queue, size, impact, fees or dividends.[S1] | Conservative shadow fills, displayed-size caps, rejected-order simulation and realized closeout. |
| **Long volatility compares realized gap with implied percentage only.** | Correct move still loses after premium, spread and IV collapse. | Contract-level executable payoff using contemporaneous surfaces and exit quotes. |
| **Short volatility reports many small wins.** | One gap, assignment or margin event erases the run. | Defined worst case, gap stress and barrier-hit analysis; prohibit naked writers.[S3] |
| **Adaptive backtest uses the future leaderboard path.** | Live policy chases noise, correlated rivals or violates organizer rules. | Preregister the state machine and replay only information observable at each timestamp. |

The decisive red-team question is not whether the selected backtest has a high Sharpe. It is how many universes, features, windows, event definitions, contracts and fill assumptions were tried before that result was selected. DSR specifically identifies unreported trial count as central to assessing a backtest.[S8]

## 10. Preferred approach under explicit baseline assumptions

Assume equal capital and no flows; a fixed 20-session horizon; roughly two-thirds PnL and one-third technical judging; current OPRA and atomic debit spreads; a defined 25% high-water disqualification barrier; all positions closed before cutoff; and no discretionary post-launch selection.

Use **playbook C's auditable event-driven platform as a barbell**:

- Six to ten deterministically ranked liquid equity event/momentum positions.
- Two to three long-option/debit-spread positions whose aggregate premium is capped by both account equity and unused drawdown capacity.
- Current-liquidity and event-time gates, with no forced trade.
- A material buying-power reserve and internal liquidation threshold materially inside the official boundary.
- Final-quarter de-risking when positive; late catch-up risk comes only from the original unused sleeve budget.
- Official PnL and executable shadow PnL displayed together.

Why: pure playbook A has the strongest extreme tail but too much total-premium and fill-model risk; pure playbook B has the strongest survival profile but may be too narrow-tailed for a short raw-PnL contest. The barbell retains a credible first-place path while making selection, execution and risk independently auditable. It does not guarantee positive PnL.

### Rules that reverse the choice

- **Pure first-place PnL, negligible judging and no drawdown barrier:** use playbook A with a larger but still frozen convex budget.
- **Sharpe/Sortino, low drawdown, survival or positive-return scoring dominates:** use playbook B and remove or sharply reduce lumpy options.
- **Unknown/extendable end date:** prefer B/C equities and prohibit near-expiry event bets.
- **Delayed/no OPRA, no atomic combo, midpoint marks or simulator size exploits:** disable options and run equity-only B/C.
- **Technology is most of the score:** favor C and prioritize replay, lineage, gate proofs and incident drills.
- **Very tight leverage/drawdown:** reduce gross and premium mechanically; if no position fits with buffer, do not trade it.
- **Unequal capital or raw-dollar score:** demand normalization; otherwise capital allocation may dominate strategy quality.
- **Stale terminal marks:** voluntarily realize before cutoff and disclose any leaderboard disadvantage rather than exploit the mark.

## 11. Questions organizers must answer

1. What is the exact score formula, weight of raw PnL, percentage, risk metrics and judging, tie-break and prize distribution?
2. Is PnL realized, marked or both? Are options marked at last, midpoint, bid/ask or theoretical value, and how are stale quotes handled?
3. What are starting capital, start/end timestamps and timezone, fixed versus surprise stop, overnight/weekend treatment and mandatory closeout?
4. Are accounts equal, and are deposits, withdrawals, resets, interest and cash yield prohibited or normalized?
5. Which stocks, ETFs, shorts and option strategies are allowed? Are zero-DTE, multi-leg, exercise and assignment supported?
6. Which simulator and feeds are official? Are equities consolidated or single-venue? Is OPRA current? What are entitlements and latency?
7. How are queue position, displayed size, partial fills, improvement, impact, auctions, halts and cancel/fill races modeled?
8. Which order types and time-in-force values exist? Are option combinations atomic? What are API limits and outage policies?
9. Which commissions, exchange/regulatory fees, option fees, borrow fees, dividends, splits, corporate actions and locate failures enter PnL?
10. What leverage, buying-power and intraday-margin formulas apply? How are open orders, exercise, assignment and margin calls treated?
11. Is there a daily-loss or drawdown limit? Is it intraday or close-to-close, high-water or initial-equity based, and does breach disable, liquidate or disqualify?
12. Is the leaderboard visible, live or delayed? May algorithms consume rank, and must adaptive policies be preregistered?
13. What counts as autonomous? Which manual vetoes, emergency flattening and configuration changes are allowed and how are they audited?
14. What anti-gaming rules cover resets, impossible-size fills, stale marks, quote glitches, event-data timing and post-hoc changes?
15. What judging artifacts are required: source, container, replay, logs, architecture, tests, ablations, security review, demo or report?
16. How many teams are expected, is reward winner-take-all or rank-graded, and will benchmark results be published?

Until these are answered, the correct runtime state is **data collection and dry-run, entries disabled**.

---

## Ordered source manifest

1. [Paper Trading](https://docs.alpaca.markets/docs/paper-trading)
   - Date: Page displayed 'Updated about 2 months ago' when accessed 2026-08-30
   - Used for: Concrete evidence that a paper simulator can omit market impact, latency, queue position and fees, and can fill beyond displayed NBBO size; used as a warning, not as an assumption about the hackathon platform.
2. [Frequent Intraday Trading: Understanding the Basics](https://www.finra.org/investors/investing/investment-products/stocks/day-trading)
   - Date: 2026-06-04
   - Used for: Grounds the distinction among settled-cash constraints, intraday margin, minimum equity and broker house requirements.
3. [An Introduction to Options – Investor Bulletin](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-63)
   - Date: Published 2015-03-18; updated 2026-07-16
   - Used for: Grounds total-premium loss for option holders and potentially unlimited loss for some option writers.
4. [Options Price Reporting Authority (OPRA)](https://www.opraplan.com)
   - Date: Undated; accessed 2026-08-30
   - Used for: Grounds consolidated listed-options last-sale and quotation data and the distinction between current and delayed data.
5. [Limit Up–Limit Down Plan](https://www.luldplan.com)
   - Date: Permanent plan approved 2019-04-11; accessed 2026-08-30
   - Used for: Grounds price-band, limit-state and trading-pause risk in catalyst names.
6. [EDGAR Application Programming Interfaces (APIs)](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
   - Date: 2024-06-06; page displayed last reviewed/updated 2025-04-08
   - Used for: Grounds real-time, unauthenticated SEC submissions/XBRL JSON access for filing-event ingestion.
7. [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
   - Date: 2015-02-27
   - Used for: Grounds the risk that selecting the best of many investment backtests overfits historical data and supports trial disclosure.
8. [The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
   - Date: 2014-07-31
   - Used for: Grounds correction for multiple testing and non-normal returns and the need to disclose the number of trials.
9. [Of Tournaments and Temptations: An Analysis of Managerial Incentives in the Mutual Fund Industry](https://api.crossref.org/works/10.1111%2Fj.1540-6261.1996.tb05203.x)
   - Date: 1996-03
   - Used for: Empirical analogy for relative-performance tournament incentives: lagging managers increased later-period volatility more than leaders; not treated as a hackathon effect estimate.
