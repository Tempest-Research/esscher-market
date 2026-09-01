"""Deterministic monitored-lifecycle state machine.

Every transition is a pure function of the current state and one trigger;
unmodeled transitions fail closed to ``MANUAL_REQUIRED``. The worker persists
each transition and side-effect intent before the next mutation, so a restart
resumes from broker/ledger truth rather than replaying intent. Broker
acknowledgement is never treated as fill proof.
"""

from __future__ import annotations

from enum import StrEnum

from ringdown_market.lifecycle.reasons import LifecycleReason, LifecycleState, _reject


class LifecycleTrigger(StrEnum):
    """Events that drive monitored-lifecycle transitions."""

    OPEN_SUBMIT = "OPEN_SUBMIT"
    OPEN_FILL = "OPEN_FILL"
    OPEN_PARTIAL_FILL = "OPEN_PARTIAL_FILL"
    OPEN_CANCEL = "OPEN_CANCEL"
    OPEN_AMBIGUOUS = "OPEN_AMBIGUOUS"
    HOLD_ELAPSED = "HOLD_ELAPSED"
    CLOSE_SUBMIT = "CLOSE_SUBMIT"
    CLOSE_FILL = "CLOSE_FILL"
    CLOSE_PARTIAL_FILL = "CLOSE_PARTIAL_FILL"
    CLOSE_AMBIGUOUS = "CLOSE_AMBIGUOUS"
    FLATTENING_DEADLINE = "FLATTENING_DEADLINE"
    MANUAL = "MANUAL"


_TRANSITIONS: dict[tuple[LifecycleState, LifecycleTrigger], LifecycleState] = {
    # Entry.
    (LifecycleState.APPROVED, LifecycleTrigger.OPEN_SUBMIT): LifecycleState.OPEN_SUBMITTED,
    # Opening reconciliation.
    (LifecycleState.OPEN_SUBMITTED, LifecycleTrigger.OPEN_FILL): LifecycleState.OPEN_FILLED,
    (LifecycleState.OPEN_SUBMITTED, LifecycleTrigger.OPEN_PARTIAL_FILL): (
        LifecycleState.OPEN_PARTIAL
    ),
    (LifecycleState.OPEN_SUBMITTED, LifecycleTrigger.OPEN_CANCEL): LifecycleState.OPEN_CANCELED,
    (LifecycleState.OPEN_SUBMITTED, LifecycleTrigger.OPEN_AMBIGUOUS): LifecycleState.OPEN_UNKNOWN,
    # An unknown opening can be resolved by a later readback, or stop for manual.
    (LifecycleState.OPEN_UNKNOWN, LifecycleTrigger.OPEN_FILL): LifecycleState.OPEN_FILLED,
    (LifecycleState.OPEN_UNKNOWN, LifecycleTrigger.OPEN_PARTIAL_FILL): (
        LifecycleState.OPEN_PARTIAL
    ),
    (LifecycleState.OPEN_UNKNOWN, LifecycleTrigger.OPEN_CANCEL): LifecycleState.OPEN_CANCELED,
    (LifecycleState.OPEN_UNKNOWN, LifecycleTrigger.MANUAL): LifecycleState.MANUAL_REQUIRED,
    # A partial opening cannot be repaired by sequential option legging.
    (LifecycleState.OPEN_PARTIAL, LifecycleTrigger.MANUAL): LifecycleState.MANUAL_REQUIRED,
    # Holding and time exit.
    (LifecycleState.OPEN_FILLED, LifecycleTrigger.HOLD_ELAPSED): LifecycleState.CLOSE_DUE,
    (LifecycleState.HOLDING, LifecycleTrigger.HOLD_ELAPSED): LifecycleState.CLOSE_DUE,
    # Closing.
    (LifecycleState.CLOSE_DUE, LifecycleTrigger.CLOSE_SUBMIT): LifecycleState.CLOSE_SUBMITTED,
    (LifecycleState.CLOSE_SUBMITTED, LifecycleTrigger.CLOSE_FILL): LifecycleState.CLOSED_FLAT,
    (LifecycleState.CLOSE_SUBMITTED, LifecycleTrigger.CLOSE_PARTIAL_FILL): (
        LifecycleState.CLOSE_PARTIAL
    ),
    (LifecycleState.CLOSE_SUBMITTED, LifecycleTrigger.CLOSE_AMBIGUOUS): (
        LifecycleState.MANUAL_REQUIRED
    ),
    (LifecycleState.CLOSE_PARTIAL, LifecycleTrigger.MANUAL): LifecycleState.MANUAL_REQUIRED,
}


def next_lifecycle_state(current: LifecycleState, trigger: LifecycleTrigger) -> LifecycleState:
    """Return the deterministic next lifecycle state for one trigger."""

    key = (current, trigger)
    if key not in _TRANSITIONS:
        # Unmodeled transitions fail closed to MANUAL_REQUIRED.
        return LifecycleState.MANUAL_REQUIRED
    return _TRANSITIONS[key]


def require_transition(current: LifecycleState, trigger: LifecycleTrigger) -> LifecycleState:
    """Return the next state, or fail closed when the transition is unmodeled."""

    key = (current, trigger)
    if key not in _TRANSITIONS:
        raise _reject(
            LifecycleReason.LIFECYCLE_STATE_INVALID,
            f"transition.{current.value}",
            f"trigger {trigger.value} is not permitted from {current.value}",
        )
    return _TRANSITIONS[key]


def is_terminal(state: LifecycleState) -> bool:
    """Terminal states end the lifecycle and admit no further mutation."""

    return state in {
        LifecycleState.OPEN_CANCELED,
        LifecycleState.CLOSED_FLAT,
        LifecycleState.MANUAL_REQUIRED,
    }


def entry_permitted(state: LifecycleState) -> bool:
    """A new opening is permitted only from the APPROVED state."""

    return state is LifecycleState.APPROVED


def close_permitted(state: LifecycleState) -> bool:
    """Close/reconciliation authority survives exposure-bearing and manual states."""

    return state in {
        LifecycleState.OPEN_PARTIAL,
        LifecycleState.OPEN_FILLED,
        LifecycleState.OPEN_UNKNOWN,
        LifecycleState.HOLDING,
        LifecycleState.CLOSE_DUE,
        LifecycleState.CLOSE_SUBMITTED,
        LifecycleState.CLOSE_PARTIAL,
        LifecycleState.MANUAL_REQUIRED,
    }
