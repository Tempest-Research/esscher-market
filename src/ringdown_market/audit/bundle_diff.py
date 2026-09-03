"""Deterministic, offline comparison of known Esscher JSON artifacts.

The comparison layer copies values from already-produced artifacts. It never
reruns research, contacts a source, starts a broker session, or infers a
financial conclusion from a changed value. Complete replay bundles are checked
through the existing replay-evidence contract before comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final

from ringdown_market.contracts.replay_evidence import (
    ReplayEvidenceRejected,
    validate_replay_evidence_set,
)


class BundleDiffErrorReason(StrEnum):
    """Stable machine-readable reasons for rejecting comparison inputs."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    UNEXPECTED_TOP_LEVEL_SHAPE = "UNEXPECTED_TOP_LEVEL_SHAPE"
    UNKNOWN_ARTIFACT_TYPE = "UNKNOWN_ARTIFACT_TYPE"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    CONTRACT_VALIDATION_FAILED = "CONTRACT_VALIDATION_FAILED"
    INPUT_NOT_FOUND = "INPUT_NOT_FOUND"
    INPUT_KIND_MISMATCH = "INPUT_KIND_MISMATCH"
    EMPTY_BUNDLE = "EMPTY_BUNDLE"
    UNSUPPORTED_FILE = "UNSUPPORTED_FILE"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    SYMLINK_NOT_ALLOWED = "SYMLINK_NOT_ALLOWED"
    OUTPUT_ERROR = "OUTPUT_ERROR"


class BundleDiffError(ValueError):
    """A deterministic fail-closed comparison error."""

    def __init__(self, reason: BundleDiffErrorReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


_MISSING: Final = object()
_JSON_SUFFIX: Final = ".json"
_REPORT_SCHEMA: Final = "ringdown.evidence_bundle_diff_report"
_REPORT_VERSION: Final = 1
_MAX_JSON_DEPTH: Final = 128
_SHA256_CHARS: Final = frozenset("0123456789abcdef")
_LEGACY_REPORT_PROJECT: Final = "Ring" + "down"

STATUS_CONTRACT_VALIDATED: Final = "CONTRACT_VALIDATED"
STATUS_PARTIALLY_CONTRACT_VALIDATED: Final = "PARTIALLY_CONTRACT_VALIDATED"
STATUS_SCHEMA_RECOGNIZED: Final = "STRICT_JSON_SCHEMA_RECOGNIZED"
_VALIDATION_STATUSES: Final = frozenset(
    {
        STATUS_CONTRACT_VALIDATED,
        STATUS_PARTIALLY_CONTRACT_VALIDATED,
        STATUS_SCHEMA_RECOGNIZED,
    }
)

# This registry is deliberately closed. A new artifact schema must be
# registered before it can appear in an audit report.
_KNOWN_SCHEMA_VERSIONS: Final[dict[str, frozenset[int]]] = {
    "ringdown.alpaca_mcp_protocol": frozenset({1}),
    "ringdown.earnings_replay_selection_rule": frozenset({1}),
    "ringdown.evidence_bundle_diff_report": frozenset({1}),
    "ringdown.evaluation_report": frozenset({1}),
    "ringdown.feature_input_snapshot": frozenset({1}),
    "ringdown.frozen_earnings_event_list": frozenset({1}),
    "ringdown.frozen_research_decision_fixture": frozenset({1}),
    "ringdown.frozen_research_decision": frozenset({1}),
    "ringdown.paper_demo_approval": frozenset({1}),
    "ringdown.paper_demo_lifecycle_fixture": frozenset({1}),
    "ringdown.paper_execution_permit": frozenset({1}),
    "ringdown.paper_execution_permit_identity": frozenset({1}),
    "ringdown.paper_execution_permit_policy": frozenset({1}),
    "ringdown.paper_receipt_bundle": frozenset({1}),
    "ringdown.point_in_time_evidence_manifest": frozenset({1, 2}),
    "ringdown.research_decision_protocol": frozenset({1}),
    "ringdown.scheduled_event_manifest": frozenset({1}),
    "ringdown.scheduled_event_state": frozenset({1}),
    "ringdown.scheduled_run_error": frozenset({1}),
    "ringdown.scheduled_run_result": frozenset({1}),
    "ringdown.synthetic_contract_panel": frozenset({1}),
}
_IDENTITY_FIELDS: Final[dict[str, str]] = {
    "ringdown.point_in_time_evidence_manifest": "event_id",
    "ringdown.frozen_earnings_event_list": "list_id",
    "ringdown.earnings_replay_selection_rule": "rule_id",
    "ringdown.frozen_research_decision": "event_id",
    "ringdown.feature_input_snapshot": "event_id",
    "ringdown.scheduled_event_manifest": "event_run_id",
    "ringdown.scheduled_event_state": "event_run_id",
    "ringdown.scheduled_run_error": "event_run_id",
    "ringdown.scheduled_run_result": "event_run_id",
    "ringdown.paper_execution_permit": "permit_id",
    "ringdown.paper_demo_approval": "permit_id",
    "ringdown.paper_receipt_bundle": "event_run_id",
}
_KEYED_LISTS: Final[dict[str, str]] = {
    "records": "evidence_id",
    "feature_dependencies": "feature_id",
}
_SET_LIKE_LIST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "claim_boundary",
        "claims",
        "data_qualifiers",
        "limitations",
        "missing_or_conflicting_evidence",
        "reject_reasons",
        "source_refs",
    }
)
_TIME_KEYS: Final[frozenset[str]] = frozenset(
    {
        "accepted_at",
        "approved_at",
        "close_filled_at",
        "decision_cutoff",
        "entry_session_policy",
        "event_category",
        "expires_at",
        "feature_computed_at",
        "feature_snapshot_at",
        "final_flat_observed_at",
        "frozen_at",
        "not_before",
        "observation_type",
        "observed_at",
        "open_filled_at",
        "published_at",
        "published_at_interval",
        "published_date_or_interval",
        "published_at_type",
        "retrieved_at",
        "scheduled_event_at",
        "session_close_at",
        "session_id",
        "session_open_at",
        "source_max_public_at",
        "source_observed_at",
        "source_timezone",
        "timing_bucket",
        "updated_at",
    }
)
_PROVENANCE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "content_sha256",
        "definition_version",
        "dependency_check",
        "entitlement_note",
        "field_source_refs",
        "field_status",
        "hash_representation",
        "issuer_release_url",
        "missing_or_conflicting_evidence",
        "published_at_precision",
        "publisher",
        "redistribution_note",
        "redistribution_status",
        "source_kind",
        "source_refs",
        "source_url",
    }
)
_INCLUSION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "excluded",
        "exclusion",
        "exclusion_reason",
        "included",
        "inclusion",
        "inclusion_or_exclusion_reason",
        "inclusion_reason",
    }
)
_VERDICT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "admitted",
        "candidate_advantage",
        "candidate_signal",
        "decision_state",
        "direction",
        "disposition",
        "eligibility",
        "lifecycle",
        "lifecycle_outcome",
        "qfast_status",
        "status",
        "strongest_baseline",
        "verdict",
    }
)
_LATENCY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "actual_latency_ms",
        "latency_gate",
        "latency_mode",
        "latency_ms",
        "latency_profiles",
        "qlatency_status",
        "requested_latency_ms",
        "required_latency_profile",
        "required_profile",
    }
)
_REPORT_CLAIMS: Final = ["COMPARISON_ONLY", "NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION"]
_REPORT_DATA_CLASS: Final = "OFFLINE_ARTIFACT_COMPARISON"
_NON_SEMANTIC_CATEGORIES: Final = frozenset({"IDENTITY", "ARTIFACT"})


class _DuplicateKey(ValueError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class _NonFiniteNumber(ValueError):
    pass


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256_CHARS for character in value)
    )


