from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ringdown_market.execution.models import (
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    VerticalType,
    debit_vertical_permit_id,
)
from ringdown_market.runtime.autonomous import (
    ActiveLifecycleIdentity,
    AutonomousOpportunity,
    AutonomousSessionArm,
)
from ringdown_market.runtime.option_events import (
    AssetClass,
    EvidenceClass,
    NormalizedOptionEvent,
    OptionActivityCoverage,
    OptionEventConflict,
    OptionEventJournal,
    OptionEventKind,
    OptionEventRejected,
    OptionEventStatus,
    OptionLifecycleBinding,
    OptionPortfolioObservation,
    OptionReconciliationState,
    PortfolioPosition,
    normalized_option_event_bytes,
    option_activity_coverage_bytes,
    option_event_reconciliation_receipt_bytes,
    option_lifecycle_binding_bytes,
    option_portfolio_observation_bytes,
    parse_normalized_option_event,
    parse_option_activity_coverage,
    parse_option_event_reconciliation_receipt,
    parse_option_lifecycle_binding,
    parse_option_portfolio_observation,
    reconcile_option_events,
)

ACTIVATED_AT = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)
EXPIRY_DATE = date(2026, 9, 18)
EXPIRY_CLOSE = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)
ACTIVITY_HORIZON = datetime(2026, 9, 21, 13, 31, tzinfo=UTC)
LONG_SYMBOL = "NVDA260918C00180000"
SHORT_SYMBOL = "NVDA260918C00185000"
PUT_LONG_SYMBOL = "NVDA260918P00185000"
PUT_SHORT_SYMBOL = "NVDA260918P00180000"
ACCOUNT_SHA256 = "a" * 64


def _arm(*, account_sha256: str = ACCOUNT_SHA256) -> AutonomousSessionArm:
    return AutonomousSessionArm.for_trading_date(
        session_id="ESSCHER-20260918",
        session_date=EXPIRY_DATE,
        release_code_sha256="b" * 64,
        account_fingerprint_sha256=account_sha256,
    )


def _opportunity(
    arm: AutonomousSessionArm,
    *,
    opportunity_id: str = "OPPORTUNITY-OPTION-01",
) -> AutonomousOpportunity:
    return AutonomousOpportunity.for_window(
        arm=arm,
        window_id=arm.windows[0].window_id,
        opportunity_id=opportunity_id,
        strategy_context_sha256="c" * 64,
        candidate_id=arm.windows[0].candidate_ids[0],
    )


