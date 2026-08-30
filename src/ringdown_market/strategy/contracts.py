"""Strict canonical JSON contracts for frozen strategy artifacts.

The module is intentionally stdlib-only and has no execution, runtime, network,
credential, account, or broker dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ringdown_market.alpha.models import Direction
from ringdown_market.strategy.models import (
    CandidateManifest,
    CandidateRecord,
    Contradiction,
    DataHealthState,
    DecisionDisposition,
    DecodingParameters,
    EligibilityState,
    EventCategory,
    EvidenceRef,
    EvidenceRole,
    ExchangeStatus,
    Falsifier,
    FeatureComponent,
    FeatureReceipt,
    FeatureStatus,
    FeatureValue,
    FeatureValueType,
    GuidanceDirection,
    ReactionRelation,
    ReasonerDecision,
    ReasonerExchange,
    ReleaseFamily,
    StrategyDecision,
    StrategyInput,
    StrategySnapshot,
    TimingBucket,
)

CANDIDATE_MANIFEST_SCHEMA = "esscher.candidate_manifest"
STRATEGY_SNAPSHOT_SCHEMA = "esscher.strategy_snapshot"
FEATURE_RECEIPT_SCHEMA = "esscher.feature_receipt"
REASONER_EXCHANGE_SCHEMA = "esscher.reasoner_exchange"
VALIDATED_DECISION_SCHEMA = "esscher.validated_decision"
SCHEMA_VERSION = 1
DECISION_AUTHORITY = "DIRECTION_ONLY"

_REASONER_POLICY_HASH_REGISTRY = (
    (
        "EARNINGS_RESIDUAL_CONTINUATION_V1",
        (
            "af801a9baf24cff5b1f093e3802834855e8b82d56491b7244bba59ba357b30e3",
            "617897661b723c2315f3cb60fbb15b6e57dfc571098a4be4563b324cd6a0354f",
            "08dd5302e8e03e01a7012acb59048329516e6a801f8b24827066f43430c04fa4",
        ),
    ),
    (
        "MACRO_SPY_CONTINUATION_CHALLENGER_V1",
        (
            "c2dd3668be1595f6658506f830ccad06b92b532c36732fff667f7f59ce641dd2",
            "52f7b1c152128414363225aa441bf40e3b099ff045952891d9b2743bb3bccfec",
            "08dd5302e8e03e01a7012acb59048329516e6a801f8b24827066f43430c04fa4",
        ),
    ),
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_DECIMAL_TEXT = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MAX_TEXT_CHARS = 1_000
_FEATURE_UNIT = re.compile(r"^[A-Z][A-Z0-9_|]{0,127}$")


class StrategyContractReason(StrEnum):
    """Stable reasons for rejecting a strategy contract before downstream use."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    NON_CANONICAL_DOCUMENT = "NON_CANONICAL_DOCUMENT"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    HASH_MISMATCH = "HASH_MISMATCH"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    POINT_IN_TIME_VIOLATION = "POINT_IN_TIME_VIOLATION"
    INELIGIBLE_EVENT = "INELIGIBLE_EVENT"
    DATA_HEALTH_REJECTED = "DATA_HEALTH_REJECTED"
    FEATURE_SET_MISMATCH = "FEATURE_SET_MISMATCH"
    FEATURE_VALUE_INVALID = "FEATURE_VALUE_INVALID"
    UNKNOWN_CITATION = "UNKNOWN_CITATION"
    REASONER_OUTPUT_INVALID = "REASONER_OUTPUT_INVALID"


class StrategyContractRejected(ValueError):
    """Typed deterministic failure for invalid canonical strategy bytes."""

    def __init__(
        self,
        reason: StrategyContractReason,
        path: str,
        detail: str,
    ) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


class _DuplicateFieldError(ValueError):
    pass


def _reject(reason: StrategyContractReason, path: str, detail: str) -> NoReturn:
    raise StrategyContractRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant {value}")


def _invalid_float(value: str) -> NoReturn:
    raise ValueError(f"JSON numeric literal {value} is forbidden; use canonical decimal text")


def _require_bytes(value: object, *, path: str) -> bytes:
    if type(value) is not bytes:
        _reject(
            StrategyContractReason.INVALID_DOCUMENT,
            path,
            "contract inputs must be immutable bytes",
        )
    return value  # type: ignore[return-value]


def _decode(raw: bytes, *, path: str) -> Mapping[str, object]:
    raw = _require_bytes(raw, path=path)
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_invalid_float,
            parse_constant=_invalid_constant,
        )
    except _DuplicateFieldError as error:
        _reject(StrategyContractReason.DUPLICATE_FIELD, path, f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, str(error))
    if not isinstance(payload, Mapping):
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "root must be an object")
    return payload


def canonical_json_bytes(value: object) -> bytes:
    """Return the one canonical UTF-8 JSON representation used by new contracts."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Hash exact immutable bytes."""

    return hashlib.sha256(_require_bytes(value, path="bytes")).hexdigest()


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(
            StrategyContractReason.MISSING_FIELD,
            f"{path}.{missing[0]}",
            "required field is missing",
        )
    unknown = sorted(keys - fields)
    if unknown:
        _reject(
            StrategyContractReason.UNKNOWN_FIELD,
            f"{path}.{unknown[0]}",
            "field is not part of the frozen schema",
        )
    return value


def _text(value: object, *, path: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value != unicodedata.normalize("NFC", value)
        or len(value) > maximum
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        _reject(
            StrategyContractReason.INVALID_DOCUMENT,
            path,
            "must be bounded, normalized, non-empty text",
        )
    return value


def _identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "must be a normalized identifier")
    return value


def _reason_code(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _REASON_CODE.fullmatch(value) is None:
        _reject(
            StrategyContractReason.INVALID_DOCUMENT,
            path,
            "must be an uppercase stable reason code",
        )
    return value


def _feature_unit(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _FEATURE_UNIT.fullmatch(value) is None:
        _reject(
            StrategyContractReason.INVALID_DOCUMENT,
            path,
            "must be a normalized feature unit",
        )
    return value


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "must be a lowercase SHA-256")
    return value


def _integer(value: object, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "must be an integer")
    return value


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: object, *, path: str) -> EnumT:
    if not isinstance(value, str):
        _reject(StrategyContractReason.UNKNOWN_STATE, path, "state must be text")
    try:
        return enum_type(value)
    except ValueError:
        _reject(StrategyContractReason.UNKNOWN_STATE, path, f"unknown state {value}")


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamps must be UTC")
    result = value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if result.endswith(".000000Z"):
        return result.removesuffix(".000000Z") + "Z"
    prefix, fraction = result[:-1].split(".", maxsplit=1)
    return f"{prefix}.{fraction.rstrip('0')}Z"


def _timestamp(value: object, *, path: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        _reject(
            StrategyContractReason.INVALID_DOCUMENT,
            path,
            "must be a canonical explicit UTC timestamp ending in Z",
        )
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00").astimezone(UTC)
    except ValueError as error:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, str(error))
    if _timestamp_text(parsed) != value:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "timestamp is not canonical")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal values must be finite")
    if value == 0:
        return "0"
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result


def _decimal(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_TEXT.fullmatch(value) is None:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "must be canonical decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, str(error))
    if not parsed.is_finite() or _decimal_text(parsed) != value:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "decimal text is not canonical")
    return parsed


def _policy_decimal(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_TEXT.fullmatch(value) is None:
        _reject(StrategyContractReason.POLICY_MISMATCH, path, "must be finite decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, path, str(error))
    if not parsed.is_finite():
        _reject(StrategyContractReason.POLICY_MISMATCH, path, "must be finite decimal text")
    return parsed


def _string_list(
    value: object,
    *,
    path: str,
    reason_codes: bool = False,
    nonempty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "must be a list")
    result = tuple(
        (
            _reason_code(item, path=f"{path}[{index}]")
            if reason_codes
            else _identifier(item, path=f"{path}[{index}]")
        )
        for index, item in enumerate(value)
    )
    if result != tuple(sorted(set(result))):
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, "must be sorted and unique")
    return result


def _verify_schema(payload: Mapping[str, object], *, schema: str, path: str) -> None:
    if payload["schema"] != schema or payload["schema_version"] != SCHEMA_VERSION:
        _reject(
            StrategyContractReason.UNSUPPORTED_SCHEMA,
            path,
            f"expected {schema}/v{SCHEMA_VERSION}",
        )


def _wrap_model_error(path: str, build: object) -> object:
    try:
        return build()  # type: ignore[operator]
    except StrategyContractRejected:
        raise
    except (TypeError, ValueError) as error:
        _reject(StrategyContractReason.INVALID_DOCUMENT, path, str(error))


def _parse_evidence_ref(value: object, *, path: str) -> EvidenceRef:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "evidence_id",
                "role",
                "source_class",
                "published_at",
                "available_at",
                "content_sha256",
            }
        ),
    )
    published_at = _timestamp(payload["published_at"], path=f"{path}.published_at", nullable=True)
    available_at = _timestamp(payload["available_at"], path=f"{path}.available_at")
    assert available_at is not None
    return EvidenceRef(
        evidence_id=_identifier(payload["evidence_id"], path=f"{path}.evidence_id"),
        role=_enum(EvidenceRole, payload["role"], path=f"{path}.role"),
        source_class=_reason_code(payload["source_class"], path=f"{path}.source_class"),
        published_at=published_at,
        available_at=available_at,
        content_sha256=_sha256(payload["content_sha256"], path=f"{path}.content_sha256"),
    )


def _evidence_ref_payload(value: EvidenceRef) -> dict[str, object]:
    return {
        "content_sha256": value.content_sha256,
        "evidence_id": value.evidence_id,
        "available_at": _timestamp_text(value.available_at),
        "published_at": (
            _timestamp_text(value.published_at) if value.published_at is not None else None
        ),
        "role": value.role.value,
        "source_class": value.source_class,
    }


_CANDIDATE_RECORD_FIELDS = frozenset(
    {
        "cohort_id",
        "eligibility",
        "event_id",
        "issuer",
        "reason_codes",
        "scheduled_at",
        "security_id",
        "ticker",
    }
)
_CANDIDATE_MANIFEST_FIELDS = frozenset(
    {
        "candidate_id",
        "frozen_at",
        "manifest_id",
        "policy_sha256",
        "producer_build_sha256",
        "records",
        "schema",
        "schema_version",
        "selection_rule_id",
    }
)


def _candidate_record_payload(value: CandidateRecord) -> dict[str, object]:
    return {
        "cohort_id": value.cohort_id,
        "eligibility": value.eligibility.value,
        "event_id": value.event_id,
        "issuer": value.issuer,
        "reason_codes": list(value.reason_codes),
        "scheduled_at": _timestamp_text(value.scheduled_at),
        "security_id": value.security_id,
        "ticker": value.ticker,
    }


