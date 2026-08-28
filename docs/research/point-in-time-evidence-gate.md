# Point-in-time evidence gate

Status: Lane B research contract for issue #1. This is a documentation-only
proposal. It does not create a historical panel, tune a strategy, place an
order, or claim alpha.

The short answers requested by the issue are in [Understanding Gate: Simple
Answers](#understanding-gate-simple-answers). The proposed contract below is
written to match the current offline harness and its existing safety labels.

## 1. Scope and claim boundary

Ringdown asks a narrow question: after a scheduled earnings event becomes
observable, does a preregistered signal describe the issuer's move after a
realistic delayed entry better than frozen comparators?

That question has three separate clocks:

1. **Evidence clock:** when a source was publicly observable and when our
   process retrieved it.
2. **Decision clock:** the last instant at which the signal may use evidence.
3. **Market clock:** when a tradable market observation could actually provide
   an entry and a later exit.

Those clocks must not be collapsed into one timestamp. The current code already
enforces two important boundaries: evidence and feature snapshots cannot be
later than `decision_cutoff` (`src/ringdown_market/alpha/models.py`), and
evaluation starts at the first path point at or after the modeled latency
(`src/ringdown_market/alpha/evaluation.py`).

This note defines a research contract for a future
`POINT_IN_TIME_EVENT_PANEL`. It does not turn the current synthetic fixture
into historical data. The repository's existing `SYNTHETIC_CONTRACT_FIXTURE`
must continue to carry `NOT_HISTORICAL_DATA`, `NOT_ALPHA_EVIDENCE`, and
`NO_BROKER_EXECUTION`.

### Claim levels

Keep these claims separate:

| Level | What it can support | What it cannot support |
| --- | --- | --- |
| Engineering evidence | Deterministic contracts, tests, hashes, and adapter behavior | Alpha, profitability, or real-world fill quality |
| Historical research evidence | Results from an untouched point-in-time panel that passes the registered gates | Executable option fills or live performance |
| Paper execution evidence | Sanitized readback from the dedicated Alpaca paper account | Historical executable pricing or profitability outside paper simulation |
| `INDICATIVE_DATA` | Research or demonstration observations | OPRA/NBBO, executable-fill, or option-P&L claims |

The permitted execution mode remains `PAPER`. This issue does not add broker
code and does not change the repository's `NO_CREDENTIALS`, `NO_NETWORK_CALLS`,
or `NO_ALPHA_CLAIM` offline boundaries.

## 2. Source hierarchy and provenance

Use the strongest source available, in this order:

1. official event rules and sponsor documentation;
2. the issuer's investor-relations release or earnings page, plus the issuer's
   SEC filing when relevant;
3. official SEC EDGAR records and official Alpaca documentation;
4. licensed market-data records with timestamps and entitlement notes;
5. reputable secondary reporting only when a primary source is unavailable.

Do not use a competitor submission, screenshot, dashboard, copied fixture, or
generated summary as replicated evidence. A source link in a bibliography is
not enough: each event record must identify which source supplied each fact.

### Required evidence record

Every proposed evidence item must retain at least these fields:

| Field | Required meaning |
| --- | --- |
| `event_id` | Stable identifier that does not change when the record is re-downloaded. |
| `issuer` | Issuer name and, where applicable, ticker or CIK recorded separately. |
| `source_url` | Exact public URL used for the item. Keep the canonical URL and any accession/document identifier. |
| `publisher` | The organization that published the source, for example the issuer or SEC. |
| `published_at` | The source-reported original publication time or regulator acceptance time, with an explicit timezone and a declared timestamp type. |
| `retrieved_at` | UTC time when our process fetched or observed the exact bytes. This is not a substitute for `published_at`. |
| `decision_cutoff` | UTC time after which no item may affect this decision. It is recorded per event, not inferred later from an outcome. |
| `feature_snapshot_at` | UTC time at which the input features were frozen. It must be at or before `decision_cutoff`. |
| `content_sha256` | SHA-256 of the exact byte representation frozen for the decision. Use raw source bytes by default; if a deterministic canonical representation is required, record that representation and its rules. |
| `data_class` | One of `POINT_IN_TIME_EVENT_PANEL`, `INDICATIVE_DATA`, or another registered class. |
| `redistribution_note` | Whether the bytes may be redistributed, or whether only the URL, accession, metadata, and hash may be published. |
| `field_status` | Explicit `missing`, `revised`, or `conflicting` markers where the source does not provide one unambiguous value. |

The final record should also retain the event category, venue/session
calendar, source timestamp type, observation window, latency profile, and
exclusion reason. These are needed to replay the decision without guessing.

### `published_at` is typed, not assumed

Store both the time and its meaning, for example:

```text
published_at: 2026-08-28T13:35:00Z
published_at_type: issuer_release_timestamp
```

Useful timestamp types include:

- `issuer_release_timestamp`: timestamp printed by the issuer's release
  system or an issuer-hosted document;
- `sec_acceptance_datetime`: EDGAR's acceptance timestamp for a filing;
- `sec_filed_as_of_date`: filing date, which is a date rather than an exact
  intraday observation time;
- `unknown`: explicit failure state, never silently replaced by retrieval time.

The [SEC Developer FAQ](https://www.sec.gov/os/webmaster-faq#timestamps) states
that EDGAR supplies acceptance and filing metadata, but no timestamp proving
when filing content first became available on `sec.gov`. Therefore
`sec_acceptance_datetime` is evidence of EDGAR acceptance, not automatic proof
of first public web observability. A separate optional `source_observed_at`
field may record when our collector first saw the source, but it must never be
stored as the source's publication time. If the release or filing does not
establish an exact public time, the event must use a conservative policy or
abstain; it must not invent precision.

### Retrieval and content hash rules

`retrieved_at` records when the exact source bytes entered our evidence store.
It is useful for audit and for detecting later changes, but it does not make a
later download available at an earlier decision. A late retrieval may be used
only if an independently preserved historical version proves that the same
content was available before the cutoff.

For `content_sha256`:

1. fetch the exact permitted representation;
2. preserve the raw bytes before parsing or whitespace normalization;
3. use the raw bytes by default, or apply a deterministic, pre-registered
   canonicalization rule when the decision requires one;
4. compute SHA-256 over the frozen representation;
5. parse a separate copy into features;
6. keep the URL, retrieval time, hash, representation rule, and parser/version
   metadata together.

If a provider's terms prohibit storing or publishing the bytes, retain the
permitted source reference and hash only if the terms allow hashing. Use
`UNAVAILABLE_NOT_PERMITTED` when even that is not allowed. A hash proves that
the stored representation has not changed; it does not prove the provider's
publication time.

## 3. Event timing contract

All timestamps are stored in UTC with timezone information. Human-readable
examples below use U.S. Eastern Time because the NYSE session calendar is
defined in ET. The session calendar used in a real panel must be versioned and
must account for holidays and early closes.

The [NYSE published schedule](https://www.nyse.com/markets/hours-calendars)
identifies the core equity session as 9:30 a.m. to 4:00 p.m. ET. The exact
venue and instrument calendar must be used for the actual security; an equity
session clock is not automatically an options fill clock.

### Event categories

Classify an earnings release by its verified publication time relative to the
relevant regular session:

| Category | Definition | Earliest ordinary reaction window |
| --- | --- | --- |
| `BEFORE_OPEN` | Release is public before the relevant regular session opens. | The next eligible session, normally the same day. |
| `AFTER_CLOSE` | Release is public after the relevant regular session closes. | The next eligible trading session; never the already-ended session. |
| `INTRADAY` | Release is public during the regular session. | A preregistered post-publication reaction window after processing latency. |
| `UNRESOLVED` | Timing or session status cannot be established safely. | No trade or historical admission until resolved. |

"Before" and "after" are not determined from the date alone. Use the source
timezone, venue calendar, daylight-saving rules, holiday schedule, and any
declared release-time convention.

### Before-open example

The following is an illustrative protocol, not a historical event. It uses a
release at 08:00 ET, an evidence-processing allowance ending at 08:05 ET, a
feature freeze at 08:05 ET, and a modeled 30-second delay. The entry is the
first observed eligible price at or after 08:05:30 ET; if the strategy trades
only the core session, the path may not admit an entry until the 09:30 ET open.

```text
BEFORE_OPEN (illustrative, regular session opens 09:30 ET)

08:00       08:05                 08:05:30                         09:30
  |-----------|---------------------|--------------------------------|
  release    cutoff + snapshot     earliest modeled entry            core open
  public     frozen                (first eligible quote/path point)  auction
  |
  +-- source bytes, publication time, and hash are captured
  +-- no evidence arriving after 08:05 can change the signal
  +-- if premarket is not an admitted venue, entry waits for 09:30

                         |<------ hold window ------>|
                         achieved entry              fill-relative exit
                         (entry_at)                  (exit_at)
```

The `decision_cutoff` is not necessarily the release time. It is the
pre-registered end of processing and feature construction. The market path
must still choose an achievable `entry_at`, not a release-time price or an
idealized zero-latency fill.

### After-close example

This is also illustrative. A release at 16:05 ET follows a 16:00 ET close. A
same-day decision is impossible for the regular session because that session
has ended. A policy may process the release after 16:05, but the earliest
ordinary entry is in the next eligible session. If the next day is a holiday,
the calendar advances to the following eligible session.

```text
AFTER_CLOSE (illustrative, regular session closes 16:00 ET)

prior session       16:00          16:05       next eligible open
|--------------------|--------------|-------------------------|
|  regular trading   | session ends | release public          |
|                    |              | cutoff/snapshot later   |
|                    |              |                         |
|                    +-- no same-session entry is allowed       |
|                                                           |
|                                                           v
|                                             first achievable entry
|                                             at/after cutoff + latency

                                             |<--- hold window --->|
                                             entry_at              exit_at
```

The after-close event belongs to the next tradable reaction window. Backdating
the decision to the prior close, or using the next morning's information in a
prior-session return, is look-ahead.

### Intraday policy

For an intraday release, retain the source's exact timestamp if available. Set
the processing cutoff to a declared point after that timestamp, for example:

```text
event_public_at <= processing_complete_at = decision_cutoff
feature_snapshot_at <= decision_cutoff
entry_at >= decision_cutoff + modeled_latency
exit_at >= entry_at + hold_period
```

The reaction window must be registered before outcomes are inspected. It must
state whether the opening or last price of a bar is usable, which quote/trade
observation is eligible, how missing bars are handled, and what happens if the
market closes before the hold completes. If the required path is missing, mark
the row unavailable or exclude it under a predeclared rule; do not substitute a
future observation and do not select a more convenient event.

## 4. What each decision timestamp means

These fields are related but not interchangeable:

| Timestamp | Meaning | Can it move because of an outcome? |
| --- | --- | --- |
| `published_at` | When the source says the information was published or accepted. | No. It is a source fact or an explicit unknown. |
| `retrieved_at` | When our process captured the source representation. | No. It is recorded at collection time. |
| `decision_cutoff` | Last instant at which evidence/features may enter the decision. | No. It is registered before evaluation. |
| `feature_snapshot_at` | When the feature vector was frozen. | No. It must be at or before the cutoff. |
| `entry_at` | First eligible market observation at or after cutoff plus modeled delay. | No. It is selected by the preregistered path rule. |
| `exit_at` | First eligible observation at or after entry plus the hold period. | No. It is fill-relative, not cutoff-relative. |

The current `DecisionSnapshot` rejects a later `latest_evidence_at` or
`feature_snapshot_at`. The current evaluator computes the target entry from
`decision_cutoff + latency`, chooses the first available point, and computes
the exit from that achieved entry. A future panel loader should preserve these
semantics rather than replacing them with a single event date.

### Missing, revised, and conflicting evidence

- **Missing time:** set `published_at_type: unknown`, record the missing field,
  and apply the conservative admission policy. Do not infer "before open" from
  a date-only page.
- **Revised source:** retain the original captured hash and the later revision
  as separate evidence versions. The decision can use only the version known
  to be available by its cutoff.
- **Conflicting times:** keep both sources and their hashes, mark the event
  `CONFLICTING`, and use a conservative cutoff or abstain until a policy
  resolves the conflict.
- **Late SEC availability ambiguity:** distinguish EDGAR acceptance from first
  availability on `sec.gov`; if the latter is not established, do not claim an
  exact public-observation time.

## 5. Residual return

The raw issuer move contains more than an issuer-specific reaction. It can
include a broad market move and a sector move during the same entry-to-exit
window. Ringdown removes those components with frozen market and sector betas.

For event `i`, define log returns over the **same achieved window** from
`entry_at` to `exit_at`:

```text
r_stock  = log(stock_exit  / stock_entry)
r_market = log(market_exit / market_entry)
r_sector = log(sector_exit / sector_entry)

residual_return = r_stock - beta_market * r_market - beta_sector * r_sector
signed_return   = direction.multiplier * residual_return
```

This is the same structure implemented in
`src/ringdown_market/alpha/evaluation.py`. A positive residual means the
issuer outperformed the frozen market/sector expectation over that window; a
negative residual means it underperformed. It does not prove that the signal
caused the move.

### Residualization rules

1. Register the market and sector proxy before looking at the event outcome.
2. Estimate or freeze `beta_market` and `beta_sector` using only data available
   at the feature cutoff. Do not estimate them from the post-event reaction
   window.
3. Use synchronized, timezone-aware observations for issuer, market, and
   sector.
4. Use the same achievable entry and fill-relative exit window for all three
   series.
5. Record missing observations, corporate-action treatment, proxy identity,
   estimation window, and beta version.
6. If the market or sector series cannot be aligned without look-ahead, fail
   closed rather than silently falling back to an unrelated window.

Residualization is an evaluation choice, not a guarantee that all confounding
has been removed. MacKinlay's [academic event-study
survey](https://ideas.repec.org/a/aea/jeclit/v35y1997i1p13-39.html) describes
event studies as measuring price effects around an event and discusses their
complications; it does not validate this repository's exact beta model.
Ringdown's chosen formula and window must therefore remain explicit and
preregistered.

## 6. Abstention and denominators

`UNCERTAIN` is a real decision state, not a deleted row. In the current
evaluator, it is not admitted and its signed return is `0.0`. In the current
Q-FAST panel metrics, every row contributes to `eligible_events` and
`mean_all`, while only admitted rows contribute to `mean_admitted`.

For method `m` with `N` eligible events:

```text
coverage_m       = admitted_events_m / N
mean_all_m       = sum(signed_return_i,m for all eligible i) / N
mean_admitted_m  = mean(signed_return_i,m for admitted i), if any
```

Keep both views. `mean_admitted` describes outcomes conditional on taking a
signal. `mean_all` describes the registered strategy across the eligible event
universe, where abstention carries no return. Reporting only admitted events
can make a strategy look better by hiding difficult cases and can make
comparisons unfair if methods admit different rows.

The denominator is frozen before outcomes are reviewed. Do not remove an
abstention because its eventual move would have been favorable or unfavorable.
Do not compare a candidate on one event subset with a baseline on another.
Candidate and frozen baselines must use the same eligible panel, timing rules,
latency profile, risk convention, and hold window.

## 7. Data access, redistribution, and options limits

### Public event evidence

The [SEC EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
states that `data.sec.gov` submissions and XBRL APIs require no authentication
and are updated as filings are disseminated. The [SEC Developer
FAQ](https://www.sec.gov/os/webmaster-faq#reuse) also says public EDGAR content
is free to access and reuse. This supports public source references and, where
permitted, sanitized extracts. We still need to preserve the exact source URL,
timestamp type, retrieval time, and hash for replay.

Issuer investor-relations releases are primary event sources when they provide
the release itself and its publication time. Their website terms and document
delivery behavior may differ. If the raw release cannot be redistributed,
publish only the permitted metadata, a canonical URL/accession, and an allowed
hash.

### Market data

Any market-data record used for a historical panel needs an entitlement note,
timestamp precision, exchange/venue semantics, corporate-action policy, and
redistribution permission. A public API endpoint does not by itself grant a
right to publish the raw dataset.

### Alpaca options data

The [Alpaca historical option-data documentation](https://docs.alpaca.markets/us/docs/historical-option-data)
says historical options are available from February 2024 onward and
distinguishes:

- `Indicative`: a free derivative of OPRA; its quotes are not actual OPRA
  quotes, and trades are delayed by 15 minutes;
- `OPRA`: consolidated BBO data available to subscribed users.

Alpaca's [market-data documentation](https://docs.alpaca.markets/us/docs/about-market-data-api)
further describes Basic-plan options as indicative and the subscribed complete
feed as OPRA. Therefore a historical panel built from free indicative data
must be labeled `INDICATIVE_DATA`. It cannot support claims about an
executable option fill, NBBO, spread capture, slippage, or option P&L.

The [option stream documentation](https://docs.alpaca.markets/us/docs/real-time-option-data)
gives quote/trade timestamps and venue fields, but a timestamped quote is still
not a broker fill. To claim a paper execution, use sanitized paper-account
order and fill readback under the repository's one official adapter. Do not use
the paper result as historical option-price evidence.

### Paper simulation limits

The [Alpaca paper-trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)
describes paper trading as a real-time simulation, not live exchange routing.
It lists limitations including no market impact, information leakage, latency
slippage, queue position, price improvement, regulatory fees, or dividends. It
also states that paper orders are matched against the best available current
market price and that quantity is not checked against NBBO quantity.

These limitations mean:

- a paper fill can show that our adapter and reconciliation path behaved in a
  simulated account;
- a paper fill cannot prove that an historical option quote was executable;
- paper P&L cannot establish profitability in live markets;
- a free indicative option quote cannot be relabeled as OPRA or NBBO.

The document and any future public trace must retain `PAPER`,
`INDICATIVE_DATA`, and `NOT_ALPHA_EVIDENCE` where applicable.

## 8. Historical-panel admission gate

Before a row enters a `POINT_IN_TIME_EVENT_PANEL`, check all of the following:

1. The event has a stable ID, issuer, category, session calendar, and source
   hierarchy record.
2. Every feature source has `source_url`, `publisher`, `published_at`,
   `retrieved_at`, `decision_cutoff`, `feature_snapshot_at`, and
   `content_sha256` semantics.
3. Every timestamp has a timezone and typed meaning. Unknown or conflicting
   timing is explicitly marked.
4. No source or feature used by the decision is later than
   `decision_cutoff`.
5. `feature_snapshot_at <= decision_cutoff`.
6. The event category and next eligible session are determined without using
   the realized return.
7. The market, sector, and issuer paths are synchronized and cover the
   achievable delayed-entry and fill-relative exit windows.
8. The latency profile and reaction/hold window were registered before
   outcomes were inspected.
9. Abstentions remain in the panel denominator.
10. Data class, entitlement, redistribution, and any `INDICATIVE_DATA` limits
    are recorded.
11. Exclusions are declared before outcome review and carry a reason.
12. No selection is based on realized return, favorable fills, or a desired
    sample size.

The repository's source policy additionally requires at least 20 untouched
eligible events for a Q-FAST panel. Fewer rows may exercise code contracts but
must remain insufficient research evidence. A passing or non-rejected Q-FAST
screen retains `NOT_ALPHA_EVIDENCE` under the current implementation.

## 9. Verified facts, implementation choices, unresolved questions

### Verified facts

- The current harness rejects evidence and feature snapshots after the
  decision cutoff (`src/ringdown_market/alpha/models.py`).
- The current evaluator starts at the first available path point at or after
  cutoff plus modeled latency and measures the exit from that achieved entry
  (`src/ringdown_market/alpha/evaluation.py`).
- The current evaluator subtracts market and sector components over the same
  entry-to-exit window (`src/ringdown_market/alpha/evaluation.py`).
- The current evaluator maps `UNCERTAIN` to zero signed return while retaining
  the event for panel construction.
- The current Q-FAST metrics retain all eligible rows in the denominator and
  expose admitted-only metrics separately (`src/ringdown_market/alpha/qfast.py`).
- SEC's EDGAR API page says `data.sec.gov` APIs require no authentication or
  API keys and are updated as submissions are disseminated.
- SEC's Developer FAQ distinguishes acceptance, filed-as-of, and change dates
  and says there is no timestamp proving when content first became available on
  `sec.gov`.
- NYSE publishes core equity hours of 09:30 to 16:00 ET and separate options
  hours; the applicable calendar must be chosen per instrument.
- Alpaca documents historical options from February 2024 onward and separates
  indicative data from subscribed OPRA data.
- Alpaca documents paper trading as a simulation with material fill and P&L
  limitations.
- MacKinlay's *Event Studies in Economics and Finance* describes event studies
  as measuring price effects around an economic event and discusses potential
  complications. It is methodological background, not primary timestamp
  evidence.

### Implementation choices proposed here

- Store all machine timestamps in UTC and retain the source timezone/type for
  every publication claim.
- Treat SEC acceptance as acceptance evidence, not automatic first-web-
  availability evidence.
- Use a typed `published_at` plus an explicit unknown state instead of guessing
  a timestamp from page dates or retrieval time.
- Classify before-open, after-close, and intraday events against a versioned
  venue calendar.
- Freeze the feature vector by `decision_cutoff`, then choose entry and exit
  from an achievable synchronized path using the registered latency and hold
  rules.
- Estimate or freeze residualization betas before looking at the outcome and
  measure issuer, market, and sector returns over the same achieved window.
- Keep abstentions in the eligible denominator and report both all-event and
  admitted-only metrics.
- Treat free Alpaca option data as `INDICATIVE_DATA` and prohibit executable
  option-fill or option-P&L claims from it.
- Publish source references, metadata, and hashes where allowed; publish raw
  source bytes only where the source terms permit it.
- Keep this deliverable documentation-only. No code, fixtures, UI, adapter,
  credentials, or network behavior changes are part of issue #1.

### Unresolved questions

The following remain `UNCONFIRMED` until an organizer, source owner, or future
contract answers them. They must not be silently turned into rules:

- Does the hackathon require positions to be flat at submission?
- Is there a minimum trade count beyond the current research-panel requirement?
- Does the event require disclosure of AI assistance?
- Which exact option-data entitlement and redistribution rights will be used
  for the historical panel?
- Which issuer release channel is authoritative when an issuer publishes the
  same earnings information on multiple pages with different timestamps?
- What is the permitted fallback when an event's publication time is only
  available as a date or when EDGAR acceptance-to-web availability is unknown?
- Which venue/session calendar and quote-versus-trade convention will the
  strategy register for each instrument?
- What minimum path coverage is required when a delayed entry or fill-relative
  exit falls across a holiday, early close, halt, or option expiration?

Each unresolved item should be closed with a dated source, an explicit policy,
or an explicit abstention rule before the corresponding historical fixture is
created.

## 10. Understanding Gate: Simple Answers

These are the five answers requested by issue #1, in simple words. They are
intended as a study aid and a location marker. Before posting an issue comment,
the human contributor should explain the same ideas in their own words.

### 1. Why is evidence published after `decision_cutoff` forbidden?

Because the agent could not have known it at the time it made the decision. If
we use it anyway, the test sees the future and the result is too good to trust.

### 2. Why does an abstention stay in the denominator?

Because abstaining is part of the strategy. Keeping that event in the total
shows how often the strategy acted and prevents us from hiding hard events.
The abstention gets zero return, while the coverage number says how many events
received a direction.

### 3. What does residual return remove?

It removes the part of the issuer's move that can be explained by the broad
market and the issuer's sector over the same time window. What remains is the
issuer move after those reference moves are taken out. It is not proof that the
signal caused the move.

### 4. Why can a deterministic synthetic test not prove alpha?

Because the test uses made-up, fixed inputs designed to check software rules.
It proves that the program gives the same expected answer and rejects bad
timestamps. It does not prove the inputs happened in real markets, that prices
were tradable, or that the strategy makes money.

### 5. Why is a source URL alone insufficient without publication and retrieval timestamps?

Because a URL does not tell us which version was visible or when we obtained
it. A page can change later, and downloading it later does not mean its current
contents were available earlier. Publication time tells us what could have
been known; retrieval time tells us when we captured the version we used.

## Sources

The links below are the sources used for the verified external claims in this
note. Access dates are recorded so a later reviewer can distinguish the source
from the wording of this proposal.

### Official event and repository sources

1. [Issue #1: Lane B point-in-time evidence gate](https://github.com/Tempest-Research/ringdown-market/issues/1) - repository issue and acceptance criteria; checked 2026-08-29.
2. [Ringdown source and claim policy](../SOURCE_AND_CLAIM_POLICY.md) - repository policy; checked 2026-08-29.
3. [Ringdown architecture](../ARCHITECTURE.md) - implemented evaluation boundaries; checked 2026-08-29.
4. [Ringdown team onboarding](../TEAM_ONBOARDING.md) - current research and claim boundaries; checked 2026-08-29.

### SEC and EDGAR

5. [SEC EDGAR Application Programming Interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) - public API access, update behavior, and no API-key requirement for `data.sec.gov`; checked 2026-08-29.
6. [SEC Webmaster FAQ](https://www.sec.gov/os/webmaster-faq#developers) - request limits, user-agent guidance, EDGAR timestamp meanings, and lack of a first-availability timestamp; checked 2026-08-29.
7. [SEC Search Filings](https://www.sec.gov/edgar/search-and-access) - public EDGAR filing access and search entry points; checked 2026-08-29.

### Market sessions

8. [NYSE Holidays and Trading Hours](https://www.nyse.com/markets/hours-calendars) - equity, options, holiday, and early-close session schedule; checked 2026-08-29.

### Alpaca

9. [Alpaca Historical Option Data](https://docs.alpaca.markets/us/docs/historical-option-data) - availability from February 2024 and `Indicative` versus `OPRA` data sources; checked 2026-08-29.
10. [Alpaca About Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api) - Trading API authentication and Basic versus Algo Trader Plus data entitlements; checked 2026-08-29.
11. [Alpaca Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading) - simulation model, paper/live differences, fill assumptions, and limitations; checked 2026-08-29.
12. [Alpaca Options Trading](https://docs.alpaca.markets/us/docs/options-trading) - paper options enablement, contract details, order constraints, and paper non-trade activity timing; checked 2026-08-29.
13. [Alpaca Real-time Option Data](https://docs.alpaca.markets/us/docs/real-time-option-data) - option quote/trade timestamp fields and feed-specific stream behavior; checked 2026-08-29.

### Academic method

14. A. Craig MacKinlay, [Event Studies in Economics and Finance](https://ideas.repec.org/a/aea/jeclit/v35y1997i1p13-39.html), *Journal of Economic Literature*, 35(1), 13-39, 1997 - event-study methodology and complications; abstract and citation checked 2026-08-29.

## Review checklist

- [ ] Verify every source claim against the linked source, not only this note.
- [ ] Replace any illustrative policy with a dated, approved contract before
  creating historical fixtures.
- [ ] Record the actual source bytes, hashes, timestamps, and entitlement notes
  in a private or permitted evidence store.
- [ ] Keep the public artifact static, sanitized, and free of credentials,
  account IDs, raw private documents, and outbound-network capability.
- [ ] Have the human contributor answer the five understanding questions in
  their own words in the issue comment.
