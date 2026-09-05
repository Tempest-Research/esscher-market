"""Exposure-aware order/fill/position reducer.

The reducer folds broker order/fill/position truth into lifecycle states
without ever collapsing ambiguity. Partial fills stay partial, unknown or
contradictory terminal states stay unknown, and a non-flat position truth is
never declared flat. Broker acknowledgement is not fill proof: reductions are
driven by read-back truth only.
"""

from __future__ import annotations

from collections.abc import Sequence

from esscher.lifecycle.broker import (
    OPEN_UNFILLED_TERMINAL_STATES,
    OPEN_WORKING_STATES,
    BrokerOrderState,
    BrokerOrderTruth,
    PositionTruth,
)
from esscher.lifecycle.reasons import LifecycleState


def reduce_open_order(truth: BrokerOrderTruth, *, expected_qty: int) -> LifecycleState:
    """Reduce one opening-order readback into a lifecycle state."""

    status = truth.status
    filled = truth.filled_qty
    if status == BrokerOrderState.FILLED:
        if filled == expected_qty:
            return LifecycleState.OPEN_FILLED
        # A fill whose quantity disagrees with the permit is ambiguous.
        return LifecycleState.OPEN_UNKNOWN
    if status == BrokerOrderState.PARTIALLY_FILLED:
        return LifecycleState.OPEN_PARTIAL
    if status in OPEN_UNFILLED_TERMINAL_STATES:
        if filled == 0:
            return LifecycleState.OPEN_CANCELED
        # A terminal order with a nonzero fill cannot be declared canceled.
        return LifecycleState.OPEN_UNKNOWN
    if status in OPEN_WORKING_STATES:
        return LifecycleState.OPEN_SUBMITTED
    return LifecycleState.OPEN_UNKNOWN


def reduce_close_order(
    truth: BrokerOrderTruth,
    positions: Sequence[PositionTruth],
    *,
    expected_qty: int,
) -> LifecycleState:
    """Reduce one closing-order readback plus position truth into a state."""

    status = truth.status
    filled = truth.filled_qty
    residue = tuple(p for p in positions if p.qty != 0)
    if status == BrokerOrderState.PARTIALLY_FILLED:
        return LifecycleState.CLOSE_PARTIAL
    if status == BrokerOrderState.FILLED:
        if filled != expected_qty:
            # Fill quantity disagrees with the close permit: keep it manual.
            return LifecycleState.MANUAL_REQUIRED
        if residue:
            return LifecycleState.MANUAL_REQUIRED
        return LifecycleState.CLOSED_FLAT
    # Any other close outcome leaves exposure ambiguous.
    return LifecycleState.MANUAL_REQUIRED


def opening_exposure_bearing(state: LifecycleState) -> bool:
    """Whether an opening state still bears exposure (blocks new entries)."""

    return state in {
        LifecycleState.OPEN_SUBMITTED,
        LifecycleState.OPEN_PARTIAL,
        LifecycleState.OPEN_FILLED,
        LifecycleState.OPEN_UNKNOWN,
    }


def positions_flat(positions: Sequence[PositionTruth]) -> bool:
    """True only when every observed position has zero quantity."""

    return all(position.qty == 0 for position in positions)
