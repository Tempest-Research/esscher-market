import hashlib
import json
from pathlib import Path
from shutil import copyfile

import pytest

from ringdown_market.audit import (
    BundleDiffError,
    BundleDiffErrorReason,
    canonical_report_bytes,
    compare_artifacts,
    compare_paths,
    main,
    write_report,
)
from ringdown_market.cli import build_report
from ringdown_market.contracts.research_to_permit import (
    PAPER_PERMIT_POLICY_SHA256,
    PAPER_PERMIT_POLICY_VERSION,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    map_frozen_decision_to_permit,
)
from ringdown_market.execution.models import debit_vertical_permit_bytes

ROOT = Path(__file__).parents[1]
DATA = ROOT / "data" / "earnings-replays"
EVENT_LIST = DATA / "event-list-v1.json"
SELECTION_RULE = DATA / "selection-rule-v1.json"
EVIDENCE = DATA / "events" / "KR-2026Q2-EARNINGS.json"
SYNTHETIC_PANEL = ROOT / "tests" / "fixtures" / "synthetic_contract_panel.json"
DECISION_FIXTURE = ROOT / "tests" / "contract_fixtures" / "frozen_research_decision_v1.json"
EVALUATION_INPUT = SYNTHETIC_PANEL

SHIPPED_ARTIFACTS = (
    DATA / "event-list-v1.json",
    DATA / "selection-rule-v1.json",
    DATA / "events" / "GIS-2027Q1-EARNINGS.json",
    DATA / "events" / "KR-2026Q2-EARNINGS.json",
    DATA / "events" / "MU-2026Q4-EARNINGS.json",
    DATA / "events" / "NKE-2027Q1-EARNINGS.json",
    ROOT / "tests" / "contract_fixtures" / "frozen_research_decision_v1.json",
    ROOT / "tests" / "contract_fixtures" / "paper_demo_lifecycle_v1.json",
    ROOT / "tests" / "contract_fixtures" / "scheduled_manual_reconciliation_v1.json",
    ROOT / "tests" / "contract_fixtures" / "scheduled_rejected_before_mutation_v1.json",
    ROOT / "tests" / "contract_fixtures" / "scheduled_terminal_flat_v1.json",
    SYNTHETIC_PANEL,
    ROOT / "src" / "ringdown_market" / "demo" / "fixtures" / "KR-2026Q2-EARNINGS.json",
    ROOT
    / "src"
    / "ringdown_market"
    / "demo"
    / "fixtures"
    / "scheduled_manual_reconciliation_v1.json",
    ROOT
    / "src"
    / "ringdown_market"
    / "demo"
    / "fixtures"
    / "scheduled_rejected_before_mutation_v1.json",
    ROOT / "src" / "ringdown_market" / "demo" / "fixtures" / "scheduled_terminal_flat_v1.json",
)

BUNDLE_MEMBERS = (
    DATA / "event-list-v1.json",
    DATA / "selection-rule-v1.json",
    DATA / "events" / "GIS-2027Q1-EARNINGS.json",
    DATA / "events" / "KR-2026Q2-EARNINGS.json",
    DATA / "events" / "MU-2026Q4-EARNINGS.json",
    DATA / "events" / "NKE-2027Q1-EARNINGS.json",
)


