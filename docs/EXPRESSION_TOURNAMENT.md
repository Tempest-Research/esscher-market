# Expression tournament and compiler (Gate D)

This document describes the read-only Gate D lane added for issue #29: a
deterministic comparison of cash/no-trade, shares, one long option, and a
defined-risk debit vertical for the same frozen directional decisions, and a
production compiler for the single promoted expression. The lane never places
orders, touches accounts or positions, tunes policy, or promotes expressions.

## Boundary

- Inputs: one validated `UP`/`DOWN` `StrategyDecision` from the bounded
  decision engine (#28), one immutable `esscher.expression_market_snapshot`,
  and one frozen `esscher.promoted_expression_policy`.
- Outputs: one canonical `esscher.gate_d_report` receipt (tournament) and one
  `esscher.compiled_expression` artifact or a stable `NO_PACKAGE` rejection.
- The supplied policy SHA-256 must equal the digest of the exact canonical
  promoted-policy bytes; a mismatch fails closed with `POLICY_HASH_MISMATCH`.
- `UNCERTAIN` never reaches compilation. A non-accepted disposition fails with
  `DIRECTION_NOT_VALIDATED`.
- The compiler has no account, order, position, mutation, model, or
  policy-promotion authority. Risk/reservation belongs to #30; nothing here
  bypasses it.

## Read-only adapters

Market observations enter only through two read-only protocol boundaries:

- `ShareObservationSource.share_quote(symbol, asof)` — one point-in-time
  two-sided share quote, or `None`.
- `OptionObservationSource.option_chain(underlying, asof)` /
  `packages(underlying, asof)` / `borrow_locate(symbol, asof)` — point-in-time
  contract observations, atomic package quotes, and explicit borrow/locate
  evidence, or `None`/empty.

No adapter exposes order, account, position, trading, or mutation methods.
Unit tests never perform live capture: all fixtures are deterministic,
synthetic, and local. A live read-only probe would be a separate recorded
gate, not part of this lane.

Every observation carries a pinned `FeedIdentity` (feed, tool, schema,
version). Unknown or unpinned feeds fail closed with `UNKNOWN_FEED`; feed
identities are never inferred. Observations labeled `INDICATIVE_DATA` never
become executable-fill evidence (`INDICATIVE_ONLY`).

The compiler and tournament apply the same pinned-feed, freshness, two-sided
size, and spread bounds to shares, option legs, and atomic packages. Freshness
and vertical-skew bounds are inclusive at the exact policy limit and compare
exact UTC durations, so any one-microsecond overage fails closed. A short share
also needs explicit borrow/locate evidence dated no later than the snapshot
clock and fresh within the frozen observation-age bound.

## Quote-side economics

Midpoint-only PnL is prohibited by construction: buys consume the ask, sells
consume the bid, and the entry after-cost is the cost of crossing the quoted
spread, measured from two-sided quotes only. Underlying returns are never
reported as option PnL. Cash, shares, long option, and debit-vertical
economics are reported separately.

The preregistered after-cost objective is
`AFTER_COST_EXPECTED_EDGE_VS_CASH`: the directional hit rate above one half,
in basis points, minus the mean entry spread drag expressed as a fraction of
entry cost. An expression qualifies only if it is comparable on at least
`evidence_min_events` events and its edge meets `evidence_threshold`;
otherwise the report says `NO_EXPRESSION` and PAPER mutation stays blocked.
All failures stay in the denominator.

Every tournament event carries one decision timestamp (the snapshot
observation clock) and one frozen exit clock shared by every expression, so
cash, shares, long option, and debit vertical are always compared on identical
event terms. The report records both per event.

## Report claims and data blockers

Every Gate D report carries the frozen claim labels
`NOT_ALPHA_EVIDENCE`, `OPTION_FILL_PROVES_ELIGIBILITY_NOT_SUPERIORITY`,
`UNDERLYING_RETURNS_ARE_NOT_OPTION_PNL`, and `NO_PAPER_MUTATION_AUTHORIZED`,
and always reports `paper_mutation_blocked: true`. An option-required
competition fill proves eligibility and operation, never option superiority.

Missing legitimate option history is reported as `option_history_status:
NOT_RUN` with explicit blocker reason codes — it is never filled with
underlying returns. When eligible option observations exist the status is
`AVAILABLE`.

## Deterministic selection

- Long leg: direction-first — an up view longs the lowest eligible strike, a
  down view longs the highest eligible strike. Remaining ties break on lowest
  ask, then symbol order.
- Short leg: nearest policy-eligible opposite-side strike in the same expiry.
- Eligibility bounds (DTE, absolute delta, open interest, width) come from
  the promoted policy for both vertical legs; 0DTE shortcuts are prohibited
  (`LIFECYCLE_CHECK_FAILED`).
- Packages are matched by exact leg symbols; a missing or mismatched package
  fails with `PACKAGE_UNAVAILABLE`.

Debit-vertical output is permit-boundary compatible: legs validate through the
existing `OptionLeg` OCC rules (long `BUY`/`buy_to_open` first, short
`SELL`/`sell_to_open`, shared underlying/type/expiry, strike ordering by
vertical type, limit below width), extending the existing permit path rather
than creating a second broker path. Every compiled position block declares
`order_type: LIMIT` (market orders are prohibited) and debit verticals declare
`legging: ATOMIC_PACKAGE` (sequential legging is prohibited).

## NO_PACKAGE rejection paths

`DIRECTION_NOT_VALIDATED`, `DECISION_BINDING_MISMATCH`, `POLICY_HASH_MISMATCH`,
`TIME_INCONSISTENT`, `GATE_D_RECEIPT_MISMATCH`, `UNKNOWN_FEED`,
`INDICATIVE_ONLY`, `STALE_QUOTE`,
`CROSSED_QUOTE`, `INSUFFICIENT_SIZE`, `SPREAD_TOO_WIDE`,
`ASYNCHRONOUS_QUOTES`, `UNSUPPORTED_CONTRACT`, `LIFECYCLE_CHECK_FAILED`,
`WIDTH_OUT_OF_BOUNDS`, `PACKAGE_UNAVAILABLE`, `DEBIT_NOT_BELOW_WIDTH`,
`BORROW_LOCATE_MISSING`, `EXPOSURE_BUDGET_EXCEEDED`, and geometry failures all
fail closed with stable reason codes and deterministic canonical artifacts.

## Determinism

Identical decision bytes, snapshot, policy, and clocks produce identical
compiled-expression bytes and identical Gate D report bytes. No wall clock is
read inside the lane; all timestamps are explicit inputs.

## Verification

Exact commands observed during implementation:

```text
command: uv run pytest tests/test_expression_tournament.py -q
result: 63 passed

command: uv run ruff check .
result: All checks passed!
```
