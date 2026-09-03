"""Issue #67 P0 evidence tests: prospective ledger, panel reports, comparison.

Every fixture is synthetic or fake-only, deterministic, and offline.  The real
frozen untouched universe (23 events / 7 sectors) is consumed read-only from
``data/qfast-panel``; all decisions, paths, releases, and service receipts are
synthetic rehearsal artifacts labelled NOT_ALPHA_EVIDENCE.
"""

from __future__ import annotations

import ast
import hashlib
import json
import socket
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest

import ringdown_market.alpha.fullstack_shadow_comparison as comparison_module
import ringdown_market.alpha.prospective_ledger as ledger_module
import ringdown_market.alpha.qfast_panel_reports as reports_module
from ringdown_market.alpha.direction_receipts import (
    DirectionReceipt,
    ProducerKind,
    direction_receipt_bytes,
)
from ringdown_market.alpha.fullstack_shadow_comparison import (
    ComparisonFindingCode,
    ServiceEventReceipts,
    compare_fullstack,
    service_event_receipts_from_window_run,
)
from ringdown_market.alpha.models import Direction
from ringdown_market.alpha.prospective_ledger import (
    LEDGER_GENESIS_SHA256,
    FrozenEventRegistration,
    ProspectiveLedger,
    ProspectiveLedgerReason,
    ProspectiveLedgerRejected,
    verify_ledger_bytes,
)
from ringdown_market.alpha.qfast import CANDIDATE_METHOD
from ringdown_market.alpha.qfast_panel_reports import (
    NOT_AVAILABLE,
    PANEL_CONTRACT_MULTIPLIER,
    PANEL_EVIDENCE_REPORT_SCHEMA,
    PANEL_FEE_PER_TRADE_USD,
    PANEL_SLIPPAGE_BPS,
    FakeExecutionLink,
    PanelPromotionRecommendation,
    PanelReportReason,
    PanelReportStatus,
    build_panel_manifest,
    resolve_panel_manifest,
    run_qfast_panel,
    run_source_health_gate,
)
from ringdown_market.contracts.execution_policy import RESEARCH_DECISION_PROTOCOL_SHA256
from ringdown_market.contracts.latency_profile import (
    latency_profile_content_sha256,
    packaged_latency_profile_bytes,
)
from ringdown_market.contracts.strategy_release import (
    EXPECTED_LANE_BINDINGS,
    StrategyRelease,
    parse_strategy_release,
    strategy_release_bytes,
)
from ringdown_market.panel.manifest import DATA_CLASS_REAL, PANEL_MANIFEST_SCHEMA
from ringdown_market.runtime.autonomous_application_service import (
    STAGE_ORDER,
    RunDisposition,
    ServiceTerminalReceipt,
    StageReceipt,
    StageStatus,
    WindowRunResult,
    WindowTerminalRecord,
    service_terminal_receipt_sha256,
    stage_receipt_sha256,
)
from ringdown_market.runtime.health_receipts import (
    build_operational_health_receipt,
    health_receipt_sha256,
)
from ringdown_market.strategy.policy import strategy_policy_sha256

ROOT = Path(__file__).parents[1]
PANEL_DATA = ROOT / "data" / "qfast-panel"
UNIVERSE = PANEL_DATA / "universe"
EVENTS_DIR = UNIVERSE / "events"
WINDOWS_DIR = PANEL_DATA / "market-windows"
FREEZE_DOCUMENT = PANEL_DATA / "universe-freeze-v1.json"

