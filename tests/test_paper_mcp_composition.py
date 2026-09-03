"""Issue #90: the production PAPER_MCP composition through the real host runner.

Every test here is the acceptance-mandated fake-response probe: the real
high-level production composition (plan factory, guarded session, lifecycle
MCP broker, real pipeline services, real host runner) driven exclusively by
fake MCP responses and captured fixture bytes.  No network, provider, account,
credential, or broker mutation exists anywhere in this file.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import test_autonomous_host_composition as synth
from ringdown_market.contracts.reasoner_route import load_approved_reasoner_route_v2
from ringdown_market.execution.host_mcp import (
    HostMcpEnvironment,
    HostMcpPaperSessionFactory,
    HostMcpSessionIdentity,
)
from ringdown_market.risk import RiskLedger
from ringdown_market.runtime.autonomous_host import (
    AutonomousHostDisposition,
    AutonomousHostRejected,
    HostExecutionClass,
    run_autonomous_host_command,
    validate_autonomous_host_authority,
)
from ringdown_market.runtime.host_composition import (
    EARNINGS_LANE_V2,
    SyntheticRehearsalRoute,
    rehearsal_direction,
    rehearsal_expression_snapshot,
    rehearsal_lifecycle_clocks,
    rehearsal_timeline,
    rejoin_composition_fixture,
    split_composition_fixture,
    synthetic_promoted_expression_policy,
)
from ringdown_market.runtime.host_fake_broker import SyntheticPaperBroker
from ringdown_market.runtime.paper_mcp_composition import (
    BLOCKED_STATE_FILENAME,
    BlockedStateJournal,
    PaperMcpCompositionRejected,
    PaperMcpFeed,
    PaperMcpFeedEvent,
    PaperMcpHostDoors,
    paper_mcp_plan_factory,
)
from ringdown_market.sourcedata import (
    CaptureConfiguration,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
)
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes
from ringdown_market.strategy.host_route import OpenAiCompatibleReasonerRoute
from ringdown_market.strategy.reasoner import SYNTHETIC_ROUTE_IDENTITY, _cited_evidence_ids

ACCOUNT_PAYLOAD: dict[str, object] = {
    "id": "PA5XSNL1XT43",
    "account_class": "PAPER",
    "status": "ACTIVE",
    "trading_blocked": False,
    "account_blocked": False,
    # Mirrors the frozen synthetic account economics: the deterministic V2
    # allocator admits exactly one contract when unborrowed cash sits in
    # [max_debit, 2*max_debit) for the frozen synthetic package.
    "equity": "100000.00",
    "buying_power": "100000.00",
    "cash": "3.00",
    "currency": "USD",
    "options_enabled": True,
}
ACCOUNT_FINGERPRINT = sha256_bytes(canonical_json_bytes(ACCOUNT_PAYLOAD))
ALL_TOOLS = (
    "cancel_order_by_id",
    "get_account_activities",
    "get_account_info",
    "get_all_positions",
    "get_order_by_client_id",
    "get_order_by_id",
    "get_orders",
    "place_option_order",
)
TEST_ROUTE_KEY = "host-owned-test-key-not-a-real-credential"


class PhaseClock:
    """Injected wall-clock double advancing with the session phase."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class FakeMcpHost:
    """State-machine double of the official Alpaca MCP PAPER host process."""

    def __init__(
        self,
        *,
        account: dict[str, object] | None = None,
        activities: list[dict[str, object]] | None = None,
        open_working: list[dict[str, object]] | None = None,
        open_timeout: bool = False,
        readback_timeout: bool = False,
        on_first_fill=None,
    ) -> None:
        self.account = dict(account or ACCOUNT_PAYLOAD)
        self.activities = list(activities or [])
        self.open_working = list(open_working or [])
        self.open_timeout = open_timeout
        self.readback_timeout = readback_timeout
        self.on_first_fill = on_first_fill
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.orders: dict[str, dict[str, object]] = {}
        self.by_client: dict[str, str] = {}
        self.positions: list[dict[str, object]] = []
        self.place_calls = 0
        self.cancel_calls: list[str] = []
        self._open_timed_out = False
        self._close_phase = False

    def seed_active(self, other: FakeMcpHost) -> None:
        """Continue the broker truth of a prior process (restart semantics)."""

        self.orders = {key: dict(value) for key, value in other.orders.items()}
        self.by_client = dict(other.by_client)
        self.positions = [dict(item) for item in other.positions]
        self.place_calls = other.place_calls

    async def list_tools(self) -> object:
        return list(ALL_TOOLS)

    async def call_tool(self, name: str, arguments) -> object:
        args = dict(arguments)
        self.calls.append((name, args))
        if name == "get_account_info":
            return dict(self.account)
        if name == "get_orders":
            status = args.get("status", "open")
            if status == "all":
                # First "all" read after an opening fill is the close-phase
                # truth refresh; the injected wall clock advances to hard-flat
                # exactly like the production scheduler reaching the boundary.
                if self.positions and not self._close_phase and self.on_first_fill is not None:
                    self._close_phase = True
                    self.on_first_fill()
                return [dict(record) for record in self.orders.values()]
            return [dict(item) for item in self.open_working]
        if name == "get_all_positions":
            return [dict(item) for item in self.positions]
        if name == "place_option_order":
            self.place_calls += 1
            if self.open_timeout and not self._open_timed_out:
                self._open_timed_out = True
                raise TimeoutError("secret transport detail must never leak")
            client_order_id = str(args["client_order_id"])
            order_id = f"mcp-order-{len(self.orders) + 1}"
            legs = list(args.get("legs") or [])
            record = {
                "id": order_id,
                "client_order_id": client_order_id,
                "status": "filled",
                "filled_qty": "1",
                "legs": [{"symbol": str(leg["symbol"])} for leg in legs],
            }
            self.orders[order_id] = record
            self.by_client[client_order_id] = order_id
            intents = [str(leg.get("position_intent", "")) for leg in legs]
            opening = not any(intent.endswith("_to_close") for intent in intents)
            if opening:
                positions = []
                for leg in legs:
                    side = str(leg.get("side", "buy"))
                    ratio = int(str(leg.get("ratio_qty", "1")))
                    qty = int(str(args.get("qty", "1"))) * ratio
                    positions.append(
                        {
                            "asset_class": "us_option",
                            "market_value": "84" if side == "buy" else "-48",
                            "qty": str(qty if side == "buy" else -qty),
                            "symbol": str(leg["symbol"]),
                        }
                    )
                self.positions = sorted(positions, key=lambda item: str(item["symbol"]))
            else:
                self.positions = []
            return dict(record)
        if name == "get_order_by_id":
            record = self.orders.get(str(args["order_id"]))
            if record is None:
                return {"error": "unknown order"}
            return dict(record)
        if name == "get_order_by_client_id":
            if self.readback_timeout:
                raise TimeoutError("private transport detail must never leak")
            order_id = self.by_client.get(str(args["client_order_id"]))
            record = self.orders.get(order_id or "")
            if record is None:
                return {"error": "unknown client order"}
            return dict(record)
        if name == "cancel_order_by_id":
            order_id = str(args["order_id"])
            self.cancel_calls.append(order_id)
            self.open_working = [
                item for item in self.open_working if str(item.get("id")) != order_id
            ]
            return {}
        if name == "get_account_activities":
            return list(self.activities)
        raise AssertionError(f"unexpected MCP tool: {name}")


