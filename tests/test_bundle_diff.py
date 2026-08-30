from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ringdown_market.audit.bundle_diff import (
    BundleDiffError,
    BundleDiffReport,
    compare_bundle_bytes,
    compare_bundle_paths,
    write_diff_report,
)
from ringdown_market.cli import build_report


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _manifest(event_id: str = "EVENT-1") -> dict[str, object]:
    return {
        "schema": "ringdown.point_in_time_evidence_manifest",
        "schema_version": 2,
        "event_id": event_id,
        "data_class": "POINT_IN_TIME_EVENT_PANEL",
        "data_qualifiers": ["INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE"],
        "event_list_sha256": "a" * 64,
        "selection_rule_sha256": "b" * 64,
        "event_context": {
            "inclusion_or_exclusion_reason": "INCLUDED: frozen before outcome inspection."
        },
        "records": [
            {
                "evidence_id": "issuer-release",
                "source_url": "https://example.invalid/release",
                "publisher": "Example Issuer",
                "published_at_precision": "SECOND",
                "content_sha256": "c" * 64,
            }
        ],
        "feature_dependencies": [
            {
                "feature_id": "event_schedule",
                "source_refs": ["issuer-release"],
                "dependency_check": "ELIGIBLE",
            }
        ],
        "limitations": ["NO_BROKER_EXECUTION"],
    }


def _event_list(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "ringdown.frozen_earnings_event_list",
        "schema_version": 1,
        "list_id": "LIST-1",
        "event_ids": [event["event_id"] for event in events],
        "events": events,
    }


def _qfast_report() -> dict[str, object]:
    metrics = {
        method: {
            "eligible_events": 1,
            "admitted_events": 1,
            "coverage": 1.0,
            "mean_all": 0.0,
            "median_all": 0.0,
            "mean_admitted": 0.0,
            "median_admitted": 0.0,
        }
        for method in ("ringdown", "ALWAYS_ABSTAIN", "PRICE_ONLY")
    }
    return {
        "schema_version": 1,
        "project": "Ring" + "down",
        "product_name": "Esscher",
        "mode": "OFFLINE_RESEARCH",
        "data_class": "POINT_IN_TIME_EVENT_PANEL",
        "claims": ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"],
        "limitations": ["INDICATIVE_DATA"],
        "input_sha256": "d" * 64,
        "protocol_sha256": "e" * 64,
        "event_count": 1,
        "latency_profiles": {
            "p95": {
                "requested_latency_ms": 30_000,
                "actual_latency_ms": {"minimum": 30_000, "maximum": 30_000},
                "qfast": {
                    "status": "NOT_REJECTED_SMALL_SAMPLE",
                    "claim": "NOT_ALPHA_EVIDENCE",
                    "event_count": 1,
                    "metrics": metrics,
                    "strongest_baseline": "ALWAYS_ABSTAIN",
                    "candidate_advantage": 0.0,
                    "leave_best_out_mean": 0.0,
                    "reject_reasons": [],
                },
            }
        },
        "latency_gate": {
            "status": "NOT_REJECTED_SMALL_SAMPLE",
            "required_profile": "p95",
            "qfast_status": "NOT_REJECTED_SMALL_SAMPLE",
        },
    }


def _deltas_by_pointer(report: BundleDiffReport) -> dict[str, dict[str, object]]:
    payload = report.to_dict()
    return {delta["json_pointer"]: delta for delta in payload["deltas"]}


def test_identical_artifacts_produce_a_byte_stable_empty_report() -> None:
    raw = _json_bytes(_manifest())

    first = compare_bundle_bytes(raw, raw)
    second = compare_bundle_bytes(raw, raw)

    assert first.to_json_bytes() == second.to_json_bytes()
    assert first.changed is False
    assert first.to_dict()["summary"] == {
        "changed": False,
        "delta_count": 0,
        "artifacts_added": [],
        "artifacts_removed": [],
    }
    assert first.to_json_bytes().endswith(b"\n")
    assert first.before["raw_sha256"] == first.after["raw_sha256"]
    assert first.before["canonical_sha256"] == first.after["canonical_sha256"]


