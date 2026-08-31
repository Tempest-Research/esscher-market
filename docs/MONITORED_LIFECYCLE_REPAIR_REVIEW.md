# Monitored PAPER lifecycle repair — security/correctness design review

Historical pre-repair read-only review of branch
`fix/monitored-paper-lifecycle-repair` (corrected-risk base #59 + untracked
reconstructed lifecycle files). It made no edits and no GitHub writes; its
failure evidence and recommendations remain below for audit lineage.

## Verified baseline (real test runs, Python 3.12 venv, since removed)

- `tests/test_monitored_lifecycle.py`: **18 failed / 27 passed**
- `tests/test_risk_kernel.py`: 24/24 passed — the corrected RiskLedger API is stable and pinned.

Failure clusters:
1. `worker._record_open_transition` passes `quantity=str(ack.filled_qty)` to
   `RiskLedger.record_fill`, which requires `Decimal` → `RiskRejected(EXPOSURE_NOT_CALCULABLE)`.
   Breaks 5 pre-existing tests (open fill / partial / cancel / duplicate / recover).
2. `open()` trusts the submit ack when FILLED (readback only when the ack is still working);
   `close()` never calls `read_order`; `open()` never reads positions.
3. No binding verification: `BrokerOrderAck` carries only
   `order_id/client_order_id/status/filled_qty/observed_at`. `LifecycleReason.BROKER_TRUTH_MISMATCH`,
   `FakePaperBroker.order_readbacks / position_readbacks / open_truth_overrides /
   close_truth_overrides` do not exist anywhere in `src/`.

## Recommended smallest change (3 surfaces)

### A. Broker truth data contract (`lifecycle/broker.py`)
Extend `BrokerOrderAck` (or a `BrokerOrderTruth` returned by `read_order`) with echoed binding
fields: `permit_id`, `event_run_id`, `reservation_id`, `policy_sha256`, `snapshot_sha256`
(pinned by the red tests) **plus** `leg_symbols`, `account_id`, `asset_class`, `order_role`
(OPEN/CLOSE) per the legs/account/class/order requirement. `FakePaperBroker`: capture the
permit at `submit_open`/`submit_close`; derive defaults (`reservation_id = f"rsv-{event_run_id}"`
matching the ledger convention; policy/snapshot/legs from the permit); increment
`order_readbacks`/`position_readbacks` in the read methods; apply the override dicts on
readback. No `PaperBroker` protocol signature change needed.

### B. Worker flow (`lifecycle/worker.py`)
`open()`:
1. Gates/clocks/deadline as now.
2. **Durable one-use pre-submit gate**: `ledger.permit_state(permit_id)` — None →
   `PERMIT_NOT_ACTIVE`; non-`ISSUED` → `DUPLICATE_TICK`; verify the row's `event_id` and
   `reservation_id` match the correlation identity (cross-event rejection at rest). Replaces
   the in-memory `_submitted_open_permits` set → survives restart.
