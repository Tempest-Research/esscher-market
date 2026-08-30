"""Read-only source adapter interfaces and immutable source records.

Every record here is frozen and carries its raw observation or publication
time alongside retrieval time. Adapters expose only read methods; no order,
account, position, trading, or mutation surface exists in this package.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ringdown_market.sourcedata._checks import require_sha256, require_utc


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Provenance for one retrieved source record.

    Publisher time, retrieval time, entitlement, and redistribution status
    remain distinct fields; none of them substitutes for another.
    """

    source_class: str
    publisher: str
    content_sha256: str
    published_at: datetime | None
    published_at_precision: str
    retrieved_at: datetime
    entitlement: str
    redistribution_status: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.source_class or not self.source_class == self.source_class.upper():
            raise ValueError("source_class must be non-empty uppercase text")
        if not self.publisher:
            raise ValueError("publisher must be non-empty text")
        require_sha256(self.content_sha256, "content_sha256")
        if self.published_at is not None:
            require_utc(self.published_at, "published_at")
        require_utc(self.retrieved_at, "retrieved_at")
        if not self.published_at_precision:
            raise ValueError("published_at_precision must be non-empty text")
        if self.entitlement not in {"ENTITLED", "PUBLIC", "UNVERIFIED"}:
            raise ValueError("entitlement must be ENTITLED, PUBLIC, or UNVERIFIED")
        if self.redistribution_status not in {
            "REDISTRIBUTABLE",
            "NON_REDISTRIBUTABLE",
            "UNKNOWN",
        }:
            raise ValueError("redistribution_status is not a registered value")
        if self.entitlement == "UNVERIFIED" and not self.limitations:
            raise ValueError("unverified entitlement requires explicit limitations")


@dataclass(frozen=True, slots=True)
class QuarterFact:
    """One reported fiscal quarter used by earnings features."""

    fiscal_period: str
    revenue: Decimal | None
    eps_diluted: Decimal | None

    def __post_init__(self) -> None:
        if not self.fiscal_period:
            raise ValueError("fiscal_period must be non-empty text")
        for value in (self.revenue, self.eps_diluted):
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError("quarter facts must be finite decimals")


@dataclass(frozen=True, slots=True)
class GuidanceStatement:
    """Quantitative issuer guidance for one fiscal period."""

    fiscal_period: str
    withdrawn: bool
    revenue_low: Decimal | None
    revenue_high: Decimal | None
    eps_low: Decimal | None
    eps_high: Decimal | None

    def __post_init__(self) -> None:
        if not self.fiscal_period:
            raise ValueError("fiscal_period must be non-empty text")
        for value in (self.revenue_low, self.revenue_high, self.eps_low, self.eps_high):
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError("guidance bounds must be finite decimals")
        if self.withdrawn and any(
            value is not None
            for value in (self.revenue_low, self.revenue_high, self.eps_low, self.eps_high)
        ):
            raise ValueError("withdrawn guidance cannot carry quantitative bounds")


