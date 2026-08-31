"""Deterministic, approval-gated monitored PAPER lifecycle worker.

The worker submits only an identity-bound order request after persisting a durable
intent. A submission acknowledgement is not a fill claim. Before it persists any
open fill or close-flat result it reads fresh account, order, and position truth,
then checks the whole permit/correlation/reservation/order/leg binding.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from ringdown_market.execution.models import (
    ClosePermit,
    DebitVerticalPermit,
    OptionSide,
    PositionIntent,
    debit_vertical_permit_id,
)
from ringdown_market.lifecycle.broker import (
    MULTI_LEG_ORDER_CLASS,
    PAPER_ACCOUNT_CLASS,
    AccountTruth,
    BrokerOptionLeg,
    BrokerOrderAck,
    BrokerOrderRequest,
    BrokerOrderState,
    BrokerOrderTruth,
    BrokerOutage,
    BrokerPositionSnapshot,
    PaperBroker,
    broker_order_request_payload,
    broker_order_request_sha256,
    ensure_no_residue,
    parse_broker_order_request_payload,
)
from ringdown_market.lifecycle.clocks import LifecycleClocks
from ringdown_market.lifecycle.correlation import CorrelationIdentity, correlation_sha256
from ringdown_market.lifecycle.reasons import LifecycleReason, LifecycleState, _reject
from ringdown_market.lifecycle.reducer import reduce_close_order, reduce_open_order
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.risk.reasons import RiskRejected
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

# Conservative bound on how old broker account/order/position truth may be.
DEFAULT_TRUTH_MAX_AGE_SECONDS: int = 30


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
    """Issue one deterministic one-use close permit bound to the opening permit."""

    if open_permit.event_run_id != event_run_id:
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT,
            "close_permit.event_run_id",
            "close permit must bind the opening permit's event",
        )
    if (
        policy_sha256 != open_permit.policy_sha256
        or snapshot_sha256 != open_permit.snapshot_sha256
    ):
        raise _reject(
            LifecycleReason.BROKER_TRUTH_MISMATCH,
            "close_permit",
            "close permit must preserve the opening policy and snapshot binding",
        )
    if not _close_credit_within_vertical_width(open_permit, limit_price):
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT,
            "close_permit.limit_price",
            "close credit cannot exceed the opening vertical width",
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


def _close_credit_within_vertical_width(
    open_permit: DebitVerticalPermit, limit_price: object
) -> bool:
    """Whether an atomic close credit is finite, negative, and no wider than the spread."""

    if not isinstance(limit_price, Decimal) or not limit_price.is_finite() or limit_price >= 0:
        return False
    width = abs(open_permit.legs[1].strike - open_permit.legs[0].strike)
    return abs(limit_price) <= width


@dataclass
class MonitoredPaperLifecycle:
    """Drive one risk-approved expression through its frozen PAPER lifecycle."""

    broker: PaperBroker
    ledger: RiskLedger
    clocks: LifecycleClocks
    correlation: CorrelationIdentity
    mutation_gate: MutationGate
    clock: Callable[[], datetime]
    account_id: str
    account_class: str
    order_class: str = MULTI_LEG_ORDER_CLASS
    truth_max_age_seconds: int = DEFAULT_TRUTH_MAX_AGE_SECONDS
    _submitted_open_permits: set[str] = field(default_factory=set)
    _submitted_close_permits: set[str] = field(default_factory=set)

    def _now(self) -> datetime:
        observed = self.clock()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise _reject(LifecycleReason.UNSUPPORTED_INPUT, "clock", "clock must be UTC")
        return observed

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

    def _require_paper_boundary(self) -> None:
        """Reject an invalid boundary before intent persistence or broker mutation."""

        if (
            not isinstance(self.account_id, str)
            or not self.account_id.strip()
            or self.account_id != self.account_id.strip()
            or self.account_class != PAPER_ACCOUNT_CLASS
            or self.order_class != MULTI_LEG_ORDER_CLASS
        ):
            raise _reject(
                LifecycleReason.UNSUPPORTED_INPUT,
                "broker_boundary",
                (
                    "lifecycle mutation requires a named PAPER account and atomic "
                    "multi-leg order class"
                ),
            )

    def _require_clock_scope(self, open_permit: DebitVerticalPermit) -> None:
        if (
            self.clocks.event_run_id != open_permit.event_run_id
            or self.clocks.policy_sha256 != open_permit.policy_sha256
        ):
            raise _reject(
                LifecycleReason.EXIT_PLAN_UNVERIFIED,
                "clocks",
                "exit-plan clocks do not bind the opening permit's event and policy",
            )

    def _require_active_window(
        self,
        *,
        issued_at: object,
        expires_at: object,
        path: str,
        reason: LifecycleReason,
    ) -> None:
        if not isinstance(issued_at, datetime) or not isinstance(expires_at, datetime):
            raise _reject(reason, path, "permit timing is malformed")
        now = self._now()
        if now < issued_at or now >= expires_at:
            raise _reject(reason, path, "permit is not active at the lifecycle clock")

    def _reject_truth(self, path: str, detail: str) -> None:
        raise _reject(LifecycleReason.BROKER_TRUTH_MISMATCH, path, detail)

    def _require_open_identity(self, open_permit: DebitVerticalPermit) -> None:
        if (
            open_permit.permit_id != self.correlation.open_permit_id
            or open_permit.permit_id != debit_vertical_permit_id(open_permit)
            or open_permit.event_run_id != self.correlation.event_run_id
            or open_permit.snapshot_sha256 != self.correlation.snapshot_sha256
            or open_permit.decision_sha256 != self.correlation.decision_sha256
        ):
            self._reject_truth("open_permit", "permit does not match the lifecycle correlation")

    def _require_open_binding(self, open_permit: DebitVerticalPermit) -> None:
        self._require_open_identity(open_permit)
        reservation = self.ledger.reservation_for_event(open_permit.event_run_id)
        permit = self.ledger.permit_for_event(open_permit.event_run_id)
        if (
            reservation is None
            or permit is None
            or reservation["reservation_id"] != self.correlation.reservation_id
            or reservation["state"] != "RESERVED"
            or permit["permit_id"] != open_permit.permit_id
            or permit["reservation_id"] != self.correlation.reservation_id
            or permit["state"] != "ISSUED"
        ):
            self._reject_truth(
                "open_permit.ledger",
                "permit/reservation is missing, mismatched, or not active for this event",
            )

    def _require_close_binding(
        self,
        open_permit: DebitVerticalPermit,
        close_permit: ClosePermit,
        open_order_id: str,
    ) -> CorrelationIdentity:
        self._require_open_identity(open_permit)
        if (
            close_permit.open_permit_id != open_permit.permit_id
            or close_permit.event_run_id != self.correlation.event_run_id
            or close_permit.policy_sha256 != open_permit.policy_sha256
            or close_permit.snapshot_sha256 != open_permit.snapshot_sha256
        ):
            self._reject_truth("close_permit", "close permit is not bound to the opened expression")
        if not _close_credit_within_vertical_width(open_permit, close_permit.limit_price):
            self._reject_truth(
                "close_permit.limit_price", "close credit exceeds the opening vertical width"
            )
        expected_close_permit_id = _close_permit_id(
            open_permit_id=close_permit.open_permit_id,
            event_run_id=close_permit.event_run_id,
            policy_sha256=close_permit.policy_sha256,
            snapshot_sha256=close_permit.snapshot_sha256,
            issued_at=close_permit.issued_at,
            expires_at=close_permit.expires_at,
            limit_price=close_permit.limit_price,
        )
        if close_permit.permit_id != expected_close_permit_id:
            self._reject_truth(
                "close_permit.permit_id", "close permit identifier is not deterministic"
            )
        if self.correlation.close_permit_id not in {None, close_permit.permit_id}:
            self._reject_truth(
                "correlation.close_permit_id", "close permit differs from correlation"
            )
        submission = self.ledger.submission_for_permit(open_permit.permit_id)
        reservation = self.ledger.reservation_for_event(open_permit.event_run_id)
        if (
            submission is None
            or submission["broker_order_id"] != open_order_id
            or self.ledger.permit_state(open_permit.permit_id) != "FILLED"
            or reservation is None
            or reservation["reservation_id"] != self.correlation.reservation_id
            or reservation["state"] != "CONSUMED"
        ):
            self._reject_truth(
                "close.open_proof",
                "open permit/order/reservation does not prove a filled correlated opening",
            )
        return self.correlation.with_close_permit(close_permit.permit_id)

    @staticmethod
    def _open_legs(open_permit: DebitVerticalPermit) -> tuple[BrokerOptionLeg, ...]:
        return tuple(
            BrokerOptionLeg(
                symbol=leg.symbol,
                quantity=open_permit.quantity * leg.ratio_qty,
                side=leg.side.value,
                position_intent=leg.position_intent.value,
            )
            for leg in open_permit.legs
        )

    @staticmethod
    def _close_legs(open_permit: DebitVerticalPermit) -> tuple[BrokerOptionLeg, ...]:
        inverse_side = {OptionSide.BUY: OptionSide.SELL, OptionSide.SELL: OptionSide.BUY}
        inverse_intent = {
            PositionIntent.BUY_TO_OPEN: PositionIntent.SELL_TO_CLOSE,
            PositionIntent.SELL_TO_OPEN: PositionIntent.BUY_TO_CLOSE,
        }
        try:
            return tuple(
                BrokerOptionLeg(
                    symbol=leg.symbol,
                    quantity=open_permit.quantity * leg.ratio_qty,
                    side=inverse_side[leg.side].value,
                    position_intent=inverse_intent[leg.position_intent].value,
                )
                for leg in open_permit.legs
            )
        except KeyError:
            raise _reject(
                LifecycleReason.UNSUPPORTED_INPUT,
                "close.legs",
                "close legs must reverse the frozen opening legs",
            ) from None

    def _request(
        self,
        *,
        phase: str,
        permit_id: str,
        open_permit: DebitVerticalPermit,
        correlation: CorrelationIdentity,
        limit_price: Decimal,
        legs: tuple[BrokerOptionLeg, ...],
    ) -> BrokerOrderRequest:
        digest = correlation_sha256(correlation)
        return BrokerOrderRequest(
            client_order_id=f"{phase.lower()}-{permit_id}-{digest[:16]}",
            phase=phase,
            permit_id=permit_id,
            open_permit_id=open_permit.permit_id,
            event_run_id=correlation.event_run_id,
            reservation_id=correlation.reservation_id,
            correlation_sha256=digest,
            policy_sha256=open_permit.policy_sha256,
            snapshot_sha256=open_permit.snapshot_sha256,
            account_id=self.account_id,
            account_class=self.account_class,
            order_class=self.order_class,
            limit_price=limit_price,
            legs=legs,
        )

    def _record_intent(self, request: BrokerOrderRequest, *, expected_qty: int) -> None:
        request_payload = broker_order_request_payload(
            request, expected_quantity=expected_qty
        )
        request_json = canonical_json_bytes(request_payload).decode("utf-8")
        try:
            self.ledger.record_lifecycle_intent(
                permit_id=request.permit_id,
                phase=request.phase,
                event_id=request.event_run_id,
                open_permit_id=request.open_permit_id,
                reservation_id=request.reservation_id,
                correlation_sha256=request.correlation_sha256,
                policy_sha256=request.policy_sha256,
                snapshot_sha256=request.snapshot_sha256,
                account_id=request.account_id,
                account_class=request.account_class,
                order_class=request.order_class,
                client_order_id=request.client_order_id,
                request_sha256=broker_order_request_sha256(
                    request, expected_quantity=expected_qty
                ),
                request_json=request_json,
                now=self._now(),
            )
        except RiskRejected as error:
            raise _reject(
                LifecycleReason.DUPLICATE_TICK,
                f"intent.{request.permit_id}",
                f"durable intent already exists or is invalid: {error.reason.value}",
            ) from error

    def _bind_ack(self, request: BrokerOrderRequest, ack: BrokerOrderAck) -> None:
        if (
            not isinstance(ack.order_id, str)
            or not ack.order_id.strip()
            or ack.client_order_id != request.client_order_id
        ):
            self._reject_truth("submission.ack", "ack does not bind to the submitted client order")
        try:
            self.ledger.bind_lifecycle_intent(
                permit_id=request.permit_id,
                phase=request.phase,
                broker_order_id=ack.order_id,
                now=self._now(),
            )
        except RiskRejected as error:
            self._reject_truth(
                "submission.intent", f"ack cannot bind durable intent: {error.reason.value}"
            )

    def _durable_submitted_request(
        self, intent: Mapping[str, object], *, phase: str
    ) -> tuple[BrokerOrderRequest, int, str]:
        """Restore one exact request only from its durable pre-submit intent."""

        permit_id = intent.get("permit_id")
        order_id = intent.get("broker_order_id")
        request_json = intent.get("request_json")
        request_hash = intent.get("request_sha256")
        if (
            intent.get("phase") != phase
            or intent.get("event_id") != self.correlation.event_run_id
            or not isinstance(permit_id, str)
            or not isinstance(order_id, str)
            or not order_id.strip()
            or intent.get("state") not in {"SUBMITTED", "RECONCILED"}
            or not isinstance(request_json, str)
            or not isinstance(request_hash, str)
        ):
            raise _reject(
                LifecycleReason.RESTART_STATE_INVALID,
                "recovery.intent",
                "a submitted durable lifecycle intent is required",
            )
        expected_correlation = (
            self.correlation
            if phase == "OPEN"
            else self.correlation.with_close_permit(permit_id)
        )
        try:
            payload = json.loads(request_json)
            if not isinstance(payload, Mapping):
                raise ValueError("request JSON is not an object")
            request, expected_qty = parse_broker_order_request_payload(payload)
        except (TypeError, ValueError) as error:
            raise _reject(
                LifecycleReason.RESTART_STATE_INVALID,
                "recovery.intent.request_json",
                f"stored request cannot be restored: {error}",
            ) from None
        if (
            broker_order_request_sha256(request, expected_quantity=expected_qty)
            != request_hash
            or request.permit_id != permit_id
            or request.phase != phase
            or request.event_run_id != self.correlation.event_run_id
            or request.open_permit_id != self.correlation.open_permit_id
            or request.correlation_sha256 != correlation_sha256(expected_correlation)
        ):
            raise _reject(
                LifecycleReason.RESTART_STATE_INVALID,
                "recovery.intent",
                "stored request does not match the durable lifecycle identity",
            )
        return request, expected_qty, order_id

    # -- fresh broker truth --------------------------------------------------

    async def _read_order_or_outage(self, order_id: str) -> BrokerOrderTruth:
        try:
            return await self.broker.read_order(order_id)
        except BrokerOutage as error:
            raise _reject(
                LifecycleReason.BROKER_OUTAGE, f"read_order.{order_id}", str(error)
            ) from error

    async def _read_positions_or_outage(self) -> BrokerPositionSnapshot:
        try:
            return await self.broker.read_positions()
        except BrokerOutage as error:
            raise _reject(LifecycleReason.BROKER_OUTAGE, "read_positions", str(error)) from error

    async def _read_account_or_outage(self) -> AccountTruth:
        try:
            return await self.broker.read_account()
        except BrokerOutage as error:
            raise _reject(LifecycleReason.BROKER_OUTAGE, "read_account", str(error)) from error

    def _require_fresh(self, observed_at: object, path: str, reason: LifecycleReason) -> None:
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            self._reject_truth(path, "broker truth has no timezone-aware observation time")
        age = (self._now() - observed_at).total_seconds()
        if age < 0:
            raise _reject(LifecycleReason.CLOCK_JUMP, path, "broker truth is from the future")
        if age > self.truth_max_age_seconds:
            raise _reject(
                reason, path, f"truth age {age:.0f}s exceeds {self.truth_max_age_seconds}s"
            )

    def _validate_account(self, account: AccountTruth, request: BrokerOrderRequest) -> None:
        self._require_fresh(
            getattr(account, "observed_at", None),
            "account.observed_at",
            LifecycleReason.STALE_ACCOUNT_TRUTH,
        )
        if (
            getattr(account, "account_id", None) != request.account_id
            or getattr(account, "account_class", None) != request.account_class
            or request.account_class != PAPER_ACCOUNT_CLASS
            or request.order_class != MULTI_LEG_ORDER_CLASS
        ):
            self._reject_truth("account", "account/class/order class is missing or mismatched")

    def _validate_order_truth(
        self,
        request: BrokerOrderRequest,
        ack: BrokerOrderAck,
        truth: BrokerOrderTruth,
    ) -> None:
        self._require_fresh(
            getattr(truth, "observed_at", None),
            f"order.{ack.order_id}.observed_at",
            LifecycleReason.STALE_BROKER_TRUTH,
        )
        expected = {
            "order_id": ack.order_id,
            "client_order_id": request.client_order_id,
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
            "legs": request.legs,
        }
        for field_name, expected_value in expected.items():
            if getattr(truth, field_name, None) != expected_value:
                self._reject_truth(
                    f"order.{field_name}", "broker truth does not match submitted order"
                )
        if (
            not isinstance(getattr(truth, "status", None), str)
            or not truth.status
            or isinstance(getattr(truth, "filled_qty", None), bool)
            or not isinstance(getattr(truth, "filled_qty", None), int)
            or truth.filled_qty < 0
            or not isinstance(getattr(truth, "limit_price", None), Decimal)
            or not truth.limit_price.is_finite()
        ):
            self._reject_truth("order", "broker order status, quantity, or price is malformed")
        if request.phase == "OPEN":
            price_in_bounds = Decimal(0) < truth.limit_price <= request.limit_price
        else:
            price_in_bounds = request.limit_price <= truth.limit_price <= Decimal(0)
        if not price_in_bounds:
            self._reject_truth(
                "order.limit_price", "broker order price exceeds the frozen permit bound"
            )

    def _validate_position_truth(
        self,
        request: BrokerOrderRequest,
        ack: BrokerOrderAck,
        snapshot: BrokerPositionSnapshot,
        truth: BrokerOrderTruth,
    ) -> None:
        self._require_fresh(
            getattr(snapshot, "observed_at", None),
            f"positions.{ack.order_id}.observed_at",
            LifecycleReason.STALE_QUOTE,
        )
        expected = {
            "order_id": ack.order_id,
            "client_order_id": request.client_order_id,
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
            "limit_price": request.limit_price,
            "legs": request.legs,
        }
        for field_name, expected_value in expected.items():
            if getattr(snapshot, field_name, None) != expected_value:
                self._reject_truth(
                    f"positions.{field_name}", "position truth is cross-event or mismatched"
                )
        positions = getattr(snapshot, "positions", None)
        if not isinstance(positions, tuple):
            self._reject_truth("positions", "broker position truth is missing")
        for position in positions:
            if (
                not isinstance(getattr(position, "symbol", None), str)
                or not position.symbol
                or not isinstance(getattr(position, "qty", None), Decimal)
                or not position.qty.is_finite()
            ):
                self._reject_truth("positions", "broker position leg is malformed")
            self._require_fresh(
                getattr(position, "observed_at", None),
                f"position.{position.symbol}.observed_at",
                LifecycleReason.STALE_QUOTE,
            )
        if request.phase == "OPEN" and truth.status == BrokerOrderState.FILLED:
            expected_positions = {
                leg.symbol: Decimal(
                    leg.quantity if leg.side == OptionSide.BUY.value else -leg.quantity
                )
                for leg in request.legs
            }
            observed_positions = {position.symbol: position.qty for position in positions}
            if observed_positions != expected_positions:
                self._reject_truth(
                    "positions.legs",
                    "opened position legs do not exactly match the submitted option legs",
                )

    async def _fresh_bound_truth(
        self,
        request: BrokerOrderRequest,
        ack: BrokerOrderAck,
    ) -> tuple[BrokerOrderTruth, BrokerPositionSnapshot]:
        # These readbacks are intentionally unconditional: an acknowledgement's
        # declared status/fill can never skip order or position proof.
        account = await self._read_account_or_outage()
        truth = await self._read_order_or_outage(ack.order_id)
        positions = await self._read_positions_or_outage()
        self._validate_account(account, request)
        self._validate_order_truth(request, ack, truth)
        self._validate_position_truth(request, ack, positions, truth)
        return truth, positions

    # -- opening -------------------------------------------------------------

    async def open(self, open_permit: DebitVerticalPermit) -> tuple[LifecycleState, str]:
        """Submit an opening and persist only fresh, identity-bound broker truth."""

        self._require_clocks_verified()
        self._require_paper_boundary()
        self._require_clock_scope(open_permit)
        self._require_active_window(
            issued_at=open_permit.issued_at,
            expires_at=open_permit.expires_at,
            path="open_permit",
            reason=LifecycleReason.PERMIT_NOT_ACTIVE,
        )
        if self.ledger.lifecycle_intent_for_permit(open_permit.permit_id) is not None:
            raise _reject(
                LifecycleReason.DUPLICATE_TICK,
                f"open.{open_permit.permit_id}",
                "a durable opening intent already exists; restart recovery must not replay it",
            )
        now = self._now()
        if now > self.clocks.entry_deadline_at:
            raise _reject(
                LifecycleReason.ENTRY_DEADLINE_PASSED,
                "clocks.entry_deadline_at",
                "the entry deadline has passed before submission",
            )
        self._require_mutation()
        self._require_open_binding(open_permit)
        if open_permit.permit_id in self._submitted_open_permits:
            raise _reject(
                LifecycleReason.DUPLICATE_TICK,
                f"open.{open_permit.permit_id}",
                "this opening permit was already submitted; a tick cannot duplicate it",
            )
        request = self._request(
            phase="OPEN",
            permit_id=open_permit.permit_id,
            open_permit=open_permit,
            correlation=self.correlation,
            limit_price=open_permit.limit_price,
            legs=self._open_legs(open_permit),
        )
        self._record_intent(request, expected_qty=open_permit.quantity)
        self._submitted_open_permits.add(open_permit.permit_id)
        try:
            ack = await self.broker.submit_open(request)
        except BrokerOutage as error:
            raise _reject(LifecycleReason.BROKER_OUTAGE, "open.submit", str(error)) from error
        self._bind_ack(request, ack)
        try:
            self.ledger.record_submission(
                event_id=request.event_run_id,
                permit_id=request.permit_id,
                broker_order_id=ack.order_id,
                now=self._now(),
                append_passport=False,
            )
        except RiskRejected as error:
            self._reject_truth(
                "open.submission", f"ledger rejected broker order binding: {error.reason.value}"
            )
        truth, _ = await self._fresh_bound_truth(request, ack)
        state = reduce_open_order(truth, expected_qty=open_permit.quantity)
        self._record_open_transition(state, truth)
        return state, ack.order_id

    def _record_open_transition(self, state: LifecycleState, truth: BrokerOrderTruth) -> None:
        now = self._now()
        try:
            if state is LifecycleState.OPEN_FILLED:
                self.ledger.reconcile_observed_order(
                    event_id=self.correlation.event_run_id,
                    permit_id=self.correlation.open_permit_id,
                    broker_order_id=truth.order_id,
                    status="FILLED",
                    filled_quantity=Decimal(truth.filled_qty),
                    observed_at=truth.observed_at,
                    now=now,
                )
                self.ledger.mark_lifecycle_intent_reconciled(
                    permit_id=self.correlation.open_permit_id,
                    now=now,
                )
            elif state is LifecycleState.OPEN_CANCELED:
                self.ledger.reconcile_observed_order(
                    event_id=self.correlation.event_run_id,
                    permit_id=self.correlation.open_permit_id,
                    broker_order_id=truth.order_id,
                    status="CANCELED",
                    filled_quantity=Decimal(0),
                    observed_at=truth.observed_at,
                    now=now,
                )
                self.ledger.mark_lifecycle_intent_reconciled(
                    permit_id=self.correlation.open_permit_id,
                    now=now,
                )
            else:
                self.ledger.record_fill(
                    fill_id=f"open-{truth.order_id}",
                    permit_id=self.correlation.open_permit_id,
                    event_id=self.correlation.event_run_id,
                    quantity=Decimal(truth.filled_qty),
                    status=state.value,
                    observed_at=truth.observed_at,
                )
        except RiskRejected as error:
            self._reject_truth(
                "open.persistence", f"ledger rejected verified broker truth: {error.reason.value}"
            )

    # -- closing -------------------------------------------------------------

    async def close(
        self,
        open_permit: DebitVerticalPermit,
        close_permit: ClosePermit,
        *,
        open_order_id: str,
    ) -> tuple[LifecycleState, str | None]:
        """Submit one exact close and declare flat only from fresh broker proof."""

        self._require_clocks_verified()
        self._require_paper_boundary()
        self._require_clock_scope(open_permit)
        self._require_mutation()
        now = self._now()
        if now > self.clocks.flattening_deadline_at:
            raise _reject(
                LifecycleReason.FLATTENING_DEADLINE_PASSED,
                "clocks.flattening_deadline_at",
                "the flattening deadline has passed; deterministic close is no longer safe",
            )
        close_correlation = self._require_close_binding(open_permit, close_permit, open_order_id)
        if now < self.clocks.time_exit_at:
            raise _reject(
                LifecycleReason.CLOSE_PERMIT_UNAVAILABLE,
                "clocks.time_exit_at",
                "the frozen time-exit clock has not arrived",
            )
        self._require_active_window(
            issued_at=close_permit.issued_at,
            expires_at=close_permit.expires_at,
            path="close_permit",
            reason=LifecycleReason.CLOSE_PERMIT_UNAVAILABLE,
        )
        if (
            self.ledger.lifecycle_intent_for_event_phase(
                self.correlation.event_run_id, "CLOSE"
            )
            is not None
        ):
            raise _reject(
                LifecycleReason.DUPLICATE_TICK,
                f"close.{close_permit.permit_id}",
                "a durable close intent already exists for this lifecycle event",
            )
        if close_permit.permit_id in self._submitted_close_permits:
            raise _reject(
                LifecycleReason.DUPLICATE_TICK,
                f"close.{close_permit.permit_id}",
                "this close permit was already submitted; a tick cannot duplicate it",
            )
        request = self._request(
            phase="CLOSE",
            permit_id=close_permit.permit_id,
            open_permit=open_permit,
            correlation=close_correlation,
            limit_price=close_permit.limit_price,
            legs=self._close_legs(open_permit),
        )
        self._record_intent(request, expected_qty=open_permit.quantity)
        self._submitted_close_permits.add(close_permit.permit_id)
        try:
            ack = await self.broker.submit_close(request)
        except BrokerOutage as error:
            raise _reject(LifecycleReason.BROKER_OUTAGE, "close.submit", str(error)) from error
        self._bind_ack(request, ack)
        truth, positions = await self._fresh_bound_truth(request, ack)
        state = reduce_close_order(truth, positions.positions, expected_qty=open_permit.quantity)
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
        ensure_no_residue(positions.positions)
        try:
            self.ledger.release_consumed_after_flat(
                event_id=self.correlation.event_run_id,
                permit_id=open_permit.permit_id,
                now=self._now(),
            )
            self.ledger.mark_lifecycle_intent_reconciled(
                permit_id=close_permit.permit_id,
                now=self._now(),
            )
        except RiskRejected as error:
            self._reject_truth(
                "close.persistence", f"ledger rejected broker-flat proof: {error.reason.value}"
            )
        return state, ack.order_id

    # -- recovery ------------------------------------------------------------

    async def recover_open_state(self, open_order_id: str, *, expected_qty: int) -> LifecycleState:
        """Resume an opening state from broker truth; never replay intent."""

        intent = self.ledger.lifecycle_intent_for_permit(self.correlation.open_permit_id)
        if intent is None:
            raise _reject(
                LifecycleReason.RESTART_STATE_INVALID,
                "recovery.open_intent",
                "no durable opening intent exists for this lifecycle",
            )
        request, durable_qty, durable_order_id = self._durable_submitted_request(
            intent, phase="OPEN"
        )
        if open_order_id != durable_order_id or expected_qty != durable_qty:
            raise _reject(
                LifecycleReason.RESTART_STATE_INVALID,
                "recovery.open_intent",
                "requested recovery order or quantity differs from the durable intent",
            )
        ack = BrokerOrderAck(
            order_id=durable_order_id,
            client_order_id=request.client_order_id,
            observed_at=self._now(),
        )
        truth, _ = await self._fresh_bound_truth(request, ack)
        return reduce_open_order(truth, expected_qty=durable_qty)

    async def recover_flatness(self) -> LifecycleState:
        """Resume flatness from fresh broker position truth; never replay intent."""

        intent = self.ledger.lifecycle_intent_for_event_phase(
            self.correlation.event_run_id, "CLOSE"
        )
        if intent is None:
            return LifecycleState.MANUAL_REQUIRED
        request, durable_qty, durable_order_id = self._durable_submitted_request(
            intent, phase="CLOSE"
        )
        ack = BrokerOrderAck(
            order_id=durable_order_id,
            client_order_id=request.client_order_id,
            observed_at=self._now(),
        )
        truth, positions = await self._fresh_bound_truth(request, ack)
        state = reduce_close_order(truth, positions.positions, expected_qty=durable_qty)
        return state if state is LifecycleState.CLOSED_FLAT else LifecycleState.MANUAL_REQUIRED
