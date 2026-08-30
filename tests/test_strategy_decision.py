from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from ringdown_market.alpha.models import Direction
from ringdown_market.strategy import (
    STRATEGY_POLICY_V1_SHA256,
    TRACE_STAGES,
    AbstentionReason,
    DecisionRejectionReason,
    ReactionRelation,
    StrategyDecision,
    StrategyDecisionRejected,
    StrategyDecisionState,
    parse_strategy_decision,
    strategy_decision_bytes,
    strategy_decision_sha256,
    validate_decision_policy_binding,
)

POLICY_SHA256 = "a" * 64
SNAPSHOT_SHA256 = "b" * 64
ROUTE_SHA256 = "c" * 64
REASONER_SHA256 = "d" * 64
CLAIM_SHA256 = "e" * 64
FALSIFIER_SHA256 = "f" * 64


def valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "esscher.strategy_decision",
        "schema_version": 1,
        "event_id": "synthetic-acme-2026q3",
        "issuer": "ACME SYNTHETIC CORP",
        "ticker": "ACME",
        "decision_cutoff": "2026-09-11T13:35:00Z",
        "feature_snapshot_at": "2026-09-11T13:35:00Z",
        "decided_at": "2026-09-11T13:36:00Z",
        "decision_deadline": "2026-09-11T13:36:05Z",
        "direction": "UP",
        "decision_state": "APPROVED",
        "abstention_reasons": [],
        "reaction_relation": "CONTINUE",
        "opening_residual": "0.012345",
        "evidence_citations": [
            {
                "citation_id": "c-1",
                "evidence_id": "issuer-release",
                "claim_sha256": CLAIM_SHA256,
            }
        ],
        "strongest_falsifier": {
            "falsifier_id": "f-1",
            "evidence_id": "sec-filing",
            "claim_sha256": FALSIFIER_SHA256,
        },
        "snapshot_sha256": SNAPSHOT_SHA256,
        "policy_version": "esscher-strategy-v1",
        "policy_sha256": POLICY_SHA256,
        "route_sha256": ROUTE_SHA256,
        "reasoner_output_sha256": REASONER_SHA256,
        "trace_stages": list(TRACE_STAGES),
        "claim": "NOT_ALPHA_EVIDENCE",
        "data_qualifiers": ["INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE"],
    }
    payload.update(overrides)
    return payload