def _canonicalize_set_like(value: object, *, set_like: bool = False) -> object:
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key in sorted(value):
            child = value[key]
            child_set_like = set_like or key in _SET_LIKE_LIST_KEYS or key == "event_ids"
            normalized[key] = _canonicalize_set_like(child, set_like=child_set_like)
        return normalized
    if isinstance(value, list):
        items = [_canonicalize_set_like(item, set_like=False) for item in value]
        if set_like:
            return sorted(
                items,
                key=lambda item: _canonical_json(item),
            )
        return items
    if isinstance(value, tuple):
        return _canonicalize_set_like(list(value), set_like=set_like)
    return value


def _canonical_json(value: object) -> bytes:
    try:
        normalized = _canonicalize_set_like(value)
        return json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            "report",
            f"value cannot be represented as strict JSON: {error}",
        ) from error


def canonical_report_bytes(report: Mapping[str, object]) -> bytes:
    """Serialize a report using the stable canonical JSON representation."""

    return _canonical_json(dict(report)) + b"\n"


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _NonFiniteNumber(value)


def _escape_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _join_path(path: str, part: str | int) -> str:
    escaped = _escape_pointer_part(str(part))
    return f"/{escaped}" if not path else f"{path}/{escaped}"


def _key_from_path(path: str) -> str:
    if not path:
        return ""
    value = path.rsplit("/", 1)[-1]
    return value.replace("~1", "/").replace("~0", "~")


def _validate_json_value(value: object, *, path: str, depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path or "/",
            "JSON nesting exceeds the supported depth",
        )
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                path or "/",
                "surrogate code points are not valid UTF-8 report values",
            )
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise BundleDiffError(
            BundleDiffErrorReason.NON_FINITE_NUMBER,
            path or "/",
            "numbers must be finite",
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    path or "/",
                    "JSON object keys must be text",
                )
            _validate_json_value(item, path=_join_path(path, key), depth=depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=_join_path(path, index), depth=depth + 1)


def _validate_string_list(value: object, *, path: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "must be a list of strings",
        )
    if len(set(value)) != len(value):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "list values must be unique",
        )


def _validate_semantic_fields(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key in sorted(value):
            item = value[key]
            child_path = _join_path(path, key)
            if key in _SET_LIKE_LIST_KEYS or key == "event_ids":
                _validate_string_list(item, path=child_path)
            elif (
                key
                in {
                    "claim",
                    "event_id",
                    "fixture_class",
                    "data_class",
                    "artifact_class",
                }
                and item is not None
                and not isinstance(item, str)
            ):
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    child_path,
                    "must be text",
                )
            elif key == "field_source_refs":
                if not isinstance(item, Mapping):
                    raise BundleDiffError(
                        BundleDiffErrorReason.INVALID_DOCUMENT,
                        child_path,
                        "must be an object",
                    )
                for field, refs in item.items():
                    _validate_string_list(refs, path=_join_path(child_path, field))
            _validate_semantic_fields(item, path=child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_semantic_fields(item, path=_join_path(path, index))


def _parse_json(raw: bytes, *, path: str) -> dict[str, object]:
    if type(raw) is not bytes:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "comparison inputs must be immutable UTF-8 bytes",
        )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateKey as error:
        raise BundleDiffError(
            BundleDiffErrorReason.DUPLICATE_KEY,
            path,
            f"duplicate JSON key {error.key}",
        ) from error
    except _NonFiniteNumber as error:
        raise BundleDiffError(
            BundleDiffErrorReason.NON_FINITE_NUMBER,
            path,
            f"non-finite JSON constant {error}",
        ) from error
    except RecursionError as error:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "JSON nesting exceeds the parser limit",
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BundleDiffError(BundleDiffErrorReason.INVALID_DOCUMENT, path, str(error)) from error
    if not isinstance(value, dict):
        raise BundleDiffError(
            BundleDiffErrorReason.UNEXPECTED_TOP_LEVEL_SHAPE,
            path,
            "the JSON root must be an object",
        )
    try:
        _validate_json_value(value, path="")
        _validate_semantic_fields(value, path="")
    except RecursionError as error:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "JSON nesting exceeds the supported depth",
        ) from error
    return value


def _schema_nodes(value: object, *, path: str = "") -> list[tuple[str, str, int]]:
    nodes: list[tuple[str, str, int]] = []
    if isinstance(value, Mapping):
        if "schema" in value:
            schema = value["schema"]
            version = value.get("schema_version", _MISSING)
            schema_path = _join_path(path, "schema")
            version_path = _join_path(path, "schema_version")
            if not isinstance(schema, str) or not schema:
                raise BundleDiffError(
                    BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
                    schema_path,
                    "schema must be non-empty text",
                )
            if type(version) is not int:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    version_path,
                    "a schema-bearing object requires an integer schema_version",
                )
            supported = _KNOWN_SCHEMA_VERSIONS.get(schema)
            if supported is None:
                raise BundleDiffError(
                    BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
                    schema_path,
                    f"schema {schema!r} is not registered",
                )
            if version not in supported:
                raise BundleDiffError(
                    BundleDiffErrorReason.UNSUPPORTED_SCHEMA_VERSION,
                    version_path,
                    f"schema {schema!r} does not support version {version}",
                )
            nodes.append((path, schema, version))
        for key in sorted(value):
            nodes.extend(_schema_nodes(value[key], path=_join_path(path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            nodes.extend(_schema_nodes(item, path=_join_path(path, index)))
    return nodes


def _synthetic_class(value: Mapping[str, object]) -> str | None:
    marker = value.get("fixture_class", _MISSING)
    if marker is not _MISSING:
        if marker is None:
            return None
        if not isinstance(marker, str):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                "/fixture_class",
                "fixture_class must be text",
            )
        return marker
    metadata = value.get("fixture_metadata")
    if isinstance(metadata, Mapping) and "artifact_class" in metadata:
        marker = metadata["artifact_class"]
        if marker is None:
            return None
        if not isinstance(marker, str):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                "/fixture_metadata/artifact_class",
                "artifact_class must be text",
            )
        return marker
    return None


def _classification(
    value: Mapping[str, object], nested: Mapping[str, object] | None = None
) -> tuple[str | None, str | None]:
    candidates = (value, nested) if nested is not None else (value,)
    data_class: str | None = None
    fixture_class: str | None = None
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if "data_class" in candidate:
            candidate_data_class = candidate["data_class"]
            if candidate_data_class is None:
                continue
            if not isinstance(candidate_data_class, str):
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    "/data_class",
                    "data_class must be text",
                )
            if data_class is None:
                data_class = candidate_data_class
        candidate_fixture_class = _synthetic_class(candidate)
        if candidate_fixture_class is not None and fixture_class is None:
            fixture_class = candidate_fixture_class
    return data_class, fixture_class


def _require_special_keys(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    path: str,
) -> None:
    if set(value) != required:
        raise BundleDiffError(
            BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
            path,
            "special artifact fields do not match the registered shape",
        )


def _optional_special_version(value: Mapping[str, object], *, path: str) -> None:
    if "schema_version" not in value:
        return
    version = value["schema_version"]
    if type(version) is not int:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "schema_version must be an integer",
        )
    if version != 1:
        raise BundleDiffError(
            BundleDiffErrorReason.UNSUPPORTED_SCHEMA_VERSION,
            path,
            "special artifact type supports schema version 1 only",
        )


def _nonnegative_int(value: object, *, path: str) -> int:
    if type(value) is not int or value < 0:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "must be a non-negative integer",
        )
    return value


def _optional_nonnegative_int(value: object, *, path: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, path=path)


def _finite_number(value: object, *, path: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "must be a finite number",
        )
    return float(value)


def _optional_finite_number(value: object, *, path: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, path=path)


