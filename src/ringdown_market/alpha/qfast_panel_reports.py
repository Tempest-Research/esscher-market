"""Canonical Q-FAST panel evidence reports over the frozen untouched universe.

This module compiles the P0 evidence report for issue #67: per-event rows at
zero and preregistered p95 latency over the identical eligible denominator,
abstentions retained with zero signed return, frozen baselines and
perturbation/stability controls, four separately reported PnL conventions,
explicit conservative cost/slippage/latency/missing-fill/option-case handling,
and a machine-readable promotion recommendation that binds exactly one
content-addressed strategy release or rejects with reasons.  A source-health
gate over every frozen source manifest runs first; any non-healthy manifest
rejects the whole report.  Everything is deterministic, offline, synthetic or
fake-only, and permanently labelled NOT_ALPHA_EVIDENCE.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from ringdown_market.audit.source_health import check_manifest
from ringdown_market.contracts.strategy_release import (
    StrategyReleaseRejected,
    parse_strategy_release,
)
from ringdown_market.panel.assembler import _validate_bundle
from ringdown_market.panel.manifest import (
    P0_CONTRACT_DEVELOPMENT_EVENT_IDS,
    P0_EXCLUSION_REASON_CODE,
    PANEL_MANIFEST_SCHEMA,
    PanelRejected,
    validate_panel_manifest,
    validate_panel_selection_rule,
)
from ringdown_market.panel.universe import validate_panel_universe
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

from .direction_receipts import (
    DirectionReceipt,
    direction_receipt_bytes,
)
from .evaluation import MissingPricePoint, evaluate_event
from .models import Direction
from .qfast import QFastStatus
from .shadow_runner import (
    NUMERICAL_EPSILON,
    PromotionRecommendation,
    ShadowRunResult,
    _qfast_payload,
    run_shadow_evaluation,
)

PANEL_EVIDENCE_REPORT_SCHEMA: Final = "esscher.qfast_panel_report"
PANEL_EVIDENCE_REPORT_SCHEMA_VERSION: Final = 1
SOURCE_HEALTH_GATE_SCHEMA: Final = "esscher.qfast_source_health_gate"
SOURCE_HEALTH_GATE_SCHEMA_VERSION: Final = 1
PANEL_REPORT_CLAIM: Final = "NOT_ALPHA_EVIDENCE"
OUTCOME_LABEL_CLASS: Final = "OUTCOME_DERIVED_SYNTHETIC_FAKE"
NOT_AVAILABLE: Final = "NOT_AVAILABLE"

QFAST_UNTOUCHED_PANEL_ID: Final = "QFAST_UNTOUCHED_PANEL_SYNTHETIC_REHEARSAL_V1"
PANEL_HOLD_SECONDS: Final = 3600
# Requested p95 arm for built manifests; aligned with the promoted
# HOST_MEASURED packaged latency profile (issue #68, measured 2026-09-04 on the
# V5 route: nearest-rank p95 5578 ms over 30 warm observations).  The evidence
# validator requires profile p95 == manifest requested p95.
PANEL_P95_LATENCY_MS: Final = 5578
P0_EXCLUSION_DETAIL: Final = (
    "Frozen P0 contract-development exclusion preserved from the untouched universe freeze."
)

PANEL_CONTRACT_MULTIPLIER: Final = 100
PANEL_FEE_PER_TRADE_USD: Final = Decimal("1.00")
PANEL_SLIPPAGE_BPS: Final = Decimal("5.0")
PANEL_PNL_QUANTUM: Final = Decimal("0.000001")
PANEL_LATENCY_TREATMENT: Final = (
    "ENTRY_AT_FIRST_ACHIEVABLE_SYNTHETIC_POINT_AT_OR_AFTER_CUTOFF_PLUS_ARM_LATENCY"
)
PANEL_MISSING_FILL_POLICY: Final = "ABSTENTION_AND_MISSING_FILL_KEEP_ZERO_SIGNED_RETURN"
PANEL_OPTION_CASE_POLICY: Final = (
    "NOT_MODELED_NO_OPTION_ASSIGNMENT_EXERCISE_OR_EXPIRY_IN_SHADOW_REHEARSAL"
)
STABILITY_MAX_ABS_MEAN_DELTA: Final = 0.02

_HISTORICAL_MANIFEST_SCHEMA: Final = "ringdown.historical_evidence_manifest"
_POINT_IN_TIME_MANIFEST_SCHEMA: Final = "ringdown.point_in_time_evidence_manifest"
_MANIFEST_INDEX: Final = re.compile(r"manifests\[(\d+)\]")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


class PanelReportStatus(StrEnum):
    """The only statuses a panel evidence report may carry."""

    SHADOW_ONLY = "SHADOW_ONLY"
    REJECTED = "REJECTED"


class PanelPromotionRecommendation(StrEnum):
    """The only promotion recommendations the report may emit."""

    REJECTED = "REJECTED"
    BIND_SINGLE_RELEASE = "BIND_SINGLE_RELEASE"


class PanelReportReason(StrEnum):
    """Panel-level machine-readable rejection codes beyond the shadow runner."""

    SOURCE_LINEAGE_GAP = "SOURCE_LINEAGE_GAP"
    SOURCE_HEALTH_GATE_REJECTED = "SOURCE_HEALTH_GATE_REJECTED"
    SOURCE_HEALTH_CONTEXT_MISSING = "SOURCE_HEALTH_CONTEXT_MISSING"
    UNIVERSE_VALIDATION_NOT_REACHED = "UNIVERSE_VALIDATION_NOT_REACHED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    WEAK_CANDIDATE_OR_BASELINE = "WEAK_CANDIDATE_OR_BASELINE"
    PERTURBATION_INSTABILITY = "PERTURBATION_INSTABILITY"
    RELEASE_UNPARSEABLE = "RELEASE_UNPARSEABLE"
    RELEASE_NOT_BOUND = "RELEASE_NOT_BOUND"
    ROW_EVALUATION_MISSING = "ROW_EVALUATION_MISSING"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(PANEL_PNL_QUANTUM))


def _require_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class SourceHealthGateReport:
    """The deterministic gate state over every frozen source manifest."""

    healthy: bool
    statuses: Mapping[str, str]
    codes: Mapping[str, tuple[str, ...]]
    manifest_sha256s: Mapping[str, str]

    @property
    def payload(self) -> dict[str, object]:
        """Return the canonical gate payload."""

        return {
            "claim": PANEL_REPORT_CLAIM,
            "events": {
                event_id: {
                    "codes": list(self.codes[event_id]),
                    "manifest_sha256": self.manifest_sha256s[event_id],
                    "status": self.statuses[event_id],
                }
                for event_id in sorted(self.statuses)
            },
            "healthy": self.healthy,
            "schema": SOURCE_HEALTH_GATE_SCHEMA,
            "schema_version": SOURCE_HEALTH_GATE_SCHEMA_VERSION,
        }

    @property
    def bytes(self) -> bytes:
        """Serialize the gate report deterministically."""

        return canonical_json_bytes(self.payload)

    @property
    def sha256(self) -> str:
        """Content-address the gate report."""

        return sha256_bytes(self.bytes)


def _manifest_schema_of(raw: bytes) -> tuple[object, object] | None:
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    return record.get("schema"), record.get("schema_version")


def run_source_health_gate(
    manifest_bytes_by_event: Mapping[str, bytes],
    *,
    event_list_bytes: bytes | None = None,
    selection_rule_bytes: bytes | None = None,
) -> SourceHealthGateReport:
    """Check every frozen source manifest offline and explain every failure.

    Version-2 point-in-time manifests are checked through
    ``audit.source_health.check_manifest``.  The frozen untouched universe
    ships version-1 ``ringdown.historical_evidence_manifest`` documents, which
    ``check_manifest`` fails closed on by schema; those manifests are instead
    validated through the frozen ``panel.universe.validate_panel_universe``
    point-in-time contract (publication, retrieval, and acceptance clocks,
    content hashes, and issuer URL bindings).  Both routes are offline,
    deterministic, and fail closed.
    """

    statuses: dict[str, str] = {}
    codes: dict[str, tuple[str, ...]] = {}
    shas: dict[str, str] = {}
    historical: list[str] = []
    for event_id in sorted(manifest_bytes_by_event):
        raw = manifest_bytes_by_event[event_id]
        if type(raw) is not bytes:
            raise ValueError(f"manifest for {event_id} must be immutable bytes")
        shas[event_id] = _sha256(raw)
        declared = _manifest_schema_of(raw)
        if declared is None:
            statuses[event_id] = "FAILED_CLOSED"
            codes[event_id] = ("PARSE_FAILED",)
            continue
        schema, version = declared
        if schema == _POINT_IN_TIME_MANIFEST_SCHEMA and version == 2:
            kwargs: dict[str, bytes] = {}
            if event_list_bytes is not None and selection_rule_bytes is not None:
                kwargs = {"event_list": event_list_bytes, "selection_rule": selection_rule_bytes}
            report = check_manifest(raw, **kwargs)
            if report.status.value == "HEALTHY":
                statuses[event_id] = "HEALTHY"
                codes[event_id] = ()
            else:
                statuses[event_id] = report.status.value
                codes[event_id] = tuple(sorted({finding.code.value for finding in report.findings}))
        elif schema == _HISTORICAL_MANIFEST_SCHEMA and version == 1:
            historical.append(event_id)
        else:
            statuses[event_id] = "FAILED_CLOSED"
            codes[event_id] = ("UNSUPPORTED_SCHEMA",)

    if historical:
        if event_list_bytes is None or selection_rule_bytes is None:
            for event_id in historical:
                statuses[event_id] = "NOT_VERIFIED"
                codes[event_id] = (PanelReportReason.SOURCE_HEALTH_CONTEXT_MISSING.value,)
        else:
            raws = [manifest_bytes_by_event[event_id] for event_id in historical]
            try:
                validate_panel_universe(selection_rule_bytes, event_list_bytes, raws)
            except (PanelRejected, UnicodeDecodeError, json.JSONDecodeError) as error:
                reason = (
                    error.reason.value if isinstance(error, PanelRejected) else "INVALID_DOCUMENT"
                )
                match = _MANIFEST_INDEX.search(str(getattr(error, "path", "")))
                failed = historical[int(match.group(1))] if match is not None else None
                for event_id in historical:
                    statuses[event_id] = "NOT_VERIFIED"
                    codes[event_id] = (PanelReportReason.UNIVERSE_VALIDATION_NOT_REACHED.value,)
                if failed is not None:
                    statuses[failed] = "REJECTED"
                    codes[failed] = (reason,)
            else:
                for event_id in historical:
                    statuses[event_id] = "HEALTHY"
                    codes[event_id] = ()

    healthy = bool(statuses) and all(status == "HEALTHY" for status in statuses.values())
    return SourceHealthGateReport(
        healthy=healthy,
        statuses=statuses,
        codes=codes,
        manifest_sha256s=shas,
    )


def build_panel_manifest(
    *,
    event_list_bytes: bytes,
    selection_rule_bytes: bytes,
    strategy_policy_sha256: str,
    snapshot_protocol_sha256: str,
    decision_protocol_sha256: str,
    panel_id: str = QFAST_UNTOUCHED_PANEL_ID,
    hold_seconds: int = PANEL_HOLD_SECONDS,
    p95_latency_ms: int = PANEL_P95_LATENCY_MS,
) -> bytes:
    """Build canonical ``ringdown.qfast_panel_manifest`` bytes for the universe.

    The manifest is a synthetic-rehearsal manifest: the untouched universe
    keeps its real source provenance in the source-health gate and the
    prospective ledger, while the rehearsal manifest binds the frozen event
    IDs, frozen order, frozen selection-rule bytes, and the frozen P0
    exclusions without claiming historical evidence provenance it cannot
    carry.  Identical inputs always produce byte-identical output.
    """

    rule = validate_panel_selection_rule(selection_rule_bytes)
    frozen_at = str(rule["frozen_at"])
    try:
        event_list = json.loads(event_list_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"event list must be strict UTF-8 JSON: {error}") from error
    if not isinstance(event_list, dict):
        raise ValueError("event list must be a JSON object")
    raw_ids = event_list.get("event_ids")
    if isinstance(raw_ids, list) and raw_ids:
        event_ids = [str(item) for item in raw_ids]
    else:
        events = event_list.get("events")
        if not isinstance(events, list) or not events:
            raise ValueError("event list carries neither event_ids nor events")
        event_ids = [str(item["event_id"]) for item in events]
    _require_digest(strategy_policy_sha256, field="strategy_policy_sha256")
    _require_digest(snapshot_protocol_sha256, field="snapshot_protocol_sha256")
    _require_digest(decision_protocol_sha256, field="decision_protocol_sha256")
    measurement_seed = canonical_json_bytes(
        {
            "panel_id": panel_id,
            "p95_latency_ms": p95_latency_ms,
            "publisher": "qfast panel evidence synthetic rehearsal",
        }
    )
    payload = {
        "schema": PANEL_MANIFEST_SCHEMA,
        "schema_version": 1,
        "panel_id": panel_id,
        "frozen_at": frozen_at,
        "selection_rule_sha256": _sha256(selection_rule_bytes),
        "strategy_policy_sha256": strategy_policy_sha256,
        "snapshot_protocol_sha256": snapshot_protocol_sha256,
        "decision_protocol_sha256": decision_protocol_sha256,
        "data_class": "SYNTHETIC_CONTRACT_FIXTURE",
        "data_qualifiers": ["NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA", "NO_BROKER_EXECUTION"],
        "hold_seconds": hold_seconds,
        "required_latency_profile": "p95",
        "latency_profiles": {
            "zero": {"requested_latency_ms": 0, "measurement": None},
            "p95": {
                "requested_latency_ms": p95_latency_ms,
                "measurement": {
                    "kind": "SYNTHETIC",
                    "publisher": "qfast panel evidence synthetic rehearsal",
                    "measured_at": frozen_at,
                    "content_sha256": _sha256(measurement_seed),
                },
            },
        },
        "eligible_events": [
            {"event_id": event_id, "evidence_manifest_sha256": None} for event_id in event_ids
        ],
        "excluded_events": [
            {
                "event_id": event_id,
                "reason_code": P0_EXCLUSION_REASON_CODE,
                "reason_detail": P0_EXCLUSION_DETAIL,
            }
            for event_id in sorted(P0_CONTRACT_DEVELOPMENT_EVENT_IDS)
        ],
        "limitations": ["NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA", "NO_BROKER_EXECUTION"],
    }
    return canonical_json_bytes(payload)


def resolve_panel_manifest(
    *,
    universe_document_bytes: bytes,
    selection_rule_bytes: bytes,
    event_list_bytes: bytes,
    strategy_policy_sha256: str,
    snapshot_protocol_sha256: str,
    decision_protocol_sha256: str,
) -> bytes:
    """Reuse a frozen document when it already is the panel manifest.

    The committed ``universe-freeze-v1.json`` carries the
    ``ringdown.qfast_panel_universe_freeze`` schema, not the panel manifest
    schema, so it is never mistaken for one; only an exact, validating
    ``ringdown.qfast_panel_manifest`` document is reused byte-for-byte.
    """

    declared = _manifest_schema_of(universe_document_bytes)
    if declared is not None and declared == (PANEL_MANIFEST_SCHEMA, 1):
        try:
            validate_panel_manifest(universe_document_bytes, selection_rule_bytes)
        except PanelRejected:
            pass
        else:
            return universe_document_bytes
    return build_panel_manifest(
        event_list_bytes=event_list_bytes,
        selection_rule_bytes=selection_rule_bytes,
        strategy_policy_sha256=strategy_policy_sha256,
        snapshot_protocol_sha256=snapshot_protocol_sha256,
        decision_protocol_sha256=decision_protocol_sha256,
    )


@dataclass(frozen=True, slots=True)
class ArmRow:
    """One event's evaluated row inside one latency arm."""

    signed_residual: float
    residual_return: float
    actual_latency_ms: int
    admitted: bool
    entry_at: str
    exit_at: str
    entry_price: float

    def payload(self) -> dict[str, object]:
        """Return the canonical arm-row payload."""

        return {
            "actual_latency_ms": self.actual_latency_ms,
            "admitted": self.admitted,
            "entry_at": self.entry_at,
            "entry_price": self.entry_price,
            "exit_at": self.exit_at,
            "residual_return": self.residual_return,
            "signed_residual": self.signed_residual,
        }


