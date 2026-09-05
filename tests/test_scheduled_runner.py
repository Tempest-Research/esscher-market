from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import types
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import esscher.runtime.scheduled as scheduled_module
from esscher.cli import main as cli_main
from esscher.contracts.execution_policy import (
    ALPACA_MCP_PROTOCOL_SHA256,
    PAPER_PERMIT_POLICY_SHA256,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    paper_event_run_id,
)
from esscher.execution.host_mcp import (
    HostMcpEnvironment,
    HostMcpPaperSessionFactory,
    HostMcpSessionIdentity,
)
from esscher.execution.mcp import (
    CANCEL_TOOL,
    OPEN_TOOL,
    ORDER_BY_ID_TOOL,
    POSITIONS_TOOL,
    READBACK_TOOL,
    build_close_order_call,
    build_open_order_call,
)
from esscher.execution.models import (
    ClosePermit,
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    VerticalType,
    debit_vertical_permit_id,
)
from esscher.execution.paper_demo import PaperDemoPlan
from esscher.runtime.scheduled import (
    FileScheduledEventStore,
    ScheduledEventManifest,
    ScheduledEventOverlap,
    ScheduledManifestRejected,
    ScheduledManualReconciliationRequired,
    run_scheduled_event_command,
)

NOW = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
CONTRACT_FIXTURES = Path(__file__).parent / "contract_fixtures"


