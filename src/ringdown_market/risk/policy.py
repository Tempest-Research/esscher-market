"""Immutable PAPER account risk policy.

The policy is strict canonical data with an immutable identifier/hash. It is
permanently Alpaca PAPER-only, uses Decimal arithmetic, and rejects any
expression whose exposure cannot be calculated conservatively. When a required
constant is unverified there is no fallback: the kernel fails closed with
``POLICY_UNVERIFIED_CONSTANT``. Illustrative values are never approved
defaults.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from ringdown_market.risk.reasons import RiskReason, RiskRejected, _reject
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

RISK_POLICY_SCHEMA: Final = "esscher.paper_account_risk_policy"
RISK_POLICY_SCHEMA_VERSION: Final = 1

_POLICY_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "policy_id",
        "version",
        "run_mode",
        "account_capital",
        "per_event_loss_budget",
        "aggregate_exposure_limit",
        "daily_loss_limit",
        "drawdown_limit",
        "concentration_limit",
        "max_entries_per_day",
        "max_open_expressions",
        "close_only_equity_threshold",
        "truth_max_age_seconds",
        "constants_source_sha256",
    }
)
_DECIMAL_FIELDS: Final = frozenset(
    {
        "account_capital",
        "per_event_loss_budget",
        "aggregate_exposure_limit",
        "daily_loss_limit",
        "drawdown_limit",
        "concentration_limit",
        "close_only_equity_threshold",
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
    raise _reject(
        RiskReason.UNSUPPORTED_INPUT,
        "risk_policy",
        f"non-finite JSON constant {value} is forbidden",
    )


def _positive_decimal(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be decimal text")
    try:
        result = Decimal(value)
    except ArithmeticError as error:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, str(error)) from None
    if not result.is_finite():
        raise _reject(RiskReason.EXPOSURE_NOT_CALCULABLE, path, "must be finite")
    if result <= 0:
        raise _reject(RiskReason.POLICY_UNVERIFIED_CONSTANT, path, "must be positive")
    return result


def _positive_int(value: object, *, path: str) -> int:
    if type(value) is not int or value <= 0:
        raise _reject(RiskReason.POLICY_UNVERIFIED_CONSTANT, path, "must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """One immutable PAPER account risk policy."""

    policy_id: str
    version: str
    run_mode: str
    account_capital: Decimal
    per_event_loss_budget: Decimal
    aggregate_exposure_limit: Decimal
    daily_loss_limit: Decimal
    drawdown_limit: Decimal
    concentration_limit: Decimal
    max_entries_per_day: int
    max_open_expressions: int
    close_only_equity_threshold: Decimal
    truth_max_age_seconds: int
    constants_source_sha256: str

    def __post_init__(self) -> None:
        if not self.policy_id or not self.version:
            raise ValueError("policy_id and version must be non-empty text")
        if self.run_mode != "PAPER":
            raise ValueError("risk policy is permanently PAPER-only")
        for field in _DECIMAL_FIELDS:
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be a positive finite decimal")
        if type(self.max_entries_per_day) is not int or self.max_entries_per_day <= 0:
            raise ValueError("max_entries_per_day must be a positive integer")
        if type(self.max_open_expressions) is not int or self.max_open_expressions <= 0:
            raise ValueError("max_open_expressions must be a positive integer")
        if type(self.truth_max_age_seconds) is not int or self.truth_max_age_seconds <= 0:
            raise ValueError("truth_max_age_seconds must be a positive integer")

    @property
    def constants_verified(self) -> bool:
        """A policy is usable only when its constants source is bound.

        An empty or malformed constants source means the risk constants are
        unverified; the kernel refuses to authorize and there is no fallback.
        """

        value = self.constants_source_sha256
        return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def risk_policy_payload(value: RiskPolicy) -> dict[str, object]:
    """Return the single versioned serialization for one risk policy."""

    return {
        "schema": RISK_POLICY_SCHEMA,
        "schema_version": RISK_POLICY_SCHEMA_VERSION,
        "policy_id": value.policy_id,
        "version": value.version,
        "run_mode": value.run_mode,
        "account_capital": str(value.account_capital),
        "per_event_loss_budget": str(value.per_event_loss_budget),
        "aggregate_exposure_limit": str(value.aggregate_exposure_limit),
        "daily_loss_limit": str(value.daily_loss_limit),
        "drawdown_limit": str(value.drawdown_limit),
        "concentration_limit": str(value.concentration_limit),
        "max_entries_per_day": value.max_entries_per_day,
        "max_open_expressions": value.max_open_expressions,
        "close_only_equity_threshold": str(value.close_only_equity_threshold),
        "truth_max_age_seconds": value.truth_max_age_seconds,
        "constants_source_sha256": value.constants_source_sha256,
    }


def risk_policy_bytes(value: RiskPolicy) -> bytes:
    """Serialize one risk policy to deterministic canonical bytes."""

    return canonical_json_bytes(risk_policy_payload(value))


def risk_policy_sha256(value: RiskPolicy) -> str:
    return sha256_bytes(risk_policy_bytes(value))


def parse_risk_policy(raw: bytes) -> RiskPolicy:
    """Strictly parse canonical risk-policy bytes, failing closed."""

    if type(raw) is not bytes:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy", "input must be bytes")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except _DuplicateFieldError as error:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT, "risk_policy", f"duplicate JSON field {error}"
        ) from None
    except RiskRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy", str(error)) from None
    if not isinstance(payload, dict):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy", "root must be an object")
    actual = frozenset(payload)
    missing = sorted(_POLICY_FIELDS - actual)
    unknown = sorted(actual - _POLICY_FIELDS)
    if missing or unknown:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "risk_policy",
            f"field mismatch; missing={missing} unknown={unknown}",
        )
    if payload["schema"] != RISK_POLICY_SCHEMA:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy.schema", "unsupported schema")
    if payload["schema_version"] != RISK_POLICY_SCHEMA_VERSION:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT, "risk_policy.schema_version", "unsupported version"
        )
    if payload["run_mode"] != "PAPER":
        raise _reject(RiskReason.POLICY_NOT_PAPER_ONLY, "risk_policy.run_mode", "must be PAPER")
    decimals = {
        field: _positive_decimal(payload[field], path=f"risk_policy.{field}")
        for field in _DECIMAL_FIELDS
    }
    result = RiskPolicy(
        policy_id=str(payload["policy_id"]),
        version=str(payload["version"]),
        run_mode=str(payload["run_mode"]),
        account_capital=decimals["account_capital"],
        per_event_loss_budget=decimals["per_event_loss_budget"],
        aggregate_exposure_limit=decimals["aggregate_exposure_limit"],
        daily_loss_limit=decimals["daily_loss_limit"],
        drawdown_limit=decimals["drawdown_limit"],
        concentration_limit=decimals["concentration_limit"],
        max_entries_per_day=_positive_int(
            payload["max_entries_per_day"], path="risk_policy.max_entries_per_day"
        ),
        max_open_expressions=_positive_int(
            payload["max_open_expressions"], path="risk_policy.max_open_expressions"
        ),
        close_only_equity_threshold=decimals["close_only_equity_threshold"],
        truth_max_age_seconds=_positive_int(
            payload["truth_max_age_seconds"], path="risk_policy.truth_max_age_seconds"
        ),
        constants_source_sha256=str(payload["constants_source_sha256"]),
    )
    if risk_policy_bytes(result) != raw:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy", "policy bytes are not canonical")
    return result