def render(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def assert_rejected(
    payload: dict[str, Any], reason: DecisionRejectionReason
) -> StrategyDecisionRejected:
    with pytest.raises(StrategyDecisionRejected) as caught:
        parse_strategy_decision(render(payload))
    assert caught.value.reason is reason
    return caught.value


def approved_decision() -> StrategyDecision:
    return parse_strategy_decision(render(valid_payload()))


def test_approved_up_decision_parses() -> None:
    decision = approved_decision()
    assert decision.direction is Direction.UP
    assert decision.decision_state is StrategyDecisionState.APPROVED
    assert decision.is_abstention is False
    assert decision.reaction_relation is ReactionRelation.CONTINUE
    assert decision.opening_residual == Decimal("0.012345")
    assert decision.evidence_citations[0].evidence_id == "issuer-release"
    assert decision.strongest_falsifier.falsifier_id == "f-1"


def test_decision_serialization_is_byte_deterministic() -> None:
    decision = approved_decision()
    first = strategy_decision_bytes(decision)
    second = strategy_decision_bytes(decision)
    assert first == second
    assert first == render(valid_payload())
    assert strategy_decision_sha256(decision) == strategy_decision_sha256(decision)

    reparsed = parse_strategy_decision(first)
    assert reparsed == decision
    assert strategy_decision_bytes(reparsed) == first


def test_abstention_round_trip_keeps_zero_return_semantics() -> None:
    payload = valid_payload(
        direction="UNCERTAIN",
        decision_state="ABSTAIN",
        abstention_reasons=["MISSING_EVIDENCE", "CONFLICTING_EVIDENCE"],
        evidence_citations=[],
    )
    decision = parse_strategy_decision(render(payload))
    assert decision.is_abstention is True
    assert decision.abstention_reasons == (
        AbstentionReason.MISSING_EVIDENCE,
        AbstentionReason.CONFLICTING_EVIDENCE,
    )
    assert strategy_decision_bytes(parse_strategy_decision(strategy_decision_bytes(decision))) == (
        strategy_decision_bytes(decision)
    )


def test_abstention_reasons_force_uncertain_abstain() -> None:
    assert_rejected(
        valid_payload(abstention_reasons=["MISSING_EVIDENCE"]),
        DecisionRejectionReason.INVALID_ABSTENTION,
    )


def test_approved_decision_requires_directional_output() -> None:
    assert_rejected(
        valid_payload(direction="UNCERTAIN"),
        DecisionRejectionReason.INVALID_ABSTENTION,
    )


def test_approved_decision_requires_evidence_citation() -> None:
    assert_rejected(
        valid_payload(evidence_citations=[]),
        DecisionRejectionReason.INVALID_ABSTENTION,
    )


@pytest.mark.parametrize(
    "field", sorted(["limit_price", "quantity", "strategy", "permit_id", "symbol"])
)
def test_execution_fields_are_forbidden(field: str) -> None:
    assert_rejected(
        valid_payload(**{field: "1.25"}), DecisionRejectionReason.EXECUTION_FIELD_FORBIDDEN
    )


def test_unknown_field_rejected() -> None:
    assert_rejected(valid_payload(tuning_knob=1), DecisionRejectionReason.UNKNOWN_FIELD)


def test_duplicate_key_rejected() -> None:
    raw = render(valid_payload())
    text = raw.decode("utf-8")
    mutated = text.replace('"ticker":"ACME"', '"ticker":"ACME","ticker":"ACME"', 1)
    assert mutated != text
    with pytest.raises(StrategyDecisionRejected) as caught:
        parse_strategy_decision(mutated.encode("utf-8"))
    assert caught.value.reason is DecisionRejectionReason.DUPLICATE_KEY


def test_late_decision_rejected() -> None:
    assert_rejected(
        valid_payload(decided_at="2026-09-11T13:36:06Z"),
        DecisionRejectionReason.DEADLINE_VIOLATION,
    )


def test_post_cutoff_feature_snapshot_rejected() -> None:
    assert_rejected(
        valid_payload(feature_snapshot_at="2026-09-11T13:35:01Z"),
        DecisionRejectionReason.CUTOFF_VIOLATION,
    )


def test_non_finite_residual_rejected() -> None:
    assert_rejected(
        valid_payload(opening_residual="0.1234567"), DecisionRejectionReason.INVALID_TYPE
    )
    assert_rejected(valid_payload(opening_residual="1e3"), DecisionRejectionReason.INVALID_TYPE)


def test_trace_stages_must_be_exact() -> None:
    assert_rejected(
        valid_payload(trace_stages=["INPUT", "FEATURE", "REASONER", "OUTPUT"]),
        DecisionRejectionReason.INVALID_VALUE,
    )


def test_schema_mismatch_rejected() -> None:
    assert_rejected(
        valid_payload(schema="esscher.other_schema"), DecisionRejectionReason.UNSUPPORTED_SCHEMA
    )
    assert_rejected(valid_payload(schema_version=2), DecisionRejectionReason.UNSUPPORTED_SCHEMA)


def test_policy_binding_validation() -> None:
    decision = approved_decision()
    validate_decision_policy_binding(decision, expected_sha256=POLICY_SHA256)

    with pytest.raises(StrategyDecisionRejected) as caught:
        validate_decision_policy_binding(decision, expected_sha256=STRATEGY_POLICY_V1_SHA256)
    assert caught.value.reason is DecisionRejectionReason.POLICY_HASH_MISMATCH


def test_decision_bound_to_frozen_policy_version_only() -> None:
    assert_rejected(
        valid_payload(policy_version="esscher-strategy-v2"),
        DecisionRejectionReason.POLICY_HASH_MISMATCH,
    )


def test_future_timestamp_format_rejected() -> None:
    assert_rejected(
        valid_payload(decision_cutoff="2026-09-11T13:35:00+02:00"),
        DecisionRejectionReason.INVALID_TYPE,
    )


def test_post_init_rejects_naive_timestamps() -> None:
    decision = approved_decision()
    with pytest.raises(StrategyDecisionRejected) as caught:
        StrategyDecision(
            event_id=decision.event_id,
            issuer=decision.issuer,
            ticker=decision.ticker,
            decision_cutoff=datetime(2026, 9, 11, 13, 35, 0),
            feature_snapshot_at=decision.feature_snapshot_at,
            decided_at=decision.decided_at,
            decision_deadline=decision.decision_deadline,
            direction=decision.direction,
            decision_state=decision.decision_state,
            abstention_reasons=decision.abstention_reasons,
            reaction_relation=decision.reaction_relation,
            opening_residual=decision.opening_residual,
            evidence_citations=decision.evidence_citations,
            strongest_falsifier=decision.strongest_falsifier,
            snapshot_sha256=decision.snapshot_sha256,
            policy_version=decision.policy_version,
            policy_sha256=decision.policy_sha256,
            route_sha256=decision.route_sha256,
            reasoner_output_sha256=decision.reasoner_output_sha256,
            trace_stages=decision.trace_stages,
        )
    assert caught.value.reason is DecisionRejectionReason.INVALID_TYPE


def test_microsecond_timestamps_are_rejected() -> None:
    raw = render(valid_payload()).decode("utf-8")
    mutated = raw.replace('"2026-09-11T13:35:00Z"', '"2026-09-11T13:35:00.123Z"', 1)
    assert mutated != raw
    with pytest.raises(StrategyDecisionRejected) as caught:
        parse_strategy_decision(mutated.encode("utf-8"))
    assert caught.value.reason is DecisionRejectionReason.INVALID_TYPE


def test_decision_timestamps_are_utc_normalized() -> None:
    decision = approved_decision()
    assert decision.decision_cutoff.tzinfo is not None
    assert decision.decision_cutoff.utcoffset() == UTC.utcoffset(None)