def manifest_bytes(**changes: object) -> bytes:
    payload: dict[str, object] = {
        "schema": "ringdown.scheduled_event_manifest",
        "schema_version": 1,
        "event_run_id": "paper-event-test-001",
        "open_permit_id": "permit-open-test-001",
        "close_permit_id": "permit-close-test-001",
        "capability_sha256": "a" * 64,
        "run_mode": "PAPER",
        "data_class": "INDICATIVE_DATA",
        "approved_at": (NOW - timedelta(minutes=1)).isoformat(),
        "not_before": (NOW - timedelta(seconds=5)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
        "claims": ["PAPER_OPERATIONAL_RESULT", "INDICATIVE_DATA"],
    }
    payload.update(changes)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class RecordingSession:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_tools(self) -> tuple[str, ...]:
        return (
            "get_account_info",
            OPEN_TOOL,
            READBACK_TOOL,
            ORDER_BY_ID_TOOL,
            CANCEL_TOOL,
            POSITIONS_TOOL,
            "get_account_activities",
            "get_orders",
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


def open_permit(decision_sha256: str = "d" * 64) -> DebitVerticalPermit:
    candidate = DebitVerticalPermit._from_frozen_decision(
        permit_id="UNBOUND",
        event_run_id=paper_event_run_id(decision_sha256),
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256="b" * 64,
        decision_sha256=decision_sha256,
        evidence_sha256="e" * 64,
        protocol_sha256=RESEARCH_DECISION_PROTOCOL_SHA256,
        execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
        issued_at=NOW - timedelta(minutes=2),
        expires_at=NOW + timedelta(minutes=2),
        vertical_type=VerticalType.BULL_CALL,
        quantity=1,
        limit_price=Decimal("1.25"),
        legs=(
            OptionLeg(
                symbol="NVDA260918C00180000",
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                option_type=OptionType.CALL,
                strike=Decimal("180"),
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            OptionLeg(
                symbol="NVDA260918C00185000",
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                option_type=OptionType.CALL,
                strike=Decimal("185"),
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )
    return replace(candidate, permit_id=debit_vertical_permit_id(candidate))


def close_permit(opening: DebitVerticalPermit) -> ClosePermit:
    return ClosePermit(
        permit_id="permit-close-test-001",
        open_permit_id=opening.permit_id,
        event_run_id=opening.event_run_id,
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256=opening.snapshot_sha256,
        issued_at=NOW - timedelta(minutes=2),
        expires_at=NOW + timedelta(minutes=2),
        limit_price=Decimal("-0.40"),
    )


def prepared_session(session: RecordingSession):
    factory = HostMcpPaperSessionFactory(
        HostMcpSessionIdentity(HostMcpEnvironment.PAPER),
        clock=lambda: NOW - timedelta(seconds=10),
    )
    return asyncio.run(factory.connect(session))


def paper_plan(
    session: RecordingSession,
    *,
    decision_sha256: str = "d" * 64,
) -> PaperDemoPlan:
    opening = open_permit(decision_sha256)
    return PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=opening,
        close_permit=close_permit(opening),
    )


def bound_manifest_bytes(plan: PaperDemoPlan, **changes: object) -> bytes:
    values: dict[str, object] = {
        "event_run_id": plan.open_permit.event_run_id,
        "open_permit_id": plan.open_permit.permit_id,
        "close_permit_id": plan.close_permit.permit_id,
        "capability_sha256": plan.prepared.observation.capability_sha256,
    }
    values.update(changes)
    return manifest_bytes(**values)


def order(
    *,
    order_id: str,
    client_order_id: str,
    status: str,
    filled_qty: str,
    filled_avg_price: str | None = None,
    filled_at: str | None = None,
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


def test_nonpaper_manifest_rejects_before_plan_factory_or_state_mutation(tmp_path: Path) -> None:
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("plan factory must not be called")

    with pytest.raises(ScheduledManifestRejected, match="PAPER"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=manifest_bytes(run_mode="LIVE"),
                state_dir=tmp_path / "state",
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW,
            )
        )

    assert plan_factory_called is False
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "window",
    [
        {
            "not_before": (NOW + timedelta(seconds=1)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
        },
        {
            "not_before": (NOW - timedelta(minutes=1)).isoformat(),
            "expires_at": NOW.isoformat(),
        },
    ],
)
def test_outside_due_window_rejects_before_plan_factory_or_state_mutation(
    tmp_path: Path,
    window: dict[str, object],
) -> None:
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("plan factory must not be called")

    with pytest.raises(ScheduledManifestRejected, match="due window"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=manifest_bytes(**window),
                state_dir=tmp_path / "state",
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW,
            )
        )

    assert plan_factory_called is False
    assert not (tmp_path / "state").exists()


def test_dry_run_validates_exact_plan_without_state_or_session_mutation(tmp_path: Path) -> None:
    session = RecordingSession()
    plan = paper_plan(session)
    state_dir = tmp_path / "state"

    result = asyncio.run(
        run_scheduled_event_command(
            manifest_bytes=bound_manifest_bytes(plan),
            state_dir=state_dir,
            plan_factory=lambda: plan,
            dry_run=True,
            clock=lambda: NOW,
        )
    )

    assert result.disposition == "DRY_RUN_VALIDATED"
    assert result.lifecycle == "VALIDATED"
    payload = json.loads(result.to_json_bytes())
    assert payload["claims"] == [
        "PAPER_OPERATIONAL_RESULT",
        "INDICATIVE_DATA",
        "NOT_ALPHA_EVIDENCE",
    ]
    assert payload["run_mode"] == "PAPER"
    assert payload["data_class"] == "INDICATIVE_DATA"
    assert payload["event_run_id"] == plan.open_permit.event_run_id
    assert payload["broker_mutation"] == "NOT_ATTEMPTED"
    assert session.calls == []
    assert not state_dir.exists()


def test_armed_unfilled_event_persists_sanitized_terminal_state(tmp_path: Path) -> None:
    bootstrap_session = RecordingSession()
    bootstrap_plan = paper_plan(bootstrap_session)
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    submitted = order(
        order_id="broker-open-SENSITIVE",
        client_order_id=open_call.client_order_id,
        status="new",
        filled_qty="0",
    )
    canceled = {**submitted, "status": "canceled"}
    session = RecordingSession([submitted, submitted, submitted, {}, canceled, [], canceled])
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    state_dir = tmp_path / "state"

    result = asyncio.run(
        run_scheduled_event_command(
            manifest_bytes=bound_manifest_bytes(plan),
            state_dir=state_dir,
            plan_factory=lambda: plan,
            clock=lambda: NOW,
        )
    )

    assert result.disposition == "EXECUTED_TO_TERMINAL"
    assert result.lifecycle == "CANCELED_FLAT"
    assert result.broker_mutation == "BOUNDED_PAPER_PIPELINE"
    assert result.receipt is not None
    assert result.receipt["paper_pnl"]["classification"] == "ZERO_NO_FILL"  # type: ignore[index]
    assert [name for name, _ in session.calls] == [
        "place_option_order",
        "get_order_by_client_id",
        "get_order_by_id",
        "cancel_order_by_id",
        "get_order_by_id",
        "get_all_positions",
        "get_order_by_id",
    ]

    store = FileScheduledEventStore(state_dir)
    rendered_state = store.state_path(plan.open_permit.event_run_id).read_text(encoding="utf-8")
    state = json.loads(rendered_state)
    state_sha256 = state.pop("state_sha256")
    assert (
        state_sha256
        == hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    assert state["lifecycle"] == "CANCELED_FLAT"
    assert state["manifest_sha256"] == result.manifest_sha256
    assert state["receipt"] == result.receipt
    assert "broker-open-SENSITIVE" not in rendered_state
    assert not list(state_dir.glob("*.tmp"))


def test_terminal_repeat_is_noop_even_after_due_window(tmp_path: Path) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    submitted = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="new",
        filled_qty="0",
    )
    canceled = {**submitted, "status": "canceled"}
    first_session = RecordingSession([submitted, submitted, submitted, {}, canceled, [], canceled])
    plan = PaperDemoPlan(
        prepared=prepared_session(first_session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    manifest = bound_manifest_bytes(plan)
    state_dir = tmp_path / "state"
    first = asyncio.run(
        run_scheduled_event_command(
            manifest_bytes=manifest,
            state_dir=state_dir,
            plan_factory=lambda: plan,
            clock=lambda: NOW,
        )
    )
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("terminal repeats must not construct a host plan")

    repeated = asyncio.run(
        run_scheduled_event_command(
            manifest_bytes=manifest,
            state_dir=state_dir,
            plan_factory=forbidden_plan_factory,
            clock=lambda: NOW + timedelta(minutes=5),
        )
    )

    assert repeated.disposition == "TERMINAL_NOOP"
    assert repeated.lifecycle == "CANCELED_FLAT"
    assert repeated.receipt == first.receipt
    assert repeated.broker_mutation == "NOT_ATTEMPTED"
    assert plan_factory_called is False
    assert [name for name, _ in first_session.calls].count("place_option_order") == 1


def test_restart_from_reconciling_state_reads_broker_truth_without_resubmitting(
    tmp_path: Path,
) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    canceled = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="canceled",
        filled_qty="0",
    )
    session = RecordingSession([canceled, canceled, [], canceled])
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    raw_manifest = bound_manifest_bytes(plan)
    manifest = ScheduledEventManifest.from_json_bytes(raw_manifest)
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=manifest,
        lifecycle="RECONCILING",
        updated_at=NOW - timedelta(seconds=1),
        receipt=None,
    )
    assert store.attempt_store(manifest.event_run_id).claim(open_call.client_order_id)

    result = asyncio.run(
        run_scheduled_event_command(
            manifest_bytes=raw_manifest,
            state_dir=store.root,
            plan_factory=lambda: plan,
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
    assert "cancel_order_by_id" not in names
    assert result.lifecycle == "CANCELED_FLAT"


def test_nonterminal_state_rejects_changed_manifest_before_plan_or_broker_use(
    tmp_path: Path,
) -> None:
    session = RecordingSession()
    plan = paper_plan(session)
    original_bytes = bound_manifest_bytes(plan)
    original = ScheduledEventManifest.from_json_bytes(original_bytes)
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=original,
        lifecycle="RECONCILING",
        updated_at=NOW - timedelta(seconds=1),
        receipt=None,
    )
    changed_bytes = bound_manifest_bytes(
        plan,
        approved_at=(NOW - timedelta(minutes=2)).isoformat(),
    )
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("mismatched restart state must fail before plan creation")

    with pytest.raises(ScheduledManualReconciliationRequired, match="state identity"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=changed_bytes,
                state_dir=store.root,
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW,
            )
        )

    assert plan_factory_called is False
    assert session.calls == []
    assert (
        json.loads(store.state_path(original.event_run_id).read_bytes())["manifest_sha256"]
        == original.manifest_sha256
    )


def test_partial_fill_persists_manual_reconciliation_without_guessed_pnl(tmp_path: Path) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    partial = order(
        order_id="broker-open-SENSITIVE",
        client_order_id=open_call.client_order_id,
        status="partially_filled",
        filled_qty="0.5",
    )
    session = RecordingSession([partial, partial, partial])
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    raw_manifest = bound_manifest_bytes(plan)
    state_dir = tmp_path / "state"

    with pytest.raises(
        ScheduledManualReconciliationRequired,
        match="manual reconciliation required",
    ):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=raw_manifest,
                state_dir=state_dir,
                plan_factory=lambda: plan,
                clock=lambda: NOW,
            )
        )

    names = [name for name, _ in session.calls]
    assert names == ["place_option_order", "get_order_by_client_id", "get_order_by_id"]
    assert "cancel_order_by_id" not in names
    state_path = FileScheduledEventStore(state_dir).state_path(plan.open_permit.event_run_id)
    rendered = state_path.read_text(encoding="utf-8")
    state = json.loads(rendered)
    assert state["lifecycle"] == "MANUAL_RECONCILIATION"
    assert state["failure_code"] == "AMBIGUOUS_OR_PARTIAL_BROKER_STATE"
    assert state["receipt"] is None
    assert "broker-open-SENSITIVE" not in rendered
    assert "0.5" not in rendered
    assert "paper_pnl" not in rendered


def test_manual_reconciliation_state_never_auto_resumes(tmp_path: Path) -> None:
    session = RecordingSession()
    plan = paper_plan(session)
    raw_manifest = bound_manifest_bytes(plan)
    manifest = ScheduledEventManifest.from_json_bytes(raw_manifest)
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=manifest,
        lifecycle="MANUAL_RECONCILIATION",
        updated_at=NOW - timedelta(seconds=1),
        receipt=None,
        failure_code="AMBIGUOUS_OR_PARTIAL_BROKER_STATE",
    )
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("manual state must not auto-resume")

    with pytest.raises(ScheduledManualReconciliationRequired, match="manual reconciliation"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=raw_manifest,
                state_dir=store.root,
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW,
            )
        )

    assert plan_factory_called is False
    assert session.calls == []


