"""Pure, fail-closed mapping from frozen research bytes to one PAPER permit."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import urlsplit

from esscher.alpha.models import Direction
from esscher.alpha.qfast import LatencyGateStatus, QFastStatus
from esscher.contracts.execution_policy import (
    ALPACA_MCP_PROTOCOL_SHA256,
    PAPER_PERMIT_MAXIMUM_LOSS,
    PAPER_PERMIT_POLICY_SHA256,
    PAPER_PERMIT_POLICY_VERSION,
    PAPER_PERMIT_TTL_SECONDS,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    paper_event_run_id,
)
from esscher.execution.models import (
    DataClass,
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    RunMode,
    VerticalType,
    debit_vertical_permit_id,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)

DECISION_SCHEMA = "ringdown.frozen_research_decision"
EVIDENCE_SCHEMA = "ringdown.point_in_time_evidence_manifest"
INPUT_SCHEMA = "ringdown.feature_input_snapshot"
SCHEMA_VERSION = 1
_REQUIRED_QUALIFIERS = ("INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class PermitRejectionReason(StrEnum):
    """Stable machine-readable reasons returned before any broker boundary exists."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    HASH_MISMATCH = "HASH_MISMATCH"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    POINT_IN_TIME_VIOLATION = "POINT_IN_TIME_VIOLATION"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    INELIGIBLE_DECISION = "INELIGIBLE_DECISION"
    QFAST_REJECTED = "QFAST_REJECTED"
    QLATENCY_REJECTED = "QLATENCY_REJECTED"
    CLAIM_BOUNDARY_MISMATCH = "CLAIM_BOUNDARY_MISMATCH"
    UNSUPPORTED_STRATEGY = "UNSUPPORTED_STRATEGY"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"


