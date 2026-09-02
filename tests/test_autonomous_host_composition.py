"""End-to-end synthetic composition tests for the autonomous host runner.

Every scenario drives the real coordinator through ``run_autonomous_host_command``
and the real application services through the composition backends.  All feeds,
quotes, fills, clocks, and broker state are deterministic synthetic fixtures;
no test performs a network, provider, broker, or account call.
"""

from __future__ import annotations

import ast
import hashlib
import json
import socket
import sys
import types
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from ringdown_market.application import autonomous_bridge
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
from ringdown_market.risk import RiskLedger, verify_passport
from ringdown_market.runtime import host_composition, host_fake_broker, host_persistence
from ringdown_market.runtime.autonomous import (
    ActiveLifecycleIdentity,
    AutonomousOpportunity,
    AutonomousSessionArm,
    LifecycleCloseRequest,
    autonomous_session_arm_bytes,
)
from ringdown_market.runtime.autonomous_host import (
    AutonomousHostAuthorityInput,
    AutonomousHostDisposition,
    AutonomousHostInvocation,
    AutonomousHostRejected,
    HostLifecycleCloserAdapter,
    run_autonomous_host_command,
)
from ringdown_market.runtime.host_composition import (
    EARNINGS_LANE_V2,
    MARKET_ANCHOR_LANE_V2,
    CompositionFeed,
    CompositionFeedEvent,
    composition_plan_factory,
    split_composition_fixture,
)
from ringdown_market.runtime.host_fake_broker import SyntheticPaperBroker
from ringdown_market.runtime.host_persistence import (
    HOST_PERSISTENCE_FILENAME,
    HostPersistenceSidecar,
)
from ringdown_market.sourcedata.fakes import load_fixture

BUILD_SHA256 = "b" * 64
CODE_REVISION = "a" * 40
CAPABILITY_ID = "PAPER-CAPABILITY-COMPOSITION"
SOURCE_IDS = ("ALPACA_MCP", "BENZINGA")
LEDGER_ID = "PAPER-LEDGER-COMPOSITION"
PROCESS_ID = "PAPER-PROCESS-COMPOSITION"
SESSION_ID = "ESSCHER-COMPOSITION-20260911"
SESSION_DATE = date(2026, 9, 11)
FIXTURE_EVENT_ID = "KR-2026Q2-EARNINGS"
NEUTRAL_LAST_KR_PRICE = "61.51"
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "imaplib",
        "requests",
        "smtplib",
        "socket",
        "socks",
        "ssl",
        "subprocess",
        "telnetlib",
        "urllib",
        "urllib3",
        "xmlrpc",
    }
)


def _loaded_fixture(*, neutral: bool = False) -> dict[str, object]:
    fixture = json.loads(json.dumps(load_fixture()))
    if neutral:
        trades = fixture["reaction_trades"]
        assert isinstance(trades, dict)
        kr_trades = trades["KR"]
        assert isinstance(kr_trades, list) and kr_trades
        kr_trades[-1]["price"] = NEUTRAL_LAST_KR_PRICE
    return fixture


def _feed_event(
    *,
    neutral: bool = False,
    window_id: str = "SCAN_1000_ET",
    candidate_id: str = EARNINGS_LANE_V2,
) -> CompositionFeedEvent:
    fixture = _loaded_fixture(neutral=neutral)
    evidence_bytes, market_bytes = split_composition_fixture(fixture)
    capture_at = datetime.fromisoformat(str(fixture["capture_at"]).replace("Z", "+00:00"))
    return CompositionFeedEvent(
        event_id=str(fixture["event_id"]),
        window_id=window_id,
        candidate_id=candidate_id,
        evidence_manifest_bytes=evidence_bytes,
        market_window_bytes=market_bytes,
        capture_at=capture_at,
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )


