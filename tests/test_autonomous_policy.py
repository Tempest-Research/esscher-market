from __future__ import annotations

import json
from decimal import Decimal

import pytest

from ringdown_market.autonomy import (
    ACCEPTED_AUTONOMOUS_POLICY_V1_SHA256,
    AutonomousPolicyError,
    autonomous_policy_bytes,
    autonomous_policy_sha256,
    load_autonomous_policy,
    parse_autonomous_policy,
)


def test_packaged_policy_expresses_owner_approved_autonomous_agent() -> None:
    policy = load_autonomous_policy()

    assert policy.policy_id == "HACKATHON_AUTONOMOUS_V1"
    assert policy.run_mode == "PAPER"
    assert policy.objective == "MAXIMIZE_JUDGED_PAPER_PNL"
    assert policy.strategy_lanes == (
        "EARNINGS_RESIDUAL_CONTINUATION_V1",
        "SPY_QQQ_INTRADAY_REGIME_SPREAD_V1",
        "LIQUID_STOCK_CATALYST_SPREAD_V1",
    )
    assert policy.intraday_decision_times_et == (
        "10:00:00",
        "11:00:00",
        "12:00:00",
        "13:00:00",
        "14:00:00",
        "15:00:00",
    )
    assert policy.event_triggered_decisions is True
    assert policy.trade_count_cap_per_day is None
    assert policy.one_entry_per_decision_identity is True
    assert policy.abstention_allowed is True
    assert policy.hard_flat_time_et == "15:30:00"

    assert policy.reasoner_provider == "FURRY_VG_GATEWAY"
    assert policy.reasoner_base_url == "https://ai.furry.vg/v1"
    assert policy.reasoner_model == "deepseek-v4-flash-0731-free"
    assert policy.reasoner_credential_env == "FURRY_API_KEY"
    assert policy.reasoner_broker_authority is False

    assert policy.max_loss_tiers == (
        Decimal("0.10"),
        Decimal("0.05"),
        Decimal("0.20"),
    )
    assert policy.max_aggregate_open_debit_fraction == Decimal("0.50")
    assert policy.emergency_drawdown_freeze_fraction == Decimal("0.50")
    assert policy.daily_loss_stop is None
    assert policy.defined_risk_spreads_only is True
    assert policy.liquidity_capacity_required is True

    assert policy.memory_mode == "IMMUTABLE_EPISODIC_LEDGER"
    assert policy.memory_input_families == (
        "PRIOR_DECISIONS",
        "FILLS",
        "EXITS",
        "REALIZED_PAPER_PNL",
        "SHADOW_PNL",
        "FAILURE_MODES",
        "CURRENT_PORTFOLIO",
    )
    assert policy.online_self_training is False
    assert policy.policy_self_modification is False
    assert policy.lookahead_permitted is False


def test_parser_rejects_non_paper_mode() -> None:
    payload = json.loads(autonomous_policy_bytes())
    payload["run_mode"] = "LIVE"
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    with pytest.raises(AutonomousPolicyError, match="permanently PAPER-only"):
        parse_autonomous_policy(raw)


def test_parser_rejects_unknown_top_level_fields() -> None:
    payload = json.loads(autonomous_policy_bytes())
    payload["live_endpoint"] = "https://api.alpaca.markets"
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    with pytest.raises(AutonomousPolicyError, match=r"unknown=.*live_endpoint"):
        parse_autonomous_policy(raw)


def test_packaged_policy_is_bound_to_the_owner_approved_bytes() -> None:
    expected = "19c4669ad9fefce6ec7f03cec94ec6ce175fd8f969f7c883f0c045cbc6357b20"

    assert expected == ACCEPTED_AUTONOMOUS_POLICY_V1_SHA256
    assert autonomous_policy_sha256() == expected


def test_packaged_policy_retains_deterministic_execution_authority() -> None:
    policy = load_autonomous_policy()

    assert policy.reasoner_broker_authority is False
    assert policy.margin_funded_sizing is False
    assert policy.naked_options is False
    assert policy.uncovered_short_shares is False
    assert policy.unknown_broker_state_disables_entry is True
    assert policy.reconciliation_failure_disables_entry is True


@pytest.mark.parametrize(
    ("section", "field", "unsafe_value"),
    (
        ("reasoner", "broker_authority", True),
        ("risk", "defined_risk_spreads_only", False),
        ("risk", "liquidity_capacity_required", False),
        ("risk", "margin_funded_sizing", True),
        ("risk", "naked_options", True),
        ("risk", "uncovered_short_shares", True),
        ("risk", "unknown_broker_state_disables_entry", False),
        ("risk", "reconciliation_failure_disables_entry", False),
        ("memory", "online_self_training", True),
        ("memory", "policy_self_modification", True),
        ("memory", "lookahead_permitted", True),
    ),
)
def test_parser_rejects_unsafe_authority_mutations(
    section: str,
    field: str,
    unsafe_value: bool,
) -> None:
    payload = json.loads(autonomous_policy_bytes())
    nested = payload[section]
    assert isinstance(nested, dict)
    nested[field] = unsafe_value
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    with pytest.raises(AutonomousPolicyError, match="unsafe authority setting"):
        parse_autonomous_policy(raw)


def test_parser_rejects_noncanonical_bytes() -> None:
    payload = json.loads(autonomous_policy_bytes())
    noncanonical = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )

    with pytest.raises(AutonomousPolicyError, match="not canonical"):
        parse_autonomous_policy(noncanonical)


@pytest.mark.parametrize("section", ("strategy", "reasoner", "risk", "memory"))
def test_parser_rejects_unknown_nested_fields(section: str) -> None:
    payload = json.loads(autonomous_policy_bytes())
    nested = payload[section]
    assert isinstance(nested, dict)
    nested["hidden_override"] = True
    raw = (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )

    with pytest.raises(AutonomousPolicyError, match=f"{section}.*unknown=.*hidden_override"):
        parse_autonomous_policy(raw)