def _parse_candidate_record(value: object, *, path: str) -> CandidateRecord:
    payload = _strict_object(value, path=path, fields=_CANDIDATE_RECORD_FIELDS)
    scheduled_at = _timestamp(payload["scheduled_at"], path=f"{path}.scheduled_at")
    assert scheduled_at is not None
    return CandidateRecord(
        event_id=_identifier(payload["event_id"], path=f"{path}.event_id"),
        issuer=_text(payload["issuer"], path=f"{path}.issuer"),
        security_id=_identifier(payload["security_id"], path=f"{path}.security_id"),
        ticker=_text(payload["ticker"], path=f"{path}.ticker"),
        cohort_id=_identifier(payload["cohort_id"], path=f"{path}.cohort_id"),
        scheduled_at=scheduled_at,
        eligibility=_enum(
            EligibilityState,
            payload["eligibility"],
            path=f"{path}.eligibility",
        ),
        reason_codes=_string_list(
            payload["reason_codes"],
            path=f"{path}.reason_codes",
            reason_codes=True,
        ),
    )


def candidate_manifest_payload(value: CandidateManifest) -> dict[str, object]:
    """Return the complete-denominator candidate manifest object."""

    return {
        "candidate_id": value.candidate_id,
        "frozen_at": _timestamp_text(value.frozen_at),
        "manifest_id": value.manifest_id,
        "policy_sha256": value.policy_sha256,
        "producer_build_sha256": value.producer_build_sha256,
        "records": [_candidate_record_payload(item) for item in value.records],
        "schema": CANDIDATE_MANIFEST_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "selection_rule_id": value.selection_rule_id,
    }


def candidate_manifest_bytes(value: CandidateManifest) -> bytes:
    """Serialize a frozen candidate denominator to canonical bytes."""

    return canonical_json_bytes(candidate_manifest_payload(value))


def candidate_manifest_sha256(value: CandidateManifest) -> str:
    """Hash canonical candidate-manifest bytes."""

    return sha256_bytes(candidate_manifest_bytes(value))


def parse_candidate_manifest(raw: bytes) -> CandidateManifest:
    """Strictly parse canonical ``esscher.candidate_manifest/v1`` bytes."""

    payload = _strict_object(
        _decode(raw, path="candidate_manifest"),
        path="candidate_manifest",
        fields=_CANDIDATE_MANIFEST_FIELDS,
    )
    _verify_schema(payload, schema=CANDIDATE_MANIFEST_SCHEMA, path="candidate_manifest")
    records_value = payload["records"]
    if not isinstance(records_value, list):
        _reject(
            StrategyContractReason.INVALID_DOCUMENT,
            "candidate_manifest.records",
            "must be a list",
        )
    frozen_at = _timestamp(payload["frozen_at"], path="candidate_manifest.frozen_at")
    assert frozen_at is not None
    result = _wrap_model_error(
        "candidate_manifest",
        lambda: CandidateManifest(
            manifest_id=_identifier(payload["manifest_id"], path="candidate_manifest.manifest_id"),
            candidate_id=_identifier(
                payload["candidate_id"], path="candidate_manifest.candidate_id"
            ),
            policy_sha256=_sha256(
                payload["policy_sha256"], path="candidate_manifest.policy_sha256"
            ),
            selection_rule_id=_identifier(
                payload["selection_rule_id"],
                path="candidate_manifest.selection_rule_id",
            ),
            producer_build_sha256=_sha256(
                payload["producer_build_sha256"],
                path="candidate_manifest.producer_build_sha256",
            ),
            frozen_at=frozen_at,
            records=tuple(
                _parse_candidate_record(
                    item,
                    path=f"candidate_manifest.records[{index}]",
                )
                for index, item in enumerate(records_value)
            ),
        ),
    )
    assert isinstance(result, CandidateManifest)
    if candidate_manifest_bytes(result) != raw:
        _reject(
            StrategyContractReason.NON_CANONICAL_DOCUMENT,
            "candidate_manifest",
            "bytes do not match canonical serialization",
        )
    return result


_SNAPSHOT_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "event_id",
        "candidate_id",
        "cohort_id",
        "event_category",
        "issuer",
        "security_id",
        "ticker",
        "policy_sha256",
        "candidate_manifest_sha256",
        "producer_build_sha256",
        "created_at",
        "universe_frozen_at",
        "timing_bucket",
        "release_family",
        "event_published_at",
        "prior_eligible_session_close_at",
        "reaction_session_id",
        "reaction_session_open_at",
        "reaction_session_close_at",
        "observation_window_start_at",
        "observation_window_end_at",
        "evidence_cutoff_at",
        "decision_cutoff_at",
        "candidate_entry_deadline_at",
        "evidence_packet_sha256",
        "evidence_refs",
        "eligibility",
        "eligibility_reason_codes",
        "data_health",
        "health_reason_codes",
        "allowed_unknown_codes",
        "critical_unknown_codes",
    }
)


def strategy_snapshot_payload(value: StrategySnapshot) -> dict[str, object]:
    """Return the versioned JSON object for a strategy snapshot."""

    return {
        "allowed_unknown_codes": list(value.allowed_unknown_codes),
        "candidate_entry_deadline_at": _timestamp_text(value.candidate_entry_deadline_at),
        "candidate_id": value.candidate_id,
        "candidate_manifest_sha256": value.candidate_manifest_sha256,
        "cohort_id": value.cohort_id,
        "created_at": _timestamp_text(value.created_at),
        "critical_unknown_codes": list(value.critical_unknown_codes),
        "data_health": value.data_health.value,
        "decision_cutoff_at": _timestamp_text(value.decision_cutoff_at),
        "eligibility": value.eligibility.value,
        "eligibility_reason_codes": list(value.eligibility_reason_codes),
        "event_category": value.event_category.value,
        "event_id": value.event_id,
        "event_published_at": _timestamp_text(value.event_published_at),
        "prior_eligible_session_close_at": (
            _timestamp_text(value.prior_eligible_session_close_at)
            if value.prior_eligible_session_close_at is not None
            else None
        ),
        "evidence_cutoff_at": _timestamp_text(value.evidence_cutoff_at),
        "evidence_packet_sha256": value.evidence_packet_sha256,
        "evidence_refs": [_evidence_ref_payload(item) for item in value.evidence_refs],
        "health_reason_codes": list(value.health_reason_codes),
        "issuer": value.issuer,
        "observation_window_end_at": _timestamp_text(value.observation_window_end_at),
        "observation_window_start_at": _timestamp_text(value.observation_window_start_at),
        "policy_sha256": value.policy_sha256,
        "producer_build_sha256": value.producer_build_sha256,
        "reaction_session_close_at": _timestamp_text(value.reaction_session_close_at),
        "reaction_session_id": value.reaction_session_id,
        "reaction_session_open_at": _timestamp_text(value.reaction_session_open_at),
        "release_family": value.release_family.value if value.release_family is not None else None,
        "schema": STRATEGY_SNAPSHOT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "security_id": value.security_id,
        "ticker": value.ticker,
        "timing_bucket": value.timing_bucket.value,
        "universe_frozen_at": _timestamp_text(value.universe_frozen_at),
    }


def strategy_snapshot_bytes(value: StrategySnapshot) -> bytes:
    """Serialize a strategy snapshot to canonical immutable bytes."""

    return canonical_json_bytes(strategy_snapshot_payload(value))


def strategy_snapshot_sha256(value: StrategySnapshot) -> str:
    """Hash the canonical strategy-snapshot bytes."""

    return sha256_bytes(strategy_snapshot_bytes(value))


def parse_strategy_snapshot(raw: bytes) -> StrategySnapshot:
    """Strictly parse canonical ``esscher.strategy_snapshot/v1`` bytes."""

    payload = _strict_object(
        _decode(raw, path="strategy_snapshot"), path="strategy_snapshot", fields=_SNAPSHOT_FIELDS
    )
    _verify_schema(payload, schema=STRATEGY_SNAPSHOT_SCHEMA, path="strategy_snapshot")
    evidence_value = payload["evidence_refs"]
    if not isinstance(evidence_value, list):
        _reject(
            StrategyContractReason.INVALID_DOCUMENT,
            "strategy_snapshot.evidence_refs",
            "must be a list",
        )
    release_value = payload["release_family"]
    release_family = (
        None
        if release_value is None
        else _enum(ReleaseFamily, release_value, path="strategy_snapshot.release_family")
    )
    timestamps: dict[str, datetime] = {}
    for field in (
        "created_at",
        "universe_frozen_at",
        "event_published_at",
        "reaction_session_open_at",
        "reaction_session_close_at",
        "observation_window_start_at",
        "observation_window_end_at",
        "evidence_cutoff_at",
        "decision_cutoff_at",
        "candidate_entry_deadline_at",
    ):
        parsed = _timestamp(payload[field], path=f"strategy_snapshot.{field}")
        assert parsed is not None
        timestamps[field] = parsed
    prior_eligible_session_close_at = _timestamp(
        payload["prior_eligible_session_close_at"],
        path="strategy_snapshot.prior_eligible_session_close_at",
        nullable=True,
    )
    result = _wrap_model_error(
        "strategy_snapshot",
        lambda: StrategySnapshot(
            event_id=_identifier(payload["event_id"], path="strategy_snapshot.event_id"),
            candidate_id=_identifier(
                payload["candidate_id"], path="strategy_snapshot.candidate_id"
            ),
            cohort_id=_identifier(payload["cohort_id"], path="strategy_snapshot.cohort_id"),
            event_category=_enum(
                EventCategory, payload["event_category"], path="strategy_snapshot.event_category"
            ),
            issuer=_text(payload["issuer"], path="strategy_snapshot.issuer"),
            security_id=_identifier(payload["security_id"], path="strategy_snapshot.security_id"),
            ticker=_text(payload["ticker"], path="strategy_snapshot.ticker"),
            policy_sha256=_sha256(payload["policy_sha256"], path="strategy_snapshot.policy_sha256"),
            candidate_manifest_sha256=_sha256(
                payload["candidate_manifest_sha256"],
                path="strategy_snapshot.candidate_manifest_sha256",
            ),
            producer_build_sha256=_sha256(
                payload["producer_build_sha256"], path="strategy_snapshot.producer_build_sha256"
            ),
            created_at=timestamps["created_at"],
            universe_frozen_at=timestamps["universe_frozen_at"],
            timing_bucket=_enum(
                TimingBucket, payload["timing_bucket"], path="strategy_snapshot.timing_bucket"
            ),
            release_family=release_family,
            event_published_at=timestamps["event_published_at"],
            reaction_session_id=_identifier(
                payload["reaction_session_id"], path="strategy_snapshot.reaction_session_id"
            ),
            reaction_session_open_at=timestamps["reaction_session_open_at"],
            reaction_session_close_at=timestamps["reaction_session_close_at"],
            observation_window_start_at=timestamps["observation_window_start_at"],
            observation_window_end_at=timestamps["observation_window_end_at"],
            evidence_cutoff_at=timestamps["evidence_cutoff_at"],
            decision_cutoff_at=timestamps["decision_cutoff_at"],
            candidate_entry_deadline_at=timestamps["candidate_entry_deadline_at"],
            evidence_packet_sha256=_sha256(
                payload["evidence_packet_sha256"], path="strategy_snapshot.evidence_packet_sha256"
            ),
            evidence_refs=tuple(
                _parse_evidence_ref(item, path=f"strategy_snapshot.evidence_refs[{index}]")
                for index, item in enumerate(evidence_value)
            ),
            eligibility=_enum(
                EligibilityState, payload["eligibility"], path="strategy_snapshot.eligibility"
            ),
            eligibility_reason_codes=_string_list(
                payload["eligibility_reason_codes"],
                path="strategy_snapshot.eligibility_reason_codes",
                reason_codes=True,
            ),
            data_health=_enum(
                DataHealthState, payload["data_health"], path="strategy_snapshot.data_health"
            ),
            health_reason_codes=_string_list(
                payload["health_reason_codes"],
                path="strategy_snapshot.health_reason_codes",
                reason_codes=True,
            ),
            allowed_unknown_codes=_string_list(
                payload["allowed_unknown_codes"],
                path="strategy_snapshot.allowed_unknown_codes",
                reason_codes=True,
            ),
            critical_unknown_codes=_string_list(
                payload["critical_unknown_codes"],
                path="strategy_snapshot.critical_unknown_codes",
                reason_codes=True,
            ),
            prior_eligible_session_close_at=prior_eligible_session_close_at,
        ),
    )
    assert isinstance(result, StrategySnapshot)
    if strategy_snapshot_bytes(result) != raw:
        _reject(
            StrategyContractReason.NON_CANONICAL_DOCUMENT,
            "strategy_snapshot",
            "bytes do not match canonical serialization",
        )
    return result


