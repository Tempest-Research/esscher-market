# Clean-room quantitative firm analysis

**Status:** independent research input; not implementation authority
**Role simulation:** Chief quantitative researcher
**Context boundary:** neutral autonomous U.S. equities/options PAPER-trading hackathon brief only; no Esscher repository, current plan, issue graph, or prior worker conclusions supplied

## Preferred approach

> Systematic, event-driven post-earnings information underreaction in liquid U.S. common stocks: point-in-time numeric/guidance/text/revision ranking, diversified sector- and beta-controlled long-short shares by default, with long post-event options only as a conditional high-variance tournament expression.

## Explicit assumptions

- This is a PAPER-trading research design only; no broker calls, credentials, live trades, publication, or claim of guaranteed profitability is authorized.
- The contest lasts roughly 20-60 U.S. trading sessions and overlaps enough quarterly earnings announcements to form a cross-section; shorter or longer windows trigger the stated pivots.
- Starting capital is small enough that each equity order can remain below 1% of 20-day ADV and 5% of expected execution-window volume; all sizing is expressed as a percentage of NAV because capital is unknown.
- Regular-session trading and overnight holdings are permitted; short sales, ETFs, margin, and single-stock options are uncertain and therefore conditional rather than assumed.
- The likely primary score is legitimate end-of-contest PnL, but whether it is raw dollars, percentage return, realized/marked, risk-adjusted, or combined with technology judging is unknown and decisive.
- Fills must be modeled from executable NBBO plus latency, spread, impact, fees, borrow, and option mechanics; simulator defects or stale/midpoint fills will not be exploited.
- The desk can obtain legally usable point-in-time security-master, estimates/guidance, document/transcript, equity quote, corporate-action, borrow, and—if needed—OPRA/options data.
- Enough historical data exist for modern nested walk-forward tests and a locked 2024-2025 holdout, and ideally at least 30 prospective sessions or 100 independent events are available before full paper risk.
- The scientific default is diversified common shares; the concentrated long-option variant is used only if options are allowed, quotes are realistic, raw PnL is the objective, and the team accepts a materially lower probability of a good outcome.
- Any strategy whose modern after-cost holdout, prospective fills, data lineage, or operational reconciliation fails the stated gates is disabled; abstention is an acceptable outcome.

---

# Independent blank-slate quantitative research recommendation

## Decision in one sentence

Build a **systematic, post-earnings information-underreaction strategy in liquid U.S. common stocks**: rank every eligible event with point-in-time numeric surprise, guidance, document/call-text surprise, subsequent analyst revisions, and an executable initial-price response; trade the strongest positive and negative residual-return forecasts in shares; keep the main portfolio diversified and sector/beta neutral; and reserve long, post-event single-stock options for an explicitly higher-variance tournament variant.

This is not a promise of profitability. It is the research candidate I would fund first. A strategy does not graduate because its story is attractive or its backtest is green; it graduates only if a modern, point-in-time, after-cost, untouched holdout and prospective paper run survive the falsification gates below.

### Evidence labels

- **FACT** means a claim directly supported by a cited public source.
- **HYPOTHESIS** means a plausible economic or statistical edge that still has to survive our tests.
- **TACTIC** means a contest-specific choice that may raise leaderboard upside but is not evidence of alpha.
- **CONTROL** means an execution, data-quality, or risk requirement.

## 1. Preferred strategy and why

### The choice

- **Selection:** systematic, not hand-picked. The only hybrid element is an audited text-extraction/quality-control layer; a person may veto corrupt data but may not insert a favorite stock.
- **Primary instrument:** common shares. Options are a secondary expression only after an event, only in the high-variance variant, and only when executable quotes pass strict filters.
- **Family:** event-driven, cross-sectional relative value around quarterly earnings, not generic market direction.
- **Expression:** directional at the stock level, but normally dollar-, beta-, and sector-controlled at the portfolio level.
- **Concentration:** moderate diversification in the production candidate; concentration is confined to the stated tournament tactic.
- **Holding period:** roughly 5–20 trading days, aligned to the tested decay curve and shortened if the contest ends sooner.

### Economic edge

**FACT:** Classic post-earnings-announcement drift is not a safe premise by itself. Ng, Rusticus, and Verdi found that conventional PEAD returns were concentrated in high-transaction-cost securities and that implied trading costs exceeded strategy returns [1]. A later manuscript argues that classic PEAD in large stocks has been absent since 2006 [2]. A broad modern anomaly study estimates that publication decay and effective spreads reduce most anomaly profits to economically tiny levels [10].

**FACT:** There is nevertheless more recent evidence that the information *inside* earnings communications matters beyond the headline EPS number. Ke, Kelly, and Xiu construct an out-of-sample, regularized text surprise from earnings calls and report a text-based drift larger than classic PEAD in 2010–2019, including periods when classic PEAD was near zero [3]. Novy-Marx reports that earnings momentum explains much of price momentum and that controlling price momentum for earnings surprises reduces return without reducing volatility [4]. These are research findings, not proof of an executable 2026 edge; the PEAD.txt result is an academic gross-return result and must be retested with our data, timing, liquidity, and costs.

