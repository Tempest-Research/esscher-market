# Esscher v1: quant signal, data, and AI evidence design

Status: independent research memo; read-only; no broker/MCP calls or repository changes.

## Executive verdict

**VERDICT: PROCEED — with a deliberately narrow, evidence-first thesis and a hard historical-data gate.**

The scheduled-earnings residual-direction idea is testable and has a credible academic starting point: earnings surprises are associated with abnormal announcement returns, and market/sector adjustment is necessary because an earnings event is not the only information arriving at the open. However, the evidence does **not** justify a profitability claim, an options-alpha claim, or a learned-model claim by itself. V1 should be a reproducible shadow/PAPER research system whose primary estimand is underlying residual direction after the opening observation, with options treated as a later deterministic expression. If point-in-time consensus, release timestamps, synchronized bars, and option quote history cannot be licensed and reconstructed, revise the thesis to a primary-evidence-only event study rather than silently substituting revised data.

## Frozen scope taken from issues #26–#33

I read GitHub issues #26–#33. The relevant fixed decisions are:

- Universe: scheduled BMO/AMC earnings for supported US-listed, optionable common equities priced >= $10.
- Observe 09:30–09:35 America/New_York; signal deadline 09:36:05; no opening submission after 09:37.
- Predict residual underlying direction only: `UP|DOWN|UNCERTAIN`; UNCERTAIN means no trade.
- Model/reasoner cannot select contracts, quantity, prices, entries, exits, accounts, or invoke broker/MCP tools.
- Code computes reaction relation; prose cannot control arithmetic.
- P0 expression is one 7–21 DTE debit vertical, quantity one, selected deterministically later.
- Hold is 60 minutes from reconciled opening fill; no model exit, profit target, or stop loss; close by 15:30 ET.
- Permanently Alpaca PAPER-only; historical/prospective evidence gates precede any PAPER mutation.
- Issues #26/#27/#28 require preregistration, point-in-time snapshots, abstention, strict provenance, and no post-outcome tuning. #32 requires all events in the denominator, including abstentions and failures.

These constraints are treated as authority, not redesigned here.

## Evidence table