def _feature_component_payload(value: FeatureComponent) -> dict[str, object]:
    return {
        "component_id": value.component_id,
        "source_refs": list(value.source_refs),
        "status": value.status.value,
        "unit": value.unit,
        "value": _feature_scalar_payload(value.value),
    }


def _feature_scalar_payload(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean feature values are forbidden")
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str):
        return value
    raise TypeError("unsupported feature value type")


def _feature_payload(value: FeatureValue) -> dict[str, object]:
    return {
        "components": [_feature_component_payload(item) for item in value.components],
        "feature_id": value.feature_id,
        "observed_at": _timestamp_text(value.observed_at) if value.observed_at else None,
        "source_refs": list(value.source_refs),
        "status": value.status.value,
        "unit": value.unit,
        "value": _feature_scalar_payload(value.value),
        "value_type": value.value_type.value,
    }


_FEATURE_COMPONENT_FIELDS = frozenset({"component_id", "status", "value", "unit", "source_refs"})
_FEATURE_FIELDS = frozenset(
    {
        "feature_id",
        "status",
        "value",
        "value_type",
        "unit",
        "observed_at",
        "source_refs",
        "components",
    }
)
_FEATURE_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "event_id",
        "candidate_id",
        "cohort_id",
        "policy_sha256",
        "strategy_snapshot_sha256",
        "producer_build_sha256",
        "created_at",
        "feature_snapshot_at",
        "features",
    }
)


def _parse_feature_scalar(
    value: object,
    *,
    value_type: FeatureValueType,
    status: FeatureStatus,
    path: str,
) -> Decimal | int | GuidanceDirection | str | None:
    if status is not FeatureStatus.PRESENT:
        if value is not None:
            _reject(
                StrategyContractReason.FEATURE_VALUE_INVALID,
                path,
                "non-present features require null value",
            )
        return None
    if value_type is FeatureValueType.DECIMAL_STRING_MAP:
        if value is not None:
            _reject(
                StrategyContractReason.FEATURE_VALUE_INVALID,
                path,
                "vector features require null scalar value",
            )
        return None
    if value_type is FeatureValueType.INTEGER:
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            _reject(
                StrategyContractReason.FEATURE_VALUE_INVALID,
                path,
                "millisecond values must be nonnegative canonical integer text",
            )
        parsed = int(value)
        if str(parsed) != value:
            _reject(StrategyContractReason.FEATURE_VALUE_INVALID, path, "integer is not canonical")
        return parsed
    if value_type is FeatureValueType.ENUM:
        category = _reason_code(value, path=path)
        try:
            return GuidanceDirection(category)
        except ValueError:
            return category
    return _decimal(value, path=path)


def _parse_feature_component(value: object, *, path: str) -> FeatureComponent:
    payload = _strict_object(value, path=path, fields=_FEATURE_COMPONENT_FIELDS)
    status = _enum(FeatureStatus, payload["status"], path=f"{path}.status")
    unit = _feature_unit(payload["unit"], path=f"{path}.unit")
    scalar = _parse_feature_scalar(
        payload["value"],
        value_type=FeatureValueType.DECIMAL_STRING,
        status=status,
        path=f"{path}.value",
    )
    if isinstance(scalar, (GuidanceDirection, str)):
        _reject(
            StrategyContractReason.FEATURE_VALUE_INVALID,
            f"{path}.value",
            "vector components must be numeric",
        )
    return FeatureComponent(
        component_id=_identifier(payload["component_id"], path=f"{path}.component_id"),
        status=status,
        value=scalar,
        unit=unit,
        source_refs=_string_list(payload["source_refs"], path=f"{path}.source_refs"),
    )


def _parse_feature(value: object, *, path: str) -> FeatureValue:
    payload = _strict_object(value, path=path, fields=_FEATURE_FIELDS)
    status = _enum(FeatureStatus, payload["status"], path=f"{path}.status")
    value_type = _enum(
        FeatureValueType,
        payload["value_type"],
        path=f"{path}.value_type",
    )
    unit = _feature_unit(payload["unit"], path=f"{path}.unit")
    components_value = payload["components"]
    if not isinstance(components_value, list):
        _reject(StrategyContractReason.INVALID_DOCUMENT, f"{path}.components", "must be a list")
    observed_at = _timestamp(payload["observed_at"], path=f"{path}.observed_at", nullable=True)
    return FeatureValue(
        feature_id=_identifier(payload["feature_id"], path=f"{path}.feature_id"),
        status=status,
        value=_parse_feature_scalar(
            payload["value"],
            value_type=value_type,
            status=status,
            path=f"{path}.value",
        ),
        value_type=value_type,
        unit=unit,
        observed_at=observed_at,
        source_refs=_string_list(payload["source_refs"], path=f"{path}.source_refs"),
        components=tuple(
            _parse_feature_component(item, path=f"{path}.components[{index}]")
            for index, item in enumerate(components_value)
        ),
    )


def feature_receipt_payload(value: FeatureReceipt) -> dict[str, object]:
    """Return the versioned JSON object for a deterministic feature receipt."""

    return {
        "candidate_id": value.candidate_id,
        "cohort_id": value.cohort_id,
        "created_at": _timestamp_text(value.created_at),
        "event_id": value.event_id,
        "feature_snapshot_at": _timestamp_text(value.feature_snapshot_at),
        "features": [_feature_payload(item) for item in value.features],
        "policy_sha256": value.policy_sha256,
        "producer_build_sha256": value.producer_build_sha256,
        "schema": FEATURE_RECEIPT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "strategy_snapshot_sha256": value.strategy_snapshot_sha256,
    }


def feature_receipt_bytes(value: FeatureReceipt) -> bytes:
    """Serialize a feature receipt to canonical immutable bytes."""

    return canonical_json_bytes(feature_receipt_payload(value))


def feature_receipt_sha256(value: FeatureReceipt) -> str:
    """Hash canonical feature-receipt bytes."""

    return sha256_bytes(feature_receipt_bytes(value))


def parse_feature_receipt(raw: bytes) -> FeatureReceipt:
    """Strictly parse canonical ``esscher.feature_receipt/v1`` bytes."""

    payload = _strict_object(
        _decode(raw, path="feature_receipt"),
        path="feature_receipt",
        fields=_FEATURE_RECEIPT_FIELDS,
    )
    _verify_schema(payload, schema=FEATURE_RECEIPT_SCHEMA, path="feature_receipt")
    features_value = payload["features"]
    if not isinstance(features_value, list):
        _reject(
            StrategyContractReason.INVALID_DOCUMENT, "feature_receipt.features", "must be a list"
        )
    created_at = _timestamp(payload["created_at"], path="feature_receipt.created_at")
    feature_snapshot_at = _timestamp(
        payload["feature_snapshot_at"], path="feature_receipt.feature_snapshot_at"
    )
    assert created_at is not None
    assert feature_snapshot_at is not None
    result = _wrap_model_error(
        "feature_receipt",
        lambda: FeatureReceipt(
            event_id=_identifier(payload["event_id"], path="feature_receipt.event_id"),
            candidate_id=_identifier(payload["candidate_id"], path="feature_receipt.candidate_id"),
            cohort_id=_identifier(payload["cohort_id"], path="feature_receipt.cohort_id"),
            policy_sha256=_sha256(payload["policy_sha256"], path="feature_receipt.policy_sha256"),
            strategy_snapshot_sha256=_sha256(
                payload["strategy_snapshot_sha256"],
                path="feature_receipt.strategy_snapshot_sha256",
            ),
            producer_build_sha256=_sha256(
                payload["producer_build_sha256"], path="feature_receipt.producer_build_sha256"
            ),
            created_at=created_at,
            feature_snapshot_at=feature_snapshot_at,
            features=tuple(
                _parse_feature(item, path=f"feature_receipt.features[{index}]")
                for index, item in enumerate(features_value)
            ),
        ),
    )
    assert isinstance(result, FeatureReceipt)
    if feature_receipt_bytes(result) != raw:
        _reject(
            StrategyContractReason.NON_CANONICAL_DOCUMENT,
            "feature_receipt",
            "bytes do not match canonical serialization",
        )
    return result


