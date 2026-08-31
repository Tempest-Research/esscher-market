"""Stable fail-closed reason codes for the PAPER account risk kernel.

The risk kernel authorizes nothing by default: every unsafe account,
portfolio, clock, quote, policy, or lifecycle truth fails closed with one of
these codes. It never places orders, touches real money, or promotes policy.
"""

from __future__ import annotations

from enum import StrEnum


class ControlState(StrEnum):
    """Deterministic risk control states."""

    ACTIVE = "ACTIVE"
    ENTRY_DISABLED = "ENTRY_DISABLED"
    CLOSE_ONLY = "CLOSE_ONLY"
    KILL = "KILL"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class RiskReason(StrEnum):
    """Machine-readable rejection reasons for the risk kernel."""

    POLICY_UNVERIFIED_CONSTANT = "POLICY_UNVERIFIED_CONSTANT"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"
    POLICY_NOT_PAPER_ONLY = "POLICY_NOT_PAPER_ONLY"
    CONTROL_STATE_BLOCKS_ENTRY = "CONTROL_STATE_BLOCKS_ENTRY"
    DUPLICATE_EVENT_RESERVATION = "DUPLICATE_EVENT_RESERVATION"
    IMMUTABLE_EVENT_REPLAY = "IMMUTABLE_EVENT_REPLAY"
    NOT_RUN_EVENT = "NOT_RUN_EVENT"
    EVENT_LIFECYCLE_INVALID = "EVENT_LIFECYCLE_INVALID"
    PERMIT_LIFECYCLE_INVALID = "PERMIT_LIFECYCLE_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    DAILY_LOSS_LIMIT_BREACHED = "DAILY_LOSS_LIMIT_BREACHED"
    DRAWDOWN_LIMIT_BREACHED = "DRAWDOWN_LIMIT_BREACHED"
    CONCENTRATION_LIMIT_BREACHED = "CONCENTRATION_LIMIT_BREACHED"
    ENTRY_COUNT_LIMIT_REACHED = "ENTRY_COUNT_LIMIT_REACHED"
    EXPRESSION_LIMIT_REACHED = "EXPRESSION_LIMIT_REACHED"
    UNKNOWN_EXPOSURE = "UNKNOWN_EXPOSURE"
    EXPOSURE_NOT_CALCULABLE = "EXPOSURE_NOT_CALCULABLE"
    STALE_ACCOUNT_TRUTH = "STALE_ACCOUNT_TRUTH"
    STALE_POSITION_TRUTH = "STALE_POSITION_TRUTH"
    STALE_ORDER_TRUTH = "STALE_ORDER_TRUTH"
    STALE_CLOCK = "STALE_CLOCK"
    STALE_QUOTE = "STALE_QUOTE"
    CONTRADICTORY_TRUTH = "CONTRADICTORY_TRUTH"
    UNKNOWN_ROUTE = "UNKNOWN_ROUTE"
    MISSING_CAPABILITY = "MISSING_CAPABILITY"
    NON_FLAT_STATE = "NON_FLAT_STATE"
    PARTIAL_FILL_STATE = "PARTIAL_FILL_STATE"
    RESERVATION_NOT_RELEASED = "RESERVATION_NOT_RELEASED"
    LEDGER_MIGRATION_REQUIRED = "LEDGER_MIGRATION_REQUIRED"
    PASSPORT_VERIFICATION_FAILED = "PASSPORT_VERIFICATION_FAILED"
    UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"


class RiskRejected(ValueError):
    """A deterministic fail-closed risk error."""

    def __init__(self, reason: RiskReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


def _reject(reason: RiskReason, path: str, detail: str) -> RiskRejected:
    return RiskRejected(reason, path, detail)


ENTRY_BLOCKING_STATES: frozenset[ControlState] = frozenset(
    {
        ControlState.ENTRY_DISABLED,
        ControlState.CLOSE_ONLY,
        ControlState.KILL,
        ControlState.MANUAL_REQUIRED,
    }
)
CLOSE_ALLOWED_STATES: frozenset[ControlState] = frozenset(
    {
        ControlState.ACTIVE,
        ControlState.ENTRY_DISABLED,
        ControlState.CLOSE_ONLY,
        ControlState.MANUAL_REQUIRED,
    }
)