def test_formatting_only_change_reports_raw_identity_but_not_semantic_delta() -> None:
    payload = _manifest()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")

    report = compare_bundle_bytes(compact, pretty)

    assert report.changed is True
    assert report.before["raw_sha256"] != report.after["raw_sha256"]
    assert report.before["canonical_sha256"] == report.after["canonical_sha256"]
    assert report.deltas == (
        {
            "artifact": "before.json -> after.json",
            "aspect": "RAW_BYTES_SHA256",
            "category": "IDENTITY",
            "change": "CHANGED",
            "json_pointer": "",
            "before": report.before["raw_sha256"],
            "before_present": True,
            "after": report.after["raw_sha256"],
            "after_present": True,
        },
    )


def test_manifest_reports_schema_classification_identity_provenance_event_and_claim_deltas() -> (
    None
):
    before = _manifest()
    before["policy_sha256"] = "1" * 64
    after = _manifest()
    after["schema_version"] = 1
    after["data_class"] = "SYNTHETIC_CONTRACT_FIXTURE"
    after["event_list_sha256"] = "f" * 64
    after["selection_rule_sha256"] = "8" * 64
    after["policy_sha256"] = "2" * 64
    after["data_qualifiers"] = ["INDICATIVE_DATA", "NO_OUTCOME_VALUES"]
    after["limitations"] = ["NO_BROKER_EXECUTION", "NOT_HISTORICAL_DATA"]
    after_context = after["event_context"]
    assert isinstance(after_context, dict)
    after_context["inclusion_or_exclusion_reason"] = "EXCLUDED: timestamp unresolved."
    after_records = after["records"]
    assert isinstance(after_records, list)
    assert isinstance(after_records[0], dict)
    after_records[0]["published_at_precision"] = "DATE_INTERVAL"

    deltas = _deltas_by_pointer(compare_bundle_bytes(_json_bytes(before), _json_bytes(after)))

    assert deltas["/schema_version"]["category"] == "SCHEMA"
    assert deltas["/data_class"]["category"] == "DATA_CLASSIFICATION"
    assert deltas["/event_list_sha256"]["category"] == "IDENTITY"
    assert deltas["/selection_rule_sha256"]["category"] == "IDENTITY"
    assert deltas["/policy_sha256"]["category"] == "IDENTITY"
    assert deltas["/data_qualifiers"]["category"] == "CLAIM"
    assert deltas["/limitations"]["category"] == "CLAIM"
    assert deltas["/event_context/inclusion_or_exclusion_reason"]["category"] == "EVENT"
    assert deltas["/records/issuer-release/published_at_precision"]["category"] == "PROVENANCE"


def test_keyed_events_are_reported_as_added_removed_or_changed() -> None:
    before = _event_list(
        [
            {"event_id": "A", "inclusion_or_exclusion_reason": "INCLUDED"},
            {"event_id": "B", "inclusion_or_exclusion_reason": "INCLUDED"},
        ]
    )
    after = _event_list(
        [
            {"event_id": "B", "inclusion_or_exclusion_reason": "EXCLUDED"},
            {"event_id": "C", "inclusion_or_exclusion_reason": "INCLUDED"},
        ]
    )

    deltas = _deltas_by_pointer(compare_bundle_bytes(_json_bytes(before), _json_bytes(after)))

    assert deltas["/events/A"]["change"] == "REMOVED"
    assert deltas["/events/B/inclusion_or_exclusion_reason"]["change"] == "CHANGED"
    assert deltas["/events/C"]["change"] == "ADDED"
    assert all(
        delta["category"] == "EVENT"
        for pointer, delta in deltas.items()
        if pointer.startswith("/events")
    )


def test_untrusted_event_identifier_is_escaped_as_json_pointer_data() -> None:
    before = _event_list([])
    after = _event_list([{"event_id": "HOSTILE/~VALUE", "issuer": "<script>"}])

    deltas = _deltas_by_pointer(compare_bundle_bytes(_json_bytes(before), _json_bytes(after)))

    event_delta = deltas["/events/HOSTILE~1~0VALUE"]
    assert event_delta["category"] == "EVENT"
    assert event_delta["after"] == {"event_id": "HOSTILE/~VALUE", "issuer": "<script>"}


