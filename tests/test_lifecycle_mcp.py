"""Exact guarded-fake contract tests for the monitored lifecycle MCP adapter.

These tests cross the real in-package host-session guard only with an in-memory
host fake. They never create a provider session, access an account, or mutate a
broker.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ringdown_market.contracts.execution_policy import (
    ACCOUNT_TOOL,
    CANCEL_TOOL,
    OPEN_TOOL,
    ORDER_BY_ID_TOOL,
    POSITIONS_TOOL,
    READBACK_TOOL,
)
from ringdown_market.execution.host_mcp import (
    HostMcpEnvironment,
    HostMcpPaperSessionFactory,
    HostMcpSecretBoundaryError,
    HostMcpSessionIdentity,
)
from ringdown_market.lifecycle.broker import (
    BrokerOptionLeg,
    BrokerOrderRequest,
    BrokerOutage,
)

NOW = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
ORDER_ID = "paper-order-1"
REQUIRED_TOOLS = (
    ACCOUNT_TOOL,
    OPEN_TOOL,
    READBACK_TOOL,
    ORDER_BY_ID_TOOL,
    CANCEL_TOOL,
    POSITIONS_TOOL,
)


class GuardedFakeHost:
    """In-memory host double used only through the real guarded-session factory."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.failures: dict[str, Exception] = {}
        self.responses: dict[str, object] = {}
        self.last_client_order_id: str | None = None

    async def list_tools(self) -> tuple[str, ...]:
        return REQUIRED_TOOLS

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        copied_arguments = dict(arguments)
        self.calls.append((name, copied_arguments))
        if name == OPEN_TOOL:
            client_order_id = copied_arguments.get("client_order_id")
            assert isinstance(client_order_id, str)
            self.last_client_order_id = client_order_id
        failure = self.failures.get(name)
        if failure is not None:
            raise failure
        if name in self.responses:
            return self.responses[name]
        if name == ACCOUNT_TOOL:
            return {
                "id": "paper-account-1",
                "status": "ACTIVE",
                "trading_blocked": False,
                "account_blocked": False,
                "equity": "10000.50",
                "buying_power": "9000.25",
            }
        if name in {OPEN_TOOL, READBACK_TOOL, ORDER_BY_ID_TOOL}:
            client_order_id = self.last_client_order_id
            if name == READBACK_TOOL:
                requested = copied_arguments.get("client_order_id")
                assert isinstance(requested, str)
                client_order_id = requested
            assert isinstance(client_order_id, str)
            return {
                "id": ORDER_ID,
                "client_order_id": client_order_id,
                "status": "filled",
                "filled_qty": "1",
            }
        if name == CANCEL_TOOL:
            return {}
        if name == POSITIONS_TOOL:
            return []
        raise AssertionError(f"unexpected MCP tool: {name}")


def _request(phase: str = "OPEN") -> BrokerOrderRequest:
    if phase == "OPEN":
        legs = (
            BrokerOptionLeg("NVDA260918C00180000", 1, "buy", "buy_to_open"),
            BrokerOptionLeg("NVDA260918C00185000", 1, "sell", "sell_to_open"),
        )
    else:
        legs = (
            BrokerOptionLeg("NVDA260918C00180000", 1, "sell", "sell_to_close"),
            BrokerOptionLeg("NVDA260918C00185000", 1, "buy", "buy_to_close"),
        )
    return BrokerOrderRequest(
        client_order_id=f"rd-{phase.lower()}-permit-1",
        phase=phase,
        permit_id="permit-1" if phase == "OPEN" else "close-permit-1",
        open_permit_id="permit-1",
        event_run_id="event-1",
        reservation_id="reservation-1",
        correlation_sha256="a" * 64,
        policy_sha256="b" * 64,
        snapshot_sha256="c" * 64,
        account_id="paper-account-1",
        account_class="PAPER",
        order_class="MULTI_LEG",
        limit_price=Decimal("1.25"),
        legs=legs,
    )


