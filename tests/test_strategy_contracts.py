from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from esscher.alpha.models import Direction
from esscher.strategy import (
    CandidateManifest,
    CandidateRecord,
    DataHealthState,
    DecisionDisposition,
    DecodingParameters,
    EligibilityState,
    EventCategory,
    EvidenceRef,
    EvidenceRole,
    ExchangeStatus,
    Falsifier,
    FeatureComponent,
    FeatureReceipt,
    FeatureStatus,
    FeatureValue,
    FeatureValueType,
    GuidanceDirection,
    ReactionRelation,
    ReasonerDecision,
    ReasonerExchange,
    ReleaseFamily,
    StrategyContractReason,
    StrategyContractRejected,
    StrategySnapshot,
    TimingBucket,
    build_strategy_input,
    candidate_manifest_bytes,
    candidate_manifest_sha256,
    feature_receipt_bytes,
    load_strategy_policy,
    parse_candidate_manifest,
    parse_feature_receipt,
    parse_reasoner_decision,
    parse_reasoner_exchange,
    parse_strategy_snapshot,
    reasoner_exchange_bytes,
    reasoner_exchange_sha256,
    reasoner_model_config_sha256,
    reasoner_policy_hashes,
    strategy_decision_bytes,
    strategy_snapshot_bytes,
    strategy_snapshot_sha256,
    validate_reasoner_response,
)
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
EARNINGS_CANDIDATE = "EARNINGS_RESIDUAL_CONTINUATION_V1"
MACRO_CANDIDATE = "MACRO_SPY_CONTINUATION_CHALLENGER_V1"
SYNTHETIC_BUNDLE_PATH = (
    Path(__file__).parent / "contract_fixtures" / "synthetic_strategy_development_v1.json"
)


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


_AMC_DEFAULT_FROZEN_AT = _at("2026-09-09T20:15:00Z")
_AMC_DEFAULT_SCHEDULED_AT = _at("2026-09-10T20:00:00Z")
_AMC_DEFAULT_PUBLISHED_AT = _at("2026-09-10T20:05:00Z")
_AMC_DEFAULT_PRIOR_CLOSE_AT = _at("2026-09-10T20:00:00Z")
_AMC_DEFAULT_REACTION_OPEN_AT = _at("2026-09-11T13:30:00Z")
_AMC_DEFAULT_REACTION_CLOSE_AT = _at("2026-09-11T20:00:00Z")


def _candidate_manifest() -> CandidateManifest:
    policy = load_strategy_policy()
    return CandidateManifest(
        manifest_id="earnings-candidates-2026-09-11",
        candidate_id=EARNINGS_CANDIDATE,
        policy_sha256=policy.sha256,
        selection_rule_id="earnings-universe-v1",
        producer_build_sha256=HASH_A,
        frozen_at=_at("2026-09-10T20:15:00Z"),
        records=(
            CandidateRecord(
                event_id="KR-2026Q2-EARNINGS",
                issuer="The Kroger Co.",
                security_id="CIK-0000056873",
                ticker="KR",
                cohort_id="BMO",
                scheduled_at=_at("2026-09-11T12:00:00Z"),
                eligibility=EligibilityState.ELIGIBLE,
                reason_codes=(),
            ),
            CandidateRecord(
                event_id="ZZZZ-2026Q3-EARNINGS",
                issuer="Synthetic Excluded Issuer",
                security_id="SYNTHETIC-EXCLUDED-1",
                ticker="ZZZZ",
                cohort_id="BMO",
                scheduled_at=_at("2026-09-11T12:30:00Z"),
                eligibility=EligibilityState.INELIGIBLE,
                reason_codes=("PRIOR_CLOSE_BELOW_MINIMUM",),
            ),
        ),
    )


def _amc_candidate_manifest(
    *,
    frozen_at: datetime = _AMC_DEFAULT_FROZEN_AT,
    scheduled_at: datetime = _AMC_DEFAULT_SCHEDULED_AT,
) -> CandidateManifest:
    policy = load_strategy_policy()
    return CandidateManifest(
        manifest_id="amc-candidates-2026-09-10",
        candidate_id=EARNINGS_CANDIDATE,
        policy_sha256=policy.sha256,
        selection_rule_id="earnings-universe-v1",
        producer_build_sha256=HASH_A,
        frozen_at=frozen_at,
        records=(
            CandidateRecord(
                event_id="KR-2026Q2-AMC-EARNINGS",
                issuer="The Kroger Co.",
                security_id="CIK-0000056873",
                ticker="KR",
                cohort_id="AMC",
                scheduled_at=scheduled_at,
                eligibility=EligibilityState.ELIGIBLE,
                reason_codes=(),
            ),
        ),
    )


def _evidence_refs() -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef(
            evidence_id="calendar",
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            source_class="OFFICIAL_EXCHANGE_CALENDAR",
            published_at=None,
            available_at=_at("2026-09-11T13:29:00Z"),
            content_sha256=HASH_A,
        ),
        EvidenceRef(
            evidence_id="corporate-action",
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            source_class="CORPORATE_ACTION_RECORD",
            published_at=None,
            available_at=_at("2026-09-11T13:29:00Z"),
            content_sha256=HASH_B,
        ),
        EvidenceRef(
            evidence_id="earnings-release",
            role=EvidenceRole.ISSUER_PRIMARY,
            source_class="ISSUER_INVESTOR_RELATIONS",
            published_at=_at("2026-09-11T12:00:00Z"),
            available_at=_at("2026-09-11T12:00:05Z"),
            content_sha256=HASH_C,
        ),
        EvidenceRef(
            evidence_id="market-snapshot",
            role=EvidenceRole.ISSUER_MARKET,
            source_class="LICENSED_SIP_EQUITY_TRADES",
            published_at=None,
            available_at=_at("2026-09-11T13:35:00Z"),
            content_sha256=HASH_D,
        ),
        EvidenceRef(
            evidence_id="security-master",
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            source_class="POINT_IN_TIME_SECURITY_MASTER",
            published_at=None,
            available_at=_at("2026-09-11T13:29:00Z"),
            content_sha256=HASH_A,
        ),
    )


