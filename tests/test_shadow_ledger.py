from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ringdown_market.data import run_capture_request
from ringdown_market.data.panel import parse_confirmation_panel
from ringdown_market.evaluation import (
    SampleClass,
    ShadowLedger,
    ShadowLedgerError,
    ShadowRunInputs,
    ShadowStage,
    build_shadow_report,
    run_shadow_event,
    shadow_report_sha256,
)
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.risk.policy import build_frozen_limits
from ringdown_market.risk.truth import AccountTruth
from ringdown_market.strategy import STRATEGY_POLICY_V1_SHA256, parse_frozen_strategy_policy_v1
from ringdown_market.strategy.engine import DecisionEngine
from ringdown_market.strategy.reasoner import FakeReasoner, ReasonerRoute

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "configs" / "strategy_v1.json"
CAPTURE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "strategy_v1_synthetic_capture_request.json"
PANEL_PATH = REPO_ROOT / "data" / "confirmation-panel" / "event-list-v1.json"

RECORDED_AT = datetime(2026, 9, 11, 13, 36, 0, tzinfo=UTC)
DECISION_CUTOFF = datetime(2026, 9, 11, 13, 35, 0, tzinfo=UTC)
ROUTE = ReasonerRoute(
    route_id="esscher-v1-route-1", prompt_sha256="a1" * 32, output_schema_sha256="b2" * 32
)
DEVELOPMENT_IDS = frozenset(
    {
        "KR-2026Q2-EARNINGS",
        "GIS-2027Q1-EARNINGS",
        "MU-2026Q4-EARNINGS",
        "NKE-2027Q1-EARNINGS",
    }
)


def policy():
    return parse_frozen_strategy_policy_v1(POLICY_PATH.read_bytes())


def eligible_snapshot_bytes() -> bytes:
    payload = json.loads(CAPTURE_FIXTURE.read_text(encoding="utf-8"))
    snapshot = run_capture_request(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        policy=policy(),
        expected_policy_sha256=STRATEGY_POLICY_V1_SHA256,
    )
    assert snapshot.eligible
    return snapshot.raw


def reasoner_payload(direction: str = "UP") -> dict:
    return {
        "schema": "esscher.reasoner_output",
        "schema_version": 1,
        "direction": direction,
        "confidence": "0.72",
        "citations": [
            {
                "citation_id": "c-1",
                "evidence_id": "issuer-release",
                "claim_sha256": "1" * 64,
            }
        ],
        "falsifier": {
            "falsifier_id": "f-1",
            "evidence_id": "sec-filing",
            "claim_sha256": "2" * 64,
        },
    }


def chain_bytes() -> bytes:
    from test_option_compiler import NEAR_EXPIRY, contract
    from test_option_compiler import chain_bytes as build_chain

    return build_chain(
        [
            contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), bid="1.10", ask="1.30"),
            contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
        ]
    )


def fresh_inputs(**overrides) -> ShadowRunInputs:
    defaults = {
        "snapshot_bytes": eligible_snapshot_bytes(),
        "chain_bytes": chain_bytes(),
        "decision_cutoff": DECISION_CUTOFF,
        "recorded_at": RECORDED_AT,
        "account": AccountTruth(
            equity=Decimal("100000.00"), observed_at=RECORDED_AT, raw_sha256="f" * 64
        ),
        "positions": (),
        "open_orders": (),
    }
    defaults.update(overrides)
    return ShadowRunInputs(**defaults)


def run_event(
    tmp_path: Path,
    *,
    event_id: str = "shadow-acme-2026q3",
    sample_class: SampleClass = SampleClass.PROSPECTIVE,
    reasoner: dict | None = None,
    inputs: ShadowRunInputs | None = None,
):
    ledger = RiskLedger(tmp_path / "risk.db")
    shadow = ShadowLedger()
    record = run_shadow_event(
        event_id=event_id,
        sample_class=sample_class,
        development_event_ids=DEVELOPMENT_IDS,
        inputs=inputs or fresh_inputs(),
        engine=DecisionEngine(
            policy=policy(), route=ROUTE, reasoner=FakeReasoner(reasoner or reasoner_payload())
        ),
        route=ROUTE,
        chain_underlying_ticker="ACME",
        risk_cap=Decimal("500.00"),
        ledger=ledger,
        limits=build_frozen_limits(),
        shadow_ledger=shadow,
    )
    return record, shadow, ledger


def test_full_shadow_run_retains_every_stage(tmp_path: Path) -> None:
    record, shadow, ledger = run_event(tmp_path)
    stages = [outcome.stage for outcome in record.stages]
    assert stages == [
        ShadowStage.SNAPSHOT,
        ShadowStage.DECISION,
        ShadowStage.PACKAGE,
        ShadowStage.RISK,
        ShadowStage.EXIT,
    ]
    assert record.terminal_disposition == "SHADOW_HOLD_SCHEDULED"
    assert shadow.records() == (record,)
    assert len(ledger.open_reservations()) == 1