def _policy_feature_specs(policy: object, candidate_id: str) -> dict[str, Mapping[str, object]]:
    try:
        values = policy.features(candidate_id)  # type: ignore[attr-defined]
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, "policy.features", str(error))
    result: dict[str, Mapping[str, object]] = {}
    for index, item in enumerate(values):
        if not isinstance(item, Mapping) or not isinstance(item.get("feature_id"), str):
            _reject(
                StrategyContractReason.POLICY_MISMATCH,
                f"policy.features[{index}]",
                "policy feature spec is malformed",
            )
        feature_id = item["feature_id"]
        if feature_id in result:
            _reject(
                StrategyContractReason.POLICY_MISMATCH,
                f"policy.features[{index}].feature_id",
                "duplicate policy feature ID",
            )
        result[feature_id] = item
    return result


def _policy_unknown_code_sets(policy: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        data = policy.data  # type: ignore[attr-defined]
        reasoner = data["reasoner"]
        tolerated = tuple(reasoner["tolerated_unknown_codes"])
        critical = tuple(reasoner["critical_unknown_codes"])
    except (AttributeError, InvalidOperation, KeyError, TypeError) as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, "policy.reasoner", str(error))
    return tuple(sorted(tolerated)), tuple(sorted(critical))


def _conditional_feature_required(
    required_if: object,
    *,
    snapshot: StrategySnapshot,
    features: Mapping[str, FeatureValue],
) -> bool:
    if required_if in {"ALWAYS", "VERIFIED_SIP_QUOTE_ENTITLEMENT"}:
        return True
    if required_if == "COHORT_IS_BLS_JOLTS":
        return snapshot.cohort_id == "BLS_JOLTS"
    if required_if == "COHORT_IS_BLS_EMPLOYMENT_SITUATION":
        return snapshot.cohort_id == "BLS_EMPLOYMENT_SITUATION"
    if required_if == "OFFICIAL_REPORT_PUBLISHES_REVISIONS":
        return snapshot.candidate_id == "MACRO_SPY_CONTINUATION_CHALLENGER_V1"
    if required_if in {
        "POINT_IN_TIME_CONSENSUS_AVAILABLE",
        "POINT_IN_TIME_EPS_CONSENSUS_AVAILABLE",
        "POINT_IN_TIME_REVENUE_CONSENSUS_AVAILABLE",
    }:
        return False
    if required_if == "REQUIRED_WHEN_EPS_CONSENSUS_SURPRISE_IS_UNAVAILABLE":
        consensus = features.get("earnings.eps_consensus_surprise_pct.v1")
        if consensus is None:
            _reject(
                StrategyContractReason.POLICY_MISMATCH,
                "policy.features.earnings.eps_consensus_surprise_pct.v1",
                "conditional feature dependency is absent",
            )
        return consensus.status is not FeatureStatus.PRESENT
    _reject(
        StrategyContractReason.POLICY_MISMATCH,
        "policy.features.required_if",
        f"unknown conditional requirement {required_if}",
    )


def _validate_input_against_policy(value: StrategyInput, policy: object) -> None:
    manifest = value.candidate_manifest
    snapshot = value.snapshot
    receipt = value.feature_receipt
    try:
        registered_sha256 = policy.sha256  # type: ignore[attr-defined]
        candidate_ids = tuple(policy.candidate_ids)  # type: ignore[attr-defined]
        cohort_ids = tuple(policy.cohort_ids(snapshot.candidate_id))  # type: ignore[attr-defined]
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, "policy", str(error))
    if snapshot.policy_sha256 != registered_sha256:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.policy_sha256",
            "snapshot does not bind the registered strategy policy",
        )
    if manifest.policy_sha256 != registered_sha256:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "candidate_manifest.policy_sha256",
            "candidate manifest does not bind the registered strategy policy",
        )
    if snapshot.candidate_id not in candidate_ids:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.candidate_id",
            "candidate is absent from the registered strategy policy",
        )
    if manifest.candidate_id != snapshot.candidate_id:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "candidate_manifest.candidate_id",
            "candidate manifest does not match the snapshot candidate",
        )
    if snapshot.cohort_id not in cohort_ids:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.cohort_id",
            "cohort is absent from the registered candidate policy",
        )
    if any(record.cohort_id not in cohort_ids for record in manifest.records):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "candidate_manifest.records.cohort_id",
            "candidate manifest contains an unregistered cohort",
        )
    clock = _validate_snapshot_clock(snapshot, policy)
    candidate_policy = _policy_candidate(policy, snapshot.candidate_id)
    evidence_policy = candidate_policy.get("evidence")
    _validate_amc_prior_session_binding(snapshot, manifest, clock)
    _validate_macro_release_binding(snapshot, manifest, candidate_policy)
    if not isinstance(evidence_policy, Mapping):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.evidence",
            "evidence policy must be an object",
        )
    permitted_source_classes = evidence_policy.get("permitted_source_classes")
    required_source_classes = evidence_policy.get("required_source_classes")
    if not isinstance(permitted_source_classes, tuple) or not isinstance(
        required_source_classes,
        tuple,
    ):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.evidence",
            "source-class registries must be immutable arrays",
        )
    observed_source_classes = {item.source_class for item in snapshot.evidence_refs}
    if not observed_source_classes <= set(permitted_source_classes):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.evidence_refs.source_class",
            "snapshot contains a source class not permitted by policy",
        )
    if not set(required_source_classes) <= observed_source_classes:
        _reject(
            StrategyContractReason.DATA_HEALTH_REJECTED,
            "strategy_snapshot.evidence_refs.source_class",
            "snapshot is missing a required source class",
        )
    tolerated, critical = _policy_unknown_code_sets(policy)
    if snapshot.allowed_unknown_codes != tuple(sorted((*tolerated, *critical))):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.allowed_unknown_codes",
            "unknown-code registry does not match policy",
        )
    if snapshot.critical_unknown_codes != critical:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.critical_unknown_codes",
            "critical unknown codes do not match policy",
        )
    specs = _policy_feature_specs(policy, snapshot.candidate_id)
    actual = receipt.features
    actual_by_id = {feature.feature_id: feature for feature in actual}
    if tuple(feature.feature_id for feature in actual) != tuple(sorted(specs)):
        _reject(
            StrategyContractReason.FEATURE_SET_MISMATCH,
            "feature_receipt.features",
            "feature IDs do not exactly match the candidate policy",
        )
    for feature in actual:
        spec = specs[feature.feature_id]
        expected_unit = spec.get("unit")
        if expected_unit != feature.unit:
            _reject(
                StrategyContractReason.FEATURE_SET_MISMATCH,
                f"feature_receipt.features.{feature.feature_id}.unit",
                "feature unit does not match policy",
            )
        expected_value_type = spec.get("value_type")
        if expected_value_type != feature.value_type.value:
            _reject(
                StrategyContractReason.FEATURE_SET_MISMATCH,
                f"feature_receipt.features.{feature.feature_id}.value_type",
                "feature value type does not match policy",
            )
        statuses = spec.get("status_values")
        if not isinstance(statuses, (list, tuple)) or feature.status.value not in statuses:
            _reject(
                StrategyContractReason.FEATURE_SET_MISMATCH,
                f"feature_receipt.features.{feature.feature_id}.status",
                "feature status is not admitted by policy",
            )
        if spec.get("required") is True and feature.status is not FeatureStatus.PRESENT:
            _reject(
                StrategyContractReason.DATA_HEALTH_REJECTED,
                f"feature_receipt.features.{feature.feature_id}.status",
                "required feature is not PRESENT",
            )
        required_if = spec.get("required_if")
        condition_applies = _conditional_feature_required(
            required_if,
            snapshot=snapshot,
            features=actual_by_id,
        )
        if condition_applies and feature.status is not FeatureStatus.PRESENT:
            _reject(
                StrategyContractReason.DATA_HEALTH_REJECTED,
                f"feature_receipt.features.{feature.feature_id}.status",
                f"feature is required when {required_if}",
            )


def build_strategy_input(
    snapshot_bytes: bytes,
    *,
    candidate_manifest_bytes: bytes,
    feature_receipt_bytes: bytes,
) -> StrategyInput:
    """Join exact canonical #27 snapshot and feature artifacts under frozen policy."""

    manifest_raw = _require_bytes(candidate_manifest_bytes, path="candidate_manifest")
    snapshot_raw = _require_bytes(snapshot_bytes, path="strategy_snapshot")
    receipt_raw = _require_bytes(feature_receipt_bytes, path="feature_receipt")
    manifest = parse_candidate_manifest(manifest_raw)
    snapshot = parse_strategy_snapshot(snapshot_raw)
    receipt = parse_feature_receipt(receipt_raw)
    try:
        value = StrategyInput(
            candidate_manifest=manifest,
            snapshot=snapshot,
            feature_receipt=receipt,
            candidate_manifest_sha256=sha256_bytes(manifest_raw),
            snapshot_sha256=sha256_bytes(snapshot_raw),
            feature_receipt_sha256=sha256_bytes(receipt_raw),
        )
    except ValueError as error:
        _reject(StrategyContractReason.IDENTITY_MISMATCH, "strategy_input", str(error))
    try:
        from ringdown_market.strategy.policy import load_strategy_policy

        policy = load_strategy_policy()
    except (ImportError, OSError, TypeError, ValueError) as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, "policy", str(error))
    _validate_input_against_policy(value, policy)
    return value


_CONTRADICTION_FIELDS = frozenset({"evidence_ids", "summary"})
_FALSIFIER_FIELDS = frozenset({"evidence_id", "summary"})
_REASONER_DECISION_FIELDS = frozenset(
    {
        "decision",
        "evidence_ids",
        "contradictions",
        "unknowns",
        "strongest_falsifier",
        "summary",
    }
)


def _parse_contradiction(value: object, *, path: str) -> Contradiction:
    payload = _strict_object(value, path=path, fields=_CONTRADICTION_FIELDS)
    evidence_ids = _string_list(payload["evidence_ids"], path=f"{path}.evidence_ids", nonempty=True)
    if len(evidence_ids) != 2:
        _reject(
            StrategyContractReason.REASONER_OUTPUT_INVALID,
            f"{path}.evidence_ids",
            "a contradiction requires exactly two distinct evidence IDs",
        )
    return Contradiction(
        evidence_ids=(evidence_ids[0], evidence_ids[1]),
        summary=_text(payload["summary"], path=f"{path}.summary", maximum=400),
    )


def _contradiction_payload(value: Contradiction) -> dict[str, object]:
    return {"evidence_ids": list(value.evidence_ids), "summary": value.summary}


def _parse_falsifier(value: object, *, path: str) -> Falsifier | None:
    if value is None:
        return None
    payload = _strict_object(value, path=path, fields=_FALSIFIER_FIELDS)
    return Falsifier(
        evidence_id=_identifier(payload["evidence_id"], path=f"{path}.evidence_id"),
        summary=_text(payload["summary"], path=f"{path}.summary", maximum=400),
    )