def test_overlapping_active_event_rejects_before_plan_or_broker_use(tmp_path: Path) -> None:
    first_plan = paper_plan(RecordingSession(), decision_sha256="d" * 64)
    first_manifest = ScheduledEventManifest.from_json_bytes(bound_manifest_bytes(first_plan))
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=first_manifest,
        lifecycle="RECONCILING",
        updated_at=NOW - timedelta(seconds=1),
        receipt=None,
    )

    second_session = RecordingSession()
    second_plan = paper_plan(second_session, decision_sha256="c" * 64)
    second_manifest = bound_manifest_bytes(second_plan)
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("overlap must fail before plan creation")

    with pytest.raises(ScheduledEventOverlap, match="overlapping active event"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=second_manifest,
                state_dir=store.root,
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW,
            )
        )

    assert plan_factory_called is False
    assert second_session.calls == []
    assert not store.state_path(second_plan.open_permit.event_run_id).exists()


def test_restart_after_close_attempt_reconciles_both_deterministic_orders_without_submit(
    tmp_path: Path,
) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    close_call = build_close_order_call(
        bootstrap_plan.open_permit,
        bootstrap_plan.close_permit,
    )
    open_fill = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="1.25",
        filled_at="2026-08-29T19:59:50Z",
    )
    close_fill = order(
        order_id="broker-close-456",
        client_order_id=close_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="-1.60",
        filled_at="2026-08-29T19:59:58Z",
    )
    session = RecordingSession([open_fill, open_fill, close_fill, [], open_fill, close_fill])
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    raw_manifest = bound_manifest_bytes(plan)
    manifest = ScheduledEventManifest.from_json_bytes(raw_manifest)
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=manifest,
        lifecycle="RECONCILING",
        updated_at=NOW - timedelta(seconds=1),
        receipt=None,
    )
    attempts = store.attempt_store(manifest.event_run_id)
    assert attempts.claim(open_call.client_order_id)
    assert attempts.claim(close_call.client_order_id)

    result = asyncio.run(
        run_scheduled_event_command(
            manifest_bytes=raw_manifest,
            state_dir=store.root,
            plan_factory=lambda: plan,
            clock=lambda: NOW,
        )
    )

    names = [name for name, _ in session.calls]
    assert names == [
        "get_order_by_client_id",
        "get_order_by_id",
        "get_order_by_client_id",
        "get_all_positions",
        "get_order_by_id",
        "get_order_by_id",
    ]
    assert "place_option_order" not in names
    assert result.lifecycle == "CLOSED_FLAT"
    assert result.receipt is not None
    assert result.receipt["paper_pnl"]["classification"] == "PAPER_REALIZED_PNL"  # type: ignore[index]
    assert result.receipt["paper_pnl"]["gross_realized_pnl"] == "35"  # type: ignore[index]


