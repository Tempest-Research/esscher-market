from __future__ import annotations

import hashlib
import importlib
import json
import sys
import types
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ringdown_market.cli import main as cli_main
from ringdown_market.contracts.strategy_release import (
    EXPECTED_LANE_BINDINGS,
    ArmRecord,
    ReleaseLog,
    StrategyRelease,
    arm_record_bytes,
    current_semantic_ids,
    evaluate_release,
    strategy_release_bytes,
)
from ringdown_market.runtime.autonomous import (
    AutonomousSessionArm,
    autonomous_session_arm_bytes,
)
from ringdown_market.runtime.autonomous_host import (
    AutonomousHostAuthorityInput,
    AutonomousHostInvocation,
    AutonomousHostPlan,
    HostExecutionClass,
    HostReconciliationObservation,
    HostReconciliationStatus,
    SyntheticBrokerTruth,
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True)
class _AuthorityFixture:
    release: StrategyRelease
    release_bytes: bytes
    arm_record_bytes: bytes
    session_arm: AutonomousSessionArm
    session_arm_bytes: bytes


def _authority_fixture() -> _AuthorityFixture:
    release = StrategyRelease(
        release_id="ESSCHER_AUTONOMOUS_CLI_TEST",
        release_version=1,
        created_at=datetime(2026, 8, 31, 16, 0, tzinfo=UTC),
        mode="PAPER",
        code_revision="a" * 40,
        build_artifact_sha256="b" * 64,
        evidence_report_sha256="c" * 64,
        security_report_sha256="d" * 64,
        evidence_qualified=True,
        security_passed=True,
        lane_bindings=EXPECTED_LANE_BINDINGS,
        supersedes_release_sha256=None,
        **current_semantic_ids(),
    )
    session_arm = AutonomousSessionArm.for_trading_date(
        session_id="ESSCHER-CLI-20260901",
        session_date=date(2026, 9, 1),
        release_code_sha256=release.build_artifact_sha256,
        account_fingerprint_sha256="f" * 64,
    )
    arm_record = ArmRecord(
        arm_id=session_arm.session_id,
        release_sha256=release.release_sha256,
        account_capability_id="PAPER_ACCOUNT_CAPABILITY_CLI",
        source_ids=("ALPACA_MCP", "BENZINGA"),
        starts_at=session_arm.starts_at,
        expires_at=session_arm.hard_flat_at,
        ledger_id="PAPER_LEDGER_CLI",
        process_id="PAPER_PROCESS_CLI",
        flatten_authority=True,
        recovery_authority=True,
    )
    return _AuthorityFixture(
        release=release,
        release_bytes=strategy_release_bytes(release),
        arm_record_bytes=arm_record_bytes(arm_record),
        session_arm=session_arm,
        session_arm_bytes=autonomous_session_arm_bytes(session_arm),
    )


class _ReconciliationBackend:
    def __init__(self, status: HostReconciliationStatus) -> None:
        self.status = status
        self.calls = []

    def observe_reconciliation(self, request):
        self.calls.append(request)
        print("HOST_BACKEND_STDOUT_NOISE")
        print("HOST_BACKEND_STDERR_NOISE", file=sys.stderr)
        is_flat = not request.active_lifecycle_ids
        complete = HostReconciliationObservation.complete(
            request,
            broker_truth=SyntheticBrokerTruth.for_request(
                request,
                account_state_sha256="6" * 64,
                orders_state_sha256="7" * 64,
                positions_state_sha256="8" * 64,
                open_order_count=0,
                open_position_count=(0 if is_flat else len(request.active_lifecycle_ids)),
                is_flat=is_flat,
            ),
        )
        return HostReconciliationObservation(
            session_id=complete.session_id,
            arm_sha256=complete.arm_sha256,
            account_fingerprint_sha256=complete.account_fingerprint_sha256,
            execution_protocol_sha256=complete.execution_protocol_sha256,
            observed_at=complete.observed_at,
            phase=complete.phase,
            active_lifecycle_ids=complete.active_lifecycle_ids,
            status=self.status,
            broker_truth=complete.broker_truth,
        )


class _EmptyCollectorBackend:
    def observe_due_window(self, request):
        return ()


class _UnexpectedCandidateBackend:
    def process_candidate(self, request):
        raise AssertionError(f"unexpected candidate {request.opportunity.opportunity_id}")


class _UnexpectedLifecycleBackend:
    def close_lifecycle(self, request):
        raise AssertionError(f"unexpected lifecycle {request.lifecycle.lifecycle_id}")