def _validate_qfast_profile(
    value: Mapping[str, object],
    *,
    event_count: int,
    path: str,
) -> str:
    _require_special_keys(
        value,
        required=frozenset(
            {
                "status",
                "claim",
                "event_count",
                "metrics",
                "strongest_baseline",
                "candidate_advantage",
                "leave_best_out_mean",
                "reject_reasons",
            }
        ),
        path=path,
    )
    status = value["status"]
    if status not in {"INSUFFICIENT_DATA", "REJECTED", "NOT_REJECTED_SMALL_SAMPLE"}:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/status",
            "unsupported Q-FAST status",
        )
    if value["claim"] != "NOT_ALPHA_EVIDENCE":
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/claim",
            "invalid Q-FAST claim boundary",
        )
    if _nonnegative_int(value["event_count"], path=f"{path}/event_count") != event_count:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/event_count",
            "top-level event count mismatch",
        )
    metrics = value["metrics"]
    if not isinstance(metrics, Mapping):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/metrics",
            "must be an object",
        )
    if event_count and "ringdown" not in metrics:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/metrics",
            "candidate method ringdown is missing",
        )
    for method in sorted(metrics):
        raw_metrics = metrics[method]
        if not method or not isinstance(raw_metrics, Mapping):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{path}/metrics",
                "method metrics must be non-empty named objects",
            )
        method_path = f"{path}/metrics/{_escape_pointer_part(method)}"
        _require_special_keys(
            raw_metrics,
            required=frozenset(
                {
                    "eligible_events",
                    "admitted_events",
                    "coverage",
                    "mean_all",
                    "median_all",
                    "mean_admitted",
                    "median_admitted",
                }
            ),
            path=method_path,
        )
        eligible = _nonnegative_int(
            raw_metrics["eligible_events"], path=f"{method_path}/eligible_events"
        )
        admitted = _nonnegative_int(
            raw_metrics["admitted_events"], path=f"{method_path}/admitted_events"
        )
        if eligible != event_count or admitted > eligible:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                method_path,
                "invalid event counts",
            )
        coverage = _finite_number(raw_metrics["coverage"], path=f"{method_path}/coverage")
        expected_coverage = admitted / eligible if eligible else 0.0
        if not 0 <= coverage <= 1 or coverage != expected_coverage:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{method_path}/coverage",
                "coverage disagrees with the event counts",
            )
        _finite_number(raw_metrics["mean_all"], path=f"{method_path}/mean_all")
        _finite_number(raw_metrics["median_all"], path=f"{method_path}/median_all")
        _optional_finite_number(raw_metrics["mean_admitted"], path=f"{method_path}/mean_admitted")
        _optional_finite_number(
            raw_metrics["median_admitted"], path=f"{method_path}/median_admitted"
        )
    strongest = value["strongest_baseline"]
    if strongest is not None and (
        not isinstance(strongest, str) or strongest not in metrics or strongest == "ringdown"
    ):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/strongest_baseline",
            "unknown baseline method",
        )
    _optional_finite_number(value["candidate_advantage"], path=f"{path}/candidate_advantage")
    _optional_finite_number(value["leave_best_out_mean"], path=f"{path}/leave_best_out_mean")
    _validate_string_list(value["reject_reasons"], path=f"{path}/reject_reasons")
    return status


def _validate_qfast_report(value: Mapping[str, object], *, path: str) -> None:
    _require_special_keys(
        value,
        required=frozenset(
            {
                "schema_version",
                "project",
                "product_name",
                "mode",
                "data_class",
                "claims",
                "limitations",
                "input_sha256",
                "protocol_sha256",
                "event_count",
                "latency_profiles",
                "latency_gate",
            }
        ),
        path=path,
    )
    if value["project"] != _LEGACY_REPORT_PROJECT or value["product_name"] != "Esscher":
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/project",
            "evaluation report product identity is invalid",
        )
    if value["data_class"] not in {"SYNTHETIC_CONTRACT_FIXTURE", "POINT_IN_TIME_EVENT_PANEL"}:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/data_class",
            "evaluation report data class is unsupported",
        )
    if value["claims"] != ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"]:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/claims",
            "evaluation report claim boundary is invalid",
        )
    _validate_string_list(value["limitations"], path=f"{path}/limitations")
    for field in ("input_sha256", "protocol_sha256"):
        if not _is_sha256(value[field]):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{path}/{field}",
                "must be a lowercase SHA-256 digest",
            )
    event_count = _nonnegative_int(value["event_count"], path=f"{path}/event_count")
    profiles = value["latency_profiles"]
    if not isinstance(profiles, Mapping) or not profiles:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/latency_profiles",
            "must be a non-empty object",
        )
    profile_statuses: dict[str, str] = {}
    for name in sorted(profiles):
        profile = profiles[name]
        if not name or not isinstance(profile, Mapping):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{path}/latency_profiles",
                "profiles must be non-empty named objects",
            )
        profile_path = f"{path}/latency_profiles/{_escape_pointer_part(name)}"
        _require_special_keys(
            profile,
            required=frozenset({"requested_latency_ms", "actual_latency_ms", "qfast"}),
            path=profile_path,
        )
        _nonnegative_int(
            profile["requested_latency_ms"], path=f"{profile_path}/requested_latency_ms"
        )
        actual = profile["actual_latency_ms"]
        if not isinstance(actual, Mapping):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{profile_path}/actual_latency_ms",
                "must be an object",
            )
        _require_special_keys(
            actual,
            required=frozenset({"minimum", "maximum"}),
            path=f"{profile_path}/actual_latency_ms",
        )
        minimum = _optional_nonnegative_int(
            actual["minimum"], path=f"{profile_path}/actual_latency_ms/minimum"
        )
        maximum = _optional_nonnegative_int(
            actual["maximum"], path=f"{profile_path}/actual_latency_ms/maximum"
        )
        if (minimum is None) != (maximum is None) or (
            minimum is not None and maximum is not None and minimum > maximum
        ):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{profile_path}/actual_latency_ms",
                "latency range is invalid",
            )
        if (event_count == 0) != (minimum is None):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{profile_path}/actual_latency_ms",
                "latency observations disagree with the event count",
            )
        qfast = profile["qfast"]
        if not isinstance(qfast, Mapping):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{profile_path}/qfast",
                "must be an object",
            )
        profile_statuses[name] = _validate_qfast_profile(
            qfast, event_count=event_count, path=f"{profile_path}/qfast"
        )
    gate = value["latency_gate"]
    if not isinstance(gate, Mapping):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/latency_gate",
            "must be an object",
        )
    _require_special_keys(
        gate,
        required=frozenset({"status", "required_profile", "qfast_status"}),
        path=f"{path}/latency_gate",
    )
    required_profile = gate["required_profile"]
    if not isinstance(required_profile, str) or required_profile not in profile_statuses:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/latency_gate/required_profile",
            "required profile is unavailable",
        )
    qfast_status = gate["qfast_status"]
    if qfast_status != profile_statuses[required_profile]:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/latency_gate/qfast_status",
            "gate status disagrees with the required profile",
        )
    expected_gate_status = {
        "INSUFFICIENT_DATA": "INSUFFICIENT_DATA",
        "REJECTED": "SHADOW_ONLY",
        "NOT_REJECTED_SMALL_SAMPLE": "NOT_REJECTED_SMALL_SAMPLE",
    }[qfast_status]
    if gate["status"] != expected_gate_status:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/latency_gate/status",
            "gate status is inconsistent with Q-FAST semantics",
        )


def _is_evaluation_report(value: Mapping[str, object]) -> bool:
    required = {
        "schema_version",
        "project",
        "product_name",
        "mode",
        "data_class",
        "claims",
        "limitations",
        "input_sha256",
        "protocol_sha256",
        "event_count",
        "latency_profiles",
        "latency_gate",
    }
    return (
        required <= set(value) and "schema" not in value and value.get("mode") == "OFFLINE_RESEARCH"
    )


@dataclass(frozen=True, slots=True)
class _ArtifactIdentity:
    artifact_type: str
    schema: str
    schema_version: int
    data_class: str | None
    fixture_class: str | None
    identity: str | None
    validation_status: str


