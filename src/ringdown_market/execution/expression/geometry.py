"""Deterministic contract eligibility and vertical geometry.

Selection is pure enumeration with frozen bounds and deterministic
tie-breaking; nothing is inferred. Leg validation reuses the permit
boundary's ``OptionLeg`` so compiled geometry is OCC-compatible without
creating a second broker path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ringdown_market.execution.expression.observations import (
    EXECUTABLE_DATA,
    ExpressionMarketSnapshot,
    OptionContractObservation,
    PackageObservation,
)
from ringdown_market.execution.expression.policy import PromotedExpressionPolicy
from ringdown_market.execution.expression.reasons import (
    ExpressionReason,
    ExpressionRejected,
)
from ringdown_market.execution.models import OptionLeg, OptionSide, OptionType, PositionIntent


def contract_dte(contract: OptionContractObservation, asof: date) -> int:
    """Days from the observation date to contract expiry."""

    return (contract.expiry - asof).days


def _quote_is_tradable(contract: OptionContractObservation) -> bool:
    quote = contract.quote
    return (
        contract.data_class == EXECUTABLE_DATA
        and quote.bid > 0
        and quote.ask > 0
        and not quote.crossed
    )


def _contract_is_eligible(
    contract: OptionContractObservation,
    policy: PromotedExpressionPolicy,
    *,
    option_type: OptionType,
    asof: date,
) -> bool:
    if contract.option_type != option_type.value:
        return False
    if not _quote_is_tradable(contract):
        return False
    dte = contract_dte(contract, asof)
    if dte < policy.min_dte or dte > policy.max_dte:
        return False
    if contract.reported_delta is None:
        return False
    absolute_delta = abs(contract.reported_delta)
    if absolute_delta < policy.delta_min or absolute_delta > policy.delta_max:
        return False
    if contract.open_interest is None:
        return False
    return contract.open_interest >= policy.liquidity_min_open_interest


def eligible_long_contracts(
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    *,
    option_type: OptionType,
    asof: date,
) -> tuple[OptionContractObservation, ...]:
    """All contracts satisfying the frozen DTE, delta, and liquidity bounds."""

    return tuple(
        contract
        for contract in snapshot.chain
        if _contract_is_eligible(contract, policy, option_type=option_type, asof=asof)
    )


def select_long_contract(
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    *,
    option_type: OptionType,
    asof: date,
    direction_is_up: bool = True,
) -> OptionContractObservation:
    """Deterministically select one eligible long leg or fail closed.

    Direction-first geometry: an up view longs the lowest eligible strike, a
    down view longs the highest eligible strike. Remaining ties break on
    lowest ask, then symbol order.
    """

    eligible = eligible_long_contracts(snapshot, policy, option_type=option_type, asof=asof)
    if not eligible:
        raise ExpressionRejected(
            ExpressionReason.UNSUPPORTED_CONTRACT,
            f"chain.{snapshot.underlying}",
            "no eligible contract satisfies the frozen bounds",
        )
    if direction_is_up:
        ordered = sorted(
            eligible,
            key=lambda contract: (contract.strike, contract.quote.ask, contract.symbol),
        )
    else:
        ordered = sorted(
            eligible,
            key=lambda contract: (-contract.strike, contract.quote.ask, contract.symbol),
        )
    return ordered[0]


@dataclass(frozen=True, slots=True)
class VerticalGeometry:
    """One validated same-expiry same-type two-leg geometry."""

    long_leg: OptionContractObservation
    short_leg: OptionContractObservation
    width: Decimal

    def __post_init__(self) -> None:
        if self.long_leg.expiry != self.short_leg.expiry:
            raise ValueError("vertical legs must share one expiry")
        if self.long_leg.option_type != self.short_leg.option_type:
            raise ValueError("vertical legs must share one option type")
        if not self.width.is_finite() or self.width <= 0:
            raise ValueError("vertical width must be positive")


def select_vertical_geometry(
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    *,
    direction_is_up: bool,
    asof: date,
) -> VerticalGeometry:
    """Select long and short legs for one direction or fail closed."""

    option_type = OptionType.CALL if direction_is_up else OptionType.PUT
    long_leg = select_long_contract(
        snapshot, policy, option_type=option_type, asof=asof, direction_is_up=direction_is_up
    )
    candidates = tuple(
        contract
        for contract in snapshot.chain
        if contract.expiry == long_leg.expiry
        and contract.symbol != long_leg.symbol
        and _contract_is_eligible(contract, policy, option_type=option_type, asof=asof)
        and (
            (direction_is_up and contract.strike > long_leg.strike)
            or (not direction_is_up and contract.strike < long_leg.strike)
        )
    )
    if not candidates:
        raise ExpressionRejected(
            ExpressionReason.GEOMETRY_INVALID,
            f"vertical.{snapshot.underlying}",
            "no opposite-side strike exists in the same expiry",
        )
    ordered = sorted(
        candidates,
        key=lambda contract: (
            abs(contract.strike - long_leg.strike),
            contract.quote.ask,
            contract.symbol,
        ),
    )
    short_leg = ordered[0]
    width = abs(short_leg.strike - long_leg.strike)
    if width < policy.width_min or width > policy.width_max:
        raise ExpressionRejected(
            ExpressionReason.WIDTH_OUT_OF_BOUNDS,
            f"vertical.{snapshot.underlying}",
            f"width {width} is outside the frozen bounds",
        )
    return VerticalGeometry(long_leg=long_leg, short_leg=short_leg, width=width)


def select_package(
    snapshot: ExpressionMarketSnapshot, geometry: VerticalGeometry
) -> PackageObservation:
    """Select the atomic package quote for one geometry or fail closed."""

    package_id = f"{geometry.long_leg.symbol}+{geometry.short_leg.symbol}"
    package = snapshot.package(package_id)
    if package is None:
        raise ExpressionRejected(
            ExpressionReason.PACKAGE_UNAVAILABLE,
            f"package.{package_id}",
            "no atomic package quote exists for the geometry",
        )
    if tuple(package.legs) != (geometry.long_leg.symbol, geometry.short_leg.symbol):
        raise ExpressionRejected(
            ExpressionReason.PACKAGE_UNAVAILABLE,
            f"package.{package_id}",
            "package legs do not match the geometry",
        )
    return package


def build_option_leg(
    contract: OptionContractObservation,
    *,
    side: OptionSide,
    position_intent: PositionIntent,
) -> OptionLeg:
    """Construct a permit-boundary-compatible option leg or fail closed."""

    try:
        return OptionLeg(
            symbol=contract.symbol,
            underlying=contract.underlying,
            expiry=contract.expiry,
            option_type=OptionType(contract.option_type),
            strike=contract.strike,
            side=side,
            position_intent=position_intent,
        )
    except ValueError as error:
        raise ExpressionRejected(
            ExpressionReason.UNSUPPORTED_CONTRACT,
            f"leg.{contract.symbol}",
            str(error),
        ) from None
