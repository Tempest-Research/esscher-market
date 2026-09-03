"""Deterministic synthetic PAPER broker for the autonomous host rehearsal.

This broker is an in-memory fake: it has no provider credentials, network
code, MCP calls, account access, or real broker mutation path.  Order and
position truth objects use the exact ``lifecycle.broker`` field sets, order
identities are SHA-derived and deterministic, and every clock is injected so
rehearsals remain reproducible.  Canonical state helpers expose content
addresses consumed by the synthetic reconciliation truth; they are synthetic
attestations, never broker-observed operational facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from ringdown_market.lifecycle.broker import (
    MULTI_LEG_ORDER_CLASS,
    OPEN_WORKING_STATES,
    PAPER_ACCOUNT_CLASS,
    AccountTruth,
    BrokerOrderAck,
    BrokerOrderRequest,
    BrokerOrderState,
    BrokerOrderTruth,
    BrokerOutage,
    BrokerPositionSnapshot,
    PositionTruth,
)
from ringdown_market.risk.snapshots import AccountSnapshot, OrderSnapshot, PositionSnapshot

SYNTHETIC_PAPER_ACCOUNT_ID = "SYNTHETIC-PAPER-ACCOUNT"
SYNTHETIC_BROKER_STATE_SCHEMA = "esscher.synthetic_paper_broker_state"
SYNTHETIC_BROKER_STATE_SCHEMA_VERSION = 1
SYNTHETIC_BROKER_EPOCH = datetime(2026, 9, 11, 13, 36, 5, tzinfo=UTC)

_OCC_ROOT = re.compile(r"^([A-Z]{1,6})\d")

_ORDER_STATE_TO_SNAPSHOT_STATUS = {
    BrokerOrderState.NEW: "NEW",
    BrokerOrderState.FILLED: "FILLED",
    BrokerOrderState.CANCELED: "CANCELED",
    BrokerOrderState.REJECTED: "REJECTED",
    BrokerOrderState.EXPIRED: "EXPIRED",
    BrokerOrderState.PARTIALLY_FILLED: "PARTIALLY_FILLED",
}


class SyntheticBrokerAmbiguousMutation(RuntimeError):
    """Raised after a recorded submission whose outcome cannot be proven."""


class SyntheticBrokerRejected(ValueError):
    """Raised when the synthetic broker configuration or usage is invalid."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _option_underlying(symbol: str) -> str:
    match = _OCC_ROOT.match(symbol)
    if match is None:
        raise SyntheticBrokerRejected("synthetic position symbol is not an OCC option root")
    return match.group(1)


def _default_clock() -> datetime:
    return SYNTHETIC_BROKER_EPOCH


