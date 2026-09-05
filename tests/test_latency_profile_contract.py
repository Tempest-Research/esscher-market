from __future__ import annotations

import json

import pytest

from esscher.contracts.latency_profile import (
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


def test_packaged_host_measured_profile_validates() -> None:
    profile = load_latency_profile()

    assert profile.kind is LatencyProfileKind.HOST_MEASURED
    assert profile.p95_latency_ms == 5578
    assert profile.observed_samples == 30
    assert profile.observed_samples >= profile_minimum_observations()
    assert profile.evaluation_eligible is True
    assert profile.promotion_eligible is True


def profile_minimum_observations() -> int:
    return json.loads(packaged_latency_profile_bytes())["minimum_sample_observations"]


def test_profile_missing_field_fails_closed() -> None:
    payload = _profile()
    del payload["clock_source"]

    with pytest.raises(LatencyProfileRejected) as caught:
        validate_latency_profile(_bytes(payload))

    assert caught.value.reason is LatencyProfileReason.MISSING_FIELD


def test_profile_unknown_field_fails_closed() -> None:
    payload = _profile()
    payload["extra_field"] = "x"

    with pytest.raises(LatencyProfileRejected) as caught:
        validate_latency_profile(_bytes(payload))

    assert caught.value.reason is LatencyProfileReason.UNKNOWN_FIELD


def test_profile_is_reproducible() -> None:
    payload = _profile()
    first = latency_profile_content_sha256(payload)
    second = latency_profile_content_sha256(payload)

    assert first == second
    assert load_latency_profile().content_sha256 == first


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
