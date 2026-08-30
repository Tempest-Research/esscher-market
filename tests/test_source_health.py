"""Tests for the offline source-provenance health checker."""

from __future__ import annotations

import hashlib
import json
import os
import socket
from collections.abc import Callable
from pathlib import Path

import pytest

from ringdown_market.audit import source_health
from ringdown_market.audit.source_health import (
    FindingSeverity,
    SourceHealthCode,
    SourceHealthReport,
    SourceHealthStatus,
    canonical_report_bytes,
    check_manifest,
    check_path,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "earnings-replays"
EVENTS = DATA / "events"
EVENT_LIST = DATA / "event-list-v1.json"
SELECTION_RULE = DATA / "selection-rule-v1.json"
FROZEN_MANIFESTS = tuple(sorted(EVENTS.glob("*.json")))


def _load_manifest() -> dict[str, object]:
    return json.loads((EVENTS / "KR-2026Q2-EARNINGS.json").read_bytes())


def _manifest_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _mutated(mutate: Callable[[dict[str, object]], None]) -> bytes:
    payload = _load_manifest()
    mutate(payload)
    return _manifest_bytes(payload)


def _codes(report: SourceHealthReport) -> list[tuple[str, str]]:
    return [(finding.code.value, finding.pointer) for finding in report.findings]


def test_public_api_exports_are_importable() -> None:
    for name in (
        "Finding",
        "FindingSeverity",
        "SourceHealthCode",
        "SourceHealthReport",
        "SourceHealthStatus",
        "canonical_report_bytes",
        "check_manifest",
        "check_path",
    ):
        assert getattr(source_health, name) is not None


@pytest.mark.parametrize("path", FROZEN_MANIFESTS, ids=lambda p: p.name)
def test_clean_frozen_manifest_is_healthy(path: Path) -> None:
    raw = path.read_bytes()

    report = check_manifest(raw)

    assert report.status is SourceHealthStatus.HEALTHY
    assert report.findings == ()
    assert report.manifest_sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("path", FROZEN_MANIFESTS, ids=lambda p: p.name)
def test_clean_frozen_manifest_with_context_is_healthy(path: Path) -> None:
    report = check_manifest(
        path.read_bytes(),
        event_list=EVENT_LIST.read_bytes(),
        selection_rule=SELECTION_RULE.read_bytes(),
    )

    assert report.status is SourceHealthStatus.HEALTHY
    assert report.findings == ()


def test_identical_manifest_bytes_produce_identical_canonical_findings() -> None:
    raw = (EVENTS / "KR-2026Q2-EARNINGS.json").read_bytes()

    first = canonical_report_bytes(check_manifest(raw))
    second = canonical_report_bytes(check_manifest(bytes(raw)))

    assert first == second


def test_findings_are_deterministic_for_mutated_input() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][0]["publisher"] = ""
        payload["records"][0]["source_url"] = "http://example.com/release"
        payload["records"][0]["content_sha256"] = "not-a-hash"

    raw = _mutated(mutate)

    assert _codes(check_manifest(raw)) == _codes(check_manifest(raw))
    assert canonical_report_bytes(check_manifest(raw)) == canonical_report_bytes(
        check_manifest(raw)
    )


def test_finding_order_is_pinned_to_the_check_sequence() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][0]["publisher"] = ""
        payload["records"][0]["source_url"] = "http://example.com/release"
        payload["records"][0]["content_sha256"] = "not-a-hash"
        payload["feature_dependencies"][0]["dependency_check"] = "PENDING"

    assert _codes(check_manifest(_mutated(mutate))) == [
        (SourceHealthCode.URL_NOT_HTTPS.value, "/records/0/source_url"),
        (SourceHealthCode.PUBLISHER_MISSING.value, "/records/0/publisher"),
        (SourceHealthCode.REVISION_IDENTITY_MISSING.value, "/records/0/content_sha256"),
        (
            SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN.value,
            "/feature_dependencies/0/dependency_check",
        ),
    ]


def _unescape_pointer_part(part: str) -> str:
    return part.replace("~1", "/").replace("~0", "~")


def _resolve_parent(document: object, pointer: str) -> tuple[object, str]:
    parts = [_unescape_pointer_part(part) for part in pointer.split("/")[1:]]
    node = document
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node, parts[-1]


