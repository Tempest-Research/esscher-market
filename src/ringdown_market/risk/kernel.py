"""The isolated PAPER account risk kernel.

Compilation creates an expression but never a permit. This module binds a
pre-frozen candidate, the active risk-policy hash, the compiled expression,
and broker-observed truth before atomically issuing a one-use permit. It makes
no network, broker, account, order, or real-money mutation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ringdown_market.execution.expression.compiler import (
    CompiledExpression,
    compiled_expression_sha256,
)
from ringdown_market.execution.expression.reasons import ExpressionKind
from ringdown_market.risk.controls import ControlTrigger, entry_allowed, next_control_state
from ringdown_market.risk.exposure import expression_exposure
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.risk.policy import RiskPolicy, risk_policy_sha256
from ringdown_market.risk.reasons import ControlState, RiskReason, RiskRejected, _reject
from ringdown_market.risk.snapshots import (
    AccountTruthSource,
    OrderSnapshot,
    _require_utc,
    validate_account_freshness,
    validate_orders_freshness,
    validate_positions_freshness,
)

_STALE_REASONS = frozenset(
    {
        RiskReason.STALE_ACCOUNT_TRUTH,
        RiskReason.STALE_POSITION_TRUTH,
        RiskReason.STALE_ORDER_TRUTH,
        RiskReason.STALE_CLOCK,
    }
)


@dataclass(frozen=True, slots=True)
class RiskApproval:
    """One persisted reservation and one-use permit authorization."""

    event_id: str
    candidate_id: str
    reservation_id: str
    permit_id: str
    permit_sha256: str
    exposure: Decimal
    control_state: ControlState


def _identifier(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be non-empty exact text")
    return value


def _underlying(value: object, path: str) -> str:
    text = _identifier(value, path)
    if text != text.upper():
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be normalized uppercase text")
    return text


def _compiled_hash(compiled: CompiledExpression) -> str:
    try:
        return compiled_expression_sha256(compiled)
    except (ArithmeticError, AttributeError, TypeError, ValueError) as error:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "compiled_expression",
            f"cannot derive canonical expression identity: {error}",
        ) from None


def _compiled_underlying(compiled: CompiledExpression) -> str:
    kind = compiled.expression_kind
    block: Mapping[str, object] | None
    if kind is ExpressionKind.SHARES:
        block = compiled.shares
    elif kind is ExpressionKind.ONE_LONG_OPTION:
        block = compiled.long_option
    elif kind is ExpressionKind.DEBIT_VERTICAL:
        block = compiled.debit_vertical
    else:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT, "compiled.expression_kind", "cannot authorize cash"
        )
    if not isinstance(block, Mapping):
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "compiled.underlying",
            "compiled expression has no position block",
        )
    value = block.get("underlying")
    if value is None and kind is ExpressionKind.SHARES:
        # A share expression's symbol is its underlying identity. Option
        # expressions carry an explicit underlying and never use this fallback.
        value = block.get("symbol")
    if value is None:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "compiled.underlying",
            "compiled expression has no underlying identity",
        )
    return _underlying(value, "compiled.underlying")


class RiskKernel:
    """Authorizes only PAPER entries against durable, broker-observed truth."""

    def __init__(self, policy: RiskPolicy, ledger: RiskLedger, truth: AccountTruthSource) -> None:
        self._policy = policy
        self._ledger = ledger
        self._truth = truth

    # -- state and startup reconciliation -----------------------------------

    def _apply_trigger(self, trigger: ControlTrigger, *, now: datetime) -> ControlState:
        current, _ = self._ledger.get_control_state()
        updated = next_control_state(current, trigger)
        self._ledger.set_control_state_with_passport(
            state=updated,
            reason=trigger.value,
            event_payload={"from": current.value, "to": updated.value, "trigger": trigger.value},
            now=now,
        )
        return updated

    @staticmethod
    def _failure_trigger(error: RiskRejected) -> ControlTrigger:
        return (
            ControlTrigger.STALE_TRUTH
            if error.reason in _STALE_REASONS
            else ControlTrigger.CONTRADICTORY_TRUTH
        )

    def startup_reconciliation(self, *, now: datetime) -> ControlState:
        """Make fresh broker truth the only path from safe boot to ACTIVE."""

        current = _require_utc(now, "now")
        try:
            broker_clock = _require_utc(self._truth.broker_clock(), "broker_clock")
            skew = abs((broker_clock - current).total_seconds())
            if skew > self._policy.truth_max_age_seconds:
                raise _reject(
                    RiskReason.STALE_CLOCK,
                    "broker_clock",
                    f"broker clock skew {skew:.0f}s exceeds {self._policy.truth_max_age_seconds}s",
                )
            account = validate_account_freshness(
                self._truth.account(),
                now=current,
                max_age_seconds=self._policy.truth_max_age_seconds,
            )
            positions = validate_positions_freshness(
                self._truth.positions(),
                now=current,
                max_age_seconds=self._policy.truth_max_age_seconds,
            )
            orders = validate_orders_freshness(
                self._truth.orders(),
                now=current,
                max_age_seconds=self._policy.truth_max_age_seconds,
            )
        except RiskRejected as error:
            return self._apply_trigger(self._failure_trigger(error), now=current)
        except (AttributeError, TypeError) as error:
            _ = error
            return self._apply_trigger(ControlTrigger.CONTRADICTORY_TRUTH, now=current)

        _ = account
        if any(order.is_partial_fill for order in orders):
            return self._apply_trigger(ControlTrigger.PARTIAL_FILL_STATE, now=current)
        if any(position.quantity != Decimal(0) for position in positions):
            return self._apply_trigger(ControlTrigger.NON_FLAT_STATE, now=current)
        state, _ = self._ledger.get_control_state()
        if state is ControlState.KILL or state is ControlState.ACTIVE:
            return state
        return self._apply_trigger(ControlTrigger.RESOLVED, now=current)

    # -- immutable candidate chain ------------------------------------------

    def freeze_candidate(
        self,
        *,
        event_id: str,
        candidate_id: str,
        compiled: CompiledExpression,
        evidence_mode: str,
        now: datetime,
    ) -> None:
        """Freeze the only candidate identity later eligible for authorization."""

        if not isinstance(compiled, CompiledExpression):
            raise _reject(RiskReason.UNSUPPORTED_INPUT, "compiled", "must be a CompiledExpression")
        current = _require_utc(now, "now")
        event = _identifier(event_id, "candidate.event_id")
        candidate = _identifier(candidate_id, "candidate.candidate_id")
        if event != _identifier(compiled.event_id, "compiled.event_id"):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "candidate.event_id",
                "caller and compiled event identities differ",
            )
        self._ledger.freeze_candidate(
            event_id=event,
            candidate_id=candidate,
            policy_sha256=risk_policy_sha256(self._policy),
            decision_sha256=_identifier(compiled.decision_sha256, "compiled.decision_sha256"),
            expression_sha256=_compiled_hash(compiled),
            evidence_mode=_identifier(evidence_mode, "candidate.evidence_mode"),
            now=current,
        )

    def mark_not_run(self, *, event_id: str, reason: str, now: datetime) -> None:
        """Persist an immutable abstention that can never become an entry."""

        self._ledger.mark_not_run(
            event_id=_identifier(event_id, "not_run.event_id"),
            reason=_identifier(reason, "not_run.reason"),
            now=_require_utc(now, "now"),
        )

    def _validate_candidate_binding(
        self,
        *,
        event_id: str,
        underlying: str,
        candidate_id: str,
        compiled: CompiledExpression,
    ) -> tuple[str, str, str]:
        if not isinstance(compiled, CompiledExpression):
            raise _reject(RiskReason.UNSUPPORTED_INPUT, "compiled", "must be a CompiledExpression")
        event = _identifier(event_id, "event_id")
        caller_underlying = _underlying(underlying, "underlying")
        candidate = _identifier(candidate_id, "candidate_id")
        if event != _identifier(compiled.event_id, "compiled.event_id"):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "event_id",
                "caller event differs from compiled event",
            )
        if caller_underlying != _compiled_underlying(compiled):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "underlying",
                "caller underlying differs from compiled underlying",
            )
        if self._ledger.not_run_reason(event) is not None:
            raise _reject(RiskReason.NOT_RUN_EVENT, f"event.{event}", "event is marked NOT_RUN")
        frozen = self._ledger.candidate_for_event(event)
        if frozen is None:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                f"candidate.{event}",
                "a matching immutable candidate was not frozen",
            )
        active_policy = risk_policy_sha256(self._policy)
        if frozen["policy_sha256"] != active_policy:
            raise _reject(
                RiskReason.POLICY_HASH_MISMATCH,
                f"candidate.{event}.policy_sha256",
                "frozen candidate does not bind the active risk policy",
            )
        expected = {
            "candidate_id": candidate,
            "decision_sha256": _identifier(compiled.decision_sha256, "compiled.decision_sha256"),
            "expression_sha256": _compiled_hash(compiled),
        }
        for field, value in expected.items():
            if frozen[field] != value:
                raise _reject(
                    RiskReason.UNSUPPORTED_INPUT,
                    f"candidate.{event}.{field}",
                    "caller identity differs from the immutable candidate",
                )
        return event, caller_underlying, active_policy

    # -- pre-permit gate -----------------------------------------------------

    def authorize_entry(
        self,
        *,
        event_id: str,
        underlying: str,
        candidate_id: str,
        compiled: CompiledExpression,
        now: datetime,
    ) -> RiskApproval:
        """Atomically issue one permit or reject without an external mutation."""

        current = _require_utc(now, "now")
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
        event, symbol, active_policy = self._validate_candidate_binding(
            event_id=event_id,
            underlying=underlying,
            candidate_id=candidate_id,
            compiled=compiled,
        )

        try:
            broker_clock = _require_utc(self._truth.broker_clock(), "broker_clock")
            skew = abs((broker_clock - current).total_seconds())
            if skew > policy.truth_max_age_seconds:
                raise _reject(
                    RiskReason.STALE_CLOCK,
                    "broker_clock",
                    f"broker clock skew {skew:.0f}s exceeds {policy.truth_max_age_seconds}s",
                )
            account = validate_account_freshness(
                self._truth.account(), now=current, max_age_seconds=policy.truth_max_age_seconds
            )
            positions = validate_positions_freshness(
                self._truth.positions(), now=current, max_age_seconds=policy.truth_max_age_seconds
            )
            orders = validate_orders_freshness(
                self._truth.orders(), now=current, max_age_seconds=policy.truth_max_age_seconds
            )
        except RiskRejected as error:
            self._apply_trigger(self._failure_trigger(error), now=current)
            raise
        except (AttributeError, TypeError) as error:
            self._apply_trigger(ControlTrigger.CONTRADICTORY_TRUTH, now=current)
            raise _reject(RiskReason.CONTRADICTORY_TRUTH, "truth", str(error)) from None

        if any(order.is_partial_fill for order in orders):
            self._apply_trigger(ControlTrigger.PARTIAL_FILL_STATE, now=current)
            raise _reject(
                RiskReason.PARTIAL_FILL_STATE,
                "orders",
                "a partial fill leaves an ambiguous state",
            )
        try:
            exposure = expression_exposure(compiled)
        except RiskRejected:
            raise
        except (ArithmeticError, AttributeError, TypeError, ValueError) as error:
            raise _reject(
                RiskReason.EXPOSURE_NOT_CALCULABLE,
                f"exposure.{event}",
                str(error),
            ) from None
        if not exposure.is_finite() or exposure < 0:
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                f"exposure.{event}",
                "exposure is unknown or incalculable",
            )
        if exposure > policy.per_event_loss_budget:
            raise _reject(
                RiskReason.BUDGET_EXCEEDED,
                f"exposure.{event}",
                "exposure exceeds the per-event loss budget",
            )
        if exposure > account.buying_power:
            raise _reject(
                RiskReason.BUDGET_EXCEEDED,
                "buying_power",
                "exposure exceeds verified buying power",
            )

        self._ledger.record_account_snapshot(equity=account.equity, now=current)
        intraday_peak = self._ledger.intraday_peak_equity(now=current)
        daily_loss = intraday_peak - account.equity
        if daily_loss > policy.daily_loss_limit:
            self._apply_trigger(ControlTrigger.DAILY_LOSS_LIMIT_BREACHED, now=current)
            raise _reject(
                RiskReason.DAILY_LOSS_LIMIT_BREACHED,
                "daily_loss",
                "daily loss exceeds the policy limit",
            )
        drawdown = policy.account_capital - account.equity
        if drawdown > policy.drawdown_limit:
            self._apply_trigger(ControlTrigger.DRAWDOWN_LIMIT_BREACHED, now=current)
            raise _reject(
                RiskReason.DRAWDOWN_LIMIT_BREACHED,
                "drawdown",
                "drawdown exceeds the policy limit",
            )
        if account.equity <= policy.close_only_equity_threshold:
            self._apply_trigger(ControlTrigger.CLOSE_ONLY_EQUITY_THRESHOLD, now=current)
            raise _reject(
                RiskReason.CONTROL_STATE_BLOCKS_ENTRY,
                "close_only_equity_threshold",
                "equity is at or below the close-only threshold",
            )
        gross_existing = sum(
            (abs(position.market_value) for position in positions if position.underlying == symbol),
            Decimal(0),
        )
        held_for_underlying = self._ledger.open_reservation_total_for_underlying(symbol)
        if gross_existing + held_for_underlying + exposure > policy.concentration_limit:
            self._apply_trigger(ControlTrigger.CONCENTRATION_LIMIT_BREACHED, now=current)
            raise _reject(
                RiskReason.CONCENTRATION_LIMIT_BREACHED,
                f"concentration.{symbol}",
                "gross absolute concentration exceeds the policy limit",
            )

        reservation_id, permit_id, permit_sha256 = self._ledger.reserve_and_issue_permit(
            event_id=event,
            underlying=symbol,
            candidate_id=candidate_id,
            risk_policy_sha256=active_policy,
            decision_sha256=_identifier(compiled.decision_sha256, "compiled.decision_sha256"),
            expression_sha256=_compiled_hash(compiled),
            amount=exposure,
            aggregate_limit=policy.aggregate_exposure_limit,
            max_open_expressions=policy.max_open_expressions,
            max_entries_per_day=policy.max_entries_per_day,
            now=current,
        )
        return RiskApproval(
            event_id=event,
            candidate_id=candidate_id,
            reservation_id=reservation_id,
            permit_id=permit_id,
            permit_sha256=permit_sha256,
            exposure=exposure,
            control_state=state,
        )

    # -- identity-bound post-trade reconciliation ---------------------------

    def record_submission(
        self, *, event_id: str, permit_id: str, broker_order_id: str, now: datetime
    ) -> None:
        """Record external execution's order identity; this does not submit anything."""

        self._ledger.record_submission(
            event_id=event_id,
            permit_id=permit_id,
            broker_order_id=broker_order_id,
            now=_require_utc(now, "now"),
        )

    def reconcile_fill(
        self,
        *,
        event_id: str,
        permit_id: str,
        fill: OrderSnapshot,
        now: datetime,
    ) -> None:
        """Reconcile only an identity-bound, fresh broker-observed terminal order."""

        current = _require_utc(now, "now")
        if not isinstance(fill, OrderSnapshot):
            raise _reject(RiskReason.UNSUPPORTED_INPUT, "fill", "must be an OrderSnapshot")
        try:
            broker_clock = _require_utc(self._truth.broker_clock(), "broker_clock")
            skew = abs((broker_clock - current).total_seconds())
            if skew > self._policy.truth_max_age_seconds:
                raise _reject(
                    RiskReason.STALE_CLOCK,
                    "broker_clock",
                    "broker clock skew exceeds the policy truth age",
                )
            observed_orders = validate_orders_freshness(
                self._truth.orders(),
                now=current,
                max_age_seconds=self._policy.truth_max_age_seconds,
            )
        except RiskRejected as error:
            self._apply_trigger(self._failure_trigger(error), now=current)
            raise
        if fill not in observed_orders:
            self._apply_trigger(ControlTrigger.CONTRADICTORY_TRUTH, now=current)
            raise _reject(
                RiskReason.CONTRADICTORY_TRUTH,
                "fill",
                "provided fill is absent from fresh broker-observed order truth",
            )
        try:
            self._ledger.reconcile_observed_order(
                event_id=event_id,
                permit_id=permit_id,
                broker_order_id=fill.order_id,
                status=fill.status,
                filled_quantity=fill.filled_quantity,
                observed_at=fill.observed_at,
                now=current,
            )
        except RiskRejected as error:
            if error.reason is RiskReason.PARTIAL_FILL_STATE:
                self._apply_trigger(ControlTrigger.PARTIAL_FILL_STATE, now=current)
            raise

    def reconcile_flat(self, *, event_id: str, permit_id: str, now: datetime) -> None:
        """Release filled exposure only after fresh broker truth proves flatness."""

        current = _require_utc(now, "now")
        reservation = self._ledger.reservation_for_event(event_id)
        if reservation is None:
            raise _reject(
                RiskReason.EVENT_LIFECYCLE_INVALID,
                f"flat.{event_id}",
                "reservation does not exist",
            )
        symbol = _underlying(reservation["underlying"], "reservation.underlying")
        try:
            broker_clock = _require_utc(self._truth.broker_clock(), "broker_clock")
            skew = abs((broker_clock - current).total_seconds())
            if skew > self._policy.truth_max_age_seconds:
                raise _reject(
                    RiskReason.STALE_CLOCK,
                    "broker_clock",
                    "broker clock skew exceeds the policy truth age",
                )
            positions = validate_positions_freshness(
                self._truth.positions(),
                now=current,
                max_age_seconds=self._policy.truth_max_age_seconds,
            )
            orders = validate_orders_freshness(
                self._truth.orders(),
                now=current,
                max_age_seconds=self._policy.truth_max_age_seconds,
            )
        except RiskRejected as error:
            self._apply_trigger(self._failure_trigger(error), now=current)
            raise
        if any(order.is_partial_fill for order in orders):
            self._apply_trigger(ControlTrigger.PARTIAL_FILL_STATE, now=current)
            raise _reject(
                RiskReason.PARTIAL_FILL_STATE, "orders", "partial order prevents flat proof"
            )
        if any(
            position.underlying == symbol and position.quantity != Decimal(0)
            for position in positions
        ):
            self._apply_trigger(ControlTrigger.NON_FLAT_STATE, now=current)
            raise _reject(
                RiskReason.NON_FLAT_STATE,
                f"positions.{symbol}",
                "broker truth is not flat for the reserved underlying",
            )
        self._ledger.release_consumed_after_flat(
            event_id=event_id, permit_id=permit_id, now=current
        )
