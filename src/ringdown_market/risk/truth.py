"""Broker-observed truth interfaces for the risk kernel.

Truth is whatever the broker last observed, sanitized: no account identifiers,
credentials, or raw broker payloads enter the kernel or the ledger. Stale or
missing truth fails closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

MAX_TRUTH_AGE_SECONDS = 30


class TruthRejected(ValueError):
    """Raised when broker-observed truth is missing, stale, or inconsistent."""


def _require_aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TruthRejected(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AccountTruth:
    """Sanitized broker-observed account state."""

    equity: Decimal
    observed_at: datetime
    raw_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.equity, Decimal) or not self.equity.is_finite():
            raise TruthRejected("equity must be a finite Decimal")
        _require_aware(self.observed_at, "observed_at")
        if not isinstance(self.raw_sha256, str) or len(self.raw_sha256) != 64:
            raise TruthRejected("raw_sha256 must be a sha256 hex digest")


@dataclass(frozen=True, slots=True)
class PositionTruth:
    """One broker-observed position."""

    symbol: str
    quantity: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise TruthRejected("symbol must be non-empty")
        if not isinstance(self.quantity, Decimal) or not self.quantity.is_finite():
            raise TruthRejected("quantity must be a finite Decimal")
        _require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class OrderTruth:
    """One broker-observed open order."""

    client_order_id: str
    symbol: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.client_order_id.strip() or not self.symbol.strip():
            raise TruthRejected("order identity must be non-empty")
        _require_aware(self.observed_at, "observed_at")


def assert_fresh(observed_at: datetime, *, now: datetime, label: str) -> None:
    """Fail closed when truth is stale, future-dated, or beyond the frozen age."""

    _require_aware(now, "now")
    _require_aware(observed_at, f"{label}.observed_at")
    age = (now - observed_at).total_seconds()
    if age < 0:
        raise TruthRejected(f"{label} is future-dated")
    if age > MAX_TRUTH_AGE_SECONDS:
        raise TruthRejected(f"{label} is stale")


@dataclass(frozen=True, slots=True)
class FakeTruthSource:
    """Deterministic injected truth for offline tests and dry runs."""

    account: AccountTruth | None
    positions: tuple[PositionTruth, ...] = ()
    open_orders: tuple[OrderTruth, ...] = ()
    account_available: bool = True

    def observe_account(self) -> AccountTruth | None:
        return self.account if self.account_available else None

    def observe_positions(self) -> Sequence[PositionTruth]:
        return self.positions

    def observe_open_orders(self) -> Sequence[OrderTruth]:
        return self.open_orders
