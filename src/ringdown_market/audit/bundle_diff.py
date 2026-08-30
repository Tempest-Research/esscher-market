"""Deterministic, offline comparison of supported evidence and Q-FAST artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ringdown_market.contracts.replay_evidence import (
    ReplayEvidenceRejected,
    validate_replay_evidence_set,
)

_REPORT_SCHEMA = "ringdown.evidence_bundle_diff"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_REPORT_PROJECT = "Ring" + "down"
_SUPPORTED_SCHEMAS: dict[str, frozenset[int]] = {
    "ringdown.point_in_time_evidence_manifest": frozenset({1, 2}),
    "ringdown.frozen_earnings_event_list": frozenset({1}),
    "ringdown.earnings_replay_selection_rule": frozenset({1}),
}
_IDENTITY_FIELDS = {
    "ringdown.point_in_time_evidence_manifest": "event_id",
    "ringdown.frozen_earnings_event_list": "list_id",
    "ringdown.earnings_replay_selection_rule": "rule_id",
    "ringdown.qfast_report": None,
}
_KEYED_LISTS = {
    "events": "event_id",
    "records": "evidence_id",
    "feature_dependencies": "feature_id",
}
_SET_LIKE_LISTS = frozenset(
    {
        "claim_boundary",
        "claims",
        "data_qualifiers",
        "event_ids",
        "limitations",
        "missing_or_conflicting_evidence",
        "reject_reasons",
        "source_refs",
    }
)


class BundleDiffError(ValueError):
    """A stable fail-closed error while reading or comparing a bundle."""


class _DuplicateFieldError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Artifact:
    name: str
    kind: str
    identity: str
    payload: Mapping[str, object]
    raw_bytes: bytes
    validation_status: str

    @property
    def raw_sha256(self) -> str:
        return _sha256(self.raw_bytes)

    @property
    def canonical_sha256(self) -> str:
        return _sha256(_canonical_json(self.payload))

    def descriptor(self) -> dict[str, object]:
        return {
            "canonical_sha256": self.canonical_sha256,
            "identity": self.identity,
            "kind": self.kind,
            "name": self.name,
            "raw_sha256": self.raw_sha256,
            "validation_status": self.validation_status,
        }


@dataclass(frozen=True, slots=True)
class _Bundle:
    source_type: str
    artifacts: tuple[_Artifact, ...]

    @property
    def canonical_sha256(self) -> str:
        if self.source_type == "file":
            payload: object = self.artifacts[0].payload
        else:
            payload = {artifact.name: artifact.payload for artifact in self.artifacts}
        return _sha256(_canonical_json(payload))

    @property
    def raw_sha256(self) -> str:
        if self.source_type == "file":
            return self.artifacts[0].raw_sha256
        identities = {artifact.name: artifact.raw_sha256 for artifact in self.artifacts}
        return _sha256(_canonical_json(identities))

    @property
    def validation_status(self) -> str:
        statuses = {artifact.validation_status for artifact in self.artifacts}
        if statuses == {"CONTRACT_VALIDATED"}:
            return "CONTRACT_VALIDATED"
        if "CONTRACT_VALIDATED" in statuses:
            return "PARTIALLY_CONTRACT_VALIDATED"
        return "STRICT_JSON_SCHEMA_RECOGNIZED"

    def descriptor(self) -> dict[str, object]:
        return {
            "artifact_count": len(self.artifacts),
            "canonical_sha256": self.canonical_sha256,
            "raw_sha256": self.raw_sha256,
            "source_type": self.source_type,
            "validation_status": self.validation_status,
        }


@dataclass(frozen=True, slots=True)
class BundleDiffReport:
    """Canonical comparison output with no research or execution inference."""

    before: Mapping[str, object]
    after: Mapping[str, object]
    artifacts_added: tuple[str, ...]
    artifacts_removed: tuple[str, ...]
    deltas: tuple[Mapping[str, object], ...]

    @property
    def changed(self) -> bool:
        return bool(self.artifacts_added or self.artifacts_removed or self.deltas)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _REPORT_SCHEMA,
            "schema_version": 1,
            "data_class": "OFFLINE_ARTIFACT_COMPARISON",
            "claims": [
                "COMPARISON_ONLY",
                "NOT_ALPHA_EVIDENCE",
                "NO_BROKER_EXECUTION",
            ],
            "before": dict(self.before),
            "after": dict(self.after),
            "summary": {
                "changed": self.changed,
                "delta_count": len(self.deltas),
                "artifacts_added": list(self.artifacts_added),
                "artifacts_removed": list(self.artifacts_removed),
            },
            "deltas": [dict(delta) for delta in self.deltas],
        }

    def to_json_bytes(self) -> bytes:
        """Return byte-stable canonical JSON with a trailing newline."""

        return _canonical_json(self.to_dict()) + b"\n"


def compare_bundle_bytes(
    before_bytes: bytes,
    after_bytes: bytes,
    *,
    before_name: str = "before.json",
    after_name: str = "after.json",
) -> BundleDiffReport:
    """Compare two supported strict-JSON artifacts without filesystem access."""

    before = _Bundle(
        source_type="file",
        artifacts=(_artifact_from_bytes(before_bytes, before_name),),
    )
    after = _Bundle(
        source_type="file",
        artifacts=(_artifact_from_bytes(after_bytes, after_name),),
    )
    return _compare_bundles(before, after, pair_single_artifacts=True)


def compare_bundle_paths(before_path: str | Path, after_path: str | Path) -> BundleDiffReport:
    """Compare two artifact files or bundle directories using read-only local access."""

    before = _load_bundle_path(before_path, side="before")
    after = _load_bundle_path(after_path, side="after")
    return _compare_bundles(before, after, pair_single_artifacts=False)


def write_diff_report(report: BundleDiffReport, output_path: str | Path) -> None:
    """Write a report only to the exact, explicitly requested output file."""

    output = _checked_path(output_path, label="output")
    if output.is_symlink():
        raise BundleDiffError("output path must not be a symbolic link")
    if not output.parent.is_dir():
        raise BundleDiffError("output parent directory does not exist")
    output.write_bytes(report.to_json_bytes())


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise BundleDiffError(f"value is not canonical JSON: {error}") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise BundleDiffError(f"non-finite JSON number is forbidden: {value}")


def _strict_json(raw: bytes, *, source: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        raise BundleDiffError(f"{source}: input must be immutable bytes")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except _DuplicateFieldError as error:
        raise BundleDiffError(f"{source}: duplicate JSON field: {error}") from None
    except BundleDiffError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BundleDiffError(f"{source}: invalid strict JSON: {error}") from None
    if not isinstance(value, Mapping):
        raise BundleDiffError(f"{source}: artifact root must be an object")
    return value


def _artifact_from_bytes(raw: bytes, name: str) -> _Artifact:
    payload = _strict_json(raw, source=name)
    kind, identity = _recognize_artifact(payload, source=name)
    validation_status = "STRICT_JSON_SCHEMA_RECOGNIZED"
    if kind == "ringdown.qfast_report":
        _validate_qfast_report(payload, source=name)
        validation_status = "CONTRACT_VALIDATED"
    return _Artifact(
        name=name,
        kind=kind,
        identity=identity,
        payload=payload,
        raw_bytes=raw,
        validation_status=validation_status,
    )


def _recognize_artifact(payload: Mapping[str, object], *, source: str) -> tuple[str, str]:
    schema = payload.get("schema")
    version = payload.get("schema_version")
    if isinstance(schema, str):
        supported_versions = _SUPPORTED_SCHEMAS.get(schema)
        if supported_versions is None:
            raise BundleDiffError(f"{source}: unsupported artifact schema: {schema}")
        if type(version) is not int or version not in supported_versions:
            raise BundleDiffError(f"{source}: unsupported {schema} schema version: {version!r}")
        identity_field = _IDENTITY_FIELDS[schema]
        identity = payload.get(identity_field) if identity_field is not None else None
        if not isinstance(identity, str) or not identity:
            raise BundleDiffError(f"{source}: {schema} requires non-empty {identity_field}")
        return schema, identity

    if _is_qfast_report(payload):
        return "ringdown.qfast_report", "qfast-report"
    raise BundleDiffError(f"{source}: unsupported or unexpected top-level artifact shape")


def _is_qfast_report(payload: Mapping[str, object]) -> bool:
    required = {
        "schema_version",
        "mode",
        "data_class",
        "input_sha256",
        "protocol_sha256",
        "latency_profiles",
        "latency_gate",
    }
    return (
        required <= payload.keys()
        and type(payload.get("schema_version")) is int
        and payload.get("schema_version") == 1
        and payload.get("mode") == "OFFLINE_RESEARCH"
        and isinstance(payload.get("latency_profiles"), Mapping)
        and isinstance(payload.get("latency_gate"), Mapping)
    )


def _validate_qfast_report(payload: Mapping[str, object], *, source: str) -> None:
    _exact_fields(
        payload,
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
        },
        path=source,
    )
    if payload["project"] != _LEGACY_REPORT_PROJECT or payload["product_name"] != "Esscher":
        raise BundleDiffError(f"{source}: Q-FAST product identity is invalid")
    if payload["data_class"] not in {
        "SYNTHETIC_CONTRACT_FIXTURE",
        "POINT_IN_TIME_EVENT_PANEL",
    }:
        raise BundleDiffError(f"{source}: Q-FAST data_class is unsupported")
    if payload["claims"] != ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"]:
        raise BundleDiffError(f"{source}: Q-FAST claim boundary is invalid")
    _string_list(payload["limitations"], path=f"{source}.limitations")
    _sha256_field(payload["input_sha256"], path=f"{source}.input_sha256")
    _sha256_field(payload["protocol_sha256"], path=f"{source}.protocol_sha256")
    event_count = _nonnegative_int(payload["event_count"], path=f"{source}.event_count")

    profiles = _object(payload["latency_profiles"], path=f"{source}.latency_profiles")
    if not profiles:
        raise BundleDiffError(f"{source}.latency_profiles: must not be empty")
    profile_statuses: dict[str, str] = {}
    for name, value in sorted(profiles.items()):
        if not name:
            raise BundleDiffError(f"{source}.latency_profiles: profile name must not be empty")
        profile = _object(value, path=f"{source}.latency_profiles.{name}")
        _exact_fields(
            profile,
            {"requested_latency_ms", "actual_latency_ms", "qfast"},
            path=f"{source}.latency_profiles.{name}",
        )
        _nonnegative_int(
            profile["requested_latency_ms"],
            path=f"{source}.latency_profiles.{name}.requested_latency_ms",
        )
        actual = _object(
            profile["actual_latency_ms"],
            path=f"{source}.latency_profiles.{name}.actual_latency_ms",
        )
        _exact_fields(
            actual,
            {"minimum", "maximum"},
            path=f"{source}.latency_profiles.{name}.actual_latency_ms",
        )
        minimum = _optional_nonnegative_int(
            actual["minimum"],
            path=f"{source}.latency_profiles.{name}.actual_latency_ms.minimum",
        )
        maximum = _optional_nonnegative_int(
            actual["maximum"],
            path=f"{source}.latency_profiles.{name}.actual_latency_ms.maximum",
        )
        if (minimum is None) != (maximum is None) or (
            minimum is not None and maximum is not None and minimum > maximum
        ):
            raise BundleDiffError(
                f"{source}.latency_profiles.{name}.actual_latency_ms: invalid range"
            )
        if (event_count == 0) != (minimum is None):
            raise BundleDiffError(
                f"{source}.latency_profiles.{name}.actual_latency_ms: event count mismatch"
            )
        qfast = _object(profile["qfast"], path=f"{source}.latency_profiles.{name}.qfast")
        profile_statuses[name] = _validate_qfast_profile(
            qfast,
            event_count=event_count,
            path=f"{source}.latency_profiles.{name}.qfast",
        )

    gate = _object(payload["latency_gate"], path=f"{source}.latency_gate")
    _exact_fields(
        gate,
        {"status", "required_profile", "qfast_status"},
        path=f"{source}.latency_gate",
    )
    required_profile = gate["required_profile"]
    if not isinstance(required_profile, str) or required_profile not in profile_statuses:
        raise BundleDiffError(f"{source}.latency_gate.required_profile: profile is unavailable")
    qfast_status = gate["qfast_status"]
    if qfast_status != profile_statuses[required_profile]:
        raise BundleDiffError(f"{source}.latency_gate.qfast_status: profile status mismatch")
    expected_gate_status = {
        "INSUFFICIENT_DATA": "INSUFFICIENT_DATA",
        "REJECTED": "SHADOW_ONLY",
        "NOT_REJECTED_SMALL_SAMPLE": "NOT_REJECTED_SMALL_SAMPLE",
    }[qfast_status]
    if gate["status"] != expected_gate_status:
        raise BundleDiffError(f"{source}.latency_gate.status: inconsistent gate status")


def _validate_qfast_profile(
    value: Mapping[str, object],
    *,
    event_count: int,
    path: str,
) -> str:
    _exact_fields(
        value,
        {
            "status",
            "claim",
            "event_count",
            "metrics",
            "strongest_baseline",
            "candidate_advantage",
            "leave_best_out_mean",
            "reject_reasons",
        },
        path=path,
    )
    status = value["status"]
    if status not in {"INSUFFICIENT_DATA", "REJECTED", "NOT_REJECTED_SMALL_SAMPLE"}:
        raise BundleDiffError(f"{path}.status: unsupported Q-FAST status")
    if value["claim"] != "NOT_ALPHA_EVIDENCE":
        raise BundleDiffError(f"{path}.claim: invalid Q-FAST claim boundary")
    if _nonnegative_int(value["event_count"], path=f"{path}.event_count") != event_count:
        raise BundleDiffError(f"{path}.event_count: top-level count mismatch")
    metrics = _object(value["metrics"], path=f"{path}.metrics")
    if event_count and "ringdown" not in metrics:
        raise BundleDiffError(f"{path}.metrics: candidate method ringdown is missing")
    for method, raw_metrics in sorted(metrics.items()):
        if not method:
            raise BundleDiffError(f"{path}.metrics: method name must not be empty")
        method_metrics = _object(raw_metrics, path=f"{path}.metrics.{method}")
        _exact_fields(
            method_metrics,
            {
                "eligible_events",
                "admitted_events",
                "coverage",
                "mean_all",
                "median_all",
                "mean_admitted",
                "median_admitted",
            },
            path=f"{path}.metrics.{method}",
        )
        eligible = _nonnegative_int(
            method_metrics["eligible_events"], path=f"{path}.metrics.{method}.eligible_events"
        )
        admitted = _nonnegative_int(
            method_metrics["admitted_events"], path=f"{path}.metrics.{method}.admitted_events"
        )
        if eligible != event_count or admitted > eligible:
            raise BundleDiffError(f"{path}.metrics.{method}: invalid event counts")
        coverage = _finite_number(
            method_metrics["coverage"], path=f"{path}.metrics.{method}.coverage"
        )
        if not 0 <= coverage <= 1:
            raise BundleDiffError(f"{path}.metrics.{method}.coverage: must be between zero and one")
        expected_coverage = admitted / eligible if eligible else 0.0
        if coverage != expected_coverage:
            raise BundleDiffError(f"{path}.metrics.{method}.coverage: event counts disagree")
        _finite_number(method_metrics["mean_all"], path=f"{path}.metrics.{method}.mean_all")
        _finite_number(method_metrics["median_all"], path=f"{path}.metrics.{method}.median_all")
        _optional_finite_number(
            method_metrics["mean_admitted"], path=f"{path}.metrics.{method}.mean_admitted"
        )
        _optional_finite_number(
            method_metrics["median_admitted"], path=f"{path}.metrics.{method}.median_admitted"
        )
    strongest = value["strongest_baseline"]
    if strongest is not None and (
        not isinstance(strongest, str) or strongest not in metrics or strongest == "ringdown"
    ):
        raise BundleDiffError(f"{path}.strongest_baseline: unknown method")
    _optional_finite_number(value["candidate_advantage"], path=f"{path}.candidate_advantage")
    _optional_finite_number(value["leave_best_out_mean"], path=f"{path}.leave_best_out_mean")
    _string_list(value["reject_reasons"], path=f"{path}.reject_reasons")
    return status


def _object(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BundleDiffError(f"{path}: must be an object")
    return value


def _exact_fields(value: Mapping[str, object], fields: set[str], *, path: str) -> None:
    missing = sorted(fields - value.keys())
    unknown = sorted(value.keys() - fields)
    if missing:
        raise BundleDiffError(f"{path}: missing field {missing[0]}")
    if unknown:
        raise BundleDiffError(f"{path}: unknown field {unknown[0]}")


def _string_list(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BundleDiffError(f"{path}: must be a list of strings")
    if len(set(value)) != len(value):
        raise BundleDiffError(f"{path}: values must be unique")
    return tuple(value)


def _sha256_field(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BundleDiffError(f"{path}: must be a lowercase SHA-256")
    return value


def _nonnegative_int(value: object, *, path: str) -> int:
    if type(value) is not int or value < 0:
        raise BundleDiffError(f"{path}: must be a non-negative integer")
    return value


def _optional_nonnegative_int(value: object, *, path: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, path=path)


def _finite_number(value: object, *, path: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise BundleDiffError(f"{path}: must be a finite number")
    return float(value)


def _optional_finite_number(value: object, *, path: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, path=path)


def _checked_path(value: str | Path, *, label: str) -> Path:
    path = Path(value)
    if ".." in path.parts:
        raise BundleDiffError(f"{label} path must not contain parent traversal")
    return path


def _load_bundle_path(value: str | Path, *, side: str) -> _Bundle:
    path = _checked_path(value, label=side)
    if path.is_symlink():
        raise BundleDiffError(f"{side} path must not be a symbolic link")
    if path.is_file():
        if path.suffix.lower() != ".json":
            raise BundleDiffError(f"{side} artifact file must use the .json suffix")
        return _Bundle(
            source_type="file",
            artifacts=(_artifact_from_bytes(path.read_bytes(), path.name),),
        )
    if not path.is_dir():
        raise BundleDiffError(f"{side} path must be a JSON file or directory")

    artifacts: list[_Artifact] = []
    root = path.resolve(strict=True)
    for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            raise BundleDiffError(f"{side} bundle must not contain symbolic links")
        if not candidate.is_file() or candidate.suffix.lower() != ".json":
            continue
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise BundleDiffError(f"{side} bundle path escaped its root")
        try:
            name = candidate.relative_to(path).as_posix()
        except ValueError:
            raise BundleDiffError(f"{side} bundle path escaped its root") from None
        if name.startswith("../") or name == "..":
            raise BundleDiffError(f"{side} bundle path escaped its root")
        artifacts.append(_artifact_from_bytes(candidate.read_bytes(), name))
    if not artifacts:
        raise BundleDiffError(f"{side} bundle directory contains no JSON artifacts")
    return _validated_bundle(tuple(artifacts), source_type="directory", source=side)


def _validated_bundle(
    artifacts: tuple[_Artifact, ...],
    *,
    source_type: str,
    source: str,
) -> _Bundle:
    rules = [
        artifact
        for artifact in artifacts
        if artifact.kind == "ringdown.earnings_replay_selection_rule"
    ]
    event_lists = [
        artifact for artifact in artifacts if artifact.kind == "ringdown.frozen_earnings_event_list"
    ]
    manifests = [
        artifact
        for artifact in artifacts
        if artifact.kind == "ringdown.point_in_time_evidence_manifest"
        and artifact.payload.get("schema_version") == 2
    ]
    if len(rules) == len(event_lists) == 1 and manifests:
        try:
            validated = validate_replay_evidence_set(
                event_lists[0].raw_bytes,
                rules[0].raw_bytes,
                [artifact.raw_bytes for artifact in manifests],
            )
        except ReplayEvidenceRejected as error:
            raise BundleDiffError(
                f"{source} bundle failed replay contract validation: {error}"
            ) from None
        validated_ids = {item.event_id for item in validated}
        artifacts = tuple(
            replace(artifact, validation_status="CONTRACT_VALIDATED")
            if artifact in rules
            or artifact in event_lists
            or (
                artifact.kind == "ringdown.point_in_time_evidence_manifest"
                and artifact.identity in validated_ids
            )
            else artifact
            for artifact in artifacts
        )
    return _Bundle(source_type=source_type, artifacts=artifacts)


def _compare_bundles(
    before: _Bundle,
    after: _Bundle,
    *,
    pair_single_artifacts: bool,
) -> BundleDiffReport:
    pairs, removed, added = _pair_artifacts(
        before.artifacts,
        after.artifacts,
        pair_single_artifacts=pair_single_artifacts,
    )
    deltas: list[dict[str, object]] = []
    for old, new in pairs:
        artifact_label = _artifact_label(old, new)
        if before.source_type == after.source_type == "directory" and old.name != new.name:
            deltas.append(
                {
                    "artifact": artifact_label,
                    "category": "ARTIFACT",
                    "change": "RENAMED",
                    "json_pointer": "",
                    "before": old.name,
                    "before_present": True,
                    "after": new.name,
                    "after_present": True,
                }
            )
        if old.raw_sha256 != new.raw_sha256:
            deltas.append(
                {
                    "artifact": artifact_label,
                    "aspect": "RAW_BYTES_SHA256",
                    "category": "IDENTITY",
                    "change": "CHANGED",
                    "json_pointer": "",
                    "before": old.raw_sha256,
                    "before_present": True,
                    "after": new.raw_sha256,
                    "after_present": True,
                }
            )
        _diff_values(old.payload, new.payload, (), artifact_label, deltas)

    for artifact in removed:
        deltas.append(
            {
                "artifact": artifact.name,
                "category": "ARTIFACT",
                "change": "REMOVED",
                "json_pointer": "",
                "before": artifact.descriptor(),
                "before_present": True,
                "after_present": False,
            }
        )
    for artifact in added:
        deltas.append(
            {
                "artifact": artifact.name,
                "category": "ARTIFACT",
                "change": "ADDED",
                "json_pointer": "",
                "before_present": False,
                "after_present": True,
                "after": artifact.descriptor(),
            }
        )

    deltas.sort(key=_delta_sort_key)
    added_names = tuple(sorted(artifact.name for artifact in added))
    removed_names = tuple(sorted(artifact.name for artifact in removed))
    return BundleDiffReport(
        before=before.descriptor(),
        after=after.descriptor(),
        artifacts_added=added_names,
        artifacts_removed=removed_names,
        deltas=tuple(deltas),
    )


def _pair_artifacts(
    before: tuple[_Artifact, ...],
    after: tuple[_Artifact, ...],
    *,
    pair_single_artifacts: bool,
) -> tuple[list[tuple[_Artifact, _Artifact]], list[_Artifact], list[_Artifact]]:
    if pair_single_artifacts and len(before) == len(after) == 1:
        return [(before[0], after[0])], [], []

    old_by_name = {artifact.name: artifact for artifact in before}
    new_by_name = {artifact.name: artifact for artifact in after}
    common_names = sorted(old_by_name.keys() & new_by_name.keys())
    pairs = [(old_by_name[name], new_by_name[name]) for name in common_names]
    unmatched_old = [old_by_name[name] for name in sorted(old_by_name.keys() - set(common_names))]
    unmatched_new = [new_by_name[name] for name in sorted(new_by_name.keys() - set(common_names))]

    old_counts = Counter((item.kind, item.identity) for item in unmatched_old)
    new_counts = Counter((item.kind, item.identity) for item in unmatched_new)
    new_by_identity = {(item.kind, item.identity): item for item in unmatched_new}
    matched_identities = {
        identity
        for identity, count in old_counts.items()
        if count == 1 and new_counts[identity] == 1
    }
    for old in unmatched_old:
        key = (old.kind, old.identity)
        if key in matched_identities:
            pairs.append((old, new_by_identity[key]))

    removed = [
        item for item in unmatched_old if (item.kind, item.identity) not in matched_identities
    ]
    added = [item for item in unmatched_new if (item.kind, item.identity) not in matched_identities]
    pairs.sort(key=lambda pair: (_artifact_label(*pair), pair[0].name, pair[1].name))
    return pairs, removed, added


def _artifact_label(before: _Artifact, after: _Artifact) -> str:
    if before.name == after.name:
        return before.name
    return f"{before.name} -> {after.name}"


def _diff_values(
    before: object,
    after: object,
    tokens: tuple[str, ...],
    artifact: str,
    deltas: list[dict[str, object]],
) -> None:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        keys = sorted(before.keys() | after.keys())
        for key in keys:
            child_tokens = (*tokens, key)
            if key not in before:
                _append_delta(
                    deltas, artifact, child_tokens, "ADDED", None, after[key], False, True
                )
            elif key not in after:
                _append_delta(
                    deltas, artifact, child_tokens, "REMOVED", before[key], None, True, False
                )
            else:
                _diff_values(before[key], after[key], child_tokens, artifact, deltas)
        return

    list_key = tokens[-1] if tokens else ""
    if isinstance(before, list) and isinstance(after, list):
        identifier = _KEYED_LISTS.get(list_key)
        if identifier is not None:
            old_items = _index_list(before, identifier)
            new_items = _index_list(after, identifier)
            if old_items is not None and new_items is not None:
                for item_id in sorted(old_items.keys() | new_items.keys()):
                    item_tokens = (*tokens, item_id)
                    if item_id not in old_items:
                        _append_delta(
                            deltas,
                            artifact,
                            item_tokens,
                            "ADDED",
                            None,
                            new_items[item_id],
                            False,
                            True,
                        )
                    elif item_id not in new_items:
                        _append_delta(
                            deltas,
                            artifact,
                            item_tokens,
                            "REMOVED",
                            old_items[item_id],
                            None,
                            True,
                            False,
                        )
                    else:
                        _diff_values(
                            old_items[item_id],
                            new_items[item_id],
                            item_tokens,
                            artifact,
                            deltas,
                        )
                return
        if list_key in _SET_LIKE_LISTS:
            old_canonical = _sorted_json_values(before)
            new_canonical = _sorted_json_values(after)
            if old_canonical != new_canonical:
                _append_delta(
                    deltas,
                    artifact,
                    tokens,
                    "CHANGED",
                    old_canonical,
                    new_canonical,
                    True,
                    True,
                )
            return

    if before != after or type(before) is not type(after):
        _append_delta(deltas, artifact, tokens, "CHANGED", before, after, True, True)


def _index_list(values: list[object], identifier: str) -> dict[str, object] | None:
    result: dict[str, object] = {}
    for value in values:
        if not isinstance(value, Mapping):
            return None
        item_id = value.get(identifier)
        if not isinstance(item_id, str) or not item_id or item_id in result:
            return None
        result[item_id] = value
    return result


def _sorted_json_values(values: list[object]) -> list[object]:
    return sorted(values, key=_canonical_json)


def _append_delta(
    deltas: list[dict[str, object]],
    artifact: str,
    tokens: tuple[str, ...],
    change: str,
    before: object,
    after: object,
    before_present: bool,
    after_present: bool,
) -> None:
    delta: dict[str, object] = {
        "artifact": artifact,
        "category": _category(tokens),
        "change": change,
        "json_pointer": _json_pointer(tokens),
        "before_present": before_present,
        "after_present": after_present,
    }
    if before_present:
        delta["before"] = before
    if after_present:
        delta["after"] = after
    deltas.append(delta)


def _category(tokens: tuple[str, ...]) -> str:
    lowered = tuple(token.lower() for token in tokens)
    field = lowered[-1] if lowered else ""
    if field in {"schema", "schema_version", "artifact_schema_version"}:
        return "SCHEMA"
    if field in {"artifact_class", "data_class", "fixture_class"}:
        return "DATA_CLASSIFICATION"
    if field in {"claims", "claim", "claim_boundary", "data_qualifiers", "limitations"}:
        return "CLAIM"
    if (
        "inclusion_or_exclusion_reason" in lowered
        or "events" in lowered
        or field in {"event_id", "event_ids"}
    ):
        return "EVENT"
    if (
        "records" in lowered
        or "field_source_refs" in lowered
        or "feature_dependencies" in lowered
        or "sec_filing" in lowered
        or field.startswith(("published_at", "retrieved_at", "source_"))
        or field
        in {
            "content_sha256",
            "entitlement_note",
            "field_status",
            "publisher",
            "redistribution_status",
        }
    ):
        return "PROVENANCE"
    if "qfast" in lowered or "metrics" in lowered:
        return "VERDICT"
    if any("latency" in token for token in lowered):
        return "LATENCY"
    if field.endswith(("_sha256", "_hash")) or field in {"permit_id", "receipt_sha256"}:
        return "IDENTITY"
    if field.endswith(("status", "verdict")) or field in {
        "candidate_advantage",
        "leave_best_out_mean",
        "metrics",
        "reject_reasons",
        "strongest_baseline",
    }:
        return "VERDICT"
    return "CONTENT"


def _json_pointer(tokens: tuple[str, ...]) -> str:
    if not tokens:
        return ""
    return "/" + "/".join(token.replace("~", "~0").replace("/", "~1") for token in tokens)


def _delta_sort_key(delta: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(delta["artifact"]),
        str(delta["json_pointer"]),
        str(delta["category"]),
        str(delta["change"]),
    )
