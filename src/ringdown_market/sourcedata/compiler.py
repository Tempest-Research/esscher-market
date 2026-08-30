"""Strict point-in-time strategy snapshot compiler.

The compiler consumes exact frozen candidate bytes and read-only source
records, then emits canonical ``esscher.strategy_snapshot/v1`` and
``esscher.feature_receipt/v1`` artifacts bound to the accepted policy hash.
It never calls a model, a broker, an MCP tool, or the network, and it never
imputes a missing fact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from ringdown_market.sourcedata.adjustments import adjust_series
from ringdown_market.sourcedata.betas import (
    FrozenBeta,
    daily_log_returns,
    estimate_betas,
    select_beta_window,
)
from ringdown_market.sourcedata.decimal_math import log_return
from ringdown_market.sourcedata.evidence import EvidenceEntry, EvidencePacket, build_evidence_packet
from ringdown_market.sourcedata.features import (
    FeatureBuildInput,
    MacroFeatureBuildInput,
    build_earnings_features,
    build_macro_features,
)
from ringdown_market.sourcedata.interfaces import (
    CorporateAction,
    DailyBar,
    EvidenceSource,
    IssuerRelease,
    MacroReleaseSource,
    MarketDataSource,
    QuoteSample,
    SessionRecord,
    SourceProvenance,
    Trade,
)
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.receipts import (
    CorporateActionReceipt,
    SourceReceipt,
)
from ringdown_market.sourcedata.windows import (
    SynchronizedWindow,
    build_synchronized_window,
)
from ringdown_market.strategy.contracts import (
    build_strategy_input,
    canonical_json_bytes,
    feature_receipt_bytes,
    parse_candidate_manifest,
    sha256_bytes,
    strategy_snapshot_bytes,
)
from ringdown_market.strategy.models import (
    DataHealthState,
    EligibilityState,
    EventCategory,
    EvidenceRole,
    FeatureReceipt,
    ReleaseFamily,
    StrategyInput,
    StrategySnapshot,
    TimingBucket,
)
from ringdown_market.strategy.policy import StrategyPolicy, load_strategy_policy

EARNINGS_CANDIDATE: Final = "EARNINGS_RESIDUAL_CONTINUATION_V1"
MACRO_CANDIDATE: Final = "MACRO_SPY_CONTINUATION_CHALLENGER_V1"
MARKET_PROXY: Final = "SPY"
TRADING_TIMEZONE: Final = ZoneInfo("America/New_York")
_PRODUCER_LABEL: Final = {
    "producer": "esscher.sourcedata.snapshot_compiler",
    "contract": "esscher.strategy_snapshot",
    "version": 1,
}
PRODUCER_BUILD_SHA256: Final = sha256_bytes(canonical_json_bytes(_PRODUCER_LABEL))
MARKET_TRADE_CLASS: Final = "LICENSED_SIP_EQUITY_TRADES"
MARKET_QUOTE_CLASS: Final = "LICENSED_SIP_EQUITY_QUOTES"
SPY_TRADE_CLASS: Final = "LICENSED_SIP_SPY_TRADES"
SPY_QUOTE_CLASS: Final = "LICENSED_SIP_SPY_QUOTES"
BLS_CALENDAR_CLASS: Final = "OFFICIAL_BLS_RELEASE_CALENDAR"
BLS_RELEASE_CLASS: Final = "OFFICIAL_BLS_RELEASE"
BLS_REVISION_CLASS: Final = "OFFICIAL_BLS_REVISION_TABLE"
JOLTS_ANCHOR_LOOKBACK_MINUTES: Final = 5
NORMALIZATION_PRIOR_SESSIONS: Final = 60
MARKET_LIMITATIONS: Final = ("LICENSED_MARKET_DATA", "NO_REDISTRIBUTION")
RELATIVE_VOLUME_PRIOR_SESSIONS: Final = 20


@dataclass(frozen=True, slots=True)
class CaptureConfiguration:
    """Explicit host-supplied capture inputs; credentials are never part of it."""

    candidate_manifest_bytes: bytes
    event_id: str
    capture_at: datetime
    market_publisher: str
    market_entitlement: str
    market_redistribution: str
    retrieval_pages: Mapping[str, tuple[int, int]] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.capture_at.tzinfo != UTC:
            raise CollectorRejected(
                CollectorReason.UNSUPPORTED_INPUT,
                "capture_at",
                "capture clock must be UTC",
            )
        if not self.market_publisher:
            raise CollectorRejected(
                CollectorReason.UNSUPPORTED_INPUT,
                "market_publisher",
                "market publisher must be explicit",
            )
        if self.market_entitlement not in {"ENTITLED", "UNVERIFIED"}:
            raise CollectorRejected(
                CollectorReason.UNSUPPORTED_INPUT,
                "market_entitlement",
                "market entitlement must be ENTITLED or UNVERIFIED",
            )
        if self.market_redistribution not in {"REDISTRIBUTABLE", "NON_REDISTRIBUTABLE"}:
            raise CollectorRejected(
                CollectorReason.UNSUPPORTED_INPUT,
                "market_redistribution",
                "market redistribution status must be explicit",
            )
        for evidence_id, pages in self.retrieval_pages.items():
            if (
                not isinstance(pages, tuple)
                or len(pages) != 2
                or any(type(value) is not int or value < 1 for value in pages)
            ):
                raise CollectorRejected(
                    CollectorReason.UNSUPPORTED_INPUT,
                    f"retrieval_pages.{evidence_id}",
                    "pagination must be a pair of positive integers",
                )

    def pages_for(self, evidence_id: str) -> tuple[int, int]:
        return self.retrieval_pages.get(evidence_id, (1, 1))


@dataclass(frozen=True, slots=True)
class CaptureClocks:
    """Frozen policy clocks realized for one reaction session."""

    session: SessionRecord
    observation_window_start_at: datetime
    observation_window_end_at: datetime
    evidence_cutoff_at: datetime
    decision_cutoff_at: datetime
    candidate_entry_deadline_at: datetime


@dataclass(frozen=True, slots=True)
class CompiledSnapshot:
    """The complete deterministic capture output for one eligible event."""

    candidate_manifest_bytes: bytes
    strategy_snapshot_bytes: bytes
    feature_receipt_bytes: bytes
    snapshot: StrategySnapshot
    feature_receipt: FeatureReceipt
    evidence_packet: EvidencePacket
    betas: FrozenBeta | None
    window: SynchronizedWindow
    source_receipts: tuple[SourceReceipt, ...]
    action_receipts: tuple[CorporateActionReceipt, ...]


def _policy_clock(
    policy: StrategyPolicy, candidate_id: str, cohort_id: str
) -> Mapping[str, object]:
    candidate = policy.candidate(candidate_id)
    clocks = candidate["clocks"]
    matches = tuple(clock for clock in clocks if clock.get("cohort_id") == cohort_id)
    if len(matches) != 1:
        raise CollectorRejected(
            CollectorReason.CLOCK_MISMATCH,
            f"policy.clocks.{cohort_id}",
            "cohort must select exactly one registered clock",
        )
    return matches[0]


def _local_time_to_utc(session_date: date, wall_time: str) -> datetime:
    hour, minute, second = (int(part) for part in wall_time.split(":"))
    local = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        hour,
        minute,
        second,
        tzinfo=TRADING_TIMEZONE,
    )
    return local.astimezone(UTC)


def derive_clocks(
    policy: StrategyPolicy,
    *,
    cohort_id: str,
    reaction_session: SessionRecord,
    candidate_id: str = EARNINGS_CANDIDATE,
) -> CaptureClocks:
    """Realize the frozen cohort clocks for one full regular session."""

    if not reaction_session.full_regular:
        raise CollectorRejected(
            CollectorReason.CLOCK_MISMATCH,
            f"session.{reaction_session.session_id}",
            "reaction session must be a full regular session",
        )
    local_open = reaction_session.open_at.astimezone(TRADING_TIMEZONE)
    local_close = reaction_session.close_at.astimezone(TRADING_TIMEZONE)
    if (
        local_open.time().isoformat(timespec="seconds") != "09:30:00"
        or local_close.time().isoformat(timespec="seconds") != "16:00:00"
        or local_open.date() != local_close.date()
        or local_open.date() != reaction_session.session_date
    ):
        raise CollectorRejected(
            CollectorReason.CLOCK_MISMATCH,
            f"session.{reaction_session.session_id}",
            "reaction session must run 09:30-16:00 America/New_York",
        )
    clock = _policy_clock(policy, candidate_id, cohort_id)
    return CaptureClocks(
        session=reaction_session,
        observation_window_start_at=_local_time_to_utc(
            reaction_session.session_date, str(clock["observation_start"])
        ),
        observation_window_end_at=_local_time_to_utc(
            reaction_session.session_date, str(clock["observation_end"])
        ),
        evidence_cutoff_at=_local_time_to_utc(
            reaction_session.session_date, str(clock["evidence_cutoff"])
        ),
        decision_cutoff_at=_local_time_to_utc(
            reaction_session.session_date, str(clock["decision_cutoff"])
        ),
        candidate_entry_deadline_at=_local_time_to_utc(
            reaction_session.session_date, str(clock["candidate_entry_deadline"])
        ),
    )


def _reaction_session(
    evidence: EvidenceSource,
    *,
    cohort_id: str,
    scheduled_at: datetime,
    exchange_mic: str,
) -> SessionRecord:
    scheduled_local = scheduled_at.astimezone(TRADING_TIMEZONE)
    if cohort_id == "BMO":
        target = scheduled_local.date()
        sessions = evidence.sessions(exchange_mic, target, target)
    elif cohort_id == "AMC":
        start = scheduled_local.date()
        sessions = tuple(
            session
            for session in evidence.sessions(
                exchange_mic, start, date.fromordinal(start.toordinal() + 14)
            )
            if session.session_date > start
        )
    else:
        raise CollectorRejected(
            CollectorReason.TIMING_BUCKET_UNKNOWN,
            f"cohort.{cohort_id}",
            "only BMO and AMC cohorts are supported by this collector",
        )
    matches = tuple(session for session in sessions if session.full_regular)
    if not matches:
        raise CollectorRejected(
            CollectorReason.CLOCK_MISMATCH,
            "reaction_session",
            "no full regular reaction session exists on the calendar",
        )
    return sorted(matches, key=lambda item: item.session_date)[0]


def _sector_proxy(policy: StrategyPolicy, sector: str) -> str:
    candidate = policy.candidate(EARNINGS_CANDIDATE)
    universe = candidate["universe"]
    for rule in universe["rules"]:
        if rule["rule_id"] == "sector_proxy_by_point_in_time_gics":
            mapping = dict(str(item).split(":", 1) for item in rule["value"])
            proxy = mapping.get(sector)
            if proxy is None:
                raise CollectorRejected(
                    CollectorReason.UNSUPPORTED_INPUT,
                    f"sector.{sector}",
                    "unknown sector mapping is not guessed",
                )
            return str(proxy)
    raise CollectorRejected(
        CollectorReason.POLICY_HASH_MISMATCH,
        "policy.universe.sector_proxy",
        "sector proxy rule is absent from policy",
    )


def _market_provenance(
    configuration: CaptureConfiguration,
    *,
    source_class: str,
    content_sha256: str,
    observed_precision: str,
) -> SourceProvenance:
    return SourceProvenance(
        source_class=source_class,
        publisher=configuration.market_publisher,
        content_sha256=content_sha256,
        published_at=None,
        published_at_precision=observed_precision,
        retrieved_at=configuration.capture_at,
        entitlement=configuration.market_entitlement,
        redistribution_status=configuration.market_redistribution,
        limitations=MARKET_LIMITATIONS,
    )


def _trade_content_sha256(trades: Sequence[Trade]) -> str:
    payload = [
        {
            "symbol": trade.symbol,
            "session_id": trade.session_id,
            "observed_at": trade.observed_at.isoformat().replace("+00:00", "Z"),
            "price": str(trade.price),
            "size": trade.size,
            "sale_condition": trade.sale_condition,
        }
        for trade in sorted(trades, key=lambda item: (item.symbol, item.observed_at))
    ]
    return sha256_bytes(canonical_json_bytes(payload))


def _quote_content_sha256(quotes: Sequence[QuoteSample]) -> str:
    payload = [
        {
            "symbol": quote.symbol,
            "session_id": quote.session_id,
            "observed_at": quote.observed_at.isoformat().replace("+00:00", "Z"),
            "bid": str(quote.bid),
            "ask": str(quote.ask),
        }
        for quote in sorted(quotes, key=lambda item: (item.symbol, item.observed_at))
    ]
    return sha256_bytes(canonical_json_bytes(payload))


def _validate_universe(master, *, exchange_mic: str) -> None:
    if master.primary_exchange_mic != exchange_mic or master.primary_exchange_mic not in {
        "XNAS",
        "XNYS",
    }:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_PRIMARY_EXCHANGE,
            f"security.{master.ticker}",
            "primary exchange is not supported",
        )
    if master.security_type != "US_COMMON_STOCK":
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_SECURITY_TYPE,
            f"security.{master.ticker}",
            "security type is not supported",
        )
    if not master.active_at_freeze:
        raise CollectorRejected(
            CollectorReason.INACTIVE_SECURITY,
            f"security.{master.ticker}",
            "security must be active at freeze",
        )
    if master.prior_regular_close < Decimal("10.00"):
        raise CollectorRejected(
            CollectorReason.PRICE_BELOW_MINIMUM,
            f"security.{master.ticker}",
            "prior regular close is below the frozen floor",
        )
    if not master.listed_option_exists:
        raise CollectorRejected(
            CollectorReason.NOT_OPTIONABLE_AT_FREEZE,
            f"security.{master.ticker}",
            "no listed option exists at freeze",
        )


def _validate_release_timing(
    release: IssuerRelease,
    *,
    cohort_id: str,
    clocks: CaptureClocks,
    prior_session_close_at: datetime | None,
) -> None:
    published_at = release.provenance.published_at
    if published_at is None:
        raise CollectorRejected(
            CollectorReason.PUBLICATION_TIME_UNKNOWN,
            f"release.{release.event_id}",
            "primary release requires a publisher timestamp",
        )
    if published_at >= clocks.session.open_at:
        raise CollectorRejected(
            CollectorReason.PRIMARY_RELEASE_LATE,
            f"release.{release.event_id}",
            "results must be published before the reaction session open",
        )
    if cohort_id == "AMC" and (
        prior_session_close_at is None or published_at < prior_session_close_at
    ):
        raise CollectorRejected(
            CollectorReason.PRIMARY_RELEASE_LATE,
            f"release.{release.event_id}",
            "AMC results must be published after the prior session close",
        )
    if cohort_id == "BMO" and published_at.astimezone(TRADING_TIMEZONE).date() != (
        clocks.session.session_date
    ):
        raise CollectorRejected(
            CollectorReason.PRIMARY_RELEASE_LATE,
            f"release.{release.event_id}",
            "BMO results must be published on the reaction session date",
        )


def compile_strategy_snapshot(
    configuration: CaptureConfiguration,
    evidence: EvidenceSource,
    market: MarketDataSource,
) -> CompiledSnapshot:
    """Compile one deterministic snapshot bundle or fail closed."""

    policy = load_strategy_policy()
    manifest = parse_candidate_manifest(configuration.candidate_manifest_bytes)
    if manifest.policy_sha256 != policy.sha256:
        raise CollectorRejected(
            CollectorReason.POLICY_HASH_MISMATCH,
            "candidate_manifest.policy_sha256",
            "manifest does not bind the registered strategy policy",
        )
    if manifest.candidate_id != EARNINGS_CANDIDATE:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "candidate_manifest.candidate_id",
            "collector supports only the earnings residual candidate",
        )
    record = manifest.record(configuration.event_id)
    if record.eligibility is not EligibilityState.ELIGIBLE:
        raise CollectorRejected(
            CollectorReason.EVENT_NOT_CONFIRMED,
            f"candidate.{record.event_id}",
            "event is not eligible in the frozen manifest",
        )

    master = evidence.security_master(record.ticker, manifest.frozen_at)
    _validate_universe(master, exchange_mic="XNYS")
    sector_symbol = _sector_proxy(policy, master.sector)
    reaction_session = _reaction_session(
        evidence,
        cohort_id=record.cohort_id,
        scheduled_at=record.scheduled_at,
        exchange_mic=master.primary_exchange_mic,
    )
    clocks = derive_clocks(policy, cohort_id=record.cohort_id, reaction_session=reaction_session)
    if configuration.capture_at > clocks.decision_cutoff_at:
        raise CollectorRejected(
            CollectorReason.RETRIEVED_AFTER_CUTOFF,
            "capture_at",
            "capture clock exceeds the decision cutoff",
        )

    prior_sessions = tuple(
        session
        for session in evidence.sessions(
            master.primary_exchange_mic,
            date.fromordinal(reaction_session.session_date.toordinal() - 10),
            date.fromordinal(reaction_session.session_date.toordinal() - 1),
        )
        if session.full_regular and session.session_date < reaction_session.session_date
    )
    prior_session = (
        sorted(prior_sessions, key=lambda item: item.session_date)[-1] if (prior_sessions) else None
    )
    prior_eligible_session_close_at: datetime | None = None
    if record.cohort_id == "AMC":
        if prior_session is None:
            raise CollectorRejected(
                CollectorReason.PRIMARY_RELEASE_LATE,
                f"candidate.{record.event_id}",
                "AMC requires an eligible prior regular session before the reaction session",
            )
        scheduled_date = record.scheduled_at.astimezone(TRADING_TIMEZONE).date()
        if prior_session.close_at.astimezone(TRADING_TIMEZONE).date() != scheduled_date:
            raise CollectorRejected(
                CollectorReason.TIMING_BUCKET_CONFLICT,
                f"candidate.{record.event_id}",
                "AMC prior session close must share the scheduled ET date",
            )
        prior_eligible_session_close_at = prior_session.close_at

    release = evidence.issuer_release(record.event_id)
    if release is None:
        raise CollectorRejected(
            CollectorReason.PRIMARY_RELEASE_MISSING,
            f"release.{record.event_id}",
            "primary issuer release is missing",
        )
    _validate_release_timing(
        release,
        cohort_id=record.cohort_id,
        clocks=clocks,
        prior_session_close_at=prior_session.close_at if prior_session else None,
    )

    actions = tuple(
        evidence.corporate_actions(
            record.ticker,
            date.fromordinal(reaction_session.session_date.toordinal() - 400),
            reaction_session.session_date,
        )
    )
    symbol_changes = tuple(action for action in actions if action.action_type == "SYMBOL_CHANGE")
    if symbol_changes:
        raise CollectorRejected(
            CollectorReason.CORPORATE_ACTION_UNRESOLVED,
            f"corporate_action.{record.ticker}",
            "symbol changes are not resolved by this collector version",
        )
    action_receipts = tuple(
        CorporateActionReceipt.from_action(
            f"action-{record.ticker.lower()}-{action.ex_date.isoformat()}-{action.action_type.lower()}",
            action,
            source_receipt_id=f"source-actions-{record.ticker.lower()}",
        )
        for action in sorted(actions, key=lambda item: (item.ex_date, item.action_type))
        if action.action_type == "SPLIT"
    )

    beta_window_dates = select_beta_window(
        tuple(
            session.session_date
            for session in evidence.sessions(
                master.primary_exchange_mic,
                date.fromordinal(reaction_session.session_date.toordinal() - 400),
                reaction_session.session_date,
            )
            if session.full_regular
        ),
        reaction_session.session_date,
    )
    beta_start = beta_window_dates[0]

    def adjusted(symbol: str) -> tuple[tuple[DailyBar, ...], tuple[CorporateAction, ...]]:
        bars = tuple(
            market.daily_bars(
                symbol,
                date.fromordinal(beta_start.toordinal() - 7),
                date.fromordinal(reaction_session.session_date.toordinal() - 1),
            )
        )
        symbol_actions = tuple(
            evidence.corporate_actions(symbol, beta_start, reaction_session.session_date)
        )
        return bars, symbol_actions

    stock_bars, stock_actions = adjusted(record.ticker)
    market_bars, market_actions = adjusted(MARKET_PROXY)
    sector_bars, sector_actions = adjusted(sector_symbol)
    receipts_by_action: dict[CorporateAction, CorporateActionReceipt] = {
        action: receipt
        for action in stock_actions
        for receipt in action_receipts
        if action.ex_date == receipt.ex_date and action.action_type == receipt.action_type
    }
    stock_series = adjust_series(
        stock_bars, stock_actions, ticker=record.ticker, receipts_by_action=receipts_by_action
    )
    market_series = adjust_series(
        market_bars, market_actions, ticker=MARKET_PROXY, receipts_by_action={}
    )
    sector_series = adjust_series(
        sector_bars, sector_actions, ticker=sector_symbol, receipts_by_action={}
    )
    betas = estimate_betas(
        stock_series,
        market_series,
        sector_series,
        session_dates=tuple(
            session.session_date
            for session in evidence.sessions(
                master.primary_exchange_mic, beta_start, reaction_session.session_date
            )
            if session.full_regular
        ),
        reaction_session_date=reaction_session.session_date,
    )

    symbols = (record.ticker, MARKET_PROXY, sector_symbol)
    trades_by_symbol = {
        symbol: tuple(market.window_trades(symbol, reaction_session.session_id))
        for symbol in symbols
    }
    quotes_by_symbol = {
        symbol: tuple(market.window_quotes(symbol, reaction_session.session_id))
        for symbol in symbols
    }
    quote_entitlement_verified = configuration.market_entitlement == "ENTITLED"
    window = build_synchronized_window(
        trades_by_symbol,
        quotes_by_symbol,
        session_id=reaction_session.session_id,
        symbols=symbols,
        window_start_at=clocks.observation_window_start_at,
        window_end_at=clocks.observation_window_end_at,
        require_quotes=quote_entitlement_verified,
    )

    prior_window_volumes: dict[str, int | None] = {}
    prior_session_ids = tuple(
        session.session_id
        for session in sorted(
            (
                session
                for session in evidence.sessions(
                    master.primary_exchange_mic,
                    date.fromordinal(reaction_session.session_date.toordinal() - 40),
                    date.fromordinal(reaction_session.session_date.toordinal() - 1),
                )
                if session.full_regular
            ),
            key=lambda item: item.session_date,
        )
    )[-RELATIVE_VOLUME_PRIOR_SESSIONS:]
    for session_id in prior_session_ids:
        prior_trades = tuple(market.window_trades(record.ticker, session_id))
        eligible = tuple(
            trade for trade in prior_trades if trade.sale_condition == "REGULAR_CONTINUOUS"
        )
        prior_window_volumes[session_id] = (
            sum(trade.size for trade in eligible) if eligible else None
        )

    reaction_trades = tuple(trade for trades in trades_by_symbol.values() for trade in trades)
    reaction_quotes = tuple(quote for quotes in quotes_by_symbol.values() for quote in quotes)
    entries = [
        EvidenceEntry(
            evidence_id="calendar",
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            receipt=SourceReceipt.from_provenance("source-calendar", reaction_session.provenance),
            pages_retrieved=configuration.pages_for("calendar")[0],
            pages_total=configuration.pages_for("calendar")[1],
        ),
        EvidenceEntry(
            evidence_id="security-master",
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            receipt=SourceReceipt.from_provenance("source-security-master", master.provenance),
            pages_retrieved=configuration.pages_for("security-master")[0],
            pages_total=configuration.pages_for("security-master")[1],
        ),
        EvidenceEntry(
            evidence_id="earnings-release",
            role=EvidenceRole.ISSUER_PRIMARY,
            receipt=SourceReceipt.from_provenance(
                f"source-release-{record.event_id.lower()}", release.provenance
            ),
            pages_retrieved=configuration.pages_for("earnings-release")[0],
            pages_total=configuration.pages_for("earnings-release")[1],
        ),
        EvidenceEntry(
            evidence_id="corporate-actions",
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            receipt=SourceReceipt.from_provenance(
                f"source-actions-{record.ticker.lower()}",
                _actions_provenance(actions, configuration),
            ),
            pages_retrieved=configuration.pages_for("corporate-actions")[0],
            pages_total=configuration.pages_for("corporate-actions")[1],
        ),
        EvidenceEntry(
            evidence_id="market-trades",
            role=EvidenceRole.ISSUER_MARKET,
            receipt=SourceReceipt.from_provenance(
                "source-market-trades",
                _market_provenance(
                    configuration,
                    source_class=MARKET_TRADE_CLASS,
                    content_sha256=_trade_content_sha256(reaction_trades),
                    observed_precision="SECOND",
                ),
            ),
            pages_retrieved=configuration.pages_for("market-trades")[0],
            pages_total=configuration.pages_for("market-trades")[1],
        ),
        EvidenceEntry(
            evidence_id="market-quotes",
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            receipt=SourceReceipt.from_provenance(
                "source-market-quotes",
                _market_provenance(
                    configuration,
                    source_class=MARKET_QUOTE_CLASS,
                    content_sha256=_quote_content_sha256(reaction_quotes),
                    observed_precision="MILLISECOND",
                ),
            ),
            pages_retrieved=configuration.pages_for("market-quotes")[0],
            pages_total=configuration.pages_for("market-quotes")[1],
        ),
    ]
    candidate_policy = policy.candidate(EARNINGS_CANDIDATE)
    evidence_policy = candidate_policy["evidence"]
    packet = build_evidence_packet(
        entries,
        evidence_cutoff_at=clocks.evidence_cutoff_at,
        permitted_source_classes=tuple(evidence_policy["permitted_source_classes"]),
        required_source_classes=tuple(evidence_policy["required_source_classes"]),
    )

    feature_inputs = FeatureBuildInput(
        release=release,
        window=window,
        beta=betas,
        ticker_symbol=record.ticker,
        market_symbol=MARKET_PROXY,
        sector_symbol=sector_symbol,
        stock_returns=daily_log_returns(stock_series),
        market_returns=daily_log_returns(market_series),
        sector_returns=daily_log_returns(sector_series),
        reaction_session_date=reaction_session.session_date,
        prior_closes={
            series.symbol: series.series[-1].adjusted_close
            for series in (stock_series, market_series, sector_series)
        },
        window_volumes_by_session=prior_window_volumes,
        quotes_by_symbol=quotes_by_symbol,
        quote_entitlement_verified=quote_entitlement_verified,
        evidence=packet,
    )
    features = build_earnings_features(feature_inputs)

    reasoner = policy.data["reasoner"]
    tolerated = tuple(sorted(reasoner["tolerated_unknown_codes"]))
    critical = tuple(sorted(reasoner["critical_unknown_codes"]))
    timing_bucket = (
        TimingBucket.BEFORE_OPEN if record.cohort_id == "BMO" else TimingBucket.AFTER_CLOSE
    )
    assert release.provenance.published_at is not None
    snapshot = StrategySnapshot(
        event_id=record.event_id,
        candidate_id=manifest.candidate_id,
        cohort_id=record.cohort_id,
        event_category=EventCategory.SCHEDULED_EARNINGS,
        issuer=record.issuer,
        security_id=record.security_id,
        ticker=record.ticker,
        policy_sha256=policy.sha256,
        candidate_manifest_sha256=sha256_bytes(configuration.candidate_manifest_bytes),
        producer_build_sha256=PRODUCER_BUILD_SHA256,
        created_at=configuration.capture_at,
        universe_frozen_at=manifest.frozen_at,
        timing_bucket=timing_bucket,
        release_family=None,
        event_published_at=release.provenance.published_at,
        prior_eligible_session_close_at=prior_eligible_session_close_at,
        reaction_session_id=reaction_session.session_id,
        reaction_session_open_at=reaction_session.open_at,
        reaction_session_close_at=reaction_session.close_at,
        observation_window_start_at=clocks.observation_window_start_at,
        observation_window_end_at=clocks.observation_window_end_at,
        evidence_cutoff_at=clocks.evidence_cutoff_at,
        decision_cutoff_at=clocks.decision_cutoff_at,
        candidate_entry_deadline_at=clocks.candidate_entry_deadline_at,
        evidence_packet_sha256=packet.packet_sha256,
        evidence_refs=packet.refs,
        eligibility=EligibilityState.ELIGIBLE,
        eligibility_reason_codes=(),
        data_health=DataHealthState.VALID,
        health_reason_codes=(),
        allowed_unknown_codes=tuple(sorted((*tolerated, *critical))),
        critical_unknown_codes=critical,
    )
    snapshot_bytes = strategy_snapshot_bytes(snapshot)
    receipt = FeatureReceipt(
        event_id=record.event_id,
        candidate_id=manifest.candidate_id,
        cohort_id=record.cohort_id,
        policy_sha256=policy.sha256,
        strategy_snapshot_sha256=sha256_bytes(snapshot_bytes),
        producer_build_sha256=PRODUCER_BUILD_SHA256,
        created_at=configuration.capture_at,
        feature_snapshot_at=clocks.observation_window_end_at,
        features=features,
    )
    receipt_bytes = feature_receipt_bytes(receipt)
    return CompiledSnapshot(
        candidate_manifest_bytes=configuration.candidate_manifest_bytes,
        strategy_snapshot_bytes=snapshot_bytes,
        feature_receipt_bytes=receipt_bytes,
        snapshot=snapshot,
        feature_receipt=receipt,
        evidence_packet=packet,
        betas=betas,
        window=window,
        source_receipts=packet.receipts,
        action_receipts=action_receipts,
    )


def compiled_strategy_input(compiled: CompiledSnapshot) -> StrategyInput:
    """Join the compiled artifacts through the frozen #26 contract."""

    return build_strategy_input(
        compiled.strategy_snapshot_bytes,
        candidate_manifest_bytes=compiled.candidate_manifest_bytes,
        feature_receipt_bytes=compiled.feature_receipt_bytes,
    )


