from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from ringdown_market.alpha.baselines import BaselineName
from ringdown_market.strategy import (
    STRATEGY_POLICY_V1_SHA256,
    STRATEGY_POLICY_VERSION,
    AbstentionReason,
    PolicyRejectionReason,
    StrategyPolicyRejected,
    parse_frozen_strategy_policy_v1,
    parse_strategy_policy,
    strategy_policy_sha256,
)

POLICY_PATH = Path(__file__).resolve().parents[1] / "configs" / "strategy_v1.json"


def raw_policy() -> bytes:
    return POLICY_PATH.read_bytes()


def mutated_policy(old: bytes, new: bytes) -> bytes:
    raw = raw_policy()
    assert old in raw, "mutation anchor missing from canonical policy"
    mutated = raw.replace(old, new, 1)
    assert mutated != raw
    return mutated


def parse_mutated(raw: bytes) -> object:
    return parse_strategy_policy(raw, expected_sha256=strategy_policy_sha256(raw))


def assert_policy_rejected(raw: bytes, reason: PolicyRejectionReason) -> StrategyPolicyRejected:
    with pytest.raises(StrategyPolicyRejected) as caught:
        parse_mutated(raw)
    assert caught.value.reason is reason
    return caught.value


def test_canonical_policy_bytes_match_pinned_frozen_identity() -> None:
    raw = raw_policy()
    assert strategy_policy_sha256(raw) == STRATEGY_POLICY_V1_SHA256

    policy = parse_frozen_strategy_policy_v1(raw)
    assert policy.policy_version == STRATEGY_POLICY_VERSION
    assert policy.frozen is True
    assert policy.sha256 == STRATEGY_POLICY_V1_SHA256


def test_policy_parse_is_deterministic() -> None:
    first = parse_frozen_strategy_policy_v1(raw_policy())
    second = parse_frozen_strategy_policy_v1(raw_policy())
    assert first == second
    assert first.sha256 == second.sha256


def test_policy_freezes_issue_contract_values() -> None:
    policy = parse_frozen_strategy_policy_v1(raw_policy())

    assert policy.universe.minimum_price == Decimal("10.00")
    assert policy.universe.earnings_timing == ("BMO", "AMC")
    assert policy.timing.timezone == "America/New_York"
    assert policy.timing.observation_window_start == "09:30:00"
    assert policy.timing.observation_window_end == "09:35:00"
    assert policy.timing.valid_signal_by == "09:36:05"
    assert policy.timing.no_open_submission_after == "09:37:00"
    assert policy.timing.close_all_positions_by == "15:30:00"
    assert policy.decision.outputs == ("UP", "DOWN", "UNCERTAIN")
    assert policy.decision.confidence_authorizes_trade is False
    assert policy.decision.prose_controls_arithmetic is False
    assert policy.decision.no_fallback_signal is True
    assert policy.decision.reaction_relation_values == ("CONTINUE", "REVERSE", "NONE")
    assert policy.reasoner.route_count == 1
    assert policy.reasoner.transparent_fallback is False
    assert policy.expression.kind == "DEBIT_VERTICAL"
    assert policy.expression.quantity == 1
    assert policy.expression.dte_min_days == 7
    assert policy.expression.dte_max_days == 21
    assert policy.expression.allowed_widths_usd == (Decimal("2.50"), Decimal("5.00"))
    assert policy.exit.hold_minutes == 60
    assert policy.exit.hold_anchor == "RECONCILED_OPENING_FILL"
    assert policy.exit.model_exit is False
    assert policy.exit.profit_take is False
    assert policy.exit.stop_loss is False
    assert policy.evidence_requirements.panel_min_events == 20
    assert policy.evidence_requirements.panel_max_events == 30
    assert policy.evidence_requirements.latency_required_profile == "p95"
    assert policy.evidence_requirements.historical_confirmation_required_before_paper_mutation
    assert policy.evidence_requirements.prospective_shadow_required_before_paper_mutation
    assert policy.event_sets.development_event_ids == (
        "KR-2026Q2-EARNINGS",
        "GIS-2027Q1-EARNINGS",
        "MU-2026Q4-EARNINGS",
        "NKE-2027Q1-EARNINGS",
    )
    assert policy.event_sets.development_events_excluded_from_confirmation_panel is True
    assert policy.boundaries.execution_mode == "PAPER_ONLY"
    assert policy.boundaries.real_money_mode is False


