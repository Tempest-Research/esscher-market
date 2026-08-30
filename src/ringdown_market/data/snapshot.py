"""Strict point-in-time strategy snapshot compiler (`esscher.strategy_snapshot/v1`)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from math import isfinite, log

from ringdown_market.strategy.policy import StrategyPolicy

from .beta import BetaEstimationRejected, estimate_frozen_betas
from .provenance import (
    ADJUSTMENT_POLICY_V1,
    HASH_REPRESENTATION,
    BarObservation,
    CorporateActionReceipt,
    CorporateActionType,
    EstimationPoint,
    RedistributionStatus,
    SourceEvidence,
    utc_second,
)

SNAPSHOT_SCHEMA = "esscher.strategy_snapshot"
SNAPSHOT_SCHEMA_VERSION = 1
FEATURE_COMPUTATION_PRECISION = "%.12f"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


STRATEGY_SNAPSHOT_PROTOCOL = {
    "schema": "esscher.strategy_snapshot_protocol",
    "schema_version": 1,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
    "cutoff_rule": "OBSERVATION_WINDOW_END",
    "adjustment_policy": ADJUSTMENT_POLICY_V1,
    "hash_representation": HASH_REPRESENTATION,
    "data_qualifiers": ["INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE"],
    "imputation_forbidden": True,
    "post_cutoff_evidence_forbidden": True,
}
STRATEGY_SNAPSHOT_PROTOCOL_SHA256 = hashlib.sha256(
    _canonical_json_bytes(STRATEGY_SNAPSHOT_PROTOCOL)
).hexdigest()


class SnapshotRejectionReason(StrEnum):
    """Stable fail-closed reasons a snapshot cannot become eligible."""

    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
    INVALID_EVENT = "INVALID_EVENT"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    POST_CUTOFF_EVIDENCE = "POST_CUTOFF_EVIDENCE"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    MISSING_BARS = "MISSING_BARS"
    UNSYNCHRONIZED_WINDOW = "UNSYNCHRONIZED_WINDOW"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    INELIGIBLE_UNIVERSE = "INELIGIBLE_UNIVERSE"
    CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
    REDISTRIBUTION_VIOLATION = "REDISTRIBUTION_VIOLATION"
    BETA_ESTIMATION_FAILED = "BETA_ESTIMATION_FAILED"


class SnapshotRejected(ValueError):
    """Raised for structural violations of the snapshot contract."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True, slots=True)
