"""Single-door Alpaca MCP boundary for governed paper option lifecycles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol

from ringdown_market.contracts.execution_policy import (
    ALPACA_MCP_COMMIT,
    ALPACA_MCP_PROTOCOL_SHA256,
    ALPACA_MCP_VERSION,
    CANCEL_TOOL,
    OPEN_TOOL,
    ORDER_BY_ID_TOOL,
    PAPER_PERMIT_POLICY_SHA256,
    POSITIONS_TOOL,
    READBACK_TOOL,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    paper_event_run_id,
)

from .models import (
    ClosePermit,
    DataClass,
    DebitVerticalPermit,
    OptionSide,
    PositionIntent,
    RunMode,
    debit_vertical_permit_id,
)


class PermitNotExecutable(RuntimeError):
    """Raised before mutation when an entry permit is not currently valid."""


class BrokerResponseError(RuntimeError):
    """Raised when broker acknowledgement or readback is missing or ambiguous."""


class PaperLifecycleManualRequired(RuntimeError):
    """Raised when atomic automation cannot safely resolve paper exposure."""


class PaperLifecycleNotFlat(RuntimeError):
    """Raised when broker position truth still contains an event leg."""


class McpToolSession(Protocol):
    """Normalized subset of an MCP session consumed by the broker gateway."""

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object: ...


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class OpenOrderCall:
    """Exact official Alpaca MCP call derived from one entry permit."""

    client_order_id: str
    request_sha256: str
    _arguments: tuple[tuple[str, object], ...]

    @property
    def tool(self) -> str:
        return OPEN_TOOL

    @property
    def arguments(self) -> dict[str, object]:
        return deepcopy(dict(self._arguments))


def build_lifecycle_order_call(
    *,
    client_order_id: str,
    limit_price: Decimal,
    legs: tuple[object, ...],
) -> OpenOrderCall:
    """Compile a durable lifecycle request through the pinned MCP wire schema.

    This helper deliberately receives no permit.  The monitored lifecycle has
    already persisted and bound the ``BrokerOrderRequest``; this boundary only
    translates its exact immutable legs into the same official multi-leg MCP
    shape used by the legacy bridge.
    """

    if not isinstance(client_order_id, str) or not client_order_id.strip():
        raise ValueError("client_order_id must be non-empty exact text")
    if not isinstance(limit_price, Decimal) or not limit_price.is_finite():
        raise ValueError("limit_price must be a finite Decimal")
    if not legs:
        raise ValueError("lifecycle order requires at least one leg")
    encoded_legs: list[dict[str, str]] = []
    quantities: set[int] = set()
    for index, leg in enumerate(legs):
        symbol = getattr(leg, "symbol", None)
        quantity = getattr(leg, "quantity", None)
        side = getattr(leg, "side", None)
        position_intent = getattr(leg, "position_intent", None)
        if (
            not isinstance(symbol, str)
            or not symbol.strip()
            or isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity <= 0
            or not isinstance(side, str)
            or not side.strip()
            or not isinstance(position_intent, str)
            or not position_intent.strip()
        ):
            raise ValueError(f"lifecycle order leg {index} is malformed")
        quantities.add(quantity)
        encoded_legs.append(
            {
                "symbol": symbol,
                "ratio_qty": str(quantity),
                "side": side,
                "position_intent": position_intent,
            }
        )
    if len(quantities) != 1:
        raise ValueError("lifecycle multi-leg quantities must be equal")
    arguments: dict[str, object] = {
        "qty": str(quantities.pop()),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": _decimal_text(limit_price),
        "client_order_id": client_order_id,
        "order_class": "mleg",
        "legs": encoded_legs,
    }
    request_sha256 = _sha256(
        {
            "adapter": "ALPACA_MCP",
            "adapter_version": ALPACA_MCP_VERSION,
            "adapter_commit": ALPACA_MCP_COMMIT,
            "tool": OPEN_TOOL,
            "arguments": arguments,
        }
    )
    return OpenOrderCall(
        client_order_id=client_order_id,
        request_sha256=request_sha256,
        _arguments=tuple(arguments.items()),
    )


@dataclass(frozen=True, slots=True)
class OpenOrderReceipt:
    """Sanitized identity receipt after submit and broker readback agree."""

    broker_order_id: str
    client_order_id: str
    broker_status: str
    request_sha256: str
    permit_id: str
    event_run_id: str
    observed_at: datetime
    run_mode: RunMode
    data_class: DataClass
    adapter: str = "ALPACA_MCP"
    adapter_version: str = ALPACA_MCP_VERSION
    adapter_commit: str = ALPACA_MCP_COMMIT


class PaperLifecycleOutcome(StrEnum):
    CANCELED_FLAT = "CANCELED_FLAT"
    CLOSED_FLAT = "CLOSED_FLAT"


@dataclass(frozen=True, slots=True)
class PaperLifecycleReceipt:
    """Sanitized terminal receipt backed by broker position truth."""

    event_run_id: str
    open_permit_id: str
    close_permit_id: str
    open_order_id: str
    open_order_status: str
    close_order_id: str | None
    close_order_status: str | None
    close_request_sha256: str | None
    target_symbols: tuple[str, str]
    outcome: PaperLifecycleOutcome
    observed_at: datetime
    run_mode: RunMode = RunMode.PAPER
    data_class: DataClass = DataClass.INDICATIVE_DATA

    def __post_init__(self) -> None:
        required = (
            self.event_run_id,
            self.open_permit_id,
            self.close_permit_id,
            self.open_order_id,
            self.open_order_status,
        )
        if not all(value.strip() for value in required):
            raise ValueError("paper lifecycle receipt identities must be non-empty")
        if len(set(self.target_symbols)) != 2:
            raise ValueError("target_symbols must contain two distinct symbols")
        if self.outcome is PaperLifecycleOutcome.CLOSED_FLAT:
            if not self.close_order_id or not self.close_order_status:
                raise ValueError("CLOSED_FLAT requires close-order identity")
            if self.close_request_sha256 is None:
                raise ValueError("CLOSED_FLAT requires close_request_sha256")
        elif any(
            value is not None
            for value in (
                self.close_order_id,
                self.close_order_status,
                self.close_request_sha256,
            )
        ):
            raise ValueError("CANCELED_FLAT cannot claim a close order")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.run_mode is not RunMode.PAPER:
            raise ValueError("paper lifecycle receipts must remain PAPER")
        if self.data_class is not DataClass.INDICATIVE_DATA:
            raise ValueError("paper lifecycle receipts must remain INDICATIVE_DATA")


def _validate_registered_open_permit(permit: DebitVerticalPermit) -> None:
    expected_id = debit_vertical_permit_id(permit)
    if (
        permit.protocol_sha256 != RESEARCH_DECISION_PROTOCOL_SHA256
        or permit.execution_protocol_sha256 != ALPACA_MCP_PROTOCOL_SHA256
        or permit.policy_sha256 != PAPER_PERMIT_POLICY_SHA256
        or permit.permit_id != expected_id
        or permit.event_run_id != paper_event_run_id(permit.decision_sha256)
    ):
        raise ValueError("opening permit is not a registered frozen decision permit")


def _permit_identity(permit: DebitVerticalPermit) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "OPEN",
        "permit_id": permit.permit_id,
        "event_run_id": permit.event_run_id,
        "decision_sha256": permit.decision_sha256,
        "evidence_sha256": permit.evidence_sha256,
        "protocol_sha256": permit.protocol_sha256,
        "execution_protocol_sha256": permit.execution_protocol_sha256,
        "policy_sha256": permit.policy_sha256,
        "snapshot_sha256": permit.snapshot_sha256,
        "issued_at": permit.issued_at.isoformat(),
        "expires_at": permit.expires_at.isoformat(),
        "vertical_type": permit.vertical_type.value,
        "quantity": permit.quantity,
        "limit_price": _decimal_text(permit.limit_price),
        "run_mode": permit.run_mode.value,
        "data_class": permit.data_class.value,
        "legs": [
            {
                "symbol": leg.symbol,
                "underlying": leg.underlying,
                "expiry": leg.expiry.isoformat(),
                "option_type": leg.option_type.value,
                "strike": _decimal_text(leg.strike),
                "side": leg.side.value,
                "position_intent": leg.position_intent.value,
                "ratio_qty": leg.ratio_qty,
            }
            for leg in permit.legs
        ],
    }


def build_open_order_call(permit: DebitVerticalPermit) -> OpenOrderCall:
    """Compile a permit into Alpaca MCP's pinned multi-leg option schema."""

    _validate_registered_open_permit(permit)
    permit_sha256 = _sha256(_permit_identity(permit))
    client_order_id = f"rd-open-{permit_sha256[:32]}"
    arguments: dict[str, object] = {
        "qty": str(permit.quantity),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": _decimal_text(permit.limit_price),
        "client_order_id": client_order_id,
        "order_class": "mleg",
        "legs": [
            {
                "symbol": leg.symbol,
                "ratio_qty": str(leg.ratio_qty),
                "side": leg.side.value,
                "position_intent": leg.position_intent.value,
            }
            for leg in permit.legs
        ],
    }
    request_sha256 = _sha256(
        {
            "adapter": "ALPACA_MCP",
            "adapter_version": ALPACA_MCP_VERSION,
            "adapter_commit": ALPACA_MCP_COMMIT,
            "tool": OPEN_TOOL,
            "arguments": arguments,
        }
    )
    return OpenOrderCall(
        client_order_id=client_order_id,
        request_sha256=request_sha256,
        _arguments=tuple(arguments.items()),
    )


