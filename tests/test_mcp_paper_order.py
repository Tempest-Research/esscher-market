from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from ringdown_market.execution.mcp import (
    BrokerResponseError,
    McpPaperBroker,
    PermitNotExecutable,
    build_open_order_call,
)
from ringdown_market.execution.models import (
    DataClass,
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    RunMode,
    VerticalType,
)

NOW = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
EXPIRY = date(2026, 9, 18)


def option_leg(
    *,
    symbol: str,
    strike: str,
    option_type: OptionType = OptionType.CALL,
    side: OptionSide,
    position_intent: PositionIntent,
) -> OptionLeg:
    return OptionLeg(
        symbol=symbol,
        underlying="NVDA",
        expiry=EXPIRY,
        option_type=option_type,
        strike=Decimal(strike),
        side=side,
        position_intent=position_intent,
    )


def bull_call_permit(**overrides: Any) -> DebitVerticalPermit:
    values: dict[str, Any] = {
        "permit_id": "permit-nvda-2026q2-01",
        "event_run_id": "nvda-2026q2-bmo",
        "policy_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "issued_at": NOW - timedelta(seconds=5),
        "expires_at": NOW + timedelta(seconds=30),
        "vertical_type": VerticalType.BULL_CALL,
        "quantity": 1,
        "limit_price": Decimal("1.25"),
        "legs": (
            option_leg(
                symbol="NVDA260918C00180000",
                strike="180",
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            option_leg(
                symbol="NVDA260918C00185000",
                strike="185",
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    }
    values.update(overrides)
    return DebitVerticalPermit(**values)


class RecordingSession:
    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append((name, dict(arguments)))
        return self.responses.pop(0)


class TimeoutThenReadbackSession:
    def __init__(self, readback: Mapping[str, object]) -> None:
        self.readback = readback
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append((name, dict(arguments)))
        if name == "place_option_order":
            raise TimeoutError("submission result was not observed")
        return self.readback


def test_compiles_exact_pinned_alpaca_mcp_multileg_order() -> None:
    permit = bull_call_permit()

    call = build_open_order_call(permit)

    assert call.tool == "place_option_order"
    assert call.arguments == {
        "qty": "1",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "1.25",
        "client_order_id": call.client_order_id,
        "order_class": "mleg",
        "legs": [
            {
                "symbol": "NVDA260918C00180000",
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_open",
            },
            {
                "symbol": "NVDA260918C00185000",
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_open",
            },
        ],
    }
    assert call.client_order_id.startswith("rd-open-")
    assert len(call.client_order_id) <= 48


def test_client_order_id_is_deterministic_and_bound_to_the_permit() -> None:
    first = build_open_order_call(bull_call_permit())
    second = build_open_order_call(bull_call_permit())
    changed = build_open_order_call(bull_call_permit(limit_price=Decimal("1.30")))

    assert first.client_order_id == second.client_order_id
    assert first.request_sha256 == second.request_sha256
    assert first.client_order_id != changed.client_order_id
    assert first.request_sha256 != changed.request_sha256


def test_compiled_call_returns_a_defensive_arguments_copy() -> None:
    call = build_open_order_call(bull_call_permit())
    first = call.arguments
    legs = first["legs"]
    assert isinstance(legs, list)
    assert isinstance(legs[0], dict)
    legs[0]["symbol"] = "CORRUPTED"

    assert call.arguments["legs"][0]["symbol"] == "NVDA260918C00180000"  # type: ignore[index]


def test_accepts_a_valid_bear_put_structure() -> None:
    permit = bull_call_permit(
        vertical_type=VerticalType.BEAR_PUT,
        legs=(
            option_leg(
                symbol="NVDA260918P00180000",
                strike="180",
                option_type=OptionType.PUT,
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            option_leg(
                symbol="NVDA260918P00175000",
                strike="175",
                option_type=OptionType.PUT,
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )

    assert build_open_order_call(permit).arguments["order_class"] == "mleg"


def test_rejects_vertical_with_mismatched_occ_metadata() -> None:
    with pytest.raises(ValueError, match="OCC symbol strike"):
        option_leg(
            symbol="NVDA260918C00180000",
            strike="181",
            side=OptionSide.BUY,
            position_intent=PositionIntent.BUY_TO_OPEN,
        )


def test_rejects_debit_that_is_not_below_vertical_width() -> None:
    with pytest.raises(ValueError, match="below the vertical width"):
        bull_call_permit(limit_price=Decimal("5.00"))


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"run_mode": "LIVE"}, "run_mode must be PAPER"),
        ({"data_class": "OPRA"}, "data_class must be INDICATIVE_DATA"),
    ],
)
def test_rejects_non_competition_execution_modes(override: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        bull_call_permit(**override)


def test_submits_once_then_reads_back_by_client_order_id() -> None:
    permit = bull_call_permit()
    call = build_open_order_call(permit)
    session = RecordingSession(
        [
            {
                "id": "order-123",
                "client_order_id": call.client_order_id,
                "status": "accepted",
            },
            {
                "id": "order-123",
                "client_order_id": call.client_order_id,
                "status": "new",
            },
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    receipt = asyncio.run(broker.submit_open(permit))

    assert [name for name, _ in session.calls] == [
        "place_option_order",
        "get_order_by_client_id",
    ]
    assert session.calls[1][1] == {"client_order_id": call.client_order_id}
    assert receipt.broker_order_id == "order-123"
    assert receipt.client_order_id == call.client_order_id
    assert receipt.broker_status == "new"
    assert receipt.run_mode is RunMode.PAPER
    assert receipt.data_class is DataClass.INDICATIVE_DATA
    assert receipt.request_sha256 == call.request_sha256


def test_transport_ambiguity_reads_back_without_resubmitting() -> None:
    permit = bull_call_permit()
    call = build_open_order_call(permit)
    session = TimeoutThenReadbackSession(
        {
            "id": "order-123",
            "client_order_id": call.client_order_id,
            "status": "new",
        }
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    receipt = asyncio.run(broker.submit_open(permit))

    assert [name for name, _ in session.calls] == [
        "place_option_order",
        "get_order_by_client_id",
    ]
    assert receipt.broker_order_id == "order-123"
    assert receipt.client_order_id == call.client_order_id


def test_open_receipt_uses_post_readback_observation_time() -> None:
    permit = bull_call_permit()
    call = build_open_order_call(permit)
    readback_observed_at = NOW + timedelta(seconds=2)
    clock_values = iter((NOW, readback_observed_at))
    session = RecordingSession(
        [
            {
                "id": "order-123",
                "client_order_id": call.client_order_id,
                "status": "accepted",
            },
            {
                "id": "order-123",
                "client_order_id": call.client_order_id,
                "status": "new",
            },
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: next(clock_values))

    receipt = asyncio.run(broker.submit_open(permit))

    assert receipt.observed_at == readback_observed_at


def test_expired_permit_makes_no_mcp_call() -> None:
    session = RecordingSession([])
    broker = McpPaperBroker(session, clock=lambda: NOW)
    permit = bull_call_permit(expires_at=NOW - timedelta(microseconds=1))

    with pytest.raises(PermitNotExecutable, match="expired"):
        asyncio.run(broker.submit_open(permit))

    assert session.calls == []


def test_rejects_ambiguous_readback_identity() -> None:
    permit = bull_call_permit()
    call = build_open_order_call(permit)
    session = RecordingSession(
        [
            {
                "id": "order-123",
                "client_order_id": call.client_order_id,
                "status": "accepted",
            },
            {
                "id": "order-other",
                "client_order_id": call.client_order_id,
                "status": "new",
            },
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    with pytest.raises(BrokerResponseError, match="order identity"):
        asyncio.run(broker.submit_open(permit))


def test_rejects_tool_error_without_attempting_readback() -> None:
    permit = bull_call_permit()
    session = RecordingSession([{"error": {"message": "paper account rejected order"}}])
    broker = McpPaperBroker(session, clock=lambda: NOW)

    with pytest.raises(BrokerResponseError, match="rejected"):
        asyncio.run(broker.submit_open(permit))

    assert [name for name, _ in session.calls] == ["place_option_order"]