**HYPOTHESIS:** Prices rapidly absorb the simple EPS headline but sometimes underreact to a multidimensional, harder-to-compress package: revenue composition, margins, changed guidance, one-off versus recurring drivers, management language, and the breadth of later estimate revisions. The expected edge is therefore not “buy beats, short misses.” It is **trade only unusually coherent, economically material information that remains predictive after the immediately executable reaction and all costs**. Limited attention, slow analyst updating, mandate constraints, and model disagreement are possible mechanisms. A risk-premium explanation is also possible; the tests must not assume mispricing.

### Why this candidate beats the other blank-slate choices

1. **Known timestamps reduce ambiguity.** Earnings releases, 8-K exhibits, and calls create auditable information clocks. That makes leakage tests and realistic execution much cleaner than for loosely timestamped alternative data.
2. **It fits a short hackathon.** Earnings create recurring catalysts and faster feedback than a quarterly rebalanced value factor. If the event calendar is sparse, the momentum alternative below becomes preferable.
3. **It has a credible technology story without pretending complexity is alpha.** The system can demonstrate bitemporal data, source-linked document extraction, model/version lineage, independent risk, execution replay, and broker reconciliation.
4. **Shares remove one unnecessary model.** With options, direction, realized volatility, implied volatility, surface dynamics, spread, Greeks, assignment, and exercise all matter. Shares let us test the information edge first.
5. **Systematic selection prevents narrative overfitting.** No stock is selected because it is famous, exciting, or recently profitable. Every candidate passes the same point-in-time rules.
6. **Abstention is natural.** Event density varies. Cash is an admissible position when signal confidence, liquidity, or data lineage is inadequate.

## 2. Two credible alternatives

### Alternative A — sector-neutral fundamental and residual momentum

Use the same liquid-stock universe but rank weekly on three predeclared blocks: 50% earnings/fundamental momentum (recent standardized earnings surprises and forward-estimate revisions), 25% 12-minus-1-month stock momentum residualized against market and industry, and 25% quality/financial-strength confirmation. Hold 4–12 weeks, long the top decile and short the bottom decile, with beta, sector, volatility, borrow, and turnover constraints.

- **Economic rationale:** gradual information diffusion, analyst herding, and slow-moving institutional demand. Novy-Marx's evidence makes earnings momentum preferable to price momentum alone [4].
- **Why it is credible:** many more positions, less dependence on a single event parser, and a longer documented history.
- **Why it is second:** generic momentum is crowded and regime-sensitive. Daniel and Moskowitz document momentum crashes during sharp market rebounds after bear states [5]. A short tournament may also end before the signal realizes.
- **When it becomes first:** contest duration above roughly three months, weak earnings-event density, no reliable real-time document/estimate feed, or rules that reward risk-adjusted return rather than raw short-horizon PnL.

### Alternative B — diversified, defined-risk earnings volatility premium

For optionable names only, estimate the earnings-event variance embedded in the term structure, compare it with a hierarchical forecast of realized event variance, and sell a fully collateralized iron fly/iron condor only when implied event variance exceeds the conservative forecast by more than spreads, fees, and a tail reserve. Buy wings outside roughly 1.5 times the option-implied move; cap maximum loss per event; diversify by sector and announcement date; close the morning after the report.

- **Economic rationale:** investors may pay for event insurance and gamma. Earnings uncertainty is quantitatively important in option prices and informative about future realized volatility [8]. Research finds higher volatility risk premiums for some later reporters [7], and recent evidence links concave pre-earnings implied-volatility curves to event risk and negative returns on delta-neutral option positions, consistent with a premium paid by option buyers [9].
- **Why it is credible:** scheduled event resolution, many independent events, and an explicit implied-versus-forecast variance comparison.
- **Why it is second, not preferred:** short-volatility returns are negatively skewed; rare gaps dominate; option execution is easy to overstate; paper simulators often ignore queue position and market impact [19]. Option-cost measurement materially changes inferred returns [6]. A high percentage of profitable trades is not the same as a high probability of winning the contest or avoiding a ruinous tail.
- **When it becomes first:** rules reward hit rate or risk-adjusted PnL, the desk has full OPRA/OptionMetrics history and conservative fill simulation, naked short options are unnecessary because defined-risk spreads are permitted, and the event-variance model passes tail stress tests.

## 3. Exact universe and instrument-selection method

### Point-in-time equity universe, rebuilt before every decision

A security is eligible only if all conditions were knowable at the decision timestamp:

1. Primary listing on NYSE, Nasdaq, or NYSE American; U.S. common share. Exclude ETFs, ETNs, mutual/closed-end funds, preferreds, warrants, rights, units, ADRs, SPAC units, and securities in bankruptcy or a pending cash acquisition.
2. Unadjusted prior close at least $5; float-adjusted market capitalization at least $750 million; at least 126 trading days since IPO or de-SPAC.
3. Twenty-day median daily dollar volume at least $25 million; sixty-day median quoted spread no more than 25 basis points during regular hours; no active halt or limit-state anomaly.
4. A proposed parent order must be no more than 1% of trailing 20-day ADV and no more than 5% of expected volume in its execution window. If the capital base makes those limits binding, leave cash rather than broaden into illiquidity.
5. For shorts: a current locate/borrow indication, annualized fee no more than 5%, short interest below 20% of float, and no broker hard-to-borrow flag. If broker borrow truth is unavailable, the short is ineligible; do not assume paper availability equals live borrowability.
6. Resolve tickers through permanent identifiers and point-in-time mappings. Include inactive and delisted securities in research. CRSP explicitly describes coverage of active and inactive U.S. securities and permanent identifiers [15].
7. Exclude unresolved splits, symbol changes, stale corporate-action records, missing primary-source timestamps, or contradictory release times. Financials, REITs, and other specialized industries may remain only if their sector-specific numeric fields and consensus definitions pass a separate schema; otherwise abstain rather than compare noncomparable margins.

