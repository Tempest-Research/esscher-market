import pytest

from ringdown_market.alpha.qfast import (
    CANDIDATE_METHOD,
    LatencyGateStatus,
    PanelRow,
    QFastStatus,
    evaluate_latency_gate,
    run_qfast,
)

BASELINE = "always_abstain"


def row(
    event_id: str,
    candidate: float,
    *,
    admitted: bool = True,
    baseline: float = 0.0,
) -> PanelRow:
    return PanelRow(
        event_id=event_id,
        signed_returns={CANDIDATE_METHOD: candidate, BASELINE: baseline},
        admitted={CANDIDATE_METHOD: admitted, BASELINE: baseline != 0.0},
    )


def test_keeps_abstentions_in_the_eligible_event_denominator() -> None:
    rows = [
        row("a", 0.10),
        row("b", -0.05),
        row("c", 0.0, admitted=False),
        row("d", 0.0, admitted=False),
    ]

    report = run_qfast(rows, minimum_events=4)
    metrics = report.metrics[CANDIDATE_METHOD]

    assert metrics.mean_all == pytest.approx(0.0125)
    assert metrics.mean_admitted == pytest.approx(0.025)
    assert metrics.coverage == pytest.approx(0.5)


def test_median_gate_uses_all_eligible_events_including_abstentions() -> None:
    rows = [
        row("loss-1", -0.10),
        row("loss-2", -0.10),
        row("loss-3", -0.10),
        row("win-1", 0.30),
        row("win-2", 0.30),
        row("abstain-1", 0.0, admitted=False),
        row("abstain-2", 0.0, admitted=False),
    ]

    report = run_qfast(rows, minimum_events=7)

    assert report.metrics[CANDIDATE_METHOD].median_all == 0.0
    assert report.status is QFastStatus.NOT_REJECTED_SMALL_SAMPLE
    assert "negative_median" not in report.reject_reasons


def test_positive_small_sample_is_never_reported_as_proven_alpha() -> None:
    rows = [row(str(index), 0.02) for index in range(4)]

    report = run_qfast(rows, minimum_events=4)

    assert report.status is QFastStatus.NOT_REJECTED_SMALL_SAMPLE
    assert report.claim == "NOT_ALPHA_EVIDENCE"


def test_rejects_when_one_best_event_is_the_only_source_of_positive_mean() -> None:
    rows = [
        row("lucky", 0.40),
        row("loss-1", -0.10),
        row("loss-2", -0.10),
        row("loss-3", -0.10),
    ]

    report = run_qfast(rows, minimum_events=4)

    assert report.status is QFastStatus.REJECTED
    assert "best_event_fragility" in report.reject_reasons
    assert report.leave_best_out_mean == pytest.approx(-0.10)


def test_rejects_when_candidate_loses_to_the_strongest_baseline() -> None:
    rows = [row(str(index), 0.01, baseline=0.02) for index in range(4)]

    report = run_qfast(rows, minimum_events=4)

    assert report.status is QFastStatus.REJECTED
    assert "loses_to_strongest_baseline" in report.reject_reasons
    assert report.strongest_baseline == BASELINE


def test_p95_latency_failure_forces_shadow_only() -> None:
    zero = run_qfast([row(str(index), 0.02) for index in range(4)], minimum_events=4)
    p95 = run_qfast([row(str(index), -0.01) for index in range(4)], minimum_events=4)

    result = evaluate_latency_gate({"zero": zero, "p95": p95}, required_profile="p95")

    assert result.status is LatencyGateStatus.SHADOW_ONLY
    assert result.required_profile == "p95"
    assert result.qfast_status is QFastStatus.REJECTED