3. `submit_open` → ack = identity only, never truth.
4. `ledger.record_submission(event_id, permit_id, broker_order_id=ack.order_id, now)` —
   durable ISSUED→SUBMITTED binding, one broker order per permit, appends `ORDER_SUBMITTED`
   passport (drop the worker's duplicate append).
5. **Exactly one `read_order` + one `read_positions`**; outage → `BROKER_OUTAGE`; stale →
   `STALE_QUOTE`/`CLOCK_JUMP` via existing `_require_positions_fresh`.
6. Verify readback binding vs permit/correlation → mismatch → `BROKER_TRUTH_MISMATCH`
   (new reason), persist nothing.
7. Reduce from the **fresh** ack.
8. Terminal persistence only via `ledger.reconcile_observed_order(...)` with `Decimal` qty and
   status mapping `filled → "FILLED"`, `canceled/expired/rejected (filled=0) → "CANCELED"`;
   partial/unknown persist nothing and return `OPEN_PARTIAL`/`OPEN_UNKNOWN`.

`close()`:
1. Gates/clocks/flattening deadline as now.
2. `submit_close` → ack = identity only.
3. **Fresh `read_order(close_order_id)`** (outage → `BROKER_OUTAGE`) + fresh positions with
   the existing freshness check.
4. Binding verify vs close_permit/open_permit/correlation → `BROKER_TRUTH_MISMATCH`.
5. `reduce_close_order(fresh_ack, positions, expected_qty)`; partial → `CLOSE_ORDER_PARTIAL`;
   ambiguous → `MANUAL_REQUIRED`; flat → passport `RECONCILED`, return `CLOSED_FLAT`.
6. **Do not** call `record_fill` or `release_consumed_after_flat` in `close()` (see hidden
   interactions 4–5).

### C. `lifecycle/reasons.py`
Add `LifecycleReason.BROKER_TRUTH_MISMATCH`.

This satisfies every red assertion: `order_readbacks == 1`, `position_readbacks == 1`,
`fills_for_permit(...)[0]["quantity"] == "1"` (reconcile writes `str(Decimal(1)) == "1"`),
`BROKER_OUTAGE` on read outage, `BROKER_TRUTH_MISMATCH` on all five override classes, and no
fill persisted on any rejection.

## Hidden interactions (must respect)
1. `reconcile_observed_order` is the **only** terminal permit transition and the only path
   that moves the reservation (`RESERVED→CONSUMED` on fill, `→RELEASED` on cancel). Today's
   `record_fill` bypass leaves permit ISSUED + reservation RESERVED forever → **permanent
   exposure leak** (`open_reservation_total` / `open_reservation_count` keep blocking entries
   in `reserve_and_issue_permit`).
2. Forced ordering: `record_submission` requires reservation RESERVED + permit ISSUED;
   `reconcile_observed_order` requires permit SUBMITTED + reservation still RESERVED.
   Sequence: submit → record_submission → reconcile.
3. Fill identity: reconcile uses `fill_id = broker_order_id` (one fill per order,
   replay-safe); the `f"open-{order_id}"` fill_id scheme dies with the `record_fill` removal.
   Status vocab differs: ledger wants `"FILLED"`/`"CANCELED"` uppercase, not lifecycle state
   values (`"OPEN_FILLED"` etc.).
4. `release_consumed_after_flat` requires permit FILLED + reservation CONSUMED — reachable
   only if `open()` reconciled on the same ledger. The close tests call `close()` without a
   prior `open()`, so wiring release into `close()` would reject `EVENT_LIFECYCLE_INVALID`.
   Flat-release belongs to `RiskKernel.reconcile_flat` (own fresh position/order reads,
   partial-order rejection). Document that handoff.
5. **One permit per event**: `record_permit` rejects a second permit for the event → the
   ClosePermit can never be ledger-recorded and never gets a fills row (`record_fill` rejects
   unknown permits). Close persistence is passport-only by design; adding close permits to the
   ledger needs a v3 migration — out of scope for minimal.
6. Passport duplication: `record_submission` / `reconcile_observed_order` already append
   `ORDER_SUBMITTED` / `FILL_OBSERVED` / `RECONCILED`; the worker's manual appends for the
   same facts must be dropped or the chain double-records.
7. Ownership split: worker → `record_submission` + `reconcile_observed_order` directly (tests
   give it only a ledger). `RiskKernel.reconcile_fill` is the same binding plus a fresh-truth
   cross-check — never call both for one order (second call → `PERMIT_LIFECYCLE_INVALID`).

## Implemented repair and current local receipt

The implementation retains the corrected #59 ledger lineage and resolves the
review's failure clusters without any provider, broker, account, credential, or
network action:

1. `lifecycle_intents` now stores a canonical full request JSON and SHA-256,
   binds the open permit/client order/legs/expected quantity, and permits only
   one `OPEN` and one `CLOSE` intent per event. Its `ORDER_INTENDED` and
   post-ack `ORDER_SUBMITTED` passport events carry that same full identity.
2. The worker rejects unbound/fake permit terms, mismatched clock event/policy,
   non-PAPER or non-atomic boundaries, inactive permits, early time-exit closes,
   excessive close credits, duplicate alternate close permits, and all fresh
   account/order/position identity mismatches before a fill or flat result.
3. Restart recovery restores only the hash-checked durable request and requires
   fresh account/order/position proof; absent close intent is
   `MANUAL_REQUIRED`, not a fabricated `CLOSED_FLAT` result.

Local code receipt before this documentation update: `uv run pytest -q` returned
**573 passed** and `uv run ruff check .` returned **All checks passed**. This is
not an independent approval or CI receipt; the exact committed head still needs
the pre-created read-only review child.

## Historical residual risks (resolved by this repair)
- **Crash window** between `submit_open` and `record_submission` leaves a live broker order
  with permit ISSUED → retry resubmits. Mitigate with a deterministic `client_order_id`
  derived from `permit_id` (broker-deduped, recoverable); aligns with the existing
  `RESTART_INTENT_REPLAYED` reason.
- Close-side one-use remains in-memory; smallest durable option is a pre-submit
  `CLOSE_SUBMITTED` passport intent scanned via `ledger.passport_events()` before mutation
  (no schema change; the submissions table has an FK to permits so it cannot hold close
  permits).
- `recover_open_state` reduces without binding verification — worth the same check once truth
  carries binding fields.
