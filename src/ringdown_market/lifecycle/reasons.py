"""Stable fail-closed reason codes for the monitored PAPER lifecycle.

The lifecycle worker authorizes no mutation that the risk kernel and the
frozen exit plan have not already authorized. Every unsafe, unknown, partial,
or ambiguous state fails closed with one of these codes; terminal success and
PnL are never fabricated.
"""

from __future__ import annotations

from enum import StrEnum


class LifecycleState(StrEnum):
    """The frozen monitored PAPER lifecycle states."""

    APPROVED = "APPROVED"
    OPEN_SUBMITTED = "OPEN_SUBMITTED"
    OPEN_PARTIAL = "OPEN_PARTIAL"
    OPEN_FILLED = "OPEN_FILLED"
    OPEN_CANCELED = "OPEN_CANCELED"
    OPEN_UNKNOWN = "OPEN_UNKNOWN"
    HOLDING = "HOLDING"
    CLOSE_DUE = "CLOSE_DUE"
    CLOSE_SUBMITTED = "CLOSE_SUBMITTED"
    CLOSE_PARTIAL = "CLOSE_PARTIAL"
    CLOSED_FLAT = "CLOSED_FLAT"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class LifecycleReason(StrEnum):
    """Machine-readable rejection reasons for the monitored lifecycle."""

    EXIT_PLAN_UNVERIFIED = "EXIT_PLAN_UNVERIFIED"
    EXIT_PLAN_CLOCKS_MISORDERED = "EXIT_PLAN_CLOCKS_MISORDERED"
    EXIT_PLAN_COHORT_MISMATCH = "EXIT_PLAN_COHORT_MISMATCH"
    ENTRY_DEADLINE_PASSED = "ENTRY_DEADLINE_PASSED"
    ENTRY_BLOCKED_BY_CONTROL = "ENTRY_BLOCKED_BY_CONTROL"
    PERMIT_NOT_ACTIVE = "PERMIT_NOT_ACTIVE"
    DUPLICATE_TICK = "DUPLICATE_TICK"
    OPEN_ORDER_UNKNOWN = "OPEN_ORDER_UNKNOWN"
    OPEN_ORDER_PARTIAL = "OPEN_ORDER_PARTIAL"
    CLOSE_PERMIT_UNAVAILABLE = "CLOSE_PERMIT_UNAVAILABLE"
    CLOSE_ORDER_UNKNOWN = "CLOSE_ORDER_UNKNOWN"
    CLOSE_ORDER_PARTIAL = "CLOSE_ORDER_PARTIAL"
    BROKER_OUTAGE = "BROKER_OUTAGE"
    STALE_QUOTE = "STALE_QUOTE"
    STALE_ACCOUNT_TRUTH = "STALE_ACCOUNT_TRUTH"
    NON_FLAT_CLOSE = "NON_FLAT_CLOSE"
    FLATTENING_DEADLINE_PASSED = "FLATTENING_DEADLINE_PASSED"
    CLOCK_JUMP = "CLOCK_JUMP"
    RESTART_STATE_INVALID = "RESTART_STATE_INVALID"
    RESTART_INTENT_REPLAYED = "RESTART_INTENT_REPLAYED"
    LIFECYCLE_STATE_INVALID = "LIFECYCLE_STATE_INVALID"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    MUTATION_GATE_CLOSED = "MUTATION_GATE_CLOSED"
    UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"


class LifecycleRejected(ValueError):
    """A deterministic fail-closed lifecycle error."""

    def __init__(self, reason: LifecycleReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


def _reject(reason: LifecycleReason, path: str, detail: str) -> LifecycleRejected:
    return LifecycleRejected(reason, path, detail)


# States that still carry exposure and therefore block new entries.
EXPOSURE_BEARING_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.OPEN_SUBMITTED,
        LifecycleState.OPEN_PARTIAL,
        LifecycleState.OPEN_FILLED,
        LifecycleState.OPEN_UNKNOWN,
        LifecycleState.HOLDING,
        LifecycleState.CLOSE_DUE,
        LifecycleState.CLOSE_SUBMITTED,
        LifecycleState.CLOSE_PARTIAL,
    }
)
# Terminal states that end the lifecycle.
TERMINAL_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.OPEN_CANCELED,
        LifecycleState.CLOSED_FLAT,
        LifecycleState.MANUAL_REQUIRED,
    }
)
# States in which close/reconciliation authority is preserved.
CLOSE_ALLOWED_STATES: frozenset[LifecycleState] = frozenset(
    {
        LifecycleState.OPEN_PARTIAL,
        LifecycleState.OPEN_FILLED,
        LifecycleState.OPEN_UNKNOWN,
        LifecycleState.HOLDING,
        LifecycleState.CLOSE_DUE,
        LifecycleState.CLOSE_SUBMITTED,
        LifecycleState.CLOSE_PARTIAL,
        LifecycleState.MANUAL_REQUIRED,
    }
)