def _authority(
    tmp_path: Path,
    broker: SyntheticPaperBroker,
    *,
    fingerprint: str | None = None,
    session_id: str = SESSION_ID,
) -> tuple[AutonomousHostAuthorityInput, AutonomousSessionArm]:
    account_fingerprint = broker.account_state_sha256() if fingerprint is None else fingerprint
    session_arm = AutonomousSessionArm.for_trading_date(
        session_id=session_id,
        session_date=SESSION_DATE,
        release_code_sha256=BUILD_SHA256,
        account_fingerprint_sha256=account_fingerprint,
    )
    release = StrategyRelease(
        release_id="ESSCHER-PAPER-COMPOSITION-1",
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
        arm_id=session_id,
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
            account_fingerprint_sha256=account_fingerprint,
            source_ids=SOURCE_IDS,
            ledger_id=LEDGER_ID,
            process_id=PROCESS_ID,
            state_dir=tmp_path / "state",
        ),
        session_arm,
    )


def _window_point(arm: AutonomousSessionArm, index: int = 0) -> datetime:
    return arm.windows[index].opens_at + timedelta(minutes=5)


def _sidecar_entries(state_dir: Path) -> list[dict[str, object]]:
    path = state_dir / HOST_PERSISTENCE_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _receipt_payload(receipt) -> dict[str, object]:
    return json.loads(receipt.to_json_bytes())


def _assert_synthetic_claims(receipt) -> None:
    payload = _receipt_payload(receipt)
    assert payload["execution_class"] == "SYNTHETIC_FAKE"
    assert payload["data_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert payload["run_mode"] == "PAPER"
    assert "SYNTHETIC_FAKE" in payload["claims"]
    assert "NOT_ALPHA_EVIDENCE" in payload["claims"]
    assert "NOT_HISTORICAL_DATA" in payload["claims"]
    assert "HOST_PLAN_ATTESTS_NO_BROKER_EXECUTION" in payload["claims"]


def test_s1_neutral_confirmation_abstains_without_any_mutation(tmp_path: Path) -> None:
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(neutral=True),))

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=composition_plan_factory(feed, ledger=ledger, broker=broker),
        observation_timeline=(_window_point(arm), arm.hard_flat_at),
    )

    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert receipt.terminal_flat_proven is True
    assert receipt.processed_opportunity_ids == (f"OPP-{FIXTURE_EVENT_ID}-SCAN_1000_ET",)
    assert receipt.disposition_counts["ABSTAINED"] == 1
    assert receipt.disposition_counts["ACTIVE"] == 0
    assert receipt.manual_reasons == ()
    assert broker.open_submissions == 0
    assert broker.close_submissions == 0
    assert broker.is_flat()
    assert ledger.decision_episode_rows() == []
    assert ledger.outcome_episode_rows() == []
    assert _sidecar_entries(authority_input.state_dir) == []
    _assert_synthetic_claims(receipt)


def test_v2_lane_cannot_relabel_an_unrelated_v1_source_fixture(tmp_path: Path) -> None:
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(candidate_id=MARKET_ANCHOR_LANE_V2),))

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=composition_plan_factory(feed, ledger=ledger, broker=broker),
        observation_timeline=(_window_point(arm), arm.hard_flat_at),
    )

    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert receipt.disposition_counts["REJECTED_BEFORE_MUTATION"] == 1
    assert broker.open_submissions == 0
    assert broker.close_submissions == 0
    assert ledger.decision_episode_rows() == []
    assert ledger.outcome_episode_rows() == []