def _falsifier_payload(value: Falsifier | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {"evidence_id": value.evidence_id, "summary": value.summary}


def reasoner_decision_payload(value: ReasonerDecision) -> dict[str, object]:
    """Return the six-field provider-facing reasoner decision payload."""

    return {
        "contradictions": [_contradiction_payload(item) for item in value.contradictions],
        "decision": value.decision.value,
        "evidence_ids": list(value.evidence_ids),
        "strongest_falsifier": _falsifier_payload(value.strongest_falsifier),
        "summary": value.summary,
        "unknowns": list(value.unknowns),
    }


def reasoner_decision_bytes(value: ReasonerDecision) -> bytes:
    """Canonicalize strictly parsed reasoner semantics for identity binding."""

    return canonical_json_bytes(reasoner_decision_payload(value))


def reasoner_decision_sha256(value: ReasonerDecision) -> str:
    """Hash canonical typed reasoner semantics, not provider formatting."""

    return sha256_bytes(reasoner_decision_bytes(value))


def parse_reasoner_decision(raw_response: bytes) -> ReasonerDecision:
    """Parse an exact untrusted JSON response with no prose or authority fields.

    Unlike durable internal artifacts, provider bytes are not required to already
    use Esscher's canonical whitespace/key order.  Their exact raw hash belongs in
    :class:`ReasonerExchange`; this parser supplies the separate semantic hash.
    """

    payload = _strict_object(
        _decode(raw_response, path="reasoner_response"),
        path="reasoner_response",
        fields=_REASONER_DECISION_FIELDS,
    )
    contradictions_value = payload["contradictions"]
    if not isinstance(contradictions_value, list):
        _reject(
            StrategyContractReason.REASONER_OUTPUT_INVALID,
            "reasoner_response.contradictions",
            "must be a list",
        )
    try:
        return ReasonerDecision(
            decision=_enum(Direction, payload["decision"], path="reasoner_response.decision"),
            evidence_ids=_string_list(
                payload["evidence_ids"], path="reasoner_response.evidence_ids"
            ),
            contradictions=tuple(
                _parse_contradiction(
                    item,
                    path=f"reasoner_response.contradictions[{index}]",
                )
                for index, item in enumerate(contradictions_value)
            ),
            unknowns=_string_list(
                payload["unknowns"],
                path="reasoner_response.unknowns",
                reason_codes=True,
            ),
            strongest_falsifier=_parse_falsifier(
                payload["strongest_falsifier"],
                path="reasoner_response.strongest_falsifier",
            ),
            summary=_text(payload["summary"], path="reasoner_response.summary", maximum=800),
        )
    except StrategyContractRejected:
        raise
    except ValueError as error:
        _reject(StrategyContractReason.REASONER_OUTPUT_INVALID, "reasoner_response", str(error))


def _decoding_payload(value: DecodingParameters) -> dict[str, object]:
    return {
        "max_output_tokens": value.max_output_tokens,
        "seed": value.seed,
        "temperature": _decimal_text(value.temperature),
        "top_p": _decimal_text(value.top_p),
    }


def reasoner_model_config_sha256(
    *,
    provider: str,
    model: str,
    model_revision: str | None,
    decoding: DecodingParameters,
) -> str:
    """Hash the provider identity and exact decoding configuration."""

    return sha256_bytes(
        canonical_json_bytes(
            {
                "decoding": _decoding_payload(decoding),
                "model": model,
                "model_revision": model_revision,
                "provider": provider,
                "schema": "esscher.reasoner_model_config",
                "schema_version": SCHEMA_VERSION,
            }
        )
    )


def _parse_decoding(value: object, *, path: str) -> DecodingParameters:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset({"temperature", "top_p", "max_output_tokens", "seed"}),
    )
    seed_value = payload["seed"]
    seed = None if seed_value is None else _integer(seed_value, path=f"{path}.seed")
    return DecodingParameters(
        temperature=_decimal(payload["temperature"], path=f"{path}.temperature"),
        top_p=_decimal(payload["top_p"], path=f"{path}.top_p"),
        max_output_tokens=_integer(payload["max_output_tokens"], path=f"{path}.max_output_tokens"),
        seed=seed,
    )


_REASONER_EXCHANGE_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "event_id",
        "candidate_id",
        "policy_sha256",
        "strategy_snapshot_sha256",
        "feature_receipt_sha256",
        "evidence_packet_sha256",
        "route_sha256",
        "prompt_sha256",
        "output_schema_sha256",
        "request_sha256",
        "raw_response_sha256",
        "provider",
        "model",
        "model_revision",
        "model_config_sha256",
        "decoding",
        "started_at",
        "responded_at",
        "deadline_at",
        "status",
        "error_code",
        "producer_build_sha256",
        "created_at",
    }
)


def reasoner_exchange_payload(value: ReasonerExchange) -> dict[str, object]:
    """Return the canonical hosted-reasoner exchange receipt object."""

    return {
        "candidate_id": value.candidate_id,
        "created_at": _timestamp_text(value.created_at),
        "deadline_at": _timestamp_text(value.deadline_at),
        "decoding": _decoding_payload(value.decoding),
        "error_code": value.error_code,
        "evidence_packet_sha256": value.evidence_packet_sha256,
        "event_id": value.event_id,
        "feature_receipt_sha256": value.feature_receipt_sha256,
        "model": value.model,
        "model_config_sha256": value.model_config_sha256,
        "model_revision": value.model_revision,
        "output_schema_sha256": value.output_schema_sha256,
        "policy_sha256": value.policy_sha256,
        "producer_build_sha256": value.producer_build_sha256,
        "prompt_sha256": value.prompt_sha256,
        "provider": value.provider,
        "raw_response_sha256": value.raw_response_sha256,
        "request_sha256": value.request_sha256,
        "responded_at": _timestamp_text(value.responded_at) if value.responded_at else None,
        "route_sha256": value.route_sha256,
        "schema": REASONER_EXCHANGE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "started_at": _timestamp_text(value.started_at),
        "status": value.status.value,
        "strategy_snapshot_sha256": value.strategy_snapshot_sha256,
    }


def reasoner_exchange_bytes(value: ReasonerExchange) -> bytes:
    """Serialize a reasoner exchange to canonical immutable bytes."""

    return canonical_json_bytes(reasoner_exchange_payload(value))


def reasoner_exchange_sha256(value: ReasonerExchange) -> str:
    """Hash canonical reasoner-exchange bytes."""

    return sha256_bytes(reasoner_exchange_bytes(value))


def parse_reasoner_exchange(raw: bytes) -> ReasonerExchange:
    """Strictly parse canonical ``esscher.reasoner_exchange/v1`` bytes."""

    payload = _strict_object(
        _decode(raw, path="reasoner_exchange"),
        path="reasoner_exchange",
        fields=_REASONER_EXCHANGE_FIELDS,
    )
    _verify_schema(payload, schema=REASONER_EXCHANGE_SCHEMA, path="reasoner_exchange")
    timestamps: dict[str, datetime | None] = {}
    for field, nullable in (
        ("created_at", False),
        ("started_at", False),
        ("responded_at", True),
        ("deadline_at", False),
    ):
        timestamps[field] = _timestamp(
            payload[field], path=f"reasoner_exchange.{field}", nullable=nullable
        )
    raw_hash_value = payload["raw_response_sha256"]
    raw_hash = (
        None
        if raw_hash_value is None
        else _sha256(raw_hash_value, path="reasoner_exchange.raw_response_sha256")
    )
    error_value = payload["error_code"]
    error_code = (
        None
        if error_value is None
        else _reason_code(error_value, path="reasoner_exchange.error_code")
    )
    revision_value = payload["model_revision"]
    model_revision = (
        None
        if revision_value is None
        else _text(revision_value, path="reasoner_exchange.model_revision")
    )
    assert timestamps["created_at"] is not None
    assert timestamps["started_at"] is not None
    assert timestamps["deadline_at"] is not None
    result = _wrap_model_error(
        "reasoner_exchange",
        lambda: ReasonerExchange(
            event_id=_identifier(payload["event_id"], path="reasoner_exchange.event_id"),
            candidate_id=_identifier(
                payload["candidate_id"], path="reasoner_exchange.candidate_id"
            ),
            policy_sha256=_sha256(payload["policy_sha256"], path="reasoner_exchange.policy_sha256"),
            strategy_snapshot_sha256=_sha256(
                payload["strategy_snapshot_sha256"],
                path="reasoner_exchange.strategy_snapshot_sha256",
            ),
            feature_receipt_sha256=_sha256(
                payload["feature_receipt_sha256"],
                path="reasoner_exchange.feature_receipt_sha256",
            ),
            evidence_packet_sha256=_sha256(
                payload["evidence_packet_sha256"],
                path="reasoner_exchange.evidence_packet_sha256",
            ),
            route_sha256=_sha256(payload["route_sha256"], path="reasoner_exchange.route_sha256"),
            prompt_sha256=_sha256(payload["prompt_sha256"], path="reasoner_exchange.prompt_sha256"),
            output_schema_sha256=_sha256(
                payload["output_schema_sha256"],
                path="reasoner_exchange.output_schema_sha256",
            ),
            request_sha256=_sha256(
                payload["request_sha256"], path="reasoner_exchange.request_sha256"
            ),
            raw_response_sha256=raw_hash,
            provider=_text(payload["provider"], path="reasoner_exchange.provider"),
            model=_text(payload["model"], path="reasoner_exchange.model"),
            model_revision=model_revision,
            model_config_sha256=_sha256(
                payload["model_config_sha256"],
                path="reasoner_exchange.model_config_sha256",
            ),
            decoding=_parse_decoding(payload["decoding"], path="reasoner_exchange.decoding"),
            started_at=timestamps["started_at"],
            responded_at=timestamps["responded_at"],
            deadline_at=timestamps["deadline_at"],
            status=_enum(ExchangeStatus, payload["status"], path="reasoner_exchange.status"),
            error_code=error_code,
            producer_build_sha256=_sha256(
                payload["producer_build_sha256"],
                path="reasoner_exchange.producer_build_sha256",
            ),
            created_at=timestamps["created_at"],
        ),
    )
    assert isinstance(result, ReasonerExchange)
    if reasoner_exchange_bytes(result) != raw:
        _reject(
            StrategyContractReason.NON_CANONICAL_DOCUMENT,
            "reasoner_exchange",
            "bytes do not match canonical serialization",
        )
    return result


_STRATEGY_DECISION_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "authority",
        "event_id",
        "security_id",
        "candidate_id",
        "cohort_id",
        "policy_sha256",
        "candidate_manifest_sha256",
        "strategy_snapshot_sha256",
        "feature_receipt_sha256",
        "reasoner_exchange_sha256",
        "reasoner_decision_sha256",
        "producer_build_sha256",
        "decision_at",
        "reasoner_direction",
        "direction",
        "disposition",
        "reaction_relation",
        "evidence_ids",
        "contradictions",
        "unknowns",
        "strongest_falsifier",
        "reason_codes",
        "summary",
    }
)


