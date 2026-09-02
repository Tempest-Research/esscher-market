# Option-event reconciliation contract

This note scopes the focused, fake-only child of issue #66 implemented by
`runtime.option_events`. It defines how assignment, exercise, expiry, and other option non-trade
adjustments enter durable autonomous-session state. This child implementation is complete and
tested, but it does **not** complete the #66 umbrella or supply alpha, broker-connectivity, fill,
external position, external flatness, or profitability evidence.

The slice is observation-only: no network client, credential, broker/account call, order
submission/cancellation, exercise request, or account mutation belongs in it. Tests must use
synthetic fixtures or explicitly host-normalized observations.

## Data flow

```text
INPUT
  exact arm/session/account/protocol/lifecycle/permit/leg/calendar identities
  + one complete account-activity coverage assertion
  + synthetic or host-normalized activities and position snapshot
        |
        v
NORMALIZATION
  validate closed schemas, clocks, coverage, hashes, and correlations
  (a future host adapter maps source-grounded raw codes to semantic kinds)
        |
        v
PURE REDUCER
  binding + observation + coverage + events -> state/exposure/gate/reasons
  no I/O, clock read, persistence, broker call, or mutation
        |
        v
APPEND-ONLY JOURNAL
  atomically claim the account-global activity; append canonical input and output
        |
        v
STATE / GATE
  replay lifecycle; block the account/session on incomplete or conflicting truth
```

Validation and normalization precede reduction. The global claim and journal append precede
publication of derived state. Derived state never substitutes for fresh external observation.

## Input, normalization, and evidence class

A lifecycle binding, observation, activity-coverage receipt, and normalized event set bind, without
mutable aliases:

- the exact autonomous-session arm hash, session, account fingerprint, execution protocol,
  lifecycle identity, opportunity identity, permit bytes/hash, and reservation ID;
- both canonical option legs, expected ratio and quantities, and common expiration;
- each source activity ID, effective date, observation time, and source-payload SHA-256;
- whether pagination is complete for a declared account-wide interval and the exact sorted
  normalized-event SHA-256 set covered by that assertion;
- the fresh position-observation identity and account-wide positions needed to expose both bound
  option legs and resulting underlying quantity; and
- an explicit expiration session date, applicable close, delayed-reporting horizon, and exact
  calendar-artifact SHA-256.

Raw account identifiers, credentials, provider payloads, and exception prose do not enter the
journal. A raw-payload hash records byte identity; it does not establish provenance or truth.

Every input and receipt uses one non-interchangeable evidence label:

- `SYNTHETIC_FIXTURE`: invented test data; its receipts also say
  `NOT_BROKER_CONNECTIVITY_EVIDENCE` and `NOT_ALPHA_EVIDENCE`;
- `HOST_NORMALIZED_BROKER_INPUT`: a host assertion normalized from an exact named source/protocol.
  Repository validation proves structure and correlation, not that Alpaca supplied it.

A fixture cannot be relabelled as a host observation. A valid host attestation is not source-native
broker proof or connectivity evidence.

Provider-specific values must stop at the future host adapter. The pure reducer consumes `ASSIGNMENT`,
`EXERCISE`, `EXPIRY`, `BROKER_SELL_OUT`, `CONTRACT_ADJUSTMENT`, and
`EXERCISE_REJECTED_BUYING_POWER`; the normalized schema rejects unknown kinds. A future adapter must
use an exact, source-versioned mapping, bind that mapping by hash, preserve the source payload's byte
hash, and route unknown or contradictory raw codes to manual reconciliation before this reducer. It
must not guess from a plausible abbreviation.

Normalization keeps effective date distinct from observation time and event occurrence distinct
from later reporting. Alpaca documents that PAPER balance and positions update immediately while
option non-trade activities are visible in the Activities endpoint on the next day. Assignment is
not delivered over the trade websocket and must be polled. This is not treated as a stronger
ordering guarantee or reporting SLA.
Absence of an activity, a disappeared contract, a zero local quantity, or arrival at expiration
never proves that no assignment, exercise, or expiration occurred.

Sources:

