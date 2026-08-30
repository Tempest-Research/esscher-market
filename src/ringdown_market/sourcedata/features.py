"""Deterministic construction of the frozen earnings-candidate features.

Every feature is built only from point-in-time evidence and synchronized
market observations at or before the registered clocks. Missing consensus is
reported as UNAVAILABLE and never imputed; missing required dependencies fail
closed with stable reason codes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ringdown_market.sourcedata.betas import FrozenBeta, residualize
from ringdown_market.sourcedata.decimal_math import collector_context, decimal_sqrt, log_return
from ringdown_market.sourcedata.evidence import EvidencePacket, source_refs_for
from ringdown_market.sourcedata.interfaces import IssuerRelease, QuoteSample
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.windows import SynchronizedWindow, quote_spread_basis_points
from ringdown_market.strategy.models import (
    FeatureStatus,
    FeatureValue,
    FeatureValueType,
    GuidanceDirection,
)

FEATURE_EPS_CONSENSUS = "earnings.eps_consensus_surprise_pct.v1"
FEATURE_REVENUE_CONSENSUS = "earnings.revenue_consensus_surprise_pct.v1"
FEATURE_EPS_SUE = "earnings.eps_timeseries_sue.v1"
FEATURE_REVENUE_YOY = "earnings.revenue_yoy_pct.v1"
FEATURE_GUIDANCE = "earnings.guidance_direction.v1"
FEATURE_EVENT_GAP = "market.event_gap_residual.v1"
FEATURE_OPENING_RESIDUAL = "market.opening_residual_log_return.v1"
FEATURE_RELATIVE_VOLUME = "market.opening_relative_volume_20d.v1"
FEATURE_NBBO_SPREAD = "market.opening_nbbo_spread_bps.v1"
FEATURE_QUOTE_AGE = "market.quote_age_ms.v1"
FEATURE_REALIZED_VOL = "market.realized_volatility_20d.v1"
FEATURE_RESIDUAL_MOMENTUM = "market.pre_event_residual_momentum_20d.v1"
FEATURE_VWAP_DISTANCE = "market.distance_from_opening_vwap_bps.v1"

VOLATILITY_SESSIONS = 20
RELATIVE_VOLUME_SESSIONS = 20
RELATIVE_VOLUME_MIN_MATCHING = 15
SUE_SEASONAL_DIFFERENCES = 8
MARKET_TRADE_CLASSES = ("LICENSED_SIP_EQUITY_TRADES",)
MARKET_QUOTE_CLASSES = ("LICENSED_SIP_EQUITY_QUOTES",)
ISSUER_CLASSES = ("ISSUER_INVESTOR_RELATIONS",)


@dataclass(frozen=True, slots=True)
class FeatureBuildInput:
    """All point-in-time inputs required by the earnings feature set."""

    release: IssuerRelease
    window: SynchronizedWindow
    beta: FrozenBeta
    ticker_symbol: str
    market_symbol: str
    sector_symbol: str
    stock_returns: Mapping[date, Decimal]
    market_returns: Mapping[date, Decimal]
    sector_returns: Mapping[date, Decimal]
    reaction_session_date: date
    prior_closes: Mapping[str, Decimal]
    window_volumes_by_session: Mapping[str, int | None]
    quotes_by_symbol: Mapping[str, Sequence[QuoteSample]]
    quote_entitlement_verified: bool
    evidence: EvidencePacket


def _unavailable(feature_id: str, *, unit: str, value_type: FeatureValueType) -> FeatureValue:
    return FeatureValue(
        feature_id=feature_id,
        status=FeatureStatus.UNAVAILABLE,
        value=None,
        value_type=value_type,
        unit=unit,
        observed_at=None,
        source_refs=(),
    )


def _quarter_sequence(release: IssuerRelease) -> tuple[object, ...]:
    history = tuple(release.quarter_history)
    return (release.current_quarter, *history)


def _eps_seasonal_differences(release: IssuerRelease) -> tuple[Decimal, ...]:
    quarters = _quarter_sequence(release)
    needed = SUE_SEASONAL_DIFFERENCES + 4
    if len(quarters) < needed:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.eps_timeseries_sue",
            f"at least {needed} quarters with reported EPS are required",
        )
    differences: list[Decimal] = []
    with collector_context():
        for index in range(SUE_SEASONAL_DIFFERENCES):
            current = quarters[index].eps_diluted
            prior = quarters[index + 4].eps_diluted
            if current is None or prior is None:
                raise CollectorRejected(
                    CollectorReason.FEATURE_DEPENDENCY_MISSING,
                    "features.eps_timeseries_sue",
                    "seasonal EPS history contains a missing quarter",
                )
            differences.append(current - prior)
    return tuple(differences)


def _sample_standard_deviation(values: Sequence[Decimal]) -> Decimal:
    count = Decimal(len(values))
    if len(values) < 2:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.sample_std",
            "sample standard deviation requires at least two observations",
        )
    with collector_context():
        mean = sum(values) / count
        variance = sum((item - mean) ** 2 for item in values) / (count - 1)
        return decimal_sqrt(variance)


def _build_eps_sue(
    release: IssuerRelease, *, observed_at: datetime, source_refs: tuple[str, ...]
) -> FeatureValue:
    differences = _eps_seasonal_differences(release)
    standard_deviation = _sample_standard_deviation(differences)
    with collector_context():
        if standard_deviation == 0:
            raise CollectorRejected(
                CollectorReason.NON_FINITE_FEATURE,
                "features.eps_timeseries_sue",
                "seasonal EPS differences have zero variance",
            )
        numerator = differences[0]
        value = numerator / standard_deviation
        if not value.is_finite():
            raise CollectorRejected(
                CollectorReason.NON_FINITE_FEATURE,
                "features.eps_timeseries_sue",
                "SUE value is not finite",
            )
    return FeatureValue(
        feature_id=FEATURE_EPS_SUE,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="STANDARD_DEVIATIONS",
        observed_at=observed_at,
        source_refs=source_refs,
    )


def _build_revenue_yoy(
    release: IssuerRelease, *, observed_at: datetime, source_refs: tuple[str, ...]
) -> FeatureValue:
    quarters = _quarter_sequence(release)
    if len(quarters) < 5:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.revenue_yoy",
            "prior-year revenue requires five reported quarters",
        )
    current = quarters[0].revenue
    prior = quarters[4].revenue
    if current is None or prior is None:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.revenue_yoy",
            "revenue history contains a missing quarter",
        )
    with collector_context():
        if prior <= 0:
            raise CollectorRejected(
                CollectorReason.FEATURE_DEPENDENCY_MISSING,
                "features.revenue_yoy",
                "prior-year revenue denominator must be positive",
            )
        value = (current - prior) / abs(prior)
    return FeatureValue(
        feature_id=FEATURE_REVENUE_YOY,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="RATIO",
        observed_at=observed_at,
        source_refs=source_refs,
    )


def _guidance_midpoint(low: Decimal | None, high: Decimal | None) -> Decimal | None:
    if low is None or high is None:
        return None
    with collector_context():
        return (low + high) / 2


def _compare_guidance_dimension(current: Decimal | None, prior: Decimal | None) -> int | None:
    if current is None or prior is None:
        return None
    with collector_context():
        if current > prior:
            return 1
        if current < prior:
            return -1
        return 0


def _build_guidance_direction(
    release: IssuerRelease, *, observed_at: datetime, source_refs: tuple[str, ...]
) -> FeatureValue:
    current = release.current_guidance
    prior = release.prior_guidance
    if current is None:
        direction = GuidanceDirection.NOT_GIVEN
    elif current.withdrawn:
        direction = GuidanceDirection.WITHDRAWN
    else:
        comparisons: list[int] = []
        if prior is not None and not prior.withdrawn:
            revenue_signal = _compare_guidance_dimension(
                _guidance_midpoint(current.revenue_low, current.revenue_high),
                _guidance_midpoint(prior.revenue_low, prior.revenue_high),
            )
            eps_signal = _compare_guidance_dimension(
                _guidance_midpoint(current.eps_low, current.eps_high),
                _guidance_midpoint(prior.eps_low, prior.eps_high),
            )
            comparisons = [signal for signal in (revenue_signal, eps_signal) if signal is not None]
        if not comparisons:
            direction = GuidanceDirection.NOT_GIVEN
        elif all(signal > 0 for signal in comparisons):
            direction = GuidanceDirection.RAISED
        elif all(signal < 0 for signal in comparisons):
            direction = GuidanceDirection.LOWERED
        elif all(signal == 0 for signal in comparisons):
            direction = GuidanceDirection.REITERATED
        else:
            direction = GuidanceDirection.MIXED
    return FeatureValue(
        feature_id=FEATURE_GUIDANCE,
        status=FeatureStatus.PRESENT,
        value=direction,
        value_type=FeatureValueType.ENUM,
        unit="RAISED|LOWERED|REITERATED|MIXED|WITHDRAWN|NOT_GIVEN",
        observed_at=observed_at,
        source_refs=source_refs,
    )


def _window_log_return(window: SynchronizedWindow, symbol: str) -> Decimal:
    symbol_window = window.symbol(symbol)
    with collector_context():
        return log_return(symbol_window.first_price, symbol_window.last_price)


def _gap_log_return(
    window: SynchronizedWindow, prior_closes: Mapping[str, Decimal], symbol: str
) -> Decimal:
    symbol_window = window.symbol(symbol)
    prior_close = prior_closes.get(symbol)
    if prior_close is None:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            f"features.event_gap.{symbol}",
            "prior regular close is required for the event gap",
        )
    with collector_context():
        return log_return(prior_close, symbol_window.first_price)


def _build_opening_residual(inputs: FeatureBuildInput) -> FeatureValue:
    with collector_context():
        stock = _window_log_return(inputs.window, inputs.ticker_symbol)
        market = _window_log_return(inputs.window, inputs.market_symbol)
        sector = _window_log_return(inputs.window, inputs.sector_symbol)
        value = residualize(stock, market, sector, inputs.beta)
    return FeatureValue(
        feature_id=FEATURE_OPENING_RESIDUAL,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="LOG_RETURN",
        observed_at=inputs.window.window_end_at,
        source_refs=source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES),
    )


def _build_event_gap(inputs: FeatureBuildInput) -> FeatureValue:
    with collector_context():
        stock = _gap_log_return(inputs.window, inputs.prior_closes, inputs.ticker_symbol)
        market = _gap_log_return(inputs.window, inputs.prior_closes, inputs.market_symbol)
        sector = _gap_log_return(inputs.window, inputs.prior_closes, inputs.sector_symbol)
        value = residualize(stock, market, sector, inputs.beta)
    return FeatureValue(
        feature_id=FEATURE_EVENT_GAP,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="LOG_RETURN",
        observed_at=inputs.window.window_end_at,
        source_refs=source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES),
    )


def _median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    if count % 2 == 1:
        return ordered[count // 2]
    with collector_context():
        return (ordered[count // 2 - 1] + ordered[count // 2]) / 2


def _build_relative_volume(inputs: FeatureBuildInput) -> FeatureValue:
    current_volume = inputs.window.symbol(inputs.ticker_symbol).window_volume
    volumes: list[Decimal] = []
    for session_id, volume in sorted(inputs.window_volumes_by_session.items()):
        if session_id == inputs.window.session_id:
            continue
        if volume is not None and volume > 0:
            volumes.append(Decimal(volume))
    if len(volumes) < RELATIVE_VOLUME_MIN_MATCHING:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.opening_relative_volume",
            f"at least {RELATIVE_VOLUME_MIN_MATCHING} matching prior sessions are required",
        )
    if len(volumes) > RELATIVE_VOLUME_SESSIONS:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "features.opening_relative_volume",
            f"at most {RELATIVE_VOLUME_SESSIONS} prior sessions may be supplied",
        )
    with collector_context():
        value = Decimal(current_volume) / _median(volumes)
    return FeatureValue(
        feature_id=FEATURE_RELATIVE_VOLUME,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="RATIO",
        observed_at=inputs.window.window_end_at,
        source_refs=source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES),
    )


def _build_nbbo_spread(inputs: FeatureBuildInput) -> FeatureValue:
    if not inputs.quote_entitlement_verified:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.opening_nbbo_spread",
            "verified SIP quote entitlement is required",
        )
    value = quote_spread_basis_points(
        inputs.quotes_by_symbol.get(inputs.ticker_symbol, ()),
        symbol=inputs.ticker_symbol,
        window_end_at=inputs.window.window_end_at,
    )
    return FeatureValue(
        feature_id=FEATURE_NBBO_SPREAD,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="BASIS_POINTS",
        observed_at=inputs.window.window_end_at,
        source_refs=source_refs_for(inputs.evidence, source_classes=MARKET_QUOTE_CLASSES),
    )


def _build_quote_age(inputs: FeatureBuildInput) -> FeatureValue:
    required = {inputs.ticker_symbol, inputs.market_symbol, inputs.sector_symbol}
    if set(inputs.window.quote_age_ms) != required:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.quote_age",
            "quote ages are required for issuer, market, and sector symbols",
        )
    value = max(inputs.window.quote_age_ms.values())
    return FeatureValue(
        feature_id=FEATURE_QUOTE_AGE,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.INTEGER,
        unit="MILLISECONDS",
        observed_at=inputs.window.window_end_at,
        source_refs=source_refs_for(inputs.evidence, source_classes=MARKET_QUOTE_CLASSES),
    )


def _pre_event_daily_returns(
    inputs: FeatureBuildInput,
) -> tuple[tuple[Decimal, Decimal, Decimal], ...]:
    dates = sorted(
        set(inputs.stock_returns) & set(inputs.market_returns) & set(inputs.sector_returns)
    )
    pre_event = tuple(d for d in dates if d < inputs.reaction_session_date)
    return tuple(
        (inputs.stock_returns[d], inputs.market_returns[d], inputs.sector_returns[d])
        for d in pre_event
    )


def _build_realized_volatility(inputs: FeatureBuildInput) -> FeatureValue:
    triples = _pre_event_daily_returns(inputs)
    if len(triples) < VOLATILITY_SESSIONS:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.realized_volatility",
            f"exactly {VOLATILITY_SESSIONS} pre-event sessions are required",
        )
    recent = triples[-VOLATILITY_SESSIONS:]
    with collector_context():
        values = tuple(triple[0] for triple in recent)
        volatility = _sample_standard_deviation(values) * decimal_sqrt(Decimal(252))
    return FeatureValue(
        feature_id=FEATURE_REALIZED_VOL,
        status=FeatureStatus.PRESENT,
        value=volatility,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="ANNUALIZED_LOG_RETURN_VOLATILITY",
        observed_at=inputs.window.window_end_at,
        source_refs=source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES),
    )


def _build_residual_momentum(inputs: FeatureBuildInput) -> FeatureValue:
    triples = _pre_event_daily_returns(inputs)
    if len(triples) < VOLATILITY_SESSIONS:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.pre_event_residual_momentum",
            f"exactly {VOLATILITY_SESSIONS} pre-event sessions are required",
        )
    recent = triples[-VOLATILITY_SESSIONS:]
    with collector_context():
        total = Decimal(0)
        for stock, market, sector in recent:
            total += residualize(stock, market, sector, inputs.beta)
    return FeatureValue(
        feature_id=FEATURE_RESIDUAL_MOMENTUM,
        status=FeatureStatus.PRESENT,
        value=total,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="LOG_RETURN",
        observed_at=inputs.window.window_end_at,
        source_refs=source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES),
    )


def _build_vwap_distance(inputs: FeatureBuildInput) -> FeatureValue:
    symbol_window = inputs.window.symbol(inputs.ticker_symbol)
    with collector_context():
        if symbol_window.window_vwap <= 0:
            raise CollectorRejected(
                CollectorReason.NON_FINITE_FEATURE,
                "features.distance_from_opening_vwap",
                "window VWAP must be positive",
            )
        value = (symbol_window.last_price / symbol_window.window_vwap - 1) * 10000
    return FeatureValue(
        feature_id=FEATURE_VWAP_DISTANCE,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="BASIS_POINTS",
        observed_at=inputs.window.window_end_at,
        source_refs=source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES),
    )


def build_earnings_features(inputs: FeatureBuildInput) -> tuple[FeatureValue, ...]:
    """Build the complete sorted earnings-candidate feature set."""

    issuer_refs = source_refs_for(inputs.evidence, source_classes=ISSUER_CLASSES)
    if not issuer_refs:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "features.issuer_evidence",
            "issuer evidence is required for earnings features",
        )
    release = inputs.release
    evidence_observed_at = inputs.evidence.receipt(issuer_refs[0]).retrieved_at
    features = (
        _unavailable(
            FEATURE_EPS_CONSENSUS, unit="RATIO", value_type=FeatureValueType.DECIMAL_STRING
        ),
        _unavailable(
            FEATURE_REVENUE_CONSENSUS, unit="RATIO", value_type=FeatureValueType.DECIMAL_STRING
        ),
        _build_eps_sue(release, observed_at=evidence_observed_at, source_refs=issuer_refs),
        _build_revenue_yoy(release, observed_at=evidence_observed_at, source_refs=issuer_refs),
        _build_guidance_direction(
            release, observed_at=evidence_observed_at, source_refs=issuer_refs
        ),
        _build_event_gap(inputs),
        _build_opening_residual(inputs),
        _build_relative_volume(inputs),
        _build_nbbo_spread(inputs),
        _build_quote_age(inputs),
        _build_realized_volatility(inputs),
        _build_residual_momentum(inputs),
        _build_vwap_distance(inputs),
    )
    ordered = tuple(sorted(features, key=lambda item: item.feature_id))
    for feature in ordered:
        if (
            feature.status is FeatureStatus.PRESENT
            and isinstance(feature.value, Decimal)
            and not feature.value.is_finite()
        ):
            raise CollectorRejected(
                CollectorReason.NON_FINITE_FEATURE,
                f"features.{feature.feature_id}",
                "feature value must be finite",
            )
    return ordered