def _session(tmp_path: Path):
    return synth._authority(tmp_path, SyntheticPaperBroker(), fingerprint=ACCOUNT_FINGERPRINT)


def _fixture_capture() -> tuple[dict[str, object], CaptureConfiguration]:
    fixture = synth._loaded_fixture()
    capture_at = datetime.fromisoformat(str(fixture["capture_at"]).replace("Z", "+00:00"))
    capture = CaptureConfiguration(
        candidate_manifest_bytes=build_candidate_manifest(fixture),
        event_id=str(fixture["event_id"]),
        capture_at=capture_at,
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )
    return fixture, capture


def _joined_input():
    fixture, capture = _fixture_capture()
    probe = compile_strategy_snapshot(
        capture, FixtureEvidenceSource(fixture), FixtureMarketDataSource(fixture)
    )
    return compiled_strategy_input(probe)


def _decision_response_bytes() -> bytes:
    joined = _joined_input()
    direction = rehearsal_direction(joined)
    cited = _cited_evidence_ids(joined)
    market_id = cited[-1] if cited else None
    payload = {
        "contradictions": [],
        "decision": direction.value,
        "evidence_ids": list(cited),
        "strongest_falsifier": (
            None
            if market_id is None
            else {
                "evidence_id": market_id,
                "summary": "The confirmation residual can fade after the cutoff.",
            }
        ),
        "summary": "Host fake probe response for the production composition test.",
        "unknowns": [],
    }
    return canonical_json_bytes(payload)


