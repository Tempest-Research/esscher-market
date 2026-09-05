from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from esscher.autonomy.episodes import build_episodic_summary
from esscher.autonomy.universe import (
    ProductKind,
    UniverseLane,
    UniverseObservation,
    scan_universe,
)
from esscher.contracts.reasoner_route import (
    RouteCompatibilityState,
    RouteContractRejected,
    load_approved_reasoner_route,
    load_approved_reasoner_route_v2,
    packaged_route_approval_v2_bytes,
    packaged_route_descriptor_v2_bytes,
    validate_reasoner_route_v2,
)
from esscher.risk.ledger import RiskLedger
from esscher.sourcedata.alpaca_news import (
    PUBLISHER_ID,
    REDISTRIBUTION_STATUS,
    SOURCE_ID,
    SOURCE_POLICY_SHA256,
    SOURCE_URL_PREFIX,
    ArticleAttribution,
)
from esscher.sourcedata.news import (
    NewsObservation,
    NewsSourceAuthorization,
    news_content_sha256,
    news_observation_sha256,
)
from esscher.strategy import (
    candidate_manifest_bytes,
    candidate_manifest_sha256,
    feature_receipt_bytes,
    strategy_snapshot_bytes,
    strategy_snapshot_sha256,
)
from esscher.strategy.contracts import (
    StrategyV2ContextRejected,
    build_strategy_v2_context,
    reasoner_output_schema_sha256,
    reasoner_output_schema_v2_sha256,
    reasoner_system_prompt_sha256,
    reasoner_system_prompt_v2_sha256,
    sha256_bytes,
)
from esscher.strategy.host_route import (
    HostRouteConfigurationError,
    HostRouteInputIntegrityError,
    build_kimi_k3_v2_request,
)
from esscher.strategy.models import GuidanceDirection
from esscher.strategy.policy import load_strategy_policy, load_strategy_policy_v2
from test_strategy_contracts import _strategy_input

V2_EARNINGS = "EARNINGS_RESIDUAL_CONTINUATION_V2"
V2_CATALYST = "LIQUID_STOCK_CATALYST_CONTINUATION_V1"
FORBIDDEN_BOUNDARY_KEYS = frozenset(
    {
        "account",
        "api_key",
        "broker",
        "credential",
        "entry",
        "exit",
        "order",
        "position",
        "quantity",
        "risk_tier",
        "secret",
        "sizing",
        "token",
    }
)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for nested in value.values() for key in _all_keys(nested)}
    if isinstance(value, list):
        return {key for nested in value for key in _all_keys(nested)}
    return set()


def _v2_artifacts(candidate_id: str):
    base = _strategy_input()
    policy = load_strategy_policy_v2()
    candidate = policy.candidate(candidate_id)
    reasoner = policy.data["reasoner"]
    feature_ids = set(candidate["features"])
    manifest = replace(
        base.candidate_manifest,
        candidate_id=candidate_id,
        policy_sha256=policy.sha256,
    )
    snapshot = replace(
        base.snapshot,
        candidate_id=candidate_id,
        policy_sha256=policy.sha256,
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        allowed_unknown_codes=tuple(
            sorted((*reasoner["tolerated_unknown_codes"], *reasoner["critical_unknown_codes"]))
        ),
        critical_unknown_codes=tuple(sorted(reasoner["critical_unknown_codes"])),
    )
    receipt = replace(
        base.feature_receipt,
        candidate_id=candidate_id,
        policy_sha256=policy.sha256,
        strategy_snapshot_sha256=strategy_snapshot_sha256(snapshot),
        features=tuple(
            feature
            for feature in base.feature_receipt.features
            if feature.feature_id in feature_ids
        ),
    )
    return manifest, snapshot, receipt


def _summary(ledger: RiskLedger, *, snapshot, route, limit: int = 64):
    return build_episodic_summary(
        ledger,
        as_of=snapshot.evidence_cutoff_at,
        policy_sha256=load_strategy_policy_v2().sha256,
        model_config_sha256=route.model_config_sha256,
        candidate_ids=(snapshot.candidate_id,),
        limit=limit,
    )


