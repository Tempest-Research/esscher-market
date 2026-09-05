"""Fail-closed validation for frozen data-only earnings replay evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
_IANA_TIMEZONE = re.compile(r"^[A-Za-z]+(?:/[A-Za-z_+-]+)+$")
_EVENT_CONTEXT_FIELDS = frozenset(
    {
        "ticker",
        "sector",
        "event_timezone",
        "scheduled_event_at",
        "event_category",
        "market_proxy",
        "sector_proxy",
        "session_id",
        "session_open_at",
        "session_close_at",
        "observation_type",
        "entry_session_policy",
        "missing_or_conflicting_evidence",
        "inclusion_or_exclusion_reason",
    }
)
_SOURCE_BACKED_CONTEXT_FIELDS = frozenset(
    {
        "ticker",
        "event_timezone",
        "scheduled_event_at",
        "event_category",
        "session_id",
        "session_open_at",
        "session_close_at",
        "observation_type",
    }
)
_EVENT_LIST_FIELDS = _EVENT_CONTEXT_FIELDS | frozenset(
    {"event_id", "issuer", "timing_bucket", "issuer_release_url"}
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "event_id",
        "issuer",
        "decision_cutoff",
        "latest_evidence_at",
        "feature_snapshot_at",
        "frozen_at",
        "data_class",
        "data_qualifiers",
        "event_list_sha256",
        "selection_rule_sha256",
        "event_context",
        "records",
        "field_source_refs",
        "feature_dependencies",
        "sec_filing",
        "entitlement_note",
        "redistribution_status",
        "limitations",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "evidence_id",
        "source_kind",
        "source_url",
        "publisher",
        "published_at",
        "published_at_interval",
        "published_at_type",
        "published_at_precision",
        "retrieved_at",
        "content_sha256",
        "field_status",
        "entitlement_note",
        "redistribution_status",
        "limitations",
    }
)


class ReplayEvidenceRejectionReason(StrEnum):
    """Stable machine-readable reasons for rejecting a replay evidence set."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    HASH_MISMATCH = "HASH_MISMATCH"
    DUPLICATE_EVENT_ID = "DUPLICATE_EVENT_ID"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    POINT_IN_TIME_VIOLATION = "POINT_IN_TIME_VIOLATION"
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    CLAIM_BOUNDARY_MISMATCH = "CLAIM_BOUNDARY_MISMATCH"
    SELECTION_RULE_VIOLATION = "SELECTION_RULE_VIOLATION"


class ReplayEvidenceRejected(ValueError):
    """A deterministic validation failure for data-only replay evidence."""

    def __init__(
        self,
        reason: ReplayEvidenceRejectionReason,
        path: str,
        detail: str,
    ) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


@dataclass(frozen=True, slots=True)
class ValidatedReplayEvidence:
    """Identity of a valid data-only event manifest."""

    event_id: str
    manifest_sha256: str
    permit_eligible: bool = False


class _DuplicateFieldError(ValueError):
    pass