def build_close_order_call(
    open_permit: DebitVerticalPermit,
    close_permit: ClosePermit,
) -> OpenOrderCall:
    """Compile one atomic reversed multi-leg close through the same MCP tool."""

    if close_permit.policy_sha256 != PAPER_PERMIT_POLICY_SHA256:
        raise ValueError("close permit does not match the registered PAPER policy")
    if close_permit.open_permit_id != open_permit.permit_id:
        raise ValueError("close permit does not reference the opening permit")
    if close_permit.event_run_id != open_permit.event_run_id:
        raise ValueError("close and opening permits must share one event_run_id")
    if close_permit.snapshot_sha256 != open_permit.snapshot_sha256:
        raise ValueError("close and opening permits must share one snapshot")
    if close_permit.run_mode is not RunMode.PAPER or open_permit.run_mode is not RunMode.PAPER:
        raise ValueError("option lifecycle must remain PAPER")
    if (
        close_permit.data_class is not DataClass.INDICATIVE_DATA
        or open_permit.data_class is not DataClass.INDICATIVE_DATA
    ):
        raise ValueError("option lifecycle must remain INDICATIVE_DATA")

    width = abs(open_permit.legs[1].strike - open_permit.legs[0].strike)
    if abs(close_permit.limit_price) > width:
        raise ValueError("close credit cannot exceed the vertical width")

    close_legs = []
    for leg in open_permit.legs:
        if leg.side is OptionSide.BUY:
            side = OptionSide.SELL
            intent = PositionIntent.SELL_TO_CLOSE
        else:
            side = OptionSide.BUY
            intent = PositionIntent.BUY_TO_CLOSE
        close_legs.append(
            {
                "symbol": leg.symbol,
                "ratio_qty": str(leg.ratio_qty),
                "side": side.value,
                "position_intent": intent.value,
            }
        )

    close_identity = {
        "schema_version": 1,
        "operation": "CLOSE",
        "permit_id": close_permit.permit_id,
        "open_permit_id": close_permit.open_permit_id,
        "event_run_id": close_permit.event_run_id,
        "policy_sha256": close_permit.policy_sha256,
        "snapshot_sha256": close_permit.snapshot_sha256,
        "issued_at": close_permit.issued_at.isoformat(),
        "expires_at": close_permit.expires_at.isoformat(),
        "limit_price": _decimal_text(close_permit.limit_price),
        "run_mode": close_permit.run_mode.value,
        "data_class": close_permit.data_class.value,
        "quantity": open_permit.quantity,
        "legs": close_legs,
    }
    close_sha256 = _sha256(close_identity)
    client_order_id = f"rd-close-{close_sha256[:32]}"
    arguments: dict[str, object] = {
        "qty": str(open_permit.quantity),
        "type": "limit",
        "time_in_force": "day",
        "limit_price": _decimal_text(close_permit.limit_price),
        "client_order_id": client_order_id,
        "order_class": "mleg",
        "legs": close_legs,
    }
    request_sha256 = _sha256(
        {
            "adapter": "ALPACA_MCP",
            "adapter_version": ALPACA_MCP_VERSION,
            "adapter_commit": ALPACA_MCP_COMMIT,
            "tool": OPEN_TOOL,
            "arguments": arguments,
        }
    )
    return OpenOrderCall(
        client_order_id=client_order_id,
        request_sha256=request_sha256,
        _arguments=tuple(arguments.items()),
    )


