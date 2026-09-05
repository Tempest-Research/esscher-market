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
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from typing import Final

from esscher.risk.reasons import RiskReason, RiskRejected, _reject
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes

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


# -- V2 account-relative autonomy policy ------------------------------------

RISK_POLICY_V2_SCHEMA: Final = "esscher.paper_account_risk_policy"
RISK_POLICY_V2_SCHEMA_VERSION: Final = 2
RISK_POLICY_V2_RESOURCE_NAME: Final = "policies/risk_policy_v2.json"
_RISK_POLICY_V2_ID: Final = "PAPER_ACCOUNT_RISK_POLICY_V2"
_RISK_POLICY_V2_VERSION: Final = "v2"
_V2_STARTING_EQUITY: Final = Decimal("100000")
# Owner-approved refreeze 2026-09-04 (MS-Mesh, issue #68 sizing directive):
# the FIRST tier is the operative sizing tier consumed by derived_risk_tier;
# 0.10 puts one-position capacity at 10% of current equity.  The approved
# value SET is unchanged - 0.05/0.10/0.20 remain the only legal tiers.
_V2_RISK_TIERS: Final = (Decimal("0.10"), Decimal("0.05"), Decimal("0.20"))
_V2_PER_UNDERLYING_FRACTION: Final = Decimal("0.20")
_V2_AGGREGATE_FRACTION: Final = Decimal("0.50")
_V2_FREEZE_FRACTION: Final = Decimal("0.50")
_V2_TRUTH_MAX_AGE_SECONDS: Final = 30
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_V2_POLICY_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "policy_id",
        "version",
        "run_mode",
        "starting_equity",
        "risk_tiers",
        "max_per_underlying_open_debit_fraction",
        "max_aggregate_open_debit_fraction",
        "emergency_drawdown_freeze_fraction",
        "daily_loss_stop",
        "trade_count_cap",
        "open_expression_count_cap",
        "cash_only",
        "defined_risk_only",
        "truth_max_age_seconds",
        "owner_policy_sha256",
        "constants_source_sha256",
    }
)


def _approved_owner_policy_sha256() -> str:
    """Return the digest of the immutable owner autonomy-policy resource."""

    # Import lazily so retaining V1 never requires this newer package resource.
    from esscher.autonomy.policy import autonomous_policy_sha256

    return autonomous_policy_sha256()


def _v2_decimal(value: object, *, path: str, expected: Decimal) -> Decimal:
    if not isinstance(value, str):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be canonical decimal text")
    try:
        result = Decimal(value)
    except ArithmeticError as error:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, str(error)) from None
    if not result.is_finite():
        raise _reject(RiskReason.POLICY_UNVERIFIED_CONSTANT, path, "must be finite")
    if str(result) != value or result != expected:
        raise _reject(RiskReason.POLICY_UNVERIFIED_CONSTANT, path, "does not equal the owner value")
    return result


def _v2_text(value: object, *, path: str, expected: str) -> str:
    if type(value) is not str or value != expected:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "does not equal the owner value")
    return value