def test_qfast_latency_candidate_and_baseline_changes_are_distinguished() -> None:
    before = _qfast_report()
    after = _qfast_report()
    after_gate = after["latency_gate"]
    assert isinstance(after_gate, dict)
    after_gate["status"] = "SHADOW_ONLY"
    after_gate["qfast_status"] = "REJECTED"
    after["protocol_sha256"] = "7" * 64
    after_profiles = after["latency_profiles"]
    assert isinstance(after_profiles, dict)
    after_p95 = after_profiles["p95"]
    assert isinstance(after_p95, dict)
    after_p95["requested_latency_ms"] = 45_000
    after_qfast = after_p95["qfast"]
    assert isinstance(after_qfast, dict)
    after_qfast["status"] = "REJECTED"
    after_qfast["strongest_baseline"] = "PRICE_ONLY"

    deltas = _deltas_by_pointer(compare_bundle_bytes(_json_bytes(before), _json_bytes(after)))

    assert deltas["/latency_gate/status"]["category"] == "LATENCY"
    assert deltas["/latency_profiles/p95/requested_latency_ms"]["category"] == "LATENCY"
    assert deltas["/protocol_sha256"]["category"] == "IDENTITY"
    assert deltas["/latency_profiles/p95/qfast/status"]["category"] == "VERDICT"
    assert deltas["/latency_profiles/p95/qfast/strongest_baseline"]["category"] == "VERDICT"


def test_qfast_report_is_strictly_contract_validated() -> None:
    report = compare_bundle_bytes(_json_bytes(_qfast_report()), _json_bytes(_qfast_report()))

    assert report.before["validation_status"] == "CONTRACT_VALIDATED"
    assert report.after["validation_status"] == "CONTRACT_VALIDATED"

    invalid = _qfast_report()
    invalid["data_class"] = "LIVE"
    with pytest.raises(BundleDiffError, match="Q-FAST data_class is unsupported"):
        compare_bundle_bytes(_json_bytes(invalid), _json_bytes(_qfast_report()))


def test_production_qfast_report_shape_passes_strict_validation() -> None:
    fixture = Path(__file__).parent / "fixtures" / "synthetic_contract_panel.json"
    report_bytes = _json_bytes(build_report(fixture.read_bytes()))

    report = compare_bundle_bytes(report_bytes, report_bytes)

    assert report.changed is False
    assert report.before["validation_status"] == "CONTRACT_VALIDATED"


def test_single_evidence_artifact_discloses_partial_validation_boundary() -> None:
    invalid_but_recognized = _manifest()
    invalid_but_recognized["data_class"] = "LIVE"
    invalid_but_recognized["records"] = "not-a-list"

    report = compare_bundle_bytes(
        _json_bytes(invalid_but_recognized),
        _json_bytes(invalid_but_recognized),
    )

    assert report.before["validation_status"] == "STRICT_JSON_SCHEMA_RECOGNIZED"
    assert report.after["validation_status"] == "STRICT_JSON_SCHEMA_RECOGNIZED"


def test_set_like_claim_order_creates_only_a_raw_byte_identity_delta() -> None:
    before = _manifest()
    after = _manifest()
    qualifiers = before["data_qualifiers"]
    assert isinstance(qualifiers, list)
    after["data_qualifiers"] = list(reversed(qualifiers))

    report = compare_bundle_bytes(_json_bytes(before), _json_bytes(after))

    assert report.changed is True
    assert len(report.deltas) == 1
    assert report.deltas[0]["aspect"] == "RAW_BYTES_SHA256"


def test_supported_schema_name_change_is_explicit() -> None:
    before = _manifest()
    after = _manifest()
    after["schema"] = "ringdown.frozen_earnings_event_list"
    after["schema_version"] = 1
    after["list_id"] = "LIST-1"

    deltas = _deltas_by_pointer(compare_bundle_bytes(_json_bytes(before), _json_bytes(after)))

    assert deltas["/schema"] == {
        "artifact": "before.json -> after.json",
        "category": "SCHEMA",
        "change": "CHANGED",
        "json_pointer": "/schema",
        "before_present": True,
        "after_present": True,
        "before": "ringdown.point_in_time_evidence_manifest",
        "after": "ringdown.frozen_earnings_event_list",
    }