def _permit(
    arm: AutonomousSessionArm,
    opportunity: AutonomousOpportunity,
    *,
    option_type: OptionType = OptionType.CALL,
) -> DebitVerticalPermit:
    if option_type is OptionType.CALL:
        vertical_type = VerticalType.BULL_CALL
        long_symbol, long_strike = LONG_SYMBOL, Decimal("180")
        short_symbol, short_strike = SHORT_SYMBOL, Decimal("185")
    else:
        vertical_type = VerticalType.BEAR_PUT
        long_symbol, long_strike = PUT_LONG_SYMBOL, Decimal("185")
        short_symbol, short_strike = PUT_SHORT_SYMBOL, Decimal("180")
    candidate = DebitVerticalPermit._from_frozen_decision(
        permit_id="UNBOUND",
        event_run_id=opportunity.opportunity_id,
        policy_sha256="d" * 64,
        snapshot_sha256="e" * 64,
        decision_sha256="f" * 64,
        evidence_sha256="1" * 64,
        protocol_sha256="2" * 64,
        execution_protocol_sha256=arm.execution_protocol_sha256,
        issued_at=ACTIVATED_AT - timedelta(minutes=2),
        expires_at=ACTIVATED_AT + timedelta(minutes=2),
        vertical_type=vertical_type,
        quantity=1,
        limit_price=Decimal("1.25"),
        legs=(
            OptionLeg(
                symbol=long_symbol,
                underlying="NVDA",
                expiry=EXPIRY_DATE,
                option_type=option_type,
                strike=long_strike,
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            OptionLeg(
                symbol=short_symbol,
                underlying="NVDA",
                expiry=EXPIRY_DATE,
                option_type=option_type,
                strike=short_strike,
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )
    return replace(candidate, permit_id=debit_vertical_permit_id(candidate))


def _observation(
    arm: AutonomousSessionArm,
    *,
    observed_at: datetime = ACTIVATED_AT,
    long_quantity: str = "1",
    short_quantity: str = "-1",
    underlying_quantity: str = "10",
    evidence_class: EvidenceClass = EvidenceClass.SYNTHETIC_FIXTURE,
    source_sha256: str = "3" * 64,
    long_symbol: str = LONG_SYMBOL,
    short_symbol: str = SHORT_SYMBOL,
) -> OptionPortfolioObservation:
    positions = []
    for asset_class, symbol, quantity in (
        (AssetClass.OPTION, long_symbol, Decimal(long_quantity)),
        (AssetClass.OPTION, short_symbol, Decimal(short_quantity)),
        (AssetClass.EQUITY, "NVDA", Decimal(underlying_quantity)),
    ):
        if quantity:
            positions.append(
                PortfolioPosition(
                    asset_class=asset_class,
                    symbol=symbol,
                    quantity=quantity,
                )
            )
    return OptionPortfolioObservation.create(
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
        execution_protocol_sha256=arm.execution_protocol_sha256,
        observed_at=observed_at,
        positions=positions,
        source_payload_sha256=source_sha256,
        evidence_class=evidence_class,
    )


def _binding(
    *,
    opportunity_id: str = "OPPORTUNITY-OPTION-01",
    lifecycle_id: str = "LIFECYCLE-OPTION-01",
    expiration_activity_horizon: datetime = ACTIVITY_HORIZON,
    calendar_sha256: str = "4" * 64,
    option_type: OptionType = OptionType.CALL,
) -> tuple[AutonomousSessionArm, OptionLifecycleBinding]:
    arm = _arm()
    opportunity = _opportunity(arm, opportunity_id=opportunity_id)
    lifecycle = ActiveLifecycleIdentity.for_candidate(
        arm=arm,
        opportunity=opportunity,
        lifecycle_id=lifecycle_id,
    )
    long_symbol = LONG_SYMBOL if option_type is OptionType.CALL else PUT_LONG_SYMBOL
    short_symbol = SHORT_SYMBOL if option_type is OptionType.CALL else PUT_SHORT_SYMBOL
    binding = OptionLifecycleBinding.create(
        arm=arm,
        lifecycle=lifecycle,
        permit=_permit(arm, opportunity, option_type=option_type),
        reservation_id=f"RSV-{lifecycle_id}",
        activation_observation=_observation(
            arm,
            long_symbol=long_symbol,
            short_symbol=short_symbol,
        ),
        expiration_session_date=EXPIRY_DATE,
        expiration_session_close=EXPIRY_CLOSE,
        expiration_activity_horizon=expiration_activity_horizon,
        calendar_sha256=calendar_sha256,
    )
    return arm, binding


def _coverage(
    arm: AutonomousSessionArm,
    *,
    observed_at: datetime,
    events: tuple[NormalizedOptionEvent, ...] = (),
    complete: bool = True,
    window_start: datetime = ACTIVATED_AT,
    evidence_class: EvidenceClass = EvidenceClass.SYNTHETIC_FIXTURE,
) -> OptionActivityCoverage:
    return OptionActivityCoverage.create(
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
        execution_protocol_sha256=arm.execution_protocol_sha256,
        window_start=window_start,
        window_end=observed_at,
        observed_at=observed_at,
        complete=complete,
        event_sha256s=tuple(event.event_sha256 for event in events),
        source_payload_sha256="5" * 64,
        evidence_class=evidence_class,
    )


def _event(
    arm: AutonomousSessionArm,
    *,
    activity_id: str,
    kind: OptionEventKind,
    option_symbol: str,
    observed_at: datetime,
    underlying_delta: str = "0",
    cash_delta: str = "0",
    status: OptionEventStatus = OptionEventStatus.EXECUTED,
    replacement_symbol: str | None = None,
    evidence_class: EvidenceClass = EvidenceClass.SYNTHETIC_FIXTURE,
    source_sha256: str = "6" * 64,
) -> NormalizedOptionEvent:
    return NormalizedOptionEvent.create(
        activity_id=activity_id,
        kind=kind,
        status=status,
        option_symbol=option_symbol,
        contracts=1,
        effective_date=EXPIRY_DATE,
        observed_at=observed_at,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
        execution_protocol_sha256=arm.execution_protocol_sha256,
        underlying_symbol="NVDA",
        underlying_quantity_delta=Decimal(underlying_delta),
        cash_delta=Decimal(cash_delta),
        replacement_symbol=replacement_symbol,
        source_payload_sha256=source_sha256,
        evidence_class=evidence_class,
    )


def _expiry_events(
    arm: AutonomousSessionArm,
    *,
    observed_at: datetime,
    long_symbol: str = LONG_SYMBOL,
    short_symbol: str = SHORT_SYMBOL,
) -> tuple[NormalizedOptionEvent, NormalizedOptionEvent]:
    return (
        _event(
            arm,
            activity_id="ACTIVITY-EXPIRY-LONG",
            kind=OptionEventKind.EXPIRY,
            option_symbol=long_symbol,
            observed_at=observed_at,
        ),
        _event(
            arm,
            activity_id="ACTIVITY-EXPIRY-SHORT",
            kind=OptionEventKind.EXPIRY,
            option_symbol=short_symbol,
            observed_at=observed_at,
        ),
    )


def test_contracts_are_canonical_self_hashing_and_strict() -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=1)
    observation = _observation(arm, observed_at=observed_at)
    event = _event(
        arm,
        activity_id="ACTIVITY-ASSIGNMENT-01",
        kind=OptionEventKind.ASSIGNMENT,
        option_symbol=SHORT_SYMBOL,
        observed_at=observed_at,
        underlying_delta="-100",
        cash_delta="18500",
    )
    coverage = _coverage(arm, observed_at=observed_at, events=(event,))

    for value, serializer, parser in (
        (observation, option_portfolio_observation_bytes, parse_option_portfolio_observation),
        (coverage, option_activity_coverage_bytes, parse_option_activity_coverage),
        (binding, option_lifecycle_binding_bytes, parse_option_lifecycle_binding),
        (event, normalized_option_event_bytes, parse_normalized_option_event),
    ):
        raw = serializer(value)
        assert (
            raw
            == json.dumps(
                json.loads(raw),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        assert parser(raw) == value
        with pytest.raises(OptionEventRejected):
            parser(raw + b"\n")


def test_unchanged_positions_are_active_only_with_complete_activity_coverage() -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=1)
    observation = _observation(arm, observed_at=observed_at)

    active = reconcile_option_events(
        binding=binding,
        current_observation=observation,
        activity_coverage=_coverage(arm, observed_at=observed_at),
        events=(),
    )
    assert active.state is OptionReconciliationState.ACTIVE_UNCHANGED
    assert active.long_option_quantity == 1
    assert active.short_option_quantity == -1
    assert active.underlying_quantity == 10
    assert active.underlying_quantity_delta == 0
    assert active.event_cash_delta == 0
    assert active.broker_connectivity_evidence == "NOT_BROKER_CONNECTIVITY_EVIDENCE"
    assert active.alpha_evidence == "NOT_ALPHA_EVIDENCE"

    incomplete = reconcile_option_events(
        binding=binding,
        current_observation=observation,
        activity_coverage=_coverage(arm, observed_at=observed_at, complete=False),
        events=(),
    )
    assert incomplete.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
    assert "ACCOUNT_ACTIVITY_COVERAGE_INCOMPLETE" in incomplete.reason_codes


@pytest.mark.parametrize(
    ("kind", "symbol", "long_quantity", "short_quantity", "underlying", "delta", "cash"),
    (
        (OptionEventKind.ASSIGNMENT, SHORT_SYMBOL, "1", "0", "-90", "-100", "18500"),
        (OptionEventKind.EXERCISE, LONG_SYMBOL, "0", "-1", "110", "100", "-18000"),
    ),
)
def test_assignment_and_exercise_recompute_exposure(
    kind,
    symbol,
    long_quantity,
    short_quantity,
    underlying,
    delta,
    cash,
) -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=2)
    event = _event(
        arm,
        activity_id=f"ACTIVITY-{kind.value}-01",
        kind=kind,
        option_symbol=symbol,
        observed_at=observed_at,
        underlying_delta=delta,
        cash_delta=cash,
    )
    receipt = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=observed_at,
            long_quantity=long_quantity,
            short_quantity=short_quantity,
            underlying_quantity=underlying,
        ),
        activity_coverage=_coverage(arm, observed_at=observed_at, events=(event,)),
        events=(event,),
    )

    assert receipt.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
    assert receipt.underlying_quantity_delta == Decimal(delta)
    assert receipt.event_cash_delta == Decimal(cash)
    assert "OPTION_EVENT_ECONOMICS_CONTRADICTORY" not in receipt.reason_codes
    assert "RESULTING_UNDERLYING_POSITION_CONTRADICTORY" not in receipt.reason_codes