def _json_bytes(payload: object, *, sort_keys: bool = False) -> bytes:
    return json.dumps(
        payload,
        sort_keys=sort_keys,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _mutated(raw: bytes, mutation) -> bytes:
    payload = json.loads(raw)
    mutation(payload)
    return _json_bytes(payload)


def _evaluation_bytes() -> bytes:
    return _json_bytes(build_report(EVALUATION_INPUT.read_bytes()))


def _permit_bytes() -> bytes:
    fixture = json.loads(DECISION_FIXTURE.read_text(encoding="utf-8"))
    decision = dict(fixture["decision_template"])
    decision.update(
        {
            "protocol_sha256": RESEARCH_DECISION_PROTOCOL_SHA256,
            "policy_version": PAPER_PERMIT_POLICY_VERSION,
            "policy_sha256": PAPER_PERMIT_POLICY_SHA256,
        }
    )
    evidence_bytes = _json_bytes(fixture["evidence_manifest"], sort_keys=True)
    input_bytes = _json_bytes(fixture["input_snapshot"], sort_keys=True)
    decision["evidence_manifest_sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
    decision["input_snapshot_sha256"] = hashlib.sha256(input_bytes).hexdigest()
    decision_bytes = _json_bytes(decision, sort_keys=True)
    permit = map_frozen_decision_to_permit(
        decision_bytes,
        evidence_manifest_bytes=evidence_bytes,
        input_snapshot_bytes=input_bytes,
        policy_version=PAPER_PERMIT_POLICY_VERSION,
    )
    return debit_vertical_permit_bytes(permit)


def _categories(report: dict[str, object]) -> set[str]:
    return {str(delta["category"]) for delta in report["deltas"]}


@pytest.mark.parametrize("path", SHIPPED_ARTIFACTS)
def test_every_shipped_json_artifact_is_a_supported_self_comparison(path: Path) -> None:
    report = compare_paths(path, path)

    assert report["identical"] is True
    assert report["deltas"] == []


def test_public_api_exports_are_importable() -> None:
    report = compare_artifacts(SYNTHETIC_PANEL.read_bytes(), SYNTHETIC_PANEL.read_bytes())

    assert report["schema"] == "ringdown.evidence_bundle_diff_report"
    assert report["schema_version"] == 1
    assert report["data_class"] == "OFFLINE_ARTIFACT_COMPARISON"
    assert report["claims"] == ["COMPARISON_ONLY", "NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION"]


def test_generated_report_is_a_supported_comparable_artifact() -> None:
    source_report = compare_artifacts(SYNTHETIC_PANEL.read_bytes(), SYNTHETIC_PANEL.read_bytes())
    rendered = canonical_report_bytes(source_report)

    report = compare_artifacts(rendered, rendered)

    assert report["identical"] is True


def test_tampered_report_identity_flag_fails_closed() -> None:
    source_report = compare_artifacts(SYNTHETIC_PANEL.read_bytes(), SYNTHETIC_PANEL.read_bytes())
    tampered = json.loads(canonical_report_bytes(source_report))
    tampered["identical"] = False
    rendered = canonical_report_bytes(tampered)

    with pytest.raises(BundleDiffError) as caught:
        compare_artifacts(rendered, rendered)

    assert caught.value.reason is BundleDiffErrorReason.INVALID_DOCUMENT


def test_formatting_only_change_reports_identity_but_remains_semantically_equal() -> None:
    raw = EVIDENCE.read_bytes()
    reordered = _json_bytes(json.loads(raw), sort_keys=True)

    report = compare_artifacts(raw, reordered)

    assert report["identical"] is False
    assert report["semantically_equal"] is True
    assert _categories(report) == {"IDENTITY"}
    assert report["left"]["canonical_sha256"] == report["right"]["canonical_sha256"]
    assert report["left"]["raw_sha256"] != report["right"]["raw_sha256"]


def test_set_like_list_order_is_semantically_equal() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(left, lambda payload: payload["limitations"].reverse())

    report = compare_artifacts(left, right)

    assert report["semantically_equal"] is True
    assert _categories(report) <= {"IDENTITY"}


def test_set_like_list_order_keeps_canonical_hash_stable() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(left, lambda payload: payload["limitations"].reverse())

    report = compare_artifacts(left, right)

    assert report["left"]["canonical_sha256"] == report["right"]["canonical_sha256"]
    assert report["left"]["raw_sha256"] != report["right"]["raw_sha256"]


@pytest.mark.parametrize(
    ("category", "mutation"),
    [
        ("HASH", lambda payload: payload.update({"input_sha256": "0" * 64})),
        ("HASH", lambda payload: payload.update({"protocol_sha256": "f" * 64})),
        (
            "LATENCY",
            lambda payload: payload["latency_profiles"]["p95"].update(
                {"requested_latency_ms": 45000}
            ),
        ),
        (
            "VERDICT",
            lambda payload: payload["latency_profiles"]["p95"]["qfast"].update(
                {"candidate_advantage": 1.25}
            ),
        ),
    ],
)
def test_evaluation_report_delta_categories(category: str, mutation) -> None:
    left = _evaluation_bytes()
    right = _mutated(left, mutation)

    report = compare_artifacts(left, right)

    assert category in _categories(report)


def test_manifest_label_classification_and_claim_deltas() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(
        left,
        lambda payload: (
            payload["data_qualifiers"].append("COMPARISON_ONLY"),
            payload["limitations"].append("NO_RESCORING"),
            payload.update({"data_class": "SYNTHETIC_CONTRACT_FIXTURE"}),
        ),
    )

    report = compare_artifacts(left, right)

    assert {"CLAIM", "LIMITATION", "CLASSIFICATION"} <= _categories(report)


def test_schema_version_delta_is_reported_for_a_registered_version() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(left, lambda payload: payload.update({"schema_version": 1}))

    report = compare_artifacts(left, right)

    assert any(
        delta["category"] == "SCHEMA" and delta["path"].endswith("/schema_version")
        for delta in report["deltas"]
    )


def test_event_id_addition_and_removal_are_keyed_by_id() -> None:
    left = EVENT_LIST.read_bytes()

    def replace_event(payload: dict[str, object]) -> None:
        event_ids = payload["event_ids"]
        events = payload["events"]
        assert isinstance(event_ids, list)
        assert isinstance(events, list)
        event_ids[0] = "NEW-EVENT-ID"
        assert isinstance(events[0], dict)
        events[0]["event_id"] = "NEW-EVENT-ID"

    report = compare_artifacts(left, _mutated(left, replace_event))

    assert "EVENT_ID" in _categories(report)
    assert any(delta["change"] == "ADDED" for delta in report["deltas"])
    assert any(delta["change"] == "REMOVED" for delta in report["deltas"])


def test_event_order_change_is_visible_without_positional_record_noise() -> None:
    left = EVENT_LIST.read_bytes()

    def reverse_event_order(payload: dict[str, object]) -> None:
        assert isinstance(payload["event_ids"], list)
        assert isinstance(payload["events"], list)
        payload["event_ids"].reverse()
        payload["events"].reverse()

    report = compare_artifacts(left, _mutated(left, reverse_event_order))

    assert report["identical"] is False
    assert report["semantically_equal"] is False
    assert _categories(report) == {"EVENT_ID", "IDENTITY"}
    assert any(delta["path"].endswith("/event_ids") for delta in report["deltas"])


def test_event_context_change_is_not_misreported_as_event_id_change() -> None:
    left = EVENT_LIST.read_bytes()

    right = _mutated(
        left,
        lambda payload: payload["events"][0].update(
            {"inclusion_or_exclusion_reason": "UPDATED_PROTOCOL_REASON"}
        ),
    )
    report = compare_artifacts(left, right)

    assert "INCLUSION" in _categories(report)
    assert "EVENT_ID" not in _categories(report)


def test_publication_precision_and_source_metadata_have_specific_categories() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(
        left,
        lambda payload: payload["records"][0].update(
            {
                "published_at_precision": "MINUTE",
                "source_url": "https://example.com/revised-source",
            }
        ),
    )

    report = compare_artifacts(left, right)

    assert "TIMING" in _categories(report)
    assert "PROVENANCE" in _categories(report)


def test_records_are_keyed_by_evidence_id() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(
        left,
        lambda payload: payload["records"][0].update({"publisher": "Revised Publisher"}),
    )

    report = compare_artifacts(left, right)

    assert any("/records/record:" in delta["path"] for delta in report["deltas"])


def test_permit_policy_and_protocol_hash_changes_are_hash_deltas() -> None:
    left = _permit_bytes()
    right = _mutated(
        left,
        lambda payload: (
            payload.update({"policy_sha256": "0" * 64}),
            payload.update({"protocol_sha256": "f" * 64}),
        ),
    )

    report = compare_artifacts(left, right)

    hash_paths = {delta["path"] for delta in report["deltas"] if delta["category"] == "HASH"}
    assert "/policy_sha256" in hash_paths
    assert "/protocol_sha256" in hash_paths
    assert report["left"]["artifacts"][0]["schema"] == "ringdown.paper_execution_permit"


def test_selection_rule_and_event_list_hash_changes_are_hash_deltas() -> None:
    list_left = EVENT_LIST.read_bytes()
    list_right = _mutated(
        list_left, lambda payload: payload.update({"selection_rule_sha256": "0" * 64})
    )

    list_report = compare_artifacts(list_left, list_right)

    assert any(
        delta["category"] == "HASH" and delta["path"].endswith("/selection_rule_sha256")
        for delta in list_report["deltas"]
    )

    manifest_left = EVIDENCE.read_bytes()
    manifest_right = _mutated(
        manifest_left, lambda payload: payload.update({"event_list_sha256": "f" * 64})
    )

    manifest_report = compare_artifacts(manifest_left, manifest_right)

    assert any(
        delta["category"] == "HASH" and delta["path"].endswith("/event_list_sha256")
        for delta in manifest_report["deltas"]
    )


def test_scalar_claim_and_feature_source_reference_changes_are_classified() -> None:
    left = DECISION_FIXTURE.read_bytes()
    right = _mutated(
        left,
        lambda payload: (
            payload["decision_template"].update({"claim": "COMPARISON_ONLY"}),
            payload["evidence_manifest"]["field_source_refs"]["candidate_signal"].append(
                "additional-source"
            ),
        ),
    )

    report = compare_artifacts(left, right)

    assert "CLAIM" in _categories(report)
    assert "PROVENANCE" in _categories(report)


def test_event_schedule_fields_are_timing_deltas() -> None:
    left = EVENT_LIST.read_bytes()
    right = _mutated(
        left,
        lambda payload: payload["events"][0].update({"timing_bucket": "AFTER_CLOSE"}),
    )

    report = compare_artifacts(left, right)

    assert any(
        delta["category"] == "TIMING" and delta["path"].endswith("/timing_bucket")
        for delta in report["deltas"]
    )


def test_boolean_and_number_are_not_python_equal_json_values() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(
        left,
        lambda payload: payload["records"][0].update({"content_sha256": True}),
    )

    report = compare_artifacts(left, right)

    assert report["identical"] is False
    assert any(delta["path"].endswith("/content_sha256") for delta in report["deltas"])


def test_generic_field_changes_are_reported_without_claiming_semantics() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(left, lambda payload: payload.update({"issuer": "OTHER ISSUER"}))

    report = compare_artifacts(left, right)

    assert any(
        delta["category"] == "FIELD" and delta["path"].endswith("/issuer")
        for delta in report["deltas"]
    )


def test_existing_null_and_missing_values_are_distinguished() -> None:
    left = EVIDENCE.read_bytes()
    right = _mutated(left, lambda payload: payload["sec_filing"].pop("url"))

    report = compare_artifacts(left, right)
    delta = next(delta for delta in report["deltas"] if delta["path"].endswith("/sec_filing/url"))

    assert delta["left_present"] is True
    assert delta["right_present"] is False
    assert delta["left"] is None


def test_complete_replay_bundle_is_contract_validated() -> None:
    report = compare_paths(DATA, DATA)

    assert report["identical"] is True
    assert report["left"]["validation_status"] == "CONTRACT_VALIDATED"
    assert report["right"]["validation_status"] == "CONTRACT_VALIDATED"
    assert len(report["left"]["artifacts"]) == len(BUNDLE_MEMBERS)
    assert all(
        artifact["validation_status"] == "CONTRACT_VALIDATED"
        for artifact in report["left"]["artifacts"]
    )


def test_invalid_complete_replay_bundle_fails_contract_validation(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "events").mkdir(parents=True)
    for member in BUNDLE_MEMBERS:
        copyfile(member, bundle / member.relative_to(DATA))
    tampered = _mutated(
        (bundle / "events" / "KR-2026Q2-EARNINGS.json").read_bytes(),
        lambda payload: payload["event_context"].update({"ticker": "TAMPERED"}),
    )
    (bundle / "events" / "KR-2026Q2-EARNINGS.json").write_bytes(tampered)

    with pytest.raises(BundleDiffError) as caught:
        compare_paths(bundle, bundle)

    assert caught.value.reason is BundleDiffErrorReason.CONTRACT_VALIDATION_FAILED


def test_standalone_artifact_discloses_recognition_status() -> None:
    report = compare_paths(EVIDENCE, EVIDENCE)

    assert report["left"]["validation_status"] == "STRICT_JSON_SCHEMA_RECOGNIZED"
    assert report["left"]["artifacts"][0]["validation_status"] == "STRICT_JSON_SCHEMA_RECOGNIZED"


def test_qfast_report_is_contract_validated() -> None:
    rendered = _evaluation_bytes()

    report = compare_artifacts(rendered, rendered)

    assert report["left"]["validation_status"] == "CONTRACT_VALIDATED"


def test_tampered_qfast_gate_fails_closed() -> None:
    tampered = _mutated(
        _evaluation_bytes(),
        lambda payload: payload["latency_gate"].update({"status": "SHADOW_ONLY"}),
    )

    with pytest.raises(BundleDiffError) as caught:
        compare_artifacts(tampered, tampered)

    assert caught.value.reason is BundleDiffErrorReason.INVALID_DOCUMENT


def test_renamed_bundle_member_is_matched_by_identity(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "before.json").write_bytes(EVIDENCE.read_bytes())
    (right / "after.json").write_bytes(EVIDENCE.read_bytes())

    report = compare_paths(left, right)

    assert report["identical"] is False
    assert report["semantically_equal"] is True
    assert _categories(report) == {"ARTIFACT"}
    delta = report["deltas"][0]
    assert delta["change"] == "RENAMED"
    assert delta["left"] == "before.json"
    assert delta["right"] == "after.json"


def test_bundle_member_order_is_independent_of_creation_order(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    files = (
        ("b.json", EVIDENCE),
        ("a.json", EVENT_LIST),
    )
    for name, source in files:
        (left / name).write_bytes(source.read_bytes())
    for name, source in reversed(files):
        (right / name).write_bytes(source.read_bytes())

    report = compare_paths(left, right)

    assert report["identical"] is True
    assert [item["path"] for item in report["left"]["artifacts"]] == ["a.json", "b.json"]


def test_bundle_added_and_removed_files_are_reported(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "event.json").write_bytes(EVENT_LIST.read_bytes())
    (right / "event.json").write_bytes(EVENT_LIST.read_bytes())
    (right / "evidence.json").write_bytes(EVIDENCE.read_bytes())

    report = compare_paths(left, right)

    assert any(
        delta["category"] == "FILE"
        and delta["change"] == "ADDED"
        and delta["path"].endswith("/evidence.json")
        for delta in report["deltas"]
    )


def test_bundle_event_id_membership_changes_are_reported(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "event.json").write_bytes(EVIDENCE.read_bytes())
    (right / "event.json").write_bytes(
        _mutated(
            EVIDENCE.read_bytes(),
            lambda payload: payload.update({"event_id": "new-event-id"}),
        )
    )

    report = compare_paths(left, right)

    assert any(
        delta["category"] == "EVENT_ID"
        and delta["change"] == "ADDED"
        and delta["path"].endswith("/event:new-event-id")
        for delta in report["deltas"]
    )


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (
            b'{"schema":"ringdown.frozen_earnings_event_list","schema":1}',
            BundleDiffErrorReason.DUPLICATE_KEY,
        ),
        (
            b'{"schema":"ringdown.frozen_earnings_event_list","schema_version":NaN}',
            BundleDiffErrorReason.NON_FINITE_NUMBER,
        ),
        (b"[]", BundleDiffErrorReason.UNEXPECTED_TOP_LEVEL_SHAPE),
        (
            b'{"schema":"ringdown.unknown","schema_version":1}',
            BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
        ),
    ],
)
def test_untrusted_json_fails_closed(raw: bytes, reason: BundleDiffErrorReason) -> None:
    with pytest.raises(BundleDiffError) as caught:
        compare_artifacts(raw, raw)

    assert caught.value.reason is reason


def test_unsupported_schema_version_fails_closed() -> None:
    raw = _mutated(
        EVIDENCE.read_bytes(),
        lambda payload: payload.update({"schema_version": 99}),
    )

    with pytest.raises(BundleDiffError) as caught:
        compare_artifacts(raw, raw)

    assert caught.value.reason is BundleDiffErrorReason.UNSUPPORTED_SCHEMA_VERSION


def test_malformed_label_field_fails_with_stable_error() -> None:
    raw = _mutated(_evaluation_bytes(), lambda payload: payload.update({"claims": "not-a-list"}))

    with pytest.raises(BundleDiffError) as caught:
        compare_artifacts(raw, raw)

    assert caught.value.reason is BundleDiffErrorReason.INVALID_DOCUMENT


def test_mixed_artifact_and_bundle_inputs_fail_closed(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "event.json").write_bytes(EVENT_LIST.read_bytes())

    with pytest.raises(BundleDiffError) as caught:
        compare_paths(EVENT_LIST, bundle)

    assert caught.value.reason is BundleDiffErrorReason.INPUT_KIND_MISMATCH


def test_directory_path_traversal_fails_closed(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "event.json").write_bytes(EVENT_LIST.read_bytes())

    with pytest.raises(BundleDiffError) as caught:
        compare_paths(bundle / ".." / "bundle", bundle)

    assert caught.value.reason is BundleDiffErrorReason.PATH_TRAVERSAL


def test_non_json_bundle_member_fails_closed(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "event.json").write_bytes(EVENT_LIST.read_bytes())
    (bundle / "notes.txt").write_text("not an artifact", encoding="utf-8")

    with pytest.raises(BundleDiffError) as caught:
        compare_paths(bundle, bundle)

    assert caught.value.reason is BundleDiffErrorReason.UNSUPPORTED_FILE


def test_symlink_bundle_member_fails_closed(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    outside = tmp_path / "outside.json"
    bundle.mkdir()
    outside.write_bytes(EVENT_LIST.read_bytes())
    link = bundle / "event.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(BundleDiffError) as caught:
        compare_paths(bundle, bundle)

    assert caught.value.reason is BundleDiffErrorReason.SYMLINK_NOT_ALLOWED


def test_write_report_requires_an_existing_parent_and_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_report(SYNTHETIC_PANEL, SYNTHETIC_PANEL, first)
    write_report(SYNTHETIC_PANEL, SYNTHETIC_PANEL, second)

    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8"))["identical"] is True


def test_write_report_rejects_output_directory(tmp_path: Path) -> None:
    with pytest.raises(BundleDiffError) as caught:
        write_report(SYNTHETIC_PANEL, SYNTHETIC_PANEL, tmp_path)

    assert caught.value.reason is BundleDiffErrorReason.OUTPUT_ERROR


def test_module_cli_writes_report_and_normalizes_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "report.json"

    assert main([str(SYNTHETIC_PANEL), str(SYNTHETIC_PANEL), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["identical"] is True

    bad = tmp_path / "bad.json"
    bad.write_bytes(b"[]")
    assert main([str(bad), str(bad)]) == 2
    assert "UNEXPECTED_TOP_LEVEL_SHAPE" in capsys.readouterr().err


def test_input_symlink_fails_closed(tmp_path: Path) -> None:
    link = tmp_path / "panel.json"
    try:
        link.symlink_to(SYNTHETIC_PANEL)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(BundleDiffError) as caught:
        compare_paths(link, link)

    assert caught.value.reason is BundleDiffErrorReason.SYMLINK_NOT_ALLOWED