def _prepared_broker(host: GuardedFakeHost):
    factory = HostMcpPaperSessionFactory(
        HostMcpSessionIdentity(environment=HostMcpEnvironment.PAPER),
        clock=lambda: NOW,
    )
    prepared = asyncio.run(factory.connect(host))
    host.calls.clear()  # Test only runtime adapter calls after safe preflight.
    return prepared, prepared.lifecycle_broker(clock=lambda: NOW)


def _runtime_tool_names(host: GuardedFakeHost) -> list[str]:
    return [name for name, _ in host.calls]


def test_submit_close_uses_the_pinned_guarded_open_door_with_close_legs() -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)

    acknowledgement = asyncio.run(broker.submit_close(_request("CLOSE")))

    assert acknowledgement.order_id == ORDER_ID
    assert acknowledgement.client_order_id == "rd-close-permit-1"
    assert acknowledgement.observed_at == NOW
    assert _runtime_tool_names(host) == [OPEN_TOOL]
    assert host.calls[0][1] == {
        "qty": "1",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "1.25",
        "client_order_id": "rd-close-permit-1",
        "order_class": "mleg",
        "legs": [
            {
                "symbol": "NVDA260918C00180000",
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_close",
            },
            {
                "symbol": "NVDA260918C00185000",
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_close",
            },
        ],
    }


def test_cancel_order_uses_the_guarded_cancel_tool_and_empty_ack_is_not_fill_proof() -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)
    opening = asyncio.run(broker.submit_open(_request()))
    host.calls.clear()

    cancellation = asyncio.run(broker.cancel_order(opening.order_id))

    assert cancellation.order_id == ORDER_ID
    assert cancellation.client_order_id == opening.client_order_id
    assert _runtime_tool_names(host) == [CANCEL_TOOL]
    assert host.calls[0][1] == {"order_id": ORDER_ID}


def test_invalid_cancel_acknowledgement_is_rejected_before_it_can_claim_completion() -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)
    opening = asyncio.run(broker.submit_open(_request()))
    host.calls.clear()
    host.responses[CANCEL_TOOL] = {
        "id": ORDER_ID,
        "client_order_id": "stale-client",
        "status": "canceled",
        "filled_qty": "0",
    }

    with pytest.raises(BrokerOutage, match="cancellation identity"):
        asyncio.run(broker.cancel_order(opening.order_id))

    assert _runtime_tool_names(host) == [CANCEL_TOOL]


def test_ambiguous_cancel_is_read_back_once_without_repeating_the_mutation() -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)
    opening = asyncio.run(broker.submit_open(_request()))
    host.calls.clear()
    host.failures[CANCEL_TOOL] = TimeoutError("private timeout token")

    cancellation = asyncio.run(broker.cancel_order(opening.order_id))

    assert cancellation.order_id == ORDER_ID
    assert cancellation.client_order_id == opening.client_order_id
    assert _runtime_tool_names(host) == [CANCEL_TOOL, ORDER_BY_ID_TOOL]


def test_positions_and_account_truth_are_parsed_and_correlated_to_the_latest_request() -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)
    opening = asyncio.run(broker.submit_open(_request()))
    host.calls.clear()
    host.responses[POSITIONS_TOOL] = [
        {"symbol": "NVDA260918C00180000", "qty": "1"},
        {"symbol": "NVDA260918C00185000", "qty": "-1"},
    ]

    positions = asyncio.run(broker.read_positions())
    account = asyncio.run(broker.read_account())

    assert positions.order_id == opening.order_id
    assert positions.client_order_id == opening.client_order_id
    assert positions.permit_id == "permit-1"
    assert [(position.symbol, position.qty) for position in positions.positions] == [
        ("NVDA260918C00180000", Decimal("1")),
        ("NVDA260918C00185000", Decimal("-1")),
    ]
    assert account.account_id == "paper-account-1"
    assert account.account_class == "PAPER"
    assert account.equity == Decimal("10000.50")
    assert account.buying_power == Decimal("9000.25")
    assert _runtime_tool_names(host) == [POSITIONS_TOOL, ACCOUNT_TOOL]


