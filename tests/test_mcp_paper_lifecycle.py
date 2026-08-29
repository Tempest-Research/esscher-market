from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ringdown_market.contracts.execution_policy import (
    ALPACA_MCP_PROTOCOL_SHA256,
    PAPER_PERMIT_POLICY_SHA256,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    paper_event_run_id,
)
from ringdown_market.execution.mcp import (
    McpPaperBroker,
    OpenOrderReceipt,
    PaperLifecycleManualRequired,
    PaperLifecycleNotFlat,
    PaperLifecycleOutcome,
    PermitNotExecutable,
    build_close_order_call,
    build_open_order_call,
)
from ringdown_market.execution.models import (
    ClosePermit,
    DataClass,
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    RunMode,
    VerticalType,
    debit_vertical_permit_id,
)

NOW = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
LONG_SYMBOL = "NVDA260918C00180000"
SHORT_SYMBOL = "NVDA260918C00185000"


def open_permit() -> DebitVerticalPermit:
    decision_sha256 = "d" * 64
    candidate = DebitVerticalPermit._from_frozen_decision(
        permit_id="UNBOUND",
        event_run_id=paper_event_run_id(decision_sha256),
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256="b" * 64,
        decision_sha256=decision_sha256,
        evidence_sha256="e" * 64,
        protocol_sha256=RESEARCH_DECISION_PROTOCOL_SHA256,
        execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
        issued_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(seconds=30),
        vertical_type=VerticalType.BULL_CALL,
        quantity=1,
        limit_price=Decimal("1.25"),
        legs=(
            OptionLeg(
                symbol=LONG_SYMBOL,
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                strike=Decimal("180.00"),
                option_type=OptionType.CALL,
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            OptionLeg(
                symbol=SHORT_SYMBOL,
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                strike=Decimal("185.00"),
                option_type=OptionType.CALL,
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )
    return replace(candidate, permit_id=debit_vertical_permit_id(candidate))


def close_permit() -> ClosePermit:
    opening = open_permit()
    return ClosePermit(
        permit_id="permit-close-001",
        open_permit_id=opening.permit_id,
        event_run_id=opening.event_run_id,
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256=opening.snapshot_sha256,
        issued_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(seconds=30),
        limit_price=Decimal("-0.40"),
    )


def open_receipt(permit: DebitVerticalPermit) -> OpenOrderReceipt:
    call = build_open_order_call(permit)
    return OpenOrderReceipt(
        broker_order_id="open-order-123",
        client_order_id=call.client_order_id,
        broker_status="new",
        request_sha256=call.request_sha256,
        permit_id=permit.permit_id,
        event_run_id=permit.event_run_id,
        observed_at=NOW,
        run_mode=RunMode.PAPER,
        data_class=DataClass.INDICATIVE_DATA,
    )


class RecordingSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        self.calls.append((name, dict(arguments)))
        return self.responses.pop(0)


def test_paper_is_the_only_runtime_mode() -> None:
    assert list(RunMode) == [RunMode.PAPER]


def test_rejects_receipt_outside_pinned_mcp_adapter_before_tool_use() -> None:
    permit = open_permit()
    receipt = replace(open_receipt(permit), adapter="OTHER_ADAPTER")
    session = RecordingSession([])
    broker = McpPaperBroker(session, clock=lambda: NOW)

    with pytest.raises(PermitNotExecutable, match="adapter"):
        asyncio.run(
            broker.resolve_to_flat(
                open_permit=permit,
                open_receipt=receipt,
                close_permit=close_permit(),
            )
        )

    assert session.calls == []


def test_close_permit_requires_a_credit_limit() -> None:
    with pytest.raises(ValueError, match="negative credit"):
        ClosePermit(
            permit_id="permit-close-001",
            open_permit_id="permit-open-001",
            event_run_id="event-nvda-2026q2",
            policy_sha256="c" * 64,
            snapshot_sha256="b" * 64,
            issued_at=NOW - timedelta(seconds=5),
            expires_at=NOW + timedelta(seconds=30),
            limit_price=Decimal("0.40"),
        )


def test_rejects_close_permit_outside_registered_paper_policy() -> None:
    unregistered = replace(close_permit(), policy_sha256="c" * 64)

    with pytest.raises(ValueError, match="registered PAPER policy"):
        build_close_order_call(open_permit(), unregistered)


def test_compiles_atomic_reversed_multileg_close_order() -> None:
    call = build_close_order_call(open_permit(), close_permit())

    assert call.tool == "place_option_order"
    assert call.arguments == {
        "qty": "1",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "-0.4",
        "client_order_id": call.client_order_id,
        "order_class": "mleg",
        "legs": [
            {
                "symbol": LONG_SYMBOL,
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_close",
            },
            {
                "symbol": SHORT_SYMBOL,
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_close",
            },
        ],
    }


def test_cancels_unfilled_order_then_verifies_event_positions_are_flat() -> None:
    permit = open_permit()
    receipt = open_receipt(permit)
    session = RecordingSession(
        [
            {
                "id": receipt.broker_order_id,
                "client_order_id": receipt.client_order_id,
                "status": "new",
            },
            {},
            {
                "id": receipt.broker_order_id,
                "client_order_id": receipt.client_order_id,
                "status": "canceled",
                "filled_qty": "0",
            },
            [],
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    lifecycle = asyncio.run(
        broker.resolve_to_flat(
            open_permit=permit,
            open_receipt=receipt,
            close_permit=close_permit(),
        )
    )

    assert [name for name, _ in session.calls] == [
        "get_order_by_id",
        "cancel_order_by_id",
        "get_order_by_id",
        "get_all_positions",
    ]
    assert lifecycle.outcome is PaperLifecycleOutcome.CANCELED_FLAT
    assert lifecycle.close_order_id is None
    assert lifecycle.target_symbols == (LONG_SYMBOL, SHORT_SYMBOL)


def test_terminal_receipt_uses_the_final_position_observation_time() -> None:
    permit = open_permit()
    receipt = open_receipt(permit)
    final_observed_at = NOW + timedelta(seconds=2)
    clock_values = iter((NOW, final_observed_at))
    session = RecordingSession(
        [
            {
                "id": receipt.broker_order_id,
                "client_order_id": receipt.client_order_id,
                "status": "canceled",
                "filled_qty": "0",
            },
            [],
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: next(clock_values))

    lifecycle = asyncio.run(
        broker.resolve_to_flat(
            open_permit=permit,
            open_receipt=receipt,
            close_permit=close_permit(),
        )
    )

    assert lifecycle.observed_at == final_observed_at


def test_closes_filled_spread_atomically_then_verifies_flat() -> None:
    permit = open_permit()
    receipt = open_receipt(permit)
    exit_permit = close_permit()
    close_call = build_close_order_call(permit, exit_permit)
    session = RecordingSession(
        [
            {
                "id": receipt.broker_order_id,
                "client_order_id": receipt.client_order_id,
                "status": "filled",
                "filled_qty": "1",
            },
            {
                "id": "close-order-456",
                "client_order_id": close_call.client_order_id,
                "status": "filled",
                "filled_qty": "1",
            },
            {
                "id": "close-order-456",
                "client_order_id": close_call.client_order_id,
                "status": "filled",
                "filled_qty": "1",
            },
            [],
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    lifecycle = asyncio.run(
        broker.resolve_to_flat(
            open_permit=permit,
            open_receipt=receipt,
            close_permit=exit_permit,
        )
    )

    assert [name for name, _ in session.calls] == [
        "get_order_by_id",
        "place_option_order",
        "get_order_by_client_id",
        "get_all_positions",
    ]
    assert session.calls[1][1] == close_call.arguments
    assert lifecycle.outcome is PaperLifecycleOutcome.CLOSED_FLAT
    assert lifecycle.close_order_id == "close-order-456"
    assert lifecycle.close_request_sha256 == close_call.request_sha256


def test_partial_fill_refuses_sequential_leg_repair() -> None:
    permit = open_permit()
    receipt = open_receipt(permit)
    session = RecordingSession(
        [
            {
                "id": receipt.broker_order_id,
                "client_order_id": receipt.client_order_id,
                "status": "partially_filled",
            }
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    with pytest.raises(PaperLifecycleManualRequired, match="partial fill"):
        asyncio.run(
            broker.resolve_to_flat(
                open_permit=permit,
                open_receipt=receipt,
                close_permit=close_permit(),
            )
        )

    assert [name for name, _ in session.calls] == ["get_order_by_id"]


@pytest.mark.parametrize("terminal_status", ["canceled", "expired"])
def test_terminal_order_with_nonzero_fill_requires_manual_reconciliation(
    terminal_status: str,
) -> None:
    permit = open_permit()
    receipt = open_receipt(permit)
    session = RecordingSession(
        [
            {
                "id": receipt.broker_order_id,
                "client_order_id": receipt.client_order_id,
                "status": terminal_status,
                "filled_qty": "0.5",
            }
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    with pytest.raises(PaperLifecycleManualRequired, match="nonzero fill"):
        asyncio.run(
            broker.resolve_to_flat(
                open_permit=permit,
                open_receipt=receipt,
                close_permit=close_permit(),
            )
        )

    assert [name for name, _ in session.calls] == ["get_order_by_id"]


@pytest.mark.parametrize(
    "order_fields",
    [
        {},
        {"filled_qty": None},
        {"filled_qty": ""},
        {"filled_qty": "not-a-number"},
        {"filled_qty": "-0.1"},
        {"filled_qty": "NaN"},
        {"filled_qty": "Infinity"},
        {"filled_qty": True},
    ],
)
def test_canceled_order_with_ambiguous_fill_quantity_requires_manual_reconciliation(
    order_fields: dict[str, object],
) -> None:
    permit = open_permit()
    receipt = open_receipt(permit)
    session = RecordingSession(
        [
            {
                "id": receipt.broker_order_id,
                "client_order_id": receipt.client_order_id,
                "status": "canceled",
                **order_fields,
            }
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    with pytest.raises(PaperLifecycleManualRequired, match="filled quantity"):
        asyncio.run(
            broker.resolve_to_flat(
                open_permit=permit,
                open_receipt=receipt,
                close_permit=close_permit(),
            )
        )

    assert [name for name, _ in session.calls] == ["get_order_by_id"]


def test_position_truth_prevents_false_flat_receipt() -> None:
    permit = open_permit()
    receipt = open_receipt(permit)
    session = RecordingSession(
        [
            {
                "id": receipt.broker_order_id,
                "client_order_id": receipt.client_order_id,
                "status": "canceled",
                "filled_qty": "0",
            },
            [{"symbol": LONG_SYMBOL, "qty": "1"}],
        ]
    )
    broker = McpPaperBroker(session, clock=lambda: NOW)

    with pytest.raises(PaperLifecycleNotFlat, match=LONG_SYMBOL):
        asyncio.run(
            broker.resolve_to_flat(
                open_permit=permit,
                open_receipt=receipt,
                close_permit=close_permit(),
            )
        )

    assert [name for name, _ in session.calls] == [
        "get_order_by_id",
        "get_all_positions",
    ]


def test_close_permit_rejects_non_competition_modes() -> None:
    base = {
        "permit_id": "permit-close-001",
        "open_permit_id": "permit-open-001",
        "event_run_id": "event-nvda-2026q2",
        "policy_sha256": "c" * 64,
        "snapshot_sha256": "b" * 64,
        "issued_at": NOW - timedelta(seconds=5),
        "expires_at": NOW + timedelta(seconds=30),
        "limit_price": Decimal("-0.40"),
    }
    with pytest.raises(ValueError, match="run_mode must be PAPER"):
        ClosePermit(**base, run_mode="LIVE")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="data_class must be INDICATIVE_DATA"):
        ClosePermit(**base, data_class="OPRA")  # type: ignore[arg-type]

    assert DataClass.INDICATIVE_DATA.value == "INDICATIVE_DATA"
