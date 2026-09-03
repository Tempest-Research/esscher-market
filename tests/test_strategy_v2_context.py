from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from ringdown_market.autonomy.episodes import build_episodic_summary
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.strategy import (
    candidate_manifest_bytes,
    candidate_manifest_sha256,
    feature_receipt_bytes,
    strategy_snapshot_bytes,
    strategy_snapshot_sha256,
)
from ringdown_market.strategy.contracts import (
    StrategyV2ContextRejected,
    build_strategy_v2_context,
    strategy_v2_context_bytes,
    strategy_v2_context_sha256,
)
from ringdown_market.strategy.policy import load_strategy_policy_v2
from test_strategy_contracts import _strategy_input

V2_EARNINGS = "EARNINGS_RESIDUAL_CONTINUATION_V2"


def _v2_artifacts():
    base = _strategy_input()
    policy = load_strategy_policy_v2()
    candidate = policy.candidate(V2_EARNINGS)
    feature_ids = set(candidate["features"])
    reasoner = policy.data["reasoner"]
    manifest = replace(
        base.candidate_manifest,
        candidate_id=V2_EARNINGS,
        policy_sha256=policy.sha256,
    )
    snapshot = replace(
        base.snapshot,
        candidate_id=V2_EARNINGS,
        policy_sha256=policy.sha256,
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        allowed_unknown_codes=tuple(
            sorted((*reasoner["tolerated_unknown_codes"], *reasoner["critical_unknown_codes"]))
        ),
        critical_unknown_codes=tuple(sorted(reasoner["critical_unknown_codes"])),
    )
    receipt = replace(
        base.feature_receipt,
        candidate_id=V2_EARNINGS,
        policy_sha256=policy.sha256,
        strategy_snapshot_sha256=strategy_snapshot_sha256(snapshot),
        features=tuple(
            feature
            for feature in base.feature_receipt.features
            if feature.feature_id in feature_ids
        ),
    )
    return manifest, snapshot, receipt


def _summary(ledger: RiskLedger, *, snapshot, future: bool = False):
    policy = load_strategy_policy_v2()
    return build_episodic_summary(
        ledger,
        as_of=snapshot.evidence_cutoff_at + (timedelta(seconds=1) if future else timedelta()),
        policy_sha256=policy.sha256,
        model_config_sha256="a" * 64,
        candidate_ids=(V2_EARNINGS,),
    )


def test_v2_context_binds_exact_policy_snapshot_feature_and_ledger_validated_memory(
    tmp_path,
) -> None:
    manifest, snapshot, receipt = _v2_artifacts()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        context = build_strategy_v2_context(
            strategy_snapshot_bytes(snapshot),
            candidate_manifest_bytes=candidate_manifest_bytes(manifest),
            feature_receipt_bytes=feature_receipt_bytes(receipt),
            episodic_summary=_summary(ledger, snapshot=snapshot),
            ledger=ledger,
            universe_scan=None,
            news_observations=(),
            news_authorizations={},
            article_attributions=(),
        )
    finally:
        ledger.close()

    assert context.policy_sha256 == load_strategy_policy_v2().sha256
    assert context.strategy_snapshot_sha256 == strategy_snapshot_sha256(snapshot)
    assert context.context_sha256 == strategy_v2_context_sha256(context)
    # Canonical JSON sorts the complete receipt keys, so the first key is the
    # lexicographically earliest V2 identity rather than a construction-order detail.
    assert strategy_v2_context_bytes(context).startswith(b'{"article_attribution_sha256"')
    assert context.news_observation_sha256 == ()


def test_v2_context_rejects_future_or_ledger_forged_memory_before_request_building(
    tmp_path,
) -> None:
    manifest, snapshot, receipt = _v2_artifacts()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        kwargs = {
            "candidate_manifest_bytes": candidate_manifest_bytes(manifest),
            "feature_receipt_bytes": feature_receipt_bytes(receipt),
            "ledger": ledger,
            "universe_scan": None,
            "news_observations": (),
            "news_authorizations": {},
            "article_attributions": (),
        }
        with pytest.raises(StrategyV2ContextRejected):
            build_strategy_v2_context(
                strategy_snapshot_bytes(snapshot),
                episodic_summary=_summary(ledger, snapshot=snapshot, future=True),
                **kwargs,
            )
        with pytest.raises(StrategyV2ContextRejected):
            build_strategy_v2_context(
                strategy_snapshot_bytes(snapshot),
                episodic_summary=replace(
                    _summary(ledger, snapshot=snapshot), summary_sha256="0" * 64
                ),
                **kwargs,
            )
    finally:
        ledger.close()


def test_v2_context_rejects_missing_required_feature_before_host_payload(tmp_path) -> None:
    manifest, snapshot, receipt = _v2_artifacts()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        incomplete = replace(receipt, features=receipt.features[1:])
        with pytest.raises(StrategyV2ContextRejected):
            build_strategy_v2_context(
                strategy_snapshot_bytes(snapshot),
                candidate_manifest_bytes=candidate_manifest_bytes(manifest),
                feature_receipt_bytes=feature_receipt_bytes(incomplete),
                episodic_summary=_summary(ledger, snapshot=snapshot),
                ledger=ledger,
                universe_scan=None,
                news_observations=(),
                news_authorizations={},
                article_attributions=(),
            )
    finally:
        ledger.close()