class DecisionPermitRejected(ValueError):
    """Deterministic fail-closed result for an invalid frozen decision."""

    def __init__(self, reason: PermitRejectionReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


class DecisionState(StrEnum):
    APPROVED = "APPROVED"
    ABSTAIN = "ABSTAIN"


class EligibilityState(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNRESOLVED = "UNRESOLVED"


class ResearchDataClass(StrEnum):
    POINT_IN_TIME_EVENT_PANEL = "POINT_IN_TIME_EVENT_PANEL"
    SYNTHETIC_CONTRACT_FIXTURE = "SYNTHETIC_CONTRACT_FIXTURE"


class EvidenceFieldStatus(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    REVISED = "REVISED"
    CONFLICTING = "CONFLICTING"


class EvidenceSourceKind(StrEnum):
    OFFICIAL_EVENT_RULES = "OFFICIAL_EVENT_RULES"
    ISSUER_PRIMARY = "ISSUER_PRIMARY"
    SEC_OFFICIAL = "SEC_OFFICIAL"
    ALPACA_OFFICIAL = "ALPACA_OFFICIAL"
    LICENSED_MARKET_DATA = "LICENSED_MARKET_DATA"


class PublishedAtType(StrEnum):
    ISSUER_RELEASE_TIMESTAMP = "issuer_release_timestamp"
    OFFICIAL_DISSEMINATION_TIMESTAMP = "official_dissemination_timestamp"


class DependencyCheck(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class FrozenLeg:
    symbol: str
    option_type: OptionType
    strike: Decimal


@dataclass(frozen=True, slots=True)
class FrozenDebitVertical:
    underlying: str
    vertical_type: VerticalType
    expiry: date
    quantity: int
    limit_price: Decimal
    long_leg: FrozenLeg
    short_leg: FrozenLeg


@dataclass(frozen=True, slots=True)
class FrozenResearchDecision:
    event_id: str
    issuer: str
    decision_cutoff: datetime
    latest_evidence_at: datetime
    feature_snapshot_at: datetime
    frozen_at: datetime
    decision_state: DecisionState
    direction: Direction
    eligibility: EligibilityState
    qfast_status: QFastStatus
    qlatency_status: LatencyGateStatus
    evidence_manifest_sha256: str
    input_snapshot_sha256: str
    protocol_sha256: str
    policy_version: str
    policy_sha256: str
    strategy: FrozenDebitVertical


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    published_at: datetime


class _DuplicateFieldError(ValueError):
    pass


def _reject(reason: PermitRejectionReason, path: str, detail: str) -> None:
    raise DecisionPermitRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _require_immutable_bytes(value: object, *, label: str) -> bytes:
    if type(value) is not bytes:
        _reject(
            PermitRejectionReason.INVALID_DOCUMENT,
            label,
            "contract inputs must be immutable bytes",
        )
    return value  # type: ignore[return-value]


def _decode_document(raw: bytes, *, label: str) -> Mapping[str, object]:
    _require_immutable_bytes(raw, label=label)
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=Decimal,
            parse_constant=_invalid_constant,
        )
    except _DuplicateFieldError as error:
        _reject(PermitRejectionReason.DUPLICATE_FIELD, label, f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, label, str(error))
    if not isinstance(value, Mapping):
        _reject(PermitRejectionReason.INVALID_DOCUMENT, label, "root must be an object")
    return value


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(PermitRejectionReason.MISSING_FIELD, path, f"missing {missing[0]}")
    unknown = sorted(keys - fields)
    if unknown:
        _reject(PermitRejectionReason.UNKNOWN_FIELD, path, f"unknown {unknown[0]}")
    return value


def _nonempty_text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be non-empty normalized text")
    return value


def _integer(value: object, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be an integer")
    return value


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be a lowercase SHA-256")
    return value


def _timestamp(value: object, *, path: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, str(error))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be UTC")
    return parsed.astimezone(UTC)


def _date(value: object, *, path: str) -> date:
    if not isinstance(value, str):
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, str(error))
    if parsed.isoformat() != value:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be a normalized ISO date")
    return parsed


def _money(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be decimal text with cents")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, str(error))
    if not parsed.is_finite() or parsed <= 0:
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be finite and positive")
    return parsed


def _enum(enum_type: type[StrEnum], value: object, *, path: str) -> StrEnum:
    if not isinstance(value, str):
        _reject(PermitRejectionReason.UNKNOWN_STATE, path, "state must be text")
    try:
        return enum_type(value)
    except ValueError:
        _reject(PermitRejectionReason.UNKNOWN_STATE, path, f"unknown state {value}")


def _qualifiers(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be a list of strings")
    qualifiers = tuple(value)
    if qualifiers != tuple(sorted(set(qualifiers))):
        _reject(PermitRejectionReason.INVALID_DOCUMENT, path, "must be sorted and unique")
    return qualifiers


def _parse_leg(value: object, *, path: str) -> FrozenLeg:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset({"symbol", "option_type", "strike"}),
    )
    option_type = _enum(OptionType, payload["option_type"], path=f"{path}.option_type")
    return FrozenLeg(
        symbol=_nonempty_text(payload["symbol"], path=f"{path}.symbol"),
        option_type=option_type,  # type: ignore[arg-type]
        strike=_money(payload["strike"], path=f"{path}.strike"),
    )


def _parse_strategy(value: object) -> FrozenDebitVertical:
    payload = _strict_object(
        value,
        path="decision.strategy",
        fields=frozenset(
            {
                "kind",
                "underlying",
                "vertical_type",
                "expiry",
                "quantity",
                "limit_price",
                "long_leg",
                "short_leg",
            }
        ),
    )
    if payload["kind"] != "DEBIT_VERTICAL":
        _reject(
            PermitRejectionReason.UNSUPPORTED_STRATEGY,
            "decision.strategy.kind",
            "only DEBIT_VERTICAL is permitted",
        )
    vertical_type = _enum(
        VerticalType,
        payload["vertical_type"],
        path="decision.strategy.vertical_type",
    )
    return FrozenDebitVertical(
        underlying=_nonempty_text(payload["underlying"], path="decision.strategy.underlying"),
        vertical_type=vertical_type,  # type: ignore[arg-type]
        expiry=_date(payload["expiry"], path="decision.strategy.expiry"),
        quantity=_integer(payload["quantity"], path="decision.strategy.quantity"),
        limit_price=_money(payload["limit_price"], path="decision.strategy.limit_price"),
        long_leg=_parse_leg(payload["long_leg"], path="decision.strategy.long_leg"),
        short_leg=_parse_leg(payload["short_leg"], path="decision.strategy.short_leg"),
    )


def _parse_decision(raw: bytes) -> FrozenResearchDecision:
    payload = _strict_object(
        _decode_document(raw, label="decision"),
        path="decision",
        fields=frozenset(
            {
                "schema",
                "schema_version",
                "event_id",
                "issuer",
                "decision_cutoff",
                "latest_evidence_at",
                "feature_snapshot_at",
                "frozen_at",
                "decision_state",
                "direction",
                "eligibility",
                "qfast_status",
                "qlatency_status",
                "claim",
                "data_class",
                "data_qualifiers",
                "evidence_manifest_sha256",
                "input_snapshot_sha256",
                "protocol_sha256",
                "policy_version",
                "policy_sha256",
                "strategy",
            }
        ),
    )
    if payload["schema"] != DECISION_SCHEMA or payload["schema_version"] != SCHEMA_VERSION:
        _reject(
            PermitRejectionReason.UNSUPPORTED_SCHEMA,
            "decision",
            "unsupported decision schema or version",
        )
    if payload["claim"] != "NOT_ALPHA_EVIDENCE":
        _reject(
            PermitRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "decision.claim",
            "decision must retain NOT_ALPHA_EVIDENCE",
        )
    data_class = _enum(
        ResearchDataClass,
        payload["data_class"],
        path="decision.data_class",
    )
    if data_class is not ResearchDataClass.POINT_IN_TIME_EVENT_PANEL:
        _reject(
            PermitRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "decision.data_class",
            "only POINT_IN_TIME_EVENT_PANEL may reach a permit",
        )
    if _qualifiers(payload["data_qualifiers"], path="decision.data_qualifiers") != (
        _REQUIRED_QUALIFIERS
    ):
        _reject(
            PermitRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "decision.data_qualifiers",
            "INDICATIVE_DATA and NOT_ALPHA_EVIDENCE are required",
        )

    decision_cutoff = _timestamp(payload["decision_cutoff"], path="decision.decision_cutoff")
    latest_evidence_at = _timestamp(
        payload["latest_evidence_at"], path="decision.latest_evidence_at"
    )
    feature_snapshot_at = _timestamp(
        payload["feature_snapshot_at"], path="decision.feature_snapshot_at"
    )
    frozen_at = _timestamp(payload["frozen_at"], path="decision.frozen_at")
    assert decision_cutoff is not None
    assert latest_evidence_at is not None
    assert feature_snapshot_at is not None
    assert frozen_at is not None

    return FrozenResearchDecision(
        event_id=_nonempty_text(payload["event_id"], path="decision.event_id"),
        issuer=_nonempty_text(payload["issuer"], path="decision.issuer"),
        decision_cutoff=decision_cutoff,
        latest_evidence_at=latest_evidence_at,
        feature_snapshot_at=feature_snapshot_at,
        frozen_at=frozen_at,
        decision_state=_enum(
            DecisionState,
            payload["decision_state"],
            path="decision.decision_state",
        ),  # type: ignore[arg-type]
        direction=_enum(Direction, payload["direction"], path="decision.direction"),  # type: ignore[arg-type]
        eligibility=_enum(
            EligibilityState,
            payload["eligibility"],
            path="decision.eligibility",
        ),  # type: ignore[arg-type]
        qfast_status=_enum(
            QFastStatus,
            payload["qfast_status"],
            path="decision.qfast_status",
        ),  # type: ignore[arg-type]
        qlatency_status=_enum(
            LatencyGateStatus,
            payload["qlatency_status"],
            path="decision.qlatency_status",
        ),  # type: ignore[arg-type]
        evidence_manifest_sha256=_sha256(
            payload["evidence_manifest_sha256"],
            path="decision.evidence_manifest_sha256",
        ),
        input_snapshot_sha256=_sha256(
            payload["input_snapshot_sha256"],
            path="decision.input_snapshot_sha256",
        ),
        protocol_sha256=_sha256(payload["protocol_sha256"], path="decision.protocol_sha256"),
        policy_version=_nonempty_text(payload["policy_version"], path="decision.policy_version"),
        policy_sha256=_sha256(payload["policy_sha256"], path="decision.policy_sha256"),
        strategy=_parse_strategy(payload["strategy"]),
    )


def _same_identity(
    payload: Mapping[str, object],
    *,
    decision: FrozenResearchDecision,
    path: str,
) -> None:
    comparisons = {
        "event_id": decision.event_id,
        "issuer": decision.issuer,
        "decision_cutoff": decision.decision_cutoff,
        "feature_snapshot_at": decision.feature_snapshot_at,
        "frozen_at": decision.frozen_at,
    }
    for field, expected in comparisons.items():
        raw = payload[field]
        actual: object
        if isinstance(expected, datetime):
            actual = _timestamp(raw, path=f"{path}.{field}")
        else:
            actual = _nonempty_text(raw, path=f"{path}.{field}")
        if actual != expected:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.{field}",
                "does not match the frozen decision",
            )


def _parse_evidence_manifest(
    raw: bytes,
    *,
    decision: FrozenResearchDecision,
) -> dict[str, EvidenceRecord]:
    payload = _strict_object(
        _decode_document(raw, label="evidence_manifest"),
        path="evidence_manifest",
        fields=frozenset(
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
                "field_source_refs",
                "records",
            }
        ),
    )
    if payload["schema"] != EVIDENCE_SCHEMA or payload["schema_version"] != SCHEMA_VERSION:
        _reject(
            PermitRejectionReason.UNSUPPORTED_SCHEMA,
            "evidence_manifest",
            "unsupported evidence schema or version",
        )
    if _timestamp(payload["frozen_at"], path="evidence_manifest.frozen_at") != (decision.frozen_at):
        _reject(
            PermitRejectionReason.STALE_EVIDENCE,
            "evidence_manifest.frozen_at",
            "manifest is not the decision's frozen evidence version",
        )
    _same_identity(payload, decision=decision, path="evidence_manifest")
    manifest_latest = _timestamp(
        payload["latest_evidence_at"], path="evidence_manifest.latest_evidence_at"
    )
    if manifest_latest != decision.latest_evidence_at:
        _reject(
            PermitRejectionReason.STALE_EVIDENCE,
            "evidence_manifest.latest_evidence_at",
            "manifest is not the decision's frozen evidence version",
        )
    if payload["data_class"] != ResearchDataClass.POINT_IN_TIME_EVENT_PANEL.value:
        _reject(
            PermitRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "evidence_manifest.data_class",
            "historical point-in-time provenance is required",
        )
    if (
        _qualifiers(payload["data_qualifiers"], path="evidence_manifest.data_qualifiers")
        != _REQUIRED_QUALIFIERS
    ):
        _reject(
            PermitRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            "evidence_manifest.data_qualifiers",
            "required qualifiers do not match",
        )

    records_value = payload["records"]
    if not isinstance(records_value, list) or not records_value:
        _reject(
            PermitRejectionReason.MISSING_EVIDENCE,
            "evidence_manifest.records",
            "at least one frozen evidence record is required",
        )
    record_fields = frozenset(
        {
            "evidence_id",
            "event_id",
            "issuer",
            "source_kind",
            "source_url",
            "publisher",
            "published_at",
            "published_at_type",
            "published_at_precision",
            "published_date_or_interval",
            "source_timezone",
            "accepted_at",
            "retrieved_at",
            "source_observed_at",
            "decision_cutoff",
            "feature_snapshot_at",
            "content_sha256",
            "hash_representation",
            "data_class",
            "data_qualifiers",
            "entitlement_note",
            "redistribution_note",
            "field_status",
        }
    )
    records: dict[str, EvidenceRecord] = {}
    for index, item in enumerate(records_value):
        path = f"evidence_manifest.records[{index}]"
        record = _strict_object(item, path=path, fields=record_fields)
        evidence_id = _nonempty_text(record["evidence_id"], path=f"{path}.evidence_id")
        if evidence_id in records:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.evidence_id",
                "evidence IDs must be unique",
            )
        if record["event_id"] != decision.event_id or record["issuer"] != decision.issuer:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                path,
                "record event identity does not match the decision",
            )
        _enum(EvidenceSourceKind, record["source_kind"], path=f"{path}.source_kind")
        source_url = _nonempty_text(record["source_url"], path=f"{path}.source_url")
        if urlsplit(source_url).scheme not in {"http", "https"}:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.source_url",
                "source URL must be public HTTP(S)",
            )
        _nonempty_text(record["publisher"], path=f"{path}.publisher")
        published_at = _timestamp(
            record["published_at"],
            path=f"{path}.published_at",
            nullable=True,
        )
        if published_at is None:
            _reject(
                PermitRejectionReason.MISSING_EVIDENCE,
                f"{path}.published_at",
                "an exact publication instant is required",
            )
        if published_at > decision.decision_cutoff:
            _reject(
                PermitRejectionReason.POINT_IN_TIME_VIOLATION,
                f"{path}.published_at",
                "evidence was published after decision_cutoff",
            )
        _enum(
            PublishedAtType,
            record["published_at_type"],
            path=f"{path}.published_at_type",
        )
        if record["published_at_precision"] not in {"second", "minute"}:
            _reject(
                PermitRejectionReason.MISSING_EVIDENCE,
                f"{path}.published_at_precision",
                "date-only or unknown evidence cannot support a permit",
            )
        if record["published_date_or_interval"] is not None:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.published_date_or_interval",
                "exact-time evidence cannot also use a date-only fallback",
            )
        _nonempty_text(record["source_timezone"], path=f"{path}.source_timezone")
        accepted_at = _timestamp(
            record["accepted_at"],
            path=f"{path}.accepted_at",
            nullable=True,
        )
        retrieved_at = _timestamp(record["retrieved_at"], path=f"{path}.retrieved_at")
        source_observed_at = _timestamp(
            record["source_observed_at"],
            path=f"{path}.source_observed_at",
        )
        for field, timestamp in (
            ("accepted_at", accepted_at),
            ("retrieved_at", retrieved_at),
            ("source_observed_at", source_observed_at),
        ):
            if timestamp is not None and timestamp > decision.decision_cutoff:
                _reject(
                    PermitRejectionReason.POINT_IN_TIME_VIOLATION,
                    f"{path}.{field}",
                    "evidence availability or collection was after decision_cutoff",
                )
        if _timestamp(record["decision_cutoff"], path=f"{path}.decision_cutoff") != (
            decision.decision_cutoff
        ):
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.decision_cutoff",
                "record cutoff does not match the decision",
            )
        if (
            _timestamp(record["feature_snapshot_at"], path=f"{path}.feature_snapshot_at")
            != decision.feature_snapshot_at
        ):
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.feature_snapshot_at",
                "record snapshot time does not match the decision",
            )
        _sha256(record["content_sha256"], path=f"{path}.content_sha256")
        if record["hash_representation"] != "raw_bytes":
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.hash_representation",
                "v1 accepts raw_bytes only; there is no mutable canonicalization fallback",
            )
        if record["data_class"] != ResearchDataClass.POINT_IN_TIME_EVENT_PANEL.value:
            _reject(
                PermitRejectionReason.CLAIM_BOUNDARY_MISMATCH,
                f"{path}.data_class",
                "record is not historical point-in-time evidence",
            )
        if _qualifiers(record["data_qualifiers"], path=f"{path}.data_qualifiers") != (
            _REQUIRED_QUALIFIERS
        ):
            _reject(
                PermitRejectionReason.CLAIM_BOUNDARY_MISMATCH,
                f"{path}.data_qualifiers",
                "record qualifiers do not match the decision",
            )
        _nonempty_text(record["entitlement_note"], path=f"{path}.entitlement_note")
        if record["redistribution_note"] not in {
            "PUBLIC_BYTES_ALLOWED",
            "METADATA_AND_HASH_ONLY",
        }:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.redistribution_note",
                "source bytes are unavailable or not permitted",
            )
        field_status = _enum(
            EvidenceFieldStatus,
            record["field_status"],
            path=f"{path}.field_status",
        )
        if field_status is not EvidenceFieldStatus.PRESENT:
            _reject(
                PermitRejectionReason.MISSING_EVIDENCE,
                f"{path}.field_status",
                "missing, revised, or conflicting evidence is ineligible",
            )
        records[evidence_id] = EvidenceRecord(evidence_id, published_at)

    if max(item.published_at for item in records.values()) != decision.latest_evidence_at:
        _reject(
            PermitRejectionReason.PROVENANCE_MISMATCH,
            "evidence_manifest.latest_evidence_at",
            "latest_evidence_at is not derived from the frozen records",
        )

    refs_value = payload["field_source_refs"]
    if not isinstance(refs_value, Mapping) or not refs_value:
        _reject(
            PermitRejectionReason.MISSING_EVIDENCE,
            "evidence_manifest.field_source_refs",
            "material fields require source references",
        )
    for field, refs in refs_value.items():
        if not isinstance(field, str) or not field:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                "evidence_manifest.field_source_refs",
                "material field names must be non-empty",
            )
        _validate_refs(refs, records=records, path=f"evidence_manifest.field_source_refs.{field}")
    return records