@pytest.mark.parametrize(
    ("kind", "symbol", "long_quantity", "short_quantity", "underlying", "delta", "cash"),
    (
        (
            OptionEventKind.ASSIGNMENT,
            PUT_SHORT_SYMBOL,
            "1",
            "0",
            "110",
            "100",
            "-18000",
        ),
        (
            OptionEventKind.EXERCISE,
            PUT_LONG_SYMBOL,
            "0",
            "-1",
            "-90",
            "-100",
            "18500",
        ),
    ),
)
def test_put_assignment_and_exercise_recompute_signed_exposure(
    kind,
    symbol,
    long_quantity,
    short_quantity,
    underlying,
    delta,
    cash,
) -> None:
    arm, binding = _binding(option_type=OptionType.PUT)
    observed_at = ACTIVATED_AT + timedelta(minutes=2)
    event = _event(
        arm,
        activity_id=f"ACTIVITY-PUT-{kind.value}",
        kind=kind,
        option_symbol=symbol,
        observed_at=observed_at,
        underlying_delta=delta,
        cash_delta=cash,
    )
    receipt = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=observed_at,
            long_quantity=long_quantity,
            short_quantity=short_quantity,
            underlying_quantity=underlying,
            long_symbol=PUT_LONG_SYMBOL,
            short_symbol=PUT_SHORT_SYMBOL,
        ),
        activity_coverage=_coverage(arm, observed_at=observed_at, events=(event,)),
        events=(event,),
    )
    assert receipt.underlying_quantity_delta == Decimal(delta)
    assert receipt.event_cash_delta == Decimal(cash)
    assert "OPTION_EVENT_ECONOMICS_CONTRADICTORY" not in receipt.reason_codes


