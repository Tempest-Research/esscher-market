from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.runtime.lifecycle import (
    MAX_OUTAGE_TICKS,
    FakeLifecycleBroker,
    LifecycleState,
    LifecycleWorker,
    OrderStatus,
)

NOW = datetime(2026, 9, 11, 14, 0, 0, tzinfo=UTC)
LEGS = frozenset({"ACME260918C00100000", "ACME260918C00102500"})
EVENT = "rd-event-test"
RESERVATION = "r" * 64


class FakeClock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now += timedelta(**kwargs)


def ledger(tmp_path: Path) -> RiskLedger:
    led = RiskLedger(tmp_path / "risk.db")
    led.reserve(
        reservation_id=RESERVATION,
        event_id="evt-1",
        package_sha256="p" * 64,
        max_loss=Decimal("100.00"),
        now=NOW,
    )
    return led


def worker(
    led: RiskLedger,
    broker: FakeLifecycleBroker,
    clock: FakeClock | None = None,
) -> LifecycleWorker:
    return LifecycleWorker(ledger=led, broker=broker, clock=clock or FakeClock())


def run_to_holding(
    led: RiskLedger, broker: FakeLifecycleBroker, clock: FakeClock
) -> LifecycleWorker:
    machine = worker(led, broker, clock)
    machine.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    assert machine.tick(EVENT) is LifecycleState.OPEN_SUBMITTED
    assert machine.tick(EVENT) is LifecycleState.OPEN_SUBMITTED
    assert machine.tick(EVENT) is LifecycleState.OPEN_FILLED
    assert machine.tick(EVENT) is LifecycleState.HOLDING
    return machine


def test_full_open_hold_close_lifecycle_to_flat(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)

    assert led.reservation(RESERVATION).status == "CONSUMED"
    record = led.lifecycle_state(EVENT)
    assert record["close_due_at"] == clock() + timedelta(minutes=60)

    clock.advance(minutes=59)
    assert machine.tick(EVENT) is LifecycleState.HOLDING
    assert broker.close_submissions == []

    clock.advance(minutes=1)
    assert machine.tick(EVENT) is LifecycleState.CLOSE_DUE
    assert machine.tick(EVENT) is LifecycleState.CLOSE_SUBMITTED
    assert machine.tick(EVENT) is LifecycleState.CLOSE_SUBMITTED
    assert machine.tick(EVENT) is LifecycleState.CLOSED_FLAT

    receipt = machine.receipt(EVENT)
    assert receipt.terminal_state is LifecycleState.CLOSED_FLAT
    assert receipt.opening_client_order_id == f"open-{RESERVATION}"
    assert receipt.closing_client_order_id == f"close-{RESERVATION}"
    assert receipt.fail_code is None
    assert broker.open_submissions == [f"open-{RESERVATION}"]
    assert broker.close_submissions == [f"close-{RESERVATION}"]


def test_filled_package_never_closed_early(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)

    for _ in range(5):
        clock.advance(minutes=5)
        assert machine.tick(EVENT) is LifecycleState.HOLDING
    assert broker.close_submissions == []


def test_terminal_repeats_are_no_ops(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)
    clock.advance(minutes=60)
    for _ in range(4):
        machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.CLOSED_FLAT

    submissions_before = len(broker.open_submissions) + len(broker.close_submissions)
    for _ in range(3):
        assert machine.tick(EVENT) is LifecycleState.CLOSED_FLAT
    assert len(broker.open_submissions) + len(broker.close_submissions) == submissions_before

    machine.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    assert led.lifecycle_state(EVENT)["state"] == "CLOSED_FLAT"


