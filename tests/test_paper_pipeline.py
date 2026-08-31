"""Vertical contract tests for the fail-closed PAPER application service.

All provider and broker boundaries below are deterministic fakes.  The tests
never obtain credentials, call a live provider, or create a real PAPER order.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ringdown_market.application import PaperPipelineRejected, PaperStrategyApplication
from ringdown_market.contracts.execution_policy import ALPACA_MCP_PROTOCOL_SHA256
from ringdown_market.execution.expression import (
    EXECUTABLE_DATA,
    BorrowLocateEvidence,
    ExpressionKind,
    ExpressionMarketSnapshot,
    FeedIdentity,
    OptionContractObservation,
    PackageObservation,
    PromotedExpressionPolicy,
    ShareObservation,
    TwoSidedQuote,
    compiled_expression_sha256,
)
from ringdown_market.execution.host_mcp import (
    ACCOUNT_TOOL,
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
)
from ringdown_market.execution.models import debit_vertical_permit_id
from ringdown_market.lifecycle import (
    MULTI_LEG_ORDER_CLASS,
    PAPER_ACCOUNT_CLASS,
    BrokerOptionLeg,
    BrokerOrderRequest,
    LifecycleClocks,
    LifecycleReason,
    LifecycleRejected,
    LifecycleState,
    issue_close_permit,
    lifecycle_clocks_sha256,
)
from ringdown_market.risk import (
    ControlState,
    PassportEventType,
    RiskKernel,
    RiskLedger,
    RiskPolicy,
    risk_policy_sha256,
    verify_passport,
)
from ringdown_market.risk.snapshots import AccountSnapshot, OrderSnapshot, PositionSnapshot
from ringdown_market.sourcedata import CaptureConfiguration, compiled_strategy_input
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
    load_fixture,
)
from ringdown_market.strategy.contracts import (
    candidate_manifest_bytes,
    feature_receipt_bytes,
    parse_candidate_manifest,
    parse_feature_receipt,
    parse_strategy_snapshot,
    sha256_bytes,
    strategy_snapshot_bytes,
)
from ringdown_market.strategy.engine import decision_trace_payload
from ringdown_market.strategy.reasoner import (
    SYNTHETIC_ROUTE_IDENTITY,
    DeterministicFakeReasoner,
    ReasonerRouteRequest,
    ReasonerRouteResult,
)

CLOCK = datetime(2026, 9, 11, 13, 36, 5, tzinfo=UTC)
EXPIRY = date(2026, 9, 18)
HASH = sha256_bytes(b"paper-only-application-test")

EQUITY_FEED = FeedIdentity(
    "SYNTHETIC_SIP_EQUITY_FEED", "read_only_equity_quote", "equity_quote.v1", "1"
)
OPTION_FEED = FeedIdentity(
    "SYNTHETIC_OPTION_SNAPSHOT_FEED", "read_only_option_chain", "option_chain_snapshot.v1", "1"
)
PACKAGE_FEED = FeedIdentity(
    "SYNTHETIC_PACKAGE_FEED", "read_only_package_quote", "package_quote.v1", "1"
)


class FakeHostReasoner:
    """Host-owned test route passed through the application's route boundary."""

    def __init__(self) -> None:
        self.calls: list[ReasonerRouteRequest] = []
        self._route = DeterministicFakeReasoner()

    def route_bounded_reasoner(self, request: ReasonerRouteRequest) -> ReasonerRouteResult:
        self.calls.append(request)
        return self._route(request)


class FakeTruthSource:
    """Read-only risk-truth fixture with no broker or provider client."""

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            equity=Decimal("100000.00"),
            buying_power=Decimal("100000.00"),
            currency="USD",
            observed_at=CLOCK - timedelta(seconds=1),
        )

    def positions(self) -> tuple[PositionSnapshot, ...]:
        return ()

    def orders(self) -> tuple[OrderSnapshot, ...]:
        return ()

    def broker_clock(self) -> datetime:
        return CLOCK