@dataclass(frozen=True, slots=True)
class _LoadedArtifact:
    relative_path: str
    payload: dict[str, object]
    identity: _ArtifactIdentity
    raw_bytes: bytes

    @property
    def raw_sha256(self) -> str:
        return _sha256(self.raw_bytes)

    @property
    def canonical_sha256(self) -> str:
        return _sha256(_canonical_json(self.payload))

    @property
    def pairing_key(self) -> tuple[str, str]:
        return (self.identity.schema, self.identity.identity or self.canonical_sha256)


@dataclass(frozen=True, slots=True)
class _LoadedInput:
    kind: str
    artifacts: tuple[_LoadedArtifact, ...]


def _identity(value: dict[str, object], *, path: str) -> _ArtifactIdentity:
    nodes = _schema_nodes(value)
    direct_schema = value.get("schema", _MISSING)
    nested: Mapping[str, object] | None = None
    if direct_schema is not _MISSING:
        assert isinstance(direct_schema, str)
        version = value["schema_version"]
        assert type(version) is int
        if direct_schema == _REPORT_SCHEMA:
            _validate_diff_report(value, path=path)
        data_class, fixture_class = _classification(value)
        identity_field = _IDENTITY_FIELDS.get(direct_schema)
        identity_value = value.get(identity_field) if identity_field else None
        if identity_value is not None and not isinstance(identity_value, str):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                _join_path(path, identity_field),
                "artifact identity field must be text",
            )
        return _ArtifactIdentity(
            direct_schema,
            direct_schema,
            version,
            data_class,
            fixture_class,
            identity_value,
            STATUS_SCHEMA_RECOGNIZED,
        )

    artifact = value.get("artifact", _MISSING)
    if artifact is not _MISSING:
        if not isinstance(artifact, Mapping):
            raise BundleDiffError(
                BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
                f"{path}/artifact",
                "artifact wrapper must contain an object",
            )
        nested_schema = artifact.get("schema")
        nested_version = artifact.get("schema_version")
        if not isinstance(nested_schema, str) or type(nested_version) is not int:
            raise BundleDiffError(
                BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
                path,
                "artifact wrapper must contain a registered schema and version",
            )
        if _synthetic_class(value) != "SYNTHETIC_CONTRACT_FIXTURE":
            raise BundleDiffError(
                BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
                path,
                "artifact wrappers must declare SYNTHETIC_CONTRACT_FIXTURE",
            )
        if not isinstance(value.get("scenario"), str):
            raise BundleDiffError(
                BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
                path,
                "artifact wrappers must declare a scenario",
            )
        nested = artifact
        data_class, fixture_class = _classification(value, nested)
        return _ArtifactIdentity(
            f"{nested_schema}.fixture",
            nested_schema,
            nested_version,
            data_class,
            fixture_class,
            None,
            STATUS_SCHEMA_RECOGNIZED,
        )

    fixture_class = _synthetic_class(value)
    if (
        fixture_class == "SYNTHETIC_CONTRACT_FIXTURE"
        and isinstance(value.get("spec"), Mapping)
        and isinstance(value.get("events"), list)
    ):
        _require_special_keys(
            value,
            required=frozenset({"fixture_class", "limitations", "spec", "events"}),
            path=path,
        )
        spec = value["spec"]
        assert isinstance(spec, Mapping)
        _require_special_keys(
            spec,
            required=frozenset(
                {"hold_seconds", "minimum_events", "required_latency_profile", "latency_profiles"}
            ),
            path=f"{path}/spec",
        )
        _optional_special_version(value, path=f"{path}/schema_version")
        data_class, _ = _classification(value)
        return _ArtifactIdentity(
            "ringdown.synthetic_contract_panel",
            "ringdown.synthetic_contract_panel",
            1,
            data_class,
            fixture_class,
            None,
            STATUS_SCHEMA_RECOGNIZED,
        )

    if (
        fixture_class == "SYNTHETIC_CONTRACT_FIXTURE"
        and isinstance(value.get("fixture_metadata"), Mapping)
        and all(
            isinstance(value.get(key), Mapping)
            for key in ("evidence_manifest", "input_snapshot", "decision_template")
        )
    ):
        _require_special_keys(
            value,
            required=frozenset(
                {"fixture_metadata", "evidence_manifest", "input_snapshot", "decision_template"}
            ),
            path=path,
        )
        _optional_special_version(value, path=f"{path}/schema_version")
        data_class, _ = _classification(value)
        return _ArtifactIdentity(
            "ringdown.frozen_research_decision_fixture",
            "ringdown.frozen_research_decision_fixture",
            1,
            data_class,
            fixture_class,
            None,
            STATUS_SCHEMA_RECOGNIZED,
        )

    if _is_evaluation_report(value):
        _validate_qfast_report(value, path=path)
        data_class, fixture_class = _classification(value)
        return _ArtifactIdentity(
            "ringdown.evaluation_report",
            "ringdown.evaluation_report",
            1,
            data_class,
            fixture_class,
            None,
            STATUS_CONTRACT_VALIDATED,
        )

    if nodes:
        raise BundleDiffError(
            BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
            path,
            "known nested schemas are not inside a registered artifact shape",
        )
    raise BundleDiffError(
        BundleDiffErrorReason.UNKNOWN_ARTIFACT_TYPE,
        path,
        "JSON object does not match a registered artifact shape",
    )


def _validated_replay_bundle(
    artifacts: tuple[_LoadedArtifact, ...],
    *,
    display_path: str,
) -> tuple[_LoadedArtifact, ...]:
    rules = [
        artifact
        for artifact in artifacts
        if artifact.identity.schema == "ringdown.earnings_replay_selection_rule"
    ]
    event_lists = [
        artifact
        for artifact in artifacts
        if artifact.identity.schema == "ringdown.frozen_earnings_event_list"
    ]
    manifests = [
        artifact
        for artifact in artifacts
        if artifact.identity.schema == "ringdown.point_in_time_evidence_manifest"
        and artifact.identity.schema_version == 2
    ]
    if len(rules) != 1 or len(event_lists) != 1 or not manifests:
        return artifacts
    try:
        validated = validate_replay_evidence_set(
            event_lists[0].raw_bytes,
            rules[0].raw_bytes,
            [artifact.raw_bytes for artifact in manifests],
        )
    except ReplayEvidenceRejected as error:
        raise BundleDiffError(
            BundleDiffErrorReason.CONTRACT_VALIDATION_FAILED,
            display_path,
            f"replay contract rejected the bundle: {error}",
        ) from error
    validated_ids = {item.event_id for item in validated}
    contract_member_ids = {id(artifact) for artifact in rules} | {
        id(artifact) for artifact in event_lists
    }
    return tuple(
        replace(
            artifact,
            identity=replace(artifact.identity, validation_status=STATUS_CONTRACT_VALIDATED),
        )
        if id(artifact) in contract_member_ids
        or (
            artifact.identity.schema == "ringdown.point_in_time_evidence_manifest"
            and artifact.identity.identity in validated_ids
        )
        else artifact
        for artifact in artifacts
    )


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _reject_path_links(path: Path, *, display_path: str) -> None:
    if any(part == ".." for part in path.parts):
        raise BundleDiffError(
            BundleDiffErrorReason.PATH_TRAVERSAL,
            display_path,
            "path traversal components are not permitted",
        )
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        current /= part
        try:
            linked = _is_reparse_point(current)
        except OSError as error:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                display_path,
                str(error),
            ) from error
        if linked:
            raise BundleDiffError(
                BundleDiffErrorReason.SYMLINK_NOT_ALLOWED,
                display_path,
                "symlink or junction path components are not permitted",
            )


def _read_file(path: Path, *, display_path: str) -> bytes:
    if _is_reparse_point(path):
        raise BundleDiffError(
            BundleDiffErrorReason.SYMLINK_NOT_ALLOWED,
            display_path,
            "symlink or junction inputs are not permitted",
        )
    try:
        with path.open("rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise BundleDiffError(
                    BundleDiffErrorReason.UNSUPPORTED_FILE,
                    display_path,
                    "input must be a regular file",
                )
            return handle.read()
    except BundleDiffError:
        raise
    except FileNotFoundError as error:
        raise BundleDiffError(
            BundleDiffErrorReason.INPUT_NOT_FOUND,
            display_path,
            "input file does not exist",
        ) from error
    except OSError as error:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT, display_path, str(error)
        ) from error


