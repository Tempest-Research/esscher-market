"""CLI regression for binding the canonical issue #42 lineage receipt.

The capture command must carry the digest of the exact canonical lineage bytes
through CaptureConfiguration into the feature receipt and its evidence IDs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ringdown_market.sourcedata import capture
from ringdown_market.sourcedata.lineage_gate import evaluate_lineage, lineage_receipt_bytes

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "sourcedata" / "synthetic_snapshot_inputs_v1.json"
EVENT_ID = "KR-2026Q2-EARNINGS"


def _capture_args(output_dir: Path) -> list[str]:
    return [
        "--event-id",
        EVENT_ID,
        "--fixture",
        str(FIXTURE_PATH),
        "--capture-at",
        "2026-09-11T13:35:10Z",
        "--output-dir",
        str(output_dir),
        "--condition-satisfied",
        "HUMAN_VERIFIED_CAPTURE",
        "--condition-satisfied",
        "PER_RECORD_PRIMARY_PROVENANCE",
        "--condition-satisfied",
        "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
    ]


def test_capture_cli_binds_exact_lineage_receipt_bytes_to_configuration_and_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    """The emitted digest is the SHA-256 of the canonical issue #42 receipt bytes."""

    report = evaluate_lineage(event_id=EVENT_ID)
    expected_receipt_bytes = lineage_receipt_bytes(report.resolution)
    expected_sha256 = hashlib.sha256(expected_receipt_bytes).hexdigest()
    output_dir = tmp_path / "capture-output"
    output_dir.mkdir()
    captured_configurations = []
    original_run_capture = capture.run_capture

    def observe_configuration(configuration, candidate, fixture):
        captured_configurations.append(configuration)
        return original_run_capture(configuration, candidate, fixture)

    monkeypatch.setattr(capture, "run_capture", observe_configuration)
    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")

    assert capture.main(_capture_args(output_dir)) == 0
    assert len(captured_configurations) == 1
    assert captured_configurations[0].lineage_receipt_sha256 == expected_sha256

    feature_receipt = json.loads((output_dir / "feature_receipt.json").read_text(encoding="utf-8"))
    assert feature_receipt["lineage_receipt_sha256"] == expected_sha256
    assert f"LINEAGE_RECEIPT:{expected_sha256}" in feature_receipt["evidence_ids"]
    assert feature_receipt["evidence_ids"] == sorted(set(feature_receipt["evidence_ids"]))

    emitted_lineage_receipt = (output_dir / "lineage_receipts.jsonl").read_bytes()
    assert emitted_lineage_receipt == expected_receipt_bytes + b"\n"
    emitted_sha256 = hashlib.sha256(emitted_lineage_receipt.removesuffix(b"\n")).hexdigest()
    assert emitted_sha256 == expected_sha256
