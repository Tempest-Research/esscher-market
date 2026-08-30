"""Fail-closed validation for the frozen Q-FAST point-in-time panel."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ..contracts.execution_policy import RESEARCH_DECISION_PROTOCOL_SHA256

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)

PANEL_SELECTION_RULE_SCHEMA = "ringdown.qfast_panel_selection_rule"
PANEL_MANIFEST_SCHEMA = "ringdown.qfast_panel_manifest"

DATA_CLASS_REAL = "POINT_IN_TIME_EVENT_PANEL"
DATA_CLASS_SYNTHETIC = "SYNTHETIC_CONTRACT_FIXTURE"

MINIMUM_ELIGIBLE_EVENTS = 20
MAXIMUM_ELIGIBLE_EVENTS = 30

P0_CONTRACT_DEVELOPMENT_EVENT_IDS = frozenset(
    {
        "KR-2026Q2-EARNINGS",
        "GIS-2027Q1-EARNINGS",
        "MU-2026Q4-EARNINGS",
        "NKE-2027Q1-EARNINGS",
    }
)
P0_EXCLUSION_REASON_CODE = "P0_CONTRACT_DEVELOPMENT_EVENT"

KNOWN_STRATEGY_POLICY_SHA256: frozenset[str] = frozenset()
KNOWN_SNAPSHOT_PROTOCOL_SHA256: frozenset[str] = frozenset()

PANEL_CLAIM_BOUNDARY = frozenset(
    {
        "POINT_IN_TIME_EVENT_PANEL_CANDIDATE",
        "NOT_ALPHA_EVIDENCE",
        "NO_OUTCOME_VALUES",
        "NO_BROKER_EXECUTION",
    }
)
SYNTHETIC_REQUIRED_QUALIFIERS = frozenset(
    {"NOT_HISTORICAL_DATA", "NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION"}
)
REAL_REQUIRED_QUALIFIERS = frozenset(
    {"INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE", "NO_OUTCOME_VALUES", "NO_BROKER_EXECUTION"}
)

_SELECTION_RULE_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "rule_id",
        "frozen_at",
        "decision_cutoff_policy",
        "criteria",
        "required_excluded_event_ids",
        "claim_boundary",
    }
)
_SELECTION_CRITERIA_FIELDS = frozenset(
    {
        "primary_source_evidence_required",
        "exact_publication_time_required",
        "evidence_retrieved_no_later_than_decision_cutoff",
        "synchronized_issuer_market_sector_windows_required",
        "regular_us_equity_session_required",
        "post_cutoff_paths_forbidden_at_freeze",
        "outcome_values_forbidden_at_freeze",
        "abstentions_retained_in_denominator",
        "p0_contract_development_events_excluded",
        "minimum_eligible_events",
        "maximum_eligible_events",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "panel_id",
        "frozen_at",
        "selection_rule_sha256",
        "strategy_policy_sha256",
        "snapshot_protocol_sha256",
        "decision_protocol_sha256",
        "data_class",
        "data_qualifiers",
        "hold_seconds",
        "required_latency_profile",
        "latency_profiles",
        "eligible_events",
        "excluded_events",
        "limitations",
    }
)
_ELIGIBLE_EVENT_FIELDS = frozenset({"event_id", "evidence_manifest_sha256"})
_EXCLUDED_EVENT_FIELDS = frozenset({"event_id", "reason_code", "reason_detail"})
_LATENCY_PROFILE_FIELDS = frozenset({"requested_latency_ms", "measurement"})
_MEASUREMENT_FIELDS = frozenset({"kind", "publisher", "measured_at", "content_sha256"})


class PanelRejectionReason(StrEnum):
    """Stable machine-readable reasons for rejecting panel artifacts."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    HASH_MISMATCH = "HASH_MISMATCH"
    DUPLICATE_EVENT_ID = "DUPLICATE_EVENT_ID"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    PANEL_SIZE_VIOLATION = "PANEL_SIZE_VIOLATION"
    P0_EVENT_IN_PANEL = "P0_EVENT_IN_PANEL"
    POINT_IN_TIME_VIOLATION = "POINT_IN_TIME_VIOLATION"
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    LATENCY_PROFILE_NOT_MEASURED = "LATENCY_PROFILE_NOT_MEASURED"
    UPSTREAM_CONTRACT_MISSING = "UPSTREAM_CONTRACT_MISSING"
    CLAIM_BOUNDARY_MISMATCH = "CLAIM_BOUNDARY_MISMATCH"
    SELECTION_RULE_VIOLATION = "SELECTION_RULE_VIOLATION"
    MISSING_PRICE_POINT = "MISSING_PRICE_POINT"