def test_directory_comparison_is_independent_of_creation_order(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    artifacts = {
        "events/EVENT-1.json": _manifest(),
        "event-list.json": _event_list([{"event_id": "EVENT-1"}]),
    }
    for root, names in ((before, reversed(tuple(artifacts))), (after, artifacts)):
        for name in names:
            target = root / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(_json_bytes(artifacts[name]))

    report = compare_bundle_paths(before, after)

    assert report.changed is False
    assert report.before["raw_sha256"] == report.after["raw_sha256"]
    assert report.before["canonical_sha256"] == report.after["canonical_sha256"]


def test_complete_repository_replay_bundle_uses_existing_contract_validator() -> None:
    replay_bundle = Path(__file__).parents[1] / "data" / "earnings-replays"

    report = compare_bundle_paths(replay_bundle, replay_bundle)

    assert report.changed is False
    assert report.before["validation_status"] == "CONTRACT_VALIDATED"
    assert report.after["validation_status"] == "CONTRACT_VALIDATED"


def test_invalid_complete_replay_bundle_fails_existing_contract_validation(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "data" / "earnings-replays"
    invalid = tmp_path / "invalid-replay"
    shutil.copytree(source, invalid)
    manifest_path = invalid / "events" / "KR-2026Q2-EARNINGS.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["data_class"] = "LIVE"
    manifest_path.write_bytes(_json_bytes(manifest))

    with pytest.raises(BundleDiffError, match="failed replay contract validation"):
        compare_bundle_paths(invalid, source)


def test_directory_reports_added_removed_artifacts_and_matches_a_renamed_identity(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "old-name.json").write_bytes(_json_bytes(_manifest()))
    (before / "removed.json").write_bytes(
        _json_bytes(
            {
                "schema": "ringdown.earnings_replay_selection_rule",
                "schema_version": 1,
                "rule_id": "RULE-OLD",
            }
        )
    )
    changed = _manifest()
    changed["event_list_sha256"] = "9" * 64
    (after / "new-name.json").write_bytes(_json_bytes(changed))
    (after / "added.json").write_bytes(
        _json_bytes(
            {
                "schema": "ringdown.earnings_replay_selection_rule",
                "schema_version": 1,
                "rule_id": "RULE-NEW",
            }
        )
    )

    report = compare_bundle_paths(before, after)

    assert report.artifacts_removed == ("removed.json",)
    assert report.artifacts_added == ("added.json",)
    assert any(
        delta["json_pointer"] == "/event_list_sha256"
        and delta["artifact"] == "old-name.json -> new-name.json"
        for delta in report.deltas
    )
    assert any(delta["change"] == "RENAMED" for delta in report.deltas)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"[]", "root must be an object"),
        (b'{"schema_version":1,"schema_version":1}', "duplicate JSON field"),
        (b'{"value":NaN}', "non-finite JSON number"),
        (b'{"schema":"unknown","schema_version":1}', "unsupported artifact schema"),
        (
            b'{"schema":"ringdown.point_in_time_evidence_manifest",'
            b'"schema_version":99,"event_id":"A"}',
            "unsupported ringdown.point_in_time_evidence_manifest schema version",
        ),
        (b'{"schema":"ringdown.point_in_time_evidence_manifest","schema_version":2}', "event_id"),
    ],
)
def test_malformed_or_unknown_artifacts_fail_closed(raw: bytes, message: str) -> None:
    with pytest.raises(BundleDiffError, match=message):
        compare_bundle_bytes(raw, _json_bytes(_manifest()))


def test_input_parent_traversal_is_rejected(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(_json_bytes(_manifest()))

    with pytest.raises(BundleDiffError, match="parent traversal"):
        compare_bundle_paths(bundle / ".." / "outside.json", outside)


def test_comparison_is_read_only_and_writer_uses_only_explicit_output(tmp_path: Path) -> None:
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_bytes(_json_bytes(_manifest()))
    changed = _manifest()
    changed["limitations"] = ["NO_BROKER_EXECUTION", "NEW_LIMITATION"]
    after.write_bytes(_json_bytes(changed))
    original_files = sorted(path.name for path in tmp_path.iterdir())

    report = compare_bundle_paths(before, after)

    assert sorted(path.name for path in tmp_path.iterdir()) == original_files
    output = tmp_path / "diff.json"
    write_diff_report(report, output)
    assert output.read_bytes() == report.to_json_bytes()
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "after.json",
        "before.json",
        "diff.json",
    ]


def test_output_parent_traversal_and_missing_parent_fail_closed(tmp_path: Path) -> None:
    raw = _json_bytes(_manifest())
    report = compare_bundle_bytes(raw, raw)

    with pytest.raises(BundleDiffError, match="parent traversal"):
        write_diff_report(report, tmp_path / "child" / ".." / "diff.json")
    with pytest.raises(BundleDiffError, match="parent directory does not exist"):
        write_diff_report(report, tmp_path / "missing" / "diff.json")