### Event eligibility

1. A quarterly earnings release or 8-K exhibit must be public and timestamped by a primary source; SEC/IR timestamps are stored alongside both `event_time` and `known_at`. SEC's EDGAR APIs expose submissions/XBRL data and update during the day, but filing availability still has to be captured by our own clock [14].
2. Use the last consensus snapshot whose vendor timestamp is strictly before the release. Require at least four contributing analyst estimates for the headline EPS and revenue comparison.
3. The release must occur after the prior regular close, before the current regular open, or during the session with an unambiguous publication time. Rumors and scraped calendar predictions are not events.
4. Every numeric/text field must retain a source URL, document hash, publication timestamp, unit, GAAP/non-GAAP label, and exact quote span. Unresolved unit or period mismatches make the event ineligible.
5. No new position when a merger vote, FDA decision, court ruling, or other binary non-earnings catalyst overlaps the planned holding window unless a separately validated model explicitly handles it.

### Primary instrument

Trade the common shares. A negative score is expressed by a short only when borrow passes the live eligibility checks. If shorting is prohibited, trade long positive events and hedge permitted market/sector ETFs; if neither shorts nor hedges are allowed, hold cash for negative signals rather than reinterpret them as longs.

### Option overlay eligibility

Options are eligible only for the high-variance variant or the separate volatility strategy, and only if:

- underlying 20-day median dollar volume is at least $100 million and price at least $10;
- both legs have 21–45 calendar days to expiry, open interest at least 500 contracts, prior-day volume at least 100 contracts, valid OPRA NBBO, and no crossed/stale market;
- each leg's quoted width is no more than the lesser of $0.15 or 8% of mid, with enough recent traded/displayed size for the order;
- the order is no more than 10% of the contract's recent intraday volume;
- there is no ex-dividend/assignment conflict before planned close, and the position is closed at least five days before expiry;
- the event is over before a directional option is bought; no pre-earnings long option is mislabeled as a drift trade;
- executable OPRA quotes, rates, dividends, corporate actions, and a defensible volatility surface are available. Databento describes OPRA as consolidated U.S. options trades and quotes [18], while OptionMetrics offers historical end-of-day and intraday U.S. option data suitable for research [17].

No current ticker belongs in this report. A hand-picked list without an as-of market/consensus/borrow snapshot would be stale, nonreproducible, and contrary to the selection rule.

## 4. Signal construction, ranking, sizing, entries, exits, costs, liquidity, regimes, and abstention

### Information clock and feature blocks

All features are computed at a specific `decision_time`; later revisions never overwrite the earlier vintage. Each raw feature is winsorized using trailing training data and converted to a signed industry-relative percentile in `[-1,+1]`.

- **N — numeric surprise (30% seed weight):** robust average of EPS surprise, revenue surprise, and industry-relevant operating/margin surprise. Compare reported values with the last pre-release consensus; scale by pre-event price, historical surprise dispersion, or consensus dispersion as predeclared. Keep GAAP and adjusted measures separate.
- **G — guidance surprise (25%):** compare the midpoint and range of new management guidance with both the previous guide and point-in-time consensus for the same fiscal period. Record withdrawals separately; do not turn “not provided” into zero.
- **T — text surprise (20%):** a regularized model applied only to the public release/call text available by decision time, purged of boilerplate and trained solely on earlier events. It should capture drivers, persistence, uncertainty, and changes relative to the prior quarter. The PEAD.txt study used rolling out-of-sample regularized predictions, which is the relevant methodological precedent [3]; it does not justify reusing its coefficients.
- **R — analyst revision confirmation (15%):** breadth and magnitude of next-quarter and next-twelve-month EPS/revenue revisions posted after the event. This block enters only after those revisions are actually timestamped, usually as a later add-on decision.
- **M — executable market confirmation (10%):** sector- and market-residual return from public release to the proposed execution window, interacted with abnormal volume and capped so that the price move cannot dominate the fundamental blocks. Extremely large gaps are penalized because the remaining drift-to-risk ratio may be poor.

The transparent seed score is `S = 0.30N + 0.25G + 0.20T + 0.15R + 0.10M`. Before revisions exist, use the predeclared available blocks and normalize their weights; never backfill `R`. Missing required data is generally a reason to abstain, not a model feature.

### Statistical model and candidate ranking

1. Maintain two challengers: (a) the transparent rank composite and (b) an ensemble of elastic-net and shallow monotone gradient-boosted models. Targets are 5- and 20-trading-day residual returns from the first executable price, net of market and industry exposures.
2. Models are re-estimated only on rolling prior data. The production prediction is the median across recent outer-fold fits; uncertainty is the dispersion across fits plus residual bootstrap uncertainty.
3. A candidate is tradable only if `|S| >= 0.60`, at least three available non-price components agree on sign, both model families agree on sign, and the lower confidence bound of forecast gross residual return exceeds twice the conservative all-in round-trip cost.
4. Rank eligible events by `(lower-confidence-bound residual return - stressed costs) / forecast residual expected shortfall`, not by raw model probability or headline surprise.
5. Select in rank order subject to portfolio constraints. If five names qualify, hold five; do not weaken thresholds to manufacture twenty.

