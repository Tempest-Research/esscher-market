from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

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
    AutonomousDisposition,
    AutonomousSessionArm,
    AutonomousSessionStore,
    MutationState,
    autonomous_session_arm_bytes,
)
from ringdown_market.runtime.autonomous_host import (
    AUTONOMOUS_HOST_CLAIMS,
    AutonomousHostAuthorityInput,
    AutonomousHostDisposition,
    AutonomousHostPlan,
    AutonomousHostReceipt,
    AutonomousHostRejected,
    HostCandidateObservation,
    HostCandidateOutcome,
    HostExecutionClass,
    HostLifecycleOutcome,
    HostReconciliationObservation,
    HostReconciliationStatus,
    SyntheticBrokerTruth,
    run_autonomous_host_command,
    synthetic_broker_truth_bytes,
    synthetic_broker_truth_sha256,
    validate_autonomous_host_authority,
)

BUILD_SHA256 = "b" * 64
ACCOUNT_FINGERPRINT_SHA256 = "f" * 64
CODE_REVISION = "a" * 40
CAPABILITY_ID = "PAPER-CAPABILITY-1"
SOURCE_IDS = ("ALPACA_MCP", "BENZINGA")
LEDGER_ID = "PAPER-LEDGER-1"
PROCESS_ID = "PAPER-PROCESS-1"
SESSION_ID = "ESSCHER-20260901"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _directory_link_or_skip(path: Path, target: Path) -> None:
    if os.name == "nt" and hasattr(Path, "is_junction"):
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(path), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and path.is_junction():
            return
        pytest.skip("junction creation is unavailable to this test process")
    try:
        path.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")


def _authority(tmp_path: Path) -> tuple[AutonomousHostAuthorityInput, AutonomousSessionArm]:
    session_arm = AutonomousSessionArm.for_trading_date(
        session_id=SESSION_ID,
        session_date=date(2026, 9, 1),
        release_code_sha256=BUILD_SHA256,
        account_fingerprint_sha256=ACCOUNT_FINGERPRINT_SHA256,
    )
    release = StrategyRelease(
        release_id="ESSCHER-PAPER-HOST-1",
        release_version=1,
        created_at=session_arm.starts_at - timedelta(days=1),
        mode="PAPER",
        code_revision=CODE_REVISION,
        build_artifact_sha256=BUILD_SHA256,
        evidence_report_sha256="c" * 64,
        security_report_sha256="d" * 64,
        evidence_qualified=True,
        security_passed=True,
        lane_bindings=EXPECTED_LANE_BINDINGS,
        **current_semantic_ids(),
    )
    arm_record = ArmRecord(
        arm_id=SESSION_ID,
        release_sha256=release.release_sha256,
        account_capability_id=CAPABILITY_ID,
        source_ids=SOURCE_IDS,
        starts_at=session_arm.starts_at,
        expires_at=session_arm.hard_flat_at,
        ledger_id=LEDGER_ID,
        process_id=PROCESS_ID,
        flatten_authority=True,
        recovery_authority=True,
    )
    release_log_path = tmp_path / "releases.sqlite3"
    with ReleaseLog(release_log_path) as release_log:
        release_log.promote(release, evaluate_release(release))
    return (
        AutonomousHostAuthorityInput(
            release_bytes=strategy_release_bytes(release),
            arm_record_bytes=arm_record_bytes(arm_record),
            session_arm_bytes=autonomous_session_arm_bytes(session_arm),
            release_log_path=release_log_path,
            release_sha256=release.release_sha256,
            runtime_build_artifact_sha256=BUILD_SHA256,
            runtime_code_revision=CODE_REVISION,
            account_capability_id=CAPABILITY_ID,
            account_fingerprint_sha256=ACCOUNT_FINGERPRINT_SHA256,
            source_ids=SOURCE_IDS,
            ledger_id=LEDGER_ID,
            process_id=PROCESS_ID,
            state_dir=tmp_path / "state",
        ),
        session_arm,
    )