def _safe_relative_path(relative: Path, *, display_path: str) -> str:
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise BundleDiffError(
            BundleDiffErrorReason.PATH_TRAVERSAL,
            display_path,
            "bundle paths must remain relative to the bundle root",
        )
    return relative.as_posix()


def _load_directory(root: Path, *, display_path: str) -> _LoadedInput:
    _reject_path_links(root, display_path=display_path)
    if not root.exists():
        raise BundleDiffError(
            BundleDiffErrorReason.INPUT_NOT_FOUND, display_path, "bundle does not exist"
        )
    if not root.is_dir():
        raise BundleDiffError(
            BundleDiffErrorReason.INPUT_KIND_MISMATCH,
            display_path,
            "expected a directory bundle",
        )
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise BundleDiffError(
            BundleDiffErrorReason.INPUT_NOT_FOUND, display_path, str(error)
        ) from error

    files: list[tuple[str, Path]] = []

    def onerror(error: OSError) -> None:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            display_path,
            f"cannot traverse bundle: {error}",
        ) from error

    try:
        walker = os.walk(root, topdown=True, followlinks=False, onerror=onerror)
        for current, directories, filenames in walker:
            current_path = Path(current)
            directories.sort()
            filenames.sort()
            for directory in directories:
                candidate = current_path / directory
                if _is_reparse_point(candidate):
                    relative = candidate.relative_to(root)
                    raise BundleDiffError(
                        BundleDiffErrorReason.SYMLINK_NOT_ALLOWED,
                        f"{display_path}/{relative.as_posix()}",
                        "symlink or junction directories are not permitted",
                    )
            for filename in filenames:
                candidate = current_path / filename
                relative = candidate.relative_to(root)
                relative_text = _safe_relative_path(
                    relative,
                    display_path=f"{display_path}/{relative.as_posix()}",
                )
                if _is_reparse_point(candidate):
                    raise BundleDiffError(
                        BundleDiffErrorReason.SYMLINK_NOT_ALLOWED,
                        f"{display_path}/{relative_text}",
                        "symlink or junction files are not permitted",
                    )
                if not candidate.is_file():
                    raise BundleDiffError(
                        BundleDiffErrorReason.UNSUPPORTED_FILE,
                        f"{display_path}/{relative_text}",
                        "bundle members must be regular files",
                    )
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError as error:
                    raise BundleDiffError(
                        BundleDiffErrorReason.INPUT_NOT_FOUND,
                        f"{display_path}/{relative_text}",
                        str(error),
                    ) from error
                if not resolved.is_relative_to(resolved_root):
                    raise BundleDiffError(
                        BundleDiffErrorReason.PATH_TRAVERSAL,
                        f"{display_path}/{relative_text}",
                        "resolved file escapes the bundle root",
                    )
                if candidate.suffix.casefold() != _JSON_SUFFIX:
                    raise BundleDiffError(
                        BundleDiffErrorReason.UNSUPPORTED_FILE,
                        f"{display_path}/{relative_text}",
                        "bundle members must be .json files",
                    )
                files.append((relative_text, candidate))
    except BundleDiffError:
        raise
    except (OSError, RecursionError) as error:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT, display_path, str(error)
        ) from error

    if not files:
        raise BundleDiffError(
            BundleDiffErrorReason.EMPTY_BUNDLE,
            display_path,
            "bundle contains no JSON artifacts",
        )

    loaded: list[_LoadedArtifact] = []
    for relative_text, candidate in sorted(files):
        member_path = f"{display_path}/{relative_text}"
        raw = _read_file(candidate, display_path=member_path)
        payload = _parse_json(raw, path=member_path)
        try:
            identity = _identity(payload, path=member_path)
        except RecursionError as error:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                member_path,
                "artifact nesting exceeds the supported depth",
            ) from error
        loaded.append(_LoadedArtifact(relative_text, payload, identity, raw))
    return _LoadedInput(
        "bundle", _validated_replay_bundle(tuple(loaded), display_path=display_path)
    )


def _load_input(value: bytes | os.PathLike[str] | str, *, display_path: str) -> _LoadedInput:
    if type(value) is bytes:
        payload = _parse_json(value, path=display_path)
        try:
            identity = _identity(payload, path=display_path)
        except RecursionError as error:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                display_path,
                "artifact nesting exceeds the supported depth",
            ) from error
        return _LoadedInput(
            "artifact",
            (_LoadedArtifact("artifact.json", payload, identity, value),),
        )
    if not isinstance(value, (str, os.PathLike)):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            display_path,
            "input must be immutable bytes or a filesystem path",
        )
    try:
        path = Path(value)
        _reject_path_links(path, display_path=display_path)
        if path.is_dir():
            return _load_directory(path, display_path=display_path)
        if not path.exists():
            raise BundleDiffError(
                BundleDiffErrorReason.INPUT_NOT_FOUND,
                display_path,
                "input does not exist",
            )
        if path.suffix.casefold() != _JSON_SUFFIX:
            raise BundleDiffError(
                BundleDiffErrorReason.UNSUPPORTED_FILE,
                display_path,
                "artifact files must use the .json suffix",
            )
        raw = _read_file(path, display_path=display_path)
        payload = _parse_json(raw, path=display_path)
        identity = _identity(payload, path=display_path)
    except BundleDiffError:
        raise
    except (OSError, TypeError, RecursionError) as error:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT, display_path, str(error)
        ) from error
    return _LoadedInput(
        "artifact",
        (_LoadedArtifact("artifact.json", payload, identity, raw),),
    )


def _category(path: str) -> str:
    key = _key_from_path(path).casefold()
    path_lower = path.casefold()
    if key in {"schema", "schema_version"}:
        return "SCHEMA"
    if key in {"data_class", "fixture_class", "artifact_class"}:
        return "CLASSIFICATION"
    if key in {"event_id", "event_ids"} or key.startswith("event:"):
        return "EVENT_ID"
    if key == "claim" or key in _SET_LIKE_LIST_KEYS - {"missing_or_conflicting_evidence"}:
        return "LIMITATION" if key == "limitations" else "CLAIM"
    if "sha256" in key or key.endswith("_hash") or key == "hash":
        return "HASH"
    if key in _TIME_KEYS or any(
        term in path_lower for term in ("_at", "timestamp", "precision", "timezone", "interval")
    ):
        return "TIMING"
    if "/latency_gate/" in path_lower:
        return "LATENCY"
    if "/field_source_refs/" in path_lower or key in {
        "field_source_refs",
        "source_refs",
    }:
        return "PROVENANCE"
    if key in _VERDICT_KEYS or any(
        term in path_lower for term in ("/candidate", "/baseline", "/qfast", "/verdict")
    ):
        return "VERDICT"
    if key in _LATENCY_KEYS or "/latency_profiles/" in path_lower:
        return "LATENCY"
    if key.startswith(("record:", "feature:")) or "/records/" in path_lower:
        return "PROVENANCE"
    if key in _PROVENANCE_KEYS or any(
        term in path_lower for term in ("source", "provenance", "entitlement", "redistribution")
    ):
        return "PROVENANCE"
    if key in _INCLUSION_KEYS or "inclusion" in key or "exclusion" in key:
        return "INCLUSION"
    return "FIELD"


def _delta(
    category: str,
    path: str,
    change: str,
    left: object,
    right: object,
) -> dict[str, object]:
    return {
        "category": category,
        "path": path or "/",
        "change": change,
        "left_present": left is not _MISSING,
        "right_present": right is not _MISSING,
        "left": None if left is _MISSING else left,
        "right": None if right is _MISSING else right,
    }