def test_oversized_finite_pnl_is_terminal_unavailable_not_pre_mutation_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    close_call = build_close_order_call(
        bootstrap_plan.open_permit,
        bootstrap_plan.close_permit,
    )
    open_fill = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="1.25",
        filled_at="2026-08-29T19:59:50Z",
    )
    close_fill = order(
        order_id="broker-close-456",
        client_order_id=close_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="-1E+200",
        filled_at="2026-08-29T19:59:58Z",
    )
    session = RecordingSession(
        [open_fill, open_fill, open_fill, close_fill, close_fill, [], open_fill, close_fill]
    )
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    module = types.ModuleType("test_oversized_pnl_plan")
    module.build_plan = lambda: plan  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(bound_manifest_bytes(plan))
    state_dir = tmp_path / "state"

    exit_code = cli_main(
        [
            "run-scheduled-event",
            "--manifest",
            str(manifest_path),
            "--state-dir",
            str(state_dir),
            "--host-plan",
            "test_oversized_pnl_plan:build_plan",
        ],
        clock=lambda: NOW,
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["disposition"] == "EXECUTED_TO_TERMINAL"
    assert output["lifecycle"] == "CLOSED_FLAT"
    assert output["broker_mutation"] == "BOUNDED_PAPER_PIPELINE"
    assert output["receipt"]["paper_pnl"] == {
        "classification": "PAPER_PNL_UNAVAILABLE",
        "gross_realized_pnl": None,
        "broker_fees": None,
        "net_realized_pnl": None,
        "open_filled_at": None,
        "close_filled_at": None,
        "unavailable_reason": "paper P&L decimal text is out of bounds",
    }
    durable_state = json.loads(
        FileScheduledEventStore(state_dir).state_path(plan.open_permit.event_run_id).read_bytes()
    )
    assert durable_state["lifecycle"] == "CLOSED_FLAT"
    assert durable_state["receipt"] == output["receipt"]


def test_post_pipeline_invalid_receipt_stops_durable_manual_without_echoing_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = paper_plan(RecordingSession())
    attacker_value = "broker-account-SENSITIVE"
    receipt: dict[str, object] = {
        "schema": "ringdown.paper_receipt_bundle",
        "schema_version": 1,
        "run_mode": "PAPER",
        "data_class": "INDICATIVE_DATA",
        "claims": ["PAPER_OPERATIONAL_OBSERVATION", "NOT_ALPHA_EVIDENCE"],
        "event_run_id": plan.open_permit.event_run_id,
        "open_permit_id": plan.open_permit.permit_id,
        "close_permit_id": plan.close_permit.permit_id,
        "capability_sha256": plan.prepared.observation.capability_sha256,
        "open_request_sha256": attacker_value,
        "close_request_sha256": None,
        "open_order_sha256": "b" * 64,
        "close_order_sha256": None,
        "lifecycle_outcome": "CANCELED_FLAT",
        "final_flat_observed_at": NOW.isoformat(),
        "paper_pnl": {
            "classification": "ZERO_NO_FILL",
            "gross_realized_pnl": "0",
            "broker_fees": None,
            "net_realized_pnl": None,
            "open_filled_at": None,
            "close_filled_at": None,
            "unavailable_reason": None,
        },
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    class InvalidBundle:
        lifecycle_outcome = "CANCELED_FLAT"
        final_flat_observed_at = NOW

        def to_json_bytes(self) -> bytes:
            return json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")

    pipeline_called = False

    async def fake_run_paper_demo(**_: object) -> InvalidBundle:
        nonlocal pipeline_called
        pipeline_called = True
        return InvalidBundle()

    monkeypatch.setattr(scheduled_module, "run_paper_demo", fake_run_paper_demo)
    module = types.ModuleType("test_invalid_terminal_receipt_plan")
    module.build_plan = lambda: plan  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(bound_manifest_bytes(plan))
    state_dir = tmp_path / "state"

    exit_code = cli_main(
        [
            "run-scheduled-event",
            "--manifest",
            str(manifest_path),
            "--state-dir",
            str(state_dir),
            "--host-plan",
            "test_invalid_terminal_receipt_plan:build_plan",
        ],
        clock=lambda: NOW,
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert pipeline_called is True
    assert exit_code == 3
    assert output["disposition"] == "MANUAL_RECONCILIATION_REQUIRED"
    assert output["lifecycle"] == "MANUAL_RECONCILIATION"
    assert output["error_code"] == "TERMINAL_RECEIPT_INVALID"
    assert output["broker_mutation"] == "NO_FURTHER_MUTATION"
    assert attacker_value not in output_text
    state_path = FileScheduledEventStore(state_dir).state_path(plan.open_permit.event_run_id)
    durable_state = json.loads(state_path.read_bytes())
    assert durable_state["lifecycle"] == "MANUAL_RECONCILIATION"
    assert durable_state["failure_code"] == "TERMINAL_RECEIPT_INVALID"
    assert durable_state["receipt"] is None
    assert attacker_value not in state_path.read_text(encoding="utf-8")


def test_installed_cli_exposes_one_shot_dry_run_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = RecordingSession()
    plan = paper_plan(session)
    module = types.ModuleType("test_operator_scheduled_plan")
    module.build_plan = lambda: plan  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(bound_manifest_bytes(plan))
    state_dir = tmp_path / "state"

    exit_code = cli_main(
        [
            "run-scheduled-event",
            "--manifest",
            str(manifest_path),
            "--state-dir",
            str(state_dir),
            "--host-plan",
            "test_operator_scheduled_plan:build_plan",
            "--dry-run",
        ],
        clock=lambda: NOW,
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["disposition"] == "DRY_RUN_VALIDATED"
    assert output["event_run_id"] == plan.open_permit.event_run_id
    assert output["broker_mutation"] == "NOT_ATTEMPTED"
    assert session.calls == []
    assert not state_dir.exists()


def test_armed_command_rejects_while_another_one_shot_holds_the_runtime_lock(
    tmp_path: Path,
) -> None:
    session = RecordingSession()
    plan = paper_plan(session)
    store = FileScheduledEventStore(tmp_path / "state")
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("locked invocation must fail before plan creation")

    with store.run_lock(), pytest.raises(ScheduledEventOverlap, match="already running"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=bound_manifest_bytes(plan),
                state_dir=store.root,
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW,
            )
        )

    assert plan_factory_called is False
    assert session.calls == []


def test_permit_expiry_boundary_rejects_before_session_or_state_record(tmp_path: Path) -> None:
    session = RecordingSession()
    opening = open_permit()
    expiring = replace(opening, permit_id="UNBOUND", expires_at=NOW)
    expiring = replace(expiring, permit_id=debit_vertical_permit_id(expiring))
    closing = close_permit(expiring)
    plan = PaperDemoPlan(
        prepared=paper_plan(session).prepared,
        open_permit=expiring,
        close_permit=closing,
    )
    state_dir = tmp_path / "state"

    with pytest.raises(ScheduledManifestRejected, match="expired"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=bound_manifest_bytes(plan),
                state_dir=state_dir,
                plan_factory=lambda: plan,
                dry_run=True,
                clock=lambda: NOW,
            )
        )

    assert session.calls == []
    assert not FileScheduledEventStore(state_dir).state_path(expiring.event_run_id).exists()


def test_state_store_rejects_invalid_lifecycle_before_atomic_replace(tmp_path: Path) -> None:
    plan = paper_plan(RecordingSession())
    manifest = ScheduledEventManifest.from_json_bytes(bound_manifest_bytes(plan))
    store = FileScheduledEventStore(tmp_path / "state")

    with pytest.raises(ScheduledManifestRejected, match="lifecycle"):
        store.write(
            manifest=manifest,
            lifecycle="OPEN_FILLED_ASSUMED",
            updated_at=NOW,
            receipt=None,
        )

    assert not store.state_path(manifest.event_run_id).exists()
    assert not list(store.root.glob("*.tmp"))


def test_terminal_noop_rejects_tampered_receipt_even_with_rehashed_state(tmp_path: Path) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    submitted = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="canceled",
        filled_qty="0",
    )
    session = RecordingSession([submitted, submitted, submitted, [], submitted])
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    raw_manifest = bound_manifest_bytes(plan)
    state_dir = tmp_path / "state"
    asyncio.run(
        run_scheduled_event_command(
            manifest_bytes=raw_manifest,
            state_dir=state_dir,
            plan_factory=lambda: plan,
            clock=lambda: NOW,
        )
    )
    state_path = FileScheduledEventStore(state_dir).state_path(plan.open_permit.event_run_id)
    state = json.loads(state_path.read_bytes())
    state["receipt"]["receipt_sha256"] = "0" * 64
    del state["state_sha256"]
    state["state_sha256"] = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    state_path.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("tampered terminal state must fail before plan creation")

    with pytest.raises(ScheduledManualReconciliationRequired, match="state integrity"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=raw_manifest,
                state_dir=state_dir,
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW + timedelta(minutes=5),
            )
        )

    assert plan_factory_called is False