class _RecordingBackends:
    def __init__(
        self,
        *,
        candidate_mode: str = "TERMINAL_FLAT",
        duplicate_candidate: bool = False,
        collect_candidate: bool = True,
        final_reconciliation_status: HostReconciliationStatus = (HostReconciliationStatus.COMPLETE),
        truth_transform=None,
    ) -> None:
        self.candidate_mode = candidate_mode
        self.duplicate_candidate = duplicate_candidate
        self.collect_candidate = collect_candidate
        self.final_reconciliation_status = final_reconciliation_status
        self.truth_transform = truth_transform
        self.reconciliation_calls = []
        self.collector_calls = []
        self.candidate_calls = []
        self.lifecycle_calls = []
        self.truth_by_phase: dict[str, SyntheticBrokerTruth] = {}

    def observe_reconciliation(self, request):
        self.reconciliation_calls.append(request)
        is_flat = not request.active_lifecycle_ids
        truth = SyntheticBrokerTruth.for_request(
            request,
            account_state_sha256="6" * 64,
            orders_state_sha256="7" * 64,
            positions_state_sha256="8" * 64,
            open_order_count=0,
            open_position_count=(0 if is_flat else len(request.active_lifecycle_ids)),
            is_flat=is_flat,
        )
        if self.truth_transform is not None:
            truth = self.truth_transform(request, truth)
        self.truth_by_phase[request.phase] = truth
        observation = HostReconciliationObservation.complete(
            request,
            broker_truth=truth,
        )
        if request.phase == "FINAL":
            return replace(observation, status=self.final_reconciliation_status)
        return observation

    def observe_due_window(self, request):
        self.collector_calls.append(request)
        if not self.collect_candidate:
            return ()
        observation = HostCandidateObservation.for_window(
            request,
            opportunity_id="OPPORTUNITY-1",
            candidate_id=request.window.candidate_ids[0],
            strategy_context_sha256="e" * 64,
        )
        return (observation, observation) if self.duplicate_candidate else (observation,)

    def process_candidate(self, request):
        self.candidate_calls.append(request)
        if self.candidate_mode == "ACTIVE":
            return HostCandidateOutcome.active(request, lifecycle_id="LIFECYCLE-1")
        if self.candidate_mode == "MANUAL":
            return HostCandidateOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        if self.candidate_mode == "ABSTAINED":
            return HostCandidateOutcome.abstained(
                request,
                reason_code="PROVIDER_TIMEOUT_BEFORE_MUTATION",
            )
        return HostCandidateOutcome.terminal_flat(
            request,
            terminal_flat_proof_sha256="1" * 64,
        )

    def close_lifecycle(self, request):
        self.lifecycle_calls.append(request)
        return HostLifecycleOutcome.terminal_flat(
            request,
            terminal_flat_proof_sha256="2" * 64,
        )

    def plan(self) -> AutonomousHostPlan:
        return AutonomousHostPlan(
            execution_class=HostExecutionClass.SYNTHETIC_FAKE,
            reconciliation_backend=self,
            collector_backend=self,
            candidate_backend=self,
            lifecycle_backend=self,
        )


