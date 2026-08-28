"""Single-door Alpaca MCP boundary for governed paper option openings."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from .models import DataClass, DebitVerticalPermit, RunMode

ALPACA_MCP_VERSION = "2.3.0"
ALPACA_MCP_COMMIT = "872abbf28dab6cdde7d341fc13ac139b8002d1d9"
OPEN_TOOL = "place_option_order"
READBACK_TOOL = "get_order_by_client_id"


class PermitNotExecutable(RuntimeError):
    """Raised before mutation when an entry permit is not currently valid."""


class BrokerResponseError(RuntimeError):
    """Raised when broker acknowledgement or readback is missing or ambiguous."""


class McpToolSession(Protocol):
    """Normalized subset of an MCP session consumed by the broker gateway."""

    async def call_tool(
        self, name: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]: ...


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


def _permit_identity(permit: DebitVerticalPermit) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "OPEN",
        "permit_id": permit.permit_id,
        "event_run_id": permit.event_run_id,
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


def _broker_identity(response: Mapping[str, object], *, phase: str) -> tuple[str, str, str]:
    if "error" in response:
        raise BrokerResponseError(f"Alpaca MCP {phase} rejected: {response['error']}")
    order_id = response.get("id")
    client_order_id = response.get("client_order_id")
    status = response.get("status")
    if not all(isinstance(value, str) and value for value in (order_id, client_order_id, status)):
        raise BrokerResponseError(f"Alpaca MCP {phase} omitted required order identity")
    return order_id, client_order_id, status


class McpPaperBroker:
    """Submit once through Alpaca MCP, then resolve identity by broker readback."""

    def __init__(
        self,
        session: McpToolSession,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._clock = clock

    async def submit_open(self, permit: DebitVerticalPermit) -> OpenOrderReceipt:
        """Submit one permit and return only after exact readback agreement."""

        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PermitNotExecutable("broker clock must be timezone-aware")
        if observed_at < permit.issued_at:
            raise PermitNotExecutable("entry permit is not active yet")
        if observed_at >= permit.expires_at:
            raise PermitNotExecutable("entry permit expired before submission")

        call = build_open_order_call(permit)
        submission_error: Exception | None = None
        submitted_id: str | None = None
        try:
            submitted = await self._session.call_tool(call.tool, call.arguments)
        except Exception as error:
            submission_error = error
        else:
            submitted_id, submitted_client_id, _ = _broker_identity(submitted, phase="submission")
            if submitted_client_id != call.client_order_id:
                raise BrokerResponseError("submission client order identity did not match request")

        try:
            readback = await self._session.call_tool(
                READBACK_TOOL,
                {"client_order_id": call.client_order_id},
            )
            readback_id, readback_client_id, readback_status = _broker_identity(
                readback, phase="readback"
            )
        except Exception as error:
            if submission_error is not None:
                raise BrokerResponseError(
                    "submission outcome was ambiguous and readback failed; "
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
