"""Broker boundary for the monitored PAPER lifecycle.

``PaperBroker`` is the narrow read/submit surface the lifecycle worker needs.
It is satisfied by the official constrained Alpaca PAPER adapter in the later
approval-gated integration; in this issue it is driven only by the
deterministic ``FakePaperBroker`` so unit and fault tests make zero real MCP or
broker calls. Broker acknowledgement is never treated as fill proof: the worker
always reads back order and position truth.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ringdown_market.execution.models import ClosePermit, DebitVerticalPermit
from ringdown_market.lifecycle.reasons import LifecycleReason, _reject


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


class BrokerOutage(RuntimeError):
    """Raised when the broker cannot be reached or answers ambiguously."""


@dataclass(frozen=True, slots=True)
class BrokerOrderAck:
    """One sanitized broker order observation."""

    order_id: str
    client_order_id: str
    status: str
    filled_qty: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PositionTruth:
    """One broker-observed position."""

    symbol: str
    qty: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class AccountTruth:
    """One broker-observed account truth."""

    equity: Decimal
    buying_power: Decimal
    observed_at: datetime


@runtime_checkable
class PaperBroker(Protocol):
    """The narrow broker surface the monitored lifecycle drives."""

    async def submit_open(self, permit: DebitVerticalPermit) -> BrokerOrderAck:
        """Submit the opening order and return the broker acknowledgement."""
        ...

    async def read_order(self, order_id: str) -> BrokerOrderAck:
        """Read back one order's truth (no mutation)."""
        ...

    async def cancel_order(self, order_id: str) -> BrokerOrderAck:
        """Cancel one working order and return the resulting truth."""
        ...

    async def submit_close(
        self, open_permit: DebitVerticalPermit, close_permit: ClosePermit
    ) -> BrokerOrderAck:
        """Submit the atomic close and return the broker acknowledgement."""
        ...

    async def read_positions(self) -> tuple[PositionTruth, ...]:
        """Read current position truth (no mutation)."""
        ...

    async def read_account(self) -> AccountTruth:
        """Read account truth (no mutation)."""
        ...


@dataclass
class FakePaperBroker:
    """Deterministic in-memory broker for unit and fault tests.

    Behavior is scripted via terminal states and optional outage flags; no
    network, MCP, or real broker call is ever made.
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
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    open_submissions: int = 0
    close_submissions: int = 0
    cancel_attempts: int = 0
    _open_order_id: str = "fake-open-order-1"
    _close_order_id: str = "fake-close-order-1"
    _open_state: str = BrokerOrderState.NEW
    _close_state: str = BrokerOrderState.NEW

    def _now(self) -> datetime:
        observed = self.clock()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise _reject(LifecycleReason.UNSUPPORTED_INPUT, "clock", "clock must be UTC")
        return observed

    def _ack(self, order_id: str, status: str, filled_qty: int) -> BrokerOrderAck:
        return BrokerOrderAck(
            order_id=order_id,
            client_order_id=f"client-{order_id}",
            status=status,
            filled_qty=filled_qty,
            observed_at=self._now(),
        )

    async def submit_open(self, permit: DebitVerticalPermit) -> BrokerOrderAck:
        if self.open_submit_outage:
            raise BrokerOutage("open submission outage")
        self.open_submissions += 1
        if self.open_terminal_state in OPEN_UNFILLED_TERMINAL_STATES:
            self._open_state = self.open_terminal_state
            return self._ack(self._open_order_id, self._open_state, 0)
        if self.open_terminal_state == BrokerOrderState.PARTIALLY_FILLED:
            self._open_state = BrokerOrderState.PARTIALLY_FILLED
            return self._ack(self._open_order_id, self._open_state, self.open_filled_qty)
        self._open_state = BrokerOrderState.FILLED
        return self._ack(self._open_order_id, self._open_state, self.open_filled_qty)

    async def read_order(self, order_id: str) -> BrokerOrderAck:
        if self.read_outage:
            raise BrokerOutage("order readback outage")
        if order_id == self._open_order_id:
            filled = (
                self.open_filled_qty
                if self._open_state
                in {
                    BrokerOrderState.FILLED,
                    BrokerOrderState.PARTIALLY_FILLED,
                }
                else 0
            )
            return self._ack(self._open_order_id, self._open_state, filled)
        if order_id == self._close_order_id:
            filled = self.close_filled_qty if self._close_state == BrokerOrderState.FILLED else 0
            return self._ack(self._close_order_id, self._close_state, filled)
        raise BrokerOutage(f"unknown order {order_id}")

    async def cancel_order(self, order_id: str) -> BrokerOrderAck:
        if self.cancel_outage:
            raise BrokerOutage("cancel outage")
        self.cancel_attempts += 1
        if order_id == self._open_order_id:
            self._open_state = BrokerOrderState.CANCELED
            return self._ack(self._open_order_id, self._open_state, 0)
        raise BrokerOutage(f"unknown order {order_id}")

    async def submit_close(
        self, open_permit: DebitVerticalPermit, close_permit: ClosePermit
    ) -> BrokerOrderAck:
        if self.close_submit_outage:
            raise BrokerOutage("close submission outage")
        self.close_submissions += 1
        if self.close_terminal_state == BrokerOrderState.PARTIALLY_FILLED:
            self._close_state = BrokerOrderState.PARTIALLY_FILLED
            return self._ack(self._close_order_id, self._close_state, self.close_filled_qty)
        self._close_state = BrokerOrderState.FILLED
        return self._ack(self._close_order_id, self._close_state, self.close_filled_qty)

    async def read_positions(self) -> tuple[PositionTruth, ...]:
        if self.positions_flat_after_close and self._close_state == BrokerOrderState.FILLED:
            return ()
        symbols = tuple(self.residual_position_symbols) or ("LEG_A", "LEG_B")
        return tuple(
            PositionTruth(symbol=symbol, qty=Decimal(1), observed_at=self._now())
            for symbol in symbols
        )

    async def read_account(self) -> AccountTruth:
        if self.account is not None:
            return self.account
        return AccountTruth(
            equity=Decimal("100000.00"),
            buying_power=Decimal("100000.00"),
            observed_at=self._now(),
        )


def ensure_no_residue(positions: Sequence[PositionTruth]) -> None:
    """Fail closed when any position truth remains."""

    remaining = tuple(position.symbol for position in positions if position.qty != 0)
    if remaining:
        raise _reject(
            LifecycleReason.NON_FLAT_CLOSE,
            "positions",
            "position truth still contains event legs: " + ", ".join(sorted(remaining)),
        )
