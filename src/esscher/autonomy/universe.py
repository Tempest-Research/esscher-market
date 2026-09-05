"""Deterministic liquid-universe scan and defined-risk capital allocation.

PAPER only. This module is a pure scan/readiness/allocation contract: it turns
already-supplied active/mover observations into deterministic scan candidates
and sizes already-approved opportunity identities against the owner-approved
5%/10%/20% max-loss tiers with a <=20% per-underlying cap and a <=50%
aggregate open-debit cap. It contains no data-provider, reasoner, account,
broker, margin, buying-power, or order surface, and it never submits an order.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum, StrEnum
from typing import Final, NoReturn

from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes

UNIVERSE_SCAN_SCHEMA: Final = "esscher.liquid_universe_scan"
DEFINED_RISK_OPPORTUNITY_SCHEMA: Final = "esscher.defined_risk_opportunity"
DEFINED_RISK_RESERVATION_SCHEMA: Final = "esscher.defined_risk_reservation"
SCHEMA_VERSION: Final = 1

UNDERLYING_PRICE_FLOOR: Final = Decimal("5")
MAX_STOCK_SPREAD_BPS: Final = Decimal("100")
MIN_ACTIVE_OPTION_CONTRACTS: Final = 20
MAX_QUOTE_AGE: Final = timedelta(minutes=15)
MARKET_ANCHOR_SYMBOLS: Final = frozenset({"QQQ", "SPY"})
MAX_PER_UNDERLYING_FRACTION: Final = Decimal("0.20")
MAX_AGGREGATE_OPEN_DEBIT_FRACTION: Final = Decimal("0.50")

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_TEXT = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


class ProductKind(StrEnum):
    """Normalized product classification for one observed symbol."""

    US_COMMON_STOCK = "US_COMMON_STOCK"
    US_EXCHANGE_TRADED_PRODUCT = "US_EXCHANGE_TRADED_PRODUCT"
    OTC = "OTC"
    WARRANT = "WARRANT"
    RIGHT = "RIGHT"
    UNIT = "UNIT"
    LEVERAGED_INVERSE_ETP = "LEVERAGED_INVERSE_ETP"
    NON_US_PRODUCT = "NON_US_PRODUCT"


_ELIGIBLE_PRODUCT_KINDS: Final = frozenset(
    {ProductKind.US_COMMON_STOCK, ProductKind.US_EXCHANGE_TRADED_PRODUCT}
)


class UniverseLane(StrEnum):
    """The route a scan candidate is collected for; never a direction choice."""

    MARKET_ANCHOR = "MARKET_ANCHOR"
    CATALYST_STOCK = "CATALYST_STOCK"


class Readiness(StrEnum):
    """How far one scan candidate may travel toward a decision."""

    SCAN_ELIGIBLE = "SCAN_ELIGIBLE"
    DECISION_READY = "DECISION_READY"


class ScanExclusionReason(StrEnum):
    """Stable reasons an observation never becomes a scan candidate."""

    INACTIVE_PRODUCT = "INACTIVE_PRODUCT"
    UNTRADABLE_PRODUCT = "UNTRADABLE_PRODUCT"
    EXCLUDED_PRODUCT_KIND = "EXCLUDED_PRODUCT_KIND"
    INELIGIBLE_MARKET_ANCHOR = "INELIGIBLE_MARKET_ANCHOR"
    PRICE_BELOW_FLOOR = "PRICE_BELOW_FLOOR"
    QUOTE_MISSING = "QUOTE_MISSING"
    QUOTE_STALE = "QUOTE_STALE"
    QUOTE_CLOCK_SKEW = "QUOTE_CLOCK_SKEW"
    QUOTE_ONE_SIDED = "QUOTE_ONE_SIDED"
    QUOTE_CROSSED = "QUOTE_CROSSED"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    OPTION_LIQUIDITY_INSUFFICIENT = "OPTION_LIQUIDITY_INSUFFICIENT"


class ReadinessReason(StrEnum):
    """Explicit facts that keep a scan-eligible candidate from decision-ready."""

    OPTION_PAGE_INCOMPLETE = "OPTION_PAGE_INCOMPLETE"
    NEWS_PAGE_INCOMPLETE = "NEWS_PAGE_INCOMPLETE"
    NO_CONTEMPORANEOUS_NEWS = "NO_CONTEMPORANEOUS_NEWS"


class RiskTier(Enum):
    """Owner-approved max-loss tiers as exact Decimal equity fractions."""

    FIVE_PERCENT = Decimal("0.05")
    TEN_PERCENT = Decimal("0.10")
    TWENTY_PERCENT = Decimal("0.20")

    @property
    def fraction(self) -> Decimal:
        """Return the exact equity fraction this tier may lose."""

        return self.value


class AllocationStatus(StrEnum):
    """The only two outcomes of a pure allocation decision."""

    ALLOCATED = "ALLOCATED"
    ABSTAINED = "ABSTAINED"


class AbstainReason(StrEnum):
    """Stable reasons the allocator declines to reserve capacity."""

    OPPORTUNITY_NOT_READY = "OPPORTUNITY_NOT_READY"
    TIER_CAPACITY_INSUFFICIENT = "TIER_CAPACITY_INSUFFICIENT"
    UNDERLYING_CAP_INSUFFICIENT = "UNDERLYING_CAP_INSUFFICIENT"
    AGGREGATE_CAP_INSUFFICIENT = "AGGREGATE_CAP_INSUFFICIENT"
    CASH_INSUFFICIENT = "CASH_INSUFFICIENT"


class UniverseContractReason(StrEnum):
    """Stable reasons malformed contract input is rejected fail-closed."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    NON_CANONICAL_DOCUMENT = "NON_CANONICAL_DOCUMENT"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_BOOLEAN = "INVALID_BOOLEAN"
    FLOAT_FORBIDDEN = "FLOAT_FORBIDDEN"
    INVALID_DECIMAL = "INVALID_DECIMAL"
    INVALID_CLOCK = "INVALID_CLOCK"
    INVALID_COUNT = "INVALID_COUNT"
    INVALID_RANK = "INVALID_RANK"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    INCONSISTENT_STATE = "INCONSISTENT_STATE"
    DUPLICATE_EXPOSURE = "DUPLICATE_EXPOSURE"
    DUPLICATE_SYMBOL = "DUPLICATE_SYMBOL"
    DUPLICATE_RESERVATION = "DUPLICATE_RESERVATION"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"


