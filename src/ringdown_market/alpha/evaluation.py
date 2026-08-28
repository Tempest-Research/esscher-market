"""Latency-aware residual-return evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import log

from .models import Direction, EventCase


class MissingPricePoint(ValueError):
    """Raised when the path cannot supply an achievable entry or exit."""


@dataclass(frozen=True, slots=True)
class EventEvaluation:
    event_id: str
    signal: Direction
    entry_at: datetime
    exit_at: datetime
    actual_latency_ms: int
    residual_return: float
    signed_residual: float
    admitted: bool


def evaluate_event(
    case: EventCase,
    signal: Direction,
    *,
    latency_ms: int,
    hold_seconds: int,
) -> EventEvaluation:
    """Evaluate one signal from the first achievable entry through a fill-relative hold."""

    if latency_ms < 0:
        raise ValueError("latency_ms must be non-negative")
    if hold_seconds <= 0:
        raise ValueError("hold_seconds must be positive")

    target_entry = case.decision.decision_cutoff + timedelta(milliseconds=latency_ms)
    entry = case.path.first_at_or_after(target_entry)
    if entry is None:
        raise MissingPricePoint("no achievable entry price exists")

    target_exit = entry.at + timedelta(seconds=hold_seconds)
    exit_point = case.path.first_at_or_after(target_exit)
    if exit_point is None:
        raise MissingPricePoint("no fill-relative exit price exists")

    stock_return = log(exit_point.stock / entry.stock)
    market_return = log(exit_point.market / entry.market)
    sector_return = log(exit_point.sector / entry.sector)
    residual = (
        stock_return
        - case.decision.market_beta * market_return
        - case.decision.sector_beta * sector_return
    )
    admitted = signal is not Direction.UNCERTAIN
    signed = signal.multiplier * residual if admitted else 0.0
    actual_latency_ms = int((entry.at - case.decision.decision_cutoff).total_seconds() * 1_000)

    return EventEvaluation(
        event_id=case.decision.event_id,
        signal=signal,
        entry_at=entry.at,
        exit_at=exit_point.at,
        actual_latency_ms=actual_latency_ms,
        residual_return=residual,
        signed_residual=signed,
        admitted=admitted,
    )