def _validate_refs(
    value: object,
    *,
    records: Mapping[str, EvidenceRecord],
    path: str,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        _reject(PermitRejectionReason.MISSING_EVIDENCE, path, "source_refs must be non-empty")
    refs = tuple(value)
    if refs != tuple(sorted(set(refs))):
        _reject(PermitRejectionReason.PROVENANCE_MISMATCH, path, "source_refs must be sorted")
    unknown = sorted(set(refs) - set(records))
    if unknown:
        _reject(
            PermitRejectionReason.MISSING_EVIDENCE,
            path,
            f"unknown evidence reference {unknown[0]}",
        )
    return refs


def _parse_input_snapshot(
    raw: bytes,
    *,
    decision: FrozenResearchDecision,
    records: Mapping[str, EvidenceRecord],
) -> None:
    payload = _strict_object(
        _decode_document(raw, label="input_snapshot"),
        path="input_snapshot",
        fields=frozenset(
            {
                "schema",
                "schema_version",
                "event_id",
                "issuer",
                "decision_cutoff",
                "feature_snapshot_at",
                "frozen_at",
                "features",
            }
        ),
    )
    if payload["schema"] != INPUT_SCHEMA or payload["schema_version"] != SCHEMA_VERSION:
        _reject(
            PermitRejectionReason.UNSUPPORTED_SCHEMA,
            "input_snapshot",
            "unsupported input schema or version",
        )
    _same_identity(payload, decision=decision, path="input_snapshot")
    features = payload["features"]
    if not isinstance(features, list) or not features:
        _reject(
            PermitRejectionReason.MISSING_EVIDENCE,
            "input_snapshot.features",
            "at least one dependency-closed feature is required",
        )
    feature_fields = frozenset(
        {
            "feature_id",
            "source_refs",
            "source_max_public_at",
            "feature_computed_at",
            "definition_version",
            "field_status",
            "dependency_check",
            "value_sha256",
        }
    )
    feature_ids: set[str] = set()
    for index, item in enumerate(features):
        path = f"input_snapshot.features[{index}]"
        feature = _strict_object(item, path=path, fields=feature_fields)
        feature_id = _nonempty_text(feature["feature_id"], path=f"{path}.feature_id")
        if feature_id in feature_ids:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.feature_id",
                "feature IDs must be unique",
            )
        feature_ids.add(feature_id)
        refs = _validate_refs(feature["source_refs"], records=records, path=f"{path}.source_refs")
        source_max = _timestamp(
            feature["source_max_public_at"], path=f"{path}.source_max_public_at"
        )
        assert source_max is not None
        expected_max = max(records[ref].published_at for ref in refs)
        if source_max != expected_max:
            _reject(
                PermitRejectionReason.PROVENANCE_MISMATCH,
                f"{path}.source_max_public_at",
                "source maximum does not match referenced evidence",
            )
        if source_max > decision.decision_cutoff:
            _reject(
                PermitRejectionReason.POINT_IN_TIME_VIOLATION,
                f"{path}.source_max_public_at",
                "feature depends on post-cutoff evidence",
            )
        feature_computed = _timestamp(
            feature["feature_computed_at"], path=f"{path}.feature_computed_at"
        )
        assert feature_computed is not None
        if feature_computed > decision.feature_snapshot_at:
            _reject(
                PermitRejectionReason.POINT_IN_TIME_VIOLATION,
                f"{path}.feature_computed_at",
                "feature was computed after the frozen snapshot",
            )
        _nonempty_text(feature["definition_version"], path=f"{path}.definition_version")
        field_status = _enum(
            EvidenceFieldStatus,
            feature["field_status"],
            path=f"{path}.field_status",
        )
        if field_status is not EvidenceFieldStatus.PRESENT:
            _reject(
                PermitRejectionReason.MISSING_EVIDENCE,
                f"{path}.field_status",
                "feature is not present and frozen",
            )
        dependency = _enum(
            DependencyCheck,
            feature["dependency_check"],
            path=f"{path}.dependency_check",
        )
        if dependency is not DependencyCheck.ELIGIBLE:
            _reject(
                PermitRejectionReason.INELIGIBLE_DECISION,
                f"{path}.dependency_check",
                "feature dependency gate did not pass",
            )
        _sha256(feature["value_sha256"], path=f"{path}.value_sha256")