These weights and cutoffs are a frozen starting hypothesis, not sacred constants. They may change only through the nested protocol and trial ledger; the locked holdout is opened once.

### Entry

- For an after-close or before-open release, compute only features that are public by 09:40 ET and execute from 09:45–10:00 ET using child limit orders. The 15-minute delay sacrifices some gross return to avoid fictitious opening fills and permit validation of halts, quotes, and document fields.
- For an intraday release, wait at least 15 minutes after verified publication and after any halt reopens.
- Enter 70% of target on the first qualified decision. Re-score on D+2 when revision data are available and add the remaining 30% only if direction, edge, and risk constraints still pass.
- Use marketable limit orders bounded by the current quote. Cancel rather than chase beyond the cost budget. Never fill at a stale pre-announcement price or assume the midpoint was executable.

### Exit

- Take half off after five trading days; close the rest at twenty trading days. Research can replace these horizons only before the holdout is opened.
- Exit earlier if the daily score falls inside `[-0.20,+0.20]`, a source-cited counter-event reverses the thesis, borrow becomes unavailable, risk constraints require reduction, or the position reaches a 2.5-residual-ATR catastrophic stop. A stop limits ordinary loss but cannot prevent a gap.
- Close before the next earnings release, before option expiry/ex-dividend conflicts, and before the contest marking deadline if only realized PnL counts.
- Do not average down. Re-entry requires a new independently timestamped signal.

### Main-portfolio sizing

- Initial uncapped weight is `0.30% of NAV / stop_distance_pct`; cap absolute name weight at 4%.
- Optimize the accepted names with a shrinkage covariance matrix and explicit turnover cost. Base constraints: gross exposure no more than 100% NAV, net exposure within ±5%, predicted market beta within ±0.05, GICS sector net within ±5%, sector gross no more than 25%, and one-day 97.5% expected shortfall no more than 1.25% NAV.
- Cap daily turnover at 35% NAV and preserve the ADV/execution-window participation limits. Cash is the residual.
- Do not call this “risk-free market neutral.” Earnings gaps, factor-model error, crowding, and short recalls remain.

### Transaction-cost and liquidity model

**Equities:** Reconstruct NBBO at order-arrival time. A marketable buy starts at ask and a sale at bid; add commissions/regulatory fees, measured latency slippage, borrow, and an impact function calibrated from our own fills. Before calibration, use a conservative square-root participation model and stress its coefficient. Limit-order fills require subsequent quote/trade evidence and a queue haircut; “touched midpoint” is not a fill. Include delisting returns and corporate actions.

**Options:** Mark and transact at a liquidation-aware bid/ask, not an unconditionally available midpoint. Include per-contract fees, spread, surface/Greek error, assignment/exercise, dividends, and legging risk. Option-cost research shows that crude half-spread assumptions can be badly biased and that cost treatment changes estimated strategy returns [6]. Any option backtest profitable only at midpoint is rejected.

**Capacity:** Report PnL at multiple capital levels. If a target order breaches 1% ADV, 5% execution-window volume, or option-volume limits, scale down or abstain. Do not quietly substitute less-liquid names because they have larger paper alpha; PEAD historically concentrated where costs were highest [1].

### Regime dependence and abstention

- **Sharp bear-market rebound:** halve gross and disable the generic residual-momentum overlay; momentum crashes are especially associated with rebound states [5]. Event positions may remain only if their idiosyncratic lower-bound edge survives.
- **Market shock/correlation spike:** if market volatility or average cross-sectional correlation exceeds its trailing five-year 90th percentile, halve new risk and tighten beta/sector limits. A regime classifier scales risk; it does not invent alpha.
- **Scheduled macro collision:** do not open during a market-wide FOMC/CPI shock window unless the backtest explicitly includes that interaction.
- **Data stress:** no new orders if quotes, corporate actions, broker state, release timestamps, model artifact, or source documents are stale/unreconciled.
- **Signal scarcity:** abstain when fewer than the qualification rules pass. A zero-trade day is valid.
- **Model decay:** disable entries if rolling realized information coefficient is nonpositive over the predeclared monitoring window, live slippage exceeds the model by more than 50%, or feature distributions breach drift limits.
- **Crowding/borrow:** abstain from squeeze-prone shorts and names whose borrow cannot be verified.

## 5. Highest-raw-PnL tournament variant versus highest-win-probability variant

### Highest raw-PnL/right-tail variant — concentrated long convexity after the event

**TACTIC, not a claim of higher expected risk-adjusted return.** Use only if rules clearly rank absolute PnL, do not penalize drawdown, permit options, and mark them fairly.