def test_authority_mismatch_rejects_before_plan_or_backend_and_does_not_create_state(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    mismatched = replace(authority_input, runtime_code_revision="9" * 40)
    backends = _RecordingBackends()
    factory_calls = 0

    def plan_factory(_):
        nonlocal factory_calls
        factory_calls += 1
        return backends.plan()

    with pytest.raises(AutonomousHostRejected, match="code revision"):
        run_autonomous_host_command(
            authority_input=mismatched,
            plan_factory=plan_factory,
            observation_timeline=(arm.windows[0].opens_at,),
        )

    assert factory_calls == 0
    assert backends.reconciliation_calls == []
    assert backends.collector_calls == []
    assert backends.candidate_calls == []
    assert backends.lifecycle_calls == []
    assert not authority_input.state_dir.exists()


def test_release_log_cannot_alias_the_autonomous_session_store(tmp_path: Path) -> None:
    authority_input, arm = _authority(tmp_path)
    alias_dir = tmp_path / "aliased-state"
    alias_dir.mkdir()
    alias_path = alias_dir / "autonomous.sqlite3"
    authority_input.release_log_path.replace(alias_path)
    aliased = replace(
        authority_input,
        release_log_path=alias_path,
        state_dir=alias_dir,
    )
    plan_calls = 0

    def plan_factory(_):
        nonlocal plan_calls
        plan_calls += 1
        return _RecordingBackends().plan()

    with pytest.raises(AutonomousHostRejected, match="must be distinct files"):
        run_autonomous_host_command(
            authority_input=aliased,
            plan_factory=plan_factory,
            observation_timeline=(arm.hard_flat_at,),
        )

    assert plan_calls == 0


def test_state_directory_parent_indirection_rejects_before_plan(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected_parent = tmp_path / "redirected-parent"
    _directory_link_or_skip(redirected_parent, outside)
    redirected = replace(
        authority_input,
        state_dir=redirected_parent / "state",
    )
    plan_calls = 0

    def plan_factory(_):
        nonlocal plan_calls
        plan_calls += 1
        return _RecordingBackends().plan()

    with pytest.raises(AutonomousHostRejected, match="link or junction"):
        run_autonomous_host_command(
            authority_input=redirected,
            plan_factory=plan_factory,
            observation_timeline=(arm.hard_flat_at,),
        )

    assert plan_calls == 0
    assert tuple(outside.iterdir()) == ()


def test_conflicting_persisted_arm_rejects_before_delayed_plan_construction(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    conflicting_arm = AutonomousSessionArm.for_trading_date(
        session_id=arm.session_id,
        session_date=arm.starts_at.date(),
        release_code_sha256="9" * 64,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
    )
    with AutonomousSessionStore(authority_input.state_dir / "autonomous.sqlite3") as store:
        store.ensure_arm(conflicting_arm)
    backends = _RecordingBackends()
    factory_calls = 0

    def plan_factory(_):
        nonlocal factory_calls
        factory_calls += 1
        return backends.plan()

    with pytest.raises(AutonomousHostRejected, match="durable autonomous state conflicts"):
        run_autonomous_host_command(
            authority_input=authority_input,
            plan_factory=plan_factory,
            observation_timeline=(arm.hard_flat_at,),
        )

    assert factory_calls == 0
    assert backends.reconciliation_calls == []
    assert backends.collector_calls == []
    assert backends.candidate_calls == []
    assert backends.lifecycle_calls == []


def test_finalized_replay_ignores_an_earlier_timeline_and_novel_candidate(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    initial = _RecordingBackends(collect_candidate=False)
    terminal = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: initial.plan(),
        observation_timeline=(arm.hard_flat_at,),
    )
    assert terminal.disposition is AutonomousHostDisposition.TERMINAL

    replay = _RecordingBackends()
    plan_calls = 0

    def replay_plan(_):
        nonlocal plan_calls
        plan_calls += 1
        return replay.plan()

    replayed = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=replay_plan,
        observation_timeline=(arm.windows[0].opens_at, arm.windows[0].closes_at),
    )

    assert replayed.disposition is AutonomousHostDisposition.TERMINAL
    assert replayed.processed_opportunity_ids == ()
    assert replayed.requested_timeline_count == 2
    assert replayed.observed_at == terminal.observed_at == arm.hard_flat_at
    assert plan_calls == 1
    assert [request.phase for request in replay.reconciliation_calls] == ["FINAL"]
    assert replay.reconciliation_calls[0].observed_at == arm.hard_flat_at
    assert replay.collector_calls == []
    assert replay.candidate_calls == []
    assert replay.lifecycle_calls == []


def test_failed_final_reconciliation_on_replay_durably_escalates_manual_state(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    terminal = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: _RecordingBackends(collect_candidate=False).plan(),
        observation_timeline=(arm.hard_flat_at,),
    )
    assert terminal.disposition is AutonomousHostDisposition.TERMINAL

    uncertain = _RecordingBackends(
        collect_candidate=False,
        final_reconciliation_status=HostReconciliationStatus.INCOMPLETE,
    )
    escalated = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: uncertain.plan(),
        observation_timeline=(arm.windows[0].opens_at,),
    )
    assert escalated.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert escalated.manual_reasons == ("RECONCILIATION_INCOMPLETE",)
    assert uncertain.collector_calls == []
    assert uncertain.candidate_calls == []
    assert uncertain.lifecycle_calls == []

    complete = _RecordingBackends(collect_candidate=False)
    still_manual = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: complete.plan(),
        observation_timeline=(arm.windows[0].opens_at,),
    )
    assert still_manual.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert still_manual.manual_reasons == ("RECONCILIATION_INCOMPLETE",)
    assert [request.phase for request in complete.reconciliation_calls] == ["FINAL"]