def _strategy_snapshot(manifest: CandidateManifest) -> StrategySnapshot:
    policy = load_strategy_policy()
    reasoner = policy.data["reasoner"]
    tolerated = tuple(reasoner["tolerated_unknown_codes"])
    critical = tuple(reasoner["critical_unknown_codes"])
    return StrategySnapshot(
        event_id="KR-2026Q2-EARNINGS",
        candidate_id=EARNINGS_CANDIDATE,
        cohort_id="BMO",
        event_category=EventCategory.SCHEDULED_EARNINGS,
        issuer="The Kroger Co.",
        security_id="CIK-0000056873",
        ticker="KR",
        policy_sha256=policy.sha256,
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        producer_build_sha256=HASH_B,
        created_at=_at("2026-09-11T13:35:15Z"),
        universe_frozen_at=manifest.frozen_at,
        timing_bucket=TimingBucket.BEFORE_OPEN,
        release_family=None,
        event_published_at=_at("2026-09-11T12:00:00Z"),
        reaction_session_id="XNYS-2026-09-11",
        reaction_session_open_at=_at("2026-09-11T13:30:00Z"),
        reaction_session_close_at=_at("2026-09-11T20:00:00Z"),
        observation_window_start_at=_at("2026-09-11T13:30:00Z"),
        observation_window_end_at=_at("2026-09-11T13:35:00Z"),
        evidence_cutoff_at=_at("2026-09-11T13:35:15Z"),
        decision_cutoff_at=_at("2026-09-11T13:36:05Z"),
        candidate_entry_deadline_at=_at("2026-09-11T13:37:00Z"),
        evidence_packet_sha256=HASH_C,
        evidence_refs=_evidence_refs(),
        eligibility=EligibilityState.ELIGIBLE,
        eligibility_reason_codes=(),
        data_health=DataHealthState.VALID,
        health_reason_codes=(),
        allowed_unknown_codes=tuple(sorted((*tolerated, *critical))),
        critical_unknown_codes=tuple(sorted(critical)),
    )


def _amc_strategy_snapshot(
    manifest: CandidateManifest,
    *,
    published_at: datetime = _AMC_DEFAULT_PUBLISHED_AT,
    prior_eligible_session_close_at: datetime = _AMC_DEFAULT_PRIOR_CLOSE_AT,
    reaction_session_open_at: datetime = _AMC_DEFAULT_REACTION_OPEN_AT,
    reaction_session_close_at: datetime = _AMC_DEFAULT_REACTION_CLOSE_AT,
) -> StrategySnapshot:
    policy = load_strategy_policy()
    reasoner = policy.data["reasoner"]
    tolerated = tuple(reasoner["tolerated_unknown_codes"])
    critical = tuple(reasoner["critical_unknown_codes"])
    observation_window_end_at = reaction_session_open_at + timedelta(minutes=5)
    evidence_cutoff_at = observation_window_end_at + timedelta(seconds=15)
    decision_cutoff_at = evidence_cutoff_at + timedelta(seconds=50)
    candidate_entry_deadline_at = decision_cutoff_at + timedelta(seconds=55)
    evidence_refs = tuple(
        replace(
            item,
            published_at=published_at,
            available_at=published_at + timedelta(seconds=5),
        )
        if item.evidence_id == "earnings-release"
        else replace(item, available_at=observation_window_end_at)
        if item.evidence_id == "market-snapshot"
        else item
        for item in _evidence_refs()
    )
    return StrategySnapshot(
        event_id="KR-2026Q2-AMC-EARNINGS",
        candidate_id=EARNINGS_CANDIDATE,
        cohort_id="AMC",
        event_category=EventCategory.SCHEDULED_EARNINGS,
        issuer="The Kroger Co.",
        security_id="CIK-0000056873",
        ticker="KR",
        policy_sha256=policy.sha256,
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        producer_build_sha256=HASH_B,
        created_at=evidence_cutoff_at,
        universe_frozen_at=manifest.frozen_at,
        timing_bucket=TimingBucket.AFTER_CLOSE,
        release_family=None,
        event_published_at=published_at,
        reaction_session_id=f"XNYS-{reaction_session_open_at.date().isoformat()}",
        reaction_session_open_at=reaction_session_open_at,
        reaction_session_close_at=reaction_session_close_at,
        observation_window_start_at=reaction_session_open_at,
        observation_window_end_at=observation_window_end_at,
        evidence_cutoff_at=evidence_cutoff_at,
        decision_cutoff_at=decision_cutoff_at,
        candidate_entry_deadline_at=candidate_entry_deadline_at,
        evidence_packet_sha256=HASH_C,
        evidence_refs=evidence_refs,
        eligibility=EligibilityState.ELIGIBLE,
        eligibility_reason_codes=(),
        data_health=DataHealthState.VALID,
        health_reason_codes=(),
        allowed_unknown_codes=tuple(sorted((*tolerated, *critical))),
        critical_unknown_codes=tuple(sorted(critical)),
        prior_eligible_session_close_at=prior_eligible_session_close_at,
    )