- Take the top three to five non-overlapping event scores.
- After the earnings event and initial IV reset, buy 30–45 DTE calls for positive signals or puts for negative signals with delta between 0.45 and 0.60. Use a debit vertical instead when remaining implied volatility is expensive or the vertical has a lower tested break-even; never sell naked options.
- Risk 6–8% of NAV in premium per name, total premium at risk no more than 30% of NAV, sector premium no more than 15%, and stop opening new positions after a 12% peak-to-trough contest drawdown. These are deliberately aggressive paper-tournament limits, not a live-money recommendation.
- Close by D+10 or when the event score decays; mark at executable liquidation quotes. If options are prohibited, use at most 150% gross shares across the top six events, with 15% name caps and the same liquidity controls.

Why it can win: leaderboard rank is convex in outcome when only the top result matters; concentration and long options create a large right tail while predefining maximum premium loss. Why it can fail: the most likely result may be lower than the diversified strategy because spreads, theta, forecast error, and concentration dominate. It is rational only as a transparent tournament objective, never as disguised scientific evidence.

### Highest probability of positive, defensible performance — diversified share portfolio

- Use shares only; 20–40 positions when that many qualify, otherwise cash.
- Name cap 3%; gross 80–100%; net within ±3%; beta within ±0.03; sector net within ±4%; one-day expected-shortfall cap 0.75% NAV.
- Require both transparent and statistical models to agree, lower position risk on shorts, and skip options/volatility forecasts.
- Stage entries, hold 5–20 days, and enforce cost/borrow/staleness rules exactly.

This variant should have the highest *scientific and operational* probability of a positive result among the proposals, but it may have a lower chance of finishing first on a pure raw-dollar leaderboard populated by similarly skilled entrants. Exact scoring rules decide which objective is rational.

## 6. Falsification tests and hard go/no-go gates

The research program must try to kill the strategy:

1. **Timestamp leakage:** shift every document, consensus, revision, transcript, and corporate action to the first time actually observable. Re-run with an extra one-session lag. Any “alpha” requiring a pre-publication or post-close price is leakage.
2. **Vintage reconstruction:** compare archived pre-event consensus snapshots with the vendor's current history. If revisions overwrite vintages or surprise signs change materially, reject that feed or the strategy.
3. **Survivorship/identifier test:** include inactive/delisted names, old tickers, cash/stock mergers, splits, and delisting returns. Reconcile a random sample against primary filings and price tapes.
4. **Modern-era test:** report 2010–2015, 2016–2019, 2020–2023, and locked 2024–2025 separately. Classic SUE, text, guidance, and revisions must each show their own contribution. The claim that large-stock PEAD disappeared [2] makes recent performance decisive.
5. **Cost/latency stress:** test executable side of NBBO, measured order latency, borrow, impact, and 1x/2x/3x cost assumptions. Reject options that work only at midpoint and equities that fail at 2x baseline costs.
6. **Placebos:** randomize surprise labels within industry and event week; assign “ghost earnings” dates; test pre-event windows where no public information exists. Placebos should not resemble the strategy.
7. **Ablation and horse race:** compare numeric-only, price-reaction-only, guidance-only, text-only, revisions-only, the transparent composite, and the model ensemble. The LLM/text layer ships only if it adds recent, after-cost out-of-sample value.
8. **Concentration test:** remove the best ten trades, each sector, each calendar year, micro/smallest eligible cap bucket, and the short book in turn. Reject a result whose thesis depends on one year, one sector, or a handful of gaps.
9. **Specification stability:** vary sensible event clocks, scalers, neutralization models, and holding horizons. Coefficient signs and rank information coefficients should be stable; a single narrow optimum is evidence of overfit.
10. **Multiple-testing control:** log every tried variant, report deflated Sharpe and family-wise false-discovery controls. Harvey, Liu, and Zhu argue a new factor needs a substantially higher hurdle than a conventional t-statistic [11]; the deflated Sharpe explicitly addresses selection bias, non-normality, and repeated trials [12].
11. **Prospective replay:** freeze code/config/model, run at least 30 trading sessions or 100 independent qualified events, and compare decision-time forecasts, simulated orders, broker paper fills, and independent marks. A broker's paper result is not enough because simulators can omit market impact, queue position, latency slippage, and other frictions [19].
12. **Operational chaos:** replay duplicate/out-of-order documents, stale quotes, partial fills, cancel/fill races, reconnects, option symbol changes, and broker/local position divergence. Strategy PnL is invalid if order state cannot be reconciled.

### Minimum graduation gates

Do not deploy the preferred strategy at full paper risk unless all are true:

- positive net residual return in at least 60% of outer walk-forward folds and positive aggregate return in the locked modern holdout;
- a 90% block-bootstrap lower confidence bound above zero for the aggregate after-cost mean, or an explicitly documented decision to run only the low-risk experiment because sample power is inadequate;
- nonnegative PnL under 2x baseline costs and no dependence on midpoint option fills;
- no single year or best ten trades contribute more than 35% of total PnL;
- the recent holdout preserves signal direction and material rank IC;
- prospective slippage is within 25% of the conservative model and broker/local reconciliation is exact;
- the text extractor passes its separate golden-set thresholds.

Failure means simplify, lower risk, switch to an alternative, or abstain—not tune on the holdout.

## 7. Required data, research protocol, LLM/statistical-model policy, and technology

### Data a real small desk should license or build

