# PAPER account risk kernel (issue #30)

This document describes the account-level risk kernel, durable reservation
ledger, and Trade Passport controls added for issue #30. The kernel authorizes
nothing by default: a valid expression still cannot become a permit when
account, portfolio, clock, quote, policy, or lifecycle truth is unsafe. It is
permanently Alpaca PAPER-only and never places orders, touches real money, or
promotes policy.

## Boundary

- Inputs: one compiled expression from #29, one immutable risk policy, and
  broker-observed account/position/order truth.
- Outputs: one durable reservation + one-use permit authorization, or a stable
  fail-closed rejection. Strategy and expression compilation cannot bypass the
  kernel: compilation has no reservation authority.
- No network, broker mutation, MCP tool, or wall clock is used. Every
  timestamp is an explicit input. Tests use fakes only.

## Risk policy

The risk policy is strict canonical data with an immutable identifier/hash
(`esscher.paper_account_risk_policy` v1). It is permanently PAPER-only, uses
Decimal arithmetic, and carries the account capital, per-event loss budget,
aggregate exposure limit, daily-loss limit, drawdown limit, concentration
limit, entry/expression counts, close-only threshold, and truth-max-age. The
policy binds its constants to an approved source via
`constants_source_sha256`; when the constants are unverified there is no
fallback — the kernel fails closed with `POLICY_UNVERIFIED_CONSTANT`.
Illustrative values are never approved defaults.

## Exposure calculations

Exposure is the conservative worst case of the expression, computed in Decimal
from the compiled expression: cash is zero, shares use the declared exposure,
a long option uses premium-at-risk, and a debit vertical uses its maximum
loss. Any expression whose exposure cannot be calculated conservatively is
rejected with `EXPOSURE_NOT_CALCULABLE`/`UNKNOWN_EXPOSURE`; nothing is
approximated.

## Durable ledger

One standard-library SQLite WAL ledger is the single source of truth for
candidate identity, policy/source hashes, decisions, expressions, account
snapshots, reservations, permits, submissions, fills, positions,
reconciliations, control state, evidence mode, and `NOT_RUN`. A reservation and
one-use permit are persisted before any mutation, and released only after
fill/cancel reconciliation. Migrations are deterministic and recorded; existing
attempt state delegates here so there are no split-brain writes.

Reservations are one per event, enforced by a `UNIQUE(event_id)` constraint
inside a `BEGIN IMMEDIATE` transaction, so two concurrent attempts cannot
reserve the same event.

## Broker-observed truth and staleness

Account, position, and order snapshots carry their observation time. Missing,
stale (older than the policy truth-max-age), future-dated, or contradictory
truth fails closed with `STALE_*_TRUTH`/`CONTRADICTORY_TRUTH`. Broker PAPER
PnL and conservative shadow PnL remain separate fields and separate claims.

## Control states

Stale, contradictory, unknown, partial-fill, or non-flat truth moves the
kernel into `ENTRY_DISABLED`, `CLOSE_ONLY`, or `MANUAL_REQUIRED`. A disabled
or manual-required state blocks new entries while preserving close and
reconciliation authority. `KILL` is terminal and only a startup
reconciliation can leave it. Transitions only restrict authority; they never
grant it.

## Trade Passport

The Trade Passport is an append-only hash-linked chain covering candidates,
abstentions, rejections, decisions, reservations, permits, orders, fills,
closes, reconciliation, and control-state changes. The deterministic verifier
recomputes every link; any gap, reorder, or tamper fails with
`PASSPORT_VERIFICATION_FAILED`. The passport records truth; it grants no
authority.

## Testing

All tests use fakes and make no broker/MCP mutation. Coverage includes
concurrent reservations, retries/release, restart persistence, stale truth,
unknown exposure, drawdown transitions, duplicate events, partial fills,
migration idempotency, control-state transitions, and passport integrity.
