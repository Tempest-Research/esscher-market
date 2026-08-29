"""Immutable contracts for one bounded issuer-specific debit vertical."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

_OCC_SYMBOL = re.compile(
    r"^(?P<root>[A-Z]{1,6})(?P<year>\d{2})(?P<month>\d{2})(?P<day>\d{2})"
    r"(?P<option_type>[CP])(?P<strike>\d{8})$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RunMode(StrEnum):
    """The only account mode legal for Esscher execution."""

    PAPER = "PAPER"


class DataClass(StrEnum):
    """The option-data class available to the competition account."""

    INDICATIVE_DATA = "INDICATIVE_DATA"


class VerticalType(StrEnum):
    """The two capped-loss debit structures supported by policy."""

    BULL_CALL = "BULL_CALL"
    BEAR_PUT = "BEAR_PUT"


class OptionType(StrEnum):
    """Option contract right."""

    CALL = "CALL"
    PUT = "PUT"


class OptionSide(StrEnum):
    """Broker order side for one option leg."""

    BUY = "buy"
    SELL = "sell"


class PositionIntent(StrEnum):
    """Alpaca's explicit option-position intent."""

    BUY_TO_OPEN = "buy_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_OPEN = "sell_to_open"
    SELL_TO_CLOSE = "sell_to_close"


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _require_money(value: Decimal, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")
    if value.as_tuple().exponent < -2:
        raise ValueError(f"{field} cannot use more than two decimal places")


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """One standard OCC option contract and its intended opening action."""

    symbol: str
    underlying: str
    expiry: date
    option_type: OptionType
    strike: Decimal
    side: OptionSide
    position_intent: PositionIntent
    ratio_qty: int = 1

    def __post_init__(self) -> None:
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("option symbol must be normalized uppercase OCC format")
        if self.underlying != self.underlying.strip().upper():
            raise ValueError("underlying must be normalized uppercase")
        if self.ratio_qty != 1:
            raise ValueError("P0 supports ratio_qty=1 only")
        if not self.strike.is_finite() or self.strike <= 0:
            raise ValueError("strike must be finite and positive")

        match = _OCC_SYMBOL.fullmatch(self.symbol)
        if match is None:
            raise ValueError("option symbol must use standard compact OCC format")
        contract_expiry = date(
            2000 + int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        contract_type = OptionType.CALL if match.group("option_type") == "C" else OptionType.PUT
        contract_strike = Decimal(match.group("strike")) / Decimal(1000)

        if match.group("root") != self.underlying:
            raise ValueError("OCC symbol underlying does not match leg underlying")
        if contract_expiry != self.expiry:
            raise ValueError("OCC symbol expiry does not match leg expiry")
        if contract_type is not self.option_type:
            raise ValueError("OCC symbol option type does not match leg option type")
        if contract_strike != self.strike:
            raise ValueError("OCC symbol strike does not match leg strike")


@dataclass(frozen=True, slots=True)
class DebitVerticalPermit:
    """A short-lived capability authorizing one exact paper opening attempt."""

    permit_id: str
    event_run_id: str
    policy_sha256: str
    snapshot_sha256: str
    issued_at: datetime
    expires_at: datetime
    vertical_type: VerticalType
    quantity: int
    limit_price: Decimal
    legs: tuple[OptionLeg, OptionLeg]
    run_mode: RunMode = RunMode.PAPER
    data_class: DataClass = DataClass.INDICATIVE_DATA

    def __post_init__(self) -> None:
        if not self.permit_id.strip() or not self.event_run_id.strip():
            raise ValueError("permit_id and event_run_id must be non-empty")
        if self.run_mode is not RunMode.PAPER:
            raise ValueError("run_mode must be PAPER")
        if self.data_class is not DataClass.INDICATIVE_DATA:
            raise ValueError("data_class must be INDICATIVE_DATA")
        _require_sha256(self.policy_sha256, "policy_sha256")
        _require_sha256(self.snapshot_sha256, "snapshot_sha256")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if self.quantity != 1:
            raise ValueError("P0 permits exactly one spread package")
        _require_money(self.limit_price, "limit_price")

        long_leg, short_leg = self.legs
        if long_leg.side is not OptionSide.BUY or short_leg.side is not OptionSide.SELL:
            raise ValueError("legs must be ordered as long BUY then short SELL")
        if long_leg.position_intent is not PositionIntent.BUY_TO_OPEN:
            raise ValueError("long opening leg must use buy_to_open")
        if short_leg.position_intent is not PositionIntent.SELL_TO_OPEN:
            raise ValueError("short opening leg must use sell_to_open")
        if long_leg.underlying != short_leg.underlying:
            raise ValueError("vertical legs must share one underlying")
        if long_leg.expiry != short_leg.expiry:
            raise ValueError("vertical legs must share one expiry")
        if long_leg.option_type is not short_leg.option_type:
            raise ValueError("vertical legs must share one option type")

        if self.vertical_type is VerticalType.BULL_CALL:
            if long_leg.option_type is not OptionType.CALL:
                raise ValueError("bull call vertical requires call contracts")
            if long_leg.strike >= short_leg.strike:
                raise ValueError("bull call long strike must be below short strike")
        else:
            if long_leg.option_type is not OptionType.PUT:
                raise ValueError("bear put vertical requires put contracts")
            if long_leg.strike <= short_leg.strike:
                raise ValueError("bear put long strike must be above short strike")

        width = abs(short_leg.strike - long_leg.strike)
        if self.limit_price >= width:
            raise ValueError("limit_price must be below the vertical width")

    @property
    def underlying(self) -> str:
        return self.legs[0].underlying

    @property
    def maximum_loss(self) -> Decimal:
        """Maximum opening debit in paper-account dollars."""

        return self.limit_price * Decimal(100)


@dataclass(frozen=True, slots=True)
class ClosePermit:
    """A short-lived capability authorizing one atomic paper close attempt."""

    permit_id: str
    open_permit_id: str
    event_run_id: str
    policy_sha256: str
    snapshot_sha256: str
    issued_at: datetime
    expires_at: datetime
    limit_price: Decimal
    run_mode: RunMode = RunMode.PAPER
    data_class: DataClass = DataClass.INDICATIVE_DATA

    def __post_init__(self) -> None:
        identities = (self.permit_id, self.open_permit_id, self.event_run_id)
        if not all(value.strip() for value in identities):
            raise ValueError("permit_id, open_permit_id, and event_run_id must be non-empty")
        if self.run_mode is not RunMode.PAPER:
            raise ValueError("run_mode must be PAPER")
        if self.data_class is not DataClass.INDICATIVE_DATA:
            raise ValueError("data_class must be INDICATIVE_DATA")
        _require_sha256(self.policy_sha256, "policy_sha256")
        _require_sha256(self.snapshot_sha256, "snapshot_sha256")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if not self.limit_price.is_finite() or self.limit_price >= 0:
            raise ValueError("close limit_price must be a finite negative credit")
        if self.limit_price.as_tuple().exponent < -2:
            raise ValueError("limit_price cannot use more than two decimal places")
