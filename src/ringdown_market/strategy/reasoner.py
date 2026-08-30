"""Structured reasoner protocol, route identity, and inert route-smoke harness."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ringdown_market.alpha.models import Direction

from .decisions import FORBIDDEN_EXECUTION_FIELDS, EvidenceCitation, Falsifier

REASONER_OUTPUT_SCHEMA = "esscher.reasoner_output"
REASONER_OUTPUT_SCHEMA_VERSION = 1

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONFIDENCE = re.compile(r"^(?:0|1)(?:\.[0-9]{1,6})?$")


class ReasonerRejectionReason(StrEnum):
    """Stable fail-closed reasons a reasoner output cannot be accepted."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_VALUE = "INVALID_VALUE"
    EXECUTION_FIELD_FORBIDDEN = "EXECUTION_FIELD_FORBIDDEN"


class ReasonerOutputRejected(ValueError):
    """Raised when a reasoner output violates the frozen structured contract."""

    def __init__(self, reason: ReasonerRejectionReason, path: str, detail: str) -> None:
        super().__init__(f"{reason.value} at {path}: {detail}")
        self.reason = reason
        self.path = path
        self.detail = detail


def _reject(reason: ReasonerRejectionReason, path: str, detail: str) -> None:
    raise ReasonerOutputRejected(reason, path, detail)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ReasonerRoute:
    """The single frozen reasoner route identity."""

    route_id: str
    prompt_sha256: str
    output_schema_sha256: str

    def __post_init__(self) -> None:
        if not self.route_id.strip():
            _reject(ReasonerRejectionReason.INVALID_TYPE, "route_id", "expected non-empty text")
        for field in ("prompt_sha256", "output_schema_sha256"):
            value = getattr(self, field)
            if not _SHA256.match(value):
                _reject(ReasonerRejectionReason.INVALID_TYPE, field, "expected a sha256 hex digest")

    @property
    def sha256(self) -> str:
        payload = {
            "schema": "esscher.reasoner_route",
            "schema_version": 1,
            "route_id": self.route_id,
            "prompt_sha256": self.prompt_sha256,
            "output_schema_sha256": self.output_schema_sha256,
        }
        return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class ReasonerOutput:
    """One validated structured reasoner result; confidence never authorizes a trade."""

    direction: Direction
    confidence: Decimal
    citations: tuple[EvidenceCitation, ...]
    falsifier: Falsifier | None
    raw_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.confidence, Decimal) or not self.confidence.is_finite():
            _reject(ReasonerRejectionReason.INVALID_TYPE, "confidence", "expected a finite decimal")
        if not (Decimal(0) <= self.confidence <= Decimal(1)):
            _reject(
                ReasonerRejectionReason.INVALID_VALUE, "confidence", "confidence is bounded 0..1"
            )
        if not _SHA256.match(self.raw_sha256):
            _reject(
                ReasonerRejectionReason.INVALID_TYPE, "raw_sha256", "expected a sha256 hex digest"
            )


_OUTPUT_FIELDS = frozenset(
    {"schema", "schema_version", "direction", "confidence", "citations", "falsifier"}
)