def test_s2_accepted_candidate_opens_atomically_and_flattens_at_hard_flat(
    tmp_path: Path,
) -> None:
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=composition_plan_factory(feed, ledger=ledger, broker=broker),
        observation_timeline=(_window_point(arm), arm.hard_flat_at),
    )

    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert receipt.terminal_flat_proven is True
    assert receipt.disposition_counts["TERMINAL_FLAT"] == 1
    assert receipt.processed_opportunity_ids == (f"OPP-{FIXTURE_EVENT_ID}-SCAN_1000_ET",)
    assert receipt.active_lifecycle_ids == ()
    assert receipt.manual_reasons == ()
    assert receipt.reconciliation_phase == "FINAL"
    assert receipt.reconciliation_broker_truth_sha256 is not None
    assert receipt.final_summary_sha256 is not None
    assert broker.open_submissions == 1
    assert broker.close_submissions == 1
    assert broker.is_flat()

    decisions = ledger.decision_episode_rows()
    outcomes = ledger.outcome_episode_rows()
    assert len(decisions) == 1
    assert decisions[0]["disposition"] == "ACCEPTED"
    assert decisions[0]["direction"] == "UP"
    assert decisions[0]["event_id"] == FIXTURE_EVENT_ID
    assert len(outcomes) == 1
    assert outcomes[0]["lifecycle_outcome"] == "CLOSED"
    assert int(outcomes[0]["final_flat"]) == 1
    assert outcomes[0]["decision_episode_id"] == decisions[0]["episode_id"]
    passport = ledger.passport_events()
    assert verify_passport(passport) == len(passport)
    assert ledger.reservation_for_event(FIXTURE_EVENT_ID)["state"] == "RELEASED"

    entries = _sidecar_entries(authority_input.state_dir)
    kinds = [str(entry["kind"]) for entry in entries]
    assert kinds.count("ACTIVE") == 1
    assert kinds.count("TERMINAL") == 1
    sidecar = HostPersistenceSidecar(authority_input.state_dir / HOST_PERSISTENCE_FILENAME)
    assert sidecar.chain_valid()
    lifecycle_id = str(entries[0]["lifecycle_id"])
    assert sidecar.is_terminal(lifecycle_id)
    terminal_payload = entries[-1]["payload"]
    assert isinstance(terminal_payload, dict)
    proof = terminal_payload["terminal_flat_proof_sha256"]
    assert isinstance(proof, str) and len(proof) == 64
    _assert_synthetic_claims(receipt)


def test_s3_restart_rehydrates_close_only_authority_from_the_sidecar(
    tmp_path: Path,
) -> None:
    broker = SyntheticPaperBroker()
    ledger_path = tmp_path / "risk.sqlite3"
    first_ledger = RiskLedger(ledger_path)
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))

    opening = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=composition_plan_factory(feed, ledger=first_ledger, broker=broker),
        observation_timeline=(_window_point(arm),),
    )

    assert opening.disposition is AutonomousHostDisposition.INCOMPLETE
    assert opening.reconciliation_phase == "CHECKPOINT"
    assert opening.final_summary_sha256 is None
    assert len(opening.active_lifecycle_ids) == 1
    lifecycle_id = opening.active_lifecycle_ids[0]
    assert broker.open_submissions == 1
    assert broker.close_submissions == 0
    assert not broker.is_flat()
    sidecar = HostPersistenceSidecar(authority_input.state_dir / HOST_PERSISTENCE_FILENAME)
    active_bundle = sidecar.rehydrate(lifecycle_id)
    expected_opportunity = _opportunity_for(arm, feed.events[0])
    assert active_bundle is not None
    assert active_bundle.opportunity_id == expected_opportunity.opportunity_id
    assert active_bundle.opportunity_sha256 == expected_opportunity.opportunity_sha256
    first_ledger.close()

    restarted_ledger = RiskLedger(ledger_path)
    closing = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=composition_plan_factory(feed, ledger=restarted_ledger, broker=broker),
        observation_timeline=(arm.hard_flat_at,),
    )

    assert closing.disposition is AutonomousHostDisposition.TERMINAL
    assert closing.terminal_flat_proven is True
    assert closing.processed_opportunity_ids == ()
    assert closing.active_lifecycle_ids == ()
    assert closing.disposition_counts["TERMINAL_FLAT"] == 1
    assert broker.open_submissions == 1
    assert broker.close_submissions == 1
    assert broker.is_flat()
    outcomes = restarted_ledger.outcome_episode_rows()
    assert len(restarted_ledger.decision_episode_rows()) == 1
    assert len(outcomes) == 1
    assert int(outcomes[0]["final_flat"]) == 1
    assert sidecar.is_terminal(lifecycle_id)
    _assert_synthetic_claims(closing)


