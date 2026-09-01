"""Deterministic, offline source-provenance health checks for evidence manifests.

The checker explains missing or contradictory source and provenance metadata in
frozen point-in-time evidence manifests before a researcher attempts
evaluation. It never fetches a URL, resolves a hostname, or decides whether a
claim is true; a green health check is engineering evidence only. Strict
parsing is reused from the frozen replay-evidence contract so no parallel
schema exists here.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from ringdown_market.contracts.replay_evidence import (
    _EVENT_CONTEXT_FIELDS,
    _IANA_TIMEZONE,
    _MANIFEST_FIELDS,
    _RECORD_FIELDS,
    _SOURCE_BACKED_CONTEXT_FIELDS,
    ReplayEvidenceRejected,
    ReplayEvidenceRejectionReason,
    _decode,
    _sha256,
    _text,
    _text_list,
    _timestamp,
    _validate_event_list,
)

_MANIFEST_SCHEMA: Final = "ringdown.point_in_time_evidence_manifest"
_SUPPORTED_SCHEMA_VERSION: Final = 2
_REPORT_SCHEMA: Final = "ringdown.source_health_report"
_REPORT_VERSION: Final = 1
_MANIFEST_SCHEMA_FIELD: Final = "schema"
_MANIFEST_SCHEMA_VERSION_FIELD: Final = "schema_version"
_DATA_CLASS: Final = "POINT_IN_TIME_EVENT_PANEL"
_REQUIRED_QUALIFIERS: Final = frozenset(
    {"INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE", "NO_OUTCOME_VALUES", "NO_BROKER_EXECUTION"}
)
_REDISTRIBUTION_VOCABULARY: Final = frozenset(
    {"PUBLIC_BYTES_ALLOWED", "METADATA_AND_HASH_ONLY", "UNAVAILABLE_NOT_PERMITTED"}
)
_ISSUER_PRIMARY: Final = "ISSUER_PRIMARY"
_DEPENDENCY_ELIGIBLE: Final = "ELIGIBLE"
_FILING_UNAVAILABLE: Final = "NOT_YET_AVAILABLE"
_FILING_FIELDS: Final = frozenset({"url", "accepted_at", "status", "reason"})
_DEPENDENCY_FIELDS: Final = frozenset({"feature_id", "source_refs", "dependency_check"})
_PUBLICATION_INTERVAL_FIELDS: Final = frozenset({"start", "end"})
_RETRIEVAL_FLAVORED_TOKENS: Final = ("ACCEPT", "COLLECT", "OBSERV", "RETRIEV")
_URL_CONTROL_CHARS: Final = frozenset(chr(code) for code in range(33)) | {"\x7f"}
_LOCAL_URL_SCHEMES: Final = frozenset({"file", "data"})
_SEC_FILING_AVAILABLE_REVISION_FIELDS: Final = ("url", "accepted_at")


class SourceHealthStatus(StrEnum):
    """Overall outcome of one deterministic health check."""

    HEALTHY = "HEALTHY"
    FINDINGS = "FINDINGS"
    FAILED_CLOSED = "FAILED_CLOSED"


class FindingSeverity(StrEnum):
    """Severity carried by every deterministic finding."""

    ERROR = "ERROR"
    WARNING = "WARNING"


class SourceHealthCode(StrEnum):
    """Stable machine-readable finding codes."""

    PARSE_FAILED = "PARSE_FAILED"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNTRUSTED_PATH = "UNTRUSTED_PATH"
    FIELD_MISSING = "FIELD_MISSING"
    FIELD_MALFORMED = "FIELD_MALFORMED"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    URL_MISSING = "URL_MISSING"
    URL_MALFORMED = "URL_MALFORMED"
    URL_NOT_HTTPS = "URL_NOT_HTTPS"
    URL_NOT_PUBLIC = "URL_NOT_PUBLIC"
    MUTABLE_LOCAL_REFERENCE = "MUTABLE_LOCAL_REFERENCE"
    PUBLISHER_MISSING = "PUBLISHER_MISSING"
    RETRIEVAL_TIME_MISSING = "RETRIEVAL_TIME_MISSING"
    PUBLICATION_TIME_MISSING = "PUBLICATION_TIME_MISSING"
    PUBLICATION_PRECISION_CONFLICT = "PUBLICATION_PRECISION_CONFLICT"
    UNRESOLVED_STATE_NOT_EXPLICIT = "UNRESOLVED_STATE_NOT_EXPLICIT"
    RETRIEVAL_TIME_AS_PUBLICATION = "RETRIEVAL_TIME_AS_PUBLICATION"
    CUTOFF_ORDERING_CONTRADICTION = "CUTOFF_ORDERING_CONTRADICTION"
    DEPENDENCY_MISSING_OR_OPEN = "DEPENDENCY_MISSING_OR_OPEN"
    PROVENANCE_MAPPING_INVALID = "PROVENANCE_MAPPING_INVALID"
    REVISION_IDENTITY_MISSING = "REVISION_IDENTITY_MISSING"
    CLASSIFICATION_MISSING = "CLASSIFICATION_MISSING"
    ISSUER_PRIMARY_CARDINALITY = "ISSUER_PRIMARY_CARDINALITY"
    ISSUER_URL_MISMATCH = "ISSUER_URL_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    PROVENANCE_CONTRADICTION = "PROVENANCE_CONTRADICTION"
    CONTEXT_INVALID = "CONTEXT_INVALID"


_REMEDIATION: Final[dict[SourceHealthCode, str]] = {
    SourceHealthCode.PARSE_FAILED: (
        "Restore the manifest to strict UTF-8 JSON with a single root object."
    ),
    SourceHealthCode.DUPLICATE_KEY: (
        "Remove duplicate keys; frozen evidence documents must be uniquely keyed."
    ),
    SourceHealthCode.UNSUPPORTED_SCHEMA: (
        "Use the data-only evidence manifest schema "
        f"{_MANIFEST_SCHEMA} version {_SUPPORTED_SCHEMA_VERSION}."
    ),
    SourceHealthCode.UNTRUSTED_PATH: (
        "Supply a regular file path without traversal, missing targets, or symbolic links."
    ),
    SourceHealthCode.FIELD_MISSING: (
        "Restore the required field using the evidence metadata required by "
        "docs/SOURCE_AND_CLAIM_POLICY.md."
    ),
    SourceHealthCode.FIELD_MALFORMED: (
        "Correct the field to the frozen schema shape before evaluation."
    ),
    SourceHealthCode.UNKNOWN_FIELD: (
        "Remove the unknown field; frozen schemas are closed and cannot be extended ad hoc."
    ),
    SourceHealthCode.URL_MISSING: ("Record the exact public source URL for the evidence item."),
    SourceHealthCode.URL_MALFORMED: (
        "Record a canonical, credential-free URL that identifies the public source."
    ),
    SourceHealthCode.URL_NOT_HTTPS: (
        "Use an HTTPS source reference so the artifact can be publicly re-derived."
    ),
    SourceHealthCode.URL_NOT_PUBLIC: (
        "Reference a publicly routable host; private or loopback hosts cannot support "
        "a public artifact."
    ),
    SourceHealthCode.MUTABLE_LOCAL_REFERENCE: (
        "Replace local, file, or relative references with an immutable public source URL."
    ),
    SourceHealthCode.PUBLISHER_MISSING: ("Record the organization that published the source."),
    SourceHealthCode.RETRIEVAL_TIME_MISSING: (
        "Record the UTC retrieval timestamp for the exact frozen source bytes."
    ),
    SourceHealthCode.PUBLICATION_TIME_MISSING: (
        "Supply the earliest source-supported public-observability time, a conservative "
        "date interval, or an explicit unresolved state."
    ),
    SourceHealthCode.PUBLICATION_PRECISION_CONFLICT: (
        "Align published_at, published_at_interval, published_at_precision, and "
        "published_at_type; never claim finer precision than the source supports."
    ),
    SourceHealthCode.UNRESOLVED_STATE_NOT_EXPLICIT: (
        "Make missing, revised, or conflicting evidence explicit; unresolved events "
        "must not be admitted."
    ),
    SourceHealthCode.RETRIEVAL_TIME_AS_PUBLICATION: (
        "Keep retrieval, collector-observed, and SEC acceptance times separate; they "
        "never replace publication time."
    ),
    SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION: (
        "Restore evidence-clock ordering: publication and retrieval no later than the "
        "decision cutoff, feature snapshot at or before the cutoff, and the freeze "
        "bound to the snapshot."
    ),
    SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN: (
        "Close every feature dependency to eligible source records before evaluation."
    ),
    SourceHealthCode.PROVENANCE_MAPPING_INVALID: (
        "Map every source-backed fact to evidence record IDs; references must resolve "
        "to retained records."
    ),
    SourceHealthCode.REVISION_IDENTITY_MISSING: (
        "Retain a content hash or immutable source revision for the frozen representation."
    ),
    SourceHealthCode.CLASSIFICATION_MISSING: (
        "Record data class, qualifiers, entitlement note, and redistribution status "
        "for the artifact."
    ),
    SourceHealthCode.ISSUER_PRIMARY_CARDINALITY: (
        "Retain exactly one ISSUER_PRIMARY record for the event."
    ),
    SourceHealthCode.ISSUER_URL_MISMATCH: (
        "The ISSUER_PRIMARY source URL must equal the frozen event issuer release URL."
    ),
    SourceHealthCode.IDENTITY_MISMATCH: (
        "Manifest identity must equal the frozen event list entry."
    ),
    SourceHealthCode.PROVENANCE_CONTRADICTION: (
        "Resolve contradictory provenance; retain every source and hash and never invent metadata."
    ),
    SourceHealthCode.CONTEXT_INVALID: (
        "Supply event-list and selection-rule bytes that pass the replay evidence "
        "contract, or omit the context entirely."
    ),
}

_MISSING_CODE_OVERRIDES: Final[dict[str, SourceHealthCode]] = {
    "content_sha256": SourceHealthCode.REVISION_IDENTITY_MISSING,
    "data_class": SourceHealthCode.CLASSIFICATION_MISSING,
    "data_qualifiers": SourceHealthCode.CLASSIFICATION_MISSING,
    "entitlement_note": SourceHealthCode.CLASSIFICATION_MISSING,
    "feature_dependencies": SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN,
    "field_source_refs": SourceHealthCode.PROVENANCE_MAPPING_INVALID,
    "publisher": SourceHealthCode.PUBLISHER_MISSING,
    "redistribution_status": SourceHealthCode.CLASSIFICATION_MISSING,
    "retrieved_at": SourceHealthCode.RETRIEVAL_TIME_MISSING,
    "source_url": SourceHealthCode.URL_MISSING,
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One deterministic, explainable provenance problem."""

    code: SourceHealthCode
    severity: FindingSeverity
    pointer: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class SourceHealthReport:
    """The deterministic result of one offline health check."""

    status: SourceHealthStatus
    manifest_sha256: str
    findings: tuple[Finding, ...]


