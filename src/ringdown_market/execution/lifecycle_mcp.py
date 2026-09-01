"""Official host-managed MCP adapter for the monitored PAPER lifecycle.

This is deliberately an adapter, not another provider integration.  It receives
the guarded session created by ``HostMcpPaperSessionFactory`` and translates only
the lifecycle's already-persisted request objects through the pinned Alpaca MCP
wire contract.  Credentials stay entirely inside the host-owned session.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import NoReturn
from weakref import WeakKeyDictionary

import ringdown_market.execution.host_mcp as host_mcp
from ringdown_market.contracts.execution_policy import (
    ACCOUNT_TOOL,
    CANCEL_TOOL,
    ORDER_BY_ID_TOOL,
    POSITIONS_TOOL,
    READBACK_TOOL,
)
from ringdown_market.execution.host_mcp import (
    HostMcpConfigurationError,
    HostMcpError,
    HostMcpMutationAmbiguous,
)
from ringdown_market.execution.mcp import (
    BrokerResponseError,
    McpToolSession,
    _broker_identity,
    _mapping_response,
    build_lifecycle_order_call,
)
from ringdown_market.lifecycle.broker import (
    PAPER_ACCOUNT_CLASS,
    AccountTruth,
    BrokerOrderAck,
    BrokerOrderRequest,
    BrokerOrderTruth,
    BrokerOutage,
    BrokerPositionSnapshot,
    PositionTruth,
)


class LifecycleMcpPaperBroker:
    """Factory-issued lifecycle broker over one preflighted guarded MCP session."""

    __slots__ = ("__weakref__",)

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("LifecycleMcpPaperBroker instances must be factory-created")

    def __copy__(self) -> NoReturn:
        raise TypeError("LifecycleMcpPaperBroker instances must be factory-created")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("LifecycleMcpPaperBroker instances must be factory-created")

    def _validated_state(self) -> object:
        raise HostMcpConfigurationError("lifecycle MCP broker must be factory-created")

    def _observed_at(self) -> datetime:
        observed = self._validated_state().clock()
        if (
            not isinstance(observed, datetime)
            or observed.tzinfo is None
            or observed.utcoffset() is None
        ):
            raise BrokerOutage("host MCP adapter clock must be timezone-aware")
        return observed.astimezone(UTC)

    @staticmethod
    def _request_for_order(
        order_id: str, requests: Mapping[str, BrokerOrderRequest]
    ) -> BrokerOrderRequest:
        request = requests.get(order_id)
        if request is None:
            raise BrokerOutage(
                "host MCP returned an order that was not submitted by this lifecycle"
            )
        return request

    async def _call(self, tool: str, arguments: Mapping[str, object]) -> object:
        try:
            return await self._validated_state().session.call_tool(tool, arguments)
        except HostMcpError as error:
            raise BrokerOutage(f"host MCP {tool} is unavailable: {error}") from error
        except Exception as error:
            raise BrokerOutage(f"host MCP {tool} failed") from error

    async def _readback_by_client_id(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        try:
            response = await self._call(READBACK_TOOL, {"client_order_id": request.client_order_id})
            order_id, client_order_id, _status, _filled = _broker_identity(
                response, phase="lifecycle mutation readback"
            )
        except (BrokerResponseError, ValueError, TypeError) as error:
            raise BrokerOutage(
                "host MCP mutation outcome is ambiguous; readback was invalid"
            ) from error
        if client_order_id != request.client_order_id:
            raise BrokerOutage(
                "host MCP readback client identity did not match the durable request"
            )
        state = self._validated_state()
        state.requests_by_order_id[order_id] = request
        state.latest_order_id = order_id
        return BrokerOrderAck(
            order_id=order_id,
            client_order_id=client_order_id,
            observed_at=self._observed_at(),
        )

    async def _submit(self, request: BrokerOrderRequest, *, phase: str) -> BrokerOrderAck:
        if request.phase != phase:
            raise BrokerOutage(f"lifecycle request phase must be {phase}")
        try:
            call = build_lifecycle_order_call(
                client_order_id=request.client_order_id,
                limit_price=request.limit_price,
                legs=tuple(request.legs),
            )
        except (TypeError, ValueError) as error:
            raise BrokerOutage(f"lifecycle request cannot be encoded for MCP: {error}") from error
        try:
            response = await self._validated_state().session.call_tool(call.tool, call.arguments)
        except HostMcpMutationAmbiguous:
            # A timeout is never retried.  The pinned deterministic client ID is
            # immediately read back through the official MCP query tool instead.
            return await self._readback_by_client_id(request)
        except HostMcpError as error:
            raise BrokerOutage(f"host MCP {phase} submission is unavailable: {error}") from error
        except Exception as error:
            raise BrokerOutage(f"host MCP {phase} submission failed") from error
        try:
            order_id, client_order_id, _status, _filled = _broker_identity(
                response, phase=f"lifecycle {phase.lower()} submission"
            )
        except (BrokerResponseError, ValueError, TypeError) as error:
            raise BrokerOutage(f"host MCP {phase} acknowledgement was invalid") from error
        if client_order_id != request.client_order_id:
            raise BrokerOutage(
                "host MCP acknowledgement client identity did not match durable request"
            )
        state = self._validated_state()
        state.requests_by_order_id[order_id] = request
        state.latest_order_id = order_id
        return BrokerOrderAck(
            order_id=order_id,
            client_order_id=client_order_id,
            observed_at=self._observed_at(),
        )

    async def submit_open(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        """Submit exactly one durable OPEN request through the guarded host session."""

        return await self._submit(request, phase="OPEN")

    async def submit_close(self, request: BrokerOrderRequest) -> BrokerOrderAck:
        """Submit exactly one durable CLOSE request through the guarded host session."""

        return await self._submit(request, phase="CLOSE")

    @staticmethod
    def _filled_quantity(value: Decimal | None, *, phase: str) -> int:
        if value is None or value != value.to_integral_value() or value < 0:
            raise BrokerOutage(f"host MCP {phase} readback omitted a valid whole filled quantity")
        return int(value)

    async def read_order(self, order_id: str) -> BrokerOrderTruth:
        state = self._validated_state()
        request = self._request_for_order(order_id, state.requests_by_order_id)
        try:
            response = await self._call(ORDER_BY_ID_TOOL, {"order_id": order_id})
            returned_id, client_order_id, status, filled_qty = _broker_identity(
                response, phase="lifecycle order readback"
            )
        except (BrokerResponseError, ValueError, TypeError) as error:
            raise BrokerOutage("host MCP order readback was invalid") from error
        if returned_id != order_id or client_order_id != request.client_order_id:
            raise BrokerOutage("host MCP order readback identity did not match durable request")
        return BrokerOrderTruth(
            order_id=order_id,
            client_order_id=client_order_id,
            status=status,
            filled_qty=self._filled_quantity(filled_qty, phase="order"),
            observed_at=self._observed_at(),
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
        state = self._validated_state()
        request = self._request_for_order(order_id, state.requests_by_order_id)
        try:
            response = await state.session.call_tool(CANCEL_TOOL, {"order_id": order_id})
        except HostMcpMutationAmbiguous:
            truth = await self.read_order(order_id)
            return BrokerOrderAck(
                order_id=truth.order_id,
                client_order_id=truth.client_order_id,
                observed_at=truth.observed_at,
            )
        except HostMcpError as error:
            raise BrokerOutage(f"host MCP cancellation is unavailable: {error}") from error
        except Exception as error:
            raise BrokerOutage("host MCP cancellation failed") from error
        # Alpaca cancellation responses are not a fill proof and may be empty.
        if isinstance(response, Mapping) and response:
            try:
                returned_id, client_order_id, _status, _filled = _broker_identity(
                    response, phase="lifecycle cancellation acknowledgement"
                )
            except (BrokerResponseError, ValueError, TypeError) as error:
                raise BrokerOutage("host MCP cancellation acknowledgement was invalid") from error
            if returned_id != order_id or client_order_id != request.client_order_id:
                raise BrokerOutage("host MCP cancellation identity did not match durable request")
        return BrokerOrderAck(
            order_id=order_id,
            client_order_id=request.client_order_id,
            observed_at=self._observed_at(),
        )

    async def read_positions(self) -> BrokerPositionSnapshot:
        state = self._validated_state()
        if state.latest_order_id is None:
            raise BrokerOutage("cannot read correlated positions before a lifecycle submission")
        request = self._request_for_order(state.latest_order_id, state.requests_by_order_id)
        response = await self._call(POSITIONS_TOOL, {})
        if not isinstance(response, list):
            raise BrokerOutage("host MCP position readback was not a list")
        positions: list[PositionTruth] = []
        for index, raw in enumerate(response):
            if not isinstance(raw, Mapping):
                raise BrokerOutage(f"host MCP position {index} was not an object")
            symbol = raw.get("symbol")
            qty = raw.get("qty")
            if not isinstance(symbol, str) or not symbol.strip():
                raise BrokerOutage(f"host MCP position {index} omitted a symbol")
            try:
                parsed_qty = Decimal(str(qty))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise BrokerOutage(f"host MCP position {index} had invalid quantity") from error
            if not parsed_qty.is_finite():
                raise BrokerOutage(f"host MCP position {index} had non-finite quantity")
            positions.append(
                PositionTruth(symbol=symbol, qty=parsed_qty, observed_at=self._observed_at())
            )
        return BrokerPositionSnapshot(
            order_id=state.latest_order_id,
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
            observed_at=self._observed_at(),
            positions=tuple(positions),
        )

    async def read_account(self) -> AccountTruth:
        response = await self._call(ACCOUNT_TOOL, {})
        try:
            payload = _mapping_response(response, phase="account readback")
            account_id = payload.get("id") or payload.get("account_id")
            equity = Decimal(str(payload.get("equity")))
            buying_power = Decimal(str(payload.get("buying_power")))
        except (BrokerResponseError, InvalidOperation, TypeError, ValueError) as error:
            raise BrokerOutage("host MCP account readback was invalid") from error
        if not isinstance(account_id, str) or not account_id.strip():
            raise BrokerOutage("host MCP account readback omitted an account identity")
        if not equity.is_finite() or not buying_power.is_finite():
            raise BrokerOutage("host MCP account readback contained non-finite values")
        return AccountTruth(
            account_id=account_id,
            account_class=PAPER_ACCOUNT_CLASS,
            equity=equity,
            buying_power=buying_power,
            observed_at=self._observed_at(),
        )


def _wire_factory_issued_lifecycle_broker() -> None:
    """Close lifecycle state and minting over the host's preflight-only factory path."""

    @dataclass(slots=True)
    class LifecycleMcpPaperBrokerState:
        session: McpToolSession
        clock: Callable[[], datetime]
        requests_by_order_id: dict[str, BrokerOrderRequest] = field(default_factory=dict)
        latest_order_id: str | None = None

    states: WeakKeyDictionary[LifecycleMcpPaperBroker, LifecycleMcpPaperBrokerState] = (
        WeakKeyDictionary()
    )

    def validated_state(broker: LifecycleMcpPaperBroker) -> LifecycleMcpPaperBrokerState:
        try:
            return states[broker]
        except KeyError:
            raise HostMcpConfigurationError(
                "lifecycle MCP broker must be factory-created"
            ) from None

    def mint(
        session: McpToolSession,
        *,
        clock: Callable[[], datetime],
    ) -> LifecycleMcpPaperBroker:
        broker = object.__new__(LifecycleMcpPaperBroker)
        states[broker] = LifecycleMcpPaperBrokerState(session=session, clock=clock)
        return broker

    LifecycleMcpPaperBroker._validated_state = validated_state  # type: ignore[method-assign]
    host_mcp._install_lifecycle_mcp_broker_mint(mint)


_wire_factory_issued_lifecycle_broker()
del _wire_factory_issued_lifecycle_broker


__all__ = ["LifecycleMcpPaperBroker"]
