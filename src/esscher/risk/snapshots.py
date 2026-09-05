"""Account, position, and order snapshots over broker-observed truth.

Every snapshot has an explicit UTC observation time. Missing, malformed,
stale, future, or contradictory truth fails closed as ``RiskRejected`` rather
than leaking a type or datetime exception across the risk boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from esscher.risk.reasons import RiskReason, _reject


def _require_utc(value: object, field: str) -> datetime:
    """Return an exact-UTC datetime or raise a stable risk rejection."""

    if not isinstance(value, datetime):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, field, "must be a datetime")
    if value.tzinfo is not UTC:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, field, "must be UTC")
    return value


def _require_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise _reject(RiskReason.UNSUPPORTED_INPUT, field, "must be a finite Decimal")
    return value


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """One broker-observed account truth."""

    equity: Decimal
    buying_power: Decimal
    currency: str
    observed_at: datetime
    cash: Decimal | None = None

    def __post_init__(self) -> None:
        _require_decimal(self.equity, "account.equity")
        _require_decimal(self.buying_power, "account.buying_power")
        if self.cash is not None and _require_decimal(self.cash, "account.cash") < 0:
            raise _reject(RiskReason.UNSUPPORTED_INPUT, "account.cash", "must be non-negative")
        if not isinstance(self.currency, str) or not self.currency:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT, "account.currency", "must be non-empty text"
            )
        _require_utc(self.observed_at, "account.observed_at")


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """One broker-observed position truth."""

    underlying: str
    quantity: Decimal
    market_value: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.underlying, str)
            or not self.underlying
            or self.underlying != self.underlying.strip().upper()
        ):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "position.underlying",
                "must be normalized uppercase text",
            )
        _require_decimal(self.quantity, "position.quantity")
        _require_decimal(self.market_value, "position.market_value")
        _require_utc(self.observed_at, "position.observed_at")


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """One broker-observed order truth."""

    order_id: str
    symbol: str
    status: str
    filled_quantity: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        for field, value in (
            ("order.order_id", self.order_id),
            ("order.symbol", self.symbol),
            ("order.status", self.status),
        ):
            if not isinstance(value, str) or not value:
                raise _reject(RiskReason.UNSUPPORTED_INPUT, field, "must be non-empty text")
        if _require_decimal(self.filled_quantity, "order.filled_quantity") < 0:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT, "order.filled_quantity", "must be non-negative"
            )
        _require_utc(self.observed_at, "order.observed_at")

    @property
    def is_partial_fill(self) -> bool:
        return self.status == "PARTIALLY_FILLED"


@runtime_checkable
class AccountTruthSource(Protocol):
    """Read-only boundary for broker-observed account/position/order truth."""

    def account(self) -> AccountSnapshot | None: ...

    def positions(self) -> tuple[PositionSnapshot, ...]: ...

    def orders(self) -> tuple[OrderSnapshot, ...]: ...

    def broker_clock(self) -> datetime: ...


def _validate_max_age(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "truth_max_age_seconds",
            "must be a non-negative integer",
        )
    return value


def _validate_age(
    *,
    observed_at: object,
    now: object,
    max_age_seconds: object,
    stale_reason: RiskReason,
    path: str,
) -> None:
    observed = _require_utc(observed_at, f"{path}.observed_at")
    current = _require_utc(now, "now")
    maximum = _validate_max_age(max_age_seconds)
    age = (current - observed).total_seconds()
    if age < 0:
        raise _reject(
            RiskReason.CONTRADICTORY_TRUTH, f"{path}.observed_at", "truth is future-dated"
        )
    if age > maximum:
        raise _reject(stale_reason, path, f"truth age {age:.0f}s exceeds {maximum}s")


def validate_account_freshness(
    snapshot: AccountSnapshot | None, *, now: datetime, max_age_seconds: int
) -> AccountSnapshot:
    """Fail closed when account truth is missing or stale."""

    _require_utc(now, "now")
    if snapshot is None:
        raise _reject(RiskReason.STALE_ACCOUNT_TRUTH, "account", "no account truth is available")
    if not isinstance(snapshot, AccountSnapshot):
        raise _reject(RiskReason.CONTRADICTORY_TRUTH, "account", "snapshot has an invalid type")
    _validate_age(
        observed_at=snapshot.observed_at,
        now=now,
        max_age_seconds=max_age_seconds,
        stale_reason=RiskReason.STALE_ACCOUNT_TRUTH,
        path="account",
    )
    return snapshot


def _snapshot_sequence(
    snapshots: object,
    *,
    expected_type: type[PositionSnapshot] | type[OrderSnapshot],
    path: str,
) -> Sequence[PositionSnapshot] | Sequence[OrderSnapshot]:
    if isinstance(snapshots, (str, bytes)) or not isinstance(snapshots, Sequence):
        raise _reject(RiskReason.CONTRADICTORY_TRUTH, path, "snapshots must be a sequence")
    if any(not isinstance(snapshot, expected_type) for snapshot in snapshots):
        raise _reject(RiskReason.CONTRADICTORY_TRUTH, path, "snapshot has an invalid type")
    return snapshots


def validate_positions_freshness(
    snapshots: tuple[PositionSnapshot, ...], *, now: datetime, max_age_seconds: int
) -> tuple[PositionSnapshot, ...]:
    """Fail closed when any broker-observed position is stale or malformed."""

    values = _snapshot_sequence(snapshots, expected_type=PositionSnapshot, path="positions")
    for index, snapshot in enumerate(values):
        _validate_age(
            observed_at=snapshot.observed_at,
            now=now,
            max_age_seconds=max_age_seconds,
            stale_reason=RiskReason.STALE_POSITION_TRUTH,
            path=f"positions[{index}]",
        )
    return tuple(values)  # type: ignore[arg-type]


def validate_orders_freshness(
    snapshots: tuple[OrderSnapshot, ...], *, now: datetime, max_age_seconds: int
) -> tuple[OrderSnapshot, ...]:
    """Fail closed when any broker-observed order is stale or malformed."""

    values = _snapshot_sequence(snapshots, expected_type=OrderSnapshot, path="orders")
    for index, snapshot in enumerate(values):
        _validate_age(
            observed_at=snapshot.observed_at,
            now=now,
            max_age_seconds=max_age_seconds,
            stale_reason=RiskReason.STALE_ORDER_TRUTH,
            path=f"orders[{index}]",
        )
    return tuple(values)  # type: ignore[arg-type]