@pytest.mark.parametrize(
    "receipt_field",
    [
        "schema_version.boolean",
        "open_request_sha256",
        "close_request_sha256",
        "open_order_sha256",
        "close_order_sha256",
        "final_flat_observed_at.noncanonical",
        "paper_pnl.gross_realized_pnl",
        "paper_pnl.gross_realized_pnl_noncanonical",
        "paper_pnl.broker_fees",
        "paper_pnl.net_realized_pnl",
        "paper_pnl.open_filled_at",
        "paper_pnl.close_filled_at",
        "paper_pnl.unavailable_reason",
        "paper_pnl.unavailable_reason_allowlist",
        "paper_pnl.malformed_nested_value",
        "paper_pnl.extra_nested_content",
    ],
)
def test_terminal_noop_rejects_rehashed_malformed_receipt_and_persists_manual_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    receipt_field: str,
) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    submitted = order(
        order_id="broker-open-123",
        client_order_id=open_call.client_order_id,
        status="canceled",
        filled_qty="0",
    )
    session = RecordingSession([submitted, submitted, submitted, [], submitted])
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    raw_manifest = bound_manifest_bytes(plan)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(raw_manifest)
    state_dir = tmp_path / "state"
    asyncio.run(
        run_scheduled_event_command(
            manifest_bytes=raw_manifest,
            state_dir=state_dir,
            plan_factory=lambda: plan,
            clock=lambda: NOW,
        )
    )
    state_path = FileScheduledEventStore(state_dir).state_path(plan.open_permit.event_run_id)
    state = json.loads(state_path.read_bytes())
    attacker_value = "broker-account-SENSITIVE"
    receipt = state["receipt"]
    if receipt_field == "schema_version.boolean":
        receipt["schema_version"] = True
    elif receipt_field == "final_flat_observed_at.noncanonical":
        receipt["final_flat_observed_at"] = "2026-08-29T20:00:00Z"
    elif receipt_field == "paper_pnl.gross_realized_pnl_noncanonical":
        receipt["paper_pnl"]["gross_realized_pnl"] = "00"
    elif receipt_field == "paper_pnl.unavailable_reason_allowlist":
        receipt["paper_pnl"] = {
            "classification": "PAPER_PNL_UNAVAILABLE",
            "gross_realized_pnl": None,
            "broker_fees": None,
            "net_realized_pnl": None,
            "open_filled_at": None,
            "close_filled_at": None,
            "unavailable_reason": attacker_value,
        }
    elif receipt_field == "paper_pnl.malformed_nested_value":
        receipt["paper_pnl"]["gross_realized_pnl"] = {"account_id": attacker_value}
    elif receipt_field == "paper_pnl.extra_nested_content":
        receipt["paper_pnl"]["raw_broker"] = {"account_id": attacker_value}
    elif receipt_field.startswith("paper_pnl."):
        receipt["paper_pnl"][receipt_field.removeprefix("paper_pnl.")] = attacker_value
    else:
        receipt[receipt_field] = attacker_value
    del receipt["receipt_sha256"]
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    del state["state_sha256"]
    state["state_sha256"] = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    state_path.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    exit_code = cli_main(
        [
            "run-scheduled-event",
            "--manifest",
            str(manifest_path),
            "--state-dir",
            str(state_dir),
            "--host-plan",
            "must_not_import:build_plan",
        ],
        clock=lambda: NOW + timedelta(minutes=5),
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert exit_code == 3
    assert output["disposition"] == "MANUAL_RECONCILIATION_REQUIRED"
    assert output["lifecycle"] == "MANUAL_RECONCILIATION"
    assert output["error_code"] == "DURABLE_STATE_INVALID"
    assert output["broker_mutation"] == "NO_FURTHER_MUTATION"
    assert attacker_value not in output_text
    durable_state = json.loads(state_path.read_bytes())
    assert durable_state["lifecycle"] == "MANUAL_RECONCILIATION"
    assert durable_state["failure_code"] == "DURABLE_STATE_INVALID"
    assert durable_state["receipt"] is None
    assert attacker_value not in state_path.read_text(encoding="utf-8")


def test_cli_rejection_is_sanitized_and_never_loads_host_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    rejected_manifest = manifest_bytes(run_mode="LIVE")
    manifest_path.write_bytes(rejected_manifest)
    state_dir = tmp_path / "state"

    exit_code = cli_main(
        [
            "run-scheduled-event",
            "--manifest",
            str(manifest_path),
            "--state-dir",
            str(state_dir),
            "--host-plan",
            "must_not_import:build_plan",
        ],
        clock=lambda: NOW,
    )

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "ringdown.scheduled_run_error"
    assert output["disposition"] == "REJECTED_BEFORE_MUTATION"
    assert output["lifecycle"] == "REJECTED"
    assert output["error_code"] == "MANIFEST_OR_STATE_REJECTED"
    assert output["broker_mutation"] == "NOT_ATTEMPTED"
    assert output["event_run_id"] is None
    assert output["manifest_sha256"] == hashlib.sha256(rejected_manifest).hexdigest()
    assert not state_dir.exists()


def test_cli_manual_reconciliation_output_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    partial = order(
        order_id="broker-open-SENSITIVE",
        client_order_id=open_call.client_order_id,
        status="partially_filled",
        filled_qty="0.5",
    )
    session = RecordingSession([partial, partial, partial])
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    module = types.ModuleType("test_operator_manual_plan")
    module.build_plan = lambda: plan  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(bound_manifest_bytes(plan))
    state_dir = tmp_path / "state"

    exit_code = cli_main(
        [
            "run-scheduled-event",
            "--manifest",
            str(manifest_path),
            "--state-dir",
            str(state_dir),
            "--host-plan",
            "test_operator_manual_plan:build_plan",
        ],
        clock=lambda: NOW,
    )

    assert exit_code == 3
    rendered = capsys.readouterr().out
    output = json.loads(rendered)
    assert output["disposition"] == "MANUAL_RECONCILIATION_REQUIRED"
    assert output["lifecycle"] == "MANUAL_RECONCILIATION"
    assert output["error_code"] == "AMBIGUOUS_OR_PARTIAL_BROKER_STATE"
    assert output["broker_mutation"] == "NO_FURTHER_MUTATION"
    assert output["event_run_id"] == plan.open_permit.event_run_id
    assert "SENSITIVE" not in rendered
    assert "0.5" not in rendered


def test_atomic_replace_failure_preserves_previous_restart_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = paper_plan(RecordingSession())
    manifest = ScheduledEventManifest.from_json_bytes(bound_manifest_bytes(plan))
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=manifest,
        lifecycle="RECONCILING",
        updated_at=NOW - timedelta(seconds=1),
        receipt=None,
    )
    state_path = store.state_path(manifest.event_run_id)
    previous = state_path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("sabotaged atomic replacement")

    monkeypatch.setattr(scheduled_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="sabotaged"):
        store.write(
            manifest=manifest,
            lifecycle="MANUAL_RECONCILIATION",
            updated_at=NOW,
            receipt=None,
            failure_code="AMBIGUOUS_OR_PARTIAL_BROKER_STATE",
        )

    assert state_path.read_bytes() == previous
    assert json.loads(previous)["lifecycle"] == "RECONCILING"
    assert not list(store.root.glob("*.tmp"))


