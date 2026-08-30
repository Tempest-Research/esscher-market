"""Frozen beta estimation for the Esscher v1 beta policy."""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

from .provenance import EstimationPoint

MINIMUM_ESTIMATION_POINTS = 2
VARIANCE_EPSILON = 1e-12


class BetaEstimationRejected(ValueError):
    """Raised when pre-cutoff estimation data cannot support frozen betas."""


def _covariance(xs: Sequence[float], ys: Sequence[float]) -> float:
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / len(xs)


def _variance(xs: Sequence[float]) -> float:
    mean_x = sum(xs) / len(xs)
    return sum((x - mean_x) ** 2 for x in xs) / len(xs)


def estimate_frozen_betas(points: Sequence[EstimationPoint]) -> tuple[float, float]:
    """Estimate (market_beta, sector_beta) from synchronized pre-cutoff returns only."""

    if len(points) < MINIMUM_ESTIMATION_POINTS:
        raise BetaEstimationRejected("estimation requires at least two synchronized points")
    timestamps = [point.at for point in points]
    if len(set(timestamps)) != len(timestamps):
        raise BetaEstimationRejected("estimation timestamps must be unique")
    stock = [point.stock_return for point in points]
    market = [point.market_return for point in points]
    sector = [point.sector_return for point in points]

    market_variance = _variance(market)
    sector_variance = _variance(sector)
    if market_variance < VARIANCE_EPSILON or sector_variance < VARIANCE_EPSILON:
        raise BetaEstimationRejected("proxy variance is too small to estimate a beta")

    market_beta = _covariance(stock, market) / market_variance
    sector_beta = _covariance(stock, sector) / sector_variance
    for beta in (market_beta, sector_beta):
        if not isfinite(beta):
            raise BetaEstimationRejected("estimated beta must be finite")
    return market_beta, sector_beta