@pytest.mark.parametrize(
    ("operation", "tool", "response", "message"),
    [
        ("positions", POSITIONS_TOOL, [{"symbol": "NVDA", "qty": "NaN"}], "non-finite"),
        (
            "account",
            ACCOUNT_TOOL,
            {"id": "paper-account-1", "equity": "NaN", "buying_power": "9000"},
            "non-finite",
        ),
    ],
)
def test_invalid_position_or_account_readbacks_fail_closed(
    operation: str, tool: str, response: object, message: str
) -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)
    asyncio.run(broker.submit_open(_request()))
    host.calls.clear()
    host.responses[tool] = response

    with pytest.raises(BrokerOutage, match=message):
        if operation == "positions":
            asyncio.run(broker.read_positions())
        else:
            asyncio.run(broker.read_account())

    assert _runtime_tool_names(host) == [tool]


@pytest.mark.parametrize(
    ("operation", "tool", "message"),
    [
        ("submit", OPEN_TOOL, "submission is unavailable"),
        ("account", ACCOUNT_TOOL, "unavailable"),
    ],
)
def test_host_outages_are_typed_and_redacted(operation: str, tool: str, message: str) -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)
    host.failures[tool] = RuntimeError("credential=do-not-emit")

    with pytest.raises(BrokerOutage, match=message) as captured:
        if operation == "submit":
            asyncio.run(broker.submit_open(_request()))
        else:
            asyncio.run(broker.read_account())

    assert "do-not-emit" not in str(captured.value)
    assert _runtime_tool_names(host) == [tool]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            {
                "id": ORDER_ID,
                "client_order_id": "stale-client",
                "status": "filled",
                "filled_qty": "1",
            },
            "readback client identity",
        ),
        ({"id": ORDER_ID}, "readback was invalid"),
    ],
)
def test_ambiguous_mutation_readback_never_retries_and_fails_closed(
    response: object, message: str
) -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)
    host.failures[OPEN_TOOL] = TimeoutError("transport secret")
    host.responses[READBACK_TOOL] = response

    with pytest.raises(BrokerOutage, match=message) as captured:
        asyncio.run(broker.submit_open(_request()))

    assert "transport secret" not in str(captured.value)
    assert _runtime_tool_names(host) == [OPEN_TOOL, READBACK_TOOL]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            {
                "id": "stale-order",
                "client_order_id": "rd-open-permit-1",
                "status": "filled",
                "filled_qty": "1",
            },
            "identity did not match",
        ),
        (
            {
                "id": ORDER_ID,
                "client_order_id": "wrong-client",
                "status": "filled",
                "filled_qty": "1",
            },
            "identity did not match",
        ),
    ],
)
def test_stale_or_mismatched_order_readback_is_rejected(response: object, message: str) -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)
    opening = asyncio.run(broker.submit_open(_request()))
    host.calls.clear()
    host.responses[ORDER_BY_ID_TOOL] = response

    with pytest.raises(BrokerOutage, match=message):
        asyncio.run(broker.read_order(opening.order_id))

    assert _runtime_tool_names(host) == [ORDER_BY_ID_TOOL]


def test_unknown_stale_order_identity_is_rejected_before_the_host_read() -> None:
    host = GuardedFakeHost()
    _prepared, broker = _prepared_broker(host)

    with pytest.raises(BrokerOutage, match="not submitted"):
        asyncio.run(broker.read_order("stale-order"))

    assert host.calls == []


def test_secret_like_arguments_are_rejected_before_the_fake_host_and_adapter_never_emits_them() -> (
    None
):
    host = GuardedFakeHost()
    prepared, broker = _prepared_broker(host)

    asyncio.run(broker.submit_open(_request()))
    assert all("secret" not in str(arguments).lower() for _, arguments in host.calls)
    host.calls.clear()

    with pytest.raises(HostMcpSecretBoundaryError, match="secret-like"):
        asyncio.run(
            prepared.session.call_tool(
                OPEN_TOOL,
                {"client_order_id": "safe", "metadata": {"api_key": "do-not-emit"}},
            )
        )

    assert host.calls == []
