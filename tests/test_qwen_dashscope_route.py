"""Issue #68: the owner-approved qwen3.8-max-0902 DashScope route (V5, current).

Covers the contract, builder, adapter, and the assembled-engine lane for the
official Alibaba DashScope pivot (gate measurement 30/30 strict-schema-valid,
nearest-rank p95 5062 ms; the free furry.vg gateway's evening concurrency caps
motivated the move to official metered infrastructure).

Every test is offline: the transport is a fake returning the owner-probe-verified
DashScope envelope shape. The credential is a labelled fake and is asserted to be
discarded at construction. The headliner is the engine integration: the real
adapter drives the assembled BoundedDecisionEngine to an ACCEPTED decision with
policy-registry exchange identities.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from esscher.contracts.reasoner_route import (
    QWEN_DASHSCOPE_BASE_URL,
    QWEN_DASHSCOPE_MAX_COMPLETION_TOKENS,
    QWEN_DASHSCOPE_MODEL,
    QWEN_DASHSCOPE_PROVIDER,
    ApprovalState,
    RouteCompatibilityState,
    RouteContractReason,
    RouteContractRejected,
    load_approved_reasoner_route_v4,
    load_approved_reasoner_route_v5,
    load_current_approved_reasoner_route,
    packaged_route_descriptor_v5_bytes,
    validate_reasoner_route_v5,
)
from esscher.runtime.host_composition import rehearsal_direction, rehearsal_timeline
from esscher.strategy.contracts import (
    reasoner_output_schema_sha256,
    reasoner_policy_hashes,
    reasoner_system_prompt_bytes,
)
from esscher.strategy.engine import BoundedDecisionEngine
from esscher.strategy.host_route import (
    ENV_QWEN_DASHSCOPE_API_KEY,
    HostRouteConfigurationError,
    HostRouteSecretBoundaryError,
    QwenDashScopeReasonerRoute,
    build_qwen_dashscope_request,
    invoke_qwen_dashscope_transport,
    load_qwen_dashscope_route_environment,
    unwrap_openai_chat_envelope,
)
from esscher.strategy.models import ExchangeStatus
from esscher.strategy.reasoner import (
    SYNTHETIC_ROUTE_IDENTITY,
    ReasonerRouteRequest,
    RouteIdentity,
)
from test_paper_mcp_composition import _decision_response_bytes, _joined_input

FAKE_KEY = "host-owned-test-key-not-a-real-credential"
DASHSCOPE_IDENTITY = RouteIdentity(provider=QWEN_DASHSCOPE_PROVIDER, model=QWEN_DASHSCOPE_MODEL)
CANDIDATE = "EARNINGS_RESIDUAL_CONTINUATION_V1"


def _envelope(content: bytes, *, reasoning: str | None = None) -> bytes:
    message: dict[str, object] = {"content": content.decode("utf-8"), "role": "assistant"}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    envelope: dict[str, object] = {
        "choices": [{"finish_reason": "stop", "message": message}],
        "model": QWEN_DASHSCOPE_MODEL,
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _adapter(
    transport=None, *, identity: RouteIdentity = DASHSCOPE_IDENTITY
) -> QwenDashScopeReasonerRoute:
    return QwenDashScopeReasonerRoute(
        route=load_approved_reasoner_route_v5(),
        api_key=FAKE_KEY,
        identity=identity,
        transport=transport,
    )


def _request(ablate: bool = False) -> ReasonerRouteRequest:
    joined = _joined_input()
    timeline = rehearsal_timeline(joined)
    return ReasonerRouteRequest(
        strategy_input=joined, started_at=timeline.started_at, ablate_text=ablate
    )


# --- contract layer ---------------------------------------------------------


def test_v5_package_is_approved_compatible_and_eligible() -> None:
    route = load_approved_reasoner_route_v5()

    assert route.route_id == "ESSCHER_BOUNDED_REASONER_ROUTE_V5"
    assert route.provider == QWEN_DASHSCOPE_PROVIDER
    assert route.model == QWEN_DASHSCOPE_MODEL
    assert route.model_revision is None
    assert route.base_url == QWEN_DASHSCOPE_BASE_URL
    assert route.approval_state is ApprovalState.APPROVED
    assert route.approver == "MS-Mesh"
    assert route.approved_at is not None
    assert route.compatibility_state is RouteCompatibilityState.COMPATIBLE
    assert route.evaluation_eligible is True
    # No wire response_format on this route: DashScope json_object demands a
    # literal "json" token the immutable frozen prompt never contains, so the
    # schema is prompt-directed and client-validated.
    assert route.provider_request_policy.response_format_type == "prompt_directed_json"
    assert route.provider_request_policy.reasoning_effort == "none"
    assert route.provider_request_policy.max_completion_tokens == 1024
    assert route.provider_request_policy.strict_json_schema is False
    assert route.provider_request_policy.output_schema_sha256 == reasoner_output_schema_sha256()
    assert "response_format" in route.provider_request_policy.omitted_request_fields
    assert "tool_choice" in route.provider_request_policy.omitted_request_fields


def test_current_approved_route_is_the_v5_dashscope_package() -> None:
    assert load_current_approved_reasoner_route() is load_approved_reasoner_route_v5()
    # V4 furry-gateway deepseek remains packaged and loadable as a dormant alternate.
    assert load_approved_reasoner_route_v4() is not load_current_approved_reasoner_route()


def test_v5_validation_rejects_lookalike_bytes() -> None:
    descriptor = packaged_route_descriptor_v5_bytes()
    tampered = descriptor.replace(b"qwen3.8-max-0902", b"qwen3.8-max-0901")

    with pytest.raises(RouteContractRejected) as drift:
        validate_reasoner_route_v5(tampered, tampered)
    assert drift.value.reason is RouteContractReason.HASH_MISMATCH

    with pytest.raises(RouteContractRejected):
        validate_reasoner_route_v5(descriptor, descriptor)


# --- builder ----------------------------------------------------------------


def test_builder_pins_the_probe_verified_wire_shape() -> None:
    provider_request = build_qwen_dashscope_request(load_approved_reasoner_route_v5(), _request())

    assert provider_request.endpoint == f"{QWEN_DASHSCOPE_BASE_URL}/chat/completions"
    payload = provider_request.payload
    assert payload["model"] == QWEN_DASHSCOPE_MODEL
    assert payload["max_tokens"] == QWEN_DASHSCOPE_MAX_COMPLETION_TOKENS == 1024
    assert payload["temperature"] == 0
    assert payload["top_p"] == 1.0
    assert payload["enable_thinking"] is False
    # Frozen wire omissions specific to this route: no response_format (the
    # json_object mode is incompatible with the immutable prompt) and no
    # tool_choice (no tools are ever offered).
    assert "response_format" not in payload
    assert "tool_choice" not in payload
    assert "thinking" not in payload
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == reasoner_system_prompt_bytes(CANDIDATE).decode("utf-8")
    assert messages[1]["role"] == "user"
    user_payload = json.loads(str(messages[1]["content"]))
    assert sorted(user_payload) == ["feature_receipt", "identities", "strategy_snapshot"]
    assert user_payload["identities"]["candidate_id"] == CANDIDATE
    # Frozen omissions: never on the wire.
    for omitted in (
        "max_completion_tokens",
        "reasoning_effort",
        "seed",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "tools",
        "logprobs",
        "logit_bias",
        "stream",
        "thinking",
        "response_format",
        "tool_choice",
    ):
        assert omitted not in payload
    # Deterministic request identity; no secret material anywhere.
    again = build_qwen_dashscope_request(load_approved_reasoner_route_v5(), _request())
    assert again.request_sha256 == provider_request.request_sha256
    assert again.payload_bytes == provider_request.payload_bytes
    serialized = json.dumps(payload, sort_keys=True)
    assert FAKE_KEY not in serialized
    assert "sk-" not in serialized


def test_builder_rejects_forged_and_ablation_requests() -> None:
    real = load_approved_reasoner_route_v5()
    forged = dataclasses.replace(real)

    with pytest.raises(HostRouteConfigurationError, match="exact packaged V5"):
        build_qwen_dashscope_request(forged, _request())

    with pytest.raises(HostRouteConfigurationError, match="ablation"):
        build_qwen_dashscope_request(real, _request(ablate=True))


# --- adapter ----------------------------------------------------------------


def test_adapter_completes_with_policy_registry_exchange_identities() -> None:
    content = _decision_response_bytes()
    calls: list[tuple[str, dict[str, object]]] = []

    def transport(endpoint: str, payload: dict[str, object]) -> bytes:
        calls.append((endpoint, payload))
        return _envelope(content)

    adapter = _adapter(transport)
    result = adapter(_request())

    exchange = result.exchange
    assert exchange.status is ExchangeStatus.COMPLETED
    assert result.raw_response_bytes == content
    assert exchange.raw_response_sha256 is not None
    route_sha, prompt_sha, schema_sha = reasoner_policy_hashes(CANDIDATE)
    assert exchange.route_sha256 == route_sha
    assert exchange.prompt_sha256 == prompt_sha
    assert exchange.output_schema_sha256 == schema_sha
    assert exchange.model_config_sha256 == DASHSCOPE_IDENTITY.model_config_sha256()
    assert exchange.provider == QWEN_DASHSCOPE_PROVIDER
    assert exchange.model == QWEN_DASHSCOPE_MODEL
    assert exchange.responded_at is not None
    assert exchange.responded_at <= exchange.deadline_at
    assert len(calls) == 1


def test_adapter_maps_failures_to_typed_exchanges_without_retry() -> None:
    content = _decision_response_bytes()
    attempts: list[int] = []

    def timeout_transport(endpoint: str, payload: dict[str, object]) -> bytes:
        attempts.append(1)
        raise TimeoutError("provider transport detail must never leak")

    result = _adapter(timeout_transport)(_request())
    assert result.exchange.status is ExchangeStatus.TIMEOUT
    assert result.exchange.error_code == "REASONER_TIMEOUT"
    assert result.raw_response_bytes is None
    assert len(attempts) == 1

    def capacity_transport(endpoint: str, payload: dict[str, object]) -> bytes:
        # Metered-endpoint throttling must fail closed as a typed
        # PROVIDER_ERROR abstention, never a retry and never a guess.
        attempts.append(1)
        raise RuntimeError("429 rate limited")

    result = _adapter(capacity_transport)(_request())
    assert result.exchange.status is ExchangeStatus.PROVIDER_ERROR
    assert result.exchange.error_code == "REASONER_PROVIDER_ERROR"
    assert len(attempts) == 2  # exactly one call per invocation, never a retry

    for broken in (
        _envelope(content, reasoning="leaked thinking"),
        b"not json",
        json.dumps({"choices": []}).encode(),
    ):
        result = _adapter(lambda endpoint, payload, raw=broken: raw)(_request())
        assert result.exchange.status is ExchangeStatus.PROVIDER_ERROR
        assert result.raw_response_bytes is None


def test_unwrapper_honors_the_probe_verified_envelope_contract() -> None:
    content = b'{"decision":"UP"}'
    assert unwrap_openai_chat_envelope(_envelope(content)) == content
    assert unwrap_openai_chat_envelope(_envelope(content, reasoning="think")) is None
    assert unwrap_openai_chat_envelope(b"{}") is None


def test_transport_invokes_once_and_never_leaks_exception_text() -> None:
    provider_request = build_qwen_dashscope_request(load_approved_reasoner_route_v5(), _request())
    calls: list[str] = []

    def transport(endpoint: str, payload: dict[str, object]) -> bytes:
        calls.append(endpoint)
        return _envelope(b"{}")

    result = invoke_qwen_dashscope_transport(provider_request, transport)
    assert result.status is ExchangeStatus.COMPLETED
    assert calls == [f"{QWEN_DASHSCOPE_BASE_URL}/chat/completions"]

    def failing(endpoint: str, payload: dict[str, object]) -> bytes:
        raise RuntimeError("secret provider detail")

    result = invoke_qwen_dashscope_transport(provider_request, failing)
    assert result.status is ExchangeStatus.PROVIDER_ERROR
    assert result.raw_response_bytes is None


def test_adapter_discards_the_credential_and_enforces_identity() -> None:
    adapter = _adapter(lambda endpoint, payload: b"")
    for value in vars(adapter).values():
        assert not (isinstance(value, str) and FAKE_KEY in value)
    assert adapter.identity == DASHSCOPE_IDENTITY
    assert adapter.validated_route is load_approved_reasoner_route_v5()

    with pytest.raises(HostRouteSecretBoundaryError):
        QwenDashScopeReasonerRoute(
            route=load_approved_reasoner_route_v5(),
            api_key="  ",
            identity=DASHSCOPE_IDENTITY,
        )
    with pytest.raises(HostRouteConfigurationError, match="identity"):
        _adapter(lambda endpoint, payload: b"", identity=SYNTHETIC_ROUTE_IDENTITY)
    with pytest.raises(HostRouteConfigurationError, match="transport"):
        _adapter(None)(_request())


def test_environment_loader_reads_only_the_dashscope_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_QWEN_DASHSCOPE_API_KEY, raising=False)
    with pytest.raises(HostRouteConfigurationError, match=ENV_QWEN_DASHSCOPE_API_KEY):
        load_qwen_dashscope_route_environment()

    monkeypatch.setenv(ENV_QWEN_DASHSCOPE_API_KEY, f"  {FAKE_KEY}  ")
    assert load_qwen_dashscope_route_environment() == {ENV_QWEN_DASHSCOPE_API_KEY: FAKE_KEY}


# --- assembled engine integration (the headliner) ---------------------------


def test_assembled_engine_accepts_the_real_dashscope_adapter() -> None:
    joined = _joined_input()
    timeline = rehearsal_timeline(joined)
    direction = rehearsal_direction(joined)
    assert direction.value in ("UP", "DOWN")  # fixture is non-neutral by construction

    content = _decision_response_bytes()
    engine = BoundedDecisionEngine(
        _adapter(lambda endpoint, payload: _envelope(content)), identity=DASHSCOPE_IDENTITY
    )

    outcome = engine.decide(joined, started_at=timeline.started_at)

    decision = outcome.decision
    assert decision.direction is direction
    assert decision.reasoner_exchange_sha256 is not None
    assert outcome.exchange.status is ExchangeStatus.COMPLETED
    assert outcome.exchange.model_config_sha256 == DASHSCOPE_IDENTITY.model_config_sha256()