def _feature_receipt(snapshot: StrategySnapshot) -> FeatureReceipt:
    policy = load_strategy_policy()
    feature_observed_at = snapshot.observation_window_end_at
    features: list[FeatureValue] = []
    for spec in sorted(policy.features(EARNINGS_CANDIDATE), key=lambda item: item["feature_id"]):
        value_type = FeatureValueType(spec["value_type"])
        if value_type is FeatureValueType.ENUM:
            value = GuidanceDirection.RAISED
        elif value_type is FeatureValueType.INTEGER:
            value = 100
        elif spec["feature_id"] == "market.opening_residual_log_return.v1":
            value = Decimal("0.01")
        else:
            value = Decimal("1")
        source_ref = (
            "earnings-release" if spec["feature_id"].startswith("earnings.") else "market-snapshot"
        )
        features.append(
            FeatureValue(
                feature_id=spec["feature_id"],
                status=FeatureStatus.PRESENT,
                value=value,
                value_type=value_type,
                unit=spec["unit"],
                observed_at=feature_observed_at,
                source_refs=(source_ref,),
            )
        )
    return FeatureReceipt(
        event_id=snapshot.event_id,
        candidate_id=snapshot.candidate_id,
        cohort_id=snapshot.cohort_id,
        policy_sha256=snapshot.policy_sha256,
        strategy_snapshot_sha256=strategy_snapshot_sha256(snapshot),
        producer_build_sha256=HASH_C,
        created_at=_at("2026-09-11T13:35:15Z"),
        feature_snapshot_at=_at("2026-09-11T13:35:15Z"),
        decision_cutoff_at=snapshot.decision_cutoff_at,
        maximum_public_timestamp=snapshot.event_published_at,
        data_health=snapshot.data_health,
        health_reason_codes=snapshot.health_reason_codes,
        evidence_ids=tuple(sorted(ref.evidence_id for ref in snapshot.evidence_refs)),
        lineage_receipt_sha256=None,
        features=tuple(features),
    )