def _mapping_response(response: object, *, phase: str) -> Mapping[str, object]:
    if not isinstance(response, Mapping):
        raise BrokerResponseError(f"Alpaca MCP {phase} returned a non-object response")
    if "error" in response:
        raise BrokerResponseError(f"Alpaca MCP {phase} rejected: {response['error']}")
    return response


def _broker_identity(response: object, *, phase: str) -> tuple[str, str, str, Decimal | None]:
    payload = _mapping_response(response, phase=phase)
    order_id = payload.get("id")
    client_order_id = payload.get("client_order_id")
    status = payload.get("status")
    if not all(isinstance(value, str) and value for value in (order_id, client_order_id, status)):
        raise BrokerResponseError(f"Alpaca MCP {phase} omitted required order identity")

    filled_qty: Decimal | None = None
    if "filled_qty" in payload:
        raw_filled_qty = payload["filled_qty"]
        if isinstance(raw_filled_qty, bool) or not isinstance(
            raw_filled_qty, (str, int, float, Decimal)
        ):
            raise PaperLifecycleManualRequired(
                f"Alpaca MCP {phase} returned an invalid filled quantity"
            )
        try:
            filled_qty = Decimal(str(raw_filled_qty))
        except InvalidOperation as error:
            raise PaperLifecycleManualRequired(
                f"Alpaca MCP {phase} returned an invalid filled quantity"
            ) from error
        if not filled_qty.is_finite() or filled_qty < 0:
            raise PaperLifecycleManualRequired(
                f"Alpaca MCP {phase} returned an invalid filled quantity"
            )

    return order_id, client_order_id, status, filled_qty


