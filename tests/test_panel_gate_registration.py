from __future__ import annotations

import pytest

from ringdown_market.data.snapshot import STRATEGY_SNAPSHOT_PROTOCOL_SHA256
from ringdown_market.panel import manifest as panel_manifest
from ringdown_market.panel.manifest import PanelRejectionReason, validate_panel_manifest
from ringdown_market.strategy.policy import STRATEGY_POLICY_V1_SHA256
from test_panel_manifest import (
    _assert_rejected,
    _manifest_bytes,
    _manifest_payload,
    _p0_exclusions,
    _rule_bytes,
    _sha256,
)


def test_merged_strategy_policy_hash_is_registered() -> None:
    assert STRATEGY_POLICY_V1_SHA256 in panel_manifest.KNOWN_STRATEGY_POLICY_SHA256


def test_merged_snapshot_protocol_hash_is_registered() -> None:
    assert STRATEGY_SNAPSHOT_PROTOCOL_SHA256 in panel_manifest.KNOWN_SNAPSHOT_PROTOCOL_SHA256


def test_real_manifest_validates_against_merged_contracts_without_monkeypatch() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(
        rule,
        data_class="POINT_IN_TIME_EVENT_PANEL",
        eligible_count=20,
        excluded_events=_p0_exclusions(),
        strategy_policy_sha256=STRATEGY_POLICY_V1_SHA256,
        snapshot_protocol_sha256=STRATEGY_SNAPSHOT_PROTOCOL_SHA256,
    )

    validated = validate_panel_manifest(_manifest_bytes(payload), rule)

    assert validated.minimum_events == 20
    assert len(validated.eligible_event_ids) == 20
    assert len(validated.excluded_events) == 4


def test_unregistered_strategy_hash_still_fails_closed() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(
        rule,
        data_class="POINT_IN_TIME_EVENT_PANEL",
        eligible_count=20,
        excluded_events=_p0_exclusions(),
        strategy_policy_sha256=_sha256(b"not-a-merged-policy"),
        snapshot_protocol_sha256=STRATEGY_SNAPSHOT_PROTOCOL_SHA256,
    )

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.UPSTREAM_CONTRACT_MISSING
    )
    assert caught.path.endswith("strategy_policy_sha256")


def test_unregistered_snapshot_hash_still_fails_closed() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(
        rule,
        data_class="POINT_IN_TIME_EVENT_PANEL",
        eligible_count=20,
        excluded_events=_p0_exclusions(),
        strategy_policy_sha256=STRATEGY_POLICY_V1_SHA256,
        snapshot_protocol_sha256=_sha256(b"not-a-merged-protocol"),
    )

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.UPSTREAM_CONTRACT_MISSING
    )
    assert caught.path.endswith("snapshot_protocol_sha256")


def test_snapshot_protocol_identity_is_deterministic() -> None:
    import hashlib

    from ringdown_market.data.snapshot import (
        STRATEGY_SNAPSHOT_PROTOCOL,
        _canonical_json_bytes,
    )

    assert (
        hashlib.sha256(_canonical_json_bytes(STRATEGY_SNAPSHOT_PROTOCOL)).hexdigest()
        == STRATEGY_SNAPSHOT_PROTOCOL_SHA256
    )


@pytest.mark.parametrize(
    "registry",
    ["KNOWN_STRATEGY_POLICY_SHA256", "KNOWN_SNAPSHOT_PROTOCOL_SHA256"],
)
def test_registries_are_non_empty_frozen_sets(registry: str) -> None:
    value = getattr(panel_manifest, registry)
    assert isinstance(value, frozenset)
    assert len(value) >= 1
