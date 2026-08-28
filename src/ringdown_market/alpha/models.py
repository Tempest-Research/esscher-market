"""Immutable contracts for point-in-time event evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class Direction(StrEnum):
    """A bounded directional decision with an explicit abstention state."""

    UP = "UP"
    DOWN = "DOWN"
    UNCERTAIN = "UNCERTAIN"

    @property
    def multiplier(self) -> int:
        return {self.UP: 1, self.DOWN: -1, self.UNCERTAIN: 0}[self]


class PointInTimeViolation(ValueError):
    """Raised when a decision contains information unavailable at its cutoff."""


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_finite(value: float, field: str) -> None:
    if not isfinite(value):
        raise ValueError(f"{field} must be finite")


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """Information legally available to signal construction at the cutoff."""

    event_id: str
    issuer: str
    decision_cutoff: datetime
    latest_evidence_at: datetime
    feature_snapshot_at: datetime
    opening_return: float
    market_opening_return: float
    sector_opening_return: float
    market_beta: float
    sector_beta: float
    price_only_score: float
    fundamental_score: float
    numeric_score: float
    candidate_signal: Direction

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.issuer.strip():
            raise ValueError("event_id and issuer must be non-empty")
        for field in ("decision_cutoff", "latest_evidence_at", "feature_snapshot_at"):
            _require_aware(getattr(self, field), field)
        if self.latest_evidence_at > self.decision_cutoff:
            raise PointInTimeViolation("evidence was published after the decision cutoff")
        if self.feature_snapshot_at > self.decision_cutoff:
            raise PointInTimeViolation("feature snapshot was created after the decision cutoff")
        for field in (
            "opening_return",
            "market_opening_return",
            "sector_opening_return",
            "market_beta",
            "sector_beta",
            "price_only_score",
            "fundamental_score",
            "numeric_score",
        ):
            _require_finite(getattr(self, field), field)


@dataclass(frozen=True, slots=True)
class PricePoint:
    """Synchronized stock, market, and sector prices at one instant."""

    at: datetime
    stock: float
    market: float
    sector: float

    def __post_init__(self) -> None:
        _require_aware(self.at, "price point timestamp")
        for field in ("stock", "market", "sector"):
            value = getattr(self, field)
            _require_finite(value, field)
            if value <= 0:
                raise ValueError(f"{field} price must be positive")


@dataclass(frozen=True, slots=True)
class MarketPath:
    """A strictly ordered synchronized price path used only by evaluation."""

    points: tuple[PricePoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("market path must contain at least one price point")
        timestamps = [point.at for point in self.points]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise ValueError("market path timestamps must be strictly increasing")

    def first_at_or_after(self, at: datetime) -> PricePoint | None:
        _require_aware(at, "requested price timestamp")
        return next((point for point in self.points if point.at >= at), None)


@dataclass(frozen=True, slots=True)
class EventCase:
    """A decision snapshot paired with post-decision prices for evaluation only."""

    decision: DecisionSnapshot
    path: MarketPath
