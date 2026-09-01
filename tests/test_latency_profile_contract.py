from __future__ import annotations

import json

import pytest

from ringdown_market.contracts.latency_profile import (
    LatencyProfileKind,
    LatencyProfileReason,
    LatencyProfileRejected,
    latency_profile_content_sha256,
    load_latency_profile,
    packaged_latency_profile_bytes,
    validate_latency_profile,
)


def _profile() -> dict:
    return json.loads(packaged_latency_profile_bytes())


def _bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def test_packaged_preregistered_profile_validates() -> None:
    profile = load_latency_profile()

    assert profile.kind is LatencyProfileKind.PREREGISTERED
    assert profile.p95_latency_ms == 30000
    assert profile.evaluation_eligible is True
    assert profile.promotion_eligible is True


def test_synthetic_placeholder_fails_evaluation_and_promotion() -> None:
    payload = _profile()
    payload["kind"] = "SYNTHETIC"
    payload["content_sha256"] = latency_profile_content_sha256(payload)

    with pytest.raises(LatencyProfileRejected) as caught:
        validate_latency_profile(_bytes(payload))

    assert caught.value.reason is LatencyProfileReason.SYNTHETIC_PLACEHOLDER


def test_stale_policy_binding_fails_closed() -> None:
    payload = _profile()
    payload["validity"]["policy_sha256"] = "0" * 64
    payload["content_sha256"] = latency_profile_content_sha256(payload)

    with pytest.raises(LatencyProfileRejected) as caught:
        validate_latency_profile(_bytes(payload))

    assert caught.value.reason is LatencyProfileReason.STALE_PROFILE


def test_superseded_profile_fails_closed() -> None:
    payload = _profile()
    payload["validity"]["superseded_by"] = "a-newer-profile"
    payload["content_sha256"] = latency_profile_content_sha256(payload)

    with pytest.raises(LatencyProfileRejected) as caught:
        validate_latency_profile(_bytes(payload))

    assert caught.value.reason is LatencyProfileReason.STALE_PROFILE


def test_content_hash_drift_fails_closed() -> None:
    payload = _profile()
    payload["p95_latency_ms"] = 12345

    with pytest.raises(LatencyProfileRejected) as caught:
        validate_latency_profile(_bytes(payload))

    assert caught.value.reason is LatencyProfileReason.HASH_MISMATCH


def test_quantile_method_is_frozen() -> None:
    payload = _profile()
    payload["quantile_method"] = "INTERPOLATED_P95"
    payload["content_sha256"] = latency_profile_content_sha256(payload)

    with pytest.raises(LatencyProfileRejected) as caught:
        validate_latency_profile(_bytes(payload))

    assert caught.value.reason is LatencyProfileReason.INVALID_DOCUMENT


def test_measured_profile_requires_minimum_observations() -> None:
    payload = _profile()
    payload["kind"] = "HOST_MEASURED"
    payload["observed_samples"] = 5
    payload["content_sha256"] = latency_profile_content_sha256(payload)

    with pytest.raises(LatencyProfileRejected) as caught:
        validate_latency_profile(_bytes(payload))

    assert caught.value.reason is LatencyProfileReason.INSUFFICIENT_OBSERVATIONS
