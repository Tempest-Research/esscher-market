"""Deterministic decimal math for reproducible feature arithmetic.

All collector arithmetic runs under one fixed decimal context so identical
inputs always produce identical outputs. Natural logarithms use the atanh
series and square roots use Newton iteration; both are exact to the frozen
context precision and never use binary floating point.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

PRECISION_DIGITS: int = 50
_ONE = Decimal(1)
_TWO = Decimal(2)
_BASE_CONTEXT: Context = Context(prec=PRECISION_DIGITS, rounding=ROUND_HALF_EVEN)


def collector_context() -> localcontext:
    """Return the single frozen decimal context used by the collector."""

    return localcontext(_BASE_CONTEXT)


def decimal_ln(value: Decimal) -> Decimal:
    """Compute the natural logarithm of a positive decimal deterministically."""

    if not value.is_finite() or value <= 0:
        raise ValueError("logarithm requires a positive finite decimal")
    with collector_context():
        scaled = value
        exponent = Decimal(0)
        ten = Decimal(10)
        while scaled >= ten:
            scaled /= ten
            exponent += _ONE
        while scaled < _ONE:
            scaled *= ten
            exponent -= _ONE
        argument = (scaled - _ONE) / (scaled + _ONE)
        squared = argument * argument
        term = argument
        total = argument
        index = 1
        while True:
            term *= squared
            step = term / (2 * index + 1)
            updated = total + step
            if updated == total:
                break
            total = updated
            index += 1
        ln_ten = _ln_ten_cached()
        return _TWO * total + exponent * ln_ten


_LN_TEN_CACHE: Decimal | None = None


def _ln_ten_cached() -> Decimal:
    global _LN_TEN_CACHE
    if _LN_TEN_CACHE is None:
        with collector_context():
            argument = (Decimal(10) - _ONE) / (Decimal(10) + _ONE)
            squared = argument * argument
            term = argument
            total = argument
            index = 1
            while True:
                term *= squared
                step = term / (2 * index + 1)
                updated = total + step
                if updated == total:
                    break
                total = updated
                index += 1
            _LN_TEN_CACHE = _TWO * total
    return _LN_TEN_CACHE


def decimal_sqrt(value: Decimal) -> Decimal:
    """Compute the square root of a non-negative decimal deterministically."""

    if not value.is_finite() or value < 0:
        raise ValueError("square root requires a non-negative finite decimal")
    if value == 0:
        return Decimal(0)
    with collector_context():
        guess = value
        while True:
            improved = (guess + value / guess) / _TWO
            if improved == guess:
                return guess
            guess = improved


def log_return(start_price: Decimal, end_price: Decimal) -> Decimal:
    """Compute the log return between two positive prices."""

    return decimal_ln(end_price) - decimal_ln(start_price)
