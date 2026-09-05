from datetime import UTC, datetime, timedelta
from math import log

import pytest

from esscher.alpha.evaluation import MissingPricePoint, evaluate_event
from esscher.alpha.models import (
    DecisionSnapshot,
    Direction,
    EventCase,
    MarketPath,
    PricePoint,
)

UTC = UTC


def snapshot(
    *,
    signal: Direction = Direction.UP,
    market_beta: float = 0.0,
    sector_beta: float = 0.0,
) -> DecisionSnapshot:
    cutoff = datetime(2026, 8, 28, 13, 35, tzinfo=UTC)
    return DecisionSnapshot(
        event_id="evt-latency",
        issuer="ACME",
        decision_cutoff=cutoff,
        latest_evidence_at=cutoff - timedelta(minutes=5),
        feature_snapshot_at=cutoff,
        opening_return=0.01,
        market_opening_return=0.0,
        sector_opening_return=0.0,
        market_beta=market_beta,
        sector_beta=sector_beta,
        price_only_score=0.0,
        fundamental_score=0.0,
        numeric_score=0.0,
        candidate_signal=signal,
    )


def point(at: datetime, stock: float, market: float = 100.0, sector: float = 100.0) -> PricePoint:
    return PricePoint(at=at, stock=stock, market=market, sector=sector)


def test_uses_achievable_entry_and_fill_relative_exit() -> None:
    decision = snapshot()
    cutoff = decision.decision_cutoff
    case = EventCase(
        decision=decision,
        path=MarketPath(
            (
                point(cutoff, 100.0),
                point(cutoff + timedelta(seconds=30), 102.0),
                point(cutoff + timedelta(minutes=60), 110.0),
                point(cutoff + timedelta(minutes=60, seconds=30), 104.04),
            )
        ),
    )

    result = evaluate_event(case, Direction.UP, latency_ms=30_000, hold_seconds=3_600)

    assert result.entry_at == cutoff + timedelta(seconds=30)
    assert result.exit_at == cutoff + timedelta(minutes=60, seconds=30)
    assert result.actual_latency_ms == 30_000
    assert result.signed_residual == pytest.approx(log(104.04 / 102.0))


def test_removes_market_and_sector_components_over_the_same_window() -> None:
    decision = snapshot(market_beta=1.0, sector_beta=0.5)
    cutoff = decision.decision_cutoff
    case = EventCase(
        decision=decision,
        path=MarketPath(
            (
                point(cutoff, 100.0, 100.0, 100.0),
                point(cutoff + timedelta(hours=1), 105.0, 102.0, 101.0),
            )
        ),
    )

    result = evaluate_event(case, Direction.UP, latency_ms=0, hold_seconds=3_600)

    expected = log(1.05) - log(1.02) - 0.5 * log(1.01)
    assert result.residual_return == pytest.approx(expected)


def test_uncertain_signal_remains_in_panel_as_zero_return() -> None:
    decision = snapshot(signal=Direction.UNCERTAIN)
    cutoff = decision.decision_cutoff
    case = EventCase(
        decision=decision,
        path=MarketPath(
            (
                point(cutoff, 100.0),
                point(cutoff + timedelta(hours=1), 130.0),
            )
        ),
    )

    result = evaluate_event(case, Direction.UNCERTAIN, latency_ms=0, hold_seconds=3_600)

    assert result.admitted is False
    assert result.signed_residual == 0.0


def test_fails_when_no_fill_relative_exit_price_exists() -> None:
    decision = snapshot()
    cutoff = decision.decision_cutoff
    case = EventCase(
        decision=decision,
        path=MarketPath((point(cutoff, 100.0), point(cutoff + timedelta(minutes=59), 101.0))),
    )

    with pytest.raises(MissingPricePoint, match="exit"):
        evaluate_event(case, Direction.UP, latency_ms=0, hold_seconds=3_600)