def _worst_entitlement(values: Sequence[str]) -> str:
    if "UNVERIFIED" in values:
        return "UNVERIFIED"
    if "PUBLIC" in values:
        return "PUBLIC"
    return "ENTITLED"


def _worst_redistribution(values: Sequence[str]) -> str:
    if "UNKNOWN" in values:
        return "UNKNOWN"
    if "NON_REDISTRIBUTABLE" in values:
        return "NON_REDISTRIBUTABLE"
    return "REDISTRIBUTABLE"


def _actions_provenance(
    actions: Sequence[CorporateAction], configuration: CaptureConfiguration
) -> SourceProvenance:
    payload = [
        {
            "ticker": action.ticker,
            "action_type": action.action_type,
            "ex_date": action.ex_date.isoformat(),
            "content_sha256": action.provenance.content_sha256,
        }
        for action in sorted(actions, key=lambda item: (item.ex_date, item.action_type))
    ]
    publishers = {action.provenance.publisher for action in actions}
    if len(publishers) > 1:
        raise CollectorRejected(
            CollectorReason.MATERIAL_SOURCE_CONFLICT,
            "corporate_actions.publisher",
            "corporate action records name conflicting publishers",
        )
    retrieved_at = max(
        (action.provenance.retrieved_at for action in actions),
        default=configuration.capture_at,
    )
    return SourceProvenance(
        source_class="CORPORATE_ACTION_RECORD",
        publisher=next(iter(publishers)) if publishers else configuration.market_publisher,
        content_sha256=sha256_bytes(canonical_json_bytes(payload)),
        published_at=None,
        published_at_precision="DATE",
        retrieved_at=retrieved_at,
        entitlement=_worst_entitlement(
            [action.provenance.entitlement for action in actions] or ["ENTITLED"]
        ),
        redistribution_status=_worst_redistribution(
            [action.provenance.redistribution_status for action in actions]
            or ["NON_REDISTRIBUTABLE"]
        ),
        limitations=("CORPORATE_ACTION_RECORDS",),
    )