def _reject(reason: ReplayEvidenceRejectionReason, path: str, detail: str) -> None:
    raise ReplayEvidenceRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _decode(raw: bytes, *, path: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(
            ReplayEvidenceRejectionReason.INVALID_DOCUMENT,
            path,
            "contract inputs must be immutable bytes",
        )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except _DuplicateFieldError as error:
        _reject(
            ReplayEvidenceRejectionReason.DUPLICATE_FIELD,
            path,
            f"duplicate field {error}",
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(ReplayEvidenceRejectionReason.INVALID_DOCUMENT, path, str(error))
    if not isinstance(value, Mapping):
        _reject(ReplayEvidenceRejectionReason.INVALID_DOCUMENT, path, "root must be an object")
    return value


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(ReplayEvidenceRejectionReason.INVALID_DOCUMENT, path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(
            ReplayEvidenceRejectionReason.MISSING_FIELD,
            f"{path}.{missing[0]}",
            "required field is missing",
        )
    unknown = sorted(keys - fields)
    if unknown:
        _reject(
            ReplayEvidenceRejectionReason.UNKNOWN_FIELD,
            f"{path}.{unknown[0]}",
            "field is not part of the frozen schema",
        )
    return value


def _text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(ReplayEvidenceRejectionReason.INVALID_DOCUMENT, path, "must be non-empty text")
    return value


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        _reject(
            ReplayEvidenceRejectionReason.INVALID_DOCUMENT,
            path,
            "must be an explicit UTC timestamp ending in Z",
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as error:
        _reject(ReplayEvidenceRejectionReason.INVALID_DOCUMENT, path, str(error))


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _reject(ReplayEvidenceRejectionReason.INVALID_DOCUMENT, path, "must be lowercase SHA-256")
    return value


def _https_url(value: object, *, path: str) -> str:
    url = _text(value, path=path)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        _reject(ReplayEvidenceRejectionReason.INVALID_DOCUMENT, path, "must be a public HTTPS URL")
    return url


def _text_list(
    value: object,
    *,
    path: str,
    nonempty: bool = True,
    unique: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        _reject(ReplayEvidenceRejectionReason.INVALID_DOCUMENT, path, "must be a list")
    result = tuple(_text(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    if unique and len(set(result)) != len(result):
        _reject(ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH, path, "values must be unique")
    return result


def _validate_selection_rule(raw: bytes) -> Mapping[str, object]:
    rule = _strict_object(
        _decode(raw, path="selection_rule"),
        path="selection_rule",
        fields=frozenset(
            {
                "schema",
                "schema_version",
                "rule_id",
                "frozen_at",
                "decision_cutoff_policy",
                "criteria",
                "allowed_timing_buckets",
                "official_session_source",
                "claim_boundary",
            }
        ),
    )
    if rule["schema"] != "ringdown.earnings_replay_selection_rule" or rule["schema_version"] != 1:
        _reject(
            ReplayEvidenceRejectionReason.UNSUPPORTED_SCHEMA,
            "selection_rule",
            "unsupported selection-rule schema or version",
        )
    _text(rule["rule_id"], path="selection_rule.rule_id")
    frozen_at = _timestamp(rule["frozen_at"], path="selection_rule.frozen_at")
    if rule["decision_cutoff_policy"] != "SCHEDULED_EVENT_AT":
        _reject(
            ReplayEvidenceRejectionReason.SELECTION_RULE_VIOLATION,
            "selection_rule.decision_cutoff_policy",
            "only the frozen scheduled-event cutoff is supported",
        )
    allowed_timing_buckets = _text_list(
        rule["allowed_timing_buckets"], path="selection_rule.allowed_timing_buckets"
    )
    if allowed_timing_buckets != ("BEFORE_OPEN", "AFTER_CLOSE"):
        _reject(
            ReplayEvidenceRejectionReason.SELECTION_RULE_VIOLATION,
            "selection_rule.allowed_timing_buckets",
            "frozen replay selection supports pre-open and after-close events only",
        )
    claim_boundary = set(_text_list(rule["claim_boundary"], path="selection_rule.claim_boundary"))
    if claim_boundary != {
        "POINT_IN_TIME_EVENT_PANEL_CANDIDATE",
        "NOT_ALPHA_EVIDENCE",
        "NO_OUTCOME_VALUES",
        "NO_BROKER_EXECUTION",
    }:
        _reject(
            ReplayEvidenceRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "selection_rule.claim_boundary",
            "the frozen data-only claim boundary cannot be weakened or extended",
        )
    criteria = _strict_object(
        rule["criteria"],
        path="selection_rule.criteria",
        fields=frozenset(
            {
                "issuer_primary_schedule_required",
                "exact_scheduled_event_instant_required",
                "source_retrieved_no_later_than_decision_cutoff",
                "regular_us_equity_session_required",
                "post_cutoff_source_paths_forbidden_at_freeze",
                "outcome_values_forbidden_at_freeze",
                "minimum_sector_count",
                "minimum_timing_bucket_count",
            }
        ),
    )
    for criterion in (
        "issuer_primary_schedule_required",
        "exact_scheduled_event_instant_required",
        "source_retrieved_no_later_than_decision_cutoff",
        "regular_us_equity_session_required",
        "post_cutoff_source_paths_forbidden_at_freeze",
        "outcome_values_forbidden_at_freeze",
    ):
        if criteria[criterion] is not True:
            _reject(
                ReplayEvidenceRejectionReason.SELECTION_RULE_VIOLATION,
                f"selection_rule.criteria.{criterion}",
                "frozen safety criteria must remain enabled",
            )
    if criteria["minimum_sector_count"] != 2 or criteria["minimum_timing_bucket_count"] != 2:
        _reject(
            ReplayEvidenceRejectionReason.SELECTION_RULE_VIOLATION,
            "selection_rule.criteria",
            "frozen diversity minima must remain two sectors and two timing buckets",
        )
    session_source = _strict_object(
        rule["official_session_source"],
        path="selection_rule.official_session_source",
        fields=frozenset(
            {"url", "publisher", "retrieved_at", "content_sha256", "redistribution_status"}
        ),
    )
    _https_url(session_source["url"], path="selection_rule.official_session_source.url")
    _text(session_source["publisher"], path="selection_rule.official_session_source.publisher")
    if (
        _timestamp(
            session_source["retrieved_at"],
            path="selection_rule.official_session_source.retrieved_at",
        )
        > frozen_at
    ):
        _reject(
            ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION,
            "selection_rule.official_session_source.retrieved_at",
            "session evidence must be retrieved no later than the rule freeze",
        )
    _sha256(
        session_source["content_sha256"],
        path="selection_rule.official_session_source.content_sha256",
    )
    if session_source["redistribution_status"] != "METADATA_AND_HASH_ONLY":
        _reject(
            ReplayEvidenceRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "selection_rule.official_session_source.redistribution_status",
            "raw exchange source bytes are not redistributed",
        )
    return rule


def _validate_event_list(
    raw: bytes,
    *,
    selection_rule_bytes: bytes,
) -> tuple[Mapping[str, object], tuple[str, ...], dict[str, Mapping[str, object]]]:
    payload = _strict_object(
        _decode(raw, path="event_list"),
        path="event_list",
        fields=frozenset(
            {
                "schema",
                "schema_version",
                "list_id",
                "frozen_at",
                "selection_rule_sha256",
                "event_ids",
                "events",
                "post_cutoff_paths",
                "outcome_fields",
            }
        ),
    )
    if payload["schema"] != "ringdown.frozen_earnings_event_list" or payload["schema_version"] != 1:
        _reject(
            ReplayEvidenceRejectionReason.UNSUPPORTED_SCHEMA,
            "event_list",
            "unsupported event-list schema or version",
        )
    selection_rule = _validate_selection_rule(selection_rule_bytes)
    event_list_frozen_at = _timestamp(payload["frozen_at"], path="event_list.frozen_at")
    selection_rule_frozen_at = _timestamp(
        selection_rule["frozen_at"], path="selection_rule.frozen_at"
    )
    if event_list_frozen_at != selection_rule_frozen_at:
        _reject(
            ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION,
            "event_list.frozen_at",
            "event-list freeze must equal the ex-ante selection-rule freeze",
        )
    expected_rule_sha = hashlib.sha256(selection_rule_bytes).hexdigest()
    if (
        _sha256(payload["selection_rule_sha256"], path="event_list.selection_rule_sha256")
        != expected_rule_sha
    ):
        _reject(
            ReplayEvidenceRejectionReason.HASH_MISMATCH,
            "event_list.selection_rule_sha256",
            "event list is not bound to the supplied selection-rule bytes",
        )
    event_ids = _text_list(payload["event_ids"], path="event_list.event_ids", unique=False)
    for index, event_id in enumerate(event_ids):
        if event_id in event_ids[:index]:
            _reject(
                ReplayEvidenceRejectionReason.DUPLICATE_EVENT_ID,
                f"event_list.event_ids[{index}]",
                "event IDs must be unique",
            )
    if payload["post_cutoff_paths"] != [] or payload["outcome_fields"] != []:
        _reject(
            ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION,
            "event_list",
            "freeze-stage event lists cannot contain post-cutoff paths or outcomes",
        )
    events = payload["events"]
    if not isinstance(events, list) or len(events) != len(event_ids):
        _reject(
            ReplayEvidenceRejectionReason.IDENTITY_MISMATCH,
            "event_list.events",
            "events must match event_ids one-for-one",
        )
    by_id: dict[str, Mapping[str, object]] = {}
    sectors: set[str] = set()
    timing_buckets: set[str] = set()
    for index, item in enumerate(events):
        event = _strict_object(item, path=f"event_list.events[{index}]", fields=_EVENT_LIST_FIELDS)
        event_id = _text(event["event_id"], path=f"event_list.events[{index}].event_id")
        if event_id != event_ids[index] or event_id in by_id:
            _reject(
                ReplayEvidenceRejectionReason.DUPLICATE_EVENT_ID,
                f"event_list.events[{index}].event_id",
                "event order and IDs must match event_ids exactly",
            )
        _https_url(
            event["issuer_release_url"], path=f"event_list.events[{index}].issuer_release_url"
        )
        by_id[event_id] = event
        sectors.add(_text(event["sector"], path=f"event_list.events[{index}].sector"))
        timing_buckets.add(
            _text(event["timing_bucket"], path=f"event_list.events[{index}].timing_bucket")
        )
        conflicts = _text_list(
            event["missing_or_conflicting_evidence"],
            path=f"event_list.events[{index}].missing_or_conflicting_evidence",
            nonempty=False,
        )
        if conflicts:
            _reject(
                ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
                f"event_list.events[{index}].missing_or_conflicting_evidence",
                "events with material missing or conflicting evidence must be excluded",
            )
        _text(
            event["inclusion_or_exclusion_reason"],
            path=f"event_list.events[{index}].inclusion_or_exclusion_reason",
        )
    criteria = _strict_object(
        selection_rule["criteria"],
        path="selection_rule.criteria",
        fields=frozenset(
            {
                "issuer_primary_schedule_required",
                "exact_scheduled_event_instant_required",
                "source_retrieved_no_later_than_decision_cutoff",
                "regular_us_equity_session_required",
                "post_cutoff_source_paths_forbidden_at_freeze",
                "outcome_values_forbidden_at_freeze",
                "minimum_sector_count",
                "minimum_timing_bucket_count",
            }
        ),
    )
    minimum_sector_count = criteria["minimum_sector_count"]
    minimum_timing_bucket_count = criteria["minimum_timing_bucket_count"]
    if not isinstance(minimum_sector_count, int) or not isinstance(
        minimum_timing_bucket_count, int
    ):
        _reject(
            ReplayEvidenceRejectionReason.INVALID_DOCUMENT,
            "selection_rule.criteria",
            "diversity minima must be integers",
        )
    if len(sectors) < minimum_sector_count or len(timing_buckets) < minimum_timing_bucket_count:
        _reject(
            ReplayEvidenceRejectionReason.SELECTION_RULE_VIOLATION,
            "event_list.events",
            "frozen list does not meet sector and timing diversity minima",
        )
    return payload, event_ids, by_id


def _published_upper_bound(record: Mapping[str, object], *, path: str) -> datetime:
    published_at = record["published_at"]
    interval = record["published_at_interval"]
    if published_at is not None:
        if interval is not None or record["published_at_precision"] != "SECOND":
            _reject(
                ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
                path,
                "exact publication time cannot also carry a date interval",
            )
        return _timestamp(published_at, path=f"{path}.published_at")
    window = _strict_object(
        interval,
        path=f"{path}.published_at_interval",
        fields=frozenset({"start", "end"}),
    )
    if record["published_at_precision"] != "DATE_INTERVAL":
        _reject(
            ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
            f"{path}.published_at",
            "missing exact publication time requires a conservative date interval",
        )
    start = _timestamp(window["start"], path=f"{path}.published_at_interval.start")
    end = _timestamp(window["end"], path=f"{path}.published_at_interval.end")
    if start > end:
        _reject(
            ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
            f"{path}.published_at_interval",
            "publication interval is inverted",
        )
    return end


def _validate_manifest(
    raw: bytes,
    *,
    index: int,
    event_list_sha256: str,
    selection_rule_sha256: str,
    expected_event: Mapping[str, object],
    ex_ante_freeze_at: datetime,
) -> ValidatedReplayEvidence:
    path = f"manifests[{index}]"
    manifest = _strict_object(_decode(raw, path=path), path=path, fields=_MANIFEST_FIELDS)
    if (
        manifest["schema"] != "ringdown.point_in_time_evidence_manifest"
        or manifest["schema_version"] != 2
    ):
        _reject(
            ReplayEvidenceRejectionReason.UNSUPPORTED_SCHEMA,
            path,
            "replay evidence requires the data-only evidence manifest v2",
        )
    event_id = _text(manifest["event_id"], path=f"{path}.event_id")
    if manifest["issuer"] != expected_event["issuer"] or event_id != expected_event["event_id"]:
        _reject(
            ReplayEvidenceRejectionReason.IDENTITY_MISMATCH,
            path,
            "manifest issuer or event ID differs from the frozen list",
        )
    if (
        _sha256(manifest["event_list_sha256"], path=f"{path}.event_list_sha256")
        != event_list_sha256
    ):
        _reject(
            ReplayEvidenceRejectionReason.HASH_MISMATCH,
            f"{path}.event_list_sha256",
            "manifest is not bound to the supplied event-list bytes",
        )
    if (
        _sha256(manifest["selection_rule_sha256"], path=f"{path}.selection_rule_sha256")
        != selection_rule_sha256
    ):
        _reject(
            ReplayEvidenceRejectionReason.HASH_MISMATCH,
            f"{path}.selection_rule_sha256",
            "manifest is not bound to the supplied selection-rule bytes",
        )
    if manifest["data_class"] != "POINT_IN_TIME_EVENT_PANEL":
        _reject(
            ReplayEvidenceRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.data_class",
            "replay manifests must remain point-in-time panel candidates",
        )
    qualifiers = set(_text_list(manifest["data_qualifiers"], path=f"{path}.data_qualifiers"))
    required = {"INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE", "NO_OUTCOME_VALUES", "NO_BROKER_EXECUTION"}
    if not required <= qualifiers:
        _reject(
            ReplayEvidenceRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.data_qualifiers",
            "data-only and non-alpha limitations must stay explicit",
        )
    decision_cutoff = _timestamp(manifest["decision_cutoff"], path=f"{path}.decision_cutoff")
    latest_evidence_at = _timestamp(
        manifest["latest_evidence_at"], path=f"{path}.latest_evidence_at"
    )
    feature_snapshot_at = _timestamp(
        manifest["feature_snapshot_at"], path=f"{path}.feature_snapshot_at"
    )
    frozen_at = _timestamp(manifest["frozen_at"], path=f"{path}.frozen_at")
    context = _strict_object(
        manifest["event_context"], path=f"{path}.event_context", fields=_EVENT_CONTEXT_FIELDS
    )
    for field in _EVENT_CONTEXT_FIELDS:
        if context[field] != expected_event[field]:
            _reject(
                ReplayEvidenceRejectionReason.IDENTITY_MISMATCH,
                f"{path}.event_context.{field}",
                "event context differs from the frozen list",
            )
    if decision_cutoff != _timestamp(
        context["scheduled_event_at"], path=f"{path}.event_context.scheduled_event_at"
    ):
        _reject(
            ReplayEvidenceRejectionReason.IDENTITY_MISMATCH,
            f"{path}.decision_cutoff",
            "decision cutoff must equal the frozen scheduled event instant",
        )
    timezone_name = _text(context["event_timezone"], path=f"{path}.event_context.event_timezone")
    if not _IANA_TIMEZONE.fullmatch(timezone_name):
        _reject(
            ReplayEvidenceRejectionReason.INVALID_DOCUMENT,
            f"{path}.event_context.event_timezone",
            "must be an IANA timezone identifier",
        )
    session_open = _timestamp(
        context["session_open_at"], path=f"{path}.event_context.session_open_at"
    )
    session_close = _timestamp(
        context["session_close_at"], path=f"{path}.event_context.session_close_at"
    )
    expected_bucket = "BEFORE_OPEN" if decision_cutoff < session_open else "AFTER_CLOSE"
    if decision_cutoff == session_open or (
        decision_cutoff <= session_close and decision_cutoff > session_open
    ):
        _reject(
            ReplayEvidenceRejectionReason.SELECTION_RULE_VIOLATION,
            f"{path}.event_context.scheduled_event_at",
            "freeze rule excludes events during the regular session",
        )
    if expected_event["timing_bucket"] != expected_bucket:
        _reject(
            ReplayEvidenceRejectionReason.IDENTITY_MISMATCH,
            f"{path}.event_context.scheduled_event_at",
            "event timing bucket does not match the frozen session window",
        )
    records = manifest["records"]
    if not isinstance(records, list) or not records:
        _reject(
            ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
            f"{path}.records",
            "at least one source record is required",
        )
    record_ids: set[str] = set()
    upper_bounds: list[datetime] = []
    issuer_primary_records: list[tuple[int, Mapping[str, object]]] = []
    for record_index, item in enumerate(records):
        record_path = f"{path}.records[{record_index}]"
        record = _strict_object(item, path=record_path, fields=_RECORD_FIELDS)
        evidence_id = _text(record["evidence_id"], path=f"{record_path}.evidence_id")
        if evidence_id in record_ids:
            _reject(
                ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
                f"{record_path}.evidence_id",
                "evidence IDs must be unique",
            )
        record_ids.add(evidence_id)
        _https_url(record["source_url"], path=f"{record_path}.source_url")
        _text(record["publisher"], path=f"{record_path}.publisher")
        _sha256(record["content_sha256"], path=f"{record_path}.content_sha256")
        published_upper = _published_upper_bound(record, path=record_path)
        retrieved_at = _timestamp(record["retrieved_at"], path=f"{record_path}.retrieved_at")
        if published_upper > decision_cutoff or retrieved_at > decision_cutoff:
            violating_field = (
                "published_at" if published_upper > decision_cutoff else "retrieved_at"
            )
            _reject(
                ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION,
                f"{record_path}.{violating_field}",
                "evidence timestamp is after the event decision cutoff",
            )
        if record["field_status"] != "PRESENT":
            _reject(
                ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
                f"{record_path}.field_status",
                "material replay evidence must be present and conflict-free",
            )
        if record["redistribution_status"] != "METADATA_AND_HASH_ONLY":
            _reject(
                ReplayEvidenceRejectionReason.CLAIM_BOUNDARY_MISMATCH,
                f"{record_path}.redistribution_status",
                "raw source bytes are not redistributed",
            )
        if record["source_kind"] == "ISSUER_PRIMARY":
            issuer_primary_records.append((record_index, record))
        upper_bounds.append(published_upper)
    if len(issuer_primary_records) != 1:
        _reject(
            ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
            f"{path}.records",
            "exactly one ISSUER_PRIMARY record is required",
        )
    issuer_index, issuer_record = issuer_primary_records[0]
    if issuer_record["source_url"] != expected_event["issuer_release_url"]:
        _reject(
            ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
            f"{path}.records[{issuer_index}].source_url",
            "ISSUER_PRIMARY source must equal the frozen event issuer release URL",
        )
    if latest_evidence_at != max(upper_bounds):
        _reject(
            ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
            f"{path}.latest_evidence_at",
            "must equal the latest conservative source publication bound",
        )
    if feature_snapshot_at != ex_ante_freeze_at or frozen_at != ex_ante_freeze_at:
        _reject(
            ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION,
            f"{path}.feature_snapshot_at",
            "snapshot and freeze timestamps must equal the ex-ante event-list "
            "and selection-rule freeze",
        )
    if feature_snapshot_at != frozen_at:
        _reject(
            ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
            f"{path}.frozen_at",
            "freeze must bind the exact feature snapshot instant",
        )
    field_refs = manifest["field_source_refs"]
    if not isinstance(field_refs, Mapping):
        _reject(
            ReplayEvidenceRejectionReason.INVALID_DOCUMENT,
            f"{path}.field_source_refs",
            "must be an object",
        )
    for field in _SOURCE_BACKED_CONTEXT_FIELDS:
        if field not in field_refs:
            _reject(
                ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
                f"{path}.field_source_refs.{field}",
                "source-backed event context requires provenance",
            )
        refs = _text_list(field_refs[field], path=f"{path}.field_source_refs.{field}")
        if not set(refs) <= record_ids:
            _reject(
                ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.field_source_refs.{field}",
                "field references unknown evidence",
            )
    unknown_ref_fields = set(field_refs) - _EVENT_CONTEXT_FIELDS
    if unknown_ref_fields:
        field = sorted(unknown_ref_fields)[0]
        _reject(
            ReplayEvidenceRejectionReason.UNKNOWN_FIELD,
            f"{path}.field_source_refs.{field}",
            "provenance field is not part of event_context",
        )
    dependencies = manifest["feature_dependencies"]
    if not isinstance(dependencies, list) or not dependencies:
        _reject(
            ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
            f"{path}.feature_dependencies",
            "at least one dependency-closed feature is required",
        )
    dependency_ids: set[str] = set()
    for dependency_index, item in enumerate(dependencies):
        dependency_path = f"{path}.feature_dependencies[{dependency_index}]"
        dependency = _strict_object(
            item,
            path=dependency_path,
            fields=frozenset({"feature_id", "source_refs", "dependency_check"}),
        )
        feature_id = _text(dependency["feature_id"], path=f"{dependency_path}.feature_id")
        if feature_id in dependency_ids:
            _reject(
                ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
                f"{dependency_path}.feature_id",
                "feature IDs must be unique",
            )
        dependency_ids.add(feature_id)
        refs = _text_list(dependency["source_refs"], path=f"{dependency_path}.source_refs")
        if not set(refs) <= record_ids or dependency["dependency_check"] != "ELIGIBLE":
            _reject(
                ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
                dependency_path,
                "feature dependencies must resolve to eligible source records",
            )
    sec_filing = _strict_object(
        manifest["sec_filing"],
        path=f"{path}.sec_filing",
        fields=frozenset({"url", "accepted_at", "status", "reason"}),
    )
    if sec_filing["status"] == "NOT_YET_AVAILABLE":
        if sec_filing["url"] is not None or sec_filing["accepted_at"] is not None:
            _reject(
                ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.sec_filing",
                "unavailable future filing cannot carry invented provenance",
            )
        _text(sec_filing["reason"], path=f"{path}.sec_filing.reason")
    else:
        _https_url(sec_filing["url"], path=f"{path}.sec_filing.url")
        if (
            _timestamp(sec_filing["accepted_at"], path=f"{path}.sec_filing.accepted_at")
            > decision_cutoff
        ):
            _reject(
                ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION,
                f"{path}.sec_filing.accepted_at",
                "SEC acceptance is after the cutoff",
            )
    _text(manifest["entitlement_note"], path=f"{path}.entitlement_note")
    if manifest["redistribution_status"] != "METADATA_AND_HASH_ONLY":
        _reject(
            ReplayEvidenceRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.redistribution_status",
            "raw source bytes are not redistributed",
        )
    _text_list(manifest["limitations"], path=f"{path}.limitations")
    return ValidatedReplayEvidence(
        event_id=event_id, manifest_sha256=hashlib.sha256(raw).hexdigest()
    )


def validate_replay_selection_rule(selection_rule_bytes: bytes) -> Mapping[str, object]:
    """Expose the frozen replay selection-rule validation for lane reuse."""

    return _validate_selection_rule(selection_rule_bytes)


def validate_replay_event_list(
    event_list_bytes: bytes,
    selection_rule_bytes: bytes,
) -> tuple[Mapping[str, object], tuple[str, ...], dict[str, Mapping[str, object]]]:
    """Expose the frozen replay event-list validation for lane reuse."""

    return _validate_event_list(event_list_bytes, selection_rule_bytes=selection_rule_bytes)


def validate_replay_evidence_set(
    event_list_bytes: bytes,
    selection_rule_bytes: bytes,
    manifest_bytes: Sequence[bytes],
) -> tuple[ValidatedReplayEvidence, ...]:
    """Validate exact frozen event, rule, and v2 evidence bytes as one data-only set."""

    _validate_selection_rule(selection_rule_bytes)
    event_list_payload, event_ids, expected_events = _validate_event_list(
        event_list_bytes,
        selection_rule_bytes=selection_rule_bytes,
    )
    ex_ante_freeze_at = _timestamp(event_list_payload["frozen_at"], path="event_list.frozen_at")
    event_list_sha256 = hashlib.sha256(event_list_bytes).hexdigest()
    selection_rule_sha256 = hashlib.sha256(selection_rule_bytes).hexdigest()
    by_id: dict[str, ValidatedReplayEvidence] = {}
    for index, raw in enumerate(manifest_bytes):
        candidate = _decode(raw, path=f"manifests[{index}]")
        event_id = _text(candidate.get("event_id"), path=f"manifests[{index}].event_id")
        if event_id in by_id:
            _reject(
                ReplayEvidenceRejectionReason.DUPLICATE_EVENT_ID,
                f"manifests[{index}].event_id",
                "event manifests must be unique",
            )
        expected = expected_events.get(event_id)
        if expected is None:
            _reject(
                ReplayEvidenceRejectionReason.IDENTITY_MISMATCH,
                f"manifests[{index}].event_id",
                "event is absent from the frozen event list",
            )
        by_id[event_id] = _validate_manifest(
            raw,
            index=index,
            event_list_sha256=event_list_sha256,
            selection_rule_sha256=selection_rule_sha256,
            expected_event=expected,
            ex_ante_freeze_at=ex_ante_freeze_at,
        )
    if set(by_id) != set(event_ids):
        missing = next(event_id for event_id in event_ids if event_id not in by_id)
        _reject(
            ReplayEvidenceRejectionReason.MISSING_FIELD,
            f"manifests.{missing}",
            "every frozen event requires one v2 evidence manifest",
        )
    return tuple(by_id[event_id] for event_id in event_ids)
