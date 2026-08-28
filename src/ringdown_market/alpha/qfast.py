"""Reject-only Q-FAST and Q-LATENCY gates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from statistics import fmean, median

CANDIDATE_METHOD = "ringdown"
NUMERICAL_EPSILON = 1e-12


class QFastStatus(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    REJECTED = "REJECTED"
    NOT_REJECTED_SMALL_SAMPLE = "NOT_REJECTED_SMALL_SAMPLE"


class LatencyGateStatus(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    SHADOW_ONLY = "SHADOW_ONLY"
    NOT_REJECTED_SMALL_SAMPLE = "NOT_REJECTED_SMALL_SAMPLE"


@dataclass(frozen=True, slots=True)
class PanelRow:
    event_id: str
    signed_returns: Mapping[str, float]
    admitted: Mapping[str, bool]

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must be non-empty")
        if set(self.signed_returns) != set(self.admitted):
            raise ValueError("signed_returns and admitted must contain the same methods")
        if not self.signed_returns:
            raise ValueError("panel row must contain at least one method")
        if any(not isfinite(value) for value in self.signed_returns.values()):
            raise ValueError("signed returns must be finite")
        for method, is_admitted in self.admitted.items():
            if not is_admitted and self.signed_returns[method] != 0.0:
                raise ValueError("abstentions must have zero signed return")


@dataclass(frozen=True, slots=True)
class MethodMetrics:
    eligible_events: int
    admitted_events: int
    coverage: float
    mean_all: float
    median_all: float
    mean_admitted: float | None
    median_admitted: float | None


@dataclass(frozen=True, slots=True)
class QFastReport:
    status: QFastStatus
    claim: str
    event_count: int
    metrics: Mapping[str, MethodMetrics]
    strongest_baseline: str | None
    candidate_advantage: float | None
    leave_best_out_mean: float | None
    reject_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LatencyGateReport:
    status: LatencyGateStatus
    required_profile: str
    qfast_status: QFastStatus


def _method_metrics(rows: Sequence[PanelRow], method: str) -> MethodMetrics:
    all_returns = [row.signed_returns[method] for row in rows]
    admitted_returns = [row.signed_returns[method] for row in rows if row.admitted[method]]
    count = len(rows)
    admitted_count = len(admitted_returns)
    return MethodMetrics(
        eligible_events=count,
        admitted_events=admitted_count,
        coverage=admitted_count / count if count else 0.0,
        mean_all=fmean(all_returns) if all_returns else 0.0,
        median_all=median(all_returns) if all_returns else 0.0,
        mean_admitted=fmean(admitted_returns) if admitted_returns else None,
        median_admitted=median(admitted_returns) if admitted_returns else None,
    )


def run_qfast(
    rows: Sequence[PanelRow],
    *,
    minimum_events: int = 20,
    candidate_method: str = CANDIDATE_METHOD,
) -> QFastReport:
    """Run the preregistered reject-only screen over every eligible event."""

    if minimum_events < 2:
        raise ValueError("minimum_events must be at least two")
    if len({row.event_id for row in rows}) != len(rows):
        raise ValueError("event IDs must be unique")
    if not rows:
        return QFastReport(
            status=QFastStatus.INSUFFICIENT_DATA,
            claim="NOT_ALPHA_EVIDENCE",
            event_count=0,
            metrics={},
            strongest_baseline=None,
            candidate_advantage=None,
            leave_best_out_mean=None,
            reject_reasons=(),
        )

    methods = set(rows[0].signed_returns)
    if candidate_method not in methods:
        raise ValueError(f"missing candidate method: {candidate_method}")
    if any(set(row.signed_returns) != methods for row in rows):
        raise ValueError("every panel row must contain the same methods")

    metrics = {method: _method_metrics(rows, method) for method in sorted(methods)}
    candidate = metrics[candidate_method]
    baseline_methods = sorted(methods - {candidate_method})
    strongest_baseline = (
        max(baseline_methods, key=lambda method: (metrics[method].mean_all, method))
        if baseline_methods
        else None
    )
    candidate_advantage = (
        candidate.mean_all - metrics[strongest_baseline].mean_all
        if strongest_baseline is not None
        else None
    )

    candidate_returns = [row.signed_returns[candidate_method] for row in rows]
    best_index = max(range(len(rows)), key=candidate_returns.__getitem__)
    leave_best_returns = [
        value for index, value in enumerate(candidate_returns) if index != best_index
    ]
    leave_best_out_mean = fmean(leave_best_returns) if leave_best_returns else None

    reasons: list[str] = []
    if candidate.mean_all <= NUMERICAL_EPSILON:
        reasons.append("non_positive_mean")
    if candidate.median_all < -NUMERICAL_EPSILON:
        reasons.append("negative_median")
    if candidate_advantage is not None and candidate_advantage < -NUMERICAL_EPSILON:
        reasons.append("loses_to_strongest_baseline")
    if leave_best_out_mean is not None and leave_best_out_mean < -NUMERICAL_EPSILON:
        reasons.append("best_event_fragility")

    if len(rows) < minimum_events:
        status = QFastStatus.INSUFFICIENT_DATA
    elif reasons:
        status = QFastStatus.REJECTED
    else:
        status = QFastStatus.NOT_REJECTED_SMALL_SAMPLE

    return QFastReport(
        status=status,
        claim="NOT_ALPHA_EVIDENCE",
        event_count=len(rows),
        metrics=metrics,
        strongest_baseline=strongest_baseline,
        candidate_advantage=candidate_advantage,
        leave_best_out_mean=leave_best_out_mean,
        reject_reasons=tuple(reasons),
    )


def evaluate_latency_gate(
    profiles: Mapping[str, QFastReport],
    *,
    required_profile: str = "p95",
) -> LatencyGateReport:
    """Force shadow-only unless the conservative latency profile survives Q-FAST."""

    if required_profile not in profiles:
        raise ValueError(f"missing required latency profile: {required_profile}")
    result = profiles[required_profile]
    if result.status is QFastStatus.INSUFFICIENT_DATA:
        status = LatencyGateStatus.INSUFFICIENT_DATA
    elif result.status is QFastStatus.REJECTED:
        status = LatencyGateStatus.SHADOW_ONLY
    else:
        status = LatencyGateStatus.NOT_REJECTED_SMALL_SAMPLE
    return LatencyGateReport(
        status=status,
        required_profile=required_profile,
        qfast_status=result.status,
    )