def _macro_reference_period(schedule, scheduled_at: datetime, cohort_id: str) -> str:
    matches = tuple(
        entry
        for entry in schedule
        if entry.release_family == cohort_id and entry.scheduled_at == scheduled_at
    )
    if not matches:
        raise CollectorRejected(
            CollectorReason.SCHEDULE_NOT_FROZEN,
            f"schedule.{cohort_id}",
            "no frozen official schedule entry matches this event",
        )
    if len(matches) > 1:
        raise CollectorRejected(
            CollectorReason.SCHEDULE_NOT_FROZEN,
            f"schedule.{cohort_id}",
            "conflicting frozen schedule entries match this event",
        )
    return matches[0].reference_period


def _macro_release_session(
    evidence_sessions, *, scheduled_at: datetime, exchange_mic: str
) -> SessionRecord:
    release_date = scheduled_at.astimezone(TRADING_TIMEZONE).date()
    matches = tuple(
        session
        for session in evidence_sessions(exchange_mic, release_date, release_date)
        if session.session_date == release_date
    )
    if not matches:
        raise CollectorRejected(
            CollectorReason.NON_FULL_REGULAR_SESSION,
            f"session.{release_date.isoformat()}",
            "no session exists on the release date",
        )
    session = matches[0]
    if not session.full_regular:
        raise CollectorRejected(
            CollectorReason.NON_FULL_REGULAR_SESSION,
            f"session.{session.session_id}",
            "macro reaction session must be a full regular session",
        )
    return session


