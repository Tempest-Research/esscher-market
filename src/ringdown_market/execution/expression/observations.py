"""Strict point-in-time observation schemas for expression comparison.

Every observation is immutable and carries its raw observation timestamp,
pinned feed identity, and data-class label. Indicative observations stay
labeled ``INDICATIVE_DATA`` and never become executable-fill evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

EXECUTABLE_DATA = "EXECUTABLE_DATA"
INDICATIVE_DATA = "INDICATIVE_DATA"
_DATA_CLASSES = frozenset({EXECUTABLE_DATA, INDICATIVE_DATA})


def _require_utc(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo != UTC:
        raise ValueError(f"{field} must be a UTC datetime")


def _require_symbol(value: str, field: str) -> None:
    if not value or value != value.strip().upper():
        raise ValueError(f"{field} must be normalized uppercase text")


def _require_positive_money(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a positive finite decimal")


def _require_non_negative_money(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a non-negative finite decimal")


def _require_non_negative_int(value: int, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class FeedIdentity:
    """One pinned feed/tool/schema/version identity.

    Unknown or unpinned identities fail closed downstream; a feed identity is
    never inferred.
    """

    feed: str
    tool: str
    schema: str
    version: str

    def __post_init__(self) -> None:
        for field in ("feed", "tool", "schema", "version"):
            if not getattr(self, field):
                raise ValueError(f"{field} must be non-empty text")

    def identity_key(self) -> tuple[str, str, str, str]:
        return (self.feed, self.tool, self.schema, self.version)


@dataclass(frozen=True, slots=True)
class TwoSidedQuote:
    """One two-sided quote with sizes and its raw observation time."""

    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_non_negative_money(self.bid, "bid")
        _require_non_negative_money(self.ask, "ask")
        _require_non_negative_int(self.bid_size, "bid_size")
        _require_non_negative_int(self.ask_size, "ask_size")
        _require_utc(self.observed_at, "observed_at")

    @property
    def crossed(self) -> bool:
        return self.ask < self.bid

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class ShareObservation:
    """One underlying share quote observation."""

    symbol: str
    quote: TwoSidedQuote
    feed: FeedIdentity
    data_class: str

    def __post_init__(self) -> None:
        _require_symbol(self.symbol, "symbol")
        if self.data_class not in _DATA_CLASSES:
            raise ValueError("data_class must be EXECUTABLE_DATA or INDICATIVE_DATA")


@dataclass(frozen=True, slots=True)
class OptionContractObservation:
    """One listed option contract with its two-sided quote."""

    symbol: str
    underlying: str
    expiry: date
    option_type: str
    strike: Decimal
    quote: TwoSidedQuote
    feed: FeedIdentity
    data_class: str
    open_interest: int | None = None
    reported_delta: Decimal | None = None

    def __post_init__(self) -> None:
        _require_symbol(self.symbol, "symbol")
        _require_symbol(self.underlying, "underlying")
        if self.option_type not in {"CALL", "PUT"}:
            raise ValueError("option_type must be CALL or PUT")
        _require_positive_money(self.strike, "strike")
        if self.data_class not in _DATA_CLASSES:
            raise ValueError("data_class must be EXECUTABLE_DATA or INDICATIVE_DATA")
        if self.open_interest is not None:
            _require_non_negative_int(self.open_interest, "open_interest")
        if self.reported_delta is not None:
            if not self.reported_delta.is_finite():
                raise ValueError("reported_delta must be finite")
            if not Decimal(-1) <= self.reported_delta <= Decimal(1):
                raise ValueError("reported_delta must lie in [-1, 1]")


@dataclass(frozen=True, slots=True)
class PackageObservation:
    """One atomic multi-leg package quote (net debit terms)."""

    package_id: str
    legs: tuple[str, str]
    net_bid: Decimal
    net_ask: Decimal
    size: int
    observed_at: datetime
    feed: FeedIdentity
    data_class: str

    def __post_init__(self) -> None:
        if not self.package_id:
            raise ValueError("package_id must be non-empty text")
        if len(self.legs) != 2:
            raise ValueError("packages carry exactly two leg symbols")
        for leg in self.legs:
            _require_symbol(leg, "leg symbol")
        _require_non_negative_money(self.net_bid, "net_bid")
        _require_non_negative_money(self.net_ask, "net_ask")
        _require_non_negative_int(self.size, "size")
        _require_utc(self.observed_at, "observed_at")
        if self.data_class not in _DATA_CLASSES:
            raise ValueError("data_class must be EXECUTABLE_DATA or INDICATIVE_DATA")

    @property
    def crossed(self) -> bool:
        return self.net_ask < self.net_bid


@dataclass(frozen=True, slots=True)
class BorrowLocateEvidence:
    """Explicit borrow/locate evidence for a short share expression."""

    symbol: str
    located_quantity: int
    source: str
    observed_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        _require_symbol(self.symbol, "symbol")
        if type(self.located_quantity) is not int or self.located_quantity <= 0:
            raise ValueError("located_quantity must be a positive integer")
        if not self.source:
            raise ValueError("source must be non-empty text")
        _require_utc(self.observed_at, "observed_at")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a SHA-256 digest")


@dataclass(frozen=True, slots=True)
class ExpressionMarketSnapshot:
    """One immutable market snapshot bound to one validated decision."""

    snapshot_id: str
    underlying: str
    observation_clock_at: datetime
    decision_sha256: str
    share: ShareObservation
    chain: tuple[OptionContractObservation, ...]
    packages: tuple[PackageObservation, ...]
    borrow_locate: BorrowLocateEvidence | None

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("snapshot_id must be non-empty text")
        _require_symbol(self.underlying, "underlying")
        _require_utc(self.observation_clock_at, "observation_clock_at")
        if len(self.decision_sha256) != 64:
            raise ValueError("decision_sha256 must be a SHA-256 digest")
        if self.share.symbol != self.underlying:
            raise ValueError("share observation must match the snapshot underlying")
        if self.share.quote.observed_at > self.observation_clock_at:
            raise ValueError("observations cannot postdate the snapshot clock")
        for contract in self.chain:
            if contract.underlying != self.underlying:
                raise ValueError("chain contracts must share the snapshot underlying")
            if contract.quote.observed_at > self.observation_clock_at:
                raise ValueError("observations cannot postdate the snapshot clock")
        for package in self.packages:
            if package.observed_at > self.observation_clock_at:
                raise ValueError("observations cannot postdate the snapshot clock")
        symbols = tuple(contract.symbol for contract in self.chain)
        if symbols != tuple(sorted(set(symbols))):
            raise ValueError("chain contracts must be sorted and unique")
        package_ids = tuple(package.package_id for package in self.packages)
        if package_ids != tuple(sorted(set(package_ids))):
            raise ValueError("packages must be sorted and unique")
        if self.borrow_locate is not None:
            if self.borrow_locate.symbol != self.underlying:
                raise ValueError("borrow/locate evidence must match the underlying")
            if self.borrow_locate.observed_at > self.observation_clock_at:
                raise ValueError("borrow/locate evidence cannot postdate the snapshot clock")

    def contract(self, symbol: str) -> OptionContractObservation | None:
        matches = tuple(contract for contract in self.chain if contract.symbol == symbol)
        return matches[0] if matches else None

    def package(self, package_id: str) -> PackageObservation | None:
        matches = tuple(p for p in self.packages if p.package_id == package_id)
        return matches[0] if matches else None


def share_observation_payload(value: ShareObservation) -> dict[str, object]:
    return {
        "symbol": value.symbol,
        "bid": str(value.quote.bid),
        "ask": str(value.quote.ask),
        "bid_size": value.quote.bid_size,
        "ask_size": value.quote.ask_size,
        "observed_at": value.quote.observed_at.isoformat().replace("+00:00", "Z"),
        "feed": list(value.feed.identity_key()),
        "data_class": value.data_class,
    }


def option_observation_payload(value: OptionContractObservation) -> dict[str, object]:
    return {
        "symbol": value.symbol,
        "underlying": value.underlying,
        "expiry": value.expiry.isoformat(),
        "option_type": value.option_type,
        "strike": str(value.strike),
        "bid": str(value.quote.bid),
        "ask": str(value.quote.ask),
        "bid_size": value.quote.bid_size,
        "ask_size": value.quote.ask_size,
        "observed_at": value.quote.observed_at.isoformat().replace("+00:00", "Z"),
        "feed": list(value.feed.identity_key()),
        "data_class": value.data_class,
        "open_interest": value.open_interest,
        "reported_delta": None if value.reported_delta is None else str(value.reported_delta),
    }


def package_observation_payload(value: PackageObservation) -> dict[str, object]:
    return {
        "package_id": value.package_id,
        "legs": list(value.legs),
        "net_bid": str(value.net_bid),
        "net_ask": str(value.net_ask),
        "size": value.size,
        "observed_at": value.observed_at.isoformat().replace("+00:00", "Z"),
        "feed": list(value.feed.identity_key()),
        "data_class": value.data_class,
    }


def expression_market_snapshot_payload(value: ExpressionMarketSnapshot) -> dict[str, object]:
    """Return the single versioned serialization for one market snapshot."""

    borrow_locate = value.borrow_locate
    return {
        "schema": "esscher.expression_market_snapshot",
        "schema_version": 1,
        "snapshot_id": value.snapshot_id,
        "underlying": value.underlying,
        "observation_clock_at": value.observation_clock_at.isoformat().replace("+00:00", "Z"),
        "decision_sha256": value.decision_sha256,
        "share": share_observation_payload(value.share),
        "chain": [option_observation_payload(contract) for contract in value.chain],
        "packages": [package_observation_payload(package) for package in value.packages],
        "borrow_locate": None
        if borrow_locate is None
        else {
            "symbol": borrow_locate.symbol,
            "located_quantity": borrow_locate.located_quantity,
            "source": borrow_locate.source,
            "observed_at": borrow_locate.observed_at.isoformat().replace("+00:00", "Z"),
            "content_sha256": borrow_locate.content_sha256,
        },
    }


def expression_market_snapshot_bytes(value: ExpressionMarketSnapshot) -> bytes:
    """Serialize one market snapshot to deterministic canonical bytes."""

    return canonical_json_bytes(expression_market_snapshot_payload(value))


def expression_market_snapshot_sha256(value: ExpressionMarketSnapshot) -> str:
    return sha256_bytes(expression_market_snapshot_bytes(value))


@runtime_checkable
class ShareObservationSource(Protocol):
    """Read-only boundary for underlying share quotes."""

    def share_quote(self, symbol: str, asof: datetime) -> ShareObservation | None:
        """Return one point-in-time share quote, if observed."""
        ...


@runtime_checkable
class OptionObservationSource(Protocol):
    """Read-only boundary for option-chain and package observations."""

    def option_chain(
        self, underlying: str, asof: datetime
    ) -> tuple[OptionContractObservation, ...]:
        """Return point-in-time contract observations, if observed."""
        ...

    def packages(self, underlying: str, asof: datetime) -> tuple[PackageObservation, ...]:
        """Return point-in-time atomic package observations, if observed."""
        ...

    def borrow_locate(self, symbol: str, asof: datetime) -> BorrowLocateEvidence | None:
        """Return explicit borrow/locate evidence, if available."""
        ...