| Source | What it supports | Publication/version signal | Claim strength | Limitation / contradiction |
|---|---|---|---|---|
| Alpaca Market Data FAQ, https://docs.alpaca.markets/us/docs/market-data-faq | IEX versus SIP is materially different; historical `feed` must be explicit; subscriptions govern recent SIP access; latest endpoints preserve received data; symbol changes and inactive assets matter; option Greeks/IV can be absent | Page says updated 25 days ago at retrieval; exact page version should be captured in an implementation receipt | High for Alpaca behavior | FAQ is not a guarantee of universal historical availability or redistribution rights. Free IEX is not a consolidated US market proxy. |
| Alpaca historical stock bars, https://docs.alpaca.markets/reference/stockbars | Historical multi-symbol stock bars, pagination, explicit query parameters | API reference updated 3 months ago at retrieval | High for endpoint shape | Does not establish that a particular account has the required feed, history, or license. |
| Alpaca historical option bars, https://docs.alpaca.markets/reference/optionbars | Historical option bars endpoint and pagination behavior | API reference updated 3 months ago at retrieval | High for endpoint shape | Bars are not necessarily quote-level bid/ask/fill evidence; entitlement and contract-history coverage must be verified. |
| SEC Developer Resources, https://www.sec.gov/about/developer-resources | Submissions/XBRL APIs, RSS/index files, fair-access expectations and 10 requests/sec guideline | Current page retrieved in 2026; SEC page is operational documentation | High for access policy | EDGAR filing availability is not identical to the first instant an investor could have consumed an issuer release; do not call filing time an information timestamp without a defensible rule. |
| SEC EDGAR APIs, https://www.sec.gov/search-filings/edgar-application-programming-interfaces | data.sec.gov APIs are unauthenticated; submissions/XBRL update throughout the day; typical processing delays; bulk ZIPs republished nightly | Last Reviewed/Updated April 8, 2025 | High for API timing | XBRL facts can be re-presented/aggregated; use accession, acceptance timestamp, filing bytes and form context, not only a later facts snapshot. |
| Event Study Tools, “Earnings Announcements”, https://www.eventstudytools.com/earnings-announcements | SUE, short announcement windows, PEAD, estimation-window/event-study practice, liquidity and transaction-cost caveats | Educational synthesis citing Ball & Brown (1968), Foster et al. (1984), Bernard & Thomas (1989/1990), Kothari (2001), Chordia et al. (2009), Livnat & Mendenhall (2006) | Medium-high as synthesis; primary papers are stronger | Web page is not peer review itself; reported magnitudes must be checked against primary samples and modern periods. |
| Foster, Olsen & Shevlin (1984), https://www.jstor.org/stable/247321 | Time-series seasonal-random-walk style unexpected earnings and announcement behavior | The Accounting Review 59(4), 1984 | High for classic methodology | Historical market structure; does not prove current after-cost performance. |
| Bernard & Thomas (1989), DOI https://doi.org/10.2307/2491062 (bibliographic anchor also at Event Study Tools) | Post-earnings-announcement drift and sequential surprise persistence | Journal of Accounting Research, 1989 | High for foundational finding | PEAD magnitude and tradability are period-, universe-, and cost-dependent. |
| Chordia et al. (2009), DOI https://doi.org/10.2469/faj.v65.n4.3 | PEAD concentrates in harder-to-arbitrage/liquidly constrained names; costs can consume much gross effect | Financial Analysts Journal 65(4), 2009 | High for cost warning | The cited synthesis reports cost consumption of roughly 70–100%; this is not an estimate for Esscher’s exact universe or option package. |
| How to measure earnings surprises: Based on revised market reaction, https://pmc.ncbi.nlm.nih.gov/articles/PMC10745228 | Consensus construction is not innocuous; analyst forecasts can be biased; alternative surprise measures can differ; unrelated anomalies contaminate CAR | PLOS ONE article, 2024 (PMC full text) | Medium-high, peer-reviewed article | China-specific market/disclosure setting; directionally useful but not directly portable to US earnings. |
| Henry (2008), DOI https://doi.org/10.1177/0021943608319388 | Wording of earnings press releases can influence investor response; motivates text features | Journal of Business Communication 45(4), 2008 | Medium-high | Text effects are not the same as a reliable tradable directional signal; publication timing and selection remain critical. |
| Araci (2019), “FinBERT”, DOI https://doi.org/10.48550/arXiv.1908.10063 | Domain-adapted financial language model is a plausible sentiment classifier baseline | arXiv preprint, 2019 | Medium for model existence, low for trading claim | Pretrained model sentiment is not causal or point-in-time proof; outputs require timestamped text and frozen model version. |
| López de Prado (2018), *Advances in Financial Machine Learning* (book; ISBN 9781119482086) | Purged/embargoed validation, leakage control, multiple-testing and backtest-overfitting precautions | Book, 2018 | Medium-high methodological guidance | Book recommendations need adaptation to event clustering and Esscher’s exact labels; not empirical evidence of this strategy. |

## 1. Ex-ante universe construction

Construct the universe from a dated, versioned **security master plus earnings-calendar observation**, never from winners, surviving tickers, or events selected after seeing returns.

