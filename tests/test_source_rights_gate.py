from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from ringdown_market.contracts.source_matrix import SOURCE_MATRIX_V1_SHA256
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.rights_gate import EARNINGS_CANDIDATE_ID, evaluate_capture_rights
from ringdown_market.strategy.policy import parse_strategy_policy, strategy_policy_bytes

REPO_ROOT = Path(__file__).parent.parent
BUNDLES_DIR = REPO_ROOT / "data" / "source-feasibility" / "golden-bundles"
FULL_DEV_CONDITIONS = frozenset(
    {
        "HUMAN_VERIFIED_CAPTURE",
        "PER_RECORD_PRIMARY_PROVENANCE",
        "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
    }
)
P0_CONTRACT_EVENTS = frozenset(
    {
        "KR-2026Q2-EARNINGS",
        "GIS-2027Q1-EARNINGS",
        "MU-2026Q4-EARNINGS",
        "NKE-2027Q1-EARNINGS",
    }
)
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "bundle_id",
        "title",
        "matrix_categories",
        "data_class",
        "claims",
        "partition",
        "event_id",
        "event_date",
        "reproducibility",
        "capture_note",
        "artifacts",
    }
)
MANIFEST_OPTIONAL_FIELDS = frozenset(
    {"retrieval_agent", "provenance_facts", "probe_time", "probe_observations"}
)
SECRET_LIKE_FRAGMENTS = ("key_id", "secret", "password", "token", "APCA")


def _manifests() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for bundle_dir in sorted(path for path in BUNDLES_DIR.iterdir() if path.is_dir()):
        manifest_path = bundle_dir / "manifest.json"
        assert manifest_path.is_file(), f"missing manifest for {bundle_dir.name}"
        result[bundle_dir.name] = json.loads(manifest_path.read_text(encoding="utf-8"))
    return result


def _untouched_windows() -> list[tuple[date, date]]:
    policy = parse_strategy_policy(strategy_policy_bytes())
    windows: list[tuple[date, date]] = []
    for candidate_id in policy.candidate_ids:
        partitions = policy.candidate(candidate_id)["partitions"]
        for partition in partitions:
            partition_id = str(partition["partition_id"])
            if not partition_id.startswith("UNTOUCHED"):
                continue
            start = str(partition["start_inclusive"])[:10]
            end = str(partition["end_exclusive"])[:10]
            windows.append((date.fromisoformat(start), date.fromisoformat(end)))
    assert windows
    return windows


def test_rights_gate_fails_closed_without_conditions() -> None:
    with pytest.raises(CollectorRejected) as error:
        evaluate_capture_rights(
            candidate_id=EARNINGS_CANDIDATE_ID, satisfied_conditions=frozenset()
        )
    assert error.value.reason == CollectorReason.SOURCE_RIGHTS_LIMITATION_UNMET


def test_rights_gate_passes_with_dev_conditions_and_binds_matrix_digest() -> None:
    report = evaluate_capture_rights(
        candidate_id=EARNINGS_CANDIDATE_ID, satisfied_conditions=FULL_DEV_CONDITIONS
    )
    assert report.source_matrix_sha256 == SOURCE_MATRIX_V1_SHA256
    assert len(report.decisions) == 5
    assert all(decision.verdict != "BLOCKED" for decision in report.decisions)


def test_rights_gate_rejects_drifted_matrix_bytes(tmp_path: Path) -> None:
    from ringdown_market.contracts.source_matrix import source_matrix_bytes

    payload = json.loads(source_matrix_bytes().decode("utf-8"))
    payload["policy_sha256"] = "0" * 64
    drifted = json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")
    with pytest.raises(CollectorRejected) as error:
        evaluate_capture_rights(
            candidate_id=EARNINGS_CANDIDATE_ID,
            matrix_bytes=drifted,
            satisfied_conditions=FULL_DEV_CONDITIONS,
        )
    assert error.value.reason == CollectorReason.SOURCE_MATRIX_DRIFT


def test_capture_command_rejects_removed_source_matrix_switch(tmp_path: Path, monkeypatch) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    fixture = REPO_ROOT / "tests" / "fixtures" / "sourcedata" / "synthetic_snapshot_inputs_v1.json"
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--event-id",
                "KR-2026Q2-EARNINGS",
                "--capture-at",
                "2026-09-11T13:35:10Z",
                "--output-dir",
                str(tmp_path),
                "--fixture",
                str(fixture),
                "--source-matrix",
                str(tmp_path / "no-such-matrix.json"),
            ]
        )
    assert error.value.code == 2


