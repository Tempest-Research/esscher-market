from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace

import pytest

from esscher.contracts.reasoner_route import (
    ApprovalState,
    RouteCompatibilityState,
    RouteContractReason,
    RouteContractRejected,
    direct_kimi_model_config_sha256,
    load_approved_reasoner_route,
    packaged_route_approval_bytes,
    packaged_route_descriptor_bytes,
    validate_reasoner_route,
)
from esscher.strategy.contracts import (
    reasoner_output_schema_bytes,
    reasoner_output_schema_payload,
    reasoner_output_schema_sha256,
    sha256_bytes,
)


def _descriptor() -> dict[str, object]:
    return json.loads(packaged_route_descriptor_bytes())


def _receipt() -> dict[str, object]:
    return json.loads(packaged_route_approval_bytes())


def _bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _rebound_receipt(descriptor_bytes: bytes) -> bytes:
    receipt = _receipt()
    receipt["route_sha256"] = sha256_bytes(descriptor_bytes)
    return _bytes(receipt)


def _nested(payload: dict[str, object], *path: str) -> dict[str, object]:
    value: object = payload
    for key in path:
        assert isinstance(value, dict)
        value = value[key]
    assert isinstance(value, dict)
    return value


def test_packaged_direct_kimi_route_is_owner_approved_but_v1_ineligible() -> None:
    route = load_approved_reasoner_route()

    assert route.provider == "moonshot_direct"
    assert route.base_url == "https://api.moonshot.ai/v1"
    assert route.model == "kimi-k3"
    assert route.approval_state is ApprovalState.APPROVED
    assert route.approver == "bbeennyy860-cyber"
    assert route.approved_at is not None
    assert route.approved_at.isoformat() == "2026-09-01T13:33:32+00:00"
    assert route.compatibility_state is RouteCompatibilityState.INCOMPATIBLE
    assert route.compatibility_reason_code == "FROZEN_POLICY_DECODING_INCOMPATIBLE"
    assert route.evaluation_eligible is False
    assert route.provider_request_policy.reasoning_effort == "low"
    assert route.provider_request_policy.max_completion_tokens == 512
    assert route.provider_request_policy.effective_temperature == "1.0"
    assert route.provider_request_policy.effective_top_p == "0.95"


def test_direct_request_policy_and_schema_are_exactly_bound() -> None:
    route = load_approved_reasoner_route()
    descriptor = _descriptor()
    policy = _nested(descriptor, "provider_request_policy")
    response_format = _nested(policy, "response_format")
    json_schema = _nested(response_format, "json_schema")

    assert policy["reasoning_effort"] == "low"
    assert policy["max_completion_tokens"] == 512
    assert policy["tool_choice"] == "none"
    assert policy["effective_decoding"] == {"temperature": "1.0", "top_p": "0.95"}
    assert policy["omitted_request_fields"] == [
        "temperature",
        "top_p",
        "seed",
        "max_tokens",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "tools",
    ]
    assert response_format["type"] == "json_schema"
    assert json_schema["strict"] is True
    assert json_schema["schema_sha256"] == reasoner_output_schema_sha256()
    assert route.provider_request_policy.output_schema_sha256 == reasoner_output_schema_sha256()

    schema = reasoner_output_schema_payload()
    assert set(schema) == {"additionalProperties", "properties", "required", "type"}
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "decision",
        "evidence_ids",
        "contradictions",
        "unknowns",
        "strongest_falsifier",
        "summary",
    ]
    assert set(schema["properties"]) == set(schema["required"])
    assert reasoner_output_schema_sha256() == sha256_bytes(reasoner_output_schema_bytes())


def test_direct_model_config_hash_binds_base_and_effective_k3_sampling() -> None:
    route = load_approved_reasoner_route()

    changed_sampling = direct_kimi_model_config_sha256(
        provider=route.provider,
        model=route.model,
        model_revision=route.model_revision,
        base_url=route.base_url,
        caller_decoding=route.caller_decoding,
        provider_request_policy=replace(route.provider_request_policy, effective_top_p="1"),
    )
    changed_base_url = direct_kimi_model_config_sha256(
        provider=route.provider,
        model=route.model,
        model_revision=route.model_revision,
        base_url="https://example.invalid/v1",
        caller_decoding=route.caller_decoding,
        provider_request_policy=route.provider_request_policy,
    )

    assert changed_sampling != route.model_config_sha256
    assert changed_base_url != route.model_config_sha256