1. At a fixed freeze time before each trading day, enumerate US-listed common equities from the permitted asset/security-master source. Exclude ETFs, ADRs if outside policy, preferreds, funds, warrants, OTC securities, halted/inactive names, and symbols without a stable issuer identifier. Keep historical identifiers and corporate-action mappings.
2. Apply the price >= $10 rule using a defined pre-event reference (for example, the last regular-session close before the event) and record whether the price is raw or adjusted. Do not use the opening reaction to qualify the event.
3. Require optionability using a pre-cutoff chain/contract availability test, not current optionability. “Optionable” must mean the deterministic compiler can find a compliant 7–21 calendar-day vertical with eligible quotes, or else the event remains in the event denominator but receives `NO_PACKAGE`.
4. Obtain earnings timing from a calendar source available before the decision cutoff. Store event identity, issuer CIK, expected/confirmed date, BMO/AMC classification, source publication/retrieval times, and confidence. An approximate calendar date is not an observed announcement timestamp.
5. Freeze the eligible list and selection rule before outcomes. Retain excluded candidates with reason codes. This prevents survivorship, outcome selection, and denominator shrinkage.
6. Handle multiple events, rescheduled releases, preliminary releases, symbol changes, splits and delistings by issuer/event identity rather than ticker alone. If BMO/AMC is unknown or contradictory, abstain.

A historical panel can have development, untouched confirmation, and prospective post-freeze partitions, but the universe rule must be identical. Tuning on the “20–30 event Q-FAST panel” would not establish generalization; it is a development/confirmation gate only.

## 2. Defensible point-in-time inputs

Every input needs at least: issuer/security/event ID, value or content hash, source class and URL/endpoint, publisher timestamp and precision, retrieval timestamp, timezone, entitlement/redistribution status, policy cutoff, and a limitation/error code.

### Primary earnings and guidance evidence

Preferred hierarchy:

- Issuer investor-relations release or filing, with publisher timestamp and immutable content hash.
- SEC 8-K and attached exhibit, with accession number, acceptance timestamp, filing timestamp, form, and raw bytes/hash. EDGAR is a strong public record but its dissemination and indexing timing must be understood.
- Issuer guidance or preliminary release only when it is explicitly identified as a separate event and was available before the cutoff.

Do not merge an earnings release first published after the cutoff into the pre-cutoff snapshot merely because its filing date is the same day. Do not use a later amended filing, restatement, or cleaned vendor field as if it were known then. If timestamp precision is only a date, the evidence is not eligible for a minute-level decision unless policy explicitly permits a conservative fallback; v1 should abstain.

### Consensus and surprise

Consensus is valuable but the highest-risk input for look-ahead and entitlement errors. If licensed consensus is used, preserve the as-of snapshot: forecast value, analyst count, median/mean choice, forecast issue timestamps, currency/units, fiscal period, and vendor revision history. “Latest consensus” downloaded today is not point-in-time.

V1 should preregister two deterministic surprise vectors:

- `SUE_consensus = (actual - frozen_consensus) / max(forecast_dispersion, floor)` when a licensed, point-in-time consensus snapshot exists.
- `SUE_timeseries = (actual - same_quarter_prior_year_actual) / rolling_std(seasonal_differences)` using only prior observations available at the event.

Do not impute a missing denominator. If consensus is absent or entitlement prevents storage/replay, retain the time-series vector and label the consensus vector unavailable; do not silently mix definitions across events. Report each vector separately and require minimum coverage before interpreting it.

The literature does not establish that mean, median, latest, or a sign-based measure is universally superior. The 2024 paper above explicitly discusses analyst optimism and disagreement between surprise measures. Therefore measure choice must be frozen before outcome inspection and sensitivity-reported, not selected for the best result.

### Market and residual inputs

Use one explicit adjustment policy for underlying, SPY/market proxy, and sector proxy. Preserve raw bars and adjusted bars or at least their source/adjustment receipt. Define:

- opening stock return over the exact 09:30–09:35 interval;
- concurrent market and sector returns over the same timestamps;
- beta estimated only from a pre-event estimation window, with a fixed minimum-observation rule;
- opening residual = stock return − frozen beta_market × market return − frozen beta_sector × sector return;
- liquidity fields: price, volume, spread/quote availability, and bar completeness, all as-of the cutoff.

If any leg is stale, non-finite, misaligned, or unavailable, the snapshot is ineligible. Do not forward-fill across the opening interval. A market proxy can be broad SPY or an explicitly frozen alternative; changing it per sector is a hidden feature choice.

