"""The monitored PAPER lifecycle worker and deterministic close-permit issuer.

The worker drives one risk-approved promoted expression through its frozen
exit plan: opening submission, holding, time-exit, deterministic close, and
broker-confirmed flatness. Every transition and side-effect intent is persisted
before the next mutation; a restart resumes from broker/ledger truth rather
than replaying intent. Strategy clocks are read from the frozen exit plan and
cannot be altered by model prose or a hard-coded fallback. The LLM cannot
initiate, delay, optimize, or cancel an exit.

Actual PAPER mutation stays blocked behind the approval gate (the later #9);
the worker therefore runs against a broker that only the gate can make live,
and every test uses a deterministic fake with zero real MCP/broker calls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from ringdown_market.execution.models import ClosePermit, DebitVerticalPermit
from ringdown_market.lifecycle.broker import (
    BrokerOrderAck,
    BrokerOutage,
    PaperBroker,
    ensure_no_residue,
)
from ringdown_market.lifecycle.clocks import LifecycleClocks
from ringdown_market.lifecycle.correlation import CorrelationIdentity
from ringdown_market.lifecycle.reasons import (
    LifecycleReason,
    LifecycleState,
    _reject,
)
from ringdown_market.lifecycle.reducer import reduce_close_order, reduce_open_order
from ringdown_market.lifecycle.states import LifecycleTrigger, next_lifecycle_state
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.risk.passport import PassportEventType
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes


class MutationGate(Protocol):
    """The approval gate that keeps actual PAPER mutation blocked until #9."""

    def mutation_permitted(self) -> bool:
        """True only when the later approval gate has opened PAPER mutation."""
        ...


class ClosedMutationGate:
    """The default gate: actual PAPER mutation remains blocked."""

    def mutation_permitted(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    """The terminal outcome of one monitored lifecycle run."""

    event_run_id: str
    state: LifecycleState
    open_order_id: str | None
    close_order_id: str | None
    realized_paper_pnl: Decimal | None
    shadow_pnl: Decimal | None
    manual_reason: str | None


def issue_close_permit(
    *,
    open_permit: DebitVerticalPermit,
    event_run_id: str,
    policy_sha256: str,
    snapshot_sha256: str,
    issued_at: datetime,
    expires_at: datetime,
    limit_price: Decimal,
) -> ClosePermit:
    """Issue one deterministic one-use close permit bound to the opening permit.

    The close limit price is a frozen parameter (a negative credit); it is never
    inferred at run time. There is no production close-permit builder elsewhere,
    so this is the single deterministic issuer.
    """

    if open_permit.event_run_id != event_run_id:
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT,
            "close_permit.event_run_id",
            "close permit must bind the opening permit's event",
        )
    permit_id = _close_permit_id(
        open_permit_id=open_permit.permit_id,
        event_run_id=event_run_id,
        policy_sha256=policy_sha256,
        snapshot_sha256=snapshot_sha256,
        issued_at=issued_at,
        expires_at=expires_at,
        limit_price=limit_price,
    )
    return ClosePermit(
        permit_id=permit_id,
        open_permit_id=open_permit.permit_id,
        event_run_id=event_run_id,
        policy_sha256=policy_sha256,
        snapshot_sha256=snapshot_sha256,
        issued_at=issued_at,
        expires_at=expires_at,
        limit_price=limit_price,
    )


def _close_permit_id(
    *,
    open_permit_id: str,
    event_run_id: str,
    policy_sha256: str,
    snapshot_sha256: str,
    issued_at: datetime,
    expires_at: datetime,
    limit_price: Decimal,
) -> str:
    payload = {
        "schema": "ringdown.close_permit_id",
        "schema_version": 1,
        "open_permit_id": open_permit_id,
        "event_run_id": event_run_id,
        "policy_sha256": policy_sha256,
        "snapshot_sha256": snapshot_sha256,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "limit_price": format(limit_price.normalize(), "f"),
    }
    return "pc-" + sha256_bytes(canonical_json_bytes(payload))[:32]