def _validate_decision_gate(decision: FrozenResearchDecision) -> None:
    if decision.latest_evidence_at > decision.decision_cutoff:
        _reject(
            PermitRejectionReason.POINT_IN_TIME_VIOLATION,
            "decision.latest_evidence_at",
            "evidence was published after decision_cutoff",
        )
    if decision.feature_snapshot_at > decision.decision_cutoff:
        _reject(
            PermitRejectionReason.POINT_IN_TIME_VIOLATION,
            "decision.feature_snapshot_at",
            "feature snapshot was frozen after decision_cutoff",
        )
    if decision.frozen_at != decision.decision_cutoff:
        _reject(
            PermitRejectionReason.STALE_EVIDENCE,
            "decision.frozen_at",
            "v1 permits only the exact decision-cutoff freeze",
        )
    if (
        decision.decision_state is not DecisionState.APPROVED
        or decision.direction is Direction.UNCERTAIN
    ):
        _reject(
            PermitRejectionReason.INELIGIBLE_DECISION,
            "decision.decision_state",
            "ABSTAIN or UNCERTAIN cannot produce a permit",
        )
    if decision.eligibility is not EligibilityState.ELIGIBLE:
        _reject(
            PermitRejectionReason.INELIGIBLE_DECISION,
            "decision.eligibility",
            "only ELIGIBLE decisions may produce a permit",
        )
    if decision.qfast_status is not QFastStatus.NOT_REJECTED_SMALL_SAMPLE:
        _reject(
            PermitRejectionReason.QFAST_REJECTED,
            "decision.qfast_status",
            "Q-FAST did not admit the frozen decision",
        )
    if decision.qlatency_status is not LatencyGateStatus.NOT_REJECTED_SMALL_SAMPLE:
        _reject(
            PermitRejectionReason.QLATENCY_REJECTED,
            "decision.qlatency_status",
            "Q-LATENCY did not admit the frozen decision",
        )


