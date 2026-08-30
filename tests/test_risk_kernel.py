from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ringdown_market.risk import (
    COMPETITION_START_EQUITY,
    RISK_POLICY_SHA256,
    AccountTruth,
    LedgerDuplicate,
    LedgerStateConflict,
    PackageRiskRequest,
    PositionTruth,
    RiskLedger,
    RiskRejectionReason,
    build_frozen_limits,
    evaluate_package,
)

NOW = datetime(2026, 9, 11, 13, 40, 0, tzinfo=UTC)
PKG_A = "a" * 64
PKG_B = "b" * 64
PKG_C = "c" * 64


def make_request(
    *,
    event_id: str = "evt-1",
    package_sha256: str = PKG_A,
    max_loss: Decimal = Decimal("100.00"),
    order_type: str = "LIMIT",
) -> PackageRiskRequest:
    return PackageRiskRequest(
        event_id=event_id,
        package_sha256=package_sha256,
        max_loss=max_loss,
        order_type=order_type,
        long_symbols=("ACME260918C00100000",),
        short_symbols=("ACME260918C00102500",),
        long_quantities=(Decimal(1),),
        short_quantities=(Decimal(1),),
    )


def fresh_account(equity: Decimal = COMPETITION_START_EQUITY) -> AccountTruth:
    return AccountTruth(equity=equity, observed_at=NOW, raw_sha256="f" * 64)


def ledger(tmp_path: Path) -> RiskLedger:
    return RiskLedger(tmp_path / "risk.db")


def approve(led: RiskLedger, request: PackageRiskRequest):
    return evaluate_package(
        request,
        ledger=led,
        limits=build_frozen_limits(),
        account=fresh_account(),
        positions=(),
        open_orders=(),
        now=NOW,
    )


def test_frozen_limits_use_decimal_and_min_rules() -> None:
    limits = build_frozen_limits()
    assert limits.maximum_loss_per_trade == Decimal("500.00")
    assert limits.daily_loss_budget == Decimal("1000.00")
    assert limits.entry_disable_drawdown == Decimal("2000.00")
    assert limits.hard_kill_drawdown == Decimal("3000.00")

    small = build_frozen_limits(Decimal("50000.00"))
    assert small.maximum_loss_per_trade == Decimal("250.00")
    assert small.daily_loss_budget == Decimal("500.00")


def test_risk_policy_identity_is_deterministic() -> None:
    assert len(RISK_POLICY_SHA256) == 64