class FixtureCaptureDoors:
    def sources_for(self, event: PaperMcpFeedEvent):
        fixture = rejoin_composition_fixture(
            event.evidence_manifest_bytes, event.market_window_bytes
        )
        capture = CaptureConfiguration(
            candidate_manifest_bytes=build_candidate_manifest(fixture),
            event_id=str(fixture["event_id"]),
            capture_at=event.capture_at,
            market_publisher=event.market_publisher,
            market_entitlement=event.market_entitlement,
            market_redistribution=event.market_redistribution,
        )
        return capture, FixtureEvidenceSource(fixture), FixtureMarketDataSource(fixture)


class RehearsalExpressionDoor:
    def snapshot_for(self, *, underlying: str, decision_sha256: str, observed_at: datetime):
        return rehearsal_expression_snapshot(
            underlying=underlying,
            decision_sha256=decision_sha256,
            observation_clock_at=observed_at,
        )


def _feed_event() -> PaperMcpFeedEvent:
    fixture = synth._loaded_fixture()
    evidence_bytes, market_bytes = split_composition_fixture(fixture)
    capture_at = datetime.fromisoformat(str(fixture["capture_at"]).replace("Z", "+00:00"))
    return PaperMcpFeedEvent(
        window_id="SCAN_1000_ET",
        candidate_id=EARNINGS_LANE_V2,
        evidence_manifest_bytes=evidence_bytes,
        market_window_bytes=market_bytes,
        capture_at=capture_at,
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )


def _decision_now() -> datetime:
    joined = _joined_input()
    return rehearsal_timeline(joined).authorization_at


def _session_lifecycle_clocks(hard_flat_at: datetime):
    """Exit-plan clock door whose flattening window contains the armed hard-flat.

    A production arm binds its risk envelope so the session hard-flat lies
    inside the promoted exit plan's [time_exit, flattening_deadline] window;
    this door mirrors that binding for the probe session.
    """

    def factory(**kwargs):
        base = rehearsal_lifecycle_clocks(**kwargs)
        return replace(
            base,
            flattening_deadline_at=max(
                base.flattening_deadline_at, hard_flat_at + timedelta(minutes=5)
            ),
        )

    return factory


def _prepared(host: FakeMcpHost, clock: PhaseClock):
    factory = HostMcpPaperSessionFactory(
        HostMcpSessionIdentity(environment=HostMcpEnvironment.PAPER), clock=clock
    )
    return asyncio.run(factory.connect(host))


def _approved_route():
    response = _decision_response_bytes()

    def transport(endpoint: str, payload: dict[str, object]) -> bytes:
        return response

    return OpenAiCompatibleReasonerRoute(
        route=load_approved_reasoner_route_v2(),
        api_key=TEST_ROUTE_KEY,
        transport=transport,
    )