def _validate_strategy(decision: FrozenResearchDecision) -> None:
    strategy = decision.strategy
    if strategy.underlying != strategy.underlying.strip().upper():
        _reject(
            PermitRejectionReason.UNSUPPORTED_STRATEGY,
            "decision.strategy.underlying",
            "underlying must be normalized uppercase",
        )
    expected = (
        (VerticalType.BULL_CALL, OptionType.CALL)
        if decision.direction is Direction.UP
        else (VerticalType.BEAR_PUT, OptionType.PUT)
    )
    if strategy.vertical_type is not expected[0]:
        _reject(
            PermitRejectionReason.UNSUPPORTED_STRATEGY,
            "decision.strategy.vertical_type",
            "vertical type would change the frozen direction",
        )
    if (
        strategy.long_leg.option_type is not expected[1]
        or strategy.short_leg.option_type is not (expected[1])
    ):
        _reject(
            PermitRejectionReason.UNSUPPORTED_STRATEGY,
            "decision.strategy",
            "option type would change the frozen direction",
        )
    if strategy.quantity != 1:
        _reject(
            PermitRejectionReason.UNSUPPORTED_STRATEGY,
            "decision.strategy.quantity",
            "v1 permits exactly one spread package",
        )
    if strategy.limit_price * Decimal(100) > PAPER_PERMIT_MAXIMUM_LOSS:
        _reject(
            PermitRejectionReason.RISK_LIMIT_EXCEEDED,
            "decision.strategy.limit_price",
            "maximum opening debit exceeds the frozen paper policy",
        )


