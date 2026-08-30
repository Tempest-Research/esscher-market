from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ringdown_market.alpha.baselines import BaselineName
from ringdown_market.alpha.models import Direction
from ringdown_market.alpha.qfast import CANDIDATE_METHOD, PanelRow, QFastStatus, run_qfast
from ringdown_market.data import run_capture_request
from ringdown_market.data.capture import CaptureRequestRejected
from ringdown_market.strategy import (
    STRATEGY_POLICY_V1_SHA256,
    AbstentionReason,
    DecisionEngine,
    FakeReasoner,
    ReactionRelation,
    ReasonerOutputRejected,
    ReasonerRejectionReason,
    ReasonerRoute,
    StrategyDecisionState,
    build_snapshot_baselines,
    compute_opening_residual,
    parse_frozen_strategy_policy_v1,
    parse_reasoner_output,
    run_route_smoke,
    strategy_decision_bytes,
    validate_decision_policy_binding,
    view_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "configs" / "strategy_v1.json"
CAPTURE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "strategy_v1_synthetic_capture_request.json"

ROUTE = ReasonerRoute(
    route_id="esscher-v1-route-1",
    prompt_sha256="a1" * 32,
    output_schema_sha256="b2" * 32,
)


def policy():
    return parse_frozen_strategy_policy_v1(POLICY_PATH.read_bytes())


def eligible_snapshot_bytes() -> bytes:
    payload = json.loads(CAPTURE_FIXTURE.read_text(encoding="utf-8"))
    snapshot = run_capture_request(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        policy=policy(),
        expected_policy_sha256=STRATEGY_POLICY_V1_SHA256,
    )
    assert snapshot.eligible
    return snapshot.raw


def tampered_snapshot_bytes(mutate) -> bytes:
    payload = json.loads(eligible_snapshot_bytes())
    mutate(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def reasoner_output_payload(
    *,
    direction: str = "UP",
    evidence_id: str = "issuer-release",
    falsifier_evidence_id: str | None = "sec-filing",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "esscher.reasoner_output",
        "schema_version": 1,
        "direction": direction,
        "confidence": "0.72",
        "citations": [
            {
                "citation_id": "c-1",
                "evidence_id": evidence_id,
                "claim_sha256": "1" * 64,
            }
        ],
        "falsifier": (
            {
                "falsifier_id": "f-1",
                "evidence_id": falsifier_evidence_id,
                "claim_sha256": "2" * 64,
            }
            if falsifier_evidence_id
            else None
        ),
    }
    payload.update(extra)
    return payload


def engine_with(reasoner_payload: dict[str, Any]) -> DecisionEngine:
    return DecisionEngine(
        policy=policy(),
        route=ROUTE,
        reasoner=FakeReasoner(reasoner_payload),
    )


def decided_at() -> datetime:
    return datetime(2026, 9, 11, 13, 36, 0, tzinfo=UTC)


def test_approved_up_decision_from_real_collector_snapshot() -> None:
    engine = engine_with(reasoner_output_payload())
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )

    assert decision.decision_state is StrategyDecisionState.APPROVED
    assert decision.direction is Direction.UP
    assert decision.reaction_relation is ReactionRelation.CONTINUE
    assert decision.opening_residual > 0
    assert decision.evidence_citations[0].evidence_id == "issuer-release"
    assert decision.strongest_falsifier is not None
    assert decision.policy_sha256 == STRATEGY_POLICY_V1_SHA256
    assert decision.snapshot_sha256 == view_snapshot(eligible_snapshot_bytes()).sha256
    assert decision.route_sha256 == ROUTE.sha256
    validate_decision_policy_binding(decision, expected_sha256=STRATEGY_POLICY_V1_SHA256)


def test_down_decision_reverses_against_positive_residual() -> None:
    engine = engine_with(reasoner_output_payload(direction="DOWN"))
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )
    assert decision.direction is Direction.DOWN
    assert decision.reaction_relation is ReactionRelation.REVERSE


def test_decision_bytes_are_deterministic() -> None:
    first_engine = engine_with(reasoner_output_payload())
    second_engine = engine_with(reasoner_output_payload())
    snapshot = eligible_snapshot_bytes()
    first = first_engine.generate_decision(snapshot_bytes=snapshot, decided_at=decided_at())
    second = second_engine.generate_decision(snapshot_bytes=snapshot, decided_at=decided_at())
    assert strategy_decision_bytes(first) == strategy_decision_bytes(second)


def test_policy_hash_drift_abstains() -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["policy_sha256"] = "0" * 64

    engine = engine_with(reasoner_output_payload())
    decision = engine.generate_decision(
        snapshot_bytes=tampered_snapshot_bytes(mutate), decided_at=decided_at()
    )
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.POLICY_HASH_MISMATCH,)