_CODES_POINTING_AT_ABSENT_FIELDS = {
    SourceHealthCode.CLASSIFICATION_MISSING,
    SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN,
    SourceHealthCode.FIELD_MISSING,
    SourceHealthCode.PROVENANCE_MAPPING_INVALID,
    SourceHealthCode.PUBLISHER_MISSING,
    SourceHealthCode.RETRIEVAL_TIME_MISSING,
    SourceHealthCode.REVISION_IDENTITY_MISSING,
    SourceHealthCode.URL_MISSING,
}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["records"][0].pop("source_url"),
        lambda p: p["records"][0].pop("publisher"),
        lambda p: p["records"][0].pop("retrieved_at"),
        lambda p: p["records"][0].pop("content_sha256"),
        lambda p: p["records"][0].update({"source_url": "file:///local"}),
        lambda p: p["records"][0].update({"field_status": "CONFLICTING"}),
        lambda p: p.pop("frozen_at"),
        lambda p: p.pop("data_class"),
        lambda p: p.pop("field_source_refs"),
        lambda p: p["feature_dependencies"][0].update({"dependency_check": "OPEN"}),
        lambda p: p["field_source_refs"].update({"ticker": ["ghost"]}),
        lambda p: p.update({"surprise": 1}),
        lambda p: p.update({"feature_snapshot_at": "2026-09-12T00:00:00Z"}),
    ],
)
def test_every_pointer_is_exact_within_the_manifest(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    raw = _mutated(mutate)
    document = json.loads(raw)
    report = check_manifest(raw)

    assert report.findings
    for finding in report.findings:
        if not finding.pointer:
            continue
        parent, last = _resolve_parent(document, finding.pointer)
        if finding.code in _CODES_POINTING_AT_ABSENT_FIELDS:
            continue
        exists = int(last) < len(parent) if isinstance(parent, list) else last in parent
        assert exists, finding


def test_non_bytes_input_raises_type_error() -> None:
    with pytest.raises(TypeError):
        check_manifest("not bytes")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"{not json", SourceHealthCode.PARSE_FAILED),
        (b"[1, 2, 3]", SourceHealthCode.PARSE_FAILED),
        (b"\xff\xfe{}", SourceHealthCode.PARSE_FAILED),
        (b'{"schema": 1, "schema": 2}', SourceHealthCode.DUPLICATE_KEY),
    ],
)
def test_unparseable_manifest_fails_closed(raw: bytes, code: SourceHealthCode) -> None:
    report = check_manifest(raw)

    assert report.status is SourceHealthStatus.FAILED_CLOSED
    assert len(report.findings) == 1
    assert report.findings[0].code is code
    assert report.findings[0].severity is FindingSeverity.ERROR


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"schema": "other.schema"}),
        lambda p: p.update({"schema_version": 1}),
        lambda p: p.update({"schema_version": 3}),
        lambda p: p.pop("schema"),
    ],
)
def test_unsupported_schema_fails_closed(mutate: Callable[[dict[str, object]], None]) -> None:
    report = check_manifest(_mutated(mutate))

    assert report.status is SourceHealthStatus.FAILED_CLOSED
    assert _codes(report) == [(SourceHealthCode.UNSUPPORTED_SCHEMA.value, "/schema")]


def test_unknown_field_is_reported_but_not_terminal() -> None:
    report = check_manifest(_mutated(lambda p: p.update({"surprise": 1})))

    assert report.status is SourceHealthStatus.FINDINGS
    assert (SourceHealthCode.UNKNOWN_FIELD.value, "/surprise") in _codes(report)


def test_missing_field_is_reported() -> None:
    report = check_manifest(_mutated(lambda p: p.pop("frozen_at")))

    assert (SourceHealthCode.FIELD_MISSING.value, "/frozen_at") in _codes(report)