def map_frozen_decision_to_permit(
    decision_bytes: bytes,
    *,
    evidence_manifest_bytes: bytes,
    input_snapshot_bytes: bytes,
    policy_version: str,
) -> DebitVerticalPermit:
    """Validate exact frozen artifacts and deterministically issue one PAPER permit.

    This pure function has no session, market-data, credential, position, or order dependency.
    The returned permit remains inert until the separate official Alpaca MCP boundary receives it.
    """

    decision_bytes = _require_immutable_bytes(decision_bytes, label="decision")
    evidence_manifest_bytes = _require_immutable_bytes(
        evidence_manifest_bytes,
        label="evidence_manifest",
    )
    input_snapshot_bytes = _require_immutable_bytes(
        input_snapshot_bytes,
        label="input_snapshot",
    )
    decision = _parse_decision(decision_bytes)
    if policy_version != PAPER_PERMIT_POLICY_VERSION or decision.policy_version != policy_version:
        _reject(
            PermitRejectionReason.POLICY_MISMATCH,
            "decision.policy_version",
            "unknown or mismatched policy version",
        )
    if decision.policy_sha256 != PAPER_PERMIT_POLICY_SHA256:
        _reject(
            PermitRejectionReason.POLICY_MISMATCH,
            "decision.policy_sha256",
            "policy hash does not match the immutable registry entry",
        )
    if decision.protocol_sha256 != RESEARCH_DECISION_PROTOCOL_SHA256:
        _reject(
            PermitRejectionReason.PROTOCOL_MISMATCH,
            "decision.protocol_sha256",
            "protocol hash does not match the frozen research decision protocol",
        )

    evidence_sha256 = _sha256_bytes(evidence_manifest_bytes)
    input_sha256 = _sha256_bytes(input_snapshot_bytes)
    if evidence_sha256 != decision.evidence_manifest_sha256:
        _reject(
            PermitRejectionReason.HASH_MISMATCH,
            "evidence_manifest",
            "supplied bytes do not match evidence_manifest_sha256",
        )
    if input_sha256 != decision.input_snapshot_sha256:
        _reject(
            PermitRejectionReason.HASH_MISMATCH,
            "input_snapshot",
            "supplied bytes do not match input_snapshot_sha256",
        )

    _validate_decision_gate(decision)
    records = _parse_evidence_manifest(evidence_manifest_bytes, decision=decision)
    _parse_input_snapshot(
        input_snapshot_bytes,
        decision=decision,
        records=records,
    )
    _validate_strategy(decision)

    decision_sha256 = _sha256_bytes(decision_bytes)
    event_run_id = paper_event_run_id(decision_sha256)
    strategy = decision.strategy
    try:
        long_leg = OptionLeg(
            symbol=strategy.long_leg.symbol,
            underlying=strategy.underlying,
            expiry=strategy.expiry,
            option_type=strategy.long_leg.option_type,
            strike=strategy.long_leg.strike,
            side=OptionSide.BUY,
            position_intent=PositionIntent.BUY_TO_OPEN,
        )
        short_leg = OptionLeg(
            symbol=strategy.short_leg.symbol,
            underlying=strategy.underlying,
            expiry=strategy.expiry,
            option_type=strategy.short_leg.option_type,
            strike=strategy.short_leg.strike,
            side=OptionSide.SELL,
            position_intent=PositionIntent.SELL_TO_OPEN,
        )
        candidate = DebitVerticalPermit._from_frozen_decision(
            permit_id="UNBOUND",
            event_run_id=event_run_id,
            decision_sha256=decision_sha256,
            evidence_sha256=evidence_sha256,
            snapshot_sha256=input_sha256,
            protocol_sha256=RESEARCH_DECISION_PROTOCOL_SHA256,
            execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
            policy_sha256=PAPER_PERMIT_POLICY_SHA256,
            issued_at=decision.frozen_at,
            expires_at=decision.frozen_at + timedelta(seconds=PAPER_PERMIT_TTL_SECONDS),
            vertical_type=strategy.vertical_type,
            quantity=strategy.quantity,
            limit_price=strategy.limit_price,
            legs=(long_leg, short_leg),
            run_mode=RunMode.PAPER,
            data_class=DataClass.INDICATIVE_DATA,
        )
        return replace(candidate, permit_id=debit_vertical_permit_id(candidate))
    except ValueError as error:
        _reject(
            PermitRejectionReason.UNSUPPORTED_STRATEGY,
            "decision.strategy",
            str(error),
        )