def test_open_partial_becomes_manual(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(
        leg_symbols=LEGS,
        open_path=(OrderStatus.SUBMITTED, OrderStatus.PARTIAL, OrderStatus.PARTIAL),
    )
    clock = FakeClock()
    machine = worker(led, broker, clock)
    machine.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    machine.tick(EVENT)
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.OPEN_PARTIAL
    assert machine.tick(EVENT) is LifecycleState.MANUAL_REQUIRED
    assert machine.receipt(EVENT).fail_code == "OPEN_PARTIAL_UNRESOLVED"


def test_open_partial_completing_to_fill_proceeds(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(
        leg_symbols=LEGS,
        open_path=(OrderStatus.PARTIAL, OrderStatus.FILLED),
    )
    clock = FakeClock()
    machine = worker(led, broker, clock)
    machine.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.OPEN_PARTIAL
    assert machine.tick(EVENT) is LifecycleState.OPEN_FILLED
    assert machine.tick(EVENT) is LifecycleState.HOLDING


def test_open_canceled_releases_reservation(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(
        leg_symbols=LEGS, open_path=(OrderStatus.SUBMITTED, OrderStatus.CANCELED)
    )
    clock = FakeClock()
    machine = worker(led, broker, clock)
    machine.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    machine.tick(EVENT)
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.OPEN_CANCELED
    assert led.reservation(RESERVATION).status == "RELEASED"


def test_unknown_open_order_fails_closed(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS, open_path=(OrderStatus.UNKNOWN,))
    clock = FakeClock()
    machine = worker(led, broker, clock)
    machine.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.MANUAL_REQUIRED
    assert machine.receipt(EVENT).fail_code == "OPEN_ORDER_UNKNOWN"


def test_open_outage_bounded_then_manual(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS, open_outages=MAX_OUTAGE_TICKS)
    clock = FakeClock()
    machine = worker(led, broker, clock)
    machine.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    for _ in range(MAX_OUTAGE_TICKS - 1):
        assert machine.tick(EVENT) is LifecycleState.APPROVED
    assert machine.tick(EVENT) is LifecycleState.MANUAL_REQUIRED
    assert machine.receipt(EVENT).fail_code == "OPEN_SUBMISSION_OUTAGE"
    assert broker.open_submissions == []


def test_open_outage_recovers_when_bounded(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS, open_outages=MAX_OUTAGE_TICKS - 1)
    clock = FakeClock()
    machine = worker(led, broker, clock)
    machine.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    for _ in range(MAX_OUTAGE_TICKS - 1):
        machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.OPEN_SUBMITTED


def test_close_stale_quote_reprices_monotonically_and_boundedly(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS, close_stale_rejections=2)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)
    clock.advance(minutes=60)
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.CLOSE_SUBMITTED
    assert broker.close_submissions == [f"close-{RESERVATION}-r2"]
    machine.tick(EVENT)
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.CLOSED_FLAT


def test_close_repricing_exhaustion_becomes_manual(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS, close_stale_rejections=10)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)
    clock.advance(minutes=60)
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.MANUAL_REQUIRED
    assert machine.receipt(EVENT).fail_code == "CLOSE_REPRICING_EXHAUSTED"
    assert broker.close_submissions == []


def test_close_outage_becomes_manual(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS, close_outages=1)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)
    clock.advance(minutes=60)
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.MANUAL_REQUIRED
    assert machine.receipt(EVENT).fail_code == "CLOSE_SUBMISSION_OUTAGE"


def test_non_flat_after_close_fill_becomes_manual(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS, flat_after_close=False)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)
    clock.advance(minutes=60)
    machine.tick(EVENT)
    machine.tick(EVENT)
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.MANUAL_REQUIRED
    assert machine.receipt(EVENT).fail_code == "NON_FLAT_AFTER_CLOSE"


def test_clock_jump_past_hold_closes_immediately(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)
    clock.advance(hours=3)
    assert machine.tick(EVENT) is LifecycleState.CLOSE_DUE
    assert machine.tick(EVENT) is LifecycleState.CLOSE_SUBMITTED


def test_restart_resumes_from_ledger_truth_without_resubmission(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS)
    clock = FakeClock()
    first = worker(led, broker, clock)
    first.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    assert first.tick(EVENT) is LifecycleState.OPEN_SUBMITTED

    restarted = LifecycleWorker(ledger=led, broker=broker, clock=clock)
    restarted.begin(event_run_id=EVENT, reservation_id=RESERVATION, now=clock())
    assert restarted.tick(EVENT) is LifecycleState.OPEN_SUBMITTED
    assert broker.open_submissions == [f"open-{RESERVATION}"]


def test_duplicate_tick_idempotent_through_ledger(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    broker = FakeLifecycleBroker(leg_symbols=LEGS)
    clock = FakeClock()
    machine = run_to_holding(led, broker, clock)
    clock.advance(minutes=60)
    machine.tick(EVENT)
    machine.tick(EVENT)
    ticks = led.lifecycle_ticks(EVENT)
    assert machine.tick(EVENT) is LifecycleState.CLOSE_SUBMITTED
    machine.tick(EVENT)
    assert machine.tick(EVENT) is LifecycleState.CLOSED_FLAT
    assert len(set(ticks)) == len(ticks)