@dataclass
class MonitoredPaperLifecycle:
    """Drive one risk-approved expression through its frozen PAPER lifecycle."""

    broker: PaperBroker
    ledger: RiskLedger
    clocks: LifecycleClocks
    correlation: CorrelationIdentity
    mutation_gate: MutationGate
    clock: Callable[[], datetime]
    _submitted_open_permits: set[str] = field(default_factory=set)
    _submitted_close_permits: set[str] = field(default_factory=set)

    def _now(self) -> datetime:
        observed = self.clock()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise _reject(LifecycleReason.UNSUPPORTED_INPUT, "clock", "clock must be UTC")
        return observed

    def _passport(self, event_type: PassportEventType, payload: Mapping[str, object]) -> None:
        self.ledger.append_passport(event_type=event_type.value, payload=payload, now=self._now())

    def _require_mutation(self) -> None:
        if not self.mutation_gate.mutation_permitted():
            raise _reject(
                LifecycleReason.MUTATION_GATE_CLOSED,
                "mutation_gate",
                "actual PAPER mutation remains blocked until the approval gate",
            )

    def _require_clocks_verified(self) -> None:
        if not self.clocks.source_verified:
            raise _reject(
                LifecycleReason.EXIT_PLAN_UNVERIFIED,
                "clocks.source_sha256",
                "exit-plan clocks are unverified; no fallback is permitted",
            )

    # -- opening -------------------------------------------------------------

    async def open(self, open_permit: DebitVerticalPermit) -> tuple[LifecycleState, str]:
        """Submit the opening order and reduce it to a terminal opening state."""

        self._require_clocks_verified()
        state = LifecycleState.APPROVED
        now = self._now()
        if now > self.clocks.entry_deadline_at:
            raise _reject(
                LifecycleReason.ENTRY_DEADLINE_PASSED,
                "clocks.entry_deadline_at",
                "the entry deadline has passed before submission",
            )
        self._require_mutation()
        if open_permit.permit_id in self._submitted_open_permits:
            raise _reject(
                LifecycleReason.DUPLICATE_TICK,
                f"open.{open_permit.permit_id}",
                "this opening permit was already submitted; a tick cannot duplicate it",
            )
        self._submitted_open_permits.add(open_permit.permit_id)

        state = next_lifecycle_state(state, LifecycleTrigger.OPEN_SUBMIT)
        try:
            ack = await self.broker.submit_open(open_permit)
        except BrokerOutage as error:
            self._passport(
                PassportEventType.ORDER_SUBMITTED,
                {"event_run_id": self.correlation.event_run_id, "outcome": "OUTAGE"},
            )
            raise _reject(LifecycleReason.BROKER_OUTAGE, "open.submit", str(error)) from error
        self._passport(
            PassportEventType.ORDER_SUBMITTED,
            {
                "event_run_id": self.correlation.event_run_id,
                "order_id": ack.order_id,
                "phase": "OPEN",
            },
        )

        state = reduce_open_order(ack, expected_qty=open_permit.quantity)
        if state is LifecycleState.OPEN_SUBMITTED:
            # Still working; poll once for a terminal opening state.
            ack = await self._read_order_or_outage(ack.order_id)
            state = reduce_open_order(ack, expected_qty=open_permit.quantity)
        self._record_open_transition(state, ack)
        return state, ack.order_id

    async def _read_order_or_outage(self, order_id: str) -> BrokerOrderAck:
        try:
            return await self.broker.read_order(order_id)
        except BrokerOutage as error:
            raise _reject(
                LifecycleReason.BROKER_OUTAGE, f"read_order.{order_id}", str(error)
            ) from error

    def _record_open_transition(self, state: LifecycleState, ack: BrokerOrderAck) -> None:
        self.ledger.record_fill(
            fill_id=f"open-{ack.order_id}",
            permit_id=self.correlation.open_permit_id,
            event_id=self.correlation.event_run_id,
            quantity=str(ack.filled_qty),
            status=state.value,
            observed_at=ack.observed_at,
        )
        self._passport(
            PassportEventType.FILL_OBSERVED,
            {
                "event_run_id": self.correlation.event_run_id,
                "order_id": ack.order_id,
                "phase": "OPEN",
                "state": state.value,
            },
        )

    # -- closing -------------------------------------------------------------

    async def close(
        self,
        open_permit: DebitVerticalPermit,
        close_permit: ClosePermit,
        *,
        open_order_id: str,
    ) -> tuple[LifecycleState, str | None]:
        """Submit the deterministic close and reconcile to broker-confirmed flat."""

        self._require_clocks_verified()
        self._require_mutation()
        if close_permit.permit_id in self._submitted_close_permits:
            raise _reject(
                LifecycleReason.DUPLICATE_TICK,
                f"close.{close_permit.permit_id}",
                "this close permit was already submitted; a tick cannot duplicate it",
            )
        self._submitted_close_permits.add(close_permit.permit_id)
        state = next_lifecycle_state(LifecycleState.CLOSE_DUE, LifecycleTrigger.CLOSE_SUBMIT)
        try:
            ack = await self.broker.submit_close(open_permit, close_permit)
        except BrokerOutage as error:
            raise _reject(LifecycleReason.BROKER_OUTAGE, "close.submit", str(error)) from error
        self._passport(
            PassportEventType.ORDER_SUBMITTED,
            {
                "event_run_id": self.correlation.event_run_id,
                "order_id": ack.order_id,
                "phase": "CLOSE",
            },
        )

        positions = await self._read_positions_or_outage()
        state = reduce_close_order(ack, positions, expected_qty=open_permit.quantity)
        if state is LifecycleState.CLOSE_PARTIAL:
            raise _reject(
                LifecycleReason.CLOSE_ORDER_PARTIAL,
                f"close.{ack.order_id}",
                "partial close cannot be repaired by sequential option legging",
            )
        if state is LifecycleState.MANUAL_REQUIRED:
            raise _reject(
                LifecycleReason.MANUAL_REQUIRED,
                f"close.{ack.order_id}",
                "close could not be confirmed flat from broker truth",
            )
        ensure_no_residue(positions)
        self._passport(
            PassportEventType.RECONCILED,
            {
                "event_run_id": self.correlation.event_run_id,
                "order_id": ack.order_id,
                "phase": "CLOSE",
                "state": state.value,
            },
        )
        return state, ack.order_id

    async def _read_positions_or_outage(self):
        try:
            return await self.broker.read_positions()
        except BrokerOutage as error:
            raise _reject(LifecycleReason.BROKER_OUTAGE, "read_positions", str(error)) from error

    # -- recovery ------------------------------------------------------------

    async def recover_open_state(self, open_order_id: str, *, expected_qty: int) -> LifecycleState:
        """Resume an opening state from broker truth (never replay intent)."""

        ack = await self._read_order_or_outage(open_order_id)
        return reduce_open_order(ack, expected_qty=expected_qty)

    async def recover_flatness(self) -> LifecycleState:
        """Resume flatness from position truth (never replay intent)."""

        positions = await self._read_positions_or_outage()
        if all(position.qty == 0 for position in positions):
            return LifecycleState.CLOSED_FLAT
        return LifecycleState.MANUAL_REQUIRED