def test_duplicate_candidates_and_restart_are_suppressed_and_receipt_is_self_hashed(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    backends = _RecordingBackends(duplicate_candidate=True)

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: backends.plan(),
        observation_timeline=(arm.windows[0].opens_at, arm.hard_flat_at),
    )

    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert len(backends.candidate_calls) == 1
    assert receipt.processed_opportunity_ids == ("OPPORTUNITY-1",)
    assert receipt.disposition_counts[AutonomousDisposition.TERMINAL_FLAT.value] == 1
    assert [request.phase for request in backends.reconciliation_calls] == ["STARTUP", "FINAL"]
    assert receipt.reconciliation_phase == "FINAL"
    assert receipt.reconciliation_broker_truth_sha256 == synthetic_broker_truth_sha256(
        backends.truth_by_phase["FINAL"]
    )

    payload = json.loads(receipt.to_json_bytes())
    receipt_sha256 = payload.pop("receipt_sha256")
    validated = validate_autonomous_host_authority(authority_input)
    assert payload["release_sha256"] == validated.release_sha256
    assert payload["arm_record_sha256"] == validated.arm_record_sha256
    assert payload["session_arm_sha256"] == validated.session_arm_sha256
    assert payload["runtime_build_artifact_sha256"] == BUILD_SHA256
    assert payload["runtime_code_revision"] == CODE_REVISION
    assert payload["account_capability_id"] == CAPABILITY_ID
    assert payload["account_fingerprint_sha256"] == ACCOUNT_FINGERPRINT_SHA256
    assert payload["data_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert payload["execution_class"] == "SYNTHETIC_FAKE"
    assert payload["claim_basis"] == "HOST_PLAN_ATTESTATION"
    assert tuple(payload["claims"]) == AUTONOMOUS_HOST_CLAIMS
    assert receipt_sha256 == hashlib.sha256(_canonical_json(payload)).hexdigest()
    truth = backends.truth_by_phase["FINAL"]
    assert (
        receipt.reconciliation_broker_truth_sha256
        == hashlib.sha256(synthetic_broker_truth_bytes(truth)).hexdigest()
    )
    assert synthetic_broker_truth_sha256(
        replace(truth, account_state_sha256="5" * 64)
    ) != synthetic_broker_truth_sha256(truth)

    restarted_backends = _RecordingBackends(duplicate_candidate=True)
    restarted = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: restarted_backends.plan(),
        observation_timeline=(arm.windows[0].opens_at, arm.hard_flat_at),
    )
    assert restarted.disposition is AutonomousHostDisposition.TERMINAL
    assert restarted.processed_opportunity_ids == ()
    assert restarted_backends.candidate_calls == []


def test_synthetic_abstention_reaches_terminal_without_a_lifecycle_mutation(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    backends = _RecordingBackends(candidate_mode="ABSTAINED")

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: backends.plan(),
        observation_timeline=(arm.windows[0].opens_at, arm.hard_flat_at),
    )

    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert receipt.disposition_counts[AutonomousDisposition.ABSTAINED.value] == 1
    assert receipt.processed_opportunity_ids == ("OPPORTUNITY-1",)
    assert len(backends.candidate_calls) == 1
    assert backends.lifecycle_calls == []


