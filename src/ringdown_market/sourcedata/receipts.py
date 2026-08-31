"""Canonical source-provenance and corporate-action receipts.

Receipts are deterministic strict-JSON artifacts. They record publisher time,
retrieval time, content identity, entitlement, and redistribution status as
separate fields, and they never carry raw licensed payloads.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final

from ringdown_market.sourcedata._checks import require_identifier, require_sha256, require_utc
from ringdown_market.sourcedata.interfaces import CorporateAction, SourceProvenance
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

SOURCE_RECEIPT_SCHEMA: Final = "esscher.source_receipt"
CORPORATE_ACTION_RECEIPT_SCHEMA: Final = "esscher.corporate_action_receipt"
_RECEIPT_SCHEMA_VERSION: Final = 1
_ENTITLEMENTS: Final = frozenset({"ENTITLED", "PUBLIC", "UNVERIFIED"})
_REDISTRIBUTION: Final = frozenset({"REDISTRIBUTABLE", "NON_REDISTRIBUTABLE", "UNKNOWN"})
_ACTION_TYPES: Final = frozenset({"SPLIT", "CASH_DIVIDEND", "SYMBOL_CHANGE"})

_SOURCE_RECEIPT_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "receipt_id",
        "source_class",
        "publisher",
        "content_sha256",
        "published_at",
        "published_at_precision",
        "retrieved_at",
        "entitlement",
        "redistribution_status",
        "limitations",
    }
)
_ACTION_RECEIPT_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "receipt_id",
        "ticker",
        "action_type",
        "ex_date",
        "ratio_numerator",
        "ratio_denominator",
        "symbol_from",
        "symbol_to",
        "source_receipt_id",
        "content_sha256",
    }
)


class _DuplicateFieldError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise CollectorRejected(
        CollectorReason.UNSUPPORTED_INPUT,
        "receipt",
        f"non-finite JSON constant {value} is forbidden",
    )


def _decode(raw: bytes, *, path: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "receipt input must be immutable bytes"
        )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except _DuplicateFieldError as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, f"duplicate JSON field {error}"
        ) from None
    except CollectorRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, f"invalid strict JSON: {error}"
        ) from None
    if not isinstance(value, dict):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "receipt root must be an object"
        )
    return value


def _strict_fields(payload: Mapping[str, object], fields: frozenset[str], path: str) -> None:
    actual = frozenset(payload)
    missing = sorted(fields - actual)
    unknown = sorted(actual - fields)
    if missing:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, f"missing fields: {', '.join(missing)}"
        )
    if unknown:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, f"unknown fields: {', '.join(unknown)}"
        )


def _text(value: object, *, path: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "must be non-empty bounded text"
        )
    return value


def _optional_text(value: object, *, path: str, maximum: int = 256) -> str | None:
    if value is None:
        return None
    return _text(value, path=path, maximum=maximum)


def _timestamp_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _timestamp(value: object, *, path: str, nullable: bool = False) -> datetime | None:
    if value is None:
        if nullable:
            return None
        raise CollectorRejected(CollectorReason.UNSUPPORTED_INPUT, path, "timestamp is required")
    if not isinstance(value, str):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "timestamp must be ISO-8601 text"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CollectorRejected(CollectorReason.UNSUPPORTED_INPUT, path, str(error)) from None
    if parsed.tzinfo != UTC:
        raise CollectorRejected(CollectorReason.UNSUPPORTED_INPUT, path, "timestamp must be UTC")
    return parsed


def _date_text(value: date) -> str:
    return value.isoformat()


def _date(value: object, *, path: str) -> date:
    if not isinstance(value, str):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "date must be ISO-8601 text"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CollectorRejected(CollectorReason.UNSUPPORTED_INPUT, path, str(error)) from None


def _optional_int(value: object, *, path: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "must be an integer or null"
        )
    return value


def _string_list(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CollectorRejected(CollectorReason.UNSUPPORTED_INPUT, path, "must be a list")
    result = tuple(_text(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    if result != tuple(sorted(set(result))):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, path, "must be sorted and unique"
        )
    return result


@dataclass(frozen=True, slots=True)
class SourceReceipt:
    """One canonical provenance receipt bound to exact source content."""

    receipt_id: str
    source_class: str
    publisher: str
    content_sha256: str
    published_at: datetime | None
    published_at_precision: str
    retrieved_at: datetime
    entitlement: str
    redistribution_status: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        if not self.source_class or self.source_class != self.source_class.upper():
            raise ValueError("source_class must be non-empty uppercase text")
        if self.entitlement not in _ENTITLEMENTS:
            raise ValueError("entitlement is not a registered value")
        if self.redistribution_status not in _REDISTRIBUTION:
            raise ValueError("redistribution_status is not a registered value")
        require_sha256(self.content_sha256, "content_sha256")
        if self.published_at is not None:
            require_utc(self.published_at, "published_at")
        require_utc(self.retrieved_at, "retrieved_at")

    @classmethod
    def from_provenance(cls, receipt_id: str, provenance: SourceProvenance) -> SourceReceipt:
        return cls(
            receipt_id=receipt_id,
            source_class=provenance.source_class,
            publisher=provenance.publisher,
            content_sha256=provenance.content_sha256,
            published_at=provenance.published_at,
            published_at_precision=provenance.published_at_precision,
            retrieved_at=provenance.retrieved_at,
            entitlement=provenance.entitlement,
            redistribution_status=provenance.redistribution_status,
            limitations=provenance.limitations,
        )


def source_receipt_payload(value: SourceReceipt) -> dict[str, object]:
    """Return the single versioned serialization for one source receipt."""

    return {
        "schema": SOURCE_RECEIPT_SCHEMA,
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "receipt_id": value.receipt_id,
        "source_class": value.source_class,
        "publisher": value.publisher,
        "content_sha256": value.content_sha256,
        "published_at": None if value.published_at is None else _timestamp_text(value.published_at),
        "published_at_precision": value.published_at_precision,
        "retrieved_at": _timestamp_text(value.retrieved_at),
        "entitlement": value.entitlement,
        "redistribution_status": value.redistribution_status,
        "limitations": list(value.limitations),
    }


def source_receipt_bytes(value: SourceReceipt) -> bytes:
    """Serialize one source receipt to deterministic canonical bytes."""

    return canonical_json_bytes(source_receipt_payload(value))


def source_receipt_sha256(value: SourceReceipt) -> str:
    return sha256_bytes(source_receipt_bytes(value))


def parse_source_receipt(raw: bytes) -> SourceReceipt:
    """Strictly parse canonical ``esscher.source_receipt/v1`` bytes."""

    payload = _decode(raw, path="source_receipt")
    _strict_fields(payload, _SOURCE_RECEIPT_FIELDS, "source_receipt")
    if payload["schema"] != SOURCE_RECEIPT_SCHEMA:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, "source_receipt.schema", "unsupported schema"
        )
    if payload["schema_version"] != _RECEIPT_SCHEMA_VERSION:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "source_receipt.schema_version",
            "unsupported schema version",
        )
    try:
        result = SourceReceipt(
            receipt_id=_text(payload["receipt_id"], path="source_receipt.receipt_id"),
            source_class=_text(payload["source_class"], path="source_receipt.source_class"),
            publisher=_text(payload["publisher"], path="source_receipt.publisher"),
            content_sha256=_text(payload["content_sha256"], path="source_receipt.content_sha256"),
            published_at=_timestamp(
                payload["published_at"], path="source_receipt.published_at", nullable=True
            ),
            published_at_precision=_text(
                payload["published_at_precision"], path="source_receipt.published_at_precision"
            ),
            retrieved_at=_timestamp(payload["retrieved_at"], path="source_receipt.retrieved_at"),
            entitlement=_text(payload["entitlement"], path="source_receipt.entitlement"),
            redistribution_status=_text(
                payload["redistribution_status"], path="source_receipt.redistribution_status"
            ),
            limitations=_string_list(payload["limitations"], path="source_receipt.limitations"),
        )
    except ValueError as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, "source_receipt", str(error)
        ) from None
    if source_receipt_bytes(result) != raw:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "source_receipt",
            "receipt bytes are not canonical",
        )
    return result


@dataclass(frozen=True, slots=True)
class CorporateActionReceipt:
    """One canonical corporate-action receipt bound to its source receipt."""

    receipt_id: str
    ticker: str
    action_type: str
    ex_date: date
    ratio_numerator: int | None
    ratio_denominator: int | None
    symbol_from: str | None
    symbol_to: str | None
    source_receipt_id: str
    content_sha256: str

    def __post_init__(self) -> None:
        require_identifier(self.receipt_id, "receipt_id")
        if self.ticker != self.ticker.strip().upper() or not self.ticker:
            raise ValueError("ticker must be normalized uppercase text")
        if self.action_type not in _ACTION_TYPES:
            raise ValueError("action_type is not a registered corporate action")
        if self.action_type == "SPLIT":
            if (
                self.ratio_numerator is None
                or self.ratio_denominator is None
                or self.ratio_numerator <= 0
                or self.ratio_denominator <= 0
            ):
                raise ValueError("splits require positive ratio terms")
        elif self.ratio_numerator is not None or self.ratio_denominator is not None:
            raise ValueError("only splits carry ratio terms")
        if self.action_type == "SYMBOL_CHANGE":
            if not self.symbol_from or not self.symbol_to:
                raise ValueError("symbol changes require both symbols")
        elif self.symbol_from is not None or self.symbol_to is not None:
            raise ValueError("only symbol changes carry symbol terms")
        require_identifier(self.source_receipt_id, "source_receipt_id")
        require_sha256(self.content_sha256, "content_sha256")

    @classmethod
    def from_action(
        cls, receipt_id: str, action: CorporateAction, source_receipt_id: str
    ) -> CorporateActionReceipt:
        return cls(
            receipt_id=receipt_id,
            ticker=action.ticker,
            action_type=action.action_type,
            ex_date=action.ex_date,
            ratio_numerator=action.ratio_numerator,
            ratio_denominator=action.ratio_denominator,
            symbol_from=action.symbol_from,
            symbol_to=action.symbol_to,
            source_receipt_id=source_receipt_id,
            content_sha256=action.provenance.content_sha256,
        )


def corporate_action_receipt_payload(value: CorporateActionReceipt) -> dict[str, object]:
    """Return the single versioned serialization for one corporate-action receipt."""

    return {
        "schema": CORPORATE_ACTION_RECEIPT_SCHEMA,
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "receipt_id": value.receipt_id,
        "ticker": value.ticker,
        "action_type": value.action_type,
        "ex_date": _date_text(value.ex_date),
        "ratio_numerator": value.ratio_numerator,
        "ratio_denominator": value.ratio_denominator,
        "symbol_from": value.symbol_from,
        "symbol_to": value.symbol_to,
        "source_receipt_id": value.source_receipt_id,
        "content_sha256": value.content_sha256,
    }


def corporate_action_receipt_bytes(value: CorporateActionReceipt) -> bytes:
    """Serialize one corporate-action receipt to deterministic canonical bytes."""

    return canonical_json_bytes(corporate_action_receipt_payload(value))


def corporate_action_receipt_sha256(value: CorporateActionReceipt) -> str:
    return sha256_bytes(corporate_action_receipt_bytes(value))


def parse_corporate_action_receipt(raw: bytes) -> CorporateActionReceipt:
    """Strictly parse canonical ``esscher.corporate_action_receipt/v1`` bytes."""

    payload = _decode(raw, path="corporate_action_receipt")
    _strict_fields(payload, _ACTION_RECEIPT_FIELDS, "corporate_action_receipt")
    if payload["schema"] != CORPORATE_ACTION_RECEIPT_SCHEMA:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "corporate_action_receipt.schema",
            "unsupported schema",
        )
    if payload["schema_version"] != _RECEIPT_SCHEMA_VERSION:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "corporate_action_receipt.schema_version",
            "unsupported schema version",
        )
    try:
        result = CorporateActionReceipt(
            receipt_id=_text(payload["receipt_id"], path="corporate_action_receipt.receipt_id"),
            ticker=_text(payload["ticker"], path="corporate_action_receipt.ticker"),
            action_type=_text(payload["action_type"], path="corporate_action_receipt.action_type"),
            ex_date=_date(payload["ex_date"], path="corporate_action_receipt.ex_date"),
            ratio_numerator=_optional_int(
                payload["ratio_numerator"], path="corporate_action_receipt.ratio_numerator"
            ),
            ratio_denominator=_optional_int(
                payload["ratio_denominator"], path="corporate_action_receipt.ratio_denominator"
            ),
            symbol_from=_optional_text(
                payload["symbol_from"], path="corporate_action_receipt.symbol_from"
            ),
            symbol_to=_optional_text(
                payload["symbol_to"], path="corporate_action_receipt.symbol_to"
            ),
            source_receipt_id=_text(
                payload["source_receipt_id"], path="corporate_action_receipt.source_receipt_id"
            ),
            content_sha256=_text(
                payload["content_sha256"], path="corporate_action_receipt.content_sha256"
            ),
        )
    except ValueError as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, "corporate_action_receipt", str(error)
        ) from None
    if corporate_action_receipt_bytes(result) != raw:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "corporate_action_receipt",
            "receipt bytes are not canonical",
        )
    return result
