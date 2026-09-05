"""Deterministic, fail-closed option-event reconciliation contracts.

This module has no broker, network, credential, order, or mutation capability.  It
accepts only synthetic or host-normalized observations, reduces them to a bounded
state, and journals the exact canonical inputs and result for restart safety.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final, NoReturn

from esscher.execution.models import (
    DebitVerticalPermit,
    OptionType,
    debit_vertical_permit_bytes,
    debit_vertical_permit_id,
)
from esscher.runtime.autonomous import (
    ActiveLifecycleIdentity,
    AutonomousSessionArm,
    active_lifecycle_bytes,
    autonomous_session_arm_bytes,
)

OPTION_PORTFOLIO_OBSERVATION_SCHEMA: Final = "esscher.option_portfolio_observation"
OPTION_ACTIVITY_COVERAGE_SCHEMA: Final = "esscher.option_activity_coverage"
NORMALIZED_OPTION_EVENT_SCHEMA: Final = "esscher.normalized_option_event"
OPTION_LIFECYCLE_BINDING_SCHEMA: Final = "esscher.option_lifecycle_binding"
OPTION_EVENT_RECEIPT_SCHEMA: Final = "esscher.option_event_reconciliation_receipt"
OPTION_EVENT_SCHEMA_VERSION: Final = 1
OPTION_EVENT_JOURNAL_SCHEMA_VERSION: Final = 1

_BOUNDED_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9._-]{0,31}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NOT_BROKER_EVIDENCE: Final = "NOT_BROKER_CONNECTIVITY_EVIDENCE"
_NOT_ALPHA_EVIDENCE: Final = "NOT_ALPHA_EVIDENCE"


class OptionEventRejected(ValueError):
    """Raised when an option-event input is malformed or semantically unbound."""


class OptionEventConflict(RuntimeError):
    """Raised when durable state conflicts with an already-bound identity."""


class _DuplicateFieldError(ValueError):
    pass


class EvidenceClass(StrEnum):
    """The only evidence labels accepted by this offline boundary."""

    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    HOST_NORMALIZED_BROKER_INPUT = "HOST_NORMALIZED_BROKER_INPUT"


class AssetClass(StrEnum):
    OPTION = "OPTION"
    EQUITY = "EQUITY"


class OptionEventKind(StrEnum):
    ASSIGNMENT = "ASSIGNMENT"
    EXERCISE = "EXERCISE"
    EXPIRY = "EXPIRY"
    BROKER_SELL_OUT = "BROKER_SELL_OUT"
    CONTRACT_ADJUSTMENT = "CONTRACT_ADJUSTMENT"
    EXERCISE_REJECTED_BUYING_POWER = "EXERCISE_REJECTED_BUYING_POWER"


class OptionEventStatus(StrEnum):
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"


class OptionReconciliationState(StrEnum):
    ACTIVE_UNCHANGED = "ACTIVE_UNCHANGED"
    MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"
    EXPIRY_FLAT_ATTESTED = "EXPIRY_FLAT_ATTESTED"


def _reject(message: str) -> NoReturn:
    raise OptionEventRejected(message)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise OptionEventRejected("canonical JSON encoding failed") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateFieldError(key)
        value[key] = item
    return value


def _decode(raw: bytes, *, artifact: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(f"{artifact} bytes must be immutable bytes")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda constant: _reject(
                f"non-standard JSON constant is forbidden: {constant}"
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateFieldError) as error:
        raise OptionEventRejected(f"{artifact} bytes are invalid") from error
    if not isinstance(value, Mapping):
        _reject(f"{artifact} must be an object")
    return value


def _object(
    value: object,
    *,
    fields: frozenset[str],
    path: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(f"{path} must be an object")
    actual = set(value)
    missing = sorted(fields - actual)
    unknown = sorted(actual - fields)
    if missing or unknown:
        _reject(f"{path} field mismatch; missing={missing} unknown={unknown}")
    return value


def _bounded_id(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _BOUNDED_ID.fullmatch(value) is None:
        _reject(f"{path} must be a bounded identifier")
    return value


def _symbol(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SYMBOL.fullmatch(value) is None:
        _reject(f"{path} must be a normalized symbol")
    return value


def _digest(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(f"{path} must be a lowercase SHA-256 digest")
    return value


def _utc(value: datetime, *, path: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject(f"{path} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime, *, path: str) -> str:
    normalized = _utc(value, path=path)
    rendered = normalized.replace(tzinfo=None).isoformat(timespec="microseconds")
    if normalized.microsecond == 0:
        rendered = rendered[:19]
    return f"{rendered}Z"


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _reject(f"{path} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        _reject(f"{path} must be a canonical UTC timestamp")
    parsed = _utc(parsed, path=path)
    if _timestamp_text(parsed, path=path) != value:
        _reject(f"{path} must be a canonical UTC timestamp")
    return parsed


def _date_text(value: date, *, path: str) -> str:
    if not isinstance(value, date) or isinstance(value, datetime):
        _reject(f"{path} must be a calendar date")
    return value.isoformat()


def _date_value(value: object, *, path: str) -> date:
    if not isinstance(value, str):
        _reject(f"{path} must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _reject(f"{path} must be an ISO calendar date")
    if parsed.isoformat() != value:
        _reject(f"{path} must be an ISO calendar date")
    return parsed


def _decimal_text(value: Decimal, *, path: str) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        _reject(f"{path} must be a finite Decimal")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _decimal(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str):
        _reject(f"{path} must be a canonical Decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _reject(f"{path} must be a canonical Decimal string")
    if _decimal_text(parsed, path=path) != value:
        _reject(f"{path} must be a canonical Decimal string")
    return parsed


def _enum(enum_type: type[StrEnum], value: object, *, path: str) -> StrEnum:
    if not isinstance(value, str):
        _reject(f"{path} is invalid")
    try:
        return enum_type(value)
    except ValueError:
        _reject(f"{path} is invalid")


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    """One non-zero normalized account position."""

    asset_class: AssetClass
    symbol: str
    quantity: Decimal


def _position_payload(value: PortfolioPosition) -> dict[str, object]:
    if type(value) is not PortfolioPosition:
        _reject("position must be a PortfolioPosition")
    if not isinstance(value.asset_class, AssetClass):
        _reject("position.asset_class is invalid")
    quantity = _decimal_text(value.quantity, path="position.quantity")
    if value.quantity == 0:
        _reject("zero positions must be omitted")
    if (
        value.asset_class is AssetClass.OPTION
        and value.quantity != value.quantity.to_integral_value()
    ):
        _reject("option position quantity must be integral")
    return {
        "asset_class": value.asset_class.value,
        "quantity": quantity,
        "symbol": _symbol(value.symbol, path="position.symbol"),
    }


def _parse_position(value: object, *, path: str) -> PortfolioPosition:
    data = _object(
        value,
        fields=frozenset({"asset_class", "quantity", "symbol"}),
        path=path,
    )
    position = PortfolioPosition(
        asset_class=_enum(AssetClass, data["asset_class"], path=f"{path}.asset_class"),  # type: ignore[arg-type]
        symbol=_symbol(data["symbol"], path=f"{path}.symbol"),
        quantity=_decimal(data["quantity"], path=f"{path}.quantity"),
    )
    _position_payload(position)
    return position


def _position_key(value: PortfolioPosition) -> tuple[str, str]:
    return value.asset_class.value, value.symbol


@dataclass(frozen=True, slots=True)
class OptionPortfolioObservation:
    """A complete canonical position observation for one sanitized account identity."""

    account_fingerprint_sha256: str
    execution_protocol_sha256: str
    observed_at: datetime
    positions: tuple[PortfolioPosition, ...]
    source_payload_sha256: str
    evidence_class: EvidenceClass
    observation_sha256: str

    @classmethod
    def create(
        cls,
        *,
        account_fingerprint_sha256: str,
        execution_protocol_sha256: str,
        observed_at: datetime,
        positions: Sequence[PortfolioPosition],
        source_payload_sha256: str,
        evidence_class: EvidenceClass,
    ) -> OptionPortfolioObservation:
        ordered = tuple(sorted(positions, key=_position_key))
        candidate = cls(
            account_fingerprint_sha256=account_fingerprint_sha256,
            execution_protocol_sha256=execution_protocol_sha256,
            observed_at=observed_at,
            positions=ordered,
            source_payload_sha256=source_payload_sha256,
            evidence_class=evidence_class,
            observation_sha256="0" * 64,
        )
        digest = _sha256(_canonical(_observation_unsigned_payload(candidate)))
        return cls(
            account_fingerprint_sha256=account_fingerprint_sha256,
            execution_protocol_sha256=execution_protocol_sha256,
            observed_at=observed_at,
            positions=ordered,
            source_payload_sha256=source_payload_sha256,
            evidence_class=evidence_class,
            observation_sha256=digest,
        )

    def to_json_bytes(self) -> bytes:
        return option_portfolio_observation_bytes(self)


def _observation_unsigned_payload(value: OptionPortfolioObservation) -> dict[str, object]:
    if type(value) is not OptionPortfolioObservation:
        _reject("observation must be an OptionPortfolioObservation")
    if not isinstance(value.positions, tuple):
        _reject("observation.positions must be a tuple")
    keys = tuple(_position_key(position) for position in value.positions)
    if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
        _reject("observation positions must be sorted and unique")
    if not isinstance(value.evidence_class, EvidenceClass):
        _reject("observation.evidence_class is invalid")
    return {
        "account_fingerprint_sha256": _digest(
            value.account_fingerprint_sha256,
            path="observation.account_fingerprint_sha256",
        ),
        "evidence_class": value.evidence_class.value,
        "execution_protocol_sha256": _digest(
            value.execution_protocol_sha256,
            path="observation.execution_protocol_sha256",
        ),
        "observed_at": _timestamp_text(value.observed_at, path="observation.observed_at"),
        "positions": [_position_payload(position) for position in value.positions],
        "schema": OPTION_PORTFOLIO_OBSERVATION_SCHEMA,
        "schema_version": OPTION_EVENT_SCHEMA_VERSION,
        "source_payload_sha256": _digest(
            value.source_payload_sha256,
            path="observation.source_payload_sha256",
        ),
    }


def option_portfolio_observation_bytes(value: OptionPortfolioObservation) -> bytes:
    unsigned = _observation_unsigned_payload(value)
    expected = _sha256(_canonical(unsigned))
    if value.observation_sha256 != expected:
        _reject("observation hash is invalid")
    return _canonical({**unsigned, "observation_sha256": expected})


def parse_option_portfolio_observation(raw: bytes) -> OptionPortfolioObservation:
    payload = _decode(raw, artifact="option portfolio observation")
    fields = frozenset(
        {
            "account_fingerprint_sha256",
            "evidence_class",
            "execution_protocol_sha256",
            "observation_sha256",
            "observed_at",
            "positions",
            "schema",
            "schema_version",
            "source_payload_sha256",
        }
    )
    data = _object(payload, fields=fields, path="observation")
    if data["schema"] != OPTION_PORTFOLIO_OBSERVATION_SCHEMA:
        _reject("observation schema mismatch")
    if data["schema_version"] != OPTION_EVENT_SCHEMA_VERSION:
        _reject("observation schema version mismatch")
    if not isinstance(data["positions"], list):
        _reject("observation.positions must be an array")
    value = OptionPortfolioObservation(
        account_fingerprint_sha256=_digest(
            data["account_fingerprint_sha256"],
            path="observation.account_fingerprint_sha256",
        ),
        execution_protocol_sha256=_digest(
            data["execution_protocol_sha256"],
            path="observation.execution_protocol_sha256",
        ),
        observed_at=_timestamp(data["observed_at"], path="observation.observed_at"),
        positions=tuple(
            _parse_position(position, path=f"observation.positions[{index}]")
            for index, position in enumerate(data["positions"])
        ),
        source_payload_sha256=_digest(
            data["source_payload_sha256"],
            path="observation.source_payload_sha256",
        ),
        evidence_class=_enum(
            EvidenceClass,
            data["evidence_class"],
            path="observation.evidence_class",
        ),  # type: ignore[arg-type]
        observation_sha256=_digest(
            data["observation_sha256"],
            path="observation.observation_sha256",
        ),
    )
    if option_portfolio_observation_bytes(value) != raw:
        _reject("observation bytes are not canonical")
    return value


@dataclass(frozen=True, slots=True)
class OptionActivityCoverage:
    """Canonical account-activity pagination coverage asserted by the host."""

    account_fingerprint_sha256: str
    execution_protocol_sha256: str
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    complete: bool
    event_sha256s: tuple[str, ...]
    source_payload_sha256: str
    evidence_class: EvidenceClass
    coverage_sha256: str

    @classmethod
    def create(
        cls,
        *,
        account_fingerprint_sha256: str,
        execution_protocol_sha256: str,
        window_start: datetime,
        window_end: datetime,
        observed_at: datetime,
        complete: bool,
        event_sha256s: Sequence[str],
        source_payload_sha256: str,
        evidence_class: EvidenceClass,
    ) -> OptionActivityCoverage:
        candidate = cls(
            account_fingerprint_sha256=account_fingerprint_sha256,
            execution_protocol_sha256=execution_protocol_sha256,
            window_start=window_start,
            window_end=window_end,
            observed_at=observed_at,
            complete=complete,
            event_sha256s=tuple(sorted(event_sha256s)),
            source_payload_sha256=source_payload_sha256,
            evidence_class=evidence_class,
            coverage_sha256="0" * 64,
        )
        digest = _sha256(_canonical(_coverage_unsigned_payload(candidate)))
        return replace(candidate, coverage_sha256=digest)

    def to_json_bytes(self) -> bytes:
        return option_activity_coverage_bytes(self)


def _coverage_unsigned_payload(value: OptionActivityCoverage) -> dict[str, object]:
    if type(value) is not OptionActivityCoverage:
        _reject("coverage must be an OptionActivityCoverage")
    if type(value.complete) is not bool:
        _reject("coverage.complete must be a boolean")
    window_start = _utc(value.window_start, path="coverage.window_start")
    window_end = _utc(value.window_end, path="coverage.window_end")
    observed_at = _utc(value.observed_at, path="coverage.observed_at")
    if window_end < window_start:
        _reject("coverage window cannot end before it starts")
    if window_end > observed_at:
        _reject("coverage window cannot extend beyond observation time")
    if not isinstance(value.evidence_class, EvidenceClass):
        _reject("coverage.evidence_class is invalid")
    if not isinstance(value.event_sha256s, tuple) or value.event_sha256s != tuple(
        sorted(set(value.event_sha256s))
    ):
        _reject("coverage event hashes must be sorted and unique")
    for index, digest in enumerate(value.event_sha256s):
        _digest(digest, path=f"coverage.event_sha256s[{index}]")
    return {
        "account_fingerprint_sha256": _digest(
            value.account_fingerprint_sha256,
            path="coverage.account_fingerprint_sha256",
        ),
        "complete": value.complete,
        "evidence_class": value.evidence_class.value,
        "event_sha256s": list(value.event_sha256s),
        "execution_protocol_sha256": _digest(
            value.execution_protocol_sha256,
            path="coverage.execution_protocol_sha256",
        ),
        "observed_at": _timestamp_text(observed_at, path="coverage.observed_at"),
        "schema": OPTION_ACTIVITY_COVERAGE_SCHEMA,
        "schema_version": OPTION_EVENT_SCHEMA_VERSION,
        "source_payload_sha256": _digest(
            value.source_payload_sha256,
            path="coverage.source_payload_sha256",
        ),
        "window_end": _timestamp_text(window_end, path="coverage.window_end"),
        "window_start": _timestamp_text(window_start, path="coverage.window_start"),
    }


def option_activity_coverage_bytes(value: OptionActivityCoverage) -> bytes:
    unsigned = _coverage_unsigned_payload(value)
    expected = _sha256(_canonical(unsigned))
    if value.coverage_sha256 != expected:
        _reject("coverage hash is invalid")
    return _canonical({**unsigned, "coverage_sha256": expected})


def parse_option_activity_coverage(raw: bytes) -> OptionActivityCoverage:
    payload = _decode(raw, artifact="option activity coverage")
    data = _object(
        payload,
        fields=frozenset(
            {
                "account_fingerprint_sha256",
                "complete",
                "coverage_sha256",
                "evidence_class",
                "event_sha256s",
                "execution_protocol_sha256",
                "observed_at",
                "schema",
                "schema_version",
                "source_payload_sha256",
                "window_end",
                "window_start",
            }
        ),
        path="coverage",
    )
    if data["schema"] != OPTION_ACTIVITY_COVERAGE_SCHEMA:
        _reject("coverage schema mismatch")
    if data["schema_version"] != OPTION_EVENT_SCHEMA_VERSION:
        _reject("coverage schema version mismatch")
    complete = data["complete"]
    if type(complete) is not bool:
        _reject("coverage.complete must be a boolean")
    if not isinstance(data["event_sha256s"], list):
        _reject("coverage.event_sha256s must be an array")
    value = OptionActivityCoverage(
        account_fingerprint_sha256=_digest(
            data["account_fingerprint_sha256"],
            path="coverage.account_fingerprint_sha256",
        ),
        execution_protocol_sha256=_digest(
            data["execution_protocol_sha256"],
            path="coverage.execution_protocol_sha256",
        ),
        window_start=_timestamp(data["window_start"], path="coverage.window_start"),
        window_end=_timestamp(data["window_end"], path="coverage.window_end"),
        observed_at=_timestamp(data["observed_at"], path="coverage.observed_at"),
        complete=complete,
        event_sha256s=tuple(
            _digest(item, path=f"coverage.event_sha256s[{index}]")
            for index, item in enumerate(data["event_sha256s"])
        ),
        source_payload_sha256=_digest(
            data["source_payload_sha256"],
            path="coverage.source_payload_sha256",
        ),
        evidence_class=_enum(
            EvidenceClass,
            data["evidence_class"],
            path="coverage.evidence_class",
        ),  # type: ignore[arg-type]
        coverage_sha256=_digest(
            data["coverage_sha256"],
            path="coverage.coverage_sha256",
        ),
    )
    if option_activity_coverage_bytes(value) != raw:
        _reject("coverage bytes are not canonical")
    return value


@dataclass(frozen=True, slots=True)
class NormalizedOptionEvent:
    """One semantic activity; raw provider activity codes are intentionally absent."""

    activity_id: str
    kind: OptionEventKind
    status: OptionEventStatus
    option_symbol: str
    contracts: int
    effective_date: date
    observed_at: datetime
    account_fingerprint_sha256: str
    execution_protocol_sha256: str
    underlying_symbol: str
    underlying_quantity_delta: Decimal
    cash_delta: Decimal
    replacement_symbol: str | None
    source_payload_sha256: str
    evidence_class: EvidenceClass
    event_sha256: str

    @classmethod
    def create(cls, **values: object) -> NormalizedOptionEvent:
        candidate = cls(event_sha256="0" * 64, **values)  # type: ignore[arg-type]
        digest = _sha256(_canonical(_event_unsigned_payload(candidate)))
        return cls(event_sha256=digest, **values)  # type: ignore[arg-type]

    def to_json_bytes(self) -> bytes:
        return normalized_option_event_bytes(self)


def _event_unsigned_payload(value: NormalizedOptionEvent) -> dict[str, object]:
    if type(value) is not NormalizedOptionEvent:
        _reject("event must be a NormalizedOptionEvent")
    if not isinstance(value.kind, OptionEventKind) or not isinstance(
        value.status, OptionEventStatus
    ):
        _reject("event kind and status are invalid")
    if not isinstance(value.evidence_class, EvidenceClass):
        _reject("event evidence class is invalid")
    if type(value.contracts) is not int or value.contracts <= 0:
        _reject("event.contracts must be a positive integer")
    if value.kind is OptionEventKind.EXERCISE_REJECTED_BUYING_POWER:
        if value.status is not OptionEventStatus.REJECTED:
            _reject("buying-power rejection must have REJECTED status")
    elif value.status is not OptionEventStatus.EXECUTED:
        _reject("non-rejection events must have EXECUTED status")
    if value.kind is OptionEventKind.CONTRACT_ADJUSTMENT:
        if value.replacement_symbol is None or value.replacement_symbol == value.option_symbol:
            _reject("contract adjustment requires a distinct replacement symbol")
    elif value.replacement_symbol is not None:
        _reject("replacement_symbol is legal only for contract adjustment")
    return {
        "account_fingerprint_sha256": _digest(
            value.account_fingerprint_sha256,
            path="event.account_fingerprint_sha256",
        ),
        "activity_id": _bounded_id(value.activity_id, path="event.activity_id"),
        "cash_delta": _decimal_text(value.cash_delta, path="event.cash_delta"),
        "contracts": value.contracts,
        "effective_date": _date_text(value.effective_date, path="event.effective_date"),
        "evidence_class": value.evidence_class.value,
        "execution_protocol_sha256": _digest(
            value.execution_protocol_sha256,
            path="event.execution_protocol_sha256",
        ),
        "kind": value.kind.value,
        "observed_at": _timestamp_text(value.observed_at, path="event.observed_at"),
        "option_symbol": _symbol(value.option_symbol, path="event.option_symbol"),
        "replacement_symbol": (
            None
            if value.replacement_symbol is None
            else _symbol(value.replacement_symbol, path="event.replacement_symbol")
        ),
        "schema": NORMALIZED_OPTION_EVENT_SCHEMA,
        "schema_version": OPTION_EVENT_SCHEMA_VERSION,
        "source_payload_sha256": _digest(
            value.source_payload_sha256,
            path="event.source_payload_sha256",
        ),
        "status": value.status.value,
        "underlying_quantity_delta": _decimal_text(
            value.underlying_quantity_delta,
            path="event.underlying_quantity_delta",
        ),
        "underlying_symbol": _symbol(value.underlying_symbol, path="event.underlying_symbol"),
    }


def normalized_option_event_bytes(value: NormalizedOptionEvent) -> bytes:
    unsigned = _event_unsigned_payload(value)
    expected = _sha256(_canonical(unsigned))
    if value.event_sha256 != expected:
        _reject("event hash is invalid")
    return _canonical({**unsigned, "event_sha256": expected})


def parse_normalized_option_event(raw: bytes) -> NormalizedOptionEvent:
    payload = _decode(raw, artifact="normalized option event")
    fields = frozenset(
        {
            "account_fingerprint_sha256",
            "activity_id",
            "cash_delta",
            "contracts",
            "effective_date",
            "event_sha256",
            "evidence_class",
            "execution_protocol_sha256",
            "kind",
            "observed_at",
            "option_symbol",
            "replacement_symbol",
            "schema",
            "schema_version",
            "source_payload_sha256",
            "status",
            "underlying_quantity_delta",
            "underlying_symbol",
        }
    )
    data = _object(payload, fields=fields, path="event")
    if data["schema"] != NORMALIZED_OPTION_EVENT_SCHEMA:
        _reject("event schema mismatch")
    if data["schema_version"] != OPTION_EVENT_SCHEMA_VERSION:
        _reject("event schema version mismatch")
    replacement = data["replacement_symbol"]
    if replacement is not None:
        replacement = _symbol(replacement, path="event.replacement_symbol")
    value = NormalizedOptionEvent(
        activity_id=_bounded_id(data["activity_id"], path="event.activity_id"),
        kind=_enum(OptionEventKind, data["kind"], path="event.kind"),  # type: ignore[arg-type]
        status=_enum(OptionEventStatus, data["status"], path="event.status"),  # type: ignore[arg-type]
        option_symbol=_symbol(data["option_symbol"], path="event.option_symbol"),
        contracts=data["contracts"],  # type: ignore[arg-type]
        effective_date=_date_value(data["effective_date"], path="event.effective_date"),
        observed_at=_timestamp(data["observed_at"], path="event.observed_at"),
        account_fingerprint_sha256=_digest(
            data["account_fingerprint_sha256"],
            path="event.account_fingerprint_sha256",
        ),
        execution_protocol_sha256=_digest(
            data["execution_protocol_sha256"],
            path="event.execution_protocol_sha256",
        ),
        underlying_symbol=_symbol(data["underlying_symbol"], path="event.underlying_symbol"),
        underlying_quantity_delta=_decimal(
            data["underlying_quantity_delta"],
            path="event.underlying_quantity_delta",
        ),
        cash_delta=_decimal(data["cash_delta"], path="event.cash_delta"),
        replacement_symbol=replacement,
        source_payload_sha256=_digest(
            data["source_payload_sha256"],
            path="event.source_payload_sha256",
        ),
        evidence_class=_enum(
            EvidenceClass,
            data["evidence_class"],
            path="event.evidence_class",
        ),  # type: ignore[arg-type]
        event_sha256=_digest(data["event_sha256"], path="event.event_sha256"),
    )
    if normalized_option_event_bytes(value) != raw:
        _reject("event bytes are not canonical")
    return value


@dataclass(frozen=True, slots=True)
class OptionLifecycleBinding:
    """Exact close-critical identity and activation truth for one P0 vertical."""

    session_id: str
    session_arm_sha256: str
    lifecycle_id: str
    lifecycle_sha256: str
    opportunity_id: str
    opportunity_sha256: str
    permit_id: str
    permit_sha256: str
    reservation_id: str
    account_fingerprint_sha256: str
    execution_protocol_sha256: str
    underlying_symbol: str
    option_type: OptionType
    option_expiry: date
    quantity: int
    long_symbol: str
    long_strike: Decimal
    short_symbol: str
    short_strike: Decimal
    activation_observation_sha256: str
    activation_evidence_class: EvidenceClass
    activation_observed_at: datetime
    baseline_underlying_quantity: Decimal
    expiration_session_date: date
    expiration_session_close: datetime
    expiration_activity_horizon: datetime
    calendar_sha256: str
    binding_sha256: str

    @classmethod
    def create(
        cls,
        *,
        arm: AutonomousSessionArm,
        lifecycle: ActiveLifecycleIdentity,
        permit: DebitVerticalPermit,
        reservation_id: str,
        activation_observation: OptionPortfolioObservation,
        expiration_session_date: date,
        expiration_session_close: datetime,
        expiration_activity_horizon: datetime,
        calendar_sha256: str,
    ) -> OptionLifecycleBinding:
        autonomous_session_arm_bytes(arm)
        active_lifecycle_bytes(lifecycle)
        permit_bytes = debit_vertical_permit_bytes(permit)
        if lifecycle.session_id != arm.session_id:
            _reject("lifecycle session does not match the arm")
        if lifecycle.opportunity_id != permit.event_run_id:
            _reject("lifecycle opportunity does not match the permit event run")
        if permit.permit_id != debit_vertical_permit_id(permit):
            _reject("permit ID is not self-derived")
        if permit.execution_protocol_sha256 != arm.execution_protocol_sha256:
            _reject("permit execution protocol does not match the arm")
        option_portfolio_observation_bytes(activation_observation)
        if (
            activation_observation.account_fingerprint_sha256 != arm.account_fingerprint_sha256
            or activation_observation.execution_protocol_sha256 != arm.execution_protocol_sha256
        ):
            _reject("activation observation does not match arm authority")
        long_leg, short_leg = permit.legs
        expected = Decimal(permit.quantity)
        if (
            _position_quantity(activation_observation, AssetClass.OPTION, long_leg.symbol)
            != expected
        ):
            _reject("activation observation does not contain the exact long leg")
        if (
            _position_quantity(activation_observation, AssetClass.OPTION, short_leg.symbol)
            != -expected
        ):
            _reject("activation observation does not contain the exact short leg")
        candidate = cls(
            session_id=arm.session_id,
            session_arm_sha256=arm.arm_sha256,
            lifecycle_id=lifecycle.lifecycle_id,
            lifecycle_sha256=lifecycle.lifecycle_sha256,
            opportunity_id=lifecycle.opportunity_id,
            opportunity_sha256=lifecycle.opportunity_sha256,
            permit_id=permit.permit_id,
            permit_sha256=_sha256(permit_bytes),
            reservation_id=reservation_id,
            account_fingerprint_sha256=arm.account_fingerprint_sha256,
            execution_protocol_sha256=arm.execution_protocol_sha256,
            underlying_symbol=permit.underlying,
            option_type=long_leg.option_type,
            option_expiry=long_leg.expiry,
            quantity=permit.quantity,
            long_symbol=long_leg.symbol,
            long_strike=long_leg.strike,
            short_symbol=short_leg.symbol,
            short_strike=short_leg.strike,
            activation_observation_sha256=activation_observation.observation_sha256,
            activation_evidence_class=activation_observation.evidence_class,
            activation_observed_at=activation_observation.observed_at,
            baseline_underlying_quantity=_position_quantity(
                activation_observation,
                AssetClass.EQUITY,
                permit.underlying,
            ),
            expiration_session_date=expiration_session_date,
            expiration_session_close=expiration_session_close,
            expiration_activity_horizon=expiration_activity_horizon,
            calendar_sha256=calendar_sha256,
            binding_sha256="0" * 64,
        )
        digest = _sha256(_canonical(_binding_unsigned_payload(candidate)))
        return replace(candidate, binding_sha256=digest)

    def to_json_bytes(self) -> bytes:
        return option_lifecycle_binding_bytes(self)


def _binding_unsigned_payload(value: OptionLifecycleBinding) -> dict[str, object]:
    if type(value) is not OptionLifecycleBinding:
        _reject("binding must be an OptionLifecycleBinding")
    if not isinstance(value.option_type, OptionType):
        _reject("binding.option_type is invalid")
    if not isinstance(value.activation_evidence_class, EvidenceClass):
        _reject("binding.activation_evidence_class is invalid")
    if type(value.quantity) is not int or value.quantity != 1:
        _reject("binding.quantity must equal one")
    if value.long_symbol == value.short_symbol:
        _reject("binding option symbols must be distinct")
    if value.long_strike <= 0 or value.short_strike <= 0:
        _reject("binding strikes must be positive")
    if value.expiration_session_date > value.option_expiry:
        _reject("expiration session cannot follow the contract expiry")
    if _utc(value.expiration_session_close, path="binding.expiration_session_close") <= _utc(
        value.activation_observed_at,
        path="binding.activation_observed_at",
    ):
        _reject("expiration session close must follow activation")
    if _utc(value.expiration_activity_horizon, path="binding.expiration_activity_horizon") <= _utc(
        value.expiration_session_close,
        path="binding.expiration_session_close",
    ):
        _reject("expiration activity horizon must follow the session close")
    return {
        "account_fingerprint_sha256": _digest(
            value.account_fingerprint_sha256,
            path="binding.account_fingerprint_sha256",
        ),
        "activation_observation_sha256": _digest(
            value.activation_observation_sha256,
            path="binding.activation_observation_sha256",
        ),
        "activation_evidence_class": value.activation_evidence_class.value,
        "activation_observed_at": _timestamp_text(
            value.activation_observed_at,
            path="binding.activation_observed_at",
        ),
        "baseline_underlying_quantity": _decimal_text(
            value.baseline_underlying_quantity,
            path="binding.baseline_underlying_quantity",
        ),
        "calendar_sha256": _digest(value.calendar_sha256, path="binding.calendar_sha256"),
        "execution_protocol_sha256": _digest(
            value.execution_protocol_sha256,
            path="binding.execution_protocol_sha256",
        ),
        "expiration_session_close": _timestamp_text(
            value.expiration_session_close,
            path="binding.expiration_session_close",
        ),
        "expiration_activity_horizon": _timestamp_text(
            value.expiration_activity_horizon,
            path="binding.expiration_activity_horizon",
        ),
        "expiration_session_date": _date_text(
            value.expiration_session_date,
            path="binding.expiration_session_date",
        ),
        "lifecycle_id": _bounded_id(value.lifecycle_id, path="binding.lifecycle_id"),
        "lifecycle_sha256": _digest(value.lifecycle_sha256, path="binding.lifecycle_sha256"),
        "long_strike": _decimal_text(value.long_strike, path="binding.long_strike"),
        "long_symbol": _symbol(value.long_symbol, path="binding.long_symbol"),
        "opportunity_id": _bounded_id(value.opportunity_id, path="binding.opportunity_id"),
        "opportunity_sha256": _digest(
            value.opportunity_sha256,
            path="binding.opportunity_sha256",
        ),
        "option_expiry": _date_text(value.option_expiry, path="binding.option_expiry"),
        "option_type": value.option_type.value,
        "permit_id": _bounded_id(value.permit_id, path="binding.permit_id"),
        "permit_sha256": _digest(value.permit_sha256, path="binding.permit_sha256"),
        "quantity": value.quantity,
        "reservation_id": _bounded_id(value.reservation_id, path="binding.reservation_id"),
        "schema": OPTION_LIFECYCLE_BINDING_SCHEMA,
        "schema_version": OPTION_EVENT_SCHEMA_VERSION,
        "session_arm_sha256": _digest(
            value.session_arm_sha256,
            path="binding.session_arm_sha256",
        ),
        "session_id": _bounded_id(value.session_id, path="binding.session_id"),
        "short_strike": _decimal_text(value.short_strike, path="binding.short_strike"),
        "short_symbol": _symbol(value.short_symbol, path="binding.short_symbol"),
        "underlying_symbol": _symbol(
            value.underlying_symbol,
            path="binding.underlying_symbol",
        ),
    }


def option_lifecycle_binding_bytes(value: OptionLifecycleBinding) -> bytes:
    unsigned = _binding_unsigned_payload(value)
    expected = _sha256(_canonical(unsigned))
    if value.binding_sha256 != expected:
        _reject("binding hash is invalid")
    return _canonical({**unsigned, "binding_sha256": expected})


def parse_option_lifecycle_binding(raw: bytes) -> OptionLifecycleBinding:
    payload = _decode(raw, artifact="option lifecycle binding")
    fields = frozenset(
        {
            "account_fingerprint_sha256",
            "activation_evidence_class",
            "activation_observation_sha256",
            "activation_observed_at",
            "baseline_underlying_quantity",
            "binding_sha256",
            "calendar_sha256",
            "execution_protocol_sha256",
            "expiration_activity_horizon",
            "expiration_session_close",
            "expiration_session_date",
            "lifecycle_id",
            "lifecycle_sha256",
            "long_strike",
            "long_symbol",
            "opportunity_id",
            "opportunity_sha256",
            "option_expiry",
            "option_type",
            "permit_id",
            "permit_sha256",
            "quantity",
            "reservation_id",
            "schema",
            "schema_version",
            "session_arm_sha256",
            "session_id",
            "short_strike",
            "short_symbol",
            "underlying_symbol",
        }
    )
    data = _object(payload, fields=fields, path="binding")
    if data["schema"] != OPTION_LIFECYCLE_BINDING_SCHEMA:
        _reject("binding schema mismatch")
    if data["schema_version"] != OPTION_EVENT_SCHEMA_VERSION:
        _reject("binding schema version mismatch")
    value = OptionLifecycleBinding(
        session_id=_bounded_id(data["session_id"], path="binding.session_id"),
        session_arm_sha256=_digest(data["session_arm_sha256"], path="binding.session_arm_sha256"),
        lifecycle_id=_bounded_id(data["lifecycle_id"], path="binding.lifecycle_id"),
        lifecycle_sha256=_digest(data["lifecycle_sha256"], path="binding.lifecycle_sha256"),
        opportunity_id=_bounded_id(data["opportunity_id"], path="binding.opportunity_id"),
        opportunity_sha256=_digest(data["opportunity_sha256"], path="binding.opportunity_sha256"),
        permit_id=_bounded_id(data["permit_id"], path="binding.permit_id"),
        permit_sha256=_digest(data["permit_sha256"], path="binding.permit_sha256"),
        reservation_id=_bounded_id(data["reservation_id"], path="binding.reservation_id"),
        account_fingerprint_sha256=_digest(
            data["account_fingerprint_sha256"], path="binding.account_fingerprint_sha256"
        ),
        execution_protocol_sha256=_digest(
            data["execution_protocol_sha256"], path="binding.execution_protocol_sha256"
        ),
        underlying_symbol=_symbol(data["underlying_symbol"], path="binding.underlying_symbol"),
        option_type=_enum(OptionType, data["option_type"], path="binding.option_type"),  # type: ignore[arg-type]
        option_expiry=_date_value(data["option_expiry"], path="binding.option_expiry"),
        quantity=data["quantity"],  # type: ignore[arg-type]
        long_symbol=_symbol(data["long_symbol"], path="binding.long_symbol"),
        long_strike=_decimal(data["long_strike"], path="binding.long_strike"),
        short_symbol=_symbol(data["short_symbol"], path="binding.short_symbol"),
        short_strike=_decimal(data["short_strike"], path="binding.short_strike"),
        activation_observation_sha256=_digest(
            data["activation_observation_sha256"],
            path="binding.activation_observation_sha256",
        ),
        activation_evidence_class=_enum(
            EvidenceClass,
            data["activation_evidence_class"],
            path="binding.activation_evidence_class",
        ),  # type: ignore[arg-type]
        activation_observed_at=_timestamp(
            data["activation_observed_at"], path="binding.activation_observed_at"
        ),
        baseline_underlying_quantity=_decimal(
            data["baseline_underlying_quantity"],
            path="binding.baseline_underlying_quantity",
        ),
        expiration_session_date=_date_value(
            data["expiration_session_date"], path="binding.expiration_session_date"
        ),
        expiration_session_close=_timestamp(
            data["expiration_session_close"], path="binding.expiration_session_close"
        ),
        expiration_activity_horizon=_timestamp(
            data["expiration_activity_horizon"],
            path="binding.expiration_activity_horizon",
        ),
        calendar_sha256=_digest(data["calendar_sha256"], path="binding.calendar_sha256"),
        binding_sha256=_digest(data["binding_sha256"], path="binding.binding_sha256"),
    )
    if option_lifecycle_binding_bytes(value) != raw:
        _reject("binding bytes are not canonical")
    return value


@dataclass(frozen=True, slots=True)
class OptionEventReconciliationReceipt:
    """Self-hashing result of one pure reconciliation."""

    binding_sha256: str
    session_id: str
    lifecycle_id: str
    observation_sha256: str
    activity_coverage_sha256: str
    event_sha256s: tuple[str, ...]
    state: OptionReconciliationState
    reason_codes: tuple[str, ...]
    observed_at: datetime
    long_option_quantity: Decimal
    short_option_quantity: Decimal
    underlying_quantity: Decimal
    underlying_quantity_delta: Decimal
    event_cash_delta: Decimal
    evidence_class: EvidenceClass
    broker_connectivity_evidence: str
    alpha_evidence: str
    receipt_sha256: str

    def to_json_bytes(self) -> bytes:
        return option_event_reconciliation_receipt_bytes(self)


def _receipt_unsigned_payload(value: OptionEventReconciliationReceipt) -> dict[str, object]:
    if type(value) is not OptionEventReconciliationReceipt:
        _reject("receipt must be an OptionEventReconciliationReceipt")
    if not isinstance(value.state, OptionReconciliationState):
        _reject("receipt.state is invalid")
    if not isinstance(value.evidence_class, EvidenceClass):
        _reject("receipt.evidence_class is invalid")
    if value.broker_connectivity_evidence != _NOT_BROKER_EVIDENCE:
        _reject("receipt cannot claim broker-connectivity evidence")
    if value.alpha_evidence != _NOT_ALPHA_EVIDENCE:
        _reject("receipt cannot claim alpha evidence")
    if not isinstance(value.event_sha256s, tuple) or value.event_sha256s != tuple(
        sorted(set(value.event_sha256s))
    ):
        _reject("receipt event hashes must be sorted and unique")
    if (
        not isinstance(value.reason_codes, tuple)
        or not value.reason_codes
        or value.reason_codes != tuple(sorted(set(value.reason_codes)))
    ):
        _reject("receipt reason codes must be sorted, unique, and non-empty")
    for index, digest in enumerate(value.event_sha256s):
        _digest(digest, path=f"receipt.event_sha256s[{index}]")
    for index, reason in enumerate(value.reason_codes):
        _bounded_id(reason, path=f"receipt.reason_codes[{index}]")
    return {
        "alpha_evidence": value.alpha_evidence,
        "activity_coverage_sha256": _digest(
            value.activity_coverage_sha256,
            path="receipt.activity_coverage_sha256",
        ),
        "binding_sha256": _digest(value.binding_sha256, path="receipt.binding_sha256"),
        "broker_connectivity_evidence": value.broker_connectivity_evidence,
        "event_cash_delta": _decimal_text(
            value.event_cash_delta,
            path="receipt.event_cash_delta",
        ),
        "event_sha256s": list(value.event_sha256s),
        "evidence_class": value.evidence_class.value,
        "lifecycle_id": _bounded_id(value.lifecycle_id, path="receipt.lifecycle_id"),
        "long_option_quantity": _decimal_text(
            value.long_option_quantity,
            path="receipt.long_option_quantity",
        ),
        "observation_sha256": _digest(
            value.observation_sha256,
            path="receipt.observation_sha256",
        ),
        "observed_at": _timestamp_text(value.observed_at, path="receipt.observed_at"),
        "reason_codes": list(value.reason_codes),
        "schema": OPTION_EVENT_RECEIPT_SCHEMA,
        "schema_version": OPTION_EVENT_SCHEMA_VERSION,
        "session_id": _bounded_id(value.session_id, path="receipt.session_id"),
        "short_option_quantity": _decimal_text(
            value.short_option_quantity,
            path="receipt.short_option_quantity",
        ),
        "state": value.state.value,
        "underlying_quantity": _decimal_text(
            value.underlying_quantity,
            path="receipt.underlying_quantity",
        ),
        "underlying_quantity_delta": _decimal_text(
            value.underlying_quantity_delta,
            path="receipt.underlying_quantity_delta",
        ),
    }


def _receipt(
    *,
    binding: OptionLifecycleBinding,
    observation: OptionPortfolioObservation,
    activity_coverage: OptionActivityCoverage,
    events: Sequence[NormalizedOptionEvent],
    state: OptionReconciliationState,
    reasons: Sequence[str],
) -> OptionEventReconciliationReceipt:
    long_quantity = _position_quantity(observation, AssetClass.OPTION, binding.long_symbol)
    short_quantity = _position_quantity(observation, AssetClass.OPTION, binding.short_symbol)
    underlying_quantity = _position_quantity(
        observation,
        AssetClass.EQUITY,
        binding.underlying_symbol,
    )
    values = {
        "binding_sha256": binding.binding_sha256,
        "session_id": binding.session_id,
        "lifecycle_id": binding.lifecycle_id,
        "observation_sha256": observation.observation_sha256,
        "activity_coverage_sha256": activity_coverage.coverage_sha256,
        "event_sha256s": tuple(sorted(event.event_sha256 for event in events)),
        "state": state,
        "reason_codes": tuple(sorted(set(reasons))),
        "observed_at": observation.observed_at,
        "long_option_quantity": long_quantity,
        "short_option_quantity": short_quantity,
        "underlying_quantity": underlying_quantity,
        "underlying_quantity_delta": (underlying_quantity - binding.baseline_underlying_quantity),
        "event_cash_delta": sum(
            (event.cash_delta for event in events),
            start=Decimal(0),
        ),
        "evidence_class": observation.evidence_class,
        "broker_connectivity_evidence": _NOT_BROKER_EVIDENCE,
        "alpha_evidence": _NOT_ALPHA_EVIDENCE,
    }
    candidate = OptionEventReconciliationReceipt(receipt_sha256="0" * 64, **values)
    digest = _sha256(_canonical(_receipt_unsigned_payload(candidate)))
    return OptionEventReconciliationReceipt(receipt_sha256=digest, **values)


def option_event_reconciliation_receipt_bytes(
    value: OptionEventReconciliationReceipt,
) -> bytes:
    unsigned = _receipt_unsigned_payload(value)
    expected = _sha256(_canonical(unsigned))
    if value.receipt_sha256 != expected:
        _reject("receipt hash is invalid")
    return _canonical({**unsigned, "receipt_sha256": expected})


def parse_option_event_reconciliation_receipt(raw: bytes) -> OptionEventReconciliationReceipt:
    payload = _decode(raw, artifact="option-event receipt")
    fields = frozenset(
        {
            "alpha_evidence",
            "activity_coverage_sha256",
            "binding_sha256",
            "broker_connectivity_evidence",
            "event_cash_delta",
            "event_sha256s",
            "evidence_class",
            "lifecycle_id",
            "long_option_quantity",
            "observation_sha256",
            "observed_at",
            "reason_codes",
            "receipt_sha256",
            "schema",
            "schema_version",
            "session_id",
            "short_option_quantity",
            "state",
            "underlying_quantity",
            "underlying_quantity_delta",
        }
    )
    data = _object(payload, fields=fields, path="receipt")
    if data["schema"] != OPTION_EVENT_RECEIPT_SCHEMA:
        _reject("receipt schema mismatch")
    if data["schema_version"] != OPTION_EVENT_SCHEMA_VERSION:
        _reject("receipt schema version mismatch")
    if not isinstance(data["event_sha256s"], list) or not isinstance(data["reason_codes"], list):
        _reject("receipt arrays are invalid")
    value = OptionEventReconciliationReceipt(
        binding_sha256=_digest(data["binding_sha256"], path="receipt.binding_sha256"),
        session_id=_bounded_id(data["session_id"], path="receipt.session_id"),
        lifecycle_id=_bounded_id(data["lifecycle_id"], path="receipt.lifecycle_id"),
        activity_coverage_sha256=_digest(
            data["activity_coverage_sha256"],
            path="receipt.activity_coverage_sha256",
        ),
        observation_sha256=_digest(data["observation_sha256"], path="receipt.observation_sha256"),
        event_sha256s=tuple(
            _digest(item, path=f"receipt.event_sha256s[{index}]")
            for index, item in enumerate(data["event_sha256s"])
        ),
        state=_enum(OptionReconciliationState, data["state"], path="receipt.state"),  # type: ignore[arg-type]
        reason_codes=tuple(
            _bounded_id(item, path=f"receipt.reason_codes[{index}]")
            for index, item in enumerate(data["reason_codes"])
        ),
        observed_at=_timestamp(data["observed_at"], path="receipt.observed_at"),
        long_option_quantity=_decimal(
            data["long_option_quantity"],
            path="receipt.long_option_quantity",
        ),
        short_option_quantity=_decimal(
            data["short_option_quantity"],
            path="receipt.short_option_quantity",
        ),
        underlying_quantity=_decimal(
            data["underlying_quantity"],
            path="receipt.underlying_quantity",
        ),
        underlying_quantity_delta=_decimal(
            data["underlying_quantity_delta"],
            path="receipt.underlying_quantity_delta",
        ),
        event_cash_delta=_decimal(
            data["event_cash_delta"],
            path="receipt.event_cash_delta",
        ),
        evidence_class=_enum(EvidenceClass, data["evidence_class"], path="receipt.evidence_class"),  # type: ignore[arg-type]
        broker_connectivity_evidence=data["broker_connectivity_evidence"],  # type: ignore[arg-type]
        alpha_evidence=data["alpha_evidence"],  # type: ignore[arg-type]
        receipt_sha256=_digest(data["receipt_sha256"], path="receipt.receipt_sha256"),
    )
    if option_event_reconciliation_receipt_bytes(value) != raw:
        _reject("receipt bytes are not canonical")
    return value


def _position_quantity(
    observation: OptionPortfolioObservation,
    asset_class: AssetClass,
    symbol: str,
) -> Decimal:
    matches = tuple(
        position.quantity
        for position in observation.positions
        if position.asset_class is asset_class and position.symbol == symbol
    )
    if len(matches) > 1:
        _reject("observation contains a duplicate position")
    return Decimal(0) if not matches else matches[0]


def _leg_terms(
    binding: OptionLifecycleBinding,
    symbol: str,
) -> tuple[Decimal, Decimal] | None:
    quantity = Decimal(binding.quantity)
    if symbol == binding.long_symbol:
        return quantity, binding.long_strike
    if symbol == binding.short_symbol:
        return -quantity, binding.short_strike
    return None


def _event_economics_valid(
    binding: OptionLifecycleBinding,
    event: NormalizedOptionEvent,
) -> bool:
    terms = _leg_terms(binding, event.option_symbol)
    if terms is None:
        return False
    signed_contracts, strike = terms
    if Decimal(event.contracts) != abs(signed_contracts):
        return False
    if event.kind is OptionEventKind.ASSIGNMENT and signed_contracts >= 0:
        return False
    if event.kind is OptionEventKind.EXERCISE and signed_contracts <= 0:
        return False
    share_delta = signed_contracts * Decimal(100)
    if binding.option_type is OptionType.PUT:
        share_delta = -share_delta
    return event.underlying_quantity_delta == share_delta and event.cash_delta == -(
        share_delta * strike
    )


def reconcile_option_events(
    *,
    binding: OptionLifecycleBinding,
    current_observation: OptionPortfolioObservation,
    activity_coverage: OptionActivityCoverage,
    events: Sequence[NormalizedOptionEvent],
) -> OptionEventReconciliationReceipt:
    """Purely reconcile one bound spread against a fresh position/activity observation."""

    option_lifecycle_binding_bytes(binding)
    option_portfolio_observation_bytes(current_observation)
    option_activity_coverage_bytes(activity_coverage)
    ordered_events = tuple(sorted(events, key=lambda event: event.activity_id))
    for event in ordered_events:
        normalized_option_event_bytes(event)
    activity_ids = tuple(event.activity_id for event in ordered_events)
    if len(activity_ids) != len(set(activity_ids)):
        _reject("event activity IDs must be unique")
    if activity_coverage.event_sha256s != tuple(
        sorted(event.event_sha256 for event in ordered_events)
    ):
        _reject("activity coverage does not bind the normalized event set")
    if (
        current_observation.account_fingerprint_sha256 != binding.account_fingerprint_sha256
        or current_observation.execution_protocol_sha256 != binding.execution_protocol_sha256
        or current_observation.evidence_class is not binding.activation_evidence_class
    ):
        _reject("observation authority does not match the lifecycle binding")
    if (
        activity_coverage.account_fingerprint_sha256 != binding.account_fingerprint_sha256
        or activity_coverage.execution_protocol_sha256 != binding.execution_protocol_sha256
        or activity_coverage.evidence_class is not binding.activation_evidence_class
        or activity_coverage.observed_at != current_observation.observed_at
    ):
        _reject("activity coverage does not match observation authority or clock")
    if current_observation.observed_at < binding.activation_observed_at:
        _reject("current observation predates lifecycle activation")
    for event in ordered_events:
        if (
            event.account_fingerprint_sha256 != binding.account_fingerprint_sha256
            or event.execution_protocol_sha256 != binding.execution_protocol_sha256
            or event.evidence_class is not current_observation.evidence_class
            or event.observed_at < binding.activation_observed_at
            or event.observed_at > current_observation.observed_at
            or event.observed_at < activity_coverage.window_start
            or event.observed_at > activity_coverage.window_end
            or event.effective_date < binding.activation_observed_at.date()
            or event.effective_date > binding.option_expiry
            or event.underlying_symbol != binding.underlying_symbol
        ):
            _reject("event authority, evidence, clock, or underlying is unbound")

    expected = Decimal(binding.quantity)
    long_quantity = _position_quantity(current_observation, AssetClass.OPTION, binding.long_symbol)
    short_quantity = _position_quantity(
        current_observation, AssetClass.OPTION, binding.short_symbol
    )
    underlying_quantity = _position_quantity(
        current_observation, AssetClass.EQUITY, binding.underlying_symbol
    )
    positions_unchanged = (
        long_quantity == expected
        and short_quantity == -expected
        and underlying_quantity == binding.baseline_underlying_quantity
    )
    coverage_complete = (
        activity_coverage.complete
        and activity_coverage.window_start <= binding.activation_observed_at
        and activity_coverage.window_end == current_observation.observed_at
    )

    if not ordered_events:
        if (
            coverage_complete
            and positions_unchanged
            and current_observation.observed_at < binding.expiration_session_close
        ):
            return _receipt(
                binding=binding,
                observation=current_observation,
                activity_coverage=activity_coverage,
                events=ordered_events,
                state=OptionReconciliationState.ACTIVE_UNCHANGED,
                reasons=("BOUND_POSITIONS_UNCHANGED",),
            )
        reasons = set()
        if not coverage_complete:
            reasons.add("ACCOUNT_ACTIVITY_COVERAGE_INCOMPLETE")
        if current_observation.observed_at < binding.expiration_session_close:
            reason = "UNATTRIBUTED_POSITION_CHANGE"
        elif current_observation.observed_at < binding.expiration_activity_horizon:
            reason = "EXPIRY_ACTIVITY_HORIZON_OPEN"
        else:
            reason = "EXPIRY_ACTIVITY_EVIDENCE_MISSING"
        reasons.add(reason)
        return _receipt(
            binding=binding,
            observation=current_observation,
            activity_coverage=activity_coverage,
            events=ordered_events,
            state=OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED,
            reasons=tuple(reasons),
        )

    reasons: set[str] = set()
    if not coverage_complete:
        reasons.add("ACCOUNT_ACTIVITY_COVERAGE_INCOMPLETE")
    bound_symbols = {binding.long_symbol, binding.short_symbol}
    if any(event.option_symbol not in bound_symbols for event in ordered_events):
        reasons.add("UNMATCHED_OPTION_ACTIVITY")
    if any(
        event.kind is OptionEventKind.EXERCISE_REJECTED_BUYING_POWER for event in ordered_events
    ):
        reasons.add("EXERCISE_REJECTED_BUYING_POWER")
    if any(event.kind is OptionEventKind.BROKER_SELL_OUT for event in ordered_events):
        reasons.add("BROKER_SELL_OUT_REQUIRES_MANUAL_RECONCILIATION")
    if any(event.kind is OptionEventKind.CONTRACT_ADJUSTMENT for event in ordered_events):
        reasons.add("CONTRACT_ADJUSTMENT_REQUIRES_MANUAL_RECONCILIATION")

    exercise_events = tuple(
        event
        for event in ordered_events
        if event.kind in {OptionEventKind.ASSIGNMENT, OptionEventKind.EXERCISE}
    )
    if exercise_events:
        if any(not _event_economics_valid(binding, event) for event in exercise_events):
            reasons.add("OPTION_EVENT_ECONOMICS_CONTRADICTORY")
        expected_underlying = binding.baseline_underlying_quantity + sum(
            (event.underlying_quantity_delta for event in exercise_events),
            start=Decimal(0),
        )
        if underlying_quantity != expected_underlying:
            reasons.add("RESULTING_UNDERLYING_POSITION_CONTRADICTORY")
        for event in exercise_events:
            if _position_quantity(current_observation, AssetClass.OPTION, event.option_symbol) != 0:
                reasons.add("OPTION_POSITION_ACTIVITY_CONTRADICTORY")
        reasons.add("ASSIGNMENT_OR_EXERCISE_REQUIRES_MANUAL_RECONCILIATION")

    expiry_events = tuple(event for event in ordered_events if event.kind is OptionEventKind.EXPIRY)
    only_expiry = len(expiry_events) == len(ordered_events)
    expiry_by_symbol = {event.option_symbol: event for event in expiry_events}
    complete_expiry = (
        only_expiry
        and len(expiry_events) == 2
        and set(expiry_by_symbol) == bound_symbols
        and all(event.contracts == binding.quantity for event in expiry_events)
        and all(event.effective_date == binding.expiration_session_date for event in expiry_events)
        and all(
            event.underlying_quantity_delta == 0 and event.cash_delta == 0
            for event in expiry_events
        )
        and coverage_complete
        and current_observation.observed_at >= binding.expiration_activity_horizon
        and long_quantity == 0
        and short_quantity == 0
        and underlying_quantity == binding.baseline_underlying_quantity
    )
    if complete_expiry and not reasons:
        return _receipt(
            binding=binding,
            observation=current_observation,
            activity_coverage=activity_coverage,
            events=ordered_events,
            state=OptionReconciliationState.EXPIRY_FLAT_ATTESTED,
            reasons=("BOTH_LEGS_EXPIRY_AND_BOUND_POSITIONS_FLAT_ATTESTED",),
        )
    if expiry_events and not complete_expiry:
        reasons.add("EXPIRY_EVIDENCE_INCOMPLETE_OR_CONTRADICTORY")
    if not reasons:
        reasons.add("OPTION_ACTIVITY_REQUIRES_MANUAL_RECONCILIATION")
    return _receipt(
        binding=binding,
        observation=current_observation,
        activity_coverage=activity_coverage,
        events=ordered_events,
        state=OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED,
        reasons=tuple(reasons),
    )


class OptionEventJournal:
    """Separate append-only SQLite journal for bound reconciliation artifacts."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        try:
            self._connection = sqlite3.connect(
                self._path,
                timeout=30.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._migrate()
            self.validate_stored_state()
        except OptionEventConflict:
            if hasattr(self, "_connection"):
                self._connection.close()
            self._closed = True
            raise
        except sqlite3.Error as error:
            if hasattr(self, "_connection"):
                self._connection.close()
            self._closed = True
            raise OptionEventConflict("option-event journal is unavailable") from error

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> OptionEventJournal:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise OptionEventConflict("option-event journal is closed")

    @contextmanager
    def _transaction(self) -> Generator[None]:
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield
            except sqlite3.Error as error:
                self._connection.rollback()
                raise OptionEventConflict("option-event transaction failed") from error
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _migrate(self) -> None:
        current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if current == OPTION_EVENT_JOURNAL_SCHEMA_VERSION:
            return
        if current != 0:
            raise OptionEventConflict("option-event journal schema is unsupported")
        with self._transaction():
            self._connection.executescript(
                """
                CREATE TABLE option_event_bindings (
                    session_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    account_fingerprint_sha256 TEXT NOT NULL,
                    binding_sha256 TEXT NOT NULL UNIQUE,
                    binding_json BLOB NOT NULL,
                    PRIMARY KEY (session_id, lifecycle_id)
                );
                CREATE TABLE option_event_observations (
                    observation_sha256 TEXT PRIMARY KEY,
                    account_fingerprint_sha256 TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    observation_json BLOB NOT NULL
                );
                CREATE TABLE option_activity_coverages (
                    coverage_sha256 TEXT PRIMARY KEY,
                    account_fingerprint_sha256 TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    coverage_json BLOB NOT NULL
                );
                CREATE TABLE normalized_option_events (
                    account_fingerprint_sha256 TEXT NOT NULL,
                    activity_id TEXT NOT NULL,
                    binding_sha256 TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL UNIQUE,
                    event_json BLOB NOT NULL,
                    PRIMARY KEY (account_fingerprint_sha256, activity_id),
                    FOREIGN KEY (binding_sha256) REFERENCES option_event_bindings(binding_sha256)
                );
                CREATE TABLE option_event_receipts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_sha256 TEXT NOT NULL UNIQUE,
                    binding_sha256 TEXT NOT NULL,
                    observation_sha256 TEXT NOT NULL,
                    activity_coverage_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    receipt_json BLOB NOT NULL,
                    FOREIGN KEY (binding_sha256) REFERENCES option_event_bindings(binding_sha256),
                    FOREIGN KEY (observation_sha256)
                        REFERENCES option_event_observations(observation_sha256),
                    FOREIGN KEY (activity_coverage_sha256)
                        REFERENCES option_activity_coverages(coverage_sha256)
                );
                """
            )
            for table in (
                "option_event_bindings",
                "option_event_observations",
                "option_activity_coverages",
                "normalized_option_events",
                "option_event_receipts",
            ):
                self._connection.execute(
                    f"""
                    CREATE TRIGGER {table}_no_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'append-only table'); END
                    """
                )
                self._connection.execute(
                    f"""
                    CREATE TRIGGER {table}_no_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'append-only table'); END
                    """
                )
            self._connection.execute(f"PRAGMA user_version = {OPTION_EVENT_JOURNAL_SCHEMA_VERSION}")

    def validate_stored_state(self) -> None:
        """Reparse every immutable row and fail closed on restart corruption."""

        with self._lock:
            self._require_open()
            try:
                quick_check = self._connection.execute("PRAGMA quick_check").fetchone()[0]
                if quick_check != "ok":
                    raise OptionEventConflict("option-event journal integrity check failed")

                bindings: dict[str, OptionLifecycleBinding] = {}
                for row in self._connection.execute(
                    """
                    SELECT session_id, lifecycle_id, account_fingerprint_sha256,
                           binding_sha256, binding_json
                    FROM option_event_bindings
                    """
                ).fetchall():
                    binding = parse_option_lifecycle_binding(bytes(row["binding_json"]))
                    if (
                        binding.session_id != row["session_id"]
                        or binding.lifecycle_id != row["lifecycle_id"]
                        or binding.account_fingerprint_sha256 != row["account_fingerprint_sha256"]
                        or binding.binding_sha256 != row["binding_sha256"]
                    ):
                        raise OptionEventConflict("stored binding columns are invalid")
                    bindings[binding.binding_sha256] = binding

                observations: dict[str, OptionPortfolioObservation] = {}
                for row in self._connection.execute(
                    """
                    SELECT observation_sha256, account_fingerprint_sha256,
                           observed_at, observation_json
                    FROM option_event_observations
                    """
                ).fetchall():
                    observation = parse_option_portfolio_observation(bytes(row["observation_json"]))
                    if (
                        observation.observation_sha256 != row["observation_sha256"]
                        or observation.account_fingerprint_sha256
                        != row["account_fingerprint_sha256"]
                        or _timestamp_text(
                            observation.observed_at,
                            path="observation.observed_at",
                        )
                        != row["observed_at"]
                    ):
                        raise OptionEventConflict("stored observation columns are invalid")
                    observations[observation.observation_sha256] = observation

                coverages: dict[str, OptionActivityCoverage] = {}
                for row in self._connection.execute(
                    """
                    SELECT coverage_sha256, account_fingerprint_sha256,
                           observed_at, coverage_json
                    FROM option_activity_coverages
                    """
                ).fetchall():
                    coverage = parse_option_activity_coverage(bytes(row["coverage_json"]))
                    if (
                        coverage.coverage_sha256 != row["coverage_sha256"]
                        or coverage.account_fingerprint_sha256 != row["account_fingerprint_sha256"]
                        or _timestamp_text(coverage.observed_at, path="coverage.observed_at")
                        != row["observed_at"]
                    ):
                        raise OptionEventConflict("stored coverage columns are invalid")
                    coverages[coverage.coverage_sha256] = coverage

                events: dict[str, tuple[NormalizedOptionEvent, str]] = {}
                for row in self._connection.execute(
                    """
                    SELECT account_fingerprint_sha256, activity_id, binding_sha256,
                           event_sha256, event_json
                    FROM normalized_option_events
                    """
                ).fetchall():
                    event = parse_normalized_option_event(bytes(row["event_json"]))
                    binding = bindings.get(str(row["binding_sha256"]))
                    if (
                        event.account_fingerprint_sha256 != row["account_fingerprint_sha256"]
                        or event.activity_id != row["activity_id"]
                        or event.event_sha256 != row["event_sha256"]
                        or binding is None
                        or event.account_fingerprint_sha256 != binding.account_fingerprint_sha256
                        or event.execution_protocol_sha256 != binding.execution_protocol_sha256
                        or event.evidence_class is not binding.activation_evidence_class
                    ):
                        raise OptionEventConflict("stored event columns are invalid")
                    events[event.event_sha256] = (event, binding.binding_sha256)

                manual_accounts: set[tuple[str, str]] = set()
                terminal_bindings: set[str] = set()
                referenced_observations: set[str] = set()
                referenced_coverages: set[str] = set()
                referenced_events: set[str] = set()
                for row in self._connection.execute(
                    """
                    SELECT sequence, receipt_sha256, binding_sha256, observation_sha256,
                           activity_coverage_sha256, state, observed_at, receipt_json
                    FROM option_event_receipts
                    ORDER BY sequence ASC
                    """
                ).fetchall():
                    receipt = parse_option_event_reconciliation_receipt(bytes(row["receipt_json"]))
                    binding = bindings.get(receipt.binding_sha256)
                    observation = observations.get(receipt.observation_sha256)
                    coverage = coverages.get(receipt.activity_coverage_sha256)
                    receipt_events = tuple(
                        events.get(event_sha256) for event_sha256 in receipt.event_sha256s
                    )
                    if (
                        receipt.receipt_sha256 != row["receipt_sha256"]
                        or receipt.binding_sha256 != row["binding_sha256"]
                        or receipt.observation_sha256 != row["observation_sha256"]
                        or receipt.activity_coverage_sha256 != row["activity_coverage_sha256"]
                        or receipt.state.value != row["state"]
                        or _timestamp_text(receipt.observed_at, path="receipt.observed_at")
                        != row["observed_at"]
                        or binding is None
                        or observation is None
                        or coverage is None
                        or receipt.session_id != binding.session_id
                        or receipt.lifecycle_id != binding.lifecycle_id
                        or receipt.evidence_class is not binding.activation_evidence_class
                        or observation.account_fingerprint_sha256
                        != binding.account_fingerprint_sha256
                        or observation.execution_protocol_sha256
                        != binding.execution_protocol_sha256
                        or observation.evidence_class is not receipt.evidence_class
                        or observation.observed_at != receipt.observed_at
                        or coverage.account_fingerprint_sha256 != binding.account_fingerprint_sha256
                        or coverage.execution_protocol_sha256 != binding.execution_protocol_sha256
                        or coverage.evidence_class is not receipt.evidence_class
                        or coverage.observed_at != receipt.observed_at
                        or any(
                            item is None or item[1] != binding.binding_sha256
                            for item in receipt_events
                        )
                    ):
                        raise OptionEventConflict("stored receipt columns are invalid")
                    parsed_events = tuple(item[0] for item in receipt_events if item is not None)
                    underlying_quantity = _position_quantity(
                        observation,
                        AssetClass.EQUITY,
                        binding.underlying_symbol,
                    )
                    if (
                        receipt.long_option_quantity
                        != _position_quantity(
                            observation,
                            AssetClass.OPTION,
                            binding.long_symbol,
                        )
                        or receipt.short_option_quantity
                        != _position_quantity(
                            observation,
                            AssetClass.OPTION,
                            binding.short_symbol,
                        )
                        or receipt.underlying_quantity != underlying_quantity
                        or receipt.underlying_quantity_delta
                        != underlying_quantity - binding.baseline_underlying_quantity
                        or receipt.event_cash_delta
                        != sum(
                            (event.cash_delta for event in parsed_events),
                            start=Decimal(0),
                        )
                    ):
                        raise OptionEventConflict("stored receipt exposure is invalid")
                    account_key = (
                        binding.session_id,
                        binding.account_fingerprint_sha256,
                    )
                    derived = reconcile_option_events(
                        binding=binding,
                        current_observation=observation,
                        activity_coverage=coverage,
                        events=parsed_events,
                    )
                    if account_key in manual_accounts:
                        derived = _receipt(
                            binding=binding,
                            observation=observation,
                            activity_coverage=coverage,
                            events=parsed_events,
                            state=OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED,
                            reasons=(*derived.reason_codes, "PRIOR_MANUAL_STATE_STICKY"),
                        )
                    if binding.binding_sha256 in terminal_bindings or derived != receipt:
                        raise OptionEventConflict("stored receipt transition is invalid")
                    referenced_observations.add(receipt.observation_sha256)
                    referenced_coverages.add(receipt.activity_coverage_sha256)
                    referenced_events.update(receipt.event_sha256s)
                    if receipt.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED:
                        manual_accounts.add(account_key)
                    if receipt.state is OptionReconciliationState.EXPIRY_FLAT_ATTESTED:
                        terminal_bindings.add(binding.binding_sha256)

                if (
                    referenced_observations != set(observations)
                    or referenced_coverages != set(coverages)
                    or referenced_events != set(events)
                ):
                    raise OptionEventConflict("stored option-event inputs are orphaned")
            except (OptionEventRejected, TypeError, ValueError) as error:
                raise OptionEventConflict("stored option-event bytes are invalid") from error

    def _ensure_binding_locked(self, binding: OptionLifecycleBinding) -> bool:
        raw = option_lifecycle_binding_bytes(binding)
        row = self._connection.execute(
            """
            SELECT account_fingerprint_sha256, binding_sha256, binding_json
            FROM option_event_bindings
            WHERE session_id = ? AND lifecycle_id = ?
            """,
            (binding.session_id, binding.lifecycle_id),
        ).fetchone()
        if row is not None:
            if row["binding_sha256"] != binding.binding_sha256 or bytes(row["binding_json"]) != raw:
                raise OptionEventConflict("lifecycle is already bound to different canonical terms")
            if row["account_fingerprint_sha256"] != binding.account_fingerprint_sha256:
                raise OptionEventConflict("stored lifecycle account identity is invalid")
            return False
        try:
            self._connection.execute(
                """
                INSERT INTO option_event_bindings
                (session_id, lifecycle_id, account_fingerprint_sha256,
                 binding_sha256, binding_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    binding.session_id,
                    binding.lifecycle_id,
                    binding.account_fingerprint_sha256,
                    binding.binding_sha256,
                    raw,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise OptionEventConflict("binding identity is already owned") from error
        return True

    def ensure_binding(self, binding: OptionLifecycleBinding) -> bool:
        """Insert an immutable binding, returning false for exact replay."""

        with self._transaction():
            return self._ensure_binding_locked(binding)

    def load_binding(self, session_id: str, lifecycle_id: str) -> OptionLifecycleBinding | None:
        session_id = _bounded_id(session_id, path="session_id")
        lifecycle_id = _bounded_id(lifecycle_id, path="lifecycle_id")
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                """
                SELECT binding_sha256, binding_json FROM option_event_bindings
                WHERE session_id = ? AND lifecycle_id = ?
                """,
                (session_id, lifecycle_id),
            ).fetchone()
        if row is None:
            return None
        try:
            value = parse_option_lifecycle_binding(bytes(row["binding_json"]))
        except OptionEventRejected as error:
            raise OptionEventConflict("stored binding is invalid") from error
        if (
            value.binding_sha256 != row["binding_sha256"]
            or value.session_id != session_id
            or value.lifecycle_id != lifecycle_id
        ):
            raise OptionEventConflict("stored binding identity is invalid")
        return value

    def _latest_receipt_locked(
        self, binding_sha256: str
    ) -> OptionEventReconciliationReceipt | None:
        row = self._connection.execute(
            """
                 SELECT binding_sha256, observation_sha256, activity_coverage_sha256,
                     state, observed_at, receipt_sha256, receipt_json
            FROM option_event_receipts WHERE binding_sha256 = ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (binding_sha256,),
        ).fetchone()
        if row is None:
            return None
        try:
            value = parse_option_event_reconciliation_receipt(bytes(row["receipt_json"]))
        except OptionEventRejected as error:
            raise OptionEventConflict("stored receipt is invalid") from error
        if (
            value.state.value != row["state"]
            or value.binding_sha256 != row["binding_sha256"]
            or value.observation_sha256 != row["observation_sha256"]
            or value.activity_coverage_sha256 != row["activity_coverage_sha256"]
            or _timestamp_text(value.observed_at, path="receipt.observed_at") != row["observed_at"]
            or value.receipt_sha256 != row["receipt_sha256"]
        ):
            raise OptionEventConflict("stored receipt columns disagree with canonical bytes")
        return value

    def latest_receipt(
        self, session_id: str, lifecycle_id: str
    ) -> OptionEventReconciliationReceipt | None:
        binding = self.load_binding(session_id, lifecycle_id)
        if binding is None:
            return None
        with self._lock:
            self._require_open()
            return self._latest_receipt_locked(binding.binding_sha256)

    def _store_observation_locked(self, observation: OptionPortfolioObservation) -> None:
        raw = option_portfolio_observation_bytes(observation)
        row = self._connection.execute(
            """
            SELECT account_fingerprint_sha256, observed_at, observation_json
            FROM option_event_observations WHERE observation_sha256 = ?
            """,
            (observation.observation_sha256,),
        ).fetchone()
        if row is not None:
            if (
                row["account_fingerprint_sha256"] != observation.account_fingerprint_sha256
                or row["observed_at"]
                != _timestamp_text(observation.observed_at, path="observation.observed_at")
                or bytes(row["observation_json"]) != raw
            ):
                raise OptionEventConflict("observation hash conflicts with stored bytes")
            return
        self._connection.execute(
            """
            INSERT INTO option_event_observations
            (observation_sha256, account_fingerprint_sha256, observed_at, observation_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                observation.observation_sha256,
                observation.account_fingerprint_sha256,
                _timestamp_text(observation.observed_at, path="observation.observed_at"),
                raw,
            ),
        )

    def _store_coverage_locked(self, coverage: OptionActivityCoverage) -> None:
        raw = option_activity_coverage_bytes(coverage)
        row = self._connection.execute(
            """
            SELECT account_fingerprint_sha256, observed_at, coverage_json
            FROM option_activity_coverages WHERE coverage_sha256 = ?
            """,
            (coverage.coverage_sha256,),
        ).fetchone()
        if row is not None:
            if (
                row["account_fingerprint_sha256"] != coverage.account_fingerprint_sha256
                or row["observed_at"]
                != _timestamp_text(coverage.observed_at, path="coverage.observed_at")
                or bytes(row["coverage_json"]) != raw
            ):
                raise OptionEventConflict("coverage hash conflicts with stored bytes")
            return
        self._connection.execute(
            """
            INSERT INTO option_activity_coverages
            (coverage_sha256, account_fingerprint_sha256, observed_at, coverage_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                coverage.coverage_sha256,
                coverage.account_fingerprint_sha256,
                _timestamp_text(coverage.observed_at, path="coverage.observed_at"),
                raw,
            ),
        )

    def _account_manual_locked(self, binding: OptionLifecycleBinding) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM option_event_receipts AS receipt
            JOIN option_event_bindings AS binding
              ON binding.binding_sha256 = receipt.binding_sha256
            WHERE binding.session_id = ?
              AND binding.account_fingerprint_sha256 = ?
              AND receipt.state = ?
            LIMIT 1
            """,
            (
                binding.session_id,
                binding.account_fingerprint_sha256,
                OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED.value,
            ),
        ).fetchone()
        return row is not None

    def _claim_event_locked(
        self,
        binding: OptionLifecycleBinding,
        event: NormalizedOptionEvent,
    ) -> None:
        raw = normalized_option_event_bytes(event)
        row = self._connection.execute(
            """
            SELECT binding_sha256, event_sha256, event_json FROM normalized_option_events
            WHERE account_fingerprint_sha256 = ? AND activity_id = ?
            """,
            (event.account_fingerprint_sha256, event.activity_id),
        ).fetchone()
        if row is not None:
            if (
                row["binding_sha256"] != binding.binding_sha256
                or row["event_sha256"] != event.event_sha256
                or bytes(row["event_json"]) != raw
            ):
                raise OptionEventConflict("account activity is already attributed differently")
            return
        try:
            self._connection.execute(
                """
                INSERT INTO normalized_option_events
                (account_fingerprint_sha256, activity_id, binding_sha256, event_sha256, event_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.account_fingerprint_sha256,
                    event.activity_id,
                    binding.binding_sha256,
                    event.event_sha256,
                    raw,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise OptionEventConflict("event identity is already attributed") from error

    def record_reconciliation(
        self,
        *,
        binding: OptionLifecycleBinding,
        current_observation: OptionPortfolioObservation,
        activity_coverage: OptionActivityCoverage,
        events: Sequence[NormalizedOptionEvent],
    ) -> OptionEventReconciliationReceipt:
        """Atomically claim activities and append one restart-safe reconciliation receipt."""

        ordered_events = tuple(sorted(events, key=lambda event: event.activity_id))
        candidate = reconcile_option_events(
            binding=binding,
            current_observation=current_observation,
            activity_coverage=activity_coverage,
            events=ordered_events,
        )
        with self._transaction():
            self._ensure_binding_locked(binding)
            previous = self._latest_receipt_locked(binding.binding_sha256)
            if previous is not None and previous.receipt_sha256 == candidate.receipt_sha256:
                return previous
            if previous is not None and current_observation.observed_at <= previous.observed_at:
                raise OptionEventConflict("reconciliation observations must advance monotonically")
            if (
                previous is not None
                and previous.state is OptionReconciliationState.EXPIRY_FLAT_ATTESTED
            ):
                raise OptionEventConflict("expiry-flat lifecycle is immutable")
            if self._account_manual_locked(binding):
                candidate = _receipt(
                    binding=binding,
                    observation=current_observation,
                    activity_coverage=activity_coverage,
                    events=ordered_events,
                    state=OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED,
                    reasons=(*candidate.reason_codes, "PRIOR_MANUAL_STATE_STICKY"),
                )
            row = self._connection.execute(
                "SELECT receipt_json FROM option_event_receipts WHERE receipt_sha256 = ?",
                (candidate.receipt_sha256,),
            ).fetchone()
            if row is not None:
                stored = parse_option_event_reconciliation_receipt(bytes(row["receipt_json"]))
                if stored != candidate:
                    raise OptionEventConflict("receipt hash conflicts with stored bytes")
                return stored
            self._store_observation_locked(current_observation)
            self._store_coverage_locked(activity_coverage)
            for event in ordered_events:
                self._claim_event_locked(binding, event)
            raw = option_event_reconciliation_receipt_bytes(candidate)
            self._connection.execute(
                """
                INSERT INTO option_event_receipts
                (receipt_sha256, binding_sha256, observation_sha256,
                 activity_coverage_sha256, state, observed_at, receipt_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.receipt_sha256,
                    candidate.binding_sha256,
                    candidate.observation_sha256,
                    candidate.activity_coverage_sha256,
                    candidate.state.value,
                    _timestamp_text(candidate.observed_at, path="receipt.observed_at"),
                    raw,
                ),
            )
        return candidate

    def activity_owner(
        self,
        account_fingerprint_sha256: str,
        activity_id: str,
    ) -> str | None:
        account_fingerprint_sha256 = _digest(
            account_fingerprint_sha256,
            path="account_fingerprint_sha256",
        )
        activity_id = _bounded_id(activity_id, path="activity_id")
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                """
                SELECT binding_sha256 FROM normalized_option_events
                WHERE account_fingerprint_sha256 = ? AND activity_id = ?
                """,
                (account_fingerprint_sha256, activity_id),
            ).fetchone()
        return None if row is None else str(row["binding_sha256"])

    def account_requires_manual_reconciliation(
        self,
        *,
        session_id: str,
        account_fingerprint_sha256: str,
    ) -> bool:
        """Return the sticky account/session entry gate without exposing raw state."""

        session_id = _bounded_id(session_id, path="session_id")
        account_fingerprint_sha256 = _digest(
            account_fingerprint_sha256,
            path="account_fingerprint_sha256",
        )
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                """
                SELECT 1 FROM option_event_receipts AS receipt
                JOIN option_event_bindings AS binding
                  ON binding.binding_sha256 = receipt.binding_sha256
                WHERE binding.session_id = ?
                  AND binding.account_fingerprint_sha256 = ?
                  AND receipt.state = ?
                LIMIT 1
                """,
                (
                    session_id,
                    account_fingerprint_sha256,
                    OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED.value,
                ),
            ).fetchone()
        return row is not None


__all__ = [
    "NORMALIZED_OPTION_EVENT_SCHEMA",
    "OPTION_ACTIVITY_COVERAGE_SCHEMA",
    "OPTION_EVENT_JOURNAL_SCHEMA_VERSION",
    "OPTION_EVENT_RECEIPT_SCHEMA",
    "OPTION_EVENT_SCHEMA_VERSION",
    "OPTION_LIFECYCLE_BINDING_SCHEMA",
    "OPTION_PORTFOLIO_OBSERVATION_SCHEMA",
    "AssetClass",
    "EvidenceClass",
    "NormalizedOptionEvent",
    "OptionActivityCoverage",
    "OptionEventConflict",
    "OptionEventJournal",
    "OptionEventKind",
    "OptionEventReconciliationReceipt",
    "OptionEventRejected",
    "OptionEventStatus",
    "OptionLifecycleBinding",
    "OptionPortfolioObservation",
    "OptionReconciliationState",
    "PortfolioPosition",
    "normalized_option_event_bytes",
    "option_activity_coverage_bytes",
    "option_event_reconciliation_receipt_bytes",
    "option_lifecycle_binding_bytes",
    "option_portfolio_observation_bytes",
    "parse_normalized_option_event",
    "parse_option_activity_coverage",
    "parse_option_event_reconciliation_receipt",
    "parse_option_lifecycle_binding",
    "parse_option_portfolio_observation",
    "reconcile_option_events",
]