def _macro_strategy_input(*, vwap_distance: Decimal):
    policy = load_strategy_policy()
    manifest = CandidateManifest(
        manifest_id="macro-candidates-2026-09-01",
        candidate_id=MACRO_CANDIDATE,
        policy_sha256=policy.sha256,
        selection_rule_id="macro-universe-v1",
        producer_build_sha256=HASH_A,
        frozen_at=_at("2026-08-31T20:15:00Z"),
        records=(
            CandidateRecord(
                event_id="BLS-JOLTS-2026-09",
                issuer="U.S. Bureau of Labor Statistics",
                security_id="SPY",
                ticker="SPY",
                cohort_id="BLS_JOLTS",
                scheduled_at=_at("2026-09-01T14:00:00Z"),
                eligibility=EligibilityState.ELIGIBLE,
                reason_codes=(),
            ),
        ),
    )
    reasoner = policy.data["reasoner"]
    tolerated = tuple(reasoner["tolerated_unknown_codes"])
    critical = tuple(reasoner["critical_unknown_codes"])
    evidence_refs = (
        EvidenceRef(
            evidence_id="bls-calendar",
            role=EvidenceRole.MACRO_PRIMARY,
            source_class="OFFICIAL_BLS_RELEASE_CALENDAR",
            published_at=None,
            available_at=_at("2026-09-01T13:59:00Z"),
            content_sha256=HASH_A,
        ),
        EvidenceRef(
            evidence_id="bls-release",
            role=EvidenceRole.MACRO_PRIMARY,
            source_class="OFFICIAL_BLS_RELEASE",
            published_at=_at("2026-09-01T14:00:00Z"),
            available_at=_at("2026-09-01T14:00:01Z"),
            content_sha256=HASH_B,
        ),
        EvidenceRef(
            evidence_id="bls-revisions",
            role=EvidenceRole.MACRO_PRIMARY,
            source_class="OFFICIAL_BLS_REVISION_TABLE",
            published_at=_at("2026-09-01T14:00:00Z"),
            available_at=_at("2026-09-01T14:00:01Z"),
            content_sha256=HASH_C,
        ),
        EvidenceRef(
            evidence_id="spy-quotes",
            role=EvidenceRole.MARKET_PROXY,
            source_class="LICENSED_SIP_SPY_QUOTES",
            published_at=None,
            available_at=_at("2026-09-01T14:15:00Z"),
            content_sha256=HASH_D,
        ),
        EvidenceRef(
            evidence_id="spy-trades",
            role=EvidenceRole.MARKET_PROXY,
            source_class="LICENSED_SIP_SPY_TRADES",
            published_at=None,
            available_at=_at("2026-09-01T14:15:00Z"),
            content_sha256=HASH_A,
        ),
    )
    snapshot = StrategySnapshot(
        event_id="BLS-JOLTS-2026-09",
        candidate_id=MACRO_CANDIDATE,
        cohort_id="BLS_JOLTS",
        event_category=EventCategory.SCHEDULED_MACRO_RELEASE,
        issuer="U.S. Bureau of Labor Statistics",
        security_id="SPY",
        ticker="SPY",
        policy_sha256=policy.sha256,
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        producer_build_sha256=HASH_B,
        created_at=_at("2026-09-01T14:15:15Z"),
        universe_frozen_at=manifest.frozen_at,
        timing_bucket=TimingBucket.SCHEDULED_RELEASE,
        release_family=ReleaseFamily.BLS_JOLTS,
        event_published_at=_at("2026-09-01T14:00:00Z"),
        reaction_session_id="XNYS-2026-09-01",
        reaction_session_open_at=_at("2026-09-01T13:30:00Z"),
        reaction_session_close_at=_at("2026-09-01T20:00:00Z"),
        observation_window_start_at=_at("2026-09-01T14:00:00Z"),
        observation_window_end_at=_at("2026-09-01T14:15:00Z"),
        evidence_cutoff_at=_at("2026-09-01T14:15:15Z"),
        decision_cutoff_at=_at("2026-09-01T14:16:05Z"),
        candidate_entry_deadline_at=_at("2026-09-01T14:17:00Z"),
        evidence_packet_sha256=HASH_C,
        evidence_refs=evidence_refs,
        eligibility=EligibilityState.ELIGIBLE,
        eligibility_reason_codes=(),
        data_health=DataHealthState.VALID,
        health_reason_codes=(),
        allowed_unknown_codes=tuple(sorted((*tolerated, *critical))),
        critical_unknown_codes=tuple(sorted(critical)),
    )
    features: list[FeatureValue] = []
    for spec in sorted(policy.features(MACRO_CANDIDATE), key=lambda item: item["feature_id"]):
        feature_id = spec["feature_id"]
        value_type = FeatureValueType(spec["value_type"])
        if feature_id.startswith("macro.employment."):
            status = FeatureStatus.NOT_APPLICABLE
            value = None
            observed_at = None
            source_refs = ()
            components = ()
        elif feature_id == "macro.consensus_surprise_vector.v1":
            status = FeatureStatus.UNAVAILABLE
            value = None
            observed_at = None
            source_refs = ()
            components = ()
        elif value_type is FeatureValueType.DECIMAL_STRING_MAP:
            status = FeatureStatus.PRESENT
            value = None
            observed_at = _at("2026-09-01T14:00:01Z")
            source_refs = ("bls-revisions",)
            components = (
                FeatureComponent(
                    component_id="prior_value_revision",
                    status=FeatureStatus.PRESENT,
                    value=Decimal("1"),
                    unit="COUNT",
                    source_refs=("bls-revisions",),
                ),
            )
        else:
            status = FeatureStatus.PRESENT
            observed_at = _at("2026-09-01T14:15:00Z")
            source_refs = (
                ("spy-quotes",)
                if "quote" in feature_id or "spread" in feature_id
                else (("spy-trades",) if feature_id.startswith("market.") else ("bls-release",))
            )
            components = ()
            if value_type is FeatureValueType.INTEGER:
                value = 100
            elif feature_id == "market.spy_event_zscore_60.v1":
                value = Decimal("2")
            elif feature_id == "market.spy_event_volume_ratio_20.v1":
                value = Decimal("1.5")
            elif feature_id == "market.spy_event_vwap_distance_bps.v1":
                value = vwap_distance
            else:
                value = Decimal("1")
        features.append(
            FeatureValue(
                feature_id=feature_id,
                status=status,
                value=value,
                value_type=value_type,
                unit=spec["unit"],
                observed_at=observed_at,
                source_refs=source_refs,
                components=components,
            )
        )
    receipt = FeatureReceipt(
        event_id=snapshot.event_id,
        candidate_id=snapshot.candidate_id,
        cohort_id=snapshot.cohort_id,
        policy_sha256=snapshot.policy_sha256,
        strategy_snapshot_sha256=strategy_snapshot_sha256(snapshot),
        producer_build_sha256=HASH_C,
        created_at=_at("2026-09-01T14:15:15Z"),
        feature_snapshot_at=_at("2026-09-01T14:15:15Z"),
        decision_cutoff_at=snapshot.decision_cutoff_at,
        maximum_public_timestamp=snapshot.event_published_at,
        data_health=snapshot.data_health,
        health_reason_codes=snapshot.health_reason_codes,
        evidence_ids=tuple(sorted(ref.evidence_id for ref in snapshot.evidence_refs)),
        lineage_receipt_sha256=None,
        features=tuple(features),
    )
    return build_strategy_input(
        strategy_snapshot_bytes(snapshot),
        candidate_manifest_bytes=candidate_manifest_bytes(manifest),
        feature_receipt_bytes=feature_receipt_bytes(receipt),
    )


def _strategy_input():
    manifest = _candidate_manifest()
    snapshot = _strategy_snapshot(manifest)
    receipt = _feature_receipt(snapshot)
    return build_strategy_input(
        strategy_snapshot_bytes(snapshot),
        candidate_manifest_bytes=candidate_manifest_bytes(manifest),
        feature_receipt_bytes=feature_receipt_bytes(receipt),
    )


def _rebuild_strategy_input(
    manifest: CandidateManifest,
    snapshot: StrategySnapshot,
    receipt: FeatureReceipt,
):
    receipt = replace(
        receipt,
        strategy_snapshot_sha256=strategy_snapshot_sha256(snapshot),
    )
    return build_strategy_input(
        strategy_snapshot_bytes(snapshot),
        candidate_manifest_bytes=candidate_manifest_bytes(manifest),
        feature_receipt_bytes=feature_receipt_bytes(receipt),
    )