@dataclass(frozen=True, slots=True)
class PanelEventRow:
    """One frozen event's complete evidence row across both latency arms."""

    event_id: str
    sector: str
    symbol: str
    decision_cutoff: str
    direction: str
    admitted: bool
    source_manifest_sha256: str | None
    decision_receipt_sha256: str
    decision_artifact_sha256: str
    zero: ArmRow
    p95: ArmRow
    outcome_label: str
    signal_match: bool
    theoretical_residual_pnl: str
    platform_convention_pnl_usd: str
    fake_execution_pnl_usd: str

    @property
    def payload(self) -> dict[str, object]:
        """Return the canonical per-event row payload."""

        return {
            "admitted": self.admitted,
            "arms": {"p95": self.p95.payload(), "zero": self.zero.payload()},
            "decision_artifact_sha256": self.decision_artifact_sha256,
            "decision_cutoff": self.decision_cutoff,
            "decision_receipt_sha256": self.decision_receipt_sha256,
            "direction": self.direction,
            "event_id": self.event_id,
            "fake_execution_pnl_usd": self.fake_execution_pnl_usd,
            "outcome_label": self.outcome_label,
            "outcome_label_class": OUTCOME_LABEL_CLASS,
            "platform_convention_pnl_usd": self.platform_convention_pnl_usd,
            "sector": self.sector,
            "signal_match": self.signal_match,
            "source_manifest_sha256": self.source_manifest_sha256,
            "symbol": self.symbol,
            "theoretical_residual_pnl": self.theoretical_residual_pnl,
        }

    @property
    def bytes(self) -> bytes:
        """Serialize the row deterministically."""

        return canonical_json_bytes(self.payload)

    @property
    def row_sha256(self) -> str:
        """Content-address the row for full-stack comparison linking."""

        return sha256_bytes(self.bytes)


