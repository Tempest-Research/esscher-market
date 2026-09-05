"""Deterministic risk control-state machine.

Stale, contradictory, unknown, partial-fill, or non-flat truth moves the
kernel into ``ENTRY_DISABLED``, ``CLOSE_ONLY``, or ``MANUAL_REQUIRED``. A
``MANUAL_REQUIRED`` or disabled state blocks new entries while preserving
close/reconciliation authority. ``KILL`` is terminal. No transition grants new
authority except an explicit successful startup reconciliation.
"""

from __future__ import annotations

from enum import StrEnum

from esscher.risk.reasons import ControlState, RiskReason, _reject


class ControlTrigger(StrEnum):
    """Events that drive control-state transitions."""

    DRAWDOWN_LIMIT_BREACHED = "DRAWDOWN_LIMIT_BREACHED"
    DAILY_LOSS_LIMIT_BREACHED = "DAILY_LOSS_LIMIT_BREACHED"
    STALE_TRUTH = "STALE_TRUTH"
    CONTRADICTORY_TRUTH = "CONTRADICTORY_TRUTH"
    PARTIAL_FILL_STATE = "PARTIAL_FILL_STATE"
    NON_FLAT_STATE = "NON_FLAT_STATE"
    CLOSE_ONLY_EQUITY_THRESHOLD = "CLOSE_ONLY_EQUITY_THRESHOLD"
    CONCENTRATION_LIMIT_BREACHED = "CONCENTRATION_LIMIT_BREACHED"
    KILL_REQUEST = "KILL_REQUEST"
    MANUAL_REQUIRED_REQUEST = "MANUAL_REQUIRED_REQUEST"
    RESOLVED = "RESOLVED"


_TRANSITIONS: dict[tuple[ControlState, ControlTrigger], ControlState] = {
    (ControlState.ACTIVE, ControlTrigger.DRAWDOWN_LIMIT_BREACHED): ControlState.ENTRY_DISABLED,
    (ControlState.ACTIVE, ControlTrigger.DAILY_LOSS_LIMIT_BREACHED): ControlState.ENTRY_DISABLED,
    (ControlState.ACTIVE, ControlTrigger.STALE_TRUTH): ControlState.ENTRY_DISABLED,
    (ControlState.ACTIVE, ControlTrigger.CONCENTRATION_LIMIT_BREACHED): ControlState.ENTRY_DISABLED,
    (ControlState.ACTIVE, ControlTrigger.CONTRADICTORY_TRUTH): ControlState.MANUAL_REQUIRED,
    (ControlState.ACTIVE, ControlTrigger.PARTIAL_FILL_STATE): ControlState.MANUAL_REQUIRED,
    (ControlState.ACTIVE, ControlTrigger.NON_FLAT_STATE): ControlState.CLOSE_ONLY,
    (ControlState.ACTIVE, ControlTrigger.CLOSE_ONLY_EQUITY_THRESHOLD): ControlState.CLOSE_ONLY,
    (ControlState.ACTIVE, ControlTrigger.MANUAL_REQUIRED_REQUEST): ControlState.MANUAL_REQUIRED,
    (ControlState.ENTRY_DISABLED, ControlTrigger.STALE_TRUTH): ControlState.ENTRY_DISABLED,
    (ControlState.ENTRY_DISABLED, ControlTrigger.CONTRADICTORY_TRUTH): ControlState.MANUAL_REQUIRED,
    (ControlState.ENTRY_DISABLED, ControlTrigger.PARTIAL_FILL_STATE): ControlState.MANUAL_REQUIRED,
    (ControlState.ENTRY_DISABLED, ControlTrigger.NON_FLAT_STATE): ControlState.CLOSE_ONLY,
    (ControlState.ENTRY_DISABLED, ControlTrigger.RESOLVED): ControlState.ACTIVE,
    (ControlState.MANUAL_REQUIRED, ControlTrigger.STALE_TRUTH): ControlState.ENTRY_DISABLED,
    (
        ControlState.MANUAL_REQUIRED,
        ControlTrigger.CONTRADICTORY_TRUTH,
    ): ControlState.MANUAL_REQUIRED,
    (ControlState.MANUAL_REQUIRED, ControlTrigger.PARTIAL_FILL_STATE): ControlState.MANUAL_REQUIRED,
    (ControlState.MANUAL_REQUIRED, ControlTrigger.NON_FLAT_STATE): ControlState.CLOSE_ONLY,
    (ControlState.MANUAL_REQUIRED, ControlTrigger.RESOLVED): ControlState.ACTIVE,
    (ControlState.CLOSE_ONLY, ControlTrigger.STALE_TRUTH): ControlState.ENTRY_DISABLED,
    (ControlState.CLOSE_ONLY, ControlTrigger.CONTRADICTORY_TRUTH): ControlState.MANUAL_REQUIRED,
    (ControlState.CLOSE_ONLY, ControlTrigger.PARTIAL_FILL_STATE): ControlState.MANUAL_REQUIRED,
    (ControlState.CLOSE_ONLY, ControlTrigger.NON_FLAT_STATE): ControlState.CLOSE_ONLY,
    (ControlState.CLOSE_ONLY, ControlTrigger.RESOLVED): ControlState.ACTIVE,
}


def next_control_state(current: ControlState, trigger: ControlTrigger) -> ControlState:
    """Return the deterministic next control state for one trigger."""

    if trigger is ControlTrigger.KILL_REQUEST:
        return ControlState.KILL
    if current is ControlState.KILL:
        if trigger is ControlTrigger.RESOLVED:
            raise _reject(
                RiskReason.CONTROL_STATE_BLOCKS_ENTRY,
                "control_state",
                "KILL is terminal; restart requires explicit operator action",
            )
        return ControlState.KILL
    return _TRANSITIONS.get((current, trigger), ControlState.MANUAL_REQUIRED)


def entry_allowed(state: ControlState) -> bool:
    """New entries are permitted only in the ACTIVE state."""

    return state is ControlState.ACTIVE


def close_allowed(state: ControlState) -> bool:
    """Close/reconciliation authority survives every state except KILL."""

    return state is not ControlState.KILL