class _Collector:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def emit(
        self,
        code: SourceHealthCode,
        severity: FindingSeverity,
        pointer: str,
        message: str,
    ) -> None:
        self.findings.append(
            Finding(
                code=code,
                severity=severity,
                pointer=pointer,
                message=message,
                remediation=_REMEDIATION[code],
            )
        )

    def error(self, code: SourceHealthCode, pointer: str, message: str) -> None:
        self.emit(code, FindingSeverity.ERROR, pointer, message)

    def warning(self, code: SourceHealthCode, pointer: str, message: str) -> None:
        self.emit(code, FindingSeverity.WARNING, pointer, message)


def _escape_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _join(pointer: str, part: str | int) -> str:
    escaped = _escape_pointer_part(str(part))
    return f"/{escaped}" if not pointer else f"{pointer}/{escaped}"


def _contains_lone_surrogate(value: object) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in current):
                return True
        elif isinstance(current, Mapping):
            pending.extend(current)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_report_bytes(report: SourceHealthReport) -> bytes:
    """Serialize a report using the stable canonical JSON representation."""

    payload = {
        "schema": _REPORT_SCHEMA,
        "schema_version": _REPORT_VERSION,
        "status": report.status.value,
        "manifest_sha256": report.manifest_sha256,
        "findings": [
            {
                "code": finding.code.value,
                "severity": finding.severity.value,
                "pointer": finding.pointer,
                "message": finding.message,
                "remediation": finding.remediation,
            }
            for finding in report.findings
        ],
    }
    return _canonical_json(payload) + b"\n"