def test_put_two_leg_expiry_attests_flat_only_after_bound_horizon() -> None:
    arm, binding = _binding(option_type=OptionType.PUT)
    events = _expiry_events(
        arm,
        observed_at=ACTIVITY_HORIZON,
        long_symbol=PUT_LONG_SYMBOL,
        short_symbol=PUT_SHORT_SYMBOL,
    )
    receipt = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=ACTIVITY_HORIZON,
            long_quantity="0",
            short_quantity="0",
            long_symbol=PUT_LONG_SYMBOL,
            short_symbol=PUT_SHORT_SYMBOL,
        ),
        activity_coverage=_coverage(
            arm,
            observed_at=ACTIVITY_HORIZON,
            events=events,
        ),
        events=events,
    )
    assert receipt.state is OptionReconciliationState.EXPIRY_FLAT_ATTESTED


def test_activity_coverage_must_bind_exact_normalized_event_set() -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=1)
    event = _event(
        arm,
        activity_id="ACTIVITY-UNBOUND-TO-COVERAGE",
        kind=OptionEventKind.BROKER_SELL_OUT,
        option_symbol=LONG_SYMBOL,
        observed_at=observed_at,
    )
    with pytest.raises(OptionEventRejected, match="does not bind the normalized event set"):
        reconcile_option_events(
            binding=binding,
            current_observation=_observation(arm, observed_at=observed_at),
            activity_coverage=_coverage(arm, observed_at=observed_at),
            events=(event,),
        )


def test_expiry_requires_both_legs_positions_and_delayed_activity_horizon() -> None:
    arm, binding = _binding()
    before_horizon = EXPIRY_CLOSE + timedelta(hours=1)
    early_events = _expiry_events(arm, observed_at=before_horizon)
    early = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=before_horizon,
            long_quantity="0",
            short_quantity="0",
        ),
        activity_coverage=_coverage(
            arm,
            observed_at=before_horizon,
            events=early_events,
        ),
        events=early_events,
    )
    assert early.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED

    terminal_events = _expiry_events(arm, observed_at=ACTIVITY_HORIZON)
    terminal = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=ACTIVITY_HORIZON,
            long_quantity="0",
            short_quantity="0",
        ),
        activity_coverage=_coverage(
            arm,
            observed_at=ACTIVITY_HORIZON,
            events=terminal_events,
        ),
        events=terminal_events,
    )
    assert terminal.state is OptionReconciliationState.EXPIRY_FLAT_ATTESTED
    assert terminal.long_option_quantity == 0
    assert terminal.short_option_quantity == 0
    assert terminal.underlying_quantity_delta == 0
    assert (
        parse_option_event_reconciliation_receipt(
            option_event_reconciliation_receipt_bytes(terminal)
        )
        == terminal
    )

    one_leg = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=ACTIVITY_HORIZON,
            long_quantity="0",
            short_quantity="0",
        ),
        activity_coverage=_coverage(
            arm,
            observed_at=ACTIVITY_HORIZON,
            events=terminal_events[:1],
        ),
        events=terminal_events[:1],
    )
    assert one_leg.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
    assert "EXPIRY_EVIDENCE_INCOMPLETE_OR_CONTRADICTORY" in one_leg.reason_codes


