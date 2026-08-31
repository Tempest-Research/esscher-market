"""Contract tests for the monitored PAPER lifecycle (issue #31).

All tests use fakes and make zero real MCP/broker calls. Coverage spans every
lifecycle transition, timeout, restart point, duplicate tick, partial fill,
broker outage, stale truth, non-flat close, and clock jump. Actual PAPER
mutation stays blocked behind the closed mutation gate unless a test explicitly
opens a fake gate.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ringdown_market.contracts.execution_policy import (
    ALPACA_MCP_PROTOCOL_SHA256,
    PAPER_PERMIT_POLICY_SHA256,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    paper_event_run_id,
)
from ringdown_market.execution.models import (
    ClosePermit,
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    VerticalType,
    debit_vertical_permit_id,
)
from ringdown_market.lifecycle import (
    BrokerOrderState,
    ClosedMutationGate,
    CorrelationIdentity,
    FakePaperBroker,
    LifecycleClocks,
    LifecycleReason,
    LifecycleRejected,
    LifecycleState,
    LifecycleTrigger,
    MonitoredPaperLifecycle,
    close_permitted,
    correlation_sha256,
    entry_permitted,
    is_terminal,
    issue_close_permit,
    lifecycle_clocks_bytes,
    lifecycle_clocks_sha256,
    next_lifecycle_state,
    parse_lifecycle_clocks,
    positions_flat,
    reduce_close_order,
    reduce_open_order,
    require_transition,
)
from ringdown_market.lifecycle.broker import AccountTruth, BrokerOrderTruth, PositionTruth
from ringdown_market.lifecycle.clocks import LIFECYCLE_CLOCKS_SCHEMA
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.risk.passport import PassportEventType, verify_passport

NOW = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)
LONG_SYMBOL = "NVDA260918C00180000"
SHORT_SYMBOL = "NVDA260918C00185000"
DECISION_SHA256 = "d" * 64
SNAPSHOT_SHA256 = "b" * 64
EXPRESSION_SHA256 = "x" * 64
POLICY_SHA256 = PAPER_PERMIT_POLICY_SHA256
SOURCE_SHA256 = "a" * 64


class OpenMutationGate:
    """A fake gate used only by tests to simulate the later approval gate."""

    def mutation_permitted(self) -> bool:
        return True


def _open_permit() -> DebitVerticalPermit:
    candidate = DebitVerticalPermit._from_frozen_decision(
        permit_id="UNBOUND",
        event_run_id=paper_event_run_id(DECISION_SHA256),
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256=SNAPSHOT_SHA256,
        decision_sha256=DECISION_SHA256,
        evidence_sha256="e" * 64,
        protocol_sha256=RESEARCH_DECISION_PROTOCOL_SHA256,
        execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
        issued_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(minutes=30),
        vertical_type=VerticalType.BULL_CALL,
        quantity=1,
        limit_price=Decimal("1.25"),
        legs=(
            OptionLeg(
                symbol=LONG_SYMBOL,
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                strike=Decimal("180.00"),
                option_type=OptionType.CALL,
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            OptionLeg(
                symbol=SHORT_SYMBOL,
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                strike=Decimal("185.00"),
                option_type=OptionType.CALL,
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )
    return replace(candidate, permit_id=debit_vertical_permit_id(candidate))


def _clocks(*, source_sha256: str = SOURCE_SHA256) -> LifecycleClocks:
    return LifecycleClocks(
        event_run_id=paper_event_run_id(DECISION_SHA256),
        cohort_id="BMO",
        policy_sha256=POLICY_SHA256,
        source_sha256=source_sha256,
        observation_window_start_at=NOW - timedelta(minutes=10),
        observation_window_end_at=NOW - timedelta(minutes=5),
        entry_deadline_at=NOW + timedelta(minutes=5),
        time_exit_at=NOW + timedelta(minutes=20),
        flattening_deadline_at=NOW + timedelta(minutes=30),
    )


def _correlation(open_permit: DebitVerticalPermit) -> CorrelationIdentity:
    return CorrelationIdentity(
        event_run_id=open_permit.event_run_id,
        snapshot_sha256=SNAPSHOT_SHA256,
        decision_sha256=DECISION_SHA256,
        expression_sha256=EXPRESSION_SHA256,
        reservation_id=f"rsv-{open_permit.event_run_id}",
        open_permit_id=open_permit.permit_id,
    )


def _worker(
    tmp_path,
    *,
    broker: FakePaperBroker | None = None,
    clocks: LifecycleClocks | None = None,
    gate=None,
    clock=lambda: NOW,
) -> MonitoredPaperLifecycle:
    permit = _open_permit()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    reservation_id = ledger.reserve(
        event_id=permit.event_run_id,
        amount=Decimal("125.00"),
        underlying="NVDA",
        now=NOW,
    )
    ledger.record_permit(
        permit_id=permit.permit_id,
        event_id=permit.event_run_id,
        reservation_id=reservation_id,
        permit_sha256="c" * 64,
        now=NOW,
    )
    return MonitoredPaperLifecycle(
        broker=broker or FakePaperBroker(clock=clock),
        ledger=ledger,
        clocks=clocks or _clocks(),
        correlation=_correlation(permit),
        mutation_gate=gate or OpenMutationGate(),
        clock=clock,
        account_id="paper-account",
        account_class="PAPER",
    )


# ---------------------------------------------------------------------------
# Exit-plan clocks
# ---------------------------------------------------------------------------


def test_clocks_round_trip_and_hash() -> None:
    clocks = _clocks()
    raw = lifecycle_clocks_bytes(clocks)
    parsed = parse_lifecycle_clocks(raw)
    assert parsed.event_run_id == clocks.event_run_id
    assert lifecycle_clocks_sha256(clocks) == lifecycle_clocks_sha256(parsed)
    assert parse_lifecycle_clocks(raw).source_verified


def test_clocks_unverified_source_fails_closed() -> None:
    clocks = _clocks(source_sha256="0" * 64)
    with pytest.raises(LifecycleRejected) as caught:
        parse_lifecycle_clocks(lifecycle_clocks_bytes(clocks))
    assert caught.value.reason is LifecycleReason.EXIT_PLAN_UNVERIFIED


def test_clocks_misordered_fails_closed() -> None:
    from ringdown_market.lifecycle.clocks import lifecycle_clocks_payload
    from ringdown_market.strategy.contracts import canonical_json_bytes

    clocks = _clocks()
    payload = lifecycle_clocks_payload(clocks)
    # Break the strict ordering: make the observation start follow the end.
    payload["observation_window_start_at"] = clocks.observation_window_end_at.isoformat().replace(
        "+00:00", "Z"
    )
    payload["observation_window_end_at"] = clocks.observation_window_start_at.isoformat().replace(
        "+00:00", "Z"
    )
    with pytest.raises(LifecycleRejected) as caught:
        parse_lifecycle_clocks(canonical_json_bytes(payload))
    assert caught.value.reason is LifecycleReason.EXIT_PLAN_CLOCKS_MISORDERED


def test_clocks_schema_constant() -> None:
    assert LIFECYCLE_CLOCKS_SCHEMA == "esscher.lifecycle_exit_plan"


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_happy_path_transitions() -> None:
    state = LifecycleState.APPROVED
    state = next_lifecycle_state(state, LifecycleTrigger.OPEN_SUBMIT)
    assert state is LifecycleState.OPEN_SUBMITTED
    state = next_lifecycle_state(state, LifecycleTrigger.OPEN_FILL)
    assert state is LifecycleState.OPEN_FILLED
    state = next_lifecycle_state(state, LifecycleTrigger.HOLD_ELAPSED)
    assert state is LifecycleState.CLOSE_DUE
    state = next_lifecycle_state(state, LifecycleTrigger.CLOSE_SUBMIT)
    assert state is LifecycleState.CLOSE_SUBMITTED
    state = next_lifecycle_state(state, LifecycleTrigger.CLOSE_FILL)
    assert state is LifecycleState.CLOSED_FLAT
    assert is_terminal(state)


def test_opening_branch_transitions() -> None:
    assert (
        next_lifecycle_state(LifecycleState.OPEN_SUBMITTED, LifecycleTrigger.OPEN_PARTIAL_FILL)
        is LifecycleState.OPEN_PARTIAL
    )
    assert (
        next_lifecycle_state(LifecycleState.OPEN_SUBMITTED, LifecycleTrigger.OPEN_CANCEL)
        is LifecycleState.OPEN_CANCELED
    )
    assert (
        next_lifecycle_state(LifecycleState.OPEN_SUBMITTED, LifecycleTrigger.OPEN_AMBIGUOUS)
        is LifecycleState.OPEN_UNKNOWN
    )
    assert (
        next_lifecycle_state(LifecycleState.OPEN_UNKNOWN, LifecycleTrigger.OPEN_FILL)
        is LifecycleState.OPEN_FILLED
    )


def test_unmodeled_transition_fails_closed() -> None:
    assert (
        next_lifecycle_state(LifecycleState.CLOSED_FLAT, LifecycleTrigger.OPEN_SUBMIT)
        is LifecycleState.MANUAL_REQUIRED
    )
    with pytest.raises(LifecycleRejected):
        require_transition(LifecycleState.CLOSED_FLAT, LifecycleTrigger.OPEN_SUBMIT)


def test_entry_and_close_authority() -> None:
    assert entry_permitted(LifecycleState.APPROVED) is True
    assert entry_permitted(LifecycleState.HOLDING) is False
    assert close_permitted(LifecycleState.HOLDING) is True
    assert close_permitted(LifecycleState.MANUAL_REQUIRED) is True
    assert close_permitted(LifecycleState.APPROVED) is False


# ---------------------------------------------------------------------------
# Reducer
# ---------------------------------------------------------------------------


def _ack(status: str, filled: int) -> BrokerOrderTruth:
    return BrokerOrderTruth(
        order_id="order-1",
        client_order_id="client-order-1",
        status=status,
        filled_qty=filled,
        observed_at=NOW,
        permit_id="permit-1",
        open_permit_id="permit-1",
        event_run_id="event-1",
        reservation_id="rsv-event-1",
        correlation_sha256="a" * 64,
        policy_sha256="b" * 64,
        snapshot_sha256="c" * 64,
        account_id="paper-account",
        account_class="PAPER",
        order_class="MULTI_LEG",
        limit_price=Decimal("1.00"),
        legs=(),
    )


def test_reduce_open_order_states() -> None:
    assert reduce_open_order(_ack(BrokerOrderState.FILLED, 1), expected_qty=1) is (
        LifecycleState.OPEN_FILLED
    )
    assert reduce_open_order(_ack(BrokerOrderState.PARTIALLY_FILLED, 1), expected_qty=1) is (
        LifecycleState.OPEN_PARTIAL
    )
    assert reduce_open_order(_ack(BrokerOrderState.CANCELED, 0), expected_qty=1) is (
        LifecycleState.OPEN_CANCELED
    )
    # A fill whose quantity disagrees is ambiguous, not filled.
    assert reduce_open_order(_ack(BrokerOrderState.FILLED, 2), expected_qty=1) is (
        LifecycleState.OPEN_UNKNOWN
    )
    # A terminal order with a nonzero fill cannot be canceled.
    assert reduce_open_order(_ack(BrokerOrderState.CANCELED, 1), expected_qty=1) is (
        LifecycleState.OPEN_UNKNOWN
    )


def test_reduce_close_order_states() -> None:
    flat: tuple[PositionTruth, ...] = ()
    residue = (PositionTruth(symbol=LONG_SYMBOL, qty=Decimal(1), observed_at=NOW),)
    assert reduce_close_order(_ack(BrokerOrderState.FILLED, 1), flat, expected_qty=1) is (
        LifecycleState.CLOSED_FLAT
    )
    assert reduce_close_order(_ack(BrokerOrderState.PARTIALLY_FILLED, 1), flat, expected_qty=1) is (
        LifecycleState.CLOSE_PARTIAL
    )
    # A filled close that still shows a position is manual, not flat.
    assert reduce_close_order(_ack(BrokerOrderState.FILLED, 1), residue, expected_qty=1) is (
        LifecycleState.MANUAL_REQUIRED
    )
    # A fill whose quantity disagrees is manual.
    assert reduce_close_order(_ack(BrokerOrderState.FILLED, 2), flat, expected_qty=1) is (
        LifecycleState.MANUAL_REQUIRED
    )
    assert positions_flat(flat) is True
    assert positions_flat(residue) is False


# ---------------------------------------------------------------------------
# Correlation + close permit
# ---------------------------------------------------------------------------


def test_correlation_identity_is_deterministic() -> None:
    permit = _open_permit()
    identity = _correlation(permit)
    assert correlation_sha256(identity) == correlation_sha256(identity)
    closed = identity.with_close_permit("permit-close-1")
    assert closed.close_permit_id == "permit-close-1"
    assert correlation_sha256(closed) != correlation_sha256(identity)


def test_close_permit_is_deterministic_and_bound() -> None:
    permit = _open_permit()
    issued = issue_close_permit(
        open_permit=permit,
        event_run_id=permit.event_run_id,
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256=permit.snapshot_sha256,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        limit_price=Decimal("-0.40"),
    )
    again = issue_close_permit(
        open_permit=permit,
        event_run_id=permit.event_run_id,
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256=permit.snapshot_sha256,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        limit_price=Decimal("-0.40"),
    )
    assert issued.permit_id == again.permit_id
    assert issued.open_permit_id == permit.permit_id


def test_close_permit_rejects_event_mismatch() -> None:
    permit = _open_permit()
    with pytest.raises(LifecycleRejected):
        issue_close_permit(
            open_permit=permit,
            event_run_id="other-event",
            policy_sha256=PAPER_PERMIT_POLICY_SHA256,
            snapshot_sha256=permit.snapshot_sha256,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            limit_price=Decimal("-0.40"),
        )


@pytest.mark.parametrize(
    ("policy_sha256", "snapshot_sha256"),
    (
        ("a" * 64, SNAPSHOT_SHA256),
        (PAPER_PERMIT_POLICY_SHA256, "f" * 64),
    ),
)
def test_close_permit_rejects_policy_or_snapshot_scope_mismatch(
    policy_sha256, snapshot_sha256
) -> None:
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        issue_close_permit(
            open_permit=permit,
            event_run_id=permit.event_run_id,
            policy_sha256=policy_sha256,
            snapshot_sha256=snapshot_sha256,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            limit_price=Decimal("-0.40"),
        )

    assert caught.value.reason is LifecycleReason.BROKER_TRUTH_MISMATCH


def test_close_permit_rejects_credit_above_the_open_vertical_width() -> None:
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        issue_close_permit(
            open_permit=permit,
            event_run_id=permit.event_run_id,
            policy_sha256=permit.policy_sha256,
            snapshot_sha256=permit.snapshot_sha256,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            limit_price=Decimal("-5.01"),
        )

    assert caught.value.reason is LifecycleReason.UNSUPPORTED_INPUT


# ---------------------------------------------------------------------------
# Worker: opening
# ---------------------------------------------------------------------------


def test_open_fills_with_gate_open(tmp_path) -> None:
    worker = _worker(tmp_path, broker=FakePaperBroker(open_terminal_state=BrokerOrderState.FILLED))
    permit = _open_permit()
    state, order_id = asyncio.run(worker.open(permit))
    assert state is LifecycleState.OPEN_FILLED
    assert order_id


def test_open_blocked_when_mutation_gate_closed(tmp_path) -> None:
    worker = _worker(tmp_path, gate=ClosedMutationGate())
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(_open_permit()))
    assert caught.value.reason is LifecycleReason.MUTATION_GATE_CLOSED


def test_open_rejects_unverified_clocks(tmp_path) -> None:
    worker = _worker(tmp_path, clocks=_clocks(source_sha256="0" * 64))
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(_open_permit()))
    assert caught.value.reason is LifecycleReason.EXIT_PLAN_UNVERIFIED


def test_open_rejects_exit_plan_scope_mismatch_before_submission(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker, clocks=replace(_clocks(), policy_sha256="a" * 64))
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(permit))

    assert caught.value.reason is LifecycleReason.EXIT_PLAN_UNVERIFIED
    assert broker.open_submissions == 0
    assert worker.ledger.lifecycle_intent_for_permit(permit.permit_id) is None


def test_open_rejects_nonpaper_worker_boundary_before_submission(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    worker.account_class = "LIVE"
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(permit))

    assert caught.value.reason is LifecycleReason.UNSUPPORTED_INPUT
    assert broker.open_submissions == 0
    assert worker.ledger.lifecycle_intent_for_permit(permit.permit_id) is None


def test_open_rejects_expired_permit_before_intent_or_submission(tmp_path) -> None:
    late_now = NOW + timedelta(minutes=31)
    late_clocks = LifecycleClocks(
        event_run_id=paper_event_run_id(DECISION_SHA256),
        cohort_id="BMO",
        policy_sha256=POLICY_SHA256,
        source_sha256=SOURCE_SHA256,
        observation_window_start_at=NOW - timedelta(minutes=20),
        observation_window_end_at=NOW - timedelta(minutes=15),
        entry_deadline_at=NOW + timedelta(minutes=40),
        time_exit_at=NOW + timedelta(minutes=50),
        flattening_deadline_at=NOW + timedelta(minutes=60),
    )
    broker = FakePaperBroker(clock=lambda: late_now)
    worker = _worker(tmp_path, broker=broker, clocks=late_clocks, clock=lambda: late_now)
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(permit))

    assert caught.value.reason is LifecycleReason.PERMIT_NOT_ACTIVE
    assert broker.open_submissions == 0
    assert worker.ledger.lifecycle_intent_for_permit(permit.permit_id) is None


def test_open_rejects_tampered_immutable_permit_terms_before_submission(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = replace(_open_permit(), limit_price=Decimal("1.20"))

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(permit))

    assert caught.value.reason is LifecycleReason.BROKER_TRUTH_MISMATCH
    assert broker.open_submissions == 0
    assert worker.ledger.lifecycle_intent_for_permit(permit.permit_id) is None


def test_open_after_entry_deadline_fails_closed(tmp_path) -> None:
    late_clocks = LifecycleClocks(
        event_run_id=paper_event_run_id(DECISION_SHA256),
        cohort_id="BMO",
        policy_sha256=POLICY_SHA256,
        source_sha256=SOURCE_SHA256,
        observation_window_start_at=NOW - timedelta(minutes=20),
        observation_window_end_at=NOW - timedelta(minutes=15),
        entry_deadline_at=NOW - timedelta(minutes=10),
        time_exit_at=NOW + timedelta(minutes=10),
        flattening_deadline_at=NOW + timedelta(minutes=20),
    )
    worker = _worker(tmp_path, clocks=late_clocks)
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(_open_permit()))
    assert caught.value.reason is LifecycleReason.ENTRY_DEADLINE_PASSED


def test_open_partial_fill_stays_partial(tmp_path) -> None:
    broker = FakePaperBroker(
        open_terminal_state=BrokerOrderState.PARTIALLY_FILLED, open_filled_qty=1
    )
    worker = _worker(tmp_path, broker=broker)
    state, _ = asyncio.run(worker.open(_open_permit()))
    assert state is LifecycleState.OPEN_PARTIAL


def test_open_cancel_yields_canceled(tmp_path) -> None:
    broker = FakePaperBroker(open_terminal_state=BrokerOrderState.CANCELED)
    worker = _worker(tmp_path, broker=broker)
    state, _ = asyncio.run(worker.open(_open_permit()))
    assert state is LifecycleState.OPEN_CANCELED


def test_open_outage_fails_closed(tmp_path) -> None:
    broker = FakePaperBroker(open_submit_outage=True)
    worker = _worker(tmp_path, broker=broker)
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(_open_permit()))
    assert caught.value.reason is LifecycleReason.BROKER_OUTAGE


def test_duplicate_open_submission_is_prevented(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    asyncio.run(worker.open(permit))
    first_submissions = broker.open_submissions
    # A second tick with the same permit must not create another submission.
    with pytest.raises(LifecycleRejected):
        asyncio.run(worker.open(permit))
    assert broker.open_submissions == first_submissions


# ---------------------------------------------------------------------------
# Worker: closing
# ---------------------------------------------------------------------------


def _close_permit(open_permit: DebitVerticalPermit) -> ClosePermit:
    return issue_close_permit(
        open_permit=open_permit,
        event_run_id=open_permit.event_run_id,
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256=open_permit.snapshot_sha256,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
        limit_price=Decimal("-0.40"),
    )


def _open_filled(worker: MonitoredPaperLifecycle, permit: DebitVerticalPermit) -> str:
    state, order_id = asyncio.run(worker.open(permit))
    assert state is LifecycleState.OPEN_FILLED
    return order_id


def _advance_to_time_exit(worker: MonitoredPaperLifecycle, broker: FakePaperBroker) -> datetime:
    """Move both deterministic clocks past the frozen time-exit clock."""

    close_now = NOW + timedelta(minutes=21)
    worker.clock = lambda: close_now
    broker.clock = worker.clock
    return close_now


def test_close_rejects_an_opening_without_verified_fill_proof(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id="fake-open-order-1"))

    assert caught.value.reason is LifecycleReason.BROKER_TRUTH_MISMATCH
    assert broker.close_submissions == 0


def test_close_waits_for_the_frozen_time_exit_clock(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id=open_order_id))

    assert caught.value.reason is LifecycleReason.CLOSE_PERMIT_UNAVAILABLE
    assert broker.close_submissions == 0
    assert worker.ledger.lifecycle_intent_for_permit(_close_permit(permit).permit_id) is None


def test_close_rejects_expired_close_permit_before_submission(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)
    _advance_to_time_exit(worker, broker)
    expired_close = issue_close_permit(
        open_permit=permit,
        event_run_id=permit.event_run_id,
        policy_sha256=permit.policy_sha256,
        snapshot_sha256=permit.snapshot_sha256,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=20),
        limit_price=Decimal("-0.40"),
    )

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, expired_close, open_order_id=open_order_id))

    assert caught.value.reason is LifecycleReason.CLOSE_PERMIT_UNAVAILABLE
    assert broker.close_submissions == 0
    assert worker.ledger.lifecycle_intent_for_permit(expired_close.permit_id) is None


def test_close_rejects_a_second_close_permit_after_a_partial_attempt(tmp_path) -> None:
    broker = FakePaperBroker(close_terminal_state=BrokerOrderState.PARTIALLY_FILLED)
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)
    _advance_to_time_exit(worker, broker)
    first_close = _close_permit(permit)

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, first_close, open_order_id=open_order_id))
    assert caught.value.reason is LifecycleReason.CLOSE_ORDER_PARTIAL

    alternate_close = issue_close_permit(
        open_permit=permit,
        event_run_id=permit.event_run_id,
        policy_sha256=permit.policy_sha256,
        snapshot_sha256=permit.snapshot_sha256,
        issued_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=30),
        limit_price=Decimal("-0.40"),
    )
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, alternate_close, open_order_id=open_order_id))

    assert caught.value.reason is LifecycleReason.DUPLICATE_TICK
    assert broker.close_submissions == 1


def test_close_to_flat(tmp_path) -> None:
    broker = FakePaperBroker(positions_flat_after_close=True)
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)
    _advance_to_time_exit(worker, broker)
    state, close_order_id = asyncio.run(
        worker.close(permit, _close_permit(permit), open_order_id=open_order_id)
    )
    assert state is LifecycleState.CLOSED_FLAT
    assert close_order_id


def test_close_partial_fails_closed(tmp_path) -> None:
    broker = FakePaperBroker(close_terminal_state=BrokerOrderState.PARTIALLY_FILLED)
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)
    _advance_to_time_exit(worker, broker)
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id=open_order_id))
    assert caught.value.reason is LifecycleReason.CLOSE_ORDER_PARTIAL


def test_close_non_flat_is_manual(tmp_path) -> None:
    broker = FakePaperBroker(
        positions_flat_after_close=False, residual_position_symbols=(LONG_SYMBOL,)
    )
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)
    _advance_to_time_exit(worker, broker)
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id=open_order_id))
    assert caught.value.reason in {
        LifecycleReason.MANUAL_REQUIRED,
        LifecycleReason.NON_FLAT_CLOSE,
    }


def test_close_outage_fails_closed(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)
    _advance_to_time_exit(worker, broker)
    broker.close_submit_outage = True
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id=open_order_id))
    assert caught.value.reason is LifecycleReason.BROKER_OUTAGE


def test_close_blocked_when_mutation_gate_closed(tmp_path) -> None:
    worker = _worker(tmp_path, gate=ClosedMutationGate())
    permit = _open_permit()
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id="fake-open-order-1"))
    assert caught.value.reason is LifecycleReason.MUTATION_GATE_CLOSED


# ---------------------------------------------------------------------------
# Worker: restart recovery
# ---------------------------------------------------------------------------


def test_recover_open_state_from_broker_truth(tmp_path) -> None:
    broker = FakePaperBroker(open_terminal_state=BrokerOrderState.FILLED)
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    _, order_id = asyncio.run(worker.open(permit))
    recovered = asyncio.run(worker.recover_open_state(order_id, expected_qty=1))
    assert recovered is LifecycleState.OPEN_FILLED


def test_restart_cannot_replay_an_opening_intent(tmp_path) -> None:
    broker = FakePaperBroker(open_terminal_state=BrokerOrderState.FILLED)
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    _open_filled(worker, permit)
    restarted = MonitoredPaperLifecycle(
        broker=broker,
        ledger=worker.ledger,
        clocks=worker.clocks,
        correlation=worker.correlation,
        mutation_gate=OpenMutationGate(),
        clock=worker.clock,
        account_id=worker.account_id,
        account_class=worker.account_class,
        order_class=worker.order_class,
    )

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(restarted.open(permit))

    assert caught.value.reason is LifecycleReason.DUPLICATE_TICK
    assert broker.open_submissions == 1


def test_recover_open_state_requires_the_durable_intent(tmp_path) -> None:
    broker = FakePaperBroker(open_terminal_state=BrokerOrderState.FILLED)
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    worker = _worker(first_dir, broker=broker)
    permit = _open_permit()
    _, order_id = asyncio.run(worker.open(permit))
    restart_dir = tmp_path / "fresh-ledger"
    restart_dir.mkdir()
    restarted = _worker(restart_dir, broker=broker)

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(restarted.recover_open_state(order_id, expected_qty=1))

    assert caught.value.reason is LifecycleReason.RESTART_STATE_INVALID


def test_recover_flatness_from_position_truth(tmp_path) -> None:
    broker = FakePaperBroker(positions_flat_after_close=True)
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)
    _advance_to_time_exit(worker, broker)
    asyncio.run(worker.close(permit, _close_permit(permit), open_order_id=open_order_id))
    recovered = asyncio.run(worker.recover_flatness())
    assert recovered is LifecycleState.CLOSED_FLAT


def test_recover_flatness_manual_when_residual(tmp_path) -> None:
    broker = FakePaperBroker(
        positions_flat_after_close=False, residual_position_symbols=(LONG_SYMBOL,)
    )
    worker = _worker(tmp_path, broker=broker)
    _open_filled(worker, _open_permit())
    recovered = asyncio.run(worker.recover_flatness())
    assert recovered is LifecycleState.MANUAL_REQUIRED


def test_recover_flatness_never_declares_flat_without_a_close_intent(tmp_path) -> None:
    worker = _worker(tmp_path, broker=FakePaperBroker())

    recovered = asyncio.run(worker.recover_flatness())

    assert recovered is LifecycleState.MANUAL_REQUIRED


# ---------------------------------------------------------------------------
# Clock safety
# ---------------------------------------------------------------------------


def test_naive_clock_fails_closed(tmp_path) -> None:
    worker = _worker(tmp_path, clock=lambda: datetime(2026, 9, 11, 14, 0))
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(_open_permit()))
    assert caught.value.reason is LifecycleReason.UNSUPPORTED_INPUT


def test_close_rejects_stale_position_truth(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    open_order_id = _open_filled(worker, permit)
    close_now = _advance_to_time_exit(worker, broker)
    broker.account = AccountTruth(
        account_id="paper-account",
        account_class="PAPER",
        equity=Decimal("100000.00"),
        buying_power=Decimal("100000.00"),
        observed_at=close_now,
    )
    broker.close_truth_overrides = {"observed_at": close_now}
    broker.clock = lambda: close_now - timedelta(seconds=60)
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id=open_order_id))
    assert caught.value.reason is LifecycleReason.STALE_QUOTE


def test_close_after_flattening_deadline_fails_closed(tmp_path) -> None:
    worker = _worker(tmp_path, clock=lambda: NOW + timedelta(minutes=60))
    permit = _open_permit()
    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id="fake-open-order-1"))
    assert caught.value.reason is LifecycleReason.FLATTENING_DEADLINE_PASSED


# ---------------------------------------------------------------------------
# Broker readback binding regressions
# ---------------------------------------------------------------------------


def test_open_persists_fill_only_after_fresh_order_and_position_readback(tmp_path) -> None:
    broker = FakePaperBroker(open_terminal_state=BrokerOrderState.FILLED)
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()

    state, _ = asyncio.run(worker.open(permit))

    assert state is LifecycleState.OPEN_FILLED
    assert broker.order_readbacks == 1
    assert broker.position_readbacks == 1
    assert worker.ledger.fills_for_permit(permit.permit_id)[0]["quantity"] == "1"


def test_passport_persists_the_full_request_before_and_after_each_broker_ack(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    _, open_order_id = asyncio.run(worker.open(permit))
    _advance_to_time_exit(worker, broker)
    close_permit = _close_permit(permit)
    _, close_order_id = asyncio.run(
        worker.close(permit, close_permit, open_order_id=open_order_id)
    )

    events = worker.ledger.passport_events()
    assert verify_passport(events) == len(events)
    for permit_id, order_id in (
        (permit.permit_id, open_order_id),
        (close_permit.permit_id, close_order_id),
    ):
        intended = next(
            event
            for event in events
            if event["event_type"] == PassportEventType.ORDER_INTENDED
            and event["payload"]["permit_id"] == permit_id
        )
        submitted = next(
            event
            for event in events
            if event["event_type"] == PassportEventType.ORDER_SUBMITTED
            and event["payload"]["permit_id"] == permit_id
        )
        for field in (
            "open_permit_id",
            "client_order_id",
            "expected_quantity",
            "limit_price",
            "legs",
            "request_sha256",
        ):
            assert field in intended["payload"]
            assert submitted["payload"][field] == intended["payload"][field]
        assert submitted["payload"]["broker_order_id"] == order_id


def test_open_rejects_declared_fill_when_fresh_readback_is_unavailable(tmp_path) -> None:
    broker = FakePaperBroker(open_terminal_state=BrokerOrderState.FILLED, read_outage=True)
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(permit))

    assert caught.value.reason is LifecycleReason.BROKER_OUTAGE
    assert worker.ledger.fills_for_permit(permit.permit_id) == []


@pytest.mark.parametrize(
    "overrides",
    (
        {"client_order_id": "other-client-order"},
        {"permit_id": "other-permit"},
        {"open_permit_id": "other-open-permit"},
        {"event_run_id": "other-event"},
        {"reservation_id": "other-reservation"},
        {"correlation_sha256": "z" * 64},
        {"policy_sha256": "q" * 64},
        {"snapshot_sha256": "s" * 64},
        {"account_id": "other-paper-account"},
        {"account_class": "LIVE"},
        {"order_class": "SINGLE"},
        {"legs": ()},
    ),
)
def test_open_rejects_mismatched_or_cross_event_broker_truth(tmp_path, overrides) -> None:
    broker = FakePaperBroker(open_terminal_state=BrokerOrderState.FILLED)
    broker.open_truth_overrides = overrides
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(permit))

    assert caught.value.reason is LifecycleReason.BROKER_TRUTH_MISMATCH
    assert worker.ledger.fills_for_permit(permit.permit_id) == []


@pytest.mark.parametrize(
    "overrides",
    (
        {"client_order_id": "other-client-order"},
        {"permit_id": "other-permit"},
        {"open_permit_id": "other-open-permit"},
        {"event_run_id": "other-event"},
        {"reservation_id": "other-reservation"},
        {"correlation_sha256": "z" * 64},
        {"policy_sha256": "q" * 64},
        {"snapshot_sha256": "s" * 64},
        {"account_id": "other-paper-account"},
        {"account_class": "LIVE"},
        {"order_class": "SINGLE"},
        {"legs": ()},
    ),
)
def test_open_rejects_mismatched_position_snapshot_identity(tmp_path, overrides) -> None:
    broker = FakePaperBroker(open_terminal_state=BrokerOrderState.FILLED)
    broker.open_position_overrides = overrides
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.open(permit))

    assert caught.value.reason is LifecycleReason.BROKER_TRUTH_MISMATCH
    assert worker.ledger.fills_for_permit(permit.permit_id) == []


def test_close_rejects_declared_fill_when_fresh_order_readback_is_unavailable(tmp_path) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    _, open_order_id = asyncio.run(worker.open(permit))
    _advance_to_time_exit(worker, broker)
    broker.read_outage = True

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id=open_order_id))

    assert caught.value.reason is LifecycleReason.BROKER_OUTAGE


@pytest.mark.parametrize(
    "overrides",
    (
        {"client_order_id": "other-client-order"},
        {"permit_id": "other-permit"},
        {"open_permit_id": "other-open-permit"},
        {"event_run_id": "other-event"},
        {"reservation_id": "other-reservation"},
        {"correlation_sha256": "z" * 64},
        {"policy_sha256": "q" * 64},
        {"snapshot_sha256": "s" * 64},
        {"account_id": "other-paper-account"},
        {"account_class": "LIVE"},
        {"order_class": "SINGLE"},
        {"legs": ()},
    ),
)
def test_close_rejects_mismatched_or_cross_event_broker_truth(tmp_path, overrides) -> None:
    broker = FakePaperBroker()
    worker = _worker(tmp_path, broker=broker)
    permit = _open_permit()
    _, open_order_id = asyncio.run(worker.open(permit))
    _advance_to_time_exit(worker, broker)
    broker.close_truth_overrides = overrides

    with pytest.raises(LifecycleRejected) as caught:
        asyncio.run(worker.close(permit, _close_permit(permit), open_order_id=open_order_id))

    assert caught.value.reason is LifecycleReason.BROKER_TRUTH_MISMATCH
