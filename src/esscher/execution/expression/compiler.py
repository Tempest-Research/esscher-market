"""Deterministic production expression compiler.

Consumes one validated ``UP`` or ``DOWN`` decision, one immutable market
snapshot, and one frozen promoted-expression policy; emits one deterministic
compiled expression or a stable ``NO_PACKAGE``. ``UNCERTAIN`` never reaches
compilation. The compiler has no account, order, position, mutation, model,
or policy-promotion authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from esscher.execution.expression.economics import (
    debit_vertical_economics,
    option_economics,
    shares_economics,
)
from esscher.execution.expression.geometry import (
    build_option_leg,
    contract_dte,
    select_long_contract,
    select_package,
    select_vertical_geometry,
)
from esscher.execution.expression.observations import (
    ExpressionMarketSnapshot,
    expression_market_snapshot_sha256,
)
from esscher.execution.expression.policy import (
    PromotedExpressionPolicy,
    promoted_expression_policy_sha256,
)
from esscher.execution.expression.reasons import (
    NO_PACKAGE,
    ExpressionKind,
    ExpressionReason,
    ExpressionRejected,
)
from esscher.execution.expression.validation import PINNED_FEEDS as _PINNED_FEEDS
from esscher.execution.expression.validation import (
    validate_borrow_locate,
    validate_cross_leg_skew,
    validate_executable_data,
    validate_feed,
    validate_package,
    validate_quote,
)
from esscher.execution.models import (
    OptionSide,
    OptionType,
    PositionIntent,
    VerticalType,
)
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes
from esscher.strategy.models import (
    DecisionDisposition,
    Direction,
    StrategyDecision,
)

COMPILED_EXPRESSION_SCHEMA: Final = "esscher.compiled_expression"
COMPILED_EXPRESSION_SCHEMA_VERSION: Final = 1
COMPILED: Final = "COMPILED"
PINNED_FEEDS: Final = _PINNED_FEEDS


def _reject(reason: ExpressionReason, path: str, detail: str) -> ExpressionRejected:
    return ExpressionRejected(reason, path, detail)


@dataclass(frozen=True, slots=True)
class CompiledExpression:
    """One deterministic compiled expression bound to its inputs."""

    expression_kind: ExpressionKind
    event_id: str
    decision_sha256: str
    snapshot_sha256: str
    policy_sha256: str
    gate_d_report_sha256: str
    compiled_at: datetime
    shares: dict[str, object] | None
    long_option: dict[str, object] | None
    debit_vertical: dict[str, object] | None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must be non-empty text")
        for field in (
            "decision_sha256",
            "snapshot_sha256",
            "policy_sha256",
            "gate_d_report_sha256",
        ):
            if len(getattr(self, field)) != 64:
                raise ValueError(f"{field} must be a SHA-256 digest")
        if self.compiled_at.tzinfo != UTC:
            raise ValueError("compiled_at must be UTC")
        filled = sum(1 for block in (self.shares, self.long_option, self.debit_vertical) if block)
        if self.expression_kind is ExpressionKind.CASH_NO_TRADE:
            if filled != 0:
                raise ValueError("cash expressions carry no position block")
        elif filled != 1:
            raise ValueError("exactly one position block must be present")


def compiled_expression_payload(value: CompiledExpression) -> dict[str, object]:
    """Return the single versioned serialization for one compiled expression."""

    return {
        "schema": COMPILED_EXPRESSION_SCHEMA,
        "schema_version": COMPILED_EXPRESSION_SCHEMA_VERSION,
        "expression_kind": value.expression_kind.value,
        "event_id": value.event_id,
        "decision_sha256": value.decision_sha256,
        "snapshot_sha256": value.snapshot_sha256,
        "policy_sha256": value.policy_sha256,
        "gate_d_report_sha256": value.gate_d_report_sha256,
        "compiled_at": value.compiled_at.isoformat().replace("+00:00", "Z"),
        "shares": value.shares,
        "long_option": value.long_option,
        "debit_vertical": value.debit_vertical,
        "no_package": False,
    }


def compiled_expression_bytes(value: CompiledExpression) -> bytes:
    """Serialize one compiled expression to deterministic canonical bytes."""

    return canonical_json_bytes(compiled_expression_payload(value))


def compiled_expression_sha256(value: CompiledExpression) -> str:
    return sha256_bytes(compiled_expression_bytes(value))


def no_package_payload(
    *,
    reason: ExpressionReason,
    event_id: str,
    decision_sha256: str,
    snapshot_sha256: str,
    policy_sha256: str,
    gate_d_report_sha256: str,
    compiled_at: datetime,
) -> dict[str, object]:
    """Return the canonical rejection artifact for one failed compilation."""

    return {
        "schema": COMPILED_EXPRESSION_SCHEMA,
        "schema_version": COMPILED_EXPRESSION_SCHEMA_VERSION,
        "expression_kind": NO_PACKAGE,
        "event_id": event_id,
        "decision_sha256": decision_sha256,
        "snapshot_sha256": snapshot_sha256,
        "policy_sha256": policy_sha256,
        "gate_d_report_sha256": gate_d_report_sha256,
        "compiled_at": compiled_at.isoformat().replace("+00:00", "Z"),
        "no_package_reason": reason.value,
        "shares": None,
        "long_option": None,
        "debit_vertical": None,
        "no_package": True,
    }


def _compile_cash(
    *,
    event_id: str,
    bindings: dict[str, str],
    compiled_at: datetime,
) -> CompiledExpression:
    return CompiledExpression(
        expression_kind=ExpressionKind.CASH_NO_TRADE,
        event_id=event_id,
        compiled_at=compiled_at,
        shares=None,
        long_option=None,
        debit_vertical=None,
        **bindings,  # type: ignore[arg-type]
    )


def _compile_shares(
    *,
    decision: StrategyDecision,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    event_id: str,
    bindings: dict[str, str],
    compiled_at: datetime,
) -> CompiledExpression:
    direction_is_up = decision.direction is Direction.UP
    share = snapshot.share
    validate_feed(share.feed, "share.feed")
    validate_executable_data(
        share.data_class, "share.data_class", allows_indicative=policy.allows_indicative_data
    )
    validate_quote(share.quote, snapshot=snapshot, policy=policy, path="share.quote")
    if not direction_is_up:
        validate_borrow_locate(snapshot.borrow_locate, snapshot=snapshot, policy=policy)
    economics = shares_economics(event_id, snapshot, policy, direction_is_up=direction_is_up)
    if not economics.compared:
        raise _reject(
            economics.reason or ExpressionReason.NO_QUOTE,
            "shares",
            "share expression failed quote-side validation",
        )
    side = "BUY" if direction_is_up else "SELL_SHORT"
    exposure = share.quote.ask if direction_is_up else share.quote.bid
    return CompiledExpression(
        expression_kind=ExpressionKind.SHARES,
        event_id=event_id,
        compiled_at=compiled_at,
        shares={
            "symbol": share.symbol,
            "side": side,
            "quantity": 1,
            "order_type": "LIMIT",
            "price_rule": "ASK" if direction_is_up else "BID",
            "exposure": str(exposure),
            "borrow_locate_sha256": (
                snapshot.borrow_locate.content_sha256 if not direction_is_up else None
            ),
        },
        long_option=None,
        debit_vertical=None,
        **bindings,  # type: ignore[arg-type]
    )


def _compile_long_option(
    *,
    decision: StrategyDecision,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    event_id: str,
    bindings: dict[str, str],
    compiled_at: datetime,
) -> CompiledExpression:
    direction_is_up = decision.direction is Direction.UP
    option_type = OptionType.CALL if direction_is_up else OptionType.PUT
    contract = select_long_contract(
        snapshot,
        policy,
        option_type=option_type,
        asof=snapshot.observation_clock_at.date(),
        direction_is_up=direction_is_up,
    )
    validate_feed(contract.feed, f"option.{contract.symbol}.feed")
    validate_executable_data(
        contract.data_class,
        f"option.{contract.symbol}.data_class",
        allows_indicative=policy.allows_indicative_data,
    )
    validate_quote(
        contract.quote,
        snapshot=snapshot,
        policy=policy,
        path=f"option.{contract.symbol}.quote",
    )
    dte = contract_dte(contract, snapshot.observation_clock_at.date())
    if dte < 1:
        raise _reject(
            ExpressionReason.LIFECYCLE_CHECK_FAILED,
            f"option.{contract.symbol}.dte",
            "0DTE shortcuts are prohibited",
        )
    leg = build_option_leg(
        contract, side=OptionSide.BUY, position_intent=PositionIntent.BUY_TO_OPEN
    )
    economics = option_economics(event_id, contract, policy)
    if not economics.compared:
        raise _reject(
            economics.reason or ExpressionReason.NO_QUOTE,
            "long_option",
            "long option expression failed quote-side validation",
        )
    return CompiledExpression(
        expression_kind=ExpressionKind.ONE_LONG_OPTION,
        event_id=event_id,
        compiled_at=compiled_at,
        shares=None,
        long_option={
            "symbol": leg.symbol,
            "underlying": leg.underlying,
            "expiry": leg.expiry.isoformat(),
            "option_type": leg.option_type.value,
            "strike": str(leg.strike),
            "side": "BUY",
            "position_intent": "BUY_TO_OPEN",
            "quantity": 1,
            "order_type": "LIMIT",
            "dte": dte,
            "premium_at_risk": str(economics.max_loss),
            "limit_price_rule": "ASK",
        },
        debit_vertical=None,
        **bindings,  # type: ignore[arg-type]
    )


def _compile_debit_vertical(
    *,
    decision: StrategyDecision,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    event_id: str,
    bindings: dict[str, str],
    compiled_at: datetime,
) -> CompiledExpression:
    direction_is_up = decision.direction is Direction.UP
    asof = snapshot.observation_clock_at.date()
    geometry = select_vertical_geometry(
        snapshot, policy, direction_is_up=direction_is_up, asof=asof
    )
    for contract in (geometry.long_leg, geometry.short_leg):
        validate_feed(contract.feed, f"vertical.{contract.symbol}.feed")
        validate_executable_data(
            contract.data_class,
            f"vertical.{contract.symbol}.data_class",
            allows_indicative=policy.allows_indicative_data,
        )
        validate_quote(
            contract.quote,
            snapshot=snapshot,
            policy=policy,
            path=f"vertical.{contract.symbol}.quote",
        )
    validate_cross_leg_skew(
        [
            (geometry.long_leg.quote, geometry.long_leg.symbol),
            (geometry.short_leg.quote, geometry.short_leg.symbol),
        ],
        policy=policy,
    )
    dte = contract_dte(geometry.long_leg, asof)
    if dte < 1:
        raise _reject(
            ExpressionReason.LIFECYCLE_CHECK_FAILED,
            "vertical.dte",
            "0DTE shortcuts are prohibited",
        )
    long_leg = build_option_leg(
        geometry.long_leg, side=OptionSide.BUY, position_intent=PositionIntent.BUY_TO_OPEN
    )
    short_leg = build_option_leg(
        geometry.short_leg, side=OptionSide.SELL, position_intent=PositionIntent.SELL_TO_OPEN
    )
    package = select_package(snapshot, geometry)
    validate_package(
        package,
        snapshot=snapshot,
        policy=policy,
        path=f"package.{package.package_id}",
    )
    economics = debit_vertical_economics(event_id, package, geometry.width, policy)
    if not economics.compared:
        raise _reject(
            economics.reason or ExpressionReason.PACKAGE_UNAVAILABLE,
            "debit_vertical",
            "debit vertical expression failed quote-side validation",
        )
    vertical_type = VerticalType.BULL_CALL if direction_is_up else VerticalType.BEAR_PUT
    return CompiledExpression(
        expression_kind=ExpressionKind.DEBIT_VERTICAL,
        event_id=event_id,
        compiled_at=compiled_at,
        shares=None,
        long_option=None,
        debit_vertical={
            "underlying": snapshot.underlying,
            "vertical_type": vertical_type.value,
            "expiry": long_leg.expiry.isoformat(),
            "quantity": 1,
            "order_type": "LIMIT",
            "legging": "ATOMIC_PACKAGE",
            "limit_price": str(package.net_ask),
            "limit_price_rule": "PACKAGE_NET_ASK",
            "width": str(geometry.width),
            "maximum_loss": str(economics.max_loss),
            "package_id": package.package_id,
            # Permit-boundary-compatible frozen vertical fields.
            "long_leg": {
                "symbol": long_leg.symbol,
                "option_type": long_leg.option_type.value,
                "strike": str(long_leg.strike),
            },
            "short_leg": {
                "symbol": short_leg.symbol,
                "option_type": short_leg.option_type.value,
                "strike": str(short_leg.strike),
            },
        },
        **bindings,  # type: ignore[arg-type]
    )


def compile_expression(
    *,
    decision: StrategyDecision,
    decision_bytes: bytes,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    policy_sha256: str,
    gate_d_report_sha256: str,
    compiled_at: datetime,
) -> CompiledExpression:
    """Compile the promoted expression or raise a stable NO_PACKAGE reason."""

    if compiled_at.tzinfo != UTC:
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT, "compiled_at", "compilation clock must be UTC"
        )
    if decision.disposition is not DecisionDisposition.ACCEPTED:
        raise _reject(
            ExpressionReason.DIRECTION_NOT_VALIDATED,
            "decision.disposition",
            "only accepted decisions reach compilation",
        )
    if decision.direction is Direction.UNCERTAIN:
        raise _reject(
            ExpressionReason.DIRECTION_NOT_VALIDATED,
            "decision.direction",
            "UNCERTAIN never reaches compilation",
        )
    if sha256_bytes(decision_bytes) != snapshot.decision_sha256:
        raise _reject(
            ExpressionReason.DECISION_BINDING_MISMATCH,
            "snapshot.decision_sha256",
            "snapshot does not bind the supplied decision bytes",
        )
    if decision.decision_at > snapshot.observation_clock_at:
        raise _reject(
            ExpressionReason.TIME_INCONSISTENT,
            "snapshot.observation_clock_at",
            "market snapshot predates the validated decision",
        )
    if policy_sha256 != promoted_expression_policy_sha256(policy):
        raise _reject(
            ExpressionReason.POLICY_HASH_MISMATCH,
            "policy_sha256",
            "supplied policy digest does not match canonical policy bytes",
        )
    if policy.gate_d_report_sha256 != gate_d_report_sha256:
        raise _reject(
            ExpressionReason.GATE_D_RECEIPT_MISMATCH,
            "policy.gate_d_report_sha256",
            "policy receipt does not match the supplied Gate D report",
        )
    bindings = {
        "decision_sha256": sha256_bytes(decision_bytes),
        "snapshot_sha256": expression_market_snapshot_sha256(snapshot),
        "policy_sha256": policy_sha256,
        "gate_d_report_sha256": gate_d_report_sha256,
    }
    common = {
        "decision": decision,
        "snapshot": snapshot,
        "policy": policy,
        "event_id": decision.event_id,
        "bindings": bindings,
        "compiled_at": compiled_at,
    }
    kind = policy.expression_kind
    if kind is ExpressionKind.CASH_NO_TRADE:
        return _compile_cash(event_id=decision.event_id, bindings=bindings, compiled_at=compiled_at)
    if kind is ExpressionKind.SHARES:
        return _compile_shares(**common)
    if kind is ExpressionKind.ONE_LONG_OPTION:
        return _compile_long_option(**common)
    if kind is ExpressionKind.DEBIT_VERTICAL:
        return _compile_debit_vertical(**common)
    raise _reject(
        ExpressionReason.EXPRESSION_NOT_PROMOTED,
        "policy.expression_kind",
        "no expression is promoted by the policy",
    )


def compile_or_no_package(
    *,
    decision: StrategyDecision,
    decision_bytes: bytes,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    policy_sha256: str,
    gate_d_report_sha256: str,
    compiled_at: datetime,
) -> tuple[str, CompiledExpression | ExpressionReason]:
    """Compile the promoted expression or return the stable NO_PACKAGE reason."""

    try:
        return COMPILED, compile_expression(
            decision=decision,
            decision_bytes=decision_bytes,
            snapshot=snapshot,
            policy=policy,
            policy_sha256=policy_sha256,
            gate_d_report_sha256=gate_d_report_sha256,
            compiled_at=compiled_at,
        )
    except ExpressionRejected as error:
        return NO_PACKAGE, error.reason
