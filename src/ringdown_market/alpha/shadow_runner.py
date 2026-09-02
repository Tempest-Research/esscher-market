"""Deterministic Q-FAST shadow evaluation over frozen inputs and V2 receipts.

The runner consumes the exact frozen panel, immutable direction receipts, the
zero and preregistered p95 latency profiles, frozen costs/baselines/policy, and
produces byte-stable canonical reports.  It never invokes a provider, never
inspects outcomes before validation accepts the configuration, and never lets
a baseline observe candidate outcomes during configuration.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ringdown_market.contracts.latency_profile import (
    ValidatedLatencyProfile,
    validate_latency_profile,
)
from ringdown_market.panel.assembler import _validate_bundle
from ringdown_market.panel.manifest import ValidatedPanelManifest, validate_panel_manifest

from .direction_receipts import (
    DirectionReceipt,
    DirectionReceiptRejected,
    direction_receipt_bytes,
    parse_direction_receipt_set,
)
from .evaluation import MissingPricePoint, evaluate_event
from .evidence_validator import EvidenceValidationReport, validate_evidence_configuration
from .models import Direction, EventCase
from .qfast import (
    CANDIDATE_METHOD,
    LatencyGateReport,
    PanelRow,
    QFastReport,
    evaluate_latency_gate,
    run_qfast,
)

NUMERICAL_EPSILON: Final = 1e-9
CASH_METHOD: Final = "cash_always_abstain"
PRICE_ONLY_METHOD: Final = "price_only_continuation"
NUMERIC_METHOD: Final = "numeric_score_continuation"
CLAIM_NOT_ALPHA: Final = "NOT_ALPHA_EVIDENCE"


class ShadowRunReason(StrEnum):
    """Runner-level rejection codes beyond the evidence validator."""

    RECEIPT_BUNDLE_MISMATCH = "RECEIPT_BUNDLE_MISMATCH"
    PATH_INSUFFICIENT = "PATH_INSUFFICIENT"


class PromotionRecommendation(StrEnum):
    REJECT_PROMOTION = "REJECT_PROMOTION"
    PROMOTE_TO_PROSPECTIVE_LEDGER = "PROMOTE_TO_PROSPECTIVE_LEDGER"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sign_direction(score: float) -> Direction:
    if score > NUMERICAL_EPSILON:
        return Direction.UP
    if score < -NUMERICAL_EPSILON:
        return Direction.DOWN
    return Direction.UNCERTAIN


def _qfast_payload(report: QFastReport) -> dict[str, object]:
    return {
        "status": report.status.value,
        "claim": report.claim,
        "event_count": report.event_count,
        "metrics": {
            method: {
                "eligible_events": metrics.eligible_events,
                "admitted_events": metrics.admitted_events,
                "coverage": metrics.coverage,
                "mean_all": metrics.mean_all,
                "median_all": metrics.median_all,
                "mean_admitted": metrics.mean_admitted,
                "median_admitted": metrics.median_admitted,
            }
            for method, metrics in report.metrics.items()
        },
        "strongest_baseline": report.strongest_baseline,
        "candidate_advantage": report.candidate_advantage,
        "leave_best_out_mean": report.leave_best_out_mean,
        "reject_reasons": list(report.reject_reasons),
    }


def _window_event_id(raw: bytes) -> str | None:
    try:
        window = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return str(window["event_id"])


def _symbol_for(event_id: str, windows: Mapping[str, bytes]) -> str:
    raw = windows.get(event_id)
    if raw is not None:
        window = json.loads(raw.decode("utf-8"))
        return str(window["symbols"]["issuer"])
    return event_id.split("-", 1)[0]


@dataclass(frozen=True, slots=True)
class ShadowRunResult:
    """The complete deterministic outcome of one shadow evaluation run."""

    validation: EvidenceValidationReport
    accepted: bool
    receipts: tuple[DirectionReceipt, ...]
    symbols: Mapping[str, str]
    reports: Mapping[str, QFastReport]
    gate: LatencyGateReport | None
    perturbation_deltas: Mapping[str, float]
    promotion_recommendation: PromotionRecommendation
    promotion_reasons: tuple[str, ...]
    claim: str
    classification: str
    rejection_reasons: tuple[str, ...]
    evaluations: Mapping[str, object]
    payload: Mapping[str, object]

    @property
    def bytes(self) -> bytes:
        return _canonical(self.payload)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.bytes).hexdigest()


def _method_directions(receipt: DirectionReceipt) -> dict[str, Direction]:
    return {
        CANDIDATE_METHOD: receipt.direction,
        PRICE_ONLY_METHOD: _sign_direction(receipt.price_only_score),
        NUMERIC_METHOD: _sign_direction(receipt.numeric_score),
        CASH_METHOD: Direction.UNCERTAIN,
    }


def _rows_for_arm(
    cases: Mapping[str, EventCase],
    receipts: Mapping[str, DirectionReceipt],
    *,
    latency_ms: int,
    hold_seconds: int,
) -> tuple[list[PanelRow], tuple[str, ...]]:
    rows: list[PanelRow] = []
    failures: list[str] = []
    for event_id in sorted(cases):
        case = cases[event_id]
        directions = _method_directions(receipts[event_id])
        signed: dict[str, float] = {}
        admitted: dict[str, bool] = {}
        broken = False
        for method in sorted(directions):
            try:
                evaluation = evaluate_event(
                    case,
                    directions[method],
                    latency_ms=latency_ms,
                    hold_seconds=hold_seconds,
                )
            except MissingPricePoint:
                failures.append(f"{event_id}:{method}:{latency_ms}ms")
                broken = True
                break
            signed[method] = evaluation.signed_residual
            admitted[method] = evaluation.admitted
        if not broken:
            rows.append(PanelRow(event_id=event_id, signed_returns=signed, admitted=admitted))
    return rows, tuple(failures)


def run_shadow_evaluation(
    *,
    manifest_bytes: bytes,
    selection_rule_bytes: bytes,
    bundle_bytes: bytes,
    receipt_bytes: Sequence[bytes],
    latency_profile_bytes: bytes,
    expected_policy_sha256: str,
    event_list_bytes: bytes | None = None,
    universe_manifest_bytes: Sequence[bytes] = (),
    window_bytes: Sequence[bytes] = (),
) -> ShadowRunResult:
    """Run one deterministic shadow evaluation or fail closed with reasons."""

    validation = validate_evidence_configuration(
        manifest_bytes=manifest_bytes,
        selection_rule_bytes=selection_rule_bytes,
        receipt_bytes=receipt_bytes,
        latency_profile_bytes=latency_profile_bytes,
        expected_policy_sha256=expected_policy_sha256,
        event_list_bytes=event_list_bytes,
        universe_manifest_bytes=universe_manifest_bytes,
        window_bytes=window_bytes,
    )

    receipts_map: dict[str, DirectionReceipt] = {}
    try:
        receipts_map = parse_direction_receipt_set(receipt_bytes)
    except DirectionReceiptRejected:
        receipts_map = {}
    receipts = tuple(receipts_map[event_id] for event_id in sorted(receipts_map))
    classification = (
        "SYNTHETIC_RECEIPTS"
        if any(receipt.producer_kind.value == "SYNTHETIC" for receipt in receipts)
        else "ROUTE_BOUND_RECEIPTS"
    )
    windows: dict[str, bytes] = {}
    for raw in window_bytes:
        window_event = _window_event_id(raw)
        if window_event is not None:
            windows[window_event] = raw

    reasons: list[str] = []
    if not validation.accepted:
        reasons.extend(validation.rejection_reasons)

    manifest: ValidatedPanelManifest | None = None
    profile: ValidatedLatencyProfile | None = None
    cases: Mapping[str, EventCase] = {}
    reports: dict[str, QFastReport] = {}
    gate: LatencyGateReport | None = None
    deltas: dict[str, float] = {}
    evaluations: dict[str, object] = {}
    if validation.accepted:
        manifest = validate_panel_manifest(manifest_bytes, selection_rule_bytes)
        profile = validate_latency_profile(latency_profile_bytes)
        _, parsed_cases = _validate_bundle(bundle_bytes, manifest)
        cases = {case.decision.event_id: case for case in parsed_cases}
        for event_id in sorted(cases):
            receipt = receipts_map[event_id]
            decision = cases[event_id].decision
            mismatch = (
                receipt.direction is not decision.candidate_signal
                or receipt.decision_cutoff_at != decision.decision_cutoff
                or abs(receipt.market_beta - decision.market_beta) > NUMERICAL_EPSILON
                or abs(receipt.sector_beta - decision.sector_beta) > NUMERICAL_EPSILON
                or abs(receipt.price_only_score - decision.price_only_score) > NUMERICAL_EPSILON
                or abs(receipt.numeric_score - decision.numeric_score) > NUMERICAL_EPSILON
            )
            if mismatch:
                reasons.append(ShadowRunReason.RECEIPT_BUNDLE_MISMATCH.value)
        if reasons:
            cases = {}
    if validation.accepted and cases:
        assert manifest is not None and profile is not None
        arms = {"zero": 0, "p95": profile.p95_latency_ms}
        path_failures: list[str] = []
        for arm, latency in arms.items():
            rows, failures = _rows_for_arm(
                cases, receipts_map, latency_ms=latency, hold_seconds=manifest.hold_seconds
            )
            path_failures.extend(failures)
            reports[arm] = run_qfast(rows, minimum_events=manifest.minimum_events)
        if path_failures:
            reasons.append(ShadowRunReason.PATH_INSUFFICIENT.value)
            reasons.extend(path_failures)
        else:
            gate = evaluate_latency_gate(
                reports, required_profile=manifest.required_latency_profile
            )
            base_mean = reports["p95"].metrics[CANDIDATE_METHOD].mean_all
            zero_mean = reports["zero"].metrics[CANDIDATE_METHOD].mean_all
            deltas["zero_minus_p95_mean"] = zero_mean - base_mean
            perturbations = (
                ("p95_hold_x2", profile.p95_latency_ms, manifest.hold_seconds * 2),
                ("p95_latency_half", profile.p95_latency_ms // 2, manifest.hold_seconds),
                ("zero_hold_x2", 0, manifest.hold_seconds * 2),
            )
            for label, latency, hold in perturbations:
                rows, failures = _rows_for_arm(
                    cases, receipts_map, latency_ms=latency, hold_seconds=hold
                )
                if failures:
                    reasons.append(ShadowRunReason.PATH_INSUFFICIENT.value)
                    reasons.extend(failures)
                    break
                perturbed = run_qfast(rows, minimum_events=manifest.minimum_events)
                deltas[label] = perturbed.metrics[CANDIDATE_METHOD].mean_all - base_mean
            if not reasons:
                for event_id in sorted(cases):
                    evaluations[event_id] = evaluate_event(
                        cases[event_id],
                        receipts_map[event_id].direction,
                        latency_ms=profile.p95_latency_ms,
                        hold_seconds=manifest.hold_seconds,
                    )

    promotion_reasons: list[str] = []
    if not validation.accepted or reasons:
        promotion_reasons.append("configuration_rejected")
    elif profile is not None and gate is not None:
        if profile.kind.value != "HOST_MEASURED":
            promotion_reasons.append("latency_profile_not_measured")
        if classification != "ROUTE_BOUND_RECEIPTS":
            promotion_reasons.append("synthetic_receipts_not_candidate_evidence")
        if gate.status.value != "NOT_REJECTED_SMALL_SAMPLE":
            promotion_reasons.append(f"latency_gate_{gate.status.value}")
        p95_report = reports.get("p95") or reports.get("zero")
        if p95_report is not None and p95_report.status.value == "INSUFFICIENT_DATA":
            promotion_reasons.append("insufficient_events")
    recommendation = (
        PromotionRecommendation.PROMOTE_TO_PROSPECTIVE_LEDGER
        if not promotion_reasons
        else PromotionRecommendation.REJECT_PROMOTION
    )

    symbols = {event_id: _symbol_for(event_id, windows) for event_id in sorted(receipts_map)}
    payload: dict[str, object] = {
        "schema": "esscher.qfast_shadow_report",
        "schema_version": 1,
        "accepted": validation.accepted and not reasons,
        "claim": CLAIM_NOT_ALPHA,
        "classification": classification,
        "validation": validation.payload(),
        "bindings": {
            "panel_manifest_sha256": validation.manifest_sha256,
            "selection_rule_sha256": validation.selection_rule_sha256,
            "strategy_policy_sha256": expected_policy_sha256,
            "direction_receipt_sha256": [
                hashlib.sha256(direction_receipt_bytes(receipt)).hexdigest() for receipt in receipts
            ],
        },
        "latency_arms": {arm: _qfast_payload(report) for arm, report in reports.items()},
        "latency_gate": (
            None
            if gate is None
            else {
                "status": gate.status.value,
                "required_profile": gate.required_profile,
                "qfast_status": gate.qfast_status.value,
            }
        ),
        "perturbation_deltas": deltas,
        "promotion": {
            "recommendation": recommendation.value,
            "reasons": promotion_reasons,
        },
        "rejection_reasons": list(dict.fromkeys([*validation.rejection_reasons, *reasons])),
        "event_ids": sorted(receipts_map),
    }
    return ShadowRunResult(
        validation=validation,
        accepted=validation.accepted and not reasons,
        receipts=receipts,
        symbols=symbols,
        reports=reports,
        gate=gate,
        perturbation_deltas=deltas,
        promotion_recommendation=recommendation,
        promotion_reasons=tuple(promotion_reasons),
        claim=CLAIM_NOT_ALPHA,
        classification=classification,
        rejection_reasons=tuple(dict.fromkeys([*validation.rejection_reasons, *reasons])),
        evaluations=evaluations,
        payload=payload,
    )