def _reasoner_parts(strategy_input, *, responded_at: datetime | None = None):
    if strategy_input.snapshot.candidate_id == MACRO_CANDIDATE:
        primary_evidence_id = "bls-release"
        market_evidence_id = "spy-trades"
        summary = "The official release and confirmed SPY reaction point upward."
    else:
        primary_evidence_id = "earnings-release"
        market_evidence_id = "market-snapshot"
        summary = "Guidance and the synchronized opening residual point upward."
    decision = ReasonerDecision(
        decision=Direction.UP,
        evidence_ids=(primary_evidence_id, market_evidence_id),
        contradictions=(),
        unknowns=(),
        strongest_falsifier=Falsifier(
            evidence_id=market_evidence_id,
            summary="The confirmed reaction could fade after the cutoff.",
        ),
        summary=summary,
    )
    raw = canonical_json_bytes(
        {
            "contradictions": [],
            "decision": "UP",
            "evidence_ids": [primary_evidence_id, market_evidence_id],
            "strongest_falsifier": {
                "evidence_id": market_evidence_id,
                "summary": "The confirmed reaction could fade after the cutoff.",
            },
            "summary": summary,
            "unknowns": [],
        }
    )
    decoding = DecodingParameters(
        temperature=Decimal("0"),
        top_p=Decimal("1"),
        max_output_tokens=512,
        seed=7,
    )
    route_sha256, prompt_sha256, output_schema_sha256 = reasoner_policy_hashes(
        strategy_input.snapshot.candidate_id
    )
    started_at = strategy_input.snapshot.evidence_cutoff_at + timedelta(seconds=5)
    completed_at = responded_at or started_at + timedelta(seconds=5)
    exchange = ReasonerExchange(
        event_id=strategy_input.snapshot.event_id,
        candidate_id=strategy_input.snapshot.candidate_id,
        policy_sha256=strategy_input.snapshot.policy_sha256,
        strategy_snapshot_sha256=strategy_input.snapshot_sha256,
        feature_receipt_sha256=strategy_input.feature_receipt_sha256,
        evidence_packet_sha256=strategy_input.snapshot.evidence_packet_sha256,
        route_sha256=route_sha256,
        prompt_sha256=prompt_sha256,
        output_schema_sha256=output_schema_sha256,
        model_config_sha256=reasoner_model_config_sha256(
            provider="synthetic-provider",
            model="synthetic-model",
            model_revision="development-fixture-v1",
            decoding=decoding,
        ),
        request_sha256=HASH_D,
        raw_response_sha256=sha256_bytes(raw),
        provider="synthetic-provider",
        model="synthetic-model",
        model_revision="development-fixture-v1",
        decoding=decoding,
        started_at=started_at,
        responded_at=completed_at,
        deadline_at=min(
            started_at + timedelta(seconds=8),
            strategy_input.snapshot.decision_cutoff_at,
        ),
        status=ExchangeStatus.COMPLETED,
        error_code=None,
        producer_build_sha256=HASH_D,
        created_at=completed_at,
    )
    return decision, raw, exchange


def test_candidate_manifest_round_trips_and_retains_exclusions() -> None:
    manifest = _candidate_manifest()

    parsed = parse_candidate_manifest(candidate_manifest_bytes(manifest))

    assert parsed == manifest
    assert [record.eligibility for record in parsed.records] == [
        EligibilityState.ELIGIBLE,
        EligibilityState.INELIGIBLE,
    ]
    assert parsed.records[1].reason_codes == ("PRIOR_CLOSE_BELOW_MINIMUM",)


def test_static_synthetic_bundle_contains_parseable_direction_only_artifacts() -> None:
    bundle_bytes = SYNTHETIC_BUNDLE_PATH.read_bytes()
    bundle = json.loads(bundle_bytes)
    artifacts = bundle["artifacts"]

    manifest = parse_candidate_manifest(canonical_json_bytes(artifacts["candidate_manifest"]))
    response = parse_reasoner_decision(canonical_json_bytes(artifacts["reasoner_response"]))

    assert bundle_bytes == canonical_json_bytes(bundle)
    assert manifest.policy_sha256 == load_strategy_policy().sha256
    assert manifest.records[0].eligibility is EligibilityState.ELIGIBLE
    assert manifest.records[1].eligibility is EligibilityState.INELIGIBLE
    assert response.decision is Direction.UP
    assert set(artifacts["reasoner_response"]).isdisjoint(
        {"account", "contract", "expiry", "limit_price", "order", "permit", "quantity", "strike"}
    )


def test_strategy_input_rejects_discretionary_symbol_not_in_manifest() -> None:
    manifest = _candidate_manifest()
    snapshot = replace(_strategy_snapshot(manifest), ticker="NVDA")
    receipt = _feature_receipt(snapshot)

    with pytest.raises(StrategyContractRejected) as caught:
        build_strategy_input(
            strategy_snapshot_bytes(snapshot),
            candidate_manifest_bytes=candidate_manifest_bytes(manifest),
            feature_receipt_bytes=feature_receipt_bytes(receipt),
        )

    assert caught.value.reason is StrategyContractReason.IDENTITY_MISMATCH


def test_strategy_snapshot_rejects_evidence_after_cutoff() -> None:
    manifest = _candidate_manifest()
    payload = json.loads(strategy_snapshot_bytes(_strategy_snapshot(manifest)))
    payload["evidence_refs"][0]["available_at"] = "2026-09-11T13:35:16Z"

    with pytest.raises(StrategyContractRejected):
        parse_strategy_snapshot(canonical_json_bytes(payload))