1. **Security master and total returns:** CRSP-class survivorship-free research data or a commercial equivalent with permanent IDs, inactive securities, delisting returns, distributions, splits, and exchange/share codes [15].
2. **Fundamentals and estimates:** point-in-time Compustat plus LSEG I/B/E/S Detail/Summary History and Guidance, or licensed equivalents. WRDS lists I/B/E/S detail, summary history, actuals, and guidance products [16]. Store raw snapshots; do not trust a vendor's latest consensus as historical truth.
3. **Primary documents:** SEC EDGAR submissions/company facts and archived issuer releases/8-K exhibits, captured with publication time and hash [14]. Use a licensed transcript feed with legally permitted automated use; never backfill a transcript earlier than its availability.
4. **Equity execution data:** consolidated quotes/trades or TAQ-quality history, opening/closing auctions, halts, corrections, corporate actions, and an exchange calendar.
5. **Options:** OptionMetrics for cleaned historical chains/surfaces and OPRA tick/NBBO for executable simulation [17][18].
6. **Borrow and fees:** broker securities-lending/locate snapshots, fee schedule, margin rules, and recalls. Paper short availability is not sufficient.
7. **Macro/risk:** sector ETFs/factors, rates, dividends, market calendar, scheduled macro events, and volatility/correlation inputs.

Every dataset needs license review for hackathon use. “Publicly viewable” is not the same as licensed for bulk automated trading research.

### Point-in-time backtest and walk-forward design

- Use append-only bitemporal tables with `event_time`, `known_at`, `ingested_at`, vendor revision ID, and payload hash. Feature code queries `known_at <= decision_time`.
- Build the universe from contemporaneous classifications and inactive securities. Apply corporate actions exactly once. Price decisions at the first executable quote after the modeled computation/order latency.
- Keep labels separate from features. Purge at least the maximum 20-trading-day holding horizon around fold boundaries and embargo overlapping events from the same issuer.
- For text-era research, use six years of trailing training data; reserve the last year inside that window for time-ordered tuning; test the next six months; roll six months. Compare with shorter windows as a prespecified robustness test, not a hunt for the best curve.
- Keep 2024–2025 untouched while designing on earlier outer folds; open that holdout once. Treat 2026 data, if available before the contest, as prospective shadow data rather than another tuning set.
- Cap the experiment budget by family before running it. Save every parameter set and failed result. Use event-week block bootstraps, date/firm-aware uncertainty, turnover/capacity curves, and a deflated Sharpe rather than random cross-validation [11][12].
- Report gross and net returns, residual IC, hit rate, payoff ratio, turnover, max drawdown, expected shortfall, beta/sector drift, long/short attribution, borrow rejection rate, capacity, and PnL by year/sector/cap/liquidity/regime.
- Run a frozen prospective shadow/paper phase before increasing risk. Compare independent executable marks with broker marks and preserve every signal-to-fill trace.

### How an LLM should be used

Use an LLM as a **source-grounded extraction and classification component**, not as the portfolio manager:

- Inputs are only documents public by decision time. Output is strict JSON: values, units, periods, GAAP status, guidance direction/range, persistence/one-off tags, uncertainty, exact quote spans, source hash, and confidence.
- Require deterministic schema validation and arithmetic/unit checks against XBRL and consensus fields. Any unsupported field is `null`, never guessed.
- Build a golden set of at least 500 stratified events including amended filings, negation, withdrawn guidance, mixed GAAP/non-GAAP language, tables, unit changes, and adversarial boilerplate. Required gates: 100% schema validity, at least 99.5% exact accuracy on order-affecting numeric fields, at least 0.90 macro-F1 for guidance direction, at least 99% valid quote-span support, and zero uncited numeric inventions in the test set.
- Freeze provider/model version, prompt, temperature, parser, and retrieval corpus; log latency/cost and regression-test every change. Textual-analysis reviews emphasize domain-specific language and measurement ambiguity [13].
- Use the LLM to flag unusual documents for human/data-quality review and to generate research code only behind tests. It must not bypass risk or execution.

### How an LLM should not be used

- Do not ask an open-ended model “will this stock go up?” and turn prose confidence into size.
- Do not let it browse untimestamped summaries, social posts, later transcripts, or analyst notes that can leak the label.
- Do not let it calculate option Greeks, PnL, units, or exposure without deterministic recomputation.
- Do not fine-tune or prompt-select on the locked holdout, use an LLM judge as the sole evaluator, or treat a persuasive explanation as causal evidence.
- Do not allow autonomous order submission. The deterministic statistical ranker proposes; independent risk approves; OMS executes; reconciliation verifies.

### Classical statistical model policy

Start with ranks, elastic net, and a shallow monotone tree ensemble. They can estimate nonlinear interactions while retaining enough stability for diagnostics. Calibrate predictions to after-cost residual returns and uncertainty; shrink aggressively; ensemble across rolling fits. Deep learning or reinforcement learning has no presumption of superiority here and is rejected unless it beats transparent baselines in the locked and prospective tests with the same trial budget. The model is a candidate ranker, not a generator of certainty.

### Minimal but convincing technology

