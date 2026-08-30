"""Synchronized observation windows with raw timestamps preserved.

The frozen data-health timing rules are enforced exactly: the start
observation may be at most fifteen seconds late, the end observation at most
fifteen seconds old, cross-instrument endpoints at most five seconds apart,
and quote samples at most one thousand milliseconds old. Forward filling is
forbidden, so gaps fail closed as missing observations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ringdown_market.sourcedata.decimal_math import collector_context
from ringdown_market.sourcedata.interfaces import QuoteSample, Trade
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected

START_DELAY_MAX_SECONDS = 15
END_AGE_MAX_SECONDS = 15
ENDPOINT_SKEW_MAX_SECONDS = 5
QUOTE_AGE_MAX_MILLISECONDS = 1000
CONTINUOUS_SALE_CONDITION = "REGULAR_CONTINUOUS"
QUOTE_SPREAD_SAMPLE_SECONDS = 60
QUOTE_SPREAD_MIN_SAMPLES = 30


@dataclass(frozen=True, slots=True)
class SymbolWindow:
    """One symbol's synchronized opening-window observations."""

    symbol: str
    first_observed_at: datetime
    first_price: Decimal
    last_observed_at: datetime
    last_price: Decimal
    window_vwap: Decimal
    window_volume: int
    trade_count: int


@dataclass(frozen=True, slots=True)
class SynchronizedWindow:
    """Aligned opening-window observations for every required symbol."""

    session_id: str
    window_start_at: datetime
    window_end_at: datetime
    symbols: tuple[SymbolWindow, ...]
    quote_age_ms: Mapping[str, int]

    def symbol(self, symbol: str) -> SymbolWindow:
        matches = tuple(item for item in self.symbols if item.symbol == symbol)
        if len(matches) != 1:
            raise CollectorRejected(
                CollectorReason.MARKET_OBSERVATION_MISSING,
                f"window.{symbol}",
                "symbol is absent from the synchronized window",
            )
        return matches[0]


def _eligible_trades(
    trades: Sequence[Trade],
    *,
    symbol: str,
    window_start_at: datetime,
    window_end_at: datetime,
) -> tuple[Trade, ...]:
    eligible: list[Trade] = []
    seen: set[datetime] = set()
    for trade in trades:
        if trade.symbol != symbol:
            continue
        if trade.observed_at < window_start_at or trade.observed_at > window_end_at:
            continue
        if trade.sale_condition != CONTINUOUS_SALE_CONDITION:
            continue
        if trade.observed_at in seen:
            raise CollectorRejected(
                CollectorReason.DUPLICATE_OBSERVATION,
                f"window.{symbol}.{trade.observed_at.isoformat()}",
                "duplicate continuous observation at one timestamp",
            )
        seen.add(trade.observed_at)
        eligible.append(trade)
    return tuple(sorted(eligible, key=lambda item: item.observed_at))


def build_symbol_window(
    symbol: str,
    trades: Sequence[Trade],
    *,
    window_start_at: datetime,
    window_end_at: datetime,
) -> SymbolWindow:
    """Build one symbol's window or fail closed on missing observations."""

    eligible = _eligible_trades(
        trades, symbol=symbol, window_start_at=window_start_at, window_end_at=window_end_at
    )
    if not eligible:
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_MISSING,
            f"window.{symbol}",
            "no continuous observations inside the registered window",
        )
    first = eligible[0]
    if first.observed_at > window_start_at + timedelta(seconds=START_DELAY_MAX_SECONDS):
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_MISSING,
            f"window.{symbol}.start",
            "first observation exceeds the frozen start-delay bound",
        )
    last = eligible[-1]
    if last.observed_at < window_end_at - timedelta(seconds=END_AGE_MAX_SECONDS):
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_STALE,
            f"window.{symbol}.end",
            "final observation exceeds the frozen end-age bound",
        )
    volume = 0
    notional = Decimal(0)
    with collector_context():
        for trade in eligible:
            volume += trade.size
            notional += trade.price * trade.size
        if volume <= 0:
            raise CollectorRejected(
                CollectorReason.MARKET_OBSERVATION_MISSING,
                f"window.{symbol}.volume",
                "window volume must be positive",
            )
        vwap = notional / volume
    return SymbolWindow(
        symbol=symbol,
        first_observed_at=first.observed_at,
        first_price=first.price,
        last_observed_at=last.observed_at,
        last_price=last.price,
        window_vwap=vwap,
        window_volume=volume,
        trade_count=len(eligible),
    )