def test_unbound_evidence_and_clocks_are_rejected_before_reduction() -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=1)
    host_observation = _observation(
        arm,
        observed_at=observed_at,
        evidence_class=EvidenceClass.HOST_NORMALIZED_BROKER_INPUT,
    )
    with pytest.raises(OptionEventRejected):
        reconcile_option_events(
            binding=binding,
            current_observation=host_observation,
            activity_coverage=_coverage(
                arm,
                observed_at=observed_at,
                evidence_class=EvidenceClass.HOST_NORMALIZED_BROKER_INPUT,
            ),
            events=(),
        )

    stale_event = _event(
        arm,
        activity_id="ACTIVITY-STALE",
        kind=OptionEventKind.EXPIRY,
        option_symbol=LONG_SYMBOL,
        observed_at=ACTIVATED_AT - timedelta(seconds=1),
    )
    with pytest.raises(OptionEventRejected):
        reconcile_option_events(
            binding=binding,
            current_observation=_observation(arm, observed_at=observed_at),
            activity_coverage=_coverage(
                arm,
                observed_at=observed_at,
                window_start=ACTIVATED_AT - timedelta(minutes=1),
                events=(stale_event,),
            ),
            events=(stale_event,),
        )


def test_buying_power_adjustment_and_contradictory_positions_fail_closed() -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=2)
    rejected = _event(
        arm,
        activity_id="ACTIVITY-BUYING-POWER",
        kind=OptionEventKind.EXERCISE_REJECTED_BUYING_POWER,
        option_symbol=LONG_SYMBOL,
        observed_at=observed_at,
        status=OptionEventStatus.REJECTED,
    )
    adjustment = _event(
        arm,
        activity_id="ACTIVITY-ADJUSTMENT",
        kind=OptionEventKind.CONTRACT_ADJUSTMENT,
        option_symbol=SHORT_SYMBOL,
        observed_at=observed_at,
        replacement_symbol="NVDA260918C00187500",
    )
    receipt = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=observed_at,
            underlying_quantity="11",
        ),
        activity_coverage=_coverage(
            arm,
            observed_at=observed_at,
            events=(rejected, adjustment),
        ),
        events=(rejected, adjustment),
    )
    assert receipt.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
    assert "EXERCISE_REJECTED_BUYING_POWER" in receipt.reason_codes
    assert "CONTRACT_ADJUSTMENT_REQUIRES_MANUAL_RECONCILIATION" in receipt.reason_codes


def test_sell_out_unmatched_activity_and_contradictory_economics_fail_closed() -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=3)
    sell_out = _event(
        arm,
        activity_id="ACTIVITY-SELL-OUT",
        kind=OptionEventKind.BROKER_SELL_OUT,
        option_symbol="NVDA260918C00190000",
        observed_at=observed_at,
    )
    contradictory_assignment = _event(
        arm,
        activity_id="ACTIVITY-BAD-ASSIGNMENT",
        kind=OptionEventKind.ASSIGNMENT,
        option_symbol=SHORT_SYMBOL,
        observed_at=observed_at,
        underlying_delta="100",
        cash_delta="-18500",
    )
    receipt = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=observed_at,
            short_quantity="0",
            underlying_quantity="110",
        ),
        activity_coverage=_coverage(
            arm,
            observed_at=observed_at,
            events=(sell_out, contradictory_assignment),
        ),
        events=(sell_out, contradictory_assignment),
    )
    assert receipt.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
    assert "BROKER_SELL_OUT_REQUIRES_MANUAL_RECONCILIATION" in receipt.reason_codes
    assert "UNMATCHED_OPTION_ACTIVITY" in receipt.reason_codes
    assert "OPTION_EVENT_ECONOMICS_CONTRADICTORY" in receipt.reason_codes


def test_unknown_event_code_and_duplicate_activity_identity_are_rejected() -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=1)
    event = _event(
        arm,
        activity_id="ACTIVITY-DUPLICATE",
        kind=OptionEventKind.EXPIRY,
        option_symbol=LONG_SYMBOL,
        observed_at=observed_at,
    )
    payload = json.loads(normalized_option_event_bytes(event))
    payload["kind"] = "UNKNOWN_PROVIDER_CODE"
    with pytest.raises(OptionEventRejected):
        parse_normalized_option_event(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )

    conflicting = _event(
        arm,
        activity_id=event.activity_id,
        kind=OptionEventKind.BROKER_SELL_OUT,
        option_symbol=LONG_SYMBOL,
        observed_at=observed_at,
    )
    with pytest.raises(OptionEventRejected, match="activity IDs must be unique"):
        reconcile_option_events(
            binding=binding,
            current_observation=_observation(arm, observed_at=observed_at),
            activity_coverage=_coverage(arm, observed_at=observed_at),
            events=(event, conflicting),
        )