def test_feature_receipt_rejects_nonfinite_decimal_text() -> None:
    manifest = _candidate_manifest()
    snapshot = _strategy_snapshot(manifest)
    payload = json.loads(feature_receipt_bytes(_feature_receipt(snapshot)))
    payload["features"][0]["value"] = "NaN"

    with pytest.raises(StrategyContractRejected) as caught:
        parse_feature_receipt(canonical_json_bytes(payload))

    assert caught.value.reason is StrategyContractReason.INVALID_DOCUMENT


def test_strategy_input_rejects_clock_shift_from_frozen_cohort_policy() -> None:
    manifest = _candidate_manifest()
    snapshot = replace(
        _strategy_snapshot(manifest),
        observation_window_start_at=_at("2026-09-11T13:31:00Z"),
    )
    receipt = _feature_receipt(snapshot)

    with pytest.raises(StrategyContractRejected) as caught:
        build_strategy_input(
            strategy_snapshot_bytes(snapshot),
            candidate_manifest_bytes=candidate_manifest_bytes(manifest),
            feature_receipt_bytes=feature_receipt_bytes(receipt),
        )

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_accepts_amc_release_at_the_bound_prior_session_close() -> None:
    manifest = _amc_candidate_manifest()
    snapshot = replace(
        _amc_strategy_snapshot(manifest),
        event_published_at=_at("2026-09-10T20:00:00Z"),
    )

    strategy_input = _rebuild_strategy_input(manifest, snapshot, _feature_receipt(snapshot))

    assert strategy_input.snapshot.prior_eligible_session_close_at == _at("2026-09-10T20:00:00Z")


def test_strategy_input_accepts_friday_amc_close_for_monday_reaction() -> None:
    manifest = _amc_candidate_manifest(
        frozen_at=_at("2026-09-10T20:15:00Z"),
        scheduled_at=_at("2026-09-11T20:00:00Z"),
    )
    snapshot = _amc_strategy_snapshot(
        manifest,
        published_at=_at("2026-09-11T20:00:00Z"),
        prior_eligible_session_close_at=_at("2026-09-11T20:00:00Z"),
        reaction_session_open_at=_at("2026-09-14T13:30:00Z"),
        reaction_session_close_at=_at("2026-09-14T20:00:00Z"),
    )

    strategy_input = _rebuild_strategy_input(manifest, snapshot, _feature_receipt(snapshot))

    assert strategy_input.snapshot.reaction_session_id == "XNYS-2026-09-14"


def test_strategy_input_rejects_bmo_as_amc_with_stale_prior_session_boundary() -> None:
    manifest = _amc_candidate_manifest(
        frozen_at=_at("2026-09-10T20:15:00Z"),
        scheduled_at=_at("2026-09-11T20:00:00Z"),
    )
    snapshot = _amc_strategy_snapshot(
        manifest,
        published_at=_at("2026-09-11T12:00:00Z"),
        prior_eligible_session_close_at=_at("2026-09-10T20:00:00Z"),
        reaction_session_open_at=_at("2026-09-14T13:30:00Z"),
        reaction_session_close_at=_at("2026-09-14T20:00:00Z"),
    )

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(manifest, snapshot, _feature_receipt(snapshot))

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_rejects_amc_without_bound_prior_session_close() -> None:
    manifest = _amc_candidate_manifest()
    snapshot = replace(
        _amc_strategy_snapshot(manifest),
        prior_eligible_session_close_at=None,
    )

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(manifest, snapshot, _feature_receipt(snapshot))

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_rejects_amc_release_before_prior_session_close() -> None:
    manifest = _amc_candidate_manifest()
    snapshot = replace(
        _amc_strategy_snapshot(manifest),
        event_published_at=_at("2026-09-10T19:59:59Z"),
    )
    receipt = _feature_receipt(snapshot)

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(manifest, snapshot, receipt)

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_rejects_amc_release_at_next_reaction_session_open() -> None:
    manifest = _amc_candidate_manifest()
    snapshot = replace(
        _amc_strategy_snapshot(manifest),
        event_published_at=_at("2026-09-11T13:30:00Z"),
    )

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(manifest, snapshot, _feature_receipt(snapshot))

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_rejects_macro_release_family_for_the_wrong_cohort() -> None:
    strategy_input = _macro_strategy_input(vwap_distance=Decimal("5"))
    snapshot = replace(
        strategy_input.snapshot,
        release_family=ReleaseFamily.BLS_EMPLOYMENT_SITUATION,
    )

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(
            strategy_input.candidate_manifest,
            snapshot,
            strategy_input.feature_receipt,
        )

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_rejects_macro_event_outside_frozen_schedule_tolerance() -> None:
    strategy_input = _macro_strategy_input(vwap_distance=Decimal("5"))
    event_published_at = _at("2026-09-01T14:01:01Z")
    evidence_refs = tuple(
        replace(item, published_at=event_published_at)
        if item.evidence_id == "bls-release"
        else item
        for item in strategy_input.snapshot.evidence_refs
    )
    snapshot = replace(
        strategy_input.snapshot,
        event_published_at=event_published_at,
        evidence_refs=evidence_refs,
    )

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(
            strategy_input.candidate_manifest,
            snapshot,
            strategy_input.feature_receipt,
        )

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_rejects_macro_primary_evidence_with_unbound_publication_time() -> None:
    strategy_input = _macro_strategy_input(vwap_distance=Decimal("5"))
    evidence_refs = tuple(
        replace(item, published_at=_at("2026-09-01T14:00:01Z"))
        if item.evidence_id == "bls-release"
        else item
        for item in strategy_input.snapshot.evidence_refs
    )
    snapshot = replace(strategy_input.snapshot, evidence_refs=evidence_refs)

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(
            strategy_input.candidate_manifest,
            snapshot,
            strategy_input.feature_receipt,
        )

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_present_feature_values_require_evidence_source_refs() -> None:
    manifest = _candidate_manifest()
    snapshot = _strategy_snapshot(manifest)
    payload = json.loads(feature_receipt_bytes(_feature_receipt(snapshot)))
    payload["features"][0]["source_refs"] = []

    with pytest.raises(StrategyContractRejected) as caught:
        parse_feature_receipt(canonical_json_bytes(payload))

    assert caught.value.reason is StrategyContractReason.INVALID_DOCUMENT