class FakeHostMcp:
    """Host-owned MCP fake; it records only normalized non-secret arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self._client_order_id: str | None = None
        self._open_legs: list[Mapping[str, object]] = []
        self._orders: dict[str, Mapping[str, object]] = {}
        self._positions_open = False
        self.ambiguous_open = False

    async def list_tools(self) -> tuple[str, ...]:
        return (
            ACCOUNT_TOOL,
            OPEN_TOOL,
            READBACK_TOOL,
            ORDER_BY_ID_TOOL,
            CANCEL_TOOL,
            POSITIONS_TOOL,
        )

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        self.calls.append((name, dict(arguments)))
        if name == ACCOUNT_TOOL:
            return {
                "id": "paper-account-1",
                "status": "ACTIVE",
                "trading_blocked": False,
                "account_blocked": False,
                "equity": "100000.00",
                "buying_power": "100000.00",
            }
        if name == OPEN_TOOL:
            client_order_id = arguments.get("client_order_id")
            assert isinstance(client_order_id, str)
            self._client_order_id = client_order_id
            legs = arguments.get("legs")
            assert isinstance(legs, list)
            closing = client_order_id.startswith("close-")
            order_id = "paper-close-order-1" if closing else "paper-order-1"
            order = {
                "id": order_id,
                "client_order_id": client_order_id,
                "status": "filled",
                "filled_qty": "1",
                "limit_price": arguments["limit_price"],
            }
            self._orders[order_id] = order
            if closing:
                self._positions_open = False
            else:
                self._open_legs = list(legs)
                self._positions_open = True
                if self.ambiguous_open:
                    raise TimeoutError("simulated open timeout")
            return order
        if name == READBACK_TOOL:
            client_order_id = arguments.get("client_order_id")
            return next(
                order
                for order in self._orders.values()
                if order["client_order_id"] == client_order_id
            )
        if name == ORDER_BY_ID_TOOL:
            return self._orders[str(arguments["order_id"])]
        if name == POSITIONS_TOOL:
            if not self._positions_open:
                return []
            return [
                {
                    "symbol": leg["symbol"],
                    "qty": "1" if leg["side"] == "buy" else "-1",
                }
                for leg in self._open_legs
            ]
        if name == CANCEL_TOOL:
            return {
                "id": arguments["order_id"],
                "client_order_id": self._client_order_id,
                "status": "canceled",
            }
        raise AssertionError(f"unexpected host MCP tool: {name}")


class OpenMutationGate:
    """Test-only stand-in for the later human approval boundary."""

    def mutation_permitted(self) -> bool:
        return True


def _capture_configuration() -> tuple[CaptureConfiguration, dict[str, object]]:
    fixture = load_fixture()
    capture_at = datetime.fromisoformat(
        str(fixture["capture_at"]).replace("Z", "+00:00")
    ).astimezone(UTC)
    return (
        CaptureConfiguration(
            candidate_manifest_bytes=build_candidate_manifest(fixture),
            event_id=str(fixture["event_id"]),
            capture_at=capture_at,
            market_publisher=str(fixture["market_publisher"]),
            market_entitlement=str(fixture["market_entitlement"]),
            market_redistribution=str(fixture["market_redistribution"]),
        ),
        fixture,
    )


def _quote(bid: str, ask: str) -> TwoSidedQuote:
    return TwoSidedQuote(
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=100,
        ask_size=100,
        observed_at=CLOCK - timedelta(seconds=2),
    )


def _expression_snapshot(decision_sha256: str) -> ExpressionMarketSnapshot:
    long_leg = OptionContractObservation(
        symbol="KR260918C00061000",
        underlying="KR",
        expiry=EXPIRY,
        option_type="CALL",
        strike=Decimal("61"),
        quote=_quote("0.80", "0.84"),
        feed=OPTION_FEED,
        data_class=EXECUTABLE_DATA,
        open_interest=200,
        reported_delta=Decimal("0.45"),
    )
    short_leg = OptionContractObservation(
        symbol="KR260918C00062000",
        underlying="KR",
        expiry=EXPIRY,
        option_type="CALL",
        strike=Decimal("62"),
        quote=_quote("0.46", "0.48"),
        feed=OPTION_FEED,
        data_class=EXECUTABLE_DATA,
        open_interest=200,
        reported_delta=Decimal("0.30"),
    )
    return ExpressionMarketSnapshot(
        snapshot_id="paper-only-application-snapshot",
        underlying="KR",
        observation_clock_at=CLOCK,
        decision_sha256=decision_sha256,
        share=ShareObservation(
            symbol="KR",
            quote=_quote("61.40", "61.44"),
            feed=EQUITY_FEED,
            data_class=EXECUTABLE_DATA,
        ),
        chain=(long_leg, short_leg),
        packages=(
            PackageObservation(
                package_id="KR260918C00061000+KR260918C00062000",
                legs=(long_leg.symbol, short_leg.symbol),
                net_bid=Decimal("0.35"),
                net_ask=Decimal("0.36"),
                size=10,
                observed_at=CLOCK - timedelta(seconds=2),
                feed=PACKAGE_FEED,
                data_class=EXECUTABLE_DATA,
            ),
        ),
        borrow_locate=BorrowLocateEvidence(
            symbol="KR",
            located_quantity=100,
            source="SYNTHETIC_LOCATE_FEED",
            observed_at=CLOCK - timedelta(seconds=2),
            content_sha256=HASH,
        ),
    )


def _expression_policy() -> PromotedExpressionPolicy:
    return PromotedExpressionPolicy(
        policy_id="PROMOTED_EXPRESSION_POLICY_V1",
        version="v1",
        gate_d_report_sha256=HASH,
        expression_kind=ExpressionKind.DEBIT_VERTICAL,
        objective="AFTER_COST_EXPECTED_EDGE_VS_CASH",
        evidence_threshold=Decimal("0"),
        evidence_min_events=1,
        operational_loss_budget=Decimal("500"),
        quote_max_age_ms=5000,
        cross_leg_skew_max_ms=1000,
        spread_max_bps=Decimal("500"),
        min_quote_size=1,
        min_dte=7,
        max_dte=21,
        delta_min=Decimal("0.20"),
        delta_max=Decimal("0.60"),
        width_min=Decimal("0.5"),
        width_max=Decimal("10"),
        liquidity_min_open_interest=50,
    )


def _risk_policy() -> RiskPolicy:
    return RiskPolicy(
        policy_id="PAPER_ACCOUNT_RISK_POLICY_V1",
        version="v1",
        run_mode="PAPER",
        account_capital=Decimal("100000.00"),
        per_event_loss_budget=Decimal("1000.00"),
        aggregate_exposure_limit=Decimal("5000.00"),
        daily_loss_limit=Decimal("2000.00"),
        drawdown_limit=Decimal("5000.00"),
        concentration_limit=Decimal("3000.00"),
        max_entries_per_day=10,
        max_open_expressions=5,
        close_only_equity_threshold=Decimal("90000.00"),
        truth_max_age_seconds=30,
        constants_source_sha256=HASH,
    )


def _clocks(policy_sha256: str) -> LifecycleClocks:
    return LifecycleClocks(
        event_run_id="KR-2026Q2-EARNINGS",
        cohort_id="BMO",
        policy_sha256=policy_sha256,
        source_sha256=HASH,
        observation_window_start_at=datetime(2026, 9, 11, 13, 35, 0, tzinfo=UTC),
        observation_window_end_at=datetime(2026, 9, 11, 13, 35, 30, tzinfo=UTC),
        entry_deadline_at=datetime(2026, 9, 11, 13, 37, 0, tzinfo=UTC),
        time_exit_at=datetime(2026, 9, 11, 14, 0, 0, tzinfo=UTC),
        flattening_deadline_at=datetime(2026, 9, 11, 14, 5, 0, tzinfo=UTC),
    )


def _prepared_host(host: FakeHostMcp):
    factory = HostMcpPaperSessionFactory(
        HostMcpSessionIdentity(HostMcpEnvironment.PAPER), clock=lambda: CLOCK
    )
    return asyncio.run(factory.connect(host))


def _prepared_pipeline(tmp_path):
    """Build one fully prepared but not-yet-opened lifecycle through fakes only."""

    host = FakeHostMcp()
    host_session = _prepared_host(host)
    host.calls.clear()
    current = [CLOCK]
    reasoner = FakeHostReasoner()
    capture, fixture = _capture_configuration()
    risk_policy = _risk_policy()
    ledger = RiskLedger(tmp_path / "risk.sqlite")
    kernel = RiskKernel(risk_policy, ledger, FakeTruthSource())
    assert kernel.startup_reconciliation(now=CLOCK) is ControlState.ACTIVE
    policy_sha256 = risk_policy_sha256(risk_policy)
    service = PaperStrategyApplication(
        reasoner_route=reasoner.route_bounded_reasoner,
        expression_policy=_expression_policy(),
        gate_d_report_sha256=HASH,
        risk_kernel=kernel,
        risk_policy_sha256=policy_sha256,
        execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
        lifecycle_clocks=lambda **_: _clocks(policy_sha256),
        account_id="paper-account-1",
        route_identity=SYNTHETIC_ROUTE_IDENTITY,
    )
    prepared = service.prepare(
        capture_configuration=capture,
        evidence=FixtureEvidenceSource(fixture),
        market=FixtureMarketDataSource(fixture),
        expression_snapshot=_expression_snapshot,
        now=CLOCK,
        decision_started_at=CLOCK - timedelta(seconds=5),
    )
    return service, prepared, host, host_session, ledger, current


def _close_permit(prepared):
    issued_at = datetime(2026, 9, 11, 14, 0, 5, tzinfo=UTC)
    return issue_close_permit(
        open_permit=prepared.permit,
        event_run_id=prepared.permit.event_run_id,
        policy_sha256=prepared.permit.policy_sha256,
        snapshot_sha256=prepared.permit.snapshot_sha256,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=1),
        limit_price=Decimal("-0.20"),
    )


def _request() -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id="open-permit-1-correlation",
        phase="OPEN",
        permit_id="permit-1",
        open_permit_id="permit-1",
        event_run_id="KR-2026Q2-EARNINGS",
        reservation_id="reservation-1",
        correlation_sha256=HASH,
        policy_sha256=HASH,
        snapshot_sha256=HASH,
        account_id="paper-account-1",
        account_class=PAPER_ACCOUNT_CLASS,
        order_class=MULTI_LEG_ORDER_CLASS,
        limit_price=Decimal("0.36"),
        legs=(
            BrokerOptionLeg("KR260918C00061000", 1, "buy", "buy_to_open"),
            BrokerOptionLeg("KR260918C00062000", 1, "sell", "sell_to_open"),
        ),
    )


def test_prepare_binds_capture_to_closed_lifecycle_without_provider_or_broker_mutation(
    tmp_path,
) -> None:
    host = FakeHostMcp()
    host_session = _prepared_host(host)
    host.calls.clear()  # Connection preflight is outside the application run.
    reasoner = FakeHostReasoner()
    capture, fixture = _capture_configuration()
    risk_policy = _risk_policy()
    ledger = RiskLedger(tmp_path / "risk.sqlite")
    kernel = RiskKernel(risk_policy, ledger, FakeTruthSource())
    assert kernel.startup_reconciliation(now=CLOCK) is ControlState.ACTIVE
    policy_sha256 = risk_policy_sha256(risk_policy)
    service = PaperStrategyApplication(
        reasoner_route=reasoner.route_bounded_reasoner,
        expression_policy=_expression_policy(),
        gate_d_report_sha256=HASH,
        risk_kernel=kernel,
        risk_policy_sha256=policy_sha256,
        execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
        lifecycle_clocks=lambda **_: _clocks(policy_sha256),
        account_id="paper-account-1",
        route_identity=SYNTHETIC_ROUTE_IDENTITY,
    )

    result = service.prepare(
        capture_configuration=capture,
        evidence=FixtureEvidenceSource(fixture),
        market=FixtureMarketDataSource(fixture),
        expression_snapshot=_expression_snapshot,
        now=CLOCK,
        decision_started_at=CLOCK - timedelta(seconds=5),
    )

    assert len(reasoner.calls) == 1
    assert result.source_snapshot.snapshot.event_id == result.strategy_input.snapshot.event_id
    assert (
        sha256_bytes(result.engine_outcome.decision_bytes)
        == result.compiled_expression.decision_sha256
    )
    assert result.risk_approval.permit_id == result.permit.permit_id
    assert result.permit.permit_id == debit_vertical_permit_id(result.permit)
    assert result.correlation.snapshot_sha256 == result.compiled_expression.snapshot_sha256
    assert result.correlation.decision_sha256 == result.compiled_expression.decision_sha256
    assert result.correlation.reservation_id == result.risk_approval.reservation_id
    assert (
        ledger.permit_for_event(result.permit.event_run_id)["permit_id"] == result.permit.permit_id
    )
    assert ledger.get_control_state()[0] is ControlState.ACTIVE
    assert host.calls == []

    with pytest.raises(LifecycleRejected) as rejected:
        asyncio.run(
            service.open_host(prepared=result, host_session=host_session, clock=lambda: CLOCK)
        )
    assert rejected.value.reason is LifecycleReason.MUTATION_GATE_CLOSED
    assert host.calls == []


def test_open_and_close_host_join_pipeline_to_flat_hash_linked_passport(tmp_path) -> None:
    """The explicit test gate reaches flatness only through the guarded fake host door."""

    host = FakeHostMcp()
    host_session = _prepared_host(host)
    host.calls.clear()
    current = [CLOCK]
    reasoner = FakeHostReasoner()
    capture, fixture = _capture_configuration()
    risk_policy = _risk_policy()
    ledger = RiskLedger(tmp_path / "risk.sqlite")
    kernel = RiskKernel(risk_policy, ledger, FakeTruthSource())
    assert kernel.startup_reconciliation(now=CLOCK) is ControlState.ACTIVE
    policy_sha256 = risk_policy_sha256(risk_policy)
    service = PaperStrategyApplication(
        reasoner_route=reasoner.route_bounded_reasoner,
        expression_policy=_expression_policy(),
        gate_d_report_sha256=HASH,
        risk_kernel=kernel,
        risk_policy_sha256=policy_sha256,
        execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
        lifecycle_clocks=lambda **_: _clocks(policy_sha256),
        account_id="paper-account-1",
        route_identity=SYNTHETIC_ROUTE_IDENTITY,
    )
    prepared = service.prepare(
        capture_configuration=capture,
        evidence=FixtureEvidenceSource(fixture),
        market=FixtureMarketDataSource(fixture),
        expression_snapshot=_expression_snapshot,
        now=CLOCK,
        decision_started_at=CLOCK - timedelta(seconds=5),
    )

    assert prepared.permit.execution_protocol_sha256 == ALPACA_MCP_PROTOCOL_SHA256
    active = asyncio.run(
        service.open_host(
            prepared=prepared,
            host_session=host_session,
            clock=lambda: current[0],
            mutation_gate=OpenMutationGate(),
        )
    )

    assert len(reasoner.calls) == 1
    assert active.open_state is LifecycleState.OPEN_FILLED
    assert active.open_order_id == "paper-order-1"
    assert active.prepared.permit.permit_id == prepared.risk_approval.permit_id
    assert ledger.permit_for_event(prepared.permit.event_run_id)["state"] == "FILLED"
    assert [name for name, _ in host.calls] == [
        OPEN_TOOL,
        ACCOUNT_TOOL,
        ORDER_BY_ID_TOOL,
        POSITIONS_TOOL,
    ]
    opening = host.calls[0][1]
    assert opening["client_order_id"].startswith(f"open-{prepared.permit.permit_id}-")
    assert all("secret" not in str(arguments).lower() for _, arguments in host.calls)

    current[0] = datetime(2026, 9, 11, 14, 0, 5, tzinfo=UTC)
    close_permit = issue_close_permit(
        open_permit=prepared.permit,
        event_run_id=prepared.permit.event_run_id,
        policy_sha256=prepared.permit.policy_sha256,
        snapshot_sha256=prepared.permit.snapshot_sha256,
        issued_at=current[0],
        expires_at=current[0] + timedelta(minutes=1),
        limit_price=Decimal("-0.20"),
    )
    closed_state, close_order_id = asyncio.run(
        service.close(active=active, close_permit=close_permit)
    )

    assert closed_state is LifecycleState.CLOSED_FLAT
    assert close_order_id == "paper-close-order-1"
    assert ledger.reservation_for_event(prepared.permit.event_run_id)["state"] == "RELEASED"
    close_intent = ledger.lifecycle_intent_for_event_phase(prepared.permit.event_run_id, "CLOSE")
    assert close_intent is not None
    assert close_intent["permit_id"] == close_permit.permit_id
    assert close_intent["state"] == "RECONCILED"
    passport = ledger.passport_events()
    assert verify_passport(passport) == len(passport)
    assert passport[-1]["event_type"] == PassportEventType.RECONCILED.value
    assert passport[-1]["payload"] == {
        "event_id": prepared.permit.event_run_id,
        "permit_id": prepared.permit.permit_id,
        "result": "FLAT",
    }
    assert [name for name, _ in host.calls] == [
        OPEN_TOOL,
        ACCOUNT_TOOL,
        ORDER_BY_ID_TOOL,
        POSITIONS_TOOL,
        OPEN_TOOL,
        ACCOUNT_TOOL,
        ORDER_BY_ID_TOOL,
        POSITIONS_TOOL,
    ]
    closing = host.calls[4][1]
    assert closing["client_order_id"].startswith(f"close-{close_permit.permit_id}-")
    assert [leg["position_intent"] for leg in closing["legs"]] == [
        "sell_to_close",
        "buy_to_close",
    ]
    assert all("secret" not in str(arguments).lower() for _, arguments in host.calls)


def test_application_rejects_a_dynamic_host_capability_as_a_permit_protocol(tmp_path) -> None:
    host = FakeHostMcp()
    host_session = _prepared_host(host)
    risk_policy = _risk_policy()
    ledger = RiskLedger(tmp_path / "risk.sqlite")
    kernel = RiskKernel(risk_policy, ledger, FakeTruthSource())

    with pytest.raises(ValueError, match="frozen official Alpaca MCP protocol"):
        PaperStrategyApplication(
            reasoner_route=FakeHostReasoner().route_bounded_reasoner,
            expression_policy=_expression_policy(),
            gate_d_report_sha256=HASH,
            risk_kernel=kernel,
            risk_policy_sha256=risk_policy_sha256(risk_policy),
            execution_protocol_sha256=host_session.observation.capability_sha256,
            lifecycle_clocks=lambda **_: _clocks(risk_policy_sha256(risk_policy)),
            account_id="paper-account-1",
            route_identity=SYNTHETIC_ROUTE_IDENTITY,
        )


def test_host_mcp_lifecycle_adapter_uses_the_prepared_guarded_door_only() -> None:
    host = FakeHostMcp()
    prepared = _prepared_host(host)
    host.calls.clear()
    broker = prepared.lifecycle_broker(clock=lambda: CLOCK)
    ack = asyncio.run(broker.submit_open(_request()))
    truth = asyncio.run(broker.read_order(ack.order_id))
    account = asyncio.run(broker.read_account())

    assert ack.order_id == "paper-order-1"
    assert truth.client_order_id == ack.client_order_id
    assert truth.filled_qty == 1
    assert account.account_id == "paper-account-1"
    assert [name for name, _ in host.calls] == [OPEN_TOOL, ORDER_BY_ID_TOOL, ACCOUNT_TOOL]
    arguments = host.calls[0][1]
    assert arguments == {
        "qty": "1",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "0.36",
        "client_order_id": "open-permit-1-correlation",
        "order_class": "mleg",
        "legs": [
            {
                "symbol": "KR260918C00061000",
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_open",
            },
            {
                "symbol": "KR260918C00062000",
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_open",
            },
        ],
    }
    assert all("secret" not in str(arguments).lower() for _, arguments in host.calls)


def test_ambiguous_open_is_read_back_once_without_a_mutation_retry() -> None:
    host = FakeHostMcp()
    host.ambiguous_open = True
    prepared = _prepared_host(host)
    host.calls.clear()

    ack = asyncio.run(prepared.lifecycle_broker(clock=lambda: CLOCK).submit_open(_request()))

    assert ack.order_id == "paper-order-1"
    assert [name for name, _ in host.calls] == [OPEN_TOOL, READBACK_TOOL]


def test_open_rejects_forged_lifecycle_clocks_before_host_mutation(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    forged_clocks = replace(
        prepared.lifecycle_clocks,
        source_sha256="f" * 64,
        time_exit_at=datetime(2026, 9, 11, 13, 38, tzinfo=UTC),
        flattening_deadline_at=datetime(2026, 9, 11, 13, 39, tzinfo=UTC),
    )
    forged = replace(
        prepared,
        lifecycle_clocks=forged_clocks,
        lifecycle_clocks_sha256=lifecycle_clocks_sha256(forged_clocks),
    )

    with pytest.raises(PaperPipelineRejected, match=r"prepared\.lifecycle_clocks"):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_open_rejects_forged_full_trace_before_host_mutation(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    forged_trace = dict(prepared.engine_outcome.trace)
    forged_trace.update(
        {
            "schema": "forged.decision_trace",
            "schema_version": 99,
            "ablate_text": True,
            "stages": [{"stage": "FORGED"}],
        }
    )
    forged_outcome = replace(prepared.engine_outcome, trace=forged_trace)
    object.__setattr__(prepared, "engine_outcome", forged_outcome)

    with pytest.raises(PaperPipelineRejected, match=r"prepared\.engine_outcome\.trace"):
        asyncio.run(
            service.open_host(
                prepared=prepared,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_open_rejects_forged_evidence_packet_before_host_mutation(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    forged_packet = replace(prepared.source_snapshot.evidence_packet, refs=(), receipts=())
    forged_source = replace(
        prepared.source_snapshot,
        evidence_packet=forged_packet,
        source_receipts=(),
    )
    forged = replace(prepared, source_snapshot=forged_source)

    with pytest.raises(
        PaperPipelineRejected,
        match=r"prepared\.source_snapshot\.evidence_packet",
    ):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_open_rejects_source_receipt_drift_before_host_mutation(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    source_receipts = list(prepared.source_snapshot.source_receipts)
    source_receipts[0] = replace(source_receipts[0], content_sha256="f" * 64)
    forged = replace(
        prepared,
        source_snapshot=replace(
            prepared.source_snapshot,
            source_receipts=tuple(source_receipts),
        ),
    )

    with pytest.raises(
        PaperPipelineRejected,
        match=r"prepared\.source_snapshot\.source_receipts",
    ):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_open_rejects_rehashed_expression_policy_forgery_before_host_mutation(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    forged_expression = replace(prepared.compiled_expression, policy_sha256="f" * 64)
    forged_expression_sha256 = compiled_expression_sha256(forged_expression)
    forged = replace(
        prepared,
        compiled_expression=forged_expression,
        expression_sha256=forged_expression_sha256,
        correlation=replace(
            prepared.correlation,
            expression_sha256=forged_expression_sha256,
        ),
    )

    with pytest.raises(PaperPipelineRejected, match="expression policy"):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_open_rejects_cross_account_application_replay_before_host_mutation(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    different_account_application = replace(service, account_id="paper-account-2")

    with pytest.raises(PaperPipelineRejected, match="application configuration"):
        asyncio.run(
            different_account_application.open_host(
                prepared=prepared,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


@pytest.mark.parametrize("field", ("clocks", "correlation", "broker", "ledger", "account_id"))
def test_close_rejects_substituted_active_lifecycle_binding_before_host_mutation(
    tmp_path, field: str
) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    active = asyncio.run(
        service.open_host(
            prepared=prepared,
            host_session=host_session,
            clock=lambda: current[0],
            mutation_gate=OpenMutationGate(),
        )
    )
    host_calls_before_close = list(host.calls)
    if field == "clocks":
        forged = replace(active.lifecycle.clocks, source_sha256="f" * 64)
    elif field == "correlation":
        forged = replace(active.lifecycle.correlation, expression_sha256="f" * 64)
    elif field == "account_id":
        forged = "paper-account-2"
    else:
        forged = object()
    object.__setattr__(active.lifecycle, field, forged)
    current[0] = datetime(2026, 9, 11, 14, 0, 5, tzinfo=UTC)

    with pytest.raises(PaperPipelineRejected, match="active lifecycle"):
        asyncio.run(service.close(active=active, close_permit=_close_permit(prepared)))

    assert host.calls == host_calls_before_close


def test_close_still_flattens_when_non_close_critical_expression_evidence_drifts(tmp_path) -> None:
    service, prepared, host, host_session, ledger, current = _prepared_pipeline(tmp_path)
    active = asyncio.run(
        service.open_host(
            prepared=prepared,
            host_session=host_session,
            clock=lambda: current[0],
            mutation_gate=OpenMutationGate(),
        )
    )
    active.prepared.compiled_expression.debit_vertical["limit_price"] = "0.40"
    active.prepared.compiled_expression.debit_vertical["maximum_loss"] = "40.00"
    current[0] = datetime(2026, 9, 11, 14, 0, 5, tzinfo=UTC)

    state, order_id = asyncio.run(
        service.close(active=active, close_permit=_close_permit(prepared))
    )

    assert state is LifecycleState.CLOSED_FLAT
    assert order_id == "paper-close-order-1"
    assert ledger.reservation_for_event(prepared.permit.event_run_id)["state"] == "RELEASED"
    assert [name for name, _ in host.calls][-4:] == [
        OPEN_TOOL,
        ACCOUNT_TOOL,
        ORDER_BY_ID_TOOL,
        POSITIONS_TOOL,
    ]


@pytest.mark.parametrize(
    ("field", "value", "expected_path"),
    (
        ("ablate_text", True, "prepared.engine_outcome.ablate_text"),
        ("route_invoked", False, "prepared.engine_outcome.route_invoked"),
    ),
)
def test_open_rejects_accepted_ablation_or_uninvoked_route_before_host_mutation(
    tmp_path, field: str, value: bool, expected_path: str
) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    values = {
        "route_invoked": prepared.engine_outcome.route_invoked,
        "ablate_text": prepared.engine_outcome.ablate_text,
    }
    values[field] = value
    forged_trace = decision_trace_payload(
        strategy_input=prepared.strategy_input,
        decision=prepared.engine_outcome.decision,
        exchange=prepared.engine_outcome.exchange,
        route_invoked=values["route_invoked"],
        ablate_text=values["ablate_text"],
    )
    forged_outcome = replace(
        prepared.engine_outcome,
        trace=forged_trace,
        route_invoked=values["route_invoked"],
        ablate_text=values["ablate_text"],
    )
    forged = replace(
        prepared,
        engine_outcome=forged_outcome,
        trace_sha256=sha256_bytes(forged_outcome.trace_bytes),
    )

    with pytest.raises(PaperPipelineRejected, match=expected_path):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


@pytest.mark.parametrize(
    "tamper",
    ("schema", "schema_version", "ablate_text", "missing", "extra", "reordered", "stage_field"),
)
def test_open_rejects_each_exact_trace_shape_drift_before_host_mutation(
    tmp_path, tamper: str
) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    trace = json.loads(json.dumps(prepared.engine_outcome.trace))
    if tamper == "schema":
        trace["schema"] = "forged.decision_trace"
    elif tamper == "schema_version":
        trace["schema_version"] = 99
    elif tamper == "ablate_text":
        trace["ablate_text"] = True
    elif tamper == "missing":
        trace["stages"].pop()
    elif tamper == "extra":
        trace["stages"].append({"stage": "EXTRA"})
    elif tamper == "reordered":
        trace["stages"].reverse()
    else:
        trace["stages"][0]["policy_sha256"] = "f" * 64
    forged_outcome = replace(prepared.engine_outcome, trace=trace)
    forged = replace(
        prepared,
        engine_outcome=forged_outcome,
        trace_sha256=sha256_bytes(forged_outcome.trace_bytes),
    )

    with pytest.raises(PaperPipelineRejected, match=r"prepared\.engine_outcome\.trace"):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_open_rejects_well_formed_replayed_source_bytes_at_prepared_strategy_input(
    tmp_path,
) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    manifest = parse_candidate_manifest(prepared.source_snapshot.candidate_manifest_bytes)
    forged_manifest = replace(manifest, manifest_id=f"{manifest.manifest_id}-replayed")
    forged_manifest_bytes = candidate_manifest_bytes(forged_manifest)
    snapshot = parse_strategy_snapshot(prepared.source_snapshot.strategy_snapshot_bytes)
    forged_snapshot = replace(
        snapshot,
        candidate_manifest_sha256=sha256_bytes(forged_manifest_bytes),
    )
    forged_snapshot_bytes = strategy_snapshot_bytes(forged_snapshot)
    receipt = parse_feature_receipt(prepared.source_snapshot.feature_receipt_bytes)
    forged_receipt = replace(
        receipt,
        strategy_snapshot_sha256=sha256_bytes(forged_snapshot_bytes),
    )
    forged_receipt_bytes = feature_receipt_bytes(forged_receipt)
    forged_source = replace(
        prepared.source_snapshot,
        candidate_manifest_bytes=forged_manifest_bytes,
        strategy_snapshot_bytes=forged_snapshot_bytes,
        feature_receipt_bytes=forged_receipt_bytes,
        snapshot=forged_snapshot,
        feature_receipt=forged_receipt,
    )
    assert compiled_strategy_input(forged_source) != prepared.strategy_input
    forged = replace(prepared, source_snapshot=forged_source)

    with pytest.raises(PaperPipelineRejected, match=r"prepared\.strategy_input"):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_open_rejects_rehashed_evidence_receipts_with_stale_packet_digest(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    refs = list(prepared.source_snapshot.evidence_packet.refs)
    receipts = list(prepared.source_snapshot.evidence_packet.receipts)
    refs[0] = replace(refs[0], content_sha256="f" * 64)
    receipts[0] = replace(receipts[0], content_sha256="f" * 64)
    forged_packet = replace(
        prepared.source_snapshot.evidence_packet,
        refs=tuple(refs),
        receipts=tuple(receipts),
    )
    forged_source = replace(
        prepared.source_snapshot,
        evidence_packet=forged_packet,
        source_receipts=tuple(receipts),
    )
    forged = replace(prepared, source_snapshot=forged_source)

    with pytest.raises(
        PaperPipelineRejected,
        match=r"prepared\.source_snapshot\.evidence_packet\.packet_sha256",
    ):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_open_rejects_rehashed_expression_terms_at_exact_permit_binding(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    debit_vertical = dict(prepared.compiled_expression.debit_vertical)
    debit_vertical["limit_price"] = "0.40"
    debit_vertical["maximum_loss"] = "40.00"
    forged_expression = replace(prepared.compiled_expression, debit_vertical=debit_vertical)
    forged_expression_sha256 = compiled_expression_sha256(forged_expression)
    forged = replace(
        prepared,
        compiled_expression=forged_expression,
        expression_sha256=forged_expression_sha256,
        correlation=replace(
            prepared.correlation,
            expression_sha256=forged_expression_sha256,
        ),
    )

    with pytest.raises(PaperPipelineRejected, match=r"prepared\.permit"):
        asyncio.run(
            service.open_host(
                prepared=forged,
                host_session=host_session,
                clock=lambda: current[0],
                mutation_gate=OpenMutationGate(),
            )
        )

    assert host.calls == []


def test_close_rejects_replaced_active_lifecycle_and_keeps_the_open_binding_usable(
    tmp_path,
) -> None:
    service, prepared, host, host_session, ledger, current = _prepared_pipeline(tmp_path)
    active = asyncio.run(
        service.open_host(
            prepared=prepared,
            host_session=host_session,
            clock=lambda: current[0],
            mutation_gate=OpenMutationGate(),
        )
    )
    original_lifecycle = active.lifecycle
    object.__setattr__(active, "lifecycle", replace(original_lifecycle, broker=object()))
    current[0] = datetime(2026, 9, 11, 14, 0, 5, tzinfo=UTC)
    host_calls_before_close = list(host.calls)

    with pytest.raises(PaperPipelineRejected, match=r"active\.close_binding\.lifecycle"):
        asyncio.run(service.close(active=active, close_permit=_close_permit(prepared)))

    assert host.calls == host_calls_before_close
    object.__setattr__(active, "lifecycle", original_lifecycle)
    state, order_id = asyncio.run(
        service.close(active=active, close_permit=_close_permit(prepared))
    )

    assert state is LifecycleState.CLOSED_FLAT
    assert order_id == "paper-close-order-1"
    assert ledger.reservation_for_event(prepared.permit.event_run_id)["state"] == "RELEASED"


def test_opened_lifecycle_field_freezes_execution_bindings_but_keeps_idempotency_sets(
    tmp_path,
) -> None:
    service, prepared, _host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    active = asyncio.run(
        service.open_host(
            prepared=prepared,
            host_session=host_session,
            clock=lambda: current[0],
            mutation_gate=OpenMutationGate(),
        )
    )

    with pytest.raises(FrozenInstanceError):
        active.lifecycle.broker = object()

    active.lifecycle._submitted_close_permits.add("test-only-close-permit")
    assert "test-only-close-permit" in active.lifecycle._submitted_close_permits


def test_close_rejects_coherent_correlation_substitution_before_host_mutation(tmp_path) -> None:
    service, prepared, host, host_session, _ledger, current = _prepared_pipeline(tmp_path)
    active = asyncio.run(
        service.open_host(
            prepared=prepared,
            host_session=host_session,
            clock=lambda: current[0],
            mutation_gate=OpenMutationGate(),
        )
    )
    forged_correlation = replace(active.lifecycle.correlation, expression_sha256="f" * 64)
    object.__setattr__(active.lifecycle, "correlation", forged_correlation)
    object.__setattr__(active.close_binding, "correlation", forged_correlation)
    current[0] = datetime(2026, 9, 11, 14, 0, 5, tzinfo=UTC)
    host_calls_before_close = list(host.calls)

    with pytest.raises(
        PaperPipelineRejected,
        match=r"active\.close_binding permit and correlation",
    ):
        asyncio.run(service.close(active=active, close_permit=_close_permit(prepared)))

    assert host.calls == host_calls_before_close


def test_close_uses_original_binding_after_prepared_evidence_and_application_drift(
    tmp_path,
) -> None:
    service, prepared, _host, host_session, ledger, current = _prepared_pipeline(tmp_path)
    active = asyncio.run(
        service.open_host(
            prepared=prepared,
            host_session=host_session,
            clock=lambda: current[0],
            mutation_gate=OpenMutationGate(),
        )
    )
    object.__setattr__(active.prepared.source_snapshot, "strategy_snapshot_bytes", b"{}")
    active.prepared.engine_outcome.trace["schema"] = "forged.decision_trace"
    active.prepared.compiled_expression.debit_vertical["limit_price"] = "0.40"
    active.prepared.compiled_expression.debit_vertical["maximum_loss"] = "40.00"
    service.account_id = "paper-account-2"
    current[0] = datetime(2026, 9, 11, 14, 0, 5, tzinfo=UTC)

    state, order_id = asyncio.run(
        service.close(active=active, close_permit=_close_permit(prepared))
    )

    assert state is LifecycleState.CLOSED_FLAT
    assert order_id == "paper-close-order-1"
    assert ledger.reservation_for_event(prepared.permit.event_run_id)["state"] == "RELEASED"
    close_intent = ledger.lifecycle_intent_for_event_phase(prepared.permit.event_run_id, "CLOSE")
    assert close_intent is not None
    assert close_intent["account_id"] == "paper-account-1"
