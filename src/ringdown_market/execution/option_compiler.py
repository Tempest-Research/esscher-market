"""Deterministic quote-safe debit-vertical option package compiler.

Converts one validated strategy decision plus one frozen read-only option-chain
snapshot into either one bounded debit vertical or an explicit NO_PACKAGE result.
The compiler never places an order and carries no account, position, mutation,
or model authority. Quotes remain INDICATIVE_DATA, never executable-fill evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

from ringdown_market.alpha.models import Direction
from ringdown_market.contracts.execution_policy import PAPER_PERMIT_MAXIMUM_LOSS

OPTION_CHAIN_SNAPSHOT_SCHEMA = "esscher.option_chain_snapshot"
OPTION_CHAIN_SNAPSHOT_SCHEMA_VERSION = 1

MAX_QUOTE_AGE_SECONDS = 2
MAX_PAIR_SKEW_SECONDS = 2
MAX_RELATIVE_SPREAD = Decimal("0.30")
FROZEN_WIDTHS = (Decimal("2.50"), Decimal("5.00"))
DTE_MIN_DAYS = 7
DTE_MAX_DAYS = 21
ENTRY_UTC_WINDOW_START = time(12, 0, 0)
ENTRY_UTC_WINDOW_END = time(16, 0, 0)
CONTRACT_MULTIPLIER = Decimal(100)
_CENTS = Decimal("0.01")

_OCC_SYMBOL = re.compile(
    r"^(?P<root>[A-Z]{1,6})(?P<year>\d{2})(?P<month>\d{2})(?P<day>\d{2})"
    r"(?P<option_type>[CP])(?P<strike>\d{8})$"
)
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,4})?$")
_MONEY = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class PackageRejectionReason(StrEnum):
    """Stable NO_PACKAGE reasons; deterministic and complete."""

    UNCERTAIN_DECISION = "UNCERTAIN_DECISION"
    CHAIN_DOCUMENT_REJECTED = "CHAIN_DOCUMENT_REJECTED"
    ENTRY_DATE_UNRESOLVED = "ENTRY_DATE_UNRESOLVED"
    NO_ELIGIBLE_EXPIRY = "NO_ELIGIBLE_EXPIRY"
    NO_ELIGIBLE_STRIKE_PAIR = "NO_ELIGIBLE_STRIKE_PAIR"
    NON_POSITIVE_QUOTE = "NON_POSITIVE_QUOTE"
    ZERO_QUOTE_SIZE = "ZERO_QUOTE_SIZE"
    CROSSED_QUOTE = "CROSSED_QUOTE"
    STALE_QUOTE = "STALE_QUOTE"
    QUOTE_SKEW_EXCEEDED = "QUOTE_SKEW_EXCEEDED"
    SPREAD_EXCEEDED = "SPREAD_EXCEEDED"
    DEBIT_NOT_POSITIVE = "DEBIT_NOT_POSITIVE"
    DEBIT_EXCEEDS_WIDTH = "DEBIT_EXCEEDS_WIDTH"
    DEBIT_EXCEEDS_RISK_CAP = "DEBIT_EXCEEDS_RISK_CAP"


class OptionCompilerRejected(ValueError):
    """Raised for structural violations of the chain-snapshot contract."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"{path}: {detail}")
        self.path = path
        self.detail = detail


def _reject(path: str, detail: str) -> None:
    raise OptionCompilerRejected(path, detail)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _parse_money(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str) or not _MONEY.match(value):
        _reject(path, "expected canonical decimal money text")
    try:
        return Decimal(value)
    except InvalidOperation:
        _reject(path, "not a valid decimal")


