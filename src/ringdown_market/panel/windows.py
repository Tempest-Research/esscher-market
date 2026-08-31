"""Fail-closed validation for synchronized panel market-window provenance records."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..contracts.replay_evidence import (
    ReplayEvidenceRejected,
    _decode,
    _sha256,
    _strict_object,
    _text,
    _text_list,
    _timestamp,
)
from .manifest import PanelRejected, PanelRejectionReason

MARKET_WINDOW_SCHEMA = "ringdown.panel_market_window_provenance"
_WINDOW_ROLES = ("issuer", "market", "sector")
_MINIMUM_BARS = 60

_WINDOW_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "event_id",
        "entry_session_id",
        "entry_session_date",
        "timeframe",
        "adjustment",
        "window_start",
        "window_end",
        "symbols",
        "retrieved_at",
        "redistribution_status",
        "entitlement_note",
        "limitations",
        "bars",
        "status",
    }
)
_BAR_FIELDS = frozenset(
    {
        "symbol",
        "status",
        "bar_count",
        "first_bar_at",
        "last_bar_at",
        "content_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class ValidatedMarketWindow:
    """Identity of a complete synchronized market window."""

    event_id: str
    bar_count: int
    window_sha256: str


def _reject(reason: PanelRejectionReason, path: str, detail: str) -> None:
    raise PanelRejected(reason, path, detail)


def validate_market_window(
    raw: bytes,
    *,
    expected_event_id: str,
    path: str = "market_window",
) -> ValidatedMarketWindow:
    try:
        return _validate_market_window(raw, expected_event_id=expected_event_id, path=path)
    except ReplayEvidenceRejected as error:
        raise PanelRejected(
            PanelRejectionReason(error.reason.value), error.path, error.detail
        ) from error


def _validate_market_window(
    raw: bytes,
    *,
    expected_event_id: str,
    path: str,
) -> ValidatedMarketWindow:
    window = _strict_object(_decode(raw, path=path), path=path, fields=_WINDOW_FIELDS)
    if window["schema"] != MARKET_WINDOW_SCHEMA or window["schema_version"] != 1:
        _reject(
            PanelRejectionReason.UNSUPPORTED_SCHEMA,
            path,
            "unsupported market-window schema or version",
        )
    event_id = _text(window["event_id"], path=f"{path}.event_id")
    if event_id != expected_event_id:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            f"{path}.event_id",
            "market window belongs to a different frozen event",
        )
    if window["timeframe"] != "1Min":
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            f"{path}.timeframe",
            "panel windows use the frozen one-minute timeframe",
        )
    if window["adjustment"] != "all":
        _reject(
            PanelRejectionReason.SELECTION_RULE_VIOLATION,
            f"{path}.adjustment",
            "panel windows use fully split- and dividend-adjusted bars",
        )
    _timestamp(window["window_start"], path=f"{path}.window_start")
    _timestamp(window["window_end"], path=f"{path}.window_end")
    _timestamp(window["retrieved_at"], path=f"{path}.retrieved_at")
    if window["redistribution_status"] != "METADATA_AND_HASH_ONLY":
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.redistribution_status",
            "licensed market bars are not redistributed",
        )
    _text(window["entitlement_note"], path=f"{path}.entitlement_note")
    qualifiers = set(_text_list(window["limitations"], path=f"{path}.limitations"))
    required = {"INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE", "NO_OUTCOME_VALUES", "NO_BROKER_EXECUTION"}
    if not required <= qualifiers:
        _reject(
            PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH,
            f"{path}.limitations",
            "indicative and non-alpha limitations must stay explicit",
        )
    if window["status"] != "COMPLETE":
        _reject(
            PanelRejectionReason.MISSING_PRICE_POINT,
            f"{path}.status",
            "panel requires a complete synchronized window for every symbol",
        )
    symbols = window["symbols"]
    bars = window["bars"]
    if not isinstance(symbols, Mapping) or not isinstance(bars, Mapping):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "symbols and bars must be objects")
    bar_counts: set[int] = set()
    first_ats: set[str] = set()
    last_ats: set[str] = set()
    for role in _WINDOW_ROLES:
        if role not in symbols or role not in bars:
            _reject(
                PanelRejectionReason.MISSING_FIELD,
                f"{path}.bars.{role}",
                f"synchronized window requires the {role} series",
            )
        record = _strict_object(bars[role], path=f"{path}.bars.{role}", fields=_BAR_FIELDS)
        if record["status"] != "PRESENT":
            _reject(
                PanelRejectionReason.MISSING_PRICE_POINT,
                f"{path}.bars.{role}.status",
                "every synchronized series must be present",
            )
        if record["symbol"] != symbols[role]:
            _reject(
                PanelRejectionReason.IDENTITY_MISMATCH,
                f"{path}.bars.{role}.symbol",
                "series symbol differs from the frozen window symbol",
            )
        _sha256(record["content_sha256"], path=f"{path}.bars.{role}.content_sha256")
        bar_count = record["bar_count"]
        if (
            not isinstance(bar_count, int)
            or isinstance(bar_count, bool)
            or bar_count < _MINIMUM_BARS
        ):
            _reject(
                PanelRejectionReason.MISSING_PRICE_POINT,
                f"{path}.bars.{role}.bar_count",
                f"window requires at least {_MINIMUM_BARS} one-minute bars",
            )
        first_ats.add(_text(record["first_bar_at"], path=f"{path}.bars.{role}.first_bar_at"))
        last_ats.add(_text(record["last_bar_at"], path=f"{path}.bars.{role}.last_bar_at"))
        bar_counts.add(bar_count)
    if len(bar_counts) != 1:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            f"{path}.bars",
            "issuer, market, and sector windows carry different bar counts",
        )
    if len(first_ats) != 1 or len(last_ats) != 1:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            f"{path}.bars",
            "issuer, market, and sector windows are not timestamp-aligned",
        )
    return ValidatedMarketWindow(
        event_id=event_id,
        bar_count=bar_counts.pop(),
        window_sha256=hashlib.sha256(raw).hexdigest(),
    )


def validate_market_window_set(
    window_bytes: Sequence[bytes],
    expected_event_ids: Sequence[str],
) -> tuple[ValidatedMarketWindow, ...]:
    try:
        return _validate_market_window_set(window_bytes, expected_event_ids)
    except ReplayEvidenceRejected as error:
        raise PanelRejected(
            PanelRejectionReason(error.reason.value), error.path, error.detail
        ) from error


def _validate_market_window_set(
    window_bytes: Sequence[bytes],
    expected_event_ids: Sequence[str],
) -> tuple[ValidatedMarketWindow, ...]:
    if len(window_bytes) != len(expected_event_ids):
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            "market_windows",
            "window set must match the frozen event universe one-for-one",
        )
    seen: set[str] = set()
    validated = []
    for index, (raw, event_id) in enumerate(zip(window_bytes, expected_event_ids, strict=True)):
        record = validate_market_window(
            raw, expected_event_id=event_id, path=f"market_windows[{index}]"
        )
        if record.event_id in seen:
            _reject(
                PanelRejectionReason.DUPLICATE_EVENT_ID,
                f"market_windows[{index}].event_id",
                "market windows must be unique per event",
            )
        seen.add(record.event_id)
        validated.append(record)
    return tuple(validated)


__all__ = [
    "MARKET_WINDOW_SCHEMA",
    "ValidatedMarketWindow",
    "validate_market_window",
    "validate_market_window_set",
]