@pytest.mark.parametrize(
    ("path", "value", "expected_reason"),
    [
        (("provider",), "different_provider", RouteContractReason.IDENTITY_MISMATCH),
        (("base_url",), "https://example.invalid/v1", RouteContractReason.IDENTITY_MISMATCH),
        (("model",), "different-model", RouteContractReason.IDENTITY_MISMATCH),
        (
            ("provider_request_policy", "reasoning_effort"),
            "high",
            RouteContractReason.POLICY_MISMATCH,
        ),
        (
            ("provider_request_policy", "effective_decoding", "temperature"),
            "0.9",
            RouteContractReason.POLICY_MISMATCH,
        ),
        (
            ("provider_request_policy", "omitted_request_fields"),
            ["temperature"],
            RouteContractReason.POLICY_MISMATCH,
        ),
        (
            ("provider_request_policy", "response_format", "json_schema", "schema_sha256"),
            "0" * 64,
            RouteContractReason.POLICY_MISMATCH,
        ),
    ],
)
def test_direct_provider_identity_and_request_policy_mutations_fail_typed(
    path: tuple[str, ...], value: object, expected_reason: RouteContractReason
) -> None:
    descriptor = _descriptor()
    target = descriptor
    for key in path[:-1]:
        target = _nested(target, key)
    target[path[-1]] = value

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is expected_reason


def test_receipt_exact_byte_binding_rejects_reformat_without_rebinding() -> None:
    descriptor = _descriptor()
    reformatted = json.dumps(descriptor, separators=(",", ":"), sort_keys=True).encode("utf-8")

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(reformatted, packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.HASH_MISMATCH


def test_semantically_identical_reformat_rebinds_only_the_exact_route_hash() -> None:
    packaged = load_approved_reasoner_route()
    descriptor = _descriptor()
    reformatted = json.dumps(descriptor, separators=(",", ":"), sort_keys=True).encode("utf-8")

    route = validate_reasoner_route(reformatted, _rebound_receipt(reformatted))

    assert route.route_sha256 == sha256_bytes(reformatted)
    assert route.route_sha256 != packaged.route_sha256
    assert route.model_config_sha256 == packaged.model_config_sha256
    assert (
        route.provider_request_policy.output_schema_sha256
        == packaged.provider_request_policy.output_schema_sha256
    )
    assert route.evaluation_eligible is False


def test_model_config_receipt_hash_is_rejected_when_it_does_not_bind_k3_semantics() -> None:
    receipt = _receipt()
    receipt["model_config_sha256"] = "0" * 64

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(packaged_route_descriptor_bytes(), _bytes(receipt))

    assert caught.value.reason is RouteContractReason.HASH_MISMATCH


def test_route_hashes_and_schema_hash_are_restart_deterministic() -> None:
    first = load_approved_reasoner_route()
    second = validate_reasoner_route(
        packaged_route_descriptor_bytes(), packaged_route_approval_bytes()
    )

    assert (
        first.route_sha256 == second.route_sha256 == sha256_bytes(packaged_route_descriptor_bytes())
    )
    assert first.model_config_sha256 == second.model_config_sha256
    assert reasoner_output_schema_bytes() == reasoner_output_schema_bytes()
    assert reasoner_output_schema_sha256() == reasoner_output_schema_sha256()

    restarted = subprocess.run(
        [
            sys.executable,
            "-c",
            "; ".join(
                (
                    "import esscher.contracts.reasoner_route as r",
                    "import esscher.strategy.contracts as s",
                    "route = r.load_approved_reasoner_route()",
                    "h = s.reasoner_output_schema_sha256",
                    "print(route.route_sha256, route.model_config_sha256, h())",
                )
            ),
        ],
        capture_output=True,
        check=True,
        text=True,
    )

    assert restarted.stdout.split() == [
        first.route_sha256,
        first.model_config_sha256,
        reasoner_output_schema_sha256(),
    ]


def test_descriptor_broker_account_and_secret_arguments_remain_denied() -> None:
    for path, value, expected_reason in (
        (("authority", "broker"), True, RouteContractReason.AUTHORITY_VIOLATION),
        (("authority", "account"), True, RouteContractReason.AUTHORITY_VIOLATION),
        (
            ("application_arguments",),
            {"api_key": "not-allowed"},
            RouteContractReason.SECRET_ARGUMENT,
        ),
    ):
        descriptor = _descriptor()
        target = descriptor
        for key in path[:-1]:
            target = _nested(target, key)
        target[path[-1]] = value

        with pytest.raises(RouteContractRejected) as caught:
            validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

        assert caught.value.reason is expected_reason