def _doors(
    host: FakeMcpHost,
    ledger: RiskLedger,
    clock: PhaseClock,
    arm,
    *,
    mutation_permitted: bool = True,
    approved_route: object | None = None,
) -> PaperMcpHostDoors:
    prepared = _prepared(host, clock)
    return PaperMcpHostDoors(
        prepared_session=prepared,
        approved_route=(approved_route if approved_route is not None else _approved_route()),  # type: ignore[arg-type]
        # The engine reasoner door for this fake-response probe is the
        # deterministic offline double; the owner-approved direct-Kimi binding
        # above is enforced against the armed session independently, and an
        # armed #68 session wires the approved adapter itself.
        reasoner=SyntheticRehearsalRoute(),
        reasoner_identity=SYNTHETIC_ROUTE_IDENTITY,
        feed=PaperMcpFeed(events=(_feed_event(),)),
        capture_sources=FixtureCaptureDoors(),
        expression_snapshots=RehearsalExpressionDoor(),
        lifecycle_clocks=_session_lifecycle_clocks(arm.hard_flat_at),
        expression_policy=synthetic_promoted_expression_policy(),
        clock=clock,
        ledger=ledger,
        close_limit_price=Decimal("-0.20"),
        account_id="PA5XSNL1XT43",
        mutation_permitted=mutation_permitted,
    )


def _run(authority_input, arm, doors, *, timeline_tail_flat: bool = True):
    timeline = (synth._window_point(arm),)
    if timeline_tail_flat:
        timeline = (*timeline, arm.hard_flat_at)
    return run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=paper_mcp_plan_factory(doors),
        observation_timeline=timeline,
    )


def test_production_probe_accepted_candidate_opens_closes_and_proves_flat(tmp_path: Path) -> None:
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost(on_first_fill=lambda: setattr(clock, "now", arm.hard_flat_at))
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)

    receipt = _run(authority_input, arm, doors)

    assert receipt.execution_class is HostExecutionClass.PAPER_MCP
    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert receipt.terminal_flat_proven is True
    assert receipt.reconciliation_broker_truth_sha256 is not None
    assert receipt.manual_reasons == ()
    assert receipt.disposition_counts["ACTIVE"] == 0
    assert receipt.disposition_counts["TERMINAL_FLAT"] == 1
    assert len(receipt.processed_opportunity_ids) == 1

    payload = json.loads(receipt.to_json_bytes())
    assert payload["execution_class"] == "PAPER_MCP"
    assert payload["run_mode"] == "PAPER"
    assert payload["data_class"] == "PAPER_MCP_HOST_OBSERVATION"
    assert payload["claim_basis"] == "PRODUCTION_COMPOSITION"
    assert "SYNTHETIC_FAKE" not in payload["claims"]
    assert "PAPER_OPERATIONAL_RESULT" in payload["claims"]
    assert "NO_CREDENTIALS_RECORDED" in payload["claims"]

    tool_names = [name for name, _ in host.calls]
    assert tool_names.count("place_option_order") == 2
    assert "cancel_order_by_id" not in tool_names
    assert host.positions == []
    # Durable journals exist and no credential material was recorded.
    sidecar = (authority_input.state_dir / "host_persistence.jsonl").read_text(encoding="utf-8")
    assert "TERMINAL" in sidecar
    assert TEST_ROUTE_KEY not in sidecar
    assert TEST_ROUTE_KEY not in json.dumps(payload)


def test_no_mutation_rehearsal_runs_the_full_path_without_any_order_tool(tmp_path: Path) -> None:
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm, mutation_permitted=False)

    receipt = _run(authority_input, arm, doors)

    tool_names = [name for name, _ in host.calls]
    assert "place_option_order" not in tool_names
    assert "cancel_order_by_id" not in tool_names
    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert receipt.terminal_flat_proven is True
    assert receipt.disposition_counts["REJECTED_BEFORE_MUTATION"] == 1
    assert receipt.manual_reasons == ()
    assert not (authority_input.state_dir / "host_persistence.jsonl").exists()


def test_ambiguous_open_never_retries_and_freezes_manual(tmp_path: Path) -> None:
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost(open_timeout=True, readback_timeout=True)
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)

    receipt = _run(authority_input, arm, doors)

    tool_names = [name for name, _ in host.calls]
    assert tool_names.count("place_option_order") == 1
    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert "UNKNOWN_BROKER_STATE" in receipt.manual_reasons
    for _name, args in host.calls:
        assert TEST_ROUTE_KEY not in json.dumps(args, default=str)


