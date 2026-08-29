# Source and claim policy

Record what was knowable at the decision time and do not make claims stronger than the evidence supports.

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
- earliest source-supported public-observability timestamp with timezone, or an explicit unknown/date-only state;
- source timestamp type and precision, with SEC acceptance retained separately;
- retrieval timestamp with timezone;
- collector-observed timestamp, when available, retained separately from publication time;
- decision cutoff and feature snapshot timestamp;
- per-feature source dependencies and the dependency-check result;
- content hash or immutable source revision where permitted;
- data classification, feed/data-quality qualifiers, entitlement note, and redistribution status;
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

`INDICATIVE_DATA` is a qualifier on a source or artifact, not a replacement for the artifact class `POINT_IN_TIME_EVENT_PANEL`. A panel may carry this qualifier when one of its feeds is indicative; the qualifier remains visible in reports and public traces.

## Status vocabulary

- `ELIGIBLE`: the evidence, timing, feature dependencies, and common outcome path pass the registered gate;
- `UNCERTAIN`: a method abstains on an eligible event and contributes zero signed return for that method;
- `UNAVAILABLE`: a required outcome input or path is absent after evidence and timing passed; retain the row and exclusion reason, but exclude it from the common eligible denominator;
- `UNRESOLVED`: publication timing, source conflict, or session status is not safely established; do not admit the row.

## Claim levels

- **Engineering evidence:** contracts, deterministic output, tests, and adapter behavior.
- **Historical research evidence:** untouched point-in-time panels that pass registered gates.
- **Paper execution evidence:** sanitized broker readback from the dedicated paper account.
- **Other execution modes:** absent from the product definition.

Do not promote evidence between levels. Green tests do not prove alpha. A paper fill does not establish executable historical pricing. Positive paper P&L does not prove profitability.

## Reusing sources and code

- Do not copy from competing submissions.
- Record the license and exact revision before reusing third-party code or assets.
- Prefer official schemas over reverse-engineered examples.
- Link every material research fact to its source.
- Do not present unverified terminal output, citations, timestamps, prices, or broker receipts as evidence.

## Public artifacts

Public exports contain only the minimum sanitized trace required for judging. They exclude credentials, account IDs, private source documents, raw databases, internal prompts, mutation code, and outbound-network capability.

If a field cannot be safely published, render an explicit redaction or unavailable state. Do not replace it with invented content.

## Submission policy

The Ringdown submission policy is:

- close all positions and cancel all open orders before submission;
- require no minimum number of live trades;
- require at least 20 untouched eligible events for Q-FAST; and
- keep abstentions in the event denominator.

These are project policies, not claims about organizer requirements.