def test_malformed_identity_field_is_reported() -> None:
    report = check_manifest(_mutated(lambda p: p.update({"event_id": 123})))

    assert (SourceHealthCode.FIELD_MALFORMED.value, "/event_id") in _codes(report)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://example.com/release", SourceHealthCode.URL_NOT_HTTPS),
        ("ftp://example.com/release", SourceHealthCode.URL_NOT_HTTPS),
        ("file:///etc/passwd", SourceHealthCode.MUTABLE_LOCAL_REFERENCE),
        ("data:text/plain,abc", SourceHealthCode.MUTABLE_LOCAL_REFERENCE),
        ("notes/evidence.html", SourceHealthCode.MUTABLE_LOCAL_REFERENCE),
        ("https://localhost/release", SourceHealthCode.URL_NOT_PUBLIC),
        ("https://127.0.0.1/release", SourceHealthCode.URL_NOT_PUBLIC),
        ("https://10.0.0.5/release", SourceHealthCode.URL_NOT_PUBLIC),
        ("https://internal.corp.local/release", SourceHealthCode.URL_NOT_PUBLIC),
        ("https://user:secret@example.com/release", SourceHealthCode.URL_MALFORMED),
        ("https://example.com/a b", SourceHealthCode.URL_MALFORMED),
        ("", SourceHealthCode.URL_MALFORMED),
    ],
)
def test_source_url_findings(url: str, code: SourceHealthCode) -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][0]["source_url"] = url

    assert (code.value, "/records/0/source_url") in _codes(check_manifest(_mutated(mutate)))


def test_missing_source_url_is_reported() -> None:
    def mutate(payload: dict[str, object]) -> None:
        del payload["records"][0]["source_url"]

    assert (SourceHealthCode.URL_MISSING.value, "/records/0/source_url") in _codes(
        check_manifest(_mutated(mutate))
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["records"][0].pop("publisher"),
        lambda p: p["records"][0].update({"publisher": ""}),
        lambda p: p["records"][0].update({"publisher": 7}),
    ],
)
def test_missing_or_empty_publisher_is_reported(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    assert (SourceHealthCode.PUBLISHER_MISSING.value, "/records/0/publisher") in _codes(
        check_manifest(_mutated(mutate))
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["records"][0].pop("retrieved_at"),
        lambda p: p["records"][0].update({"retrieved_at": "yesterday"}),
    ],
)
def test_missing_or_malformed_retrieval_time_is_reported(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    assert (SourceHealthCode.RETRIEVAL_TIME_MISSING.value, "/records/0/retrieved_at") in _codes(
        check_manifest(_mutated(mutate))
    )


def test_publication_time_missing_without_interval_or_unresolved_state() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][0]["published_at"] = None
        payload["records"][0]["published_at_interval"] = None

    codes = _codes(check_manifest(_mutated(mutate)))
    assert (SourceHealthCode.PUBLICATION_TIME_MISSING.value, "/records/0/published_at") in codes


@pytest.mark.parametrize(
    ("mutate", "pointer"),
    [
        (
            lambda p: p["records"][1].update(
                {"published_at_interval": {"start": "2026-01-01T00:00:00Z"}}
            ),
            "/records/1/published_at_interval",
        ),
        (
            lambda p: p["records"][1].update({"published_at_precision": "DATE_INTERVAL"}),
            "/records/1/published_at_precision",
        ),
        (
            lambda p: p["records"][0].update({"published_at_precision": "DAY"}),
            "/records/0/published_at_precision",
        ),
        (
            lambda p: p["records"][0].update(
                {
                    "published_at": None,
                    "published_at_interval": {
                        "start": "2026-08-15T00:00:00Z",
                        "end": "2026-08-14T00:00:00Z",
                    },
                }
            ),
            "/records/0/published_at_interval",
        ),
        (
            lambda p: p["records"][0].update(
                {"published_at": None, "published_at_precision": "SECOND"}
            ),
            "/records/0/published_at_precision",
        ),
    ],
)
def test_publication_precision_conflicts(
    mutate: Callable[[dict[str, object]], None], pointer: str
) -> None:
    codes = _codes(check_manifest(_mutated(mutate)))
    assert (SourceHealthCode.PUBLICATION_PRECISION_CONFLICT.value, pointer) in codes


def test_unresolved_field_status_is_reported() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][0]["field_status"] = "CONFLICTING"

    codes = _codes(check_manifest(_mutated(mutate)))
    assert (
        SourceHealthCode.UNRESOLVED_STATE_NOT_EXPLICIT.value,
        "/records/0/field_status",
    ) in codes


def test_declared_conflicting_context_is_reported() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["event_context"]["missing_or_conflicting_evidence"] = ["time conflict"]

    codes = _codes(check_manifest(_mutated(mutate)))
    assert (
        SourceHealthCode.UNRESOLVED_STATE_NOT_EXPLICIT.value,
        "/event_context/missing_or_conflicting_evidence",
    ) in codes


