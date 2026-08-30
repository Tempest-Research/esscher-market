"""Read-only source provenance contracts for Esscher strategy snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import isfinite

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_PRICE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$")

ADJUSTMENT_POLICY_V1 = "SPLIT_AND_DIVIDEND_ADJUSTED_V1"
HASH_REPRESENTATION = "raw_bytes"


class ProvenanceRejected(ValueError):
    """Raised when a source record fails the provenance contract."""


class RedistributionStatus(StrEnum):
    """Explicit publication boundary for captured source bytes."""

    PUBLIC_BYTES_ALLOWED = "PUBLIC_BYTES_ALLOWED"
    METADATA_AND_HASH_ONLY = "METADATA_AND_HASH_ONLY"
    UNAVAILABLE_NOT_PERMITTED = "UNAVAILABLE_NOT_PERMITTED"


class EvidenceSourceKind(StrEnum):
    """Permitted primary evidence source classes for strategy snapshots."""

    ISSUER_PRIMARY = "ISSUER_PRIMARY"
    SEC_OFFICIAL = "SEC_OFFICIAL"


class PublishedAtType(StrEnum):
    """Typed meaning of an evidence publication instant."""

    ISSUER_RELEASE_TIMESTAMP = "issuer_release_timestamp"
    OFFICIAL_DISSEMINATION_TIMESTAMP = "official_dissemination_timestamp"


class CorporateActionType(StrEnum):
    """Corporate actions that must remain explicit in adjusted series."""

    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"


def _require_aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProvenanceRejected(f"{field} must be timezone-aware")


def _require_second_precision(value: datetime, field: str) -> None:
    if value.microsecond != 0:
        raise ProvenanceRejected(f"{field} must use second precision")


def _require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or not _SHA256.match(value):
        raise ProvenanceRejected(f"{field} must be a sha256 hex digest")


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProvenanceRejected(f"{field} must be non-empty text")


def _require_price(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal):
        raise ProvenanceRejected(f"{field} must be a Decimal")
    if not value.is_finite() or value <= 0:
        raise ProvenanceRejected(f"{field} must be a positive finite decimal")


def parse_price(value: str, field: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_PRICE.match(value):
        raise ProvenanceRejected(f"{field} must be canonical positive decimal text")
    try:
        price = Decimal(value)
    except InvalidOperation:
        raise ProvenanceRejected(f"{field} is not a valid decimal") from None
    _require_price(price, field)
    return price


def utc_second(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    """One timestamped primary evidence record with entitlement provenance."""

    evidence_id: str
    event_id: str
    issuer: str
    source_kind: EvidenceSourceKind
    source_url: str
    publisher: str
    published_at: datetime
    published_at_type: PublishedAtType
    published_at_precision: str
    accepted_at: datetime | None
    retrieved_at: datetime
    content_sha256: str
    entitlement_note: str
    redistribution: RedistributionStatus
    raw_bytes: bytes | None

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        _require_text(self.event_id, "event_id")
        _require_text(self.issuer, "issuer")
        _require_text(self.source_url, "source_url")
        _require_text(self.publisher, "publisher")
        _require_text(self.entitlement_note, "entitlement_note")
        _require_aware(self.published_at, "published_at")
        _require_second_precision(self.published_at, "published_at")
        _require_aware(self.retrieved_at, "retrieved_at")
        _require_second_precision(self.retrieved_at, "retrieved_at")
        if self.accepted_at is not None:
            _require_aware(self.accepted_at, "accepted_at")
            _require_second_precision(self.accepted_at, "accepted_at")
        if self.published_at_precision not in {"second", "minute"}:
            raise ProvenanceRejected("published_at_precision must be second or minute")
        _require_sha256(self.content_sha256, "content_sha256")
        if self.redistribution is not RedistributionStatus.PUBLIC_BYTES_ALLOWED:
            if self.raw_bytes is not None:
                raise ProvenanceRejected(
                    "raw bytes may not be retained when redistribution is not permitted"
                )
        elif self.raw_bytes is None:
            raise ProvenanceRejected("permitted raw bytes must accompany public-bytes evidence")


@dataclass(frozen=True, slots=True)
class BarObservation:
    """One synchronized adjusted-bar observation with its raw observation time."""

    symbol: str
    at: datetime
    price: Decimal
    raw_observed_at: datetime
    source_id: str
    adjustment: str

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_aware(self.at, "bar timestamp")
        _require_second_precision(self.at, "bar timestamp")
        _require_price(self.price, "price")
        _require_aware(self.raw_observed_at, "raw_observed_at")
        _require_second_precision(self.raw_observed_at, "raw_observed_at")
        _require_text(self.source_id, "source_id")
        if self.adjustment != ADJUSTMENT_POLICY_V1:
            raise ProvenanceRejected("adjustment policy must be the frozen v1 policy")


@dataclass(frozen=True, slots=True)
class CorporateActionReceipt:
    """One explicit split, dividend, or symbol-change receipt for adjusted series."""

    action_type: CorporateActionType
    effective_date: date
    symbol: str
    factor: Decimal | None
    to_symbol: str | None
    source_id: str

    def __post_init__(self) -> None:
        _require_text(self.symbol, "symbol")
        _require_text(self.source_id, "source_id")
        if self.action_type is CorporateActionType.SPLIT:
            if self.factor is None or not self.factor.is_finite() or self.factor <= 0:
                raise ProvenanceRejected("split receipts require a positive factor")
        else:
            if self.factor is not None:
                raise ProvenanceRejected("only split receipts carry a factor")
        if self.action_type is CorporateActionType.SYMBOL_CHANGE and (
            not self.to_symbol or not self.to_symbol.strip()
        ):
            raise ProvenanceRejected("symbol-change receipts require the destination symbol")


@dataclass(frozen=True, slots=True)
class EstimationPoint:
    """One synchronized pre-cutoff return triple used by the frozen beta policy."""

    at: datetime
    stock_return: float
    market_return: float
    sector_return: float

    def __post_init__(self) -> None:
        _require_aware(self.at, "estimation timestamp")
        _require_second_precision(self.at, "estimation timestamp")
        for field in ("stock_return", "market_return", "sector_return"):
            value = getattr(self, field)
            if not isinstance(value, float) or not isfinite(value):
                raise ProvenanceRejected(f"{field} must be a finite float")
