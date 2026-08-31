# PAPER account risk kernel (issue #30)

This document describes the isolated account-level risk kernel, durable
reservation ledger, and Trade Passport controls for issue #30. The kernel
authorizes nothing by default: a valid expression still cannot become a permit
when candidate identity, account, portfolio, clock, policy, or lifecycle truth
is unsafe. It is permanently Alpaca PAPER-only and never places orders, touches
real money, or promotes policy.

## Boundary

- Inputs: a pre-frozen candidate, one compiled expression from #29, one
  immutable risk policy, and read-only broker-observed account/position/order
  snapshots.
- Output: one durable reservation plus one-use permit authorization, or a
  stable fail-closed rejection.
- The risk package has no network, broker mutation, MCP tool, account action,
  or wall-clock dependency. Every timestamp is an explicit UTC input and tests
  use fakes only.

The explicit local sequence is:

1. `freeze_candidate(...)` writes a first-write immutable candidate receipt
   binding event ID, candidate ID, active risk-policy hash, decision hash,
   compiled-expression hash, and evidence mode.
2. Successful `startup_reconciliation(...)` is the sole route from a fresh
   `ENTRY_DISABLED` ledger to `ACTIVE`; missing/stale/contradictory, partial,
   or non-flat broker truth stays restrictive.
3. `authorize_entry(...)` requires matching caller event, underlying,
   candidate ID, compiled event, active policy hash, decision hash, and
   expression hash. It atomically reserves budget, issues a permit, and
   appends the reservation/permit passport receipts.
4. An external execution boundary may call `record_submission(...)` only to
   bind an issued permit to one broker order ID. It does not submit an order.
5. `reconcile_fill(..., fill=OrderSnapshot(...))` accepts only a fresh order
   also present in the read-only truth source and a fresh broker clock. It moves
   `ISSUED -> SUBMITTED -> FILLED|CANCELLED`; a caller boolean cannot claim a
   fill or release a reservation.
6. A filled reservation remains consumed and counts against aggregate exposure
   until `reconcile_flat(...)` observes the associated underlying flat with a
   fresh broker clock and fresh broker truth. A cancelled zero-fill order releases
   its reservation directly.

`NOT_RUN` is likewise first-write immutable and always blocks entry. A
reservation-bearing event cannot be overwritten as `NOT_RUN`.

## Risk policy and exposure

The strict canonical risk policy is permanently PAPER-only and has an immutable
hash. It carries account capital, per-event loss budget, aggregate exposure,
daily loss, drawdown, concentration, entry/expression counts, close-only
threshold, and truth maximum age. Unverified constants have no fallback:
`POLICY_UNVERIFIED_CONSTANT` rejects authorization.

Exposure is conservative and Decimal-only: shares use declared exposure, a
long option uses premium at risk, and a debit vertical uses maximum loss. Any
malformed, non-finite, negative, or unknown value is rejected; nothing is
approximated. Concentration combines gross absolute market value and held
reservation exposure by underlying, so short positions cannot offset long or
pending exposure for authorization.

## Durable ledger and concurrency

One standard-library SQLite WAL ledger is the durable authority for candidates,
NOT_RUN records, reservations, permits, submitted order IDs, fills, snapshots,
control state, and the hash-linked passport. Candidate records and abstentions
are immutable. A `BEGIN IMMEDIATE` authorization transaction rechecks the
candidate binding, NOT_RUN state, duplicate event, entry count, open expression
count, and aggregate exposure before it writes the reservation, permit, and
passport receipts. If a passport append fails, the reservation and permit roll
back with it.

Both `RESERVED` and `CONSUMED` reservations count as held exposure. This avoids
reusing risk budget after a fill but before fresh broker truth proves flatness.

## Control states and truth

New ledgers start `ENTRY_DISABLED`. Fresh, consistent, flat startup truth can
explicitly move the state to `ACTIVE`. Stale truth moves it to `ENTRY_DISABLED`,
contradictory or partial-fill truth to `MANUAL_REQUIRED`, and non-flat truth to
`CLOSE_ONLY`. Each state blocks new entries while retaining reconciliation
authority where safe. `KILL` is terminal.

All snapshot and timestamp boundaries reject naive, non-UTC, malformed,
future-dated, or stale values with stable `RiskRejected` reasons rather than
raw type or datetime errors.

## Trade Passport

The Trade Passport is an append-only, hash-linked chain. Candidate freezing,
NOT_RUN, control-state changes, reservations, permits, submitted order IDs,
observed fills, cancellations, and flat reconciliation all receive receipts.
The verifier recomputes every link and reports
`PASSPORT_VERIFICATION_FAILED` for any gap, reorder, or tamper. Passport data
records truth; it never grants authority.

## Testing

Focused tests use fakes only and cover immutable candidate/NOT_RUN replay,
event/underlying/policy/candidate/expression swaps, fresh-start behavior,
stale/contradictory/non-flat/partial truth, UTC/type failures, gross short
concentration, malformed Decimal handling, permit ownership and lifecycle,
broker order identity and replay, consumed exposure until flatness, concurrent
aggregate authorization, atomic passport rollback, migration idempotency, and
passport tamper detection.