def strategy_decision_payload(value: StrategyDecision) -> dict[str, object]:
    """Return the direction-only ``esscher.validated_decision/v1`` object."""

    return {
        "authority": DECISION_AUTHORITY,
        "candidate_id": value.candidate_id,
        "candidate_manifest_sha256": value.candidate_manifest_sha256,
        "cohort_id": value.cohort_id,
        "contradictions": [_contradiction_payload(item) for item in value.contradictions],
        "decision_at": _timestamp_text(value.decision_at),
        "direction": value.direction.value,
        "disposition": value.disposition.value,
        "event_id": value.event_id,
        "evidence_ids": list(value.evidence_ids),
        "feature_receipt_sha256": value.feature_receipt_sha256,
        "policy_sha256": value.policy_sha256,
        "producer_build_sha256": value.producer_build_sha256,
        "reaction_relation": value.reaction_relation.value,
        "reason_codes": list(value.reason_codes),
        "reasoner_decision_sha256": value.reasoner_decision_sha256,
        "reasoner_direction": (
            value.reasoner_direction.value if value.reasoner_direction is not None else None
        ),
        "reasoner_exchange_sha256": value.reasoner_exchange_sha256,
        "schema": VALIDATED_DECISION_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "security_id": value.security_id,
        "strategy_snapshot_sha256": value.strategy_snapshot_sha256,
        "strongest_falsifier": _falsifier_payload(value.strongest_falsifier),
        "summary": value.summary,
        "unknowns": list(value.unknowns),
    }


def strategy_decision_bytes(value: StrategyDecision) -> bytes:
    """Serialize a validated direction-only decision to canonical bytes."""

    return canonical_json_bytes(strategy_decision_payload(value))


def strategy_decision_sha256(value: StrategyDecision) -> str:
    """Hash canonical validated-decision bytes."""

    return sha256_bytes(strategy_decision_bytes(value))


def parse_strategy_decision(raw: bytes) -> StrategyDecision:
    """Strictly parse canonical ``esscher.validated_decision/v1`` bytes for #29."""

    payload = _strict_object(
        _decode(raw, path="strategy_decision"),
        path="strategy_decision",
        fields=_STRATEGY_DECISION_FIELDS,
    )
    _verify_schema(payload, schema=VALIDATED_DECISION_SCHEMA, path="strategy_decision")
    if payload["authority"] != DECISION_AUTHORITY:
        _reject(
            StrategyContractReason.UNKNOWN_STATE,
            "strategy_decision.authority",
            "validated strategy decisions have DIRECTION_ONLY authority",
        )
    contradictions_value = payload["contradictions"]
    if not isinstance(contradictions_value, list):
        _reject(
            StrategyContractReason.INVALID_DOCUMENT,
            "strategy_decision.contradictions",
            "must be a list",
        )
    reasoner_direction_value = payload["reasoner_direction"]
    reasoner_direction = (
        None
        if reasoner_direction_value is None
        else _enum(
            Direction,
            reasoner_direction_value,
            path="strategy_decision.reasoner_direction",
        )
    )
    semantic_hash_value = payload["reasoner_decision_sha256"]
    semantic_hash = (
        None
        if semantic_hash_value is None
        else _sha256(
            semantic_hash_value,
            path="strategy_decision.reasoner_decision_sha256",
        )
    )
    summary_value = payload["summary"]
    summary = (
        None
        if summary_value is None
        else _text(summary_value, path="strategy_decision.summary", maximum=800)
    )
    decision_at = _timestamp(payload["decision_at"], path="strategy_decision.decision_at")
    assert decision_at is not None
    result = _wrap_model_error(
        "strategy_decision",
        lambda: StrategyDecision(
            event_id=_identifier(payload["event_id"], path="strategy_decision.event_id"),
            security_id=_identifier(payload["security_id"], path="strategy_decision.security_id"),
            candidate_id=_identifier(
                payload["candidate_id"], path="strategy_decision.candidate_id"
            ),
            cohort_id=_identifier(payload["cohort_id"], path="strategy_decision.cohort_id"),
            policy_sha256=_sha256(payload["policy_sha256"], path="strategy_decision.policy_sha256"),
            candidate_manifest_sha256=_sha256(
                payload["candidate_manifest_sha256"],
                path="strategy_decision.candidate_manifest_sha256",
            ),
            strategy_snapshot_sha256=_sha256(
                payload["strategy_snapshot_sha256"],
                path="strategy_decision.strategy_snapshot_sha256",
            ),
            feature_receipt_sha256=_sha256(
                payload["feature_receipt_sha256"],
                path="strategy_decision.feature_receipt_sha256",
            ),
            reasoner_exchange_sha256=_sha256(
                payload["reasoner_exchange_sha256"],
                path="strategy_decision.reasoner_exchange_sha256",
            ),
            reasoner_decision_sha256=semantic_hash,
            producer_build_sha256=_sha256(
                payload["producer_build_sha256"],
                path="strategy_decision.producer_build_sha256",
            ),
            decision_at=decision_at,
            reasoner_direction=reasoner_direction,
            direction=_enum(Direction, payload["direction"], path="strategy_decision.direction"),
            disposition=_enum(
                DecisionDisposition,
                payload["disposition"],
                path="strategy_decision.disposition",
            ),
            reaction_relation=_enum(
                ReactionRelation,
                payload["reaction_relation"],
                path="strategy_decision.reaction_relation",
            ),
            evidence_ids=_string_list(
                payload["evidence_ids"], path="strategy_decision.evidence_ids"
            ),
            contradictions=tuple(
                _parse_contradiction(
                    item,
                    path=f"strategy_decision.contradictions[{index}]",
                )
                for index, item in enumerate(contradictions_value)
            ),
            unknowns=_string_list(
                payload["unknowns"],
                path="strategy_decision.unknowns",
                reason_codes=True,
            ),
            strongest_falsifier=_parse_falsifier(
                payload["strongest_falsifier"],
                path="strategy_decision.strongest_falsifier",
            ),
            reason_codes=_string_list(
                payload["reason_codes"],
                path="strategy_decision.reason_codes",
                reason_codes=True,
            ),
            summary=summary,
        ),
    )
    assert isinstance(result, StrategyDecision)
    if strategy_decision_bytes(result) != raw:
        _reject(
            StrategyContractReason.NON_CANONICAL_DOCUMENT,
            "strategy_decision",
            "bytes do not match canonical serialization",
        )
    return result


def _policy_candidate(policy: object, candidate_id: str) -> Mapping[str, object]:
    try:
        value = policy.candidate(candidate_id)  # type: ignore[attr-defined]
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, "policy.candidate", str(error))
    if not isinstance(value, Mapping):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate",
            "candidate policy must be an immutable mapping",
        )
    return value


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _reasoner_policy_hashes(
    policy: object,
    candidate_id: str,
) -> tuple[str, str, str]:
    candidate = _policy_candidate(policy, candidate_id)
    try:
        policy_sha256 = policy.sha256  # type: ignore[attr-defined]
        data = policy.data  # type: ignore[attr-defined]
        authority = data["authority"]
        reasoner = data["reasoner"]
    except (AttributeError, KeyError, TypeError) as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, "policy.reasoner", str(error))
    route = {
        "call_policy": _plain_json(reasoner["call_policy"]),
        "candidate_id": candidate_id,
        "hash_bindings": _plain_json(reasoner["hash_bindings"]),
        "no_tools": reasoner["no_tools"],
        "policy_sha256": policy_sha256,
        "schema": "esscher.reasoner_route_policy",
        "schema_version": SCHEMA_VERSION,
    }
    prompt = {
        "authority": _plain_json(authority),
        "candidate_id": candidate_id,
        "citation_requirements": _plain_json(reasoner["citation_requirements"]),
        "confirmation": _plain_json(candidate["confirmation"]),
        "critical_unknown_codes": _plain_json(reasoner["critical_unknown_codes"]),
        "direction_values": _plain_json(reasoner["direction_values"]),
        "evidence": _plain_json(candidate["evidence"]),
        "features": _plain_json(candidate["features"]),
        "forbidden_fields": _plain_json(reasoner["forbidden_fields"]),
        "hypothesis": candidate["hypothesis"],
        "policy_sha256": policy_sha256,
        "schema": "esscher.reasoner_prompt_contract",
        "schema_version": SCHEMA_VERSION,
        "tolerated_unknown_codes": _plain_json(reasoner["tolerated_unknown_codes"]),
    }
    output_schema = {
        "additional_properties": reasoner["additional_properties"],
        "direction_values": _plain_json(reasoner["direction_values"]),
        "output_fields": _plain_json(reasoner["output_fields"]),
        "schema": "esscher.reasoner_output_contract",
        "schema_version": SCHEMA_VERSION,
    }
    return tuple(
        sha256_bytes(canonical_json_bytes(item)) for item in (route, prompt, output_schema)
    )


def reasoner_policy_hashes(candidate_id: str) -> tuple[str, str, str]:
    """Return the frozen route, prompt-contract, and output-schema hashes."""

    from ringdown_market.strategy.policy import load_strategy_policy

    policy = load_strategy_policy()
    if candidate_id not in policy.candidate_ids:
        raise KeyError(candidate_id)
    actual = _reasoner_policy_hashes(policy, candidate_id)
    expected = dict(_REASONER_POLICY_HASH_REGISTRY)[candidate_id]
    if actual != expected:
        raise StrategyContractRejected(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.reasoner.identity",
            "derived reasoner identity does not match the frozen registry",
        )
    return actual