def test_present_feature_components_require_evidence_source_refs() -> None:
    strategy_input = _macro_strategy_input(vwap_distance=Decimal("5"))
    payload = json.loads(feature_receipt_bytes(strategy_input.feature_receipt))
    composite = next(item for item in payload["features"] if item["components"])
    composite["components"][0]["source_refs"] = []

    with pytest.raises(StrategyContractRejected) as caught:
        parse_feature_receipt(canonical_json_bytes(payload))

    assert caught.value.reason is StrategyContractReason.INVALID_DOCUMENT


def test_strategy_input_rejects_present_feature_with_late_cited_evidence() -> None:
    manifest = _candidate_manifest()
    snapshot = _strategy_snapshot(manifest)
    snapshot = replace(
        snapshot,
        evidence_refs=tuple(
            replace(
                item,
                available_at=snapshot.observation_window_end_at + timedelta(seconds=1),
            )
            if item.evidence_id == "earnings-release"
            else item
            for item in snapshot.evidence_refs
        ),
    )

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(manifest, snapshot, _feature_receipt(snapshot))

    assert caught.value.reason is StrategyContractReason.IDENTITY_MISMATCH


def test_strategy_input_rejects_present_component_with_late_cited_evidence() -> None:
    strategy_input = _macro_strategy_input(vwap_distance=Decimal("5"))
    snapshot = replace(
        strategy_input.snapshot,
        evidence_refs=tuple(
            replace(item, available_at=_at("2026-09-01T14:00:02Z"))
            if item.evidence_id == "bls-revisions"
            else item
            for item in strategy_input.snapshot.evidence_refs
        ),
    )
    vector_feature = next(
        feature for feature in strategy_input.feature_receipt.features if feature.components
    )
    features = tuple(
        replace(
            feature,
            source_refs=("bls-release",),
            components=(replace(feature.components[0], source_refs=("bls-revisions",)),),
        )
        if feature.feature_id == vector_feature.feature_id
        else feature
        for feature in strategy_input.feature_receipt.features
    )
    receipt = replace(strategy_input.feature_receipt, features=features)

    with pytest.raises(StrategyContractRejected) as caught:
        _rebuild_strategy_input(strategy_input.candidate_manifest, snapshot, receipt)

    assert caught.value.reason is StrategyContractReason.IDENTITY_MISMATCH


def test_strategy_input_rejects_uniformly_shifted_market_session() -> None:
    manifest = _candidate_manifest()
    snapshot = _strategy_snapshot(manifest)
    shift = timedelta(hours=1)
    snapshot = replace(
        snapshot,
        created_at=snapshot.created_at + shift,
        reaction_session_open_at=snapshot.reaction_session_open_at + shift,
        reaction_session_close_at=snapshot.reaction_session_close_at + shift,
        observation_window_start_at=snapshot.observation_window_start_at + shift,
        observation_window_end_at=snapshot.observation_window_end_at + shift,
        evidence_cutoff_at=snapshot.evidence_cutoff_at + shift,
        decision_cutoff_at=snapshot.decision_cutoff_at + shift,
        candidate_entry_deadline_at=snapshot.candidate_entry_deadline_at + shift,
    )
    receipt = _feature_receipt(snapshot)

    with pytest.raises(StrategyContractRejected) as caught:
        build_strategy_input(
            strategy_snapshot_bytes(snapshot),
            candidate_manifest_bytes=candidate_manifest_bytes(manifest),
            feature_receipt_bytes=feature_receipt_bytes(receipt),
        )

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_rejects_unpermitted_source_class() -> None:
    manifest = _candidate_manifest()
    snapshot = _strategy_snapshot(manifest)
    evidence = list(snapshot.evidence_refs)
    evidence[0] = replace(evidence[0], source_class="UNPERMITTED_WEB_SOURCE")
    snapshot = replace(snapshot, evidence_refs=tuple(evidence))
    receipt = _feature_receipt(snapshot)

    with pytest.raises(StrategyContractRejected) as caught:
        build_strategy_input(
            strategy_snapshot_bytes(snapshot),
            candidate_manifest_bytes=candidate_manifest_bytes(manifest),
            feature_receipt_bytes=feature_receipt_bytes(receipt),
        )

    assert caught.value.reason is StrategyContractReason.POLICY_MISMATCH