def _failed_closed(
    code: SourceHealthCode, pointer: str, message: str, sha256: str
) -> SourceHealthReport:
    finding = Finding(
        code=code,
        severity=FindingSeverity.ERROR,
        pointer=pointer,
        message=message,
        remediation=_REMEDIATION[code],
    )
    return SourceHealthReport(
        status=SourceHealthStatus.FAILED_CLOSED,
        manifest_sha256=sha256,
        findings=(finding,),
    )


def _checked(
    checker: Any,
    value: object,
    *,
    pointer: str,
    collector: _Collector,
    code: SourceHealthCode,
    message: str,
) -> Any:
    try:
        return checker(value, path=pointer)
    except ReplayEvidenceRejected:
        collector.error(code, pointer, message)
        return None


def _missing_code(field: str) -> SourceHealthCode:
    return _MISSING_CODE_OVERRIDES.get(field, SourceHealthCode.FIELD_MISSING)


def _shape_findings(
    value: object,
    *,
    pointer: str,
    fields: frozenset[str],
    collector: _Collector,
    malformed_message: str = "must be an object",
) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        collector.error(SourceHealthCode.FIELD_MALFORMED, pointer, malformed_message)
        return None
    keys = set(value)
    for field in sorted(fields - keys):
        collector.error(
            _missing_code(field),
            _join(pointer, field),
            "required field is missing",
        )
    for field in sorted(keys - fields):
        collector.error(
            SourceHealthCode.UNKNOWN_FIELD,
            _join(pointer, field),
            "field is not part of the frozen schema",
        )
    return value


def _text_field(
    value: object,
    *,
    pointer: str,
    collector: _Collector,
    code: SourceHealthCode = SourceHealthCode.FIELD_MALFORMED,
    message: str = "must be non-empty text",
) -> str | None:
    return _checked(_text, value, pointer=pointer, collector=collector, code=code, message=message)


def _timestamp_field(
    value: object,
    *,
    pointer: str,
    collector: _Collector,
    code: SourceHealthCode = SourceHealthCode.FIELD_MALFORMED,
    message: str = "must be an explicit UTC timestamp ending in Z",
) -> datetime | None:
    return _checked(
        _timestamp, value, pointer=pointer, collector=collector, code=code, message=message
    )


def _string_list(
    value: object,
    *,
    pointer: str,
    collector: _Collector,
    nonempty: bool,
    message: str,
) -> tuple[str, ...] | None:
    try:
        return _text_list(value, path=pointer, nonempty=nonempty, unique=True)
    except ReplayEvidenceRejected:
        collector.error(SourceHealthCode.FIELD_MALFORMED, pointer, message)
        return None


def _evidence_refs(
    value: object,
    *,
    pointer: str,
    collector: _Collector,
    empty_code: SourceHealthCode,
    empty_message: str,
) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        collector.error(SourceHealthCode.FIELD_MALFORMED, pointer, "must be a list of evidence IDs")
        return None
    if not value:
        collector.error(empty_code, pointer, empty_message)
        return None
    if any(not isinstance(item, str) or not item for item in value) or len(set(value)) != len(
        value
    ):
        collector.error(
            SourceHealthCode.FIELD_MALFORMED,
            pointer,
            "must be a non-empty list of unique, non-empty evidence IDs",
        )
        return None
    return tuple(value)


def _host_is_private(host: str) -> bool:
    name = host.lower().strip(".")
    if not name:
        return False
    if name == "localhost" or name.endswith((".localhost", ".local", ".internal", ".invalid")):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def _url_findings(value: object, *, pointer: str, collector: _Collector) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        collector.error(
            SourceHealthCode.URL_MALFORMED,
            pointer,
            "must be a non-empty URL string without surrounding whitespace",
        )
        return
    if any(character in _URL_CONTROL_CHARS for character in value):
        collector.error(
            SourceHealthCode.URL_MALFORMED, pointer, "must not contain control characters"
        )
        return
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if not scheme and not parsed.netloc:
        collector.error(
            SourceHealthCode.MUTABLE_LOCAL_REFERENCE,
            pointer,
            "relative or filesystem-local references cannot support a public artifact",
        )
        return
    if scheme in _LOCAL_URL_SCHEMES:
        collector.error(
            SourceHealthCode.MUTABLE_LOCAL_REFERENCE,
            pointer,
            f"'{scheme}:' references cannot support a public artifact",
        )
        return
    if scheme != "https":
        collector.error(
            SourceHealthCode.URL_NOT_HTTPS, pointer, f"remote URL uses '{scheme}' instead of https"
        )
        return
    if parsed.username is not None or parsed.password is not None:
        collector.error(SourceHealthCode.URL_MALFORMED, pointer, "must not embed credentials")
        return
    host = parsed.hostname or ""
    if not host:
        collector.error(SourceHealthCode.URL_MALFORMED, pointer, "must name a host")
        return
    if _host_is_private(host):
        collector.error(
            SourceHealthCode.URL_NOT_PUBLIC, pointer, f"host '{host}' is not publicly routable"
        )


def _retrieval_flavored(value: object) -> bool:
    return isinstance(value, str) and any(
        token in value.upper() for token in _RETRIEVAL_FLAVORED_TOKENS
    )


