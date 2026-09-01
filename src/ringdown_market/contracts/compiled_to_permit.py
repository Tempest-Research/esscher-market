"""Fail-closed bridge from a validated expression package to one PAPER permit.

The strategy engine owns a direction-only decision and Gate D owns an executable
expression.  This bridge is the only path that may turn their joined, immutable
artifacts into a ``DebitVerticalPermit`` for the new vertical pipeline.  It has
no provider, MCP, account, or broker boundary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from ringdown_market.execution.expression import CompiledExpression, ExpressionKind
from ringdown_market.execution.models import (
    DataClass,
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    RunMode,
    VerticalType,
    debit_vertical_permit_bytes,
    debit_vertical_permit_id,
)
from ringdown_market.strategy import DecisionDisposition
from ringdown_market.strategy.contracts import sha256_bytes, strategy_decision_sha256
from ringdown_market.strategy.models import StrategyDecision, StrategyInput

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CompiledPermitRejected(ValueError):
    """Raised before a compiled package can be granted permit identity."""


@dataclass(frozen=True, slots=True)
class PermitBridgeConstants:
    """Registered constants that bind a new-pipeline permit to its host contract."""

    risk_policy_sha256: str
    permit_protocol_sha256: str
    execution_protocol_sha256: str
    gate_d_report_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "risk_policy_sha256",
            "permit_protocol_sha256",
            "execution_protocol_sha256",
            "gate_d_report_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise CompiledPermitRejected(f"{name} must be a lowercase SHA-256 digest")


def _reject(path: str, detail: str) -> None:
    raise CompiledPermitRejected(f"{path}: {detail}")


def _require_utc(value: datetime, path: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject(path, "must be an explicit UTC timestamp")
    return value.astimezone(UTC)


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(path, "must be non-empty normalized text")
    return value


def _decimal(value: object, path: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise CompiledPermitRejected(f"{path}: must be a finite decimal") from error
    if not parsed.is_finite():
        _reject(path, "must be a finite decimal")
    return parsed


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(path, "must be an object")
    return value


def _validate_strategy_lineage(strategy_input: StrategyInput, decision: StrategyDecision) -> None:
    if not isinstance(strategy_input, StrategyInput):
        _reject("strategy_input", "must be a StrategyInput")
    if not isinstance(decision, StrategyDecision):
        _reject("decision", "must be a StrategyDecision")
    if decision.disposition is not DecisionDisposition.ACCEPTED:
        _reject("decision.disposition", "only ACCEPTED decisions may reach a permit bridge")
    snapshot = strategy_input.snapshot
    expected = {
        "event_id": snapshot.event_id,
        "candidate_id": snapshot.candidate_id,
        "cohort_id": snapshot.cohort_id,
        "policy_sha256": snapshot.policy_sha256,
        "candidate_manifest_sha256": strategy_input.candidate_manifest_sha256,
        "strategy_snapshot_sha256": strategy_input.snapshot_sha256,
        "feature_receipt_sha256": strategy_input.feature_receipt_sha256,
    }
    for name, value in expected.items():
        if getattr(decision, name) != value:
            _reject(f"decision.{name}", "does not bind the joined strategy input")


def _vertical_terms(
    compiled: CompiledExpression,
) -> tuple[
    str,
    VerticalType,
    date,
    int,
    Decimal,
    Decimal,
    Decimal,
    OptionLeg,
    OptionLeg,
]:
    if not isinstance(compiled, CompiledExpression):
        _reject("compiled", "must be a CompiledExpression")
    if compiled.expression_kind is not ExpressionKind.DEBIT_VERTICAL:
        _reject("compiled.expression_kind", "must be DEBIT_VERTICAL")
    block = _mapping(compiled.debit_vertical, "compiled.debit_vertical")
    try:
        underlying = _text(block.get("underlying"), "compiled.debit_vertical.underlying")
        expiry_text = _text(block.get("expiry"), "compiled.debit_vertical.expiry")
        expiry = date.fromisoformat(expiry_text)
        if expiry.isoformat() != expiry_text:
            _reject("compiled.debit_vertical.expiry", "must be a normalized ISO date")
        vertical_type = VerticalType(
            _text(block.get("vertical_type"), "compiled.debit_vertical.vertical_type")
        )
        quantity = block.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity != 1:
            _reject("compiled.debit_vertical.quantity", "must be exactly one")
        limit_price = _decimal(block.get("limit_price"), "compiled.debit_vertical.limit_price")
        width = _decimal(block.get("width"), "compiled.debit_vertical.width")
        maximum_loss = _decimal(block.get("maximum_loss"), "compiled.debit_vertical.maximum_loss")
        if limit_price <= 0 or width <= 0 or maximum_loss <= 0:
            _reject("compiled.debit_vertical", "price, width, and maximum loss must be positive")
        long = _mapping(block.get("long_leg"), "compiled.debit_vertical.long_leg")
        short = _mapping(block.get("short_leg"), "compiled.debit_vertical.short_leg")
        long_leg = OptionLeg(
            symbol=_text(long.get("symbol"), "compiled.debit_vertical.long_leg.symbol"),
            underlying=underlying,
            expiry=expiry,
            option_type=OptionType(
                _text(long.get("option_type"), "compiled.debit_vertical.long_leg.option_type")
            ),
            strike=_decimal(long.get("strike"), "compiled.debit_vertical.long_leg.strike"),
            side=OptionSide.BUY,
            position_intent=PositionIntent.BUY_TO_OPEN,
        )
        short_leg = OptionLeg(
            symbol=_text(short.get("symbol"), "compiled.debit_vertical.short_leg.symbol"),
            underlying=underlying,
            expiry=expiry,
            option_type=OptionType(
                _text(short.get("option_type"), "compiled.debit_vertical.short_leg.option_type")
            ),
            strike=_decimal(short.get("strike"), "compiled.debit_vertical.short_leg.strike"),
            side=OptionSide.SELL,
            position_intent=PositionIntent.SELL_TO_OPEN,
        )
    except (TypeError, ValueError) as error:
        raise CompiledPermitRejected(f"compiled.debit_vertical: {error}") from error

    if width != abs(short_leg.strike - long_leg.strike):
        _reject("compiled.debit_vertical.width", "does not equal the exact leg width")
    if maximum_loss != limit_price * Decimal(100) * quantity:
        _reject("compiled.debit_vertical.maximum_loss", "does not equal package debit maximum loss")
    return (
        underlying,
        vertical_type,
        expiry,
        quantity,
        limit_price,
        width,
        maximum_loss,
        long_leg,
        short_leg,
    )


def validate_compiled_permit(
    *,
    compiled: CompiledExpression,
    permit: DebitVerticalPermit,
    constants: PermitBridgeConstants,
    now: datetime | None = None,
) -> None:
    """Prove that one canonical permit is the exact compiled vertical package."""

    (
        _underlying,
        vertical_type,
        _expiry,
        quantity,
        limit_price,
        _width,
        maximum_loss,
        long_leg,
        short_leg,
    ) = _vertical_terms(compiled)
    if not isinstance(permit, DebitVerticalPermit):
        _reject("permit", "must be a DebitVerticalPermit")
    if permit.permit_id != debit_vertical_permit_id(permit):
        _reject("permit.permit_id", "is not derived from canonical permit terms")
    expected = {
        "event_run_id": compiled.event_id,
        "decision_sha256": compiled.decision_sha256,
        "snapshot_sha256": compiled.snapshot_sha256,
        "policy_sha256": constants.risk_policy_sha256,
        "protocol_sha256": constants.permit_protocol_sha256,
        "execution_protocol_sha256": constants.execution_protocol_sha256,
        "vertical_type": vertical_type,
        "quantity": quantity,
        "limit_price": limit_price,
        "legs": (long_leg, short_leg),
    }
    for name, value in expected.items():
        if getattr(permit, name) != value:
            _reject(f"permit.{name}", "does not equal the exact compiled package binding")
    if permit.maximum_loss != maximum_loss:
        _reject("permit.maximum_loss", "does not equal compiled maximum loss")
    if permit.run_mode is not RunMode.PAPER or permit.data_class is not DataClass.INDICATIVE_DATA:
        _reject("permit.boundary", "must remain PAPER with INDICATIVE_DATA")
    if permit.issued_at > permit.expires_at:
        _reject("permit.timing", "issued_at cannot follow expires_at")
    if now is not None:
        current = _require_utc(now, "now")
        if current < permit.issued_at or current >= permit.expires_at:
            _reject("permit.timing", "permit is not active at the authorization clock")


def build_debit_vertical_permit(
    *,
    compiled: CompiledExpression,
    strategy_input: StrategyInput,
    decision: StrategyDecision,
    constants: PermitBridgeConstants,
    issued_at: datetime,
    expires_at: datetime,
) -> DebitVerticalPermit:
    """Build the only canonical PAPER permit from an accepted Gate-D package.

    Every strategy, decision, Gate-D, risk-policy, host-protocol, price, leg, and
    clock term is checked before the restricted model constructor is reached.
    """

    _validate_strategy_lineage(strategy_input, decision)
    issued = _require_utc(issued_at, "issued_at")
    expires = _require_utc(expires_at, "expires_at")
    if expires <= issued:
        _reject("expires_at", "must follow issued_at")
    if issued < decision.decision_at or issued < compiled.compiled_at:
        _reject("issued_at", "cannot precede the decision or Gate-D compilation")
    if expires > strategy_input.snapshot.candidate_entry_deadline_at:
        _reject("expires_at", "cannot exceed the frozen candidate entry deadline")
    if compiled.gate_d_report_sha256 != constants.gate_d_report_sha256:
        _reject("compiled.gate_d_report_sha256", "does not match registered Gate-D receipt")
    decision_sha256 = strategy_decision_sha256(decision)
    if compiled.decision_sha256 != decision_sha256:
        _reject("compiled.decision_sha256", "does not match exact canonical decision bytes")

    (
        _underlying,
        vertical_type,
        _expiry,
        quantity,
        limit_price,
        _width,
        _maximum_loss,
        long_leg,
        short_leg,
    ) = _vertical_terms(compiled)
    values: dict[str, object] = {
        "event_run_id": compiled.event_id,
        "policy_sha256": constants.risk_policy_sha256,
        "snapshot_sha256": compiled.snapshot_sha256,
        "decision_sha256": compiled.decision_sha256,
        "evidence_sha256": strategy_input.snapshot.evidence_packet_sha256,
        "protocol_sha256": constants.permit_protocol_sha256,
        "execution_protocol_sha256": constants.execution_protocol_sha256,
        "issued_at": issued,
        "expires_at": expires,
        "vertical_type": vertical_type,
        "quantity": quantity,
        "limit_price": limit_price,
        "legs": (long_leg, short_leg),
        "run_mode": RunMode.PAPER,
        "data_class": DataClass.INDICATIVE_DATA,
    }
    provisional = DebitVerticalPermit._from_frozen_decision(permit_id="UNBOUND", **values)
    permit = DebitVerticalPermit._from_frozen_decision(
        permit_id=debit_vertical_permit_id(provisional), **values
    )
    validate_compiled_permit(compiled=compiled, permit=permit, constants=constants, now=issued)
    return permit


def canonical_permit_sha256(permit: DebitVerticalPermit) -> str:
    """Hash the full canonical permit bytes persisted by the risk ledger."""

    if not isinstance(permit, DebitVerticalPermit):
        _reject("permit", "must be a DebitVerticalPermit")
    return sha256_bytes(debit_vertical_permit_bytes(permit))
