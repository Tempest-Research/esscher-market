"""Decimal-only account and expression exposure calculations.

Exposure is always the conservative worst case for the expression, computed in
``Decimal``. Any expression whose exposure cannot be calculated conservatively
is rejected; nothing is approximated.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from esscher.execution.expression.compiler import CompiledExpression
from esscher.execution.expression.reasons import ExpressionKind
from esscher.risk.reasons import RiskReason, _reject


def _decimal_field(block: Mapping[str, object], field: str, *, path: str) -> Decimal:
    value = block.get(field)
    if value is None:
        raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, f"{path}.{field}", "field is missing")
    try:
        result = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError) as error:
        raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, f"{path}.{field}", str(error)) from None
    if not result.is_finite() or result < 0:
        raise _reject(
            RiskReason.EXPOSURE_NOT_CALCULABLE,
            f"{path}.{field}",
            "must be a finite non-negative decimal",
        )
    return result


def expression_exposure(compiled: CompiledExpression) -> Decimal:
    """Return the conservative worst-case exposure of one compiled expression."""

    kind = compiled.expression_kind
    if kind is ExpressionKind.CASH_NO_TRADE:
        return Decimal(0)
    if kind is ExpressionKind.SHARES:
        block = compiled.shares
        if block is None:
            raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, "shares", "block is missing")
        return _decimal_field(block, "exposure", path="shares")
    if kind is ExpressionKind.ONE_LONG_OPTION:
        block = compiled.long_option
        if block is None:
            raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, "long_option", "block is missing")
        return _decimal_field(block, "premium_at_risk", path="long_option")
    if kind is ExpressionKind.DEBIT_VERTICAL:
        block = compiled.debit_vertical
        if block is None:
            raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, "debit_vertical", "block is missing")
        return _decimal_field(block, "maximum_loss", path="debit_vertical")
    raise _reject(RiskReason.UNSUPPORTED_INPUT, "expression_kind", "unsupported expression kind")


def _valid_exposure(value: object, path: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise _reject(
            RiskReason.EXPOSURE_NOT_CALCULABLE,
            path,
            "must be a finite non-negative Decimal",
        )
    return value


def aggregate_exposure(open_exposures: Mapping[str, Decimal]) -> Decimal:
    """Return the total concurrent exposure across open expressions."""

    total = Decimal(0)
    for event_id, exposure in open_exposures.items():
        total += _valid_exposure(exposure, f"open_exposures.{event_id}")
    return total


def concentration_exposure(
    open_exposures_by_underlying: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    """Return gross absolute per-underlying exposure for concentration checks."""

    result: dict[str, Decimal] = {}
    for underlying, exposure in open_exposures_by_underlying.items():
        result[underlying] = abs(_valid_exposure(exposure, f"concentration.{underlying}"))
    return result