def _publication_findings(
    record: Mapping[str, object],
    *,
    pointer: str,
    collector: _Collector,
    decision_cutoff: datetime | None,
    retrieved_at: datetime | None,
) -> datetime | None:
    published_raw = record.get("published_at")
    interval_raw = record.get("published_at_interval")
    precision_raw = record.get("published_at_precision")
    type_raw = record.get("published_at_type")

    if "published_at_precision" in record and (
        not isinstance(precision_raw, str) or not precision_raw
    ):
        collector.error(
            SourceHealthCode.FIELD_MALFORMED,
            _join(pointer, "published_at_precision"),
            "must be non-empty text",
        )
        precision_raw = None
    if "published_at_type" in record and (not isinstance(type_raw, str) or not type_raw):
        collector.error(
            SourceHealthCode.FIELD_MALFORMED,
            _join(pointer, "published_at_type"),
            "must be non-empty text",
        )
        type_raw = None

    exact: datetime | None = None
    exact_text: str | None = None
    if published_raw is not None:
        exact = _timestamp_field(
            published_raw,
            pointer=_join(pointer, "published_at"),
            collector=collector,
            message="published_at must be an explicit UTC timestamp ending in Z",
        )
        if exact is not None:
            exact_text = str(published_raw)

    upper: datetime | None = None
    if exact is not None:
        if interval_raw is not None:
            collector.error(
                SourceHealthCode.PUBLICATION_PRECISION_CONFLICT,
                _join(pointer, "published_at_interval"),
                "exact publication time cannot also carry a date interval",
            )
        if isinstance(precision_raw, str) and precision_raw != "SECOND":
            collector.error(
                SourceHealthCode.PUBLICATION_PRECISION_CONFLICT,
                _join(pointer, "published_at_precision"),
                "exact publication time requires SECOND precision",
            )
        if _retrieval_flavored(type_raw):
            collector.error(
                SourceHealthCode.RETRIEVAL_TIME_AS_PUBLICATION,
                _join(pointer, "published_at_type"),
                "publication type describes retrieval, collector observation, or acceptance, "
                "not publication",
            )
        upper = exact
    else:
        if precision_raw == "SECOND":
            collector.error(
                SourceHealthCode.PUBLICATION_PRECISION_CONFLICT,
                _join(pointer, "published_at_precision"),
                "sub-date precision is claimed without an exact publication instant",
            )
        if interval_raw is None and "published_at" in record:
            collector.error(
                SourceHealthCode.PUBLICATION_TIME_MISSING,
                _join(pointer, "published_at"),
                "no exact publication time and no conservative date interval are recorded",
            )
        else:
            if isinstance(precision_raw, str) and precision_raw != "DATE_INTERVAL":
                collector.error(
                    SourceHealthCode.PUBLICATION_PRECISION_CONFLICT,
                    _join(pointer, "published_at_precision"),
                    "a date interval requires DATE_INTERVAL precision",
                )
            upper = _interval_upper_bound(interval_raw, pointer=pointer, collector=collector)

    if (
        exact_text is not None
        and isinstance(record.get("retrieved_at"), str)
        and record.get("retrieved_at") == exact_text
    ):
        collector.warning(
            SourceHealthCode.RETRIEVAL_TIME_AS_PUBLICATION,
            _join(pointer, "published_at"),
            "publication time is identical to retrieval time; retrieval alone does not "
            "establish publication",
        )

    if upper is not None and decision_cutoff is not None and upper > decision_cutoff:
        collector.error(
            SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION,
            _join(pointer, "published_at" if exact is not None else "published_at_interval"),
            "publication bound is after the decision cutoff",
        )
    if retrieved_at is not None and decision_cutoff is not None and retrieved_at > decision_cutoff:
        collector.error(
            SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION,
            _join(pointer, "retrieved_at"),
            "evidence was retrieved after the decision cutoff",
        )
    if (
        upper is not None
        and retrieved_at is not None
        and _retrieval_flavored(type_raw)
        and upper > retrieved_at
    ):
        collector.error(
            SourceHealthCode.PROVENANCE_CONTRADICTION,
            _join(pointer, "published_at_interval"),
            "collector-observed publication bound is later than the retrieval time",
        )
    return upper


def _interval_upper_bound(
    value: object,
    *,
    pointer: str,
    collector: _Collector,
) -> datetime | None:
    window = _shape_findings(
        value,
        pointer=_join(pointer, "published_at_interval"),
        fields=_PUBLICATION_INTERVAL_FIELDS,
        collector=collector,
    )
    if window is None or "start" not in window or "end" not in window:
        return None
    start = _timestamp_field(
        window["start"],
        pointer=_join(pointer, "published_at_interval/start"),
        collector=collector,
        message="interval start must be an explicit UTC timestamp ending in Z",
    )
    end = _timestamp_field(
        window["end"],
        pointer=_join(pointer, "published_at_interval/end"),
        collector=collector,
        message="interval end must be an explicit UTC timestamp ending in Z",
    )
    if start is None or end is None:
        return None
    if start > end:
        collector.error(
            SourceHealthCode.PUBLICATION_PRECISION_CONFLICT,
            _join(pointer, "published_at_interval"),
            "publication interval is inverted",
        )
        return None
    return end