@pytest.mark.parametrize(
    "identity_change",
    [
        {"open_permit_id": "different-opening-permit"},
        {"close_permit_id": "different-closing-permit"},
        {"capability_sha256": "f" * 64},
    ],
)
def test_manifest_identity_mismatch_stops_before_session_and_state_record(
    tmp_path: Path,
    identity_change: dict[str, object],
) -> None:
    session = RecordingSession()
    plan = paper_plan(session)
    state_dir = tmp_path / "state"

    with pytest.raises(ScheduledManifestRejected, match="bind"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=bound_manifest_bytes(plan, **identity_change),
                state_dir=state_dir,
                plan_factory=lambda: plan,
                dry_run=True,
                clock=lambda: NOW,
            )
        )

    assert session.calls == []
    assert not FileScheduledEventStore(state_dir).state_path(plan.open_permit.event_run_id).exists()


def test_duplicate_manifest_field_fails_strict_json_before_host_plan(tmp_path: Path) -> None:
    duplicate = manifest_bytes().replace(
        b'"run_mode":"PAPER"',
        b'"run_mode":"PAPER","run_mode":"PAPER"',
    )
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("duplicate manifest must fail before plan creation")

    with pytest.raises(ScheduledManifestRejected, match="strict JSON"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=duplicate,
                state_dir=tmp_path / "state",
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW,
            )
        )

    assert plan_factory_called is False


