"""Explicit split, dividend, and symbol-change adjustment policy.

Prices are adjusted for splits only; cash dividends follow the frozen
price-only policy and never alter returns. Every applied action is disclosed
through its receipt identity, and unresolved or conflicting actions fail
closed instead of being guessed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from esscher.sourcedata.decimal_math import collector_context
from esscher.sourcedata.interfaces import CorporateAction, DailyBar
from esscher.sourcedata.reasons import CollectorReason, CollectorRejected
from esscher.sourcedata.receipts import CorporateActionReceipt


@dataclass(frozen=True, slots=True)
class AdjustedClose:
    """One split-adjusted price-only close with its raw session identity."""

    session_id: str
    session_date: date
    adjusted_close: Decimal
    volume: int
    valid: bool


@dataclass(frozen=True, slots=True)
class AdjustmentOutcome:
    """The complete disclosed adjustment result for one symbol series."""

    symbol: str
    series: tuple[AdjustedClose, ...]
    applied_receipt_ids: tuple[str, ...]
    split_factors_by_date: Mapping[date, Decimal]


def split_factor(action: CorporateAction) -> Decimal:
    """Return the price multiplier applied to pre-ex-date prices for one split."""

    if action.action_type != "SPLIT" or action.ratio_numerator is None:
        raise CollectorRejected(
            CollectorReason.CORPORATE_ACTION_UNRESOLVED,
            f"corporate_action.{action.ticker}",
            "split factor requires a registered split",
        )
    assert action.ratio_denominator is not None
    with collector_context():
        return Decimal(action.ratio_denominator) / Decimal(action.ratio_numerator)


def resolve_actions(
    ticker: str,
    actions: Sequence[CorporateAction],
    *,
    start: date,
    end: date,
) -> tuple[tuple[CorporateAction, ...], dict[date, Decimal]]:
    """Validate and order the actions affecting one symbol range.

    Returns the relevant actions and the cumulative pre-ex-date multiplier for
    each ex-date. Conflicting or malformed actions fail closed.
    """

    relevant = tuple(
        action for action in actions if action.ticker == ticker and start <= action.ex_date <= end
    )
    seen_ex_dates: set[date] = set()
    factors: dict[date, Decimal] = {}
    with collector_context():
        for action in sorted(relevant, key=lambda item: (item.ex_date, item.action_type)):
            if action.action_type == "SYMBOL_CHANGE":
                raise CollectorRejected(
                    CollectorReason.CORPORATE_ACTION_UNRESOLVED,
                    f"corporate_action.{ticker}.{action.ex_date.isoformat()}",
                    "symbol changes must be resolved by the security master chain",
                )
            if action.action_type == "CASH_DIVIDEND":
                continue
            if action.ex_date in seen_ex_dates:
                raise CollectorRejected(
                    CollectorReason.ADJUSTMENT_POLICY_VIOLATION,
                    f"corporate_action.{ticker}.{action.ex_date.isoformat()}",
                    "conflicting splits share one ex-date and violate the frozen"
                    " split-only adjustment policy",
                )
            seen_ex_dates.add(action.ex_date)
            factors[action.ex_date] = split_factor(action)
    return relevant, factors


def adjust_series(
    bars: Sequence[DailyBar],
    actions: Sequence[CorporateAction],
    *,
    ticker: str,
    receipts_by_action: Mapping[CorporateAction, CorporateActionReceipt],
) -> AdjustmentOutcome:
    """Return one split-adjusted price-only series with disclosed actions."""

    if not bars:
        raise CollectorRejected(
            CollectorReason.MARKET_OBSERVATION_MISSING,
            f"daily_bars.{ticker}",
            "no daily bars were supplied for adjustment",
        )
    start = min(bar.session_date for bar in bars)
    end = max(bar.session_date for bar in bars)
    relevant, factors = resolve_actions(ticker, actions, start=start, end=end)
    applied_receipts: list[str] = []
    with collector_context():
        series: list[AdjustedClose] = []
        for bar in sorted(bars, key=lambda item: (item.session_date, item.session_id)):
            factor = Decimal(1)
            for ex_date, split in sorted(factors.items()):
                if ex_date > bar.session_date:
                    factor *= split
            series.append(
                AdjustedClose(
                    session_id=bar.session_id,
                    session_date=bar.session_date,
                    adjusted_close=bar.close * factor,
                    volume=bar.volume,
                    valid=bar.valid,
                )
            )
        for action in relevant:
            if action.action_type == "SPLIT":
                receipt = receipts_by_action.get(action)
                if receipt is None:
                    raise CollectorRejected(
                        CollectorReason.CORPORATE_ACTION_UNRESOLVED,
                        f"corporate_action.{ticker}.{action.ex_date.isoformat()}",
                        "split lacks a provenance receipt",
                    )
                applied_receipts.append(receipt.receipt_id)
    return AdjustmentOutcome(
        symbol=ticker,
        series=tuple(series),
        applied_receipt_ids=tuple(sorted(set(applied_receipts))),
        split_factors_by_date=dict(factors),
    )