Alpaca’s documentation makes the feed issue concrete: IEX and SIP have different coverage, and the FAQ’s example shows radically different trade counts. The feed, subscription, adjustment, pagination, and timestamp policy must therefore be part of the snapshot hash. Historical stock bars do not automatically prove that quote-level microstructure data exists.

### News and text

Use only pre-cutoff content from a source with a defensible publication time and redistribution entitlement. Store metadata plus content hash when raw redistribution is prohibited. Deduplicate syndicated copies and separate issuer-primary text from secondary news. Exclude stories whose displayed timestamp is only a date, whose publication/edit history is mutable, or whose provider timestamp is retrieval time rather than publication time.

A pre-cutoff news feature can be a deterministic count/coverage measure or a fixed financial sentiment classifier, but it must not become an unbounded “read everything” channel. Negative/positive labels should be accompanied by model version, input hash, probabilities, and abstention threshold.

### Entitlement and redistribution traps

- SEC public data is accessible without API keys, but fair-access limits still apply; use bounded, identified, cached retrieval and do not hammer EDGAR.
- Alpaca access depends on account/data subscription. A successful endpoint response is not a universal redistribution license. Record plan/feed and do not commit licensed payloads.
- Analyst consensus and news are commonly licensed. Commit schema, hashes and derived features—not raw payloads—when terms prohibit redistribution.
- Public availability, API availability, and investor availability are different clocks. The earliest defensible timestamp wins; unknown ordering means abstention.

## 3. Deterministic signal stack versus AI

### V1 deterministic stack

The minimum robust feature block is intentionally small:

1. surprise vectors: consensus SUE where licensed plus time-series SUE;
2. guidance change/sign and explicit unknown flags;
3. pre-event price/volatility/liquidity controls;
4. synchronized opening stock, market and sector reactions;
5. beta-based opening residual and residual magnitude;
6. event timing (BMO/AMC), day/season controls, and data-health receipts.

A transparent baseline should classify direction from the sign of the surprise and compare it with a zero-residual/random and market/sector-only baseline. Keep a “reaction relation” descriptive field (`CONTINUE|REVERSE|NONE`) computed by code from accepted direction and opening residual. It is not a new learned target.

V1’s bounded reasoner, if retained, should receive only the frozen snapshot and a strict schema. Its sole allowed output is `UP`, `DOWN`, or `UNCERTAIN` plus evidence citations and one strongest falsifier. It should not see realized outcomes, future bars, option quotes after the decision, account state, or execution tools. Make one bounded call, one route, one pinned prompt/schema/model identifier, fixed token/time budget, and fail closed on timeout, schema error, unsupported citation, conflicting evidence, or missing evidence. Model self-confidence must never override deterministic abstention.

The reasoner’s proper role is evidence synthesis over issuer/guidance text and structured surprises—not contract selection, sizing, execution, or a claim that fluent prose is alpha. Evaluate it against deterministic baselines on the same snapshots, with exact abstention and latency reporting.

### Financial NLP / sentiment

A FinBERT-style classifier is a reasonable later feature, but not a v1 requirement. Financial sentiment has face validity and literature support as a measurement tool, yet generic sentiment often misses negation, guidance direction, accounting context, and “bad news priced in” effects. Use it only as:

- an isolated, frozen feature with document timestamp and model hash;
- a comparison against lexical/dictionary and no-text baselines;
- an input to the bounded reasoner, never an authority;
- a feature whose incremental value is evaluated out-of-sample and after costs.

Do not use online APIs during a decision if that makes the corpus unreplayable or changes outputs. Do not train/fine-tune on texts from confirmation/prospective periods.

### Later classical ML

Only after the deterministic panel is complete and the event count supports it, test regularized logistic regression, calibrated linear models, and shallow tree ensembles. Keep feature count modest relative to independent events. Event clustering by firm and earnings season means row count exaggerates sample size; use firm/episode-aware grouping.

A defensible sequence is:

1. fixed-sign and zero-residual baselines;
2. logistic regression with surprise/residual/liquidity features;
3. regularized model with nested time-series tuning;
4. shallow tree/boosting model only if the previous steps earn it;
5. frozen model and prospective shadow evaluation.

