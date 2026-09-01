from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ringdown_market.contracts.execution_policy import (
    ALPACA_MCP_PROTOCOL_SHA256,
    PAPER_PERMIT_POLICY_SHA256,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    paper_event_run_id,
)
from ringdown_market.execution.host_mcp import (
    HostMcpConfigurationError,
    HostMcpEnvironment,
    HostMcpPaperSessionFactory,
    HostMcpSessionIdentity,
)
from ringdown_market.execution.mcp import (
    CANCEL_TOOL,
    OPEN_TOOL,
    ORDER_BY_ID_TOOL,
    POSITIONS_TOOL,
    READBACK_TOOL,
    PaperLifecycleManualRequired,
)
from ringdown_market.execution.models import (
    ClosePermit,
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    VerticalType,
    debit_vertical_permit_id,
)
from ringdown_market.execution.paper_demo import (
    FilePaperAttemptStore,
    PaperDemoApproval,
    PaperDemoNotApproved,
    PaperDemoPlan,
    PaperPnlClass,
    run_paper_demo,
)

NOW = datetime(2026, 8, 29, 17, 0, tzinfo=UTC)
LONG_SYMBOL = "NVDA260918C00180000"
SHORT_SYMBOL = "NVDA260918C00185000"
CAPABILITY_SHA256 = "b201168f2f031fbf628e10e587076cac1caff6a1728c94e062ef1ee0526380ca"
FIXTURE_PATH = Path(__file__).parent / "contract_fixtures" / "paper_demo_lifecycle_v1.json"


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
                strike=Decimal("180"),
                option_type=OptionType.CALL,
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            OptionLeg(
                symbol=SHORT_SYMBOL,
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                strike=Decimal("185"),
                option_type=OptionType.CALL,
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )
    return replace(candidate, permit_id=debit_vertical_permit_id(candidate))


def close_permit(opening: DebitVerticalPermit, **changes: object) -> ClosePermit:
    values: dict[str, object] = {
        "permit_id": "permit-close-001",
        "open_permit_id": opening.permit_id,
        "event_run_id": opening.event_run_id,
        "policy_sha256": PAPER_PERMIT_POLICY_SHA256,
        "snapshot_sha256": opening.snapshot_sha256,
        "issued_at": NOW - timedelta(seconds=5),
        "expires_at": NOW + timedelta(seconds=30),
        "limit_price": Decimal("-0.40"),
    }
    values.update(changes)
    return ClosePermit(**values)  # type: ignore[arg-type]


def approval(opening: DebitVerticalPermit, **changes: object) -> PaperDemoApproval:
    values: dict[str, object] = {
        "permit_id": opening.permit_id,
        "capability_sha256": CAPABILITY_SHA256,
        "environment": HostMcpEnvironment.PAPER,
        "approved_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=20),
    }
    values.update(changes)
    return PaperDemoApproval(**values)  # type: ignore[arg-type]


class RecordingSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_tools(self) -> tuple[str, ...]:
        return (
            "get_account_info",
            OPEN_TOOL,
            READBACK_TOOL,
            ORDER_BY_ID_TOOL,
            CANCEL_TOOL,
            POSITIONS_TOOL,
        )

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        if name == "get_account_info":
            return {
                "status": "ACTIVE",
                "trading_blocked": False,
                "account_blocked": False,
            }
        self.calls.append((name, dict(arguments)))
        return self.responses.pop(0)


def prepared(session: RecordingSession):
    factory = HostMcpPaperSessionFactory(
        HostMcpSessionIdentity(HostMcpEnvironment.PAPER),
        clock=lambda: NOW - timedelta(seconds=2),
    )
    return asyncio.run(factory.connect(session))


def order(
    *,
    order_id: str,
    client_order_id: str,
    status: str,
    filled_qty: str,
    filled_avg_price: str | None,
    filled_at: str | None,
    **extra: object,
) -> dict[str, object]:
    return {
        "id": order_id,
        "client_order_id": client_order_id,
        "status": status,
        "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price,
        "filled_at": filled_at,
        **extra,
    }


def test_invalid_approval_stops_before_any_mcp_tool(tmp_path: Path) -> None:
    opening = open_permit()
    session = RecordingSession([])

    with pytest.raises(PaperDemoNotApproved, match="permit"):
        asyncio.run(
            run_paper_demo(
                prepared=prepared(session),
                open_permit=opening,
                close_permit=close_permit(opening),
                approval=approval(opening, permit_id="wrong-permit"),
                attempt_store=FilePaperAttemptStore(tmp_path / "attempts"),
                clock=lambda: NOW,
            )
        )

    assert session.calls == []