def _install_host_plan(
    *,
    monkeypatch: pytest.MonkeyPatch,
    fixture: _AuthorityFixture,
    module_name: str,
    observed_at: datetime,
    reconciliation_status: HostReconciliationStatus,
    release_log_path: Path,
    fail_plan_construction: bool = False,
) -> tuple[str, list[object]]:
    validated_authorities: list[object] = []
    with ReleaseLog(release_log_path) as release_log:
        release_log.promote(fixture.release, evaluate_release(fixture.release))

    def build_invocation(
        *,
        release_bytes: bytes,
        arm_record_bytes: bytes,
        state_dir: Path,
    ) -> AutonomousHostInvocation:
        authority_input = AutonomousHostAuthorityInput(
            release_bytes=release_bytes,
            arm_record_bytes=arm_record_bytes,
            session_arm_bytes=fixture.session_arm_bytes,
            release_log_path=release_log_path,
            release_sha256=fixture.release.release_sha256,
            runtime_build_artifact_sha256=fixture.release.build_artifact_sha256,
            runtime_code_revision=fixture.release.code_revision,
            account_capability_id="PAPER_ACCOUNT_CAPABILITY_CLI",
            account_fingerprint_sha256=fixture.session_arm.account_fingerprint_sha256,
            source_ids=("ALPACA_MCP", "BENZINGA"),
            ledger_id="PAPER_LEDGER_CLI",
            process_id="PAPER_PROCESS_CLI",
            state_dir=state_dir,
        )

        def build_plan(validated_authority) -> AutonomousHostPlan:
            validated_authorities.append(validated_authority)
            if fail_plan_construction:
                print("PLAN_FACTORY_STDOUT_CREDENTIAL_SENTINEL")
                print("PLAN_FACTORY_STDERR_CREDENTIAL_SENTINEL", file=sys.stderr)
                raise RuntimeError("selected plan construction failed")
            return AutonomousHostPlan(
                execution_class=HostExecutionClass.SYNTHETIC_FAKE,
                reconciliation_backend=_ReconciliationBackend(reconciliation_status),
                collector_backend=_EmptyCollectorBackend(),
                candidate_backend=_UnexpectedCandidateBackend(),
                lifecycle_backend=_UnexpectedLifecycleBackend(),
            )

        return AutonomousHostInvocation(
            authority_input=authority_input,
            observation_timeline=(observed_at,),
            plan_factory=build_plan,
        )

    module = types.ModuleType(module_name)
    module.build_invocation = build_invocation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return f"{module_name}:build_invocation", validated_authorities


def _run_cli(
    *,
    fixture: _AuthorityFixture,
    tmp_path: Path,
    state_dir: Path,
    selector: str,
) -> int:
    release_path = tmp_path / "release.json"
    arm_path = tmp_path / "arm.json"
    release_path.write_bytes(fixture.release_bytes)
    arm_path.write_bytes(fixture.arm_record_bytes)
    return cli_main(
        [
            "run-autonomous-session",
            "--release",
            str(release_path),
            "--arm",
            str(arm_path),
            "--state-dir",
            str(state_dir),
            "--host-plan",
            selector,
        ]
    )