def _record_findings(
    item: object,
    *,
    pointer: str,
    collector: _Collector,
    decision_cutoff: datetime | None,
    record_ids: set[str],
    uppers: list[datetime],
    issuer_primary_indices: list[int],
    index: int,
) -> None:
    record = _shape_findings(item, pointer=pointer, fields=_RECORD_FIELDS, collector=collector)
    if record is None:
        return

    evidence_id = None
    if "evidence_id" in record:
        evidence_id = _text_field(
            record["evidence_id"],
            pointer=_join(pointer, "evidence_id"),
            collector=collector,
            message="evidence_id must be non-empty text",
        )
    if evidence_id is not None:
        if evidence_id in record_ids:
            collector.error(
                SourceHealthCode.PROVENANCE_CONTRADICTION,
                _join(pointer, "evidence_id"),
                "evidence IDs must be unique",
            )
        record_ids.add(evidence_id)

    source_kind = record.get("source_kind")
    if "source_kind" in record and (not isinstance(source_kind, str) or not source_kind):
        collector.error(
            SourceHealthCode.FIELD_MALFORMED,
            _join(pointer, "source_kind"),
            "must be non-empty text",
        )
    if source_kind == _ISSUER_PRIMARY:
        issuer_primary_indices.append(index)

    if "source_url" in record:
        _url_findings(
            record["source_url"], pointer=_join(pointer, "source_url"), collector=collector
        )

    if "publisher" in record:
        _text_field(
            record["publisher"],
            pointer=_join(pointer, "publisher"),
            collector=collector,
            code=SourceHealthCode.PUBLISHER_MISSING,
            message="publisher must be non-empty text",
        )

    retrieved_at = None
    if "retrieved_at" in record:
        retrieved_at = _timestamp_field(
            record["retrieved_at"],
            pointer=_join(pointer, "retrieved_at"),
            collector=collector,
            code=SourceHealthCode.RETRIEVAL_TIME_MISSING,
            message="retrieved_at must be an explicit UTC timestamp ending in Z",
        )

    upper = _publication_findings(
        record,
        pointer=pointer,
        collector=collector,
        decision_cutoff=decision_cutoff,
        retrieved_at=retrieved_at,
    )
    if upper is not None:
        uppers.append(upper)

    if "content_sha256" in record:
        _checked(
            _sha256,
            record["content_sha256"],
            pointer=_join(pointer, "content_sha256"),
            collector=collector,
            code=SourceHealthCode.REVISION_IDENTITY_MISSING,
            message="content_sha256 must be a lowercase SHA-256 digest",
        )

    field_status = record.get("field_status")
    if "field_status" in record and field_status != "PRESENT":
        if not isinstance(field_status, str) or not field_status:
            collector.error(
                SourceHealthCode.FIELD_MALFORMED,
                _join(pointer, "field_status"),
                "must be non-empty text",
            )
        else:
            collector.error(
                SourceHealthCode.UNRESOLVED_STATE_NOT_EXPLICIT,
                _join(pointer, "field_status"),
                f"field status '{field_status}' declares evidence that is not present "
                "and conflict-free",
            )

    if "entitlement_note" in record:
        _text_field(
            record["entitlement_note"],
            pointer=_join(pointer, "entitlement_note"),
            collector=collector,
            code=SourceHealthCode.CLASSIFICATION_MISSING,
            message="entitlement_note must be non-empty text",
        )

    redistribution = record.get("redistribution_status")
    if "redistribution_status" in record:
        if not isinstance(redistribution, str):
            collector.error(
                SourceHealthCode.FIELD_MALFORMED,
                _join(pointer, "redistribution_status"),
                "redistribution status must be text",
            )
        elif redistribution not in _REDISTRIBUTION_VOCABULARY:
            collector.error(
                SourceHealthCode.CLASSIFICATION_MISSING,
                _join(pointer, "redistribution_status"),
                "redistribution status is not one of the registered states",
            )

    if "limitations" in record:
        _string_list(
            record["limitations"],
            pointer=_join(pointer, "limitations"),
            collector=collector,
            nonempty=False,
            message="limitations must be a list of non-empty strings",
        )


def _records_findings(
    manifest: Mapping[str, object],
    *,
    collector: _Collector,
    decision_cutoff: datetime | None,
) -> tuple[bool, set[str], list[datetime], list[int]]:
    record_ids: set[str] = set()
    uppers: list[datetime] = []
    issuer_primary_indices: list[int] = []
    records = manifest.get("records")
    if "records" not in manifest:
        return False, record_ids, uppers, issuer_primary_indices
    if not isinstance(records, list):
        collector.error(
            SourceHealthCode.FIELD_MALFORMED, "/records", "must be a list of source records"
        )
        return False, record_ids, uppers, issuer_primary_indices
    if not records:
        collector.error(
            SourceHealthCode.FIELD_MALFORMED, "/records", "at least one source record is required"
        )
        return True, record_ids, uppers, issuer_primary_indices
    for index, item in enumerate(records):
        _record_findings(
            item,
            pointer=f"/records/{index}",
            collector=collector,
            decision_cutoff=decision_cutoff,
            record_ids=record_ids,
            uppers=uppers,
            issuer_primary_indices=issuer_primary_indices,
            index=index,
        )
    return True, record_ids, uppers, issuer_primary_indices


def _event_context_findings(
    manifest: Mapping[str, object], *, collector: _Collector
) -> datetime | None:
    if "event_context" not in manifest:
        return None
    context = _shape_findings(
        manifest["event_context"],
        pointer="/event_context",
        fields=_EVENT_CONTEXT_FIELDS,
        collector=collector,
    )
    if context is None:
        return None
    for field in sorted(_EVENT_CONTEXT_FIELDS & set(context)):
        pointer = _join("/event_context", field)
        value = context[field]
        if field in {"scheduled_event_at", "session_open_at", "session_close_at"}:
            _timestamp_field(
                value,
                pointer=pointer,
                collector=collector,
                message=f"{field} must be an explicit UTC timestamp ending in Z",
            )
        elif field == "event_timezone":
            if not isinstance(value, str) or not _IANA_TIMEZONE.fullmatch(value):
                collector.error(
                    SourceHealthCode.FIELD_MALFORMED,
                    pointer,
                    "must be an IANA timezone identifier",
                )
        elif field == "missing_or_conflicting_evidence":
            conflicts = _string_list(
                value,
                pointer=pointer,
                collector=collector,
                nonempty=False,
                message="must be a list of strings",
            )
            if conflicts:
                collector.error(
                    SourceHealthCode.UNRESOLVED_STATE_NOT_EXPLICIT,
                    pointer,
                    "declared missing or conflicting evidence must be resolved before evaluation",
                )
        else:
            _text_field(
                value,
                pointer=pointer,
                collector=collector,
                message=f"{field} must be non-empty text",
            )
    scheduled = context.get("scheduled_event_at")
    if isinstance(scheduled, str):
        try:
            return _timestamp(scheduled, path="/event_context/scheduled_event_at")
        except ReplayEvidenceRejected:
            return None
    return None


def _field_source_refs_findings(
    manifest: Mapping[str, object],
    *,
    collector: _Collector,
    record_ids: set[str],
    records_usable: bool,
) -> None:
    if "field_source_refs" not in manifest:
        return
    refs = manifest["field_source_refs"]
    if not isinstance(refs, Mapping):
        collector.error(SourceHealthCode.FIELD_MALFORMED, "/field_source_refs", "must be an object")
        return
    for field in sorted(_SOURCE_BACKED_CONTEXT_FIELDS):
        pointer = _join("/field_source_refs", field)
        if field not in refs:
            collector.error(
                SourceHealthCode.PROVENANCE_MAPPING_INVALID,
                pointer,
                "source-backed event context requires provenance",
            )
            continue
        values = _evidence_refs(
            refs[field],
            pointer=pointer,
            collector=collector,
            empty_code=SourceHealthCode.PROVENANCE_MAPPING_INVALID,
            empty_message="source-backed event context requires at least one evidence reference",
        )
        if values is None:
            continue
        if records_usable and not set(values) <= record_ids:
            collector.error(
                SourceHealthCode.PROVENANCE_MAPPING_INVALID,
                pointer,
                "field references unknown evidence",
            )
    for field in sorted(set(refs) - _EVENT_CONTEXT_FIELDS):
        collector.error(
            SourceHealthCode.PROVENANCE_MAPPING_INVALID,
            _join("/field_source_refs", field),
            "provenance field is not part of event_context",
        )