def test_explicit_holiday_horizon_prevents_naive_next_weekday_flatness() -> None:
    holiday_horizon = datetime(2026, 9, 22, 13, 31, tzinfo=UTC)
    arm, binding = _binding(expiration_activity_horizon=holiday_horizon)
    monday = ACTIVITY_HORIZON
    monday_events = _expiry_events(arm, observed_at=monday)
    monday_receipt = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=monday,
            long_quantity="0",
            short_quantity="0",
        ),
        activity_coverage=_coverage(arm, observed_at=monday, events=monday_events),
        events=monday_events,
    )
    assert monday_receipt.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED

    tuesday_events = _expiry_events(arm, observed_at=holiday_horizon)
    tuesday_receipt = reconcile_option_events(
        binding=binding,
        current_observation=_observation(
            arm,
            observed_at=holiday_horizon,
            long_quantity="0",
            short_quantity="0",
        ),
        activity_coverage=_coverage(
            arm,
            observed_at=holiday_horizon,
            events=tuesday_events,
        ),
        events=tuesday_events,
    )
    assert tuesday_receipt.state is OptionReconciliationState.EXPIRY_FLAT_ATTESTED


def test_journal_is_idempotent_restart_safe_and_account_global(tmp_path) -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=2)
    event = _event(
        arm,
        activity_id="ACTIVITY-ASSIGNMENT-01",
        kind=OptionEventKind.ASSIGNMENT,
        option_symbol=SHORT_SYMBOL,
        observed_at=observed_at,
        underlying_delta="-100",
        cash_delta="18500",
    )
    observation = _observation(
        arm,
        observed_at=observed_at,
        short_quantity="0",
        underlying_quantity="-90",
    )
    coverage = _coverage(arm, observed_at=observed_at, events=(event,))
    path = tmp_path / "option-events.sqlite3"

    with OptionEventJournal(path) as journal:
        first = journal.record_reconciliation(
            binding=binding,
            current_observation=observation,
            activity_coverage=coverage,
            events=(event,),
        )
        replay = journal.record_reconciliation(
            binding=binding,
            current_observation=observation,
            activity_coverage=coverage,
            events=(event,),
        )
        assert replay == first
        assert journal.activity_owner(ACCOUNT_SHA256, event.activity_id) == binding.binding_sha256

    with OptionEventJournal(path) as restarted:
        assert restarted.load_binding(binding.session_id, binding.lifecycle_id) == binding
        assert restarted.latest_receipt(binding.session_id, binding.lifecycle_id) == first
        later_at = observed_at + timedelta(minutes=1)
        sticky = restarted.record_reconciliation(
            binding=binding,
            current_observation=_observation(arm, observed_at=later_at),
            activity_coverage=_coverage(arm, observed_at=later_at),
            events=(),
        )
        assert sticky.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
        assert "PRIOR_MANUAL_STATE_STICKY" in sticky.reason_codes


def test_manual_state_is_sticky_across_lifecycles_for_the_account_session(tmp_path) -> None:
    arm, binding = _binding()
    _, second_binding = _binding(
        opportunity_id="OPPORTUNITY-OPTION-02",
        lifecycle_id="LIFECYCLE-OPTION-02",
    )
    incident_at = ACTIVATED_AT + timedelta(minutes=2)
    event = _event(
        arm,
        activity_id="ACTIVITY-ACCOUNT-MANUAL",
        kind=OptionEventKind.ASSIGNMENT,
        option_symbol=SHORT_SYMBOL,
        observed_at=incident_at,
        underlying_delta="-100",
        cash_delta="18500",
    )
    path = tmp_path / "account-manual.sqlite3"
    with OptionEventJournal(path) as journal:
        journal.record_reconciliation(
            binding=binding,
            current_observation=_observation(
                arm,
                observed_at=incident_at,
                short_quantity="0",
                underlying_quantity="-90",
            ),
            activity_coverage=_coverage(
                arm,
                observed_at=incident_at,
                events=(event,),
            ),
            events=(event,),
        )
        later_at = incident_at + timedelta(minutes=1)
        second = journal.record_reconciliation(
            binding=second_binding,
            current_observation=_observation(arm, observed_at=later_at),
            activity_coverage=_coverage(arm, observed_at=later_at),
            events=(),
        )
        assert journal.account_requires_manual_reconciliation(
            session_id=binding.session_id,
            account_fingerprint_sha256=ACCOUNT_SHA256,
        )
    assert second.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
    assert "PRIOR_MANUAL_STATE_STICKY" in second.reason_codes