def _clock_seconds(value: object, *, path: str) -> int:
    if not isinstance(value, str):
        _reject(StrategyContractReason.POLICY_MISMATCH, path, "clock value must be text")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, path, str(error))
    if parsed.tzinfo is not None or parsed.isoformat(timespec="seconds") != value:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            path,
            "clock value must be canonical local wall time",
        )
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def _validate_snapshot_clock(snapshot: StrategySnapshot, policy: object) -> Mapping[str, object]:
    candidate = _policy_candidate(policy, snapshot.candidate_id)
    clocks = candidate.get("clocks")
    if not isinstance(clocks, tuple):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.clocks",
            "registered clocks must be immutable records",
        )
    matches = tuple(clock for clock in clocks if clock.get("cohort_id") == snapshot.cohort_id)
    if len(matches) != 1:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.cohort_id",
            "snapshot cohort must select exactly one registered clock",
        )
    clock = matches[0]
    expected_release_family = clock.get("release_family")
    if expected_release_family is not None and not isinstance(expected_release_family, str):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.clocks.release_family",
            "release family must be null or a registered identifier",
        )
    observed_release_family = (
        snapshot.release_family.value if snapshot.release_family is not None else None
    )
    if observed_release_family != expected_release_family:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.release_family",
            "release family does not match the registered cohort clock",
        )
    timezone_name = clock.get("timezone")
    if timezone_name != "America/New_York":
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.clocks.timezone",
            "v1 clocks require America/New_York",
        )
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.clocks.timezone",
            str(error),
        )
    session_open = snapshot.reaction_session_open_at.astimezone(local_timezone)
    session_close = snapshot.reaction_session_close_at.astimezone(local_timezone)
    if (
        session_open.date() != session_close.date()
        or session_open.time().isoformat(timespec="seconds") != "09:30:00"
        or session_close.time().isoformat(timespec="seconds") != "16:00:00"
    ):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.reaction_session_close_at",
            "v1 requires a full 09:30-16:00 regular session",
        )
    fields = {
        "observation_window_start_at": "observation_start",
        "observation_window_end_at": "observation_end",
        "evidence_cutoff_at": "evidence_cutoff",
        "decision_cutoff_at": "decision_cutoff",
        "candidate_entry_deadline_at": "candidate_entry_deadline",
    }
    for snapshot_field, policy_field in fields.items():
        expected_seconds = _clock_seconds(
            clock.get(policy_field),
            path=f"policy.candidate.clocks.{policy_field}",
        )
        observed = getattr(snapshot, snapshot_field).astimezone(local_timezone)
        observed_seconds = observed.hour * 3600 + observed.minute * 60 + observed.second
        if observed.date() != session_open.date() or observed_seconds != expected_seconds:
            _reject(
                StrategyContractReason.POLICY_MISMATCH,
                f"strategy_snapshot.{snapshot_field}",
                f"timestamp does not match the registered {snapshot.cohort_id} clock",
            )
    expected_timing = {
        "BMO": TimingBucket.BEFORE_OPEN,
        "AMC": TimingBucket.AFTER_CLOSE,
        "BLS_JOLTS": TimingBucket.SCHEDULED_RELEASE,
        "BLS_EMPLOYMENT_SITUATION": TimingBucket.SCHEDULED_RELEASE,
    }[snapshot.cohort_id]
    if snapshot.timing_bucket is not expected_timing:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.timing_bucket",
            "timing bucket does not match the registered cohort",
        )
    event_published = snapshot.event_published_at.astimezone(local_timezone)
    if snapshot.cohort_id == "BMO" and (
        event_published.date() != session_open.date()
        or snapshot.event_published_at >= snapshot.reaction_session_open_at
    ):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.event_published_at",
            "BMO results must be published before the same reaction-session open",
        )
    if snapshot.cohort_id == "AMC":
        prior_close = snapshot.prior_eligible_session_close_at
        if prior_close is None:
            _reject(
                StrategyContractReason.POLICY_MISMATCH,
                "strategy_snapshot.prior_eligible_session_close_at",
                "AMC requires the prior eligible regular-session close boundary",
            )
        prior_close_local = prior_close.astimezone(local_timezone)
        if (
            prior_close_local.time().isoformat(timespec="seconds") != "16:00:00"
            or prior_close_local.date() >= session_open.date()
            or prior_close >= snapshot.reaction_session_open_at
        ):
            _reject(
                StrategyContractReason.POLICY_MISMATCH,
                "strategy_snapshot.prior_eligible_session_close_at",
                "AMC prior eligible-session close must be a preceding 16:00 ET boundary",
            )
        if not (prior_close <= snapshot.event_published_at < snapshot.reaction_session_open_at):
            _reject(
                StrategyContractReason.POLICY_MISMATCH,
                "strategy_snapshot.event_published_at",
                "AMC results must be published at or after the prior close "
                "and before the next open",
            )
    elif snapshot.prior_eligible_session_close_at is not None:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.prior_eligible_session_close_at",
            "only AMC snapshots may bind a prior eligible-session close",
        )
    return clock


def _validate_amc_prior_session_binding(
    snapshot: StrategySnapshot,
    manifest: CandidateManifest,
    clock: Mapping[str, object],
) -> None:
    if snapshot.cohort_id != "AMC":
        return
    prior_close = snapshot.prior_eligible_session_close_at
    if prior_close is None:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.prior_eligible_session_close_at",
            "AMC requires a prior eligible-session close boundary",
        )
    timezone_name = clock.get("timezone")
    if not isinstance(timezone_name, str):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.clocks.timezone",
            "AMC clock timezone must be text",
        )
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.clocks.timezone",
            str(error),
        )
    try:
        scheduled_at = manifest.record(snapshot.event_id).scheduled_at
    except ValueError as error:
        _reject(
            StrategyContractReason.IDENTITY_MISMATCH,
            "candidate_manifest.records",
            str(error),
        )
    prior_close_date = prior_close.astimezone(local_timezone).date()
    scheduled_date = scheduled_at.astimezone(local_timezone).date()
    if prior_close_date != scheduled_date:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.prior_eligible_session_close_at",
            "AMC prior close must share the retained candidate schedule's ET date",
        )


def _macro_schedule_tolerance_seconds(candidate: Mapping[str, object]) -> int:
    data_health = candidate.get("data_health")
    if not isinstance(data_health, Mapping):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.data_health",
            "data-health policy must be an object",
        )
    rules = data_health.get("rules")
    if not isinstance(rules, tuple):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.data_health.rules",
            "data-health rules must be immutable records",
        )
    matches = tuple(
        rule
        for rule in rules
        if isinstance(rule, Mapping)
        and rule.get("rule_id") == "official_publication_schedule_delta_max"
    )
    if len(matches) != 1:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.data_health.rules",
            "macro policy requires exactly one publication/schedule tolerance",
        )
    rule = matches[0]
    tolerance = rule.get("value")
    if (
        rule.get("operator") != "LTE"
        or rule.get("unit") != "SECONDS"
        or isinstance(tolerance, bool)
        or not isinstance(tolerance, int)
        or tolerance < 0
    ):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.data_health.rules.official_publication_schedule_delta_max",
            "macro publication/schedule tolerance must be a non-negative LTE seconds rule",
        )
    return tolerance


def _validate_macro_release_binding(
    snapshot: StrategySnapshot,
    manifest: CandidateManifest,
    candidate: Mapping[str, object],
) -> None:
    if snapshot.event_category is not EventCategory.SCHEDULED_MACRO_RELEASE:
        return
    try:
        scheduled_at = manifest.record(snapshot.event_id).scheduled_at
    except ValueError as error:
        _reject(
            StrategyContractReason.IDENTITY_MISMATCH,
            "candidate_manifest.records",
            str(error),
        )
    tolerance_seconds = _macro_schedule_tolerance_seconds(candidate)
    if abs(snapshot.event_published_at - scheduled_at) > timedelta(seconds=tolerance_seconds):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.event_published_at",
            "official macro publication time exceeds the retained schedule tolerance",
        )
    official_primary = tuple(
        item
        for item in snapshot.evidence_refs
        if item.role is EvidenceRole.MACRO_PRIMARY and item.source_class == "OFFICIAL_BLS_RELEASE"
    )
    if len(official_primary) != 1:
        _reject(
            StrategyContractReason.DATA_HEALTH_REJECTED,
            "strategy_snapshot.evidence_refs",
            "macro snapshots require exactly one official primary release citation",
        )
    if official_primary[0].published_at != snapshot.event_published_at:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_snapshot.evidence_refs",
            "official macro release citation must bind the declared publication time",
        )


def _policy_threshold(policy: object, candidate_id: str, threshold_id: str) -> Decimal:
    try:
        raw = policy.threshold(candidate_id, threshold_id)  # type: ignore[attr-defined]
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            f"policy.threshold.{threshold_id}",
            str(error),
        )
    return _policy_decimal(raw, path=f"policy.threshold.{threshold_id}")


def _confirmation_feature_id(policy: object, candidate_id: str) -> str:
    candidate = _policy_candidate(policy, candidate_id)
    confirmation = candidate.get("confirmation")
    if not isinstance(confirmation, Mapping):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.candidate.confirmation",
            "confirmation policy must be an object",
        )
    return _identifier(
        confirmation.get("confirmation_feature_id"),
        path="policy.candidate.confirmation.confirmation_feature_id",
    )


def _numeric_feature(value: StrategyInput, feature_id: str) -> Decimal | None:
    feature = value.feature_by_id.get(feature_id)
    if (
        feature is None
        or feature.status is not FeatureStatus.PRESENT
        or not isinstance(feature.value, Decimal)
    ):
        return None
    return feature.value


def _reaction_relation(
    value: StrategyInput,
    reasoner_direction: Direction,
    policy: object,
) -> ReactionRelation:
    if reasoner_direction is Direction.UNCERTAIN:
        return ReactionRelation.NOT_APPLICABLE
    candidate_id = value.snapshot.candidate_id
    feature_id = _confirmation_feature_id(policy, candidate_id)
    confirmation = _numeric_feature(value, feature_id)
    if confirmation is None:
        return ReactionRelation.NONE
    if candidate_id == "EARNINGS_RESIDUAL_CONTINUATION_V1":
        minimum = _policy_threshold(policy, candidate_id, "opening_residual_epsilon")
        if abs(confirmation) < minimum:
            return ReactionRelation.NONE
    elif candidate_id == "MACRO_SPY_CONTINUATION_CHALLENGER_V1":
        minimum = _policy_threshold(policy, candidate_id, "event_zscore_min_abs")
        volume_minimum = _policy_threshold(policy, candidate_id, "event_volume_ratio_min")
        volume = _numeric_feature(value, "market.spy_event_volume_ratio_20.v1")
        vwap_distance = _numeric_feature(value, "market.spy_event_vwap_distance_bps.v1")
        if (
            volume is None
            or volume < volume_minimum
            or vwap_distance is None
            or abs(confirmation) < minimum
            or (confirmation > 0 and vwap_distance <= 0)
            or (confirmation < 0 and vwap_distance >= 0)
        ):
            return ReactionRelation.NONE
    else:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "strategy_input.candidate_id",
            "candidate has no registered confirmation rule",
        )
    same_sign = (reasoner_direction is Direction.UP and confirmation > 0) or (
        reasoner_direction is Direction.DOWN and confirmation < 0
    )
    return ReactionRelation.CONTINUE if same_sign else ReactionRelation.REVERSE


