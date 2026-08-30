"""Quote-side after-cost economics for the Gate D tournament.

Midpoint-only PnL is prohibited: buys consume the ask, sells consume the bid,
and the entry after-cost is the cost of crossing the quoted spread, measured
from two-sided quotes only. Underlying returns are never treated as option
PnL.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from ringdown_market.execution.expression.observations import (
    EXECUTABLE_DATA,
    ExpressionMarketSnapshot,
    OptionContractObservation,
    PackageObservation,
)
from ringdown_market.execution.expression.policy import PromotedExpressionPolicy
from ringdown_market.execution.expression.reasons import (
    ExpressionKind,
    ExpressionReason,
)

CONTRACT_MULTIPLIER: Final = Decimal(100)
_COMPARED: Final = "COMPARED"
_REJECTED: Final = "REJECTED"


@dataclass(frozen=True, slots=True)
class ExpressionEconomics:
    """One expression's quote-side economics for one event."""

    event_id: str
    expression_kind: ExpressionKind
    outcome: str
    reason: ExpressionReason | None
    entry_cost: Decimal | None
    max_loss: Decimal | None
    entry_spread_cost: Decimal | None

    @property
    def compared(self) -> bool:
        return self.outcome == _COMPARED


def _rejected(
    event_id: str,
    kind: ExpressionKind,
    reason: ExpressionReason,
) -> ExpressionEconomics:
    return ExpressionEconomics(
        event_id=event_id,
        expression_kind=kind,
        outcome=_REJECTED,
        reason=reason,
        entry_cost=None,
        max_loss=None,
        entry_spread_cost=None,
    )


def _check_quote_quality(
    bid: Decimal,
    ask: Decimal,
    bid_size: int,
    ask_size: int,
    *,
    policy: PromotedExpressionPolicy,
) -> ExpressionReason | None:
    if bid <= 0 or ask <= 0:
        return ExpressionReason.NO_QUOTE
    if ask < bid:
        return ExpressionReason.CROSSED_QUOTE
    if bid_size < policy.min_quote_size or ask_size < policy.min_quote_size:
        return ExpressionReason.INSUFFICIENT_SIZE
    return None


def cash_economics(event_id: str) -> ExpressionEconomics:
    """The cash/no-trade baseline carries no cost and no risk."""

    return ExpressionEconomics(
        event_id=event_id,
        expression_kind=ExpressionKind.CASH_NO_TRADE,
        outcome=_COMPARED,
        reason=None,
        entry_cost=Decimal(0),
        max_loss=Decimal(0),
        entry_spread_cost=Decimal(0),
    )


def shares_economics(
    event_id: str,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    *,
    direction_is_up: bool,
) -> ExpressionEconomics:
    """Share economics: buys cross the ask; shorts require borrow/locate."""

    kind = ExpressionKind.SHARES
    share = snapshot.share
    if share.data_class != EXECUTABLE_DATA:
        return _rejected(event_id, kind, ExpressionReason.INDICATIVE_ONLY)
    reason = _check_quote_quality(
        share.quote.bid,
        share.quote.ask,
        share.quote.bid_size,
        share.quote.ask_size,
        policy=policy,
    )
    if reason is not None:
        return _rejected(event_id, kind, reason)
    if not direction_is_up and snapshot.borrow_locate is None:
        return _rejected(event_id, kind, ExpressionReason.BORROW_LOCATE_MISSING)
    entry_price = share.quote.ask if direction_is_up else share.quote.bid
    exposure = entry_price
    if exposure > policy.operational_loss_budget:
        return _rejected(event_id, kind, ExpressionReason.EXPOSURE_BUDGET_EXCEEDED)
    return ExpressionEconomics(
        event_id=event_id,
        expression_kind=kind,
        outcome=_COMPARED,
        reason=None,
        entry_cost=entry_price,
        max_loss=exposure,
        entry_spread_cost=share.quote.spread,
    )