Use chronological walk-forward splits, purged gaps around events, and an embargo around labels/holding windows. Never random-split adjacent earnings events. Tune thresholds/calibration only inside the training window. Report Brier score, log loss, calibration curve, balanced accuracy/MCC, coverage, abstention rate, confusion matrix, residual-direction conditional performance, and economic results separately. A directional accuracy above 50% is not sufficient: include costs, spread, slippage, option decay, quote staleness, package rejection, and the exact one-contract constraint.

## 4. Falsification and sample-size gates

Pre-register falsifiers rather than searching for favorable ones:

- no monotonic relation between surprise bins and residual returns;
- no improvement over sign, zero, market/sector, and simple logistic baselines;
- performance disappears under leakage-safe walk-forward evaluation;
- performance is concentrated in one firm, sector, season, date range, or one release source;
- performance vanishes after realistic stock/option spread, slippage and quote-age rules;
- calibration is materially poor or abstention does not improve conditional quality;
- the reasoner’s incremental performance is not reproducible under a frozen prompt/model;
- timing/latency or option-chain coverage makes the strategy unable to produce valid packages;
- sensitivity to beta window, adjustment, consensus statistic, or event-window choice is larger than the observed effect.

A 20–30 event Q-FAST panel can be useful for wiring and a first sanity check, but it cannot establish statistical credibility. Minimum evidence should be expressed as a gate, not an invented universal magic number. At minimum:

- a pre-registered historical panel spanning multiple years and market regimes, with enough eligible events to estimate uncertainty after grouping by issuer (preferably hundreds, not dozens);
- an untouched confirmation slice never used for feature/threshold/model choices;
- a prospective shadow ledger collected after policy freeze, retaining every eligible event, abstention, data failure, package failure and risk rejection;
- an option quote-history audit showing that the deterministic expression is observable under the exact 2-second quote-age/skew and spread rules;
- confidence intervals or bootstrap intervals clustered by issuer/event episode, plus multiple-testing disclosure;
- a minimum number of post-freeze events before any decision about PAPER mutation, with the threshold frozen in #26 and honestly reported if unmet.

None of this proves profitability. It establishes whether the data and policy are capable of a fair test.

## 5. Minimum credible portfolio-project evidence

Before describing Esscher as a working strategy, require this chain:

`frozen universe/event list -> point-in-time source bytes/hashes -> synchronized snapshot -> deterministic baselines and bounded reasoner decision -> validated option package or visible rejection -> risk result -> hypothetical 60-minute hold -> conservative cost report -> prospective shadow ledger`

For a portfolio project, the minimum credible package is:

- readable policy and data schemas with hashes;
- one reproducible historical compiler run from permitted fixtures/source identities;
- held-out and prospective reports with denominators intact;
- baseline parity and ablation (no text, deterministic text, bounded reasoner);
- latency, coverage, abstention, data-gap and package-failure rates;
- explicit separation of underlying residual results, indicative option expression, and broker-free assumptions;
- no wording implying live profitability, fill certainty, or generalizable AI alpha.

Only after these pass should a separately approved Alpaca PAPER lifecycle be considered, and even then PAPER fills are not live-market evidence.

## Rejected ideas

- **Outcome-selected universe:** selecting only names with clean options or large post-earnings moves; survivorship/selection bias.
- **Today’s revised consensus or fundamentals:** look-ahead through vendor restatements and revision history.
- **Random train/test split:** leakage across the same issuer, season and adjacent event windows.
- **Unbounded web/news LLM:** irreproducible corpus, mutable timestamps, licensing risk, and hidden future information.
- **LLM contract/size/exit decisions:** violates the frozen authority boundary and destroys auditability.
- **Trading on self-reported confidence:** confidence is not calibration and cannot authorize a trade.
- **Using option mid-price as fill proof:** midpoint is indicative; quote age, spread, skew and actual fills remain distinct.
- **Treating Alpaca’s default feed as canonical:** default depends on subscription; IEX versus SIP changes coverage.
- **Calling SEC filing date the complete information timestamp:** filing dissemination/indexing and issuer publication can differ.
- **Training on the untouched Q-FAST or prospective set:** contaminates the evidence gate.
- **Profit targets/stops learned from outcomes in v1:** violates the frozen 60-minute hold and invites overfitting.