def _parse_decimal(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.match(value):
        _reject(path, "expected canonical decimal text")
    try:
        return Decimal(value)
    except InvalidOperation:
        _reject(path, "not a valid decimal")


def _parse_integer(value: object, *, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _reject(path, "expected an integer")
    return value


def _parse_timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.match(value):
        _reject(path, "expected UTC second-precision timestamp")
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        _reject(path, "timestamp does not exist")


def _parse_date(value: object, *, path: str) -> date:
    if not isinstance(value, str):
        _reject(path, "expected ISO-8601 date text")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _reject(path, "date does not exist")


@dataclass(frozen=True, slots=True)
class OptionContract:
    """One OCC contract identity."""

    symbol: str
    option_type: str
    strike: Decimal
    expiry: date


@dataclass(frozen=True, slots=True)
class LegQuote:
    """One two-sided indicative quote with raw observation time."""

    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    quoted_at: datetime


@dataclass(frozen=True, slots=True)
class QuotedContract:
    contract: OptionContract
    quote: LegQuote


@dataclass(frozen=True, slots=True)
class OptionChainSnapshot:
    """One frozen read-only option-chain observation."""

    underlying: str
    as_of: datetime
    source_id: str
    contracts: tuple[QuotedContract, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class CompiledOptionPackage:
    """One bounded debit vertical compatible with the frozen permit boundary."""

    strategy_payload: Mapping[str, object]
    raw: bytes
    sha256: str
    debit: Decimal
    width: Decimal
    expiry: date


@dataclass(frozen=True, slots=True)
class PackageCompilationResult:
    """Deterministic compiler outcome: one package or explicit NO_PACKAGE."""

    package: CompiledOptionPackage | None
    reasons: tuple[str, ...]

    @property
    def is_no_package(self) -> bool:
        return self.package is None


_CONTRACT_FIELDS = frozenset({"symbol", "option_type", "strike", "expiry", "quote"})
_QUOTE_FIELDS = frozenset({"bid", "ask", "bid_size", "ask_size", "quoted_at"})


def _parse_contract(value: object, *, path: str, underlying: str) -> QuotedContract:
    if not isinstance(value, Mapping):
        _reject(path, "expected an object")
    for key in value:
        if key not in _CONTRACT_FIELDS:
            _reject(f"{path}.{key}", "unknown field")
    for key in _CONTRACT_FIELDS:
        if key not in value:
            _reject(f"{path}.{key}", "missing required field")

    symbol = value["symbol"]
    if not isinstance(symbol, str):
        _reject(f"{path}.symbol", "expected text")
    match = _OCC_SYMBOL.fullmatch(symbol)
    if match is None:
        _reject(f"{path}.symbol", "malformed OCC symbol")

    option_type = value["option_type"]
    expected_type = "CALL" if match["option_type"] == "C" else "PUT"
    if option_type != expected_type:
        _reject(f"{path}.option_type", "option type contradicts the OCC symbol")

    strike = _parse_decimal(value["strike"], path=f"{path}.strike")
    symbol_strike = Decimal(match["strike"]) / Decimal(1000)
    if strike != symbol_strike:
        _reject(f"{path}.strike", "strike contradicts the OCC symbol")

    expiry = _parse_date(value["expiry"], path=f"{path}.expiry")
    symbol_expiry = date(2000 + int(match["year"]), int(match["month"]), int(match["day"]))
    if expiry != symbol_expiry:
        _reject(f"{path}.expiry", "expiry contradicts the OCC symbol")

    if match["root"] != underlying:
        _reject(f"{path}.symbol", "contract root contradicts the chain underlying")

    quote_value = value["quote"]
    if not isinstance(quote_value, Mapping):
        _reject(f"{path}.quote", "expected an object")
    for key in quote_value:
        if key not in _QUOTE_FIELDS:
            _reject(f"{path}.quote.{key}", "unknown field")
    for key in _QUOTE_FIELDS:
        if key not in quote_value:
            _reject(f"{path}.quote.{key}", "missing required field")
    quote = LegQuote(
        bid=_parse_money(quote_value["bid"], path=f"{path}.quote.bid"),
        ask=_parse_money(quote_value["ask"], path=f"{path}.quote.ask"),
        bid_size=_parse_integer(quote_value["bid_size"], path=f"{path}.quote.bid_size"),
        ask_size=_parse_integer(quote_value["ask_size"], path=f"{path}.quote.ask_size"),
        quoted_at=_parse_timestamp(quote_value["quoted_at"], path=f"{path}.quote.quoted_at"),
    )
    return QuotedContract(
        contract=OptionContract(
            symbol=symbol, option_type=expected_type, strike=strike, expiry=expiry
        ),
        quote=quote,
    )


def parse_option_chain_snapshot(raw: bytes) -> OptionChainSnapshot:
    """Parse strict chain bytes; malformed or ambiguous chains fail closed."""

    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise OptionCompilerRejected("chain", "chain bytes are required")
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OptionCompilerRejected("chain", f"invalid JSON: {error}") from None
    if not isinstance(payload, Mapping):
        _reject("chain", "expected an object")
    fields = frozenset(
        {"schema", "schema_version", "underlying", "as_of", "source_id", "contracts"}
    )
    for key in payload:
        if key not in fields:
            _reject(f"chain.{key}", "unknown field")
    for key in fields:
        if key not in payload:
            _reject(f"chain.{key}", "missing required field")
    if payload["schema"] != OPTION_CHAIN_SNAPSHOT_SCHEMA:
        _reject("chain.schema", "unsupported chain schema")
    if payload["schema_version"] != OPTION_CHAIN_SNAPSHOT_SCHEMA_VERSION:
        _reject("chain.schema_version", "unsupported chain schema version")

    underlying = payload["underlying"]
    if not isinstance(underlying, str) or underlying != underlying.strip().upper():
        _reject("chain.underlying", "underlying must be normalized uppercase")
    contracts_value = payload["contracts"]
    if not isinstance(contracts_value, list):
        _reject("chain.contracts", "expected a list of contracts")
    seen: set[str] = set()
    contracts: list[QuotedContract] = []
    for index, item in enumerate(contracts_value):
        quoted = _parse_contract(item, path=f"chain.contracts[{index}]", underlying=underlying)
        if quoted.contract.symbol in seen:
            _reject(f"chain.contracts[{index}].symbol", "duplicate contract")
        seen.add(quoted.contract.symbol)
        contracts.append(quoted)

    snapshot = OptionChainSnapshot(
        underlying=underlying,
        as_of=_parse_timestamp(payload["as_of"], path="chain.as_of"),
        source_id=payload["source_id"] if isinstance(payload["source_id"], str) else "",
        contracts=tuple(contracts),
        sha256=hashlib.sha256(bytes(raw)).hexdigest(),
    )
    if not snapshot.source_id.strip():
        _reject("chain.source_id", "source identity is required")
    return snapshot


def _quote_reasons(quote: LegQuote, *, as_of: datetime, path: str) -> list[PackageRejectionReason]:
    reasons: list[PackageRejectionReason] = []
    if quote.bid <= 0 or quote.ask <= 0:
        reasons.append(PackageRejectionReason.NON_POSITIVE_QUOTE)
        return reasons
    if quote.bid_size <= 0 or quote.ask_size <= 0:
        reasons.append(PackageRejectionReason.ZERO_QUOTE_SIZE)
    if quote.ask < quote.bid:
        reasons.append(PackageRejectionReason.CROSSED_QUOTE)
    age = (as_of - quote.quoted_at).total_seconds()
    if age < 0 or age > MAX_QUOTE_AGE_SECONDS:
        reasons.append(PackageRejectionReason.STALE_QUOTE)
    mid = (quote.ask + quote.bid) / 2
    relative_spread = (quote.ask - quote.bid) / mid
    if relative_spread > MAX_RELATIVE_SPREAD:
        reasons.append(PackageRejectionReason.SPREAD_EXCEEDED)
    return reasons


def _strategy_payload(
    *,
    underlying: str,
    direction: Direction,
    expiry: date,
    debit: Decimal,
    long_contract: OptionContract,
    short_contract: OptionContract,
) -> dict[str, object]:
    vertical_type = "BULL_CALL" if direction is Direction.UP else "BEAR_PUT"
    return {
        "kind": "DEBIT_VERTICAL",
        "underlying": underlying,
        "vertical_type": vertical_type,
        "expiry": expiry.isoformat(),
        "quantity": 1,
        "limit_price": str(debit),
        "long_leg": {
            "symbol": long_contract.symbol,
            "option_type": long_contract.option_type,
            "strike": str(long_contract.strike),
        },
        "short_leg": {
            "symbol": short_contract.symbol,
            "option_type": short_contract.option_type,
            "strike": str(short_contract.strike),
        },
    }


def compile_option_package(
    *,
    direction: Direction,
    chain: OptionChainSnapshot,
    entry_date: date,
    risk_cap: Decimal = PAPER_PERMIT_MAXIMUM_LOSS,
) -> PackageCompilationResult:
    """Compile one quote-safe debit vertical or an explicit NO_PACKAGE result."""

    if direction is Direction.UNCERTAIN:
        return PackageCompilationResult(
            package=None, reasons=(PackageRejectionReason.UNCERTAIN_DECISION.value,)
        )
    option_type = "CALL" if direction is Direction.UP else "PUT"

    reasons: set[PackageRejectionReason] = set()
    expiries = sorted(
        {
            quoted.contract.expiry
            for quoted in chain.contracts
            if quoted.contract.option_type == option_type
        }
    )
    eligible_expiries = [
        expiry for expiry in expiries if DTE_MIN_DAYS <= (expiry - entry_date).days <= DTE_MAX_DAYS
    ]
    if not eligible_expiries:
        reasons.add(PackageRejectionReason.NO_ELIGIBLE_EXPIRY)

    candidates: list[tuple[date, Decimal, Decimal, QuotedContract, QuotedContract]] = []
    for expiry in eligible_expiries:
        strikes = {
            quoted.contract.strike: quoted
            for quoted in chain.contracts
            if quoted.contract.option_type == option_type and quoted.contract.expiry == expiry
        }
        for width in FROZEN_WIDTHS:
            for long_strike in sorted(strikes):
                short_strike = (
                    long_strike + width if direction is Direction.UP else long_strike - width
                )
                if short_strike not in strikes:
                    continue
                long_quoted = strikes[long_strike]
                short_quoted = strikes[short_strike]
                long_reasons = _quote_reasons(long_quoted.quote, as_of=chain.as_of, path="long")
                short_reasons = _quote_reasons(short_quoted.quote, as_of=chain.as_of, path="short")
                reasons.update(long_reasons)
                reasons.update(short_reasons)
                skew = abs(
                    (long_quoted.quote.quoted_at - short_quoted.quote.quoted_at).total_seconds()
                )
                if skew > MAX_PAIR_SKEW_SECONDS:
                    reasons.add(PackageRejectionReason.QUOTE_SKEW_EXCEEDED)
                if long_reasons or short_reasons or skew > MAX_PAIR_SKEW_SECONDS:
                    continue
                debit = (long_quoted.quote.ask - short_quoted.quote.bid).quantize(
                    _CENTS, rounding=ROUND_HALF_UP
                )
                if debit <= 0:
                    reasons.add(PackageRejectionReason.DEBIT_NOT_POSITIVE)
                    continue
                if debit >= width:
                    reasons.add(PackageRejectionReason.DEBIT_EXCEEDS_WIDTH)
                    continue
                if debit * CONTRACT_MULTIPLIER > risk_cap:
                    reasons.add(PackageRejectionReason.DEBIT_EXCEEDS_RISK_CAP)
                    continue
                candidates.append((expiry, width, long_strike, long_quoted, short_quoted))

    if not candidates:
        if not reasons:
            reasons.add(PackageRejectionReason.NO_ELIGIBLE_STRIKE_PAIR)
        ordered = tuple(sorted(reason.value for reason in reasons))
        return PackageCompilationResult(package=None, reasons=ordered)

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    expiry, width, _long_strike, long_quoted, short_quoted = candidates[0]
    debit = (long_quoted.quote.ask - short_quoted.quote.bid).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )
    payload = _strategy_payload(
        underlying=chain.underlying,
        direction=direction,
        expiry=expiry,
        debit=debit,
        long_contract=long_quoted.contract,
        short_contract=short_quoted.contract,
    )
    raw = _canonical_json_bytes(payload)
    return PackageCompilationResult(
        package=CompiledOptionPackage(
            strategy_payload=payload,
            raw=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            debit=debit,
            width=width,
            expiry=expiry,
        ),
        reasons=(),
    )


def _entry_date(decision_cutoff: datetime) -> date | None:
    """Derive the reaction-session calendar date from the frozen cutoff window.

    The frozen v1 window is 09:30:00-09:36:05 America/New_York, which is
    13:30-14:37 UTC under EDT and 14:30-15:37 UTC under EST. Inside the
    registered UTC guard bounds the Eastern calendar date equals the UTC date;
    outside them the entry date cannot be established safely and fails closed.
    """

    normalized = decision_cutoff.astimezone(UTC)
    if not ENTRY_UTC_WINDOW_START <= normalized.time() <= ENTRY_UTC_WINDOW_END:
        return None
    return normalized.date()


def compile_option_package_from_decision(
    *,
    decision_direction: Direction,
    decision_ticker: str,
    chain_bytes: bytes,
    decision_cutoff: datetime,
    risk_cap: Decimal = PAPER_PERMIT_MAXIMUM_LOSS,
) -> PackageCompilationResult:
    """Compile from validated decision identity plus raw chain bytes.

    Malformed, missing, or ambiguous chain documents yield NO_PACKAGE with a
    stable reason instead of raising, so the production path fails closed.
    """

    entry_date = _entry_date(decision_cutoff)
    if entry_date is None:
        return PackageCompilationResult(
            package=None, reasons=(PackageRejectionReason.ENTRY_DATE_UNRESOLVED.value,)
        )
    try:
        chain = parse_option_chain_snapshot(chain_bytes)
    except OptionCompilerRejected:
        return PackageCompilationResult(
            package=None, reasons=(PackageRejectionReason.CHAIN_DOCUMENT_REJECTED.value,)
        )
    if chain.underlying != decision_ticker.strip().upper():
        return PackageCompilationResult(
            package=None, reasons=(PackageRejectionReason.CHAIN_DOCUMENT_REJECTED.value,)
        )
    return compile_option_package(
        direction=decision_direction,
        chain=chain,
        entry_date=entry_date,
        risk_cap=risk_cap,
    )
