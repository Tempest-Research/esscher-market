"""Adversarial contract tests for the isolated PAPER risk kernel.

All tests use read-only fakes. They make no broker, MCP, account, order, or
network mutation.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from esscher.execution.expression.compiler import (
    CompiledExpression,
    compiled_expression_sha256,
)
from esscher.execution.expression.reasons import ExpressionKind
from esscher.execution.models import (
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    VerticalType,
    debit_vertical_permit_id,
)
from esscher.risk import (
    SCHEMA_VERSION,
    ControlState,
    PassportEventType,
    RiskKernel,
    RiskLedger,
    RiskPolicy,
    RiskReason,
    RiskRejected,
    parse_risk_policy,
    risk_policy_bytes,
    risk_policy_sha256,
    verify_passport,
)
from esscher.risk.snapshots import AccountSnapshot, OrderSnapshot, PositionSnapshot

NOW = datetime(2026, 9, 11, 14, 0, 0, tzinfo=UTC)
EVENT = "KR-2026Q2-EARNINGS"
OTHER_EVENT = "MSFT-2026Q3-EARNINGS"
CANDIDATE = "earnings-v1"
_HASH = "a" * 64


class FakeTruthSource:
    """Read-only broker-observed truth fake; setters only change test fixtures."""

    def __init__(
        self,
        *,
        account: AccountSnapshot | None,
        positions: tuple[PositionSnapshot, ...] = (),
        orders: tuple[OrderSnapshot, ...] = (),
        clock: object = NOW,
    ) -> None:
        self._account = account
        self._positions = positions
        self._orders = orders
        self._clock = clock

    def account(self) -> AccountSnapshot | None:
        return self._account

    def positions(self) -> tuple[PositionSnapshot, ...]:
        return self._positions

    def orders(self) -> tuple[OrderSnapshot, ...]:
        return self._orders

    def broker_clock(self) -> object:
        return self._clock

    def set_orders(self, orders: tuple[OrderSnapshot, ...]) -> None:
        self._orders = orders

    def set_positions(self, positions: tuple[PositionSnapshot, ...]) -> None:
        self._positions = positions

    def set_clock(self, clock: object) -> None:
        self._clock = clock


def _account(
    *,
    equity: str = "100000.00",
    buying_power: str = "100000.00",
    observed_at: datetime | None = None,
) -> AccountSnapshot:
    return AccountSnapshot(
        equity=Decimal(equity),
        buying_power=Decimal(buying_power),
        currency="USD",
        observed_at=observed_at or (NOW - timedelta(seconds=1)),
    )


def _policy(
    *,
    constants_source_sha256: str = _HASH,
    per_event_loss_budget: str = "1000.00",
    aggregate_exposure_limit: str = "5000.00",
    daily_loss_limit: str = "2000.00",
    drawdown_limit: str = "5000.00",
    concentration_limit: str = "3000.00",
    account_capital: str = "100000.00",
    truth_max_age_seconds: int = 30,
    max_entries_per_day: int = 10,
    max_open_expressions: int = 5,
    close_only_equity_threshold: str = "90000.00",
) -> RiskPolicy:
    return RiskPolicy(
        policy_id="PAPER_ACCOUNT_RISK_POLICY_V1",
        version="v1",
        run_mode="PAPER",
        account_capital=Decimal(account_capital),
        per_event_loss_budget=Decimal(per_event_loss_budget),
        aggregate_exposure_limit=Decimal(aggregate_exposure_limit),
        daily_loss_limit=Decimal(daily_loss_limit),
        drawdown_limit=Decimal(drawdown_limit),
        concentration_limit=Decimal(concentration_limit),
        max_entries_per_day=max_entries_per_day,
        max_open_expressions=max_open_expressions,
        close_only_equity_threshold=Decimal(close_only_equity_threshold),
        truth_max_age_seconds=truth_max_age_seconds,
        constants_source_sha256=constants_source_sha256,
    )


def _compiled(
    event_id: str = EVENT,
    *,
    maximum_loss: str = "36.00",
    underlying: str = "KR",
    decision_sha256: str = _HASH,
) -> CompiledExpression:
    long_symbol = f"{underlying}260918C00061000"
    short_symbol = f"{underlying}260918C00062000"
    return CompiledExpression(
        expression_kind=ExpressionKind.DEBIT_VERTICAL,
        event_id=event_id,
        decision_sha256=decision_sha256,
        snapshot_sha256=_HASH,
        policy_sha256=_HASH,
        gate_d_report_sha256=_HASH,
        compiled_at=NOW,
        shares=None,
        long_option=None,
        debit_vertical={
            "underlying": underlying,
            "vertical_type": "BULL_CALL",
            "expiry": "2026-09-18",
            "quantity": 1,
            "order_type": "LIMIT",
            "legging": "ATOMIC_PACKAGE",
            "limit_price": "0.36",
            "limit_price_rule": "PACKAGE_NET_ASK",
            "width": "1",
            "maximum_loss": maximum_loss,
            "package_id": f"{long_symbol}+{short_symbol}",
            "long_leg": {"symbol": long_symbol, "option_type": "CALL", "strike": "61"},
            "short_leg": {"symbol": short_symbol, "option_type": "CALL", "strike": "62"},
        },
    )


def _shares_compiled(event_id: str = EVENT, *, symbol: str = "KR") -> CompiledExpression:
    return CompiledExpression(
        expression_kind=ExpressionKind.SHARES,
        event_id=event_id,
        decision_sha256=_HASH,
        snapshot_sha256=_HASH,
        policy_sha256=_HASH,
        gate_d_report_sha256=_HASH,
        compiled_at=NOW,
        shares={
            "symbol": symbol,
            "side": "BUY",
            "quantity": 1,
            "order_type": "LIMIT",
            "price_rule": "ASK",
            "exposure": "36.00",
            "borrow_locate_sha256": None,
        },
        long_option=None,
        debit_vertical=None,
    )


def _kernel(
    tmp_path,
    *,
    policy: RiskPolicy | None = None,
    truth: FakeTruthSource | None = None,
    ledger: RiskLedger | None = None,
) -> RiskKernel:
    policy = policy or _policy()
    ledger = ledger or RiskLedger(tmp_path / "risk.sqlite3")
    truth = truth or FakeTruthSource(account=_account())
    return RiskKernel(policy, ledger, truth)


def _freeze(
    kernel: RiskKernel,
    compiled: CompiledExpression,
    *,
    candidate_id: str = CANDIDATE,
) -> None:
    kernel.freeze_candidate(
        event_id=compiled.event_id,
        candidate_id=candidate_id,
        compiled=compiled,
        evidence_mode="EVALUATED",
        now=NOW,
    )


def _permit(kernel: RiskKernel, compiled: CompiledExpression) -> DebitVerticalPermit:
    """Create a test-only canonical permit bound to the exact compiled vertical."""

    block = compiled.debit_vertical
    assert block is not None
    long = block["long_leg"]
    short = block["short_leg"]
    assert isinstance(long, dict)
    assert isinstance(short, dict)
    expiry = date.fromisoformat(str(block["expiry"]))
    candidate = DebitVerticalPermit._from_frozen_decision(
        permit_id="UNBOUND",
        event_run_id=compiled.event_id,
        policy_sha256=kernel.policy_sha256,
        snapshot_sha256=compiled.snapshot_sha256,
        decision_sha256=compiled.decision_sha256,
        evidence_sha256=_HASH,
        protocol_sha256=_HASH,
        execution_protocol_sha256=_HASH,
        issued_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=60),
        vertical_type=VerticalType(str(block["vertical_type"])),
        quantity=int(block["quantity"]),
        limit_price=Decimal(str(block["limit_price"])),
        legs=(
            OptionLeg(
                symbol=str(long["symbol"]),
                underlying=str(block["underlying"]),
                expiry=expiry,
                option_type=OptionType(str(long["option_type"])),
                strike=Decimal(str(long["strike"])),
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            OptionLeg(
                symbol=str(short["symbol"]),
                underlying=str(block["underlying"]),
                expiry=expiry,
                option_type=OptionType(str(short["option_type"])),
                strike=Decimal(str(short["strike"])),
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )
    return replace(candidate, permit_id=debit_vertical_permit_id(candidate))


def _authorize(
    kernel: RiskKernel,
    compiled: CompiledExpression,
    *,
    event_id: str | None = None,
    underlying: str = "KR",
    candidate_id: str = CANDIDATE,
):
    return kernel.authorize_entry(
        event_id=event_id or compiled.event_id,
        underlying=underlying,
        candidate_id=candidate_id,
        compiled=compiled,
        permit=_permit(kernel, compiled),
        now=NOW,
    )


def _ready_kernel(tmp_path, **kwargs) -> tuple[RiskKernel, CompiledExpression]:
    kernel = _kernel(tmp_path, **kwargs)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    compiled = _compiled()
    _freeze(kernel, compiled)
    return kernel, compiled


# ---------------------------------------------------------------------------
# Policy, migration, and fail-closed boot
# ---------------------------------------------------------------------------


def test_policy_round_trip_and_hash() -> None:
    policy = _policy()
    raw = risk_policy_bytes(policy)
    assert parse_risk_policy(raw).policy_id == policy.policy_id
    assert risk_policy_sha256(policy) == risk_policy_sha256(parse_risk_policy(raw))


def test_policy_rejects_non_paper_mode_and_duplicate_fields() -> None:
    raw = risk_policy_bytes(_policy()).replace(b'"run_mode":"PAPER"', b'"run_mode":"LIVE_"')
    with pytest.raises(RiskRejected) as non_paper:
        parse_risk_policy(raw)
    assert non_paper.value.reason is RiskReason.POLICY_NOT_PAPER_ONLY

    duplicated = (
        risk_policy_bytes(_policy())
        .decode("utf-8")
        .replace('"version":"v1"', '"version":"v1","version":"v1"', 1)
    )
    with pytest.raises(RiskRejected):
        parse_risk_policy(duplicated.encode("utf-8"))


def test_migration_is_idempotent_and_versioned(tmp_path) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    assert ledger.migrate(now=NOW) == SCHEMA_VERSION
    assert ledger.migrate(now=NOW) == SCHEMA_VERSION

    legacy_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(legacy_path)
    try:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (1, '2026-09-11T14:00:00Z')"
        )
        connection.execute(
            "CREATE TABLE reservations ("
            "reservation_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, amount TEXT NOT NULL, "
            "state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.commit()
    finally:
        connection.close()

    upgraded = RiskLedger(legacy_path)
    try:
        assert upgraded.schema_version() == SCHEMA_VERSION
        columns = {row[1] for row in upgraded._conn.execute("PRAGMA table_info(reservations)")}
        assert "underlying" in columns
        assert upgraded._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='submissions'"
        ).fetchone()
        lifecycle_columns = {
            row[1] for row in upgraded._conn.execute("PRAGMA table_info(lifecycle_intents)")
        }
        assert {
            "open_permit_id",
            "client_order_id",
            "request_sha256",
            "request_json",
        } <= lifecycle_columns
        lifecycle_ddl = upgraded._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='lifecycle_intents'"
        ).fetchone()[0]
        assert "UNIQUE(event_id, phase)" in lifecycle_ddl
    finally:
        upgraded.close()


def test_fresh_ledger_blocks_entries_until_startup_reconciliation(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    compiled = _compiled()
    _freeze(kernel, compiled)

    with pytest.raises(RiskRejected) as blocked:
        _authorize(kernel, compiled)
    assert blocked.value.reason is RiskReason.CONTROL_STATE_BLOCKS_ENTRY
    assert kernel._ledger.get_control_state()[0] is ControlState.ENTRY_DISABLED

    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    approval = _authorize(kernel, compiled)
    assert approval.permit_id
    assert approval.reservation_id


def test_stale_and_contradictory_startup_truth_persist_restrictive_state(tmp_path) -> None:
    stale = _kernel(
        tmp_path / "stale",
        truth=FakeTruthSource(account=_account(observed_at=NOW - timedelta(minutes=5))),
    )
    assert stale.startup_reconciliation(now=NOW) is ControlState.ENTRY_DISABLED
    assert stale._ledger.get_control_state()[1] == "STALE_TRUTH"

    contradictory = _kernel(
        tmp_path / "contradictory",
        truth=FakeTruthSource(account=_account(), clock="not-a-datetime"),
    )
    assert contradictory.startup_reconciliation(now=NOW) is ControlState.MANUAL_REQUIRED
    assert contradictory._ledger.get_control_state()[1] == "CONTRADICTORY_TRUTH"


def test_startup_nonflat_and_partial_truth_never_grant_entry_authority(tmp_path) -> None:
    nonflat = PositionSnapshot(
        underlying="KR",
        quantity=Decimal("1"),
        market_value=Decimal("36"),
        observed_at=NOW - timedelta(seconds=1),
    )
    position_kernel = _kernel(
        tmp_path / "position",
        truth=FakeTruthSource(account=_account(), positions=(nonflat,)),
    )
    assert position_kernel.startup_reconciliation(now=NOW) is ControlState.CLOSE_ONLY
    assert position_kernel._ledger.get_control_state()[1] == "NON_FLAT_STATE"

    partial = OrderSnapshot(
        order_id="old-order",
        symbol="KR",
        status="PARTIALLY_FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW - timedelta(seconds=1),
    )
    partial_kernel = _kernel(
        tmp_path / "partial",
        truth=FakeTruthSource(account=_account(), orders=(partial,)),
    )
    assert partial_kernel.startup_reconciliation(now=NOW) is ControlState.MANUAL_REQUIRED
    assert partial_kernel._ledger.get_control_state()[1] == "PARTIAL_FILL_STATE"


# ---------------------------------------------------------------------------
# Immutable candidate/not-run identity and authorization binding
# ---------------------------------------------------------------------------


def test_candidate_and_not_run_records_are_first_write_immutable(tmp_path) -> None:
    kernel, compiled = _ready_kernel(tmp_path)
    with pytest.raises(RiskRejected) as same_candidate:
        _freeze(kernel, compiled)
    assert same_candidate.value.reason is RiskReason.IMMUTABLE_EVENT_REPLAY
    with pytest.raises(RiskRejected) as conflicting_candidate:
        _freeze(kernel, _compiled(maximum_loss="37.00"))
    assert conflicting_candidate.value.reason is RiskReason.IMMUTABLE_EVENT_REPLAY

    kernel.mark_not_run(event_id=OTHER_EVENT, reason="NO_SIGNAL", now=NOW)
    with pytest.raises(RiskRejected) as same_not_run:
        kernel.mark_not_run(event_id=OTHER_EVENT, reason="NO_SIGNAL", now=NOW)
    assert same_not_run.value.reason is RiskReason.IMMUTABLE_EVENT_REPLAY
    with pytest.raises(RiskRejected) as conflicting_not_run:
        kernel.mark_not_run(event_id=OTHER_EVENT, reason="LATER_DIFFERENT_REASON", now=NOW)
    assert conflicting_not_run.value.reason is RiskReason.IMMUTABLE_EVENT_REPLAY


def test_not_run_blocks_entry_and_cannot_overwrite_reserved_event(tmp_path) -> None:
    kernel, compiled = _ready_kernel(tmp_path)
    kernel.mark_not_run(event_id=EVENT, reason="NO_SIGNAL", now=NOW)
    with pytest.raises(RiskRejected) as denied:
        _authorize(kernel, compiled)
    assert denied.value.reason is RiskReason.NOT_RUN_EVENT

    kernel, compiled = _ready_kernel(tmp_path / "reserved")
    _authorize(kernel, compiled)
    with pytest.raises(RiskRejected) as overwrite:
        kernel.mark_not_run(event_id=EVENT, reason="LATE", now=NOW)
    assert overwrite.value.reason is RiskReason.EVENT_LIFECYCLE_INVALID


def test_authorization_rejects_event_underlying_candidate_and_expression_swaps(tmp_path) -> None:
    kernel, compiled = _ready_kernel(tmp_path)

    wrong_event = _compiled(OTHER_EVENT)
    _freeze(kernel, wrong_event, candidate_id="other-candidate")
    with pytest.raises(RiskRejected) as event_swap:
        _authorize(kernel, wrong_event, event_id=EVENT, candidate_id="other-candidate")
    assert event_swap.value.reason is RiskReason.UNSUPPORTED_INPUT

    with pytest.raises(RiskRejected) as underlying_swap:
        _authorize(kernel, compiled, underlying="MSFT")
    assert underlying_swap.value.reason is RiskReason.UNSUPPORTED_INPUT

    with pytest.raises(RiskRejected) as candidate_swap:
        _authorize(kernel, compiled, candidate_id="different-candidate")
    assert candidate_swap.value.reason is RiskReason.UNSUPPORTED_INPUT

    changed_expression = _compiled(maximum_loss="37.00")
    with pytest.raises(RiskRejected) as expression_swap:
        _authorize(kernel, changed_expression)
    assert expression_swap.value.reason is RiskReason.UNSUPPORTED_INPUT


def test_authorization_requires_pre_frozen_candidate_and_active_risk_policy_hash(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    compiled = _compiled()
    with pytest.raises(RiskRejected) as missing_candidate:
        _authorize(kernel, compiled)
    assert missing_candidate.value.reason is RiskReason.UNSUPPORTED_INPUT

    ledger = RiskLedger(tmp_path / "policy.sqlite3")
    truth = FakeTruthSource(account=_account())
    first = RiskKernel(_policy(), ledger, truth)
    assert first.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze(first, compiled)
    swapped = RiskKernel(_policy(aggregate_exposure_limit="4000.00"), ledger, truth)
    with pytest.raises(RiskRejected) as policy_swap:
        _authorize(swapped, compiled)
    assert policy_swap.value.reason is RiskReason.POLICY_HASH_MISMATCH


def test_non_debit_vertical_expression_cannot_receive_an_opening_permit(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    compiled = _shares_compiled()
    _freeze(kernel, compiled)

    with pytest.raises(RiskRejected) as rejected:
        kernel.authorize_entry(
            event_id=EVENT,
            underlying="KR",
            candidate_id=CANDIDATE,
            compiled=compiled,
            permit=_permit(kernel, _compiled()),
            now=NOW,
        )
    assert rejected.value.reason is RiskReason.UNSUPPORTED_INPUT


def test_risk_rejects_rebound_price_mismatch_before_persisting_a_reservation(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    compiled = _compiled()
    _freeze(kernel, compiled)
    valid = _permit(kernel, compiled)
    altered = replace(valid, limit_price=Decimal("0.37"))
    altered = replace(altered, permit_id=debit_vertical_permit_id(altered))

    with pytest.raises(RiskRejected) as rejected:
        kernel.authorize_entry(
            event_id=EVENT,
            underlying="KR",
            candidate_id=CANDIDATE,
            compiled=compiled,
            permit=altered,
            now=NOW,
        )
    assert rejected.value.reason is RiskReason.UNSUPPORTED_INPUT
    assert kernel.ledger.reservation_state(EVENT) is None
    assert kernel.ledger.permit_for_event(EVENT) is None


def test_reservation_passport_binds_candidate_policy_decision_and_expression(tmp_path) -> None:
    kernel, compiled = _ready_kernel(tmp_path)
    approval = _authorize(kernel, compiled)
    held = next(
        event
        for event in kernel._ledger.passport_events()
        if event["event_type"] == PassportEventType.RESERVATION_HELD.value
    )
    payload = held["payload"]
    assert payload["event_id"] == EVENT
    assert payload["candidate_id"] == CANDIDATE
    assert payload["risk_policy_sha256"] == risk_policy_sha256(kernel._policy)
    assert payload["decision_sha256"] == compiled.decision_sha256
    assert payload["compiled_expression_sha256"] == compiled_expression_sha256(compiled)
    assert payload["permit_id"] == approval.permit_id
    assert verify_passport(kernel._ledger.passport_events()) >= 3


# ---------------------------------------------------------------------------
# UTC/type validation and conservative portfolio accounting
# ---------------------------------------------------------------------------


def test_naive_now_and_broker_clock_fail_as_stable_risk_rejections(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    compiled = _compiled()
    _freeze(kernel, compiled)
    with pytest.raises(RiskRejected) as naive_now:
        kernel.authorize_entry(
            event_id=EVENT,
            underlying="KR",
            candidate_id=CANDIDATE,
            compiled=compiled,
            permit=_permit(kernel, compiled),
            now=NOW.replace(tzinfo=None),
        )
    assert naive_now.value.reason is RiskReason.UNSUPPORTED_INPUT
    with pytest.raises(RiskRejected) as wrong_now_type:
        kernel.startup_reconciliation(now="not-a-datetime")
    assert wrong_now_type.value.reason is RiskReason.UNSUPPORTED_INPUT

    truth = FakeTruthSource(account=_account(), clock=NOW.replace(tzinfo=None))
    kernel = _kernel(tmp_path / "clock", truth=truth)
    kernel._ledger.set_control_state(state=ControlState.ACTIVE, reason="TEST", now=NOW)
    _freeze(kernel, compiled)
    with pytest.raises(RiskRejected) as naive_clock:
        _authorize(kernel, compiled)
    assert naive_clock.value.reason is RiskReason.UNSUPPORTED_INPUT


def test_short_positions_count_at_gross_absolute_concentration(tmp_path) -> None:
    short = PositionSnapshot(
        underlying="KR",
        quantity=Decimal("-10"),
        market_value=Decimal("-2990.00"),
        observed_at=NOW - timedelta(seconds=1),
    )
    truth = FakeTruthSource(account=_account(), positions=(short,))
    kernel = _kernel(tmp_path, truth=truth)
    # Startup correctly refuses non-flat truth. This focused calculation test
    # starts from a persisted active state so it can exercise the gross check.
    kernel._ledger.set_control_state(state=ControlState.ACTIVE, reason="TEST", now=NOW)
    compiled = _compiled()
    _freeze(kernel, compiled)
    with pytest.raises(RiskRejected) as rejected:
        _authorize(kernel, compiled)
    assert rejected.value.reason is RiskReason.CONCENTRATION_LIMIT_BREACHED


def test_ledger_rejects_nonfinite_and_malformed_decimal_amounts(tmp_path) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    with pytest.raises(RiskRejected) as nonfinite:
        ledger.reserve(event_id="nan-event", amount=Decimal("NaN"), underlying="KR", now=NOW)
    assert nonfinite.value.reason is RiskReason.EXPOSURE_NOT_CALCULABLE

    ledger.reserve(event_id="valid-event", amount=Decimal("1"), underlying="KR", now=NOW)
    ledger._conn.execute(
        "UPDATE reservations SET amount='not-a-decimal' WHERE event_id='valid-event'"
    )
    with pytest.raises(RiskRejected) as malformed:
        ledger.open_reservation_total()
    assert malformed.value.reason is RiskReason.EXPOSURE_NOT_CALCULABLE


# ---------------------------------------------------------------------------
# Permit ownership, broker-observed reconciliation, and held exposure
# ---------------------------------------------------------------------------


def test_permit_must_own_same_event_reserved_reservation_and_obey_lifecycle(tmp_path) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    reservation_id = ledger.reserve(
        event_id="event-a", amount=Decimal("1"), underlying="KR", now=NOW
    )
    with pytest.raises(RiskRejected) as cross_event:
        ledger.record_permit(
            permit_id="permit-b",
            event_id="event-b",
            reservation_id=reservation_id,
            permit_sha256=_HASH,
            now=NOW,
        )
    assert cross_event.value.reason is RiskReason.EVENT_LIFECYCLE_INVALID

    ledger.record_permit(
        permit_id="permit-a",
        event_id="event-a",
        reservation_id=reservation_id,
        permit_sha256=_HASH,
        now=NOW,
    )
    with pytest.raises(RiskRejected) as illegal_terminal:
        ledger.update_permit_state(permit_id="permit-a", to_state="FILLED", now=NOW)
    assert illegal_terminal.value.reason is RiskReason.PERMIT_LIFECYCLE_INVALID

    ledger.update_permit_state(permit_id="permit-a", to_state="SUBMITTED", now=NOW)
    with pytest.raises(RiskRejected) as unobserved_terminal:
        ledger.update_permit_state(permit_id="permit-a", to_state="FILLED", now=NOW)
    assert unobserved_terminal.value.reason is RiskReason.PERMIT_LIFECYCLE_INVALID

    with pytest.raises(RiskRejected) as replay:
        ledger.update_permit_state(permit_id="permit-a", to_state="SUBMITTED", now=NOW)
    assert replay.value.reason is RiskReason.PERMIT_LIFECYCLE_INVALID

    with pytest.raises(RiskRejected) as missing:
        ledger.update_permit_state(permit_id="no-such-permit", to_state="SUBMITTED", now=NOW)
    assert missing.value.reason is RiskReason.PERMIT_LIFECYCLE_INVALID


def test_reconcile_requires_fresh_observed_submitted_order_and_persists_fill(tmp_path) -> None:
    truth = FakeTruthSource(account=_account())
    kernel, compiled = _ready_kernel(tmp_path, truth=truth)
    approval = _authorize(kernel, compiled)
    kernel.record_submission(
        event_id=EVENT,
        permit_id=approval.permit_id,
        broker_order_id="order-1",
        now=NOW,
    )
    fill = OrderSnapshot(
        order_id="order-1",
        symbol="KR",
        status="FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW - timedelta(seconds=1),
    )
    truth.set_orders((fill,))
    kernel.reconcile_fill(event_id=EVENT, permit_id=approval.permit_id, fill=fill, now=NOW)

    assert kernel._ledger.permit_state(approval.permit_id) == "FILLED"
    assert kernel._ledger.reservation_state(EVENT) == "CONSUMED"
    assert kernel._ledger.fills_for_permit(approval.permit_id) == [
        {
            "fill_id": "order-1",
            "quantity": "1",
            "status": "FILLED",
            "observed_at": (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        }
    ]
    with pytest.raises(RiskRejected) as replayed_fill:
        kernel.reconcile_fill(event_id=EVENT, permit_id=approval.permit_id, fill=fill, now=NOW)
    assert replayed_fill.value.reason is RiskReason.PERMIT_LIFECYCLE_INVALID


def test_reconcile_rejects_forged_stale_or_cross_event_fill_proof(tmp_path) -> None:
    truth = FakeTruthSource(account=_account())
    kernel, compiled = _ready_kernel(tmp_path, truth=truth)
    approval = _authorize(kernel, compiled)
    kernel.record_submission(
        event_id=EVENT,
        permit_id=approval.permit_id,
        broker_order_id="order-1",
        now=NOW,
    )
    forged = OrderSnapshot(
        order_id="order-1",
        symbol="KR",
        status="FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW - timedelta(seconds=1),
    )
    with pytest.raises(RiskRejected) as unobserved:
        kernel.reconcile_fill(event_id=EVENT, permit_id=approval.permit_id, fill=forged, now=NOW)
    assert unobserved.value.reason is RiskReason.CONTRADICTORY_TRUTH

    stale = OrderSnapshot(
        order_id="order-1",
        symbol="KR",
        status="FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW - timedelta(minutes=5),
    )
    truth.set_orders((stale,))
    with pytest.raises(RiskRejected) as stale_proof:
        kernel.reconcile_fill(event_id=EVENT, permit_id=approval.permit_id, fill=stale, now=NOW)
    assert stale_proof.value.reason is RiskReason.STALE_ORDER_TRUTH

    fresh = OrderSnapshot(
        order_id="order-1",
        symbol="KR",
        status="FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW - timedelta(seconds=1),
    )
    truth.set_orders((fresh,))
    with pytest.raises(RiskRejected) as wrong_event:
        kernel.reconcile_fill(
            event_id=OTHER_EVENT, permit_id=approval.permit_id, fill=fresh, now=NOW
        )
    assert wrong_event.value.reason is RiskReason.EVENT_LIFECYCLE_INVALID


def test_partial_broker_order_preserves_reservation_and_requires_manual_state(tmp_path) -> None:
    truth = FakeTruthSource(account=_account())
    kernel, compiled = _ready_kernel(tmp_path, truth=truth)
    approval = _authorize(kernel, compiled)
    kernel.record_submission(
        event_id=EVENT,
        permit_id=approval.permit_id,
        broker_order_id="order-1",
        now=NOW,
    )
    partial = OrderSnapshot(
        order_id="order-1",
        symbol="KR",
        status="PARTIALLY_FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW - timedelta(seconds=1),
    )
    truth.set_orders((partial,))
    with pytest.raises(RiskRejected) as rejected:
        kernel.reconcile_fill(event_id=EVENT, permit_id=approval.permit_id, fill=partial, now=NOW)
    assert rejected.value.reason is RiskReason.PARTIAL_FILL_STATE
    assert kernel._ledger.get_control_state()[0] is ControlState.MANUAL_REQUIRED
    assert kernel._ledger.permit_state(approval.permit_id) == "SUBMITTED"
    assert kernel._ledger.reservation_state(EVENT) == "RESERVED"


def test_cancel_requires_observed_cancel_and_release_is_not_boolean_controlled(tmp_path) -> None:
    truth = FakeTruthSource(account=_account())
    kernel, compiled = _ready_kernel(tmp_path, truth=truth)
    approval = _authorize(kernel, compiled)
    kernel.record_submission(
        event_id=EVENT,
        permit_id=approval.permit_id,
        broker_order_id="order-1",
        now=NOW,
    )
    canceled = OrderSnapshot(
        order_id="order-1",
        symbol="KR",
        status="CANCELED",
        filled_quantity=Decimal("0"),
        observed_at=NOW - timedelta(seconds=1),
    )
    truth.set_orders((canceled,))
    kernel.reconcile_fill(event_id=EVENT, permit_id=approval.permit_id, fill=canceled, now=NOW)
    assert kernel._ledger.permit_state(approval.permit_id) == "CANCELLED"
    assert kernel._ledger.reservation_state(EVENT) == "RELEASED"


def test_consumed_reservations_hold_budget_until_broker_confirmed_flatness(tmp_path) -> None:
    truth = FakeTruthSource(account=_account())
    policy = _policy(aggregate_exposure_limit="50.00")
    kernel, compiled = _ready_kernel(tmp_path, policy=policy, truth=truth)
    approval = _authorize(kernel, compiled)
    kernel.record_submission(
        event_id=EVENT,
        permit_id=approval.permit_id,
        broker_order_id="order-1",
        now=NOW,
    )
    fill = OrderSnapshot(
        order_id="order-1",
        symbol="KR",
        status="FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW - timedelta(seconds=1),
    )
    truth.set_orders((fill,))
    kernel.reconcile_fill(event_id=EVENT, permit_id=approval.permit_id, fill=fill, now=NOW)
    assert kernel._ledger.open_reservation_total() == Decimal("36.00")

    other = _compiled(OTHER_EVENT, underlying="MSFT")
    _freeze(kernel, other, candidate_id="other-candidate")
    with pytest.raises(RiskRejected) as held:
        _authorize(kernel, other, underlying="MSFT", candidate_id="other-candidate")
    assert held.value.reason is RiskReason.BUDGET_EXCEEDED

    truth.set_clock(NOW + timedelta(minutes=5))
    with pytest.raises(RiskRejected) as stale_flatness:
        kernel.reconcile_flat(event_id=EVENT, permit_id=approval.permit_id, now=NOW)
    assert stale_flatness.value.reason is RiskReason.STALE_CLOCK
    assert kernel._ledger.reservation_state(EVENT) == "CONSUMED"
    truth.set_clock(NOW)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    kernel.reconcile_flat(event_id=EVENT, permit_id=approval.permit_id, now=NOW)
    assert kernel._ledger.open_reservation_total() == Decimal(0)
    second = _authorize(kernel, other, underlying="MSFT", candidate_id="other-candidate")
    assert second.event_id == OTHER_EVENT


# ---------------------------------------------------------------------------
# Concurrency, transaction/passport atomicity, and hash-chain integrity
# ---------------------------------------------------------------------------


def test_two_independent_ledgers_cannot_exceed_aggregate_limit(tmp_path) -> None:
    database = tmp_path / "risk.sqlite3"
    policy = _policy(aggregate_exposure_limit="50.00")
    truth = FakeTruthSource(account=_account())
    bootstrap = RiskKernel(policy, RiskLedger(database), truth)
    assert bootstrap.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    first = _compiled(EVENT)
    second = _compiled(OTHER_EVENT, underlying="MSFT")
    _freeze(bootstrap, first)
    _freeze(bootstrap, second, candidate_id="other-candidate")

    def authorize(event_id: str, underlying: str, candidate_id: str, compiled: CompiledExpression):
        ledger = RiskLedger(database)
        try:
            kernel = RiskKernel(policy, ledger, truth)
            return kernel.authorize_entry(
                event_id=event_id,
                underlying=underlying,
                candidate_id=candidate_id,
                compiled=compiled,
                permit=_permit(kernel, compiled),
                now=NOW,
            )
        except RiskRejected as error:
            return error
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(authorize, EVENT, "KR", CANDIDATE, first),
            executor.submit(authorize, OTHER_EVENT, "MSFT", "other-candidate", second),
        ]
        results = [future.result() for future in futures]

    assert sum(not isinstance(result, RiskRejected) for result in results) == 1
    rejected = next(result for result in results if isinstance(result, RiskRejected))
    assert rejected.reason is RiskReason.BUDGET_EXCEEDED
    ledger = RiskLedger(database)
    try:
        assert ledger.open_reservation_total() == Decimal("36.00")
        assert verify_passport(ledger.passport_events()) >= 4
    finally:
        ledger.close()


def test_authorization_rolls_back_reservation_and_permit_if_passport_append_fails(
    tmp_path, monkeypatch
) -> None:
    kernel, compiled = _ready_kernel(tmp_path)
    before = kernel._ledger.passport_events()

    def fail_append(*args, **kwargs):
        raise RuntimeError("simulated append crash")

    monkeypatch.setattr(kernel._ledger, "_append_passport", fail_append)
    with pytest.raises(RuntimeError, match="simulated append crash"):
        _authorize(kernel, compiled)

    assert kernel._ledger.reservation_state(EVENT) is None
    assert kernel._ledger.permit_for_event(EVENT) is None
    assert kernel._ledger.passport_events() == before


def test_passport_chain_detects_tampering(tmp_path) -> None:
    kernel, compiled = _ready_kernel(tmp_path)
    _authorize(kernel, compiled)
    events = kernel._ledger.passport_events()
    assert verify_passport(events) == len(events)
    tampered = [dict(event) for event in events]
    tampered[-1]["payload"] = {"tampered": True}
    with pytest.raises(RiskRejected) as caught:
        verify_passport(tampered)
    assert caught.value.reason is RiskReason.PASSPORT_VERIFICATION_FAILED