def test_retrieval_time_as_publication_type_is_an_error() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][1]["published_at_type"] = "RETRIEVAL_TIMESTAMP"

    report = check_manifest(_mutated(mutate))
    finding = next(
        f for f in report.findings if f.code is SourceHealthCode.RETRIEVAL_TIME_AS_PUBLICATION
    )

    assert finding.severity is FindingSeverity.ERROR
    assert finding.pointer == "/records/1/published_at_type"


def test_publication_equal_to_retrieval_time_is_a_warning() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][1]["published_at"] = payload["records"][1]["retrieved_at"]

    report = check_manifest(_mutated(mutate))
    finding = next(
        f for f in report.findings if f.code is SourceHealthCode.RETRIEVAL_TIME_AS_PUBLICATION
    )

    assert finding.severity is FindingSeverity.WARNING
    assert finding.pointer == "/records/1/published_at"


@pytest.mark.parametrize(
    ("mutate", "pointer"),
    [
        (
            lambda p: p.update({"feature_snapshot_at": "2026-09-12T00:00:00Z"}),
            "/feature_snapshot_at",
        ),
        (
            lambda p: p.update({"latest_evidence_at": "2026-09-12T00:00:00Z"}),
            "/latest_evidence_at",
        ),
        (
            lambda p: p["records"][0].update({"retrieved_at": "2026-09-12T00:00:00Z"}),
            "/records/0/retrieved_at",
        ),
        (
            lambda p: p.update({"decision_cutoff": "2026-09-12T12:00:00Z"}),
            "/decision_cutoff",
        ),
        (
            lambda p: p.update({"frozen_at": "2026-08-30T00:00:00Z"}),
            "/frozen_at",
        ),
    ],
)
def test_cutoff_ordering_contradictions(
    mutate: Callable[[dict[str, object]], None], pointer: str
) -> None:
    codes = _codes(check_manifest(_mutated(mutate)))
    assert (SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION.value, pointer) in codes


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"feature_dependencies": []}),
        lambda p: p["feature_dependencies"][0].update({"dependency_check": "PENDING"}),
        lambda p: p["feature_dependencies"][0].update({"source_refs": ["ghost"]}),
        lambda p: p["feature_dependencies"][0].update({"source_refs": []}),
        lambda p: p["feature_dependencies"].append(
            {
                "feature_id": "event_schedule",
                "source_refs": ["kr-issuer-schedule"],
                "dependency_check": "ELIGIBLE",
            }
        ),
        lambda p: p.pop("feature_dependencies"),
    ],
)
def test_feature_dependencies_missing_or_open(mutate: Callable[[dict[str, object]], None]) -> None:
    codes = _codes(check_manifest(_mutated(mutate)))
    assert any(code == SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN.value for code, _ in codes)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["field_source_refs"].pop("ticker"),
        lambda p: p["field_source_refs"].update({"ticker": ["ghost"]}),
        lambda p: p["field_source_refs"].update({"ticker": []}),
        lambda p: p["field_source_refs"].update({"not_a_context_field": ["kr-issuer-schedule"]}),
        lambda p: p.pop("field_source_refs"),
    ],
)
def test_provenance_mapping_invalid(mutate: Callable[[dict[str, object]], None]) -> None:
    codes = _codes(check_manifest(_mutated(mutate)))
    assert any(code == SourceHealthCode.PROVENANCE_MAPPING_INVALID.value for code, _ in codes)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["records"][0].pop("content_sha256"),
        lambda p: p["records"][0].update({"content_sha256": "NOT-A-SHA"}),
    ],
)
def test_revision_identity_missing(mutate: Callable[[dict[str, object]], None]) -> None:
    codes = _codes(check_manifest(_mutated(mutate)))
    assert (SourceHealthCode.REVISION_IDENTITY_MISSING.value, "/records/0/content_sha256") in codes


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("data_class"),
        lambda p: p.update({"data_class": "SYNTHETIC_CONTRACT_FIXTURE"}),
        lambda p: p["data_qualifiers"].remove("NOT_ALPHA_EVIDENCE"),
        lambda p: p.update({"entitlement_note": ""}),
        lambda p: p.update({"redistribution_status": "FREE_FOR_ALL"}),
        lambda p: p["records"][0].update({"redistribution_status": "UNKNOWN"}),
        lambda p: p["records"][0].update({"entitlement_note": ""}),
    ],
)
def test_classification_missing(mutate: Callable[[dict[str, object]], None]) -> None:
    codes = _codes(check_manifest(_mutated(mutate)))
    assert any(code == SourceHealthCode.CLASSIFICATION_MISSING.value for code, _ in codes)