def test_s4_duplicate_opportunity_replay_processes_nothing_new(tmp_path: Path) -> None:
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))
    factory = composition_plan_factory(feed, ledger=ledger, broker=broker)

    opening = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=factory,
        observation_timeline=(_window_point(arm),),
    )
    assert opening.disposition is AutonomousHostDisposition.INCOMPLETE
    assert opening.processed_opportunity_ids == (f"OPP-{FIXTURE_EVENT_ID}-SCAN_1000_ET",)
    decisions_before = len(ledger.decision_episode_rows())

    replay = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=factory,
        observation_timeline=(_window_point(arm), arm.hard_flat_at),
    )

    replay_state = factory.states[-1]
    assert replay.disposition is AutonomousHostDisposition.TERMINAL
    assert replay.terminal_flat_proven is True
    assert replay.processed_opportunity_ids == ()
    assert replay_state.candidate_calls == 0
    assert replay_state.collector_calls == 1
    assert broker.open_submissions == 1
    assert broker.close_submissions == 1
    assert len(ledger.decision_episode_rows()) == decisions_before == 1
    assert len(ledger.outcome_episode_rows()) == 1


def test_s5_ambiguous_open_freezes_manual_without_retry_or_new_exposure(
    tmp_path: Path,
) -> None:
    broker = SyntheticPaperBroker()
    broker.ambiguous_open = True
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))
    factory = composition_plan_factory(feed, ledger=ledger, broker=broker)

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=factory,
        observation_timeline=(_window_point(arm), _window_point(arm, 1), arm.hard_flat_at),
    )

    state = factory.states[-1]
    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert receipt.manual_reasons == ("UNKNOWN_BROKER_STATE",)
    assert receipt.terminal_flat_proven is False
    assert receipt.disposition_counts["MANUAL_RECONCILIATION_REQUIRED"] == 1
    assert receipt.processed_opportunity_ids == (f"OPP-{FIXTURE_EVENT_ID}-SCAN_1000_ET",)
    assert receipt.reconciliation_phase == "FINAL"
    assert state.candidate_calls == 1
    assert state.collector_calls == 1
    assert broker.open_submissions == 1
    assert broker.close_submissions == 0
    permit_row = ledger.permit_for_event(FIXTURE_EVENT_ID)
    assert permit_row is not None
    assert ledger.submission_for_permit(str(permit_row["permit_id"])) is None
    assert ledger.decision_episode_rows() == []
    assert ledger.outcome_episode_rows() == []
    assert ledger.reservation_for_event(FIXTURE_EVENT_ID)["state"] == "RESERVED"
    _assert_synthetic_claims(receipt)


def test_account_fingerprint_mismatch_is_rejected_before_any_port(tmp_path: Path) -> None:
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker, fingerprint="f" * 64)
    feed = CompositionFeed(events=(_feed_event(),))
    factory = composition_plan_factory(feed, ledger=ledger, broker=broker)

    with pytest.raises(AutonomousHostRejected, match="ACCOUNT_FINGERPRINT_MISMATCH"):
        run_autonomous_host_command(
            authority_input=authority_input,
            plan_factory=factory,
            observation_timeline=(_window_point(arm), arm.hard_flat_at),
        )

    assert factory.states == []
    assert broker.open_submissions == 0
    assert not (authority_input.state_dir / HOST_PERSISTENCE_FILENAME).exists()
    assert ledger.decision_episode_rows() == []


def test_sidecar_chain_detects_tampering(tmp_path: Path) -> None:
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))
    run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=composition_plan_factory(feed, ledger=ledger, broker=broker),
        observation_timeline=(_window_point(arm), arm.hard_flat_at),
    )
    sidecar_path = authority_input.state_dir / HOST_PERSISTENCE_FILENAME
    sidecar = HostPersistenceSidecar(sidecar_path)
    assert sidecar.chain_valid()

    raw = sidecar_path.read_bytes()
    tampered = raw.replace(b'"kind":"ACTIVE"', b'"kind":"ACTIVF"', 1)
    assert tampered != raw
    sidecar_path.write_bytes(tampered)
    assert not sidecar.chain_valid()


