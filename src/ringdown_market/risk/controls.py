"""Deterministic risk control-state machine.

Stale, contradictory, unknown, partial-fill, or non-flat truth moves the
kernel into ``ENTRY_DISABLED``, ``CLOSE_ONLY``, or ``MANUAL_REQUIRED``. A
``MANUAL_REQUIRED`` or disabled state blocks new entries while preserving
close/reconciliation authority. ``KILL`` is terminal. No transition grants new
authority; transitions only restrict it.
"""

from __future__ import annotations

from enum import StrEnum

from ringdown_market.risk.reasons import ControlState, RiskReason, _reject


class ControlTrigger(StrEnum):
    """Events that drive control-state transitions."""

    DRAWDOWN_LIMIT_BREACHED = "DRAWDOWN_LIMIT_BREACHED"
    DAILY_LOSS_LIMIT_BREACHED = "DAILY_LOSS_LIMIT_BREACHED"
    STALE_TRUTH = "STALE_TRUTH"
    CONTRADICTORY_TRUTH = "CONTRADICTORY_TRUTH"
    PARTIAL_FILL_STATE = "PARTIAL_FILL_STATE"
    NON_FLAT_STATE = "NON_FLAT_STATE"
    CONCENTRATION_LIMIT_BREACHED = "CONCENTRATION_LIMIT_BREACHED"
    KILL_REQUEST = "KILL_REQUEST"
    MANUAL_REQUIRED_REQUEST = "MANUAL_REQUIRED_REQUEST"
    RESOLVED = "RESOLVED"


# Transitions that restrict authority. ``KILL`` is terminal.
_TRANSITIONS: dict[tuple[ControlState, ControlTrigger], ControlState] = {
    (ControlState.ACTIVE, ControlTrigger.DRAWDOWN_LIMIT_BREACHED): ControlState.ENTRY_DISABLED,
    (ControlState.ACTIVE, ControlTrigger.DAILY_LOSS_LIMIT_BREACHED): ControlState.ENTRY_DISABLED,
    (ControlState.ACTIVE, ControlTrigger.STALE_TRUTH): ControlState.ENTRY_DISABLED,
    (ControlState.ACTIVE, ControlTrigger.CONCENTRATION_LIMIT_BREACHED): (
        ControlState.ENTRY_DISABLED
    ),
    (ControlState.ACTIVE, ControlTrigger.CONTRADICTORY_TRUTH): ControlState.MANUAL_REQUIRED,
    (ControlState.ACTIVE, ControlTrigger.PARTIAL_FILL_STATE): ControlState.MANUAL_REQUIRED,
    (ControlState.ACTIVE, ControlTrigger.NON_FLAT_STATE): ControlState.CLOSE_ONLY,
    (ControlState.ACTIVE, ControlTrigger.MANUAL_REQUIRED_REQUEST): ControlState.MANUAL_REQUIRED,
    (ControlState.ENTRY_DISABLED, ControlTrigger.RESOLVED): ControlState.ACTIVE,
    (ControlState.MANUAL_REQUIRED, ControlTrigger.RESOLVED): ControlState.ACTIVE,
    (ControlState.CLOSE_ONLY, ControlTrigger.RESOLVED): ControlState.ACTIVE,
}


def next_control_state(current: ControlState, trigger: ControlTrigger) -> ControlState:
    """Return the deterministic next control state for one trigger."""

    if trigger is ControlTrigger.KILL_REQUEST:
        return ControlState.KILL
    if current is ControlState.KILL:
        # KILL is terminal; only an explicit RESOLVED restart leaves it, and that
        # is handled by startup reconciliation, not by an in-run transition.
        if trigger is ControlTrigger.RESOLVED:
            raise _reject(
                RiskReason.CONTROL_STATE_BLOCKS_ENTRY,
                "control_state",
                "KILL is terminal; restart requires startup reconciliation",
            )
        return ControlState.KILL
    key = (current, trigger)
    if key not in _TRANSITIONS:
        # Unknown or unmodeled transitions fail closed to MANUAL_REQUIRED.
        return ControlState.MANUAL_REQUIRED
    return _TRANSITIONS[key]


def entry_allowed(state: ControlState) -> bool:
    """New entries are permitted only in the ACTIVE state."""

    return state is ControlState.ACTIVE


def close_allowed(state: ControlState) -> bool:
    """Close/reconciliation authority survives every state except KILL."""

    return state is not ControlState.KILL
