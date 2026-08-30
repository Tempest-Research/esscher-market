"""Frozen promoted-expression policy.

The policy pins exactly one promoted expression together with its Gate D
receipt, declared after-cost objective, evidence threshold, and all geometry,
liquidity, freshness, and budget bounds. The compiler cannot silently choose
another expression: the promoted kind and the receipt hash are part of the
policy identity.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from ringdown_market.execution.expression.reasons import (
    ExpressionKind,
    ExpressionReason,
    ExpressionRejected,
)
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

PROMOTED_EXPRESSION_POLICY_SCHEMA: Final = "esscher.promoted_expression_policy"
PROMOTED_EXPRESSION_POLICY_SCHEMA_VERSION: Final = 1
AFTER_COST_OBJECTIVE: Final = "AFTER_COST_EXPECTED_EDGE_VS_CASH"

_POLICY_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "policy_id",
        "version",
        "gate_d_report_sha256",
        "expression_kind",
        "objective",
        "evidence_threshold",
        "evidence_min_events",
        "operational_loss_budget",
        "quote_max_age_ms",
        "cross_leg_skew_max_ms",
        "spread_max_bps",
        "min_quote_size",
        "min_dte",
        "max_dte",
        "delta_min",
        "delta_max",
        "width_min",
        "width_max",
        "liquidity_min_open_interest",
    }
)


class _DuplicateFieldError(ValueError):
    pass


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ExpressionRejected(
        ExpressionReason.UNSUPPORTED_INPUT,
        "promoted_expression_policy",
        f"non-finite JSON constant {value} is forbidden",
    )


def _reject(reason: ExpressionReason, path: str, detail: str) -> ExpressionRejected:
    return ExpressionRejected(reason, path, detail)


@dataclass(frozen=True, slots=True)
class PromotedExpressionPolicy:
    """One immutable promoted-expression policy."""

    policy_id: str
    version: str
    gate_d_report_sha256: str
    expression_kind: ExpressionKind
    objective: str
    evidence_threshold: Decimal
    evidence_min_events: int
    operational_loss_budget: Decimal
    quote_max_age_ms: int
    cross_leg_skew_max_ms: int
    spread_max_bps: Decimal
    min_quote_size: int
    min_dte: int
    max_dte: int
    delta_min: Decimal
    delta_max: Decimal
    width_min: Decimal
    width_max: Decimal
    liquidity_min_open_interest: int

    def __post_init__(self) -> None:
        if not self.policy_id or not self.version:
            raise ValueError("policy_id and version must be non-empty text")
        if len(self.gate_d_report_sha256) != 64:
            raise ValueError("gate_d_report_sha256 must be a SHA-256 digest")
        if self.objective != AFTER_COST_OBJECTIVE:
            raise ValueError("objective must be the frozen after-cost objective")
        for field in ("evidence_threshold", "spread_max_bps"):
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{field} must be a non-negative finite decimal")
        if not self.evidence_threshold.is_finite():
            raise ValueError("evidence_threshold must be finite")
        if not self.operational_loss_budget.is_finite() or self.operational_loss_budget <= 0:
            raise ValueError("operational_loss_budget must be positive")
        for field in (
            "evidence_min_events",
            "quote_max_age_ms",
            "cross_leg_skew_max_ms",
            "min_quote_size",
            "min_dte",
            "max_dte",
            "liquidity_min_open_interest",
        ):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.min_dte > self.max_dte:
            raise ValueError("min_dte cannot exceed max_dte")
        if self.delta_min > self.delta_max:
            raise ValueError("delta_min cannot exceed delta_max")
        if self.width_min > self.width_max:
            raise ValueError("width_min cannot exceed width_max")


def promoted_expression_policy_payload(value: PromotedExpressionPolicy) -> dict[str, object]:
    """Return the single versioned serialization for one policy."""

    return {
        "schema": PROMOTED_EXPRESSION_POLICY_SCHEMA,
        "schema_version": PROMOTED_EXPRESSION_POLICY_SCHEMA_VERSION,
        "policy_id": value.policy_id,
        "version": value.version,
        "gate_d_report_sha256": value.gate_d_report_sha256,
        "expression_kind": value.expression_kind.value,
        "objective": value.objective,
        "evidence_threshold": str(value.evidence_threshold),
        "evidence_min_events": value.evidence_min_events,
        "operational_loss_budget": str(value.operational_loss_budget),
        "quote_max_age_ms": value.quote_max_age_ms,
        "cross_leg_skew_max_ms": value.cross_leg_skew_max_ms,
        "spread_max_bps": str(value.spread_max_bps),
        "min_quote_size": value.min_quote_size,
        "min_dte": value.min_dte,
        "max_dte": value.max_dte,
        "delta_min": str(value.delta_min),
        "delta_max": str(value.delta_max),
        "width_min": str(value.width_min),
        "width_max": str(value.width_max),
        "liquidity_min_open_interest": value.liquidity_min_open_interest,
    }


def promoted_expression_policy_bytes(value: PromotedExpressionPolicy) -> bytes:
    """Serialize one policy to deterministic canonical bytes."""

    return canonical_json_bytes(promoted_expression_policy_payload(value))


def promoted_expression_policy_sha256(value: PromotedExpressionPolicy) -> str:
    return sha256_bytes(promoted_expression_policy_bytes(value))


def _decimal(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str):
        raise _reject(ExpressionReason.UNSUPPORTED_INPUT, path, "must be decimal text")
    try:
        result = Decimal(value)
    except ArithmeticError as error:
        raise _reject(ExpressionReason.UNSUPPORTED_INPUT, path, str(error)) from None
    if not result.is_finite():
        raise _reject(ExpressionReason.NON_FINITE_VALUE, path, "must be finite")
    return result


def parse_promoted_expression_policy(raw: bytes) -> PromotedExpressionPolicy:
    """Strictly parse canonical promoted-expression policy bytes."""

    if type(raw) is not bytes:
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT,
            "promoted_expression_policy",
            "policy input must be immutable bytes",
        )
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except _DuplicateFieldError as error:
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT,
            "promoted_expression_policy",
            f"duplicate JSON field {error}",
        ) from None
    except ExpressionRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT, "promoted_expression_policy", str(error)
        ) from None
    if not isinstance(payload, dict):
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT,
            "promoted_expression_policy",
            "policy root must be an object",
        )
    actual = frozenset(payload)
    missing = sorted(_POLICY_FIELDS - actual)
    unknown = sorted(actual - _POLICY_FIELDS)
    if missing or unknown:
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT,
            "promoted_expression_policy",
            f"field mismatch; missing={missing} unknown={unknown}",
        )
    if payload["schema"] != PROMOTED_EXPRESSION_POLICY_SCHEMA:
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT,
            "promoted_expression_policy.schema",
            "unsupported schema",
        )
    if payload["schema_version"] != PROMOTED_EXPRESSION_POLICY_SCHEMA_VERSION:
        raise _reject(
            ExpressionReason.UNSUPPORTED_SCHEMA_VERSION,
            "promoted_expression_policy.schema_version",
            "unsupported schema version",
        )
    try:
        expression_kind = ExpressionKind(str(payload["expression_kind"]))
    except ValueError as error:
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT,
            "promoted_expression_policy.expression_kind",
            str(error),
        ) from None
    result = PromotedExpressionPolicy(
        policy_id=str(payload["policy_id"]),
        version=str(payload["version"]),
        gate_d_report_sha256=str(payload["gate_d_report_sha256"]),
        expression_kind=expression_kind,
        objective=str(payload["objective"]),
        evidence_threshold=_decimal(
            payload["evidence_threshold"], path="promoted_expression_policy.evidence_threshold"
        ),
        evidence_min_events=int(payload["evidence_min_events"]),  # type: ignore[arg-type]
        operational_loss_budget=_decimal(
            payload["operational_loss_budget"],
            path="promoted_expression_policy.operational_loss_budget",
        ),
        quote_max_age_ms=int(payload["quote_max_age_ms"]),  # type: ignore[arg-type]
        cross_leg_skew_max_ms=int(payload["cross_leg_skew_max_ms"]),  # type: ignore[arg-type]
        spread_max_bps=_decimal(
            payload["spread_max_bps"], path="promoted_expression_policy.spread_max_bps"
        ),
        min_quote_size=int(payload["min_quote_size"]),  # type: ignore[arg-type]
        min_dte=int(payload["min_dte"]),  # type: ignore[arg-type]
        max_dte=int(payload["max_dte"]),  # type: ignore[arg-type]
        delta_min=_decimal(payload["delta_min"], path="promoted_expression_policy.delta_min"),
        delta_max=_decimal(payload["delta_max"], path="promoted_expression_policy.delta_max"),
        width_min=_decimal(payload["width_min"], path="promoted_expression_policy.width_min"),
        width_max=_decimal(payload["width_max"], path="promoted_expression_policy.width_max"),
        liquidity_min_open_interest=int(payload["liquidity_min_open_interest"]),  # type: ignore[arg-type]
    )
    if promoted_expression_policy_bytes(result) != raw:
        raise _reject(
            ExpressionReason.UNSUPPORTED_INPUT,
            "promoted_expression_policy",
            "policy bytes are not canonical",
        )
    return result