def test_synthetic_active_identity_is_replayed_and_closed_at_hard_flat(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    opening = _RecordingBackends(candidate_mode="ACTIVE")

    incomplete = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: opening.plan(),
        observation_timeline=(arm.windows[0].opens_at,),
    )

    assert incomplete.disposition is AutonomousHostDisposition.INCOMPLETE
    assert incomplete.active_lifecycle_ids == ("LIFECYCLE-1",)
    assert incomplete.reconciliation_phase == "CHECKPOINT"
    assert opening.reconciliation_calls[-1].phase == "CHECKPOINT"
    assert opening.reconciliation_calls[-1].active_lifecycle_ids == ("LIFECYCLE-1",)

    closing = _RecordingBackends(collect_candidate=False)
    terminal = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: closing.plan(),
        observation_timeline=(arm.hard_flat_at,),
    )

    assert terminal.disposition is AutonomousHostDisposition.TERMINAL
    assert terminal.active_lifecycle_ids == ()
    assert [request.lifecycle.lifecycle_id for request in closing.lifecycle_calls] == [
        "LIFECYCLE-1"
    ]
    assert closing.reconciliation_calls[-1].phase == "FINAL"
    assert closing.reconciliation_calls[-1].active_lifecycle_ids == ()


def test_ambiguous_candidate_outcome_freezes_manual_but_still_forces_final_reconciliation(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    backends = _RecordingBackends(candidate_mode="MANUAL")

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: backends.plan(),
        observation_timeline=(arm.windows[0].opens_at, arm.hard_flat_at),
    )

    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert receipt.manual_reasons == ("UNKNOWN_BROKER_STATE",)
    assert len(backends.candidate_calls) == 1
    assert backends.reconciliation_calls[-1].phase == "FINAL"
    assert receipt.reconciliation_phase == "FINAL"
    assert receipt.reconciliation_broker_truth_sha256 == synthetic_broker_truth_sha256(
        backends.truth_by_phase["FINAL"]
    )
    assert receipt.terminal_flat_proven is False


def test_incomplete_final_broker_truth_overrides_an_otherwise_terminal_summary(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)
    backends = _RecordingBackends(
        collect_candidate=False,
        final_reconciliation_status=HostReconciliationStatus.INCOMPLETE,
    )

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: backends.plan(),
        observation_timeline=(arm.hard_flat_at,),
    )

    assert [request.phase for request in backends.reconciliation_calls] == ["FINAL"]
    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert receipt.manual_reasons == ("RECONCILIATION_INCOMPLETE",)
    assert receipt.final_summary_sha256 is not None
    assert receipt.reconciliation_phase == "FINAL"
    assert receipt.reconciliation_broker_truth_sha256 is None
    assert receipt.terminal_flat_proven is False


@pytest.mark.parametrize(
    "truth_transform",
    [
        lambda _request, truth: replace(truth, session_id="OTHER-SESSION"),
        lambda _request, truth: replace(truth, session_arm_sha256="1" * 64),
        lambda _request, truth: replace(truth, account_fingerprint_sha256="2" * 64),
        lambda _request, truth: replace(truth, execution_protocol_sha256="3" * 64),
        lambda _request, truth: replace(
            truth,
            observed_at=truth.observed_at + timedelta(seconds=1),
        ),
        lambda _request, truth: replace(truth, phase="CHECKPOINT"),
        lambda _request, truth: replace(
            truth,
            active_lifecycle_ids=("UNEXPECTED-LIFECYCLE",),
            open_position_count=1,
            is_flat=False,
        ),
    ],
)
def test_tampered_synthetic_broker_truth_cannot_be_attributed_to_final_request(
    tmp_path: Path,
    truth_transform,
) -> None:
    authority_input, arm = _authority(tmp_path)
    backends = _RecordingBackends(
        collect_candidate=False,
        truth_transform=truth_transform,
    )

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: backends.plan(),
        observation_timeline=(arm.hard_flat_at,),
    )

    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert receipt.reconciliation_phase == "FINAL"
    assert receipt.reconciliation_broker_truth_sha256 is None
    assert receipt.terminal_flat_proven is False


