"""End-to-end synthetic tests for the deadline-aware application service.

Every scenario drives the real stage chain over the frozen fixture feed, the
real prepare/open/close application path, the synthetic in-memory broker, and
the durable option-event journal.  All clocks are injected and deterministic;
no test performs a network, provider, broker, or account call.
"""

from __future__ import annotations

import ast
import json
import socket
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest

from ringdown_market.contracts.latency_profile import load_latency_profile
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
from ringdown_market.risk import RiskLedger
from ringdown_market.runtime import (
    autonomous_application_service,
    health_receipts,
    stage_budgets,
)
from ringdown_market.runtime.autonomous import (
    AutonomousSessionArm,
    DueWindowRequest,
    autonomous_session_arm_bytes,
)
from ringdown_market.runtime.autonomous_application_service import (
    EXPOSURE_JOURNAL_FILENAME,
    REASON_BUDGET_VIOLATION,
    REASON_CAPTURE_REJECTED,
    REASON_DEADLINE_EXHAUSTED,
    REASON_MANUAL_RECONCILIATION_STICKY,
    REASON_RECONCILIATION_MANUAL_REQUIRED,
    REASON_UPSTREAM_STOPPED,
    STAGE_DECISION,
    STAGE_EVIDENCE_CAPTURE,
    STAGE_ORDER,
    STAGE_RECONCILIATION,
    ApplicationServiceStopped,
    AutonomousApplicationService,
    RunDisposition,
    StageStatus,
    WindowOptionActivityFeed,
    exposure_state_sha256,
    stage_receipt_sha256,
    verify_stage_receipt_chain,
)
from ringdown_market.runtime.autonomous_host import (
    AutonomousHostAuthorityInput,
    ValidatedAutonomousHostAuthority,
    validate_autonomous_host_authority,
)
from ringdown_market.runtime.health_receipts import (
    CircuitState,
    health_receipt_bytes,
    health_receipt_sha256,
)
from ringdown_market.runtime.host_composition import (
    EARNINGS_LANE_V2,
    CompositionFeed,
    CompositionFeedEvent,
    rehearsal_timeline,
    split_composition_fixture,
)
from ringdown_market.runtime.host_fake_broker import SyntheticPaperBroker
from ringdown_market.runtime.host_persistence import (
    HOST_PERSISTENCE_FILENAME,
    HostPersistenceSidecar,
)
from ringdown_market.runtime.option_events import (
    AssetClass,
    EvidenceClass,
    NormalizedOptionEvent,
    OptionActivityCoverage,
    OptionEventKind,
    OptionEventStatus,
    OptionPortfolioObservation,
    OptionReconciliationState,
    PortfolioPosition,
)
from ringdown_market.runtime.stage_budgets import (
    StageBudgets,
    StageBudgetsRejected,
    arm_window_set_sha256,
    derive_stage_budgets,
    stage_budgets_sha256,
    validate_stage_budgets_within_window,
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
    load_fixture,
)
from ringdown_market.strategy.contracts import canonical_json_bytes

BUILD_SHA256 = "b" * 64
CODE_REVISION = "a" * 40
CAPABILITY_ID = "PAPER-CAPABILITY-APPLICATION-SERVICE"
SOURCE_IDS = ("ALPACA_MCP", "BENZINGA")
LEDGER_ID = "PAPER-LEDGER-APPLICATION-SERVICE"
PROCESS_ID = "PAPER-PROCESS-APPLICATION-SERVICE"
SESSION_ID = "ESSCHER-APPSVC-20260911"
SESSION_DATE = date(2026, 9, 11)
FIXTURE_EVENT_ID = "KR-2026Q2-EARNINGS"
STAGE_CLOCK = datetime(2026, 9, 11, 13, 36, 0, tzinfo=UTC)
WINDOW_OBSERVED_AT_OFFSET = timedelta(minutes=5)
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


def _loaded_fixture() -> dict[str, object]:
    return json.loads(json.dumps(load_fixture()))


def _feed_event(
    *,
    window_id: str = "SCAN_1000_ET",
    candidate_id: str = EARNINGS_LANE_V2,
    corrupt_evidence: bool = False,
) -> CompositionFeedEvent:
    fixture = _loaded_fixture()
    evidence_bytes, market_bytes = split_composition_fixture(fixture)
    if corrupt_evidence:
        evidence = json.loads(evidence_bytes.decode("utf-8"))
        del evidence["sessions"]
        evidence_bytes = canonical_json_bytes(evidence)
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
    root: Path,
    broker: SyntheticPaperBroker,
    *,
    session_id: str = SESSION_ID,
) -> tuple[AutonomousHostAuthorityInput, AutonomousSessionArm]:
    account_fingerprint = broker.account_state_sha256()
    session_arm = AutonomousSessionArm.for_trading_date(
        session_id=session_id,
        session_date=SESSION_DATE,
        release_code_sha256=BUILD_SHA256,
        account_fingerprint_sha256=account_fingerprint,
    )
    release = StrategyRelease(
        release_id=f"ESSCHER-PAPER-APPSVC-{session_id}",
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
    release_log_path = root / "releases.sqlite3"
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
            state_dir=root / "state",
        ),
        session_arm,
    )


