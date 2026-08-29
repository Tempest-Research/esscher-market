"""Inert-by-default orchestration for one approved PAPER open-to-flat proof."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from .host_mcp import HostMcpEnvironment, PreparedHostMcpSession
from .mcp import (
    ORDER_BY_ID_TOOL,
    BrokerResponseError,
    PaperLifecycleOutcome,
    PaperLifecycleReceipt,
    build_close_order_call,
    build_open_order_call,
)
from .models import ClosePermit, DebitVerticalPermit


class PaperDemoNotApproved(RuntimeError):
    """Raised before tool use when approval does not bind the exact PAPER run."""


class PaperPnlClass(StrEnum):
    """Allowed event-level PAPER P&L conclusions."""

    PAPER_REALIZED_PNL = "PAPER_REALIZED_PNL"
    ZERO_NO_FILL = "ZERO_NO_FILL"
    PAPER_PNL_UNAVAILABLE = "PAPER_PNL_UNAVAILABLE"


PAPER_PNL_DECIMAL_TEXT_MAX_LENGTH = 128
PAPER_PNL_UNAVAILABLE_REASONS = (
    frozenset(
        {
            "broker fees are negative",
            "broker fill prices contradict the registered debit/credit signs",
            "canceled order has a nonzero filled quantity",
            "closing fill predates opening fill",
            "closing order economics are missing",
            "opening and closing fill quantities are not exact",
            "paper P&L decimal text is out of bounds",
            "paper P&L inputs are invalid",
        }
    )
    | frozenset(
        f"{field} {suffix}"
        for field in (
            "broker fees",
            "closing filled average price",
            "closing filled quantity",
            "opening filled average price",
            "opening filled quantity",
        )
        for suffix in ("is invalid", "is missing or invalid", "is not finite")
    )
    | frozenset(
        f"{field} {suffix}"
        for field in ("closing filled_at", "opening filled_at")
        for suffix in ("is invalid", "is missing", "is not timezone-aware")
    )
)


@dataclass(frozen=True, slots=True)
class PaperDemoApproval:
    """Short-lived operator approval bound to one permit and capability proof."""

    permit_id: str
    capability_sha256: str
    environment: HostMcpEnvironment
    approved_at: datetime
    expires_at: datetime

    @classmethod
    def from_json_bytes(cls, value: bytes) -> PaperDemoApproval:
        """Parse one strict operator-authored approval document."""

        try:
            payload = json.loads(value, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise PaperDemoNotApproved("approval document is not strict JSON") from error
        required = {
            "schema",
            "schema_version",
            "permit_id",
            "capability_sha256",
            "environment",
            "approved_at",
            "expires_at",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise PaperDemoNotApproved("approval document fields do not match schema v1")
        if payload["schema"] != "ringdown.paper_demo_approval" or payload["schema_version"] != 1:
            raise PaperDemoNotApproved("approval document schema is unsupported")
        try:
            environment = HostMcpEnvironment(payload["environment"])
            approved_at = _aware_datetime(payload["approved_at"], "approved_at")
            expires_at = _aware_datetime(payload["expires_at"], "expires_at")
        except (TypeError, ValueError) as error:
            raise PaperDemoNotApproved("approval document values are invalid") from error
        permit_id = payload["permit_id"]
        capability_sha256 = payload["capability_sha256"]
        if not isinstance(permit_id, str) or not permit_id:
            raise PaperDemoNotApproved("approval permit_id is invalid")
        if (
            not isinstance(capability_sha256, str)
            or len(capability_sha256) != 64
            or any(character not in "0123456789abcdef" for character in capability_sha256)
        ):
            raise PaperDemoNotApproved("approval capability_sha256 is invalid")
        return cls(
            permit_id=permit_id,
            capability_sha256=capability_sha256,
            environment=environment,
            approved_at=approved_at,
            expires_at=expires_at,
        )


@dataclass(frozen=True, slots=True)
class PaperDemoPlan:
    """Host-built, read-only preflight result consumed by the bounded runner."""

    prepared: PreparedHostMcpSession
    open_permit: DebitVerticalPermit
    close_permit: ClosePermit

    def approval_template_json_bytes(
        self,
        *,
        observed_at: datetime | None = None,
    ) -> bytes:
        """Render exact identities for a human to approve without enabling mutation."""

        _validate_plan_at(
            prepared=self.prepared,
            open_permit=self.open_permit,
            close_permit=self.close_permit,
            observed_at=datetime.now(UTC) if observed_at is None else observed_at,
        )
        return _canonical_json(
            {
                "schema": "ringdown.paper_demo_approval",
                "schema_version": 1,
                "permit_id": self.open_permit.permit_id,
                "capability_sha256": self.prepared.observation.capability_sha256,
                "environment": self.prepared.observation.environment.value,
                "approved_at": "REPLACE_WITH_OPERATOR_APPROVAL_TIME",
                "expires_at": "REPLACE_WITH_SHORT_EXPIRY_TIME",
            }
        )


class FilePaperAttemptStore:
    """Durable submit-once claims stored before any paper order submission."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def claim(self, client_order_id: str) -> bool:
        """Atomically claim a deterministic client ID; false means read back only."""

        self._root.mkdir(parents=True, exist_ok=True)
        if self._root.is_symlink() or not self._root.is_dir():
            raise PaperDemoNotApproved("attempt store must be a real directory")
        identity_sha256 = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()
        marker = self._root / f"{identity_sha256}.attempted"
        try:
            with marker.open("x", encoding="utf-8") as handle:
                handle.write("PAPER_SUBMISSION_ATTEMPTED\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            return False
        return True


@dataclass(frozen=True, slots=True)
class PaperPnlObservation:
    """Exact broker fill economics or a typed unavailable state."""

    classification: PaperPnlClass
    gross_realized_pnl: Decimal | None
    broker_fees: Decimal | None
    net_realized_pnl: Decimal | None
    open_filled_at: datetime | None
    close_filled_at: datetime | None
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PaperReceiptBundle:
    """Sanitized terminal bundle with no raw broker or account identifiers."""

    event_run_id: str
    open_permit_id: str
    close_permit_id: str
    capability_sha256: str
    open_request_sha256: str
    close_request_sha256: str | None
    open_order_sha256: str
    close_order_sha256: str | None
    lifecycle_outcome: str
    final_flat_observed_at: datetime
    pnl: PaperPnlObservation

    def _payload(self) -> dict[str, object]:
        pnl = {
            "classification": self.pnl.classification.value,
            "gross_realized_pnl": _decimal_text(self.pnl.gross_realized_pnl),
            "broker_fees": _decimal_text(self.pnl.broker_fees),
            "net_realized_pnl": _decimal_text(self.pnl.net_realized_pnl),
            "open_filled_at": _datetime_text(self.pnl.open_filled_at),
            "close_filled_at": _datetime_text(self.pnl.close_filled_at),
            "unavailable_reason": self.pnl.unavailable_reason,
        }
        return {
            "schema": "ringdown.paper_receipt_bundle",
            "schema_version": 1,
            "run_mode": "PAPER",
            "data_class": "INDICATIVE_DATA",
            "claims": ["PAPER_OPERATIONAL_OBSERVATION", "NOT_ALPHA_EVIDENCE"],
            "event_run_id": self.event_run_id,
            "open_permit_id": self.open_permit_id,
            "close_permit_id": self.close_permit_id,
            "capability_sha256": self.capability_sha256,
            "open_request_sha256": self.open_request_sha256,
            "close_request_sha256": self.close_request_sha256,
            "open_order_sha256": self.open_order_sha256,
            "close_order_sha256": self.close_order_sha256,
            "lifecycle_outcome": self.lifecycle_outcome,
            "final_flat_observed_at": self.final_flat_observed_at.isoformat(),
            "paper_pnl": pnl,
        }

    @property
    def receipt_sha256(self) -> str:
        """Hash the exact sanitized lifecycle payload, excluding the hash itself."""

        return hashlib.sha256(_canonical_json(self._payload())).hexdigest()

    def to_json_bytes(self) -> bytes:
        """Serialize deterministic sanitized JSON with its self-verifiable hash."""

        payload = self._payload()
        payload["receipt_sha256"] = self.receipt_sha256
        return _canonical_json(payload)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    _, digits, exponent = normalized.as_tuple()
    if len(digits) > PAPER_PNL_DECIMAL_TEXT_MAX_LENGTH or abs(exponent) > (
        PAPER_PNL_DECIMAL_TEXT_MAX_LENGTH
    ):
        raise ValueError("paper P&L decimal text is out of bounds")
    rendered = format(normalized, "f")
    if len(rendered) > PAPER_PNL_DECIMAL_TEXT_MAX_LENGTH:
        raise ValueError("paper P&L decimal text is out of bounds")
    return rendered


def _datetime_text(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _order_sha256(order_id: str) -> str:
    return hashlib.sha256(order_id.encode("utf-8")).hexdigest()


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} is not timezone-aware")
    return parsed


def _finite_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{field} is missing or invalid")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{field} is invalid") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} is not finite")
    return parsed