def _compare_set_like(
    left: list[object],
    right: list[object],
    path: str,
    deltas: list[dict[str, object]],
) -> bool:
    if not all(isinstance(item, str) for item in left) or not all(
        isinstance(item, str) for item in right
    ):
        return False
    category = _category(path)
    left_values = {item for item in left if isinstance(item, str)}
    right_values = {item for item in right if isinstance(item, str)}
    for item in sorted(left_values - right_values):
        deltas.append(_delta(category, _join_path(path, item), "REMOVED", item, _MISSING))
    for item in sorted(right_values - left_values):
        deltas.append(_delta(category, _join_path(path, item), "ADDED", _MISSING, item))
    return True


def _compare_event_id_list(
    left: list[str],
    right: list[str],
    path: str,
    deltas: list[dict[str, object]],
) -> None:
    for event_id in sorted(set(left) - set(right)):
        deltas.append(
            _delta("EVENT_ID", _join_path(path, f"event:{event_id}"), "REMOVED", event_id, _MISSING)
        )
    for event_id in sorted(set(right) - set(left)):
        deltas.append(
            _delta("EVENT_ID", _join_path(path, f"event:{event_id}"), "ADDED", _MISSING, event_id)
        )
    if set(left) == set(right) and left != right:
        deltas.append(_delta("EVENT_ID", path, "CHANGED", left, right))


def _event_identifier(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    event_id = value.get("event_id")
    if isinstance(event_id, str):
        return event_id
    decision = value.get("decision")
    if isinstance(decision, Mapping) and isinstance(decision.get("event_id"), str):
        return decision["event_id"]
    return None


def _event_collection(
    left: list[object],
    right: list[object],
    path: str,
    deltas: list[dict[str, object]],
) -> bool:
    left_map: dict[str, object] = {}
    right_map: dict[str, object] = {}
    for side, values, target in (("left", left, left_map), ("right", right, right_map)):
        for index, value in enumerate(values):
            event_id = _event_identifier(value)
            if event_id is None:
                return False
            if event_id in target:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{path}/{index}",
                    f"duplicate event ID {event_id!r} in {side} event collection",
                )
            target[event_id] = value
    for event_id in sorted(set(left_map) | set(right_map)):
        left_value = left_map.get(event_id, _MISSING)
        right_value = right_map.get(event_id, _MISSING)
        event_path = _join_path(path, f"event:{event_id}")
        if left_value is _MISSING or right_value is _MISSING:
            deltas.append(
                _delta(
                    "EVENT_ID",
                    event_path,
                    "ADDED" if right_value is not _MISSING else "REMOVED",
                    left_value,
                    right_value,
                )
            )
        else:
            _compare_values(left_value, right_value, event_path, deltas)
    return True


def _keyed_collection(
    left: list[object],
    right: list[object],
    path: str,
    deltas: list[dict[str, object]],
    *,
    identifier: str,
    prefix: str,
) -> bool:
    left_map: dict[str, object] = {}
    right_map: dict[str, object] = {}
    for side, values, target in (("left", left, left_map), ("right", right, right_map)):
        for index, value in enumerate(values):
            if not isinstance(value, Mapping):
                return False
            item_id = value.get(identifier)
            if not isinstance(item_id, str) or not item_id:
                return False
            if item_id in target:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{path}/{index}",
                    f"duplicate {identifier} {item_id!r} in {side} collection",
                )
            target[item_id] = value
    for item_id in sorted(set(left_map) | set(right_map)):
        left_value = left_map.get(item_id, _MISSING)
        right_value = right_map.get(item_id, _MISSING)
        item_path = _join_path(path, f"{prefix}:{item_id}")
        if left_value is _MISSING or right_value is _MISSING:
            deltas.append(
                _delta(
                    _category(item_path),
                    item_path,
                    "ADDED" if right_value is not _MISSING else "REMOVED",
                    left_value,
                    right_value,
                )
            )
        else:
            _compare_values(left_value, right_value, item_path, deltas)
    return True


def _json_values_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _compare_values(
    left: object,
    right: object,
    path: str,
    deltas: list[dict[str, object]],
    *,
    set_like_lists: bool = False,
) -> None:
    if left is _MISSING or right is _MISSING:
        if left is right:
            return
        deltas.append(
            _delta(
                _category(path),
                path,
                "ADDED" if right is not _MISSING else "REMOVED",
                left,
                right,
            )
        )
        return
    key = _key_from_path(path).casefold()
    if key == "event_ids":
        _compare_event_id_list(left, right, path, deltas)
        return
    if (
        key in _SET_LIKE_LIST_KEYS
        and isinstance(left, list)
        and isinstance(right, list)
        and _compare_set_like(left, right, path, deltas)
    ):
        return
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        for child_key in sorted(set(left) | set(right)):
            child_path = _join_path(path, child_key)
            _compare_values(
                left.get(child_key, _MISSING),
                right.get(child_key, _MISSING),
                child_path,
                deltas,
                set_like_lists=key == "field_source_refs",
            )
        return
    if isinstance(left, list) and isinstance(right, list):
        if key == "events" and _event_collection(left, right, path, deltas):
            return
        keyed = _KEYED_LISTS.get(key)
        if keyed is not None and _keyed_collection(
            left,
            right,
            path,
            deltas,
            identifier=keyed,
            prefix="record" if key == "records" else "feature",
        ):
            return
        if set_like_lists and _compare_set_like(left, right, path, deltas):
            return
        for index in range(max(len(left), len(right))):
            child_path = _join_path(path, index)
            left_item = left[index] if index < len(left) else _MISSING
            right_item = right[index] if index < len(right) else _MISSING
            _compare_values(left_item, right_item, child_path, deltas)
        return
    if not _json_values_equal(left, right):
        deltas.append(_delta(_category(path), path, "CHANGED", left, right))


def _sort_deltas(deltas: list[dict[str, object]]) -> list[dict[str, object]]:
    ordered = sorted(
        deltas,
        key=lambda item: (
            str(item["category"]),
            str(item["path"]),
            str(item["change"]),
            _canonical_json(item.get("left")),
            _canonical_json(item.get("right")),
        ),
    )
    unique: list[dict[str, object]] = []
    seen: set[bytes] = set()
    for delta in ordered:
        identity = _canonical_json(delta)
        if identity not in seen:
            seen.add(identity)
            unique.append(delta)
    return unique


def _artifact_metadata(artifact: _LoadedArtifact) -> dict[str, object]:
    return {
        "path": artifact.relative_path,
        "artifact_type": artifact.identity.artifact_type,
        "schema": artifact.identity.schema,
        "schema_version": artifact.identity.schema_version,
        "data_class": artifact.identity.data_class,
        "fixture_class": artifact.identity.fixture_class,
        "identity": artifact.identity.identity,
        "raw_sha256": artifact.raw_sha256,
        "canonical_sha256": artifact.canonical_sha256,
        "validation_status": artifact.identity.validation_status,
    }


def _collect_event_ids(value: object) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            event_id = item.get("event_id")
            if isinstance(event_id, str) and event_id:
                found.add(event_id)
            event_ids = item.get("event_ids")
            if isinstance(event_ids, list):
                found.update(event_id for event_id in event_ids if isinstance(event_id, str))
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def _pair_artifacts(
    left: tuple[_LoadedArtifact, ...],
    right: tuple[_LoadedArtifact, ...],
) -> tuple[
    list[tuple[_LoadedArtifact, _LoadedArtifact]], list[_LoadedArtifact], list[_LoadedArtifact]
]:
    left_by_path = {artifact.relative_path: artifact for artifact in left}
    right_by_path = {artifact.relative_path: artifact for artifact in right}
    common_paths = sorted(set(left_by_path) & set(right_by_path))
    pairs = [(left_by_path[name], right_by_path[name]) for name in common_paths]
    unmatched_left = [left_by_path[name] for name in sorted(set(left_by_path) - set(common_paths))]
    unmatched_right = [
        right_by_path[name] for name in sorted(set(right_by_path) - set(common_paths))
    ]

    left_counts: dict[tuple[str, str], int] = {}
    right_counts: dict[tuple[str, str], int] = {}
    for artifact in unmatched_left:
        left_counts[artifact.pairing_key] = left_counts.get(artifact.pairing_key, 0) + 1
    for artifact in unmatched_right:
        right_counts[artifact.pairing_key] = right_counts.get(artifact.pairing_key, 0) + 1
    right_by_key = {artifact.pairing_key: artifact for artifact in unmatched_right}
    matched_keys = {
        key for key, count in left_counts.items() if count == 1 and right_counts.get(key) == 1
    }
    for artifact in unmatched_left:
        if artifact.pairing_key in matched_keys:
            pairs.append((artifact, right_by_key[artifact.pairing_key]))
    pairs.sort(key=lambda pair: (pair[0].relative_path, pair[1].relative_path))
    removed = [item for item in unmatched_left if item.pairing_key not in matched_keys]
    added = [item for item in unmatched_right if item.pairing_key not in matched_keys]
    return pairs, removed, added