def test_expired_or_nonpaper_approval_stops_before_any_mcp_tool(tmp_path: Path) -> None:
    opening = open_permit()
    for bad_approval in (
        approval(opening, expires_at=NOW),
        approval(opening, environment="LIVE"),
        approval(opening, capability_sha256="f" * 64),
    ):
        session = RecordingSession([])
        with pytest.raises(PaperDemoNotApproved):
            asyncio.run(
                run_paper_demo(
                    prepared=prepared(session),
                    open_permit=opening,
                    close_permit=close_permit(opening),
                    approval=bad_approval,
                    attempt_store=FilePaperAttemptStore(tmp_path / bad_approval.capability_sha256),
                    clock=lambda: NOW,
                )
            )
        assert session.calls == []


def test_duplicate_approval_fields_fail_closed() -> None:
    payload = (
        b'{"schema":"ringdown.paper_demo_approval","schema_version":1,'
        b'"permit_id":"first","permit_id":"second",'
        b'"capability_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"environment":"PAPER","approved_at":"2026-08-29T16:59:00Z",'
        b'"expires_at":"2026-08-29T17:01:00Z"}'
    )

    with pytest.raises(PaperDemoNotApproved, match="strict JSON"):
        PaperDemoApproval.from_json_bytes(payload)


def test_preflight_rejects_nonpaper_capability_before_writing_approval() -> None:
    session = RecordingSession([])

    with pytest.raises(HostMcpConfigurationError, match="paper environment"):
        factory = HostMcpPaperSessionFactory(
            HostMcpSessionIdentity(environment="LIVE"),  # type: ignore[arg-type]
            clock=lambda: NOW,
        )
        asyncio.run(factory.connect(session))

    assert session.calls == []


def test_preflight_rejects_expired_permit_before_writing_approval() -> None:
    opening = replace(open_permit(), expires_at=NOW - timedelta(seconds=1))
    plan = PaperDemoPlan(
        prepared=prepared(RecordingSession([])),
        open_permit=opening,
        close_permit=close_permit(opening),
    )

    with pytest.raises(PaperDemoNotApproved, match="opening permit"):
        plan.approval_template_json_bytes(observed_at=NOW)


def test_preflight_rejects_unregistered_close_policy_before_writing_approval() -> None:
    opening = open_permit()
    plan = PaperDemoPlan(
        prepared=prepared(RecordingSession([])),
        open_permit=opening,
        close_permit=close_permit(opening, policy_sha256="c" * 64),
    )

    with pytest.raises(ValueError, match="registered PAPER policy"):
        plan.approval_template_json_bytes(observed_at=NOW)


def test_unregistered_close_policy_stops_before_any_mcp_tool(tmp_path: Path) -> None:
    opening = open_permit()
    session = RecordingSession([])

    with pytest.raises(ValueError, match="registered PAPER policy"):
        asyncio.run(
            run_paper_demo(
                prepared=prepared(session),
                open_permit=opening,
                close_permit=close_permit(opening, policy_sha256="c" * 64),
                approval=approval(opening),
                attempt_store=FilePaperAttemptStore(tmp_path / "attempts"),
                clock=lambda: NOW,
            )
        )

    assert session.calls == []
    assert not (tmp_path / "attempts").exists()


def test_expired_permit_does_not_consume_attempt_identity(tmp_path: Path) -> None:
    opening = replace(open_permit(), expires_at=NOW - timedelta(seconds=1))

    with pytest.raises(PaperDemoNotApproved, match="opening permit"):
        asyncio.run(
            run_paper_demo(
                prepared=prepared(RecordingSession([])),
                open_permit=opening,
                close_permit=close_permit(opening),
                approval=approval(opening),
                attempt_store=FilePaperAttemptStore(tmp_path / "attempts"),
                clock=lambda: NOW,
            )
        )

    assert not (tmp_path / "attempts").exists()