def test_two_consecutive_poison_rows_survive_restart_and_block_the_candidate(
    tmp_path: Path,
) -> None:
    event = _feed_event()
    authority_input, arm = _session(tmp_path)
    authority_input.state_dir.mkdir(parents=True, exist_ok=True)
    journal = BlockedStateJournal(
        authority_input.state_dir / BLOCKED_STATE_FILENAME, retry_budget=2
    )
    poisoned_at = synth._window_point(arm)
    journal.record_failure(event.opportunity_id, "PORT_OUTPUT_INVALID", poisoned_at)
    journal.record_failure(event.opportunity_id, "PORT_OUTPUT_INVALID", poisoned_at)
    assert journal.is_exhausted(event.opportunity_id)

    clock = PhaseClock(_decision_now())
    host = FakeMcpHost()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)

    receipt = _run(authority_input, arm, doors)

    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert "BLOCKED_RETRY_BUDGET_EXHAUSTED" in receipt.manual_reasons
    assert "place_option_order" not in [name for name, _ in host.calls]
    # The exhausted state is durable: a fresh process sees the same block.
    reopened = BlockedStateJournal(authority_input.state_dir / BLOCKED_STATE_FILENAME)
    assert reopened.is_exhausted(event.opportunity_id)


def test_restart_recovers_broker_truth_first_and_closes_without_a_second_open(
    tmp_path: Path,
) -> None:
    authority_input, arm = _session(tmp_path)
    ledger_path = tmp_path / "risk.sqlite3"
    clock_one = PhaseClock(_decision_now())
    host_one = FakeMcpHost(on_first_fill=lambda: setattr(clock_one, "now", arm.hard_flat_at))
    doors_one = _doors(host_one, RiskLedger(ledger_path), clock_one, arm)

    first = _run(authority_input, arm, doors_one, timeline_tail_flat=False)
    assert first.disposition is AutonomousHostDisposition.INCOMPLETE
    assert first.reconciliation_phase == "CHECKPOINT"
    assert host_one.place_calls == 1
    assert host_one.positions != []

    # Fresh process objects: new host double seeded with the same broker truth,
    # new prepared session, new doors, new ledger connection, same state dir.
    clock_two = PhaseClock(arm.hard_flat_at)
    host_two = FakeMcpHost()
    host_two.seed_active(host_one)
    doors_two = _doors(host_two, RiskLedger(ledger_path), clock_two, arm)

    second = _run(authority_input, arm, doors_two)

    assert second.disposition is AutonomousHostDisposition.TERMINAL
    assert second.terminal_flat_proven is True
    assert second.processed_opportunity_ids == ()
    assert second.disposition_counts["TERMINAL_FLAT"] == 1
    # Exactly one close submission in the restarted process; no duplicate open.
    assert host_two.place_calls == 2
    assert [name for name, _ in host_two.calls].count("place_option_order") == 1
    assert host_two.positions == []


def test_orphaned_working_order_is_cancelled_risk_reducing_at_startup(tmp_path: Path) -> None:
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost(
        open_working=[{"client_order_id": "unknown-client", "id": "orphan-1", "status": "new"}],
        on_first_fill=lambda: setattr(clock, "now", arm.hard_flat_at),
    )
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)

    receipt = _run(authority_input, arm, doors)

    assert host.cancel_calls == ["orphan-1"]
    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert receipt.manual_reasons == ()


def test_activity_manual_route_blocks_the_terminal_close(tmp_path: Path) -> None:
    decision_now = _decision_now()
    adjustment = {
        "activity_type": "OPCA",
        "date": decision_now.date().isoformat(),
        "description": "KR option corporate action",
        "id": "20260918120000000::opca0001::9",
        "qty": "1",
        "symbol": "KR260925C00061000",
        "transaction_time": decision_now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "type": "adjustment",
    }
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(decision_now)
    host = FakeMcpHost(
        activities=[adjustment], on_first_fill=lambda: setattr(clock, "now", arm.hard_flat_at)
    )
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)

    receipt = _run(authority_input, arm, doors)

    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert "ACTIVITY_MANUAL_ROUTE" in receipt.manual_reasons
    # The close still happened; only the terminal attestation is withheld.
    assert host.place_calls == 2