@dataclass(frozen=True, slots=True)
class IssuerRelease:
    """One primary issuer earnings release with quantitative facts."""

    event_id: str
    ticker: str
    provenance: SourceProvenance
    report_fiscal_period: str
    current_quarter: QuarterFact
    quarter_history: tuple[QuarterFact, ...]
    current_guidance: GuidanceStatement | None
    prior_guidance: GuidanceStatement | None

    def __post_init__(self) -> None:
        if not self.event_id or not self.ticker:
            raise ValueError("event_id and ticker must be non-empty text")
        if self.ticker != self.ticker.strip().upper():
            raise ValueError("ticker must be normalized uppercase text")
        if not self.report_fiscal_period:
            raise ValueError("report_fiscal_period must be non-empty text")
        periods = [self.current_quarter.fiscal_period]
        periods.extend(quarter.fiscal_period for quarter in self.quarter_history)
        if len(periods) != len(set(periods)):
            raise ValueError("quarter history must not repeat fiscal periods")


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One split, cash dividend, or symbol-change record."""

    ticker: str
    action_type: str
    ex_date: date
    ratio_numerator: int | None
    ratio_denominator: int | None
    symbol_from: str | None
    symbol_to: str | None
    provenance: SourceProvenance

    def __post_init__(self) -> None:
        if self.action_type not in {"SPLIT", "CASH_DIVIDEND", "SYMBOL_CHANGE"}:
            raise ValueError("action_type is not a registered corporate action")
        if not self.ticker:
            raise ValueError("ticker must be non-empty text")
        if self.action_type == "SPLIT":
            if (
                self.ratio_numerator is None
                or self.ratio_denominator is None
                or self.ratio_numerator <= 0
                or self.ratio_denominator <= 0
            ):
                raise ValueError("splits require positive ratio numerator and denominator")
        elif self.ratio_numerator is not None or self.ratio_denominator is not None:
            raise ValueError("only splits carry ratio terms")
        if self.action_type == "SYMBOL_CHANGE":
            if not self.symbol_from or not self.symbol_to:
                raise ValueError("symbol changes require both symbols")
        elif self.symbol_from is not None or self.symbol_to is not None:
            raise ValueError("only symbol changes carry symbol terms")


@dataclass(frozen=True, slots=True)
class SecurityMasterRecord:
    """Point-in-time security reference at one freeze instant."""

    ticker: str
    security_id: str
    issuer: str
    primary_exchange_mic: str
    security_type: str
    sector: str
    active_at_freeze: bool
    listed_option_exists: bool
    prior_regular_close: Decimal
    asof: datetime
    provenance: SourceProvenance

    def __post_init__(self) -> None:
        if self.ticker != self.ticker.strip().upper() or not self.ticker:
            raise ValueError("ticker must be normalized uppercase text")
        if not self.security_id or not self.issuer:
            raise ValueError("security_id and issuer must be non-empty text")
        if not self.primary_exchange_mic or not self.security_type or not self.sector:
            raise ValueError("exchange, security type, and sector must be non-empty text")
        if not self.prior_regular_close.is_finite() or self.prior_regular_close <= 0:
            raise ValueError("prior regular close must be a positive finite decimal")
        require_utc(self.asof, "asof")


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """One exchange session from the official calendar."""

    exchange_mic: str
    session_id: str
    session_date: date
    open_at: datetime
    close_at: datetime
    full_regular: bool
    provenance: SourceProvenance

    def __post_init__(self) -> None:
        if not self.exchange_mic or not self.session_id:
            raise ValueError("exchange_mic and session_id must be non-empty text")
        require_utc(self.open_at, "open_at")
        require_utc(self.close_at, "close_at")
        if self.close_at <= self.open_at:
            raise ValueError("session close must follow session open")


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One unadjusted end-of-session bar with raw volume."""

    symbol: str
    session_id: str
    session_date: date
    close: Decimal
    volume: int
    valid: bool

    def __post_init__(self) -> None:
        if self.symbol != self.symbol.strip().upper() or not self.symbol:
            raise ValueError("symbol must be normalized uppercase text")
        if not self.session_id:
            raise ValueError("session_id must be non-empty text")
        if not self.close.is_finite() or self.close <= 0:
            raise ValueError("close must be a positive finite decimal")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")


@dataclass(frozen=True, slots=True)
class Trade:
    """One consolidated last-sale print with its raw observation time."""

    symbol: str
    session_id: str
    observed_at: datetime
    price: Decimal
    size: int
    sale_condition: str

    def __post_init__(self) -> None:
        if self.symbol != self.symbol.strip().upper() or not self.symbol:
            raise ValueError("symbol must be normalized uppercase text")
        require_utc(self.observed_at, "observed_at")
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("price must be a positive finite decimal")
        if self.size <= 0:
            raise ValueError("size must be positive")
        if not self.sale_condition:
            raise ValueError("sale_condition must be non-empty text")


@dataclass(frozen=True, slots=True)
class QuoteSample:
    """One consolidated NBBO quote sample with its raw observation time."""

    symbol: str
    session_id: str
    observed_at: datetime
    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        if self.symbol != self.symbol.strip().upper() or not self.symbol:
            raise ValueError("symbol must be normalized uppercase text")
        require_utc(self.observed_at, "observed_at")
        if not self.bid.is_finite() or not self.ask.is_finite():
            raise ValueError("quote prices must be finite decimals")
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("quotes must be positive with ask at least bid")


@runtime_checkable
class EvidenceSource(Protocol):
    """Read-only boundary for issuer, calendar, master, and action evidence."""

    def sessions(self, exchange_mic: str, start: date, end: date) -> Sequence[SessionRecord]:
        """Return calendar sessions in the inclusive date range."""
        ...

    def security_master(self, ticker: str, asof: datetime) -> SecurityMasterRecord:
        """Return the point-in-time security record at one freeze instant."""
        ...

    def issuer_release(self, event_id: str) -> IssuerRelease | None:
        """Return the primary issuer release for one event, if published."""
        ...

    def sec_filing(self, event_id: str) -> IssuerRelease | None:
        """Return the corroborating SEC filing for one event, if available.

        SEC acceptance time never substitutes for issuer publication time.
        """
        ...

    def corporate_actions(self, ticker: str, start: date, end: date) -> Sequence[CorporateAction]:
        """Return corporate actions with ex-dates in the inclusive range."""
        ...


@runtime_checkable
class MarketDataSource(Protocol):
    """Read-only boundary for equity bars, trades, and quotes."""

    def daily_bars(self, symbol: str, start: date, end: date) -> Sequence[DailyBar]:
        """Return daily bars in the inclusive session-date range."""
        ...

    def window_trades(self, symbol: str, session_id: str) -> Sequence[Trade]:
        """Return consolidated prints for one session."""
        ...

    def window_quotes(self, symbol: str, session_id: str) -> Sequence[QuoteSample]:
        """Return consolidated quote samples for one session."""
        ...