def test_unfilled_open_produces_zero_no_fill_bundle(tmp_path: Path) -> None:
    opening = open_permit()
    from ringdown_market.execution.mcp import build_open_order_call

    open_call = build_open_order_call(opening)
    submitted = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="new",
        filled_qty="0",
        filled_avg_price=None,
        filled_at=None,
    )
    canceled = {**submitted, "status": "canceled", "updated_at": "2026-08-29T16:59:59Z"}
    session = RecordingSession([submitted, submitted, submitted, {}, canceled, [], canceled])

    bundle = asyncio.run(
        run_paper_demo(
            prepared=prepared(session),
            open_permit=opening,
            close_permit=close_permit(opening),
            approval=approval(opening),
            attempt_store=FilePaperAttemptStore(tmp_path / "attempts"),
            clock=lambda: NOW,
        )
    )

    assert bundle.pnl.classification is PaperPnlClass.ZERO_NO_FILL
    assert bundle.pnl.gross_realized_pnl == Decimal("0")
    assert bundle.pnl.broker_fees is None
    assert bundle.pnl.net_realized_pnl is None
    assert bundle.lifecycle_outcome == "CANCELED_FLAT"
    assert [name for name, _ in session.calls] == [
        "place_option_order",
        "get_order_by_client_id",
        "get_order_by_id",
        "cancel_order_by_id",
        "get_order_by_id",
        "get_all_positions",
        "get_order_by_id",
    ]


def test_full_open_and_close_fill_reports_exact_gross_paper_pnl(tmp_path: Path) -> None:
    opening = open_permit()
    exit_permit = close_permit(opening)
    from ringdown_market.execution.mcp import build_close_order_call, build_open_order_call

    open_call = build_open_order_call(opening)
    close_call = build_close_order_call(opening, exit_permit)
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    open_fill = {**fixture["opening_order"], "client_order_id": open_call.client_order_id}
    close_fill = {**fixture["closing_order"], "client_order_id": close_call.client_order_id}
    session = RecordingSession(
        [open_fill, open_fill, open_fill, close_fill, close_fill, [], open_fill, close_fill]
    )

    bundle = asyncio.run(
        run_paper_demo(
            prepared=prepared(session),
            open_permit=opening,
            close_permit=exit_permit,
            approval=approval(opening),
            attempt_store=FilePaperAttemptStore(tmp_path / "attempts"),
            clock=lambda: NOW,
        )
    )

    assert bundle.pnl.classification.value == fixture["expected"]["classification"]
    assert bundle.pnl.gross_realized_pnl == Decimal(fixture["expected"]["gross_realized_pnl"])
    assert bundle.pnl.broker_fees is None
    assert bundle.pnl.net_realized_pnl is None
    assert bundle.pnl.open_filled_at.isoformat() == "2026-08-29T16:59:50+00:00"
    assert bundle.pnl.close_filled_at.isoformat() == "2026-08-29T16:59:58+00:00"
    assert bundle.to_json_bytes() == bundle.to_json_bytes()


def test_missing_or_contradictory_fill_economics_never_guesses_pnl(tmp_path: Path) -> None:
    opening = open_permit()
    exit_permit = close_permit(opening)
    from ringdown_market.execution.mcp import build_close_order_call, build_open_order_call

    open_call = build_open_order_call(opening)
    close_call = build_close_order_call(opening, exit_permit)
    open_fill = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="1.25",
        filled_at="2026-08-29T16:59:50Z",
    )
    close_fill = order(
        order_id="broker-close-456",
        client_order_id=close_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="-1.60",
        filled_at="2026-08-29T16:59:58Z",
    )
    contradictory_close_economics = {**close_fill, "filled_qty": "0.5"}
    session = RecordingSession(
        [
            open_fill,
            open_fill,
            open_fill,
            close_fill,
            close_fill,
            [],
            open_fill,
            contradictory_close_economics,
        ]
    )

    bundle = asyncio.run(
        run_paper_demo(
            prepared=prepared(session),
            open_permit=opening,
            close_permit=exit_permit,
            approval=approval(opening),
            attempt_store=FilePaperAttemptStore(tmp_path / "attempts"),
            clock=lambda: NOW,
        )
    )

    assert bundle.pnl.classification is PaperPnlClass.PAPER_PNL_UNAVAILABLE
    assert bundle.pnl.gross_realized_pnl is None
    assert "quantit" in bundle.pnl.unavailable_reason


def test_unavailable_pnl_reason_sanitizes_invalid_broker_timestamp(tmp_path: Path) -> None:
    opening = open_permit()
    exit_permit = close_permit(opening)
    from ringdown_market.execution.mcp import build_close_order_call, build_open_order_call

    open_call = build_open_order_call(opening)
    close_call = build_close_order_call(opening, exit_permit)
    open_fill = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="1.25",
        filled_at="2026-08-29T16:59:50Z",
    )
    attacker_value = "broker-account-SENSITIVE"
    close_fill = order(
        order_id="broker-close-456",
        client_order_id=close_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="-1.60",
        filled_at=attacker_value,
    )
    session = RecordingSession(
        [open_fill, open_fill, open_fill, close_fill, close_fill, [], open_fill, close_fill]
    )

    bundle = asyncio.run(
        run_paper_demo(
            prepared=prepared(session),
            open_permit=opening,
            close_permit=exit_permit,
            approval=approval(opening),
            attempt_store=FilePaperAttemptStore(tmp_path / "attempts"),
            clock=lambda: NOW,
        )
    )

    assert bundle.pnl.classification is PaperPnlClass.PAPER_PNL_UNAVAILABLE
    assert bundle.pnl.unavailable_reason == "closing filled_at is invalid"
    assert attacker_value not in bundle.to_json_bytes().decode("utf-8")