def test_account_fingerprint_mismatch_is_rejected_before_any_backend(tmp_path: Path) -> None:
    authority_input, arm = synth._authority(tmp_path, SyntheticPaperBroker(), fingerprint="ee" * 32)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)

    with pytest.raises(AutonomousHostRejected, match="ACCOUNT_FINGERPRINT_MISMATCH"):
        run_autonomous_host_command(
            authority_input=authority_input,
            plan_factory=paper_mcp_plan_factory(doors),
            observation_timeline=(synth._window_point(arm), arm.hard_flat_at),
        )
    assert "place_option_order" not in [name for name, _ in host.calls]


def test_account_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)
    # The frozen dataclass forbids replacement in place; rebuild with a wrong id.
    from dataclasses import replace

    wrong = replace(doors, account_id="SOME-OTHER-ACCOUNT")
    # The fingerprint is over the full canonical account payload, so the wrong
    # identity surfaces as the explicit identity mismatch, not the fingerprint.
    with pytest.raises(AutonomousHostRejected, match="ACCOUNT_IDENTITY_MISMATCH"):
        run_autonomous_host_command(
            authority_input=authority_input,
            plan_factory=paper_mcp_plan_factory(wrong),
            observation_timeline=(synth._window_point(arm), arm.hard_flat_at),
        )
    assert "place_option_order" not in [name for name, _ in host.calls]


def test_non_paper_account_class_fails_closed(tmp_path: Path) -> None:
    live_account = dict(ACCOUNT_PAYLOAD)
    live_account["account_class"] = "LIVE"
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost(account=live_account)
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)

    with pytest.raises(AutonomousHostRejected, match="ACCOUNT_TRUTH_UNAVAILABLE"):
        run_autonomous_host_command(
            authority_input=authority_input,
            plan_factory=paper_mcp_plan_factory(doors),
            observation_timeline=(synth._window_point(arm), arm.hard_flat_at),
        )
    assert "place_option_order" not in [name for name, _ in host.calls]


def test_synthetic_route_can_never_front_the_production_composition(tmp_path: Path) -> None:
    _authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm, approved_route=SyntheticRehearsalRoute())

    with pytest.raises(PaperMcpCompositionRejected, match="ROUTE_NOT_APPROVED"):
        paper_mcp_plan_factory(doors)
    assert "place_option_order" not in [name for name, _ in host.calls]


def test_production_module_never_imports_the_synthetic_stack() -> None:
    import ast

    source = (
        Path(__file__).parent.parent
        / "src"
        / "ringdown_market"
        / "runtime"
        / "paper_mcp_composition.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden_modules = {
        "ringdown_market.runtime.host_fake_broker",
        "ringdown_market.runtime.host_composition",
        "ringdown_market.sourcedata.fakes",
        "ringdown_market.execution.paper_demo",
    }
    assert not (imported & forbidden_modules)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    forbidden_names = {
        "SyntheticPaperBroker",
        "SyntheticRehearsalClock",
        "SyntheticAccountTruthSource",
        "SyntheticRehearsalRoute",
        "SyntheticRehearsalMutationGate",
        "FixtureEvidenceSource",
        "FixtureMarketDataSource",
        "FakePaperBroker",
    }
    assert not (names & forbidden_names)


def test_candidate_processing_refuses_mutation_before_startup_broker_truth(
    tmp_path: Path,
) -> None:
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    doors = _doors(host, ledger, clock, arm)
    authority = validate_autonomous_host_authority(authority_input)

    plan = paper_mcp_plan_factory(doors)(authority)

    assert plan.execution_class is HostExecutionClass.PAPER_MCP
    backend = plan.candidate_backend
    assert (
        backend.production_binding_sha256 == plan.reconciliation_backend.production_binding_sha256
    )
    state = backend._state  # whitebox invariant check
    assert state.broker_truth_established is False
