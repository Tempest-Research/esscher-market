"""Account, position, and order snapshot interfaces over broker-observed truth.

All truth comes from broker-observed snapshots; nothing is inferred. Every
snapshot carries its observation time, and stale, missing, or contradictory
truth fails closed. Broker PAPER PnL and conservative shadow PnL remain
separate fields and separate claims.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ringdown_market.risk.reasons import RiskReason, _reject


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo != UTC:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, field, "must be UTC")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """One broker-observed account truth."""

    equity: Decimal
    buying_power: Decimal
    currency: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.equity.is_finite() or not self.buying_power.is_finite():
            raise ValueError("account amounts must be finite")
        if not self.currency:
            raise ValueError("currency must be non-empty text")
        _require_utc(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """One broker-observed position truth."""

    underlying: str
    quantity: Decimal
    market_value: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.underlying != self.underlying.strip().upper():
            raise ValueError("underlying must be normalized uppercase")
        if not self.quantity.is_finite() or not self.market_value.is_finite():
            raise ValueError("position amounts must be finite")
        _require_utc(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """One broker-observed order truth."""

    order_id: str
    symbol: str
    status: str
    filled_quantity: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.order_id or not self.symbol or not self.status:
            raise ValueError("order fields must be non-empty text")
        if not self.filled_quantity.is_finite():
            raise ValueError("filled_quantity must be finite")
        _require_utc(self.observed_at, "observed_at")

    @property
    def is_partial_fill(self) -> bool:
        return self.status == "PARTIALLY_FILLED"


@runtime_checkable
class AccountTruthSource(Protocol):
    """Read-only boundary for broker-observed account/position/order truth."""

    def account(self) -> AccountSnapshot | None:
        """Return the latest observed account truth, if available."""
        ...

    def positions(self) -> tuple[PositionSnapshot, ...]:
        """Return the latest observed position truths."""
        ...

    def orders(self) -> tuple[OrderSnapshot, ...]:
        """Return the latest observed order truths."""
        ...

    def broker_clock(self) -> datetime:
        """Return the broker-observed clock."""
        ...


def validate_account_freshness(
    snapshot: AccountSnapshot | None, *, now: datetime, max_age_seconds: int
) -> AccountSnapshot:
    """Fail closed when account truth is missing or stale."""

    _require_utc(now, "now")
    if snapshot is None:
        raise _reject(RiskReason.STALE_ACCOUNT_TRUTH, "account", "no account truth is available")
    age = (now - snapshot.observed_at).total_seconds()
    if age < 0:
        raise _reject(
            RiskReason.CONTRADICTORY_TRUTH,
            "account.observed_at",
            "account truth is from the future",
        )
    if age > max_age_seconds:
        raise _reject(
            RiskReason.STALE_ACCOUNT_TRUTH,
            "account.observed_at",
            f"account truth age {age:.0f}s exceeds {max_age_seconds}s",
        )
    return snapshot


def validate_positions_freshness(
    positions: Sequence[PositionSnapshot], *, now: datetime, max_age_seconds: int
) -> tuple[PositionSnapshot, ...]:
    """Fail closed when any position truth is stale."""

    _require_utc(now, "now")
    for position in positions:
        age = (now - position.observed_at).total_seconds()
        if age < 0:
            raise _reject(
                RiskReason.CONTRADICTORY_TRUTH,
                f"position.{position.underlying}.observed_at",
                "position truth is from the future",
            )
        if age > max_age_seconds:
            raise _reject(
                RiskReason.STALE_POSITION_TRUTH,
                f"position.{position.underlying}.observed_at",
                f"position truth age {age:.0f}s exceeds {max_age_seconds}s",
            )
    return tuple(positions)


def validate_orders_freshness(
    orders: Sequence[OrderSnapshot], *, now: datetime, max_age_seconds: int
) -> tuple[OrderSnapshot, ...]:
    """Fail closed when any order truth is stale."""

    _require_utc(now, "now")
    for order in orders:
        age = (now - order.observed_at).total_seconds()
        if age < 0:
            raise _reject(
                RiskReason.CONTRADICTORY_TRUTH,
                f"order.{order.order_id}.observed_at",
                "order truth is from the future",
            )
        if age > max_age_seconds:
            raise _reject(
                RiskReason.STALE_ORDER_TRUTH,
                f"order.{order.order_id}.observed_at",
                f"order truth age {age:.0f}s exceeds {max_age_seconds}s",
            )
    return tuple(orders)