def _authorized_benzinga_news(snapshot, *, headline: str = "Catalyst headline"):
    body = "Quoted private-use Benzinga article body."
    available_at = snapshot.evidence_cutoff_at - timedelta(seconds=2)
    observation = NewsObservation(
        observation_id="NEWS-STRATEGY-V2-0001",
        source_id=SOURCE_ID,
        source_policy_sha256=SOURCE_POLICY_SHA256,
        publisher_id=PUBLISHER_ID,
        canonical_url=f"{SOURCE_URL_PREFIX}news/strategy-v2-0001",
        provider_article_id="strategy-v2-0001",
        publisher_published_at=available_at - timedelta(minutes=2),
        provider_available_at=available_at,
        retrieved_at=available_at,
        content_sha256=news_content_sha256(headline, body),
        raw_blob_sha256="a" * 64,
        entitlement_status="FEASIBLE",
        redistribution_status=REDISTRIBUTION_STATUS,
        revision_of=None,
        retrieval_status="COMPLETE",
        headline=headline,
        body=body,
    )
    attribution = ArticleAttribution(
        provider_article_id=observation.provider_article_id,
        symbols=(snapshot.ticker,),
        observation_id=observation.observation_id,
        observation_sha256=news_observation_sha256(observation),
    )
    authorizations = {
        SOURCE_ID: NewsSourceAuthorization(
            source_id=SOURCE_ID,
            source_policy_sha256=SOURCE_POLICY_SHA256,
            verdict="FEASIBLE",
            publisher_ids=(PUBLISHER_ID,),
            canonical_url_prefixes=(SOURCE_URL_PREFIX,),
            redistribution_status=REDISTRIBUTION_STATUS,
        )
    }
    return (observation,), authorizations, (attribution,)


def _decision_ready_universe(snapshot, *, last: Decimal = Decimal("50")):
    as_of = snapshot.evidence_cutoff_at
    observation = UniverseObservation(
        symbol=snapshot.ticker,
        product_kind=ProductKind.US_COMMON_STOCK,
        lane=UniverseLane.CATALYST_STOCK,
        active=True,
        tradable=True,
        last=last,
        bid=last - Decimal("0.01"),
        ask=last + Decimal("0.01"),
        quoted_at=as_of - timedelta(seconds=15),
        observed_at=as_of,
        option_contracts_active=100,
        option_page_complete=True,
        news_records=1,
        news_page_complete=True,
        iv_available=True,
        greeks_available=True,
        activity_rank=1,
        absolute_movement=Decimal("0.03"),
    )
    return scan_universe((observation,), as_of=as_of)


def _context(
    ledger: RiskLedger,
    route,
    *,
    candidate_id: str = V2_EARNINGS,
    manifest=None,
    snapshot=None,
    receipt=None,
    summary_limit: int = 64,
    universe=None,
    news_observations=(),
    news_authorizations=None,
    article_attributions=(),
):
    default_manifest, default_snapshot, default_receipt = _v2_artifacts(candidate_id)
    manifest = default_manifest if manifest is None else manifest
    snapshot = default_snapshot if snapshot is None else snapshot
    receipt = default_receipt if receipt is None else receipt
    if candidate_id == V2_CATALYST and universe is None:
        universe = _decision_ready_universe(snapshot)
    if candidate_id == V2_CATALYST and not news_observations:
        news_observations, news_authorizations, article_attributions = _authorized_benzinga_news(
            snapshot
        )
    if news_authorizations is None:
        news_authorizations = {}
    return build_strategy_v2_context(
        strategy_snapshot_bytes(snapshot),
        candidate_manifest_bytes=candidate_manifest_bytes(manifest),
        feature_receipt_bytes=feature_receipt_bytes(receipt),
        episodic_summary=_summary(ledger, snapshot=snapshot, route=route, limit=summary_limit),
        ledger=ledger,
        universe_scan=universe,
        news_observations=news_observations,
        news_authorizations=news_authorizations,
        article_attributions=article_attributions,
    )


def _mutate_feature(receipt):
    feature = receipt.features[0]
    if isinstance(feature.value, Decimal):
        value = feature.value + Decimal("0.01")
    elif isinstance(feature.value, int):
        value = feature.value + 1
    else:
        value = (
            GuidanceDirection.LOWERED
            if str(feature.value) != GuidanceDirection.LOWERED.value
            else GuidanceDirection.RAISED
        )
    return replace(receipt, features=(replace(feature, value=value), *receipt.features[1:]))