class PanelRejected(ValueError):
    """A deterministic validation failure for Q-FAST panel artifacts."""

    def __init__(self, reason: PanelRejectionReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


@dataclass(frozen=True, slots=True)
class ExcludedEvent:
    """One preserved exclusion with its stable reason."""

    event_id: str
    reason_code: str
    reason_detail: str


@dataclass(frozen=True, slots=True)
class ValidatedPanelManifest:
    """Identity and frozen parameters of a valid panel manifest."""

    panel_id: str
    data_class: str
    frozen_at: datetime
    panel_manifest_sha256: str
    selection_rule_sha256: str
    strategy_policy_sha256: str
    snapshot_protocol_sha256: str
    decision_protocol_sha256: str
    hold_seconds: int
    latency_profiles: Mapping[str, int]
    required_latency_profile: str
    eligible_event_ids: tuple[str, ...]
    excluded_events: tuple[ExcludedEvent, ...]
    limitations: tuple[str, ...]
    minimum_events: int


class _DuplicateFieldError(ValueError):
    pass


def _reject(reason: PanelRejectionReason, path: str, detail: str) -> None:
    raise PanelRejected(reason, path, detail)


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
            PanelRejectionReason.INVALID_DOCUMENT,
            path,
            "contract inputs must be immutable bytes",
        )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except _DuplicateFieldError as error:
        _reject(PanelRejectionReason.DUPLICATE_FIELD, path, f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, str(error))
    if not isinstance(value, Mapping):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "root must be an object")
    return value


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(
            PanelRejectionReason.MISSING_FIELD,
            f"{path}.{missing[0]}",
            "required field is missing",
        )
    unknown = sorted(keys - fields)
    if unknown:
        _reject(
            PanelRejectionReason.UNKNOWN_FIELD,
            f"{path}.{unknown[0]}",
            "field is not part of the frozen schema",
        )
    return value


def _text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "must be non-empty text")
    return value


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        _reject(
            PanelRejectionReason.INVALID_DOCUMENT,
            path,
            "must be an explicit UTC timestamp ending in Z",
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as error:
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, str(error))


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "must be lowercase SHA-256")
    return value


def _integer(value: object, *, path: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        _reject(
            PanelRejectionReason.INVALID_DOCUMENT,
            path,
            f"must be an integer of at least {minimum}",
        )
    return value


def _text_list(
    value: object,
    *,
    path: str,
    nonempty: bool = True,
    unique: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "must be a list")
    result = tuple(_text(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    if unique and len(set(result)) != len(result):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "values must be unique")
    return result


def _boolean_true(value: object, *, path: str) -> None:
    if value is not True:
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            path,
            "frozen safety criteria must remain enabled",
        )


def validate_panel_selection_rule(raw: bytes) -> Mapping[str, object]:
    """Validate the frozen ex-ante panel selection rule."""

    rule = _strict_object(
        _decode(raw, path="selection_rule"),
        path="selection_rule",
        fields=_SELECTION_RULE_FIELDS,
    )
    if rule["schema"] != PANEL_SELECTION_RULE_SCHEMA or rule["schema_version"] != 1:
        _reject(
            PanelRejectionReason.UNSUPPORTED_SCHEMA,
            "selection_rule",
            "unsupported panel selection-rule schema or version",
        )
    _text(rule["rule_id"], path="selection_rule.rule_id")
    _timestamp(rule["frozen_at"], path="selection_rule.frozen_at")
    if rule["decision_cutoff_policy"] != "SCHEDULED_EVENT_AT":
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            "selection_rule.decision_cutoff_policy",
            "only the frozen scheduled-event cutoff is supported",
        )
    criteria = _strict_object(
        rule["criteria"],
        path="selection_rule.criteria",
        fields=_SELECTION_CRITERIA_FIELDS,
    )
    for criterion in (
        "primary_source_evidence_required",
        "exact_publication_time_required",
        "evidence_retrieved_no_later_than_decision_cutoff",
        "synchronized_issuer_market_sector_windows_required",
        "regular_us_equity_session_required",
        "post_cutoff_paths_forbidden_at_freeze",
        "outcome_values_forbidden_at_freeze",
        "abstentions_retained_in_denominator",
        "p0_contract_development_events_excluded",
    ):
        _boolean_true(criteria[criterion], path=f"selection_rule.criteria.{criterion}")
    if criteria["minimum_eligible_events"] != MINIMUM_ELIGIBLE_EVENTS:
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            "selection_rule.criteria.minimum_eligible_events",
            "the preregistered twenty-event floor cannot move",
        )
    if criteria["maximum_eligible_events"] != MAXIMUM_ELIGIBLE_EVENTS:
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            "selection_rule.criteria.maximum_eligible_events",
            "the preregistered thirty-event ceiling cannot move",
        )
    required_excluded = _text_list(
        rule["required_excluded_event_ids"],
        path="selection_rule.required_excluded_event_ids",
    )
    if set(required_excluded) != P0_CONTRACT_DEVELOPMENT_EVENT_IDS:
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            "selection_rule.required_excluded_event_ids",
            "the four P0 contract-development events must stay excluded",
        )
    claim_boundary = set(_text_list(rule["claim_boundary"], path="selection_rule.claim_boundary"))
    if claim_boundary != PANEL_CLAIM_BOUNDARY:
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "selection_rule.claim_boundary",
            "the frozen panel claim boundary cannot be weakened or extended",
        )
    return rule