def test_non_standard_json_constant_fails_before_host_plan(tmp_path: Path) -> None:
    non_standard = manifest_bytes().replace(b'"schema_version":1', b'"schema_version":NaN')
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("non-standard JSON must fail before plan creation")

    with pytest.raises(ScheduledManifestRejected, match="strict JSON"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=non_standard,
                state_dir=tmp_path / "state",
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW,
            )
        )

    assert plan_factory_called is False


def test_expired_reconciling_restart_persists_manual_stop_without_host_plan(
    tmp_path: Path,
) -> None:
    session = RecordingSession()
    plan = paper_plan(session)
    raw_manifest = bound_manifest_bytes(plan)
    manifest = ScheduledEventManifest.from_json_bytes(raw_manifest)
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=manifest,
        lifecycle="RECONCILING",
        updated_at=NOW,
        receipt=None,
    )
    plan_factory_called = False

    def forbidden_plan_factory() -> object:
        nonlocal plan_factory_called
        plan_factory_called = True
        raise AssertionError("expired restart must stop before host-plan creation")

    with pytest.raises(ScheduledManualReconciliationRequired, match="due window expired"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=raw_manifest,
                state_dir=store.root,
                plan_factory=forbidden_plan_factory,
                clock=lambda: NOW + timedelta(minutes=2),
            )
        )

    state = store.read(manifest.event_run_id)
    assert state is not None
    assert state["lifecycle"] == "MANUAL_RECONCILIATION"
    assert state["failure_code"] == "DUE_WINDOW_EXPIRED_DURING_RECONCILIATION"
    assert plan_factory_called is False
    assert session.calls == []


def test_expired_reconciling_dry_run_reports_manual_without_state_mutation(
    tmp_path: Path,
) -> None:
    plan = paper_plan(RecordingSession())
    raw_manifest = bound_manifest_bytes(plan)
    manifest = ScheduledEventManifest.from_json_bytes(raw_manifest)
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=manifest,
        lifecycle="RECONCILING",
        updated_at=NOW,
        receipt=None,
    )
    before = store.state_path(manifest.event_run_id).read_bytes()

    with pytest.raises(ScheduledManualReconciliationRequired, match="due window expired"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=raw_manifest,
                state_dir=store.root,
                plan_factory=lambda: plan,
                dry_run=True,
                clock=lambda: NOW + timedelta(minutes=2),
            )
        )

    assert store.state_path(manifest.event_run_id).read_bytes() == before
    state = store.read(manifest.event_run_id)
    assert state is not None
    assert state["lifecycle"] == "RECONCILING"


def test_expired_permit_on_reconciling_restart_persists_manual_stop_before_tool_use(
    tmp_path: Path,
) -> None:
    session = RecordingSession()
    opening = open_permit()
    expiring = replace(opening, permit_id="UNBOUND", expires_at=NOW)
    expiring = replace(expiring, permit_id=debit_vertical_permit_id(expiring))
    plan = PaperDemoPlan(
        prepared=paper_plan(session).prepared,
        open_permit=expiring,
        close_permit=close_permit(expiring),
    )
    raw_manifest = bound_manifest_bytes(plan)
    manifest = ScheduledEventManifest.from_json_bytes(raw_manifest)
    store = FileScheduledEventStore(tmp_path / "state")
    store.write(
        manifest=manifest,
        lifecycle="RECONCILING",
        updated_at=NOW - timedelta(seconds=1),
        receipt=None,
    )

    with pytest.raises(ScheduledManualReconciliationRequired, match="restart plan"):
        asyncio.run(
            run_scheduled_event_command(
                manifest_bytes=raw_manifest,
                state_dir=store.root,
                plan_factory=lambda: plan,
                clock=lambda: NOW,
            )
        )

    state = store.read(manifest.event_run_id)
    assert state is not None
    assert state["lifecycle"] == "MANUAL_RECONCILIATION"
    assert state["failure_code"] == "RESTART_PLAN_INVALID_OR_EXPIRED"
    assert session.calls == []


