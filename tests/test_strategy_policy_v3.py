"""V3 delayed-capture demo lane: policy package, registry, bridge, expression flag.

The V3 generation (owner-approved 2026-09-04, MS-Mesh, #68/#101) keeps the
frozen 09:30:00-09:35:00 ET signal window and every validation computation
byte-identical to V1; only the capture/decision/entry clocks shift so the
window can be retrieved as legal fifteen-minute-old historical SIP data on the
Basic data plan.  These tests pin: the V3 package identity and clocks, V1/V2
immutability, the additive reasoner registry, the confirmation-bridge reuse,
the expression policy's opt-in indicative flag (with byte-stable legacy
digests), and a FULL compile of the packaged fixture through the V3 clocks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from esscher.application.autonomous_bridge import (
    confirmation_epsilon,
    confirmation_epsilon_map,
)
from esscher.execution.expression.observations import (
    EXECUTABLE_DATA,
    INDICATIVE_DATA,
)
from esscher.execution.expression.policy import (
    parse_promoted_expression_policy,
    promoted_expression_policy_bytes,
    promoted_expression_policy_payload,
)
from esscher.execution.expression.reasons import (
    ExpressionReason,
    ExpressionRejected,
)
from esscher.execution.expression.validation import validate_executable_data
from esscher.runtime.host_composition import (
    demo_delayed_promoted_expression_policy,
    synthetic_promoted_expression_policy,
)
from esscher.sourcedata.compiler import (
    CaptureConfiguration,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from esscher.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    load_fixture,
)
from esscher.strategy.contracts import (
    canonical_json_bytes,
    reasoner_policy_hashes,
    reasoner_system_prompt_bytes,
    reasoner_system_prompt_sha256,
)
from esscher.strategy.policy import (
    ACCEPTED_EVENT_POLICY_V1_SHA256,
    ACCEPTED_EVENT_POLICY_V2_SHA256,
    ACCEPTED_EVENT_POLICY_V3_SHA256,
    StrategyPolicyError,
    load_strategy_policy,
    load_strategy_policy_v2,
    load_strategy_policy_v3,
    parse_strategy_policy_v3,
    strategy_policy_sha256,
    strategy_policy_v3_bytes,
)

V3_CANDIDATE = "EARNINGS_RESIDUAL_CONTINUATION_V3"


# --- policy package ----------------------------------------------------------


def test_v3_package_identity_and_delayed_clocks() -> None:
    policy = load_strategy_policy_v3()

    assert policy.policy_id == "ESSCHER_ACCEPTED_EVENT_POLICY_V3"
    assert policy.policy_version == 3
    assert policy.schema_version == 3
    assert policy.sha256 == ACCEPTED_EVENT_POLICY_V3_SHA256
    assert policy.candidate_ids == (V3_CANDIDATE,)
    clocks = {str(cl["cohort_id"]): cl for cl in policy.candidate(V3_CANDIDATE)["clocks"]}
    assert set(clocks) == {"BMO", "AMC"}
    for clock in clocks.values():
        # The signal window is byte-identical to V1; only capture/decision/entry move.
        assert clock["observation_start"] == "09:30:00"
        assert clock["observation_end"] == "09:35:00"
        assert clock["evidence_cutoff"] == "09:51:00"
        assert clock["decision_cutoff"] == "09:51:50"
        assert clock["candidate_entry_deadline"] == "10:01:00"
        assert clock["timezone"] == "America/New_York"
        assert clock["reasoner_hard_timeout_seconds"] == 8


def test_v1_and_v2_packages_remain_untouched() -> None:
    assert strategy_policy_sha256() == ACCEPTED_EVENT_POLICY_V1_SHA256
    assert load_strategy_policy().policy_version == 1
    assert load_strategy_policy_v2().sha256 == ACCEPTED_EVENT_POLICY_V2_SHA256


def test_v3_parser_rejects_any_drift() -> None:
    raw = strategy_policy_v3_bytes()
    tampered = raw.replace(b"09:51:00", b"09:52:00")
    with pytest.raises(StrategyPolicyError):
        parse_strategy_policy_v3(tampered)
    with pytest.raises(StrategyPolicyError):
        parse_strategy_policy_v3(raw[:-1])


def test_v3_disclosure_labels_are_frozen_in_the_bytes() -> None:
    payload = json.loads(strategy_policy_v3_bytes())
    labels = payload["delayed_capture_disclosure"]["labels"]
    assert "DELAYED_EXECUTION_DEMO" in labels
    assert "NOT_THE_VALIDATED_LANE" in labels


# --- reasoner registry + prompt ----------------------------------------------


def test_registry_serves_the_v3_candidate_and_keeps_v1_triples() -> None:
    v3_route, v3_prompt, v3_schema = reasoner_policy_hashes(V3_CANDIDATE)
    v1_route, v1_prompt, v1_schema = reasoner_policy_hashes("EARNINGS_RESIDUAL_CONTINUATION_V1")
    # The output contract is generation-independent by construction.
    assert v3_schema == v1_schema
    # Route and prompt hashes embed the policy sha, so they must differ.
    assert v3_route != v1_route
    assert v3_prompt != v1_prompt
    # The provider system prompt is its own contract family (distinct payload
    # from the registry's prompt-contract hash) and is candidate-specific.
    assert reasoner_system_prompt_sha256(V3_CANDIDATE) != reasoner_system_prompt_sha256(
        "EARNINGS_RESIDUAL_CONTINUATION_V1"
    )
    prompt = json.loads(reasoner_system_prompt_bytes(V3_CANDIDATE))
    assert prompt["candidate"]["candidate_id"] == V3_CANDIDATE
    assert prompt["direction_values"] == ["UP", "DOWN", "UNCERTAIN"]


# --- confirmation bridge ------------------------------------------------------


def test_confirmation_bridge_reuses_the_v1_rule_for_v3() -> None:
    assert confirmation_epsilon(V3_CANDIDATE) == confirmation_epsilon(
        "EARNINGS_RESIDUAL_CONTINUATION_V1"
    )
    # The content-addressed rule map stays V1-bound (byte-stable digest).
    assert set(confirmation_epsilon_map()) == {
        "EARNINGS_RESIDUAL_CONTINUATION_V1",
        "MACRO_SPY_CONTINUATION_CHALLENGER_V1",
    }


# --- expression indicative flag ------------------------------------------------


def test_expression_flag_is_additive_and_byte_stable() -> None:
    legacy = synthetic_promoted_expression_policy()
    assert legacy.allows_indicative_data is False
    # Legacy policies never serialize the field: canonical bytes (and therefore
    # every historical digest) are unchanged by the additive field.
    assert "allows_indicative_data" not in promoted_expression_policy_payload(legacy)
    round_tripped = parse_promoted_expression_policy(promoted_expression_policy_bytes(legacy))
    assert round_tripped == legacy

    demo = demo_delayed_promoted_expression_policy()
    assert demo.allows_indicative_data is True
    assert demo.policy_id == "DELAYED_DEMO_PROMOTED_EXPRESSION_V1"
    assert promoted_expression_policy_payload(demo)["allows_indicative_data"] is True
    assert parse_promoted_expression_policy(promoted_expression_policy_bytes(demo)) == demo
    # Every bound is identical to the frozen synthetic policy.
    for field in (
        "expression_kind",
        "objective",
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
    ):
        assert getattr(demo, field) == getattr(legacy, field)


def test_executable_data_gate_defaults_closed_and_opens_only_with_the_flag() -> None:
    validate_executable_data(EXECUTABLE_DATA, "x")  # never rejected
    with pytest.raises(ExpressionRejected) as rejected:
        validate_executable_data(INDICATIVE_DATA, "x")
    assert rejected.value.reason is ExpressionReason.INDICATIVE_ONLY
    with pytest.raises(ExpressionRejected):
        validate_executable_data("SOMETHING_ELSE", "x", allows_indicative=True)
    validate_executable_data(INDICATIVE_DATA, "x", allows_indicative=True)


# --- full delayed-lane compile (the headliner) ----------------------------------


def _v3_fixture_inputs() -> tuple[
    CaptureConfiguration, FixtureEvidenceSource, FixtureMarketDataSource
]:
    fixture = load_fixture()
    base_manifest = dict(fixture["candidate_manifest"])
    base_manifest["candidate_id"] = V3_CANDIDATE
    base_manifest["manifest_id"] = "synthetic-earnings-candidates-delayed-demo-2026-09-11"
    base_manifest["policy_sha256"] = ACCEPTED_EVENT_POLICY_V3_SHA256
    base_manifest["schema"] = "esscher.candidate_manifest"
    base_manifest["schema_version"] = 1
    blob = dict(fixture)
    blob["capture_at"] = "2026-09-11T13:50:10Z"
    configuration = CaptureConfiguration(
        candidate_manifest_bytes=canonical_json_bytes(base_manifest),
        event_id=str(fixture["event_id"]),
        capture_at=datetime.fromisoformat("2026-09-11T13:50:10+00:00").astimezone(UTC),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )
    return configuration, FixtureEvidenceSource(blob), FixtureMarketDataSource(blob)


def test_full_compile_runs_on_the_delayed_v3_clocks() -> None:
    configuration, evidence, market = _v3_fixture_inputs()

    compiled = compile_strategy_snapshot(configuration, evidence, market)
    snapshot = compiled.snapshot

    assert snapshot.candidate_id == V3_CANDIDATE
    assert snapshot.policy_sha256 == ACCEPTED_EVENT_POLICY_V3_SHA256
    # Identical signal window as V1 (13:30-13:35 UTC), delayed gate clocks.
    assert snapshot.observation_window_start_at == datetime(2026, 9, 11, 13, 30, tzinfo=UTC)
    assert snapshot.observation_window_end_at == datetime(2026, 9, 11, 13, 35, tzinfo=UTC)
    assert snapshot.evidence_cutoff_at == datetime(2026, 9, 11, 13, 51, tzinfo=UTC)
    assert snapshot.decision_cutoff_at == datetime(2026, 9, 11, 13, 51, 50, tzinfo=UTC)
    assert snapshot.candidate_entry_deadline_at == datetime(2026, 9, 11, 14, 1, tzinfo=UTC)

    joined = compiled_strategy_input(compiled)
    assert joined.snapshot.candidate_id == V3_CANDIDATE


def test_v3_compile_still_enforces_the_delayed_cutoffs() -> None:
    from esscher.sourcedata.compiler import CollectorRejected

    configuration, evidence, market = _v3_fixture_inputs()
    late = CaptureConfiguration(
        candidate_manifest_bytes=configuration.candidate_manifest_bytes,
        event_id=configuration.event_id,
        capture_at=datetime(2026, 9, 11, 13, 52, tzinfo=UTC),  # past 13:51:50
        market_publisher=configuration.market_publisher,
        market_entitlement=configuration.market_entitlement,
        market_redistribution=configuration.market_redistribution,
    )
    with pytest.raises(CollectorRejected) as rejected:
        compile_strategy_snapshot(late, evidence, market)
    assert "RETRIEVED_AFTER_CUTOFF" in str(rejected.value)


def test_v1_fixture_still_compiles_under_v1_clocks() -> None:
    # The additive V3 generation must not disturb the validated V1 lane.
    from esscher.sourcedata.fakes import build_candidate_manifest

    fixture = load_fixture()
    configuration = CaptureConfiguration(
        candidate_manifest_bytes=build_candidate_manifest(fixture),
        event_id=str(fixture["event_id"]),
        capture_at=datetime.fromisoformat(str(fixture["capture_at"]).replace("Z", "+00:00")),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )
    compiled = compile_strategy_snapshot(
        configuration, FixtureEvidenceSource(fixture), FixtureMarketDataSource(fixture)
    )
    assert compiled.snapshot.candidate_id == "EARNINGS_RESIDUAL_CONTINUATION_V1"
    assert compiled.snapshot.evidence_cutoff_at == datetime(2026, 9, 11, 13, 35, 15, tzinfo=UTC)


def test_production_lane_mapping_accepts_the_v3_source_candidate() -> None:
    from esscher.runtime.paper_mcp_composition import (
        _SOURCE_CANDIDATE_BY_AUTONOMOUS_LANE,
        EARNINGS_LANE_V2,
    )

    assert V3_CANDIDATE in _SOURCE_CANDIDATE_BY_AUTONOMOUS_LANE[EARNINGS_LANE_V2]
    assert (
        "EARNINGS_RESIDUAL_CONTINUATION_V1"
        in _SOURCE_CANDIDATE_BY_AUTONOMOUS_LANE[EARNINGS_LANE_V2]
    )