def test_duplicate_decision_abstains() -> None:
    engine = engine_with(reasoner_output_payload())
    snapshot = eligible_snapshot_bytes()
    first = engine.generate_decision(snapshot_bytes=snapshot, decided_at=decided_at())
    assert first.decision_state is StrategyDecisionState.APPROVED
    second = engine.generate_decision(snapshot_bytes=snapshot, decided_at=decided_at())
    assert second.is_abstention
    assert second.abstention_reasons == (AbstentionReason.DUPLICATE_DECISION,)


def test_ineligible_snapshot_with_post_cutoff_evidence_abstains_stale() -> None:
    payload = json.loads(CAPTURE_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence"][1]["published_at"] = "2026-09-11T13:36:00Z"
    snapshot = run_capture_request(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        policy=policy(),
        expected_policy_sha256=STRATEGY_POLICY_V1_SHA256,
    )
    assert not snapshot.eligible
    engine = engine_with(reasoner_output_payload())
    decision = engine.generate_decision(snapshot_bytes=snapshot.raw, decided_at=decided_at())
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.STALE_INPUT,)


def test_missing_evidence_snapshot_abstains() -> None:
    payload = json.loads(CAPTURE_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence"] = []
    snapshot = run_capture_request(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        policy=policy(),
        expected_policy_sha256=STRATEGY_POLICY_V1_SHA256,
    )
    engine = engine_with(reasoner_output_payload())
    decision = engine.generate_decision(snapshot_bytes=snapshot.raw, decided_at=decided_at())
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.MISSING_EVIDENCE,)


def test_missing_numeric_feature_abstains() -> None:
    def mutate(payload: dict[str, Any]) -> None:
        payload["features"] = [
            feature for feature in payload["features"] if feature["feature_id"] != "market_beta/v1"
        ]

    engine = engine_with(reasoner_output_payload())
    decision = engine.generate_decision(
        snapshot_bytes=tampered_snapshot_bytes(mutate), decided_at=decided_at()
    )
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.MISSING_EVIDENCE,)


def test_late_reasoner_output_abstains() -> None:
    engine = engine_with(reasoner_output_payload())
    late = datetime(2026, 9, 11, 13, 37, 0, tzinfo=UTC)
    decision = engine.generate_decision(snapshot_bytes=eligible_snapshot_bytes(), decided_at=late)
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.LATE_REASONER_OUTPUT,)


def test_deadline_boundary_is_inclusive() -> None:
    engine = engine_with(reasoner_output_payload())
    boundary = datetime(2026, 9, 11, 13, 36, 5, tzinfo=UTC)
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=boundary
    )
    assert decision.decision_state is StrategyDecisionState.APPROVED


def test_hostile_execution_fields_abstain() -> None:
    engine = engine_with(reasoner_output_payload(limit_price="1.25"))
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.INVALID_REASONER_OUTPUT,)


def test_invalid_reasoner_json_abstains() -> None:
    class BrokenReasoner:
        def reason(self, snapshot_payload):
            return b"{not-json"

    engine = DecisionEngine(policy=policy(), route=ROUTE, reasoner=BrokenReasoner())
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.INVALID_REASONER_OUTPUT,)


def test_uncertain_reasoner_output_abstains() -> None:
    engine = engine_with(reasoner_output_payload(direction="UNCERTAIN", falsifier_evidence_id=None))
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )
    assert decision.is_abstention


def test_unknown_evidence_citation_abstains_unbounded() -> None:
    engine = engine_with(reasoner_output_payload(evidence_id="ghost-evidence"))
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.UNBOUNDED_FALSIFIER,)


def test_directional_output_without_citations_abstains() -> None:
    payload = reasoner_output_payload()
    payload["citations"] = []
    engine = engine_with(payload)
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )
    assert decision.is_abstention
    assert decision.abstention_reasons == (AbstentionReason.INVALID_REASONER_OUTPUT,)


def test_strategy_modules_never_import_broker_surfaces() -> None:
    strategy_root = REPO_ROOT / "src" / "ringdown_market" / "strategy"
    forbidden = ("ringdown_market.execution", "ringdown_market.runtime")
    for source_file in sorted(strategy_root.glob("*.py")):
        text = source_file.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{source_file.name} imports {marker}"


def test_qfast_consumes_generated_decision_without_special_cases() -> None:
    engine = engine_with(reasoner_output_payload())
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )
    view = view_snapshot(eligible_snapshot_bytes())
    baselines = build_snapshot_baselines(compute_opening_residual(view))
    rows = [
        PanelRow(
            event_id=f"synthetic-acme-2026q3-{index}",
            signed_returns={CANDIDATE_METHOD: 0.004, **{name.value: 0.0 for name in baselines}},
            admitted={
                CANDIDATE_METHOD: decision.direction is not Direction.UNCERTAIN,
                **{
                    name.value: direction is not Direction.UNCERTAIN
                    for name, direction in baselines.items()
                },
            },
        )
        for index in range(20)
    ]
    report = run_qfast(rows)
    assert report.status is QFastStatus.NOT_REJECTED_SMALL_SAMPLE