def test_immediate_position_change_then_delayed_activity_remains_blocked(tmp_path) -> None:
    arm, binding = _binding()
    changed_at = ACTIVATED_AT + timedelta(minutes=1)
    path = tmp_path / "delayed-activity.sqlite3"
    changed_observation = _observation(
        arm,
        observed_at=changed_at,
        short_quantity="0",
        underlying_quantity="-90",
    )
    with OptionEventJournal(path) as journal:
        missing = journal.record_reconciliation(
            binding=binding,
            current_observation=changed_observation,
            activity_coverage=_coverage(arm, observed_at=changed_at),
            events=(),
        )
        assert "UNATTRIBUTED_POSITION_CHANGE" in missing.reason_codes

        reported_at = changed_at + timedelta(days=1)
        assignment = _event(
            arm,
            activity_id="ACTIVITY-DELAYED-ASSIGNMENT",
            kind=OptionEventKind.ASSIGNMENT,
            option_symbol=SHORT_SYMBOL,
            observed_at=reported_at,
            underlying_delta="-100",
            cash_delta="18500",
        )
        delayed = journal.record_reconciliation(
            binding=binding,
            current_observation=_observation(
                arm,
                observed_at=reported_at,
                short_quantity="0",
                underlying_quantity="-90",
            ),
            activity_coverage=_coverage(
                arm,
                observed_at=reported_at,
                events=(assignment,),
            ),
            events=(assignment,),
        )
    assert delayed.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
    assert "PRIOR_MANUAL_STATE_STICKY" in delayed.reason_codes
    assert delayed.underlying_quantity_delta == -100


def test_journal_rejects_nonmonotonic_and_conflicting_replays_atomically(tmp_path) -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=1)
    path = tmp_path / "conflict.sqlite3"
    original = _event(
        arm,
        activity_id="ACTIVITY-CONFLICT",
        kind=OptionEventKind.BROKER_SELL_OUT,
        option_symbol=LONG_SYMBOL,
        observed_at=observed_at,
    )
    with OptionEventJournal(path) as journal:
        first = journal.record_reconciliation(
            binding=binding,
            current_observation=_observation(arm, observed_at=observed_at),
            activity_coverage=_coverage(
                arm,
                observed_at=observed_at,
                events=(original,),
            ),
            events=(original,),
        )
        with pytest.raises(OptionEventConflict, match="advance monotonically"):
            journal.record_reconciliation(
                binding=binding,
                current_observation=_observation(
                    arm,
                    observed_at=observed_at,
                    source_sha256="7" * 64,
                ),
                activity_coverage=_coverage(
                    arm,
                    observed_at=observed_at,
                    events=(original,),
                ),
                events=(original,),
            )

        later_at = observed_at + timedelta(minutes=1)
        conflicting = _event(
            arm,
            activity_id=original.activity_id,
            kind=OptionEventKind.BROKER_SELL_OUT,
            option_symbol=LONG_SYMBOL,
            observed_at=later_at,
            source_sha256="8" * 64,
        )
        with pytest.raises(OptionEventConflict, match="attributed differently"):
            journal.record_reconciliation(
                binding=binding,
                current_observation=_observation(arm, observed_at=later_at),
                activity_coverage=_coverage(
                    arm,
                    observed_at=later_at,
                    events=(conflicting,),
                ),
                events=(conflicting,),
            )
        assert journal.latest_receipt(binding.session_id, binding.lifecycle_id) == first


def test_changed_calendar_identity_conflicts_with_persisted_binding(tmp_path) -> None:
    _, binding = _binding()
    _, changed = _binding(calendar_sha256="9" * 64)
    with OptionEventJournal(tmp_path / "calendar.sqlite3") as journal:
        assert journal.ensure_binding(binding) is True
        with pytest.raises(OptionEventConflict, match="different canonical terms"):
            journal.ensure_binding(changed)


def test_concurrent_global_claim_has_one_owner_and_rolls_back_conflict(tmp_path) -> None:
    arm, binding = _binding()
    _, other_binding = _binding(
        opportunity_id="OPPORTUNITY-OPTION-02",
        lifecycle_id="LIFECYCLE-OPTION-02",
    )
    observed_at = ACTIVATED_AT + timedelta(minutes=2)
    event = _event(
        arm,
        activity_id="ACTIVITY-GLOBAL-01",
        kind=OptionEventKind.ASSIGNMENT,
        option_symbol=SHORT_SYMBOL,
        observed_at=observed_at,
        underlying_delta="-100",
        cash_delta="18500",
    )
    observation = _observation(
        arm,
        observed_at=observed_at,
        short_quantity="0",
        underlying_quantity="-90",
    )
    coverage = _coverage(arm, observed_at=observed_at, events=(event,))
    path = tmp_path / "concurrent.sqlite3"

    def claim(candidate: OptionLifecycleBinding) -> str:
        try:
            with OptionEventJournal(path) as journal:
                journal.record_reconciliation(
                    binding=candidate,
                    current_observation=observation,
                    activity_coverage=coverage,
                    events=(event,),
                )
            return "RECORDED"
        except OptionEventConflict:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(claim, (binding, other_binding)))

    assert sorted(outcomes) == ["CONFLICT", "RECORDED"]
    with OptionEventJournal(path) as journal:
        owner = journal.activity_owner(ACCOUNT_SHA256, event.activity_id)
        assert owner in {binding.binding_sha256, other_binding.binding_sha256}
        losing = other_binding if owner == binding.binding_sha256 else binding
        assert journal.latest_receipt(losing.session_id, losing.lifecycle_id) is None


