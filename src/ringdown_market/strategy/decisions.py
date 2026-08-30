"""Immutable Esscher strategy decision contract with deterministic serialization."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from ringdown_market.alpha.models import Direction

STRATEGY_DECISION_SCHEMA = "esscher.strategy_decision"
STRATEGY_DECISION_SCHEMA_VERSION = 1
TRACE_STAGES = ("INPUT", "FEATURE", "REASONER", "VALIDATOR", "OUTPUT")
REQUIRED_CLAIM = "NOT_ALPHA_EVIDENCE"
REQUIRED_QUALIFIERS = ("INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE")
REQUIRED_POLICY_VERSION = "esscher-strategy-v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RESIDUAL = re.compile(r"^(?:0|-?[1-9][0-9]*)(?:\.[0-9]{1,6})?$")

FORBIDDEN_EXECUTION_FIELDS = frozenset(
    {
        "account",
        "account_id",
        "client_order_id",
        "expiry",
        "legs",
        "limit_price",
        "long_leg",
        "order",
        "order_id",
        "permit",
        "permit_id",
        "quantity",
        "short_leg",
        "strategy",
        "strike",
        "symbol",
        "vertical_type",
    }
)


class DecisionRejectionReason(StrEnum):
    """Stable fail-closed reasons a strategy decision document cannot be accepted."""

    DUPLICATE_KEY = "DUPLICATE_KEY"
    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_VALUE = "INVALID_VALUE"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    EXECUTION_FIELD_FORBIDDEN = "EXECUTION_FIELD_FORBIDDEN"
    CUTOFF_VIOLATION = "CUTOFF_VIOLATION"
    DEADLINE_VIOLATION = "DEADLINE_VIOLATION"
    INVALID_ABSTENTION = "INVALID_ABSTENTION"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"


class StrategyDecisionRejected(ValueError):
    """Raised when strategy decision bytes or values fail the frozen contract."""

    def __init__(self, reason: DecisionRejectionReason, path: str, detail: str) -> None:
        super().__init__(f"{reason.value} at {path}: {detail}")
        self.reason = reason
        self.path = path
        self.detail = detail


class StrategyDecisionState(StrEnum):
    """Terminal validation state of one strategy decision."""

    APPROVED = "APPROVED"
    ABSTAIN = "ABSTAIN"


class ReactionRelation(StrEnum):
    """Code-derived relation between accepted direction and the opening residual."""

    CONTINUE = "CONTINUE"
    REVERSE = "REVERSE"
    NONE = "NONE"


class AbstentionReason(StrEnum):
    """Stable abstention codes; identical to the frozen policy abstention rules."""

    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    STALE_INPUT = "STALE_INPUT"
    CUTOFF_VIOLATION = "CUTOFF_VIOLATION"
    INELIGIBLE_UNIVERSE = "INELIGIBLE_UNIVERSE"
    INELIGIBLE_TIMING = "INELIGIBLE_TIMING"
    LATE_REASONER_OUTPUT = "LATE_REASONER_OUTPUT"
    INVALID_REASONER_OUTPUT = "INVALID_REASONER_OUTPUT"
    UNBOUNDED_FALSIFIER = "UNBOUNDED_FALSIFIER"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
    SNAPSHOT_HASH_MISMATCH = "SNAPSHOT_HASH_MISMATCH"
    ROUTE_MISMATCH = "ROUTE_MISMATCH"
    DUPLICATE_DECISION = "DUPLICATE_DECISION"
    NO_FALLBACK = "NO_FALLBACK"


def _reject(reason: DecisionRejectionReason, path: str, detail: str) -> None:
    raise StrategyDecisionRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            _reject(DecisionRejectionReason.DUPLICATE_KEY, key, "duplicate JSON key")
        payload[key] = value
    return payload


def _invalid_constant(value: str) -> None:
    _reject(DecisionRejectionReason.NON_FINITE_VALUE, value, "non-finite JSON constant")


def _decode_document(raw: bytes, *, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except UnicodeDecodeError as error:
        _reject(DecisionRejectionReason.INVALID_DOCUMENT, label, f"not valid UTF-8: {error}")
    except json.JSONDecodeError as error:
        _reject(DecisionRejectionReason.INVALID_DOCUMENT, label, f"invalid JSON: {error.msg}")
    if not isinstance(payload, dict):
        _reject(DecisionRejectionReason.INVALID_TYPE, label, "top-level document must be an object")
    return payload


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyDecisionRejected(
            DecisionRejectionReason.INVALID_TYPE, "timestamp", "timestamp must be timezone-aware"
        )
    normalized = value.astimezone(UTC)
    if normalized.microsecond != 0:
        raise StrategyDecisionRejected(
            DecisionRejectionReason.INVALID_VALUE,
            "timestamp",
            "timestamps use second precision only",
        )
    return normalized.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    """One accepted claim bound to one input evidence record."""

    citation_id: str
    evidence_id: str
    claim_sha256: str

    def __post_init__(self) -> None:
        for field in ("citation_id", "evidence_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                _reject(
                    DecisionRejectionReason.INVALID_TYPE,
                    f"citation.{field}",
                    "expected non-empty text",
                )
        if not isinstance(self.claim_sha256, str) or not _SHA256.match(self.claim_sha256):
            _reject(
                DecisionRejectionReason.INVALID_TYPE,
                "citation.claim_sha256",
                "expected a sha256 hex digest",
            )


@dataclass(frozen=True, slots=True)
class Falsifier:
    """The strongest falsifier considered for the decision."""

    falsifier_id: str
    evidence_id: str
    claim_sha256: str

    def __post_init__(self) -> None:
        for field in ("falsifier_id", "evidence_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                _reject(
                    DecisionRejectionReason.INVALID_TYPE,
                    f"falsifier.{field}",
                    "expected non-empty text",
                )
        if not isinstance(self.claim_sha256, str) or not _SHA256.match(self.claim_sha256):
            _reject(
                DecisionRejectionReason.INVALID_TYPE,
                "falsifier.claim_sha256",
                "expected a sha256 hex digest",
            )


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    """One source-attributable residual direction decision or explicit abstention."""

    event_id: str
    issuer: str
    ticker: str
    decision_cutoff: datetime
    feature_snapshot_at: datetime
    decided_at: datetime
    decision_deadline: datetime
    direction: Direction
    decision_state: StrategyDecisionState
    abstention_reasons: tuple[AbstentionReason, ...]
    reaction_relation: ReactionRelation
    opening_residual: Decimal
    evidence_citations: tuple[EvidenceCitation, ...]
    strongest_falsifier: Falsifier
    snapshot_sha256: str
    policy_version: str
    policy_sha256: str
    route_sha256: str
    reasoner_output_sha256: str
    trace_stages: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("event_id", "issuer", "ticker"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                _reject(DecisionRejectionReason.INVALID_TYPE, field, "expected non-empty text")
        for field in ("decision_cutoff", "feature_snapshot_at", "decided_at", "decision_deadline"):
            value = getattr(self, field)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                _reject(DecisionRejectionReason.INVALID_TYPE, field, "expected aware datetime")
        if self.feature_snapshot_at > self.decision_cutoff:
            _reject(
                DecisionRejectionReason.CUTOFF_VIOLATION,
                "feature_snapshot_at",
                "feature snapshot cannot postdate the decision cutoff",
            )
        if self.decided_at > self.decision_deadline:
            _reject(
                DecisionRejectionReason.DEADLINE_VIOLATION,
                "decided_at",
                "decision arrived after the valid-signal deadline",
            )
        for field in ("snapshot_sha256", "policy_sha256", "route_sha256", "reasoner_output_sha256"):
            value = getattr(self, field)
            if not isinstance(value, str) or not _SHA256.match(value):
                _reject(DecisionRejectionReason.INVALID_TYPE, field, "expected a sha256 hex digest")
        if not isinstance(self.opening_residual, Decimal) or not self.opening_residual.is_finite():
            _reject(
                DecisionRejectionReason.NON_FINITE_VALUE,
                "opening_residual",
                "opening residual must be a finite decimal",
            )
        if self.policy_version != REQUIRED_POLICY_VERSION:
            _reject(
                DecisionRejectionReason.POLICY_HASH_MISMATCH,
                "policy_version",
                "decision is not bound to the frozen Esscher v1 policy",
            )
        if tuple(self.trace_stages) != TRACE_STAGES:
            _reject(
                DecisionRejectionReason.INVALID_VALUE,
                "trace_stages",
                f"trace must be exactly {list(TRACE_STAGES)}",
            )
        abstaining = bool(self.abstention_reasons)
        if abstaining and (
            self.decision_state is not StrategyDecisionState.ABSTAIN
            or self.direction is not Direction.UNCERTAIN
        ):
            _reject(
                DecisionRejectionReason.INVALID_ABSTENTION,
                "decision_state",
                "any abstention reason forces ABSTAIN with UNCERTAIN",
            )
        if not abstaining and self.decision_state is StrategyDecisionState.ABSTAIN:
            _reject(
                DecisionRejectionReason.INVALID_ABSTENTION,
                "abstention_reasons",
                "an abstaining decision must carry at least one reason",
            )
        if self.decision_state is StrategyDecisionState.APPROVED:
            if self.direction not in (Direction.UP, Direction.DOWN):
                _reject(
                    DecisionRejectionReason.INVALID_ABSTENTION,
                    "direction",
                    "an approved decision must carry UP or DOWN",
                )
            if not self.evidence_citations:
                _reject(
                    DecisionRejectionReason.INVALID_ABSTENTION,
                    "evidence_citations",
                    "an approved decision must cite at least one evidence record",
                )

    @property
    def is_abstention(self) -> bool:
        return self.decision_state is StrategyDecisionState.ABSTAIN


def strategy_decision_payload(decision: StrategyDecision) -> dict[str, object]:
    """Return the canonical serializable payload for one strategy decision."""

    return {
        "schema": STRATEGY_DECISION_SCHEMA,
        "schema_version": STRATEGY_DECISION_SCHEMA_VERSION,
        "event_id": decision.event_id,
        "issuer": decision.issuer,
        "ticker": decision.ticker,
        "decision_cutoff": _timestamp_text(decision.decision_cutoff),
        "feature_snapshot_at": _timestamp_text(decision.feature_snapshot_at),
        "decided_at": _timestamp_text(decision.decided_at),
        "decision_deadline": _timestamp_text(decision.decision_deadline),
        "direction": decision.direction.value,
        "decision_state": decision.decision_state.value,
        "abstention_reasons": [reason.value for reason in decision.abstention_reasons],
        "reaction_relation": decision.reaction_relation.value,
        "opening_residual": str(decision.opening_residual),
        "evidence_citations": [
            {
                "citation_id": citation.citation_id,
                "evidence_id": citation.evidence_id,
                "claim_sha256": citation.claim_sha256,
            }
            for citation in decision.evidence_citations
        ],
        "strongest_falsifier": {
            "falsifier_id": decision.strongest_falsifier.falsifier_id,
            "evidence_id": decision.strongest_falsifier.evidence_id,
            "claim_sha256": decision.strongest_falsifier.claim_sha256,
        },
        "snapshot_sha256": decision.snapshot_sha256,
        "policy_version": decision.policy_version,
        "policy_sha256": decision.policy_sha256,
        "route_sha256": decision.route_sha256,
        "reasoner_output_sha256": decision.reasoner_output_sha256,
        "trace_stages": list(decision.trace_stages),
        "claim": REQUIRED_CLAIM,
        "data_qualifiers": list(REQUIRED_QUALIFIERS),
    }


def strategy_decision_bytes(decision: StrategyDecision) -> bytes:
    """Serialize one decision to deterministic canonical bytes."""

    return _canonical_json_bytes(strategy_decision_payload(decision))


def strategy_decision_sha256(decision: StrategyDecision) -> str:
    """Return the SHA-256 of the deterministic decision bytes."""

    return hashlib.sha256(strategy_decision_bytes(decision)).hexdigest()


def _parse_timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.match(value):
        _reject(
            DecisionRejectionReason.INVALID_TYPE, path, "expected UTC second-precision timestamp"
        )
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        _reject(DecisionRejectionReason.INVALID_VALUE, path, "timestamp does not exist")


def _parse_sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _SHA256.match(value):
        _reject(DecisionRejectionReason.INVALID_TYPE, path, "expected a sha256 hex digest")
    return value


def _parse_residual(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str) or not _RESIDUAL.match(value):
        _reject(
            DecisionRejectionReason.INVALID_TYPE, path, "expected canonical decimal residual text"
        )
    try:
        residual = Decimal(value)
    except InvalidOperation:
        _reject(DecisionRejectionReason.NON_FINITE_VALUE, path, "residual is not finite")
    if not residual.is_finite():
        _reject(DecisionRejectionReason.NON_FINITE_VALUE, path, "residual is not finite")
    return residual


def _parse_citation(value: object, *, path: str) -> EvidenceCitation:
    if not isinstance(value, Mapping):
        _reject(DecisionRejectionReason.INVALID_TYPE, path, "expected an object")
    fields = frozenset({"citation_id", "evidence_id", "claim_sha256"})
    for key in value:
        if key not in fields:
            _reject(DecisionRejectionReason.UNKNOWN_FIELD, f"{path}.{key}", "unknown field")
    for key in fields:
        if key not in value:
            _reject(
                DecisionRejectionReason.MISSING_FIELD, f"{path}.{key}", "missing required field"
            )
    return EvidenceCitation(
        citation_id=value["citation_id"],  # type: ignore[arg-type]
        evidence_id=value["evidence_id"],  # type: ignore[arg-type]
        claim_sha256=value["claim_sha256"],  # type: ignore[arg-type]
    )


def _parse_falsifier(value: object, *, path: str) -> Falsifier:
    if not isinstance(value, Mapping):
        _reject(DecisionRejectionReason.INVALID_TYPE, path, "expected an object")
    fields = frozenset({"falsifier_id", "evidence_id", "claim_sha256"})
    for key in value:
        if key not in fields:
            _reject(DecisionRejectionReason.UNKNOWN_FIELD, f"{path}.{key}", "unknown field")
    for key in fields:
        if key not in value:
            _reject(
                DecisionRejectionReason.MISSING_FIELD, f"{path}.{key}", "missing required field"
            )
    return Falsifier(
        falsifier_id=value["falsifier_id"],  # type: ignore[arg-type]
        evidence_id=value["evidence_id"],  # type: ignore[arg-type]
        claim_sha256=value["claim_sha256"],  # type: ignore[arg-type]
    )


_DECISION_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "event_id",
        "issuer",
        "ticker",
        "decision_cutoff",
        "feature_snapshot_at",
        "decided_at",
        "decision_deadline",
        "direction",
        "decision_state",
        "abstention_reasons",
        "reaction_relation",
        "opening_residual",
        "evidence_citations",
        "strongest_falsifier",
        "snapshot_sha256",
        "policy_version",
        "policy_sha256",
        "route_sha256",
        "reasoner_output_sha256",
        "trace_stages",
        "claim",
        "data_qualifiers",
    }
)


def parse_strategy_decision(raw: bytes) -> StrategyDecision:
    """Parse strict strategy decision bytes; execution fields and drift fail closed."""

    if not isinstance(raw, (bytes, bytearray)):
        raise StrategyDecisionRejected(
            DecisionRejectionReason.INVALID_DOCUMENT, "decision", "decision bytes are required"
        )
    payload = _decode_document(bytes(raw), label="decision")
    for key in payload:
        if key in FORBIDDEN_EXECUTION_FIELDS:
            _reject(
                DecisionRejectionReason.EXECUTION_FIELD_FORBIDDEN,
                f"decision.{key}",
                "strategy decisions cannot carry order, permit, or contract fields",
            )
        if key not in _DECISION_FIELDS:
            _reject(DecisionRejectionReason.UNKNOWN_FIELD, f"decision.{key}", "unknown field")
    for key in _DECISION_FIELDS:
        if key not in payload:
            _reject(
                DecisionRejectionReason.MISSING_FIELD, f"decision.{key}", "missing required field"
            )

    if payload["schema"] != STRATEGY_DECISION_SCHEMA:
        _reject(DecisionRejectionReason.UNSUPPORTED_SCHEMA, "decision.schema", "unsupported schema")
    if payload["schema_version"] != STRATEGY_DECISION_SCHEMA_VERSION:
        _reject(
            DecisionRejectionReason.UNSUPPORTED_SCHEMA,
            "decision.schema_version",
            "unsupported schema version",
        )
    if payload["claim"] != REQUIRED_CLAIM:
        _reject(
            DecisionRejectionReason.INVALID_VALUE,
            "decision.claim",
            "claim must remain NOT_ALPHA_EVIDENCE",
        )

    try:
        direction = Direction(payload["direction"])
    except ValueError:
        _reject(DecisionRejectionReason.INVALID_VALUE, "decision.direction", "unknown direction")
    try:
        decision_state = StrategyDecisionState(payload["decision_state"])
    except ValueError:
        _reject(
            DecisionRejectionReason.INVALID_VALUE,
            "decision.decision_state",
            "unknown decision state",
        )
    try:
        reaction_relation = ReactionRelation(payload["reaction_relation"])
    except ValueError:
        _reject(
            DecisionRejectionReason.INVALID_VALUE,
            "decision.reaction_relation",
            "unknown reaction relation",
        )

    reasons_value = payload["abstention_reasons"]
    if not isinstance(reasons_value, list):
        _reject(
            DecisionRejectionReason.INVALID_TYPE,
            "decision.abstention_reasons",
            "expected a list of abstention codes",
        )
    reasons: list[AbstentionReason] = []
    for index, item in enumerate(reasons_value):
        try:
            reason = AbstentionReason(item)
        except ValueError:
            _reject(
                DecisionRejectionReason.INVALID_VALUE,
                f"decision.abstention_reasons[{index}]",
                "unknown abstention code",
            )
        if reason in reasons:
            _reject(
                DecisionRejectionReason.INVALID_VALUE,
                f"decision.abstention_reasons[{index}]",
                "duplicate abstention code",
            )
        reasons.append(reason)

    citations_value = payload["evidence_citations"]
    if not isinstance(citations_value, list):
        _reject(
            DecisionRejectionReason.INVALID_TYPE,
            "decision.evidence_citations",
            "expected a list of citations",
        )
    citations = tuple(
        _parse_citation(item, path=f"decision.evidence_citations[{index}]")
        for index, item in enumerate(citations_value)
    )

    stages_value = payload["trace_stages"]
    if not isinstance(stages_value, list):
        _reject(
            DecisionRejectionReason.INVALID_TYPE, "decision.trace_stages", "expected stage list"
        )

    qualifiers_value = payload["data_qualifiers"]
    if not isinstance(qualifiers_value, list) or tuple(qualifiers_value) != REQUIRED_QUALIFIERS:
        _reject(
            DecisionRejectionReason.INVALID_VALUE,
            "decision.data_qualifiers",
            "INDICATIVE_DATA and NOT_ALPHA_EVIDENCE are required",
        )

    return StrategyDecision(
        event_id=payload["event_id"],  # type: ignore[arg-type]
        issuer=payload["issuer"],  # type: ignore[arg-type]
        ticker=payload["ticker"],  # type: ignore[arg-type]
        decision_cutoff=_parse_timestamp(
            payload["decision_cutoff"], path="decision.decision_cutoff"
        ),
        feature_snapshot_at=_parse_timestamp(
            payload["feature_snapshot_at"], path="decision.feature_snapshot_at"
        ),
        decided_at=_parse_timestamp(payload["decided_at"], path="decision.decided_at"),
        decision_deadline=_parse_timestamp(
            payload["decision_deadline"], path="decision.decision_deadline"
        ),
        direction=direction,
        decision_state=decision_state,
        abstention_reasons=tuple(reasons),
        reaction_relation=reaction_relation,
        opening_residual=_parse_residual(
            payload["opening_residual"], path="decision.opening_residual"
        ),
        evidence_citations=citations,
        strongest_falsifier=_parse_falsifier(
            payload["strongest_falsifier"], path="decision.strongest_falsifier"
        ),
        snapshot_sha256=_parse_sha256(payload["snapshot_sha256"], path="decision.snapshot_sha256"),
        policy_version=payload["policy_version"],  # type: ignore[arg-type]
        policy_sha256=_parse_sha256(payload["policy_sha256"], path="decision.policy_sha256"),
        route_sha256=_parse_sha256(payload["route_sha256"], path="decision.route_sha256"),
        reasoner_output_sha256=_parse_sha256(
            payload["reasoner_output_sha256"], path="decision.reasoner_output_sha256"
        ),
        trace_stages=tuple(stages_value),
    )


def validate_decision_policy_binding(decision: StrategyDecision, *, expected_sha256: str) -> None:
    """Fail closed when the decision is not bound to the expected frozen policy."""

    if decision.policy_sha256 != expected_sha256:
        _reject(
            DecisionRejectionReason.POLICY_HASH_MISMATCH,
            "decision.policy_sha256",
            "decision policy hash does not match the frozen policy identity",
        )