def test_attempt_store_prevents_resubmission_after_restart(tmp_path: Path) -> None:
    opening = open_permit()
    from ringdown_market.execution.mcp import build_open_order_call

    open_call = build_open_order_call(opening)
    attempt_store = FilePaperAttemptStore(tmp_path / "attempts")
    assert attempt_store.claim(open_call.client_order_id)
    canceled = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="canceled",
        filled_qty="0",
        filled_avg_price=None,
        filled_at=None,
    )
    session = RecordingSession([canceled, canceled, [], canceled])

    bundle = asyncio.run(
        run_paper_demo(
            prepared=prepared(session),
            open_permit=opening,
            close_permit=close_permit(opening),
            approval=approval(opening),
            attempt_store=attempt_store,
            clock=lambda: NOW,
        )
    )

    names = [name for name, _ in session.calls]
    assert names == [
        "get_order_by_client_id",
        "get_order_by_id",
        "get_all_positions",
        "get_order_by_id",
    ]
    assert "place_option_order" not in names
    assert bundle.pnl.classification is PaperPnlClass.ZERO_NO_FILL


def test_sanitized_bundle_contains_no_raw_broker_or_secret_identifiers(tmp_path: Path) -> None:
    opening = open_permit()
    from ringdown_market.execution.mcp import build_open_order_call

    open_call = build_open_order_call(opening)
    submitted = order(
        order_id="broker-open-SENSITIVE",
        client_order_id=open_call.client_order_id,
        status="canceled",
        filled_qty="0",
        filled_avg_price=None,
        filled_at=None,
        account_id="account-SENSITIVE",
        api_key="secret-SENSITIVE",
        nested={"authorization": "bearer-SENSITIVE"},
    )
    session = RecordingSession([submitted, submitted, [], submitted])
    attempt_store = FilePaperAttemptStore(tmp_path / "attempts")
    attempt_store.claim(open_call.client_order_id)

    bundle = asyncio.run(
        run_paper_demo(
            prepared=prepared(session),
            open_permit=opening,
            close_permit=close_permit(opening),
            approval=approval(opening),
            attempt_store=attempt_store,
            clock=lambda: NOW,
        )
    )
    rendered = bundle.to_json_bytes().decode("utf-8")
    parsed = json.loads(rendered)

    assert parsed["schema"] == "ringdown.paper_receipt_bundle"
    assert parsed["schema_version"] == 1
    assert parsed["claims"] == ["PAPER_OPERATIONAL_OBSERVATION", "NOT_ALPHA_EVIDENCE"]
    assert "SENSITIVE" not in rendered
    assert "account_id" not in rendered
    assert "api_key" not in rendered
    assert "authorization" not in rendered
    assert "open_order_sha256" in parsed
    assert parsed["receipt_sha256"] == bundle.receipt_sha256


def test_partial_close_fill_stops_before_final_flat_receipt(tmp_path: Path) -> None:
    opening = open_permit()
    exit_permit = close_permit(opening)
    from ringdown_market.execution.mcp import build_close_order_call, build_open_order_call

    open_call = build_open_order_call(opening)
    close_call = build_close_order_call(opening, exit_permit)
    open_fill = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="1.25",
        filled_at="2026-08-29T16:59:50Z",
    )
    partial_close = order(
        order_id="broker-close-456",
        client_order_id=close_call.client_order_id,
        status="filled",
        filled_qty="0.5",
        filled_avg_price="-1.60",
        filled_at="2026-08-29T16:59:58Z",
    )
    session = RecordingSession([open_fill, open_fill, open_fill, partial_close, partial_close])

    with pytest.raises(PaperLifecycleManualRequired, match="closing order quantity"):
        asyncio.run(
            run_paper_demo(
                prepared=prepared(session),
                open_permit=opening,
                close_permit=exit_permit,
                approval=approval(opening),
                attempt_store=FilePaperAttemptStore(tmp_path / "attempts"),
                clock=lambda: NOW,
            )
        )

    assert session.calls[-1][0] == READBACK_TOOL
