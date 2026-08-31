from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ringdown_market.contracts.security_lineage import (
    SECURITY_LINEAGE_V1_SHA256,
    load_security_lineage,
)
from ringdown_market.contracts.source_matrix import source_matrix_bytes
from ringdown_market.sourcedata.lineage_gate import evaluate_lineage
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected

REPO_ROOT = Path(__file__).parent.parent
LINEAGE_PATH = (
    REPO_ROOT / "src" / "ringdown_market" / "contracts" / "policies" / "security_lineage_v1.json"
)
EVIDENCE_DIR = REPO_ROOT / "data" / "security-lineage" / "evidence"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "sourcedata" / "synthetic_snapshot_inputs_v1.json"
FULL_DEV_CONDITIONS = (
    "--condition-satisfied",
    "HUMAN_VERIFIED_CAPTURE",
    "--condition-satisfied",
    "PER_RECORD_PRIMARY_PROVENANCE",
    "--condition-satisfied",
    "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
)


def _mutated_lineage_bytes(mutate) -> bytes:
    payload = json.loads(LINEAGE_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    return json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")


def test_lineage_gate_resolves_packaged_lineage() -> None:
    report = evaluate_lineage(event_id="KR-2026Q2-EARNINGS")
    assert report.security_lineage_sha256 == SECURITY_LINEAGE_V1_SHA256
    assert report.resolution.issuer_id == "0000056873"
    assert report.resolution.active_at_cutoff is True


def test_lineage_gate_fails_closed_on_missing_chain() -> None:
    with pytest.raises(CollectorRejected) as error:
        evaluate_lineage(event_id="GHOST-2099Q1-EARNINGS")
    assert error.value.reason == CollectorReason.LINEAGE_MISSING


def test_lineage_gate_fails_closed_on_delisted_listing() -> None:
    def mutate(payload: dict) -> None:
        payload["listings"][0]["listed_to"] = "2026-01-15"
        payload["listings"][0]["delisting_reason"] = "VOLUNTARY_DELISTING"

    drifted = _mutated_lineage_bytes(mutate)
    with pytest.raises(CollectorRejected) as error:
        evaluate_lineage(event_id="KR-2026Q2-EARNINGS", lineage_bytes=drifted)
    assert error.value.reason == CollectorReason.LINEAGE_MISSING
    assert "no current-survivor fallback" in error.value.detail


def test_lineage_gate_fails_closed_on_symbol_reuse() -> None:
    def mutate(payload: dict) -> None:
        ghost = {
            "issuer_id": "0009999999",
            "names": [
                {
                    "name": "Ghost Issuer",
                    "effective_from": "2000-01-01T00:00:00Z",
                    "provenance": payload["issuers"][0]["names"][0]["provenance"],
                }
            ],
            "tickers": [
                {
                    "ticker": "KR",
                    "security_id": "0009999999:COMMON",
                    "valid_from": "2000-01-01",
                    "valid_to": None,
                    "provenance": payload["issuers"][0]["tickers"][0]["provenance"],
                }
            ],
        }
        payload["issuers"].append(ghost)
        payload["securities"].append(
            {
                "security_id": "0009999999:COMMON",
                "issuer_id": "0009999999",
                "security_type": "US_COMMON_STOCK",
                "provenance": payload["securities"][0]["provenance"],
            }
        )
        payload["issuers"][0]["tickers"][0]["valid_to"] = "2025-01-01"

    drifted = _mutated_lineage_bytes(mutate)
    with pytest.raises(CollectorRejected) as error:
        evaluate_lineage(event_id="KR-2026Q2-EARNINGS", lineage_bytes=drifted)
    assert error.value.reason == CollectorReason.SYMBOL_REUSE_DETECTED


def test_lineage_gate_fails_closed_on_drifted_upstream_binding() -> None:
    def mutate(payload: dict) -> None:
        payload["source_matrix_sha256"] = "1" * 64

    drifted = _mutated_lineage_bytes(mutate)
    with pytest.raises(CollectorRejected) as error:
        evaluate_lineage(event_id="KR-2026Q2-EARNINGS", lineage_bytes=drifted)
    assert error.value.reason == CollectorReason.LINEAGE_DRIFT


def _capture_args(output_dir: Path, *, event_id: str = "KR-2026Q2-EARNINGS") -> list[str]:
    return [
        "--event-id",
        event_id,
        "--fixture",
        str(FIXTURE_PATH),
        "--capture-at",
        "2026-09-11T13:35:10Z",
        "--output-dir",
        str(output_dir),
        *FULL_DEV_CONDITIONS,
    ]


def test_capture_command_writes_lineage_receipt_and_identity_binding(
    tmp_path: Path, monkeypatch
) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    exit_code = main(_capture_args(tmp_path))

    assert exit_code == 0
    receipt_lines = (tmp_path / "lineage_receipts.jsonl").read_text().splitlines()
    assert len(receipt_lines) == 1
    receipt = json.loads(receipt_lines[0])
    assert receipt["schema"] == "esscher.lineage_receipt"
    assert receipt["issuer_id"] == "0000056873"
    assert receipt["active_at_cutoff"] is True
    identity = json.loads((tmp_path / "capture_identity.json").read_text())
    assert identity["security_lineage_sha256"] == SECURITY_LINEAGE_V1_SHA256


def test_capture_uses_one_canonical_matrix_for_identity_and_lineage(
    tmp_path: Path, monkeypatch
) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert main(_capture_args(tmp_path)) == 0

    canonical_matrix_sha256 = hashlib.sha256(source_matrix_bytes()).hexdigest()
    identity = json.loads((tmp_path / "capture_identity.json").read_text())
    assert identity["source_matrix_sha256"] == canonical_matrix_sha256
    assert load_security_lineage().source_matrix_sha256 == canonical_matrix_sha256


def test_lineage_gate_rejects_an_alternate_matrix_before_resolution() -> None:
    with pytest.raises(CollectorRejected) as error:
        evaluate_lineage(
            event_id="KR-2026Q2-EARNINGS",
            matrix_bytes=b"\n" + source_matrix_bytes(),
        )

    assert error.value.reason == CollectorReason.LINEAGE_DRIFT


def test_capture_command_rejects_unknown_lineage_before_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    exit_code = main(_capture_args(tmp_path, event_id="GHOST-2099Q1-EARNINGS"))

    assert exit_code == 2
    assert not (tmp_path / "strategy_snapshot.json").exists()


def test_capture_is_byte_identical_with_lineage_gate(tmp_path: Path, monkeypatch) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    outputs = []
    for index in range(2):
        output_dir = tmp_path / f"run-{index}"
        output_dir.mkdir()
        assert main(_capture_args(output_dir)) == 0
        outputs.append(output_dir)
    for name in (
        "strategy_snapshot.json",
        "feature_receipt.json",
        "lineage_receipts.jsonl",
        "capture_identity.json",
    ):
        assert (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes(), name


def test_lineage_evidence_bundles_match_committed_hashes() -> None:
    import hashlib

    manifests = sorted(EVIDENCE_DIR.glob("*/manifest.json"))
    assert len(manifests) == 4
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["schema"] == "esscher.lineage_evidence"
        assert manifest["data_class"] == "SOURCE_FEASIBILITY_PROBE"
        for claim in ("DEV_ONLY", "NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION"):
            assert claim in manifest["claims"]
        for artifact in manifest["artifacts"]:
            artifact_path = manifest_path.parent / artifact["file"]
            assert artifact_path.is_file()
            raw = artifact_path.read_bytes()
            assert len(raw) == artifact["bytes"]
            assert hashlib.sha256(raw).hexdigest() == artifact["sha256"]


def test_lineage_evidence_covers_required_action_classes() -> None:
    classes = set()
    for manifest_path in EVIDENCE_DIR.glob("*/manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        classes.add(manifest["lineage_class"])
    assert classes == {
        "SPLIT_RECORD",
        "DELISTING_RECORD",
        "SYMBOL_CHANGE_RECORD",
        "ACCESS_BOUNDARY",
    }


def test_occ_access_boundary_is_recorded_fail_closed() -> None:
    manifest = json.loads((EVIDENCE_DIR / "lr4-occ-access-boundary" / "manifest.json").read_text())
    assert manifest["provenance_facts"]["access_status"] == "AUTOMATED_RETRIEVAL_BLOCKED"
    assert manifest["provenance_facts"]["required_condition"] == "HUMAN_VERIFIED_CAPTURE"
    boundary = json.loads(
        (EVIDENCE_DIR / "lr4-occ-access-boundary" / "occ-access-boundary.json").read_text()
    )
    assert all(entry["status"] == 403 for entry in boundary)