def test_rehashed_sidecar_opportunity_forgery_cannot_rebuild_close_authority(
    tmp_path: Path,
) -> None:
    broker = SyntheticPaperBroker()
    ledger_path = tmp_path / "risk.sqlite3"
    ledger = RiskLedger(ledger_path)
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))
    factory = composition_plan_factory(feed, ledger=ledger, broker=broker)
    opening = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=factory,
        observation_timeline=(_window_point(arm),),
    )
    assert opening.disposition is AutonomousHostDisposition.INCOMPLETE
    ledger.close()

    sidecar_path = authority_input.state_dir / HOST_PERSISTENCE_FILENAME
    entries = _sidecar_entries(authority_input.state_dir)
    assert len(entries) == 1
    entry = entries[0]
    payload = entry["payload"]
    assert isinstance(payload, dict)
    payload["opportunity_sha256"] = "f" * 64
    unsigned = {key: value for key, value in entry.items() if key != "entry_sha256"}
    entry["entry_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    sidecar_path.write_bytes(
        json.dumps(
            entry,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )

    lifecycle_id = opening.active_lifecycle_ids[0]
    lifecycle = ActiveLifecycleIdentity.for_candidate(
        arm=arm,
        opportunity=_opportunity_for(arm, feed.events[0]),
        lifecycle_id=lifecycle_id,
    )
    close_request = LifecycleCloseRequest(
        arm=arm,
        lifecycle=lifecycle,
        observed_at=arm.hard_flat_at,
    )
    direct_result = HostLifecycleCloserAdapter(
        host_composition.CompositionLifecycleBackend(factory.states[-1])
    ).close_and_reconcile(close_request)
    assert direct_result.reason_code == "CLAIM_RECOVERY_UNKNOWN"

    restarted_ledger = RiskLedger(ledger_path)
    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=composition_plan_factory(
            feed,
            ledger=restarted_ledger,
            broker=broker,
        ),
        observation_timeline=(arm.hard_flat_at,),
    )

    assert receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
    assert receipt.manual_reasons == (
        "CLAIM_RECOVERY_UNKNOWN",
        "HARD_FLAT_UNRESOLVED",
        "RECONCILIATION_INCOMPLETE",
    )
    assert broker.open_submissions == 1
    assert broker.close_submissions == 0
    assert not broker.is_flat()


def test_close_fault_with_residual_positions_maps_to_manual_at_backend_level(
    tmp_path: Path,
) -> None:
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))
    factory = composition_plan_factory(feed, ledger=ledger, broker=broker)
    opening = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=factory,
        observation_timeline=(_window_point(arm),),
    )
    assert opening.disposition is AutonomousHostDisposition.INCOMPLETE
    broker.residual_position_after_close = True

    state = factory.states[-1]
    lifecycle = state.sidecar.active_bundles(arm.session_id)[0]
    request = LifecycleCloseRequest(
        arm=arm,
        lifecycle=ActiveLifecycleIdentity.for_candidate(
            arm=arm,
            opportunity=_opportunity_for(arm, feed.events[0]),
            lifecycle_id=lifecycle.lifecycle_id,
        ),
        observed_at=arm.hard_flat_at,
    )
    backend = host_composition.CompositionLifecycleBackend(state)
    outcome = backend.close_lifecycle(request)

    assert outcome.disposition.value == "MANUAL_RECONCILIATION_REQUIRED"
    assert outcome.mutation_state.value == "PARTIAL"
    assert outcome.reason_code == "UNKNOWN_BROKER_STATE"
    assert outcome.terminal_flat_proof_sha256 is None
    assert not state.sidecar.is_terminal(lifecycle.lifecycle_id)


def _opportunity_for(
    arm: AutonomousSessionArm, event: CompositionFeedEvent
) -> AutonomousOpportunity:
    return AutonomousOpportunity.for_window(
        arm=arm,
        window_id=event.window_id,
        opportunity_id=event.opportunity_id,
        candidate_id=event.candidate_id,
        strategy_context_sha256=event.strategy_context_sha256(
            next(
                window.window_sha256
                for window in arm.windows
                if window.window_id == event.window_id
            )
        ),
    )


