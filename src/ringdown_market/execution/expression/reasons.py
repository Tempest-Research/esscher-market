"""Stable fail-closed reason codes for the Gate D expression lane.

This lane compares and compiles trade expressions. It never places orders,
touches accounts or positions, or promotes policy; every ambiguity fails
closed with one of these codes.
"""

from __future__ import annotations

from enum import StrEnum


class ExpressionKind(StrEnum):
    """The four frozen Gate D expressions, compared on identical terms."""

    CASH_NO_TRADE = "CASH_NO_TRADE"
    SHARES = "SHARES"
    ONE_LONG_OPTION = "ONE_LONG_OPTION"
    DEBIT_VERTICAL = "DEBIT_VERTICAL"


class ExpressionReason(StrEnum):
    """Machine-readable rejection reasons for expression compilation."""

    DIRECTION_NOT_VALIDATED = "DIRECTION_NOT_VALIDATED"
    DECISION_BINDING_MISMATCH = "DECISION_BINDING_MISMATCH"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
    GATE_D_RECEIPT_MISMATCH = "GATE_D_RECEIPT_MISMATCH"
    EXPRESSION_NOT_PROMOTED = "EXPRESSION_NOT_PROMOTED"
    NO_QUOTE = "NO_QUOTE"
    STALE_QUOTE = "STALE_QUOTE"
    ASYNCHRONOUS_QUOTES = "ASYNCHRONOUS_QUOTES"
    CROSSED_QUOTE = "CROSSED_QUOTE"
    INSUFFICIENT_SIZE = "INSUFFICIENT_SIZE"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    UNKNOWN_FEED = "UNKNOWN_FEED"
    INDICATIVE_ONLY = "INDICATIVE_ONLY"
    UNSUPPORTED_CONTRACT = "UNSUPPORTED_CONTRACT"
    DTE_OUT_OF_BOUNDS = "DTE_OUT_OF_BOUNDS"
    DELTA_OUT_OF_BOUNDS = "DELTA_OUT_OF_BOUNDS"
    GEOMETRY_INVALID = "GEOMETRY_INVALID"
    WIDTH_OUT_OF_BOUNDS = "WIDTH_OUT_OF_BOUNDS"
    DEBIT_NOT_BELOW_WIDTH = "DEBIT_NOT_BELOW_WIDTH"
    PACKAGE_UNAVAILABLE = "PACKAGE_UNAVAILABLE"
    BORROW_LOCATE_MISSING = "BORROW_LOCATE_MISSING"
    LIFECYCLE_CHECK_FAILED = "LIFECYCLE_CHECK_FAILED"
    EXPOSURE_BUDGET_EXCEEDED = "EXPOSURE_BUDGET_EXCEEDED"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    TIME_INCONSISTENT = "TIME_INCONSISTENT"
    UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"


class ExpressionRejected(ValueError):
    """A deterministic fail-closed expression error."""

    def __init__(self, reason: ExpressionReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


NO_PACKAGE = "NO_PACKAGE"
NO_EXPRESSION = "NO_EXPRESSION"
