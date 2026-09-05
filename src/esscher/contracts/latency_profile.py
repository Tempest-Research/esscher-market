"""Frozen p95 execution-latency profile contract.

The profile is the owner-controlled latency input consumed by strategy
evaluation.  It is either host-measured under an authorized PAPER lifecycle or
owner-preregistered as a conservative bound; a synthetic placeholder can never
authorize evaluation or promotion.  A profile bound to a different strategy
policy, a stale supersession state, or a mismatched content hash fails closed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from importlib import resources
from typing import NoReturn

from ..strategy.contracts import canonical_json_bytes, sha256_bytes
from ..strategy.policy import strategy_policy_sha256

PROFILE_SCHEMA = "esscher.latency_profile"
SCHEMA_VERSION = 1

PROFILE_ID = "ESSCHER_P95_EXECUTION_LATENCY_V1"
QUANTILE_METHOD = "NEAREST_RANK_P95"
MINIMUM_MEASURED_OBSERVATIONS = 20

CLAIM_LABELS = (
    "NO_BROKER_MUTATION",
    "NO_CREDENTIALS",
    "PAPER_ONLY",
    "SOURCE_GROUNDED",
)


class LatencyProfileKind(StrEnum):
    """Provenance class of a p95 execution-latency profile."""

    HOST_MEASURED = "HOST_MEASURED"
    PREREGISTERED = "PREREGISTERED"
    SYNTHETIC = "SYNTHETIC"


class LatencyProfileReason(StrEnum):
    """Stable machine-readable reasons for rejecting latency profiles."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    HASH_MISMATCH = "HASH_MISMATCH"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    SYNTHETIC_PLACEHOLDER = "SYNTHETIC_PLACEHOLDER"
    INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
    STALE_PROFILE = "STALE_PROFILE"


