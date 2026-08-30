"""The PAPER account risk kernel.

Strategy and expression compilation produce a candidate expression, but a
permit is issued only after this kernel approves it. The kernel enforces
account, portfolio, clock, quote, policy, and lifecycle truth before any
reservation, and it reserves before mutation, releasing only after fill/cancel
reconciliation. It cannot be bypassed by compilation: compilation has no
reservation authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ringdown_market.execution.expression.compiler import CompiledExpression
from ringdown_market.risk.controls import ControlTrigger, entry_allowed, next_control_state
from ringdown_market.risk.exposure import expression_exposure
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.risk.passport import PassportEventType
from ringdown_market.risk.policy import RiskPolicy
from ringdown_market.risk.reasons import ControlState, RiskReason, _reject
from ringdown_market.risk.snapshots import (
    AccountTruthSource,
    validate_account_freshness,
    validate_orders_freshness,
    validate_positions_freshness,
)


@dataclass(frozen=True, slots=True)
class RiskApproval:
    """One approved reservation authorizing exactly one permit."""

    event_id: str
    reservation_id: str
    exposure: Decimal
    control_state: ControlState


class RiskKernel:
    """Authorizes PAPER entries against verified truth and frozen budgets."""

    def __init__(
        self,
        policy: RiskPolicy,
        ledger: RiskLedger,
        truth: AccountTruthSource,
    ) -> None:
        self._policy = policy
        self._ledger = ledger
        self._truth = truth

    # -- startup reconciliation ---------------------------------------------

    def startup_reconciliation(self, *, now: datetime) -> ControlState:
        """Reconcile broker truth at startup and return the control state."""

        account = self._truth.account()
        if account is None:
            self._apply_trigger(ControlTrigger.STALE_TRUTH, now=now)
            return self._ledger.get_control_state()[0]
        max_age = self._policy.truth_max_age_seconds
        validate_account_freshness(account, now=now, max_age_seconds=max_age)
        validate_positions_freshness(self._truth.positions(), now=now, max_age_seconds=max_age)
        validate_orders_freshness(self._truth.orders(), now=now, max_age_seconds=max_age)
        state, _ = self._ledger.get_control_state()
        if state is ControlState.KILL:
            return state
        return state

    def _apply_trigger(self, trigger: ControlTrigger, *, now: datetime) -> ControlState:
        current, _ = self._ledger.get_control_state()
        updated = next_control_state(current, trigger)
        if updated is not current:
            self._ledger.set_control_state(state=updated, reason=trigger.value, now=now)
            self._ledger.append_passport(
                event_type=PassportEventType.CONTROL_STATE_CHANGED.value,
                payload={"from": current.value, "to": updated.value, "trigger": trigger.value},
                now=now,
            )
        return updated

    # -- pre-permit gate -----------------------------------------------------

    def authorize_entry(
        self,
        *,
        event_id: str,
        underlying: str,
        compiled: CompiledExpression,
        now: datetime,
    ) -> RiskApproval:
        """Approve one expression or fail closed before any mutation."""

        policy = self._policy
        if not policy.constants_verified:
            raise _reject(
                RiskReason.POLICY_UNVERIFIED_CONSTANT,
                "risk_policy.constants",
                "risk constants are unverified; no fallback is permitted",
            )
        if policy.run_mode != "PAPER":
            raise _reject(
                RiskReason.POLICY_NOT_PAPER_ONLY,
                "risk_policy.run_mode",
                "the risk kernel is permanently PAPER-only",
            )

        state, state_reason = self._ledger.get_control_state()
        if not entry_allowed(state):
            raise _reject(
                RiskReason.CONTROL_STATE_BLOCKS_ENTRY,
                f"control_state.{state.value}",
                state_reason or "entry is blocked by the current control state",
            )

        max_age = policy.truth_max_age_seconds
        broker_clock = self._truth.broker_clock()
        clock_skew = abs((broker_clock - now).total_seconds())
        if clock_skew > max_age:
            raise _reject(
                RiskReason.STALE_CLOCK,
                "broker_clock",
                f"broker clock skew {clock_skew:.0f}s exceeds {max_age}s",
            )

        if self._ledger.entries_today(now=now) >= policy.max_entries_per_day:
            raise _reject(
                RiskReason.ENTRY_COUNT_LIMIT_REACHED,
                "entries_today",
                f"entry count has reached the daily limit {policy.max_entries_per_day}",
            )
        if self._ledger.open_reservation_count() >= policy.max_open_expressions:
            raise _reject(
                RiskReason.EXPRESSION_LIMIT_REACHED,
                "open_expressions",
                f"open expression count has reached the limit {policy.max_open_expressions}",
            )

        account = validate_account_freshness(
            self._truth.account(), now=now, max_age_seconds=max_age
        )
        positions = validate_positions_freshness(
            self._truth.positions(), now=now, max_age_seconds=max_age
        )
        orders = validate_orders_freshness(self._truth.orders(), now=now, max_age_seconds=max_age)

        if account.equity <= policy.close_only_equity_threshold:
            self._apply_trigger(ControlTrigger.CLOSE_ONLY_EQUITY_THRESHOLD, now=now)
            raise _reject(
                RiskReason.CONTROL_STATE_BLOCKS_ENTRY,
                "close_only_equity_threshold",
                f"equity {account.equity} is at or below the close-only threshold",
            )

        self._ledger.record_account_snapshot(equity=account.equity, now=now)
        intraday_peak = self._ledger.intraday_peak_equity(now=now)
        daily_loss = intraday_peak - account.equity
        if daily_loss > policy.daily_loss_limit:
            self._apply_trigger(ControlTrigger.DAILY_LOSS_LIMIT_BREACHED, now=now)
            raise _reject(
                RiskReason.DAILY_LOSS_LIMIT_BREACHED,
                "daily_loss",
                f"daily loss {daily_loss} exceeds the limit {policy.daily_loss_limit}",
            )

        for order in orders:
            if order.is_partial_fill:
                self._apply_trigger(ControlTrigger.PARTIAL_FILL_STATE, now=now)
                raise _reject(
                    RiskReason.PARTIAL_FILL_STATE,
                    f"order.{order.order_id}",
                    "a partial fill leaves an ambiguous state",
                )

        exposure = expression_exposure(compiled)
        if exposure < 0 or not exposure.is_finite():
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                f"exposure.{event_id}",
                "exposure is unknown or incalculable",
            )
        if exposure > policy.per_event_loss_budget:
            raise _reject(
                RiskReason.BUDGET_EXCEEDED,
                f"exposure.{event_id}",
                f"exposure {exposure} exceeds the per-event loss budget",
            )

        open_total = self._ledger.open_reservation_total()
        if open_total + exposure > policy.aggregate_exposure_limit:
            raise _reject(
                RiskReason.BUDGET_EXCEEDED,
                "aggregate_exposure",
                f"aggregate exposure {open_total + exposure} exceeds the limit",
            )

        underlying_key = underlying.strip().upper()
        existing = Decimal(0)
        for position in positions:
            if position.underlying == underlying_key:
                existing += position.market_value
        if existing + exposure > policy.concentration_limit:
            raise _reject(
                RiskReason.CONCENTRATION_LIMIT_BREACHED,
                f"concentration.{underlying_key}",
                f"concentration {existing + exposure} exceeds the limit",
            )

        drawdown = policy.account_capital - account.equity
        if drawdown > policy.drawdown_limit:
            self._apply_trigger(ControlTrigger.DRAWDOWN_LIMIT_BREACHED, now=now)
            raise _reject(
                RiskReason.DRAWDOWN_LIMIT_BREACHED,
                "drawdown",
                f"drawdown {drawdown} exceeds the limit",
            )

        if exposure > account.buying_power:
            raise _reject(
                RiskReason.BUDGET_EXCEEDED,
                "buying_power",
                f"exposure {exposure} exceeds verified buying power",
            )

        reservation_id = self._ledger.reserve(event_id=event_id, amount=exposure, now=now)
        self._ledger.append_passport(
            event_type=PassportEventType.RESERVATION_HELD.value,
            payload={
                "event_id": event_id,
                "reservation_id": reservation_id,
                "exposure": str(exposure),
            },
            now=now,
        )
        return RiskApproval(
            event_id=event_id,
            reservation_id=reservation_id,
            exposure=exposure,
            control_state=state,
        )

    # -- post-trade reconciliation -------------------------------------------

    def reconcile_fill(self, *, event_id: str, fully_filled: bool, now: datetime) -> None:
        """Consume a reservation after a confirmed fill, or release after cancel."""

        if fully_filled:
            self._ledger.consume_reservation(event_id=event_id, now=now)
            self._ledger.append_passport(
                event_type=PassportEventType.RECONCILED.value,
                payload={"event_id": event_id, "result": "FILLED"},
                now=now,
            )
        else:
            self._ledger.release_reservation(event_id=event_id, now=now)
            self._ledger.append_passport(
                event_type=PassportEventType.RESERVATION_RELEASED.value,
                payload={"event_id": event_id, "result": "CANCELLED"},
                now=now,
            )
