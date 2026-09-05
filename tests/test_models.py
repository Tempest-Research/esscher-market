from datetime import UTC, datetime, timedelta

import pytest

from esscher.alpha.models import (
    DecisionSnapshot,
    Direction,
    PointInTimeViolation,
    PricePoint,
)

UTC = UTC


def valid_snapshot(**overrides: object) -> DecisionSnapshot:
    cutoff = datetime(2026, 8, 28, 13, 35, tzinfo=UTC)
    values: dict[str, object] = {
        "event_id": "evt-1",
        "issuer": "ACME",
        "decision_cutoff": cutoff,
        "latest_evidence_at": cutoff - timedelta(minutes=10),
        "feature_snapshot_at": cutoff,
        "opening_return": 0.01,
        "market_opening_return": 0.002,
        "sector_opening_return": 0.003,
        "market_beta": 1.0,
        "sector_beta": 0.5,
        "price_only_score": 0.1,
        "fundamental_score": 0.2,
        "numeric_score": 0.2,
        "candidate_signal": Direction.UP,
    }
    values.update(overrides)
    return DecisionSnapshot(**values)


def test_rejects_evidence_published_after_decision_cutoff() -> None:
    cutoff = datetime(2026, 8, 28, 13, 35, tzinfo=UTC)

    with pytest.raises(PointInTimeViolation, match="evidence"):
        valid_snapshot(latest_evidence_at=cutoff + timedelta(seconds=1))


def test_rejects_feature_snapshot_after_decision_cutoff() -> None:
    cutoff = datetime(2026, 8, 28, 13, 35, tzinfo=UTC)

    with pytest.raises(PointInTimeViolation, match="feature snapshot"):
        valid_snapshot(feature_snapshot_at=cutoff + timedelta(milliseconds=1))


def test_rejects_timezone_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        valid_snapshot(decision_cutoff=datetime(2026, 8, 28, 13, 35))


def test_price_point_rejects_non_positive_prices() -> None:
    at = datetime(2026, 8, 28, 13, 35, tzinfo=UTC)

    with pytest.raises(ValueError, match="positive"):
        PricePoint(at=at, stock=0.0, market=100.0, sector=100.0)
