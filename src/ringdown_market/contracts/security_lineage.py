"""Point-in-time security master and corporate-action lineage contract.

Issue #42 makes issuer, security, listing, and event identity reconstructible
at every decision cutoff without depending on the current ticker. Identity is
CIK-rooted: an EDGAR Central Index Key is permanent and never reused, while
tickers are attributes that change, lapse, and get reused. Every identity
link, listing period, and corporate action carries provenance, and missing or
contradictory lineage rejects the candidate with a stable reason code. The
contract grants no collection, trading, or publication authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from importlib import resources
from typing import Final, NoReturn

SCHEMA_ID: Final = "esscher.security_lineage"
LINEAGE_RESOURCE_NAME: Final = "policies/security_lineage_v1.json"
LINEAGE_ID: Final = "ESSCHER_SECURITY_LINEAGE_V1"
# Updated only when the canonical lineage bytes are intentionally amended.
SECURITY_LINEAGE_V1_SHA256: Final = (
    "b400453a62ced05dacaa338dd59b90bceeba04853d9aef572ebfbcd16cb97ff5"
)
IDENTITY_RULE: Final = "CIK_ROOTED_IDENTITY"

ACTION_TYPES: Final = frozenset(
    {"SPLIT", "CASH_DIVIDEND", "SYMBOL_CHANGE", "MERGER", "SPINOFF", "OPTION_ADJUSTMENT"}
)
PROVENANCE_SOURCE_CLASSES: Final = frozenset(
    {"SEC_EDGAR", "OCC_INFO_MEMO", "EXCHANGE_OFFICIAL_RECORD", "SYNTHETIC_FIXTURE"}
)
PRICE_ADJUSTING_ACTIONS: Final = ("SPLIT",)
RECORD_ONLY_ACTIONS: Final = ("CASH_DIVIDEND",)
OPTION_ADJUSTMENT_AUTHORITY: Final = "OCC_INFO_MEMO"
CROSS_SERIES_RULE: Final = "ISSUER_ACTIONS_NEVER_ADJUST_MARKET_OR_SECTOR_SERIES"

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_CIK: Final = re.compile(r"^[0-9]{10}$")
_TICKER: Final = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
_IDENTIFIER: Final = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,63}$")
_DATE: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_UTC_TIMESTAMP: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"
)
_MIC: Final = re.compile(r"^[A-Z]{4}$")

_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "lineage_id",
        "lineage_version",
        "decided_at",
        "policy_sha256",
        "source_matrix_sha256",
        "identity_rule",
        "adjustment_policy",
        "issuers",
        "securities",
        "listings",
        "actions",
        "chains",
    }
)
_ADJUSTMENT_FIELDS: Final = frozenset(
    {
        "price_adjusting_actions",
        "record_only_actions",
        "option_adjustment_authority",
        "cross_series_rule",
    }
)
_ISSUER_FIELDS: Final = frozenset({"issuer_id", "names", "tickers"})
_ISSUER_NAME_FIELDS: Final = frozenset({"name", "effective_from", "provenance"})
_ISSUER_TICKER_FIELDS: Final = frozenset(
    {"ticker", "security_id", "valid_from", "valid_to", "provenance"}
)
_SECURITY_FIELDS: Final = frozenset({"security_id", "issuer_id", "security_type", "provenance"})
_LISTING_FIELDS: Final = frozenset(
    {
        "listing_id",
        "security_id",
        "exchange_mic",
        "listed_from",
        "listed_to",
        "delisting_reason",
        "provenance",
    }
)
_ACTION_FIELDS: Final = frozenset(
    {
        "action_id",
        "issuer_id",
        "action_type",
        "ex_date",
        "ratio_numerator",
        "ratio_denominator",
        "symbol_from",
        "symbol_to",
        "successor_issuer_id",
        "memo_id",
        "provenance",
    }
)
_CHAIN_FIELDS: Final = frozenset(
    {
        "event_id",
        "issuer_id",
        "security_id",
        "listing_id",
        "ticker_at_cutoff",
        "listed_option_exists",
        "asof",
        "provenance_links",
    }
)
_PROVENANCE_FIELDS: Final = frozenset(
    {"source_class", "record_id", "retrieved_at", "content_sha256"}
)


class LineageReason(StrEnum):
    """Machine-readable fail-closed reasons for the lineage contract."""

    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MALFORMED_VALUE = "MALFORMED_VALUE"
    REFERENTIAL_INTEGRITY = "REFERENTIAL_INTEGRITY"
    LINEAGE_MISSING = "LINEAGE_MISSING"
    LINEAGE_CONFLICT = "LINEAGE_CONFLICT"
    SYMBOL_REUSE_DETECTED = "SYMBOL_REUSE_DETECTED"
    OPTION_ADJUSTMENT_CONFLICT = "OPTION_ADJUSTMENT_CONFLICT"
    OPTION_ADJUSTMENT_UNRESOLVED = "OPTION_ADJUSTMENT_UNRESOLVED"
    UPSTREAM_CONTRACT_DRIFT = "UPSTREAM_CONTRACT_DRIFT"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"


class LineageRejected(ValueError):
    """A deterministic fail-closed lineage error."""

    def __init__(self, reason: LineageReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


@dataclass(frozen=True)
class Provenance:
    source_class: str
    record_id: str
    retrieved_at: datetime
    content_sha256: str | None


@dataclass(frozen=True)
class IssuerName:
    name: str
    effective_from: datetime
    provenance: Provenance


@dataclass(frozen=True)
class IssuerTicker:
    ticker: str
    security_id: str
    valid_from: date
    valid_to: date | None
    provenance: Provenance


@dataclass(frozen=True)
class IssuerIdentity:
    issuer_id: str
    names: tuple[IssuerName, ...]
    tickers: tuple[IssuerTicker, ...]


@dataclass(frozen=True)
class SecurityIdentity:
    security_id: str
    issuer_id: str
    security_type: str
    provenance: Provenance


@dataclass(frozen=True)
class ListingRecord:
    listing_id: str
    security_id: str
    exchange_mic: str
    listed_from: date
    listed_to: date | None
    delisting_reason: str | None
    provenance: Provenance

    def active_on(self, asof: date) -> bool:
        return self.listed_from <= asof and (self.listed_to is None or asof < self.listed_to)


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    issuer_id: str
    action_type: str
    ex_date: date
    ratio_numerator: int | None
    ratio_denominator: int | None
    symbol_from: str | None
    symbol_to: str | None
    successor_issuer_id: str | None
    memo_id: str | None
    provenance: Provenance


@dataclass(frozen=True)
class LineageChain:
    event_id: str
    issuer_id: str
    security_id: str
    listing_id: str
    ticker_at_cutoff: str
    listed_option_exists: bool
    asof: datetime
    provenance_links: tuple[Provenance, ...]


@dataclass(frozen=True)
class AdjustmentPolicy:
    price_adjusting_actions: tuple[str, ...]
    record_only_actions: tuple[str, ...]
    option_adjustment_authority: str
    cross_series_rule: str


@dataclass(frozen=True)
class SecurityLineage:
    lineage_id: str
    lineage_version: str
    decided_at: datetime
    policy_sha256: str
    source_matrix_sha256: str
    adjustment_policy: AdjustmentPolicy
    issuers: tuple[IssuerIdentity, ...]
    securities: tuple[SecurityIdentity, ...]
    listings: tuple[ListingRecord, ...]
    actions: tuple[ActionRecord, ...]
    chains: tuple[LineageChain, ...]
    sha256: str

    def issuers_by_id(self) -> dict[str, IssuerIdentity]:
        return {issuer.issuer_id: issuer for issuer in self.issuers}

    def securities_by_id(self) -> dict[str, SecurityIdentity]:
        return {security.security_id: security for security in self.securities}

    def listings_by_id(self) -> dict[str, ListingRecord]:
        return {listing.listing_id: listing for listing in self.listings}

    def chains_by_event(self) -> dict[str, LineageChain]:
        return {chain.event_id: chain for chain in self.chains}

    def actions_for_issuer(self, issuer_id: str) -> tuple[ActionRecord, ...]:
        return tuple(action for action in self.actions if action.issuer_id == issuer_id)


def _reject(reason: LineageReason, path: str, detail: str) -> NoReturn:
    raise LineageRejected(reason, path, detail)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            _reject(LineageReason.DUPLICATE_FIELD, key, f"duplicate field '{key}'")
        seen.add(key)
    return dict(pairs)


def _decode(raw: bytes, *, label: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except UnicodeDecodeError as exc:
        _reject(LineageReason.MALFORMED_VALUE, label, f"not valid UTF-8: {exc}")
    except json.JSONDecodeError as exc:
        _reject(LineageReason.MALFORMED_VALUE, label, f"invalid JSON: {exc.msg}")


def _strict_object(value: object, *, path: str, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        _reject(LineageReason.MALFORMED_VALUE, path, "expected an object")
    for key in value:
        if key not in fields:
            _reject(LineageReason.UNKNOWN_FIELD, f"{path}.{key}", f"unknown field '{key}'")
    for required in fields:
        if required not in value:
            _reject(LineageReason.MISSING_FIELD, f"{path}.{required}", "missing required field")
    return value


def _text(value: object, *, path: str, nullable: bool = False) -> str | None:
    if value is None:
        if nullable:
            return None
        _reject(LineageReason.MALFORMED_VALUE, path, "value must not be null")
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        _reject(LineageReason.MALFORMED_VALUE, path, "value must be a non-empty string")
    return value


def _sha256(value: object, *, path: str, nullable: bool = False) -> str | None:
    text = _text(value, path=path, nullable=nullable)
    if text is None:
        return None
    if _SHA256.fullmatch(text) is None:
        _reject(LineageReason.MALFORMED_VALUE, path, "value must be a lowercase SHA-256 hex digest")
    return text


def _timestamp(value: object, *, path: str) -> datetime:
    text = _text(value, path=path)
    assert text is not None
    if _UTC_TIMESTAMP.fullmatch(text) is None:
        _reject(
            LineageReason.MALFORMED_VALUE,
            path,
            "value must be an ISO-8601 timestamp with explicit UTC Z or +00:00 offset",
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _reject(LineageReason.MALFORMED_VALUE, path, "value must be an ISO-8601 UTC timestamp")
    return parsed.astimezone(UTC)


def _calendar_date(value: object, *, path: str, nullable: bool = False) -> date | None:
    text = _text(value, path=path, nullable=nullable)
    if text is None:
        return None
    if _DATE.fullmatch(text) is None:
        _reject(LineageReason.MALFORMED_VALUE, path, "value must be an ISO-8601 calendar date")
    try:
        return date.fromisoformat(text)
    except ValueError:
        _reject(LineageReason.MALFORMED_VALUE, path, "value is not a valid calendar date")


def _cik(value: object, *, path: str) -> str:
    text = _text(value, path=path)
    assert text is not None
    if _CIK.fullmatch(text) is None:
        _reject(
            LineageReason.MALFORMED_VALUE, path, "issuer identity must be a zero-padded EDGAR CIK"
        )
    return text


def _ticker(value: object, *, path: str) -> str:
    text = _text(value, path=path)
    assert text is not None
    if _TICKER.fullmatch(text) is None:
        _reject(LineageReason.MALFORMED_VALUE, path, "value must be an uppercase ticker")
    return text


def _identifier(value: object, *, path: str) -> str:
    text = _text(value, path=path)
    assert text is not None
    if _IDENTIFIER.fullmatch(text) is None:
        _reject(
            LineageReason.MALFORMED_VALUE, path, "value must be an uppercase machine identifier"
        )
    return text


def _positive_int(value: object, *, path: str, nullable: bool = False) -> int | None:
    if value is None:
        if nullable:
            return None
        _reject(LineageReason.MALFORMED_VALUE, path, "value must not be null")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _reject(LineageReason.MALFORMED_VALUE, path, "value must be a positive integer")
    return value


def _parse_provenance(value: object, *, path: str) -> Provenance:
    record = _strict_object(value, path=path, fields=_PROVENANCE_FIELDS)
    source_class = _text(record["source_class"], path=f"{path}.source_class")
    if source_class not in PROVENANCE_SOURCE_CLASSES:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.source_class",
            f"unknown provenance source class '{source_class}'",
        )
    return Provenance(
        source_class=source_class,
        record_id=_text(record["record_id"], path=f"{path}.record_id"),
        retrieved_at=_timestamp(record["retrieved_at"], path=f"{path}.retrieved_at"),
        content_sha256=_sha256(
            record["content_sha256"], path=f"{path}.content_sha256", nullable=True
        ),
    )


def _parse_issuer(value: object, *, path: str) -> IssuerIdentity:
    record = _strict_object(value, path=path, fields=_ISSUER_FIELDS)
    issuer_id = _cik(record["issuer_id"], path=f"{path}.issuer_id")
    names_payload = record["names"]
    if not isinstance(names_payload, list) or not names_payload:
        _reject(LineageReason.MALFORMED_VALUE, f"{path}.names", "names must not be empty")
    names: list[IssuerName] = []
    for index, entry in enumerate(names_payload):
        item = _strict_object(entry, path=f"{path}.names[{index}]", fields=_ISSUER_NAME_FIELDS)
        names.append(
            IssuerName(
                name=_text(item["name"], path=f"{path}.names[{index}].name"),
                effective_from=_timestamp(
                    item["effective_from"], path=f"{path}.names[{index}].effective_from"
                ),
                provenance=_parse_provenance(
                    item["provenance"], path=f"{path}.names[{index}].provenance"
                ),
            )
        )
    tickers_payload = record["tickers"]
    if not isinstance(tickers_payload, list) or not tickers_payload:
        _reject(LineageReason.MALFORMED_VALUE, f"{path}.tickers", "tickers must not be empty")
    tickers: list[IssuerTicker] = []
    for index, entry in enumerate(tickers_payload):
        item = _strict_object(entry, path=f"{path}.tickers[{index}]", fields=_ISSUER_TICKER_FIELDS)
        valid_from = _calendar_date(item["valid_from"], path=f"{path}.tickers[{index}].valid_from")
        valid_to = _calendar_date(
            item["valid_to"], path=f"{path}.tickers[{index}].valid_to", nullable=True
        )
        if valid_to is not None and valid_to < valid_from:
            _reject(
                LineageReason.MALFORMED_VALUE,
                f"{path}.tickers[{index}].valid_to",
                "ticker validity must not end before it starts",
            )
        tickers.append(
            IssuerTicker(
                ticker=_ticker(item["ticker"], path=f"{path}.tickers[{index}].ticker"),
                security_id=_identifier(
                    item["security_id"], path=f"{path}.tickers[{index}].security_id"
                ),
                valid_from=valid_from,
                valid_to=valid_to,
                provenance=_parse_provenance(
                    item["provenance"], path=f"{path}.tickers[{index}].provenance"
                ),
            )
        )
    return IssuerIdentity(issuer_id=issuer_id, names=tuple(names), tickers=tuple(tickers))


def _parse_security(value: object, *, path: str) -> SecurityIdentity:
    record = _strict_object(value, path=path, fields=_SECURITY_FIELDS)
    return SecurityIdentity(
        security_id=_identifier(record["security_id"], path=f"{path}.security_id"),
        issuer_id=_cik(record["issuer_id"], path=f"{path}.issuer_id"),
        security_type=_text(record["security_type"], path=f"{path}.security_type"),
        provenance=_parse_provenance(record["provenance"], path=f"{path}.provenance"),
    )


def _parse_listing(value: object, *, path: str) -> ListingRecord:
    record = _strict_object(value, path=path, fields=_LISTING_FIELDS)
    exchange_mic = _text(record["exchange_mic"], path=f"{path}.exchange_mic")
    if _MIC.fullmatch(exchange_mic) is None:
        _reject(LineageReason.MALFORMED_VALUE, f"{path}.exchange_mic", "value must be a MIC")
    listed_from = _calendar_date(record["listed_from"], path=f"{path}.listed_from")
    listed_to = _calendar_date(record["listed_to"], path=f"{path}.listed_to", nullable=True)
    if listed_to is not None and listed_to <= listed_from:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.listed_to",
            "listing must not terminate before it starts",
        )
    delisting_reason = _text(
        record["delisting_reason"], path=f"{path}.delisting_reason", nullable=True
    )
    if listed_to is None and delisting_reason is not None:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.delisting_reason",
            "an active listing cannot carry a delisting reason",
        )
    if listed_to is not None and delisting_reason is None:
        _reject(
            LineageReason.MISSING_FIELD,
            f"{path}.delisting_reason",
            "a terminated listing requires a delisting reason",
        )
    return ListingRecord(
        listing_id=_identifier(record["listing_id"], path=f"{path}.listing_id"),
        security_id=_identifier(record["security_id"], path=f"{path}.security_id"),
        exchange_mic=exchange_mic,
        listed_from=listed_from,
        listed_to=listed_to,
        delisting_reason=delisting_reason,
        provenance=_parse_provenance(record["provenance"], path=f"{path}.provenance"),
    )


def _parse_action(value: object, *, path: str) -> ActionRecord:
    record = _strict_object(value, path=path, fields=_ACTION_FIELDS)
    action_type = _text(record["action_type"], path=f"{path}.action_type")
    if action_type not in ACTION_TYPES:
        _reject(
            LineageReason.MALFORMED_VALUE, f"{path}.action_type", f"unknown action '{action_type}'"
        )
    ratio_numerator = _positive_int(
        record["ratio_numerator"], path=f"{path}.ratio_numerator", nullable=True
    )
    ratio_denominator = _positive_int(
        record["ratio_denominator"], path=f"{path}.ratio_denominator", nullable=True
    )
    if action_type == "SPLIT":
        if ratio_numerator is None or ratio_denominator is None:
            _reject(
                LineageReason.MISSING_FIELD,
                f"{path}.ratio_numerator",
                "splits require positive ratio terms",
            )
    elif ratio_numerator is not None or ratio_denominator is not None:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.ratio_numerator",
            "only splits carry ratio terms",
        )
    symbol_from = _text(record["symbol_from"], path=f"{path}.symbol_from", nullable=True)
    symbol_to = _text(record["symbol_to"], path=f"{path}.symbol_to", nullable=True)
    if action_type == "SYMBOL_CHANGE":
        if not symbol_from or not symbol_to:
            _reject(
                LineageReason.MISSING_FIELD,
                f"{path}.symbol_from",
                "symbol changes require both symbols",
            )
        _ticker(symbol_from, path=f"{path}.symbol_from")
        _ticker(symbol_to, path=f"{path}.symbol_to")
    elif symbol_from is not None or symbol_to is not None:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.symbol_from",
            "only symbol changes carry symbol terms",
        )
    successor = record["successor_issuer_id"]
    successor_issuer_id: str | None = None
    if action_type in {"MERGER", "SPINOFF"}:
        successor_issuer_id = _cik(successor, path=f"{path}.successor_issuer_id")
    elif successor is not None:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.successor_issuer_id",
            "only mergers and spinoffs carry a successor issuer",
        )
    memo_id = _text(record["memo_id"], path=f"{path}.memo_id", nullable=True)
    if action_type == "OPTION_ADJUSTMENT" and memo_id is None:
        _reject(
            LineageReason.MISSING_FIELD,
            f"{path}.memo_id",
            "option adjustments require the OCC info-memo identifier",
        )
    return ActionRecord(
        action_id=_identifier(record["action_id"], path=f"{path}.action_id"),
        issuer_id=_cik(record["issuer_id"], path=f"{path}.issuer_id"),
        action_type=action_type,
        ex_date=_calendar_date(record["ex_date"], path=f"{path}.ex_date"),
        ratio_numerator=ratio_numerator,
        ratio_denominator=ratio_denominator,
        symbol_from=symbol_from,
        symbol_to=symbol_to,
        successor_issuer_id=successor_issuer_id,
        memo_id=memo_id,
        provenance=_parse_provenance(record["provenance"], path=f"{path}.provenance"),
    )


def _parse_chain(value: object, *, path: str) -> LineageChain:
    record = _strict_object(value, path=path, fields=_CHAIN_FIELDS)
    if not isinstance(record["listed_option_exists"], bool):
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.listed_option_exists",
            "listed_option_exists must be a boolean",
        )
    links_payload = record["provenance_links"]
    if not isinstance(links_payload, list) or not links_payload:
        _reject(
            LineageReason.MISSING_FIELD,
            f"{path}.provenance_links",
            "every chain requires at least one provenance link",
        )
    return LineageChain(
        event_id=_identifier(record["event_id"], path=f"{path}.event_id"),
        issuer_id=_cik(record["issuer_id"], path=f"{path}.issuer_id"),
        security_id=_identifier(record["security_id"], path=f"{path}.security_id"),
        listing_id=_identifier(record["listing_id"], path=f"{path}.listing_id"),
        ticker_at_cutoff=_ticker(record["ticker_at_cutoff"], path=f"{path}.ticker_at_cutoff"),
        listed_option_exists=record["listed_option_exists"],
        asof=_timestamp(record["asof"], path=f"{path}.asof"),
        provenance_links=tuple(
            _parse_provenance(entry, path=f"{path}.provenance_links[{index}]")
            for index, entry in enumerate(links_payload)
        ),
    )


def _parse_adjustment_policy(value: object, *, path: str) -> AdjustmentPolicy:
    record = _strict_object(value, path=path, fields=_ADJUSTMENT_FIELDS)
    price_adjusting = record["price_adjusting_actions"]
    if not isinstance(price_adjusting, list) or tuple(price_adjusting) != PRICE_ADJUSTING_ACTIONS:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.price_adjusting_actions",
            f"price-adjusting actions must be exactly {list(PRICE_ADJUSTING_ACTIONS)}",
        )
    record_only = record["record_only_actions"]
    if not isinstance(record_only, list) or tuple(record_only) != RECORD_ONLY_ACTIONS:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.record_only_actions",
            f"record-only actions must be exactly {list(RECORD_ONLY_ACTIONS)}",
        )
    authority = _text(
        record["option_adjustment_authority"], path=f"{path}.option_adjustment_authority"
    )
    if authority != OPTION_ADJUSTMENT_AUTHORITY:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.option_adjustment_authority",
            f"option adjustments are authoritative only via '{OPTION_ADJUSTMENT_AUTHORITY}'",
        )
    cross_series = _text(record["cross_series_rule"], path=f"{path}.cross_series_rule")
    if cross_series != CROSS_SERIES_RULE:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{path}.cross_series_rule",
            f"cross-series rule must be exactly '{CROSS_SERIES_RULE}'",
        )
    return AdjustmentPolicy(
        price_adjusting_actions=PRICE_ADJUSTING_ACTIONS,
        record_only_actions=RECORD_ONLY_ACTIONS,
        option_adjustment_authority=authority,
        cross_series_rule=cross_series,
    )


def parse_security_lineage(raw: bytes) -> SecurityLineage:
    """Parse exact lineage bytes with fail-closed strictness."""

    label = "security_lineage"
    payload = _decode(raw, label=label)
    record = _strict_object(payload, path=label, fields=_TOP_LEVEL_FIELDS)
    schema = _text(record["schema"], path=f"{label}.schema")
    if schema != SCHEMA_ID:
        _reject(
            LineageReason.UNSUPPORTED_SCHEMA, f"{label}.schema", f"unsupported schema '{schema}'"
        )
    schema_version = record["schema_version"]
    if isinstance(schema_version, bool) or schema_version != 1:
        _reject(LineageReason.UNSUPPORTED_SCHEMA, f"{label}.schema_version", "unsupported version")
    lineage_id = _identifier(record["lineage_id"], path=f"{label}.lineage_id")
    if lineage_id != LINEAGE_ID:
        _reject(
            LineageReason.UNSUPPORTED_SCHEMA,
            f"{label}.lineage_id",
            f"unsupported id '{lineage_id}'",
        )
    identity_rule = _text(record["identity_rule"], path=f"{label}.identity_rule")
    if identity_rule != IDENTITY_RULE:
        _reject(
            LineageReason.MALFORMED_VALUE,
            f"{label}.identity_rule",
            f"identity rule must be exactly '{IDENTITY_RULE}'",
        )
    issuers_payload = record["issuers"]
    if not isinstance(issuers_payload, list) or not issuers_payload:
        _reject(LineageReason.MALFORMED_VALUE, f"{label}.issuers", "issuers must not be empty")
    issuers = tuple(
        _parse_issuer(entry, path=f"{label}.issuers[{index}]")
        for index, entry in enumerate(issuers_payload)
    )
    issuer_ids = {issuer.issuer_id for issuer in issuers}
    if len(issuer_ids) != len(issuers):
        _reject(LineageReason.DUPLICATE_FIELD, f"{label}.issuers", "duplicate issuer identity")
    securities_payload = record["securities"]
    if not isinstance(securities_payload, list) or not securities_payload:
        _reject(
            LineageReason.MALFORMED_VALUE, f"{label}.securities", "securities must not be empty"
        )
    securities = tuple(
        _parse_security(entry, path=f"{label}.securities[{index}]")
        for index, entry in enumerate(securities_payload)
    )
    security_ids = {security.security_id for security in securities}
    if len(security_ids) != len(securities):
        _reject(LineageReason.DUPLICATE_FIELD, f"{label}.securities", "duplicate security identity")
    for security in securities:
        if security.issuer_id not in issuer_ids:
            _reject(
                LineageReason.REFERENTIAL_INTEGRITY,
                f"{label}.securities",
                f"security {security.security_id} binds unknown issuer {security.issuer_id}",
            )
    listings_payload = record["listings"]
    if not isinstance(listings_payload, list) or not listings_payload:
        _reject(LineageReason.MALFORMED_VALUE, f"{label}.listings", "listings must not be empty")
    listings = tuple(
        _parse_listing(entry, path=f"{label}.listings[{index}]")
        for index, entry in enumerate(listings_payload)
    )
    listing_ids = {listing.listing_id for listing in listings}
    if len(listing_ids) != len(listings):
        _reject(LineageReason.DUPLICATE_FIELD, f"{label}.listings", "duplicate listing identity")
    for listing in listings:
        if listing.security_id not in security_ids:
            _reject(
                LineageReason.REFERENTIAL_INTEGRITY,
                f"{label}.listings",
                f"listing {listing.listing_id} binds unknown security {listing.security_id}",
            )
    _validate_listing_periods(listings, path=f"{label}.listings")
    actions_payload = record["actions"]
    if not isinstance(actions_payload, list):
        _reject(LineageReason.MALFORMED_VALUE, f"{label}.actions", "expected an array")
    actions = tuple(
        _parse_action(entry, path=f"{label}.actions[{index}]")
        for index, entry in enumerate(actions_payload)
    )
    action_ids = {action.action_id for action in actions}
    if len(action_ids) != len(actions):
        _reject(LineageReason.DUPLICATE_FIELD, f"{label}.actions", "duplicate action identity")
    for action in actions:
        if action.issuer_id not in issuer_ids:
            _reject(
                LineageReason.REFERENTIAL_INTEGRITY,
                f"{label}.actions",
                f"action {action.action_id} binds unknown issuer {action.issuer_id}",
            )
        if action.successor_issuer_id is not None and action.successor_issuer_id not in issuer_ids:
            _reject(
                LineageReason.REFERENTIAL_INTEGRITY,
                f"{label}.actions",
                f"action {action.action_id} names unknown successor {action.successor_issuer_id}",
            )
    _validate_action_consistency(actions, path=f"{label}.actions")
    chains_payload = record["chains"]
    if not isinstance(chains_payload, list) or not chains_payload:
        _reject(LineageReason.MALFORMED_VALUE, f"{label}.chains", "chains must not be empty")
    chains = tuple(
        _parse_chain(entry, path=f"{label}.chains[{index}]")
        for index, entry in enumerate(chains_payload)
    )
    event_ids = {chain.event_id for chain in chains}
    if len(event_ids) != len(chains):
        _reject(LineageReason.DUPLICATE_FIELD, f"{label}.chains", "duplicate event chain")
    listings_by_id = {listing.listing_id: listing for listing in listings}
    securities_by_id = {security.security_id: security for security in securities}
    issuers_by_id = {issuer.issuer_id: issuer for issuer in issuers}
    for chain in chains:
        _validate_chain(
            chain,
            label=label,
            issuers_by_id=issuers_by_id,
            securities_by_id=securities_by_id,
            listings_by_id=listings_by_id,
        )
    return SecurityLineage(
        lineage_id=lineage_id,
        lineage_version=_text(record["lineage_version"], path=f"{label}.lineage_version"),
        decided_at=_timestamp(record["decided_at"], path=f"{label}.decided_at"),
        policy_sha256=_sha256(record["policy_sha256"], path=f"{label}.policy_sha256"),
        source_matrix_sha256=_sha256(
            record["source_matrix_sha256"], path=f"{label}.source_matrix_sha256"
        ),
        adjustment_policy=_parse_adjustment_policy(
            record["adjustment_policy"], path=f"{label}.adjustment_policy"
        ),
        issuers=issuers,
        securities=securities,
        listings=listings,
        actions=actions,
        chains=chains,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _validate_listing_periods(listings: tuple[ListingRecord, ...], *, path: str) -> None:
    by_security: dict[str, list[ListingRecord]] = {}
    for listing in listings:
        by_security.setdefault(listing.security_id, []).append(listing)
    for security_id, records in by_security.items():
        ordered = sorted(records, key=lambda item: (item.listed_from, item.exchange_mic))
        for index, current in enumerate(ordered[:-1]):
            following = ordered[index + 1]
            if current.listed_to is None or following.listed_from < current.listed_to:
                _reject(
                    LineageReason.LINEAGE_CONFLICT,
                    path,
                    f"overlapping listing periods for security {security_id}",
                )


def _validate_action_consistency(actions: tuple[ActionRecord, ...], *, path: str) -> None:
    splits: dict[tuple[str, date], list[ActionRecord]] = {}
    adjustments: dict[tuple[str, date], list[ActionRecord]] = {}
    for action in actions:
        if action.action_type == "SPLIT":
            splits.setdefault((action.issuer_id, action.ex_date), []).append(action)
        if action.action_type == "OPTION_ADJUSTMENT":
            adjustments.setdefault((action.issuer_id, action.ex_date), []).append(action)
    for (issuer_id, ex_date), records in splits.items():
        if len(records) > 1:
            _reject(
                LineageReason.LINEAGE_CONFLICT,
                path,
                f"conflicting split records for issuer {issuer_id} on {ex_date}",
            )
    for (issuer_id, ex_date), records in adjustments.items():
        if len(records) > 1:
            _reject(
                LineageReason.OPTION_ADJUSTMENT_CONFLICT,
                path,
                f"conflicting option adjustments for issuer {issuer_id} on {ex_date}",
            )


def _validate_chain(
    chain: LineageChain,
    *,
    label: str,
    issuers_by_id: dict[str, IssuerIdentity],
    securities_by_id: dict[str, SecurityIdentity],
    listings_by_id: dict[str, ListingRecord],
) -> None:
    path = f"{label}.chains[{chain.event_id}]"
    issuer = issuers_by_id.get(chain.issuer_id)
    if issuer is None:
        _reject(LineageReason.LINEAGE_MISSING, path, f"unknown issuer {chain.issuer_id}")
    security = securities_by_id.get(chain.security_id)
    if security is None or security.issuer_id != chain.issuer_id:
        _reject(
            LineageReason.LINEAGE_MISSING,
            path,
            f"security {chain.security_id} is not bound to issuer {chain.issuer_id}",
        )
    listing = listings_by_id.get(chain.listing_id)
    if listing is None or listing.security_id != chain.security_id:
        _reject(
            LineageReason.LINEAGE_MISSING,
            path,
            f"listing {chain.listing_id} is not bound to security {chain.security_id}",
        )
    cutoff_date = chain.asof.date()
    ticker_entry = next(
        (
            entry
            for entry in issuer.tickers
            if entry.ticker == chain.ticker_at_cutoff
            and entry.valid_from <= cutoff_date
            and (entry.valid_to is None or cutoff_date < entry.valid_to)
        ),
        None,
    )
    if ticker_entry is None:
        reused_by = next(
            (
                other.issuer_id
                for other in issuers_by_id.values()
                if other.issuer_id != chain.issuer_id
                and any(
                    entry.ticker == chain.ticker_at_cutoff
                    and entry.valid_from <= cutoff_date
                    and (entry.valid_to is None or cutoff_date < entry.valid_to)
                    for entry in other.tickers
                )
            ),
            None,
        )
        if reused_by is not None:
            _reject(
                LineageReason.SYMBOL_REUSE_DETECTED,
                path,
                f"ticker {chain.ticker_at_cutoff} at cutoff belongs to issuer {reused_by},"
                f" not {chain.issuer_id}",
            )
        _reject(
            LineageReason.LINEAGE_MISSING,
            path,
            f"ticker {chain.ticker_at_cutoff} is not valid for issuer {chain.issuer_id} at cutoff",
        )
    if ticker_entry.security_id != chain.security_id:
        _reject(
            LineageReason.LINEAGE_CONFLICT,
            path,
            f"ticker {chain.ticker_at_cutoff} binds security {ticker_entry.security_id},"
            f" not {chain.security_id}",
        )


def security_lineage_bytes() -> bytes:
    """Return the canonical packaged lineage bytes."""

    return resources.files("ringdown_market.contracts").joinpath(LINEAGE_RESOURCE_NAME).read_bytes()


def load_security_lineage() -> SecurityLineage:
    """Load, authenticate, and parse the packaged security lineage."""

    raw = security_lineage_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SECURITY_LINEAGE_V1_SHA256:
        _reject(
            LineageReason.DIGEST_MISMATCH,
            LINEAGE_RESOURCE_NAME,
            f"packaged lineage digest {digest} != frozen digest {SECURITY_LINEAGE_V1_SHA256}",
        )
    return parse_security_lineage(raw)


def verify_lineage_upstream_bindings(
    lineage: SecurityLineage, *, policy_bytes: bytes, source_matrix_bytes: bytes
) -> None:
    """Fail closed when the lineage no longer binds the frozen upstream contracts."""

    policy_sha = hashlib.sha256(policy_bytes).hexdigest()
    if policy_sha != lineage.policy_sha256:
        _reject(
            LineageReason.UPSTREAM_CONTRACT_DRIFT,
            "policy_sha256",
            f"accepted event policy digest {policy_sha} != bound digest {lineage.policy_sha256}",
        )
    matrix_sha = hashlib.sha256(source_matrix_bytes).hexdigest()
    if matrix_sha != lineage.source_matrix_sha256:
        _reject(
            LineageReason.UPSTREAM_CONTRACT_DRIFT,
            "source_matrix_sha256",
            f"source matrix digest {matrix_sha} != bound digest {lineage.source_matrix_sha256}",
        )


@dataclass(frozen=True)
class LineageResolution:
    event_id: str
    issuer_id: str
    security_id: str
    listing_id: str
    ticker_at_cutoff: str
    active_at_cutoff: bool
    actions: tuple[ActionRecord, ...]
    option_adjustments: tuple[ActionRecord, ...]


def resolve_lineage(lineage: SecurityLineage, event_id: str) -> LineageResolution:
    """Resolve one event chain as-of its cutoff or fail closed."""

    chain = lineage.chains_by_event().get(event_id)
    if chain is None:
        _reject(LineageReason.LINEAGE_MISSING, event_id, "no lineage chain for this event")
    listing = lineage.listings_by_id()[chain.listing_id]
    cutoff_date = chain.asof.date()
    active = listing.active_on(cutoff_date)
    actions = lineage.actions_for_issuer(chain.issuer_id)
    adjustments = tuple(action for action in actions if action.action_type == "OPTION_ADJUSTMENT")
    if chain.listed_option_exists:
        splits = tuple(action for action in actions if action.action_type == "SPLIT")
        for split in splits:
            matched = any(adjustment.ex_date == split.ex_date for adjustment in adjustments)
            if not matched:
                _reject(
                    LineageReason.OPTION_ADJUSTMENT_UNRESOLVED,
                    f"{event_id}.split.{split.ex_date}",
                    "listed options at freeze require an OCC option adjustment for every split",
                )
    return LineageResolution(
        event_id=event_id,
        issuer_id=chain.issuer_id,
        security_id=chain.security_id,
        listing_id=chain.listing_id,
        ticker_at_cutoff=chain.ticker_at_cutoff,
        active_at_cutoff=active,
        actions=actions,
        option_adjustments=adjustments,
    )