def test_policy_baselines_align_with_alpha_plane() -> None:
    policy = parse_frozen_strategy_policy_v1(raw_policy())
    assert policy.baselines == tuple(baseline.value for baseline in BaselineName)


def test_policy_abstention_codes_align_with_decision_contract() -> None:
    policy = parse_frozen_strategy_policy_v1(raw_policy())
    config_codes = {rule.code for rule in policy.decision.abstention_rules}
    assert config_codes == {reason.value for reason in AbstentionReason}


def test_policy_document_is_inert() -> None:
    raw = raw_policy()
    for marker in (b"http://", b"https://", b"ftp://", b"file://"):
        assert marker not in raw


def test_policy_mutation_after_freeze_rejected() -> None:
    mutated = mutated_policy(b'"hold_minutes": 60', b'"hold_minutes": 61')
    with pytest.raises(StrategyPolicyRejected) as caught:
        parse_frozen_strategy_policy_v1(mutated)
    assert caught.value.reason is PolicyRejectionReason.POLICY_HASH_MISMATCH


def test_policy_rejects_unfrozen_document() -> None:
    assert_policy_rejected(
        mutated_policy(b'"frozen": true', b'"frozen": false'),
        PolicyRejectionReason.UNFROZEN_POLICY,
    )


def test_policy_rejects_unknown_field() -> None:
    mutated = mutated_policy(
        b'"policy_id": "ESSCHER_STRATEGY_V1",',
        b'"policy_id": "ESSCHER_STRATEGY_V1",\n  "tuning_knob": 1,',
    )
    assert_policy_rejected(mutated, PolicyRejectionReason.UNKNOWN_FIELD)


def test_policy_rejects_missing_field() -> None:
    mutated = mutated_policy(b'  "product_name": "Esscher",\n', b"")
    assert_policy_rejected(mutated, PolicyRejectionReason.MISSING_FIELD)


def test_policy_rejects_duplicate_key() -> None:
    mutated = mutated_policy(
        b'"policy_id": "ESSCHER_STRATEGY_V1",',
        b'"policy_id": "ESSCHER_STRATEGY_V1",\n  "policy_id": "ESSCHER_STRATEGY_V1",',
    )
    assert_policy_rejected(mutated, PolicyRejectionReason.DUPLICATE_KEY)


def test_policy_rejects_non_finite_value() -> None:
    mutated = mutated_policy(
        b'"abstention_signed_return": 0.0',
        b'"abstention_signed_return": NaN',
    )
    assert_policy_rejected(mutated, PolicyRejectionReason.NON_FINITE_VALUE)


def test_policy_rejects_future_timestamp() -> None:
    mutated = mutated_policy(
        b'"frozen_at": "2026-08-30T14:12:39Z"',
        b'"frozen_at": "2999-01-01T00:00:00Z"',
    )
    assert_policy_rejected(mutated, PolicyRejectionReason.FUTURE_TIMESTAMP)


def test_policy_rejects_invalid_decimal_text() -> None:
    mutated = mutated_policy(b'"minimum_price_usd": "10.00"', b'"minimum_price_usd": "10.005"')
    assert_policy_rejected(mutated, PolicyRejectionReason.INVALID_TYPE)


def test_policy_rejects_broken_timing_order() -> None:
    mutated = mutated_policy(b'"valid_signal_by": "09:36:05"', b'"valid_signal_by": "09:34:00"')
    assert_policy_rejected(mutated, PolicyRejectionReason.INVALID_VALUE)


def test_policy_rejects_quantity_greater_than_one() -> None:
    mutated = mutated_policy(b'"quantity": 1', b'"quantity": 2')
    assert_policy_rejected(mutated, PolicyRejectionReason.INVALID_VALUE)


def test_policy_rejects_non_bytes_input() -> None:
    with pytest.raises(StrategyPolicyRejected) as caught:
        parse_strategy_policy("not-bytes", expected_sha256="0" * 64)  # type: ignore[arg-type]
    assert caught.value.reason is PolicyRejectionReason.INVALID_DOCUMENT