def build_synchronized_window(
    trades_by_symbol: Mapping[str, Sequence[Trade]],
    quotes_by_symbol: Mapping[str, Sequence[QuoteSample]],
    *,
    session_id: str,
    symbols: Sequence[str],
    window_start_at: datetime,
    window_end_at: datetime,
    require_quotes: bool,
) -> SynchronizedWindow:
    """Align every required symbol or fail closed with a stable reason."""

    if not symbols:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT, "window.symbols", "symbols must not be empty"
        )
    windows = tuple(
        build_symbol_window(
            symbol,
            trades_by_symbol.get(symbol, ()),
            window_start_at=window_start_at,
            window_end_at=window_end_at,
        )
        for symbol in sorted(set(symbols))
    )
    endpoints = [window.last_observed_at for window in windows]
    skew = max(endpoints) - min(endpoints)
    if skew > timedelta(seconds=ENDPOINT_SKEW_MAX_SECONDS):
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_ASYNCHRONOUS,
            "window.endpoints",
            f"cross-instrument endpoint skew {skew.total_seconds():.0f}s exceeds the bound",
        )
    quote_age_ms: dict[str, int] = {}
    for symbol in sorted(set(symbols)):
        samples = tuple(
            quote
            for quote in quotes_by_symbol.get(symbol, ())
            if quote.symbol == symbol and quote.observed_at <= window_end_at
        )
        if not samples:
            if require_quotes:
                raise CollectorRejected(
                    CollectorReason.MARKET_OBSERVATION_MISSING,
                    f"window.{symbol}.quotes",
                    "no quote samples were observed",
                )
            continue
        latest = max(samples, key=lambda item: item.observed_at)
        age_ms = int((window_end_at - latest.observed_at).total_seconds() * 1000)
        if age_ms > QUOTE_AGE_MAX_MILLISECONDS:
            raise CollectorRejected(
                CollectorReason.MARKET_OBSERVATION_STALE,
                f"window.{symbol}.quote_age",
                f"quote age {age_ms}ms exceeds the frozen bound",
            )
        quote_age_ms[symbol] = age_ms
    if require_quotes and set(quote_age_ms) != set(symbols):
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_MISSING,
            "window.quotes",
            "quote samples are required for every synchronized symbol",
        )
    return SynchronizedWindow(
        session_id=session_id,
        window_start_at=window_start_at,
        window_end_at=window_end_at,
        symbols=windows,
        quote_age_ms=dict(quote_age_ms),
    )


def quote_spread_basis_points(
    quotes: Sequence[QuoteSample],
    *,
    symbol: str,
    window_end_at: datetime,
) -> Decimal:
    """Median SIP NBBO spread in basis points over the final sixty seconds."""

    start = window_end_at - timedelta(seconds=QUOTE_SPREAD_SAMPLE_SECONDS)
    samples = tuple(
        quote
        for quote in quotes
        if quote.symbol == symbol and start < quote.observed_at <= window_end_at
    )
    if len(samples) < QUOTE_SPREAD_MIN_SAMPLES:
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_MISSING,
            f"quotes.{symbol}",
            f"at least {QUOTE_SPREAD_MIN_SAMPLES} quote samples are required",
        )
    spreads: list[Decimal] = []
    with collector_context():
        for quote in samples:
            mid = (quote.bid + quote.ask) / 2
            if mid <= 0:
                raise CollectorRejected(
                    CollectorReason.NON_FINITE_FEATURE,
                    f"quotes.{symbol}.mid",
                    "quote midpoint must be positive",
                )
            spreads.append((quote.ask - quote.bid) / mid * 10000)
    spreads.sort()
    count = len(spreads)
    if count % 2 == 1:
        return spreads[count // 2]
    with collector_context():
        return (spreads[count // 2 - 1] + spreads[count // 2]) / 2
