"""Decimal-only account and expression exposure calculations.

Exposure is always the conservative worst case for the expression, computed in
Decimal. Any expression whose exposure cannot be calculated conservatively is
rejected with ``EXPOSURE_NOT_CALCULABLE``; nothing is approximated.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ringdown_market.execution.expression.compiler import CompiledExpression
from ringdown_market.execution.expression.reasons import ExpressionKind
from ringdown_market.risk.reasons import RiskReason, _reject


def _decimal_field(block: Mapping[str, object], field: str, *, path: str) -> Decimal:
    value = block.get(field)
    if value is None:
        raise _reject(
            RiskReason.EXPOSURE_NOT_CALCULABLE,
            f"{path}.{field}",
            "required exposure field is missing",
        )
    try:
        result = Decimal(str(value))
    except ArithmeticError as error:
        raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, f"{path}.{field}", str(error)) from None
    if not result.is_finite():
        raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, f"{path}.{field}", "must be finite")
    if result < 0:
        raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, f"{path}.{field}", "must be non-negative")
    return result


def expression_exposure(compiled: CompiledExpression) -> Decimal:
    """Return the conservative worst-case exposure of one compiled expression."""

    kind = compiled.expression_kind
    if kind is ExpressionKind.CASH_NO_TRADE:
        return Decimal(0)
    if kind is ExpressionKind.SHARES:
        block = compiled.shares
        if block is None:
            raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, "shares", "missing shares block")
        return _decimal_field(block, "exposure", path="shares")
    if kind is ExpressionKind.ONE_LONG_OPTION:
        block = compiled.long_option
        if block is None:
            raise _reject(
                RiskReason.EXPOSURE_NOT_CALCULABLE, "long_option", "missing long_option block"
            )
        return _decimal_field(block, "premium_at_risk", path="long_option")
    if kind is ExpressionKind.DEBIT_VERTICAL:
        block = compiled.debit_vertical
        if block is None:
            raise _reject(
                RiskReason.EXPOSURE_NOT_CALCULABLE,
                "debit_vertical",
                "missing debit_vertical block",
            )
        return _decimal_field(block, "maximum_loss", path="debit_vertical")
    raise _reject(
        RiskReason.UNSUPPORTED_INPUT,
        "expression_kind",
        f"unsupported expression kind {kind.value}",
    )


def aggregate_exposure(open_exposures: Mapping[str, Decimal]) -> Decimal:
    """Return the total concurrent exposure across open expressions."""

    total = Decimal(0)
    for event_id, exposure in open_exposures.items():
        if not exposure.is_finite() or exposure < 0:
            raise _reject(
                RiskReason.EXPOSURE_NOT_CALCULABLE,
                f"open_exposures.{event_id}",
                "open exposure must be a non-negative finite decimal",
            )
        total += exposure
    return total


def concentration_exposure(
    open_exposures_by_underlying: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    """Return per-underlying exposure for concentration checks."""

    result: dict[str, Decimal] = {}
    for underlying, exposure in open_exposures_by_underlying.items():
        if not exposure.is_finite() or exposure < 0:
            raise _reject(
                RiskReason.EXPOSURE_NOT_CALCULABLE,
                f"concentration.{underlying}",
                "underlying exposure must be a non-negative finite decimal",
            )
        result[underlying] = exposure
    return result