def _has_explicit_schema(value: Mapping[str, object]) -> bool:
    artifact = value.get("artifact")
    return "schema" in value or (isinstance(artifact, Mapping) and "schema" in artifact)


def _compare_artifact_pair(
    left: _LoadedArtifact,
    right: _LoadedArtifact,
    base_path: str,
    deltas: list[dict[str, object]],
) -> None:
    if left.identity.artifact_type != right.identity.artifact_type and not (
        _has_explicit_schema(left.payload) or _has_explicit_schema(right.payload)
    ):
        deltas.append(
            _delta(
                "SCHEMA",
                _join_path(base_path, "@artifact_type"),
                "CHANGED",
                left.identity.artifact_type,
                right.identity.artifact_type,
            )
        )
    if left.raw_sha256 != right.raw_sha256:
        deltas.append(
            _delta(
                "IDENTITY",
                _join_path(base_path, "@raw_bytes_sha256"),
                "CHANGED",
                left.raw_sha256,
                right.raw_sha256,
            )
        )
    _compare_values(left.payload, right.payload, base_path, deltas)


def _side_descriptor(loaded: _LoadedInput, event_ids: tuple[str, ...]) -> dict[str, object]:
    if loaded.kind == "artifact":
        only = loaded.artifacts[0]
        raw_sha256 = only.raw_sha256
        canonical_sha256 = only.canonical_sha256
    else:
        raw_sha256 = _sha256(
            _canonical_json(
                {artifact.relative_path: artifact.raw_sha256 for artifact in loaded.artifacts}
            )
        )
        canonical_sha256 = _sha256(
            _canonical_json(
                {artifact.relative_path: artifact.payload for artifact in loaded.artifacts}
            )
        )
    statuses = {artifact.identity.validation_status for artifact in loaded.artifacts}
    if statuses == {STATUS_CONTRACT_VALIDATED}:
        validation_status = STATUS_CONTRACT_VALIDATED
    elif STATUS_CONTRACT_VALIDATED in statuses:
        validation_status = STATUS_PARTIALLY_CONTRACT_VALIDATED
    else:
        validation_status = STATUS_SCHEMA_RECOGNIZED
    return {
        "kind": loaded.kind,
        "validation_status": validation_status,
        "raw_sha256": raw_sha256,
        "canonical_sha256": canonical_sha256,
        "event_ids": list(event_ids),
        "artifacts": [_artifact_metadata(artifact) for artifact in loaded.artifacts],
    }


def _compare_loaded(left: _LoadedInput, right: _LoadedInput) -> dict[str, object]:
    if left.kind != right.kind:
        raise BundleDiffError(
            BundleDiffErrorReason.INPUT_KIND_MISMATCH,
            "inputs",
            "both inputs must be artifacts or both must be directory bundles",
        )
    deltas: list[dict[str, object]] = []
    if left.kind == "artifact":
        pairs = [(left.artifacts[0], right.artifacts[0])]
        removed: list[_LoadedArtifact] = []
        added: list[_LoadedArtifact] = []
    else:
        pairs, removed, added = _pair_artifacts(left.artifacts, right.artifacts)

    for old, new in pairs:
        if left.kind == "bundle" and old.relative_path != new.relative_path:
            deltas.append(
                _delta(
                    "ARTIFACT",
                    "/files",
                    "RENAMED",
                    old.relative_path,
                    new.relative_path,
                )
            )
            base_path = (
                f"/files/{_escape_pointer_part(old.relative_path)}"
                f" -> {_escape_pointer_part(new.relative_path)}"
            )
        elif left.kind == "bundle":
            base_path = f"/files/{_escape_pointer_part(old.relative_path)}"
        else:
            base_path = ""
        _compare_artifact_pair(old, new, base_path, deltas)

    for artifact in removed:
        deltas.append(
            _delta(
                "FILE",
                f"/files/{_escape_pointer_part(artifact.relative_path)}",
                "REMOVED",
                _artifact_metadata(artifact),
                _MISSING,
            )
        )
    for artifact in added:
        deltas.append(
            _delta(
                "FILE",
                f"/files/{_escape_pointer_part(artifact.relative_path)}",
                "ADDED",
                _MISSING,
                _artifact_metadata(artifact),
            )
        )

    left_event_ids = _collect_event_ids(
        {artifact.relative_path: artifact.payload for artifact in left.artifacts}
    )
    right_event_ids = _collect_event_ids(
        {artifact.relative_path: artifact.payload for artifact in right.artifacts}
    )
    left_has_event_list = any(
        isinstance(artifact.payload.get("event_ids"), list) for artifact in left.artifacts
    )
    right_has_event_list = any(
        isinstance(artifact.payload.get("event_ids"), list) for artifact in right.artifacts
    )
    if not left_has_event_list and not right_has_event_list:
        for event_id in sorted(set(left_event_ids) - set(right_event_ids)):
            deltas.append(
                _delta(
                    "EVENT_ID",
                    _join_path("/events", f"event:{event_id}"),
                    "REMOVED",
                    event_id,
                    _MISSING,
                )
            )
        for event_id in sorted(set(right_event_ids) - set(left_event_ids)):
            deltas.append(
                _delta(
                    "EVENT_ID",
                    _join_path("/events", f"event:{event_id}"),
                    "ADDED",
                    _MISSING,
                    event_id,
                )
            )

    sorted_deltas = _sort_deltas(deltas)
    semantically_equal = all(
        str(delta["category"]) in _NON_SEMANTIC_CATEGORIES for delta in sorted_deltas
    )
    return {
        "schema": _REPORT_SCHEMA,
        "schema_version": _REPORT_VERSION,
        "data_class": _REPORT_DATA_CLASS,
        "claims": list(_REPORT_CLAIMS),
        "left": _side_descriptor(left, left_event_ids),
        "right": _side_descriptor(right, right_event_ids),
        "identical": not sorted_deltas,
        "semantically_equal": semantically_equal,
        "deltas": sorted_deltas,
    }


