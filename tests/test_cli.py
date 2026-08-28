import json
from pathlib import Path

import pytest

from ringdown_market.cli import build_report, main

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_contract_panel.json"


def test_cli_writes_a_deterministic_explicitly_limited_report(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert main(["evaluate", "--input", str(FIXTURE), "--output", str(first)]) == 0
    assert main(["evaluate", "--input", str(FIXTURE), "--output", str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()
    report = json.loads(first.read_text(encoding="utf-8"))
    assert report["project"] == "Ringdown"
    assert report["mode"] == "OFFLINE_RESEARCH"
    assert report["data_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert report["event_count"] == 4
    assert report["claims"] == ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"]
    assert report["latency_profiles"]["p95"]["requested_latency_ms"] == 30_000
    assert report["latency_profiles"]["p95"]["qfast"]["status"] == "NOT_REJECTED_SMALL_SAMPLE"
    assert report["latency_gate"]["status"] == "NOT_REJECTED_SMALL_SAMPLE"
    assert len(report["input_sha256"]) == 64
    assert len(report["protocol_sha256"]) == 64


def test_rejects_an_unclassified_dataset() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del payload["fixture_class"]

    with pytest.raises(ValueError, match="data class"):
        build_report(json.dumps(payload).encode("utf-8"))


def test_real_panel_cannot_lower_the_preregistered_twenty_event_floor() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["fixture_class"] = "POINT_IN_TIME_EVENT_PANEL"
    payload["spec"]["minimum_events"] = 4

    with pytest.raises(ValueError, match="at least 20"):
        build_report(json.dumps(payload).encode("utf-8"))