def _open_position_symbols(response: object) -> set[str]:
    if isinstance(response, Mapping):
        _mapping_response(response, phase="position readback")
        raise BrokerResponseError(
            "Alpaca MCP position readback returned an object instead of a list"
        )
    if not isinstance(response, list):
        raise BrokerResponseError("Alpaca MCP position readback returned an invalid response")

    symbols: set[str] = set()
    for position in response:
        if not isinstance(position, Mapping):
            raise BrokerResponseError("Alpaca MCP position readback contained an invalid position")
        symbol = position.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise BrokerResponseError("Alpaca MCP position readback omitted a position symbol")
        symbols.add(symbol)
    return symbols


class McpPaperBroker:
    """Drive one bounded paper lifecycle through a single Alpaca MCP door."""

    _CANCELLABLE_STATUSES = frozenset(
        {"new", "accepted", "pending_new", "accepted_for_bidding", "held", "calculated"}
    )
    _UNFILLED_TERMINAL_STATUSES = frozenset({"canceled", "expired", "rejected"})

    def __init__(
        self,
        session: McpToolSession,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._clock = clock
        self._consumed_open_permit_ids: set[str] = set()

    def _observed_time(self) -> datetime:
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PermitNotExecutable("broker clock must be timezone-aware")
        return observed_at

    def _active_time(self, *, issued_at: datetime, expires_at: datetime, label: str) -> datetime:
        observed_at = self._observed_time()
        if observed_at < issued_at:
            raise PermitNotExecutable(f"{label} permit is not active yet")
        if observed_at >= expires_at:
            raise PermitNotExecutable(f"{label} permit expired before submission")
        return observed_at

    async def _submit_and_readback(
        self,
        call: OpenOrderCall,
        *,
        submission_phase: str,
        readback_phase: str,
    ) -> tuple[str, str, str, Decimal | None]:
        submission_error: Exception | None = None
        submitted_id: str | None = None
        try:
            submitted = await self._session.call_tool(call.tool, call.arguments)
        except Exception as error:
            submission_error = error
        else:
            submitted_id, submitted_client_id, _, _ = _broker_identity(
                submitted, phase=submission_phase
            )
            if submitted_client_id != call.client_order_id:
                raise BrokerResponseError(
                    f"{submission_phase} client order identity did not match request"
                )

        try:
            readback = await self._session.call_tool(
                READBACK_TOOL,
                {"client_order_id": call.client_order_id},
            )
            readback_id, readback_client_id, readback_status, readback_filled_qty = (
                _broker_identity(readback, phase=readback_phase)
            )
        except Exception as error:
            if submission_error is not None:
                raise BrokerResponseError(
                    f"{submission_phase} outcome was ambiguous and readback failed; "
                    "manual reconciliation required"
                ) from error
            raise

        if readback_client_id != call.client_order_id or (
            submitted_id is not None and readback_id != submitted_id
        ):
            raise BrokerResponseError(
                "broker order identity differed between submission and readback; "
                "manual reconciliation required"
            )
        return readback_id, readback_client_id, readback_status, readback_filled_qty

    async def _read_order(
        self, order_id: str, client_order_id: str
    ) -> tuple[str, str, str, Decimal | None]:
        response = await self._session.call_tool(ORDER_BY_ID_TOOL, {"order_id": order_id})
        read_id, read_client_id, status, filled_qty = _broker_identity(
            response, phase="order readback"
        )
        if read_id != order_id or read_client_id != client_order_id:
            raise BrokerResponseError(
                "order readback identity did not match the expected paper order"
            )
        return read_id, read_client_id, status.lower(), filled_qty

    async def _verify_event_flat(self, target_symbols: tuple[str, str]) -> datetime:
        response = await self._session.call_tool(POSITIONS_TOOL, {})
        open_symbols = _open_position_symbols(response)
        remaining = sorted(set(target_symbols) & open_symbols)
        if remaining:
            raise PaperLifecycleNotFlat(
                "broker position truth still contains event legs: " + ", ".join(remaining)
            )
        return self._observed_time()

    async def submit_open(self, permit: DebitVerticalPermit) -> OpenOrderReceipt:
        """Submit one permit and return only after exact readback agreement."""

        self._active_time(
            issued_at=permit.issued_at,
            expires_at=permit.expires_at,
            label="entry",
        )
        call = build_open_order_call(permit)
        if permit.permit_id in self._consumed_open_permit_ids:
            raise PermitNotExecutable("entry permit was already consumed by this broker session")
        self._consumed_open_permit_ids.add(permit.permit_id)
        readback_id, readback_client_id, readback_status, _ = await self._submit_and_readback(
            call,
            submission_phase="submission",
            readback_phase="readback",
        )
        observed_at = self._observed_time()

        return OpenOrderReceipt(
            broker_order_id=readback_id,
            client_order_id=readback_client_id,
            broker_status=readback_status,
            request_sha256=call.request_sha256,
            permit_id=permit.permit_id,
            event_run_id=permit.event_run_id,
            observed_at=observed_at,
            run_mode=permit.run_mode,
            data_class=permit.data_class,
        )

    async def read_open(self, permit: DebitVerticalPermit) -> OpenOrderReceipt:
        """Recover an already-attempted opening order without another mutation."""

        call = build_open_order_call(permit)
        response = await self._session.call_tool(
            READBACK_TOOL,
            {"client_order_id": call.client_order_id},
        )
        order_id, client_order_id, status, _ = _broker_identity(
            response,
            phase="opening recovery readback",
        )
        if client_order_id != call.client_order_id:
            raise BrokerResponseError(
                "opening recovery readback did not match deterministic client order identity"
            )
        return OpenOrderReceipt(
            broker_order_id=order_id,
            client_order_id=client_order_id,
            broker_status=status,
            request_sha256=call.request_sha256,
            permit_id=permit.permit_id,
            event_run_id=permit.event_run_id,
            observed_at=self._observed_time(),
            run_mode=permit.run_mode,
            data_class=permit.data_class,
        )

    async def resolve_to_flat(
        self,
        *,
        open_permit: DebitVerticalPermit,
        open_receipt: OpenOrderReceipt,
        close_permit: ClosePermit,
        claim_close_submission: Callable[[str], bool] | None = None,
        claim_cancel_mutation: Callable[[str], bool] | None = None,
    ) -> PaperLifecycleReceipt:
        """Cancel an unfilled order or atomically close a fill, then prove event-flat."""

        self._active_time(
            issued_at=close_permit.issued_at,
            expires_at=close_permit.expires_at,
            label="close",
        )
        open_call = build_open_order_call(open_permit)
        if (
            open_receipt.permit_id != open_permit.permit_id
            or open_receipt.event_run_id != open_permit.event_run_id
            or open_receipt.client_order_id != open_call.client_order_id
            or open_receipt.request_sha256 != open_call.request_sha256
        ):
            raise PermitNotExecutable("open receipt does not match the opening permit")
        if (
            open_receipt.run_mode is not RunMode.PAPER
            or open_receipt.data_class is not DataClass.INDICATIVE_DATA
        ):
            raise PermitNotExecutable("open receipt is outside the paper competition boundary")
        if (
            open_receipt.adapter != "ALPACA_MCP"
            or open_receipt.adapter_version != ALPACA_MCP_VERSION
            or open_receipt.adapter_commit != ALPACA_MCP_COMMIT
        ):
            raise PermitNotExecutable("open receipt does not match the pinned MCP adapter")

        close_call = build_close_order_call(open_permit, close_permit)
        _, _, open_status, open_filled_qty = await self._read_order(
            open_receipt.broker_order_id,
            open_receipt.client_order_id,
        )

        if open_status == "partially_filled":
            raise PaperLifecycleManualRequired(
                "partial fill cannot be repaired by sequential option legging"
            )

        if open_status in self._CANCELLABLE_STATUSES:
            cancel_error: Exception | None = None
            cancel_claimed = (
                claim_cancel_mutation(open_receipt.broker_order_id)
                if claim_cancel_mutation is not None
                else True
            )
            if cancel_claimed:
                try:
                    cancel_response = await self._session.call_tool(
                        CANCEL_TOOL,
                        {"order_id": open_receipt.broker_order_id},
                    )
                    if isinstance(cancel_response, Mapping) and "error" in cancel_response:
                        _mapping_response(cancel_response, phase="cancel")
                except Exception as error:
                    cancel_error = error

            try:
                _, _, open_status, open_filled_qty = await self._read_order(
                    open_receipt.broker_order_id,
                    open_receipt.client_order_id,
                )
            except Exception as error:
                if cancel_error is not None:
                    raise PaperLifecycleManualRequired(
                        "cancel outcome was ambiguous and order readback failed"
                    ) from error
                raise

        if open_status == "partially_filled":
            raise PaperLifecycleManualRequired(
                "partial fill cannot be repaired by sequential option legging"
            )

        target_symbols = tuple(leg.symbol for leg in open_permit.legs)
        if open_status in self._UNFILLED_TERMINAL_STATUSES:
            if open_filled_qty is None:
                raise PaperLifecycleManualRequired(
                    "terminal paper order omitted the filled quantity required for reconciliation"
                )
            if open_filled_qty != 0:
                raise PaperLifecycleManualRequired(
                    "terminal paper order has a nonzero fill and requires manual reconciliation"
                )
            flat_observed_at = await self._verify_event_flat(target_symbols)
            return PaperLifecycleReceipt(
                event_run_id=open_permit.event_run_id,
                open_permit_id=open_permit.permit_id,
                close_permit_id=close_permit.permit_id,
                open_order_id=open_receipt.broker_order_id,
                open_order_status=open_status,
                close_order_id=None,
                close_order_status=None,
                close_request_sha256=None,
                target_symbols=target_symbols,
                outcome=PaperLifecycleOutcome.CANCELED_FLAT,
                observed_at=flat_observed_at,
            )

        if open_status != "filled":
            raise PaperLifecycleManualRequired(
                f"unsupported paper order state requires reconciliation: {open_status}"
            )
        if open_filled_qty != Decimal(open_permit.quantity):
            raise PaperLifecycleManualRequired(
                "filled opening order quantity does not match the permitted package quantity"
            )

        close_claimed = (
            claim_close_submission(close_call.client_order_id)
            if claim_close_submission is not None
            else True
        )
        if close_claimed:
            close_order_id, _, close_status, close_filled_qty = await self._submit_and_readback(
                close_call,
                submission_phase="close submission",
                readback_phase="close readback",
            )
        else:
            recovered = await self._session.call_tool(
                READBACK_TOOL,
                {"client_order_id": close_call.client_order_id},
            )
            close_order_id, recovered_client_id, close_status, close_filled_qty = _broker_identity(
                recovered,
                phase="close recovery readback",
            )
            if recovered_client_id != close_call.client_order_id:
                raise BrokerResponseError(
                    "close recovery readback did not match deterministic client order identity"
                )
        close_status = close_status.lower()
        if close_status != "filled":
            raise PaperLifecycleNotFlat(
                f"atomic close order is not filled; broker status is {close_status}"
            )
        if close_filled_qty != Decimal(open_permit.quantity):
            raise PaperLifecycleManualRequired(
                "filled closing order quantity does not match the permitted package quantity"
            )

        flat_observed_at = await self._verify_event_flat(target_symbols)
        return PaperLifecycleReceipt(
            event_run_id=open_permit.event_run_id,
            open_permit_id=open_permit.permit_id,
            close_permit_id=close_permit.permit_id,
            open_order_id=open_receipt.broker_order_id,
            open_order_status=open_status,
            close_order_id=close_order_id,
            close_order_status=close_status,
            close_request_sha256=close_call.request_sha256,
            target_symbols=target_symbols,
            outcome=PaperLifecycleOutcome.CLOSED_FLAT,
            observed_at=flat_observed_at,
        )