@dataclass(frozen=True, slots=True)
class _Rig:
    authority_input: AutonomousHostAuthorityInput
    authority: ValidatedAutonomousHostAuthority
    arm: AutonomousSessionArm
    broker: SyntheticPaperBroker
    ledger: RiskLedger
    feed: CompositionFeed
    sidecar: HostPersistenceSidecar
    budgets: StageBudgets

    @property
    def session_id(self) -> str:
        return self.arm.session_id


def _rig(
    tmp_path: Path,
    *,
    name: str = "primary",
    session_id: str = SESSION_ID,
    corrupt_evidence: bool = False,
    broker: SyntheticPaperBroker | None = None,
    ledger: RiskLedger | None = None,
    window_id: str = "SCAN_1000_ET",
) -> _Rig:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    synthetic_broker = broker or SyntheticPaperBroker()
    risk_ledger = ledger or RiskLedger(root / "risk.sqlite3")
    authority_input, arm = _authority(root, synthetic_broker, session_id=session_id)
    authority = validate_autonomous_host_authority(authority_input)
    feed = CompositionFeed(
        events=(_feed_event(window_id=window_id, corrupt_evidence=corrupt_evidence),)
    )
    budgets = derive_stage_budgets(profile=load_latency_profile(), arm=arm)
    sidecar = HostPersistenceSidecar(authority.state_dir / HOST_PERSISTENCE_FILENAME)
    return _Rig(
        authority_input=authority_input,
        authority=authority,
        arm=arm,
        broker=synthetic_broker,
        ledger=risk_ledger,
        sidecar=sidecar,
        budgets=budgets,
        feed=feed,
    )


def _constant_clock() -> datetime:
    return STAGE_CLOCK


def _service(
    rig: _Rig,
    *,
    option_events: tuple[WindowOptionActivityFeed, ...] = (),
    clock=None,
    reconciliation_fault: str | None = None,
) -> AutonomousApplicationService:
    return AutonomousApplicationService(
        authority=rig.authority,
        feed=rig.feed,
        broker=rig.broker,
        ledger=rig.ledger,
        sidecar=rig.sidecar,
        budgets=rig.budgets,
        clock=clock or _constant_clock,
        option_events=option_events,
        reconciliation_fault=reconciliation_fault,
    )


def _request(
    rig: _Rig, *, window_index: int = 0, observed_at: datetime | None = None
) -> DueWindowRequest:
    window = rig.arm.windows[window_index]
    return DueWindowRequest(
        arm=rig.arm,
        window=window,
        observed_at=observed_at or (window.opens_at + WINDOW_OBSERVED_AT_OFFSET),
    )


@lru_cache(maxsize=1)
def _fixture_facts() -> tuple[datetime, date, str, str]:
    fixture = _loaded_fixture()
    capture_at = datetime.fromisoformat(str(fixture["capture_at"]).replace("Z", "+00:00"))
    configuration = CaptureConfiguration(
        candidate_manifest_bytes=build_candidate_manifest(fixture),
        event_id=str(fixture["event_id"]),
        capture_at=capture_at,
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )
    probe = compile_strategy_snapshot(
        configuration,
        FixtureEvidenceSource(fixture),
        FixtureMarketDataSource(fixture),
    )
    joined = compiled_strategy_input(probe)
    timeline = rehearsal_timeline(joined)
    expiry = (timeline.authorization_at.astimezone(UTC) + timedelta(days=7)).date()
    long_symbol = f"KR{expiry:%y%m%d}C00061000"
    short_symbol = f"KR{expiry:%y%m%d}C00062000"
    return timeline.open_at, expiry, long_symbol, short_symbol


