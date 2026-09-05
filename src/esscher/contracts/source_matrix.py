"""Strict source-rights and point-in-time feasibility matrix.

Issue #41 freezes the decision about every required data source before any
collector implementation: each source records endpoint, identifiers,
publisher/availability clock, timestamp precision, revisions, depth,
adjustments, completeness, entitlement, retention/redistribution rights, and
rate limits, and ends in exactly one of ``FEASIBLE``,
``FEASIBLE_WITH_LIMITATIONS``, or ``BLOCKED``. Rights ambiguity is always
``BLOCKED``, and no paid plan is ever selected without a recorded human
approval. The contract grants no collection, trading, or publication
authority; it only decides which lanes remain blocked.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib import resources
from typing import Final, NoReturn

SCHEMA_ID: Final = "esscher.source_matrix"
MATRIX_RESOURCE_NAME: Final = "policies/source_matrix_v1.json"
MATRIX_ID: Final = "ESSCHER_SOURCE_MATRIX_V1"
# Updated only when the canonical matrix bytes are intentionally amended.
SOURCE_MATRIX_V1_SHA256: Final = "888447640aa705510bc0594abc9a78f22c988e961282ff82a6f44337181d04ca"
RIGHTS_RULE: Final = "RIGHTS_AMBIGUITY_IS_BLOCKED"
PAID_PLAN_POLICY: Final = "NO_PAID_PLAN_WITHOUT_HUMAN_APPROVAL"

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER: Final = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_UTC_TIMESTAMP: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"
)
# Assembled from fragments so the committed source never carries a literal
# direct host, mirroring the hygiene checker's own construction.
_ALPACA_DOMAIN: Final = "alpaca" + ".markets"
_DIRECT_ALPACA_HOSTS: Final = frozenset(
    {
        "api." + _ALPACA_DOMAIN,
        "data." + _ALPACA_DOMAIN,
        "paper-api." + _ALPACA_DOMAIN,
        "stream.data." + _ALPACA_DOMAIN,
    }
)

REQUIRED_CATEGORY_ORDER: Final = (
    "EARNINGS_CALENDAR_REVISIONS",
    "ISSUER_SEC_BYTES",
    "NEWS",
    "CONSENSUS",
    "FUNDAMENTALS",
    "EQUITY_MARKET_SECTOR_OBSERVATIONS",
    "MACRO_VINTAGES",
    "CURRENT_OPTIONS",
    "HISTORICAL_OPTION_BBO",
)
VERDICTS: Final = frozenset({"FEASIBLE", "FEASIBLE_WITH_LIMITATIONS", "BLOCKED"})
ENTITLEMENTS: Final = frozenset({"VERIFIED_PUBLIC", "VERIFIED_LICENSED", "UNVERIFIED", "AMBIGUOUS"})
RETENTION_STATUSES: Final = frozenset(
    {
        "PUBLIC_DOMAIN_REDISTRIBUTABLE",
        "OFFICIAL_DOCUMENT_BYTES",
        "RETENTION_ONLY_HASH_RECEIPTS",
        "AMBIGUOUS",
    }
)
EVIDENCE_KINDS: Final = frozenset({"DOCUMENT", "BUNDLE"})
CONDITIONS: Final = frozenset(
    {
        "HUMAN_VERIFIED_CAPTURE",
        "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
        "GATE_A_OPTION_ENTITLEMENT_RECEIPT",
        "PER_RECORD_PRIMARY_PROVENANCE",
        "HUMAN_APPROVAL_FOR_PAID_PLAN",
    }
)

_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "matrix_id",
        "matrix_version",
        "decided_at",
        "policy_sha256",
        "gate_a_contract_sha256",
        "rights_rule",
        "paid_plan_policy",
        "evidence_registry",
        "categories",
        "bundles",
        "sources",
    }
)
_SOURCE_FIELDS: Final = frozenset(
    {
        "source_id",
        "category",
        "source_classes",
        "endpoint",
        "identifiers",
        "publisher_availability_clock",
        "timestamp_precision",
        "revision_policy",
        "depth",
        "adjustment_policy",
        "completeness",
        "entitlement",
        "retention_redistribution",
        "rate_limits",
        "paid_plan_required",
        "human_approval",
        "verdict",
        "limitations",
        "conditions",
        "evidence",
    }
)
_EVIDENCE_FIELDS: Final = frozenset(
    {"kind", "reference", "retrieved_at", "content_sha256", "quote"}
)
_HUMAN_APPROVAL_FIELDS: Final = frozenset({"approved_by", "approved_at", "decision"})


class MatrixReason(StrEnum):
    """Machine-readable fail-closed reasons for the source matrix."""

    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MALFORMED_VALUE = "MALFORMED_VALUE"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    RIGHTS_AMBIGUOUS = "RIGHTS_AMBIGUOUS"
    PAID_PLAN_UNAPPROVED = "PAID_PLAN_UNAPPROVED"
    MATRIX_INCOMPLETE = "MATRIX_INCOMPLETE"
    UPSTREAM_CONTRACT_DRIFT = "UPSTREAM_CONTRACT_DRIFT"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    SOURCE_RIGHTS_BLOCKED = "SOURCE_RIGHTS_BLOCKED"
    SOURCE_RIGHTS_LIMITATION_UNMET = "SOURCE_RIGHTS_LIMITATION_UNMET"


class HumanApprovalDecision(StrEnum):
    """The only state that authorizes a paid-plan matrix source."""

    APPROVED = "APPROVED"


class MatrixRejected(ValueError):
    """A deterministic fail-closed source-matrix error."""

    def __init__(self, reason: MatrixReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


@dataclass(frozen=True)
class HumanApproval:
    approved_by: str
    approved_at: datetime
    decision: HumanApprovalDecision


@dataclass(frozen=True)
class SourceEvidence:
    kind: str
    reference: str
    retrieved_at: datetime | None
    content_sha256: str | None
    quote: str | None


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    category: str
    source_classes: tuple[str, ...]
    endpoint: str
    identifiers: str
    publisher_availability_clock: str
    timestamp_precision: str
    revision_policy: str
    depth: str
    adjustment_policy: str
    completeness: str
    entitlement: str
    retention_redistribution: str
    rate_limits: str
    paid_plan_required: bool
    human_approval: HumanApproval | None
    verdict: str
    limitations: tuple[str, ...]
    conditions: tuple[str, ...]
    evidence: tuple[SourceEvidence, ...]

    @property
    def blocked(self) -> bool:
        return self.verdict == "BLOCKED"


@dataclass(frozen=True)
class SourceMatrix:
    matrix_id: str
    matrix_version: str
    decided_at: datetime
    policy_sha256: str
    gate_a_contract_sha256: str
    evidence_registry: str
    categories: tuple[str, ...]
    bundles: tuple[str, ...]
    sources: tuple[SourceRecord, ...]
    sha256: str

    def sources_by_id(self) -> dict[str, SourceRecord]:
        return {source.source_id: source for source in self.sources}

    def sources_for_class(self, source_class: str) -> tuple[SourceRecord, ...]:
        return tuple(source for source in self.sources if source_class in source.source_classes)


def _reject(reason: MatrixReason, path: str, detail: str) -> NoReturn:
    raise MatrixRejected(reason, path, detail)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            _reject(MatrixReason.DUPLICATE_FIELD, key, f"duplicate field '{key}'")
        seen.add(key)
    return dict(pairs)


def _decode(raw: bytes, *, label: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except UnicodeDecodeError as exc:
        _reject(MatrixReason.MALFORMED_VALUE, label, f"not valid UTF-8: {exc}")
    except json.JSONDecodeError as exc:
        _reject(MatrixReason.MALFORMED_VALUE, label, f"invalid JSON: {exc.msg}")


def _strict_object(
    value: object, *, path: str, fields: frozenset[str], reason: MatrixReason
) -> dict[str, object]:
    if not isinstance(value, dict):
        _reject(reason, path, "expected an object")
    for key in value:
        if key not in fields:
            _reject(MatrixReason.UNKNOWN_FIELD, f"{path}.{key}", f"unknown field '{key}'")
    for required in fields:
        if required not in value:
            _reject(MatrixReason.MISSING_FIELD, f"{path}.{required}", "missing required field")
    return value


def _text(value: object, *, path: str, nullable: bool = False) -> str | None:
    if value is None:
        if nullable:
            return None
        _reject(MatrixReason.MALFORMED_VALUE, path, "value must not be null")
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        _reject(MatrixReason.MALFORMED_VALUE, path, "value must be a non-empty string")
    return value


def _sha256(value: object, *, path: str, nullable: bool = False) -> str | None:
    text = _text(value, path=path, nullable=nullable)
    if text is None:
        return None
    if _SHA256.fullmatch(text) is None:
        _reject(MatrixReason.MALFORMED_VALUE, path, "value must be a lowercase SHA-256 hex digest")
    return text


def _timestamp(value: object, *, path: str) -> datetime:
    text = _text(value, path=path)
    assert text is not None
    if _UTC_TIMESTAMP.fullmatch(text) is None:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            path,
            "value must be an ISO-8601 timestamp with explicit UTC Z or +00:00 offset",
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _reject(MatrixReason.MALFORMED_VALUE, path, "value must be an ISO-8601 UTC timestamp")
    return parsed.astimezone(UTC)


def _identifier(value: object, *, path: str) -> str:
    text = _text(value, path=path)
    assert text is not None
    if _IDENTIFIER.fullmatch(text) is None:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            path,
            "value must be an uppercase machine identifier",
        )
    return text


def _string_array(value: object, *, path: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        _reject(MatrixReason.MALFORMED_VALUE, path, "expected an array")
    if not value and not allow_empty:
        _reject(MatrixReason.MALFORMED_VALUE, path, "array must not be empty")
    seen: set[str] = set()
    items: list[str] = []
    for index, entry in enumerate(value):
        text = _text(entry, path=f"{path}[{index}]")
        assert text is not None
        if text in seen:
            _reject(MatrixReason.DUPLICATE_FIELD, f"{path}[{index}]", f"duplicate entry '{text}'")
        seen.add(text)
        items.append(text)
    return tuple(items)


def _no_direct_hosts(text: str, *, path: str) -> None:
    lowered = text.casefold()
    for host in _DIRECT_ALPACA_HOSTS:
        if host in lowered:
            _reject(
                MatrixReason.MALFORMED_VALUE,
                path,
                "direct Alpaca hosts bypass the adapter boundary and must not be committed",
            )


def _parse_evidence(value: object, *, path: str, bundles: frozenset[str]) -> SourceEvidence:
    record = _strict_object(
        value, path=path, fields=_EVIDENCE_FIELDS, reason=MatrixReason.MALFORMED_VALUE
    )
    kind = _text(record["kind"], path=f"{path}.kind")
    if kind not in EVIDENCE_KINDS:
        _reject(MatrixReason.MALFORMED_VALUE, f"{path}.kind", f"unknown evidence kind '{kind}'")
    reference = _text(record["reference"], path=f"{path}.reference")
    if kind == "BUNDLE" and reference not in bundles:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            f"{path}.reference",
            f"evidence bundle '{reference}' is not registered in the matrix bundle list",
        )
    retrieved_at = record["retrieved_at"]
    retrieved: datetime | None = None
    if retrieved_at is not None:
        retrieved = _timestamp(retrieved_at, path=f"{path}.retrieved_at")
    elif kind == "DOCUMENT":
        _reject(
            MatrixReason.MISSING_FIELD,
            f"{path}.retrieved_at",
            "document evidence requires a retrieval time",
        )
    content_sha256 = _sha256(record["content_sha256"], path=f"{path}.content_sha256", nullable=True)
    quote = _text(record["quote"], path=f"{path}.quote", nullable=True)
    return SourceEvidence(
        kind=kind,
        reference=reference,
        retrieved_at=retrieved,
        content_sha256=content_sha256,
        quote=quote,
    )


def _parse_approval(
    value: object, *, path: str, matrix_decided_at: datetime
) -> HumanApproval | None:
    if value is None:
        return None
    record = _strict_object(
        value, path=path, fields=_HUMAN_APPROVAL_FIELDS, reason=MatrixReason.MALFORMED_VALUE
    )
    approved_by = _identifier(record["approved_by"], path=f"{path}.approved_by")
    approved_at = _timestamp(record["approved_at"], path=f"{path}.approved_at")
    decision_text = _text(record["decision"], path=f"{path}.decision")
    assert decision_text is not None
    try:
        decision = HumanApprovalDecision(decision_text)
    except ValueError:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            f"{path}.decision",
            "decision must be a recognized human-approval state",
        )
    if approved_at > matrix_decided_at:
        _reject(
            MatrixReason.PAID_PLAN_UNAPPROVED,
            f"{path}.approved_at",
            "approval must be recorded at or before the matrix decision time",
        )
    return HumanApproval(
        approved_by=approved_by,
        approved_at=approved_at,
        decision=decision,
    )


def _parse_source(
    value: object,
    *,
    path: str,
    categories: frozenset[str],
    bundles: frozenset[str],
    matrix_decided_at: datetime,
) -> SourceRecord:
    record = _strict_object(
        value, path=path, fields=_SOURCE_FIELDS, reason=MatrixReason.MALFORMED_VALUE
    )
    source_id = _identifier(record["source_id"], path=f"{path}.source_id")
    category = _text(record["category"], path=f"{path}.category")
    if category not in categories:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            f"{path}.category",
            f"unknown category '{category}'",
        )
    classes = _string_array(
        record["source_classes"], path=f"{path}.source_classes", allow_empty=True
    )
    for index, entry in enumerate(classes):
        _identifier(entry, path=f"{path}.source_classes[{index}]")
    text_fields = {
        "endpoint": "endpoint",
        "identifiers": "identifiers",
        "publisher_availability_clock": "publisher_availability_clock",
        "timestamp_precision": "timestamp_precision",
        "revision_policy": "revision_policy",
        "depth": "depth",
        "adjustment_policy": "adjustment_policy",
        "completeness": "completeness",
        "rate_limits": "rate_limits",
    }
    texts: dict[str, str] = {}
    for field, attr in text_fields.items():
        texts[attr] = _text(record[field], path=f"{path}.{field}")
    _no_direct_hosts(texts["endpoint"], path=f"{path}.endpoint")
    entitlement = _text(record["entitlement"], path=f"{path}.entitlement")
    if entitlement not in ENTITLEMENTS:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            f"{path}.entitlement",
            f"unknown entitlement '{entitlement}'",
        )
    retention = _text(record["retention_redistribution"], path=f"{path}.retention_redistribution")
    if retention not in RETENTION_STATUSES:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            f"{path}.retention_redistribution",
            f"unknown retention status '{retention}'",
        )
    if not isinstance(record["paid_plan_required"], bool):
        _reject(
            MatrixReason.MALFORMED_VALUE,
            f"{path}.paid_plan_required",
            "paid_plan_required must be a boolean",
        )
    approval = _parse_approval(
        record["human_approval"],
        path=f"{path}.human_approval",
        matrix_decided_at=matrix_decided_at,
    )
    verdict = _text(record["verdict"], path=f"{path}.verdict")
    if verdict not in VERDICTS:
        _reject(MatrixReason.MALFORMED_VALUE, f"{path}.verdict", f"unknown verdict '{verdict}'")
    limitations = _string_array(record["limitations"], path=f"{path}.limitations", allow_empty=True)
    conditions = _string_array(record["conditions"], path=f"{path}.conditions", allow_empty=True)
    for index, condition in enumerate(conditions):
        if condition not in CONDITIONS:
            _reject(
                MatrixReason.MALFORMED_VALUE,
                f"{path}.conditions[{index}]",
                f"unknown condition '{condition}'",
            )
    evidence_payload = record["evidence"]
    if not isinstance(evidence_payload, list) or not evidence_payload:
        _reject(
            MatrixReason.EVIDENCE_MISSING,
            f"{path}.evidence",
            "at least one evidence record is required",
        )
    evidence = tuple(
        _parse_evidence(entry, path=f"{path}.evidence[{index}]", bundles=bundles)
        for index, entry in enumerate(evidence_payload)
    )
    source = SourceRecord(
        source_id=source_id,
        category=category,
        source_classes=classes,
        endpoint=texts["endpoint"],
        identifiers=texts["identifiers"],
        publisher_availability_clock=texts["publisher_availability_clock"],
        timestamp_precision=texts["timestamp_precision"],
        revision_policy=texts["revision_policy"],
        depth=texts["depth"],
        adjustment_policy=texts["adjustment_policy"],
        completeness=texts["completeness"],
        entitlement=entitlement,
        retention_redistribution=retention,
        rate_limits=texts["rate_limits"],
        paid_plan_required=record["paid_plan_required"],
        human_approval=approval,
        verdict=verdict,
        limitations=limitations,
        conditions=conditions,
        evidence=evidence,
    )
    _validate_rights_consistency(source, path=path)
    return source


def _validate_rights_consistency(source: SourceRecord, *, path: str) -> None:
    if (
        source.entitlement == "AMBIGUOUS" or source.retention_redistribution == "AMBIGUOUS"
    ) and source.verdict != "BLOCKED":
        _reject(
            MatrixReason.RIGHTS_AMBIGUOUS,
            f"{path}.verdict",
            "ambiguous entitlement or redistribution rights must yield BLOCKED",
        )
    if source.paid_plan_required and source.human_approval is None and source.verdict != "BLOCKED":
        _reject(
            MatrixReason.PAID_PLAN_UNAPPROVED,
            f"{path}.verdict",
            "a paid plan without recorded human approval must yield BLOCKED",
        )
    if (
        source.paid_plan_required
        and source.human_approval is not None
        and source.human_approval.decision is not HumanApprovalDecision.APPROVED
        and source.verdict != "BLOCKED"
    ):
        _reject(
            MatrixReason.PAID_PLAN_UNAPPROVED,
            f"{path}.human_approval.decision",
            "a paid plan must have an explicit APPROVED decision before it is feasible",
        )


def parse_source_matrix(raw: bytes) -> SourceMatrix:
    """Parse exact source-matrix bytes with fail-closed strictness."""

    label = "source_matrix"
    payload = _decode(raw, label=label)
    record = _strict_object(
        payload, path=label, fields=_TOP_LEVEL_FIELDS, reason=MatrixReason.MALFORMED_VALUE
    )
    schema = _text(record["schema"], path=f"{label}.schema")
    if schema != SCHEMA_ID:
        _reject(
            MatrixReason.UNSUPPORTED_SCHEMA, f"{label}.schema", f"unsupported schema '{schema}'"
        )
    schema_version = record["schema_version"]
    if isinstance(schema_version, bool) or schema_version != 1:
        _reject(
            MatrixReason.UNSUPPORTED_SCHEMA, f"{label}.schema_version", "unsupported schema version"
        )
    matrix_id = _identifier(record["matrix_id"], path=f"{label}.matrix_id")
    if matrix_id != MATRIX_ID:
        _reject(
            MatrixReason.UNSUPPORTED_SCHEMA,
            f"{label}.matrix_id",
            f"unsupported matrix id '{matrix_id}'",
        )
    matrix_version = _text(record["matrix_version"], path=f"{label}.matrix_version")
    decided_at = _timestamp(record["decided_at"], path=f"{label}.decided_at")
    policy_sha256 = _sha256(record["policy_sha256"], path=f"{label}.policy_sha256")
    gate_a_sha256 = _sha256(
        record["gate_a_contract_sha256"], path=f"{label}.gate_a_contract_sha256"
    )
    rights_rule = _text(record["rights_rule"], path=f"{label}.rights_rule")
    if rights_rule != RIGHTS_RULE:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            f"{label}.rights_rule",
            f"rights rule must be exactly '{RIGHTS_RULE}'",
        )
    paid_plan_policy = _text(record["paid_plan_policy"], path=f"{label}.paid_plan_policy")
    if paid_plan_policy != PAID_PLAN_POLICY:
        _reject(
            MatrixReason.MALFORMED_VALUE,
            f"{label}.paid_plan_policy",
            f"paid plan policy must be exactly '{PAID_PLAN_POLICY}'",
        )
    evidence_registry = _text(record["evidence_registry"], path=f"{label}.evidence_registry")
    categories_payload = record["categories"]
    if not isinstance(categories_payload, list):
        _reject(MatrixReason.MALFORMED_VALUE, f"{label}.categories", "expected an array")
    categories = tuple(
        _identifier(entry, path=f"{label}.categories[{index}]")
        for index, entry in enumerate(categories_payload)
    )
    if categories != REQUIRED_CATEGORY_ORDER:
        _reject(
            MatrixReason.MATRIX_INCOMPLETE,
            f"{label}.categories",
            "categories must cover exactly the nine frozen issue categories in order",
        )
    bundles = _string_array(record["bundles"], path=f"{label}.bundles", allow_empty=True)
    for index, bundle in enumerate(bundles):
        _identifier(bundle, path=f"{label}.bundles[{index}]")
    sources_payload = record["sources"]
    if not isinstance(sources_payload, list) or not sources_payload:
        _reject(MatrixReason.MATRIX_INCOMPLETE, f"{label}.sources", "sources must not be empty")
    bundle_set = frozenset(bundles)
    category_set = frozenset(categories)
    sources = tuple(
        _parse_source(
            entry,
            path=f"{label}.sources[{index}]",
            categories=category_set,
            bundles=bundle_set,
            matrix_decided_at=decided_at,
        )
        for index, entry in enumerate(sources_payload)
    )
    seen_ids: set[str] = set()
    for source in sources:
        if source.source_id in seen_ids:
            _reject(
                MatrixReason.DUPLICATE_FIELD,
                f"{label}.sources",
                f"duplicate source_id '{source.source_id}'",
            )
        seen_ids.add(source.source_id)
    covered = {source.category for source in sources}
    missing = category_set - covered
    if missing:
        _reject(
            MatrixReason.MATRIX_INCOMPLETE,
            f"{label}.sources",
            f"categories without any source record: {sorted(missing)}",
        )
    return SourceMatrix(
        matrix_id=matrix_id,
        matrix_version=matrix_version,
        decided_at=decided_at,
        policy_sha256=policy_sha256,
        gate_a_contract_sha256=gate_a_sha256,
        evidence_registry=evidence_registry,
        categories=categories,
        bundles=bundles,
        sources=sources,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def source_matrix_bytes() -> bytes:
    """Return the canonical packaged source-matrix bytes."""

    return resources.files("esscher.contracts").joinpath(MATRIX_RESOURCE_NAME).read_bytes()


def source_matrix_sha256() -> str:
    """Verify the packaged matrix digest and return it."""

    actual = hashlib.sha256(source_matrix_bytes()).hexdigest()
    if actual != SOURCE_MATRIX_V1_SHA256:
        _reject(
            MatrixReason.DIGEST_MISMATCH,
            MATRIX_RESOURCE_NAME,
            f"packaged matrix digest {actual} != frozen digest {SOURCE_MATRIX_V1_SHA256}",
        )
    return actual


def load_source_matrix() -> SourceMatrix:
    """Load, authenticate, and parse the packaged source matrix."""

    raw = source_matrix_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_MATRIX_V1_SHA256:
        _reject(
            MatrixReason.DIGEST_MISMATCH,
            MATRIX_RESOURCE_NAME,
            f"packaged matrix digest {digest} != frozen digest {SOURCE_MATRIX_V1_SHA256}",
        )
    return parse_source_matrix(raw)


def verify_upstream_bindings(
    matrix: SourceMatrix, *, policy_bytes: bytes, gate_a_contract_bytes: bytes
) -> None:
    """Fail closed when the matrix no longer binds the frozen upstream contracts."""

    policy_sha = hashlib.sha256(policy_bytes).hexdigest()
    if policy_sha != matrix.policy_sha256:
        _reject(
            MatrixReason.UPSTREAM_CONTRACT_DRIFT,
            "policy_sha256",
            f"accepted event policy digest {policy_sha} != bound digest {matrix.policy_sha256}",
        )
    gate_a_sha = hashlib.sha256(gate_a_contract_bytes).hexdigest()
    if gate_a_sha != matrix.gate_a_contract_sha256:
        _reject(
            MatrixReason.UPSTREAM_CONTRACT_DRIFT,
            "gate_a_contract_sha256",
            f"Gate A contract digest {gate_a_sha} != bound digest {matrix.gate_a_contract_sha256}",
        )


@dataclass(frozen=True)
class ClassRightsDecision:
    source_class: str
    source_id: str
    verdict: str
    conditions: tuple[str, ...]
    limitations: tuple[str, ...]


def evaluate_capture_rights(
    matrix: SourceMatrix,
    required_classes: tuple[str, ...] | list[str],
    *,
    satisfied_conditions: frozenset[str],
) -> tuple[ClassRightsDecision, ...]:
    """Decide whether every required source class may feed a capture.

    A class passes only when at least one covering source is not BLOCKED and
    every condition on the chosen source is satisfied by the caller. Anything
    else fails closed with a stable machine reason.
    """

    decisions: list[ClassRightsDecision] = []
    for source_class in required_classes:
        covering = matrix.sources_for_class(source_class)
        if not covering:
            _reject(
                MatrixReason.MATRIX_INCOMPLETE,
                source_class,
                "no matrix source covers this required source class",
            )
        ranked = sorted(
            covering,
            key=lambda source: (
                {"FEASIBLE": 0, "FEASIBLE_WITH_LIMITATIONS": 1, "BLOCKED": 2}[source.verdict],
                source.source_id,
            ),
        )
        chosen: SourceRecord | None = None
        blocked_only = True
        unmet: tuple[str, ...] = ()
        for source in ranked:
            if source.blocked:
                continue
            blocked_only = False
            missing = tuple(
                condition
                for condition in source.conditions
                if condition not in satisfied_conditions
            )
            if not missing:
                chosen = source
                break
            unmet = missing
        if blocked_only:
            _reject(
                MatrixReason.SOURCE_RIGHTS_BLOCKED,
                source_class,
                "every covering source is BLOCKED; rights ambiguity and unapproved"
                " paid plans stay blocked",
            )
        if chosen is None:
            _reject(
                MatrixReason.SOURCE_RIGHTS_LIMITATION_UNMET,
                source_class,
                f"unmet conditions on the best non-blocked source: {list(unmet)}",
            )
        decisions.append(
            ClassRightsDecision(
                source_class=source_class,
                source_id=chosen.source_id,
                verdict=chosen.verdict,
                conditions=chosen.conditions,
                limitations=chosen.limitations,
            )
        )
    return tuple(decisions)
