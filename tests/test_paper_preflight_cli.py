"""Issue #90: read-only paper-preflight CLI, paper-run CLI, and wall-clock scheduler."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ringdown_market.cli import main as cli_main
from ringdown_market.contracts.broker_preflight import (
    PreflightVerdict,
    parse_broker_preflight_receipt,
)
from ringdown_market.contracts.execution_policy import CANCEL_TOOL, OPEN_TOOL
from ringdown_market.execution.host_mcp import (
    HostMcpConfigurationError,
    HostMcpEnvironment,
    HostMcpPaperSessionFactory,
    HostMcpSessionIdentity,
)
from ringdown_market.risk import RiskLedger
from ringdown_market.runtime.autonomous_host import AutonomousHostDisposition
from ringdown_market.runtime.paper_mcp_composition import paper_mcp_plan_factory
from ringdown_market.runtime.paper_scheduler import (
    PaperSessionInvocation,
    run_paper_session,
    session_observation_timeline,
    wait_until,
)
from test_paper_mcp_composition import (
    ACCOUNT_FINGERPRINT,
    FakeMcpHost,
    PhaseClock,
    _decision_now,
    _doors,
    _session,
)

NOW = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)
MUTATING_TOOLS = {OPEN_TOOL, CANCEL_TOOL}


def _preflight_args(output: Path, *, fingerprint: str = ACCOUNT_FINGERPRINT) -> list[str]:
    return [
        "paper-preflight",
        "--host-session",
        "test_preflight_session_module:build_session",
        "--receipt-id",
        "preflight-run-0001",
        "--expected-account-id",
        "PA5XSNL1XT43",
        "--expected-account-fingerprint",
        fingerprint,
        "--starting-equity",
        "100000.00",
        "--account-capability-id",
        "paper-capability-0001",
        "--runtime-code-revision",
        "0123456789abcdef0123456789abcdef01234567",
        "--runtime-build-sha256",
        "ab" * 32,
        "--output",
        str(output),
    ]


def _install_session_selector(
    monkeypatch: pytest.MonkeyPatch, host: FakeMcpHost, module_name: str
) -> None:
    module = types.ModuleType(module_name)

    def build_session() -> FakeMcpHost:
        return host

    module.build_session = build_session  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, module)


def test_paper_preflight_cli_passes_and_writes_the_redacted_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeMcpHost()
    _install_session_selector(monkeypatch, host, "test_preflight_session_module")
    output = tmp_path / "artifacts" / "paper-preflight" / "preflight-run-0001" / "receipt.json"

    exit_code = cli_main(_preflight_args(output), clock=lambda: NOW)

    assert exit_code == 0
    receipt = parse_broker_preflight_receipt(output.read_bytes())
    assert receipt.verdict is PreflightVerdict.PASSED
    assert receipt.reason_codes == ()
    assert receipt.is_flat is True
    assert receipt.starting_balance_satisfied is True
    assert receipt.account_query_succeeded is True
    assert receipt.orders_query_succeeded is True
    assert receipt.positions_query_succeeded is True
    assert receipt.activities_query_succeeded is True
    assert receipt.activities_page_count >= 1
    assert receipt.route_config_sha256 != "0" * 64
    assert receipt.latency_profile_sha256 != "0" * 64
    # Read-only by construction: no mutating tool ever reached the host.
    assert not ({name for name, _ in host.calls} & MUTATING_TOOLS)
    # The receipt never carries the raw account identifier.
    assert "PA5XSNL1XT43" not in output.read_text(encoding="utf-8")


def test_paper_preflight_cli_emits_reason_coded_rejection_for_non_flat_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeMcpHost(
        open_working=[{"client_order_id": "leftover", "id": "order-9", "status": "new"}]
    )
    _install_session_selector(monkeypatch, host, "test_preflight_session_module")
    output = tmp_path / "preflight-receipt.json"

    exit_code = cli_main(_preflight_args(output), clock=lambda: NOW)

    assert exit_code == 2
    receipt = parse_broker_preflight_receipt(output.read_bytes())
    assert receipt.verdict is PreflightVerdict.REJECTED
    assert "NON_FLAT_START" in receipt.reason_codes
    assert receipt.open_order_count == 1
    assert not ({name for name, _ in host.calls} & MUTATING_TOOLS)


def test_paper_preflight_cli_rejects_account_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeMcpHost()
    _install_session_selector(monkeypatch, host, "test_preflight_session_module")
    output = tmp_path / "preflight-receipt.json"

    exit_code = cli_main(_preflight_args(output, fingerprint="ee" * 32), clock=lambda: NOW)

    assert exit_code == 2
    receipt = parse_broker_preflight_receipt(output.read_bytes())
    assert receipt.verdict is PreflightVerdict.REJECTED
    assert "ACCOUNT_MISMATCH" in receipt.reason_codes


def test_preflight_cli_rejects_invalid_expectations_before_any_host_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeMcpHost()
    _install_session_selector(monkeypatch, host, "test_preflight_session_module")
    output = tmp_path / "preflight-receipt.json"
    args = _preflight_args(output)
    args[args.index("--starting-equity") + 1] = "not-a-decimal"

    exit_code = cli_main(args, clock=lambda: NOW)

    assert exit_code == 2
    assert host.calls == []


def test_a_session_recording_a_mutation_attempt_fails_the_readonly_door() -> None:
    host = FakeMcpHost()
    factory = HostMcpPaperSessionFactory(
        HostMcpSessionIdentity(environment=HostMcpEnvironment.PAPER), clock=lambda: NOW
    )
    prepared = asyncio.run(factory.connect(host))
    host.calls.clear()

    for tool in sorted(MUTATING_TOOLS):
        with pytest.raises(HostMcpConfigurationError, match="read-only door"):
            asyncio.run(prepared.readonly_call(tool, {}))

    assert not ({name for name, _ in host.calls} & MUTATING_TOOLS)


def _install_paper_run_selector(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    *,
    invocation_base: PaperSessionInvocation,
) -> str:
    module = types.ModuleType(module_name)

    def build_invocation(
        *,
        release_bytes: bytes,
        arm_record_bytes: bytes,
        state_dir: Path,
        ledger_path: Path,
        output_dir: Path,
    ) -> PaperSessionInvocation:
        del ledger_path, output_dir
        return replace(
            invocation_base,
            authority_input=replace(
                invocation_base.authority_input,
                release_bytes=release_bytes,
                arm_record_bytes=arm_record_bytes,
                state_dir=state_dir,
            ),
        )

    module.build_invocation = build_invocation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, module)
    return f"{module_name}:build_invocation"


class SchedulerClock:
    """Wall-clock double advanced exclusively by recorded sleeps."""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


def _paper_run_invocation(
    tmp_path: Path, host: FakeMcpHost, authority_input, arm
) -> PaperSessionInvocation:
    clock = PhaseClock(_decision_now())
    host.on_first_fill = lambda: setattr(clock, "now", arm.hard_flat_at)
    ledger_path = tmp_path / "risk.sqlite3"
    doors = _doors(host, RiskLedger(ledger_path), clock, arm)
    scheduler = SchedulerClock(arm.starts_at - timedelta(minutes=30))
    return PaperSessionInvocation(
        authority_input=authority_input,
        doors=doors,
        ledger_path=ledger_path,
        scheduler_clock=scheduler,
        scheduler_sleep=scheduler.sleep,
    )


def test_paper_run_cli_drives_the_scheduled_session_to_terminal_flat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority_input, arm = _session(tmp_path)
    host = FakeMcpHost()
    invocation = _paper_run_invocation(tmp_path, host, authority_input, arm)
    selector = _install_paper_run_selector(
        monkeypatch, "test_paper_run_module", invocation_base=invocation
    )
    release_path = tmp_path / "release.json"
    arm_path = tmp_path / "arm.json"
    release_path.write_bytes(invocation.authority_input.release_bytes)
    arm_path.write_bytes(invocation.authority_input.arm_record_bytes)
    state_dir = tmp_path / "state"
    output_dir = tmp_path / "output"

    exit_code = cli_main(
        [
            "paper-run",
            "--release",
            str(release_path),
            "--arm",
            str(arm_path),
            "--state-dir",
            str(state_dir),
            "--ledger",
            str(invocation.ledger_path),
            "--output-dir",
            str(output_dir),
            "--host-invocation",
            selector,
        ]
    )

    assert exit_code == 0
    receipt_bytes = (output_dir / "paper-run-receipt.json").read_bytes()
    receipt_payload = json.loads(receipt_bytes)
    assert receipt_payload["execution_class"] == "PAPER_MCP"
    assert receipt_payload["disposition"] == "TERMINAL"
    assert host.place_calls == 2
    assert host.positions == []
    scheduler_clock = invocation.scheduler_clock
    assert isinstance(scheduler_clock, SchedulerClock)
    timeline = session_observation_timeline(
        parse_arm_from_bytes(invocation.authority_input.session_arm_bytes)
    )
    # One sleep per scheduled point at most: no busy loop.
    assert len(scheduler_clock.sleeps) <= len(timeline)
    assert scheduler_clock.now >= timeline[-1]


def parse_arm_from_bytes(raw: bytes):
    from ringdown_market.runtime.autonomous import parse_autonomous_session_arm

    return parse_autonomous_session_arm(raw)


def test_scheduler_waits_once_per_point_and_never_busy_loops() -> None:
    sleeps: list[float] = []
    current = [datetime(2026, 9, 18, 13, 0, tzinfo=UTC)]

    def clock() -> datetime:
        return current[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current[0] = current[0] + timedelta(seconds=seconds)

    target = datetime(2026, 9, 18, 14, 30, tzinfo=UTC)
    wait_until(target, clock=clock, sleep=sleep)
    assert sleeps == [5400.0]

    # Already past the target: zero waits, no negative sleep.
    wait_until(target, clock=clock, sleep=sleep)
    assert sleeps == [5400.0]


def test_scheduler_stops_at_manual_reconciliation_without_later_points(
    tmp_path: Path,
) -> None:
    authority_input, arm = _session(tmp_path)
    clock = PhaseClock(_decision_now())
    host = FakeMcpHost(open_timeout=True, readback_timeout=True)
    ledger_path = tmp_path / "risk.sqlite3"
    doors = _doors(host, RiskLedger(ledger_path), clock, arm)
    scheduler = SchedulerClock(arm.starts_at - timedelta(minutes=30))
    timeline = session_observation_timeline(arm)

    receipt = run_paper_session(
        authority_input=authority_input,
        plan_factory=paper_mcp_plan_factory(doors),
        timeline=timeline,
        clock=scheduler,
        sleep=scheduler.sleep,
    )

    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert "UNKNOWN_BROKER_STATE" in receipt.manual_reasons
    # The manual stop happened at the first processed window; the scheduler
    # never waited for the remaining timeline points.
    assert len(scheduler.sleeps) < len(timeline)
    assert host.place_calls == 1