def _install_cli_plan(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    *,
    authority_input: AutonomousHostAuthorityInput,
    timeline: tuple[datetime, ...],
    plan_factory,
) -> str:
    def build_invocation(
        *,
        release_bytes: bytes,
        arm_record_bytes: bytes,
        state_dir: Path,
    ) -> AutonomousHostInvocation:
        return AutonomousHostInvocation(
            authority_input=replace(
                authority_input,
                release_bytes=release_bytes,
                arm_record_bytes=arm_record_bytes,
                state_dir=state_dir,
            ),
            observation_timeline=timeline,
            plan_factory=plan_factory,
        )

    module = types.ModuleType(module_name)
    module.build_invocation = build_invocation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, module)
    return f"{module_name}:build_invocation"


def _run_cli(
    tmp_path: Path, authority_input: AutonomousHostAuthorityInput, selector: str, state_dir: Path
) -> int:
    release_path = tmp_path / "release.json"
    arm_path = tmp_path / "arm.json"
    release_path.write_bytes(authority_input.release_bytes)
    arm_path.write_bytes(authority_input.arm_record_bytes)
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


def test_cli_composition_terminal_run_exits_zero_with_synthetic_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))
    selector = _install_cli_plan(
        monkeypatch,
        "test_cli_composition_terminal_plan",
        authority_input=authority_input,
        timeline=(_window_point(arm), arm.hard_flat_at),
        plan_factory=composition_plan_factory(feed, ledger=ledger, broker=broker),
    )

    exit_code = _run_cli(tmp_path, authority_input, selector, tmp_path / "state")

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["disposition"] == "TERMINAL"
    assert payload["terminal_flat_proven"] is True
    assert payload["execution_class"] == "SYNTHETIC_FAKE"
    assert payload["data_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert "SYNTHETIC_FAKE" in payload["claims"]
    assert "NOT_ALPHA_EVIDENCE" in payload["claims"]
    assert broker.is_flat()


def test_cli_rejected_authority_exits_two_before_the_composition_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
            "test_cli_never_imported:build_invocation",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["disposition"] == "REJECTED"
    assert payload["error_code"] == "AUTHORITY_INPUT_REJECTED"
    assert payload["broker_mutation"] == "NOT_ATTEMPTED"


def test_cli_manual_reconciliation_exits_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = SyntheticPaperBroker()
    broker.ambiguous_open = True
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))
    selector = _install_cli_plan(
        monkeypatch,
        "test_cli_composition_manual_plan",
        authority_input=authority_input,
        timeline=(_window_point(arm), arm.hard_flat_at),
        plan_factory=composition_plan_factory(feed, ledger=ledger, broker=broker),
    )

    exit_code = _run_cli(tmp_path, authority_input, selector, tmp_path / "state")

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert payload["disposition"] == "MANUAL_RECONCILIATION_REQUIRED"
    assert payload["manual_reasons"] == ["UNKNOWN_BROKER_STATE"]
    assert payload["terminal_flat_proven"] is False
    assert broker.open_submissions == 1


def test_new_composition_modules_import_no_network_or_subprocess_surface() -> None:
    modules = (host_composition, host_fake_broker, host_persistence, autonomous_bridge)
    for module in modules:
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in FORBIDDEN_IMPORT_ROOTS, (module.__name__, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                root = node.module.split(".")[0]
                assert root not in FORBIDDEN_IMPORT_ROOTS, (module.__name__, node.module)


def test_terminal_scenario_runs_with_the_socket_surface_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    def deny_getaddrinfo(*args: object, **kwargs: object) -> None:
        attempts.append("getaddrinfo")
        raise AssertionError("synthetic composition attempted name resolution")

    def deny_create_connection(*args: object, **kwargs: object) -> None:
        attempts.append("create_connection")
        raise AssertionError("synthetic composition attempted a network connection")

    monkeypatch.setattr(socket, "getaddrinfo", deny_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", deny_create_connection)
    broker = SyntheticPaperBroker()
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    authority_input, arm = _authority(tmp_path, broker)
    feed = CompositionFeed(events=(_feed_event(),))

    receipt = run_autonomous_host_command(
        authority_input=authority_input,
        plan_factory=composition_plan_factory(feed, ledger=ledger, broker=broker),
        observation_timeline=(_window_point(arm), arm.hard_flat_at),
    )

    assert attempts == []
    assert receipt.disposition is AutonomousHostDisposition.TERMINAL
    assert receipt.terminal_flat_proven is True