def _exchange_reason_code(status: ExchangeStatus) -> str:
    return {
        ExchangeStatus.TIMEOUT: "REASONER_TIMEOUT",
        ExchangeStatus.CANCELED: "REASONER_CANCELED",
        ExchangeStatus.PROVIDER_ERROR: "REASONER_PROVIDER_ERROR",
        ExchangeStatus.COMPLETED: "REASONER_SCHEMA_INVALID",
    }[status]


def _reasoner_exchange_policy_reasons(
    strategy_input: StrategyInput,
    exchange: ReasonerExchange,
    policy: object,
) -> set[str]:
    reasons: set[str] = set()
    snapshot = strategy_input.snapshot
    expected_route, expected_prompt, expected_schema = reasoner_policy_hashes(snapshot.candidate_id)
    if (
        exchange.route_sha256,
        exchange.prompt_sha256,
        exchange.output_schema_sha256,
    ) != (expected_route, expected_prompt, expected_schema):
        reasons.add("REASONER_POLICY_MISMATCH")
    expected_model_config = reasoner_model_config_sha256(
        provider=exchange.provider,
        model=exchange.model,
        model_revision=exchange.model_revision,
        decoding=exchange.decoding,
    )
    if exchange.model_config_sha256 != expected_model_config:
        reasons.add("REASONER_MODEL_CONFIG_MISMATCH")
    try:
        reasoner = policy.data["reasoner"]  # type: ignore[attr-defined]
        call_policy = reasoner["call_policy"]
        hard_timeout_seconds = call_policy["hard_timeout_seconds"]
        maximum_output_tokens = call_policy["max_output_tokens"]
        temperature = _policy_decimal(
            call_policy["temperature"],
            path="policy.reasoner.call_policy.temperature",
        )
    except (AttributeError, InvalidOperation, KeyError, TypeError) as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, "policy.reasoner", str(error))
    if temperature < 0:
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.reasoner.call_policy.temperature",
            "temperature must be finite and nonnegative",
        )
    if (
        type(hard_timeout_seconds) is not int
        or hard_timeout_seconds <= 0
        or type(maximum_output_tokens) is not int
        or maximum_output_tokens <= 0
    ):
        _reject(
            StrategyContractReason.POLICY_MISMATCH,
            "policy.reasoner.call_policy",
            "timeout and token limits must be positive integers",
        )
    if (
        exchange.decoding.temperature != temperature
        or exchange.decoding.max_output_tokens != maximum_output_tokens
    ):
        reasons.add("REASONER_POLICY_MISMATCH")
    expected_deadline = min(
        exchange.started_at + timedelta(seconds=hard_timeout_seconds),
        snapshot.decision_cutoff_at,
    )
    if (
        exchange.started_at < strategy_input.feature_receipt.created_at
        or exchange.started_at > snapshot.decision_cutoff_at
    ):
        reasons.add("CLOCK_MISMATCH")
    if exchange.deadline_at != expected_deadline:
        reasons.add("CLOCK_MISMATCH")
    if exchange.responded_at is not None and exchange.responded_at > exchange.deadline_at:
        reasons.add("LATE_RESPONSE")
    if exchange.created_at > snapshot.decision_cutoff_at:
        reasons.add("LATE_RESPONSE")
    return reasons


def validate_strategy_decision(
    strategy_input: StrategyInput,
    reasoner_exchange: ReasonerExchange,
    reasoner_decision: ReasonerDecision | None,
    *,
    validator_build_sha256: str,
    reasoner_error_code: str | None = None,
) -> StrategyDecision:
    """Apply deterministic gates and return direction or recorded ``UNCERTAIN``.

    All clocks and hashes are supplied artifacts.  The validator never reads a
    wall clock and cannot choose a security, package, quantity, price, risk, or
    execution action.
    """

    validator_hash = _sha256(validator_build_sha256, path="validator_build_sha256")
    snapshot = strategy_input.snapshot
    exchange = reasoner_exchange
    try:
        from ringdown_market.strategy.policy import load_strategy_policy

        policy = load_strategy_policy()
    except (ImportError, OSError, TypeError, ValueError) as error:
        _reject(StrategyContractReason.POLICY_MISMATCH, "policy", str(error))
    _validate_input_against_policy(strategy_input, policy)

    reasons: set[str] = set()
    if snapshot.eligibility is not EligibilityState.ELIGIBLE:
        reasons.add("EVENT_INELIGIBLE")
        reasons.update(snapshot.eligibility_reason_codes)
    if snapshot.data_health is not DataHealthState.VALID:
        reasons.add("DATA_HEALTH_INVALID")
        reasons.update(snapshot.health_reason_codes)
    lineage = {
        "event_id": snapshot.event_id,
        "candidate_id": snapshot.candidate_id,
        "policy_sha256": snapshot.policy_sha256,
        "strategy_snapshot_sha256": strategy_input.snapshot_sha256,
        "feature_receipt_sha256": strategy_input.feature_receipt_sha256,
        "evidence_packet_sha256": snapshot.evidence_packet_sha256,
    }
    for field, expected in lineage.items():
        if getattr(exchange, field) != expected:
            reasons.add("EXCHANGE_IDENTITY_MISMATCH")
    reasons.update(_reasoner_exchange_policy_reasons(strategy_input, exchange, policy))
    if exchange.status is not ExchangeStatus.COMPLETED:
        reasons.add(_exchange_reason_code(exchange.status))
        if exchange.error_code is not None:
            reasons.add(exchange.error_code)
    if reasoner_error_code is not None:
        reasons.add(_reason_code(reasoner_error_code, path="reasoner_error_code"))
    if reasoner_decision is None:
        reasons.add(_exchange_reason_code(exchange.status))

    decision_at = exchange.responded_at or exchange.created_at
    evidence_ids: tuple[str, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()
    unknowns: tuple[str, ...] = ()
    falsifier: Falsifier | None = None
    summary: str | None = None
    reasoner_direction: Direction | None = None
    semantic_sha256: str | None = None
    relation = ReactionRelation.NOT_APPLICABLE
    disposition = DecisionDisposition.REJECTED

    if reasoner_decision is not None:
        reasoner_direction = reasoner_decision.decision
        semantic_sha256 = reasoner_decision_sha256(reasoner_decision)
        evidence_ids = reasoner_decision.evidence_ids
        contradictions = reasoner_decision.contradictions
        unknowns = reasoner_decision.unknowns
        falsifier = reasoner_decision.strongest_falsifier
        summary = reasoner_decision.summary
        known = {item.evidence_id: item for item in snapshot.evidence_refs}
        referenced = set(evidence_ids)
        for contradiction in contradictions:
            referenced.update(contradiction.evidence_ids)
        if falsifier is not None:
            referenced.add(falsifier.evidence_id)
        if not referenced <= set(known):
            reasons.add("UNSUPPORTED_CITATION")
        if not set(unknowns) <= set(snapshot.allowed_unknown_codes):
            reasons.add("UNSUPPORTED_UNKNOWN_CODE")
        if set(unknowns) & set(snapshot.critical_unknown_codes):
            reasons.add("MATERIAL_UNKNOWN")
        if reasoner_direction is not Direction.UNCERTAIN:
            cited = [known[item] for item in evidence_ids if item in known]
            if not any(item.role.is_primary for item in cited):
                reasons.add("MISSING_PRIMARY_CITATION")
            if not any(item.role.is_market for item in cited):
                reasons.add("MISSING_MARKET_CITATION")
            if falsifier is None:
                reasons.add("MISSING_FALSIFIER")
        relation = _reaction_relation(strategy_input, reasoner_direction, policy)
        if reasoner_direction is Direction.UNCERTAIN:
            reasons.add("REASONER_UNCERTAIN")
            disposition = DecisionDisposition.ABSTAINED
        elif relation is ReactionRelation.REVERSE:
            reasons.add("CONFIRMATION_OPPOSED")
        elif relation is ReactionRelation.NONE:
            reasons.add("CONFIRMATION_NEUTRAL")

    final_direction = Direction.UNCERTAIN
    if reasoner_direction in {Direction.UP, Direction.DOWN} and not reasons:
        if relation is ReactionRelation.CONTINUE:
            final_direction = reasoner_direction
            disposition = DecisionDisposition.ACCEPTED
    elif disposition is DecisionDisposition.ABSTAINED:
        non_abstention_reasons = reasons - {"REASONER_UNCERTAIN"}
        if non_abstention_reasons:
            disposition = DecisionDisposition.REJECTED

    return StrategyDecision(
        event_id=snapshot.event_id,
        security_id=snapshot.security_id,
        candidate_id=snapshot.candidate_id,
        cohort_id=snapshot.cohort_id,
        policy_sha256=snapshot.policy_sha256,
        candidate_manifest_sha256=snapshot.candidate_manifest_sha256,
        strategy_snapshot_sha256=strategy_input.snapshot_sha256,
        feature_receipt_sha256=strategy_input.feature_receipt_sha256,
        reasoner_exchange_sha256=reasoner_exchange_sha256(exchange),
        reasoner_decision_sha256=semantic_sha256,
        producer_build_sha256=validator_hash,
        decision_at=decision_at,
        reasoner_direction=reasoner_direction,
        direction=final_direction,
        disposition=disposition,
        reaction_relation=relation,
        evidence_ids=evidence_ids,
        contradictions=contradictions,
        unknowns=unknowns,
        strongest_falsifier=falsifier,
        reason_codes=tuple(sorted(reasons)),
        summary=summary,
    )


def validate_reasoner_response(
    strategy_input: StrategyInput,
    reasoner_exchange: ReasonerExchange,
    raw_response_bytes: bytes | None,
    *,
    validator_build_sha256: str,
) -> StrategyDecision:
    """Validate an exact provider response and record every failure as abstention."""

    parsed: ReasonerDecision | None = None
    error_code: str | None = None
    if reasoner_exchange.status is ExchangeStatus.COMPLETED:
        if raw_response_bytes is None or type(raw_response_bytes) is not bytes:
            error_code = "REASONER_SCHEMA_INVALID"
        elif sha256_bytes(raw_response_bytes) != reasoner_exchange.raw_response_sha256:
            error_code = "REASONER_RAW_HASH_MISMATCH"
        else:
            try:
                parsed = parse_reasoner_decision(raw_response_bytes)
            except StrategyContractRejected:
                error_code = "REASONER_SCHEMA_INVALID"
    elif raw_response_bytes is not None:
        _require_bytes(raw_response_bytes, path="reasoner_response")
        if (
            reasoner_exchange.raw_response_sha256 is not None
            and sha256_bytes(raw_response_bytes) != reasoner_exchange.raw_response_sha256
        ):
            error_code = "REASONER_RAW_HASH_MISMATCH"
    return validate_strategy_decision(
        strategy_input,
        reasoner_exchange,
        parsed,
        validator_build_sha256=validator_build_sha256,
        reasoner_error_code=error_code,
    )