def _feature_dependencies_findings(
    manifest: Mapping[str, object],
    *,
    collector: _Collector,
    record_ids: set[str],
    records_usable: bool,
) -> None:
    if "feature_dependencies" not in manifest:
        return
    dependencies = manifest["feature_dependencies"]
    if not isinstance(dependencies, list):
        collector.error(
            SourceHealthCode.FIELD_MALFORMED,
            "/feature_dependencies",
            "must be a list of feature dependencies",
        )
        return
    if not dependencies:
        collector.error(
            SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN,
            "/feature_dependencies",
            "at least one dependency-closed feature is required",
        )
        return
    seen_ids: set[str] = set()
    for index, item in enumerate(dependencies):
        pointer = f"/feature_dependencies/{index}"
        dependency = _shape_findings(
            item, pointer=pointer, fields=_DEPENDENCY_FIELDS, collector=collector
        )
        if dependency is None:
            continue
        feature_id = None
        if "feature_id" in dependency:
            feature_id = _text_field(
                dependency["feature_id"],
                pointer=_join(pointer, "feature_id"),
                collector=collector,
                message="feature_id must be non-empty text",
            )
        if feature_id is not None:
            if feature_id in seen_ids:
                collector.error(
                    SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN,
                    _join(pointer, "feature_id"),
                    "feature IDs must be unique",
                )
            seen_ids.add(feature_id)
        if "source_refs" in dependency:
            values = _evidence_refs(
                dependency["source_refs"],
                pointer=_join(pointer, "source_refs"),
                collector=collector,
                empty_code=SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN,
                empty_message="feature dependency requires at least one source reference",
            )
            if values is None:
                pass
            elif records_usable and not set(values) <= record_ids:
                collector.error(
                    SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN,
                    _join(pointer, "source_refs"),
                    "feature dependency references unknown evidence",
                )
        if (
            "dependency_check" in dependency
            and dependency["dependency_check"] != _DEPENDENCY_ELIGIBLE
        ):
            collector.error(
                SourceHealthCode.DEPENDENCY_MISSING_OR_OPEN,
                _join(pointer, "dependency_check"),
                "feature dependency check is not closed as ELIGIBLE",
            )


def _sec_filing_findings(
    manifest: Mapping[str, object],
    *,
    collector: _Collector,
    decision_cutoff: datetime | None,
) -> None:
    if "sec_filing" not in manifest:
        return
    filing = _shape_findings(
        manifest["sec_filing"],
        pointer="/sec_filing",
        fields=_FILING_FIELDS,
        collector=collector,
    )
    if filing is None:
        return
    status = filing.get("status")
    if "status" in filing and (not isinstance(status, str) or not status):
        collector.error(
            SourceHealthCode.FIELD_MALFORMED, "/sec_filing/status", "must be non-empty text"
        )
        return
    if status == _FILING_UNAVAILABLE:
        for field in _SEC_FILING_AVAILABLE_REVISION_FIELDS:
            if field in filing and filing[field] is not None:
                collector.error(
                    SourceHealthCode.PROVENANCE_CONTRADICTION,
                    _join("/sec_filing", field),
                    "unavailable future filing cannot carry invented provenance",
                )
        if "reason" in filing:
            _text_field(
                filing["reason"],
                pointer="/sec_filing/reason",
                collector=collector,
                message="reason must be non-empty text",
            )
        return
    if "url" in filing:
        _url_findings(filing["url"], pointer="/sec_filing/url", collector=collector)
    accepted_at = None
    if "accepted_at" in filing:
        accepted_at = _timestamp_field(
            filing["accepted_at"],
            pointer="/sec_filing/accepted_at",
            collector=collector,
            code=SourceHealthCode.REVISION_IDENTITY_MISSING,
            message="accepted_at must be an explicit UTC timestamp ending in Z",
        )
    if accepted_at is not None and decision_cutoff is not None and accepted_at > decision_cutoff:
        collector.error(
            SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION,
            "/sec_filing/accepted_at",
            "SEC acceptance is after the decision cutoff",
        )
    if "reason" in filing and filing["reason"] is not None:
        _text_field(
            filing["reason"],
            pointer="/sec_filing/reason",
            collector=collector,
            message="reason must be non-empty text",
        )


def _ordering_findings(
    manifest: Mapping[str, object],
    *,
    collector: _Collector,
    decision_cutoff: datetime | None,
    latest_evidence_at: datetime | None,
    feature_snapshot_at: datetime | None,
    frozen_at: datetime | None,
    scheduled_event_at: datetime | None,
    uppers: list[datetime],
    record_count: int,
) -> None:
    if decision_cutoff is None:
        return
    if scheduled_event_at is not None and decision_cutoff != scheduled_event_at:
        collector.error(
            SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION,
            "/decision_cutoff",
            "decision cutoff must equal the frozen scheduled event instant",
        )
    if feature_snapshot_at is not None and feature_snapshot_at > decision_cutoff:
        collector.error(
            SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION,
            "/feature_snapshot_at",
            "feature snapshot is after the decision cutoff",
        )
    if (
        frozen_at is not None
        and feature_snapshot_at is not None
        and frozen_at != feature_snapshot_at
    ):
        collector.error(
            SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION,
            "/frozen_at",
            "freeze must bind the exact feature snapshot instant",
        )
    if latest_evidence_at is not None and latest_evidence_at > decision_cutoff:
        collector.error(
            SourceHealthCode.CUTOFF_ORDERING_CONTRADICTION,
            "/latest_evidence_at",
            "latest evidence is after the decision cutoff",
        )
    if (
        latest_evidence_at is not None
        and record_count
        and len(uppers) == record_count
        and latest_evidence_at != max(uppers)
    ):
        collector.error(
            SourceHealthCode.PROVENANCE_CONTRADICTION,
            "/latest_evidence_at",
            "must equal the latest conservative source publication bound",
        )


