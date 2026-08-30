"""Account-level PAPER risk kernel with durable reservations.

A valid option package still cannot become a permit when account, portfolio,
clock, quote, or lifecycle truth is unsafe. The kernel never mutates a broker;
it persists one reservation plus one-use permit binding before any downstream
mutation and integrates with the existing permit compiler instead of creating
a second authorization path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from .ledger import RiskLedger
from .policy import RISK_POLICY_SHA256, RISK_POLICY_VERSION, RiskLimits
from .truth import AccountTruth, OrderTruth, PositionTruth, TruthRejected, assert_fresh


class RiskRejectionReason(StrEnum):
    """Stable fail-closed reasons a package cannot reserve risk capacity."""

    CONTROL_STATE_BLOCKS_ENTRIES = "CONTROL_STATE_BLOCKS_ENTRIES"
    MISSING_ACCOUNT_TRUTH = "MISSING_ACCOUNT_TRUTH"
    STALE_TRUTH = "STALE_TRUTH"
    UNKNOWN_EXPOSURE = "UNKNOWN_EXPOSURE"
    OPEN_PACKAGE_EXISTS = "OPEN_PACKAGE_EXISTS"
    DUPLICATE_EVENT_PACKAGE = "DUPLICATE_EVENT_PACKAGE"
    MAX_LOSS_EXCEEDED = "MAX_LOSS_EXCEEDED"
    DAILY_LOSS_BUDGET_EXCEEDED = "DAILY_LOSS_BUDGET_EXCEEDED"
    DAILY_ENTRY_LIMIT_EXCEEDED = "DAILY_ENTRY_LIMIT_EXCEEDED"
    PERIOD_ENTRY_LIMIT_EXCEEDED = "PERIOD_ENTRY_LIMIT_EXCEEDED"
    ENTRY_DISABLE_DRAWDOWN = "ENTRY_DISABLE_DRAWDOWN"
    HARD_KILL_DRAWDOWN = "HARD_KILL_DRAWDOWN"
    MARKET_ORDER_FORBIDDEN = "MARKET_ORDER_FORBIDDEN"
    NAKED_SHORT_FORBIDDEN = "NAKED_SHORT_FORBIDDEN"


@dataclass(frozen=True, slots=True)
class PackageRiskRequest:
    """One sanitized package identity presented for risk reservation."""

    event_id: str
    package_sha256: str
    max_loss: Decimal
    order_type: str
    long_symbols: tuple[str, ...]
    short_symbols: tuple[str, ...]
    long_quantities: tuple[Decimal, ...]
    short_quantities: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must be non-empty")
        if len(self.package_sha256) != 64:
            raise ValueError("package_sha256 must be a sha256 hex digest")
        if not isinstance(self.max_loss, Decimal) or not self.max_loss.is_finite():
            raise ValueError("max_loss must be a finite Decimal")
        if self.max_loss <= 0:
            raise ValueError("max_loss must be positive")
        if len(self.long_symbols) != len(self.long_quantities):
            raise ValueError("long legs must pair symbols with quantities")
        if len(self.short_symbols) != len(self.short_quantities):
            raise ValueError("short legs must pair symbols with quantities")


@dataclass(frozen=True, slots=True)
class RiskReservation:
    """One approved durable reservation with its one-use permit binding."""

    reservation_id: str
    event_id: str
    package_sha256: str
    permit_binding: str
    max_loss: Decimal
    reserved_at: datetime


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    approved: bool
    reason: RiskRejectionReason | None
    reservation: RiskReservation | None


def _canonical_reservation_identity(
    *, event_id: str, package_sha256: str, policy_sha256: str
) -> str:
    identity = f"{event_id}:{package_sha256}:{policy_sha256}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def evaluate_package(
    request: PackageRiskRequest,
    *,
    ledger: RiskLedger,
    limits: RiskLimits,
    account: AccountTruth | None,
    positions: tuple[PositionTruth, ...],
    open_orders: tuple[OrderTruth, ...],
    now: datetime,
    realized_loss_today: Decimal = Decimal("0"),
) -> RiskVerdict:
    """Evaluate one package against frozen limits and durable ledger truth."""

    control_state, _control_reason = ledger.current_control_state()
    if control_state in {"ENTRY_DISABLED", "CLOSE_ONLY", "KILLED"}:
        return RiskVerdict(
            approved=False,
            reason=RiskRejectionReason.CONTROL_STATE_BLOCKS_ENTRIES,
            reservation=None,
        )

    if request.order_type != "LIMIT":
        _escalate(ledger, RiskRejectionReason.MARKET_ORDER_FORBIDDEN, now)
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.MARKET_ORDER_FORBIDDEN, reservation=None
        )

    total_short = sum(request.short_quantities, Decimal(0))
    total_long = sum(request.long_quantities, Decimal(0))
    if total_short > total_long:
        _escalate(ledger, RiskRejectionReason.NAKED_SHORT_FORBIDDEN, now)
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.NAKED_SHORT_FORBIDDEN, reservation=None
        )

    if account is None:
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.MISSING_ACCOUNT_TRUTH, reservation=None
        )
    try:
        assert_fresh(account.observed_at, now=now, label="account")
        for position in positions:
            assert_fresh(position.observed_at, now=now, label="position")
        for order in open_orders:
            assert_fresh(order.observed_at, now=now, label="order")
    except TruthRejected:
        _escalate(ledger, RiskRejectionReason.STALE_TRUTH, now)
        return RiskVerdict(approved=False, reason=RiskRejectionReason.STALE_TRUTH, reservation=None)

    expected_symbols = set(request.long_symbols) | set(request.short_symbols)
    expected_symbols.update(ledger.position_symbols())
    for position in positions:
        if position.quantity != 0 and position.symbol not in expected_symbols:
            _escalate(ledger, RiskRejectionReason.UNKNOWN_EXPOSURE, now)
            return RiskVerdict(
                approved=False, reason=RiskRejectionReason.UNKNOWN_EXPOSURE, reservation=None
            )

    for record in ledger.open_reservations():
        if record.event_id == request.event_id or record.package_sha256 == request.package_sha256:
            return RiskVerdict(
                approved=False,
                reason=RiskRejectionReason.DUPLICATE_EVENT_PACKAGE,
                reservation=None,
            )

    if len(ledger.open_reservations()) >= limits.maximum_open_packages:
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.OPEN_PACKAGE_EXISTS, reservation=None
        )

    drawdown = limits.start_equity - account.equity
    if drawdown >= limits.hard_kill_drawdown:
        ledger.set_control_state(state="CLOSE_ONLY", reason="hard_kill_drawdown", now=now)
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.HARD_KILL_DRAWDOWN, reservation=None
        )
    if drawdown >= limits.entry_disable_drawdown:
        ledger.set_control_state(state="ENTRY_DISABLED", reason="entry_disable_drawdown", now=now)
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.ENTRY_DISABLE_DRAWDOWN, reservation=None
        )

    if request.max_loss > limits.maximum_loss_per_trade:
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.MAX_LOSS_EXCEEDED, reservation=None
        )

    exposure = realized_loss_today + ledger.reserved_loss() + request.max_loss
    if exposure > limits.daily_loss_budget:
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.DAILY_LOSS_BUDGET_EXCEEDED, reservation=None
        )

    day = now.astimezone(UTC).date().isoformat()
    if ledger.entries_on_day(day) >= limits.maximum_new_entries_per_day:
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.DAILY_ENTRY_LIMIT_EXCEEDED, reservation=None
        )
    if ledger.entries_in_period() >= limits.maximum_new_entries_per_period:
        return RiskVerdict(
            approved=False,
            reason=RiskRejectionReason.PERIOD_ENTRY_LIMIT_EXCEEDED,
            reservation=None,
        )

    reservation_id = _canonical_reservation_identity(
        event_id=request.event_id,
        package_sha256=request.package_sha256,
        policy_sha256=RISK_POLICY_SHA256,
    )
    try:
        ledger.reserve(
            reservation_id=reservation_id,
            event_id=request.event_id,
            package_sha256=request.package_sha256,
            max_loss=request.max_loss,
            now=now,
        )
        ledger.record_entry(event_id=request.event_id, now=now)
    except Exception:
        return RiskVerdict(
            approved=False, reason=RiskRejectionReason.DUPLICATE_EVENT_PACKAGE, reservation=None
        )

    permit_binding = hashlib.sha256(f"{reservation_id}:{RISK_POLICY_VERSION}".encode()).hexdigest()
    ledger.bind_permit(reservation_id=reservation_id, permit_id=permit_binding, now=now)

    return RiskVerdict(
        approved=True,
        reason=None,
        reservation=RiskReservation(
            reservation_id=reservation_id,
            event_id=request.event_id,
            package_sha256=request.package_sha256,
            permit_binding=permit_binding,
            max_loss=request.max_loss,
            reserved_at=now,
        ),
    )


def _escalate(ledger: RiskLedger, reason: RiskRejectionReason, now: datetime) -> None:
    ledger.record_reconciliation(outcome="RISK_ESCALATION", detail=reason.value, now=now)
    ledger.set_control_state(state="ENTRY_DISABLED", reason=reason.value, now=now)
