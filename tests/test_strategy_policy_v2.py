from __future__ import annotations

import json
from types import MappingProxyType

import pytest

from ringdown_market.strategy.policy import (
    StrategyPolicyError,
    load_strategy_policy,
    load_strategy_policy_v2,
    parse_strategy_policy,
    parse_strategy_policy_v2,
    strategy_policy_bytes,
    strategy_policy_sha256,
    strategy_policy_v2_bytes,
    strategy_policy_v2_sha256,
)

V2_CANDIDATES = (
    "EARNINGS_RESIDUAL_CONTINUATION_V2",
    "MARKET_ANCHOR_INTRADAY_CONTINUATION_V1",
    "LIQUID_STOCK_CATALYST_CONTINUATION_V1",
)


def test_v2_policy_is_an_immutable_canonical_closed_three_lane_contract() -> None:
    policy = load_strategy_policy_v2()

    assert policy.candidate_ids == V2_CANDIDATES
    assert policy.sha256 == strategy_policy_v2_sha256()
    assert strategy_policy_v2_bytes() == json.dumps(
        json.loads(strategy_policy_v2_bytes()),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert isinstance(policy.data, MappingProxyType)
    with pytest.raises(TypeError):
        policy.data["policy_id"] = "forged"  # type: ignore[index]

    by_id = {candidate["candidate_id"]: candidate for candidate in policy.data["candidates"]}
    assert tuple(by_id) == V2_CANDIDATES
    for candidate in by_id.values():
        assert set(candidate) == {
            "candidate_id",
            "confirmation",
            "critical_unknown_codes",
            "evidence",
            "features",
            "lane",
            "requirements",
        }
        assert candidate["features"]
        assert candidate["critical_unknown_codes"]
        assert candidate["confirmation"]["deterministic_downstream_only"] is True
        assert candidate["confirmation"]["validated_alpha_claim"] is False

    market_anchor = by_id["MARKET_ANCHOR_INTRADAY_CONTINUATION_V1"]
    assert market_anchor["requirements"]["symbol_allowlist"] == ("QQQ", "SPY")
    catalyst = by_id["LIQUID_STOCK_CATALYST_CONTINUATION_V1"]
    assert catalyst["requirements"]["requires_decision_ready_universe"] is True
    assert catalyst["requirements"]["requires_complete_authorized_benzinga_news"] is True


def test_v2_policy_rejects_any_forged_duplicate_unknown_float_or_noncanonical_bytes() -> None:
    raw = strategy_policy_v2_bytes()
    variations = (
        raw + b"\n",
        raw.replace(b'"schema":', b'"extra":true,"schema":', 1),
        raw.replace(b'"schema":', b'"schema":"forged","schema":', 1),
        raw.replace(b'"schema_version":2', b'"schema_version":2.0', 1),
    )

    for forged in variations:
        with pytest.raises(StrategyPolicyError):
            parse_strategy_policy_v2(forged)


def test_v1_policy_bytes_hash_and_loader_remain_unchanged() -> None:
    v1 = load_strategy_policy()

    assert parse_strategy_policy(strategy_policy_bytes()) == v1
    assert strategy_policy_sha256() == v1.sha256
    assert v1.candidate_ids == (
        "EARNINGS_RESIDUAL_CONTINUATION_V1",
        "MACRO_SPY_CONTINUATION_CHALLENGER_V1",
    )