@dataclass(frozen=True, slots=True)
class SyntheticBrokerAccountConfiguration:
    """The frozen initial account truth of one synthetic rehearsal broker.

    The default unborrowed cash deliberately sits in the interval
    ``[SYNTHETIC_MAX_DEBIT_PER_CONTRACT, 2 * SYNTHETIC_MAX_DEBIT_PER_CONTRACT)``
    so the deterministic V2 allocator admits exactly one synthetic contract at
    the frozen synthetic max debit while the equity stays far above the
    owner-approved drawdown freeze.
    """

    account_id: str = SYNTHETIC_PAPER_ACCOUNT_ID
    equity: Decimal = Decimal("100000.00")
    cash: Decimal = Decimal("3.00")
    buying_power: Decimal = Decimal("100000.00")

    def __post_init__(self) -> None:
        if not self.account_id or not self.account_id.strip():
            raise SyntheticBrokerRejected("account_id must be non-empty exact text")
        for name in ("equity", "cash", "buying_power"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise SyntheticBrokerRejected(f"{name} must be a finite non-negative Decimal")
        if self.cash > self.equity:
            raise SyntheticBrokerRejected("synthetic cash cannot exceed synthetic equity")


@dataclass
class _RecordedOrder:
    """One stored synthetic order with its request and terminal state."""

    order_id: str
    request: BrokerOrderRequest
    status: str
    filled_qty: int
    observed_at: datetime


@dataclass
class SyntheticPaperBroker:
    """In-memory deterministic PAPER broker with injectable fault flags."""

    configuration: SyntheticBrokerAccountConfiguration = field(
        default_factory=SyntheticBrokerAccountConfiguration
    )
    clock: Callable[[], datetime] = field(default=_default_clock)
    ambiguous_open: bool = False
    ambiguous_close: bool = False
    outage: bool = False
    residual_position_after_close: bool = False
    open_submissions: int = 0
    close_submissions: int = 0
    cancel_attempts: int = 0
    order_readbacks: int = 0
    position_readbacks: int = 0
    account_readbacks: int = 0
    _orders: dict[str, _RecordedOrder] = field(default_factory=dict)
    _positions: dict[str, Decimal] = field(default_factory=dict)
    _position_underlyings: dict[str, str] = field(default_factory=dict)
    _position_reference_prices: dict[str, Decimal] = field(default_factory=dict)
    _submission_order: list[str] = field(default_factory=list)
    _latest_request_order_id: str | None = None
    _last_clock: datetime | None = None

    def _now(self) -> datetime:
        """Return the injected broker clock, clamped to be monotonic in UTC."""

        observed = self.clock()
        if not isinstance(observed, datetime) or observed.tzinfo is None:
            raise SyntheticBrokerRejected("synthetic broker clock must be timezone-aware")
        observed = observed.astimezone(UTC)
        if self._last_clock is not None and observed < self._last_clock:
            observed = self._last_clock
        self._last_clock = observed
        return observed

    def _require_available(self) -> None:
        if self.outage:
            raise BrokerOutage("synthetic broker outage")

    def _derived_order_id(self, request: BrokerOrderRequest) -> str:
        identity = _canonical_json(
            {
                "schema": SYNTHETIC_BROKER_STATE_SCHEMA,
                "schema_version": SYNTHETIC_BROKER_STATE_SCHEMA_VERSION,
                "purpose": "order_id",
                "client_order_id": request.client_order_id,
                "phase": request.phase,
                "permit_id": request.permit_id,
                "correlation_sha256": request.correlation_sha256,
            }
        )
        return f"synthetic-{request.phase.lower()}-{_sha256(identity)[:32]}"

    @staticmethod
    def _expected_package_quantity(request: BrokerOrderRequest) -> int:
        quantities = {leg.quantity for leg in request.legs}
        if len(quantities) != 1:
            raise SyntheticBrokerRejected("synthetic broker supports only uniform leg quantities")
        return quantities.pop()

    def _record(
        self,
        request: BrokerOrderRequest,
        *,
        status: str,
    ) -> _RecordedOrder:
        order_id = self._derived_order_id(request)
        if order_id in self._orders:
            raise SyntheticBrokerRejected("synthetic order identity was already recorded")
        filled = (
            self._expected_package_quantity(request) if status == BrokerOrderState.FILLED else 0
        )
        recorded = _RecordedOrder(
            order_id=order_id,
            request=request,
            status=status,
            filled_qty=filled,
            observed_at=self._now(),
        )
        self._orders[order_id] = recorded
        self._submission_order.append(order_id)
        self._latest_request_order_id = order_id
        return recorded

    def _apply_open_positions(self, request: BrokerOrderRequest) -> None:
        for leg in request.legs:
            signed = Decimal(leg.quantity if leg.side == "buy" else -leg.quantity)
            self._positions[leg.symbol] = self._positions.get(leg.symbol, Decimal(0)) + signed
            self._position_underlyings.setdefault(leg.symbol, _option_underlying(leg.symbol))
            self._position_reference_prices[leg.symbol] = request.limit_price

    def _clear_positions(self) -> None:
        self._positions.clear()
        self._position_underlyings.clear()
        self._position_reference_prices.clear()

    def _ack(self, recorded: _RecordedOrder) -> BrokerOrderAck:
        return BrokerOrderAck(
            order_id=recorded.order_id,
            client_order_id=recorded.request.client_order_id,
            observed_at=self._now(),
        )

    async def submit_open(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        """Record one opening submission; the ambiguity fault records then raises."""

        self._require_available()
        self.open_submissions += 1
        if self.ambiguous_open:
            self._record(request, status=BrokerOrderState.NEW)
            raise SyntheticBrokerAmbiguousMutation(
                "synthetic broker recorded the opening but cannot prove its outcome"
            )
        recorded = self._record(request, status=BrokerOrderState.FILLED)
        self._apply_open_positions(request)
        return self._ack(recorded)

    async def submit_close(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        """Record one atomic close submission and flatten the synthetic book."""

        self._require_available()
        self.close_submissions += 1
        if self.ambiguous_close:
            self._record(request, status=BrokerOrderState.NEW)
            raise SyntheticBrokerAmbiguousMutation(
                "synthetic broker recorded the close but cannot prove its outcome"
            )
        recorded = self._record(request, status=BrokerOrderState.FILLED)
        if self.residual_position_after_close:
            residual = next(iter(self._positions), None)
            cleared = dict(self._positions)
            cleared.pop(residual, None)
            self._positions = cleared
        else:
            self._clear_positions()
        return self._ack(recorded)

    async def read_order(self, order_id: str) -> BrokerOrderTruth:
        """Read back one recorded order's fresh synthetic truth."""

        self._require_available()
        self.order_readbacks += 1
        recorded = self._orders.get(order_id)
        if recorded is None:
            raise BrokerOutage(f"unknown synthetic order {order_id}")
        request = recorded.request
        return BrokerOrderTruth(
            order_id=recorded.order_id,
            client_order_id=request.client_order_id,
            status=recorded.status,
            filled_qty=recorded.filled_qty,
            observed_at=self._now(),
            permit_id=request.permit_id,
            open_permit_id=request.open_permit_id,
            event_run_id=request.event_run_id,
            reservation_id=request.reservation_id,
            correlation_sha256=request.correlation_sha256,
            policy_sha256=request.policy_sha256,
            snapshot_sha256=request.snapshot_sha256,
            account_id=request.account_id,
            account_class=request.account_class,
            order_class=request.order_class,
            limit_price=request.limit_price,
            legs=request.legs,
        )

    async def cancel_order(self, order_id: str) -> BrokerOrderAck:
        """Cancel one recorded working synthetic order."""

        self._require_available()
        self.cancel_attempts += 1
        recorded = self._orders.get(order_id)
        if recorded is None or recorded.status not in OPEN_WORKING_STATES:
            raise BrokerOutage(f"cannot cancel synthetic order {order_id}")
        recorded.status = BrokerOrderState.CANCELED
        recorded.filled_qty = 0
        recorded.observed_at = self._now()
        return self._ack(recorded)

    async def read_positions(self) -> BrokerPositionSnapshot:
        """Read fresh synthetic position truth correlated to the latest order."""

        self._require_available()
        self.position_readbacks += 1
        if self._latest_request_order_id is None:
            raise BrokerOutage("no submitted synthetic order has position truth")
        recorded = self._orders[self._latest_request_order_id]
        request = recorded.request
        observed_at = self._now()
        positions = tuple(
            PositionTruth(symbol=symbol, qty=quantity, observed_at=observed_at)
            for symbol, quantity in sorted(self._positions.items())
            if quantity != 0
        )
        return BrokerPositionSnapshot(
            order_id=recorded.order_id,
            client_order_id=request.client_order_id,
            permit_id=request.permit_id,
            open_permit_id=request.open_permit_id,
            event_run_id=request.event_run_id,
            reservation_id=request.reservation_id,
            correlation_sha256=request.correlation_sha256,
            policy_sha256=request.policy_sha256,
            snapshot_sha256=request.snapshot_sha256,
            account_id=request.account_id,
            account_class=request.account_class,
            order_class=request.order_class,
            limit_price=request.limit_price,
            legs=request.legs,
            observed_at=observed_at,
            positions=positions,
        )

    async def read_account(self) -> AccountTruth:
        """Read fresh synthetic account truth."""

        self._require_available()
        self.account_readbacks += 1
        return AccountTruth(
            account_id=self.configuration.account_id,
            account_class=PAPER_ACCOUNT_CLASS,
            equity=self.configuration.equity,
            buying_power=self.configuration.buying_power,
            observed_at=self._now(),
        )

    def _account_payload(self) -> dict[str, object]:
        return {
            "schema": SYNTHETIC_BROKER_STATE_SCHEMA,
            "schema_version": SYNTHETIC_BROKER_STATE_SCHEMA_VERSION,
            "surface": "account",
            "account_id": self.configuration.account_id,
            "account_class": PAPER_ACCOUNT_CLASS,
            "order_class": MULTI_LEG_ORDER_CLASS,
            "equity": _decimal_text(self.configuration.equity),
            "cash": _decimal_text(self.configuration.cash),
            "buying_power": _decimal_text(self.configuration.buying_power),
        }

    def _orders_payload(self) -> dict[str, object]:
        return {
            "schema": SYNTHETIC_BROKER_STATE_SCHEMA,
            "schema_version": SYNTHETIC_BROKER_STATE_SCHEMA_VERSION,
            "surface": "orders",
            "orders": [
                {
                    "order_id": self._orders[order_id].order_id,
                    "client_order_id": self._orders[order_id].request.client_order_id,
                    "phase": self._orders[order_id].request.phase,
                    "permit_id": self._orders[order_id].request.permit_id,
                    "status": self._orders[order_id].status,
                    "filled_qty": self._orders[order_id].filled_qty,
                    "limit_price": _decimal_text(self._orders[order_id].request.limit_price),
                }
                for order_id in sorted(self._orders)
            ],
        }

    def _positions_payload(self) -> dict[str, object]:
        return {
            "schema": SYNTHETIC_BROKER_STATE_SCHEMA,
            "schema_version": SYNTHETIC_BROKER_STATE_SCHEMA_VERSION,
            "surface": "positions",
            "positions": [
                {"symbol": symbol, "qty": _decimal_text(quantity)}
                for symbol, quantity in sorted(self._positions.items())
                if quantity != 0
            ],
        }

    def account_state_sha256(self) -> str:
        """Content-address the synthetic account truth."""

        return hashlib.sha256(_canonical_json(self._account_payload())).hexdigest()

    def orders_state_sha256(self) -> str:
        """Content-address the synthetic order book."""

        return hashlib.sha256(_canonical_json(self._orders_payload())).hexdigest()

    def positions_state_sha256(self) -> str:
        """Content-address the synthetic position book."""

        return hashlib.sha256(_canonical_json(self._positions_payload())).hexdigest()

    def open_order_count(self) -> int:
        """Count synthetic orders still in a working state."""

        return sum(
            1 for recorded in self._orders.values() if recorded.status in OPEN_WORKING_STATES
        )

    def working_order_ids(self) -> tuple[str, ...]:
        """Return deterministic identities of synthetic orders still working."""

        return tuple(
            sorted(
                order_id
                for order_id, recorded in self._orders.items()
                if recorded.status in OPEN_WORKING_STATES
            )
        )

    def open_position_count(self) -> int:
        """Count synthetic position legs with a nonzero quantity."""

        return sum(1 for quantity in self._positions.values() if quantity != 0)

    def is_flat(self) -> bool:
        """True only with no working order and no nonzero synthetic position."""

        return self.open_order_count() == 0 and self.open_position_count() == 0

    def account_snapshot(self) -> AccountSnapshot:
        """Return the risk-kernel account snapshot over the synthetic state."""

        return AccountSnapshot(
            equity=self.configuration.equity,
            buying_power=self.configuration.buying_power,
            currency="USD",
            observed_at=self._now(),
            cash=self.configuration.cash,
        )

    def position_snapshots(self) -> tuple[PositionSnapshot, ...]:
        """Return risk-kernel position snapshots over the synthetic book."""

        observed_at = self._now()
        snapshots: list[PositionSnapshot] = []
        for symbol, quantity in sorted(self._positions.items()):
            if quantity == 0:
                continue
            reference = self._position_reference_prices.get(symbol, Decimal(0))
            snapshots.append(
                PositionSnapshot(
                    underlying=self._position_underlyings.get(symbol, symbol),
                    quantity=quantity,
                    market_value=quantity * reference * Decimal(100),
                    observed_at=observed_at,
                )
            )
        return tuple(snapshots)

    def order_snapshots(self) -> tuple[OrderSnapshot, ...]:
        """Return risk-kernel order snapshots over the synthetic order book."""

        observed_at = self._now()
        snapshots: list[OrderSnapshot] = []
        for order_id in sorted(self._orders):
            recorded = self._orders[order_id]
            snapshots.append(
                OrderSnapshot(
                    order_id=order_id,
                    symbol=recorded.request.legs[0].symbol,
                    status=_ORDER_STATE_TO_SNAPSHOT_STATUS.get(recorded.status, recorded.status),
                    filled_quantity=Decimal(recorded.filled_qty),
                    observed_at=observed_at,
                )
            )
        return tuple(snapshots)

    def broker_now(self) -> datetime:
        """Return the current monotonic synthetic broker clock."""

        return self._now()


class SyntheticAccountTruthSource:
    """Read-only risk truth surface over one synthetic broker's state."""

    def __init__(self, broker: SyntheticPaperBroker) -> None:
        self._broker = broker

    def account(self) -> AccountSnapshot:
        return self._broker.account_snapshot()

    def positions(self) -> tuple[PositionSnapshot, ...]:
        return self._broker.position_snapshots()

    def orders(self) -> tuple[OrderSnapshot, ...]:
        return self._broker.order_snapshots()

    def broker_clock(self) -> datetime:
        return self._broker.broker_now()


def synthetic_account_fingerprint_sha256(
    configuration: SyntheticBrokerAccountConfiguration | None = None,
) -> str:
    """Return the account fingerprint bound to a fresh synthetic broker state."""

    broker = SyntheticPaperBroker(
        configuration=configuration or SyntheticBrokerAccountConfiguration()
    )
    return broker.account_state_sha256()


__all__ = [
    "MULTI_LEG_ORDER_CLASS",
    "PAPER_ACCOUNT_CLASS",
    "SYNTHETIC_BROKER_EPOCH",
    "SYNTHETIC_BROKER_STATE_SCHEMA",
    "SYNTHETIC_BROKER_STATE_SCHEMA_VERSION",
    "SYNTHETIC_PAPER_ACCOUNT_ID",
    "SyntheticAccountTruthSource",
    "SyntheticBrokerAccountConfiguration",
    "SyntheticBrokerAmbiguousMutation",
    "SyntheticBrokerRejected",
    "SyntheticPaperBroker",
    "synthetic_account_fingerprint_sha256",
]