def test_empty_limitations_list_is_malformed() -> None:
    codes = _codes(check_manifest(_mutated(lambda p: p.update({"limitations": []}))))
    assert (SourceHealthCode.FIELD_MALFORMED.value, "/limitations") in codes


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["records"][0].update({"source_kind": "OFFICIAL_DISSEMINATION"}),
        lambda p: p["records"][1].update({"source_kind": "ISSUER_PRIMARY"}),
    ],
)
def test_issuer_primary_cardinality(mutate: Callable[[dict[str, object]], None]) -> None:
    codes = _codes(check_manifest(_mutated(mutate)))
    assert (SourceHealthCode.ISSUER_PRIMARY_CARDINALITY.value, "/records") in codes


def test_issuer_url_mismatch_with_context() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][0]["source_url"] = "https://ir.kroger.com/some-other-page"

    report = check_manifest(
        _mutated(mutate),
        event_list=EVENT_LIST.read_bytes(),
        selection_rule=SELECTION_RULE.read_bytes(),
    )

    assert (SourceHealthCode.ISSUER_URL_MISMATCH.value, "/records/0/source_url") in _codes(report)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"issuer": "Another Issuer"}),
        lambda p: p["event_context"].update({"ticker": "XX"}),
        lambda p: p.update({"event_id": "ZZ-2099Q1-EARNINGS"}),
    ],
)
def test_identity_mismatch_with_context(mutate: Callable[[dict[str, object]], None]) -> None:
    report = check_manifest(
        _mutated(mutate),
        event_list=EVENT_LIST.read_bytes(),
        selection_rule=SELECTION_RULE.read_bytes(),
    )

    assert any(code == SourceHealthCode.IDENTITY_MISMATCH.value for code, _ in _codes(report))


def test_context_hash_binding_mismatch_is_reported() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["event_list_sha256"] = "0" * 64

    report = check_manifest(
        _mutated(mutate),
        event_list=EVENT_LIST.read_bytes(),
        selection_rule=SELECTION_RULE.read_bytes(),
    )

    assert (
        SourceHealthCode.PROVENANCE_CONTRADICTION.value,
        "/event_list_sha256",
    ) in _codes(report)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["records"][1].update({"evidence_id": "kr-issuer-schedule"}),
        lambda p: p.update({"latest_evidence_at": "2026-08-20T00:00:00Z"}),
        lambda p: p["sec_filing"].update({"url": "https://www.sec.gov/invented"}),
    ],
)
def test_provenance_contradictions(mutate: Callable[[dict[str, object]], None]) -> None:
    codes = _codes(check_manifest(_mutated(mutate)))
    assert any(code == SourceHealthCode.PROVENANCE_CONTRADICTION.value for code, _ in codes)


def test_sec_filing_acceptance_after_cutoff_is_reported() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["sec_filing"] = {
            "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
            "accepted_at": "2026-09-12T00:00:00Z",
            "status": "ACCEPTED",
            "reason": None,
        }

    codes = _codes(check_manifest(_mutated(mutate)))
    assert (
        SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION.value,
        "/sec_filing/accepted_at",
    ) in codes


def test_context_supplied_partially_fails_closed_for_cross_checks() -> None:
    raw = _manifest_bytes(_load_manifest())

    report = check_manifest(raw, event_list=EVENT_LIST.read_bytes())

    assert (SourceHealthCode.CONTEXT_INVALID.value, "/event_id") in _codes(report)