- **Research plane:** Python, Polars/Arrow, DuckDB over versioned Parquet in object storage, Jupyter for exploration, scikit-learn/LightGBM/statsmodels, cvxpy, MLflow for artifacts/trials, and Git plus containerized reproducibility.
- **Data plane:** append-only raw lake; PostgreSQL for bitemporal metadata, security master, strategy state, and durable event/outbox records; Dagster or Prefect for idempotent scheduled ingestion; Pandera/Great Expectations data contracts.
- **Trading plane:** separate services for market/document ingestion, feature generation, signal ranking, independent pre-trade risk, OMS, paper-broker adapter, position/fill reconciler, and independent marking/PnL. Strategy code cannot call the broker directly.
- **Controls:** environment-locked paper credentials only; idempotency keys; max order/name/sector/gross/net/beta/expected-shortfall controls; stale-data and duplicate-event lockouts; entry-disable and cancel-all controls; explicit liquidation mode; immutable config/model hash attached to every decision.
- **Truth and observability:** broker/exchange state repairs local state; acknowledgments are not fills; reconcile positions/orders/fills at startup, continuously, and end of day. Prometheus/Grafana dashboards show feed freshness, signal lineage, order lifecycle, exposure, independent versus broker PnL, slippage, and kill-switch state. Structured logs carry one correlation ID from source document through decision, risk verdict, order, fill, and mark.
- **Verification:** deterministic market replay, property tests for order-state transitions, golden documents, cost-model tests, partial-fill/cancel race tests, and a one-command contest-day dry run. The implementation should impress through auditability and truthful fills, not microservice count.

## 8. Key rule-dependent pivots

1. **Score definition:** if the leaderboard is raw dollars with no risk penalty, the long-option concentrated tactic is rationally more competitive; if it is return, Sharpe, drawdown, or composite judging, use the diversified share strategy. If capital differs by team, raw dollars primarily measure capital unless normalized.
2. **Contest length:** under five trading days, use only immediately executable post-event continuation and do not expect a 20-day drift; 2–8 weeks fits the preferred strategy; above roughly three months favors diversified fundamental/residual momentum.
3. **Marking:** if only realized PnL counts, flatten before deadline; if positions are marked, require the official liquidation convention. Options expiring after the contest are unusable if marks are stale or midpoint based.
4. **Shorting and margin:** with shorts and ETFs, run relative value; without shorts, use long-only positives and cash/allowed hedges. Without margin, rescale rather than weaken liquidity.
5. **Options:** if options, multi-leg orders, or short options are forbidden, drop the overlay/VRP alternative. If options are allowed but the simulator has unrealistic fills, do not exploit them; that would not be legitimate recorded PnL.
6. **Trading hours/latency:** if after-hours fills are permitted and accurately modeled, test them prospectively; otherwise use regular-session 09:45 execution. A latency race is not the chosen edge.
7. **Fees/borrow:** actual assessed costs replace estimates. If the paper broker omits borrow/fees, keep a parallel economically adjusted PnL and present both.
8. **Position/order limits:** tighter limits push toward more names and shares; permissive leverage plus raw-PnL scoring favors the convex tactic. Liquidity participation caps remain even if the rules omit them.
9. **Data entitlements:** without point-in-time consensus/transcripts, do not approximate them with current web pages. Fall back to the transparent price/fundamental momentum alternative or abstain.
10. **Technology judging:** if engineering quality is material, prioritize replay, lineage, risk separation, and reconciliation. Do not add opaque AI or fake low latency merely for spectacle.
11. **Start date versus earnings season:** a sparse calendar elevates the momentum alternative; a dense earnings season increases event diversification. The universe threshold never changes merely to force activity.
12. **Time to validate:** if fewer than 30 prospective sessions or 100 events are available, launch only the low-risk transparent baseline and label the result experimental.

## 9. Ranked next research experiments

Ranked by information value, not by how exciting the backtest may look:

1. **Modern point-in-time replication:** reproduce classic SUE, initial reaction, and PEAD.txt-style text surprise from first executable price on 2010–2025 data, with inactive securities and full costs. Decision: whether any preferred-strategy edge exists in the eligible universe.
2. **Leakage/vintage audit:** manually verify 200 random events across vendors, SEC/IR clocks, consensus vintages, transcript availability, units, and corporate actions. Decision: whether the dataset is fit for modeling at all.
3. **Incremental-information ablation:** add guidance, text, and analyst revisions one at a time to numeric surprise and price response; use a fixed trial budget and modern holdout. Decision: whether complexity earns its operational cost.
4. **Execution-timing frontier:** compare first regular auction, 09:35, 09:45, 10:00, close, and D+2 using real quotes, latency, spread, and impact. Decision: the net rather than gross entry clock.
5. **Holding/exit decay:** prespecify 1/5/10/20/40-day labels, staged exits, and score-decay exits. Decision: contest-compatible horizon and turnover.
6. **Portfolio/concentration study:** compare rank weights, inverse residual volatility, constrained mean-variance, and expected-shortfall sizing under identical signals. Decision: diversified production constraints and whether concentration adds only variance.
7. **Borrow and short-side reality:** reconstruct locate acceptance, fees, recalls, squeeze filters, and long-versus-short attribution. Decision: whether the short book is economic or should be replaced by ETF hedges/cash.
8. **Post-event option overlay:** compare shares, 45–60 delta options, and debit verticals using OPRA executable marks after the IV reset. Decision: whether options add net convexity or only paper slippage.
9. **Alternative A replication:** test earnings/fundamental plus residual momentum over longer holding periods with rebound-state risk scaling. Decision: fallback for sparse calendars/long contests.
10. **Alternative B event-VRP test:** estimate implied event variance versus hierarchical realized variance; include defined-risk wings, tail bootstraps, and full option costs. Decision: whether a diversified short-vol sleeve is real after crash reserve.
11. **Frozen prospective tournament rehearsal:** shadow-run the final candidates, reconcile broker versus independent PnL, inject failures, and conduct a blind rules-compliant replay. Decision: go/no-go and chosen variant.