@dataclass(frozen=True, slots=True)
class FakeExecutionLink:
    """Fills and costs from one linked #66 application-service fake run."""

    run_id: str
    terminal_receipt_sha256: str
    net_pnl_usd_by_event: Mapping[str, str]
    costs_usd_by_event: Mapping[str, str]
    option_case_status: str

    def __post_init__(self) -> None:
        if not self.run_id or not self.run_id.strip():
            raise ValueError("run_id must be non-empty text")
        _require_digest(self.terminal_receipt_sha256, field="terminal_receipt_sha256")
        if not self.option_case_status or not self.option_case_status.strip():
            raise ValueError("option_case_status must be explicit text")
        for mapping in (self.net_pnl_usd_by_event, self.costs_usd_by_event):
            for value in mapping.values():
                try:
                    Decimal(str(value))
                except InvalidOperation as error:
                    raise ValueError(
                        f"fake-execution amounts must be decimal text: {value}"
                    ) from error


@dataclass(frozen=True, slots=True)
class PanelPromotion:
    """The machine-readable promotion recommendation of one report."""

    recommendation: PanelPromotionRecommendation
    release_sha256: str | None
    release_identity_sha256: str | None
    reasons: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        """Return the canonical promotion payload."""

        return {
            "reasons": list(self.reasons),
            "recommendation": self.recommendation.value,
            "release_identity_sha256": self.release_identity_sha256,
            "release_sha256": self.release_sha256,
        }