def _validate_diff_report(value: Mapping[str, object], *, path: str) -> None:
    _require_special_keys(
        value,
        required=frozenset(
            {
                "schema",
                "schema_version",
                "data_class",
                "claims",
                "left",
                "right",
                "identical",
                "semantically_equal",
                "deltas",
            }
        ),
        path=path,
    )
    if value["data_class"] != _REPORT_DATA_CLASS:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/data_class",
            "report data class is invalid",
        )
    if value["claims"] != _REPORT_CLAIMS:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/claims",
            "report claim boundary is invalid",
        )
    for side in ("left", "right"):
        side_value = value[side]
        if not isinstance(side_value, Mapping):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{path}/{side}",
                "report side must be an object",
            )
        _require_special_keys(
            side_value,
            required=frozenset(
                {
                    "kind",
                    "validation_status",
                    "raw_sha256",
                    "canonical_sha256",
                    "event_ids",
                    "artifacts",
                }
            ),
            path=f"{path}/{side}",
        )
        if side_value["kind"] not in {"artifact", "bundle"}:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{path}/{side}/kind",
                "report side kind is invalid",
            )
        if side_value["validation_status"] not in _VALIDATION_STATUSES:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{path}/{side}/validation_status",
                "validation status is invalid",
            )
        for field in ("raw_sha256", "canonical_sha256"):
            if not _is_sha256(side_value[field]):
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{path}/{side}/{field}",
                    "must be a lowercase SHA-256 digest",
                )
        _validate_string_list(side_value["event_ids"], path=f"{path}/{side}/event_ids")
        artifacts = side_value["artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{path}/{side}/artifacts",
                "report side must contain artifacts",
            )
        for index, artifact in enumerate(artifacts):
            artifact_path = f"{path}/{side}/artifacts/{index}"
            if not isinstance(artifact, Mapping):
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    artifact_path,
                    "artifact metadata must be an object",
                )
            _require_special_keys(
                artifact,
                required=frozenset(
                    {
                        "path",
                        "artifact_type",
                        "schema",
                        "schema_version",
                        "data_class",
                        "fixture_class",
                        "identity",
                        "raw_sha256",
                        "canonical_sha256",
                        "validation_status",
                    }
                ),
                path=artifact_path,
            )
            for field in ("path", "artifact_type", "schema"):
                if not isinstance(artifact[field], str):
                    raise BundleDiffError(
                        BundleDiffErrorReason.INVALID_DOCUMENT,
                        f"{artifact_path}/{field}",
                        "artifact metadata field must be text",
                    )
            if not artifact["path"] or any(
                part in {".", ".."} for part in Path(artifact["path"]).parts
            ):
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{artifact_path}/path",
                    "artifact metadata path must be a non-empty normalized relative path",
                )
            if artifact["artifact_type"] not in {
                artifact["schema"],
                f"{artifact['schema']}.fixture",
            }:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{artifact_path}/artifact_type",
                    "artifact metadata type does not match its schema",
                )
            if type(artifact["schema_version"]) is not int:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{artifact_path}/schema_version",
                    "artifact schema_version must be an integer",
                )
            for field in ("data_class", "fixture_class", "identity"):
                if artifact[field] is not None and not isinstance(artifact[field], str):
                    raise BundleDiffError(
                        BundleDiffErrorReason.INVALID_DOCUMENT,
                        f"{artifact_path}/{field}",
                        "nullable artifact metadata field must be text or null",
                    )
            for field in ("raw_sha256", "canonical_sha256"):
                if not _is_sha256(artifact[field]):
                    raise BundleDiffError(
                        BundleDiffErrorReason.INVALID_DOCUMENT,
                        f"{artifact_path}/{field}",
                        "must be a lowercase SHA-256 digest",
                    )
            if artifact["validation_status"] not in _VALIDATION_STATUSES:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{artifact_path}/validation_status",
                    "validation status is invalid",
                )
    if (
        type(value["identical"]) is not bool
        or type(value["semantically_equal"]) is not bool
        or not isinstance(value["deltas"], list)
    ):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "report flags must be boolean and deltas must be a list",
        )
    allowed_categories = {
        "SCHEMA",
        "CLASSIFICATION",
        "EVENT_ID",
        "CLAIM",
        "LIMITATION",
        "HASH",
        "TIMING",
        "LATENCY",
        "VERDICT",
        "PROVENANCE",
        "INCLUSION",
        "FIELD",
        "IDENTITY",
        "ARTIFACT",
        "FILE",
    }
    allowed_changes = {"ADDED", "REMOVED", "CHANGED", "RENAMED"}
    delta_fields = frozenset(
        {"category", "path", "change", "left_present", "right_present", "left", "right"}
    )
    for index, delta in enumerate(value["deltas"]):
        delta_path = f"{path}/deltas/{index}"
        if not isinstance(delta, Mapping):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                delta_path,
                "delta must be an object",
            )
        _require_special_keys(delta, required=delta_fields, path=delta_path)
        for field in ("category", "path", "change"):
            if not isinstance(delta[field], str) or not delta[field]:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{delta_path}/{field}",
                    "delta text fields must be non-empty text",
                )
        if delta["category"] not in allowed_categories:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{delta_path}/category",
                "delta category is invalid",
            )
        if delta["change"] not in allowed_changes:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{delta_path}/change",
                "delta change kind is invalid",
            )
        for field in ("left_present", "right_present"):
            if type(delta[field]) is not bool:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{delta_path}/{field}",
                    "delta presence field must be boolean",
                )
        if (not delta["left_present"] and delta["left"] is not None) or (
            not delta["right_present"] and delta["right"] is not None
        ):
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                delta_path,
                "absent delta values must be represented as null",
            )
    if value["identical"] != (len(value["deltas"]) == 0):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/identical",
            "identical must agree with whether deltas are present",
        )
    semantic_deltas = [
        delta
        for delta in value["deltas"]
        if isinstance(delta, Mapping) and delta.get("category") not in _NON_SEMANTIC_CATEGORIES
    ]
    if value["semantically_equal"] != (len(semantic_deltas) == 0):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/semantically_equal",
            "semantically_equal must agree with the semantic deltas",
        )


def compare_artifacts(left: bytes, right: bytes) -> dict[str, object]:
    """Compare two strict-JSON artifact byte strings offline."""

    return _compare_loaded(
        _load_input(left, display_path="left"),
        _load_input(right, display_path="right"),
    )


def compare_paths(
    left: os.PathLike[str] | str,
    right: os.PathLike[str] | str,
) -> dict[str, object]:
    """Compare two `.json` files or two JSON directory bundles offline."""

    return _compare_loaded(
        _load_input(left, display_path="left"),
        _load_input(right, display_path="right"),
    )


def compare(
    left: bytes | os.PathLike[str] | str,
    right: bytes | os.PathLike[str] | str,
) -> dict[str, object]:
    """Compare either two immutable artifact bytes or two filesystem inputs."""

    if type(left) is bytes and type(right) is bytes:
        return compare_artifacts(left, right)
    if type(left) is bytes or type(right) is bytes:
        raise BundleDiffError(
            BundleDiffErrorReason.INPUT_KIND_MISMATCH,
            "inputs",
            "both inputs must be bytes or both inputs must be filesystem paths",
        )
    return compare_paths(left, right)


def _validate_output_path(output: Path) -> None:
    _reject_path_links(output, display_path="output")
    if output.exists() and output.is_dir():
        raise BundleDiffError(
            BundleDiffErrorReason.OUTPUT_ERROR,
            "output",
            "output must be a file",
        )
    if not output.parent.exists() or not output.parent.is_dir():
        raise BundleDiffError(
            BundleDiffErrorReason.OUTPUT_ERROR,
            "output",
            "output parent directory must already exist",
        )


def _write_output(output: os.PathLike[str] | str, rendered: bytes) -> None:
    output_path = Path(output)
    _validate_output_path(output_path)
    try:
        output_path.write_bytes(rendered)
    except OSError as error:
        raise BundleDiffError(BundleDiffErrorReason.OUTPUT_ERROR, "output", str(error)) from error


def write_report(
    left: bytes | os.PathLike[str] | str,
    right: bytes | os.PathLike[str] | str,
    output: os.PathLike[str] | str,
) -> None:
    """Write one canonical report to an explicitly requested output file."""

    _write_output(output, canonical_report_bytes(compare(left, right)))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ringdown_market.audit.bundle_diff",
        description="Compare two known Esscher JSON artifacts or directory bundles offline.",
    )
    parser.add_argument("left", type=Path, help="left JSON artifact or directory bundle")
    parser.add_argument("right", type=Path, help="right JSON artifact or directory bundle")
    parser.add_argument("--output", type=Path, help="write the canonical report to this file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the isolated module entry point without touching the product CLI."""

    args = _build_parser().parse_args(argv)
    try:
        rendered = canonical_report_bytes(compare_paths(args.left, args.right))
        if args.output is None:
            sys.stdout.buffer.write(rendered)
        else:
            _write_output(args.output, rendered)
    except BundleDiffError as error:
        print(str(error), file=sys.stderr)
        return 2
    except OSError as error:
        print(
            f"{BundleDiffErrorReason.OUTPUT_ERROR.value} at output: {error}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