## 10. Decisive unknowns

Resolve these before choosing leverage or claiming a likely winner:

1. Exact score: raw dollar PnL, percentage return, realized versus marked PnL, Sharpe/drawdown penalties, technology weight, and tie-breaks.
2. Equal starting capital, maximum gross/net leverage, margin interest, concentration limits, day-trade rules, and whether leaderboard capital changes are normalized.
3. Contest start/end, trading sessions, forced-close rule, and whether the window overlaps earnings season.
4. Allowed instruments: common shares, ETFs, short sales, single-stock options, multi-leg orders, after-hours trading, and option expiry/assignment treatment.
5. Paper broker and precise fill simulation: quote source, latency, partial fills, queue, price improvement, stale-quote rejection, borrow availability, fees, dividends, and option marks.
6. Data entitlements and redistribution/automation rights for consensus, transcripts, OPRA, historical NBBO, corporate actions, and borrow.
7. Order/API rate limits, supported order types, market-data timestamp resolution, and reliability/maintenance windows.
8. Required audit trail, source-code/demo judging rubric, cloud restrictions, model/vendor disclosure, and any ban on external AI services.
9. Time and sample available for a genuine prospective run before scoring begins.
10. Number and behavior of competitors. The “highest probability of winning” cannot be estimated without a field model; the report can only optimize conditional objectives.

## Bottom line

Fund the systematic earnings-information strategy first, but begin by trying to falsify it. Use shares and diversified relative-value constraints as the scientific default. The most aggressive legitimate tournament expression is concentrated **long** post-event optionality, not naked short volatility or pre-earnings gambling. If recent point-in-time net evidence fails, switch to fundamental/residual momentum or abstain; do not rescue the idea with looser filters, midpoint option fills, or an LLM narrative. The operational edge is as important as the statistical one: accurate clocks, source-linked extraction, executable prices, cost truth, independent risk, and reconciliation are what make a paper PnL record convincing rather than merely large.

---

## Ordered source manifest

1. [Implications of Transaction Costs for the Post-Earnings Announcement Drift](https://doi.org/10.1111/j.1475-679X.2008.00290.x)
   - Date: 2008-12-17 online
2. [Rest in Peace Post-Earnings Announcement Drift](https://doi.org/10.31235/osf.io/z7k3p)
   - Date: 2021-08-20 manuscript
3. [PEAD.txt: Post-Earnings-Announcement Drift Using Text](https://doi.org/10.1017/S0022109022001181)
   - Date: 2022-12-19 online; 2023 journal issue
4. [Fundamentally, Momentum is Fundamental Momentum](https://doi.org/10.3386/w20984)
   - Date: 2015-02 working paper; DOI posted 2015-03-05
5. [Momentum Crashes](https://doi.org/10.1016/j.jfineco.2015.12.002)
   - Date: 2016-01-26 online; 2016-07 issue
6. [Option Trading Costs Are Lower than You Think](https://doi.org/10.1093/rfs/hhaa010)
   - Date: 2020-02-11 online; 2021-03 issue
7. [Earnings announcement timing, uncertainty, and volatility risk premiums](https://doi.org/10.1002/fut.22150)
   - Date: 2020-07-15 online
8. [Option Pricing of Earnings Announcement Risks](https://doi.org/10.1093/rfs/hhy060)
   - Date: 2018-05-11 online
9. [Pricing event risk: evidence from concave implied volatility curves](https://doi.org/10.1093/rof/rfaf016)
   - Date: 2025-03-12 online
10. [Zeroing In on the Expected Returns of Anomalies](https://doi.org/10.1017/S0022109022000874)
   - Date: 2022-08-12 online; 2023 journal issue
11. [… and the Cross-Section of Expected Returns](https://doi.org/10.1093/rfs/hhv059)
   - Date: 2015-10-09 online; 2016 issue
12. [The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality](https://doi.org/10.3905/jpm.2014.40.5.094)
   - Date: 2014-09-30
13. [Textual Analysis in Finance](https://doi.org/10.1146/annurev-financial-012820-032249)
   - Date: 2020-11-01
14. [EDGAR Application Programming Interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
   - Date: Page undated; accessed 2026-08-30
15. [Center for Research in Security Prices, LLC (CRSP)](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/center-for-research-in-security-prices-crsp/)
   - Date: Page undated; accessed 2026-08-30
16. [LSEG data on WRDS (including I/B/E/S Detail, Summary History, Actuals, and Guidance)](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/lseg/)
   - Date: Page undated; accessed 2026-08-30
17. [OptionMetrics IvyDB US](https://optionmetrics.com/united-states/)
   - Date: Page undated; accessed 2026-08-30
18. [Databento OPRA.PILLAR dataset](https://databento.com/datasets/OPRA.PILLAR)
   - Date: Page undated; accessed 2026-08-30
19. [Alpaca Paper Trading](https://docs.alpaca.markets/docs/paper-trading)
   - Date: Page undated; accessed 2026-08-30
