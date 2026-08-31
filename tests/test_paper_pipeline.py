"""Vertical contract tests for the fail-closed PAPER application service.

All provider and broker boundaries below are deterministic fakes.  The tests
never obtain credentials, call a live provider, or create a real PAPER order.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ringdown_market.application import PaperStrategyApplication
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
from ringdown_market.sourcedata import CaptureConfiguration
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
    load_fixture,
)
from ringdown_market.strategy.contracts import sha256_bytes
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
