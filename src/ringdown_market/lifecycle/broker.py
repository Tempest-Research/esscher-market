"""PAPER-only broker boundary for the monitored lifecycle.

A submission acknowledgement is deliberately not broker truth. The worker creates a
fully-bound ``BrokerOrderRequest`` before mutation, then accepts a terminal result
only from fresh ``BrokerOrderTruth`` and ``BrokerPositionSnapshot`` readbacks. This
module contains no real adapter: ``FakePaperBroker`` is deterministic, in-memory,
and makes no network, MCP, account, or broker calls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ringdown_market.lifecycle.reasons import LifecycleReason, _reject
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes


class BrokerOrderState:
    """Broker order-state vocabulary (lowercase Alpaca-style values)."""

    NEW = "new"
    ACCEPTED = "accepted"
    PENDING_NEW = "pending_new"
    ACCEPTED_FOR_BIDDING = "accepted_for_bidding"
    HELD = "held"
    CALCULATED = "calculated"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"


OPEN_WORKING_STATES = frozenset(
    {
        BrokerOrderState.NEW,
        BrokerOrderState.ACCEPTED,
        BrokerOrderState.PENDING_NEW,
        BrokerOrderState.ACCEPTED_FOR_BIDDING,
        BrokerOrderState.HELD,
        BrokerOrderState.CALCULATED,
    }
)
OPEN_UNFILLED_TERMINAL_STATES = frozenset(
    {BrokerOrderState.CANCELED, BrokerOrderState.EXPIRED, BrokerOrderState.REJECTED}
)

PAPER_ACCOUNT_CLASS = "PAPER"
MULTI_LEG_ORDER_CLASS = "MULTI_LEG"

# Deterministic default truth clock for the fake broker. Real wall time is never
# used so unit tests stay reproducible.
FAKE_BROKER_EPOCH: datetime = datetime(2026, 9, 11, 14, 0, tzinfo=UTC)


class BrokerOutage(RuntimeError):
    """Raised when the broker cannot be reached or answers ambiguously."""


@dataclass(frozen=True, slots=True)
class BrokerOptionLeg:
    """One broker-reported option leg in a submitted multi-leg order."""

    symbol: str
    quantity: int
    side: str
    position_intent: str


@dataclass(frozen=True, slots=True)
class BrokerOrderRequest:
    """The exact order identity persisted before a PAPER mutation."""

    client_order_id: str
    phase: str
    permit_id: str
    open_permit_id: str
    event_run_id: str
    reservation_id: str
    correlation_sha256: str
    policy_sha256: str
    snapshot_sha256: str
    account_id: str
    account_class: str
    order_class: str
    limit_price: Decimal
    legs: tuple[BrokerOptionLeg, ...]


ORDER_REQUEST_SCHEMA = "ringdown.lifecycle_order_request"
ORDER_REQUEST_SCHEMA_VERSION = 1


def broker_order_request_payload(
    request: BrokerOrderRequest, *, expected_quantity: int
) -> dict[str, object]:
    """Return one canonical durable pre-submit request payload."""

    return {
        "schema": ORDER_REQUEST_SCHEMA,
        "schema_version": ORDER_REQUEST_SCHEMA_VERSION,
        "client_order_id": request.client_order_id,
        "phase": request.phase,
        "permit_id": request.permit_id,
        "open_permit_id": request.open_permit_id,
        "event_run_id": request.event_run_id,
        "reservation_id": request.reservation_id,
        "correlation_sha256": request.correlation_sha256,
        "policy_sha256": request.policy_sha256,
        "snapshot_sha256": request.snapshot_sha256,
        "account_id": request.account_id,
        "account_class": request.account_class,
        "order_class": request.order_class,
        "expected_quantity": expected_quantity,
        "limit_price": format(request.limit_price.normalize(), "f"),
        "legs": [
            {
                "symbol": leg.symbol,
                "quantity": leg.quantity,
                "side": leg.side,
                "position_intent": leg.position_intent,
            }
            for leg in request.legs
        ],
    }


def broker_order_request_sha256(request: BrokerOrderRequest, *, expected_quantity: int) -> str:
    """Hash every immutable pre-submit order term for ledger/passport correlation."""

    return sha256_bytes(
        canonical_json_bytes(
            broker_order_request_payload(request, expected_quantity=expected_quantity)
        )
    )


def parse_broker_order_request_payload(
    payload: Mapping[str, object],
) -> tuple[BrokerOrderRequest, int]:
    """Parse a validated canonical request payload for restart reconciliation."""

    if (
        payload.get("schema") != ORDER_REQUEST_SCHEMA
        or payload.get("schema_version") != ORDER_REQUEST_SCHEMA_VERSION
    ):
        raise ValueError("unsupported lifecycle order-request schema")

    def text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"{name} must be non-empty exact text")
        return value

    phase = text("phase")
    if phase not in {"OPEN", "CLOSE"}:
        raise ValueError("phase must be OPEN or CLOSE")
    expected_quantity = payload.get("expected_quantity")
    if (
        isinstance(expected_quantity, bool)
        or not isinstance(expected_quantity, int)
        or expected_quantity <= 0
    ):
        raise ValueError("expected_quantity must be a positive integer")
    try:
        limit_price = Decimal(text("limit_price"))
    except Exception as error:
        raise ValueError("limit_price must be a finite Decimal string") from error
    if not limit_price.is_finite():
        raise ValueError("limit_price must be finite")
    raw_legs = payload.get("legs")
    if not isinstance(raw_legs, list) or not raw_legs:
        raise ValueError("legs must be a non-empty list")
    legs: list[BrokerOptionLeg] = []
    for raw_leg in raw_legs:
        if not isinstance(raw_leg, Mapping):
            raise ValueError("leg must be an object")
        leg_quantity = raw_leg.get("quantity")
        if isinstance(leg_quantity, bool) or not isinstance(leg_quantity, int) or leg_quantity <= 0:
            raise ValueError("leg quantity must be a positive integer")
        for field_name in ("symbol", "side", "position_intent"):
            value = raw_leg.get(field_name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"leg.{field_name} must be non-empty exact text")
        legs.append(
            BrokerOptionLeg(
                symbol=str(raw_leg["symbol"]),
                quantity=leg_quantity,
                side=str(raw_leg["side"]),
                position_intent=str(raw_leg["position_intent"]),
            )
        )
    return (
        BrokerOrderRequest(
            client_order_id=text("client_order_id"),
            phase=phase,
            permit_id=text("permit_id"),
            open_permit_id=text("open_permit_id"),
            event_run_id=text("event_run_id"),
            reservation_id=text("reservation_id"),
            correlation_sha256=text("correlation_sha256"),
            policy_sha256=text("policy_sha256"),
            snapshot_sha256=text("snapshot_sha256"),
            account_id=text("account_id"),
            account_class=text("account_class"),
            order_class=text("order_class"),
            limit_price=limit_price,
            legs=tuple(legs),
        ),
        expected_quantity,
    )


@dataclass(frozen=True, slots=True)
class BrokerOrderAck:
    """A submission receipt, deliberately not a fill or order-truth assertion."""

    order_id: str
    client_order_id: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class BrokerOrderTruth:
    """Fresh broker readback for one exact order identity."""

    order_id: str
    client_order_id: str
    status: str
    filled_qty: int
    observed_at: datetime
    permit_id: str
    open_permit_id: str
    event_run_id: str
    reservation_id: str
    correlation_sha256: str
    policy_sha256: str
    snapshot_sha256: str
    account_id: str
    account_class: str
    order_class: str
    limit_price: Decimal
    legs: tuple[BrokerOptionLeg, ...]


@dataclass(frozen=True, slots=True)
class PositionTruth:
    """One broker-observed option position."""

    symbol: str
    qty: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class BrokerPositionSnapshot:
    """Fresh broker position truth correlated to one submitted order."""

    order_id: str
    client_order_id: str
    permit_id: str
    open_permit_id: str
    event_run_id: str
    reservation_id: str
    correlation_sha256: str
    policy_sha256: str
    snapshot_sha256: str
    account_id: str
    account_class: str
    order_class: str
    limit_price: Decimal
    legs: tuple[BrokerOptionLeg, ...]
    observed_at: datetime
    positions: tuple[PositionTruth, ...]


@dataclass(frozen=True, slots=True)
class AccountTruth:
    """One broker-observed account truth."""

    account_id: str
    account_class: str
    equity: Decimal
    buying_power: Decimal
    observed_at: datetime


@runtime_checkable
class PaperBroker(Protocol):
    """The narrow PAPER-only submit/read surface the lifecycle drives."""

    async def submit_open(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        """Submit one pre-persisted opening request and return its receipt."""
        ...

    async def read_order(self, order_id: str) -> BrokerOrderTruth:
        """Read back one order's fresh broker truth (no mutation)."""
        ...

    async def cancel_order(self, order_id: str) -> BrokerOrderAck:
        """Cancel one working order and return a non-proof receipt."""
        ...

    async def submit_close(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        """Submit one pre-persisted atomic close and return its receipt."""
        ...

    async def read_positions(self) -> BrokerPositionSnapshot:
        """Read fresh broker position truth linked to the most recent order."""
        ...

    async def read_account(self) -> AccountTruth:
        """Read fresh broker account truth (no mutation)."""
        ...


@dataclass
class FakePaperBroker:
    """Deterministic in-memory PAPER broker for unit and fault tests only.

    All behavior is scripted with terminal states and readback overrides. This
    fake has no provider credentials, network code, MCP calls, account access, or
    real broker mutation path.
    """

    open_terminal_state: str = BrokerOrderState.FILLED
    close_terminal_state: str = BrokerOrderState.FILLED
    open_filled_qty: int = 1
    close_filled_qty: int = 1
    open_submit_outage: bool = False
    close_submit_outage: bool = False
    read_outage: bool = False
    cancel_outage: bool = False
    positions_flat_after_close: bool = True
    residual_position_symbols: tuple[str, ...] = ()
    account: AccountTruth | None = None
    open_truth_overrides: Mapping[str, object] = field(default_factory=dict)
    close_truth_overrides: Mapping[str, object] = field(default_factory=dict)
    open_position_overrides: Mapping[str, object] = field(default_factory=dict)
    close_position_overrides: Mapping[str, object] = field(default_factory=dict)
    clock: Callable[[], datetime] = field(default=lambda: FAKE_BROKER_EPOCH)
    open_submissions: int = 0
    close_submissions: int = 0
    cancel_attempts: int = 0
    order_readbacks: int = 0
    position_readbacks: int = 0
    account_readbacks: int = 0
    _open_order_id: str = "fake-open-order-1"
    _close_order_id: str = "fake-close-order-1"
    _open_state: str = BrokerOrderState.NEW
    _close_state: str = BrokerOrderState.NEW
    _open_request: BrokerOrderRequest | None = None
    _close_request: BrokerOrderRequest | None = None

    def _now(self) -> datetime:
        observed = self.clock()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise _reject(LifecycleReason.UNSUPPORTED_INPUT, "clock", "clock must be UTC")
        return observed

    def _ack(self, order_id: str, request: BrokerOrderRequest) -> BrokerOrderAck:
        return BrokerOrderAck(
            order_id=order_id,
            client_order_id=request.client_order_id,
            observed_at=self._now(),
        )

    async def submit_open(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        if self.open_submit_outage:
            raise BrokerOutage("open submission outage")
        self.open_submissions += 1
        self._open_request = request
        self._open_state = self.open_terminal_state
        return self._ack(self._open_order_id, request)

    async def read_order(self, order_id: str) -> BrokerOrderTruth:
        if self.read_outage:
            raise BrokerOutage("order readback outage")
        self.order_readbacks += 1
        if order_id == self._open_order_id and self._open_request is not None:
            return self._order_truth(
                order_id=self._open_order_id,
                request=self._open_request,
                status=self._open_state,
                filled_qty=self._filled_qty(self._open_state, self.open_filled_qty),
                overrides=self.open_truth_overrides,
            )
        if order_id == self._close_order_id and self._close_request is not None:
            return self._order_truth(
                order_id=self._close_order_id,
                request=self._close_request,
                status=self._close_state,
                filled_qty=self._filled_qty(self._close_state, self.close_filled_qty),
                overrides=self.close_truth_overrides,
            )
        raise BrokerOutage(f"unknown order {order_id}")

    def _order_truth(
        self,
        *,
        order_id: str,
        request: BrokerOrderRequest,
        status: str,
        filled_qty: int,
        overrides: Mapping[str, object],
    ) -> BrokerOrderTruth:
        truth = BrokerOrderTruth(
            order_id=order_id,
            client_order_id=request.client_order_id,
            status=status,
            filled_qty=filled_qty,
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
        return replace(truth, **dict(overrides))

    @staticmethod
    def _filled_qty(status: str, configured_qty: int) -> int:
        if status in {BrokerOrderState.FILLED, BrokerOrderState.PARTIALLY_FILLED}:
            return configured_qty
        return 0

    async def cancel_order(self, order_id: str) -> BrokerOrderAck:
        if self.cancel_outage:
            raise BrokerOutage("cancel outage")
        self.cancel_attempts += 1
        if order_id == self._open_order_id and self._open_request is not None:
            self._open_state = BrokerOrderState.CANCELED
            return self._ack(self._open_order_id, self._open_request)
        raise BrokerOutage(f"unknown order {order_id}")

    async def submit_close(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        if self.close_submit_outage:
            raise BrokerOutage("close submission outage")
        self.close_submissions += 1
        self._close_request = request
        self._close_state = self.close_terminal_state
        return self._ack(self._close_order_id, request)

    async def read_positions(self) -> BrokerPositionSnapshot:
        if self.read_outage:
            raise BrokerOutage("position readback outage")
        self.position_readbacks += 1
        if self._close_request is not None:
            request = self._close_request
            if self.positions_flat_after_close and self._close_state == BrokerOrderState.FILLED:
                positions: tuple[PositionTruth, ...] = ()
            else:
                symbols = tuple(self.residual_position_symbols) or tuple(
                    leg.symbol for leg in request.legs
                )
                positions = tuple(
                    PositionTruth(symbol=symbol, qty=Decimal(1), observed_at=self._now())
                    for symbol in symbols
                )
            return self._position_snapshot(
                order_id=self._close_order_id,
                request=request,
                positions=positions,
                overrides=self.close_position_overrides,
            )
        if self._open_request is not None:
            request = self._open_request
            positions = tuple(
                PositionTruth(
                    symbol=leg.symbol,
                    qty=Decimal(leg.quantity if leg.side == "buy" else -leg.quantity),
                    observed_at=self._now(),
                )
                for leg in request.legs
            )
            return self._position_snapshot(
                order_id=self._open_order_id,
                request=request,
                positions=positions,
                overrides=self.open_position_overrides,
            )
        raise BrokerOutage("no submitted order has position truth")

    def _position_snapshot(
        self,
        *,
        order_id: str,
        request: BrokerOrderRequest,
        positions: tuple[PositionTruth, ...],
        overrides: Mapping[str, object],
    ) -> BrokerPositionSnapshot:
        snapshot = BrokerPositionSnapshot(
            order_id=order_id,
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
            observed_at=self._now(),
            positions=positions,
        )
        return replace(snapshot, **dict(overrides))

    async def read_account(self) -> AccountTruth:
        if self.read_outage:
            raise BrokerOutage("account readback outage")
        self.account_readbacks += 1
        if self.account is not None:
            return self.account
        return AccountTruth(
            account_id="paper-account",
            account_class=PAPER_ACCOUNT_CLASS,
            equity=Decimal("100000.00"),
            buying_power=Decimal("100000.00"),
            observed_at=self._now(),
        )


def ensure_no_residue(positions: Sequence[PositionTruth]) -> None:
    """Fail closed when any broker-observed position truth remains."""

    remaining = tuple(position.symbol for position in positions if position.qty != 0)
    if remaining:
        raise _reject(
            LifecycleReason.NON_FLAT_CLOSE,
            "positions",
            "position truth still contains event legs: " + ", ".join(sorted(remaining)),
        )