def test_baselines_follow_opening_residual_sign() -> None:
    up = build_snapshot_baselines(Decimal("0.01"))
    down = build_snapshot_baselines(Decimal("-0.01"))
    flat = build_snapshot_baselines(Decimal("0"))
    missing = build_snapshot_baselines(None)
    assert up[BaselineName.GAP_CONTINUE] is Direction.UP
    assert up[BaselineName.GAP_REVERSE] is Direction.DOWN
    assert down[BaselineName.GAP_CONTINUE] is Direction.DOWN
    assert flat[BaselineName.GAP_CONTINUE] is Direction.UNCERTAIN
    assert missing[BaselineName.ALWAYS_ABSTAIN] is Direction.UNCERTAIN
    for mapping in (up, down, flat, missing):
        assert mapping[BaselineName.PRICE_ONLY] is Direction.UNCERTAIN
        assert mapping[BaselineName.FUNDAMENTAL_RULE] is Direction.UNCERTAIN
        assert mapping[BaselineName.NO_TEXT_ABLATION] is Direction.UNCERTAIN


def test_route_smoke_records_latency_and_schema_outcomes() -> None:
    ticks = iter(range(0, 1000, 7))

    def clock_ms() -> int:
        return next(ticks)

    result = run_route_smoke(
        route=ROUTE,
        reasoner=FakeReasoner(reasoner_output_payload()),
        snapshot_payload={},
        attempts=5,
        clock_ms=clock_ms,
    )
    assert result.attempts == 5
    assert result.schema_valid == 5
    assert result.schema_invalid == 0
    assert result.route_sha256 == ROUTE.sha256
    assert result.p95_latency_ms is not None


def test_reasoner_output_parser_rejections() -> None:
    def render(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(ReasonerOutputRejected) as caught:
        parse_reasoner_output(render(reasoner_output_payload(quantity=5)))
    assert caught.value.reason is ReasonerRejectionReason.EXECUTION_FIELD_FORBIDDEN

    with pytest.raises(ReasonerOutputRejected) as caught:
        parse_reasoner_output(render(reasoner_output_payload(direction="SIDEWAYS")))
    assert caught.value.reason is ReasonerRejectionReason.INVALID_VALUE

    missing = reasoner_output_payload()
    del missing["citations"]
    with pytest.raises(ReasonerOutputRejected) as caught:
        parse_reasoner_output(render(missing))
    assert caught.value.reason is ReasonerRejectionReason.MISSING_FIELD

    unknown = reasoner_output_payload(extra_field=1)
    with pytest.raises(ReasonerOutputRejected) as caught:
        parse_reasoner_output(render(unknown))
    assert caught.value.reason is ReasonerRejectionReason.UNKNOWN_FIELD

    bad_confidence = reasoner_output_payload()
    bad_confidence["confidence"] = "1.5"
    with pytest.raises(ReasonerOutputRejected) as caught:
        parse_reasoner_output(render(bad_confidence))
    assert caught.value.reason is ReasonerRejectionReason.INVALID_VALUE


def test_snapshot_schema_rejected_by_engine_view() -> None:
    from ringdown_market.strategy import EngineRejected, EngineRejectionReason

    with pytest.raises(EngineRejected) as caught:
        view_snapshot(b'{"schema":"esscher.other","schema_version":1}')
    assert caught.value.reason is EngineRejectionReason.UNSUPPORTED_SNAPSHOT_SCHEMA


def test_capture_request_still_rejects_unknown_schema() -> None:
    payload = json.loads(CAPTURE_FIXTURE.read_text(encoding="utf-8"))
    payload["schema"] = "esscher.other_request"
    with pytest.raises(CaptureRequestRejected):
        run_capture_request(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            policy=policy(),
            expected_policy_sha256=STRATEGY_POLICY_V1_SHA256,
        )


def test_abstention_decision_serializes_with_null_falsifier() -> None:
    engine = engine_with(reasoner_output_payload(limit_price="1.25"))
    decision = engine.generate_decision(
        snapshot_bytes=eligible_snapshot_bytes(), decided_at=decided_at()
    )
    raw = strategy_decision_bytes(decision)
    payload = json.loads(raw)
    assert payload["strongest_falsifier"] is None
    assert payload["abstention_reasons"] == [AbstentionReason.INVALID_REASONER_OUTPUT.value]
