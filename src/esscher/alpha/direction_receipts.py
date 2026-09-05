"""Immutable V2 direction receipts consumed by the Q-FAST shadow runner.

A direction receipt is the strict, attributable record of one bounded
directional decision for one frozen panel event.  Receipts are inputs to
evaluation only: they carry no outcome, no order, and no broker authority.
Synthetic receipts must declare ``NOT_ALPHA_EVIDENCE`` and can never become
candidate evidence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Final, NoReturn

from .models import Direction

DIRECTION_RECEIPT_SCHEMA: Final = "esscher.direction_receipt"
SCHEMA_VERSION: Final = 2

_REASON_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


class ProducerKind(StrEnum):
    """How the directional decision was produced."""

    ROUTE_BOUND = "ROUTE_BOUND"
    SYNTHETIC = "SYNTHETIC"


class DirectionReceiptReason(StrEnum):
    """Machine-readable fail-closed reasons for the receipt contract."""

    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MALFORMED_VALUE = "MALFORMED_VALUE"
    POINT_IN_TIME_VIOLATION = "POINT_IN_TIME_VIOLATION"
    CLASSIFICATION_MISSING = "CLASSIFICATION_MISSING"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"


class DirectionReceiptRejected(ValueError):
    """Raised when direction receipt bytes fail the frozen contract."""

    def __init__(self, reason: DirectionReceiptReason, path: str, detail: str) -> None:
        super().__init__(f"{reason.value} at {path}: {detail}")
        self.reason = reason
        self.path = path
        self.detail = detail


def _reject(reason: DirectionReceiptReason, path: str, detail: str) -> NoReturn:
    raise DirectionReceiptRejected(reason, path, detail)


_RECEIPT_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "event_id",
        "candidate_id",
        "direction",
        "reason_codes",
        "decision_cutoff_at",
        "latest_evidence_at",
        "feature_snapshot_at",
        "market_beta",
        "sector_beta",
        "price_only_score",
        "fundamental_score",
        "numeric_score",
        "producer_kind",
        "route_sha256",
        "prompt_sha256",
        "model_config_sha256",
        "classification",
        "produced_at",
        "decision_artifact_sha256",
        "limitations",
    }
)


class _DuplicateFieldError(ValueError):
    pass


def _decode(raw: bytes, *, path: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, "receipt must be immutable bytes")
    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateFieldError) as error:
        reason = (
            DirectionReceiptReason.DUPLICATE_FIELD
            if isinstance(error, _DuplicateFieldError)
            else DirectionReceiptReason.MALFORMED_VALUE
        )
        _reject(reason, path, "receipt must be strict UTF-8 JSON with unique keys")
    if not isinstance(decoded, Mapping):
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, "receipt must be a JSON object")
    return decoded


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in pairs:
        if key in record:
            raise _DuplicateFieldError(key)
        record[key] = value
    return record


def _text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, "must be non-empty text")
    return value


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, "must be a lowercase SHA-256")
    return value


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, "must end in Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, str(error))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, "must be UTC")
    return parsed


def _finite(value: object, *, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, "must be a finite number")
    return float(value)


def _string_list(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _reject(DirectionReceiptReason.MALFORMED_VALUE, path, "must be a list of strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class DirectionReceipt:
    """One immutable bounded directional decision for one frozen event."""

    event_id: str
    candidate_id: str
    direction: Direction
    reason_codes: tuple[str, ...]
    decision_cutoff_at: datetime
    latest_evidence_at: datetime
    feature_snapshot_at: datetime
    market_beta: float
    sector_beta: float
    price_only_score: float
    fundamental_score: float
    numeric_score: float
    producer_kind: ProducerKind
    route_sha256: str | None
    prompt_sha256: str | None
    model_config_sha256: str | None
    classification: tuple[str, ...]
    produced_at: datetime
    decision_artifact_sha256: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.latest_evidence_at > self.decision_cutoff_at:
            raise ValueError("evidence published after the decision cutoff")
        if self.feature_snapshot_at > self.decision_cutoff_at:
            raise ValueError("feature snapshot created after the decision cutoff")
        if self.produced_at < self.decision_cutoff_at:
            raise ValueError("receipt produced before the decision cutoff")
        if (
            self.producer_kind is ProducerKind.SYNTHETIC
            and "NOT_ALPHA_EVIDENCE" not in self.classification
        ):
            raise ValueError("synthetic receipts must declare NOT_ALPHA_EVIDENCE")
        if self.producer_kind is ProducerKind.ROUTE_BOUND and not (
            self.route_sha256 and self.prompt_sha256 and self.model_config_sha256
        ):
            raise ValueError("route-bound receipts must bind route, prompt, and model config")


def direction_receipt_payload(value: DirectionReceipt) -> dict[str, object]:
    """Return the versioned JSON object for one direction receipt."""

    return {
        "schema": DIRECTION_RECEIPT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "event_id": value.event_id,
        "candidate_id": value.candidate_id,
        "direction": value.direction.value,
        "reason_codes": list(value.reason_codes),
        "decision_cutoff_at": value.decision_cutoff_at.isoformat().replace("+00:00", "Z"),
        "latest_evidence_at": value.latest_evidence_at.isoformat().replace("+00:00", "Z"),
        "feature_snapshot_at": value.feature_snapshot_at.isoformat().replace("+00:00", "Z"),
        "market_beta": value.market_beta,
        "sector_beta": value.sector_beta,
        "price_only_score": value.price_only_score,
        "fundamental_score": value.fundamental_score,
        "numeric_score": value.numeric_score,
        "producer_kind": value.producer_kind.value,
        "route_sha256": value.route_sha256,
        "prompt_sha256": value.prompt_sha256,
        "model_config_sha256": value.model_config_sha256,
        "classification": list(value.classification),
        "produced_at": value.produced_at.isoformat().replace("+00:00", "Z"),
        "decision_artifact_sha256": value.decision_artifact_sha256,
        "limitations": list(value.limitations),
    }


def direction_receipt_bytes(value: DirectionReceipt) -> bytes:
    """Serialize one direction receipt to canonical immutable bytes."""

    return json.dumps(
        direction_receipt_payload(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def parse_direction_receipt(raw: bytes, *, path: str = "direction_receipt") -> DirectionReceipt:
    """Strictly parse canonical ``esscher.direction_receipt/v2`` bytes."""

    record = _decode(raw, path=path)
    extra = set(record) - _RECEIPT_FIELDS
    if extra:
        _reject(
            DirectionReceiptReason.UNKNOWN_FIELD,
            path,
            f"unknown fields: {', '.join(sorted(extra))}",
        )
    missing = _RECEIPT_FIELDS - set(record)
    if missing:
        _reject(
            DirectionReceiptReason.MISSING_FIELD,
            path,
            f"missing fields: {', '.join(sorted(missing))}",
        )
    if record["schema"] != DIRECTION_RECEIPT_SCHEMA or record["schema_version"] != SCHEMA_VERSION:
        _reject(
            DirectionReceiptReason.UNSUPPORTED_SCHEMA,
            path,
            "unsupported direction receipt schema or version",
        )
    reason_codes = _string_list(record["reason_codes"], path=f"{path}.reason_codes")
    if tuple(sorted(reason_codes)) != reason_codes or any(
        _REASON_CODE.fullmatch(code) is None for code in reason_codes
    ):
        _reject(
            DirectionReceiptReason.MALFORMED_VALUE,
            f"{path}.reason_codes",
            "must be sorted stable uppercase reason codes",
        )
    classification = _string_list(record["classification"], path=f"{path}.classification")
    if tuple(sorted(classification)) != classification or not classification:
        _reject(
            DirectionReceiptReason.CLASSIFICATION_MISSING,
            f"{path}.classification",
            "must be a sorted non-empty classification list",
        )
    limitations = _string_list(record["limitations"], path=f"{path}.limitations")
    if tuple(sorted(limitations)) != limitations:
        _reject(
            DirectionReceiptReason.MALFORMED_VALUE,
            f"{path}.limitations",
            "must be sorted limitation strings",
        )
    try:
        direction = Direction(_text(record["direction"], path=f"{path}.direction"))
    except ValueError as error:
        _reject(DirectionReceiptReason.MALFORMED_VALUE, f"{path}.direction", str(error))
    producer_kind = ProducerKind(_text(record["producer_kind"], path=f"{path}.producer_kind"))
    optional_hashes = {}
    for field in ("route_sha256", "prompt_sha256", "model_config_sha256"):
        value = record[field]
        optional_hashes[field] = None if value is None else _sha256(value, path=f"{path}.{field}")
    try:
        return DirectionReceipt(
            event_id=_text(record["event_id"], path=f"{path}.event_id"),
            candidate_id=_text(record["candidate_id"], path=f"{path}.candidate_id"),
            direction=direction,
            reason_codes=reason_codes,
            decision_cutoff_at=_timestamp(
                record["decision_cutoff_at"], path=f"{path}.decision_cutoff_at"
            ),
            latest_evidence_at=_timestamp(
                record["latest_evidence_at"], path=f"{path}.latest_evidence_at"
            ),
            feature_snapshot_at=_timestamp(
                record["feature_snapshot_at"], path=f"{path}.feature_snapshot_at"
            ),
            market_beta=_finite(record["market_beta"], path=f"{path}.market_beta"),
            sector_beta=_finite(record["sector_beta"], path=f"{path}.sector_beta"),
            price_only_score=_finite(record["price_only_score"], path=f"{path}.price_only_score"),
            fundamental_score=_finite(
                record["fundamental_score"], path=f"{path}.fundamental_score"
            ),
            numeric_score=_finite(record["numeric_score"], path=f"{path}.numeric_score"),
            producer_kind=producer_kind,
            route_sha256=optional_hashes["route_sha256"],
            prompt_sha256=optional_hashes["prompt_sha256"],
            model_config_sha256=optional_hashes["model_config_sha256"],
            classification=classification,
            produced_at=_timestamp(record["produced_at"], path=f"{path}.produced_at"),
            decision_artifact_sha256=_sha256(
                record["decision_artifact_sha256"], path=f"{path}.decision_artifact_sha256"
            ),
            limitations=limitations,
        )
    except ValueError as error:
        _reject(DirectionReceiptReason.POINT_IN_TIME_VIOLATION, path, str(error))


def parse_direction_receipt_set(
    raws: Sequence[bytes], *, path: str = "direction_receipts"
) -> dict[str, DirectionReceipt]:
    """Parse one receipt per event or fail closed on duplicates."""

    receipts: dict[str, DirectionReceipt] = {}
    for index, raw in enumerate(raws):
        receipt = parse_direction_receipt(raw, path=f"{path}[{index}]")
        if receipt.event_id in receipts:
            _reject(
                DirectionReceiptReason.DUPLICATE_EVENT,
                f"{path}[{index}]",
                f"duplicate direction receipt for event {receipt.event_id}",
            )
        receipts[receipt.event_id] = receipt
    return receipts