def test_autonomous_help_exposes_only_operator_authority_and_plan_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli_main(["run-autonomous-session", "--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--release" in output
    assert "--arm" in output
    assert "--state-dir" in output
    assert "--host-plan" in output
    for forbidden in (
        "--account",
        "--api-key",
        "--clock",
        "--credential",
        "--observed-at",
        "--secret",
        "--timeline",
        "--token",
    ):
        assert forbidden not in output


def test_invalid_canonical_authority_is_rejected_before_host_plan_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_called = False
    plan_resolved = False

    def build_invocation(**_: object) -> object:
        nonlocal plan_called
        plan_called = True
        raise AssertionError("invalid authority must fail before the host plan is called")

    module = types.ModuleType("test_invalid_autonomous_host_plan")
    module.build_invocation = build_invocation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    real_import_module = importlib.import_module

    def record_import(name: str, package: str | None = None):
        nonlocal plan_resolved
        if name == module.__name__:
            plan_resolved = True
        return real_import_module(name, package)

    monkeypatch.setattr("ringdown_market.cli.importlib.import_module", record_import)
    release_path = tmp_path / "release.json"
    arm_path = tmp_path / "arm.json"
    release_path.write_bytes(b"{}")
    arm_path.write_bytes(b"{}")

    exit_code = cli_main(
        [
            "run-autonomous-session",
            "--release",
            str(release_path),
            "--arm",
            str(arm_path),
            "--state-dir",
            str(tmp_path / "state"),
            "--host-plan",
            f"{module.__name__}:build_invocation",
        ]
    )

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    receipt_sha256 = output.pop("receipt_sha256")
    assert plan_called is False
    assert plan_resolved is False
    assert exit_code == 2
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert output["disposition"] == "REJECTED"
    assert output["error_code"] == "AUTHORITY_INPUT_REJECTED"
    assert output["broker_mutation"] == "NOT_ATTEMPTED"
    assert "NO_BROKER_EXECUTION" in output["claims"]
    assert receipt_sha256 == hashlib.sha256(_canonical_json(output)).hexdigest()
    assert (
        captured.out
        == _canonical_json({**output, "receipt_sha256": receipt_sha256}).decode("utf-8") + "\n"
    )


def test_noisy_host_import_and_factory_failure_emit_one_honest_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _authority_fixture()
    imported = False
    factory_called = False

    def build_invocation(**_: object) -> object:
        nonlocal factory_called
        factory_called = True
        print("HOST_FACTORY_STDOUT_CREDENTIAL_SENTINEL")
        print("HOST_FACTORY_STDERR_CREDENTIAL_SENTINEL", file=sys.stderr)
        raise RuntimeError("selected invocation factory failed")

    module = types.ModuleType("test_noisy_autonomous_host_plan")
    module.build_invocation = build_invocation  # type: ignore[attr-defined]
    real_import_module = importlib.import_module

    def noisy_import(name: str, package: str | None = None):
        nonlocal imported
        if name == module.__name__:
            imported = True
            print("HOST_IMPORT_STDOUT_CREDENTIAL_SENTINEL")
            print("HOST_IMPORT_STDERR_CREDENTIAL_SENTINEL", file=sys.stderr)
            return module
        return real_import_module(name, package)

    monkeypatch.setattr("ringdown_market.cli.importlib.import_module", noisy_import)

    exit_code = _run_cli(
        fixture=fixture,
        tmp_path=tmp_path,
        state_dir=tmp_path / "state",
        selector=f"{module.__name__}:build_invocation",
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert imported is True
    assert factory_called is True
    assert exit_code == 3
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "CREDENTIAL_SENTINEL" not in captured.out
    assert payload["error_code"] == "HOST_PLAN_REJECTED"
    assert payload["disposition"] == "MANUAL_RECONCILIATION_REQUIRED"
    assert payload["broker_mutation"] == "UNKNOWN"
    assert payload["claims"] == ["FAIL_CLOSED", "OPERATOR_INTERVENTION_REQUIRED"]
    assert "NO_BROKER_EXECUTION" not in payload["claims"]
    assert captured.out == _canonical_json(payload).decode("utf-8") + "\n"


@pytest.mark.parametrize("substituted_field", ["release", "arm", "state_dir"])
def test_host_selector_cannot_substitute_cli_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    substituted_field: str,
) -> None:
    fixture = _authority_fixture()
    plan_called = False

    def build_invocation(
        *,
        release_bytes: bytes,
        arm_record_bytes: bytes,
        state_dir: Path,
    ) -> AutonomousHostInvocation:
        nonlocal plan_called

        def build_plan(_):
            nonlocal plan_called
            plan_called = True
            raise AssertionError("substituted authority must fail before plan construction")

        return AutonomousHostInvocation(
            authority_input=AutonomousHostAuthorityInput(
                release_bytes=(
                    release_bytes + b"\n" if substituted_field == "release" else release_bytes
                ),
                arm_record_bytes=(
                    arm_record_bytes + b"\n" if substituted_field == "arm" else arm_record_bytes
                ),
                session_arm_bytes=fixture.session_arm_bytes,
                release_log_path=tmp_path / "unused-release-log.sqlite3",
                release_sha256=fixture.release.release_sha256,
                runtime_build_artifact_sha256=fixture.release.build_artifact_sha256,
                runtime_code_revision=fixture.release.code_revision,
                account_capability_id="PAPER_ACCOUNT_CAPABILITY_CLI",
                account_fingerprint_sha256=fixture.session_arm.account_fingerprint_sha256,
                source_ids=("ALPACA_MCP", "BENZINGA"),
                ledger_id="PAPER_LEDGER_CLI",
                process_id="PAPER_PROCESS_CLI",
                state_dir=(
                    tmp_path / "substituted-state"
                    if substituted_field == "state_dir"
                    else state_dir
                ),
            ),
            observation_timeline=(fixture.session_arm.hard_flat_at,),
            plan_factory=build_plan,
        )

    module = types.ModuleType(f"test_substituted_{substituted_field}_host_plan")
    module.build_invocation = build_invocation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)

    exit_code = _run_cli(
        fixture=fixture,
        tmp_path=tmp_path,
        state_dir=tmp_path / "state",
        selector=f"{module.__name__}:build_invocation",
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert output["error_code"] == "HOST_PLAN_REJECTED"
    assert output["disposition"] == "MANUAL_RECONCILIATION_REQUIRED"
    assert output["broker_mutation"] == "UNKNOWN"
    assert "NO_BROKER_EXECUTION" not in output["claims"]
    assert plan_called is False
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "substituted-state").exists()


def test_terminal_synthetic_plan_has_deterministic_canonical_receipt_and_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _authority_fixture()
    selector, validated = _install_host_plan(
        monkeypatch=monkeypatch,
        fixture=fixture,
        module_name="test_terminal_autonomous_host_plan",
        observed_at=fixture.session_arm.hard_flat_at,
        reconciliation_status=HostReconciliationStatus.COMPLETE,
        release_log_path=tmp_path / "release-log.sqlite3",
    )

    first_exit = _run_cli(
        fixture=fixture,
        tmp_path=tmp_path,
        state_dir=tmp_path / "state-one",
        selector=selector,
    )
    first = capsys.readouterr()
    second_exit = _run_cli(
        fixture=fixture,
        tmp_path=tmp_path,
        state_dir=tmp_path / "state-two",
        selector=selector,
    )
    second = capsys.readouterr()

    payload = json.loads(first.out)
    unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
    assert first_exit == second_exit == 0
    assert first.err == second.err == ""
    assert first.out == second.out
    assert first.out.count("\n") == 1
    assert "HOST_BACKEND_STDOUT_NOISE" not in first.out
    assert "HOST_BACKEND_STDERR_NOISE" not in first.err
    assert first.out == _canonical_json(payload).decode("utf-8") + "\n"
    assert payload["disposition"] == "TERMINAL"
    assert payload["terminal_flat_proven"] is True
    assert payload["data_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert payload["execution_class"] == "SYNTHETIC_FAKE"
    assert payload["claim_basis"] == "HOST_PLAN_ATTESTATION"
    assert "SYNTHETIC_FAKE" in payload["claims"]
    assert "HOST_PLAN_ATTESTS_NO_BROKER_EXECUTION" in payload["claims"]
    assert "NO_BROKER_EXECUTION" not in payload["claims"]
    assert payload["receipt_sha256"] == hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    assert len(validated) == 2


def test_delayed_plan_factory_output_and_failure_emit_one_honest_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _authority_fixture()
    selector, plan_attempts = _install_host_plan(
        monkeypatch=monkeypatch,
        fixture=fixture,
        module_name="test_failing_delayed_autonomous_host_plan",
        observed_at=fixture.session_arm.hard_flat_at,
        reconciliation_status=HostReconciliationStatus.COMPLETE,
        release_log_path=tmp_path / "release-log.sqlite3",
        fail_plan_construction=True,
    )

    exit_code = _run_cli(
        fixture=fixture,
        tmp_path=tmp_path,
        state_dir=tmp_path / "state",
        selector=selector,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert len(plan_attempts) == 1
    assert exit_code == 3
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert "CREDENTIAL_SENTINEL" not in captured.out
    assert payload["error_code"] == "HOST_PLAN_REJECTED"
    assert payload["disposition"] == "MANUAL_RECONCILIATION_REQUIRED"
    assert payload["broker_mutation"] == "UNKNOWN"
    assert payload["claims"] == ["FAIL_CLOSED", "OPERATOR_INTERVENTION_REQUIRED"]
    assert "NO_BROKER_EXECUTION" not in payload["claims"]
    assert captured.out == _canonical_json(payload).decode("utf-8") + "\n"


@pytest.mark.parametrize(
    ("status", "expected_exit", "expected_disposition"),
    [
        (HostReconciliationStatus.AMBIGUOUS, 3, "MANUAL_RECONCILIATION_REQUIRED"),
        (HostReconciliationStatus.COMPLETE, 4, "INCOMPLETE"),
    ],
)
def test_nonterminal_receipt_dispositions_have_distinct_operator_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: HostReconciliationStatus,
    expected_exit: int,
    expected_disposition: str,
) -> None:
    fixture = _authority_fixture()
    selector, validated = _install_host_plan(
        monkeypatch=monkeypatch,
        fixture=fixture,
        module_name=f"test_{expected_disposition.lower()}_autonomous_host_plan",
        observed_at=fixture.session_arm.starts_at,
        reconciliation_status=status,
        release_log_path=tmp_path / "release-log.sqlite3",
    )

    exit_code = _run_cli(
        fixture=fixture,
        tmp_path=tmp_path,
        state_dir=tmp_path / "state",
        selector=selector,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == expected_exit
    assert payload["disposition"] == expected_disposition
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert len(validated) == 1
