"""Contract tests for the PAPER account risk kernel.

All tests use fakes and make no broker/MCP mutation. Concurrent reservations,
retries, restart, stale truth, unknown exposure, drawdown transitions,
duplicate events, partial fills, migration, and passport integrity are covered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ringdown_market.execution.expression.compiler import CompiledExpression
from ringdown_market.execution.expression.reasons import ExpressionKind
from ringdown_market.risk import (
    GENESIS_SHA256,
    ControlState,
    ControlTrigger,
    PassportEventType,
    RiskKernel,
    RiskLedger,
    RiskPolicy,
    RiskReason,
    RiskRejected,
    entry_allowed,
    next_control_state,
    parse_risk_policy,
    risk_policy_bytes,
    risk_policy_sha256,
    verify_passport,
)
from ringdown_market.risk.snapshots import (
    AccountSnapshot,
    OrderSnapshot,
    PositionSnapshot,
)

NOW = datetime(2026, 9, 11, 14, 0, 0, tzinfo=UTC)
_H = "a" * 64


class FakeTruthSource:
    """Read-only fake broker-observed truth source. No mutation."""

    def __init__(
        self,
        *,
        account: AccountSnapshot | None,
        positions: tuple[PositionSnapshot, ...] = (),
        orders: tuple[OrderSnapshot, ...] = (),
        clock: datetime = NOW,
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

    def broker_clock(self) -> datetime:
        return self._clock


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
    constants_source_sha256: str = _H,
    per_event_loss_budget: str = "1000.00",
    aggregate_exposure_limit: str = "5000.00",
    daily_loss_limit: str = "2000.00",
    drawdown_limit: str = "5000.00",
    concentration_limit: str = "3000.00",
    account_capital: str = "100000.00",
    truth_max_age_seconds: int = 30,
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
        max_entries_per_day=10,
        max_open_expressions=5,
        close_only_equity_threshold=Decimal("90000.00"),
        truth_max_age_seconds=truth_max_age_seconds,
        constants_source_sha256=constants_source_sha256,
    )


def _compiled(
    event_id: str = "KR-2026Q2-EARNINGS", *, maximum_loss: str = "36.00"
) -> CompiledExpression:
    return CompiledExpression(
        expression_kind=ExpressionKind.DEBIT_VERTICAL,
        event_id=event_id,
        decision_sha256=_H,
        snapshot_sha256=_H,
        policy_sha256=_H,
        gate_d_report_sha256=_H,
        compiled_at=NOW,
        shares=None,
        long_option=None,
        debit_vertical={
            "underlying": "KR",
            "vertical_type": "BULL_CALL",
            "expiry": "2026-09-18",
            "quantity": 1,
            "order_type": "LIMIT",
            "legging": "ATOMIC_PACKAGE",
            "limit_price": "0.36",
            "limit_price_rule": "PACKAGE_NET_ASK",
            "width": "1",
            "maximum_loss": maximum_loss,
            "package_id": "KR260918C00061000+KR260918C00062000",
            "long_leg": {"symbol": "KR260918C00061000", "option_type": "CALL", "strike": "61"},
            "short_leg": {"symbol": "KR260918C00062000", "option_type": "CALL", "strike": "62"},
        },
    )


def _kernel(tmp_path, *, policy=None, truth=None, ledger=None) -> RiskKernel:
    policy = policy or _policy()
    ledger = ledger or RiskLedger(tmp_path / "risk.sqlite3")
    truth = truth or FakeTruthSource(account=_account())
    return RiskKernel(policy, ledger, truth)


# ---------------------------------------------------------------------------
# Risk policy contract
# ---------------------------------------------------------------------------


def test_policy_round_trip_and_hash() -> None:
    policy = _policy()
    raw = risk_policy_bytes(policy)
    assert parse_risk_policy(raw).policy_id == policy.policy_id
    assert risk_policy_sha256(policy) == risk_policy_sha256(parse_risk_policy(raw))


def test_policy_rejects_non_paper_mode() -> None:
    policy = _policy()
    raw = risk_policy_bytes(policy).replace(b'"run_mode":"PAPER"', b'"run_mode":"LIVE_"')
    with pytest.raises(RiskRejected) as caught:
        parse_risk_policy(raw)
    assert caught.value.reason is RiskReason.POLICY_NOT_PAPER_ONLY


def test_policy_repr_unverified_constants() -> None:
    verified = _policy()
    assert verified.constants_verified is True
    unverified = _policy(constants_source_sha256="")
    assert unverified.constants_verified is False


def test_policy_rejects_duplicate_fields() -> None:
    raw = risk_policy_bytes(_policy())
    text = raw.decode("utf-8")
    duplicated = text.replace('"version":"v1"', '"version":"v1","version":"v1"', 1)
    with pytest.raises(RiskRejected):
        parse_risk_policy(duplicated.encode("utf-8"))


# ---------------------------------------------------------------------------
# Reservation and concurrency
# ---------------------------------------------------------------------------


def test_single_reservation_succeeds(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    approval = kernel.authorize_entry(
        event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
    )
    assert approval.exposure == Decimal("36.00")
    assert approval.control_state is ControlState.ACTIVE


def test_two_concurrent_attempts_cannot_reserve_same_event(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    kernel.authorize_entry(
        event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
    )
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
        )
    assert caught.value.reason is RiskReason.DUPLICATE_EVENT_RESERVATION


def test_distinct_events_can_each_reserve(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    kernel.authorize_entry(
        event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
    )
    kernel.authorize_entry(
        event_id="MSFT-2026Q3-EARNINGS",
        underlying="MSFT",
        compiled=_compiled(event_id="MSFT-2026Q3-EARNINGS"),
        now=NOW,
    )
    assert kernel._ledger.open_reservation_total() == Decimal("72.00")


def test_cannot_exceed_verified_aggregate_budget(tmp_path) -> None:
    kernel = _kernel(tmp_path, policy=_policy(aggregate_exposure_limit="50.00"))
    kernel.authorize_entry(
        event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
    )
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="MSFT-2026Q3-EARNINGS",
            underlying="MSFT",
            compiled=_compiled(event_id="MSFT-2026Q3-EARNINGS"),
            now=NOW,
        )
    assert caught.value.reason is RiskReason.BUDGET_EXCEEDED


def test_cannot_exceed_per_event_loss_budget(tmp_path) -> None:
    kernel = _kernel(tmp_path, policy=_policy(per_event_loss_budget="10.00"))
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
        )
    assert caught.value.reason is RiskReason.BUDGET_EXCEEDED


def test_release_then_retry_succeeds(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    kernel.authorize_entry(
        event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
    )
    kernel.reconcile_fill(event_id="KR-2026Q2-EARNINGS", fully_filled=False, now=NOW)
    assert kernel._ledger.reservation_state("KR-2026Q2-EARNINGS") == "RELEASED"


# ---------------------------------------------------------------------------
# Stale / missing truth
# ---------------------------------------------------------------------------


def test_missing_account_truth_fails_closed(tmp_path) -> None:
    kernel = _kernel(tmp_path, truth=FakeTruthSource(account=None))
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
        )
    assert caught.value.reason is RiskReason.STALE_ACCOUNT_TRUTH


def test_stale_account_truth_fails_closed(tmp_path) -> None:
    stale_account = _account(observed_at=NOW - timedelta(seconds=120))
    kernel = _kernel(tmp_path, truth=FakeTruthSource(account=stale_account), policy=_policy())
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
        )
    assert caught.value.reason is RiskReason.STALE_ACCOUNT_TRUTH


def test_partial_fill_state_fails_closed(tmp_path) -> None:
    partial_order = OrderSnapshot(
        order_id="ord-1",
        symbol="KR",
        status="PARTIALLY_FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW - timedelta(seconds=1),
    )
    kernel = _kernel(tmp_path, truth=FakeTruthSource(account=_account(), orders=(partial_order,)))
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
        )
    assert caught.value.reason is RiskReason.PARTIAL_FILL_STATE


# ---------------------------------------------------------------------------
# Unknown exposure
# ---------------------------------------------------------------------------


def test_unknown_exposure_fails_closed(tmp_path) -> None:
    broken = CompiledExpression(
        expression_kind=ExpressionKind.DEBIT_VERTICAL,
        event_id="KR-2026Q2-EARNINGS",
        decision_sha256=_H,
        snapshot_sha256=_H,
        policy_sha256=_H,
        gate_d_report_sha256=_H,
        compiled_at=NOW,
        shares=None,
        long_option=None,
        debit_vertical={"vertical_type": "BULL_CALL"},
    )
    kernel = _kernel(tmp_path)
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=broken, now=NOW
        )
    assert caught.value.reason in {
        RiskReason.EXPOSURE_NOT_CALCULABLE,
        RiskReason.UNKNOWN_EXPOSURE,
    }


def test_unverified_constants_block_authorization(tmp_path) -> None:
    kernel = _kernel(tmp_path, policy=_policy(constants_source_sha256=""))
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
        )
    assert caught.value.reason is RiskReason.POLICY_UNVERIFIED_CONSTANT


# ---------------------------------------------------------------------------
# Drawdown transitions
# ---------------------------------------------------------------------------


def test_drawdown_breach_transitions_to_entry_disabled(tmp_path) -> None:
    drawdown_account = _account(equity="94000.00", buying_power="94000.00")
    kernel = _kernel(
        tmp_path,
        policy=_policy(drawdown_limit="5000.00", account_capital="100000.00"),
        truth=FakeTruthSource(account=drawdown_account),
    )
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
        )
    assert caught.value.reason is RiskReason.DRAWDOWN_LIMIT_BREACHED
    state, _ = kernel._ledger.get_control_state()
    assert state is ControlState.ENTRY_DISABLED


def test_entry_disabled_state_blocks_subsequent_entries(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    kernel._ledger.set_control_state(state=ControlState.ENTRY_DISABLED, reason="test", now=NOW)
    with pytest.raises(RiskRejected) as caught:
        kernel.authorize_entry(
            event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
        )
    assert caught.value.reason is RiskReason.CONTROL_STATE_BLOCKS_ENTRY


# ---------------------------------------------------------------------------
# Control state machine
# ---------------------------------------------------------------------------


def test_control_state_transitions() -> None:
    assert (
        next_control_state(ControlState.ACTIVE, ControlTrigger.DRAWDOWN_LIMIT_BREACHED)
        is ControlState.ENTRY_DISABLED
    )
    assert (
        next_control_state(ControlState.ACTIVE, ControlTrigger.CONTRADICTORY_TRUTH)
        is ControlState.MANUAL_REQUIRED
    )
    assert (
        next_control_state(ControlState.ACTIVE, ControlTrigger.NON_FLAT_STATE)
        is ControlState.CLOSE_ONLY
    )
    assert next_control_state(ControlState.ACTIVE, ControlTrigger.KILL_REQUEST) is ControlState.KILL
    assert (
        next_control_state(ControlState.ENTRY_DISABLED, ControlTrigger.RESOLVED)
        is ControlState.ACTIVE
    )
    assert entry_allowed(ControlState.ACTIVE) is True
    assert entry_allowed(ControlState.ENTRY_DISABLED) is False
    assert entry_allowed(ControlState.MANUAL_REQUIRED) is False


def test_manual_required_preserves_close_authority() -> None:
    from ringdown_market.risk.controls import close_allowed

    assert close_allowed(ControlState.MANUAL_REQUIRED) is True
    assert close_allowed(ControlState.CLOSE_ONLY) is True
    assert close_allowed(ControlState.KILL) is False


def test_kill_is_terminal() -> None:
    with pytest.raises(RiskRejected):
        next_control_state(ControlState.KILL, ControlTrigger.RESOLVED)


# ---------------------------------------------------------------------------
# Persistence / restart
# ---------------------------------------------------------------------------


def test_reservation_persists_across_restart(tmp_path) -> None:
    db = tmp_path / "risk.sqlite3"
    kernel = _kernel(tmp_path, ledger=RiskLedger(db))
    kernel.authorize_entry(
        event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
    )
    kernel._ledger.close()
    reopened = RiskLedger(db)
    assert reopened.reservation_state("KR-2026Q2-EARNINGS") == "RESERVED"
    assert reopened.open_reservation_total() == Decimal("36.00")
    reopened.close()


def test_migration_is_idempotent_and_versioned(tmp_path) -> None:
    db = tmp_path / "risk.sqlite3"
    ledger = RiskLedger(db)
    version = ledger.schema_version()
    assert version == 1
    # Re-running migration does not duplicate or fail.
    assert ledger.migrate() == version
    ledger.close()


# ---------------------------------------------------------------------------
# Passport integrity
# ---------------------------------------------------------------------------


def test_passport_chain_verifies(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    kernel.authorize_entry(
        event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
    )
    events = kernel._ledger.passport_events()
    assert len(events) >= 1
    assert verify_passport(events) == len(events)
    assert events[0]["prev_sha256"] == GENESIS_SHA256


def test_passport_tamper_detected(tmp_path) -> None:
    kernel = _kernel(tmp_path)
    kernel.authorize_entry(
        event_id="KR-2026Q2-EARNINGS", underlying="KR", compiled=_compiled(), now=NOW
    )
    events = [dict(event) for event in kernel._ledger.passport_events()]
    events[0]["payload"] = {"tampered": True}
    with pytest.raises(RiskRejected) as caught:
        verify_passport(events)
    assert caught.value.reason is RiskReason.PASSPORT_VERIFICATION_FAILED


def test_passport_event_types_are_vocabulary() -> None:
    assert PassportEventType.RESERVATION_HELD.value == "RESERVATION_HELD"
    assert GENESIS_SHA256 == "0" * 64