def _bundle(
    rig: _Rig,
    *,
    scenario: str,
    window_id: str = "SCAN_1000_ET",
) -> WindowOptionActivityFeed:
    activation_at, expiry, long_symbol, short_symbol = _fixture_facts()
    arm = rig.arm
    fingerprint = arm.account_fingerprint_sha256
    protocol = arm.execution_protocol_sha256
    current_at = activation_at + timedelta(minutes=10)
    session_close = datetime(expiry.year, expiry.month, expiry.day, 20, 0, tzinfo=UTC)
    horizon = session_close + timedelta(days=1)

    def observation(
        observed_at: datetime, positions, source_sha: str
    ) -> OptionPortfolioObservation:
        return OptionPortfolioObservation.create(
            account_fingerprint_sha256=fingerprint,
            execution_protocol_sha256=protocol,
            observed_at=observed_at,
            positions=positions,
            source_payload_sha256=source_sha,
            evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        )

    def coverage(observed_at: datetime, event_sha256s) -> OptionActivityCoverage:
        return OptionActivityCoverage.create(
            account_fingerprint_sha256=fingerprint,
            execution_protocol_sha256=protocol,
            window_start=activation_at,
            window_end=observed_at,
            observed_at=observed_at,
            complete=True,
            event_sha256s=event_sha256s,
            source_payload_sha256="4" * 64,
            evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        )

    def activity(
        *,
        activity_id: str,
        kind: OptionEventKind,
        symbol: str,
        effective_date: date,
        observed_at: datetime,
        underlying_delta: Decimal,
        cash_delta: Decimal,
    ) -> NormalizedOptionEvent:
        return NormalizedOptionEvent.create(
            activity_id=activity_id,
            kind=kind,
            status=OptionEventStatus.EXECUTED,
            option_symbol=symbol,
            contracts=1,
            effective_date=effective_date,
            observed_at=observed_at,
            account_fingerprint_sha256=fingerprint,
            execution_protocol_sha256=protocol,
            underlying_symbol="KR",
            underlying_quantity_delta=underlying_delta,
            cash_delta=cash_delta,
            replacement_symbol=None,
            source_payload_sha256="5" * 64,
            evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        )

    long_leg = PortfolioPosition(AssetClass.OPTION, long_symbol, Decimal(1))
    short_leg = PortfolioPosition(AssetClass.OPTION, short_symbol, Decimal(-1))
    activation = observation(activation_at, (long_leg, short_leg), "1" * 64)

    if scenario == "ACTIVE_UNCHANGED":
        current = observation(current_at, (long_leg, short_leg), "3" * 64)
        events: tuple[NormalizedOptionEvent, ...] = ()
        activity_coverage = coverage(current_at, ())
    elif scenario == "ASSIGNMENT":
        assignment = activity(
            activity_id="ACT-ASSIGN-0001",
            kind=OptionEventKind.ASSIGNMENT,
            symbol=short_symbol,
            effective_date=activation_at.date(),
            observed_at=activation_at + timedelta(minutes=5),
            underlying_delta=Decimal(-100),
            cash_delta=Decimal(6200),
        )
        current = observation(
            current_at,
            (long_leg, PortfolioPosition(AssetClass.EQUITY, "KR", Decimal(-100))),
            "3" * 64,
        )
        events = (assignment,)
        activity_coverage = coverage(current_at, (assignment.event_sha256,))
    elif scenario == "EXERCISE":
        exercise = activity(
            activity_id="ACT-EXERCISE-0001",
            kind=OptionEventKind.EXERCISE,
            symbol=long_symbol,
            effective_date=activation_at.date(),
            observed_at=activation_at + timedelta(minutes=5),
            underlying_delta=Decimal(100),
            cash_delta=Decimal(-6100),
        )
        current = observation(
            current_at,
            (short_leg, PortfolioPosition(AssetClass.EQUITY, "KR", Decimal(100))),
            "3" * 64,
        )
        events = (exercise,)
        activity_coverage = coverage(current_at, (exercise.event_sha256,))
    elif scenario == "EXPIRY":
        long_expiry = activity(
            activity_id="ACT-EXPIRY-LONG",
            kind=OptionEventKind.EXPIRY,
            symbol=long_symbol,
            effective_date=expiry,
            observed_at=horizon,
            underlying_delta=Decimal(0),
            cash_delta=Decimal(0),
        )
        short_expiry = activity(
            activity_id="ACT-EXPIRY-SHORT",
            kind=OptionEventKind.EXPIRY,
            symbol=short_symbol,
            effective_date=expiry,
            observed_at=horizon,
            underlying_delta=Decimal(0),
            cash_delta=Decimal(0),
        )
        current = observation(horizon, (), "3" * 64)
        events = (long_expiry, short_expiry)
        activity_coverage = coverage(
            horizon,
            (long_expiry.event_sha256, short_expiry.event_sha256),
        )
    elif scenario == "DIVERGENT":
        current = observation(current_at, (long_leg,), "3" * 64)
        events = ()
        activity_coverage = coverage(current_at, ())
    else:
        raise AssertionError(f"unsupported scenario {scenario}")
    return WindowOptionActivityFeed(
        window_id=window_id,
        activation_observation=activation,
        current_observation=current,
        activity_coverage=activity_coverage,
        events=events,
        expiration_session_date=expiry,
        expiration_session_close=session_close,
        expiration_activity_horizon=horizon,
        calendar_sha256="2" * 64,
    )