def test_v1_is_inert_and_only_exact_packaged_v2_bytes_are_eligible() -> None:
    v1 = load_approved_reasoner_route()
    v2 = load_approved_reasoner_route_v2()

    assert v1.compatibility_state is RouteCompatibilityState.INCOMPATIBLE
    assert v1.evaluation_eligible is False
    assert v2.compatibility_state is RouteCompatibilityState.COMPATIBLE
    assert v2.evaluation_eligible is True
    assert v2.caller_decoding.seed is None
    assert str(v2.caller_decoding.temperature) == "1.0"
    assert str(v2.caller_decoding.top_p) == "0.95"
    assert v2.provider_request_policy.output_schema_sha256 == reasoner_output_schema_v2_sha256()

    descriptor = json.dumps(
        json.loads(packaged_route_descriptor_v2_bytes()),
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    rebound_approval = json.loads(packaged_route_approval_v2_bytes())
    rebound_approval["route_sha256"] = sha256_bytes(descriptor)
    approval = json.dumps(rebound_approval, indent=2, sort_keys=True).encode("utf-8")
    with pytest.raises(RouteContractRejected):
        validate_reasoner_route_v2(descriptor, approval)


def test_v2_host_requires_exact_packaged_route_and_ledger_validated_context(tmp_path) -> None:
    route = load_approved_reasoner_route_v2()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        context = _context(ledger, route)
        request = build_kimi_k3_v2_request(route, context, ledger=ledger)

        user_content = json.loads(request.payload["messages"][1]["content"])
        system_content = json.loads(request.payload["messages"][0]["content"])
        assert user_content["identities"]["context_sha256"] == context.context_sha256
        assert user_content["artifacts"]["episodic_summary"]["summary_sha256"] == (
            context.episodic_summary_sha256
        )
        assert system_content["schema"] == "esscher.kimi_k3_system_prompt_v2"
        assert request.prompt_sha256 == reasoner_system_prompt_v2_sha256(V2_EARNINGS)
        assert request.output_schema_sha256 == reasoner_output_schema_v2_sha256()
        assert FORBIDDEN_BOUNDARY_KEYS.isdisjoint(_all_keys(request.payload))

        with pytest.raises(HostRouteConfigurationError):
            build_kimi_k3_v2_request(replace(route), context, ledger=ledger)
        with pytest.raises(HostRouteInputIntegrityError):
            build_kimi_k3_v2_request(
                route,
                replace(
                    context,
                    episodic_summary=replace(context.episodic_summary, summary_sha256="0" * 64),
                ),
                ledger=ledger,
            )
    finally:
        ledger.close()


def test_v2_request_hash_changes_for_feature_evidence_and_episodic_summary_mutations(
    tmp_path,
) -> None:
    route = load_approved_reasoner_route_v2()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        manifest, snapshot, receipt = _v2_artifacts(V2_EARNINGS)
        baseline = build_kimi_k3_v2_request(
            route,
            _context(ledger, route, manifest=manifest, snapshot=snapshot, receipt=receipt),
            ledger=ledger,
        )
        changed_feature = build_kimi_k3_v2_request(
            route,
            _context(
                ledger,
                route,
                manifest=manifest,
                snapshot=snapshot,
                receipt=_mutate_feature(receipt),
            ),
            ledger=ledger,
        )
        changed_evidence_snapshot = replace(
            snapshot,
            evidence_refs=(
                replace(snapshot.evidence_refs[0], content_sha256="f" * 64),
                *snapshot.evidence_refs[1:],
            ),
        )
        changed_evidence_receipt = replace(
            receipt,
            strategy_snapshot_sha256=strategy_snapshot_sha256(changed_evidence_snapshot),
        )
        changed_evidence = build_kimi_k3_v2_request(
            route,
            _context(
                ledger,
                route,
                manifest=manifest,
                snapshot=changed_evidence_snapshot,
                receipt=changed_evidence_receipt,
            ),
            ledger=ledger,
        )
        changed_summary = build_kimi_k3_v2_request(
            route,
            _context(
                ledger,
                route,
                manifest=manifest,
                snapshot=snapshot,
                receipt=receipt,
                summary_limit=63,
            ),
            ledger=ledger,
        )
    finally:
        ledger.close()

    assert changed_feature.request_sha256 != baseline.request_sha256
    assert changed_evidence.request_sha256 != baseline.request_sha256
    assert changed_summary.request_sha256 != baseline.request_sha256
    assert reasoner_system_prompt_v2_sha256(V2_EARNINGS) != reasoner_system_prompt_sha256(
        "EARNINGS_RESIDUAL_CONTINUATION_V1"
    )
    assert reasoner_output_schema_v2_sha256() != reasoner_output_schema_sha256()
    assert route.model_config_sha256 != load_approved_reasoner_route().model_config_sha256
    assert load_strategy_policy_v2().sha256 != load_strategy_policy().sha256


def test_v2_catalyst_binds_complete_authorized_news_and_decision_ready_universe(tmp_path) -> None:
    route = load_approved_reasoner_route_v2()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        manifest, snapshot, receipt = _v2_artifacts(V2_CATALYST)
        news, authorizations, attributions = _authorized_benzinga_news(snapshot)
        universe = _decision_ready_universe(snapshot)
        context = _context(
            ledger,
            route,
            candidate_id=V2_CATALYST,
            manifest=manifest,
            snapshot=snapshot,
            receipt=receipt,
            universe=universe,
            news_observations=news,
            news_authorizations=authorizations,
            article_attributions=attributions,
        )
        baseline = build_kimi_k3_v2_request(route, context, ledger=ledger)
        payload = json.loads(baseline.payload["messages"][1]["content"])
        assert payload["identities"]["universe_scan_sha256"] == context.universe_scan_sha256
        assert payload["identities"]["news_observation_sha256"] == list(
            context.news_observation_sha256
        )
        assert (
            payload["artifacts"]["untrusted_news"][0]["classification"] == "UNTRUSTED_QUOTED_DATA"
        )
        assert payload["artifacts"]["untrusted_news"][0]["headline"] == news[0].headline

        changed_news, _, changed_attributions = _authorized_benzinga_news(
            snapshot,
            headline="Later catalyst headline",
        )
        news_changed = build_kimi_k3_v2_request(
            route,
            _context(
                ledger,
                route,
                candidate_id=V2_CATALYST,
                manifest=manifest,
                snapshot=snapshot,
                receipt=receipt,
                universe=universe,
                news_observations=changed_news,
                news_authorizations=authorizations,
                article_attributions=changed_attributions,
            ),
            ledger=ledger,
        )
        universe_changed = build_kimi_k3_v2_request(
            route,
            _context(
                ledger,
                route,
                candidate_id=V2_CATALYST,
                manifest=manifest,
                snapshot=snapshot,
                receipt=receipt,
                universe=_decision_ready_universe(snapshot, last=Decimal("51")),
                news_observations=news,
                news_authorizations=authorizations,
                article_attributions=attributions,
            ),
            ledger=ledger,
        )
    finally:
        ledger.close()

    assert news_changed.request_sha256 != baseline.request_sha256
    assert universe_changed.request_sha256 != baseline.request_sha256


def test_v2_catalyst_rejects_news_arriving_after_the_evidence_freeze(tmp_path) -> None:
    route = load_approved_reasoner_route_v2()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        manifest, snapshot, receipt = _v2_artifacts(V2_CATALYST)
        assert snapshot.evidence_cutoff_at < snapshot.decision_cutoff_at
        late_at = snapshot.evidence_cutoff_at + timedelta(seconds=1)
        assert late_at < snapshot.decision_cutoff_at
        observations, authorizations, attributions = _authorized_benzinga_news(snapshot)
        late_observation = replace(
            observations[0],
            provider_available_at=late_at,
            retrieved_at=late_at,
            raw_blob_sha256="b" * 64,
        )
        late_attribution = replace(
            attributions[0],
            observation_sha256=news_observation_sha256(late_observation),
        )

        with pytest.raises(StrategyV2ContextRejected, match="news"):
            _context(
                ledger,
                route,
                candidate_id=V2_CATALYST,
                manifest=manifest,
                snapshot=snapshot,
                receipt=receipt,
                news_observations=(late_observation,),
                news_authorizations=authorizations,
                article_attributions=(late_attribution,),
            )
    finally:
        ledger.close()
