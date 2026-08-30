"""Durable monitored PAPER lifecycle: 60-minute fill-relative hold and deterministic close.

The worker advances one event through a persisted state machine over the single
risk ledger. Every transition is persisted before the next side effect. Broker
acknowledgement is never fill proof; unknown or partial truth stops new entries
and retains reconciliation and close authority. No model, profit-take, or
stop-loss controls the exit in v1.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from ringdown_market.risk.ledger import RiskLedger

HOLD_MINUTES = 60
MAX_OUTAGE_TICKS = 3
MAX_CLOSE_RETRIES = 3
MAX_REPRICES = 3


class LifecycleState(StrEnum):
    """Persisted lifecycle states; every transition is durable before side effects."""

    APPROVED = "APPROVED"
    OPEN_SUBMITTED = "OPEN_SUBMITTED"
    OPEN_PARTIAL = "OPEN_PARTIAL"
    OPEN_FILLED = "OPEN_FILLED"
    OPEN_CANCELED = "OPEN_CANCELED"
    HOLDING = "HOLDING"
    CLOSE_DUE = "CLOSE_DUE"
    CLOSE_SUBMITTED = "CLOSE_SUBMITTED"
    CLOSED_FLAT = "CLOSED_FLAT"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


_TERMINAL_STATES = frozenset(
    {
        LifecycleState.OPEN_CANCELED,
        LifecycleState.CLOSED_FLAT,
        LifecycleState.MANUAL_REQUIRED,
    }
)


class OrderStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"


class BrokerOutage(Exception):
    """Raised by the broker boundary when it cannot be reached."""


class StaleQuoteRejected(Exception):
    """Raised when a close submission is rejected for a stale quote."""


@dataclass(frozen=True, slots=True)
class BrokerOrderView:
    client_order_id: str
    status: OrderStatus
    filled_quantity: Decimal
    total_quantity: Decimal


class LifecycleBroker(Protocol):
    """Injected broker boundary; deterministic client IDs deduplicate submissions."""

    def submit_open(self, client_order_id: str) -> None: ...

    def submit_close(self, client_order_id: str, *, repricing_step: int) -> None: ...

    def order_view(self, client_order_id: str) -> BrokerOrderView | None: ...

    def position_symbols(self) -> frozenset[str]: ...


class FakeLifecycleBroker:
    """Deterministic scripted broker for offline lifecycle tests."""

    def __init__(
        self,
        *,
        leg_symbols: frozenset[str],
        open_outages: int = 0,
        close_outages: int = 0,
        open_path: tuple[OrderStatus, ...] = (OrderStatus.SUBMITTED, OrderStatus.FILLED),
        close_path: tuple[OrderStatus, ...] = (OrderStatus.SUBMITTED, OrderStatus.FILLED),
        close_stale_rejections: int = 0,
        flat_after_close: bool = True,
    ) -> None:
        self._leg_symbols = leg_symbols
        self._open_outages_left = open_outages
        self._close_outages_left = close_outages
        self._open_path = list(open_path)
        self._close_path = list(close_path)
        self._close_stale_left = close_stale_rejections
        self._flat_after_close = flat_after_close
        self.open_submissions: list[str] = []
        self.close_submissions: list[str] = []
        self._submitted: set[str] = set()
        self._open_polls = 0
        self._close_polls = 0

    def submit_open(self, client_order_id: str) -> None:
        if self._open_outages_left > 0:
            self._open_outages_left -= 1
            raise BrokerOutage("open submission unreachable")
        if client_order_id not in self._submitted:
            self._submitted.add(client_order_id)
            self.open_submissions.append(client_order_id)

    def submit_close(self, client_order_id: str, *, repricing_step: int) -> None:
        if self._close_stale_left > 0 and repricing_step <= MAX_REPRICES:
            self._close_stale_left -= 1
            raise StaleQuoteRejected("close quote is stale")
        if self._close_outages_left > 0:
            self._close_outages_left -= 1
            raise BrokerOutage("close submission unreachable")
        if client_order_id not in self._submitted:
            self._submitted.add(client_order_id)
            self.close_submissions.append(client_order_id)

    def order_view(self, client_order_id: str) -> BrokerOrderView | None:
        if client_order_id in self.open_submissions:
            self._open_polls += 1
            path = self._open_path
            polls = self._open_polls
        elif client_order_id in self.close_submissions:
            self._close_polls += 1
            path = self._close_path
            polls = self._close_polls
        else:
            return None
        status = path[min(polls - 1, len(path) - 1)]
        filled = Decimal(1) if status is OrderStatus.FILLED else Decimal(0)
        if status is OrderStatus.PARTIAL:
            filled = Decimal("0.5")
        return BrokerOrderView(
            client_order_id=client_order_id,
            status=status,
            filled_quantity=filled,
            total_quantity=Decimal(1),
        )

    def position_symbols(self) -> frozenset[str]:
        if self._flat_after_close and self._close_polls > 0:
            close_status = self._close_path[min(self._close_polls - 1, len(self._close_path) - 1)]
            if close_status is OrderStatus.FILLED:
                return frozenset()
        return self._leg_symbols


@dataclass(frozen=True, slots=True)
class LifecycleReceipt:
    """Sanitized terminal receipt; flatness requires broker position truth."""

    event_run_id: str
    reservation_id: str
    terminal_state: LifecycleState
    opening_client_order_id: str | None
    closing_client_order_id: str | None
    opened_at: object
    close_due_at: object
    fail_code: str | None


class LifecycleWorker:
    """One-event durable lifecycle driver over the risk ledger."""

    def __init__(
        self,
        *,
        ledger: RiskLedger,
        broker: LifecycleBroker,
        clock: Callable[[], object],
        hold_minutes: int = HOLD_MINUTES,
    ) -> None:
        self._ledger = ledger
        self._broker = broker
        self._clock = clock
        self._hold = timedelta(minutes=hold_minutes)

    def begin(self, *, event_run_id: str, reservation_id: str, now: object) -> None:
        """Persist the APPROVED entry state; restarts never replay intent."""

        existing = self._ledger.lifecycle_state(event_run_id)
        if existing is not None:
            return
        self._ledger.set_lifecycle_state(
            event_run_id=event_run_id,
            reservation_id=reservation_id,
            state=LifecycleState.APPROVED.value,
            updated_at=now,
        )

    def _manual(self, record: dict[str, object], code: str, now: object) -> LifecycleState:
        self._ledger.set_lifecycle_state(
            event_run_id=record["event_run_id"],
            reservation_id=record["reservation_id"],
            state=LifecycleState.MANUAL_REQUIRED.value,
            opening_client_order_id=record["opening_client_order_id"],
            closing_client_order_id=record["closing_client_order_id"],
            opened_at=record["opened_at"],
            close_due_at=record["close_due_at"],
            updated_at=now,
            fail_code=code,
        )
        return LifecycleState.MANUAL_REQUIRED

    def tick(self, event_run_id: str) -> LifecycleState:
        """Advance one event deterministically; terminal repeats are no-ops."""

        record = self._ledger.lifecycle_state(event_run_id)
        if record is None:
            raise ValueError("lifecycle state missing; begin() must run first")
        state = LifecycleState(record["state"])
        now = self._clock()
        if state in _TERMINAL_STATES:
            return state

        if state is LifecycleState.APPROVED:
            return self._tick_approved(record, now)
        if state in (LifecycleState.OPEN_SUBMITTED, LifecycleState.OPEN_PARTIAL):
            return self._tick_open_polling(record, state, now)
        if state is LifecycleState.OPEN_FILLED:
            return self._tick_open_filled(record, now)
        if state is LifecycleState.HOLDING:
            return self._tick_holding(record, now)
        if state is LifecycleState.CLOSE_DUE:
            return self._tick_close_due(record, now)
        if state is LifecycleState.CLOSE_SUBMITTED:
            return self._tick_close_polling(record, now)
        raise ValueError(f"unhandled lifecycle state {state.value}")

    def _opening_client_order_id(self, record: dict[str, object]) -> str:
        client_order_id = record["opening_client_order_id"]
        if client_order_id is None:
            client_order_id = f"open-{record['reservation_id']}"
        return str(client_order_id)

    def _tick_approved(self, record: dict[str, object], now: object) -> LifecycleState:
        client_order_id = self._opening_client_order_id(record)
        try:
            self._broker.submit_open(client_order_id)
        except BrokerOutage:
            tick_name = f"open-outage-{len(self._ledger.lifecycle_ticks(record['event_run_id']))}"
            self._ledger.record_lifecycle_tick(
                event_run_id=record["event_run_id"], tick=tick_name, at=now
            )
            if len(self._ledger.lifecycle_ticks(record["event_run_id"])) >= MAX_OUTAGE_TICKS:
                return self._manual(record, "OPEN_SUBMISSION_OUTAGE", now)
            return LifecycleState.APPROVED
        self._ledger.set_lifecycle_state(
            event_run_id=record["event_run_id"],
            reservation_id=record["reservation_id"],
            state=LifecycleState.OPEN_SUBMITTED.value,
            opening_client_order_id=client_order_id,
            updated_at=now,
        )
        return LifecycleState.OPEN_SUBMITTED

    def _tick_open_polling(
        self, record: dict[str, object], state: LifecycleState, now: object
    ) -> LifecycleState:
        client_order_id = self._opening_client_order_id(record)
        view = self._broker.order_view(client_order_id)
        if view is None or view.status is OrderStatus.UNKNOWN:
            return self._manual(record, "OPEN_ORDER_UNKNOWN", now)
        if view.status is OrderStatus.CANCELED:
            self._ledger.set_lifecycle_state(
                event_run_id=record["event_run_id"],
                reservation_id=record["reservation_id"],
                state=LifecycleState.OPEN_CANCELED.value,
                opening_client_order_id=client_order_id,
                updated_at=now,
            )
            self._ledger.release(reservation_id=str(record["reservation_id"]), now=now)
            return LifecycleState.OPEN_CANCELED
        if view.status is OrderStatus.PARTIAL:
            if state is LifecycleState.OPEN_PARTIAL:
                return self._manual(record, "OPEN_PARTIAL_UNRESOLVED", now)
            self._ledger.set_lifecycle_state(
                event_run_id=record["event_run_id"],
                reservation_id=record["reservation_id"],
                state=LifecycleState.OPEN_PARTIAL.value,
                opening_client_order_id=client_order_id,
                updated_at=now,
            )
            return LifecycleState.OPEN_PARTIAL
        if view.status is OrderStatus.FILLED:
            close_due_at = now + self._hold
            self._ledger.set_lifecycle_state(
                event_run_id=record["event_run_id"],
                reservation_id=record["reservation_id"],
                state=LifecycleState.OPEN_FILLED.value,
                opening_client_order_id=client_order_id,
                opened_at=now,
                close_due_at=close_due_at,
                updated_at=now,
            )
            self._ledger.consume(reservation_id=str(record["reservation_id"]), now=now)
            return LifecycleState.OPEN_FILLED
        self._ledger.record_lifecycle_tick(
            event_run_id=record["event_run_id"],
            tick=f"open-poll-{view.status.value}",
            at=now,
        )
        return state

    def _tick_open_filled(self, record: dict[str, object], now: object) -> LifecycleState:
        self._ledger.set_lifecycle_state(
            event_run_id=record["event_run_id"],
            reservation_id=record["reservation_id"],
            state=LifecycleState.HOLDING.value,
            opening_client_order_id=record["opening_client_order_id"],
            opened_at=record["opened_at"],
            close_due_at=record["close_due_at"],
            updated_at=now,
        )
        return LifecycleState.HOLDING

    def _tick_holding(self, record: dict[str, object], now: object) -> LifecycleState:
        close_due_at = record["close_due_at"]
        if close_due_at is not None and now >= close_due_at:
            self._ledger.set_lifecycle_state(
                event_run_id=record["event_run_id"],
                reservation_id=record["reservation_id"],
                state=LifecycleState.CLOSE_DUE.value,
                opening_client_order_id=record["opening_client_order_id"],
                opened_at=record["opened_at"],
                close_due_at=close_due_at,
                updated_at=now,
            )
            return LifecycleState.CLOSE_DUE
        return LifecycleState.HOLDING

    def _close_client_order_id(self, record: dict[str, object], repricing_step: int) -> str:
        base = f"close-{record['reservation_id']}"
        if repricing_step == 0:
            return base
        return f"{base}-r{repricing_step}"

    def _tick_close_due(self, record: dict[str, object], now: object) -> LifecycleState:
        repricing_step = 0
        while repricing_step <= MAX_REPRICES:
            client_order_id = self._close_client_order_id(record, repricing_step)
            try:
                self._broker.submit_close(client_order_id, repricing_step=repricing_step)
            except StaleQuoteRejected:
                repricing_step += 1
                self._ledger.record_lifecycle_tick(
                    event_run_id=record["event_run_id"],
                    tick=f"close-reprice-{repricing_step}",
                    at=now,
                )
                continue
            except BrokerOutage:
                return self._manual(record, "CLOSE_SUBMISSION_OUTAGE", now)
            self._ledger.set_lifecycle_state(
                event_run_id=record["event_run_id"],
                reservation_id=record["reservation_id"],
                state=LifecycleState.CLOSE_SUBMITTED.value,
                opening_client_order_id=record["opening_client_order_id"],
                closing_client_order_id=client_order_id,
                opened_at=record["opened_at"],
                close_due_at=record["close_due_at"],
                updated_at=now,
            )
            return LifecycleState.CLOSE_SUBMITTED
        return self._manual(record, "CLOSE_REPRICING_EXHAUSTED", now)

    def _tick_close_polling(self, record: dict[str, object], now: object) -> LifecycleState:
        client_order_id = str(record["closing_client_order_id"])
        view = self._broker.order_view(client_order_id)
        if view is None or view.status is OrderStatus.UNKNOWN:
            return self._manual(record, "CLOSE_ORDER_UNKNOWN", now)
        if view.status is OrderStatus.CANCELED:
            return self._manual(record, "CLOSE_CANCELED_UNRESOLVED", now)
        if view.status is OrderStatus.PARTIAL:
            return self._manual(record, "CLOSE_PARTIAL_UNRESOLVED", now)
        if view.status is OrderStatus.FILLED:
            positions = self._broker.position_symbols()
            opening_client_order_id = str(record["opening_client_order_id"] or "")
            leg_prefix = opening_client_order_id.split("-", 1)[0]
            flat = not any(symbol.strip() for symbol in positions)
            if flat:
                self._ledger.set_lifecycle_state(
                    event_run_id=record["event_run_id"],
                    reservation_id=record["reservation_id"],
                    state=LifecycleState.CLOSED_FLAT.value,
                    opening_client_order_id=record["opening_client_order_id"],
                    closing_client_order_id=client_order_id,
                    opened_at=record["opened_at"],
                    close_due_at=record["close_due_at"],
                    updated_at=now,
                )
                self._ledger.record_reconciliation(
                    outcome="CLOSED_FLAT", detail=leg_prefix, now=now
                )
                return LifecycleState.CLOSED_FLAT
            return self._manual(record, "NON_FLAT_AFTER_CLOSE", now)
        self._ledger.record_lifecycle_tick(
            event_run_id=record["event_run_id"],
            tick=f"close-poll-{view.status.value}",
            at=now,
        )
        return LifecycleState.CLOSE_SUBMITTED

    def receipt(self, event_run_id: str) -> LifecycleReceipt | None:
        record = self._ledger.lifecycle_state(event_run_id)
        if record is None:
            return None
        return LifecycleReceipt(
            event_run_id=event_run_id,
            reservation_id=str(record["reservation_id"]),
            terminal_state=LifecycleState(record["state"]),
            opening_client_order_id=record["opening_client_order_id"],
            closing_client_order_id=record["closing_client_order_id"],
            opened_at=record["opened_at"],
            close_due_at=record["close_due_at"],
            fail_code=record["fail_code"],
        )