def _classification_findings(manifest: Mapping[str, object], *, collector: _Collector) -> None:
    data_class = manifest.get("data_class")
    if "data_class" in manifest:
        if not isinstance(data_class, str) or not data_class:
            collector.error(
                SourceHealthCode.FIELD_MALFORMED, "/data_class", "must be non-empty text"
            )
        elif data_class != _DATA_CLASS:
            collector.error(
                SourceHealthCode.CLASSIFICATION_MISSING,
                "/data_class",
                f"data class '{data_class}' is not the frozen {_DATA_CLASS} class",
            )
    if "data_qualifiers" in manifest:
        qualifiers = _string_list(
            manifest["data_qualifiers"],
            pointer="/data_qualifiers",
            collector=collector,
            nonempty=True,
            message="must be a non-empty list of qualifier strings",
        )
        if qualifiers is not None:
            missing = sorted(_REQUIRED_QUALIFIERS - set(qualifiers))
            if missing:
                collector.error(
                    SourceHealthCode.CLASSIFICATION_MISSING,
                    "/data_qualifiers",
                    f"required qualifiers are missing: {', '.join(missing)}",
                )
    if "entitlement_note" in manifest:
        _text_field(
            manifest["entitlement_note"],
            pointer="/entitlement_note",
            collector=collector,
            code=SourceHealthCode.CLASSIFICATION_MISSING,
            message="entitlement_note must be non-empty text",
        )
    if "redistribution_status" in manifest:
        status = manifest["redistribution_status"]
        if not isinstance(status, str):
            collector.error(
                SourceHealthCode.FIELD_MALFORMED,
                "/redistribution_status",
                "redistribution status must be text",
            )
        elif status not in _REDISTRIBUTION_VOCABULARY:
            collector.error(
                SourceHealthCode.CLASSIFICATION_MISSING,
                "/redistribution_status",
                "redistribution status is not one of the registered states",
            )
    if "limitations" in manifest:
        _string_list(
            manifest["limitations"],
            pointer="/limitations",
            collector=collector,
            nonempty=True,
            message="must be a non-empty list of limitation strings",
        )


def _context_findings(
    manifest: Mapping[str, object],
    *,
    collector: _Collector,
    event_list: bytes | None,
    selection_rule: bytes | None,
    issuer_primary_indices: list[int],
    records: object,
) -> None:
    if event_list is None and selection_rule is None:
        return
    if event_list is None or selection_rule is None:
        collector.error(
            SourceHealthCode.CONTEXT_INVALID,
            "/event_id",
            "event list and selection rule bytes must be supplied together",
        )
        return
    try:
        _, _, expected_events = _validate_event_list(
            event_list, selection_rule_bytes=selection_rule
        )
    except ReplayEvidenceRejected as error:
        collector.error(
            SourceHealthCode.CONTEXT_INVALID,
            "/event_id",
            f"frozen context failed the replay evidence contract: {error.reason.value}",
        )
        return

    event_id = manifest.get("event_id")
    if not isinstance(event_id, str) or event_id not in expected_events:
        collector.error(
            SourceHealthCode.IDENTITY_MISMATCH,
            "/event_id",
            "event is absent from the frozen event list",
        )
        return
    expected = expected_events[event_id]

    issuer = manifest.get("issuer")
    if issuer != expected["issuer"]:
        collector.error(
            SourceHealthCode.IDENTITY_MISMATCH,
            "/issuer",
            "manifest issuer differs from the frozen list",
        )

    context = manifest.get("event_context")
    if isinstance(context, Mapping):
        for field in sorted(_EVENT_CONTEXT_FIELDS):
            if field in context and field in expected and context[field] != expected[field]:
                collector.error(
                    SourceHealthCode.IDENTITY_MISMATCH,
                    _join("/event_context", field),
                    "event context differs from the frozen list",
                )

    if (
        "event_list_sha256" in manifest
        and manifest["event_list_sha256"] != hashlib.sha256(event_list).hexdigest()
    ):
        collector.error(
            SourceHealthCode.PROVENANCE_CONTRADICTION,
            "/event_list_sha256",
            "manifest is not bound to the supplied event-list bytes",
        )
    if (
        "selection_rule_sha256" in manifest
        and manifest["selection_rule_sha256"] != hashlib.sha256(selection_rule).hexdigest()
    ):
        collector.error(
            SourceHealthCode.PROVENANCE_CONTRADICTION,
            "/selection_rule_sha256",
            "manifest is not bound to the supplied selection-rule bytes",
        )

    if len(issuer_primary_indices) == 1 and isinstance(records, list):
        index = issuer_primary_indices[0]
        record = records[index]
        if isinstance(record, Mapping):
            source_url = record.get("source_url")
            if source_url != expected["issuer_release_url"]:
                collector.error(
                    SourceHealthCode.ISSUER_URL_MISMATCH,
                    f"/records/{index}/source_url",
                    "ISSUER_PRIMARY source must equal the frozen event issuer release URL",
                )