def test_approval_persists_reservation_and_one_use_permit(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    verdict = approve(led, make_request())
    assert verdict.approved is True
    assert verdict.reason is None
    reservation = verdict.reservation
    assert reservation is not None

    record = led.reservation(reservation.reservation_id)
    assert record is not None
    assert record.status == "RESERVED"
    assert record.permit_id == reservation.permit_binding

    with pytest.raises(LedgerStateConflict):
        led.bind_permit(
            reservation_id=reservation.reservation_id, permit_id="other-permit", now=NOW
        )

    led.consume(reservation_id=reservation.reservation_id, now=NOW)
    assert led.reservation(reservation.reservation_id).status == "CONSUMED"
    with pytest.raises(LedgerStateConflict):
        led.consume(reservation_id=reservation.reservation_id, now=NOW)


def test_duplicate_event_or_package_rejected(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    assert approve(led, make_request()).approved is True

    same_event = approve(led, make_request(package_sha256=PKG_B))
    assert same_event.approved is False
    assert same_event.reason is RiskRejectionReason.DUPLICATE_EVENT_PACKAGE

    led.release(reservation_id=led.open_reservations()[0].reservation_id, now=NOW)
    same_package = approve(led, make_request(event_id="evt-2"))
    assert same_package.approved is False
    assert same_package.reason is RiskRejectionReason.DUPLICATE_EVENT_PACKAGE


def test_concurrent_reservations_allow_exactly_one_winner(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.db"
    RiskLedger(db_path).close()
    results: list[bool] = []
    lock = threading.Lock()

    def attempt() -> None:
        led = RiskLedger(db_path)
        verdict = approve(led, make_request())
        with lock:
            results.append(verdict.approved)
        led.close()

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    assert results.count(False) == 5


def test_max_loss_per_trade_enforced(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    verdict = approve(led, make_request(max_loss=Decimal("500.01")))
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.MAX_LOSS_EXCEEDED


def test_daily_loss_budget_counts_realized_and_reserved(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    first = approve(led, make_request(max_loss=Decimal("400.00")))
    assert first.approved is True
    led.release(reservation_id=first.reservation.reservation_id, now=NOW)

    verdict = evaluate_package(
        make_request(event_id="evt-2", package_sha256=PKG_B, max_loss=Decimal("400.00")),
        ledger=led,
        limits=build_frozen_limits(),
        account=fresh_account(),
        positions=(),
        open_orders=(),
        now=NOW,
        realized_loss_today=Decimal("700.00"),
    )
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.DAILY_LOSS_BUDGET_EXCEEDED


def test_open_package_limit_enforced(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    assert approve(led, make_request()).approved is True
    verdict = approve(led, make_request(event_id="evt-2", package_sha256=PKG_B))
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.OPEN_PACKAGE_EXISTS


def test_daily_and_period_entry_limits_enforced(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    first = approve(led, make_request(max_loss=Decimal("100.00")))
    led.release(reservation_id=first.reservation.reservation_id, now=NOW)
    second = approve(
        led, make_request(event_id="evt-2", package_sha256=PKG_B, max_loss=Decimal("100.00"))
    )
    led.release(reservation_id=second.reservation.reservation_id, now=NOW)

    third = approve(
        led, make_request(event_id="evt-3", package_sha256=PKG_C, max_loss=Decimal("100.00"))
    )
    assert third.approved is False
    assert third.reason is RiskRejectionReason.DAILY_ENTRY_LIMIT_EXCEEDED


def test_period_entry_limit_enforced(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    for index in range(5):
        led.record_entry(
            event_id=f"past-{index}",
            now=NOW - timedelta(days=index + 1),
        )
    verdict = approve(led, make_request())
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.PERIOD_ENTRY_LIMIT_EXCEEDED


def test_entry_disable_drawdown_transitions_control_state(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    verdict = evaluate_package(
        make_request(),
        ledger=led,
        limits=build_frozen_limits(),
        account=fresh_account(Decimal("97500.00")),
        positions=(),
        open_orders=(),
        now=NOW,
    )
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.ENTRY_DISABLE_DRAWDOWN
    assert led.current_control_state()[0] == "ENTRY_DISABLED"

    blocked = evaluate_package(
        make_request(event_id="evt-2", package_sha256=PKG_B),
        ledger=led,
        limits=build_frozen_limits(),
        account=fresh_account(),
        positions=(),
        open_orders=(),
        now=NOW,
    )
    assert blocked.approved is False
    assert blocked.reason is RiskRejectionReason.CONTROL_STATE_BLOCKS_ENTRIES


def test_hard_kill_drawdown_forces_close_only(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    verdict = evaluate_package(
        make_request(),
        ledger=led,
        limits=build_frozen_limits(),
        account=fresh_account(Decimal("96000.00")),
        positions=(),
        open_orders=(),
        now=NOW,
    )
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.HARD_KILL_DRAWDOWN
    assert led.current_control_state()[0] == "CLOSE_ONLY"


def test_missing_or_stale_truth_fails_closed(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    missing = evaluate_package(
        make_request(),
        ledger=led,
        limits=build_frozen_limits(),
        account=None,
        positions=(),
        open_orders=(),
        now=NOW,
    )
    assert missing.approved is False
    assert missing.reason is RiskRejectionReason.MISSING_ACCOUNT_TRUTH

    stale_account = AccountTruth(
        equity=COMPETITION_START_EQUITY,
        observed_at=NOW - timedelta(seconds=61),
        raw_sha256="f" * 64,
    )
    stale = evaluate_package(
        make_request(),
        ledger=led,
        limits=build_frozen_limits(),
        account=stale_account,
        positions=(),
        open_orders=(),
        now=NOW,
    )
    assert stale.approved is False
    assert stale.reason is RiskRejectionReason.STALE_TRUTH
    assert led.current_control_state()[0] == "ENTRY_DISABLED"


def test_unknown_exposure_disables_entries(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    ghost = PositionTruth(symbol="GHOST", quantity=Decimal(3), observed_at=NOW)
    verdict = evaluate_package(
        make_request(),
        ledger=led,
        limits=build_frozen_limits(),
        account=fresh_account(),
        positions=(ghost,),
        open_orders=(),
        now=NOW,
    )
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.UNKNOWN_EXPOSURE
    assert led.current_control_state()[0] == "ENTRY_DISABLED"


def test_market_orders_forbidden_zero_tolerance(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    verdict = approve(led, make_request(order_type="MARKET"))
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.MARKET_ORDER_FORBIDDEN
    assert led.current_control_state()[0] == "ENTRY_DISABLED"


def test_naked_short_forbidden_zero_tolerance(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    request = PackageRiskRequest(
        event_id="evt-1",
        package_sha256=PKG_A,
        max_loss=Decimal("100.00"),
        order_type="LIMIT",
        long_symbols=(),
        short_symbols=("ACME260918C00102500",),
        long_quantities=(),
        short_quantities=(Decimal(1),),
    )
    verdict = approve(led, request)
    assert verdict.approved is False
    assert verdict.reason is RiskRejectionReason.NAKED_SHORT_FORBIDDEN
    assert led.current_control_state()[0] == "ENTRY_DISABLED"


def test_restart_resumes_from_ledger_truth(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.db"
    led = RiskLedger(db_path)
    verdict = approve(led, make_request())
    assert verdict.approved is True
    led.set_control_state(state="ENTRY_DISABLED", reason="test", now=NOW)
    led.close()

    reopened = RiskLedger(db_path)
    open_records = reopened.open_reservations()
    assert len(open_records) == 1
    assert open_records[0].reservation_id == verdict.reservation.reservation_id
    assert reopened.current_control_state()[0] == "ENTRY_DISABLED"
    reopened.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    led.record_migrated_event(
        event_run_id="rd-event-legacy", lifecycle="CLOSED_FLAT", updated_at=NOW
    )
    led.record_migrated_event(
        event_run_id="rd-event-legacy", lifecycle="CLOSED_FLAT", updated_at=NOW
    )
    assert led.migrated_events() == ("rd-event-legacy",)


def test_duplicate_reservation_rejected_at_ledger_level(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    led.reserve(
        reservation_id="r-1",
        event_id="evt-1",
        package_sha256=PKG_A,
        max_loss=Decimal("10.00"),
        now=NOW,
    )
    with pytest.raises(LedgerDuplicate):
        led.reserve(
            reservation_id="r-2",
            event_id="evt-1",
            package_sha256=PKG_A,
            max_loss=Decimal("10.00"),
            now=NOW,
        )


def test_release_then_reuse_identity_still_duplicate(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    led.reserve(
        reservation_id="r-1",
        event_id="evt-1",
        package_sha256=PKG_A,
        max_loss=Decimal("10.00"),
        now=NOW,
    )
    led.release(reservation_id="r-1", now=NOW)
    with pytest.raises(LedgerDuplicate):
        led.reserve(
            reservation_id="r-3",
            event_id="evt-1",
            package_sha256=PKG_A,
            max_loss=Decimal("10.00"),
            now=NOW,
        )
