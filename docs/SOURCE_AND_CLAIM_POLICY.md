# Source and claim policy

Ringdown's credibility depends on proving what was knowable at the decision time and refusing stronger claims than the evidence supports.

## Source hierarchy

Use sources in this order:

1. official event rules and sponsor documentation;
2. issuer investor-relations releases and SEC filings;
3. official Alpaca documentation and pinned source code;
4. licensed market-data records with timestamps and entitlement notes;
5. reputable secondary reporting when the primary source is unavailable.

A competitor README, screenshot, dashboard, or submitted JSON file is an external claim. It is not replicated evidence.

## Required evidence metadata

Every real event manifest must retain:

- stable event ID and issuer;
- source URL and publisher;
- original publication timestamp with timezone;
- retrieval timestamp with timezone;
- decision cutoff and feature snapshot timestamp;
- content hash or immutable source revision where permitted;
- data classification and redistribution note;
- explicit missing, revised, or conflicting fields.

Retrieval time does not replace publication time. A document downloaded before the cutoff can still contain a later revision; a document downloaded later may be valid only if its historical version is independently preserved.

## Data classes

### `SYNTHETIC_CONTRACT_FIXTURE`

Purpose: deterministic software tests only.

Required limitations:

- `NOT_HISTORICAL_DATA`
- `NOT_ALPHA_EVIDENCE`
- `NO_BROKER_EXECUTION`

### `POINT_IN_TIME_EVENT_PANEL`

Purpose: historical evaluation using evidence frozen as it existed at each cutoff.

Requirements:

- at least 20 untouched eligible events for Q-FAST;
- abstentions retained;
- frozen policy and baselines;
- synchronized issuer, market, and sector windows;
- latency replay at the preregistered profile;
- exclusions declared before outcomes are inspected.

### `INDICATIVE_DATA`

Purpose: observations that are useful for research or demonstration but do not prove an executable option fill.

Free or indicative quotes must not be converted into OPRA/NBBO, fill-quality, or option-P&L claims.

## Claim levels

- **Engineering evidence:** contracts, deterministic output, tests, and adapter behavior.
- **Historical research evidence:** untouched point-in-time panels that pass registered gates.
- **Paper execution evidence:** sanitized broker readback from the dedicated paper account.
- **Live-market evidence:** outside this project's scope.

Do not promote evidence between levels. Green tests do not prove alpha. A paper fill does not prove a live fill. Positive paper P&L does not prove profitability.

## External code and generated material

- Do not copy from competing submissions.
- Record the license and exact revision before reusing third-party code or assets.
- Prefer official schemas over reverse-engineered examples.
- Generated research summaries must link to the source used for every material fact.
- Generated terminal output, citations, timestamps, prices, and broker receipts are forbidden.

## Public artifacts

Public exports contain only the minimum sanitized trace required for judging. They exclude credentials, account IDs, private source documents, raw databases, internal prompts, mutation code, and outbound-network capability.

If a field cannot be safely published, render an explicit redaction or unavailable state. Do not replace it with invented content.

## Unconfirmed rules

Record unresolved competition questions as `UNCONFIRMED` with the checked date and source. Do not convert participant recollection or inference into official rule text.