def test_strategy_input_requires_eps_surprise_fallback() -> None:
    manifest = _candidate_manifest()
    snapshot = _strategy_snapshot(manifest)
    receipt = _feature_receipt(snapshot)
    unavailable = {
        "earnings.eps_consensus_surprise_pct.v1",
        "earnings.eps_timeseries_sue.v1",
    }
    features = tuple(
        replace(feature, status=FeatureStatus.UNAVAILABLE, value=None, observed_at=None)
        if feature.feature_id in unavailable
        else feature
        for feature in receipt.features
    )
    receipt = replace(receipt, features=features)

    with pytest.raises(StrategyContractRejected) as caught:
        build_strategy_input(
            strategy_snapshot_bytes(snapshot),
            candidate_manifest_bytes=candidate_manifest_bytes(manifest),
            feature_receipt_bytes=feature_receipt_bytes(receipt),
        )

    assert caught.value.reason is StrategyContractReason.DATA_HEALTH_REJECTED


def test_directional_response_validates_without_execution_authority() -> None:
    strategy_input = _strategy_input()
    _, raw, exchange = _reasoner_parts(strategy_input)

    decision = validate_reasoner_response(
        strategy_input,
        exchange,
        raw,
        validator_build_sha256=HASH_A,
    )
    payload = json.loads(strategy_decision_bytes(decision))

    assert decision.direction is Direction.UP
    assert decision.disposition is DecisionDisposition.ACCEPTED
    assert decision.reaction_relation is ReactionRelation.CONTINUE
    assert payload["authority"] == "DIRECTION_ONLY"
    assert set(payload).isdisjoint(
        {"account", "contract", "expiry", "limit_price", "order", "permit", "quantity", "strike"}
    )


def test_reasoner_exchange_round_trips_with_every_policy_identity() -> None:
    strategy_input = _strategy_input()
    _, _, exchange = _reasoner_parts(strategy_input)

    assert parse_reasoner_exchange(reasoner_exchange_bytes(exchange)) == exchange


def test_macro_confirmation_requires_zscore_volume_and_matching_vwap_side() -> None:
    accepted_input = _macro_strategy_input(vwap_distance=Decimal("5"))
    _, accepted_raw, accepted_exchange = _reasoner_parts(accepted_input)
    accepted = validate_reasoner_response(
        accepted_input,
        accepted_exchange,
        accepted_raw,
        validator_build_sha256=HASH_A,
    )

    vetoed_input = _macro_strategy_input(vwap_distance=Decimal("-5"))
    _, vetoed_raw, vetoed_exchange = _reasoner_parts(vetoed_input)
    vetoed = validate_reasoner_response(
        vetoed_input,
        vetoed_exchange,
        vetoed_raw,
        validator_build_sha256=HASH_A,
    )

    assert accepted.direction is Direction.UP
    assert accepted.disposition is DecisionDisposition.ACCEPTED
    assert vetoed.direction is Direction.UNCERTAIN
    assert vetoed.reaction_relation is ReactionRelation.NONE
    assert "CONFIRMATION_NEUTRAL" in vetoed.reason_codes


def test_reasoner_cannot_add_quantity_or_order_authority() -> None:
    strategy_input = _strategy_input()
    _, raw, exchange = _reasoner_parts(strategy_input)
    payload = json.loads(raw)
    payload["quantity"] = 1
    unauthorized = canonical_json_bytes(payload)
    exchange = replace(exchange, raw_response_sha256=sha256_bytes(unauthorized))

    with pytest.raises(StrategyContractRejected) as caught:
        parse_reasoner_decision(unauthorized)

    decision = validate_reasoner_response(
        strategy_input,
        exchange,
        unauthorized,
        validator_build_sha256=HASH_A,
    )

    assert caught.value.reason is StrategyContractReason.UNKNOWN_FIELD
    assert decision.direction is Direction.UNCERTAIN
    assert decision.disposition is DecisionDisposition.REJECTED
    assert decision.reason_codes == ("REASONER_SCHEMA_INVALID",)


def test_reasoner_decoding_drift_is_rejected_even_when_self_hash_matches() -> None:
    strategy_input = _strategy_input()
    _, raw, exchange = _reasoner_parts(strategy_input)
    decoding = replace(exchange.decoding, temperature=Decimal("0.1"))
    exchange = replace(
        exchange,
        decoding=decoding,
        model_config_sha256=reasoner_model_config_sha256(
            provider=exchange.provider,
            model=exchange.model,
            model_revision=exchange.model_revision,
            decoding=decoding,
        ),
    )

    decision = validate_reasoner_response(
        strategy_input,
        exchange,
        raw,
        validator_build_sha256=HASH_A,
    )

    assert decision.direction is Direction.UNCERTAIN
    assert "REASONER_POLICY_MISMATCH" in decision.reason_codes


def test_late_reasoner_response_is_recorded_as_uncertain() -> None:
    strategy_input = _strategy_input()
    late = strategy_input.snapshot.decision_cutoff_at + timedelta(microseconds=1)
    _, raw, exchange = _reasoner_parts(strategy_input, responded_at=late)

    decision = validate_reasoner_response(
        strategy_input,
        exchange,
        raw,
        validator_build_sha256=HASH_A,
    )

    assert decision.direction is Direction.UNCERTAIN
    assert decision.disposition is DecisionDisposition.REJECTED
    assert "LATE_RESPONSE" in decision.reason_codes
    assert reasoner_exchange_sha256(exchange) == decision.reasoner_exchange_sha256