class SnapshotEvent:
    """Identity, session, and timing for one scheduled earnings event."""

    event_id: str
    issuer: str
    ticker: str
    sector_proxy: str
    session_id: str
    session_open_at: datetime
    timing_bucket: str
    window_start_at: datetime
    window_end_at: datetime
    decision_cutoff: datetime

    def __post_init__(self) -> None:
        for field in ("event_id", "issuer", "ticker", "sector_proxy", "session_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise SnapshotRejected(f"{field} must be non-empty text")
        if self.timing_bucket not in {"BEFORE_OPEN", "AFTER_CLOSE"}:
            raise SnapshotRejected("timing_bucket must be BEFORE_OPEN or AFTER_CLOSE")
        for field in ("session_open_at", "window_start_at", "window_end_at", "decision_cutoff"):
            value = getattr(self, field)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise SnapshotRejected(f"{field} must be timezone-aware")
            if value.microsecond != 0:
                raise SnapshotRejected(f"{field} must use second precision")
        if not self.window_start_at < self.window_end_at:
            raise SnapshotRejected("observation window must be strictly ordered")
        if self.decision_cutoff != self.window_end_at:
            raise SnapshotRejected("the frozen cutoff rule is the observation window end")


@dataclass(frozen=True, slots=True)
class CompiledSnapshot:
    """One deterministic snapshot payload with canonical bytes and identity."""

    payload: Mapping[str, object]
    raw: bytes
    sha256: str
    eligible: bool
    rejection_reasons: tuple[str, ...]


def _feature_value_text(value: float) -> str:
    if not isfinite(value):
        raise SnapshotRejected("feature value must be finite")
    return FEATURE_COMPUTATION_PRECISION % value


def _evidence_payload(record: SourceEvidence) -> dict[str, object]:
    return {
        "evidence_id": record.evidence_id,
        "event_id": record.event_id,
        "issuer": record.issuer,
        "source_kind": record.source_kind.value,
        "source_url": record.source_url,
        "publisher": record.publisher,
        "published_at": utc_second(record.published_at),
        "published_at_type": record.published_at_type.value,
        "published_at_precision": record.published_at_precision,
        "accepted_at": utc_second(record.accepted_at) if record.accepted_at else None,
        "retrieved_at": utc_second(record.retrieved_at),
        "content_sha256": record.content_sha256,
        "hash_representation": HASH_REPRESENTATION,
        "entitlement_note": record.entitlement_note,
        "redistribution_note": record.redistribution.value,
        "field_status": "PRESENT",
    }


def _bar_payload(bar: BarObservation) -> dict[str, object]:
    return {
        "symbol": bar.symbol,
        "at": utc_second(bar.at),
        "price": str(bar.price),
        "raw_observed_at": utc_second(bar.raw_observed_at),
        "source_id": bar.source_id,
        "adjustment": bar.adjustment,
        "observation_type": "bar_close",
    }


def _corporate_action_payload(receipt: CorporateActionReceipt) -> dict[str, object]:
    return {
        "action_type": receipt.action_type.value,
        "effective_date": receipt.effective_date.isoformat(),
        "symbol": receipt.symbol,
        "factor": str(receipt.factor) if receipt.factor is not None else None,
        "to_symbol": receipt.to_symbol,
        "source_id": receipt.source_id,
    }


def _validate_evidence(
    evidence: Sequence[SourceEvidence],
    *,
    event: SnapshotEvent,
    reasons: list[str],
) -> datetime | None:
    if not evidence:
        reasons.append(SnapshotRejectionReason.MISSING_EVIDENCE.value)
        return None
    latest_public: datetime | None = None
    for record in evidence:
        if record.event_id != event.event_id or record.issuer != event.issuer:
            reasons.append(SnapshotRejectionReason.INVALID_EVENT.value)
            continue
        if record.published_at > event.decision_cutoff:
            reasons.append(SnapshotRejectionReason.POST_CUTOFF_EVIDENCE.value)
        if record.retrieved_at < record.published_at:
            reasons.append(SnapshotRejectionReason.STALE_OBSERVATION.value)
        if (
            record.redistribution is RedistributionStatus.METADATA_AND_HASH_ONLY
            and record.raw_bytes is not None
        ):
            reasons.append(SnapshotRejectionReason.REDISTRIBUTION_VIOLATION.value)
        if latest_public is None or record.published_at > latest_public:
            latest_public = record.published_at
    return latest_public


def _window_bars(
    bars: Sequence[BarObservation],
    *,
    event: SnapshotEvent,
    reasons: list[str],
) -> tuple[Decimal, Decimal] | None:
    if not bars:
        reasons.append(SnapshotRejectionReason.MISSING_BARS.value)
        return None
    start = next((bar for bar in bars if bar.at == event.window_start_at), None)
    end = next((bar for bar in bars if bar.at == event.window_end_at), None)
    if start is None or end is None:
        reasons.append(SnapshotRejectionReason.UNSYNCHRONIZED_WINDOW.value)
        return None
    for bar in bars:
        if bar.adjustment != ADJUSTMENT_POLICY_V1:
            reasons.append(SnapshotRejectionReason.UNSYNCHRONIZED_WINDOW.value)
            return None
        if bar.raw_observed_at > event.decision_cutoff:
            reasons.append(SnapshotRejectionReason.STALE_OBSERVATION.value)
        if bar.at > event.decision_cutoff:
            reasons.append(SnapshotRejectionReason.POST_CUTOFF_EVIDENCE.value)
    return start.price, end.price


def _corporate_action_receipts(
    receipts: Mapping[str, Sequence[CorporateActionReceipt]],
    *,
    event: SnapshotEvent,
    reasons: list[str],
) -> list[CorporateActionReceipt]:
    flat: list[CorporateActionReceipt] = []
    for symbol in (event.ticker,):
        for receipt in receipts.get(symbol, ()):
            if receipt.symbol != symbol:
                reasons.append(SnapshotRejectionReason.CORPORATE_ACTION_UNRESOLVED.value)
                continue
            if receipt.action_type is CorporateActionType.SYMBOL_CHANGE and not receipt.to_symbol:
                reasons.append(SnapshotRejectionReason.CORPORATE_ACTION_UNRESOLVED.value)
                continue
            flat.append(receipt)
    return flat


def compile_strategy_snapshot(
    *,
    policy: StrategyPolicy,
    expected_policy_sha256: str,
    event: SnapshotEvent,
    evidence: Sequence[SourceEvidence],
    opening_bars: Mapping[str, Sequence[BarObservation]],
    estimation_series: Sequence[EstimationPoint],
    corporate_actions: Mapping[str, Sequence[CorporateActionReceipt]] | None = None,
) -> CompiledSnapshot:
    """Compile one deterministic point-in-time snapshot; ineligibility stays explicit."""

    if policy.sha256 != expected_policy_sha256:
        raise SnapshotRejected(
            f"{SnapshotRejectionReason.POLICY_HASH_MISMATCH.value}: policy identity mismatch"
        )

    reasons: list[str] = []
    latest_public = _validate_evidence(evidence, event=event, reasons=reasons)
    windows: dict[str, tuple[Decimal, Decimal] | None] = {}
    for symbol in (event.ticker, policy.beta_policy.market_proxy, event.sector_proxy):
        windows[symbol] = _window_bars(opening_bars.get(symbol, ()), event=event, reasons=reasons)

    market_beta: float | None = None
    sector_beta: float | None = None
    opening_features: dict[str, float] = {}
    if all(windows.values()):
        try:
            market_beta, sector_beta = estimate_frozen_betas(estimation_series)
        except BetaEstimationRejected:
            reasons.append(SnapshotRejectionReason.BETA_ESTIMATION_FAILED.value)
        for symbol, feature_id in (
            (event.ticker, "opening_return/v1"),
            (policy.beta_policy.market_proxy, "market_opening_return/v1"),
            (event.sector_proxy, "sector_opening_return/v1"),
        ):
            start_price, end_price = windows[symbol]  # type: ignore[misc]
            if start_price <= 0 or end_price <= 0:
                reasons.append(SnapshotRejectionReason.NON_FINITE_VALUE.value)
                continue
            opening_features[feature_id] = log(float(end_price) / float(start_price))

    stock_window = windows.get(event.ticker)
    if stock_window is not None and stock_window[0] < policy.universe.minimum_price:
        reasons.append(SnapshotRejectionReason.INELIGIBLE_UNIVERSE.value)

    receipts = _corporate_action_receipts(corporate_actions or {}, event=event, reasons=reasons)

    feature_payloads: list[dict[str, object]] = []
    if not reasons and latest_public is not None:
        evidence_identity = _canonical_json_bytes(
            [record.content_sha256 for record in sorted(evidence, key=lambda r: r.evidence_id)]
        )
        evidence_value_sha = hashlib.sha256(evidence_identity).hexdigest()
        for feature_id in ("earnings_numeric/v1", "guidance_statement/v1"):
            feature_payloads.append(
                {
                    "feature_id": feature_id,
                    "definition_version": feature_id,
                    "value_sha256": evidence_value_sha,
                    "source_max_public_at": utc_second(latest_public),
                    "feature_computed_at": utc_second(event.decision_cutoff),
                    "dependency_check": "ELIGIBLE",
                    "field_status": "PRESENT",
                }
            )
        for feature_id, value in sorted(opening_features.items()):
            value_text = _feature_value_text(value)
            feature_payloads.append(
                {
                    "feature_id": feature_id,
                    "definition_version": feature_id,
                    "value_text": value_text,
                    "value_sha256": hashlib.sha256(value_text.encode("utf-8")).hexdigest(),
                    "source_max_public_at": utc_second(event.window_end_at),
                    "feature_computed_at": utc_second(event.decision_cutoff),
                    "dependency_check": "ELIGIBLE",
                    "field_status": "PRESENT",
                }
            )
        for feature_id, beta in sorted(
            (("market_beta/v1", market_beta), ("sector_beta/v1", sector_beta))
        ):
            value_text = _feature_value_text(beta)  # type: ignore[arg-type]
            feature_payloads.append(
                {
                    "feature_id": feature_id,
                    "definition_version": feature_id,
                    "value_text": value_text,
                    "value_sha256": hashlib.sha256(value_text.encode("utf-8")).hexdigest(),
                    "source_max_public_at": utc_second(event.window_end_at),
                    "feature_computed_at": utc_second(event.decision_cutoff),
                    "dependency_check": "ELIGIBLE",
                    "field_status": "PRESENT",
                }
            )

    ordered_reasons = tuple(sorted(dict.fromkeys(reasons)))
    payload: dict[str, object] = {
        "schema": SNAPSHOT_SCHEMA,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "event_id": event.event_id,
        "issuer": event.issuer,
        "ticker": event.ticker,
        "sector_proxy": event.sector_proxy,
        "session_id": event.session_id,
        "timing_bucket": event.timing_bucket,
        "session_open_at": utc_second(event.session_open_at),
        "window_start_at": utc_second(event.window_start_at),
        "window_end_at": utc_second(event.window_end_at),
        "decision_cutoff": utc_second(event.decision_cutoff),
        "feature_snapshot_at": utc_second(event.decision_cutoff),
        "policy_version": policy.policy_version,
        "policy_sha256": policy.sha256,
        "adjustment_policy": ADJUSTMENT_POLICY_V1,
        "eligibility": "ELIGIBLE" if not ordered_reasons else "INELIGIBLE",
        "rejection_reasons": list(ordered_reasons),
        "evidence": [
            _evidence_payload(record)
            for record in sorted(evidence, key=lambda record: record.evidence_id)
        ],
        "market_observations": [
            _bar_payload(bar)
            for symbol in sorted(opening_bars)
            for bar in sorted(opening_bars[symbol], key=lambda observation: observation.at)
        ],
        "corporate_actions": [
            _corporate_action_payload(receipt)
            for receipt in sorted(receipts, key=lambda item: item.effective_date)
        ],
        "features": feature_payloads,
        "data_qualifiers": ["INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE"],
    }
    raw = _canonical_json_bytes(payload)
    return CompiledSnapshot(
        payload=payload,
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        eligible=not ordered_reasons,
        rejection_reasons=ordered_reasons,
    )