def _v2_sha256(value: object, *, path: str, expected: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be a lowercase SHA-256 digest")
    if value != expected:
        raise _reject(RiskReason.POLICY_HASH_MISMATCH, path, "does not bind owner-approved bytes")
    return value


def _reject_float_literal(value: str) -> object:
    raise _reject(
        RiskReason.UNSUPPORTED_INPUT,
        "risk_policy_v2",
        f"JSON float literal {value} is forbidden; use canonical decimal text",
    )


@dataclass(frozen=True, slots=True)
class RiskPolicyV2:
    """The fixed owner-bound, cash-only autonomy policy for account-relative sizing."""

    policy_id: str
    version: str
    run_mode: str
    starting_equity: Decimal
    risk_tiers: tuple[Decimal, Decimal, Decimal]
    max_per_underlying_open_debit_fraction: Decimal
    max_aggregate_open_debit_fraction: Decimal
    emergency_drawdown_freeze_fraction: Decimal
    daily_loss_stop: None
    trade_count_cap: None
    open_expression_count_cap: None
    cash_only: bool
    defined_risk_only: bool
    truth_max_age_seconds: int
    owner_policy_sha256: str
    constants_source_sha256: str

    def __post_init__(self) -> None:
        if self.policy_id != _RISK_POLICY_V2_ID or self.version != _RISK_POLICY_V2_VERSION:
            raise ValueError("RiskPolicyV2 identity is immutable")
        if self.run_mode != "PAPER":
            raise ValueError("RiskPolicyV2 is permanently PAPER-only")
        if self.starting_equity != _V2_STARTING_EQUITY or not self.starting_equity.is_finite():
            raise ValueError("starting_equity must equal the owner-approved Decimal")
        if self.risk_tiers != _V2_RISK_TIERS:
            raise ValueError("risk_tiers must equal the owner-approved tiers")
        for field, expected in (
            ("max_per_underlying_open_debit_fraction", _V2_PER_UNDERLYING_FRACTION),
            ("max_aggregate_open_debit_fraction", _V2_AGGREGATE_FRACTION),
            ("emergency_drawdown_freeze_fraction", _V2_FREEZE_FRACTION),
        ):
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite() or value != expected:
                raise ValueError(f"{field} must equal the owner-approved Decimal")
        if (
            self.daily_loss_stop is not None
            or self.trade_count_cap is not None
            or self.open_expression_count_cap is not None
        ):
            raise ValueError("V2 nullable caps must remain null")
        if self.cash_only is not True or self.defined_risk_only is not True:
            raise ValueError("V2 must remain cash-only and defined-risk-only")
        if self.truth_max_age_seconds != _V2_TRUTH_MAX_AGE_SECONDS:
            raise ValueError("truth_max_age_seconds must equal the owner value")
        owner = _approved_owner_policy_sha256()
        if (
            _SHA256.fullmatch(self.owner_policy_sha256) is None
            or _SHA256.fullmatch(self.constants_source_sha256) is None
            or self.owner_policy_sha256 != owner
            or self.constants_source_sha256 != owner
        ):
            raise ValueError("V2 policy must bind the exact owner autonomy-policy bytes")

    @property
    def constants_verified(self) -> bool:
        owner = _approved_owner_policy_sha256()
        return self.owner_policy_sha256 == owner and self.constants_source_sha256 == owner

    @property
    def emergency_drawdown_freeze_equity(self) -> Decimal:
        return self.starting_equity * self.emergency_drawdown_freeze_fraction


def risk_policy_v2_payload(value: RiskPolicyV2) -> dict[str, object]:
    """Return the single strict schema used for a V2 autonomy policy."""

    if not isinstance(value, RiskPolicyV2):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy_v2", "must be a RiskPolicyV2")
    return {
        "schema": RISK_POLICY_V2_SCHEMA,
        "schema_version": RISK_POLICY_V2_SCHEMA_VERSION,
        "policy_id": value.policy_id,
        "version": value.version,
        "run_mode": value.run_mode,
        "starting_equity": str(value.starting_equity),
        "risk_tiers": [str(tier) for tier in value.risk_tiers],
        "max_per_underlying_open_debit_fraction": str(value.max_per_underlying_open_debit_fraction),
        "max_aggregate_open_debit_fraction": str(value.max_aggregate_open_debit_fraction),
        "emergency_drawdown_freeze_fraction": str(value.emergency_drawdown_freeze_fraction),
        "daily_loss_stop": value.daily_loss_stop,
        "trade_count_cap": value.trade_count_cap,
        "open_expression_count_cap": value.open_expression_count_cap,
        "cash_only": value.cash_only,
        "defined_risk_only": value.defined_risk_only,
        "truth_max_age_seconds": value.truth_max_age_seconds,
        "owner_policy_sha256": value.owner_policy_sha256,
        "constants_source_sha256": value.constants_source_sha256,
    }


def _packaged_risk_policy_v2_bytes() -> bytes:
    return resources.files("esscher.risk").joinpath(RISK_POLICY_V2_RESOURCE_NAME).read_bytes()


def risk_policy_v2_bytes(value: RiskPolicyV2 | None = None) -> bytes:
    """Return V2 canonical serialization, or the exact packaged bytes when omitted."""

    if value is None:
        return _packaged_risk_policy_v2_bytes()
    return canonical_json_bytes(risk_policy_v2_payload(value))


def risk_policy_v2_sha256(value: RiskPolicyV2 | None = None) -> str:
    """Return the digest of exact V2 policy bytes."""

    return sha256_bytes(risk_policy_v2_bytes(value))


def parse_risk_policy_v2(raw: bytes) -> RiskPolicyV2:
    """Strictly parse one canonical owner-bound V2 policy; no defaults are permitted."""

    if type(raw) is not bytes:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy_v2", "input must be bytes")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float_literal,
        )
    except _DuplicateFieldError as error:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT, "risk_policy_v2", f"duplicate JSON field {error}"
        ) from None
    except RiskRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy_v2", str(error)) from None
    if not isinstance(payload, dict):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy_v2", "root must be an object")
    actual = frozenset(payload)
    missing = sorted(_V2_POLICY_FIELDS - actual)
    unknown = sorted(actual - _V2_POLICY_FIELDS)
    if missing or unknown:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT,
            "risk_policy_v2",
            f"field mismatch; missing={missing} unknown={unknown}",
        )
    if payload["schema"] != RISK_POLICY_V2_SCHEMA:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "risk_policy_v2.schema", "unsupported schema")
    if payload["schema_version"] != RISK_POLICY_V2_SCHEMA_VERSION:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT, "risk_policy_v2.schema_version", "unsupported version"
        )
    if payload["run_mode"] != "PAPER":
        raise _reject(RiskReason.POLICY_NOT_PAPER_ONLY, "risk_policy_v2.run_mode", "must be PAPER")
    tiers = payload["risk_tiers"]
    if not isinstance(tiers, list) or len(tiers) != len(_V2_RISK_TIERS):
        raise _reject(RiskReason.POLICY_UNVERIFIED_CONSTANT, "risk_policy_v2.risk_tiers", "invalid")
    parsed_tiers = tuple(
        _v2_decimal(value, path=f"risk_policy_v2.risk_tiers[{index}]", expected=expected)
        for index, (value, expected) in enumerate(zip(tiers, _V2_RISK_TIERS, strict=True))
    )
    owner = _approved_owner_policy_sha256()
    result = RiskPolicyV2(
        policy_id=_v2_text(
            payload["policy_id"], path="risk_policy_v2.policy_id", expected=_RISK_POLICY_V2_ID
        ),
        version=_v2_text(
            payload["version"], path="risk_policy_v2.version", expected=_RISK_POLICY_V2_VERSION
        ),
        run_mode="PAPER",
        starting_equity=_v2_decimal(
            payload["starting_equity"],
            path="risk_policy_v2.starting_equity",
            expected=_V2_STARTING_EQUITY,
        ),
        risk_tiers=parsed_tiers,  # type: ignore[arg-type]
        max_per_underlying_open_debit_fraction=_v2_decimal(
            payload["max_per_underlying_open_debit_fraction"],
            path="risk_policy_v2.max_per_underlying_open_debit_fraction",
            expected=_V2_PER_UNDERLYING_FRACTION,
        ),
        max_aggregate_open_debit_fraction=_v2_decimal(
            payload["max_aggregate_open_debit_fraction"],
            path="risk_policy_v2.max_aggregate_open_debit_fraction",
            expected=_V2_AGGREGATE_FRACTION,
        ),
        emergency_drawdown_freeze_fraction=_v2_decimal(
            payload["emergency_drawdown_freeze_fraction"],
            path="risk_policy_v2.emergency_drawdown_freeze_fraction",
            expected=_V2_FREEZE_FRACTION,
        ),
        daily_loss_stop=_v2_null(payload["daily_loss_stop"], "risk_policy_v2.daily_loss_stop"),
        trade_count_cap=_v2_null(payload["trade_count_cap"], "risk_policy_v2.trade_count_cap"),
        open_expression_count_cap=_v2_null(
            payload["open_expression_count_cap"], "risk_policy_v2.open_expression_count_cap"
        ),
        cash_only=_v2_true(payload["cash_only"], "risk_policy_v2.cash_only"),
        defined_risk_only=_v2_true(
            payload["defined_risk_only"], "risk_policy_v2.defined_risk_only"
        ),
        truth_max_age_seconds=_v2_int(
            payload["truth_max_age_seconds"], "risk_policy_v2.truth_max_age_seconds"
        ),
        owner_policy_sha256=_v2_sha256(
            payload["owner_policy_sha256"],
            path="risk_policy_v2.owner_policy_sha256",
            expected=owner,
        ),
        constants_source_sha256=_v2_sha256(
            payload["constants_source_sha256"],
            path="risk_policy_v2.constants_source_sha256",
            expected=owner,
        ),
    )
    if risk_policy_v2_bytes(result) != raw:
        raise _reject(
            RiskReason.UNSUPPORTED_INPUT, "risk_policy_v2", "policy bytes are not canonical"
        )
    return result


def _v2_null(value: object, path: str) -> None:
    if value is not None:
        raise _reject(RiskReason.POLICY_UNVERIFIED_CONSTANT, path, "must be null")
    return None


def _v2_true(value: object, path: str) -> bool:
    if value is not True:
        raise _reject(RiskReason.POLICY_UNVERIFIED_CONSTANT, path, "must be true")
    return True


def _v2_int(value: object, path: str) -> int:
    if type(value) is not int or value != _V2_TRUTH_MAX_AGE_SECONDS:
        raise _reject(RiskReason.POLICY_UNVERIFIED_CONSTANT, path, "must equal the owner value")
    return value


def load_risk_policy_v2() -> RiskPolicyV2:
    """Load the packaged owner-bound V2 policy after strict byte validation."""

    return parse_risk_policy_v2(risk_policy_v2_bytes())
