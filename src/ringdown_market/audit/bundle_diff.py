"""Deterministic, offline comparison of known Esscher JSON artifacts.

The comparison layer copies values from already-produced artifacts. It never
reruns research, contacts a source, starts a broker session, or infers a
financial conclusion from a changed value.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final


class BundleDiffErrorReason(StrEnum):
    """Stable machine-readable reasons for rejecting comparison inputs."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    UNEXPECTED_TOP_LEVEL_SHAPE = "UNEXPECTED_TOP_LEVEL_SHAPE"
    UNKNOWN_ARTIFACT_TYPE = "UNKNOWN_ARTIFACT_TYPE"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
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
_LABEL_LIST_KEYS: Final[frozenset[str]] = frozenset(
    {"claim_boundary", "claims", "data_qualifiers", "limitations"}
)
_TIME_KEYS: Final[frozenset[str]] = frozenset(
    {
        "accepted_at",
        "approved_at",
        "close_filled_at",
        "decision_cutoff",
        "expires_at",
        "feature_computed_at",
        "feature_snapshot_at",
        "final_flat_observed_at",
        "frozen_at",
        "not_before",
        "observed_at",
        "open_filled_at",
        "published_at",
        "published_at_interval",
        "published_date_or_interval",
        "published_at_type",
        "retrieved_at",
        "event_category",
        "entry_session_policy",
        "observation_type",
        "scheduled_event_at",
        "session_id",
        "session_close_at",
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
        "publisher",
        "redistribution_note",
        "redistribution_status",
        "source_kind",
        "source_refs",
        "source_timezone",
        "source_url",
        "issuer_release_url",
        "published_at_precision",
        "published_at_type",
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
        "strongest_baseline",
        "status",
        "verdict",
    }
)
_LATENCY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "actual_latency_ms",
        "latency_gate",
        "latency_ms",
        "latency_profiles",
        "latency_mode",
        "qlatency_status",
        "required_latency_profile",
        "required_profile",
        "requested_latency_ms",
    }
)