## Open questions / evidence gaps

1. Which exact Alpaca feed, historical depth, option quote history, and entitlement are available to the intended account, and can required raw data be replayed without prohibited redistribution?
2. Which earnings-calendar provider supplies confirmed BMO/AMC times with point-in-time history and a license compatible with this project?
3. Is licensed point-in-time analyst consensus available? If not, should v1 formally exclude consensus and use time-series surprises only?
4. What issuer-release timestamp precision can be guaranteed, especially for after-close and pre-open releases, and how are edits handled?
5. Which sector proxy and beta estimation window are frozen in #26, and is there sufficient synchronized history for every eligible event?
6. How will duplicate issuer/SEC/news records be canonicalized without discarding a genuinely earlier publication?
7. What minimum post-freeze event count and confidence/coverage thresholds will authorize the later PAPER gate? This must be a policy decision, not chosen after seeing results.
8. Can the 09:30–09:35 collection, validation and one bounded reasoner call meet 09:36:05 p95 under the real route, with a recorded zero-side-effect smoke harness?
9. What exact option quote source supports two-sided, <=2-second-age/skew evidence historically? Historical bars alone are insufficient.
10. How will firm-level dependence and event clustering be handled in uncertainty intervals and prospective reporting?

## Source families and confidence

Source families read: **12** (GitHub issue tracker #26–#33; Alpaca market-data documentation/API references; SEC developer/API documentation; event-study methodology synthesis; classic earnings-surprise/PEAD literature; peer-reviewed earnings-surprise measurement; financial-text/NLP literature). Key operational claims from Alpaca and SEC are high confidence when tied to the cited docs. Academic event-study findings are high confidence as historical stylized facts but medium confidence for modern, after-cost, option-expressed performance. Any claim about Esscher profitability, LLM incremental value, or executable fills is currently **unproven**.

## References

1. GitHub, Tempest-Research/esscher-market issues #26–#33, https://github.com/Tempest-Research/esscher-market/issues/26 through `/33`.
2. Alpaca, Market Data FAQ, https://docs.alpaca.markets/us/docs/market-data-faq.
3. Alpaca, Historical stock bars, https://docs.alpaca.markets/reference/stockbars.
4. Alpaca, Historical option bars, https://docs.alpaca.markets/reference/optionbars.
5. SEC, Developer Resources, https://www.sec.gov/about/developer-resources.
6. SEC, EDGAR APIs, https://www.sec.gov/search-filings/edgar-application-programming-interfaces (updated April 8, 2025).
7. Event Study Tools, Earnings Announcements, https://www.eventstudytools.com/earnings-announcements.
8. Foster, Olsen & Shevlin (1984), “Earnings releases, anomalies, and the behavior of security returns,” https://www.jstor.org/stable/247321.
9. Bernard & Thomas (1989), “Post-Earnings-Announcement Drift,” https://doi.org/10.2307/2491062.
10. Chordia et al. (2009), “Liquidity and the post-earnings-announcement drift,” https://doi.org/10.2469/faj.v65.n4.3.
11. “How to measure earnings surprises: Based on revised market reaction,” PMC10745228, https://pmc.ncbi.nlm.nih.gov/articles/PMC10745228.
12. Henry (2008), “Are investors influenced by how earnings press releases are written?”, https://doi.org/10.1177/0021943608319388.
13. Araci (2019), “FinBERT: Financial Sentiment Analysis with Pre-trained Language Models,” https://doi.org/10.48550/arXiv.1908.10063.
14. López de Prado (2018), *Advances in Financial Machine Learning*, ISBN 9781119482086.
