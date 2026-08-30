"""Inert capture orchestration for read-only strategy snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal

from ringdown_market.strategy.policy import StrategyPolicy

from .adapters import FakeSnapshotAdapters, collect_snapshot_inputs
from .provenance import (
    BarObservation,
    CorporateActionReceipt,
    CorporateActionType,
    EstimationPoint,
    EvidenceSourceKind,
    ProvenanceRejected,
    PublishedAtType,
    RedistributionStatus,
    SourceEvidence,
    parse_price,
)
from .snapshot import SnapshotEvent, SnapshotRejected, compile_strategy_snapshot

CAPTURE_REQUEST_SCHEMA = "esscher.snapshot_capture_request"
CAPTURE_REQUEST_SCHEMA_VERSION = 1


class CaptureRequestRejected(ValueError):
    """Raised when a capture request document fails the strict contract."""


def _reject(detail: str) -> None:
    raise CaptureRequestRejected(detail)


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(f"{label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _reject(f"{label} must be non-empty text")
    return value


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        _reject(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _reject(f"{label} must be a valid ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _reject(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        _reject(f"{label} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _reject(f"{label} must be a valid ISO-8601 date")


def _optional_datetime(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    return _datetime(value, label)


def _load_event(payload: Mapping[str, object]) -> SnapshotEvent:
    event = _require_mapping(payload.get("event"), "event")
    try:
        return SnapshotEvent(
            event_id=_text(event.get("event_id"), "event.event_id"),
            issuer=_text(event.get("issuer"), "event.issuer"),
            ticker=_text(event.get("ticker"), "event.ticker"),
            sector_proxy=_text(event.get("sector_proxy"), "event.sector_proxy"),
            session_id=_text(event.get("session_id"), "event.session_id"),
            session_open_at=_datetime(event.get("session_open_at"), "event.session_open_at"),
            timing_bucket=_text(event.get("timing_bucket"), "event.timing_bucket"),
            window_start_at=_datetime(event.get("window_start_at"), "event.window_start_at"),
            window_end_at=_datetime(event.get("window_end_at"), "event.window_end_at"),
            decision_cutoff=_datetime(event.get("decision_cutoff"), "event.decision_cutoff"),
        )
    except SnapshotRejected as error:
        raise CaptureRequestRejected(str(error)) from error


def _load_evidence(payload: Mapping[str, object]) -> list[SourceEvidence]:
    records_value = payload.get("evidence")
    if not isinstance(records_value, list):
        _reject("evidence must be a list")
    records: list[SourceEvidence] = []
    for index, item in enumerate(records_value):
        path = f"evidence[{index}]"
        record = _require_mapping(item, path)
        raw_value = record.get("raw_bytes_utf8")
        raw_bytes: bytes | None = None
        if raw_value is not None:
            if not isinstance(raw_value, str):
                _reject(f"{path}.raw_bytes_utf8 must be a string when present")
            raw_bytes = raw_value.encode("utf-8")
        redistribution_value = record.get("redistribution_note")
        try:
            redistribution = RedistributionStatus(redistribution_value)
        except ValueError:
            _reject(f"{path}.redistribution_note is not a registered status")
        try:
            records.append(
                SourceEvidence(
                    evidence_id=_text(record.get("evidence_id"), f"{path}.evidence_id"),
                    event_id=_text(record.get("event_id"), f"{path}.event_id"),
                    issuer=_text(record.get("issuer"), f"{path}.issuer"),
                    source_kind=EvidenceSourceKind(record.get("source_kind")),
                    source_url=_text(record.get("source_url"), f"{path}.source_url"),
                    publisher=_text(record.get("publisher"), f"{path}.publisher"),
                    published_at=_datetime(record.get("published_at"), f"{path}.published_at"),
                    published_at_type=PublishedAtType(record.get("published_at_type")),
                    published_at_precision=_text(
                        record.get("published_at_precision"), f"{path}.published_at_precision"
                    ),
                    accepted_at=_optional_datetime(
                        record.get("accepted_at"), f"{path}.accepted_at"
                    ),
                    retrieved_at=_datetime(record.get("retrieved_at"), f"{path}.retrieved_at"),
                    content_sha256=_text(record.get("content_sha256"), f"{path}.content_sha256"),
                    entitlement_note=_text(
                        record.get("entitlement_note"), f"{path}.entitlement_note"
                    ),
                    redistribution=redistribution,
                    raw_bytes=raw_bytes,
                )
            )
        except (ProvenanceRejected, ValueError) as error:
            _reject(f"{path}: {error}")
    return records


def _load_bars(payload: Mapping[str, object]) -> dict[str, list[BarObservation]]:
    bars_value = payload.get("opening_bars")
    if not isinstance(bars_value, Mapping):
        _reject("opening_bars must be an object keyed by symbol")
    bars: dict[str, list[BarObservation]] = {}
    for symbol, observations in bars_value.items():
        if not isinstance(observations, list):
            _reject(f"opening_bars.{symbol} must be a list")
        loaded: list[BarObservation] = []
        for index, item in enumerate(observations):
            path = f"opening_bars.{symbol}[{index}]"
            observation = _require_mapping(item, path)
            try:
                loaded.append(
                    BarObservation(
                        symbol=_text(observation.get("symbol"), f"{path}.symbol"),
                        at=_datetime(observation.get("at"), f"{path}.at"),
                        price=parse_price(
                            _text(observation.get("price"), f"{path}.price"), f"{path}.price"
                        ),
                        raw_observed_at=_datetime(
                            observation.get("raw_observed_at"), f"{path}.raw_observed_at"
                        ),
                        source_id=_text(observation.get("source_id"), f"{path}.source_id"),
                        adjustment=_text(observation.get("adjustment"), f"{path}.adjustment"),
                    )
                )
            except ProvenanceRejected as error:
                _reject(f"{path}: {error}")
        bars[str(symbol)] = loaded
    return bars


def _load_estimation(payload: Mapping[str, object]) -> list[EstimationPoint]:
    series_value = payload.get("estimation_series")
    if not isinstance(series_value, list):
        _reject("estimation_series must be a list")
    points: list[EstimationPoint] = []
    for index, item in enumerate(series_value):
        path = f"estimation_series[{index}]"
        point = _require_mapping(item, path)
        try:
            points.append(
                EstimationPoint(
                    at=_datetime(point.get("at"), f"{path}.at"),
                    stock_return=float(point.get("stock_return")),  # type: ignore[arg-type]
                    market_return=float(point.get("market_return")),  # type: ignore[arg-type]
                    sector_return=float(point.get("sector_return")),  # type: ignore[arg-type]
                )
            )
        except (ProvenanceRejected, TypeError, ValueError) as error:
            _reject(f"{path}: {error}")
    return points


def _load_corporate_actions(
    payload: Mapping[str, object],
) -> dict[str, list[CorporateActionReceipt]]:
    actions_value = payload.get("corporate_actions") or {}
    if not isinstance(actions_value, Mapping):
        _reject("corporate_actions must be an object keyed by symbol")
    actions: dict[str, list[CorporateActionReceipt]] = {}
    for symbol, receipts in actions_value.items():
        if not isinstance(receipts, list):
            _reject(f"corporate_actions.{symbol} must be a list")
        loaded: list[CorporateActionReceipt] = []
        for index, item in enumerate(receipts):
            path = f"corporate_actions.{symbol}[{index}]"
            receipt = _require_mapping(item, path)
            factor_value = receipt.get("factor")
            try:
                loaded.append(
                    CorporateActionReceipt(
                        action_type=CorporateActionType(receipt.get("action_type")),
                        effective_date=_date(
                            receipt.get("effective_date"), f"{path}.effective_date"
                        ),
                        symbol=_text(receipt.get("symbol"), f"{path}.symbol"),
                        factor=Decimal(factor_value) if factor_value is not None else None,
                        to_symbol=receipt.get("to_symbol") if receipt.get("to_symbol") else None,
                        source_id=_text(receipt.get("source_id"), f"{path}.source_id"),
                    )
                )
            except (ProvenanceRejected, ValueError) as error:
                _reject(f"{path}: {error}")
        actions[str(symbol)] = loaded
    return actions


def run_capture_request(
    raw: bytes,
    *,
    policy: StrategyPolicy,
    expected_policy_sha256: str,
):
    """Compile one snapshot from a strict capture request; never touches a network."""

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _reject(f"capture request is not valid JSON: {error}")
    payload = _require_mapping(payload, "capture_request")
    if payload.get("schema") != CAPTURE_REQUEST_SCHEMA:
        _reject("capture request schema is not supported")
    if payload.get("schema_version") != CAPTURE_REQUEST_SCHEMA_VERSION:
        _reject("capture request schema version is not supported")

    event = _load_event(payload)
    evidence = _load_evidence(payload)
    opening_bars = _load_bars(payload)
    estimation_series = _load_estimation(payload)
    corporate_actions = _load_corporate_actions(payload)

    adapters = FakeSnapshotAdapters(
        evidence=evidence,
        opening_bars=opening_bars,
        estimation_series={event.ticker: estimation_series},
        corporate_actions=corporate_actions,
    )
    collected = collect_snapshot_inputs(
        event_id=event.event_id,
        ticker=event.ticker,
        market_proxy=policy.beta_policy.market_proxy,
        sector_proxy=event.sector_proxy,
        evidence_adapter=adapters,
        market_adapter=adapters,
    )
    return compile_strategy_snapshot(
        policy=policy,
        expected_policy_sha256=expected_policy_sha256,
        event=event,
        evidence=collected["evidence"],  # type: ignore[arg-type]
        opening_bars=collected["opening_bars"],  # type: ignore[arg-type]
        estimation_series=collected["estimation_series"],  # type: ignore[arg-type]
        corporate_actions=collected["corporate_actions"],  # type: ignore[arg-type]
    )