class _DuplicateKey(ValueError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class _NonFiniteNumber(ValueError):
    pass


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


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
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
            if key in _LABEL_LIST_KEYS or key in {"event_ids", "source_refs"}:
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


def _validate_diff_report(value: Mapping[str, object], *, path: str) -> None:
    _require_special_keys(
        value,
        required=frozenset({"schema", "schema_version", "left", "right", "identical", "deltas"}),
        path=path,
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
            required=frozenset({"kind", "event_ids", "artifacts"}),
            path=f"{path}/{side}",
        )
        if side_value["kind"] not in {"artifact", "bundle"}:
            raise BundleDiffError(
                BundleDiffErrorReason.INVALID_DOCUMENT,
                f"{path}/{side}/kind",
                "report side kind is invalid",
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
            for field in ("data_class", "fixture_class"):
                if artifact[field] is not None and not isinstance(artifact[field], str):
                    raise BundleDiffError(
                        BundleDiffErrorReason.INVALID_DOCUMENT,
                        f"{artifact_path}/{field}",
                        "nullable artifact metadata field must be text or null",
                    )
    if type(value["identical"]) is not bool or not isinstance(value["deltas"], list):
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            path,
            "report identical must be boolean and deltas must be a list",
        )
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
            if not isinstance(delta[field], str):
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{delta_path}/{field}",
                    "delta field must be text",
                )
            if not delta[field]:
                raise BundleDiffError(
                    BundleDiffErrorReason.INVALID_DOCUMENT,
                    f"{delta_path}/{field}",
                    "delta text fields must be non-empty",
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


def _evaluation_report(value: Mapping[str, object], *, path: str) -> bool:
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
    if set(value) != required:
        return False
    version = value["schema_version"]
    if type(version) is not int:
        raise BundleDiffError(
            BundleDiffErrorReason.INVALID_DOCUMENT,
            f"{path}/schema_version",
            "schema_version must be an integer",
        )
    if version != 1:
        raise BundleDiffError(
            BundleDiffErrorReason.UNSUPPORTED_SCHEMA_VERSION,
            f"{path}/schema_version",
            "evaluation reports support schema version 1 only",
        )
    if value["mode"] != "OFFLINE_RESEARCH":
        return False
    if not isinstance(value["latency_profiles"], Mapping) or not isinstance(
        value["latency_gate"], Mapping
    ):
        return False
    return all(isinstance(value[field], str) for field in ("project", "product_name"))


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
        return _ArtifactIdentity(direct_schema, direct_schema, version, data_class, fixture_class)

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
        )

    if _evaluation_report(value, path=path):
        data_class, fixture_class = _classification(value)
        return _ArtifactIdentity(
            "ringdown.evaluation_report",
            "ringdown.evaluation_report",
            1,
            data_class,
            fixture_class,
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


@dataclass(frozen=True, slots=True)
class _ArtifactIdentity:
    artifact_type: str
    schema: str
    schema_version: int
    data_class: str | None
    fixture_class: str | None


@dataclass(frozen=True, slots=True)
class _LoadedArtifact:
    relative_path: str
    payload: dict[str, object]
    identity: _ArtifactIdentity


@dataclass(frozen=True, slots=True)
class _LoadedInput:
    kind: str
    artifacts: tuple[_LoadedArtifact, ...]


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
        loaded.append(_LoadedArtifact(relative_text, payload, identity))
    return _LoadedInput("bundle", tuple(loaded))


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
            (_LoadedArtifact("artifact.json", payload, identity),),
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
        (_LoadedArtifact("artifact.json", payload, identity),),
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
    if key == "claim" or key in _LABEL_LIST_KEYS:
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


def _compare_labels(
    left: list[str],
    right: list[str],
    path: str,
    deltas: list[dict[str, object]],
) -> None:
    category = "LIMITATION" if _key_from_path(path).casefold() == "limitations" else "CLAIM"
    left_values = set(left)
    right_values = set(right)
    for item in sorted(left_values - right_values):
        deltas.append(_delta(category, _join_path(path, item), "REMOVED", item, _MISSING))
    for item in sorted(right_values - left_values):
        deltas.append(_delta(category, _join_path(path, item), "ADDED", _MISSING, item))


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


def _event_id_list(
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
    if key in _LABEL_LIST_KEYS:
        _compare_labels(left, right, path, deltas)
        return
    if key == "event_ids":
        _event_id_list(left, right, path, deltas)
        return
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        for child_key in sorted(set(left) | set(right)):
            child_path = _join_path(path, child_key)
            _compare_values(
                left.get(child_key, _MISSING), right.get(child_key, _MISSING), child_path, deltas
            )
        return
    if isinstance(left, list) and isinstance(right, list):
        if key == "events" and _event_collection(left, right, path, deltas):
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
    }


def _has_explicit_schema(value: Mapping[str, object]) -> bool:
    artifact = value.get("artifact")
    return "schema" in value or (isinstance(artifact, Mapping) and "schema" in artifact)


def _compare_artifact_values(
    left: _LoadedArtifact,
    right: _LoadedArtifact,
    path: str,
    deltas: list[dict[str, object]],
) -> None:
    if left.identity.artifact_type != right.identity.artifact_type and not (
        _has_explicit_schema(left.payload) or _has_explicit_schema(right.payload)
    ):
        deltas.append(
            _delta(
                "SCHEMA",
                _join_path(path, "@artifact_type"),
                "CHANGED",
                left.identity.artifact_type,
                right.identity.artifact_type,
            )
        )
    _compare_values(left.payload, right.payload, path, deltas)


def _compare_loaded(left: _LoadedInput, right: _LoadedInput) -> dict[str, object]:
    if left.kind != right.kind:
        raise BundleDiffError(
            BundleDiffErrorReason.INPUT_KIND_MISMATCH,
            "inputs",
            "both inputs must be artifacts or both must be directory bundles",
        )
    deltas: list[dict[str, object]] = []
    if left.kind == "artifact":
        left_artifact = left.artifacts[0]
        right_artifact = right.artifacts[0]
        _compare_artifact_values(left_artifact, right_artifact, "", deltas)
        left_event_ids = _collect_event_ids(left_artifact.payload)
        right_event_ids = _collect_event_ids(right_artifact.payload)
    else:
        left_by_path = {artifact.relative_path: artifact for artifact in left.artifacts}
        right_by_path = {artifact.relative_path: artifact for artifact in right.artifacts}
        for relative_path in sorted(set(left_by_path) | set(right_by_path)):
            left_artifact = left_by_path.get(relative_path)
            right_artifact = right_by_path.get(relative_path)
            file_path = f"/files/{_escape_pointer_part(relative_path)}"
            if left_artifact is None or right_artifact is None:
                deltas.append(
                    _delta(
                        "FILE",
                        file_path,
                        "ADDED" if right_artifact is not None else "REMOVED",
                        _MISSING if left_artifact is None else _artifact_metadata(left_artifact),
                        _MISSING if right_artifact is None else _artifact_metadata(right_artifact),
                    )
                )
                continue
            _compare_artifact_values(left_artifact, right_artifact, file_path, deltas)
        left_event_ids = tuple(
            sorted(
                {
                    event_id
                    for artifact in left.artifacts
                    for event_id in _collect_event_ids(artifact.payload)
                }
            )
        )
        right_event_ids = tuple(
            sorted(
                {
                    event_id
                    for artifact in right.artifacts
                    for event_id in _collect_event_ids(artifact.payload)
                }
            )
        )
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
    return {
        "schema": _REPORT_SCHEMA,
        "schema_version": _REPORT_VERSION,
        "left": {
            "kind": left.kind,
            "event_ids": list(left_event_ids),
            "artifacts": [_artifact_metadata(artifact) for artifact in left.artifacts],
        },
        "right": {
            "kind": right.kind,
            "event_ids": list(right_event_ids),
            "artifacts": [_artifact_metadata(artifact) for artifact in right.artifacts],
        },
        "identical": not sorted_deltas,
        "deltas": sorted_deltas,
    }


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
    """Compare either two artifact bytes or two filesystem inputs."""

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
