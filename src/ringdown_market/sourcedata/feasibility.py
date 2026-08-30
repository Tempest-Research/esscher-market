"""Candidate-specific data-feasibility manifests with fail-closed verdicts.

Gate B requires one manifest per candidate recording source, endpoint,
publisher clock, timestamp precision, retrieval semantics, revision behavior,
identifiers, entitlement, retention/redistribution rights, feed/adjustment
policy, historical coverage, known gaps, and a reproducible sample receipt.

A manifest is comparison/collection tooling. An INFEASIBLE earnings verdict
records that the macro challenger evaluation is triggered; it never authorizes
a trade.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from ringdown_market.sourcedata._checks import require_identifier, require_sha256, require_utc
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

FEASIBILITY_SCHEMA: Final = "esscher.data_feasibility_manifest"
FEASIBILITY_SCHEMA_VERSION: Final = 1
VERDICT_FEASIBLE: Final = "FEASIBLE"
VERDICT_INFEASIBLE: Final = "INFEASIBLE"
REASON_MISSING_REQUIRED_SOURCE: Final = "MISSING_REQUIRED_SOURCE"
REASON_SOURCE_RIGHTS_UNVERIFIED: Final = "SOURCE_RIGHTS_UNVERIFIED"
FEASIBILITY_CLAIMS: Final = (
    "COMPARISON_TOOLING",
    "NOT_ALPHA_EVIDENCE",
    "NO_BROKER_EXECUTION",
    "NO_TRADE_AUTHORIZATION",
)

_SOURCE_FIELDS: Final = frozenset(
    {
        "source_family",
        "endpoint",
        "publisher_clock",
        "timestamp_precision",
        "retrieval",
        "revision_behavior",
        "identifiers",
        "entitlement",
        "redistribution",
        "feed_adjustment_policy",
        "historical_coverage",
        "known_gaps",
        "sample_receipt_sha256",
    }
)
_MANIFEST_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "candidate_id",
        "policy_sha256",
        "producer_build_sha256",
        "evaluated_at",
        "sources",
        "verdict",
        "verdict_reasons",
        "fallback_candidate_id",
        "claims",
    }
)


class _DuplicateFieldError(ValueError):
    pass


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise CollectorRejected(
        CollectorReason.UNSUPPORTED_INPUT,
        "feasibility_manifest",
        f"non-finite JSON constant {value} is forbidden",
    )


@dataclass(frozen=True, slots=True)
class SourceFeasibilityDeclaration:
    """One declared source family for one candidate."""

    source_family: str
    endpoint: str
    publisher_clock: str
    timestamp_precision: str
    retrieval: str
    revision_behavior: str
    identifiers: str
    entitlement: str
    redistribution: str
    feed_adjustment_policy: str
    historical_coverage: str
    known_gaps: tuple[str, ...]

    def __post_init__(self) -> None:
        require_identifier(self.source_family, "source_family")
        for field in (
            self.endpoint,
            self.publisher_clock,
            self.timestamp_precision,
            self.retrieval,
            self.revision_behavior,
            self.identifiers,
            self.feed_adjustment_policy,
            self.historical_coverage,
        ):
            if not field:
                raise ValueError("feasibility declaration fields must be non-empty text")
        if self.entitlement not in {"ENTITLED", "PUBLIC", "UNVERIFIED"}:
            raise ValueError("entitlement must be ENTITLED, PUBLIC, or UNVERIFIED")
        if self.redistribution not in {
            "REDISTRIBUTABLE",
            "NON_REDISTRIBUTABLE",
            "UNKNOWN",
        }:
            raise ValueError("redistribution must be a registered value")
        if self.known_gaps != tuple(sorted(set(self.known_gaps))):
            raise ValueError("known_gaps must be sorted unique text")


@dataclass(frozen=True, slots=True)
class FeasibilitySourceRecord:
    """One verified source entry bound to a reproducible sample receipt."""

    declaration: SourceFeasibilityDeclaration
    sample_receipt_sha256: str

    def __post_init__(self) -> None:
        require_sha256(self.sample_receipt_sha256, "sample_receipt_sha256")


@dataclass(frozen=True, slots=True)
class FeasibilityManifest:
    """The canonical Gate B feasibility artifact for one candidate."""

    candidate_id: str
    policy_sha256: str
    producer_build_sha256: str
    evaluated_at: datetime
    sources: tuple[FeasibilitySourceRecord, ...]
    verdict: str
    verdict_reasons: tuple[str, ...]
    fallback_candidate_id: str | None

    def __post_init__(self) -> None:
        require_identifier(self.candidate_id, "candidate_id")
        require_sha256(self.policy_sha256, "policy_sha256")
        require_sha256(self.producer_build_sha256, "producer_build_sha256")
        require_utc(self.evaluated_at, "evaluated_at")
        if self.verdict not in {VERDICT_FEASIBLE, VERDICT_INFEASIBLE}:
            raise ValueError("verdict must be FEASIBLE or INFEASIBLE")
        if self.verdict == VERDICT_FEASIBLE and self.verdict_reasons:
            raise ValueError("feasible verdicts cannot carry verdict reasons")
        if self.verdict == VERDICT_INFEASIBLE and not self.verdict_reasons:
            raise ValueError("infeasible verdicts require stable verdict reasons")
        if self.verdict_reasons != tuple(sorted(set(self.verdict_reasons))):
            raise ValueError("verdict reasons must be sorted unique text")


def source_record_payload(record: FeasibilitySourceRecord) -> dict[str, object]:
    declaration = record.declaration
    return {
        "source_family": declaration.source_family,
        "endpoint": declaration.endpoint,
        "publisher_clock": declaration.publisher_clock,
        "timestamp_precision": declaration.timestamp_precision,
        "retrieval": declaration.retrieval,
        "revision_behavior": declaration.revision_behavior,
        "identifiers": declaration.identifiers,
        "entitlement": declaration.entitlement,
        "redistribution": declaration.redistribution,
        "feed_adjustment_policy": declaration.feed_adjustment_policy,
        "historical_coverage": declaration.historical_coverage,
        "known_gaps": list(declaration.known_gaps),
        "sample_receipt_sha256": record.sample_receipt_sha256,
    }


def feasibility_manifest_payload(value: FeasibilityManifest) -> dict[str, object]:
    """Return the single versioned serialization for one feasibility manifest."""

    return {
        "schema": FEASIBILITY_SCHEMA,
        "schema_version": FEASIBILITY_SCHEMA_VERSION,
        "candidate_id": value.candidate_id,
        "policy_sha256": value.policy_sha256,
        "producer_build_sha256": value.producer_build_sha256,
        "evaluated_at": value.evaluated_at.isoformat().replace("+00:00", "Z"),
        "sources": [
            source_record_payload(record)
            for record in sorted(value.sources, key=lambda record: record.declaration.source_family)
        ],
        "verdict": value.verdict,
        "verdict_reasons": list(value.verdict_reasons),
        "fallback_candidate_id": value.fallback_candidate_id,
        "claims": list(FEASIBILITY_CLAIMS),
    }


def feasibility_manifest_bytes(value: FeasibilityManifest) -> bytes:
    """Serialize one feasibility manifest to deterministic canonical bytes."""

    return canonical_json_bytes(feasibility_manifest_payload(value))


def feasibility_manifest_sha256(value: FeasibilityManifest) -> str:
    return sha256_bytes(feasibility_manifest_bytes(value))


def sample_receipt_sha256_by_class(
    source_receipts: Sequence[object],
) -> dict[str, str]:
    """Map each source class to the canonical bytes hash of its first receipt."""

    from ringdown_market.sourcedata.receipts import source_receipt_bytes

    mapping: dict[str, str] = {}
    for receipt in sorted(
        source_receipts,
        key=lambda item: item.receipt_id,  # type: ignore[attr-defined]
    ):
        source_class = receipt.source_class  # type: ignore[attr-defined]
        if source_class not in mapping:
            mapping[source_class] = sha256_bytes(source_receipt_bytes(receipt))
    return mapping


def build_feasibility_for_candidate(
    *,
    policy,
    candidate_id: str,
    declarations: Sequence[SourceFeasibilityDeclaration],
    source_receipts: Sequence[object],
    evaluated_at: datetime,
    producer_build_sha256: str,
    fallback_candidate_id: str | None = None,
) -> FeasibilityManifest:
    """Assemble the feasibility manifest for one candidate under the policy."""

    candidate_policy = policy.candidate(candidate_id)
    evidence_policy = candidate_policy["evidence"]
    required = tuple(evidence_policy["required_source_classes"])
    return build_feasibility_manifest(
        candidate_id=candidate_id,
        policy_sha256=policy.sha256,
        producer_build_sha256=producer_build_sha256,
        evaluated_at=evaluated_at,
        declarations=declarations,
        sample_receipt_sha256_by_class=sample_receipt_sha256_by_class(source_receipts),
        required_source_classes=required,
        fallback_candidate_id=fallback_candidate_id,
    )


def build_feasibility_manifest(
    *,
    candidate_id: str,
    policy_sha256: str,
    producer_build_sha256: str,
    evaluated_at: datetime,
    declarations: Sequence[SourceFeasibilityDeclaration],
    sample_receipt_sha256_by_class: Mapping[str, str],
    required_source_classes: Sequence[str],
    fallback_candidate_id: str | None = None,
) -> FeasibilityManifest:
    """Assemble one feasibility manifest with a fail-closed verdict."""

    if evaluated_at.tzinfo != UTC:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "evaluated_at",
            "evaluation clock must be UTC",
        )
    declared_by_family = {declaration.source_family: declaration for declaration in declarations}
    reasons: set[str] = set()
    records: list[FeasibilitySourceRecord] = []
    for source_family in sorted(set(required_source_classes) | set(declared_by_family)):
        declaration = declared_by_family.get(source_family)
        sample_sha256 = sample_receipt_sha256_by_class.get(source_family)
        if declaration is None or sample_sha256 is None:
            reasons.add(REASON_MISSING_REQUIRED_SOURCE)
            continue
        if declaration.entitlement == "UNVERIFIED":
            reasons.add(REASON_SOURCE_RIGHTS_UNVERIFIED)
        records.append(
            FeasibilitySourceRecord(declaration=declaration, sample_receipt_sha256=sample_sha256)
        )
    for required in required_source_classes:
        if required not in declared_by_family or required not in sample_receipt_sha256_by_class:
            reasons.add(REASON_MISSING_REQUIRED_SOURCE)
    verdict = VERDICT_FEASIBLE if not reasons else VERDICT_INFEASIBLE
    return FeasibilityManifest(
        candidate_id=candidate_id,
        policy_sha256=policy_sha256,
        producer_build_sha256=producer_build_sha256,
        evaluated_at=evaluated_at,
        sources=tuple(records),
        verdict=verdict,
        verdict_reasons=tuple(sorted(reasons)),
        fallback_candidate_id=fallback_candidate_id if verdict == VERDICT_INFEASIBLE else None,
    )


def _parse_declaration(source: Mapping[str, object], *, path: str) -> SourceFeasibilityDeclaration:
    try:
        return SourceFeasibilityDeclaration(
            source_family=str(source["source_family"]),
            endpoint=str(source["endpoint"]),
            publisher_clock=str(source["publisher_clock"]),
            timestamp_precision=str(source["timestamp_precision"]),
            retrieval=str(source["retrieval"]),
            revision_behavior=str(source["revision_behavior"]),
            identifiers=str(source["identifiers"]),
            entitlement=str(source["entitlement"]),
            redistribution=str(source["redistribution"]),
            feed_adjustment_policy=str(source["feed_adjustment_policy"]),
            historical_coverage=str(source["historical_coverage"]),
            known_gaps=_string_list(source["known_gaps"], path=f"{path}.known_gaps"),
        )
    except ValueError as error:
        raise CollectorRejected(CollectorReason.UNSUPPORTED_INPUT, path, str(error)) from None


def _string_list(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "must be a list of strings"
        )
    result = tuple(value)  # type: ignore[arg-type]
    if result != tuple(sorted(set(result))):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "must be sorted and unique"
        )
    return result


def parse_feasibility_manifest(raw: bytes) -> FeasibilityManifest:
    """Strictly parse canonical ``esscher.data_feasibility_manifest/v1`` bytes."""

    if type(raw) is not bytes:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest",
            "manifest input must be immutable bytes",
        )
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except _DuplicateFieldError as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest",
            f"duplicate JSON field {error}",
        ) from None
    except CollectorRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, "feasibility_manifest", str(error)
        ) from None
    if not isinstance(payload, dict):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest",
            "manifest root must be an object",
        )
    actual = frozenset(payload)
    missing = sorted(_MANIFEST_FIELDS - actual)
    unknown = sorted(actual - _MANIFEST_FIELDS)
    if missing or unknown:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest",
            f"field mismatch; missing={missing} unknown={unknown}",
        )
    if payload["schema"] != FEASIBILITY_SCHEMA:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest.schema",
            "unsupported schema",
        )
    if payload["schema_version"] != FEASIBILITY_SCHEMA_VERSION:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest.schema_version",
            "unsupported schema version",
        )
    if payload["claims"] != list(FEASIBILITY_CLAIMS):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest.claims",
            "claim boundary is invalid",
        )
    sources_payload = payload["sources"]
    if not isinstance(sources_payload, list):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest.sources",
            "must be a list",
        )
    sources: list[FeasibilitySourceRecord] = []
    for index, source in enumerate(sources_payload):
        path = f"feasibility_manifest.sources[{index}]"
        if not isinstance(source, dict):
            raise CollectorRejected(CollectorReason.UNSUPPORTED_INPUT, path, "must be an object")
        source_actual = frozenset(source)
        if source_actual != _SOURCE_FIELDS:
            raise CollectorRejected(
                CollectorReason.UNSUPPORTED_INPUT,
                path,
                "source record fields do not match the registered shape",
            )
        declaration = _parse_declaration(source, path=path)
        sources.append(
            FeasibilitySourceRecord(
                declaration=declaration,
                sample_receipt_sha256=str(source["sample_receipt_sha256"]),
            )
        )
    evaluated_at = datetime.fromisoformat(
        str(payload["evaluated_at"]).replace("Z", "+00:00")
    ).astimezone(UTC)
    fallback = payload["fallback_candidate_id"]
    try:
        result = FeasibilityManifest(
            candidate_id=str(payload["candidate_id"]),
            policy_sha256=str(payload["policy_sha256"]),
            producer_build_sha256=str(payload["producer_build_sha256"]),
            evaluated_at=evaluated_at,
            sources=tuple(sources),
            verdict=str(payload["verdict"]),
            verdict_reasons=_string_list(
                payload["verdict_reasons"], path="feasibility_manifest.verdict_reasons"
            ),
            fallback_candidate_id=None if fallback is None else str(fallback),
        )
    except ValueError as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, "feasibility_manifest", str(error)
        ) from None
    if feasibility_manifest_bytes(result) != raw:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "feasibility_manifest",
            "manifest bytes are not canonical",
        )
    return result
