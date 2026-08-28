from datetime import UTC, datetime, timedelta

from ringdown_market.alpha.baselines import BaselineName, build_frozen_baselines
from ringdown_market.alpha.models import DecisionSnapshot, Direction

UTC = UTC


def test_builds_six_deterministic_baseline_signals() -> None:
    cutoff = datetime(2026, 8, 28, 13, 35, tzinfo=UTC)
    snapshot = DecisionSnapshot(
        event_id="evt-baselines",
        issuer="ACME",
        decision_cutoff=cutoff,
        latest_evidence_at=cutoff - timedelta(minutes=5),
        feature_snapshot_at=cutoff,
        opening_return=0.02,
        market_opening_return=0.03,
        sector_opening_return=0.0,
        market_beta=1.0,
        sector_beta=1.0,
        price_only_score=0.4,
        fundamental_score=-0.2,
        numeric_score=0.0,
        candidate_signal=Direction.UP,
    )

    signals = build_frozen_baselines(snapshot)

    assert signals == {
        BaselineName.ALWAYS_ABSTAIN: Direction.UNCERTAIN,
        BaselineName.GAP_CONTINUE: Direction.DOWN,
        BaselineName.GAP_REVERSE: Direction.UP,
        BaselineName.PRICE_ONLY: Direction.UP,
        BaselineName.FUNDAMENTAL_RULE: Direction.DOWN,
        BaselineName.NO_TEXT_ABLATION: Direction.UNCERTAIN,
    }