def _validate_latency_profile(
    value: object,
    *,
    path: str,
    name: str,
    data_class: str,
    rule_frozen_at: datetime,
) -> int:
    profile = _strict_object(value, path=path, fields=_LATENCY_PROFILE_FIELDS)
    requested = _integer(
        profile["requested_latency_ms"], path=f"{path}.requested_latency_ms", minimum=0
    )
    if name == "zero":
        if requested != 0:
            _reject(
                PanelRejectionReason.SELECTION_RULE_VIOLATION,
                f"{path}.requested_latency_ms",
                "the zero-latency profile is definitional and must request 0 ms",
            )
        if profile["measurement"] is not None:
            _reject(
                PanelRejectionReason.LATENCY_PROFILE_NOT_MEASURED,
                f"{path}.measurement",
                "the zero-latency profile carries no measurement record",
            )
        return requested
    if requested < 1:
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            f"{path}.requested_latency_ms",
            "the p95 profile must request a positive latency",
        )
    measurement = profile["measurement"]
    if measurement is None:
        _reject(
            PanelRejectionReason.LATENCY_PROFILE_NOT_MEASURED,
            f"{path}.measurement",
            "the p95 profile requires a measurement record",
        )
    record = _strict_object(measurement, path=f"{path}.measurement", fields=_MEASUREMENT_FIELDS)
    kind = record["kind"]
    if data_class == DATA_CLASS_REAL:
        if kind != "HOST_MEASURED":
            _reject(
                PanelRejectionReason.LATENCY_PROFILE_NOT_MEASURED,
                f"{path}.measurement.kind",
                "real panels require a host-measured p95 latency profile",
            )
    elif kind != "SYNTHETIC":
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.measurement.kind",
            "synthetic panels cannot claim a host-measured latency profile",
        )
    _text(record["publisher"], path=f"{path}.measurement.publisher")
    measured_at = _timestamp(record["measured_at"], path=f"{path}.measurement.measured_at")
    if measured_at > rule_frozen_at:
        _reject(
            PanelRejectionReason.POINT_IN_TIME_VIOLATION,
            f"{path}.measurement.measured_at",
            "latency measurement postdates the panel freeze",
        )
    _sha256(record["content_sha256"], path=f"{path}.measurement.content_sha256")
    return requested


def _validate_eligible_event(
    value: object,
    *,
    path: str,
    data_class: str,
    seen_ids: set[str],
    seen_evidence: set[str],
) -> str:
    event = _strict_object(value, path=path, fields=_ELIGIBLE_EVENT_FIELDS)
    event_id = _text(event["event_id"], path=f"{path}.event_id")
    if event_id in seen_ids:
        _reject(
            PanelRejectionReason.DUPLICATE_EVENT_ID,
            f"{path}.event_id",
            "eligible event IDs must be unique",
        )
    seen_ids.add(event_id)
    if event_id in P0_CONTRACT_DEVELOPMENT_EVENT_IDS:
        _reject(
            PanelRejectionReason.P0_EVENT_IN_PANEL,
            f"{path}.event_id",
            "P0 contract-development events can never enter the confirmatory panel",
        )
    evidence = event["evidence_manifest_sha256"]
    if data_class == DATA_CLASS_REAL:
        evidence_sha = _sha256(evidence, path=f"{path}.evidence_manifest_sha256")
        if evidence_sha in seen_evidence:
            _reject(
                PanelRejectionReason.DUPLICATE_EVENT_ID,
                f"{path}.evidence_manifest_sha256",
                "evidence manifests must be unique per event",
            )
        seen_evidence.add(evidence_sha)
    elif evidence is not None:
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.evidence_manifest_sha256",
            "synthetic panels cannot claim evidence-manifest provenance",
        )
    return event_id


