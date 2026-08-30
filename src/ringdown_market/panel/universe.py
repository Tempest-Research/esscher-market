"""Fail-closed validation for historical point-in-time panel universes.

Historical evidence manifests describe events whose decision cutoffs precede
the panel freeze. Retrieval therefore postdates the cutoff; validity comes from
an independently preserved historical version (an EDGAR-accessioned document)
whose content hash anchors the exact bytes, per the source and claim policy.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time

from ..contracts.replay_evidence import (
    ReplayEvidenceRejected,
    _decode,
    _reject,
    _sha256,
    _strict_object,
    _text,
    _text_list,
    _timestamp,
    validate_replay_event_list,
    validate_replay_selection_rule,
)
from .manifest import DATA_CLASS_REAL, PanelRejected, PanelRejectionReason

HISTORICAL_EVIDENCE_SCHEMA = "ringdown.historical_evidence_manifest"

_SOURCE_BACKED_CONTEXT_FIELDS = frozenset(
    {
        "ticker",
        "event_timezone",
        "scheduled_event_at",
        "event_category",
        "observation_type",
    }
)
_PROTOCOL_CONTEXT_FIELDS = frozenset(
    {
        "sector",
        "market_proxy",
        "sector_proxy",
        "session_id",
        "session_open_at",
        "session_close_at",
    }
)
_EVENT_CONTEXT_FIELDS = (
    _SOURCE_BACKED_CONTEXT_FIELDS
    | _PROTOCOL_CONTEXT_FIELDS
    | frozenset(
        {
            "entry_session_policy",
            "missing_or_conflicting_evidence",
            "inclusion_or_exclusion_reason",
        }
    )
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
        "preservation",
    }
)
_PRESERVATION_FIELDS = frozenset({"kind", "accession_number", "accepted_at", "url"})
_SESSION_OPEN = time(13, 30, 0, tzinfo=UTC)
_SESSION_CLOSE = time(20, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ValidatedHistoricalEvidence:
    """Identity of a valid historical evidence manifest."""

    event_id: str
    manifest_sha256: str
    permit_eligible: bool = False


def _https_url(value: object, *, path: str) -> str:
    from urllib.parse import urlsplit

    url = _text(value, path=path)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "must be a public HTTPS URL")
    return url


def _published_upper_bound(record: Mapping[str, object], *, path: str) -> datetime:
    published_at = record["published_at"]
    interval = record["published_at_interval"]
    if published_at is not None:
        if interval is not None or record["published_at_precision"] != "SECOND":
            _reject(
                PanelRejectionReason.PROVENANCE_MISMATCH,
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
            PanelRejectionReason.MISSING_PROVENANCE,
            f"{path}.published_at",
            "missing exact publication time requires a conservative date interval",
        )
    start = _timestamp(window["start"], path=f"{path}.published_at_interval.start")
    end = _timestamp(window["end"], path=f"{path}.published_at_interval.end")
    if start > end:
        _reject(
            PanelRejectionReason.PROVENANCE_MISMATCH,
            f"{path}.published_at_interval",
            "publication interval is inverted",
        )
    return end


def _validate_preservation(
    record: Mapping[str, object],
    *,
    path: str,
    decision_cutoff: datetime,
) -> None:
    preservation = _strict_object(
        record["preservation"], path=f"{path}.preservation", fields=_PRESERVATION_FIELDS
    )
    if preservation["kind"] != "EDGAR_ACCESSIONED":
        _reject(
            PanelRejectionReason.MISSING_PROVENANCE,
            f"{path}.preservation.kind",
            "historical evidence requires an EDGAR-accessioned preservation anchor",
        )
    _text(preservation["accession_number"], path=f"{path}.preservation.accession_number")
    accepted_at = _timestamp(preservation["accepted_at"], path=f"{path}.preservation.accepted_at")
    if accepted_at > decision_cutoff:
        _reject(
            PanelRejectionReason.POINT_IN_TIME_VIOLATION,
            f"{path}.preservation.accepted_at",
            "preserved document was accessioned after the decision cutoff",
        )
    _https_url(preservation["url"], path=f"{path}.preservation.url")


def _validate_record(
    item: object,
    *,
    path: str,
    decision_cutoff: datetime,
    required_kind: str,
) -> tuple[str, datetime]:
    record = _strict_object(item, path=path, fields=_RECORD_FIELDS)
    evidence_id = _text(record["evidence_id"], path=f"{path}.evidence_id")
    if record["source_kind"] != required_kind:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            f"{path}.source_kind",
            f"record must be {required_kind}",
        )
    _https_url(record["source_url"], path=f"{path}.source_url")
    _text(record["publisher"], path=f"{path}.publisher")
    _sha256(record["content_sha256"], path=f"{path}.content_sha256")
    _timestamp(record["retrieved_at"], path=f"{path}.retrieved_at")
    if record["field_status"] != "PRESENT":
        _reject(
            PanelRejectionReason.MISSING_PROVENANCE,
            f"{path}.field_status",
            "material historical evidence must be present and conflict-free",
        )
    if record["redistribution_status"] != "METADATA_AND_HASH_ONLY":
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.redistribution_status",
            "raw source bytes are not redistributed",
        )
    _text_list(record["limitations"], path=f"{path}.limitations")
    _validate_preservation(record, path=path, decision_cutoff=decision_cutoff)
    if required_kind == "SEC_OFFICIAL":
        published_at = record["published_at"]
        interval = record["published_at_interval"]
        accepted_at = _timestamp(
            record["preservation"]["accepted_at"], path=f"{path}.preservation.accepted_at"
        )
        if interval is not None or record["published_at_precision"] != "SECOND":
            _reject(
                PanelRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.published_at",
                "EDGAR dissemination is exact at acceptance",
            )
        if _timestamp(published_at, path=f"{path}.published_at") != accepted_at:
            _reject(
                PanelRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.published_at",
                "EDGAR dissemination time must equal the acceptance time",
            )
        if record["published_at_type"] != "OFFICIAL_DISSEMINATION_TIMESTAMP":
            _reject(
                PanelRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.published_at_type",
                "EDGAR dissemination must be typed as official dissemination",
            )
    else:
        if record["published_at"] is not None:
            _reject(
                PanelRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.published_at",
                "issuer release requires a conservative dissemination interval",
            )
        if record["published_at_precision"] != "DATE_INTERVAL":
            _reject(
                PanelRejectionReason.MISSING_PROVENANCE,
                f"{path}.published_at_precision",
                "issuer release publication bound must be a conservative interval",
            )
        if record["published_at_type"] != "COLLECTOR_OBSERVED_UPPER_BOUND":
            _reject(
                PanelRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.published_at_type",
                "issuer release bound must be typed as a collector-observed upper bound",
            )
    upper = _published_upper_bound(record, path=path)
    if upper > decision_cutoff:
        _reject(
            PanelRejectionReason.POINT_IN_TIME_VIOLATION,
            f"{path}.published_at",
            "evidence publication bound is after the event decision cutoff",
        )
    return evidence_id, upper


def _validate_session_context(
    context: Mapping[str, object],
    *,
    path: str,
    decision_cutoff: datetime,
    expected_event: Mapping[str, object],
) -> None:
    session_open = _timestamp(context["session_open_at"], path=f"{path}.session_open_at")
    session_close = _timestamp(context["session_close_at"], path=f"{path}.session_close_at")
    event_date = decision_cutoff.date()
    expected_open = datetime.combine(event_date, _SESSION_OPEN.replace(tzinfo=None), tzinfo=UTC)
    expected_close = datetime.combine(event_date, _SESSION_CLOSE.replace(tzinfo=None), tzinfo=UTC)
    if session_open != expected_open or session_close != expected_close:
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            f"{path}.session_open_at",
            "session window must equal the frozen regular-session rules for the event date",
        )
    exchange = context["session_id"].split("-")[0]
    if exchange not in ("XNYS", "XNAS"):
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            f"{path}.session_id",
            "session must be a frozen NYSE or Nasdaq regular session",
        )
    if decision_cutoff == session_open or (
        decision_cutoff <= session_close and decision_cutoff > session_open
    ):
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            f"{path}.scheduled_event_at",
            "frozen panel rule excludes events during the regular session",
        )
    expected_bucket = "BEFORE_OPEN" if decision_cutoff < session_open else "AFTER_CLOSE"
    if expected_bucket != expected_event["timing_bucket"]:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            f"{path}.scheduled_event_at",
            "event timing bucket does not match the frozen session window",
        )


def _validate_historical_manifest(
    raw: bytes,
    *,
    index: int,
    event_list_sha256: str,
    selection_rule_sha256: str,
    expected_event: Mapping[str, object],
    ex_ante_freeze_at: datetime,
) -> ValidatedHistoricalEvidence:
    path = f"manifests[{index}]"
    manifest = _strict_object(_decode(raw, path=path), path=path, fields=_MANIFEST_FIELDS)
    if manifest["schema"] != HISTORICAL_EVIDENCE_SCHEMA or manifest["schema_version"] != 1:
        _reject(
            PanelRejectionReason.UNSUPPORTED_SCHEMA,
            path,
            "panel universes require the historical evidence manifest v1",
        )
    event_id = _text(manifest["event_id"], path=f"{path}.event_id")
    if manifest["issuer"] != expected_event["issuer"] or event_id != expected_event["event_id"]:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            path,
            "manifest issuer or event ID differs from the frozen list",
        )
    if (
        _sha256(manifest["event_list_sha256"], path=f"{path}.event_list_sha256")
        != event_list_sha256
    ):
        _reject(
            PanelRejectionReason.HASH_MISMATCH,
            f"{path}.event_list_sha256",
            "manifest is not bound to the supplied event-list bytes",
        )
    if (
        _sha256(manifest["selection_rule_sha256"], path=f"{path}.selection_rule_sha256")
        != selection_rule_sha256
    ):
        _reject(
            PanelRejectionReason.HASH_MISMATCH,
            f"{path}.selection_rule_sha256",
            "manifest is not bound to the supplied selection-rule bytes",
        )
    if manifest["data_class"] != DATA_CLASS_REAL:
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.data_class",
            "historical manifests must be point-in-time panel evidence",
        )
    qualifiers = set(_text_list(manifest["data_qualifiers"], path=f"{path}.data_qualifiers"))
    required = {"INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE", "NO_OUTCOME_VALUES", "NO_BROKER_EXECUTION"}
    if not required <= qualifiers:
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
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
                PanelRejectionReason.IDENTITY_MISMATCH,
                f"{path}.event_context.{field}",
                "event context differs from the frozen list",
            )
    if decision_cutoff != _timestamp(
        context["scheduled_event_at"], path=f"{path}.event_context.scheduled_event_at"
    ):
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            f"{path}.decision_cutoff",
            "decision cutoff must equal the frozen scheduled event instant",
        )
    _validate_session_context(
        context,
        path=f"{path}.event_context",
        decision_cutoff=decision_cutoff,
        expected_event=expected_event,
    )
    records = manifest["records"]
    if not isinstance(records, list) or len(records) != 2:
        _reject(
            PanelRejectionReason.MISSING_PROVENANCE,
            f"{path}.records",
            "exactly two records are required: ISSUER_PRIMARY and SEC_OFFICIAL",
        )
    record_ids: set[str] = set()
    upper_bounds: list[datetime] = []
    kinds: set[str] = set()
    issuer_url: str | None = None
    for record_index, (item, required_kind) in enumerate(
        zip(records, ("ISSUER_PRIMARY", "SEC_OFFICIAL"), strict=True)
    ):
        record_path = f"{path}.records[{record_index}]"
        evidence_id, upper = _validate_record(
            item,
            path=record_path,
            decision_cutoff=decision_cutoff,
            required_kind=required_kind,
        )
        if evidence_id in record_ids:
            _reject(
                PanelRejectionReason.PROVENANCE_MISMATCH,
                f"{record_path}.evidence_id",
                "evidence IDs must be unique",
            )
        record_ids.add(evidence_id)
        kinds.add(required_kind)
        upper_bounds.append(upper)
        if required_kind == "ISSUER_PRIMARY":
            issuer_url = _text(item["source_url"], path=f"{record_path}.source_url")
    if kinds != {"ISSUER_PRIMARY", "SEC_OFFICIAL"}:
        _reject(
            PanelRejectionReason.MISSING_PROVENANCE,
            f"{path}.records",
            "one ISSUER_PRIMARY and one SEC_OFFICIAL record are required",
        )
    if issuer_url != expected_event["issuer_release_url"]:
        _reject(
            PanelRejectionReason.PROVENANCE_MISMATCH,
            f"{path}.records[0].source_url",
            "ISSUER_PRIMARY source must equal the frozen event issuer release URL",
        )
    if latest_evidence_at != max(upper_bounds):
        _reject(
            PanelRejectionReason.PROVENANCE_MISMATCH,
            f"{path}.latest_evidence_at",
            "must equal the latest conservative source publication bound",
        )
    if feature_snapshot_at != ex_ante_freeze_at or frozen_at != ex_ante_freeze_at:
        _reject(
            PanelRejectionReason.POINT_IN_TIME_VIOLATION,
            f"{path}.feature_snapshot_at",
            "snapshot and freeze timestamps must equal the ex-ante universe freeze",
        )
    field_refs = manifest["field_source_refs"]
    if not isinstance(field_refs, Mapping):
        _reject(
            PanelRejectionReason.INVALID_DOCUMENT,
            f"{path}.field_source_refs",
            "must be an object",
        )
    for field in _SOURCE_BACKED_CONTEXT_FIELDS:
        if field not in field_refs:
            _reject(
                PanelRejectionReason.MISSING_PROVENANCE,
                f"{path}.field_source_refs.{field}",
                "source-backed event context requires provenance",
            )
        refs = _text_list(field_refs[field], path=f"{path}.field_source_refs.{field}")
        if not set(refs) <= record_ids:
            _reject(
                PanelRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.field_source_refs.{field}",
                "field references unknown evidence",
            )
    unknown_ref_fields = set(field_refs) - _SOURCE_BACKED_CONTEXT_FIELDS
    if unknown_ref_fields:
        field = sorted(unknown_ref_fields)[0]
        _reject(
            PanelRejectionReason.UNKNOWN_FIELD,
            f"{path}.field_source_refs.{field}",
            "protocol classifications cannot claim source provenance",
        )
    dependencies = manifest["feature_dependencies"]
    if not isinstance(dependencies, list) or len(dependencies) != 2:
        _reject(
            PanelRejectionReason.MISSING_PROVENANCE,
            f"{path}.feature_dependencies",
            "exactly two dependency-closed features are required",
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
                PanelRejectionReason.PROVENANCE_MISMATCH,
                f"{dependency_path}.feature_id",
                "feature IDs must be unique",
            )
        dependency_ids.add(feature_id)
        refs = _text_list(
            dependency["source_refs"], path=f"{dependency_path}.source_refs", nonempty=False
        )
        if dependency["dependency_check"] != "ELIGIBLE" or not set(refs) <= record_ids:
            _reject(
                PanelRejectionReason.MISSING_PROVENANCE,
                dependency_path,
                "feature dependencies must resolve to eligible source records",
            )
    if dependency_ids != {"event_schedule", "regular_session_window"}:
        _reject(
            PanelRejectionReason.MISSING_PROVENANCE,
            f"{path}.feature_dependencies",
            "panel features are event_schedule and regular_session_window",
        )
    sec_filing = _strict_object(
        manifest["sec_filing"],
        path=f"{path}.sec_filing",
        fields=frozenset({"url", "accepted_at", "status", "reason"}),
    )
    if sec_filing["status"] != "AVAILABLE":
        _reject(
            PanelRejectionReason.MISSING_PROVENANCE,
            f"{path}.sec_filing.status",
            "historical panel events require an available earnings filing",
        )
    _https_url(sec_filing["url"], path=f"{path}.sec_filing.url")
    if (
        _timestamp(sec_filing["accepted_at"], path=f"{path}.sec_filing.accepted_at")
        > decision_cutoff
    ):
        _reject(
            PanelRejectionReason.POINT_IN_TIME_VIOLATION,
            f"{path}.sec_filing.accepted_at",
            "SEC acceptance is after the cutoff",
        )
    _text(sec_filing["reason"], path=f"{path}.sec_filing.reason")
    _text(manifest["entitlement_note"], path=f"{path}.entitlement_note")
    if manifest["redistribution_status"] != "METADATA_AND_HASH_ONLY":
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.redistribution_status",
            "raw source bytes are not redistributed",
        )
    _text_list(manifest["limitations"], path=f"{path}.limitations")
    return ValidatedHistoricalEvidence(
        event_id=event_id, manifest_sha256=hashlib.sha256(raw).hexdigest()
    )


def validate_panel_universe(
    selection_rule_bytes: bytes,
    event_list_bytes: bytes,
    manifest_bytes: Sequence[bytes],
) -> tuple[ValidatedHistoricalEvidence, ...]:
    """Validate one frozen historical panel universe as exact bytes."""

    try:
        return _validate_panel_universe(selection_rule_bytes, event_list_bytes, manifest_bytes)
    except ReplayEvidenceRejected as error:
        raise PanelRejected(
            PanelRejectionReason(error.reason.value), error.path, error.detail
        ) from error


def _validate_panel_universe(
    selection_rule_bytes: bytes,
    event_list_bytes: bytes,
    manifest_bytes: Sequence[bytes],
) -> tuple[ValidatedHistoricalEvidence, ...]:
    validate_replay_selection_rule(selection_rule_bytes)
    event_list_payload, event_ids, expected_events = validate_replay_event_list(
        event_list_bytes, selection_rule_bytes
    )
    ex_ante_freeze_at = _timestamp(event_list_payload["frozen_at"], path="event_list.frozen_at")
    event_list_sha256 = hashlib.sha256(event_list_bytes).hexdigest()
    selection_rule_sha256 = hashlib.sha256(selection_rule_bytes).hexdigest()
    by_id: dict[str, ValidatedHistoricalEvidence] = {}
    for index, raw in enumerate(manifest_bytes):
        candidate = _decode(raw, path=f"manifests[{index}]")
        event_id = _text(candidate.get("event_id"), path=f"manifests[{index}].event_id")
        if event_id in by_id:
            _reject(
                PanelRejectionReason.DUPLICATE_EVENT_ID,
                f"manifests[{index}].event_id",
                "event manifests must be unique",
            )
        expected = expected_events.get(event_id)
        if expected is None:
            _reject(
                PanelRejectionReason.IDENTITY_MISMATCH,
                f"manifests[{index}].event_id",
                "event is absent from the frozen event list",
            )
        by_id[event_id] = _validate_historical_manifest(
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
            PanelRejectionReason.MISSING_FIELD,
            f"manifests.{missing}",
            "every frozen event requires one historical evidence manifest",
        )
    return tuple(by_id[event_id] for event_id in event_ids)


__all__ = [
    "HISTORICAL_EVIDENCE_SCHEMA",
    "ValidatedHistoricalEvidence",
    "validate_panel_universe",
]