def check_manifest(
    raw: bytes,
    *,
    event_list: bytes | None = None,
    selection_rule: bytes | None = None,
) -> SourceHealthReport:
    """Check one evidence manifest offline and explain every finding.

    Identical manifest bytes always produce byte-identical canonical findings.
    Optional event-list and selection-rule bytes enable frozen-identity cross
    checks; both must be supplied together.
    """

    if type(raw) is not bytes:
        raise TypeError("manifest input must be immutable bytes")
    sha256 = hashlib.sha256(raw).hexdigest()

    try:
        manifest = _decode(raw, path="manifest")
    except ReplayEvidenceRejected as error:
        code = (
            SourceHealthCode.DUPLICATE_KEY
            if error.reason is ReplayEvidenceRejectionReason.DUPLICATE_FIELD
            else SourceHealthCode.PARSE_FAILED
        )
        return _failed_closed(code, "", error.detail, sha256)

    if _contains_lone_surrogate(manifest):
        return _failed_closed(
            SourceHealthCode.PARSE_FAILED,
            "",
            "manifest contains unpaired Unicode surrogate code points",
            sha256,
        )

    schema = manifest.get(_MANIFEST_SCHEMA_FIELD)
    version = manifest.get(_MANIFEST_SCHEMA_VERSION_FIELD)
    if schema != _MANIFEST_SCHEMA or version != _SUPPORTED_SCHEMA_VERSION:
        return _failed_closed(
            SourceHealthCode.UNSUPPORTED_SCHEMA,
            "/" + _MANIFEST_SCHEMA_FIELD,
            "replay evidence requires the data-only evidence manifest v2",
            sha256,
        )

    collector = _Collector()
    _shape_findings(manifest, pointer="", fields=_MANIFEST_FIELDS, collector=collector)

    if "event_id" in manifest:
        _text_field(
            manifest["event_id"],
            pointer="/event_id",
            collector=collector,
            message="event_id must be non-empty text",
        )
    if "issuer" in manifest:
        _text_field(
            manifest["issuer"],
            pointer="/issuer",
            collector=collector,
            message="issuer must be non-empty text",
        )

    decision_cutoff = None
    if "decision_cutoff" in manifest:
        decision_cutoff = _timestamp_field(
            manifest["decision_cutoff"],
            pointer="/decision_cutoff",
            collector=collector,
            message="decision_cutoff must be an explicit UTC timestamp ending in Z",
        )
    latest_evidence_at = None
    if "latest_evidence_at" in manifest:
        latest_evidence_at = _timestamp_field(
            manifest["latest_evidence_at"],
            pointer="/latest_evidence_at",
            collector=collector,
            message="latest_evidence_at must be an explicit UTC timestamp ending in Z",
        )
    feature_snapshot_at = None
    if "feature_snapshot_at" in manifest:
        feature_snapshot_at = _timestamp_field(
            manifest["feature_snapshot_at"],
            pointer="/feature_snapshot_at",
            collector=collector,
            message="feature_snapshot_at must be an explicit UTC timestamp ending in Z",
        )
    frozen_at = None
    if "frozen_at" in manifest:
        frozen_at = _timestamp_field(
            manifest["frozen_at"],
            pointer="/frozen_at",
            collector=collector,
            message="frozen_at must be an explicit UTC timestamp ending in Z",
        )

    for field in ("event_list_sha256", "selection_rule_sha256"):
        if field in manifest:
            _checked(
                _sha256,
                manifest[field],
                pointer=_join("", field),
                collector=collector,
                code=SourceHealthCode.FIELD_MALFORMED,
                message=f"{field} must be a lowercase SHA-256 digest",
            )

    _classification_findings(manifest, collector=collector)
    scheduled_event_at = _event_context_findings(manifest, collector=collector)

    records_usable, record_ids, uppers, issuer_primary_indices = _records_findings(
        manifest, collector=collector, decision_cutoff=decision_cutoff
    )
    record_count = len(manifest["records"]) if records_usable else 0

    _field_source_refs_findings(
        manifest, collector=collector, record_ids=record_ids, records_usable=records_usable
    )
    _feature_dependencies_findings(
        manifest, collector=collector, record_ids=record_ids, records_usable=records_usable
    )
    _sec_filing_findings(manifest, collector=collector, decision_cutoff=decision_cutoff)

    _ordering_findings(
        manifest,
        collector=collector,
        decision_cutoff=decision_cutoff,
        latest_evidence_at=latest_evidence_at,
        feature_snapshot_at=feature_snapshot_at,
        frozen_at=frozen_at,
        scheduled_event_at=scheduled_event_at,
        uppers=uppers,
        record_count=record_count,
    )

    if records_usable and len(issuer_primary_indices) != 1:
        collector.error(
            SourceHealthCode.ISSUER_PRIMARY_CARDINALITY,
            "/records",
            f"expected exactly one ISSUER_PRIMARY record, found {len(issuer_primary_indices)}",
        )

    _context_findings(
        manifest,
        collector=collector,
        event_list=event_list,
        selection_rule=selection_rule,
        issuer_primary_indices=issuer_primary_indices,
        records=manifest.get("records"),
    )

    findings = tuple(collector.findings)
    status = SourceHealthStatus.FINDINGS if findings else SourceHealthStatus.HEALTHY
    return SourceHealthReport(status=status, manifest_sha256=sha256, findings=findings)


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _untrusted_path_report(message: str, sha256: str) -> SourceHealthReport:
    return _failed_closed(SourceHealthCode.UNTRUSTED_PATH, "", message, sha256)


def _reject_path_links(path: Path) -> str | None:
    if any(part == ".." for part in path.parts):
        return "path traversal components are not permitted"
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        current /= part
        try:
            linked = _is_reparse_point(current)
        except OSError as error:
            return str(error)
        if linked:
            return "symlink or junction path components are not permitted"
    return None


def check_path(
    path: os.PathLike[str] | str,
    *,
    root: os.PathLike[str] | str | None = None,
    event_list: bytes | None = None,
    selection_rule: bytes | None = None,
) -> SourceHealthReport:
    """Check a manifest file after fail-closed untrusted-path screening."""

    candidate = Path(path)
    rejection = _reject_path_links(candidate)
    if rejection is not None:
        return _untrusted_path_report(rejection, "")
    if root is not None:
        resolved_root = Path(root)
        rejection = _reject_path_links(resolved_root)
        if rejection is not None:
            return _untrusted_path_report(rejection, "")
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            return _untrusted_path_report(
                "manifest paths must remain relative to the supplied root", ""
            )
        candidate = resolved_root / candidate

    if _is_reparse_point(candidate):
        return _untrusted_path_report("symlink or junction inputs are not permitted", "")
    try:
        with candidate.open("rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return _untrusted_path_report("input must be a regular file", "")
            raw = handle.read()
    except FileNotFoundError:
        return _untrusted_path_report("input file does not exist", "")
    except IsADirectoryError:
        return _untrusted_path_report("input must be a regular file", "")
    except OSError as error:
        return _untrusted_path_report(str(error), "")
    return check_manifest(raw, event_list=event_list, selection_rule=selection_rule)