class LatencyProfileRejected(ValueError):
    """A deterministic validation failure for latency profiles."""

    def __init__(self, reason: LatencyProfileReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


@dataclass(frozen=True, slots=True)
class ValidatedLatencyProfile:
    """A validated p95 execution-latency profile."""

    profile_id: str
    kind: LatencyProfileKind
    p95_latency_ms: int
    quantile_method: str
    clock_source: str
    sample_population: str
    warm_cold_policy: str
    observed_samples: int
    policy_sha256: str
    content_sha256: str
    frozen_at: datetime
    promotion_eligible: bool
    evaluation_eligible: bool


class _DuplicateFieldError(ValueError):
    pass


def _reject(reason: LatencyProfileReason, path: str, detail: str) -> NoReturn:
    raise LatencyProfileRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _decode(raw: bytes, *, path: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(LatencyProfileReason.INVALID_DOCUMENT, path, "artifacts must be bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except _DuplicateFieldError as error:
        _reject(LatencyProfileReason.DUPLICATE_FIELD, path, f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(LatencyProfileReason.INVALID_DOCUMENT, path, str(error))
    if not isinstance(value, Mapping):
        _reject(LatencyProfileReason.INVALID_DOCUMENT, path, "root must be an object")
    return value


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(LatencyProfileReason.INVALID_DOCUMENT, path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(
            LatencyProfileReason.MISSING_FIELD,
            f"{path}.{missing[0]}",
            "required field is missing",
        )
    unknown = sorted(keys - fields)
    if unknown:
        _reject(
            LatencyProfileReason.UNKNOWN_FIELD,
            f"{path}.{unknown[0]}",
            "field is not part of the frozen schema",
        )
    return value


def _text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(LatencyProfileReason.INVALID_DOCUMENT, path, "must be non-empty text")
    return value


def _integer(value: object, *, path: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        _reject(
            LatencyProfileReason.INVALID_DOCUMENT,
            path,
            f"must be an integer of at least {minimum}",
        )
    return value


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        _reject(LatencyProfileReason.INVALID_DOCUMENT, path, "must be SHA-256")
    return value.lower()


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str):
        _reject(LatencyProfileReason.INVALID_DOCUMENT, path, "must be a timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        _reject(LatencyProfileReason.INVALID_DOCUMENT, path, str(error))


_PROFILE_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "profile_id",
        "kind",
        "p95_latency_ms",
        "quantile_method",
        "clock_source",
        "sample_population",
        "warm_cold_policy",
        "minimum_sample_observations",
        "observed_samples",
        "validity",
        "provenance_note",
        "claim_labels",
        "content_sha256",
    }
)
_VALIDITY_FIELDS = frozenset({"frozen_at", "policy_sha256", "superseded_by"})


def latency_profile_content_sha256(payload: Mapping[str, object]) -> str:
    """Hash the canonical profile payload excluding the content hash itself."""

    body = {key: value for key, value in payload.items() if key != "content_sha256"}
    return sha256_bytes(canonical_json_bytes(body))


def validate_latency_profile(raw: bytes) -> ValidatedLatencyProfile:
    """Validate one frozen p95 execution-latency profile."""

    profile = _strict_object(
        _decode(raw, path="latency_profile"),
        path="latency_profile",
        fields=_PROFILE_FIELDS,
    )
    if profile["schema"] != PROFILE_SCHEMA or profile["schema_version"] != SCHEMA_VERSION:
        _reject(
            LatencyProfileReason.UNSUPPORTED_SCHEMA,
            "latency_profile",
            "unsupported latency profile schema or version",
        )
    if profile["profile_id"] != PROFILE_ID:
        _reject(
            LatencyProfileReason.INVALID_DOCUMENT,
            "latency_profile.profile_id",
            "profile identity differs from the frozen profile id",
        )
    kind = LatencyProfileKind(str(profile["kind"]))
    if kind is LatencyProfileKind.SYNTHETIC:
        _reject(
            LatencyProfileReason.SYNTHETIC_PLACEHOLDER,
            "latency_profile.kind",
            "synthetic latency placeholders fail evaluation and promotion",
        )
    p95 = _integer(profile["p95_latency_ms"], path="latency_profile.p95_latency_ms", minimum=1)
    if profile["quantile_method"] != QUANTILE_METHOD:
        _reject(
            LatencyProfileReason.INVALID_DOCUMENT,
            "latency_profile.quantile_method",
            "quantile method must equal the frozen nearest-rank p95 method",
        )
    clock_source = _text(profile["clock_source"], path="latency_profile.clock_source")
    sample_population = _text(
        profile["sample_population"], path="latency_profile.sample_population"
    )
    warm_cold_policy = _text(profile["warm_cold_policy"], path="latency_profile.warm_cold_policy")
    minimum = _integer(
        profile["minimum_sample_observations"],
        path="latency_profile.minimum_sample_observations",
        minimum=0,
    )
    observed = _integer(
        profile["observed_samples"], path="latency_profile.observed_samples", minimum=0
    )
    if kind is LatencyProfileKind.HOST_MEASURED:
        if minimum < MINIMUM_MEASURED_OBSERVATIONS:
            _reject(
                LatencyProfileReason.INSUFFICIENT_OBSERVATIONS,
                "latency_profile.minimum_sample_observations",
                f"measured profiles require at least {MINIMUM_MEASURED_OBSERVATIONS} observations",
            )
        if observed < minimum:
            _reject(
                LatencyProfileReason.INSUFFICIENT_OBSERVATIONS,
                "latency_profile.observed_samples",
                "measured profiles must carry at least the minimum observations",
            )
    validity = _strict_object(
        profile["validity"], path="latency_profile.validity", fields=_VALIDITY_FIELDS
    )
    frozen_at = _timestamp(validity["frozen_at"], path="latency_profile.validity.frozen_at")
    policy_sha = _sha256(validity["policy_sha256"], path="latency_profile.validity.policy_sha256")
    if policy_sha != strategy_policy_sha256():
        _reject(
            LatencyProfileReason.STALE_PROFILE,
            "latency_profile.validity.policy_sha256",
            "profile is bound to a different strategy policy and is stale",
        )
    if validity["superseded_by"] is not None:
        _reject(
            LatencyProfileReason.STALE_PROFILE,
            "latency_profile.validity.superseded_by",
            "a superseded profile can never authorize evaluation",
        )
    _text(profile["provenance_note"], path="latency_profile.provenance_note")
    labels = profile["claim_labels"]
    if not isinstance(labels, Sequence) or tuple(labels) != CLAIM_LABELS:
        _reject(
            LatencyProfileReason.INVALID_DOCUMENT,
            "latency_profile.claim_labels",
            "claim labels must equal the frozen profile claim set",
        )
    content_sha = _sha256(profile["content_sha256"], path="latency_profile.content_sha256")
    if content_sha != latency_profile_content_sha256(profile):
        _reject(
            LatencyProfileReason.HASH_MISMATCH,
            "latency_profile.content_sha256",
            "content hash does not match the canonical profile body",
        )
    return ValidatedLatencyProfile(
        profile_id=PROFILE_ID,
        kind=kind,
        p95_latency_ms=p95,
        quantile_method=QUANTILE_METHOD,
        clock_source=clock_source,
        sample_population=sample_population,
        warm_cold_policy=warm_cold_policy,
        observed_samples=observed,
        policy_sha256=policy_sha,
        content_sha256=content_sha,
        frozen_at=frozen_at,
        promotion_eligible=True,
        evaluation_eligible=True,
    )


def packaged_latency_profile_bytes() -> bytes:
    return (
        resources.files("esscher.contracts")
        .joinpath("policies/latency_profile_v1.json")
        .read_bytes()
    )


def load_latency_profile() -> ValidatedLatencyProfile:
    """Validate the packaged frozen p95 execution-latency profile."""

    return validate_latency_profile(packaged_latency_profile_bytes())


__all__ = [
    "CLAIM_LABELS",
    "MINIMUM_MEASURED_OBSERVATIONS",
    "PROFILE_ID",
    "PROFILE_SCHEMA",
    "QUANTILE_METHOD",
    "LatencyProfileKind",
    "LatencyProfileReason",
    "LatencyProfileRejected",
    "ValidatedLatencyProfile",
    "latency_profile_content_sha256",
    "load_latency_profile",
    "packaged_latency_profile_bytes",
    "validate_latency_profile",
]