def test_acceptance_golden_bundle_count_and_registration() -> None:
    manifests = _manifests()
    assert 3 <= len(manifests) <= 5
    matrix = json.loads(
        (
            REPO_ROOT
            / "src"
            / "ringdown_market"
            / "contracts"
            / "policies"
            / "source_matrix_v1.json"
        ).read_text(encoding="utf-8")
    )
    registered = {bundle_id.casefold() for bundle_id in matrix["bundles"]}
    normalized = {name.replace("-", "_").upper().casefold() for name in manifests}
    assert normalized == registered


def test_golden_bundle_manifests_are_strict_and_dev_only() -> None:
    for name, manifest in _manifests().items():
        fields = set(manifest)
        assert fields >= MANIFEST_FIELDS, name
        assert fields <= MANIFEST_FIELDS | MANIFEST_OPTIONAL_FIELDS, name
        assert manifest["schema"] == "esscher.feasibility_bundle", name
        assert manifest["schema_version"] == 1, name
        assert manifest["data_class"] == "SOURCE_FEASIBILITY_PROBE", name
        assert manifest["partition"] == "DEVELOPMENT", name
        for claim in ("DEV_ONLY", "NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION"):
            assert claim in manifest["claims"], name
        for category in manifest["matrix_categories"]:
            assert category, name


def test_golden_bundle_artifacts_match_committed_hashes() -> None:
    for name, manifest in _manifests().items():
        bundle_dir = BUNDLES_DIR / name
        for artifact in manifest["artifacts"]:
            artifact_path = bundle_dir / artifact["file"]
            assert artifact_path.is_file(), artifact["file"]
            raw = artifact_path.read_bytes()
            assert len(raw) == artifact["bytes"], artifact["file"]
            assert hashlib.sha256(raw).hexdigest() == artifact["sha256"], artifact["file"]
            assert artifact["redistribution"] in {
                "PUBLIC_DOMAIN_REDISTRIBUTABLE",
                "OFFICIAL_DOCUMENT_BYTES",
            }, artifact["file"]


def test_acceptance_golden_bundles_do_not_consume_untouched_panel() -> None:
    windows = _untouched_windows()
    for name, manifest in _manifests().items():
        event_id = manifest["event_id"]
        if event_id is not None:
            assert event_id not in P0_CONTRACT_EVENTS, name
        event_date = manifest["event_date"]
        if event_date is None:
            continue
        observed = date.fromisoformat(event_date)
        for start, end in windows:
            assert not (start <= observed < end), (
                f"{name}: event date {event_date} falls inside an untouched partition"
            )


def test_golden_bundle_probe_receipts_commit_no_licensed_bytes_or_credentials() -> None:
    probe_bundles = [
        manifest
        for manifest in _manifests().values()
        if manifest["reproducibility"] == "HASH_RECEIPT_ONLY"
    ]
    assert probe_bundles, "expected hash-receipt probe bundles"
    for manifest in probe_bundles:
        assert manifest["artifacts"] == []
        assert "HASH_RECEIPTS_ONLY" in manifest["claims"]
        observations = manifest["probe_observations"]
        assert observations
        serialized = json.dumps(observations)
        for fragment in SECRET_LIKE_FRAGMENTS:
            assert fragment not in serialized
        for observation in observations:
            assert "preview" not in observation
            if observation["status"] == 200:
                assert observation["response_sha256"]
                assert observation["response_bytes"]


def test_golden_bundle_probes_record_the_entitlement_boundary() -> None:
    gb4 = json.loads((BUNDLES_DIR / "gb4-alpaca-equity-observations" / "manifest.json").read_text())
    observations = {item["name"]: item for item in gb4["probe_observations"]}
    assert observations["historical_daily_bars_sip"]["status"] == 200
    assert observations["historical_sip_quotes_opening_window"]["status"] == 200
    assert observations["realtime_sip_snapshot_rejected"]["status"] == 403
    assert (
        observations["realtime_sip_snapshot_rejected"]["observed_limitation"]
        == "subscription does not permit querying recent SIP data"
    )
    gb5 = json.loads((BUNDLES_DIR / "gb5-alpaca-current-options" / "manifest.json").read_text())
    assert gb5["provenance_facts"]["observed_feed"] == "INDICATIVE"
    assert gb5["provenance_facts"]["competition_account_entitlement"] == "UNVERIFIED_PER_GATE_A"