@dataclass(frozen=True, slots=True)
class PanelEvidenceReport:
    """The complete deterministic outcome of one panel evidence run."""

    status: PanelReportStatus
    claim: str
    classification: str
    shadow: ShadowRunResult | None
    source_health: SourceHealthGateReport | None
    rows: Mapping[str, PanelEventRow]
    promotion: PanelPromotion
    rejection_reasons: tuple[str, ...]
    payload: Mapping[str, object]

    @property
    def bytes(self) -> bytes:
        """Serialize the report deterministically."""

        return canonical_json_bytes(self.payload)

    @property
    def sha256(self) -> str:
        """Content-address the report."""

        return sha256_bytes(self.bytes)


def _outcome_label(residual_return: float) -> str:
    if residual_return > NUMERICAL_EPSILON:
        return Direction.UP.value
    if residual_return < -NUMERICAL_EPSILON:
        return Direction.DOWN.value
    return "FLAT"


def _platform_pnl(entry_price: float, signed_residual: float, admitted: bool) -> Decimal:
    if not admitted:
        return Decimal("0.000000")
    notional = Decimal(repr(entry_price)).quantize(Decimal("0.00000001")) * Decimal(
        PANEL_CONTRACT_MULTIPLIER
    )
    gross = notional * Decimal(f"{signed_residual:.12f}")
    slippage = notional * PANEL_SLIPPAGE_BPS / Decimal(10_000)
    return gross - PANEL_FEE_PER_TRADE_USD - slippage


