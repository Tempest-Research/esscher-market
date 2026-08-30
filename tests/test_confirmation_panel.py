from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ringdown_market.alpha.models import Direction
from ringdown_market.alpha.qfast import CANDIDATE_METHOD, PanelRow
from ringdown_market.data import (
    MINIMUM_ELIGIBLE_EVENTS,
    PanelRejected,
    PanelRejectionReason,
    build_panel_report,
    panel_report_bytes,
    panel_report_sha256,
    parse_confirmation_panel,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RULE_PATH = REPO_ROOT / "data" / "confirmation-panel" / "selection-rule-v1.json"
PANEL_PATH = REPO_ROOT / "data" / "confirmation-panel" / "event-list-v1.json"


def raw_panel() -> bytes:
    return PANEL_PATH.read_bytes()


def panel_payload() -> dict[str, Any]:
    return json.loads(raw_panel())


def render(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")


def synthetic_rows(count: int = 20) -> list[PanelRow]:
    return [
        PanelRow(
            event_id=f"synthetic-event-{index}",
            signed_returns={CANDIDATE_METHOD: 0.004, "always_abstain": 0.0},
            admitted={CANDIDATE_METHOD: True, "always_abstain": False},
        )
        for index in range(count)
    ]


def test_committed_panel_manifest_is_honest_and_frozen() -> None:
    panel = parse_confirmation_panel(raw_panel())
    assert panel.panel_status == "COLLECTION_INCOMPLETE"
    assert panel.eligible_count == 0
    assert len(panel.excluded) == 4
    excluded_ids = {event_id for event_id, _ in panel.excluded}
    assert excluded_ids == {
        "KR-2026Q2-EARNINGS",
        "GIS-2027Q1-EARNINGS",
        "MU-2026Q4-EARNINGS",
        "NKE-2027Q1-EARNINGS",
    }
    assert all(reason == "CONTRACT_DEVELOPMENT_EVENT" for _, reason in panel.excluded)
    assert "ELIGIBLE_EVENTS_BELOW_MINIMUM" in panel.stop_conditions


def test_panel_manifest_binds_selection_rule_and_policy() -> None:
    panel = parse_confirmation_panel(raw_panel())
    rule_sha = hashlib.sha256(RULE_PATH.read_bytes()).hexdigest()
    assert panel.selection_rule_sha256 == rule_sha

    payload = panel_payload()
    payload["policy_sha256"] = "0" * 64
    with pytest.raises(PanelRejected) as caught:
        parse_confirmation_panel(render(payload))
    assert caught.value.reason is PanelRejectionReason.POLICY_HASH_MISMATCH


def test_panel_rejects_outcome_leakage_at_freeze() -> None:
    payload = panel_payload()
    payload["outcome_fields"] = ["residual_return"]
    with pytest.raises(PanelRejected) as caught:
        parse_confirmation_panel(render(payload))
    assert caught.value.reason is PanelRejectionReason.INVALID_VALUE


def test_panel_rejects_unknown_field() -> None:
    payload = panel_payload()
    payload["tuning_knob"] = True
    with pytest.raises(PanelRejected) as caught:
        parse_confirmation_panel(render(payload))
    assert caught.value.reason is PanelRejectionReason.UNKNOWN_FIELD


def test_development_events_cannot_be_admitted() -> None:
    payload = panel_payload()
    payload["event_ids"] = ["KR-2026Q2-EARNINGS"]
    with pytest.raises(PanelRejected) as caught:
        parse_confirmation_panel(render(payload))
    assert caught.value.reason is PanelRejectionReason.DEVELOPMENT_EVENT_ADMITTED


def test_panel_size_ceiling_enforced() -> None:
    payload = panel_payload()
    payload["event_ids"] = [f"event-{index}" for index in range(31)]
    payload["panel_status"] = "FROZEN_ELIGIBLE"
    with pytest.raises(PanelRejected) as caught:
        parse_confirmation_panel(render(payload))
    assert caught.value.reason is PanelRejectionReason.PANEL_SIZE_EXCEEDED


def test_panel_below_floor_must_declare_collection_incomplete() -> None:
    payload = panel_payload()
    payload["event_ids"] = [f"event-{index}" for index in range(5)]
    payload["panel_status"] = "FROZEN_ELIGIBLE"
    with pytest.raises(PanelRejected) as caught:
        parse_confirmation_panel(render(payload))
    assert caught.value.reason is PanelRejectionReason.INVALID_VALUE


def test_report_is_deterministic_and_latency_separated() -> None:
    panel = parse_confirmation_panel(raw_panel())
    rows = synthetic_rows()
    first = build_panel_report(panel, profile_rows={"zero": rows, "p95": rows})
    second = build_panel_report(panel, profile_rows={"zero": rows, "p95": rows})
    assert panel_report_bytes(first) == panel_report_bytes(second)
    assert panel_report_sha256(first) == panel_report_sha256(second)
    assert set(first["profiles"]) == {"zero", "p95"}
    assert first["claim"] == "NOT_ALPHA_EVIDENCE"
    assert first["latency_gate"]["required_profile"] == "p95"


def test_report_requires_both_latency_profiles() -> None:
    panel = parse_confirmation_panel(raw_panel())
    with pytest.raises(PanelRejected) as caught:
        build_panel_report(panel, profile_rows={"zero": synthetic_rows()})
    assert caught.value.reason is PanelRejectionReason.INVALID_VALUE


def test_empty_panel_reports_insufficient_data_honestly() -> None:
    panel = parse_confirmation_panel(raw_panel())
    report = build_panel_report(panel, profile_rows={"zero": [], "p95": []})
    assert report["eligible_events"] == 0
    assert report["panel_status"] == "COLLECTION_INCOMPLETE"
    assert report["profiles"]["zero"]["status"] == "INSUFFICIENT_DATA"
    assert report["profiles"]["p95"]["status"] == "INSUFFICIENT_DATA"
    assert report["latency_gate"]["status"] == "INSUFFICIENT_DATA"


def test_abstentions_remain_in_eligible_denominator() -> None:
    panel = parse_confirmation_panel(raw_panel())
    rows = [
        PanelRow(
            event_id=f"synthetic-event-{index}",
            signed_returns={CANDIDATE_METHOD: 0.0, "always_abstain": 0.0},
            admitted={CANDIDATE_METHOD: False, "always_abstain": False},
        )
        for index in range(MINIMUM_ELIGIBLE_EVENTS)
    ]
    report = build_panel_report(panel, profile_rows={"zero": rows, "p95": rows})
    metrics = report["profiles"]["zero"]
    assert metrics["event_count"] == MINIMUM_ELIGIBLE_EVENTS
    assert metrics["status"] == "INSUFFICIENT_DATA" or metrics["status"] == "REJECTED"


def test_direction_enum_covers_panel_abstention_state() -> None:
    assert Direction.UNCERTAIN.multiplier == 0