POLICY_SHA = strategy_policy_sha256()
SNAPSHOT_PROTOCOL_SHA = hashlib.sha256(b"qfast-panel-evidence-snapshot-protocol").hexdigest()
CANDIDATE_ID = "QFAST_PANEL_SYNTHETIC_CANDIDATE_V1"
FREEZE_RECORDED_AT = datetime(2026, 8, 30, 15, 30, tzinfo=UTC)
ALIGNED_RATE = 0.004
UNSTABLE_RATE = 0.05
STAGE_CLOCK = datetime(2026, 9, 11, 13, 36, tzinfo=UTC)
ARM_SHA = "e" * 64
BUDGET_SHA = "f" * 64
OUTCOME_WINDOW = timedelta(hours=2, minutes=30)
ABSTAINED_EVENT_COUNT = 3
ADMITTED_EVENT_COUNT = 20
FORBIDDEN_MODULES = (
    "aiohttp",
    "alpaca",
    "ftplib",
    "http",
    "httpx",
    "mcp",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "webbrowser",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class _Universe:
    event_ids: tuple[str, ...]
    sectors: dict[str, str]
    cutoffs: dict[str, datetime]
    latest_evidence: dict[str, datetime]
    issuers: dict[str, str]
    source_manifests: dict[str, bytes]
    windows: tuple[bytes, ...]
    event_list_bytes: bytes
    panel_rule_bytes: bytes
    universe_rule_bytes: bytes
    panel_manifest_bytes: bytes


@lru_cache(maxsize=1)
def _universe() -> _Universe:
    event_list_bytes = (UNIVERSE / "event-list-v1.json").read_bytes()
    panel_rule_bytes = (PANEL_DATA / "selection-rule-v1.json").read_bytes()
    universe_rule_bytes = (UNIVERSE / "selection-rule-v1.json").read_bytes()
    event_list = json.loads(event_list_bytes.decode("utf-8"))
    event_ids = tuple(str(event["event_id"]) for event in event_list["events"])
    sectors = {str(event["event_id"]): str(event["sector"]) for event in event_list["events"]}
    source_manifests = {
        event_id: (EVENTS_DIR / f"{event_id}.json").read_bytes() for event_id in event_ids
    }
    cutoffs: dict[str, datetime] = {}
    latest: dict[str, datetime] = {}
    issuers: dict[str, str] = {}
    for event_id, raw in source_manifests.items():
        record = json.loads(raw.decode("utf-8"))
        cutoffs[event_id] = datetime.fromisoformat(
            str(record["decision_cutoff"]).replace("Z", "+00:00")
        )
        latest[event_id] = datetime.fromisoformat(
            str(record["latest_evidence_at"]).replace("Z", "+00:00")
        )
        issuers[event_id] = str(record["issuer"])
    windows = tuple((WINDOWS_DIR / f"{event_id}.json").read_bytes() for event_id in event_ids)
    panel_manifest_bytes = build_panel_manifest(
        event_list_bytes=event_list_bytes,
        selection_rule_bytes=panel_rule_bytes,
        strategy_policy_sha256=POLICY_SHA,
        snapshot_protocol_sha256=SNAPSHOT_PROTOCOL_SHA,
        decision_protocol_sha256=RESEARCH_DECISION_PROTOCOL_SHA256,
    )
    return _Universe(
        event_ids=event_ids,
        sectors=sectors,
        cutoffs=cutoffs,
        latest_evidence=latest,
        issuers=issuers,
        source_manifests=source_manifests,
        windows=windows,
        event_list_bytes=event_list_bytes,
        panel_rule_bytes=panel_rule_bytes,
        universe_rule_bytes=universe_rule_bytes,
        panel_manifest_bytes=panel_manifest_bytes,
    )


def _direction_map(*, flip: bool = False) -> dict[str, Direction]:
    directions: dict[str, Direction] = {}
    for index, event_id in enumerate(_universe().event_ids):
        if index % 8 == 2:
            direction = Direction.UNCERTAIN
        elif index % 2 == 0:
            direction = Direction.UP
        else:
            direction = Direction.DOWN
        if flip and direction is not Direction.UNCERTAIN:
            direction = Direction.DOWN if direction is Direction.UP else Direction.UP
        directions[event_id] = direction
    return directions


def _scores(index: int, direction: Direction) -> tuple[float, float]:
    nominal = 1.0 if direction is Direction.UNCERTAIN else float(direction.multiplier)
    price_only = 0.5 * (nominal if index % 2 == 0 else -nominal)
    numeric = 0.4 * (nominal if index % 3 == 0 else -nominal)
    return price_only, numeric


def _receipt(
    event_id: str,
    direction: Direction,
    *,
    price_only: float,
    numeric: float,
    producer: ProducerKind,
) -> DirectionReceipt:
    universe = _universe()
    cutoff = universe.cutoffs[event_id]
    if producer is ProducerKind.SYNTHETIC:
        hashes = {"route_sha256": None, "prompt_sha256": None, "model_config_sha256": None}
        classification = ("NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA")
    else:
        hashes = {
            "route_sha256": _sha(f"route-{event_id}".encode()),
            "prompt_sha256": _sha(f"prompt-{event_id}".encode()),
            "model_config_sha256": _sha(f"model-{event_id}".encode()),
        }
        classification = ("CANDIDATE_EVIDENCE_FIXTURE", "SYNTHETIC_FAKE")
    return DirectionReceipt(
        event_id=event_id,
        candidate_id=CANDIDATE_ID,
        direction=direction,
        reason_codes=(),
        decision_cutoff_at=cutoff,
        latest_evidence_at=universe.latest_evidence[event_id],
        feature_snapshot_at=cutoff,
        market_beta=0.1,
        sector_beta=0.2,
        price_only_score=price_only,
        fundamental_score=0.1,
        numeric_score=numeric,
        producer_kind=producer,
        classification=classification,
        produced_at=cutoff + timedelta(minutes=1),
        decision_artifact_sha256=_sha(f"synthetic-decision-{event_id}".encode()),
        limitations=("NOT_HISTORICAL_DATA",),
        **hashes,
    )


def _receipt_bytes_and_bundle(
    *,
    flip: bool = False,
    rate: float = ALIGNED_RATE,
    producer: ProducerKind = ProducerKind.SYNTHETIC,
) -> tuple[list[bytes], bytes]:
    universe = _universe()
    aligned = _direction_map()
    signaled = _direction_map(flip=flip)
    receipts: list[bytes] = []
    bundle_events: list[dict[str, object]] = []
    for index, event_id in enumerate(universe.event_ids):
        direction = signaled[event_id]
        price_only, numeric = _scores(index, aligned[event_id])
        receipts.append(
            direction_receipt_bytes(
                _receipt(
                    event_id,
                    direction,
                    price_only=price_only,
                    numeric=numeric,
                    producer=producer,
                )
            )
        )
        cutoff = universe.cutoffs[event_id]
        nominal = float(aligned[event_id].multiplier) or 1.0
        drift_rate = rate * nominal
        points = []
        for step in range(31):
            stamp = cutoff + timedelta(minutes=5 * step)
            drift = 1.0 + drift_rate * (step / 12.0)
            points.append(
                {
                    "at": _iso(stamp),
                    "stock": (100.0 + index) * drift,
                    "market": 100.0,
                    "sector": 100.0,
                }
            )
        bundle_events.append(
            {
                "decision": {
                    "event_id": event_id,
                    "issuer": universe.issuers[event_id],
                    "decision_cutoff": _iso(cutoff),
                    "latest_evidence_at": _iso(universe.latest_evidence[event_id]),
                    "feature_snapshot_at": _iso(cutoff),
                    "opening_return": 0.0,
                    "market_opening_return": 0.0,
                    "sector_opening_return": 0.0,
                    "market_beta": 0.1,
                    "sector_beta": 0.2,
                    "price_only_score": price_only,
                    "fundamental_score": 0.1,
                    "numeric_score": numeric,
                    "candidate_signal": direction.value,
                },
                "path": points,
            }
        )
    bundle = {
        "schema": "ringdown.qfast_panel_bundle",
        "schema_version": 1,
        "fixture_class": "SYNTHETIC_CONTRACT_FIXTURE",
        "limitations": ["NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA", "NO_BROKER_EXECUTION"],
        "panel_manifest_sha256": _sha(universe.panel_manifest_bytes),
        "events": bundle_events,
    }
    return receipts, (json.dumps(bundle, indent=2) + "\n").encode("utf-8")


def _panel_kwargs(
    *,
    flip: bool = False,
    rate: float = ALIGNED_RATE,
    producer: ProducerKind = ProducerKind.SYNTHETIC,
) -> dict[str, object]:
    universe = _universe()
    receipts, bundle = _receipt_bytes_and_bundle(flip=flip, rate=rate, producer=producer)
    return {
        "manifest_bytes": universe.panel_manifest_bytes,
        "selection_rule_bytes": universe.panel_rule_bytes,
        "bundle_bytes": bundle,
        "receipt_bytes": receipts,
        "latency_profile_bytes": packaged_latency_profile_bytes(),
        "expected_policy_sha256": POLICY_SHA,
        "event_list_bytes": universe.event_list_bytes,
        "source_manifest_bytes_by_event": universe.source_manifests,
        "universe_selection_rule_bytes": universe.universe_rule_bytes,
        "window_bytes": universe.windows,
    }


def _run_panel(
    *,
    flip: bool = False,
    rate: float = ALIGNED_RATE,
    producer: ProducerKind = ProducerKind.SYNTHETIC,
    **overrides: object,
):
    kwargs = _panel_kwargs(flip=flip, rate=rate, producer=producer)
    kwargs.update(overrides)
    return run_qfast_panel(**kwargs)  # type: ignore[arg-type]


def _host_measured_profile_bytes() -> bytes:
    payload = json.loads(packaged_latency_profile_bytes().decode("utf-8"))
    payload["kind"] = "HOST_MEASURED"
    payload["observed_samples"] = 24
    payload["content_sha256"] = latency_profile_content_sha256(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _release_bytes() -> bytes:
    release = StrategyRelease(
        release_id="ESSCHER-QFAST-PANEL-EVIDENCE-TEST",
        release_version=1,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        mode="PAPER",
        code_revision="a" * 40,
        build_artifact_sha256="b" * 64,
        evidence_report_sha256="c" * 64,
        security_report_sha256="d" * 64,
        evidence_qualified=True,
        security_passed=True,
        autonomy_policy_id="AUTONOMY-POLICY-TEST-V1",
        strategy_policy_id="STRATEGY-POLICY-TEST-V1",
        reasoner_route_id="REASONER-ROUTE-TEST-V1",
        reasoner_model="test-model",
        reasoner_schema_id="test_schema_v1",
        latency_profile_id="LATENCY-PROFILE-TEST-V1",
        source_matrix_id="SOURCE-MATRIX-TEST-V1",
        risk_policy_id="RISK-POLICY-TEST-V1",
        lifecycle_policy_id="LIFECYCLE-POLICY-TEST-V1",
        lane_bindings=EXPECTED_LANE_BINDINGS,
    )
    return strategy_release_bytes(release)


def _registrations() -> tuple[FrozenEventRegistration, ...]:
    universe = _universe()
    return tuple(
        FrozenEventRegistration(
            event_id=event_id,
            sector=universe.sectors[event_id],
            decision_cutoff=universe.cutoffs[event_id],
            outcome_window_close=universe.cutoffs[event_id] + OUTCOME_WINDOW,
            source_manifest_sha256=_sha(universe.source_manifests[event_id]),
            strategy_identity_sha256=POLICY_SHA,
        )
        for event_id in universe.event_ids
    )


def _frozen_ledger(tmp_path: Path) -> ProspectiveLedger:
    ledger = ProspectiveLedger(tmp_path / "prospective" / "ledger.jsonl")
    ledger.create_freeze_entry(events=_registrations(), recorded_at=FREEZE_RECORDED_AT)
    return ledger


def _signal_all(ledger: ProspectiveLedger) -> None:
    universe = _universe()
    directions = _direction_map()
    for event_id in universe.event_ids:
        ledger.append_signal(
            event_id=event_id,
            direction=directions[event_id],
            decision_sha256=_sha(f"synthetic-decision-{event_id}".encode()),
            receipt_sha256=_sha(f"synthetic-receipt-{event_id}".encode()),
            observed_at=universe.cutoffs[event_id] + timedelta(minutes=5),
        )


def _stage_chain(event_id: str) -> tuple[StageReceipt, ...]:
    receipts: list[StageReceipt] = []
    prior = ARM_SHA
    for index, stage in enumerate(STAGE_ORDER):
        receipt = StageReceipt(
            stage=stage,
            prior_stage_sha256=prior,
            input_sha256=_sha(f"input-{event_id}-{index}".encode()),
            output_sha256=_sha(f"output-{event_id}-{index}".encode()),
            started_at=STAGE_CLOCK + timedelta(seconds=2 * index),
            finished_at=STAGE_CLOCK + timedelta(seconds=2 * index + 1),
            budget_ms=30_000,
            status=StageStatus.OK,
            reason_code=None,
        )
        receipts.append(receipt)
        prior = stage_receipt_sha256(receipt)
    return tuple(receipts)


def _service_receipts(
    report,
    *,
    skip: tuple[str, ...] = (),
    flip: tuple[str, ...] = (),
    truncate: tuple[str, ...] = (),
    extra_event: str | None = None,
) -> dict[str, ServiceEventReceipts]:
    receipts: dict[str, ServiceEventReceipts] = {}
    event_ids = [*sorted(report.rows), *([extra_event] if extra_event is not None else [])]
    for event_id in event_ids:
        if event_id in skip:
            continue
        row = report.rows.get(event_id)
        direction = Direction(row.direction) if row is not None else Direction.UP
        admitted = row.admitted if row is not None else True
        if event_id in flip:
            direction = Direction.DOWN if direction is Direction.UP else Direction.UP
        chain = _stage_chain(event_id)
        if event_id in truncate:
            chain = chain[:4]
        health = build_operational_health_receipt(
            run_id=f"RUN-{event_id}",
            arm_sha256=ARM_SHA,
            observed_at=STAGE_CLOCK,
            budget_sha256=BUDGET_SHA,
            stage_latencies={"DECISION": 10},
        )
        disposition = (
            RunDisposition.COMPLETED
            if admitted and event_id not in flip
            else RunDisposition.STOPPED
        )
        window_record = WindowTerminalRecord(
            window_id=f"WINDOW-{event_id}",
            opportunity_id=f"OPPORTUNITY-{event_id}",
            disposition=disposition,
            stage_receipt_sha256s=tuple(stage_receipt_sha256(item) for item in chain),
            health_receipt_sha256=health_receipt_sha256(health),
            option_receipt_sha256s=(),
        )
        provisional = ServiceTerminalReceipt(
            session_id="ESSCHER-PANEL-EVIDENCE-TEST",
            arm_sha256=ARM_SHA,
            windows=(window_record,),
            closes=(),
            terminal_receipt_sha256="0" * 64,
        )
        terminal = replace(
            provisional,
            terminal_receipt_sha256=service_terminal_receipt_sha256(provisional),
        )
        window_result = WindowRunResult(
            disposition=disposition,
            window_id=f"WINDOW-{event_id}",
            opportunity_id=f"OPPORTUNITY-{event_id}",
            lifecycle_id=None,
            stage_receipts=chain,
            health_receipt=health,
            option_receipt_sha256s=(),
            exposure_sha256=None,
        )
        receipts[event_id] = service_event_receipts_from_window_run(
            event_id=event_id,
            window_result=window_result,
            terminal_receipt=terminal,
            direction=direction,
        )
    return receipts


@pytest.fixture(scope="module")
def universe() -> _Universe:
    return _universe()


@pytest.fixture(scope="module")
def accepted_report():
    return _run_panel()


def test_prospective_freeze_registers_all_23_frozen_events(tmp_path: Path) -> None:
    ledger = _frozen_ledger(tmp_path)
    _signal_all(ledger)

    assert ledger.frozen_event_ids == _universe().event_ids
    assert len(ledger.frozen_event_ids) == 23
    assert len(ledger.signals()) == 23
    verification = ledger.verify_chain()
    assert verification.valid is True
    assert verification.entry_count == 24
    assert verification.head_sha256 == ledger.head_sha256
    freeze_entry = ledger.entries()[0]
    assert freeze_entry["prior_entry_sha256"] == LEDGER_GENESIS_SHA256
    events = freeze_entry["events"]
    assert len(events) == 23
    assert len({event["sector"] for event in events}) == 7
    for event in events:
        assert event["source_manifest_sha256"] == _sha(
            _universe().source_manifests[event["event_id"]]
        )
        assert event["strategy_identity_sha256"] == POLICY_SHA


def test_no_event_added_removed_or_relabeled_after_outcome_inspection(tmp_path: Path) -> None:
    ledger = _frozen_ledger(tmp_path)
    event_ids = _universe().event_ids
    ledger.inspect_outcome(
        event_id=event_ids[0],
        outcome_sha256=_sha(b"synthetic-outcome"),
        inspected_at=_universe().cutoffs[event_ids[0]] + OUTCOME_WINDOW + timedelta(minutes=1),
    )
    registrations = _registrations()

    with pytest.raises(ProspectiveLedgerRejected) as added:
        ledger.create_freeze_entry(
            events=[
                *registrations,
                FrozenEventRegistration(
                    event_id="EXTRA-20260901-EARNINGS",
                    sector="TECHNOLOGY",
                    decision_cutoff=datetime(2026, 9, 1, 12, 30, tzinfo=UTC),
                    outcome_window_close=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
                    source_manifest_sha256=_sha(b"extra"),
                    strategy_identity_sha256=POLICY_SHA,
                ),
            ],
            recorded_at=FREEZE_RECORDED_AT,
        )
    assert added.value.reason is ProspectiveLedgerReason.EVENT_ADDED_AFTER_FREEZE

    with pytest.raises(ProspectiveLedgerRejected) as removed:
        ledger.create_freeze_entry(events=registrations[1:], recorded_at=FREEZE_RECORDED_AT)
    assert removed.value.reason is ProspectiveLedgerReason.EVENT_REMOVED_AFTER_FREEZE

    relabeled = replace(registrations[0], sector="ENERGY")
    with pytest.raises(ProspectiveLedgerRejected) as moved:
        ledger.create_freeze_entry(
            events=(relabeled, *registrations[1:]), recorded_at=FREEZE_RECORDED_AT
        )
    assert moved.value.reason is ProspectiveLedgerReason.EVENT_RELABELED_AFTER_FREEZE

    with pytest.raises(ProspectiveLedgerRejected) as duplicate:
        ledger.create_freeze_entry(
            events=registrations, recorded_at=FREEZE_RECORDED_AT + timedelta(seconds=1)
        )
    assert duplicate.value.reason is ProspectiveLedgerReason.DUPLICATE_FREEZE

    replay = ledger.create_freeze_entry(events=registrations, recorded_at=FREEZE_RECORDED_AT)
    assert replay == ledger.entries()[0]["entry_sha256"]
    assert ledger.verify_chain().entry_count == 2


def test_prospective_ledger_rejects_late_and_inspected_signals(tmp_path: Path) -> None:
    ledger = _frozen_ledger(tmp_path)
    event_id = _universe().event_ids[0]
    cutoff = _universe().cutoffs[event_id]

    with pytest.raises(ProspectiveLedgerRejected) as late:
        ledger.append_signal(
            event_id=event_id,
            direction=Direction.UP,
            decision_sha256=_sha(b"decision"),
            receipt_sha256=_sha(b"receipt"),
            observed_at=cutoff + OUTCOME_WINDOW + timedelta(seconds=1),
        )
    assert late.value.reason is ProspectiveLedgerReason.LATE_SIGNAL_AFTER_OUTCOME_WINDOW

    ledger.inspect_outcome(
        event_id=event_id,
        outcome_sha256=_sha(b"synthetic-outcome"),
        inspected_at=cutoff + timedelta(hours=3),
    )
    with pytest.raises(ProspectiveLedgerRejected) as inspected:
        ledger.append_signal(
            event_id=event_id,
            direction=Direction.UP,
            decision_sha256=_sha(b"decision"),
            receipt_sha256=_sha(b"receipt"),
            observed_at=cutoff + timedelta(minutes=5),
        )
    assert inspected.value.reason is ProspectiveLedgerReason.SIGNAL_AFTER_OUTCOME_INSPECTION
    assert len(ledger.inspections(event_id)) == 1

    other = _universe().event_ids[1]
    ledger.append_signal(
        event_id=other,
        direction=Direction.DOWN,
        decision_sha256=_sha(b"decision"),
        receipt_sha256=_sha(b"receipt"),
        observed_at=_universe().cutoffs[other] + timedelta(minutes=5),
    )
    with pytest.raises(ProspectiveLedgerRejected) as duplicate:
        ledger.append_signal(
            event_id=other,
            direction=Direction.DOWN,
            decision_sha256=_sha(b"decision"),
            receipt_sha256=_sha(b"receipt"),
            observed_at=_universe().cutoffs[other] + timedelta(minutes=6),
        )
    assert duplicate.value.reason is ProspectiveLedgerReason.DUPLICATE_SIGNAL

    with pytest.raises(ProspectiveLedgerRejected) as unknown:
        ledger.append_signal(
            event_id="OUTSIDE-20260901-EARNINGS",
            direction=Direction.UP,
            decision_sha256=_sha(b"decision"),
            receipt_sha256=_sha(b"receipt"),
            observed_at=cutoff,
        )
    assert unknown.value.reason is ProspectiveLedgerReason.UNKNOWN_EVENT

    empty = ProspectiveLedger(tmp_path / "empty" / "ledger.jsonl")
    with pytest.raises(ProspectiveLedgerRejected) as unfrozen:
        empty.append_signal(
            event_id=event_id,
            direction=Direction.UP,
            decision_sha256=_sha(b"decision"),
            receipt_sha256=_sha(b"receipt"),
            observed_at=cutoff,
        )
    assert unfrozen.value.reason is ProspectiveLedgerReason.FREEZE_REQUIRED


def test_prospective_ledger_detects_tampering_and_replays(tmp_path: Path) -> None:
    path = tmp_path / "prospective" / "ledger.jsonl"
    ledger = _frozen_ledger(tmp_path)
    _signal_all(ledger)
    head = ledger.head_sha256

    reopened = ProspectiveLedger(path)
    assert reopened.head_sha256 == head
    assert reopened.frozen_event_ids == _universe().event_ids
    assert len(reopened.signals()) == 23
    assert reopened.verify_chain().valid is True

    lines = path.read_bytes().split(b"\n")
    tampered = bytearray(lines[5])
    position = tampered.find(b'"direction":')
    assert position != -1
    tampered[position + 13] = ord("X")
    lines[5] = bytes(tampered)
    path.write_bytes(b"\n".join(lines))

    result = verify_ledger_bytes(path.read_bytes())
    assert result.valid is False
    assert result.reason is ProspectiveLedgerReason.ENTRY_TAMPERED
    with pytest.raises(ProspectiveLedgerRejected):
        ProspectiveLedger(path)


def test_deterministic_ledger_bytes(tmp_path: Path) -> None:
    first = _frozen_ledger(tmp_path / "a")
    _signal_all(first)
    second = ProspectiveLedger(tmp_path / "b" / "prospective" / "ledger.jsonl")
    second.create_freeze_entry(events=_registrations(), recorded_at=FREEZE_RECORDED_AT)
    _signal_all(second)

    assert first.path.read_bytes() == second.path.read_bytes()
    assert first.head_sha256 == second.head_sha256


def test_universe_freeze_document_is_not_the_panel_manifest(universe: _Universe) -> None:
    freeze_bytes = FREEZE_DOCUMENT.read_bytes()
    declared = json.loads(freeze_bytes.decode("utf-8"))
    assert declared["schema"] != PANEL_MANIFEST_SCHEMA

    resolved = resolve_panel_manifest(
        universe_document_bytes=freeze_bytes,
        selection_rule_bytes=universe.panel_rule_bytes,
        event_list_bytes=universe.event_list_bytes,
        strategy_policy_sha256=POLICY_SHA,
        snapshot_protocol_sha256=SNAPSHOT_PROTOCOL_SHA,
        decision_protocol_sha256=RESEARCH_DECISION_PROTOCOL_SHA256,
    )
    assert resolved == universe.panel_manifest_bytes
    assert resolved != freeze_bytes

    rebuilt = build_panel_manifest(
        event_list_bytes=universe.event_list_bytes,
        selection_rule_bytes=universe.panel_rule_bytes,
        strategy_policy_sha256=POLICY_SHA,
        snapshot_protocol_sha256=SNAPSHOT_PROTOCOL_SHA,
        decision_protocol_sha256=RESEARCH_DECISION_PROTOCOL_SHA256,
    )
    assert rebuilt == universe.panel_manifest_bytes
    payload = json.loads(rebuilt.decode("utf-8"))
    assert payload["schema"] == PANEL_MANIFEST_SCHEMA
    assert [event["event_id"] for event in payload["eligible_events"]] == list(universe.event_ids)
    assert len(payload["eligible_events"]) == 23
    assert {event["event_id"] for event in payload["excluded_events"]} == {
        "KR-2026Q2-EARNINGS",
        "GIS-2027Q1-EARNINGS",
        "MU-2026Q4-EARNINGS",
        "NKE-2027Q1-EARNINGS",
    }


def test_source_health_gate_passes_over_the_frozen_23_manifests(universe: _Universe) -> None:
    gate = run_source_health_gate(
        universe.source_manifests,
        event_list_bytes=universe.event_list_bytes,
        selection_rule_bytes=universe.universe_rule_bytes,
    )

    assert gate.healthy is True
    assert len(gate.statuses) == 23
    assert all(status == "HEALTHY" for status in gate.statuses.values())
    assert all(codes == () for codes in gate.codes.values())
    for event_id, raw in universe.source_manifests.items():
        assert gate.manifest_sha256s[event_id] == _sha(raw)
    assert gate.sha256 == _sha(gate.bytes)
    assert gate.payload["claim"] == "NOT_ALPHA_EVIDENCE"


def test_source_health_gate_rejects_unhealthy_and_context_free_manifests(
    universe: _Universe,
) -> None:
    tampered = dict(universe.source_manifests)
    target = universe.event_ids[3]
    corrupt = bytearray(tampered[target])
    corrupt[12] = ord("X")
    tampered[target] = bytes(corrupt)
    gate = run_source_health_gate(
        tampered,
        event_list_bytes=universe.event_list_bytes,
        selection_rule_bytes=universe.universe_rule_bytes,
    )
    assert gate.healthy is False
    assert gate.statuses[target] == "FAILED_CLOSED"
    assert gate.codes[target] == ("PARSE_FAILED",)

    context_free = run_source_health_gate(universe.source_manifests)
    assert context_free.healthy is False
    assert all(
        PanelReportReason.SOURCE_HEALTH_CONTEXT_MISSING.value in context_free.codes[event_id]
        for event_id in universe.event_ids
    )

    foreign = run_source_health_gate(
        {"SYN-1": json.dumps({"schema": "esscher.other", "schema_version": 1}).encode()}
    )
    assert foreign.healthy is False
    assert foreign.codes["SYN-1"] == ("UNSUPPORTED_SCHEMA",)


def test_unhealthy_source_manifest_rejects_the_panel_report(universe: _Universe) -> None:
    tampered = dict(universe.source_manifests)
    target = universe.event_ids[0]
    corrupt = bytearray(tampered[target])
    corrupt[12] = ord("X")
    tampered[target] = bytes(corrupt)

    report = _run_panel(source_manifest_bytes_by_event=tampered)

    assert report.status is PanelReportStatus.REJECTED
    assert report.claim == "NOT_ALPHA_EVIDENCE"
    assert report.rows == {}
    assert PanelReportReason.SOURCE_HEALTH_GATE_REJECTED.value in report.rejection_reasons
    assert f"{target}:PARSE_FAILED" in report.rejection_reasons
    assert report.promotion.recommendation is PanelPromotionRecommendation.REJECTED
    assert report.promotion.release_sha256 is None


def test_lineage_gaps_reject_the_panel_report(universe: _Universe) -> None:
    missing = dict(universe.source_manifests)
    del missing[universe.event_ids[5]]
    report = _run_panel(source_manifest_bytes_by_event=missing)
    assert report.status is PanelReportStatus.REJECTED
    assert PanelReportReason.SOURCE_LINEAGE_GAP.value in report.rejection_reasons
    assert f"uncovered:{universe.event_ids[5]}" in report.rejection_reasons

    absent = _run_panel(source_manifest_bytes_by_event=None)
    assert absent.status is PanelReportStatus.REJECTED
    assert PanelReportReason.SOURCE_LINEAGE_GAP.value in absent.rejection_reasons
    assert absent.claim == "NOT_ALPHA_EVIDENCE"


def test_insufficient_sample_panel_is_rejected_without_profit_claim(
    universe: _Universe,
) -> None:
    payload = json.loads(universe.panel_manifest_bytes.decode("utf-8"))
    payload["data_class"] = DATA_CLASS_REAL
    payload["data_qualifiers"] = [
        "INDICATIVE_DATA",
        "NOT_ALPHA_EVIDENCE",
        "NO_OUTCOME_VALUES",
        "NO_BROKER_EXECUTION",
    ]
    payload["limitations"] = [
        "INDICATIVE_DATA",
        "NOT_ALPHA_EVIDENCE",
        "NO_OUTCOME_VALUES",
        "NO_BROKER_EXECUTION",
    ]
    payload["eligible_events"] = [
        {
            "event_id": event_id,
            "evidence_manifest_sha256": _sha(universe.source_manifests[event_id]),
        }
        for event_id in universe.event_ids[:3]
    ]
    payload["latency_profiles"]["p95"]["measurement"]["kind"] = "PREREGISTERED"
    small = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    report = _run_panel(manifest_bytes=small)

    assert report.status is PanelReportStatus.REJECTED
    assert "MANIFEST_INVALID" in report.rejection_reasons
    assert report.claim == "NOT_ALPHA_EVIDENCE"
    assert "configuration_rejected" in report.promotion.reasons
    assert report.promotion.recommendation is PanelPromotionRecommendation.REJECTED


def test_weak_candidate_and_baselines_reject_without_profit_claim() -> None:
    report = _run_panel(flip=True)

    assert report.status is PanelReportStatus.REJECTED
    assert PanelReportReason.WEAK_CANDIDATE_OR_BASELINE.value in report.rejection_reasons
    assert "qfast:non_positive_mean" in report.rejection_reasons
    assert report.claim == "NOT_ALPHA_EVIDENCE"
    assert report.rows == {}
    assert report.promotion.recommendation is PanelPromotionRecommendation.REJECTED


def test_perturbation_instability_rejects_the_panel_report() -> None:
    report = _run_panel(rate=UNSTABLE_RATE)

    assert report.status is PanelReportStatus.REJECTED
    assert PanelReportReason.PERTURBATION_INSTABILITY.value in report.rejection_reasons
    assert report.payload["stability"]["stable"] is False
    assert report.claim == "NOT_ALPHA_EVIDENCE"


def test_abstentions_stay_in_all_event_denominator_with_zero_signed_return(
    accepted_report, universe: _Universe
) -> None:
    payload = accepted_report.payload
    assert payload["eligible_event_ids"] == list(universe.event_ids)
    assert len(accepted_report.rows) == 23
    abstained = [
        event_id
        for event_id in universe.event_ids
        if accepted_report.rows[event_id].direction == "UNCERTAIN"
    ]
    assert len(abstained) == ABSTAINED_EVENT_COUNT
    for event_id in abstained:
        row = accepted_report.rows[event_id]
        assert row.admitted is False
        assert row.zero.signed_residual == 0.0
        assert row.p95.signed_residual == 0.0
        assert row.signal_match is False
        assert row.platform_convention_pnl_usd == "0.000000"
    arms = payload["arms"]
    for arm in ("zero", "p95"):
        metrics = arms[arm]["metrics"][CANDIDATE_METHOD]
        assert metrics["eligible_events"] == 23
        assert metrics["admitted_events"] == ADMITTED_EVENT_COUNT
        assert arms[arm]["event_count"] == 23
    assert payload["pnl_conventions"]["signal_accuracy"]["eligible_events"] == 23


def test_zero_and_p95_reports_share_frozen_event_set_and_strategy_identity(
    accepted_report, universe: _Universe
) -> None:
    payload = accepted_report.payload
    arms = payload["arms"]
    assert arms["zero"]["eligible_event_ids"] == list(universe.event_ids)
    assert arms["p95"]["eligible_event_ids"] == list(universe.event_ids)
    assert arms["zero"]["event_count"] == arms["p95"]["event_count"] == 23
    bindings = payload["bindings"]
    assert bindings["strategy_policy_sha256"] == POLICY_SHA
    assert bindings["panel_manifest_sha256"] == _sha(universe.panel_manifest_bytes)
    assert bindings["selection_rule_sha256"] == _sha(universe.panel_rule_bytes)
    assert bindings["event_list_sha256"] == _sha(universe.event_list_bytes)
    assert bindings["shadow_report_sha256"] == accepted_report.shadow.sha256
    assert len(bindings["market_window_sha256s"]) == 23
    assert len(bindings["source_manifest_sha256s"]) == 23
    for event_id, row in accepted_report.rows.items():
        assert row.decision_cutoff == _iso(universe.cutoffs[event_id])
        assert row.sector == universe.sectors[event_id]
        assert row.zero.admitted == row.p95.admitted
        assert row.source_manifest_sha256 == _sha(universe.source_manifests[event_id])
    assert accepted_report.shadow.validation.clock_status == "SYNCHRONIZED_CLOCKS_VERIFIED"
    assert accepted_report.shadow.reports["zero"].event_count == 23
    assert accepted_report.shadow.reports["p95"].event_count == 23


def test_four_pnl_conventions_are_reported_separately(accepted_report) -> None:
    conventions = accepted_report.payload["pnl_conventions"]
    assert set(conventions) == {
        "signal_accuracy",
        "theoretical_residual_pnl",
        "platform_convention_pnl",
        "fake_execution_pnl",
    }
    classes = {block["pnl_class"] for block in conventions.values()}
    assert classes == {
        "SIGNAL_ACCURACY",
        "SHADOW_THEORETICAL",
        "PLATFORM_CONVENTION",
        "FAKE_EXECUTION_SERVICE",
    }
    assert conventions["signal_accuracy"]["label_class"] == "OUTCOME_DERIVED_SYNTHETIC_FAKE"
    assert conventions["fake_execution_pnl"]["status"] == NOT_AVAILABLE
    assert conventions["fake_execution_pnl"]["sum_net_usd"] is None
    assert len(conventions["fake_execution_pnl"]["missing_fill_events"]) == 23
    theoretical_sum = sum(
        (Decimal(row.theoretical_residual_pnl) for row in accepted_report.rows.values()),
        Decimal(0),
    )
    assert Decimal(conventions["theoretical_residual_pnl"]["sum"]) == theoretical_sum
    matches = sum(1 for row in accepted_report.rows.values() if row.signal_match)
    assert conventions["signal_accuracy"]["matches"] == matches == ADMITTED_EVENT_COUNT
    assert conventions["signal_accuracy"]["accuracy"] == pytest.approx(ADMITTED_EVENT_COUNT / 23)


def test_platform_convention_pnl_is_conservative_and_explicit(accepted_report) -> None:
    conservatism = accepted_report.payload["conservatism"]
    assert conservatism["fee_per_trade_usd"] == str(PANEL_FEE_PER_TRADE_USD)
    assert conservatism["slippage_bps"] == str(PANEL_SLIPPAGE_BPS)
    assert conservatism["contract_multiplier"] == PANEL_CONTRACT_MULTIPLIER
    assert conservatism["costs_applied_to_abstentions"] is False
    assert conservatism["missing_fill_policy"] == reports_module.PANEL_MISSING_FILL_POLICY
    assert conservatism["option_assignment_exercise"] == reports_module.PANEL_OPTION_CASE_POLICY
    assert "LATENCY" in conservatism["latency_treatment"]

    row = next(row for row in accepted_report.rows.values() if row.admitted)
    price = Decimal(repr(row.p95.entry_price)).quantize(Decimal("0.00000001"))
    notional = price * Decimal(PANEL_CONTRACT_MULTIPLIER)
    gross = notional * Decimal(f"{row.p95.signed_residual:.12f}")
    expected = gross - PANEL_FEE_PER_TRADE_USD - notional * PANEL_SLIPPAGE_BPS / Decimal(10_000)
    actual = Decimal(row.platform_convention_pnl_usd)
    assert actual == expected.quantize(Decimal("0.000001"))
    assert actual < gross


def test_fake_execution_pnl_links_service_fills_and_costs(accepted_report) -> None:
    linked_events = sorted(accepted_report.rows)[:3]
    link = FakeExecutionLink(
        run_id="ESSCHER-PANEL-SERVICE-RUN-TEST",
        terminal_receipt_sha256=_sha(b"terminal"),
        net_pnl_usd_by_event={event_id: "-1.250000" for event_id in linked_events},
        costs_usd_by_event={event_id: "1.250000" for event_id in linked_events},
        option_case_status="NO_ASSIGNMENT_EXERCISE_OR_EXPIRY_OBSERVED_IN_FAKE_RUN",
    )
    report = _run_panel(fake_execution_link=link)

    assert report.status is PanelReportStatus.SHADOW_ONLY
    for event_id in linked_events:
        assert report.rows[event_id].fake_execution_pnl_usd == "-1.250000"
    unlinked = [event_id for event_id in report.rows if event_id not in linked_events]
    assert len(unlinked) == 20
    for event_id in unlinked:
        assert report.rows[event_id].fake_execution_pnl_usd == NOT_AVAILABLE
    block = report.payload["pnl_conventions"]["fake_execution_pnl"]
    assert block["status"] == "LINKED"
    assert block["run_id"] == link.run_id
    assert block["terminal_receipt_sha256"] == link.terminal_receipt_sha256
    assert Decimal(block["sum_net_usd"]) == Decimal("-3.750000")
    assert Decimal(block["sum_costs_usd"]) == Decimal("3.750000")
    assert block["missing_fill_events"] == sorted(unlinked)
    assert report.payload["conservatism"]["option_assignment_exercise"] == link.option_case_status


def test_promotion_is_rejected_for_synthetic_decisions_and_preregistered_profile(
    accepted_report,
) -> None:
    promotion = accepted_report.promotion
    assert promotion.recommendation is PanelPromotionRecommendation.REJECTED
    assert promotion.release_sha256 is None
    assert "synthetic_receipts_not_candidate_evidence" in promotion.reasons
    assert "latency_profile_not_measured" in promotion.reasons
    assert accepted_report.payload["promotion"]["release_sha256"] is None
    assert accepted_report.payload["promotion"]["recommendation"] == "REJECTED"


def test_promotion_binds_exactly_one_release_sha_for_candidate_evidence() -> None:
    release_raw = _release_bytes()
    release = parse_strategy_release(release_raw)
    report = _run_panel(
        receipt_bytes=_receipt_bytes_and_bundle(producer=ProducerKind.ROUTE_BOUND)[0],
        latency_profile_bytes=_host_measured_profile_bytes(),
        release_bytes=release_raw,
    )

    assert report.status is PanelReportStatus.SHADOW_ONLY
    assert report.classification == "ROUTE_BOUND_RECEIPTS"
    promotion = report.promotion
    assert promotion.recommendation is PanelPromotionRecommendation.BIND_SINGLE_RELEASE
    assert promotion.release_sha256 == release.release_sha256
    assert promotion.release_identity_sha256 == release.release_sha256
    assert promotion.reasons == ()
    payload = report.payload["promotion"]
    assert payload["release_sha256"] == release.release_sha256
    assert report.claim == "NOT_ALPHA_EVIDENCE"


def test_promotion_rejects_unparseable_and_unbound_releases() -> None:
    unparseable = _run_panel(release_bytes=b"{not-a-release")
    assert unparseable.promotion.recommendation is PanelPromotionRecommendation.REJECTED
    assert PanelReportReason.RELEASE_UNPARSEABLE.value in unparseable.rejection_reasons
    assert unparseable.promotion.release_sha256 is None
    assert unparseable.status is PanelReportStatus.REJECTED

    candidate_without_release = _run_panel(
        receipt_bytes=_receipt_bytes_and_bundle(producer=ProducerKind.ROUTE_BOUND)[0],
        latency_profile_bytes=_host_measured_profile_bytes(),
    )
    assert (
        candidate_without_release.promotion.recommendation is PanelPromotionRecommendation.REJECTED
    )
    assert PanelReportReason.RELEASE_NOT_BOUND.value in candidate_without_release.promotion.reasons
    assert candidate_without_release.status is PanelReportStatus.SHADOW_ONLY


def test_repeat_panel_runs_are_byte_identical() -> None:
    first = _run_panel()
    second = _run_panel()

    assert first.bytes == second.bytes
    assert first.sha256 == second.sha256
    payload = json.loads(first.bytes.decode("utf-8"))
    assert payload["schema"] == PANEL_EVIDENCE_REPORT_SCHEMA
    assert payload["schema_version"] == 1


def test_new_modules_import_no_network_or_broker_capability() -> None:
    paths = [Path(module.__file__) for module in (ledger_module, reports_module, comparison_module)]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                root = name.split(".", 1)[0]
                assert root not in FORBIDDEN_MODULES, f"{path.name} imports {name}"


def test_full_panel_run_performs_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted during panel evidence run")

    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)

    report = _run_panel()
    ledger = _frozen_ledger(tmp_path)
    _signal_all(ledger)
    comparison = compare_fullstack(report, _service_receipts(report))

    assert report.status is PanelReportStatus.SHADOW_ONLY
    assert ledger.verify_chain().valid is True
    assert comparison.sha256


def test_fullstack_comparison_links_service_receipts(accepted_report) -> None:
    receipts = _service_receipts(accepted_report)
    comparison = compare_fullstack(accepted_report, receipts)

    payload = json.loads(comparison.bytes.decode("utf-8"))
    assert payload["schema"] == "esscher.qfast_fullstack_comparison"
    assert payload["panel_report_sha256"] == accepted_report.sha256
    assert payload["panel_report_status"] == accepted_report.status.value
    assert payload["event_count"] == 23
    assert payload["linked_event_count"] == 23
    assert payload["findings"] == []
    assert payload["claim"] == "NOT_ALPHA_EVIDENCE"
    for event_id, row in payload["events"].items():
        assert row["linked"] is True
        assert row["panel_row_sha256"] == accepted_report.rows[event_id].row_sha256
        assert row["direction_agreement"] is True
        assert row["disposition_agreement"] is True
        assert len(row["stage_receipt_sha256s"]) == len(STAGE_ORDER)
        assert row["health_receipt_sha256"] == receipts[event_id].health_receipt_sha256
        assert row["terminal_receipt_sha256"] == receipts[event_id].terminal_receipt_sha256
    assert comparison.sha256 == _sha(comparison.bytes)
    assert compare_fullstack(accepted_report, receipts).bytes == comparison.bytes


def test_fullstack_comparison_flags_divergence_explicitly(accepted_report) -> None:
    flipped_event = sorted(accepted_report.rows)[0]
    skipped_event = sorted(accepted_report.rows)[1]
    truncated_event = sorted(accepted_report.rows)[2]
    receipts = _service_receipts(
        accepted_report,
        skip=(skipped_event,),
        flip=(flipped_event,),
        truncate=(truncated_event,),
        extra_event="OUTSIDE-20260901-EARNINGS",
    )

    comparison = compare_fullstack(accepted_report, receipts)
    codes = {(finding.code, finding.event_id) for finding in comparison.findings}
    assert (ComparisonFindingCode.DIRECTION_DIVERGENCE, flipped_event) in codes
    assert (ComparisonFindingCode.MISSING_SERVICE_RECEIPTS, skipped_event) in codes
    assert (ComparisonFindingCode.STAGE_CHAIN_INCOMPLETE, truncated_event) in codes
    assert (
        ComparisonFindingCode.UNKNOWN_SERVICE_EVENT,
        "OUTSIDE-20260901-EARNINGS",
    ) in codes
    payload = json.loads(comparison.bytes.decode("utf-8"))
    assert payload["linked_event_count"] == 22
    assert payload["events"][skipped_event]["linked"] is False
    divergent = payload["events"][flipped_event]
    assert divergent["direction_agreement"] is False
    if accepted_report.rows[flipped_event].admitted:
        assert divergent["disposition_agreement"] is False


def test_service_event_receipts_contract_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        ServiceEventReceipts(
            event_id="SYN-1",
            window_id="WINDOW-SYN-1",
            stage_receipt_sha256s=("not-a-digest",),
            health_receipt_sha256=_sha(b"health"),
            terminal_receipt_sha256=_sha(b"terminal"),
            direction="UP",
            disposition="COMPLETED",
        )
    with pytest.raises(ValueError):
        ServiceEventReceipts(
            event_id="SYN-1",
            window_id="WINDOW-SYN-1",
            stage_receipt_sha256s=(),
            health_receipt_sha256=_sha(b"health"),
            terminal_receipt_sha256=_sha(b"terminal"),
            direction="SIDEWAYS",
            disposition="COMPLETED",
        )


def test_comparison_module_stays_synthetic_labelled() -> None:
    assert comparison_module.COMPARISON_LABELS == (
        "NOT_ALPHA_EVIDENCE",
        "NO_BROKER_EXECUTION",
        "SYNTHETIC_FAKE",
    )
    assert ledger_module.LEDGER_CLAIMS == (
        "NOT_ALPHA_EVIDENCE",
        "NO_BROKER_EXECUTION",
        "SYNTHETIC_FAKE",
    )
