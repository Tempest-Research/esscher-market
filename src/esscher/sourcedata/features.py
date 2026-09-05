"""Deterministic construction of the frozen candidate features.

Every feature is built only from point-in-time evidence and synchronized
market observations at or before the registered clocks. Missing consensus is
reported as UNAVAILABLE and never imputed; missing required dependencies fail
closed with stable reason codes. Both the earnings primary candidate and the
macro challenger are supported; revised macro values never replace the base
release fields silently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from esscher.sourcedata.betas import FrozenBeta, residualize
from esscher.sourcedata.decimal_math import collector_context, decimal_sqrt, log_return
from esscher.sourcedata.evidence import EvidencePacket, source_refs_for
from esscher.sourcedata.interfaces import (
    IssuerRelease,
    MacroRelease,
    MacroRevision,
    QuoteSample,
)
from esscher.sourcedata.reasons import CollectorReason, CollectorRejected
from esscher.sourcedata.windows import SynchronizedWindow, quote_spread_basis_points
from esscher.strategy.models import (
    FeatureComponent,
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


def _proven_observed_at(
    evidence: EvidencePacket,
    source_refs: tuple[str, ...],
    lower_bound: datetime,
) -> datetime:
    """Return the instant a feature became usable.

    A feature cannot be observed before the evidence it cites was available,
    so the observation time is bounded below by every cited source's
    collector-proven availability and by the observation lower bound.
    """

    observed_at = lower_bound
    for source_ref in source_refs:
        retrieved_at = evidence.receipt(source_ref).retrieved_at
        if retrieved_at > observed_at:
            observed_at = retrieved_at
    return observed_at


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
    source_refs = source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_OPENING_RESIDUAL,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="LOG_RETURN",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window.window_end_at),
        source_refs=source_refs,
    )


def _build_event_gap(inputs: FeatureBuildInput) -> FeatureValue:
    with collector_context():
        stock = _gap_log_return(inputs.window, inputs.prior_closes, inputs.ticker_symbol)
        market = _gap_log_return(inputs.window, inputs.prior_closes, inputs.market_symbol)
        sector = _gap_log_return(inputs.window, inputs.prior_closes, inputs.sector_symbol)
        value = residualize(stock, market, sector, inputs.beta)
    source_refs = source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_EVENT_GAP,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="LOG_RETURN",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window.window_end_at),
        source_refs=source_refs,
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
    source_refs = source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_RELATIVE_VOLUME,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="RATIO",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window.window_end_at),
        source_refs=source_refs,
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
    source_refs = source_refs_for(inputs.evidence, source_classes=MARKET_QUOTE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_NBBO_SPREAD,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="BASIS_POINTS",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window.window_end_at),
        source_refs=source_refs,
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
    source_refs = source_refs_for(inputs.evidence, source_classes=MARKET_QUOTE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_QUOTE_AGE,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.INTEGER,
        unit="MILLISECONDS",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window.window_end_at),
        source_refs=source_refs,
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
    source_refs = source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_REALIZED_VOL,
        status=FeatureStatus.PRESENT,
        value=volatility,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="ANNUALIZED_LOG_RETURN_VOLATILITY",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window.window_end_at),
        source_refs=source_refs,
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
    source_refs = source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_RESIDUAL_MOMENTUM,
        status=FeatureStatus.PRESENT,
        value=total,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="LOG_RETURN",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window.window_end_at),
        source_refs=source_refs,
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
    source_refs = source_refs_for(inputs.evidence, source_classes=MARKET_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_VWAP_DISTANCE,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="BASIS_POINTS",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window.window_end_at),
        source_refs=source_refs,
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
    evidence_observed_at = _proven_observed_at(
        inputs.evidence, issuer_refs, inputs.evidence.receipt(issuer_refs[0]).retrieved_at
    )
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


MACRO_COHORT_JOLTS = "BLS_JOLTS"
MACRO_COHORT_EMPLOYMENT = "BLS_EMPLOYMENT_SITUATION"
MACRO_FIELD_FEATURES: Mapping[str, Mapping[str, str]] = {
    MACRO_COHORT_JOLTS: {
        "job_openings": "macro.jolts.job_openings.v1",
        "hires": "macro.jolts.hires.v1",
        "quits": "macro.jolts.quits.v1",
        "layoffs_and_discharges": "macro.jolts.layoffs_and_discharges.v1",
        "total_separations": "macro.jolts.total_separations.v1",
    },
    MACRO_COHORT_EMPLOYMENT: {
        "nonfarm_payrolls": "macro.employment.nonfarm_payrolls.v1",
        "unemployment_rate": "macro.employment.unemployment_rate.v1",
        "average_hourly_earnings_mom": "macro.employment.average_hourly_earnings_mom.v1",
        "participation_rate": "macro.employment.participation_rate.v1",
    },
}
MACRO_FIELD_UNITS: Mapping[str, str] = {
    "job_openings": "COUNT",
    "hires": "COUNT",
    "quits": "COUNT",
    "layoffs_and_discharges": "COUNT",
    "total_separations": "COUNT",
    "nonfarm_payrolls": "COUNT",
    "unemployment_rate": "PERCENTAGE_POINTS",
    "average_hourly_earnings_mom": "RATIO",
    "participation_rate": "PERCENTAGE_POINTS",
}
FEATURE_MACRO_CONSENSUS_VECTOR = "macro.consensus_surprise_vector.v1"
FEATURE_MACRO_REVISION_VECTOR = "macro.revision_vector.v1"
FEATURE_SPY_EVENT_LOG_RETURN = "market.spy_event_log_return.v1"
FEATURE_SPY_EVENT_ZSCORE = "market.spy_event_zscore_60.v1"
FEATURE_SPY_EVENT_VOLUME_RATIO = "market.spy_event_volume_ratio_20.v1"
FEATURE_SPY_EVENT_VWAP_DISTANCE = "market.spy_event_vwap_distance_bps.v1"
FEATURE_SPY_EVENT_RANGE = "market.spy_event_range_bps.v1"
FEATURE_SPY_EVENT_REVERSAL = "market.spy_event_reversal_bps.v1"
FEATURE_SPY_NBBO_SPREAD = "market.spy_nbbo_spread_bps.v1"
FEATURE_SPY_QUOTE_AGE = "market.spy_quote_age_ms.v1"
FEATURE_SPY_REALIZED_VOL = "market.spy_realized_volatility_20d.v1"
MACRO_BLS_SOURCE_CLASSES = ("OFFICIAL_BLS_RELEASE", "OFFICIAL_BLS_REVISION_TABLE")
MACRO_SPY_TRADE_CLASSES = ("LICENSED_SIP_SPY_TRADES",)
MACRO_SPY_QUOTE_CLASSES = ("LICENSED_SIP_SPY_QUOTES",)
ZSCORE_MIN_SESSIONS = 45
ZSCORE_SCALE_FLOOR = Decimal("0.0005")
ZSCORE_MAD_MULTIPLIER = Decimal("1.4826")


@dataclass(frozen=True, slots=True)
class MacroFeatureBuildInput:
    """All point-in-time inputs required by the macro-challenger feature set."""

    release: MacroRelease
    revisions: tuple[MacroRevision, ...]
    cohort_id: str
    anchor_mid: Decimal
    end_mid: Decimal
    window_vwap: Decimal
    window_high: Decimal
    window_low: Decimal
    window_volume: int
    prior_window_volumes: tuple[int, ...]
    normalization_returns: tuple[Decimal, ...]
    spy_daily_returns: tuple[Decimal, ...]
    spy_quotes: Sequence[QuoteSample]
    spy_symbol: str
    window_end_at: datetime
    quote_entitlement_verified: bool
    evidence: EvidencePacket


def _median_decimal(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    if count % 2 == 1:
        return ordered[count // 2]
    with collector_context():
        return (ordered[count // 2 - 1] + ordered[count // 2]) / 2


def _macro_unavailable(feature_id: str, unit: str) -> FeatureValue:
    return FeatureValue(
        feature_id=feature_id,
        status=FeatureStatus.UNAVAILABLE,
        value=None,
        value_type=FeatureValueType.DECIMAL_STRING_MAP,
        unit=unit,
        observed_at=None,
        source_refs=(),
    )


def _macro_not_applicable(feature_id: str, unit: str) -> FeatureValue:
    return FeatureValue(
        feature_id=feature_id,
        status=FeatureStatus.NOT_APPLICABLE,
        value=None,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit=unit,
        observed_at=None,
        source_refs=(),
    )


def _build_macro_components(inputs: MacroFeatureBuildInput) -> list[FeatureValue]:
    features: list[FeatureValue] = []
    bls_refs = source_refs_for(inputs.evidence, source_classes=MACRO_BLS_SOURCE_CLASSES)
    for cohort_id, field_map in sorted(MACRO_FIELD_FEATURES.items()):
        for field_id, feature_id in sorted(field_map.items()):
            unit = MACRO_FIELD_UNITS[field_id]
            if cohort_id != inputs.cohort_id:
                features.append(_macro_not_applicable(feature_id, unit))
                continue
            value = inputs.release.fields.get(field_id)
            if value is None:
                raise CollectorRejected(
                    CollectorReason.REQUIRED_COMPONENT_MISSING,
                    f"release.{inputs.release.reference_period}.{field_id}",
                    "required macro component is missing from the official release",
                )
            features.append(
                FeatureValue(
                    feature_id=feature_id,
                    status=FeatureStatus.PRESENT,
                    value=value,
                    value_type=FeatureValueType.DECIMAL_STRING,
                    unit=unit,
                    observed_at=_proven_observed_at(
                        inputs.evidence, bls_refs, inputs.release.published_at
                    ),
                    source_refs=bls_refs,
                )
            )
    return features


def _build_revision_vector(inputs: MacroFeatureBuildInput) -> FeatureValue:
    bls_refs = source_refs_for(inputs.evidence, source_classes=MACRO_BLS_SOURCE_CLASSES)
    components = []
    seen: set[str] = set()
    for revision in sorted(
        inputs.revisions,
        key=lambda item: (item.revised_reference_period, item.field_id),
    ):
        component_id = f"{revision.revised_reference_period}.{revision.field_id}"
        if component_id in seen:
            raise CollectorRejected(
                CollectorReason.REVISION_FIELD_CONFLICTING,
                f"revisions.{component_id}",
                "conflicting revisions for one macro field",
            )
        seen.add(component_id)
        unit = MACRO_FIELD_UNITS.get(revision.field_id, "COUNT")
        with collector_context():
            delta = revision.revised_value - revision.initial_value
        components.append(
            FeatureComponent(
                component_id=component_id,
                status=FeatureStatus.PRESENT,
                value=delta,
                unit=unit,
                source_refs=bls_refs,
            )
        )
    if not components:
        raise CollectorRejected(
            CollectorReason.REVISION_FIELD_MISSING,
            "revisions",
            "official revision table is required for the macro candidate",
        )
    return FeatureValue(
        feature_id=FEATURE_MACRO_REVISION_VECTOR,
        status=FeatureStatus.PRESENT,
        value=None,
        value_type=FeatureValueType.DECIMAL_STRING_MAP,
        unit="DECIMAL_VECTOR",
        observed_at=_proven_observed_at(inputs.evidence, bls_refs, inputs.window_end_at),
        source_refs=bls_refs,
        components=tuple(sorted(components, key=lambda item: item.component_id)),
    )


def _build_spy_log_return(inputs: MacroFeatureBuildInput) -> FeatureValue:
    with collector_context():
        value = log_return(inputs.anchor_mid, inputs.end_mid)
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_QUOTE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_EVENT_LOG_RETURN,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="LOG_RETURN",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def _build_spy_zscore(inputs: MacroFeatureBuildInput, event_return: Decimal) -> FeatureValue:
    history = inputs.normalization_returns
    if len(history) < ZSCORE_MIN_SESSIONS:
        raise CollectorRejected(
            CollectorReason.INSUFFICIENT_NORMALIZATION_HISTORY,
            "normalization_history",
            f"at least {ZSCORE_MIN_SESSIONS} prior sessions are required",
        )
    median = _median_decimal(history)
    with collector_context():
        deviations = tuple(abs(item - median) for item in history)
        mad = _median_decimal(deviations)
        scale = max(ZSCORE_MAD_MULTIPLIER * mad, ZSCORE_SCALE_FLOOR)
        value = (event_return - median) / scale
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_QUOTE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_EVENT_ZSCORE,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="Z_SCORE",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def _build_spy_volume_ratio(inputs: MacroFeatureBuildInput) -> FeatureValue:
    prior = tuple(volume for volume in inputs.prior_window_volumes if volume > 0)
    if len(prior) < RELATIVE_VOLUME_MIN_MATCHING:
        raise CollectorRejected(
            CollectorReason.MARKET_WINDOW_MISSING,
            "spy_prior_window_volumes",
            f"at least {RELATIVE_VOLUME_MIN_MATCHING} prior window volumes are required",
        )
    with collector_context():
        value = Decimal(inputs.window_volume) / _median_decimal(
            tuple(Decimal(volume) for volume in prior)
        )
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_EVENT_VOLUME_RATIO,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="RATIO",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def _build_spy_vwap_distance(inputs: MacroFeatureBuildInput) -> FeatureValue:
    with collector_context():
        if inputs.window_vwap <= 0:
            raise CollectorRejected(
                CollectorReason.NON_FINITE_FEATURE,
                "spy_event_vwap_distance",
                "window VWAP must be positive",
            )
        value = (inputs.end_mid / inputs.window_vwap - 1) * 10000
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_EVENT_VWAP_DISTANCE,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="BASIS_POINTS",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def _build_spy_range(inputs: MacroFeatureBuildInput) -> FeatureValue:
    with collector_context():
        value = (inputs.window_high - inputs.window_low) / inputs.window_vwap * 10000
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_QUOTE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_EVENT_RANGE,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="BASIS_POINTS",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def _build_spy_reversal(inputs: MacroFeatureBuildInput, event_return: Decimal) -> FeatureValue:
    with collector_context():
        if event_return > 0:
            value = (inputs.end_mid - inputs.window_high) / inputs.window_high * 10000
        elif event_return < 0:
            value = (inputs.end_mid - inputs.window_low) / inputs.window_low * 10000
        else:
            value = Decimal(0)
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_QUOTE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_EVENT_REVERSAL,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="BASIS_POINTS",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def _build_spy_nbbo_spread(inputs: MacroFeatureBuildInput) -> FeatureValue:
    if not inputs.quote_entitlement_verified:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "spy_nbbo_spread",
            "verified SIP quote entitlement is required",
        )
    value = quote_spread_basis_points(
        inputs.spy_quotes,
        symbol=inputs.spy_symbol,
        window_end_at=inputs.window_end_at,
    )
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_QUOTE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_NBBO_SPREAD,
        status=FeatureStatus.PRESENT,
        value=value,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="BASIS_POINTS",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def _build_spy_quote_age(inputs: MacroFeatureBuildInput) -> FeatureValue:
    if not inputs.quote_entitlement_verified:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "spy_quote_age",
            "verified SIP quote entitlement is required",
        )
    eligible = tuple(
        quote
        for quote in inputs.spy_quotes
        if quote.symbol == inputs.spy_symbol and quote.observed_at <= inputs.window_end_at
    )
    if not eligible:
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_STALE,
            "spy_quote_age",
            "no SPY quote observation exists at or before the window end",
        )
    latest = max(eligible, key=lambda item: item.observed_at)
    age_ms = int((inputs.window_end_at - latest.observed_at).total_seconds() * 1000)
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_QUOTE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_QUOTE_AGE,
        status=FeatureStatus.PRESENT,
        value=age_ms,
        value_type=FeatureValueType.INTEGER,
        unit="MILLISECONDS",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def _build_spy_realized_volatility(inputs: MacroFeatureBuildInput) -> FeatureValue:
    returns = inputs.spy_daily_returns
    if len(returns) < VOLATILITY_SESSIONS:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "spy_realized_volatility",
            f"exactly {VOLATILITY_SESSIONS} prior daily returns are required",
        )
    recent = returns[-VOLATILITY_SESSIONS:]
    with collector_context():
        volatility = _sample_standard_deviation(recent) * decimal_sqrt(Decimal(252))
    source_refs = source_refs_for(inputs.evidence, source_classes=MACRO_SPY_TRADE_CLASSES)
    return FeatureValue(
        feature_id=FEATURE_SPY_REALIZED_VOL,
        status=FeatureStatus.PRESENT,
        value=volatility,
        value_type=FeatureValueType.DECIMAL_STRING,
        unit="ANNUALIZED_LOG_RETURN_VOLATILITY",
        observed_at=_proven_observed_at(inputs.evidence, source_refs, inputs.window_end_at),
        source_refs=source_refs,
    )


def build_macro_features(inputs: MacroFeatureBuildInput) -> tuple[FeatureValue, ...]:
    """Build the complete sorted macro-challenger feature set."""

    if inputs.cohort_id not in MACRO_FIELD_FEATURES:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            f"cohort.{inputs.cohort_id}",
            "macro cohort is not registered",
        )
    log_return_feature = _build_spy_log_return(inputs)
    assert isinstance(log_return_feature.value, Decimal)
    features = tuple(
        [
            *_build_macro_components(inputs),
            _macro_unavailable(FEATURE_MACRO_CONSENSUS_VECTOR, "DECIMAL_VECTOR"),
            _build_revision_vector(inputs),
            log_return_feature,
            _build_spy_zscore(inputs, log_return_feature.value),
            _build_spy_volume_ratio(inputs),
            _build_spy_vwap_distance(inputs),
            _build_spy_range(inputs),
            _build_spy_reversal(inputs, log_return_feature.value),
            _build_spy_nbbo_spread(inputs),
            _build_spy_quote_age(inputs),
            _build_spy_realized_volatility(inputs),
        ]
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