def _validate_order_identity(
    payload: object,
    *,
    order_id: str,
    client_order_id: str,
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("order readback is not an object")
    if payload.get("id") != order_id or payload.get("client_order_id") != client_order_id:
        raise ValueError("order identity does not match the terminal lifecycle")
    return payload


def _broker_fees(*orders: Mapping[str, object]) -> Decimal | None:
    values: list[Decimal] = []
    for order in orders:
        if "fees" not in order:
            return None
        fee = _finite_decimal(order["fees"], "broker fees")
        if fee < 0:
            raise ValueError("broker fees are negative")
        values.append(fee)
    return sum(values, Decimal("0"))


def _unavailable(reason: str) -> PaperPnlObservation:
    if reason not in PAPER_PNL_UNAVAILABLE_REASONS:
        reason = "paper P&L inputs are invalid"
    return PaperPnlObservation(
        classification=PaperPnlClass.PAPER_PNL_UNAVAILABLE,
        gross_realized_pnl=None,
        broker_fees=None,
        net_realized_pnl=None,
        open_filled_at=None,
        close_filled_at=None,
        unavailable_reason=reason,
    )


def _classify_pnl(
    lifecycle: PaperLifecycleReceipt,
    *,
    open_order: Mapping[str, object],
    close_order: Mapping[str, object] | None,
    quantity: int,
) -> PaperPnlObservation:
    try:
        open_qty = _finite_decimal(open_order.get("filled_qty"), "opening filled quantity")
        if lifecycle.outcome is PaperLifecycleOutcome.CANCELED_FLAT:
            if open_qty != 0:
                raise ValueError("canceled order has a nonzero filled quantity")
            fees = _broker_fees(open_order)
            _decimal_text(Decimal("0"))
            _decimal_text(fees)
            return PaperPnlObservation(
                classification=PaperPnlClass.ZERO_NO_FILL,
                gross_realized_pnl=Decimal("0"),
                broker_fees=fees,
                net_realized_pnl=None,
                open_filled_at=None,
                close_filled_at=None,
            )

        if close_order is None:
            raise ValueError("closing order economics are missing")
        close_qty = _finite_decimal(close_order.get("filled_qty"), "closing filled quantity")
        expected_qty = Decimal(quantity)
        if open_qty != expected_qty or close_qty != expected_qty:
            raise ValueError("opening and closing fill quantities are not exact")
        open_price = _finite_decimal(
            open_order.get("filled_avg_price"),
            "opening filled average price",
        )
        close_price = _finite_decimal(
            close_order.get("filled_avg_price"),
            "closing filled average price",
        )
        if open_price <= 0 or close_price >= 0:
            raise ValueError("broker fill prices contradict the registered debit/credit signs")
        open_filled_at = _aware_datetime(open_order.get("filled_at"), "opening filled_at")
        close_filled_at = _aware_datetime(close_order.get("filled_at"), "closing filled_at")
        if close_filled_at < open_filled_at:
            raise ValueError("closing fill predates opening fill")
        gross = ((-close_price) - open_price) * Decimal(100) * expected_qty
        fees = _broker_fees(open_order, close_order)
        net = None if fees is None else gross - fees
        _decimal_text(gross)
        _decimal_text(fees)
        _decimal_text(net)
        return PaperPnlObservation(
            classification=PaperPnlClass.PAPER_REALIZED_PNL,
            gross_realized_pnl=gross,
            broker_fees=fees,
            net_realized_pnl=net,
            open_filled_at=open_filled_at,
            close_filled_at=close_filled_at,
        )
    except (ValueError, InvalidOperation) as error:
        return _unavailable(str(error))


def _validate_approval(
    *,
    prepared: PreparedHostMcpSession,
    permit: DebitVerticalPermit,
    approval: PaperDemoApproval,
    observed_at: datetime,
) -> None:
    observation = prepared.observation
    if approval.permit_id != permit.permit_id:
        raise PaperDemoNotApproved("approval does not bind the exact opening permit")
    if approval.capability_sha256 != observation.capability_sha256:
        raise PaperDemoNotApproved("approval does not bind the current capability proof")
    if (
        approval.environment is not HostMcpEnvironment.PAPER
        or observation.environment is not HostMcpEnvironment.PAPER
    ):
        raise PaperDemoNotApproved("approval and host observation must both prove PAPER mode")
    if approval.approved_at.tzinfo is None or approval.approved_at.utcoffset() is None:
        raise PaperDemoNotApproved("approval timestamp must be timezone-aware")
    if approval.expires_at.tzinfo is None or approval.expires_at.utcoffset() is None:
        raise PaperDemoNotApproved("approval expiry must be timezone-aware")
    if observed_at < approval.approved_at or observed_at >= approval.expires_at:
        raise PaperDemoNotApproved("approval is not active")


def _validate_plan_at(
    *,
    prepared: PreparedHostMcpSession,
    open_permit: DebitVerticalPermit,
    close_permit: ClosePermit,
    observed_at: datetime,
) -> None:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise PaperDemoNotApproved("runner clock must be timezone-aware")
    if prepared.observation.environment is not HostMcpEnvironment.PAPER:
        raise PaperDemoNotApproved("host observation must prove PAPER mode")
    if not (open_permit.issued_at <= observed_at <= open_permit.expires_at):
        raise PaperDemoNotApproved("opening permit is not active at execution time")
    if not (close_permit.issued_at <= observed_at <= close_permit.expires_at):
        raise PaperDemoNotApproved("closing permit is not active at execution time")
    build_open_order_call(open_permit)
    build_close_order_call(open_permit, close_permit)


async def run_paper_demo(
    *,
    prepared: PreparedHostMcpSession,
    open_permit: DebitVerticalPermit,
    close_permit: ClosePermit,
    approval: PaperDemoApproval,
    attempt_store: FilePaperAttemptStore,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PaperReceiptBundle:
    """Run exactly one approved PAPER lifecycle or recover its deterministic attempts."""

    observed_at = clock()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise PaperDemoNotApproved("runner clock must be timezone-aware")
    _validate_approval(
        prepared=prepared,
        permit=open_permit,
        approval=approval,
        observed_at=observed_at,
    )

    if not (open_permit.issued_at <= observed_at <= open_permit.expires_at):
        raise PaperDemoNotApproved("opening permit is not active at execution time")
    if not (close_permit.issued_at <= observed_at <= close_permit.expires_at):
        raise PaperDemoNotApproved("closing permit is not active at execution time")
    open_call = build_open_order_call(open_permit)
    close_call = build_close_order_call(open_permit, close_permit)

    broker = prepared.broker(clock=clock)
    if attempt_store.claim(open_call.client_order_id):
        open_receipt = await broker.submit_open(open_permit)
    else:
        open_receipt = await broker.read_open(open_permit)

    lifecycle = await broker.resolve_to_flat(
        open_permit=open_permit,
        open_receipt=open_receipt,
        close_permit=close_permit,
        claim_close_submission=attempt_store.claim,
        claim_cancel_mutation=lambda order_id: attempt_store.claim(f"cancel:{order_id}"),
    )

    open_raw = await prepared.session.call_tool(
        ORDER_BY_ID_TOOL,
        {"order_id": lifecycle.open_order_id},
    )
    try:
        open_order = _validate_order_identity(
            open_raw,
            order_id=lifecycle.open_order_id,
            client_order_id=open_call.client_order_id,
        )
    except ValueError as error:
        raise BrokerResponseError(
            "terminal opening-order economics did not match lifecycle identity"
        ) from error

    close_order: Mapping[str, object] | None = None
    if lifecycle.close_order_id is not None:
        close_raw = await prepared.session.call_tool(
            ORDER_BY_ID_TOOL,
            {"order_id": lifecycle.close_order_id},
        )
        try:
            close_order = _validate_order_identity(
                close_raw,
                order_id=lifecycle.close_order_id,
                client_order_id=close_call.client_order_id,
            )
        except ValueError as error:
            raise BrokerResponseError(
                "terminal closing-order economics did not match lifecycle identity"
            ) from error

    pnl = _classify_pnl(
        lifecycle,
        open_order=open_order,
        close_order=close_order,
        quantity=open_permit.quantity,
    )
    return PaperReceiptBundle(
        event_run_id=lifecycle.event_run_id,
        open_permit_id=lifecycle.open_permit_id,
        close_permit_id=lifecycle.close_permit_id,
        capability_sha256=approval.capability_sha256,
        open_request_sha256=open_call.request_sha256,
        close_request_sha256=lifecycle.close_request_sha256,
        open_order_sha256=_order_sha256(lifecycle.open_order_id),
        close_order_sha256=(
            None if lifecycle.close_order_id is None else _order_sha256(lifecycle.close_order_id)
        ),
        lifecycle_outcome=lifecycle.outcome.value,
        final_flat_observed_at=lifecycle.observed_at,
        pnl=pnl,
    )