def test_abstention_remains_visible_in_ledger(tmp_path: Path) -> None:
    record, shadow, _ = run_event(
        tmp_path, reasoner={**reasoner_payload(), "direction": "UNCERTAIN"}
    )
    decision_stage = record.stages[1]
    assert decision_stage.stage is ShadowStage.DECISION
    assert decision_stage.disposition == "ABSTAIN"
    assert record.terminal_disposition == "ABSTAIN"
    assert len(shadow.records()) == 1


def test_no_package_remains_visible(tmp_path: Path) -> None:
    record, _, ledger = run_event(
        tmp_path, inputs=fresh_inputs(chain_bytes=b'{"schema":"esscher.other_chain"}')
    )
    package_stage = record.stages[2]
    assert package_stage.stage is ShadowStage.PACKAGE
    assert package_stage.disposition == "NO_PACKAGE"
    assert package_stage.reasons == ("CHAIN_DOCUMENT_REJECTED",)
    assert len(ledger.open_reservations()) == 0


def test_risk_rejection_remains_visible(tmp_path: Path) -> None:
    stale = AccountTruth(
        equity=Decimal("100000.00"),
        observed_at=datetime(2026, 9, 11, 13, 30, 0, tzinfo=UTC),
        raw_sha256="f" * 64,
    )
    record, _, ledger = run_event(tmp_path, inputs=fresh_inputs(account=stale))
    risk_stage = record.stages[3]
    assert risk_stage.stage is ShadowStage.RISK
    assert risk_stage.disposition == "REJECTED"
    assert "STALE_TRUTH" in risk_stage.reasons
    assert len(ledger.open_reservations()) == 0


def test_development_events_cannot_enter_confirmation_or_prospective(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_event(tmp_path, event_id="KR-2026Q2-EARNINGS", sample_class=SampleClass.CONFIRMATION)
    with pytest.raises(ValueError):
        run_event(tmp_path, event_id="MU-2026Q4-EARNINGS", sample_class=SampleClass.PROSPECTIVE)


def test_development_sample_class_allowed_for_development_events(tmp_path: Path) -> None:
    record, _, _ = run_event(
        tmp_path, event_id="KR-2026Q2-EARNINGS", sample_class=SampleClass.DEVELOPMENT
    )
    assert record.sample_class is SampleClass.DEVELOPMENT


def test_duplicate_event_recording_rejected(tmp_path: Path) -> None:
    record, shadow, _ = run_event(tmp_path)
    with pytest.raises(ShadowLedgerError):
        shadow.record(record)


def test_shadow_report_is_deterministic_and_hash_bound(tmp_path: Path) -> None:
    record, shadow, _ = run_event(tmp_path)
    panel = parse_confirmation_panel(PANEL_PATH.read_bytes())
    first = build_shadow_report(
        shadow,
        policy=policy(),
        panel=panel,
        historical_report=None,
        threshold_met=False,
        limitations=["UNDERLYING_RESIDUAL_ONLY", "INDICATIVE_OPTION_EXPRESSION", "BROKER_FREE"],
    )
    second = build_shadow_report(
        shadow,
        policy=policy(),
        panel=panel,
        historical_report=None,
        threshold_met=False,
        limitations=["UNDERLYING_RESIDUAL_ONLY", "INDICATIVE_OPTION_EXPRESSION", "BROKER_FREE"],
    )
    assert first == second
    assert shadow_report_sha256(first) == shadow_report_sha256(second)

    report = json.loads(first)
    assert report["policy_sha256"] == STRATEGY_POLICY_V1_SHA256
    assert report["panel_sha256"] == panel.sha256
    assert report["preregistered_threshold"] == {
        "required": True,
        "met": False,
        "disposition": "NOT_MET",
    }
    assert report["records"][0]["event_id"] == record.event_id
    assert report["claim"] == "NOT_ALPHA_EVIDENCE"


def test_coverage_and_failure_counts_reported(tmp_path: Path) -> None:
    record_a, shadow_a, _ = run_event(tmp_path, event_id="shadow-a")
    panel = parse_confirmation_panel(PANEL_PATH.read_bytes())
    report = json.loads(
        build_shadow_report(
            shadow_a,
            policy=policy(),
            panel=panel,
            historical_report=None,
            threshold_met=False,
            limitations=[],
        )
    )
    assert report["coverage_by_class"]["PROSPECTIVE"] == 1
    assert report["abstention_count"] == 0
    assert report["failure_count"] == 0
    assert record_a.event_id == "shadow-a"


def test_evaluation_modules_import_no_broker_machinery() -> None:
    evaluation_root = REPO_ROOT / "src" / "ringdown_market" / "evaluation"
    for source_file in sorted(evaluation_root.glob("*.py")):
        text = source_file.read_text(encoding="utf-8")
        for marker in ("execution.mcp", "host_mcp", "paper_demo", "McpPaperBroker", "credentials"):
            assert marker not in text, f"{source_file.name} references {marker}"


def test_sample_classes_mechanically_separated() -> None:
    assert SampleClass.DEVELOPMENT.value != SampleClass.CONFIRMATION.value
    assert SampleClass.CONFIRMATION.value != SampleClass.PROSPECTIVE.value
