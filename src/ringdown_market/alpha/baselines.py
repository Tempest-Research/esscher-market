"""Frozen no-LLM baselines used to challenge the candidate signal."""

from __future__ import annotations

from enum import StrEnum

from .models import DecisionSnapshot, Direction


class BaselineName(StrEnum):
    ALWAYS_ABSTAIN = "always_abstain"
    GAP_CONTINUE = "gap_continue"
    GAP_REVERSE = "gap_reverse"
    PRICE_ONLY = "price_only"
    FUNDAMENTAL_RULE = "fundamental_rule"
    NO_TEXT_ABLATION = "no_text_ablation"


def _direction(score: float) -> Direction:
    if score > 0:
        return Direction.UP
    if score < 0:
        return Direction.DOWN
    return Direction.UNCERTAIN


def build_frozen_baselines(snapshot: DecisionSnapshot) -> dict[BaselineName, Direction]:
    """Return fixed comparison signals without reading any post-cutoff outcome."""

    opening_residual = (
        snapshot.opening_return
        - snapshot.market_beta * snapshot.market_opening_return
        - snapshot.sector_beta * snapshot.sector_opening_return
    )
    gap_direction = _direction(opening_residual)
    reverse_direction = {
        Direction.UP: Direction.DOWN,
        Direction.DOWN: Direction.UP,
        Direction.UNCERTAIN: Direction.UNCERTAIN,
    }[gap_direction]

    return {
        BaselineName.ALWAYS_ABSTAIN: Direction.UNCERTAIN,
        BaselineName.GAP_CONTINUE: gap_direction,
        BaselineName.GAP_REVERSE: reverse_direction,
        BaselineName.PRICE_ONLY: _direction(snapshot.price_only_score),
        BaselineName.FUNDAMENTAL_RULE: _direction(snapshot.fundamental_score),
        BaselineName.NO_TEXT_ABLATION: _direction(snapshot.numeric_score),
    }
