"""Frozen beta estimation under the accepted event policy.

Betas come from one OLS regression of split-adjusted price-only daily log
returns:

    r_stock = alpha + beta_market * r_SPY + beta_sector * (r_sector - r_SPY) + error

The estimation window is exactly 252 sessions ending 21 sessions before the
reaction session. At least 200 aligned observations are required; no
winsorization or forward fill is applied. The regressor condition number is
bounded by thirty and the coefficients by the frozen policy bounds.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from esscher.sourcedata.adjustments import AdjustmentOutcome
from esscher.sourcedata.decimal_math import collector_context, decimal_sqrt, log_return
from esscher.sourcedata.reasons import CollectorReason, CollectorRejected

BETA_WINDOW_SESSIONS = 252
BETA_REACTION_GAP_SESSIONS = 21
BETA_MIN_ALIGNED_OBSERVATIONS = 200
BETA_MAX_CONDITION_NUMBER = Decimal(30)
BETA_MARKET_MIN = Decimal(-1)
BETA_MARKET_MAX = Decimal(3)
BETA_SECTOR_MIN = Decimal(-2)
BETA_SECTOR_MAX = Decimal(2)


@dataclass(frozen=True, slots=True)
class FrozenBeta:
    """One disclosed frozen beta estimate."""

    beta_market: Decimal
    beta_sector: Decimal
    aligned_observation_count: int
    condition_number: Decimal
    window_start_date: date
    window_end_date: date


def daily_log_returns(series: AdjustmentOutcome) -> dict[date, Decimal]:
    """Return session-date keyed log returns from one adjusted series."""

    valid = tuple(item for item in series.series if item.valid)
    if len(valid) < 2:
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_MISSING,
            f"daily_returns.{series.symbol}",
            "at least two valid sessions are required",
        )
    ordered = sorted(valid, key=lambda item: item.session_date)
    returns: dict[date, Decimal] = {}
    with collector_context():
        for previous, current in itertools.pairwise(ordered):
            returns[current.session_date] = log_return(
                previous.adjusted_close, current.adjusted_close
            )
    return returns


def select_beta_window(
    session_dates: Sequence[date], reaction_session_date: date
) -> tuple[date, ...]:
    """Return the frozen 252-session estimation window."""

    ordered = tuple(sorted(set(session_dates)))
    if reaction_session_date not in ordered:
        raise CollectorRejected(
            CollectorReason.CLOCK_MISMATCH,
            "beta_window.reaction_session",
            "reaction session is absent from the session calendar",
        )
    index = ordered.index(reaction_session_date)
    window_end_index = index - BETA_REACTION_GAP_SESSIONS
    window_start_index = window_end_index - BETA_WINDOW_SESSIONS + 1
    if window_start_index < 0:
        raise CollectorRejected(
            CollectorReason.BETA_INSUFFICIENT_OBSERVATIONS,
            "beta_window",
            f"the calendar must contain {BETA_WINDOW_SESSIONS + BETA_REACTION_GAP_SESSIONS}"
            f" sessions before the reaction session",
        )
    return ordered[window_start_index : window_end_index + 1]


def estimate_betas(
    stock_series: AdjustmentOutcome,
    market_series: AdjustmentOutcome,
    sector_series: AdjustmentOutcome,
    *,
    session_dates: Sequence[date],
    reaction_session_date: date,
) -> FrozenBeta:
    """Estimate frozen market and sector betas or fail closed."""

    window = select_beta_window(session_dates, reaction_session_date)
    stock_returns = daily_log_returns(stock_series)
    market_returns = daily_log_returns(market_series)
    sector_returns = daily_log_returns(sector_series)
    aligned = tuple(
        session
        for session in window
        if session in stock_returns and session in market_returns and session in sector_returns
    )
    if len(aligned) < BETA_MIN_ALIGNED_OBSERVATIONS:
        raise CollectorRejected(
            CollectorReason.BETA_INSUFFICIENT_OBSERVATIONS,
            "beta.observations",
            f"aligned observations {len(aligned)} are below the frozen minimum"
            f" {BETA_MIN_ALIGNED_OBSERVATIONS}",
        )
    with collector_context():
        y = tuple(stock_returns[s] for s in aligned)
        x1 = tuple(market_returns[s] for s in aligned)
        x2 = tuple(sector_returns[s] - market_returns[s] for s in aligned)
        count = Decimal(len(aligned))
        y_mean = sum(y) / count
        x1_mean = sum(x1) / count
        x2_mean = sum(x2) / count
        s11 = Decimal(0)
        s12 = Decimal(0)
        s22 = Decimal(0)
        b1 = Decimal(0)
        b2 = Decimal(0)
        for y_item, x1_item, x2_item in zip(y, x1, x2, strict=True):
            dy = y_item - y_mean
            d1 = x1_item - x1_mean
            d2 = x2_item - x2_mean
            s11 += d1 * d1
            s12 += d1 * d2
            s22 += d2 * d2
            b1 += d1 * dy
            b2 += d2 * dy
        determinant = s11 * s22 - s12 * s12
        if determinant <= 0:
            raise CollectorRejected(
                CollectorReason.BETA_ILL_CONDITIONED,
                "beta.design",
                "regressor cross-product matrix is singular",
            )
        trace = s11 + s22
        discriminant = trace * trace - 4 * determinant
        root = decimal_sqrt(discriminant)
        lambda_max = (trace + root) / 2
        lambda_min = (trace - root) / 2
        if lambda_min <= 0:
            raise CollectorRejected(
                CollectorReason.BETA_ILL_CONDITIONED,
                "beta.design",
                "regressor cross-product matrix has a non-positive eigenvalue",
            )
        condition_number = decimal_sqrt(lambda_max / lambda_min)
        if condition_number > BETA_MAX_CONDITION_NUMBER:
            raise CollectorRejected(
                CollectorReason.BETA_ILL_CONDITIONED,
                "beta.design",
                f"condition number {condition_number} exceeds the frozen bound",
            )
        beta_market = (b1 * s22 - b2 * s12) / determinant
        beta_sector = (s11 * b2 - s12 * b1) / determinant
    if not (BETA_MARKET_MIN <= beta_market <= BETA_MARKET_MAX):
        raise CollectorRejected(
            CollectorReason.BETA_OUT_OF_BOUNDS,
            "beta.beta_market",
            f"beta_market {beta_market} is outside [-1, 3]",
        )
    if not (BETA_SECTOR_MIN <= beta_sector <= BETA_SECTOR_MAX):
        raise CollectorRejected(
            CollectorReason.BETA_OUT_OF_BOUNDS,
            "beta.beta_sector",
            f"beta_sector {beta_sector} is outside [-2, 2]",
        )
    return FrozenBeta(
        beta_market=beta_market,
        beta_sector=beta_sector,
        aligned_observation_count=len(aligned),
        condition_number=condition_number,
        window_start_date=window[0],
        window_end_date=window[-1],
    )


def residualize(
    stock_return: Decimal,
    market_return: Decimal,
    sector_return: Decimal,
    beta: FrozenBeta,
) -> Decimal:
    """Apply the frozen factor model to one aligned return triple."""

    with collector_context():
        return (
            stock_return
            - beta.beta_market * market_return
            - beta.beta_sector * (sector_return - market_return)
        )