def _sectors_by_event(event_list_bytes: bytes) -> dict[str, str]:
    try:
        event_list = json.loads(event_list_bytes.decode("utf-8"))
        return {str(event["event_id"]): str(event["sector"]) for event in event_list["events"]}
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return {}


def _frozen_event_ids(event_list_bytes: bytes) -> tuple[str, ...]:
    try:
        event_list = json.loads(event_list_bytes.decode("utf-8"))
        raw_ids = event_list.get("event_ids")
        if isinstance(raw_ids, list) and raw_ids:
            return tuple(str(item) for item in raw_ids)
        return tuple(str(event["event_id"]) for event in event_list["events"])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return ()


def _entry_price(case_path_points: Sequence[object], entry_at: datetime) -> float:
    for point in case_path_points:
        at = point.at
        if at == entry_at:
            return float(point.stock)
    raise PanelReportRejection(
        PanelReportReason.ROW_EVALUATION_MISSING,
        f"no synthetic price point at {entry_at.isoformat()}",
    )


class PanelReportRejection(ValueError):
    """Raised when row construction cannot stay fail-closed deterministically."""

    def __init__(self, reason: PanelReportReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


def _report_payload(
    *,
    status: PanelReportStatus,
    classification: str,
    shadow: ShadowRunResult | None,
    gate: SourceHealthGateReport | None,
    rows: Mapping[str, PanelEventRow],
    promotion: PanelPromotion,
    rejection_reasons: Sequence[str],
    bindings: Mapping[str, object],
    arm_payloads: Mapping[str, object],
    stability: Mapping[str, object],
    pnl_conventions: Mapping[str, object],
    conservatism: Mapping[str, object],
    eligible_event_ids: Sequence[str],
    panel_id: str | None,
) -> dict[str, object]:
    return {
        "arms": dict(arm_payloads),
        "bindings": dict(bindings),
        "claim": PANEL_REPORT_CLAIM,
        "classification": classification,
        "conservatism": dict(conservatism),
        "eligible_event_ids": list(eligible_event_ids),
        "events": [rows[event_id].payload for event_id in sorted(rows)],
        "latency_gate": (
            None
            if shadow is None or shadow.gate is None
            else {
                "qfast_status": shadow.gate.qfast_status.value,
                "required_profile": shadow.gate.required_profile,
                "status": shadow.gate.status.value,
            }
        ),
        "panel_id": panel_id,
        "pnl_conventions": dict(pnl_conventions),
        "promotion": promotion.payload(),
        "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
        "schema": PANEL_EVIDENCE_REPORT_SCHEMA,
        "schema_version": PANEL_EVIDENCE_REPORT_SCHEMA_VERSION,
        "source_health": None if gate is None else gate.payload,
        "stability": dict(stability),
        "status": status.value,
    }


def _rejected_report(
    *,
    reasons: Sequence[str],
    shadow: ShadowRunResult | None,
    gate: SourceHealthGateReport | None,
    bindings: Mapping[str, object],
    release_identity: str | None,
) -> PanelEvidenceReport:
    promotion_reasons = list(shadow.promotion_reasons) if shadow is not None else []
    promotion_reasons.extend(reason for reason in reasons if reason not in promotion_reasons)
    promotion = PanelPromotion(
        recommendation=PanelPromotionRecommendation.REJECTED,
        release_sha256=None,
        release_identity_sha256=release_identity,
        reasons=tuple(promotion_reasons),
    )
    payload = _report_payload(
        status=PanelReportStatus.REJECTED,
        classification=shadow.classification if shadow is not None else "NOT_EVALUATED",
        shadow=shadow,
        gate=gate,
        rows={},
        promotion=promotion,
        rejection_reasons=reasons,
        bindings=bindings,
        arm_payloads={},
        stability={
            "max_abs_mean_delta_threshold": STABILITY_MAX_ABS_MEAN_DELTA,
            "perturbation_deltas": dict(shadow.perturbation_deltas) if shadow is not None else {},
            "stable": False,
        },
        pnl_conventions={},
        conservatism={},
        eligible_event_ids=[],
        panel_id=None,
    )
    return PanelEvidenceReport(
        status=PanelReportStatus.REJECTED,
        claim=PANEL_REPORT_CLAIM,
        classification=payload["classification"],
        shadow=shadow,
        source_health=gate,
        rows={},
        promotion=promotion,
        rejection_reasons=tuple(dict.fromkeys(reasons)),
        payload=payload,
    )


def run_qfast_panel(
    *,
    manifest_bytes: bytes,
    selection_rule_bytes: bytes,
    bundle_bytes: bytes,
    receipt_bytes: Sequence[bytes],
    latency_profile_bytes: bytes,
    expected_policy_sha256: str,
    event_list_bytes: bytes,
    source_manifest_bytes_by_event: Mapping[str, bytes] | None = None,
    universe_selection_rule_bytes: bytes | None = None,
    window_bytes: Sequence[bytes] = (),
    release_bytes: bytes | None = None,
    fake_execution_link: FakeExecutionLink | None = None,
) -> PanelEvidenceReport:
    """Run one deterministic panel evidence report or fail closed with reasons."""

    release_identity: str | None = None
    release_unparseable = False
    if release_bytes is not None:
        try:
            release_identity = parse_strategy_release(release_bytes).release_sha256
        except (StrategyReleaseRejected, ValueError, TypeError):
            release_unparseable = True

    gate: SourceHealthGateReport | None = None
    gate_reasons: list[str] = []
    if source_manifest_bytes_by_event is None:
        gate_reasons.append(PanelReportReason.SOURCE_LINEAGE_GAP.value)
    else:
        gate = run_source_health_gate(
            source_manifest_bytes_by_event,
            event_list_bytes=event_list_bytes,
            selection_rule_bytes=universe_selection_rule_bytes,
        )
        if not gate.healthy:
            gate_reasons.append(PanelReportReason.SOURCE_HEALTH_GATE_REJECTED.value)
            for event_id in sorted(gate.statuses):
                if gate.statuses[event_id] != "HEALTHY":
                    gate_reasons.extend(f"{event_id}:{code}" for code in gate.codes[event_id])
        frozen_ids = set(_frozen_event_ids(event_list_bytes))
        uncovered = sorted(frozen_ids - set(gate.statuses))
        if uncovered:
            gate_reasons.append(PanelReportReason.SOURCE_LINEAGE_GAP.value)
            gate_reasons.extend(f"uncovered:{event_id}" for event_id in uncovered)

    bindings: dict[str, object] = {
        "bundle_sha256": _sha256(bundle_bytes),
        "event_list_sha256": _sha256(event_list_bytes),
        "latency_profile_sha256": _sha256(latency_profile_bytes),
        "panel_manifest_sha256": _sha256(manifest_bytes),
        "selection_rule_sha256": _sha256(selection_rule_bytes),
        "shadow_report_sha256": None,
        "source_health_gate_sha256": None if gate is None else gate.sha256,
        "source_manifest_sha256s": {} if gate is None else dict(gate.manifest_sha256s),
        "strategy_policy_sha256": expected_policy_sha256,
        "universe_selection_rule_sha256": (
            None
            if universe_selection_rule_bytes is None
            else _sha256(universe_selection_rule_bytes)
        ),
        "market_window_sha256s": {},
    }
    if gate_reasons:
        if release_unparseable:
            gate_reasons.append(PanelReportReason.RELEASE_UNPARSEABLE.value)
        return _rejected_report(
            reasons=gate_reasons,
            shadow=None,
            gate=gate,
            bindings=bindings,
            release_identity=release_identity,
        )
    assert gate is not None

    shadow = run_shadow_evaluation(
        manifest_bytes=manifest_bytes,
        selection_rule_bytes=selection_rule_bytes,
        bundle_bytes=bundle_bytes,
        receipt_bytes=receipt_bytes,
        latency_profile_bytes=latency_profile_bytes,
        expected_policy_sha256=expected_policy_sha256,
        event_list_bytes=event_list_bytes,
        window_bytes=window_bytes,
    )
    bindings["shadow_report_sha256"] = shadow.sha256
    reasons: list[str] = [*gate_reasons, *shadow.rejection_reasons]
    if release_unparseable:
        reasons.append(PanelReportReason.RELEASE_UNPARSEABLE.value)
    if not shadow.accepted:
        return _rejected_report(
            reasons=reasons,
            shadow=shadow,
            gate=gate,
            bindings=bindings,
            release_identity=release_identity,
        )

    manifest = validate_panel_manifest(manifest_bytes, selection_rule_bytes)
    eligible = list(manifest.eligible_event_ids)
    window_shas: dict[str, str] = {}
    if len(window_bytes) == len(eligible):
        window_shas = {
            event_id: _sha256(raw) for event_id, raw in zip(eligible, window_bytes, strict=True)
        }
    bindings["market_window_sha256s"] = window_shas

    p95_report = shadow.reports.get("p95")
    zero_report = shadow.reports.get("zero")
    if p95_report is None or zero_report is None:
        reasons.append(PanelReportReason.ROW_EVALUATION_MISSING.value)
        return _rejected_report(
            reasons=reasons,
            shadow=shadow,
            gate=gate,
            bindings=bindings,
            release_identity=release_identity,
        )
    if p95_report.status is QFastStatus.INSUFFICIENT_DATA:
        reasons.append(PanelReportReason.INSUFFICIENT_SAMPLE.value)
    if p95_report.status is QFastStatus.REJECTED:
        reasons.append(PanelReportReason.WEAK_CANDIDATE_OR_BASELINE.value)
        reasons.extend(f"qfast:{reason}" for reason in p95_report.reject_reasons)
    deltas = dict(shadow.perturbation_deltas)
    stable = bool(deltas) and all(
        abs(value) <= STABILITY_MAX_ABS_MEAN_DELTA for value in deltas.values()
    )
    if not stable:
        reasons.append(PanelReportReason.PERTURBATION_INSTABILITY.value)

    rows: dict[str, PanelEventRow] = {}
    try:
        rows = _build_rows(
            shadow=shadow,
            manifest_bytes=manifest_bytes,
            selection_rule_bytes=selection_rule_bytes,
            bundle_bytes=bundle_bytes,
            event_list_bytes=event_list_bytes,
            gate=gate,
            fake_execution_link=fake_execution_link,
        )
    except (PanelRejected, PanelReportRejection, MissingPricePoint) as error:
        reasons.append(PanelReportReason.ROW_EVALUATION_MISSING.value)
        reasons.append(str(error))
    if reasons:
        return _rejected_report(
            reasons=reasons,
            shadow=shadow,
            gate=gate,
            bindings=bindings,
            release_identity=release_identity,
        )

    promotion = _promotion(shadow, release_identity=release_identity)
    status = PanelReportStatus.SHADOW_ONLY
    arm_payloads = {
        "zero": {**_qfast_payload(zero_report), "eligible_event_ids": eligible},
        "p95": {**_qfast_payload(p95_report), "eligible_event_ids": eligible},
    }
    stability_payload = {
        "max_abs_mean_delta_threshold": STABILITY_MAX_ABS_MEAN_DELTA,
        "perturbation_deltas": deltas,
        "stable": stable,
    }
    pnl_conventions = _pnl_conventions(rows, fake_execution_link=fake_execution_link)
    conservatism = {
        "contract_multiplier": PANEL_CONTRACT_MULTIPLIER,
        "costs_applied_to_abstentions": False,
        "fee_per_trade_usd": str(PANEL_FEE_PER_TRADE_USD),
        "latency_treatment": PANEL_LATENCY_TREATMENT,
        "missing_fill_policy": PANEL_MISSING_FILL_POLICY,
        "option_assignment_exercise": (
            PANEL_OPTION_CASE_POLICY
            if fake_execution_link is None
            else fake_execution_link.option_case_status
        ),
        "pnl_quantum": str(PANEL_PNL_QUANTUM),
        "slippage_bps": str(PANEL_SLIPPAGE_BPS),
    }
    payload = _report_payload(
        status=status,
        classification=shadow.classification,
        shadow=shadow,
        gate=gate,
        rows=rows,
        promotion=promotion,
        rejection_reasons=reasons,
        bindings=bindings,
        arm_payloads=arm_payloads,
        stability=stability_payload,
        pnl_conventions=pnl_conventions,
        conservatism=conservatism,
        eligible_event_ids=eligible,
        panel_id=manifest.panel_id,
    )
    return PanelEvidenceReport(
        status=status,
        claim=PANEL_REPORT_CLAIM,
        classification=shadow.classification,
        shadow=shadow,
        source_health=gate,
        rows=rows,
        promotion=promotion,
        rejection_reasons=tuple(dict.fromkeys(reasons)),
        payload=payload,
    )


def _promotion(shadow: ShadowRunResult, *, release_identity: str | None) -> PanelPromotion:
    reasons = list(shadow.promotion_reasons)
    if shadow.promotion_recommendation is PromotionRecommendation.PROMOTE_TO_PROSPECTIVE_LEDGER:
        if release_identity is None:
            reasons.append(PanelReportReason.RELEASE_NOT_BOUND.value)
        else:
            return PanelPromotion(
                recommendation=PanelPromotionRecommendation.BIND_SINGLE_RELEASE,
                release_sha256=release_identity,
                release_identity_sha256=release_identity,
                reasons=tuple(reasons),
            )
    return PanelPromotion(
        recommendation=PanelPromotionRecommendation.REJECTED,
        release_sha256=None,
        release_identity_sha256=release_identity,
        reasons=tuple(reasons),
    )


def _build_rows(
    *,
    shadow: ShadowRunResult,
    manifest_bytes: bytes,
    selection_rule_bytes: bytes,
    bundle_bytes: bytes,
    event_list_bytes: bytes,
    gate: SourceHealthGateReport,
    fake_execution_link: FakeExecutionLink | None,
) -> dict[str, PanelEventRow]:
    manifest = validate_panel_manifest(manifest_bytes, selection_rule_bytes)
    _, cases = _validate_bundle(bundle_bytes, manifest)
    cases_by_event = {case.decision.event_id: case for case in cases}
    receipts: Mapping[str, DirectionReceipt] = {
        receipt.event_id: receipt for receipt in shadow.receipts
    }
    sectors = _sectors_by_event(event_list_bytes)
    rows: dict[str, PanelEventRow] = {}
    for event_id in manifest.eligible_event_ids:
        receipt = receipts.get(event_id)
        case = cases_by_event.get(event_id)
        if receipt is None or case is None:
            raise PanelReportRejection(
                PanelReportReason.ROW_EVALUATION_MISSING, f"missing receipt or case for {event_id}"
            )
        arms: dict[str, ArmRow] = {}
        for arm, latency_ms in (("zero", 0), ("p95", manifest.latency_profiles["p95"])):
            evaluation = evaluate_event(
                case,
                receipt.direction,
                latency_ms=latency_ms,
                hold_seconds=manifest.hold_seconds,
            )
            arms[arm] = ArmRow(
                signed_residual=evaluation.signed_residual,
                residual_return=evaluation.residual_return,
                actual_latency_ms=evaluation.actual_latency_ms,
                admitted=evaluation.admitted,
                entry_at=_iso(evaluation.entry_at),
                exit_at=_iso(evaluation.exit_at),
                entry_price=_entry_price(case.path.points, evaluation.entry_at),
            )
        zero = arms["zero"]
        p95 = arms["p95"]
        outcome_label = _outcome_label(zero.residual_return)
        platform = _platform_pnl(p95.entry_price, p95.signed_residual, p95.admitted)
        fake_pnl = NOT_AVAILABLE
        if fake_execution_link is not None:
            linked = fake_execution_link.net_pnl_usd_by_event.get(event_id)
            fake_pnl = NOT_AVAILABLE if linked is None else str(Decimal(str(linked)))
        rows[event_id] = PanelEventRow(
            event_id=event_id,
            sector=sectors.get(event_id, "UNSPECIFIED"),
            symbol=shadow.symbols.get(event_id, event_id.split("-", 1)[0]),
            decision_cutoff=_iso(receipt.decision_cutoff_at),
            direction=receipt.direction.value,
            admitted=p95.admitted,
            source_manifest_sha256=gate.manifest_sha256s.get(event_id),
            decision_receipt_sha256=sha256_bytes(direction_receipt_bytes(receipt)),
            decision_artifact_sha256=receipt.decision_artifact_sha256,
            zero=zero,
            p95=p95,
            outcome_label=outcome_label,
            signal_match=p95.admitted and receipt.direction.value == outcome_label,
            theoretical_residual_pnl=_decimal_text(Decimal(f"{p95.signed_residual:.12f}")),
            platform_convention_pnl_usd=_decimal_text(platform),
            fake_execution_pnl_usd=fake_pnl,
        )
    return rows


def _pnl_conventions(
    rows: Mapping[str, PanelEventRow],
    *,
    fake_execution_link: FakeExecutionLink | None,
) -> dict[str, object]:
    count = len(rows)
    matches = sum(1 for row in rows.values() if row.signal_match)
    theoretical_sum = sum(
        (Decimal(row.theoretical_residual_pnl) for row in rows.values()), Decimal(0)
    )
    platform_sum = sum(
        (Decimal(row.platform_convention_pnl_usd) for row in rows.values()), Decimal(0)
    )
    theoretical_mean = (
        (theoretical_sum / Decimal(count)).quantize(PANEL_PNL_QUANTUM) if count else Decimal(0)
    )
    platform_mean = (
        (platform_sum / Decimal(count)).quantize(PANEL_PNL_QUANTUM) if count else Decimal(0)
    )
    fake: dict[str, object]
    if fake_execution_link is None:
        fake = {
            "missing_fill_events": sorted(row.event_id for row in rows.values()),
            "pnl_class": "FAKE_EXECUTION_SERVICE",
            "run_id": None,
            "status": NOT_AVAILABLE,
            "sum_costs_usd": None,
            "sum_net_usd": None,
            "terminal_receipt_sha256": None,
        }
    else:
        present = [row for row in rows.values() if row.fake_execution_pnl_usd != NOT_AVAILABLE]
        fake_sum = sum(
            (Decimal(row.fake_execution_pnl_usd) for row in present), Decimal(0)
        ).quantize(PANEL_PNL_QUANTUM)
        costs_sum = sum(
            (Decimal(str(value)) for value in fake_execution_link.costs_usd_by_event.values()),
            Decimal(0),
        ).quantize(PANEL_PNL_QUANTUM)
        fake = {
            "missing_fill_events": sorted(
                row.event_id for row in rows.values() if row.fake_execution_pnl_usd == NOT_AVAILABLE
            ),
            "pnl_class": "FAKE_EXECUTION_SERVICE",
            "run_id": fake_execution_link.run_id,
            "status": "LINKED",
            "sum_costs_usd": str(costs_sum),
            "sum_net_usd": str(fake_sum),
            "terminal_receipt_sha256": fake_execution_link.terminal_receipt_sha256,
        }
    return {
        "fake_execution_pnl": fake,
        "platform_convention_pnl": {
            "contract_multiplier": PANEL_CONTRACT_MULTIPLIER,
            "fee_per_trade_usd": str(PANEL_FEE_PER_TRADE_USD),
            "pnl_class": "PLATFORM_CONVENTION",
            "slippage_bps": str(PANEL_SLIPPAGE_BPS),
            "sum_net_usd": _decimal_text(platform_sum),
            "mean_net_usd": _decimal_text(platform_mean),
        },
        "signal_accuracy": {
            "accuracy": (matches / count) if count else 0.0,
            "eligible_events": count,
            "label_class": OUTCOME_LABEL_CLASS,
            "matches": matches,
            "pnl_class": "SIGNAL_ACCURACY",
        },
        "theoretical_residual_pnl": {
            "arm": "p95",
            "mean": _decimal_text(theoretical_mean),
            "pnl_class": "SHADOW_THEORETICAL",
            "sum": _decimal_text(theoretical_sum),
        },
    }


__all__ = [
    "NOT_AVAILABLE",
    "OUTCOME_LABEL_CLASS",
    "PANEL_CONTRACT_MULTIPLIER",
    "PANEL_EVIDENCE_REPORT_SCHEMA",
    "PANEL_EVIDENCE_REPORT_SCHEMA_VERSION",
    "PANEL_FEE_PER_TRADE_USD",
    "PANEL_HOLD_SECONDS",
    "PANEL_MISSING_FILL_POLICY",
    "PANEL_OPTION_CASE_POLICY",
    "PANEL_P95_LATENCY_MS",
    "PANEL_PNL_QUANTUM",
    "PANEL_REPORT_CLAIM",
    "PANEL_SLIPPAGE_BPS",
    "QFAST_UNTOUCHED_PANEL_ID",
    "SOURCE_HEALTH_GATE_SCHEMA",
    "SOURCE_HEALTH_GATE_SCHEMA_VERSION",
    "STABILITY_MAX_ABS_MEAN_DELTA",
    "ArmRow",
    "FakeExecutionLink",
    "PanelEventRow",
    "PanelEvidenceReport",
    "PanelPromotion",
    "PanelPromotionRecommendation",
    "PanelReportReason",
    "PanelReportRejection",
    "PanelReportStatus",
    "SourceHealthGateReport",
    "build_panel_manifest",
    "resolve_panel_manifest",
    "run_qfast_panel",
    "run_source_health_gate",
]