def test_nonflat_final_truth_overrides_terminal_summary_and_requires_manual_reconciliation(
    tmp_path: Path,
) -> None:
    authority_input, arm = _authority(tmp_path)

    def nonflat(_request, truth):
        return replace(
            truth,
            open_position_count=1,
            is_flat=False,
        )

    backends = _RecordingBackends(
        collect_candidate=False,
        truth_transform=nonflat,
    )
    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=lambda _: backends.plan(),
        observation_timeline=(arm.hard_flat_at,),
    )

    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert receipt.manual_reasons == ("RECONCILIATION_INCOMPLETE",)
    assert receipt.final_summary_sha256 is not None
    assert receipt.reconciliation_broker_truth_sha256 is None
    assert receipt.terminal_flat_proven is False


@pytest.mark.parametrize(
    "changes",
    [
        {"active_lifecycle_ids": ("LIFECYCLE-UNRESOLVED",)},
        {"manual_reasons": ("UNRESOLVED_STATE",)},
        {"terminal_flat_proven": False},
        {
            "reconciliation_phase": "CHECKPOINT",
            "final_summary_sha256": None,
        },
        {
            "disposition": AutonomousHostDisposition.INCOMPLETE,
            "terminal_flat_proven": False,
        },
        {
            "disposition": AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED,
            "terminal_flat_proven": False,
        },
    ],
)
def test_receipt_factory_rejects_contradictory_terminal_state(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    authority_input, arm = _authority(tmp_path)
    values: dict[str, object] = {
        "authority": validate_autonomous_host_authority(authority_input),
        "disposition": AutonomousHostDisposition.TERMINAL,
        "observed_at": arm.hard_flat_at,
        "requested_timeline_count": 1,
        "processed_opportunity_ids": (),
        "disposition_counts": {},
        "active_lifecycle_ids": (),
        "manual_reasons": (),
        "final_summary_sha256": "4" * 64,
        "reconciliation_phase": "FINAL",
        "reconciliation_broker_truth_sha256": "5" * 64,
        "terminal_flat_proven": True,
    }
    values.update(changes)

    with pytest.raises(AutonomousHostRejected):
        AutonomousHostReceipt.create(**values)


def test_receipt_factory_rejects_free_form_manual_reason(tmp_path: Path) -> None:
    authority_input, arm = _authority(tmp_path)

    with pytest.raises(AutonomousHostRejected, match="allowlisted reason code"):
        AutonomousHostReceipt.create(
            authority=validate_autonomous_host_authority(authority_input),
            disposition=AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED,
            observed_at=arm.hard_flat_at,
            requested_timeline_count=1,
            processed_opportunity_ids=(),
            disposition_counts={},
            active_lifecycle_ids=(),
            manual_reasons=("raw secret-bearing prose",),
            final_summary_sha256="4" * 64,
            reconciliation_phase="FINAL",
            reconciliation_broker_truth_sha256=None,
            terminal_flat_proven=False,
        )


@pytest.mark.parametrize(
    "disposition_counts",
    [
        {AutonomousDisposition.ABSTAINED.value: True},
        {AutonomousDisposition.ABSTAINED.value: 1.5},
        {AutonomousDisposition.ABSTAINED.value: "1"},
        {"UNSUPPORTED_DISPOSITION": 1},
    ],
)
def test_receipt_factory_rejects_noncanonical_disposition_counts(
    tmp_path: Path,
    disposition_counts: dict[str, object],
) -> None:
    authority_input, arm = _authority(tmp_path)

    with pytest.raises(AutonomousHostRejected):
        AutonomousHostReceipt.create(
            authority=validate_autonomous_host_authority(authority_input),
            disposition=AutonomousHostDisposition.TERMINAL,
            observed_at=arm.hard_flat_at,
            requested_timeline_count=1,
            processed_opportunity_ids=(),
            disposition_counts=disposition_counts,
            active_lifecycle_ids=(),
            manual_reasons=(),
            final_summary_sha256="4" * 64,
            reconciliation_phase="FINAL",
            reconciliation_broker_truth_sha256="5" * 64,
            terminal_flat_proven=True,
        )