def _macro_window_quotes(
    quotes: Sequence[QuoteSample],
    *,
    symbol: str,
    window_start_at: datetime,
    window_end_at: datetime,
) -> tuple[QuoteSample, ...]:
    return tuple(
        quote
        for quote in quotes
        if quote.symbol == symbol and window_start_at <= quote.observed_at <= window_end_at
    )


def _midpoint(quote: QuoteSample) -> Decimal:
    return (quote.bid + quote.ask) / 2


def _macro_anchor_mid(
    quotes: Sequence[QuoteSample],
    *,
    symbol: str,
    cohort_id: str,
    window_start_at: datetime,
    window_end_at: datetime,
) -> Decimal:
    if cohort_id == "BLS_JOLTS":
        anchor_start = window_start_at - timedelta(minutes=JOLTS_ANCHOR_LOOKBACK_MINUTES)
        anchor_quotes = tuple(
            quote
            for quote in quotes
            if quote.symbol == symbol and anchor_start <= quote.observed_at < window_start_at
        )
        if not anchor_quotes:
            raise CollectorRejected(
                CollectorReason.MARKET_WINDOW_MISSING,
                "spy.anchor",
                "no SPY anchor quote exists in the pre-release anchor window",
            )
        mids = tuple(_midpoint(quote) for quote in anchor_quotes)
        ordered = sorted(mids)
        count = len(ordered)
        if count % 2 == 1:
            return ordered[count // 2]
        return (ordered[count // 2 - 1] + ordered[count // 2]) / 2
    window_quotes = _macro_window_quotes(
        quotes, symbol=symbol, window_start_at=window_start_at, window_end_at=window_end_at
    )
    if not window_quotes:
        raise CollectorRejected(
            CollectorReason.MARKET_WINDOW_MISSING,
            "spy.anchor",
            "no SPY window quote exists at or after the window start",
        )
    return _midpoint(min(window_quotes, key=lambda quote: quote.observed_at))


def _revisions_provenance(revisions, configuration) -> SourceProvenance:
    payload = [
        {
            "release_family": revision.release_family,
            "revised_reference_period": revision.revised_reference_period,
            "field_id": revision.field_id,
            "initial_value": str(revision.initial_value),
            "revised_value": str(revision.revised_value),
            "content_sha256": revision.provenance.content_sha256,
        }
        for revision in sorted(
            revisions,
            key=lambda item: (item.revised_reference_period, item.field_id),
        )
    ]
    publishers = {revision.provenance.publisher for revision in revisions}
    if len(publishers) > 1:
        raise CollectorRejected(
            CollectorReason.REVISION_FIELD_CONFLICTING,
            "revisions.publisher",
            "revision records name conflicting publishers",
        )
    retrieved_at = max(
        (revision.provenance.retrieved_at for revision in revisions),
        default=configuration.capture_at,
    )
    published_at = max((revision.published_at for revision in revisions), default=None)
    first_release_family = revisions[0].release_family if revisions else "BLS_JOLTS"
    return SourceProvenance(
        source_class=BLS_REVISION_CLASS,
        publisher=next(iter(publishers)) if publishers else "SYNTHETIC_BLS_PUBLIC_RELEASE",
        content_sha256=sha256_bytes(canonical_json_bytes(payload)),
        published_at=published_at,
        published_at_precision="SECOND" if published_at is not None else "UNKNOWN",
        retrieved_at=retrieved_at,
        entitlement=_worst_entitlement(
            [revision.provenance.entitlement for revision in revisions] or ["PUBLIC"]
        ),
        redistribution_status=_worst_redistribution(
            [revision.provenance.redistribution_status for revision in revisions]
            or ["REDISTRIBUTABLE"]
        ),
        limitations=(f"{first_release_family}_REVISIONS",),
    )


def compile_macro_snapshot(
    configuration: CaptureConfiguration,
    evidence_sessions,
    macro: MacroReleaseSource,
    market: MarketDataSource,
) -> CompiledSnapshot:
    """Compile one deterministic macro-challenger snapshot bundle or fail closed."""

    policy = load_strategy_policy()
    manifest = parse_candidate_manifest(configuration.candidate_manifest_bytes)
    if manifest.policy_sha256 != policy.sha256:
        raise CollectorRejected(
            CollectorReason.POLICY_HASH_MISMATCH,
            "candidate_manifest.policy_sha256",
            "manifest does not bind the registered strategy policy",
        )
    if manifest.candidate_id != MACRO_CANDIDATE:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "candidate_manifest.candidate_id",
            "macro compiler supports only the macro challenger candidate",
        )
    record = manifest.record(configuration.event_id)
    if record.eligibility is not EligibilityState.ELIGIBLE:
        raise CollectorRejected(
            CollectorReason.EVENT_NOT_CONFIRMED,
            f"candidate.{record.event_id}",
            "event is not eligible in the frozen manifest",
        )
    cohort_id = record.cohort_id
    if cohort_id not in {"BLS_JOLTS", "BLS_EMPLOYMENT_SITUATION"}:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            f"cohort.{cohort_id}",
            "macro cohort is not registered",
        )
    release_family = ReleaseFamily(cohort_id)

    schedule = macro.release_schedule(cohort_id)
    reference_period = _macro_reference_period(schedule, record.scheduled_at, cohort_id)
    reaction_session = _macro_release_session(
        evidence_sessions,
        scheduled_at=record.scheduled_at,
        exchange_mic="XNYS",
    )
    clocks = derive_clocks(
        policy,
        cohort_id=cohort_id,
        reaction_session=reaction_session,
        candidate_id=MACRO_CANDIDATE,
    )
    if configuration.capture_at > clocks.decision_cutoff_at:
        raise CollectorRejected(
            CollectorReason.RETRIEVED_AFTER_CUTOFF,
            "capture_at",
            "capture clock exceeds the decision cutoff",
        )

    release = macro.release(cohort_id, reference_period, 1)
    if release is None:
        raise CollectorRejected(
            CollectorReason.OFFICIAL_RELEASE_MISSING,
            f"release.{cohort_id}.{reference_period}",
            "the first vintage of the official release is missing",
        )
    if release.published_at > clocks.evidence_cutoff_at:
        raise CollectorRejected(
            CollectorReason.OFFICIAL_RELEASE_LATE,
            f"release.{cohort_id}.{reference_period}",
            "official release publication exceeds the evidence cutoff",
        )
    revisions = tuple(macro.revisions(cohort_id, clocks.evidence_cutoff_at))

    window_quotes = market.window_quotes(MARKET_PROXY, reaction_session.session_id)
    window_trades = market.window_trades(MARKET_PROXY, reaction_session.session_id)
    window = build_synchronized_window(
        {MARKET_PROXY: window_trades},
        {MARKET_PROXY: window_quotes},
        session_id=reaction_session.session_id,
        symbols=(MARKET_PROXY,),
        window_start_at=clocks.observation_window_start_at,
        window_end_at=clocks.observation_window_end_at,
        require_quotes=True,
    )
    in_window_quotes = _macro_window_quotes(
        window_quotes,
        symbol=MARKET_PROXY,
        window_start_at=clocks.observation_window_start_at,
        window_end_at=clocks.observation_window_end_at,
    )
    if not in_window_quotes:
        raise CollectorRejected(
            CollectorReason.MARKET_WINDOW_MISSING,
            "spy.window",
            "no SPY quote exists inside the observation window",
        )
    anchor_mid = _macro_anchor_mid(
        window_quotes,
        symbol=MARKET_PROXY,
        cohort_id=cohort_id,
        window_start_at=clocks.observation_window_start_at,
        window_end_at=clocks.observation_window_end_at,
    )
    end_mid = _midpoint(max(in_window_quotes, key=lambda quote: quote.observed_at))
    mids = tuple(_midpoint(quote) for quote in in_window_quotes)
    window_high = max(mids)
    window_low = min(mids)
    spy_window = window.symbol(MARKET_PROXY)

    prior_sessions = tuple(
        session
        for session in evidence_sessions(
            "XNYS",
            date.fromordinal(reaction_session.session_date.toordinal() - 120),
            date.fromordinal(reaction_session.session_date.toordinal() - 1),
        )
        if session.full_regular and session.session_date < reaction_session.session_date
    )
    ordered_prior = sorted(prior_sessions, key=lambda item: item.session_date)
    normalization_sessions = ordered_prior[-NORMALIZATION_PRIOR_SESSIONS:]
    normalization_returns: list[Decimal] = []
    for session in normalization_sessions:
        session_trades = tuple(
            trade
            for trade in market.window_trades(MARKET_PROXY, session.session_id)
            if trade.sale_condition == "REGULAR_CONTINUOUS"
            and clocks.observation_window_start_at.replace(
                year=session.session_date.year,
                month=session.session_date.month,
                day=session.session_date.day,
            )
            <= trade.observed_at
            <= clocks.observation_window_end_at.replace(
                year=session.session_date.year,
                month=session.session_date.month,
                day=session.session_date.day,
            )
        )
        if len(session_trades) < 2:
            continue
        ordered_trades = sorted(session_trades, key=lambda trade: trade.observed_at)
        normalization_returns.append(log_return(ordered_trades[0].price, ordered_trades[-1].price))
    volume_sessions = ordered_prior[-RELATIVE_VOLUME_PRIOR_SESSIONS:]
    prior_window_volumes: list[int] = []
    for session in volume_sessions:
        session_trades = tuple(
            trade
            for trade in market.window_trades(MARKET_PROXY, session.session_id)
            if trade.sale_condition == "REGULAR_CONTINUOUS"
        )
        if session_trades:
            prior_window_volumes.append(sum(trade.size for trade in session_trades))

    spy_bars = tuple(
        market.daily_bars(
            MARKET_PROXY,
            date.fromordinal(reaction_session.session_date.toordinal() - 40),
            date.fromordinal(reaction_session.session_date.toordinal() - 1),
        )
    )
    spy_series = adjust_series(spy_bars, (), ticker=MARKET_PROXY, receipts_by_action={})
    spy_returns = daily_log_returns(spy_series)
    spy_daily_returns = tuple(
        spy_returns[session.session_date]
        for session in ordered_prior
        if session.session_date in spy_returns
    )

    entries = [
        EvidenceEntry(
            evidence_id="bls-calendar",
            role=EvidenceRole.MACRO_PRIMARY,
            receipt=SourceReceipt.from_provenance(
                "source-bls-calendar",
                _macro_reference_provenance(schedule, cohort_id, record.scheduled_at),
            ),
        ),
        EvidenceEntry(
            evidence_id="bls-release",
            role=EvidenceRole.MACRO_PRIMARY,
            receipt=SourceReceipt.from_provenance(
                f"source-bls-release-{reference_period.lower()}", release.provenance
            ),
        ),
        EvidenceEntry(
            evidence_id="bls-revision-table",
            role=EvidenceRole.MACRO_PRIMARY,
            receipt=SourceReceipt.from_provenance(
                "source-bls-revision-table",
                _revisions_provenance(revisions, configuration),
            ),
        ),
        EvidenceEntry(
            evidence_id="spy-trades",
            role=EvidenceRole.MARKET_PROXY,
            receipt=SourceReceipt.from_provenance(
                "source-spy-trades",
                _market_provenance(
                    configuration,
                    source_class=SPY_TRADE_CLASS,
                    content_sha256=_trade_content_sha256(tuple(window_trades)),
                    observed_precision="SECOND",
                ),
            ),
        ),
        EvidenceEntry(
            evidence_id="spy-quotes",
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            receipt=SourceReceipt.from_provenance(
                "source-spy-quotes",
                _market_provenance(
                    configuration,
                    source_class=SPY_QUOTE_CLASS,
                    content_sha256=_quote_content_sha256(tuple(window_quotes)),
                    observed_precision="MILLISECOND",
                ),
            ),
        ),
    ]
    candidate_policy = policy.candidate(MACRO_CANDIDATE)
    evidence_policy = candidate_policy["evidence"]
    packet = build_evidence_packet(
        entries,
        evidence_cutoff_at=clocks.evidence_cutoff_at,
        permitted_source_classes=tuple(evidence_policy["permitted_source_classes"]),
        required_source_classes=tuple(evidence_policy["required_source_classes"]),
    )

    feature_inputs = MacroFeatureBuildInput(
        release=release,
        revisions=revisions,
        cohort_id=cohort_id,
        anchor_mid=anchor_mid,
        end_mid=end_mid,
        window_vwap=spy_window.window_vwap,
        window_high=window_high,
        window_low=window_low,
        window_volume=spy_window.window_volume,
        prior_window_volumes=tuple(prior_window_volumes),
        normalization_returns=tuple(normalization_returns),
        spy_daily_returns=spy_daily_returns,
        spy_quotes=tuple(window_quotes),
        spy_symbol=MARKET_PROXY,
        window_end_at=clocks.observation_window_end_at,
        quote_entitlement_verified=configuration.market_entitlement == "ENTITLED",
        evidence=packet,
    )
    features = build_macro_features(feature_inputs)

    reasoner = policy.data["reasoner"]
    tolerated = tuple(sorted(reasoner["tolerated_unknown_codes"]))
    critical = tuple(sorted(reasoner["critical_unknown_codes"]))
    snapshot = StrategySnapshot(
        event_id=record.event_id,
        candidate_id=manifest.candidate_id,
        cohort_id=cohort_id,
        event_category=EventCategory.SCHEDULED_MACRO_RELEASE,
        issuer=record.issuer,
        security_id=record.security_id,
        ticker=record.ticker,
        policy_sha256=policy.sha256,
        candidate_manifest_sha256=sha256_bytes(configuration.candidate_manifest_bytes),
        producer_build_sha256=PRODUCER_BUILD_SHA256,
        created_at=configuration.capture_at,
        universe_frozen_at=manifest.frozen_at,
        timing_bucket=TimingBucket.SCHEDULED_RELEASE,
        release_family=release_family,
        event_published_at=release.published_at,
        reaction_session_id=reaction_session.session_id,
        reaction_session_open_at=reaction_session.open_at,
        reaction_session_close_at=reaction_session.close_at,
        observation_window_start_at=clocks.observation_window_start_at,
        observation_window_end_at=clocks.observation_window_end_at,
        evidence_cutoff_at=clocks.evidence_cutoff_at,
        decision_cutoff_at=clocks.decision_cutoff_at,
        candidate_entry_deadline_at=clocks.candidate_entry_deadline_at,
        evidence_packet_sha256=packet.packet_sha256,
        evidence_refs=packet.refs,
        eligibility=EligibilityState.ELIGIBLE,
        eligibility_reason_codes=(),
        data_health=DataHealthState.VALID,
        health_reason_codes=(),
        allowed_unknown_codes=tuple(sorted((*tolerated, *critical))),
        critical_unknown_codes=critical,
    )
    snapshot_bytes = strategy_snapshot_bytes(snapshot)
    receipt = FeatureReceipt(
        event_id=record.event_id,
        candidate_id=manifest.candidate_id,
        cohort_id=cohort_id,
        policy_sha256=policy.sha256,
        strategy_snapshot_sha256=sha256_bytes(snapshot_bytes),
        producer_build_sha256=PRODUCER_BUILD_SHA256,
        created_at=configuration.capture_at,
        feature_snapshot_at=clocks.observation_window_end_at,
        features=features,
    )
    receipt_bytes = feature_receipt_bytes(receipt)
    return CompiledSnapshot(
        candidate_manifest_bytes=configuration.candidate_manifest_bytes,
        strategy_snapshot_bytes=snapshot_bytes,
        feature_receipt_bytes=receipt_bytes,
        snapshot=snapshot,
        feature_receipt=receipt,
        evidence_packet=packet,
        betas=None,
        window=window,
        source_receipts=packet.receipts,
        action_receipts=(),
    )


def _macro_reference_provenance(schedule, cohort_id: str, scheduled_at: datetime):
    matches = tuple(
        entry
        for entry in schedule
        if entry.release_family == cohort_id and entry.scheduled_at == scheduled_at
    )
    return matches[0].provenance
