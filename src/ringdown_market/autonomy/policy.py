"""Immutable policy contract for Esscher's autonomous PAPER release."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from typing import Final

POLICY_RESOURCE_NAME: Final = "policies/hackathon_autonomous_v1.json"
# Owner-approved refreeze 2026-09-04 (MS-Mesh, issue #91 governance): the
# reasoner block pivots from the withdrawn Kimi K3 entitlement to the direct
# MiniMax-M3 route and policy_version advances to 1.1.0.
# Owner-approved refreeze 2026-09-04 (MS-Mesh, issue #68 sizing directive):
# policy_version 1.2.0 reorders max_loss_tiers so the FIRST (operative) tier is
# 0.10 - the account-relative allocator then sizes one position up to 10% of
# current equity ($10,000 at the $100k starting equity).  The tier VALUE set is
# unchanged (0.05/0.10/0.20).
# Owner-approved refreeze 2026-09-04 (MS-Mesh, issue #91 measurement findings):
# policy_version 1.3.0 pivots the reasoner block to the furry.vg gateway after
# MiniMax-M3 measured above the frozen 8s one-call budget.
# Owner-approved refreeze 2026-09-04 (MS-Mesh, issue #91 measurement findings):
# policy_version 1.4.0 finalizes the reasoner model as deepseek-v4-flash-0731-free
# on that gateway after the initial Kimi-K2.6-free candidate failed the frozen
# six-field decision validator on every probe (contradictions shape drift); the
# gateway provider (FURRY_VG_GATEWAY), base_url, and credential_env are
# unchanged.  Every other owner boundary (PAPER-only, no broker authority, 20%
# per-underlying cap, 50% aggregate cap, 50% drawdown freeze, memory mode) is
# byte-identical.
ACCEPTED_AUTONOMOUS_POLICY_V1_SHA256: Final = (
    "19c4669ad9fefce6ec7f03cec94ec6ce175fd8f969f7c883f0c045cbc6357b20"
)
_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "memory",
        "objective",
        "policy_id",
        "policy_version",
        "reasoner",
        "risk",
        "run_mode",
        "schema",
        "schema_version",
        "strategy",
    }
)
_SECTION_FIELDS: Final = {
    "strategy": frozenset(
        {
            "abstention_allowed",
            "event_triggered_decisions",
            "hard_flat_time_et",
            "intraday_decision_times_et",
            "lanes",
            "one_entry_per_decision_identity",
            "trade_count_cap_per_day",
        }
    ),
    "reasoner": frozenset({"base_url", "broker_authority", "credential_env", "model", "provider"}),
    "risk": frozenset(
        {
            "daily_loss_stop",
            "defined_risk_spreads_only",
            "emergency_drawdown_freeze_fraction",
            "liquidity_capacity_required",
            "margin_funded_sizing",
            "max_aggregate_open_debit_fraction",
            "max_loss_tiers",
            "naked_options",
            "reconciliation_failure_disables_entry",
            "uncovered_short_shares",
            "unknown_broker_state_disables_entry",
        }
    ),
    "memory": frozenset(
        {
            "input_families",
            "lookahead_permitted",
            "mode",
            "online_self_training",
            "policy_self_modification",
        }
    ),
}


class AutonomousPolicyError(ValueError):
    """Raised before a malformed autonomy policy can reach runtime code."""


_SAFE_AUTHORITY_SETTINGS: Final = (
    ("reasoner", "broker_authority", False),
    ("risk", "defined_risk_spreads_only", True),
    ("risk", "liquidity_capacity_required", True),
    ("risk", "margin_funded_sizing", False),
    ("risk", "naked_options", False),
    ("risk", "uncovered_short_shares", False),
    ("risk", "unknown_broker_state_disables_entry", True),
    ("risk", "reconciliation_failure_disables_entry", True),
    ("memory", "online_self_training", False),
    ("memory", "policy_self_modification", False),
    ("memory", "lookahead_permitted", False),
)


@dataclass(frozen=True, slots=True)
class AutonomousPolicy:
    """The owner-approved PAPER-only autonomy envelope."""

    policy_id: str
    policy_version: str
    run_mode: str
    objective: str
    strategy_lanes: tuple[str, ...]
    intraday_decision_times_et: tuple[str, ...]
    event_triggered_decisions: bool
    trade_count_cap_per_day: int | None
    one_entry_per_decision_identity: bool
    abstention_allowed: bool
    hard_flat_time_et: str
    reasoner_provider: str
    reasoner_base_url: str
    reasoner_model: str
    reasoner_credential_env: str
    reasoner_broker_authority: bool
    max_loss_tiers: tuple[Decimal, ...]
    max_aggregate_open_debit_fraction: Decimal
    emergency_drawdown_freeze_fraction: Decimal
    daily_loss_stop: Decimal | None
    defined_risk_spreads_only: bool
    liquidity_capacity_required: bool
    margin_funded_sizing: bool
    naked_options: bool
    uncovered_short_shares: bool
    unknown_broker_state_disables_entry: bool
    reconciliation_failure_disables_entry: bool
    memory_mode: str
    memory_input_families: tuple[str, ...]
    online_self_training: bool
    policy_self_modification: bool
    lookahead_permitted: bool


def _policy_from_payload(payload: dict[str, object]) -> AutonomousPolicy:
    strategy = payload["strategy"]
    reasoner = payload["reasoner"]
    risk = payload["risk"]
    memory = payload["memory"]
    assert isinstance(strategy, dict)
    assert isinstance(reasoner, dict)
    assert isinstance(risk, dict)
    assert isinstance(memory, dict)
    return AutonomousPolicy(
        policy_id=payload["policy_id"],
        policy_version=payload["policy_version"],
        run_mode=payload["run_mode"],
        objective=payload["objective"],
        strategy_lanes=tuple(strategy["lanes"]),
        intraday_decision_times_et=tuple(strategy["intraday_decision_times_et"]),
        event_triggered_decisions=strategy["event_triggered_decisions"],
        trade_count_cap_per_day=strategy["trade_count_cap_per_day"],
        one_entry_per_decision_identity=strategy["one_entry_per_decision_identity"],
        abstention_allowed=strategy["abstention_allowed"],
        hard_flat_time_et=strategy["hard_flat_time_et"],
        reasoner_provider=reasoner["provider"],
        reasoner_base_url=reasoner["base_url"],
        reasoner_model=reasoner["model"],
        reasoner_credential_env=reasoner["credential_env"],
        reasoner_broker_authority=reasoner["broker_authority"],
        max_loss_tiers=tuple(Decimal(value) for value in risk["max_loss_tiers"]),
        max_aggregate_open_debit_fraction=Decimal(risk["max_aggregate_open_debit_fraction"]),
        emergency_drawdown_freeze_fraction=Decimal(risk["emergency_drawdown_freeze_fraction"]),
        daily_loss_stop=(
            Decimal(risk["daily_loss_stop"]) if risk["daily_loss_stop"] is not None else None
        ),
        defined_risk_spreads_only=risk["defined_risk_spreads_only"],
        liquidity_capacity_required=risk["liquidity_capacity_required"],
        margin_funded_sizing=risk["margin_funded_sizing"],
        naked_options=risk["naked_options"],
        uncovered_short_shares=risk["uncovered_short_shares"],
        unknown_broker_state_disables_entry=risk["unknown_broker_state_disables_entry"],
        reconciliation_failure_disables_entry=risk["reconciliation_failure_disables_entry"],
        memory_mode=memory["mode"],
        memory_input_families=tuple(memory["input_families"]),
        online_self_training=memory["online_self_training"],
        policy_self_modification=memory["policy_self_modification"],
        lookahead_permitted=memory["lookahead_permitted"],
    )


def autonomous_policy_bytes() -> bytes:
    """Return the exact packaged policy bytes."""

    return resources.files(__package__).joinpath(POLICY_RESOURCE_NAME).read_bytes()


def autonomous_policy_sha256() -> str:
    """Return the immutable identity of the packaged policy."""

    return hashlib.sha256(autonomous_policy_bytes()).hexdigest()


def parse_autonomous_policy(raw: bytes) -> AutonomousPolicy:
    """Parse an autonomy policy and preserve the permanent PAPER boundary."""

    if type(raw) is not bytes:
        raise AutonomousPolicyError("autonomy policy input must be immutable bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AutonomousPolicyError(str(error)) from None
    if not isinstance(payload, dict):
        raise AutonomousPolicyError("autonomy policy root must be an object")
    actual = frozenset(payload)
    missing = sorted(_TOP_LEVEL_FIELDS - actual)
    unknown = sorted(actual - _TOP_LEVEL_FIELDS)
    if missing or unknown:
        raise AutonomousPolicyError(f"field mismatch; missing={missing} unknown={unknown}")
    for section, expected_fields in _SECTION_FIELDS.items():
        nested = payload.get(section)
        if not isinstance(nested, dict):
            raise AutonomousPolicyError(f"{section} must be an object")
        actual_fields = frozenset(nested)
        nested_missing = sorted(expected_fields - actual_fields)
        nested_unknown = sorted(actual_fields - expected_fields)
        if nested_missing or nested_unknown:
            raise AutonomousPolicyError(
                f"{section} field mismatch; missing={nested_missing} unknown={nested_unknown}"
            )
    if payload.get("run_mode") != "PAPER":
        raise AutonomousPolicyError("autonomy policy is permanently PAPER-only")
    for section, field, expected in _SAFE_AUTHORITY_SETTINGS:
        nested = payload.get(section)
        if not isinstance(nested, dict) or nested.get(field) is not expected:
            raise AutonomousPolicyError(f"unsafe authority setting: {section}.{field}")
    canonical = (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    if raw != canonical:
        raise AutonomousPolicyError("autonomy policy bytes are not canonical")
    return _policy_from_payload(payload)


def load_autonomous_policy() -> AutonomousPolicy:
    """Load the packaged owner-approved policy."""

    return parse_autonomous_policy(autonomous_policy_bytes())