@pytest.mark.parametrize(
    "host_plan",
    [
        "missing-colon",
        ":build_plan",
        "module_that_does_not_exist_for_scheduled_test:build_plan",
    ],
)
def test_cli_invalid_host_plan_selector_is_sanitized_pre_mutation_rejection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    host_plan: str,
) -> None:
    plan = paper_plan(RecordingSession())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(bound_manifest_bytes(plan))
    state_dir = tmp_path / "state"

    exit_code = cli_main(
        [
            "run-scheduled-event",
            "--manifest",
            str(manifest_path),
            "--state-dir",
            str(state_dir),
            "--host-plan",
            host_plan,
            "--dry-run",
        ],
        clock=lambda: NOW,
    )

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["disposition"] == "REJECTED_BEFORE_MUTATION"
    assert output["error_code"] == "MANIFEST_OR_STATE_REJECTED"
    assert output["broker_mutation"] == "NOT_ATTEMPTED"
    assert not state_dir.exists()


def test_cli_missing_or_noncallable_host_plan_attribute_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = paper_plan(RecordingSession())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(bound_manifest_bytes(plan))
    module = types.ModuleType("test_invalid_host_plan_module")
    module.not_callable = object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)

    for index, host_plan in enumerate(
        ("test_invalid_host_plan_module:missing", "test_invalid_host_plan_module:not_callable")
    ):
        state_dir = tmp_path / f"state-{index}"
        assert (
            cli_main(
                [
                    "run-scheduled-event",
                    "--manifest",
                    str(manifest_path),
                    "--state-dir",
                    str(state_dir),
                    "--host-plan",
                    host_plan,
                    "--dry-run",
                ],
                clock=lambda: NOW,
            )
            == 2
        )
        output = json.loads(capsys.readouterr().out)
        assert output["disposition"] == "REJECTED_BEFORE_MUTATION"
        assert output["broker_mutation"] == "NOT_ATTEMPTED"
        assert not state_dir.exists()


def _fixture_artifact(filename: str) -> dict[str, object]:
    envelope = json.loads((CONTRACT_FIXTURES / filename).read_bytes())
    assert envelope["fixture_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert envelope["limitations"] == [
        "NOT_HISTORICAL_DATA",
        "NOT_ALPHA_EVIDENCE",
        "NO_BROKER_EXECUTION",
    ]
    artifact = envelope["artifact"]
    assert isinstance(artifact, dict)
    return artifact


def test_terminal_flat_contract_fixture_matches_real_cli_serialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    close_call = build_close_order_call(
        bootstrap_plan.open_permit,
        bootstrap_plan.close_permit,
    )
    open_fill = order(
        order_id="broker-open-fixture",
        client_order_id=open_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="1.25",
        filled_at="2026-08-29T19:59:50Z",
    )
    close_fill = order(
        order_id="broker-close-fixture",
        client_order_id=close_call.client_order_id,
        status="filled",
        filled_qty="1",
        filled_avg_price="-1.60",
        filled_at="2026-08-29T19:59:58Z",
    )
    session = RecordingSession(
        [open_fill, open_fill, open_fill, close_fill, close_fill, [], open_fill, close_fill]
    )
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    module = types.ModuleType("test_terminal_fixture_plan")
    module.build_plan = lambda: plan  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(bound_manifest_bytes(plan))

    assert (
        cli_main(
            [
                "run-scheduled-event",
                "--manifest",
                str(manifest_path),
                "--state-dir",
                str(tmp_path / "state"),
                "--host-plan",
                "test_terminal_fixture_plan:build_plan",
            ],
            clock=lambda: NOW,
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == _fixture_artifact(
        "scheduled_terminal_flat_v1.json"
    )


def test_rejected_contract_fixture_matches_real_cli_serialization(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(manifest_bytes(run_mode="LIVE"))

    assert (
        cli_main(
            [
                "run-scheduled-event",
                "--manifest",
                str(manifest_path),
                "--state-dir",
                str(tmp_path / "state"),
                "--host-plan",
                "must_not_import:build_plan",
            ],
            clock=lambda: NOW,
        )
        == 2
    )

    assert json.loads(capsys.readouterr().out) == _fixture_artifact(
        "scheduled_rejected_before_mutation_v1.json"
    )


def test_manual_contract_fixture_matches_real_cli_serialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap_plan = paper_plan(RecordingSession())
    open_call = build_open_order_call(bootstrap_plan.open_permit)
    partial = order(
        order_id="broker-open-fixture",
        client_order_id=open_call.client_order_id,
        status="partially_filled",
        filled_qty="0.5",
    )
    session = RecordingSession([partial, partial, partial])
    plan = PaperDemoPlan(
        prepared=prepared_session(session),
        open_permit=bootstrap_plan.open_permit,
        close_permit=bootstrap_plan.close_permit,
    )
    module = types.ModuleType("test_manual_fixture_plan")
    module.build_plan = lambda: plan  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(bound_manifest_bytes(plan))

    assert (
        cli_main(
            [
                "run-scheduled-event",
                "--manifest",
                str(manifest_path),
                "--state-dir",
                str(tmp_path / "state"),
                "--host-plan",
                "test_manual_fixture_plan:build_plan",
            ],
            clock=lambda: NOW,
        )
        == 3
    )

    assert json.loads(capsys.readouterr().out) == _fixture_artifact(
        "scheduled_manual_reconciliation_v1.json"
    )