class UniverseContractRejected(ValueError):
    """A deterministic fail-closed rejection of malformed contract input."""

    def __init__(self, reason: UniverseContractReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


def _reject(reason: UniverseContractReason, path: str, detail: str) -> NoReturn:
    raise UniverseContractRejected(reason, path, detail)


def _identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _reject(UniverseContractReason.INVALID_IDENTIFIER, path, "must be a normalized identifier")
    return value


def _symbol(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SYMBOL.fullmatch(value) is None:
        _reject(
            UniverseContractReason.INVALID_IDENTIFIER,
            path,
            "must be a normalized uppercase symbol",
        )
    return value


def _sha256_text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(
            UniverseContractReason.INVALID_DOCUMENT,
            path,
            "must be a lowercase SHA-256 digest",
        )
    return value


def _boolean(value: object, *, path: str) -> bool:
    if type(value) is not bool:
        _reject(UniverseContractReason.INVALID_BOOLEAN, path, "must be an explicit boolean")
    return value


def _availability(value: object, *, path: str) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        _reject(
            UniverseContractReason.INVALID_BOOLEAN,
            path,
            "must be an explicit boolean or null, never a number",
        )
    return value


def _decimal(value: object, *, path: str, none_allowed: bool = False) -> Decimal | None:
    if value is None:
        if none_allowed:
            return None
        _reject(UniverseContractReason.INVALID_DECIMAL, path, "must be a Decimal, not null")
    if isinstance(value, float):
        _reject(
            UniverseContractReason.FLOAT_FORBIDDEN,
            path,
            "Python floats are forbidden; use exact Decimal text",
        )
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _reject(UniverseContractReason.INVALID_DECIMAL, path, "must be a Decimal")
    if not value.is_finite():
        _reject(UniverseContractReason.INVALID_DECIMAL, path, "must be finite")
    return value


def _positive_decimal(value: object, *, path: str) -> Decimal:
    parsed = _decimal(value, path=path)
    assert parsed is not None
    if parsed <= 0:
        _reject(UniverseContractReason.INVALID_DECIMAL, path, "must be positive")
    return parsed


def _nonnegative_decimal(value: object, *, path: str) -> Decimal:
    parsed = _decimal(value, path=path)
    assert parsed is not None
    if parsed < 0:
        _reject(UniverseContractReason.INVALID_DECIMAL, path, "must not be negative")
    return parsed


def _count(value: object, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _reject(UniverseContractReason.INVALID_COUNT, path, "must be an integer count")
    if value < 0:
        _reject(UniverseContractReason.INVALID_COUNT, path, "must not be negative")
    return value


def _rank(value: object, *, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _reject(
            UniverseContractReason.INVALID_RANK,
            path,
            "must be a positive integer rank or null",
        )
    return value


def _timestamp(value: object, *, path: str, none_allowed: bool = False) -> datetime | None:
    if value is None:
        if none_allowed:
            return None
        _reject(UniverseContractReason.INVALID_CLOCK, path, "must be a UTC datetime, not null")
    if isinstance(value, float):
        _reject(
            UniverseContractReason.FLOAT_FORBIDDEN,
            path,
            "Python floats are forbidden for timestamps",
        )
    if not isinstance(value, datetime):
        _reject(UniverseContractReason.INVALID_CLOCK, path, "must be a datetime")
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        _reject(UniverseContractReason.INVALID_CLOCK, path, "must be an explicit UTC datetime")
    return value


def _timestamp_text(value: datetime) -> str:
    result = value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if result.endswith(".000000Z"):
        return result.removesuffix(".000000Z") + "Z"
    prefix, fraction = result[:-1].split(".", maxsplit=1)
    return f"{prefix}.{fraction.rstrip('0')}Z"


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal values must be finite")
    if value == 0:
        return "0"
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result


# ---------------------------------------------------------------------------
# Liquid-universe scan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UniverseObservation:
    """One supplied active/mover observation; never a live-provider result."""

    symbol: str
    product_kind: ProductKind
    lane: UniverseLane
    active: bool
    tradable: bool
    last: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    quoted_at: datetime | None
    observed_at: datetime
    option_contracts_active: int
    option_page_complete: bool
    news_records: int
    news_page_complete: bool
    iv_available: bool | None
    greeks_available: bool | None
    activity_rank: int | None
    absolute_movement: Decimal | None

    def __post_init__(self) -> None:
        _symbol(self.symbol, path="symbol")
        if not isinstance(self.product_kind, ProductKind):
            _reject(
                UniverseContractReason.UNKNOWN_STATE,
                "product_kind",
                "must be a ProductKind",
            )
        if not isinstance(self.lane, UniverseLane):
            _reject(UniverseContractReason.UNKNOWN_STATE, "lane", "must be a UniverseLane")
        _boolean(self.active, path="active")
        _boolean(self.tradable, path="tradable")
        _decimal(self.last, path="last", none_allowed=True)
        _decimal(self.bid, path="bid", none_allowed=True)
        _decimal(self.ask, path="ask", none_allowed=True)
        _timestamp(self.quoted_at, path="quoted_at", none_allowed=True)
        _timestamp(self.observed_at, path="observed_at")
        _count(self.option_contracts_active, path="option_contracts_active")
        _boolean(self.option_page_complete, path="option_page_complete")
        _count(self.news_records, path="news_records")
        _boolean(self.news_page_complete, path="news_page_complete")
        _availability(self.iv_available, path="iv_available")
        _availability(self.greeks_available, path="greeks_available")
        _rank(self.activity_rank, path="activity_rank")
        movement = _decimal(self.absolute_movement, path="absolute_movement", none_allowed=True)
        if movement is not None and movement < 0:
            _reject(
                UniverseContractReason.INVALID_DECIMAL,
                "absolute_movement",
                "must not be negative",
            )


@dataclass(frozen=True, slots=True)
class ScanCandidate:
    """One scan-eligible observation with explicit readiness and facts."""

    symbol: str
    lane: UniverseLane
    product_kind: ProductKind
    readiness: Readiness
    readiness_reasons: tuple[ReadinessReason, ...]
    last: Decimal
    bid: Decimal
    ask: Decimal
    spread_bps: Decimal
    quoted_at: datetime
    observed_at: datetime
    option_contracts_active: int
    option_page_complete: bool
    news_records: int
    news_page_complete: bool
    iv_available: bool | None
    greeks_available: bool | None
    activity_rank: int | None
    absolute_movement: Decimal | None


@dataclass(frozen=True, slots=True)
class ScanRejection:
    """One excluded observation with every applicable exclusion reason."""

    symbol: str
    reasons: tuple[ScanExclusionReason, ...]


@dataclass(frozen=True, slots=True)
class UniverseScanResult:
    """The deterministic ranked scan collection and rejection manifest."""

    as_of: datetime
    candidates: tuple[ScanCandidate, ...]
    rejections: tuple[ScanRejection, ...]


def _spread_bps(bid: Decimal, ask: Decimal) -> Decimal:
    return Decimal("20000") * (ask - bid) / (bid + ask)


def _exclusion_reasons(
    observation: UniverseObservation,
    *,
    as_of: datetime,
) -> tuple[ScanExclusionReason, ...]:
    reasons: list[ScanExclusionReason] = []
    if not observation.active:
        reasons.append(ScanExclusionReason.INACTIVE_PRODUCT)
    if not observation.tradable:
        reasons.append(ScanExclusionReason.UNTRADABLE_PRODUCT)
    if observation.product_kind not in _ELIGIBLE_PRODUCT_KINDS:
        reasons.append(ScanExclusionReason.EXCLUDED_PRODUCT_KIND)
    if (
        observation.lane is UniverseLane.MARKET_ANCHOR
        and observation.symbol not in MARKET_ANCHOR_SYMBOLS
    ):
        reasons.append(ScanExclusionReason.INELIGIBLE_MARKET_ANCHOR)
    if observation.last is not None and observation.last < UNDERLYING_PRICE_FLOOR:
        reasons.append(ScanExclusionReason.PRICE_BELOW_FLOOR)
    if (
        observation.last is None
        or observation.bid is None
        or observation.ask is None
        or observation.quoted_at is None
    ):
        reasons.append(ScanExclusionReason.QUOTE_MISSING)
    else:
        if observation.quoted_at > as_of:
            reasons.append(ScanExclusionReason.QUOTE_CLOCK_SKEW)
        elif as_of - observation.quoted_at > MAX_QUOTE_AGE:
            reasons.append(ScanExclusionReason.QUOTE_STALE)
        if observation.bid <= 0 or observation.ask <= 0:
            reasons.append(ScanExclusionReason.QUOTE_ONE_SIDED)
        elif observation.bid > observation.ask:
            reasons.append(ScanExclusionReason.QUOTE_CROSSED)
        elif _spread_bps(observation.bid, observation.ask) > MAX_STOCK_SPREAD_BPS:
            reasons.append(ScanExclusionReason.SPREAD_TOO_WIDE)
    if observation.option_contracts_active < MIN_ACTIVE_OPTION_CONTRACTS:
        reasons.append(ScanExclusionReason.OPTION_LIQUIDITY_INSUFFICIENT)
    return tuple(reasons)


def _readiness(observation: UniverseObservation) -> tuple[Readiness, tuple[ReadinessReason, ...]]:
    blockers: list[ReadinessReason] = []
    if not observation.option_page_complete:
        blockers.append(ReadinessReason.OPTION_PAGE_INCOMPLETE)
    if observation.lane is UniverseLane.CATALYST_STOCK:
        if not observation.news_page_complete:
            blockers.append(ReadinessReason.NEWS_PAGE_INCOMPLETE)
        elif observation.news_records < 1:
            blockers.append(ReadinessReason.NO_CONTEMPORANEOUS_NEWS)
    if blockers:
        return Readiness.SCAN_ELIGIBLE, tuple(blockers)
    return Readiness.DECISION_READY, ()


def _candidate(observation: UniverseObservation) -> ScanCandidate:
    assert observation.last is not None
    assert observation.bid is not None
    assert observation.ask is not None
    assert observation.quoted_at is not None
    readiness, blockers = _readiness(observation)
    return ScanCandidate(
        symbol=observation.symbol,
        lane=observation.lane,
        product_kind=observation.product_kind,
        readiness=readiness,
        readiness_reasons=blockers,
        last=observation.last,
        bid=observation.bid,
        ask=observation.ask,
        spread_bps=_spread_bps(observation.bid, observation.ask),
        quoted_at=observation.quoted_at,
        observed_at=observation.observed_at,
        option_contracts_active=observation.option_contracts_active,
        option_page_complete=observation.option_page_complete,
        news_records=observation.news_records,
        news_page_complete=observation.news_page_complete,
        iv_available=observation.iv_available,
        greeks_available=observation.greeks_available,
        activity_rank=observation.activity_rank,
        absolute_movement=observation.absolute_movement,
    )


def _rank_key(candidate: ScanCandidate, as_of: datetime) -> tuple[object, ...]:
    return (
        0 if candidate.lane is UniverseLane.MARKET_ANCHOR else 1,
        as_of - candidate.quoted_at,
        candidate.spread_bps,
        candidate.activity_rank is None,
        candidate.activity_rank or 0,
        candidate.absolute_movement is None,
        (-candidate.absolute_movement if candidate.absolute_movement is not None else Decimal("0")),
        0 if candidate.news_records >= 1 else 1,
        candidate.symbol,
    )


def scan_universe(
    observations: Sequence[UniverseObservation],
    *,
    as_of: datetime,
) -> UniverseScanResult:
    """Turn supplied observations into a deterministic scan collection.

    The result is order-independent: candidates carry a total deterministic
    rank key and rejections are sorted by symbol, so insertion order never
    changes the canonical bytes.
    """

    _timestamp(as_of, path="as_of")
    assert as_of is not None
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        _reject(
            UniverseContractReason.INVALID_DOCUMENT,
            "observations",
            "must be a sequence of UniverseObservation",
        )
    items = list(observations)
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, UniverseObservation):
            _reject(
                UniverseContractReason.INVALID_DOCUMENT,
                f"observations[{index}]",
                "must be a UniverseObservation",
            )
        if item.symbol in seen:
            _reject(
                UniverseContractReason.DUPLICATE_SYMBOL,
                f"observations[{index}].symbol",
                f"duplicate symbol {item.symbol}",
            )
        seen.add(item.symbol)
    candidates: list[ScanCandidate] = []
    rejections: list[ScanRejection] = []
    for item in items:
        reasons = _exclusion_reasons(item, as_of=as_of)
        if reasons:
            rejections.append(ScanRejection(symbol=item.symbol, reasons=reasons))
        else:
            candidates.append(_candidate(item))
    candidates.sort(key=lambda candidate: _rank_key(candidate, as_of))
    rejections.sort(key=lambda rejection: rejection.symbol)
    return UniverseScanResult(
        as_of=as_of,
        candidates=tuple(candidates),
        rejections=tuple(rejections),
    )


def _candidate_payload(candidate: ScanCandidate) -> dict[str, object]:
    return {
        "absolute_movement": (
            _decimal_text(candidate.absolute_movement)
            if candidate.absolute_movement is not None
            else None
        ),
        "activity_rank": candidate.activity_rank,
        "ask": _decimal_text(candidate.ask),
        "bid": _decimal_text(candidate.bid),
        "greeks_available": candidate.greeks_available,
        "iv_available": candidate.iv_available,
        "lane": candidate.lane.value,
        "last": _decimal_text(candidate.last),
        "news_page_complete": candidate.news_page_complete,
        "news_records": candidate.news_records,
        "observed_at": _timestamp_text(candidate.observed_at),
        "option_contracts_active": candidate.option_contracts_active,
        "option_page_complete": candidate.option_page_complete,
        "product_kind": candidate.product_kind.value,
        "quoted_at": _timestamp_text(candidate.quoted_at),
        "readiness": candidate.readiness.value,
        "readiness_reasons": [reason.value for reason in candidate.readiness_reasons],
        "spread_bps": _decimal_text(candidate.spread_bps),
        "symbol": candidate.symbol,
    }


def universe_scan_payload(result: UniverseScanResult) -> dict[str, object]:
    """Return the complete strict JSON object for one scan result."""

    if not isinstance(result, UniverseScanResult):
        _reject(
            UniverseContractReason.INVALID_DOCUMENT,
            "result",
            "must be a UniverseScanResult",
        )
    return {
        "as_of": _timestamp_text(result.as_of),
        "candidates": [_candidate_payload(candidate) for candidate in result.candidates],
        "rejections": [
            {
                "reasons": [reason.value for reason in rejection.reasons],
                "symbol": rejection.symbol,
            }
            for rejection in result.rejections
        ],
        "schema": UNIVERSE_SCAN_SCHEMA,
        "schema_version": SCHEMA_VERSION,
    }


def universe_scan_bytes(result: UniverseScanResult) -> bytes:
    """Serialize one scan result to its sole canonical UTF-8 JSON form."""

    return canonical_json_bytes(universe_scan_payload(result))


def universe_scan_sha256(result: UniverseScanResult) -> str:
    """Return the SHA-256 identity of the canonical scan bytes."""

    return sha256_bytes(universe_scan_bytes(result))


# ---------------------------------------------------------------------------
# Defined-risk capital allocation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UnderlyingExposure:
    """Open/reserved defined-risk debit already attributed to one underlying."""

    underlying: str
    open_debit: Decimal

    def __post_init__(self) -> None:
        _symbol(self.underlying, path="underlying")
        _positive_decimal(self.open_debit, path="open_debit")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Unborrowed-cash portfolio truth; no margin or buying-power field exists."""

    equity: Decimal
    cash: Decimal
    open_debit: Decimal
    exposures: tuple[UnderlyingExposure, ...] = ()

    def __post_init__(self) -> None:
        equity = _positive_decimal(self.equity, path="equity")
        cash = _nonnegative_decimal(self.cash, path="cash")
        open_debit = _nonnegative_decimal(self.open_debit, path="open_debit")
        if not isinstance(self.exposures, tuple) or not all(
            isinstance(exposure, UnderlyingExposure) for exposure in self.exposures
        ):
            _reject(
                UniverseContractReason.INVALID_DOCUMENT,
                "exposures",
                "must be a tuple of UnderlyingExposure",
            )
        underlyings = [exposure.underlying for exposure in self.exposures]
        if len(underlyings) != len(set(underlyings)):
            _reject(
                UniverseContractReason.DUPLICATE_EXPOSURE,
                "exposures",
                "each underlying may appear at most once",
            )
        if sum((exposure.open_debit for exposure in self.exposures), Decimal("0")) > open_debit:
            _reject(
                UniverseContractReason.INCONSISTENT_STATE,
                "exposures",
                "per-underlying open debit exceeds aggregate open debit",
            )
        if open_debit > equity:
            _reject(
                UniverseContractReason.INCONSISTENT_STATE,
                "open_debit",
                "aggregate open debit exceeds current equity",
            )
        if cash > equity:
            _reject(
                UniverseContractReason.INCONSISTENT_STATE,
                "cash",
                "unborrowed cash exceeds current equity",
            )


@dataclass(frozen=True, slots=True)
class DefinedRiskOpportunity:
    """One already-approved defined-risk identity awaiting deterministic sizing."""

    opportunity_id: str
    decision_id: str
    expression_id: str
    underlying: str
    risk_tier: RiskTier
    max_debit_per_contract: Decimal
    decision_ready: bool

    def __post_init__(self) -> None:
        _identifier(self.opportunity_id, path="opportunity_id")
        _identifier(self.decision_id, path="decision_id")
        _identifier(self.expression_id, path="expression_id")
        _symbol(self.underlying, path="underlying")
        if not isinstance(self.risk_tier, RiskTier):
            _reject(
                UniverseContractReason.UNKNOWN_STATE,
                "risk_tier",
                "must be an owner-approved RiskTier",
            )
        _positive_decimal(self.max_debit_per_contract, path="max_debit_per_contract")
        _boolean(self.decision_ready, path="decision_ready")


@dataclass(frozen=True, slots=True)
class AllocationReservation:
    """A persisted reservation identity used for idempotent replay."""

    opportunity_id: str
    opportunity_sha256: str
    reservation_id: str
    underlying: str
    quantity: int
    max_loss: Decimal

    def __post_init__(self) -> None:
        _identifier(self.opportunity_id, path="opportunity_id")
        _sha256_text(self.opportunity_sha256, path="opportunity_sha256")
        _sha256_text(self.reservation_id, path="reservation_id")
        _symbol(self.underlying, path="underlying")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            _reject(UniverseContractReason.INVALID_COUNT, "quantity", "must be an integer")
        if self.quantity < 1:
            _reject(UniverseContractReason.INVALID_COUNT, "quantity", "must be positive")
        _positive_decimal(self.max_loss, path="max_loss")


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    """The typed pure allocation outcome; it never submits an order."""

    status: AllocationStatus
    reason_codes: tuple[AbstainReason, ...]
    quantity: int
    max_loss: Decimal
    reservation_id: str | None
    remaining_tier: Decimal
    remaining_underlying: Decimal
    remaining_aggregate: Decimal
    remaining_cash: Decimal


def defined_risk_opportunity_payload(opportunity: DefinedRiskOpportunity) -> dict[str, object]:
    """Return the complete strict JSON object for one opportunity identity."""

    if not isinstance(opportunity, DefinedRiskOpportunity):
        _reject(
            UniverseContractReason.INVALID_DOCUMENT,
            "opportunity",
            "must be a DefinedRiskOpportunity",
        )
    return {
        "decision_id": opportunity.decision_id,
        "decision_ready": opportunity.decision_ready,
        "expression_id": opportunity.expression_id,
        "max_debit_per_contract": _decimal_text(opportunity.max_debit_per_contract),
        "opportunity_id": opportunity.opportunity_id,
        "risk_tier": _decimal_text(opportunity.risk_tier.fraction),
        "schema": DEFINED_RISK_OPPORTUNITY_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "underlying": opportunity.underlying,
    }


def defined_risk_opportunity_bytes(opportunity: DefinedRiskOpportunity) -> bytes:
    """Serialize one opportunity to its sole canonical UTF-8 JSON form."""

    return canonical_json_bytes(defined_risk_opportunity_payload(opportunity))


def defined_risk_opportunity_sha256(opportunity: DefinedRiskOpportunity) -> str:
    """Return the SHA-256 identity of the canonical opportunity bytes."""

    return sha256_bytes(defined_risk_opportunity_bytes(opportunity))


def _reservation_payload(
    opportunity: DefinedRiskOpportunity,
    quantity: int,
    max_loss: Decimal,
) -> dict[str, object]:
    return {
        "max_loss": _decimal_text(max_loss),
        "opportunity": defined_risk_opportunity_payload(opportunity),
        "quantity": quantity,
        "schema": DEFINED_RISK_RESERVATION_SCHEMA,
        "schema_version": SCHEMA_VERSION,
    }


def _reservation_identity(
    opportunity: DefinedRiskOpportunity,
    quantity: int,
    max_loss: Decimal,
) -> str:
    return sha256_bytes(canonical_json_bytes(_reservation_payload(opportunity, quantity, max_loss)))


class _DuplicateFieldError(ValueError):
    pass


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _invalid_float(value: str) -> NoReturn:
    raise ValueError(f"JSON numeric literal {value} is forbidden; use canonical decimal text")


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")


def _decode(raw: bytes) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(
            UniverseContractReason.INVALID_DOCUMENT,
            "bytes",
            "input must be immutable bytes",
        )
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_invalid_float,
            parse_constant=_invalid_constant,
        )
    except _DuplicateFieldError as error:
        _reject(UniverseContractReason.DUPLICATE_FIELD, "document", f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(UniverseContractReason.INVALID_DOCUMENT, "document", str(error))
    if not isinstance(payload, Mapping):
        _reject(UniverseContractReason.INVALID_DOCUMENT, "document", "root must be an object")
    return payload


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(UniverseContractReason.INVALID_DOCUMENT, path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(
            UniverseContractReason.MISSING_FIELD,
            f"{path}.{missing[0]}",
            "required field is missing",
        )
    unknown = sorted(keys - fields)
    if unknown:
        _reject(
            UniverseContractReason.UNKNOWN_FIELD,
            f"{path}.{unknown[0]}",
            "field is not part of the frozen schema",
        )
    return value


def _decimal_field(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_TEXT.fullmatch(value) is None:
        _reject(UniverseContractReason.INVALID_DECIMAL, path, "must be canonical decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        _reject(UniverseContractReason.INVALID_DECIMAL, path, str(error))
    if not parsed.is_finite() or _decimal_text(parsed) != value:
        _reject(UniverseContractReason.INVALID_DECIMAL, path, "decimal text is not canonical")
    return parsed


def _parse_risk_tier(value: object, *, path: str) -> RiskTier:
    parsed = _decimal_field(value, path=path)
    for tier in RiskTier:
        if tier.fraction == parsed:
            return tier
    _reject(UniverseContractReason.UNKNOWN_STATE, path, "must be an owner-approved tier fraction")


_OPPORTUNITY_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "opportunity_id",
        "decision_id",
        "expression_id",
        "underlying",
        "risk_tier",
        "max_debit_per_contract",
        "decision_ready",
    }
)


def parse_defined_risk_opportunity(raw: bytes) -> DefinedRiskOpportunity:
    """Parse only exact canonical opportunity bytes and reject unknown fields."""

    payload = _strict_object(_decode(raw), path="document", fields=_OPPORTUNITY_FIELDS)
    if (
        payload["schema"] != DEFINED_RISK_OPPORTUNITY_SCHEMA
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        _reject(
            UniverseContractReason.UNSUPPORTED_SCHEMA,
            "document",
            f"expected {DEFINED_RISK_OPPORTUNITY_SCHEMA}/v{SCHEMA_VERSION}",
        )
    opportunity = DefinedRiskOpportunity(
        opportunity_id=_identifier(payload["opportunity_id"], path="opportunity_id"),
        decision_id=_identifier(payload["decision_id"], path="decision_id"),
        expression_id=_identifier(payload["expression_id"], path="expression_id"),
        underlying=_symbol(payload["underlying"], path="underlying"),
        risk_tier=_parse_risk_tier(payload["risk_tier"], path="risk_tier"),
        max_debit_per_contract=_decimal_field(
            payload["max_debit_per_contract"],
            path="max_debit_per_contract",
        ),
        decision_ready=_boolean(payload["decision_ready"], path="decision_ready"),
    )
    if raw != defined_risk_opportunity_bytes(opportunity):
        _reject(
            UniverseContractReason.NON_CANONICAL_DOCUMENT,
            "document",
            "bytes do not match the sole canonical serialization",
        )
    return opportunity


def _underlying_open(portfolio: PortfolioState, underlying: str) -> Decimal:
    for exposure in portfolio.exposures:
        if exposure.underlying == underlying:
            return exposure.open_debit
    return Decimal("0")


def _validate_reservations(
    existing_reservations: Sequence[AllocationReservation],
) -> tuple[AllocationReservation, ...]:
    if isinstance(existing_reservations, (str, bytes)) or not isinstance(
        existing_reservations,
        Sequence,
    ):
        _reject(
            UniverseContractReason.INVALID_DOCUMENT,
            "existing_reservations",
            "must be a sequence of AllocationReservation",
        )
    reservations = tuple(existing_reservations)
    seen: set[str] = set()
    for index, reservation in enumerate(reservations):
        if not isinstance(reservation, AllocationReservation):
            _reject(
                UniverseContractReason.INVALID_DOCUMENT,
                f"existing_reservations[{index}]",
                "must be an AllocationReservation",
            )
        if reservation.opportunity_id in seen:
            _reject(
                UniverseContractReason.DUPLICATE_RESERVATION,
                f"existing_reservations[{index}].opportunity_id",
                f"duplicate reservation for {reservation.opportunity_id}",
            )
        seen.add(reservation.opportunity_id)
    return reservations


def allocate_defined_risk(
    portfolio: PortfolioState,
    opportunity: DefinedRiskOpportunity,
    *,
    existing_reservations: Sequence[AllocationReservation] = (),
) -> AllocationDecision:
    """Size one already-approved opportunity against frozen capacity caps.

    Capacity is the minimum of the tier fraction times current equity, the
    remaining 20% per-underlying cap, the remaining 50% aggregate-equity cap,
    and the remaining unborrowed cash. There is no daily trade-count cap and
    no margin buying-power input; the decision never submits an order.
    """

    if not isinstance(portfolio, PortfolioState):
        _reject(
            UniverseContractReason.INVALID_DOCUMENT,
            "portfolio",
            "must be a PortfolioState",
        )
    if not isinstance(opportunity, DefinedRiskOpportunity):
        _reject(
            UniverseContractReason.INVALID_DOCUMENT,
            "opportunity",
            "must be a DefinedRiskOpportunity",
        )
    reservations = _validate_reservations(existing_reservations)
    reserved_total = sum((reservation.max_loss for reservation in reservations), Decimal("0"))
    reserved_underlying = sum(
        (
            reservation.max_loss
            for reservation in reservations
            if reservation.underlying == opportunity.underlying
        ),
        Decimal("0"),
    )
    tier_capacity = opportunity.risk_tier.fraction * portfolio.equity
    underlying_open = _underlying_open(portfolio, opportunity.underlying)
    remaining_underlying = (
        MAX_PER_UNDERLYING_FRACTION * portfolio.equity - underlying_open - reserved_underlying
    )
    remaining_aggregate = (
        MAX_AGGREGATE_OPEN_DEBIT_FRACTION * portfolio.equity - portfolio.open_debit - reserved_total
    )
    remaining_cash = portfolio.cash - reserved_total
    for reservation in reservations:
        if reservation.opportunity_id == opportunity.opportunity_id:
            if reservation.opportunity_sha256 != defined_risk_opportunity_sha256(opportunity):
                _reject(
                    UniverseContractReason.IDENTITY_CONFLICT,
                    "opportunity",
                    "same opportunity identity carries conflicting canonical bytes",
                )
            return AllocationDecision(
                status=AllocationStatus.ALLOCATED,
                reason_codes=(),
                quantity=reservation.quantity,
                max_loss=reservation.max_loss,
                reservation_id=reservation.reservation_id,
                remaining_tier=tier_capacity - reservation.max_loss,
                remaining_underlying=remaining_underlying,
                remaining_aggregate=remaining_aggregate,
                remaining_cash=remaining_cash,
            )
    if not opportunity.decision_ready:
        return AllocationDecision(
            status=AllocationStatus.ABSTAINED,
            reason_codes=(AbstainReason.OPPORTUNITY_NOT_READY,),
            quantity=0,
            max_loss=Decimal("0"),
            reservation_id=None,
            remaining_tier=tier_capacity,
            remaining_underlying=remaining_underlying,
            remaining_aggregate=remaining_aggregate,
            remaining_cash=remaining_cash,
        )
    capacity = min(tier_capacity, remaining_underlying, remaining_aggregate, remaining_cash)
    quantity = int(capacity // opportunity.max_debit_per_contract)
    if quantity < 1:
        reasons: list[AbstainReason] = []
        if tier_capacity == capacity:
            reasons.append(AbstainReason.TIER_CAPACITY_INSUFFICIENT)
        if remaining_underlying == capacity:
            reasons.append(AbstainReason.UNDERLYING_CAP_INSUFFICIENT)
        if remaining_aggregate == capacity:
            reasons.append(AbstainReason.AGGREGATE_CAP_INSUFFICIENT)
        if remaining_cash == capacity:
            reasons.append(AbstainReason.CASH_INSUFFICIENT)
        return AllocationDecision(
            status=AllocationStatus.ABSTAINED,
            reason_codes=tuple(reasons),
            quantity=0,
            max_loss=Decimal("0"),
            reservation_id=None,
            remaining_tier=tier_capacity,
            remaining_underlying=remaining_underlying,
            remaining_aggregate=remaining_aggregate,
            remaining_cash=remaining_cash,
        )
    max_loss = quantity * opportunity.max_debit_per_contract
    return AllocationDecision(
        status=AllocationStatus.ALLOCATED,
        reason_codes=(),
        quantity=quantity,
        max_loss=max_loss,
        reservation_id=_reservation_identity(opportunity, quantity, max_loss),
        remaining_tier=tier_capacity - max_loss,
        remaining_underlying=remaining_underlying - max_loss,
        remaining_aggregate=remaining_aggregate - max_loss,
        remaining_cash=remaining_cash - max_loss,
    )


__all__ = [
    "AbstainReason",
    "AllocationDecision",
    "AllocationReservation",
    "AllocationStatus",
    "DefinedRiskOpportunity",
    "PortfolioState",
    "ProductKind",
    "Readiness",
    "ReadinessReason",
    "RiskTier",
    "ScanCandidate",
    "ScanExclusionReason",
    "ScanRejection",
    "UnderlyingExposure",
    "UniverseContractReason",
    "UniverseContractRejected",
    "UniverseLane",
    "UniverseObservation",
    "UniverseScanResult",
    "allocate_defined_risk",
    "defined_risk_opportunity_bytes",
    "defined_risk_opportunity_payload",
    "defined_risk_opportunity_sha256",
    "parse_defined_risk_opportunity",
    "scan_universe",
    "universe_scan_bytes",
    "universe_scan_payload",
    "universe_scan_sha256",
]