def test_receipt_append_failure_rolls_back_activity_claim_before_restart(tmp_path) -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=2)
    event = _event(
        arm,
        activity_id="ACTIVITY-ROLLBACK",
        kind=OptionEventKind.ASSIGNMENT,
        option_symbol=SHORT_SYMBOL,
        observed_at=observed_at,
        underlying_delta="-100",
        cash_delta="18500",
    )
    observation = _observation(
        arm,
        observed_at=observed_at,
        short_quantity="0",
        underlying_quantity="-90",
    )
    coverage = _coverage(arm, observed_at=observed_at, events=(event,))
    path = tmp_path / "rollback.sqlite3"
    with OptionEventJournal(path):
        pass
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TRIGGER reject_receipt_insert
            BEFORE INSERT ON option_event_receipts
            BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with (
        OptionEventJournal(path) as journal,
        pytest.raises(OptionEventConflict, match="transaction failed"),
    ):
        journal.record_reconciliation(
            binding=binding,
            current_observation=observation,
            activity_coverage=coverage,
            events=(event,),
        )

    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TRIGGER reject_receipt_insert")
        connection.commit()
    finally:
        connection.close()

    with OptionEventJournal(path) as restarted:
        assert restarted.activity_owner(ACCOUNT_SHA256, event.activity_id) is None
        assert restarted.load_binding(binding.session_id, binding.lifecycle_id) is None
        recovered = restarted.record_reconciliation(
            binding=binding,
            current_observation=observation,
            activity_coverage=coverage,
            events=(event,),
        )
    assert recovered.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED


def test_append_only_triggers_reject_mutation(tmp_path) -> None:
    _, binding = _binding()
    path = tmp_path / "append-only.sqlite3"
    with OptionEventJournal(path) as journal:
        journal.ensure_binding(binding)

    connection = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only table"):
            connection.execute(
                "UPDATE option_event_bindings SET lifecycle_id = ?",
                ("FORGED",),
            )
    finally:
        connection.close()


def test_restart_revalidates_every_stored_canonical_row(tmp_path) -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=1)
    path = tmp_path / "tampered.sqlite3"
    with OptionEventJournal(path) as journal:
        journal.record_reconciliation(
            binding=binding,
            current_observation=_observation(arm, observed_at=observed_at),
            activity_coverage=_coverage(arm, observed_at=observed_at),
            events=(),
        )

    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TRIGGER option_event_observations_no_update")
        connection.execute("UPDATE option_event_observations SET observation_json = '{}' ")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(OptionEventConflict, match="stored option-event bytes are invalid"):
        OptionEventJournal(path)


def test_restart_rejects_canonical_but_semantically_forged_receipt(tmp_path) -> None:
    arm, binding = _binding()
    observed_at = ACTIVATED_AT + timedelta(minutes=1)
    path = tmp_path / "semantic-forgery.sqlite3"
    with OptionEventJournal(path) as journal:
        journal.record_reconciliation(
            binding=binding,
            current_observation=_observation(
                arm,
                observed_at=observed_at,
                short_quantity="0",
                underlying_quantity="-90",
            ),
            activity_coverage=_coverage(arm, observed_at=observed_at),
            events=(),
        )

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT receipt_json FROM option_event_receipts").fetchone()
        payload = json.loads(bytes(row["receipt_json"]))
        payload["state"] = OptionReconciliationState.ACTIVE_UNCHANGED.value
        payload["reason_codes"] = ["BOUND_POSITIONS_UNCHANGED"]
        unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
        digest = hashlib.sha256(
            json.dumps(unsigned, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        payload["receipt_sha256"] = digest
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        connection.execute("DROP TRIGGER option_event_receipts_no_update")
        connection.execute(
            """
            UPDATE option_event_receipts
            SET receipt_sha256 = ?, state = ?, receipt_json = ?
            """,
            (digest, OptionReconciliationState.ACTIVE_UNCHANGED.value, raw),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(OptionEventConflict, match="stored receipt transition is invalid"):
        OptionEventJournal(path)