def _validate_excluded_event(
    value: object,
    *,
    path: str,
    seen_ids: set[str],
) -> ExcludedEvent:
    event = _strict_object(value, path=path, fields=_EXCLUDED_EVENT_FIELDS)
    event_id = _text(event["event_id"], path=f"{path}.event_id")
    if event_id in seen_ids:
        _reject(
            PanelRejectionReason.DUPLICATE_EVENT_ID,
            f"{path}.event_id",
            "excluded event IDs must be unique",
        )
    seen_ids.add(event_id)
    reason_code = _text(event["reason_code"], path=f"{path}.reason_code")
    reason_detail = _text(event["reason_detail"], path=f"{path}.reason_detail")
    if event_id in P0_CONTRACT_DEVELOPMENT_EVENT_IDS and (reason_code != P0_EXCLUSION_REASON_CODE):
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            f"{path}.reason_code",
            "P0 exclusions must carry the frozen P0 reason code",
        )
    return ExcludedEvent(event_id=event_id, reason_code=reason_code, reason_detail=reason_detail)


def validate_panel_manifest(
    manifest_bytes: bytes,
    selection_rule_bytes: bytes,
) -> ValidatedPanelManifest:
    """Validate exact panel manifest bytes against the frozen selection rule."""

    rule = validate_panel_selection_rule(selection_rule_bytes)
    rule_frozen_at = _timestamp(rule["frozen_at"], path="selection_rule.frozen_at")
    manifest = _strict_object(
        _decode(manifest_bytes, path="panel_manifest"),
        path="panel_manifest",
        fields=_MANIFEST_FIELDS,
    )
    if manifest["schema"] != PANEL_MANIFEST_SCHEMA or manifest["schema_version"] != 1:
        _reject(
            PanelRejectionReason.UNSUPPORTED_SCHEMA,
            "panel_manifest",
            "unsupported panel manifest schema or version",
        )
    panel_id = _text(manifest["panel_id"], path="panel_manifest.panel_id")
    frozen_at = _timestamp(manifest["frozen_at"], path="panel_manifest.frozen_at")
    if frozen_at != rule_frozen_at:
        _reject(
            PanelRejectionReason.POINT_IN_TIME_VIOLATION,
            "panel_manifest.frozen_at",
            "panel freeze must equal the ex-ante selection-rule freeze",
        )
    selection_rule_sha256 = _sha256(
        manifest["selection_rule_sha256"], path="panel_manifest.selection_rule_sha256"
    )
    if selection_rule_sha256 != hashlib.sha256(selection_rule_bytes).hexdigest():
        _reject(
            PanelRejectionReason.HASH_MISMATCH,
            "panel_manifest.selection_rule_sha256",
            "panel manifest is not bound to the supplied selection-rule bytes",
        )
    strategy_policy_sha256 = _sha256(
        manifest["strategy_policy_sha256"], path="panel_manifest.strategy_policy_sha256"
    )
    snapshot_protocol_sha256 = _sha256(
        manifest["snapshot_protocol_sha256"], path="panel_manifest.snapshot_protocol_sha256"
    )
    decision_protocol_sha256 = _sha256(
        manifest["decision_protocol_sha256"], path="panel_manifest.decision_protocol_sha256"
    )
    if decision_protocol_sha256 != RESEARCH_DECISION_PROTOCOL_SHA256:
        _reject(
            PanelRejectionReason.UPSTREAM_CONTRACT_MISSING,
            "panel_manifest.decision_protocol_sha256",
            "panel is not bound to the merged frozen-research-decision protocol",
        )
    data_class = manifest["data_class"]
    if data_class not in (DATA_CLASS_REAL, DATA_CLASS_SYNTHETIC):
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "panel_manifest.data_class",
            "panel requires an explicit supported data class",
        )
    qualifiers = set(_text_list(manifest["data_qualifiers"], path="panel_manifest.data_qualifiers"))
    required_qualifiers = (
        REAL_REQUIRED_QUALIFIERS if data_class == DATA_CLASS_REAL else SYNTHETIC_REQUIRED_QUALIFIERS
    )
    if not required_qualifiers <= qualifiers:
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "panel_manifest.data_qualifiers",
            "data-class limitations must stay explicit",
        )
    hold_seconds = _integer(manifest["hold_seconds"], path="panel_manifest.hold_seconds", minimum=1)
    if manifest["required_latency_profile"] != "p95":
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            "panel_manifest.required_latency_profile",
            "the preregistered latency gate runs on the p95 profile",
        )
    latency_raw = manifest["latency_profiles"]
    if not isinstance(latency_raw, Mapping) or set(latency_raw) != {"zero", "p95"}:
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            "panel_manifest.latency_profiles",
            "panels keep exactly the separated zero and p95 profiles",
        )
    latency_profiles = {
        name: _validate_latency_profile(
            latency_raw[name],
            path=f"panel_manifest.latency_profiles.{name}",
            name=name,
            data_class=data_class,
            rule_frozen_at=rule_frozen_at,
        )
        for name in ("zero", "p95")
    }
    eligible_raw = manifest["eligible_events"]
    if not isinstance(eligible_raw, list) or not eligible_raw:
        _reject(
            PanelRejectionReason.INVALID_DOCUMENT,
            "panel_manifest.eligible_events",
            "must be a non-empty list",
        )
    eligible_ids: list[str] = []
    seen_eligible: set[str] = set()
    seen_evidence: set[str] = set()
    for index, item in enumerate(eligible_raw):
        eligible_ids.append(
            _validate_eligible_event(
                item,
                path=f"panel_manifest.eligible_events[{index}]",
                data_class=data_class,
                seen_ids=seen_eligible,
                seen_evidence=seen_evidence,
            )
        )
    if data_class == DATA_CLASS_REAL:
        if not (MINIMUM_ELIGIBLE_EVENTS <= len(eligible_ids) <= MAXIMUM_ELIGIBLE_EVENTS):
            _reject(
                PanelRejectionReason.PANEL_SIZE_VIOLATION,
                "panel_manifest.eligible_events",
                "the untouched confirmatory panel requires 20 to 30 eligible events",
            )
        if strategy_policy_sha256 not in KNOWN_STRATEGY_POLICY_SHA256:
            _reject(
                PanelRejectionReason.UPSTREAM_CONTRACT_MISSING,
                "panel_manifest.strategy_policy_sha256",
                "the frozen strategy policy from issue #26 is not merged and registered",
            )
        if snapshot_protocol_sha256 not in KNOWN_SNAPSHOT_PROTOCOL_SHA256:
            _reject(
                PanelRejectionReason.UPSTREAM_CONTRACT_MISSING,
                "panel_manifest.snapshot_protocol_sha256",
                "the point-in-time snapshot protocol from issue #27 is not merged and registered",
            )
    elif len(eligible_ids) < 2 or len(eligible_ids) > MAXIMUM_ELIGIBLE_EVENTS:
        _reject(
            PanelRejectionReason.PANEL_SIZE_VIOLATION,
            "panel_manifest.eligible_events",
            "synthetic panels require between two and thirty events",
        )
    excluded_raw = manifest["excluded_events"]
    if not isinstance(excluded_raw, list):
        _reject(
            PanelRejectionReason.INVALID_DOCUMENT,
            "panel_manifest.excluded_events",
            "must be a list",
        )
    seen_excluded: set[str] = set()
    excluded_events = tuple(
        _validate_excluded_event(
            item,
            path=f"panel_manifest.excluded_events[{index}]",
            seen_ids=seen_excluded,
        )
        for index, item in enumerate(excluded_raw)
    )
    overlap = seen_eligible & seen_excluded
    if overlap:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            "panel_manifest.excluded_events",
            f"event {sorted(overlap)[0]} is both eligible and excluded",
        )
    if data_class == DATA_CLASS_REAL:
        required_excluded = set(
            _text_list(
                rule["required_excluded_event_ids"],
                path="selection_rule.required_excluded_event_ids",
            )
        )
        missing_exclusion = sorted(required_excluded - seen_excluded)
        if missing_exclusion:
            _reject(
                PanelRejectionReason.SELECTION_RULE_VIOLATION,
                "panel_manifest.excluded_events",
                f"frozen exclusion {missing_exclusion[0]} must stay visible in the manifest",
            )
    limitations = _text_list(manifest["limitations"], path="panel_manifest.limitations")
    minimum_events = MINIMUM_ELIGIBLE_EVENTS if data_class == DATA_CLASS_REAL else len(eligible_ids)
    return ValidatedPanelManifest(
        panel_id=panel_id,
        data_class=data_class,
        frozen_at=frozen_at,
        panel_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        selection_rule_sha256=selection_rule_sha256,
        strategy_policy_sha256=strategy_policy_sha256,
        snapshot_protocol_sha256=snapshot_protocol_sha256,
        decision_protocol_sha256=decision_protocol_sha256,
        hold_seconds=hold_seconds,
        latency_profiles=latency_profiles,
        required_latency_profile="p95",
        eligible_event_ids=tuple(eligible_ids),
        excluded_events=excluded_events,
        limitations=limitations,
        minimum_events=minimum_events,
    )
