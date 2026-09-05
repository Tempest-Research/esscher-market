"""The isolated PAPER account risk kernel.

Compilation creates an expression but never a permit. This module binds a
pre-frozen candidate, the active risk-policy hash, the compiled expression,
and broker-observed truth before atomically issuing a one-use permit. It makes
no network, broker, account, order, or real-money mutation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from esscher.autonomy.universe import (
    AllocationDecision,
    AllocationReservation,
    AllocationStatus,
    DefinedRiskOpportunity,
    PortfolioState,
    RiskTier,
    UnderlyingExposure,
    allocate_defined_risk,
    defined_risk_opportunity_sha256,
)
from esscher.execution.expression.compiler import (
    CompiledExpression,
    compiled_expression_sha256,
)
from esscher.execution.expression.reasons import ExpressionKind
from esscher.execution.models import (
    DebitVerticalPermit,
    debit_vertical_permit_bytes,
    debit_vertical_permit_id,
)
from esscher.risk.controls import ControlTrigger, entry_allowed, next_control_state
from esscher.risk.exposure import expression_exposure
from esscher.risk.ledger import RiskLedger, V2ReservationReceipt
from esscher.risk.policy import (
    RiskPolicy,
    RiskPolicyV2,
    risk_policy_sha256,
    risk_policy_v2_sha256,
)
from esscher.risk.reasons import ControlState, RiskReason, RiskRejected, _reject
from esscher.risk.snapshots import (
    AccountSnapshot,
    AccountTruthSource,
    OrderSnapshot,
    PositionSnapshot,
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


@dataclass(frozen=True, slots=True)
class RiskApprovalV2:
    """One V2 allocation whose exact permit and reservation committed together."""

    event_id: str
    candidate_id: str
    opportunity_id: str
    opportunity_sha256: str
    reservation_id: str
    allocation_reservation_id: str
    risk_tier: RiskTier
    quantity: int
    max_loss: Decimal
    account_equity: Decimal
    account_cash: Decimal
    policy_sha256: str
    permit_id: str
    permit_sha256: str
    control_state: ControlState


@dataclass(frozen=True, slots=True)
class RiskAbstentionV2:
    """A valid V2 request with no current cash/debit capacity to reserve."""

    event_id: str
    candidate_id: str
    opportunity_id: str
    account_equity: Decimal
    account_cash: Decimal
    allocation: AllocationDecision
    control_state: ControlState

    @property
    def reason_codes(self) -> tuple[object, ...]:
        """Expose pure-allocation abstention evidence without inventing a permit."""

        return self.allocation.reason_codes


@dataclass(frozen=True, slots=True)
class RiskAllocationPreviewV2:
    """A current-truth allocation calculation that cannot authorize an entry.

    This receipt deliberately has no candidate, permit, reservation, or order
    identity. Authorization must re-read truth and atomically allocate again.
    """

    authority: str
    opportunity_id: str
    opportunity_sha256: str
    expression_sha256: str
    policy_sha256: str
    account_equity: Decimal
    account_cash: Decimal
    control_state: ControlState
    allocation: AllocationDecision

    def __post_init__(self) -> None:
        if self.authority != "NON_AUTHORITATIVE_PREVIEW":
            raise ValueError("V2 allocation previews have no authorization authority")
        if not isinstance(self.opportunity_id, str) or not self.opportunity_id:
            raise ValueError("V2 allocation preview requires an opportunity identity")
        for value in (self.opportunity_sha256, self.expression_sha256, self.policy_sha256):
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError("V2 allocation preview hashes must be exact SHA-256 identities")
        if not isinstance(self.account_equity, Decimal) or not isinstance(
            self.account_cash, Decimal
        ):
            raise ValueError("V2 allocation preview requires Decimal account truth")
        if not isinstance(self.control_state, ControlState):
            raise ValueError("V2 allocation preview requires a closed control state")
        if not isinstance(self.allocation, AllocationDecision):
            raise ValueError("V2 allocation preview requires a typed pure allocation")


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


def _permit_sha256(permit: DebitVerticalPermit) -> str:
    try:
        return hashlib.sha256(debit_vertical_permit_bytes(permit)).hexdigest()
    except (ArithmeticError, AttributeError, TypeError, ValueError) as error:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "permit",
            f"cannot derive canonical permit bytes: {error}",
        ) from None


def _validate_permit_binding(
    *,
    permit: DebitVerticalPermit,
    compiled: CompiledExpression,
    active_policy_sha256: str,
    now: datetime,
) -> None:
    """Prove the risk request carries the one canonical compiled permit identity."""

    if not isinstance(permit, DebitVerticalPermit):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "permit", "must be a DebitVerticalPermit")
    if compiled.expression_kind is not ExpressionKind.DEBIT_VERTICAL:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "compiled.expression_kind",
            "only a debit vertical may receive a debit-vertical permit",
        )
    if permit.permit_id != debit_vertical_permit_id(permit):
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "permit.permit_id",
            "must equal the canonical permit identity",
        )
    if permit.issued_at > now or permit.expires_at <= now:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT, "permit.timing", "permit is not active at authorization"
        )
    if (
        permit.event_run_id != compiled.event_id
        or permit.decision_sha256 != compiled.decision_sha256
        or permit.snapshot_sha256 != compiled.snapshot_sha256
        or permit.policy_sha256 != active_policy_sha256
    ):
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "permit.lineage",
            "permit does not bind the active expression, event, and risk policy",
        )
    block = compiled.debit_vertical
    if not isinstance(block, Mapping):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "compiled.debit_vertical", "must be an object")
    long = block.get("long_leg")
    short = block.get("short_leg")
    expected = {
        "underlying": permit.underlying,
        "vertical_type": permit.vertical_type.value,
        "quantity": permit.quantity,
        "limit_price": str(permit.limit_price),
        "long_symbol": permit.legs[0].symbol,
        "long_option_type": permit.legs[0].option_type.value,
        "long_strike": str(permit.legs[0].strike),
        "short_symbol": permit.legs[1].symbol,
        "short_option_type": permit.legs[1].option_type.value,
        "short_strike": str(permit.legs[1].strike),
    }
    observed = {
        "underlying": block.get("underlying"),
        "vertical_type": block.get("vertical_type"),
        "quantity": block.get("quantity"),
        "limit_price": block.get("limit_price"),
        "long_symbol": long.get("symbol") if isinstance(long, Mapping) else None,
        "long_option_type": long.get("option_type") if isinstance(long, Mapping) else None,
        "long_strike": long.get("strike") if isinstance(long, Mapping) else None,
        "short_symbol": short.get("symbol") if isinstance(short, Mapping) else None,
        "short_option_type": short.get("option_type") if isinstance(short, Mapping) else None,
        "short_strike": short.get("strike") if isinstance(short, Mapping) else None,
    }
    if observed != expected:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "permit.expression",
            (
                "permit legs, price, quantity, or vertical geometry differ "
                "from the compiled expression"
            ),
        )
    if permit.maximum_loss != expression_exposure(compiled):
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "permit.maximum_loss",
            "permit loss differs from compiled expression exposure",
        )


class RiskKernel:
    """Authorizes only PAPER entries against durable, broker-observed truth."""

    def __init__(
        self, policy: RiskPolicy | RiskPolicyV2, ledger: RiskLedger, truth: AccountTruthSource
    ) -> None:
        self._policy = policy
        self._ledger = ledger
        self._truth = truth

    @property
    def ledger(self) -> RiskLedger:
        """Expose the same durable authority used to issue the approval."""

        return self._ledger

    @property
    def policy_sha256(self) -> str:
        """Return the exact active policy identity a permit must bind."""

        if isinstance(self._policy, RiskPolicyV2):
            return risk_policy_v2_sha256(self._policy)
        return risk_policy_sha256(self._policy)

    def _v2_policy(self) -> RiskPolicyV2:
        if not isinstance(self._policy, RiskPolicyV2):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "risk_policy",
                "V2 authorization requires a RiskPolicyV2",
            )
        if not self._policy.constants_verified:
            raise _reject(
                RiskReason.POLICY_UNVERIFIED_CONSTANT,
                "risk_policy",
                "owner-policy and constants-source hashes must bind packaged policy bytes",
            )
        return self._policy

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
            policy_sha256=self.policy_sha256,
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
        active_policy = self.policy_sha256
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

    def _v2_fresh_truth(
        self, *, now: datetime, policy: RiskPolicyV2
    ) -> tuple[AccountSnapshot, tuple[PositionSnapshot, ...], tuple[OrderSnapshot, ...]]:
        """Read every V2 truth surface once before the local SQLite transaction."""

        try:
            broker_clock = _require_utc(self._truth.broker_clock(), "broker_clock")
            skew = abs((broker_clock - now).total_seconds())
            if skew > policy.truth_max_age_seconds:
                raise _reject(
                    RiskReason.STALE_CLOCK,
                    "broker_clock",
                    f"broker clock skew {skew:.0f}s exceeds {policy.truth_max_age_seconds}s",
                )
            account = validate_account_freshness(
                self._truth.account(), now=now, max_age_seconds=policy.truth_max_age_seconds
            )
            positions = validate_positions_freshness(
                self._truth.positions(), now=now, max_age_seconds=policy.truth_max_age_seconds
            )
            orders = validate_orders_freshness(
                self._truth.orders(), now=now, max_age_seconds=policy.truth_max_age_seconds
            )
            if account.cash is None:
                raise _reject(
                    RiskReason.CONTRADICTORY_TRUTH,
                    "account.cash",
                    "V2 requires a fresh explicit unborrowed-cash snapshot",
                )
            if (
                not account.equity.is_finite()
                or account.equity <= 0
                or not account.cash.is_finite()
                or account.cash < 0
                or account.cash > account.equity
            ):
                raise _reject(
                    RiskReason.CONTRADICTORY_TRUTH,
                    "account",
                    "equity/cash must be finite, positive-equity, and unborrowed",
                )
        except RiskRejected as error:
            self._apply_trigger(self._failure_trigger(error), now=now)
            raise
        except (ArithmeticError, AttributeError, TypeError, ValueError) as error:
            self._apply_trigger(ControlTrigger.CONTRADICTORY_TRUTH, now=now)
            raise _reject(RiskReason.CONTRADICTORY_TRUTH, "truth", str(error)) from None
        return account, positions, orders

    @staticmethod
    def _v2_row_amount(row: Mapping[str, object], field: str) -> Decimal:
        value = row.get(field)
        if not isinstance(value, str):
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                f"v2_reservations.{field}",
                "durable amount must be canonical decimal text",
            )
        try:
            amount = Decimal(value)
        except (ArithmeticError, ValueError) as error:
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                f"v2_reservations.{field}",
                f"durable amount is malformed: {error}",
            ) from None
        if not amount.is_finite() or amount < 0 or format(amount.normalize(), "f") != value:
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                f"v2_reservations.{field}",
                "durable amount is non-finite, negative, or non-canonical",
            )
        return amount

    def _v2_portfolio(
        self,
        *,
        account: AccountSnapshot,
        positions: tuple[PositionSnapshot, ...],
        orders: tuple[OrderSnapshot, ...],
        policy_sha256: str,
    ) -> tuple[PortfolioState, tuple[AllocationReservation, ...]]:
        """Build allocation truth from consumed rows plus only pending reservations.

        A consumed debit remains open exposure but is already reflected in broker
        cash; a reserved debit is absent from broker cash and is therefore passed
        only as a pending ``AllocationReservation``.
        """

        if self._ledger.has_non_v2_open_reservations():
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                "reservations",
                "an open reservation has no V2 opportunity/allocation binding",
            )
        consumed_total = Decimal("0")
        consumed_by_underlying: dict[str, Decimal] = {}
        reservations: list[AllocationReservation] = []
        known_consumed_underlyings: set[str] = set()
        for row in self._ledger.v2_open_reservation_rows():
            state = row.get("state")
            permit_state = row.get("permit_state")
            row_policy = row.get("policy_sha256")
            if row_policy != policy_sha256:
                raise _reject(
                    RiskReason.UNKNOWN_EXPOSURE,
                    "v2_reservations.policy_sha256",
                    "open V2 exposure is bound to a different owner policy",
                )
            amount = self._v2_row_amount(row, "amount")
            if amount <= 0:
                raise _reject(
                    RiskReason.UNKNOWN_EXPOSURE,
                    "v2_reservations.amount",
                    "open debit must be positive",
                )
            underlying = _underlying(row.get("underlying"), "v2_reservations.underlying")
            if state == "CONSUMED":
                if permit_state != "FILLED":
                    raise _reject(
                        RiskReason.UNKNOWN_EXPOSURE,
                        "v2_reservations.permit_state",
                        "consumed debit lacks broker-confirmed FILLED permit state",
                    )
                consumed_total += amount
                consumed_by_underlying[underlying] = (
                    consumed_by_underlying.get(underlying, Decimal("0")) + amount
                )
                known_consumed_underlyings.add(underlying)
                continue
            if state != "RESERVED" or permit_state not in {"ISSUED", "SUBMITTED"}:
                raise _reject(
                    RiskReason.UNKNOWN_EXPOSURE,
                    "v2_reservations.state",
                    "open V2 state is not an attributable pending or consumed debit",
                )
            quantity = row.get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                raise _reject(
                    RiskReason.UNKNOWN_EXPOSURE,
                    "v2_reservations.quantity",
                    "pending quantity must be a positive integer",
                )
            try:
                reservations.append(
                    AllocationReservation(
                        opportunity_id=_identifier(
                            row.get("opportunity_id"), "v2_reservations.opportunity_id"
                        ),
                        opportunity_sha256=_identifier(
                            row.get("opportunity_sha256"), "v2_reservations.opportunity_sha256"
                        ),
                        reservation_id=_identifier(
                            row.get("allocation_reservation_id"),
                            "v2_reservations.allocation_reservation_id",
                        ),
                        underlying=underlying,
                        quantity=quantity,
                        max_loss=amount,
                    )
                )
            except ValueError as error:
                raise _reject(
                    RiskReason.UNKNOWN_EXPOSURE,
                    "v2_reservations",
                    f"pending V2 row cannot become an allocation reservation: {error}",
                ) from None

        unknown_positions = [
            position.underlying
            for position in positions
            if position.quantity != Decimal("0")
            and position.underlying not in known_consumed_underlyings
        ]
        if unknown_positions:
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                "positions",
                "broker position has no consumed V2 debit attribution",
            )
        known_order_ids = self._ledger.v2_submitted_order_ids()
        terminal_order_states = frozenset(
            {"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
        )
        for order in orders:
            if order.is_partial_fill:
                raise _reject(
                    RiskReason.PARTIAL_FILL_STATE,
                    "orders",
                    "a partial fill leaves V2 debit exposure ambiguous",
                )
            if order.status not in terminal_order_states and order.order_id not in known_order_ids:
                raise _reject(
                    RiskReason.UNKNOWN_EXPOSURE,
                    "orders",
                    "open broker order has no V2 permit/submission attribution",
                )
        try:
            portfolio = PortfolioState(
                equity=account.equity,
                cash=account.cash if account.cash is not None else Decimal("0"),
                open_debit=consumed_total,
                exposures=tuple(
                    UnderlyingExposure(underlying=underlying, open_debit=amount)
                    for underlying, amount in sorted(consumed_by_underlying.items())
                ),
            )
        except ValueError as error:
            raise _reject(
                RiskReason.CONTRADICTORY_TRUTH,
                "portfolio",
                f"fresh cash/equity and consumed debit state conflict: {error}",
            ) from None
        return portfolio, tuple(reservations)

    # -- pre-permit gate -----------------------------------------------------

    def preview_allocation_v2(
        self,
        *,
        opportunity: DefinedRiskOpportunity,
        compiled: CompiledExpression,
        now: datetime,
    ) -> RiskAllocationPreviewV2:
        """Calculate the current V2 allocation without creating any entry state.

        Normal previews only read ledger/truth state. Unsafe broker truth and
        drawdown conditions retain the kernel's existing fail-closed control
        transitions; a later authorization always re-reads and re-allocates.
        """

        current = _require_utc(now, "now")
        policy = self._v2_policy()
        if policy.run_mode != "PAPER" or not policy.cash_only or not policy.defined_risk_only:
            raise _reject(
                RiskReason.POLICY_NOT_PAPER_ONLY,
                "risk_policy_v2",
                "V2 previews require cash-backed PAPER defined-risk policy",
            )
        state, state_reason = self._ledger.get_control_state()
        if not entry_allowed(state):
            raise _reject(
                RiskReason.CONTROL_STATE_BLOCKS_ENTRY,
                f"control_state.{state.value}",
                state_reason or "entry is blocked by the current control state",
            )
        if not isinstance(opportunity, DefinedRiskOpportunity):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity",
                "V2 preview requires a DefinedRiskOpportunity",
            )
        if not opportunity.decision_ready:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.decision_ready",
                "unready opportunities cannot reach the V2 allocation preview",
            )
        if not isinstance(compiled, CompiledExpression):
            raise _reject(RiskReason.UNSUPPORTED_INPUT, "compiled", "must be a CompiledExpression")
        if compiled.expression_kind is not ExpressionKind.DEBIT_VERTICAL:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "compiled.expression_kind",
                "V2 preview rejects shares, long options, cash, and non-atomic expressions",
            )
        active_policy = self.policy_sha256
        if compiled.policy_sha256 != active_policy:
            raise _reject(
                RiskReason.POLICY_HASH_MISMATCH,
                "compiled.policy_sha256",
                "compiled expression is not bound to the active V2 policy",
            )
        compiled_hash = _compiled_hash(compiled)
        compiled_symbol = _compiled_underlying(compiled)
        if opportunity.decision_id != compiled.decision_sha256:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.decision_id",
                "opportunity decision identity differs from the compiled decision hash",
            )
        if opportunity.expression_id != compiled_hash:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.expression_id",
                "opportunity expression identity differs from the compiled expression hash",
            )
        if opportunity.underlying != compiled_symbol:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.underlying",
                "opportunity underlying differs from the compiled debit vertical",
            )
        if opportunity.risk_tier.fraction not in policy.risk_tiers:
            raise _reject(
                RiskReason.POLICY_UNVERIFIED_CONSTANT,
                "opportunity.risk_tier",
                "opportunity tier is not an exact owner-approved V2 tier",
            )
        account, positions, orders = self._v2_fresh_truth(now=current, policy=policy)
        assert account.cash is not None
        if account.equity <= policy.starting_equity * policy.emergency_drawdown_freeze_fraction:
            self._apply_trigger(ControlTrigger.DRAWDOWN_LIMIT_BREACHED, now=current)
            raise _reject(
                RiskReason.DRAWDOWN_LIMIT_BREACHED,
                "account.equity",
                "current equity is at or below the V2 50% starting-equity freeze threshold",
            )
        try:
            portfolio, existing_reservations = self._v2_portfolio(
                account=account,
                positions=positions,
                orders=orders,
                policy_sha256=active_policy,
            )
        except RiskRejected as error:
            self._apply_trigger(
                ControlTrigger.PARTIAL_FILL_STATE
                if error.reason is RiskReason.PARTIAL_FILL_STATE
                else ControlTrigger.CONTRADICTORY_TRUTH,
                now=current,
            )
            raise
        try:
            allocation = allocate_defined_risk(
                portfolio,
                opportunity,
                existing_reservations=existing_reservations,
            )
        except (ArithmeticError, AttributeError, TypeError, ValueError) as error:
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                "allocation",
                f"pure defined-risk allocation could not be evaluated: {error}",
            ) from None
        return RiskAllocationPreviewV2(
            authority="NON_AUTHORITATIVE_PREVIEW",
            opportunity_id=opportunity.opportunity_id,
            opportunity_sha256=defined_risk_opportunity_sha256(opportunity),
            expression_sha256=compiled_hash,
            policy_sha256=active_policy,
            account_equity=account.equity,
            account_cash=account.cash,
            control_state=state,
            allocation=allocation,
        )

    def authorize_entry(
        self,
        *,
        event_id: str,
        underlying: str,
        candidate_id: str,
        compiled: CompiledExpression,
        permit: DebitVerticalPermit,
        now: datetime,
    ) -> RiskApproval:
        """Atomically issue one permit or reject without an external mutation."""

        current = _require_utc(now, "now")
        policy = self._policy
        if not isinstance(policy, RiskPolicy):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "risk_policy",
                "V1 authorization requires a RiskPolicy",
            )
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
        _validate_permit_binding(
            permit=permit,
            compiled=compiled,
            active_policy_sha256=active_policy,
            now=current,
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

        reservation_id, issued_permit_id, permit_sha256 = self._ledger.reserve_and_issue_permit(
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
            permit=permit,
            now=current,
        )
        expected_permit_sha256 = _permit_sha256(permit)
        if issued_permit_id != permit.permit_id or permit_sha256 != expected_permit_sha256:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "risk_approval.permit",
                "ledger receipt does not preserve the exact canonical permit identity",
            )
        return RiskApproval(
            event_id=event,
            candidate_id=candidate_id,
            reservation_id=reservation_id,
            permit_id=issued_permit_id,
            permit_sha256=permit_sha256,
            exposure=exposure,
            control_state=state,
        )

    def authorize_entry_v2(
        self,
        *,
        event_id: str,
        candidate_id: str,
        opportunity: DefinedRiskOpportunity,
        compiled: CompiledExpression,
        permit: DebitVerticalPermit,
        now: datetime,
    ) -> RiskApprovalV2 | RiskAbstentionV2:
        """Atomically authorize one exact V2 defined-risk allocation or abstain.

        The pure allocation receives current equity/cash, broker-confirmed
        ``CONSUMED`` debit, and only pending ``RESERVED`` debit. Its exact output
        must equal the compiled debit vertical and permit before SQLite performs
        the serializable local reservation transaction.
        """

        current = _require_utc(now, "now")
        policy = self._v2_policy()
        if policy.run_mode != "PAPER" or not policy.cash_only or not policy.defined_risk_only:
            raise _reject(
                RiskReason.POLICY_NOT_PAPER_ONLY,
                "risk_policy_v2",
                "V2 permits only cash-backed PAPER defined-risk debit verticals",
            )
        state, state_reason = self._ledger.get_control_state()
        if not entry_allowed(state):
            raise _reject(
                RiskReason.CONTROL_STATE_BLOCKS_ENTRY,
                f"control_state.{state.value}",
                state_reason or "entry is blocked by the current control state",
            )
        if not isinstance(opportunity, DefinedRiskOpportunity):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity",
                "V2 requires a DefinedRiskOpportunity",
            )
        if not opportunity.decision_ready:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.decision_ready",
                "unready opportunities cannot reach the V2 permit path",
            )
        if not isinstance(compiled, CompiledExpression):
            raise _reject(RiskReason.UNSUPPORTED_INPUT, "compiled", "must be a CompiledExpression")
        if compiled.expression_kind is not ExpressionKind.DEBIT_VERTICAL:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "compiled.expression_kind",
                "V2 rejects shares, long options, cash, and non-atomic expressions",
            )
        active_policy = self.policy_sha256
        if compiled.policy_sha256 != active_policy:
            raise _reject(
                RiskReason.POLICY_HASH_MISMATCH,
                "compiled.policy_sha256",
                "compiled expression is not bound to the active V2 policy",
            )
        event = _identifier(event_id, "event_id")
        candidate = _identifier(candidate_id, "candidate_id")
        compiled_hash = _compiled_hash(compiled)
        compiled_symbol = _compiled_underlying(compiled)
        if event != _identifier(compiled.event_id, "compiled.event_id"):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "event_id",
                "caller event differs from the compiled expression",
            )
        if opportunity.decision_id != compiled.decision_sha256:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.decision_id",
                "opportunity decision identity differs from the compiled decision hash",
            )
        if opportunity.expression_id != compiled_hash:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.expression_id",
                "opportunity expression identity differs from the compiled expression hash",
            )
        if opportunity.underlying != compiled_symbol:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.underlying",
                "opportunity underlying differs from the compiled debit vertical",
            )
        if opportunity.risk_tier.fraction not in policy.risk_tiers:
            raise _reject(
                RiskReason.POLICY_UNVERIFIED_CONSTANT,
                "opportunity.risk_tier",
                "opportunity tier is not an exact owner-approved V2 tier",
            )
        event, symbol, active_policy = self._validate_candidate_binding(
            event_id=event,
            underlying=compiled_symbol,
            candidate_id=candidate,
            compiled=compiled,
        )
        _validate_permit_binding(
            permit=permit,
            compiled=compiled,
            active_policy_sha256=active_policy,
            now=current,
        )
        block = compiled.debit_vertical
        quantity = block.get("quantity") if isinstance(block, Mapping) else None
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "compiled.debit_vertical.quantity",
                "V2 requires an exact positive compiled vertical quantity",
            )
        try:
            max_loss = expression_exposure(compiled)
        except RiskRejected:
            raise
        except (ArithmeticError, AttributeError, TypeError, ValueError) as error:
            raise _reject(
                RiskReason.EXPOSURE_NOT_CALCULABLE,
                "compiled.debit_vertical",
                str(error),
            ) from None
        if not max_loss.is_finite() or max_loss <= 0:
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                "compiled.debit_vertical",
                "compiled maximum loss must be finite and positive",
            )
        if opportunity.max_debit_per_contract * quantity != max_loss:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "opportunity.max_debit_per_contract",
                "opportunity debit, compiled quantity, and maximum loss differ",
            )
        if permit.quantity != quantity or permit.maximum_loss != max_loss:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "permit",
                "permit quantity or maximum loss differs from compiled allocation terms",
            )

        account, positions, orders = self._v2_fresh_truth(now=current, policy=policy)
        assert account.cash is not None
        if account.equity <= policy.starting_equity * policy.emergency_drawdown_freeze_fraction:
            self._apply_trigger(ControlTrigger.DRAWDOWN_LIMIT_BREACHED, now=current)
            raise _reject(
                RiskReason.DRAWDOWN_LIMIT_BREACHED,
                "account.equity",
                "current equity is at or below the V2 50% starting-equity freeze threshold",
            )
        try:
            portfolio, existing_reservations = self._v2_portfolio(
                account=account,
                positions=positions,
                orders=orders,
                policy_sha256=active_policy,
            )
        except RiskRejected as error:
            trigger = (
                ControlTrigger.PARTIAL_FILL_STATE
                if error.reason is RiskReason.PARTIAL_FILL_STATE
                else ControlTrigger.CONTRADICTORY_TRUTH
            )
            self._apply_trigger(trigger, now=current)
            raise
        try:
            allocation = allocate_defined_risk(
                portfolio,
                opportunity,
                existing_reservations=existing_reservations,
            )
        except (ArithmeticError, AttributeError, TypeError, ValueError) as error:
            raise _reject(
                RiskReason.UNKNOWN_EXPOSURE,
                "allocation",
                f"pure defined-risk allocation could not be evaluated: {error}",
            ) from None
        if allocation.status is AllocationStatus.ABSTAINED:
            return RiskAbstentionV2(
                event_id=event,
                candidate_id=candidate,
                opportunity_id=opportunity.opportunity_id,
                account_equity=account.equity,
                account_cash=account.cash,
                allocation=allocation,
                control_state=state,
            )
        if (
            allocation.status is not AllocationStatus.ALLOCATED
            or allocation.reservation_id is None
            or allocation.quantity != quantity
            or allocation.max_loss != max_loss
        ):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "allocation",
                "allocated quantity, maximum loss, or reservation identity differs from expression",
            )
        opportunity_sha256 = defined_risk_opportunity_sha256(opportunity)
        receipt: V2ReservationReceipt = self._ledger.reserve_v2_and_issue_permit(
            event_id=event,
            candidate_id=candidate,
            underlying=symbol,
            policy_sha256=active_policy,
            decision_sha256=compiled.decision_sha256,
            expression_sha256=compiled_hash,
            opportunity_id=opportunity.opportunity_id,
            opportunity_sha256=opportunity_sha256,
            allocation_reservation_id=allocation.reservation_id,
            risk_tier=opportunity.risk_tier.fraction,
            quantity=allocation.quantity,
            amount=allocation.max_loss,
            account_equity=account.equity,
            account_cash=account.cash,
            max_per_underlying_fraction=policy.max_per_underlying_open_debit_fraction,
            max_aggregate_fraction=policy.max_aggregate_open_debit_fraction,
            permit=permit,
            now=current,
        )
        expected_permit_sha256 = _permit_sha256(permit)
        if (
            receipt.reservation_id != allocation.reservation_id
            or receipt.allocation_reservation_id != allocation.reservation_id
            or receipt.opportunity_id != opportunity.opportunity_id
            or receipt.opportunity_sha256 != opportunity_sha256
            or receipt.risk_tier != opportunity.risk_tier.fraction
            or receipt.quantity != allocation.quantity
            or receipt.amount != allocation.max_loss
            or receipt.account_equity != account.equity
            or receipt.account_cash != account.cash
            or receipt.policy_sha256 != active_policy
            or receipt.permit_id != permit.permit_id
            or receipt.permit_sha256 != expected_permit_sha256
        ):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "v2_authorization.receipt",
                "transaction receipt does not preserve every exact allocation/permit binding",
            )
        return RiskApprovalV2(
            event_id=event,
            candidate_id=candidate,
            opportunity_id=opportunity.opportunity_id,
            opportunity_sha256=opportunity_sha256,
            reservation_id=receipt.reservation_id,
            allocation_reservation_id=receipt.allocation_reservation_id,
            risk_tier=opportunity.risk_tier,
            quantity=receipt.quantity,
            max_loss=receipt.amount,
            account_equity=receipt.account_equity,
            account_cash=receipt.account_cash,
            policy_sha256=receipt.policy_sha256,
            permit_id=receipt.permit_id,
            permit_sha256=receipt.permit_sha256,
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