def test_invalid_context_bytes_fail_closed_for_cross_checks() -> None:
    raw = _manifest_bytes(_load_manifest())

    report = check_manifest(
        raw, event_list=b"{not json", selection_rule=SELECTION_RULE.read_bytes()
    )

    assert (SourceHealthCode.CONTEXT_INVALID.value, "/event_id") in _codes(report)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["records"][0].update({"publisher": "\u202e\u0000<script>"}),
        lambda p: p["records"][0].update({"source_url": "https://example.com/\u202e"}),
        lambda p: p.update({"issuer": "x" * 10_000}),
        lambda p: p["records"][0].update({"entitlement_note": "\ud83d\udcc9 emoji note"}),
    ],
)
def test_hostile_strings_are_deterministic_and_never_raise(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    raw = _mutated(mutate)

    first = check_manifest(raw)
    second = check_manifest(raw)

    assert _codes(first) == _codes(second)
    assert canonical_report_bytes(first) == canonical_report_bytes(second)


def test_check_path_reads_a_frozen_manifest() -> None:
    report = check_path(EVENTS / "KR-2026Q2-EARNINGS.json")

    assert report.status is SourceHealthStatus.HEALTHY


def test_check_path_supports_a_root_relative_manifest() -> None:
    report = check_path(Path("KR-2026Q2-EARNINGS.json"), root=EVENTS)

    assert report.status is SourceHealthStatus.HEALTHY


@pytest.mark.parametrize(
    "candidate",
    [
        Path("does-not-exist.json"),
        Path("..") / "outside.json",
        Path("."),
        Path("nested") / ".." / "KR-2026Q2-EARNINGS.json",
    ],
)
def test_check_path_untrusted_inputs_fail_closed(candidate: Path) -> None:
    report = check_path(candidate, root=EVENTS)

    assert report.status is SourceHealthStatus.FAILED_CLOSED
    assert _codes(report) == [(SourceHealthCode.UNTRUSTED_PATH.value, "")]


def test_check_path_absolute_input_with_root_fails_closed() -> None:
    report = check_path(EVENTS / "KR-2026Q2-EARNINGS.json", root=EVENTS)

    assert report.status is SourceHealthStatus.FAILED_CLOSED
    assert _codes(report) == [(SourceHealthCode.UNTRUSTED_PATH.value, "")]


def test_check_path_directory_input_fails_closed(tmp_path: Path) -> None:
    report = check_path(tmp_path)

    assert report.status is SourceHealthStatus.FAILED_CLOSED
    assert _codes(report) == [(SourceHealthCode.UNTRUSTED_PATH.value, "")]


def test_check_path_symlink_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    target.write_bytes((EVENTS / "KR-2026Q2-EARNINGS.json").read_bytes())
    link = tmp_path / "link.json"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    report = check_path(link)

    assert report.status is SourceHealthStatus.FAILED_CLOSED
    assert _codes(report) == [(SourceHealthCode.UNTRUSTED_PATH.value, "")]


def test_traversal_path_without_root_fails_closed() -> None:
    report = check_path(Path("..") / "ringdown-market" / "README.md")

    assert report.status is SourceHealthStatus.FAILED_CLOSED
    assert _codes(report) == [(SourceHealthCode.UNTRUSTED_PATH.value, "")]


def test_canonical_report_shape_and_ordering() -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["records"][0]["publisher"] = ""
        payload["records"][1]["publisher"] = ""

    report = check_manifest(_mutated(mutate))
    rendered = json.loads(canonical_report_bytes(report))

    assert rendered["schema"] == "ringdown.source_health_report"
    assert rendered["schema_version"] == 1
    assert rendered["status"] == "FINDINGS"
    assert rendered["manifest_sha256"] == report.manifest_sha256
    assert [finding["pointer"] for finding in rendered["findings"]] == [
        finding.pointer for finding in report.findings
    ]


def test_every_code_has_remediation_and_severity() -> None:
    for code in SourceHealthCode:
        assert source_health._REMEDIATION[code]


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted during offline health check")

    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket, "gethostbyname", deny)


def test_no_network_is_used_by_any_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)

    for path in FROZEN_MANIFESTS:
        report = check_manifest(
            path.read_bytes(),
            event_list=EVENT_LIST.read_bytes(),
            selection_rule=SELECTION_RULE.read_bytes(),
        )
        assert report.status is SourceHealthStatus.HEALTHY

    blocked = check_manifest(b"{not json")
    assert blocked.status is SourceHealthStatus.FAILED_CLOSED
    assert check_path(EVENTS / "KR-2026Q2-EARNINGS.json").status is SourceHealthStatus.HEALTHY


def test_module_does_not_import_network_or_broker_capabilities() -> None:
    source = Path(source_health.__file__).read_text(encoding="utf-8")
    forbidden_prefixes = (
        "socket",
        "http.client",
        "urllib.request",
        "requests",
        "httpx",
        "aiohttp",
        "webbrowser",
        "subprocess",
        "alpaca",
        "mcp",
    )

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            module = stripped.removeprefix("import ").split(" as ")[0].strip()
            assert not module.startswith(forbidden_prefixes), stripped
        elif stripped.startswith("from "):
            module = stripped.removeprefix("from ").split(" import ")[0].strip()
            assert not module.startswith(forbidden_prefixes), stripped