def option_economics(
    event_id: str,
    contract: OptionContractObservation,
    policy: PromotedExpressionPolicy,
) -> ExpressionEconomics:
    """One long option economics: premium-at-risk priced at the ask."""

    kind = ExpressionKind.ONE_LONG_OPTION
    if contract.data_class != EXECUTABLE_DATA:
        return _rejected(event_id, kind, ExpressionReason.INDICATIVE_ONLY)
    reason = _check_quote_quality(
        contract.quote.bid,
        contract.quote.ask,
        contract.quote.bid_size,
        contract.quote.ask_size,
        policy=policy,
    )
    if reason is not None:
        return _rejected(event_id, kind, reason)
    premium_at_risk = contract.quote.ask * CONTRACT_MULTIPLIER
    if premium_at_risk > policy.operational_loss_budget:
        return _rejected(event_id, kind, ExpressionReason.EXPOSURE_BUDGET_EXCEEDED)
    return ExpressionEconomics(
        event_id=event_id,
        expression_kind=kind,
        outcome=_COMPARED,
        reason=None,
        entry_cost=premium_at_risk,
        max_loss=premium_at_risk,
        entry_spread_cost=contract.quote.spread * CONTRACT_MULTIPLIER,
    )


def debit_vertical_economics(
    event_id: str,
    package: PackageObservation,
    width: Decimal,
    policy: PromotedExpressionPolicy,
) -> ExpressionEconomics:
    """Debit-vertical economics from one atomic package quote."""

    kind = ExpressionKind.DEBIT_VERTICAL
    if package.data_class != EXECUTABLE_DATA:
        return _rejected(event_id, kind, ExpressionReason.INDICATIVE_ONLY)
    if package.net_bid <= 0 or package.net_ask <= 0:
        return _rejected(event_id, kind, ExpressionReason.PACKAGE_UNAVAILABLE)
    if package.net_ask < package.net_bid:
        return _rejected(event_id, kind, ExpressionReason.CROSSED_QUOTE)
    if package.size < policy.min_quote_size:
        return _rejected(event_id, kind, ExpressionReason.INSUFFICIENT_SIZE)
    debit = package.net_ask * CONTRACT_MULTIPLIER
    if debit >= width * CONTRACT_MULTIPLIER:
        return _rejected(event_id, kind, ExpressionReason.DEBIT_NOT_BELOW_WIDTH)
    max_loss = min(debit, width * CONTRACT_MULTIPLIER)
    if max_loss > policy.operational_loss_budget:
        return _rejected(event_id, kind, ExpressionReason.EXPOSURE_BUDGET_EXCEEDED)
    return ExpressionEconomics(
        event_id=event_id,
        expression_kind=kind,
        outcome=_COMPARED,
        reason=None,
        entry_cost=debit,
        max_loss=max_loss,
        entry_spread_cost=(package.net_ask - package.net_bid) * CONTRACT_MULTIPLIER,
    )


def after_cost_edge_basis_points(
    economics: tuple[ExpressionEconomics, ...],
    *,
    direction_hits: int,
) -> Decimal | None:
    """Preregistered after-cost edge versus the cash baseline.

    Edge is the directional hit rate above one half, in basis points, minus
    the mean entry spread drag expressed as a fraction of the entry cost.
    Returns ``None`` when no event was compared.
    """

    compared = tuple(item for item in economics if item.compared)
    if not compared:
        return None
    drags: list[Decimal] = []
    for item in compared:
        if (
            item.entry_spread_cost is not None
            and item.entry_cost is not None
            and item.entry_cost > 0
        ):
            drags.append(item.entry_spread_cost / item.entry_cost)
    mean_drag = (sum(drags) / Decimal(len(drags))) if drags else Decimal(0)
    hit_rate = Decimal(direction_hits) / Decimal(len(compared))
    return (hit_rate - Decimal("0.5")) * Decimal(10000) - mean_drag * Decimal(10000)