def parse_reasoner_output(raw: bytes) -> ReasonerOutput:
    """Parse strict structured reasoner bytes; execution fields fail closed."""

    if not isinstance(raw, (bytes, bytearray)):
        raise ReasonerOutputRejected(
            ReasonerRejectionReason.INVALID_DOCUMENT, "output", "reasoner bytes are required"
        )
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _reject(ReasonerRejectionReason.INVALID_DOCUMENT, "output", f"invalid JSON: {error}")
    if not isinstance(payload, Mapping):
        _reject(ReasonerRejectionReason.INVALID_TYPE, "output", "expected an object")
    for key in payload:
        if key in FORBIDDEN_EXECUTION_FIELDS:
            _reject(
                ReasonerRejectionReason.EXECUTION_FIELD_FORBIDDEN,
                f"output.{key}",
                "reasoner output cannot carry order, permit, or contract fields",
            )
        if key not in _OUTPUT_FIELDS:
            _reject(ReasonerRejectionReason.UNKNOWN_FIELD, f"output.{key}", "unknown field")
    for key in _OUTPUT_FIELDS:
        if key not in payload:
            _reject(
                ReasonerRejectionReason.MISSING_FIELD, f"output.{key}", "missing required field"
            )

    if payload["schema"] != REASONER_OUTPUT_SCHEMA:
        _reject(ReasonerRejectionReason.UNSUPPORTED_SCHEMA, "output.schema", "unsupported schema")
    if payload["schema_version"] != REASONER_OUTPUT_SCHEMA_VERSION:
        _reject(
            ReasonerRejectionReason.UNSUPPORTED_SCHEMA,
            "output.schema_version",
            "unsupported schema version",
        )
    try:
        direction = Direction(payload["direction"])
    except ValueError:
        _reject(ReasonerRejectionReason.INVALID_VALUE, "output.direction", "unknown direction")

    confidence_value = payload["confidence"]
    if not isinstance(confidence_value, str) or not _CONFIDENCE.match(confidence_value):
        _reject(
            ReasonerRejectionReason.INVALID_TYPE,
            "output.confidence",
            "expected canonical decimal confidence text in [0,1]",
        )

    citations_value = payload["citations"]
    if not isinstance(citations_value, list):
        _reject(ReasonerRejectionReason.INVALID_TYPE, "output.citations", "expected a list")
    citations: list[EvidenceCitation] = []
    for index, item in enumerate(citations_value):
        path = f"output.citations[{index}]"
        if not isinstance(item, Mapping):
            _reject(ReasonerRejectionReason.INVALID_TYPE, path, "expected an object")
        fields = frozenset({"citation_id", "evidence_id", "claim_sha256"})
        for key in item:
            if key not in fields:
                _reject(ReasonerRejectionReason.UNKNOWN_FIELD, f"{path}.{key}", "unknown field")
        for key in fields:
            if key not in item:
                _reject(ReasonerRejectionReason.MISSING_FIELD, f"{path}.{key}", "missing field")
        citations.append(
            EvidenceCitation(
                citation_id=item["citation_id"],  # type: ignore[arg-type]
                evidence_id=item["evidence_id"],  # type: ignore[arg-type]
                claim_sha256=item["claim_sha256"],  # type: ignore[arg-type]
            )
        )

    falsifier: Falsifier | None = None
    falsifier_value = payload["falsifier"]
    if falsifier_value is not None:
        if not isinstance(falsifier_value, Mapping):
            _reject(
                ReasonerRejectionReason.INVALID_TYPE,
                "output.falsifier",
                "expected an object or null",
            )
        fields = frozenset({"falsifier_id", "evidence_id", "claim_sha256"})
        for key in falsifier_value:
            if key not in fields:
                _reject(
                    ReasonerRejectionReason.UNKNOWN_FIELD,
                    f"output.falsifier.{key}",
                    "unknown field",
                )
        for key in fields:
            if key not in falsifier_value:
                _reject(
                    ReasonerRejectionReason.MISSING_FIELD,
                    f"output.falsifier.{key}",
                    "missing field",
                )
        falsifier = Falsifier(
            falsifier_id=falsifier_value["falsifier_id"],  # type: ignore[arg-type]
            evidence_id=falsifier_value["evidence_id"],  # type: ignore[arg-type]
            claim_sha256=falsifier_value["claim_sha256"],  # type: ignore[arg-type]
        )

    return ReasonerOutput(
        direction=direction,
        confidence=Decimal(confidence_value),
        citations=tuple(citations),
        falsifier=falsifier,
        raw_sha256=hashlib.sha256(bytes(raw)).hexdigest(),
    )


@runtime_checkable
class Reasoner(Protocol):
    """Structured reasoner boundary: one route, injected, bounded, no fallback."""

    def reason(self, snapshot_payload: Mapping[str, object]) -> bytes: ...


class FakeReasoner:
    """Deterministic fake reasoner returning one fixed structured output."""

    def __init__(self, output_payload: Mapping[str, object]) -> None:
        self._raw = _canonical_json_bytes(dict(output_payload))

    def reason(self, snapshot_payload: Mapping[str, object]) -> bytes:
        return self._raw


class RouteSmokeResult:
    """Inert latency/schema record for one reasoner route probe."""

    def __init__(
        self,
        *,
        route_sha256: str,
        attempts: int,
        schema_valid: int,
        schema_invalid: int,
        latencies_ms: Sequence[int],
    ) -> None:
        self.route_sha256 = route_sha256
        self.attempts = attempts
        self.schema_valid = schema_valid
        self.schema_invalid = schema_invalid
        self.latencies_ms = tuple(latencies_ms)

    @property
    def p95_latency_ms(self) -> int | None:
        if not self.latencies_ms:
            return None
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        return ordered[index]


def run_route_smoke(
    *,
    route: ReasonerRoute,
    reasoner: Reasoner,
    snapshot_payload: Mapping[str, object],
    attempts: int,
    clock_ms,
) -> RouteSmokeResult:
    """Probe a route's latency and schema validity without any broker authority."""

    if attempts < 1:
        raise ReasonerOutputRejected(
            ReasonerRejectionReason.INVALID_VALUE, "attempts", "attempts must be positive"
        )
    valid = 0
    invalid = 0
    latencies: list[int] = []
    for _ in range(attempts):
        start = clock_ms()
        raw = reasoner.reason(snapshot_payload)
        elapsed = clock_ms() - start
        latencies.append(int(elapsed))
        try:
            parse_reasoner_output(raw)
        except ReasonerOutputRejected:
            invalid += 1
        else:
            valid += 1
    return RouteSmokeResult(
        route_sha256=route.sha256,
        attempts=attempts,
        schema_valid=valid,
        schema_invalid=invalid,
        latencies_ms=latencies,
    )


def validate_output_deadline(arrived_at: datetime, deadline: datetime) -> bool:
    """Return True when the reasoner result arrived by the valid-signal deadline."""

    return arrived_at <= deadline
