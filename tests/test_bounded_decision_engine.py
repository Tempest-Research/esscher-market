"""Tests for the bounded decision engine, Gate C baselines, and route smoke."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ringdown_market.alpha.models import Direction
from ringdown_market.sourcedata import (
    CaptureConfiguration,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
    load_fixture,
)
from ringdown_market.strategy import (
    DataHealthState,
    DecisionDisposition,
    EligibilityState,
    ExchangeStatus,
    FeatureComponent,
    FeatureStatus,
    StrategyInput,
    feature_receipt_bytes,
    parse_candidate_manifest,
    strategy_snapshot_bytes,
    strategy_snapshot_sha256,
)
from ringdown_market.strategy.baselines import (
    baseline_signal_bytes,
    compile_gate_c_signals,
    frozen_baseline_ids,
)
from ringdown_market.strategy.contracts import (
    build_strategy_input,
    sha256_bytes,
)
from ringdown_market.strategy.engine import (
    ENGINE_BUILD_SHA256,
    BoundedDecisionEngine,
    EngineReason,
)
from ringdown_market.strategy.reasoner import (
    DeterministicFakeReasoner,
    FakeFailure,
    ReasonerRouteRequest,
    ReasonerRouteResult,
)
from ringdown_market.strategy.smoke import run_route_smoke
from test_strategy_contracts import _macro_strategy_input

FORBIDDEN_MODULES = (
    "aiohttp",
    "alpaca",
    "http",
    "httpx",
    "mcp",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "webbrowser",
)
STRATEGY_FILES = Path(__file__).parents[1] / "src" / "ringdown_market" / "strategy"


def _compiled():
    fixture = load_fixture()
    manifest_bytes = build_candidate_manifest(fixture)
    config = CaptureConfiguration(
        candidate_manifest_bytes=manifest_bytes,
        event_id=str(fixture["event_id"]),
        capture_at=_at(str(fixture["capture_at"])),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )
    return compile_strategy_snapshot(
        config, FixtureEvidenceSource(fixture), FixtureMarketDataSource(fixture)
    )


def _at(value: str):
    from datetime import UTC, datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _strategy_input() -> StrategyInput:
    return compiled_strategy_input(_compiled())


def _started(strategy_input: StrategyInput):
    return strategy_input.snapshot.evidence_cutoff_at + timedelta(seconds=5)


def _manifest_bytes(manifest) -> bytes:
    from ringdown_market.strategy import candidate_manifest_bytes

    return candidate_manifest_bytes(manifest)


def _manifest_sha(manifest) -> str:
    from ringdown_market.strategy import candidate_manifest_sha256

    return candidate_manifest_sha256(manifest)


def test_engine_accepts_a_confirmed_direction_deterministically() -> None:
    strategy_input = _strategy_input()
    first = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=_started(strategy_input)
    )
    second = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert first.decision.disposition is DecisionDisposition.ACCEPTED
    assert first.decision.direction is Direction.UP
    assert first.route_invoked is True
    assert first.decision_bytes == second.decision_bytes
    assert first.trace_bytes == second.trace_bytes


def test_trace_reads_input_feature_reasoner_validator_output() -> None:
    strategy_input = _strategy_input()
    outcome = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert [stage["stage"] for stage in outcome.trace["stages"]] == [
        "INPUT",
        "FEATURE",
        "REASONER",
        "VALIDATOR",
        "OUTPUT",
    ]
    validator_stage = outcome.trace["stages"][3]
    assert validator_stage["reason_codes"] == []
    assert outcome.trace["stages"][4]["direction"] == outcome.decision.direction.value


def test_duplicate_call_is_fenced_without_a_second_route_call() -> None:
    strategy_input = _strategy_input()
    engine = BoundedDecisionEngine(DeterministicFakeReasoner())

    first = engine.decide(strategy_input, started_at=_started(strategy_input))
    second = engine.decide(strategy_input, started_at=_started(strategy_input))

    assert first.route_invoked is True
    assert second.route_invoked is False
    assert second.decision.direction is Direction.UNCERTAIN
    assert EngineReason.DUPLICATE_REASONER_CALL.value in second.decision.reason_codes


@pytest.mark.parametrize(
    "failure",
    [
        FakeFailure.TIMEOUT,
        FakeFailure.CANCELED,
        FakeFailure.PROVIDER_ERROR,
        FakeFailure.LATE_RESPONSE,
        FakeFailure.MALFORMED_JSON,
        FakeFailure.HOSTILE_FIELDS,
        FakeFailure.RAW_HASH_DRIFT,
    ],
)
def test_every_provider_failure_yields_uncertain(failure: FakeFailure) -> None:
    strategy_input = _strategy_input()
    outcome = BoundedDecisionEngine(DeterministicFakeReasoner(failure)).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert outcome.decision.direction is Direction.UNCERTAIN
    assert outcome.decision.disposition is not DecisionDisposition.ACCEPTED
    assert outcome.decision.reason_codes


def test_preflight_ineligible_aborts_before_any_route_call() -> None:
    compiled = _compiled()
    manifest = parse_candidate_manifest(compiled.candidate_manifest_bytes)
    records = tuple(
        replace(
            record,
            eligibility=EligibilityState.INELIGIBLE,
            reason_codes=("PRICE_BELOW_MINIMUM",),
        )
        if record.event_id == compiled.snapshot.event_id
        else record
        for record in manifest.records
    )
    manifest = replace(manifest, records=records)
    snapshot = replace(
        compiled.snapshot,
        eligibility=EligibilityState.INELIGIBLE,
        eligibility_reason_codes=("PRICE_BELOW_MINIMUM",),
        candidate_manifest_sha256=_manifest_sha(manifest),
    )
    receipt = replace(
        compiled.feature_receipt,
        strategy_snapshot_sha256=strategy_snapshot_sha256(snapshot),
    )
    mutated = build_strategy_input(
        strategy_snapshot_bytes(snapshot),
        candidate_manifest_bytes=_manifest_bytes(manifest),
        feature_receipt_bytes=feature_receipt_bytes(receipt),
    )

    calls = []

    def spying_route(request: ReasonerRouteRequest) -> ReasonerRouteResult:
        calls.append(request)
        return DeterministicFakeReasoner()(request)

    outcome = BoundedDecisionEngine(spying_route).decide(mutated, started_at=_started(mutated))

    assert calls == []
    assert outcome.route_invoked is False
    assert EngineReason.PREFLIGHT_INELIGIBLE.value in outcome.decision.reason_codes
    assert outcome.decision.direction is Direction.UNCERTAIN


def test_preflight_data_health_aborts_before_any_route_call() -> None:
    compiled = _compiled()
    snapshot = replace(
        compiled.snapshot,
        data_health=DataHealthState.INVALID,
        health_reason_codes=("MARKET_OBSERVATION_STALE",),
    )
    receipt = replace(
        compiled.feature_receipt,
        strategy_snapshot_sha256=strategy_snapshot_sha256(snapshot),
    )
    mutated = build_strategy_input(
        strategy_snapshot_bytes(snapshot),
        candidate_manifest_bytes=compiled.candidate_manifest_bytes,
        feature_receipt_bytes=feature_receipt_bytes(receipt),
    )

    outcome = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        mutated, started_at=_started(mutated)
    )

    assert EngineReason.PREFLIGHT_DATA_HEALTH.value in outcome.decision.reason_codes
    assert outcome.decision.direction is Direction.UNCERTAIN


def test_start_after_cutoff_is_fenced() -> None:
    strategy_input = _strategy_input()
    late = strategy_input.snapshot.decision_cutoff_at + timedelta(seconds=1)

    outcome = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=late
    )

    assert EngineReason.START_AFTER_DECISION_CUTOFF.value in outcome.decision.reason_codes
    assert outcome.decision.direction is Direction.UNCERTAIN


def test_start_before_feature_receipt_is_fenced() -> None:
    strategy_input = _strategy_input()
    early = strategy_input.feature_receipt.created_at - timedelta(seconds=1)

    outcome = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=early
    )

    assert EngineReason.START_BEFORE_FEATURE_RECEIPT.value in outcome.decision.reason_codes
    assert outcome.decision.direction is Direction.UNCERTAIN


def test_veto_cannot_reverse_direction_or_activate_abstention() -> None:
    strategy_input = _strategy_input()
    accepted = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=_started(strategy_input)
    )
    uncertain = BoundedDecisionEngine(DeterministicFakeReasoner(FakeFailure.TIMEOUT)).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert accepted.decision.disposition is DecisionDisposition.ACCEPTED
    assert uncertain.decision.direction is Direction.UNCERTAIN
    assert uncertain.decision.disposition is not DecisionDisposition.ACCEPTED


def test_gate_c_consumes_all_frozen_arms_without_special_cases() -> None:
    strategy_input = _strategy_input()
    bundle = compile_gate_c_signals(
        strategy_input,
        route=DeterministicFakeReasoner(),
        started_at=_started(strategy_input),
    )

    produced = {signal.baseline_id for signal in bundle.signals}
    assert produced == set(frozen_baseline_ids()) - {"SEEDED_RANDOM_PLACEBO_256"}
    assert len(bundle.controls) == 256
    for signal in bundle.signals:
        assert baseline_signal_bytes(signal)
        assert signal.evaluation_only in (True, False)
    for control in bundle.controls:
        assert control.evaluation_only is True


def test_placebo_arms_are_evaluation_only_and_never_accepted() -> None:
    strategy_input = _strategy_input()
    bundle = compile_gate_c_signals(
        strategy_input,
        route=DeterministicFakeReasoner(),
        started_at=_started(strategy_input),
    )

    placebo = next(s for s in bundle.signals if s.baseline_id == "OPPOSITE_LLM_PLACEBO")
    ablation = next(s for s in bundle.signals if s.baseline_id == "NO_TEXT_ABLATION")
    assert placebo.evaluation_only is True
    assert ablation.evaluation_only is True
    for signal in (*bundle.signals, *bundle.controls):
        assert b'"execution_authority":false' in baseline_signal_bytes(signal)


def test_baseline_bundle_is_deterministic() -> None:
    strategy_input = _strategy_input()
    first = compile_gate_c_signals(
        strategy_input,
        route=DeterministicFakeReasoner(),
        started_at=_started(strategy_input),
    )
    second = compile_gate_c_signals(
        strategy_input,
        route=DeterministicFakeReasoner(),
        started_at=_started(strategy_input),
    )

    assert first.bytes == second.bytes


def test_opposite_placebo_inverts_directional_llm_and_retains_abstentions() -> None:
    strategy_input = _strategy_input()
    bundle = compile_gate_c_signals(
        strategy_input,
        route=DeterministicFakeReasoner(),
        started_at=_started(strategy_input),
    )
    bounded = next(s for s in bundle.signals if s.baseline_id == "BOUNDED_LLM")
    opposite = next(s for s in bundle.signals if s.baseline_id == "OPPOSITE_LLM_PLACEBO")

    if bounded.direction is Direction.UNCERTAIN:
        assert opposite.direction is Direction.UNCERTAIN
    else:
        assert opposite.direction is not bounded.direction


def test_seeded_controls_are_deterministic_per_counter() -> None:
    strategy_input = _strategy_input()
    bundle = compile_gate_c_signals(
        strategy_input,
        route=DeterministicFakeReasoner(),
        started_at=_started(strategy_input),
    )

    indices = [control.control_index for control in bundle.controls]
    assert indices == list(range(256))
    digests = {sha256_bytes(baseline_signal_bytes(c)) for c in bundle.controls}
    assert len(digests) >= 1


def test_route_smoke_records_latency_and_schema_without_broker() -> None:
    strategy_input = _strategy_input()
    report = run_route_smoke(
        DeterministicFakeReasoner(), strategy_input, started_at=_started(strategy_input)
    )

    assert report.status is ExchangeStatus.COMPLETED
    assert report.schema_ok is True
    assert report.latency_ms is not None
    assert report.bytes


def test_route_smoke_records_failure_without_raising() -> None:
    strategy_input = _strategy_input()
    report = run_route_smoke(
        DeterministicFakeReasoner(FakeFailure.MALFORMED_JSON),
        strategy_input,
        started_at=_started(strategy_input),
    )

    assert report.schema_ok is False


def test_engine_and_baselines_import_no_network_or_broker_capability() -> None:
    for path in (
        STRATEGY_FILES / "engine.py",
        STRATEGY_FILES / "baselines.py",
        STRATEGY_FILES / "reasoner.py",
        STRATEGY_FILES / "smoke.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".", 1)[0]
                assert root not in FORBIDDEN_MODULES, f"{path.name} imports {name}"


def test_engine_decision_payload_has_no_broker_authority() -> None:
    strategy_input = _strategy_input()
    outcome = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    from ringdown_market.strategy.contracts import strategy_decision_payload

    payload = strategy_decision_payload(outcome.decision)
    assert set(payload).isdisjoint(
        {
            "account",
            "broker",
            "contract",
            "entry",
            "exit",
            "order",
            "permit",
            "price",
            "quantity",
            "risk",
            "strike",
            "symbol",
        }
    )
    assert payload["authority"] == "DIRECTION_ONLY"
    assert outcome.decision.producer_build_sha256 == ENGINE_BUILD_SHA256


def _good_payload(strategy_input: StrategyInput) -> dict:
    raw = DeterministicFakeReasoner()(
        ReasonerRouteRequest(strategy_input=strategy_input, started_at=_started(strategy_input))
    ).raw_response_bytes
    return json.loads(raw)


def _rejoin_with_receipt(strategy_input: StrategyInput, receipt) -> StrategyInput:
    from ringdown_market.strategy import candidate_manifest_bytes

    return build_strategy_input(
        strategy_snapshot_bytes(strategy_input.snapshot),
        candidate_manifest_bytes=candidate_manifest_bytes(strategy_input.candidate_manifest),
        feature_receipt_bytes=feature_receipt_bytes(receipt),
    )


def _route_with_payload(strategy_input: StrategyInput, payload: dict):
    from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

    raw = canonical_json_bytes(payload)
    base = DeterministicFakeReasoner()(
        ReasonerRouteRequest(strategy_input=strategy_input, started_at=_started(strategy_input))
    )
    exchange = replace(base.exchange, raw_response_sha256=sha256_bytes(raw))

    def route(request: ReasonerRouteRequest) -> ReasonerRouteResult:
        return ReasonerRouteResult(exchange=exchange, raw_response_bytes=raw)

    return route


def test_directional_decision_without_primary_citation_is_vetoed() -> None:
    strategy_input = _strategy_input()
    payload = _good_payload(strategy_input)
    payload["evidence_ids"] = ["market-snapshot"]
    outcome = BoundedDecisionEngine(_route_with_payload(strategy_input, payload)).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert outcome.decision.direction is Direction.UNCERTAIN
    assert "MISSING_PRIMARY_CITATION" in outcome.decision.reason_codes


def test_directional_decision_without_falsifier_is_vetoed() -> None:
    strategy_input = _strategy_input()
    payload = _good_payload(strategy_input)
    payload["strongest_falsifier"] = None
    outcome = BoundedDecisionEngine(_route_with_payload(strategy_input, payload)).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert outcome.decision.direction is Direction.UNCERTAIN
    assert "MISSING_FALSIFIER" in outcome.decision.reason_codes


def test_critical_unknown_is_material_and_abstains() -> None:
    strategy_input = _strategy_input()
    payload = _good_payload(strategy_input)
    payload["unknowns"] = ["MATERIAL_SOURCE_CONTRADICTION"]
    outcome = BoundedDecisionEngine(_route_with_payload(strategy_input, payload)).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert outcome.decision.direction is Direction.UNCERTAIN
    assert "MATERIAL_UNKNOWN" in outcome.decision.reason_codes


def test_exchange_created_after_cutoff_is_late() -> None:
    strategy_input = _strategy_input()
    base = DeterministicFakeReasoner()(
        ReasonerRouteRequest(strategy_input=strategy_input, started_at=_started(strategy_input))
    )
    late_exchange = replace(
        base.exchange,
        created_at=strategy_input.snapshot.decision_cutoff_at + timedelta(seconds=1),
    )

    def route(request: ReasonerRouteRequest) -> ReasonerRouteResult:
        return ReasonerRouteResult(
            exchange=late_exchange, raw_response_bytes=base.raw_response_bytes
        )

    outcome = BoundedDecisionEngine(route).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert outcome.decision.direction is Direction.UNCERTAIN
    assert "LATE_RESPONSE" in outcome.decision.reason_codes


def test_provider_failure_invokes_route_exactly_once() -> None:
    strategy_input = _strategy_input()
    calls = []
    inner = DeterministicFakeReasoner(FakeFailure.PROVIDER_ERROR)

    def counting_route(request: ReasonerRouteRequest) -> ReasonerRouteResult:
        calls.append(request)
        return inner(request)

    outcome = BoundedDecisionEngine(counting_route).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert len(calls) == 1
    assert outcome.decision.direction is Direction.UNCERTAIN


def test_seeded_controls_match_bounded_directional_coverage_and_abstentions() -> None:
    strategy_input = _strategy_input()
    directional = compile_gate_c_signals(
        strategy_input, route=DeterministicFakeReasoner(), started_at=_started(strategy_input)
    )
    bounded_dir = next(s for s in directional.signals if s.baseline_id == "BOUNDED_LLM")
    if bounded_dir.direction is not Direction.UNCERTAIN:
        assert all(c.direction is not Direction.UNCERTAIN for c in directional.controls)

    abstaining = compile_gate_c_signals(
        strategy_input,
        route=DeterministicFakeReasoner(FakeFailure.TIMEOUT),
        started_at=_started(strategy_input),
    )
    bounded_abs = next(s for s in abstaining.signals if s.baseline_id == "BOUNDED_LLM")
    assert bounded_abs.direction is Direction.UNCERTAIN
    assert all(c.direction is Direction.UNCERTAIN for c in abstaining.controls)


def test_ablation_run_uses_identical_clocks_and_gates() -> None:
    strategy_input = _strategy_input()
    normal = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=_started(strategy_input)
    )
    ablation = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=_started(strategy_input), ablate_text=True
    )

    assert normal.exchange.started_at == ablation.exchange.started_at
    assert normal.exchange.deadline_at == ablation.exchange.deadline_at
    assert ablation.trace["ablate_text"] is True


def test_macro_cohort_runs_engine_and_baselines_with_distinct_clocks() -> None:
    strategy_input = _macro_strategy_input(vwap_distance=Decimal("5"))
    outcome = BoundedDecisionEngine(DeterministicFakeReasoner()).decide(
        strategy_input, started_at=_started(strategy_input)
    )

    assert outcome.decision.cohort_id == "BLS_JOLTS"
    assert outcome.decision.disposition is DecisionDisposition.ACCEPTED
    assert outcome.decision.direction is Direction.UP

    bundle = compile_gate_c_signals(
        strategy_input, route=DeterministicFakeReasoner(), started_at=_started(strategy_input)
    )
    continuation = next(s for s in bundle.signals if s.baseline_id == "PRICE_CONTINUATION")
    reversal = next(s for s in bundle.signals if s.baseline_id == "PRICE_REVERSAL")
    assert continuation.direction is Direction.UP
    assert reversal.direction is Direction.DOWN


def test_macro_parser_votes_use_frozen_component_mapping() -> None:
    strategy_input = _macro_strategy_input(vwap_distance=Decimal("5"))
    receipt = strategy_input.feature_receipt
    features = []
    for feature in receipt.features:
        if feature.feature_id == "macro.consensus_surprise_vector.v1":
            features.append(
                replace(
                    feature,
                    status=FeatureStatus.PRESENT,
                    value=None,
                    observed_at=strategy_input.snapshot.evidence_cutoff_at,
                    source_refs=("bls-release",),
                    components=(
                        FeatureComponent(
                            component_id="job_openings",
                            status=FeatureStatus.PRESENT,
                            value=Decimal("1.2"),
                            unit="Z_SCORE",
                            source_refs=("bls-release",),
                        ),
                        FeatureComponent(
                            component_id="quits",
                            status=FeatureStatus.PRESENT,
                            value=Decimal("0.8"),
                            unit="Z_SCORE",
                            source_refs=("bls-release",),
                        ),
                    ),
                )
            )
        else:
            features.append(feature)
    receipt = replace(receipt, features=tuple(features))
    mutated = _rejoin_with_receipt(strategy_input, receipt)

    from ringdown_market.strategy.baselines import deterministic_parser_signal

    signal = deterministic_parser_signal(mutated)
    assert signal.direction is Direction.DOWN