- [Alpaca options trading](https://docs.alpaca.markets/docs/options-trading), including account
  activities, exercise, expiration, assignment, and PAPER NTA timing;
- [Alpaca paper trading](https://docs.alpaca.markets/docs/paper-trading), including the explicit
  simulation limitations.

## Reducer, global claim, and restart

For identical binding, observation, coverage, and normalized event bytes, the reducer returns
byte-identical output. Receipts preserve exact identities and expose the current long-leg quantity,
short-leg quantity, underlying quantity, underlying delta from activation, and aggregate event cash
delta. Incomplete pages, unmatched legs, impossible quantities, stale clocks, or position/activity
disagreement block to `MANUAL_RECONCILIATION_REQUIRED`. The reducer never issues or repairs an
order, requests exercise, or infers a fill.

Account activity is account-global, not a per-lifecycle queue. The durable claim key is therefore
account-scoped and activity-scoped before lifecycle routing:

- identical key and canonical bytes are an idempotent replay;
- identical key with different bytes is a durable conflict and the attempted transaction rolls
  back;
- coverage whose event-hash set differs from the reducer input is rejected before reduction;
- unmatched activity yields an explicit manual receipt; lifecycle correlation must be resolved by
  the #66/#82 composition layer before journal submission;
- one v1 activity is attributable to exactly one lifecycle, and concurrent workers cannot consume
  it for different lifecycles;
- persisted binding, observation, event, and receipt rows are immutable canonical records.

On every open, the journal runs SQLite's integrity check, reparses every canonical row, validates
stored columns and cross-row identities, and recomputes receipt exposure. An activity claim and its
attributable receipt are one `BEGIN IMMEDIATE` transaction, so an injected failure before receipt
append rolls the binding, observation, coverage, and activity claim back together. Restart neither
clears manual state nor permits an activity to be attributed again.

`MANUAL_RECONCILIATION_REQUIRED` is sticky across lifecycles for the affected account/session.
Empty positions, a late activity, restart, or elapsed time cannot clear it automatically. This v1
schema intentionally has no automatic recovery transition; a future separately authorized repair
record must retain the incident history and cannot invent flatness.

## Complete two-leg expiration

Expiration is not established by a date or a missing option symbol. A terminal two-leg attestation
must independently include:

1. both canonical leg identities, expected ratio, and activation quantities;
2. the explicit expiration calendar date, applicable close, calendar source, and calendar hash;
3. complete account-global activity coverage through a declared, source-grounded horizon that
   permits next-day non-trade-activity reporting;
4. a semantic terminal disposition and source activity identity for each leg, without inferring one
   leg from the other;
5. fresh before/after truth for both option legs plus account-wide positions sufficient to detect
   resulting underlying exposure;
6. quantity arithmetic explaining the entire stored spread, with no missing page, unknown code,
   adjusted contract, unmatched quantity, or residual exposure.

One attested leg, absent option positions with incomplete activities, or unexplained underlying
exposure yields `MANUAL_RECONCILIATION_REQUIRED`, never terminal flat.

Holiday and early-close handling is explicit: callers bind the actual calendar date, applicable
close, delayed-reporting horizon, and exact calendar-artifact hash. The reducer compares clocks to
those immutable values and never adds one weekday or assumes a standard close. Validation of the
calendar artifact's own provenance remains an upstream responsibility.

## Guarantees and non-guarantees

With validated inputs, the implementation guarantees deterministic reduction, exact
identity preservation, fail-closed mismatch handling, account-global duplicate suppression,
atomic append-only restart replay, recomputed exposure receipts, and sticky account/session
blocking.

It cannot guarantee that a host assertion came from Alpaca, that external state is correct, that an
activity will arrive by a particular time, or that pagination is complete without a source-backed
completeness receipt. It cannot prove broker connectivity, orders, fills, flatness, P&L, account
eligibility, or safe real-lifecycle reconstruction when permit/correlation state was not persisted.
It supplies no historical, alpha, profitability, or #68 readiness claim.

## Current blocker and follow-ons

The selected Alpaca MCP execution tools cover account preflight, order submit/readback/cancel, and
position readback, but do not include account activities. Positions alone cannot distinguish
assignment, exercise, expiration, or another adjustment, and an unavailable activity surface proves
nothing. This slice must not widen the allowlist opportunistically.

Before external integration:

1. add a separately reviewed, least-privilege read-only account-activity capability to the pinned
   MCP protocol, with exact artifact/schema provenance and complete pagination semantics;
2. freeze the source-versioned raw-code mapping and a content-addressed holiday/per-date-close
   calendar contract;
3. bind the observed host account fingerprint and compose the persisted permit/lifecycle state with
  the host-normalized position and activity inputs;
4. integrate the reducer through the single #66/#82 application path without creating another
   broker stack; and
5. leave any external receipt to a separately armed #68 PAPER session.

## Verification

`tests/test_option_events.py` provides deterministic fake-only coverage for:

- nominal assignment, exercise, and complete two-leg expiration;
- immediate position change followed by next-day activity, remaining blocked until reconciled;
- one-leg expiry, missing page, unknown code, unmatched quantity, and unexplained underlying
  exposure;
- identical replay, conflicting duplicate, concurrent global claim, and ambiguous lifecycle match;
- restart before claim, after claim/before append, after append, and from a tampered journal;
- out-of-order/stale clocks and contradictory snapshots;
- explicit delayed holiday horizon, changed calendar hash, and naive weekday-roll rejection;
- account/session-wide sticky manual state with no unauthorized clear path;
- closed schemas, sanitized storage failures, and a module with no network, provider, account,
  broker, order, cancel, or exercise capability; and
- evidence-label separation so synthetic fixtures cannot mint host or broker claims.

Until the external-input and #82 composition follow-ons exist and an end-to-end fake traverses that
accepted application path, this is completion evidence only for the focused option-event contract
and journal child, not for the #66 umbrella or a PAPER run.