def _sidecar_entries(rig: _Rig) -> list[dict[str, object]]:
    path = rig.authority.state_dir / HOST_PERSISTENCE_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _exposure_entries(rig: _Rig) -> list[dict[str, object]]:
    path = rig.authority.state_dir / EXPOSURE_JOURNAL_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _staleness_by_source(health) -> dict[str, object]:
    return {entry.source_id: entry for entry in health.staleness}


class _ScriptedClock:
    """Deterministic call-ordered clock whose final value repeats forever."""

    def __init__(self, values: tuple[datetime, ...]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> datetime:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value


def test_stage_budgets_derivation_and_window_validation(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    profile = load_latency_profile()
    budgets = derive_stage_budgets(profile=profile, arm=rig.arm)

    assert budgets.reasoner_ms == profile.p95_latency_ms == 30000
    assert budgets.market_data_ms == profile.p95_latency_ms
    assert budgets.broker_ms == profile.p95_latency_ms
    assert budgets.shutdown_reserve_ms == profile.p95_latency_ms
    assert budgets.retry_backoff_ms == 0
    assert budgets.profile_sha256 == profile.content_sha256
    assert budgets.arm_window_sha256 == arm_window_set_sha256(rig.arm)
    assert stage_budgets_sha256(budgets) == stage_budgets_sha256(
        derive_stage_budgets(profile=profile, arm=rig.arm)
    )
    validate_stage_budgets_within_window(budgets, rig.arm)

    oversized = replace(budgets, reasoner_ms=10_000_000)
    with pytest.raises(StageBudgetsRejected):
        validate_stage_budgets_within_window(oversized, rig.arm)
    foreign_arm = AutonomousSessionArm.for_trading_date(
        session_id="ESSCHER-APPSVC-FOREIGN",
        session_date=SESSION_DATE,
        release_code_sha256=BUILD_SHA256,
        account_fingerprint_sha256=rig.arm.account_fingerprint_sha256,
    )
    with pytest.raises(StageBudgetsRejected):
        validate_stage_budgets_within_window(budgets, foreign_arm)


def test_causal_identity_chain_across_all_stages(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    bundle = _bundle(rig, scenario="ACTIVE_UNCHANGED")
    service = _service(rig, option_events=(bundle,))
    try:
        result = service.run_window(request=_request(rig))

        assert result.disposition is RunDisposition.COMPLETED
        assert result.lifecycle_id is not None
        assert result.lifecycle_id.startswith("rd-permit-")
        receipts = result.stage_receipts
        assert tuple(item.stage for item in receipts) == STAGE_ORDER
        assert all(item.status is StageStatus.OK for item in receipts)
        verify_stage_receipt_chain(receipts, arm_sha256=rig.arm.arm_sha256)
        assert receipts[0].prior_stage_sha256 == rig.arm.arm_sha256
        assert receipts[0].input_sha256 == rig.feed.events[0].strategy_context_sha256(
            rig.arm.windows[0].window_sha256
        )
        for index in range(1, len(receipts)):
            assert receipts[index].prior_stage_sha256 == stage_receipt_sha256(receipts[index - 1])

        assert len(result.option_receipt_sha256s) == 1
        stored = service.option_journal.latest_receipt(rig.session_id, result.lifecycle_id)
        assert stored is not None
        assert stored.state is OptionReconciliationState.ACTIVE_UNCHANGED
        assert stored.receipt_sha256 == result.option_receipt_sha256s[0]

        terminal = service.terminal_receipt()
        payload = json.loads(terminal.to_json_bytes())
        assert payload["schema"] == "esscher.application_service_terminal_receipt"
        assert payload["arm_sha256"] == rig.arm.arm_sha256
        assert payload["claims"] == ["SYNTHETIC_FAKE", "NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA"]
        window_record = payload["windows"][0]
        assert window_record["disposition"] == "COMPLETED"
        assert window_record["stage_receipt_sha256s"] == [
            stage_receipt_sha256(item) for item in receipts
        ]
        assert window_record["health_receipt_sha256"] == health_receipt_sha256(
            result.health_receipt
        )
        assert window_record["option_receipt_sha256s"] == list(result.option_receipt_sha256s)

        closed = service.close_authority(
            lifecycle_id=result.lifecycle_id,
            observed_at=rig.arm.hard_flat_at,
        )
        assert rig.broker.is_flat()
        assert rig.broker.open_submissions == 1
        assert rig.broker.close_submissions == 1
        decisions = rig.ledger.decision_episode_rows()
        outcomes = rig.ledger.outcome_episode_rows()
        assert len(decisions) == 1
        assert len(outcomes) == 1
        assert outcomes[0]["lifecycle_outcome"] == "CLOSED"
        assert int(outcomes[0]["final_flat"]) == 1
        assert outcomes[0]["decision_episode_id"] == decisions[0]["episode_id"]
        assert rig.sidecar.is_terminal(result.lifecycle_id)

        terminal_after = service.terminal_receipt()
        payload_after = json.loads(terminal_after.to_json_bytes())
        assert payload_after["closes"][0]["lifecycle_id"] == result.lifecycle_id
        assert (
            payload_after["closes"][0]["terminal_flat_proof_sha256"]
            == closed.terminal_flat_proof_sha256
        )

        exposures = service.exposure_state(rig.session_id)
        assert len(exposures) == 1
        assert exposures[0].exposure_sha256 == result.exposure_sha256
        assert exposures[0].underlying_quantity_delta == Decimal(0)
        assert exposures[0].event_cash_delta == Decimal(0)
    finally:
        service.close()


def test_prerequisite_failure_stops_downstream(tmp_path: Path) -> None:
    rig = _rig(tmp_path, corrupt_evidence=True)
    service = _service(rig)
    try:
        with pytest.raises(ApplicationServiceStopped) as excinfo:
            service.run_window(request=_request(rig))

        stopped = excinfo.value
        assert stopped.stage_receipt.stage == STAGE_EVIDENCE_CAPTURE
        assert stopped.stage_receipt.status is StageStatus.FAILED
        assert stopped.stage_receipt.reason_code == REASON_CAPTURE_REJECTED
        assert stopped.health_receipt.circuit_state is CircuitState.NOMINAL
        chain = stopped.stage_chain
        assert len(chain) == len(STAGE_ORDER)
        verify_stage_receipt_chain(chain, arm_sha256=rig.arm.arm_sha256)
        assert all(item.status is StageStatus.SKIPPED for item in chain[1:])
        assert all(item.reason_code == REASON_UPSTREAM_STOPPED for item in chain[1:])

        assert rig.broker.open_submissions == 0
        assert rig.broker.close_submissions == 0
        assert rig.ledger.decision_episode_rows() == []
        assert rig.ledger.outcome_episode_rows() == []
        assert rig.ledger.v2_open_reservation_rows() == []
        assert [
            entry
            for entry in _sidecar_entries(rig)
            if entry["kind"] in {"ACTIVE", "TERMINAL", "EXPOSURE"}
        ] == []
        capture_dir = rig.authority.state_dir / "capture_artifacts" / "SCAN_1000_ET"
        assert not (capture_dir / "capture_identity.json").exists()
    finally:
        service.close()


def test_deadline_exhaustion_fails_closed_but_close_authority_retained(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    service = _service(rig)
    try:
        first = service.run_window(request=_request(rig))
        assert first.disposition is RunDisposition.COMPLETED
        lifecycle_id = first.lifecycle_id
        assert lifecycle_id is not None
        window = rig.arm.windows[0]

        with pytest.raises(ApplicationServiceStopped) as excinfo:
            service.run_window(request=_request(rig, observed_at=window.closes_at))
        stopped = excinfo.value
        assert stopped.stage_receipt.stage == STAGE_EVIDENCE_CAPTURE
        assert stopped.stage_receipt.status is StageStatus.SKIPPED
        assert stopped.stage_receipt.reason_code == REASON_DEADLINE_EXHAUSTED
        assert stopped.health_receipt.circuit_state is CircuitState.NOMINAL
        assert all(item.status is StageStatus.SKIPPED for item in stopped.stage_chain)
        verify_stage_receipt_chain(stopped.stage_chain, arm_sha256=rig.arm.arm_sha256)
        assert rig.broker.open_submissions == 1
        assert len(rig.ledger.decision_episode_rows()) == 1
        assert len([e for e in _sidecar_entries(rig) if e["kind"] == "ACTIVE"]) == 1

        with pytest.raises(ApplicationServiceStopped) as hard_flat_stop:
            service.run_window(request=_request(rig, observed_at=rig.arm.hard_flat_at))
        assert hard_flat_stop.value.stage_receipt.reason_code == REASON_DEADLINE_EXHAUSTED

        closed = service.close_authority(
            lifecycle_id=lifecycle_id,
            observed_at=rig.arm.hard_flat_at,
        )
        assert rig.broker.is_flat()
        assert rig.broker.close_submissions == 1
        outcomes = rig.ledger.outcome_episode_rows()
        assert len(outcomes) == 1
        assert int(outcomes[0]["final_flat"]) == 1
        assert rig.sidecar.is_terminal(lifecycle_id)
        assert closed.terminal_flat_proof_sha256

        replay = service.close_authority(
            lifecycle_id=lifecycle_id,
            observed_at=rig.arm.hard_flat_at,
        )
        assert replay.terminal_flat_proof_sha256 == closed.terminal_flat_proof_sha256
        assert len(rig.ledger.outcome_episode_rows()) == 1
    finally:
        service.close()


def test_budget_violation_stops_before_mutation(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    clock = _ScriptedClock(
        (
            STAGE_CLOCK,
            STAGE_CLOCK + timedelta(seconds=1),
            STAGE_CLOCK + timedelta(seconds=2),
            STAGE_CLOCK + timedelta(seconds=3),
            STAGE_CLOCK + timedelta(seconds=4),
            STAGE_CLOCK + timedelta(seconds=40),
        )
    )
    service = _service(rig, clock=clock)
    try:
        with pytest.raises(ApplicationServiceStopped) as excinfo:
            service.run_window(request=_request(rig))

        stopped = excinfo.value
        assert stopped.stage_receipt.stage == STAGE_DECISION
        assert stopped.stage_receipt.status is StageStatus.FAILED
        assert stopped.stage_receipt.reason_code == REASON_BUDGET_VIOLATION
        assert stopped.stage_receipt.budget_ms == rig.budgets.reasoner_ms
        assert stopped.health_receipt.budget_violations == (STAGE_DECISION,)
        assert stopped.health_receipt.circuit_state is CircuitState.NOMINAL
        assert stopped.health_receipt.stage_latencies[STAGE_DECISION] == 36000

        chain = stopped.stage_chain
        index = STAGE_ORDER.index(STAGE_DECISION)
        verify_stage_receipt_chain(chain, arm_sha256=rig.arm.arm_sha256)
        assert all(item.status is StageStatus.OK for item in chain[:index])
        assert chain[index] is stopped.stage_receipt
        assert all(item.status is StageStatus.SKIPPED for item in chain[index + 1 :])

        assert rig.broker.open_submissions == 0
        assert rig.ledger.decision_episode_rows() == []
        assert rig.ledger.outcome_episode_rows() == []
        assert [entry for entry in _sidecar_entries(rig) if entry["kind"] == "ACTIVE"] == []
    finally:
        service.close()


def test_duplicate_window_run_suppresses_without_duplicate_episodes(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    bundle = _bundle(rig, scenario="ACTIVE_UNCHANGED")
    service = _service(rig, option_events=(bundle,))
    try:
        first = service.run_window(request=_request(rig))
        assert first.disposition is RunDisposition.COMPLETED
        assert first.health_receipt.duplicate_suppressions == 0
        assert len(rig.ledger.decision_episode_rows()) == 1
        assert rig.broker.open_submissions == 1

        second = service.run_window(request=_request(rig))

        assert second.disposition is RunDisposition.DUPLICATE_SUPPRESSED
        assert second.stage_receipts == ()
        assert second.lifecycle_id is None
        assert second.exposure_sha256 is None
        assert second.health_receipt.duplicate_suppressions == 1
        assert second.health_receipt.circuit_state is CircuitState.NOMINAL
        assert service.duplicate_suppressions == 1
        assert len(rig.ledger.decision_episode_rows()) == 1
        assert rig.ledger.outcome_episode_rows() == []
        assert rig.broker.open_submissions == 1
        stored = service.option_journal.latest_receipt(rig.session_id, first.lifecycle_id)
        assert stored is not None
        assert stored.receipt_sha256 == first.option_receipt_sha256s[0]
        assert len(service.exposure_state(rig.session_id)) == 1
        assert [entry for entry in _exposure_entries(rig) if entry["kind"] == "EXPOSURE"] != []
        assert len([entry for entry in _exposure_entries(rig) if entry["kind"] == "EXPOSURE"]) == 1
    finally:
        service.close()


def test_assignment_exercise_expiry_recompute_exposure(tmp_path: Path) -> None:
    scenarios = (
        (
            "assignment",
            "ASSIGNMENT",
            Decimal(-100),
            Decimal(6200),
            Decimal(1),
            Decimal(0),
            OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED,
            "ASSIGNMENT_OR_EXERCISE_REQUIRES_MANUAL_RECONCILIATION",
            "ACT-ASSIGN-0001",
        ),
        (
            "exercise",
            "EXERCISE",
            Decimal(100),
            Decimal(-6100),
            Decimal(0),
            Decimal(-1),
            OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED,
            "ASSIGNMENT_OR_EXERCISE_REQUIRES_MANUAL_RECONCILIATION",
            "ACT-EXERCISE-0001",
        ),
        (
            "expiry",
            "EXPIRY",
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
            OptionReconciliationState.EXPIRY_FLAT_ATTESTED,
            "BOTH_LEGS_EXPIRY_AND_BOUND_POSITIONS_FLAT_ATTESTED",
            "ACT-EXPIRY-LONG",
        ),
    )
    for (
        name,
        scenario,
        underlying_delta,
        cash_delta,
        long_quantity,
        short_quantity,
        expected_state,
        expected_reason,
        activity_id,
    ) in scenarios:
        rig = _rig(tmp_path, name=name, session_id=f"ESSCHER-APPSVC-{name}")
        bundle = _bundle(rig, scenario=scenario)
        service = _service(rig, option_events=(bundle,))
        try:
            request = _request(rig)
            lifecycle_id: str
            if expected_state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED:
                with pytest.raises(ApplicationServiceStopped) as excinfo:
                    service.run_window(request=request)
                stopped = excinfo.value
                assert stopped.stage_receipt.stage == STAGE_RECONCILIATION
                assert stopped.stage_receipt.reason_code == REASON_RECONCILIATION_MANUAL_REQUIRED
                assert stopped.health_receipt.circuit_state is CircuitState.MANUAL_REQUIRED
                assert expected_reason in stopped.health_receipt.dependency_degradation
                bundles = rig.sidecar.active_bundles(rig.session_id)
                assert len(bundles) == 1
                lifecycle_id = bundles[0].lifecycle_id
            else:
                result = service.run_window(request=request)
                assert result.disposition is RunDisposition.COMPLETED
                assert result.lifecycle_id is not None
                lifecycle_id = result.lifecycle_id

            stored = service.option_journal.latest_receipt(rig.session_id, lifecycle_id)
            assert stored is not None
            assert stored.state is expected_state
            assert expected_reason in stored.reason_codes
            binding = service.option_journal.load_binding(rig.session_id, lifecycle_id)
            assert binding is not None
            assert (
                service.option_journal.activity_owner(
                    rig.arm.account_fingerprint_sha256,
                    activity_id,
                )
                == binding.binding_sha256
            )

            exposures = service.exposure_state(rig.session_id)
            assert len(exposures) == 1
            entry = exposures[0]
            assert entry.lifecycle_id == lifecycle_id
            assert entry.underlying_quantity_delta == underlying_delta
            assert entry.event_cash_delta == cash_delta
            assert entry.option_long_quantity == long_quantity
            assert entry.option_short_quantity == short_quantity
            open_total = sum(
                (Decimal(str(row["amount"])) for row in rig.ledger.v2_open_reservation_rows()),
                start=Decimal(0),
            )
            assert entry.open_reservation_total == open_total == Decimal(2)
            assert entry.option_receipt_sha256 == stored.receipt_sha256
            assert entry.exposure_sha256 == exposure_state_sha256(entry)
            replay_sha256 = entry.exposure_sha256

            if expected_state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED:
                with pytest.raises(ApplicationServiceStopped) as replay_stop:
                    service.run_window(request=request)
                assert (
                    replay_stop.value.stage_receipt.reason_code
                    == REASON_MANUAL_RECONCILIATION_STICKY
                )
            else:
                replay = service.run_window(request=request)
                assert replay.disposition is RunDisposition.DUPLICATE_SUPPRESSED

            replayed = service.exposure_state(rig.session_id)
            assert len(replayed) == 1
            assert replayed[0].exposure_sha256 == replay_sha256
            assert rig.broker.open_submissions == 1
            assert len(rig.ledger.decision_episode_rows()) == 1
            assert rig.ledger.outcome_episode_rows() == []
        finally:
            service.close()


def test_divergent_broker_truth_forces_sticky_manual(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    bundle = _bundle(rig, scenario="DIVERGENT")
    request = _request(rig)
    service = _service(rig, option_events=(bundle,))
    try:
        with pytest.raises(ApplicationServiceStopped) as excinfo:
            service.run_window(request=request)

        stopped = excinfo.value
        assert stopped.stage_receipt.stage == STAGE_RECONCILIATION
        assert stopped.stage_receipt.reason_code == REASON_RECONCILIATION_MANUAL_REQUIRED
        assert stopped.health_receipt.circuit_state is CircuitState.MANUAL_REQUIRED
        assert "UNATTRIBUTED_POSITION_CHANGE" in stopped.health_receipt.dependency_degradation
        bundles = rig.sidecar.active_bundles(rig.session_id)
        assert len(bundles) == 1
        stored = service.option_journal.latest_receipt(rig.session_id, bundles[0].lifecycle_id)
        assert stored is not None
        assert stored.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED
        assert rig.broker.open_submissions == 1
        assert rig.broker.close_submissions == 0
    finally:
        service.close()

    restarted = _service(rig, option_events=(bundle,))
    try:
        assert restarted.option_journal.account_requires_manual_reconciliation(
            session_id=rig.session_id,
            account_fingerprint_sha256=rig.arm.account_fingerprint_sha256,
        )
        with pytest.raises(ApplicationServiceStopped) as restart_stop:
            restarted.run_window(request=request)
        assert restart_stop.value.stage_receipt.reason_code == REASON_MANUAL_RECONCILIATION_STICKY
        assert restart_stop.value.stage_receipt.status is StageStatus.SKIPPED
        assert all(item.status is StageStatus.SKIPPED for item in restart_stop.value.stage_chain)
        assert restart_stop.value.health_receipt.circuit_state is CircuitState.MANUAL_REQUIRED
        assert rig.broker.open_submissions == 1
        assert rig.broker.close_submissions == 0
        assert len(rig.ledger.decision_episode_rows()) == 1
        assert restarted.duplicate_suppressions == 0
    finally:
        restarted.close()


def test_health_receipt_contents(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    bundle = _bundle(rig, scenario="ACTIVE_UNCHANGED")
    service = _service(rig, option_events=(bundle,))
    try:
        result = service.run_window(request=_request(rig))
        health = result.health_receipt

        assert health.circuit_state is CircuitState.NOMINAL
        assert health.claims == ("SYNTHETIC_FAKE", "NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA")
        assert health.arm_sha256 == rig.arm.arm_sha256
        assert health.budget_sha256 == stage_budgets_sha256(rig.budgets)
        assert health.duplicate_suppressions == 0
        assert health.budget_violations == ()
        assert health.dependency_degradation == ()
        assert set(health.stage_latencies) == set(STAGE_ORDER)
        for receipt in result.stage_receipts:
            assert health.stage_latencies[receipt.stage] <= receipt.budget_ms

        staleness = _staleness_by_source(health)
        assert staleness["FEED_CAPTURE"].age_seconds == 1790
        assert staleness["FEED_CAPTURE"].max_age_seconds == 30
        assert staleness["FEED_CAPTURE"].stale is True
        assert staleness["OPTION_OBSERVATION"].age_seconds == 1134
        assert staleness["OPTION_OBSERVATION"].max_age_seconds == 30
        assert staleness["OPTION_OBSERVATION"].stale is True
        assert staleness["BROKER_TRUTH"].age_seconds == 0
        assert staleness["BROKER_TRUTH"].stale is False

        assert health.reconciliation_lag_ms is not None
        assert health.reconciliation_lag_ms >= 0
        assert health.reconciliation_lag_ms == 606000

        payload = json.loads(health_receipt_bytes(health))
        assert payload["schema"] == "esscher.operational_health_receipt"
        assert payload["schema_version"] == 1
        assert payload["circuit_state"] == "NOMINAL"
        assert payload["run_id"] == f"RUN-OPP-{FIXTURE_EVENT_ID}-SCAN_1000_ET"
        assert payload["claims"] == [
            "SYNTHETIC_FAKE",
            "NOT_ALPHA_EVIDENCE",
            "NOT_HISTORICAL_DATA",
        ]
    finally:
        service.close()


def test_no_network_in_new_modules() -> None:
    modules = (stage_budgets, health_receipts, autonomous_application_service)
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


def test_full_accepted_run_with_socket_surface_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    def deny_getaddrinfo(*args: object, **kwargs: object) -> None:
        attempts.append("getaddrinfo")
        raise AssertionError("application service attempted name resolution")

    def deny_create_connection(*args: object, **kwargs: object) -> None:
        attempts.append("create_connection")
        raise AssertionError("application service attempted a network connection")

    monkeypatch.setattr(socket, "getaddrinfo", deny_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", deny_create_connection)
    rig = _rig(tmp_path)
    bundle = _bundle(rig, scenario="ACTIVE_UNCHANGED")
    service = _service(rig, option_events=(bundle,))
    try:
        result = service.run_window(request=_request(rig))
        assert result.disposition is RunDisposition.COMPLETED
        service.close_authority(
            lifecycle_id=result.lifecycle_id,
            observed_at=rig.arm.hard_flat_at,
        )
        assert rig.broker.is_flat()
    finally:
        service.close()
    assert attempts == []
