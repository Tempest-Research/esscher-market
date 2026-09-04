"""Issue #91: the owner-approved deepseek-v4-flash-0731-free gateway route (V4, current).

Covers the contract, builder, adapter, and the assembled-engine lane for the
furry.vg OpenAI-compatible gateway pivot (MiniMax-M3 measured above the frozen
8s one-call budget; the gateway measured 1.0-1.8s accepted latency).

Every test is offline: the transport is a fake returning the owner-probe-verified
gateway envelope shape. The credential is a labelled fake and is asserted to be
discarded at construction. The headliner is the engine integration: the real
adapter drives the assembled BoundedDecisionEngine to an ACCEPTED decision with
policy-registry exchange identities.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from ringdown_market.contracts.reasoner_route import (
    FURRY_GATEWAY_BASE_URL,
    FURRY_GATEWAY_MAX_COMPLETION_TOKENS,
    FURRY_GATEWAY_MODEL,
    FURRY_GATEWAY_PROVIDER,
    ApprovalState,
    RouteCompatibilityState,
    RouteContractReason,
    RouteContractRejected,
    load_approved_reasoner_route_v3,
    load_approved_reasoner_route_v4,
    load_current_approved_reasoner_route,
    packaged_route_descriptor_v4_bytes,
    validate_reasoner_route_v4,
)
from ringdown_market.runtime.host_composition import rehearsal_direction, rehearsal_timeline
from ringdown_market.strategy.contracts import (
    reasoner_output_schema_sha256,
    reasoner_policy_hashes,
    reasoner_system_prompt_bytes,
)
from ringdown_market.strategy.engine import BoundedDecisionEngine
from ringdown_market.strategy.host_route import (
    ENV_FURRY_API_KEY,
    FurryGatewayReasonerRoute,
    HostRouteConfigurationError,
    HostRouteSecretBoundaryError,
    build_furry_gateway_request,
    invoke_furry_gateway_transport,
    load_furry_route_environment,
    unwrap_openai_chat_envelope,
)
from ringdown_market.strategy.models import ExchangeStatus
from ringdown_market.strategy.reasoner import (
    SYNTHETIC_ROUTE_IDENTITY,
    ReasonerRouteRequest,
    RouteIdentity,
)
from test_paper_mcp_composition import _decision_response_bytes, _joined_input

FAKE_KEY = "host-owned-test-key-not-a-real-credential"
GATEWAY_IDENTITY = RouteIdentity(provider=FURRY_GATEWAY_PROVIDER, model=FURRY_GATEWAY_MODEL)
CANDIDATE = "EARNINGS_RESIDUAL_CONTINUATION_V1"


def _envelope(content: bytes, *, status_code: int = 0, reasoning: str | None = None) -> bytes:
    message: dict[str, object] = {"content": content.decode("utf-8"), "role": "assistant"}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    envelope: dict[str, object] = {
        "choices": [{"finish_reason": "stop", "message": message}],
        "model": FURRY_GATEWAY_MODEL,
    }
    if status_code != 0:
        envelope["base_resp"] = {"status_code": status_code, "status_msg": "error"}
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _adapter(
    transport=None, *, identity: RouteIdentity = GATEWAY_IDENTITY
) -> FurryGatewayReasonerRoute:
    return FurryGatewayReasonerRoute(
        route=load_approved_reasoner_route_v4(),
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


def test_v4_package_is_approved_compatible_and_eligible() -> None:
    route = load_approved_reasoner_route_v4()

    assert route.route_id == "ESSCHER_BOUNDED_REASONER_ROUTE_V4"
    assert route.provider == FURRY_GATEWAY_PROVIDER
    assert route.model == FURRY_GATEWAY_MODEL
    assert route.model_revision is None
    assert route.base_url == FURRY_GATEWAY_BASE_URL
    assert route.approval_state is ApprovalState.APPROVED
    assert route.approver == "MS-Mesh"
    assert route.approved_at is not None
    assert route.compatibility_state is RouteCompatibilityState.COMPATIBLE
    assert route.evaluation_eligible is True
    assert route.provider_request_policy.response_format_type == "json_object"
    assert route.provider_request_policy.reasoning_effort == "none"
    assert route.provider_request_policy.max_completion_tokens == 1024
    assert route.provider_request_policy.strict_json_schema is False
    assert route.provider_request_policy.output_schema_sha256 == reasoner_output_schema_sha256()


def test_current_approved_route_is_the_v4_furry_gateway_package() -> None:
    assert load_current_approved_reasoner_route() is load_approved_reasoner_route_v4()
    # V3 MiniMax remains packaged and loadable as a dormant alternate.
    assert load_approved_reasoner_route_v3() is not load_current_approved_reasoner_route()


def test_v4_validation_rejects_lookalike_bytes() -> None:
    descriptor = packaged_route_descriptor_v4_bytes()
    tampered = descriptor.replace(b"deepseek-v4-flash-0731-free", b"deepseek-v4-flash-0730-free")

    with pytest.raises(RouteContractRejected) as drift:
        validate_reasoner_route_v4(tampered, tampered)
    assert drift.value.reason is RouteContractReason.HASH_MISMATCH

    with pytest.raises(RouteContractRejected):
        validate_reasoner_route_v4(descriptor, descriptor)


# --- builder ----------------------------------------------------------------


def test_builder_pins_the_probe_verified_wire_shape() -> None:
    provider_request = build_furry_gateway_request(load_approved_reasoner_route_v4(), _request())

    assert provider_request.endpoint == f"{FURRY_GATEWAY_BASE_URL}/chat/completions"
    payload = provider_request.payload
    assert payload["model"] == FURRY_GATEWAY_MODEL
    assert payload["max_tokens"] == FURRY_GATEWAY_MAX_COMPLETION_TOKENS == 1024
    assert payload["temperature"] == 0
    assert payload["top_p"] == 1.0
    assert payload["tool_choice"] == "none"
    assert payload["response_format"] == {"type": "json_object"}
    # The gateway omits thinking controls entirely (json_schema hangs it;
    # thinking is not a pinned field on this wire).
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
    ):
        assert omitted not in payload
    # Deterministic request identity; no secret material anywhere.
    again = build_furry_gateway_request(load_approved_reasoner_route_v4(), _request())
    assert again.request_sha256 == provider_request.request_sha256
    assert again.payload_bytes == provider_request.payload_bytes
    serialized = json.dumps(payload, sort_keys=True)
    assert FAKE_KEY not in serialized
    assert "sk-" not in serialized


def test_builder_rejects_forged_and_ablation_requests() -> None:
    real = load_approved_reasoner_route_v4()
    forged = dataclasses.replace(real)

    with pytest.raises(HostRouteConfigurationError, match="exact packaged V4"):
        build_furry_gateway_request(forged, _request())

    with pytest.raises(HostRouteConfigurationError, match="ablation"):
        build_furry_gateway_request(real, _request(ablate=True))


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
    assert exchange.model_config_sha256 == GATEWAY_IDENTITY.model_config_sha256()
    assert exchange.provider == FURRY_GATEWAY_PROVIDER
    assert exchange.model == FURRY_GATEWAY_MODEL
    assert exchange.responded_at is not None
    assert exchange.responded_at <= exchange.deadline_at
    assert len(calls) == 1


def test_adapter_maps_failures_to_typed_exchanges_without_retry() -> None:
    content = _decision_response_bytes()
    attempts: list[int] = []

    def timeout_transport(endpoint: str, payload: dict[str, object]) -> bytes:
        attempts.append(1)
        raise TimeoutError("gateway transport detail must never leak")

    result = _adapter(timeout_transport)(_request())
    assert result.exchange.status is ExchangeStatus.TIMEOUT
    assert result.exchange.error_code == "REASONER_TIMEOUT"
    assert result.raw_response_bytes is None
    assert len(attempts) == 1

    def capacity_transport(endpoint: str, payload: dict[str, object]) -> bytes:
        # The free gateway is capacity-limited: 429/30s stalls must fail closed
        # as a typed PROVIDER_ERROR abstention, never a retry and never a guess.
        attempts.append(1)
        raise RuntimeError("429 rate limited")

    result = _adapter(capacity_transport)(_request())
    assert result.exchange.status is ExchangeStatus.PROVIDER_ERROR
    assert result.exchange.error_code == "REASONER_PROVIDER_ERROR"
    assert len(attempts) == 2  # exactly one call per invocation, never a retry

    for broken in (
        _envelope(content, status_code=2013),
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
    assert unwrap_openai_chat_envelope(_envelope(content, status_code=1004)) is None
    assert unwrap_openai_chat_envelope(b"{}") is None


def test_transport_invokes_once_and_never_leaks_exception_text() -> None:
    provider_request = build_furry_gateway_request(load_approved_reasoner_route_v4(), _request())
    calls: list[str] = []

    def transport(endpoint: str, payload: dict[str, object]) -> bytes:
        calls.append(endpoint)
        return _envelope(b"{}")

    result = invoke_furry_gateway_transport(provider_request, transport)
    assert result.status is ExchangeStatus.COMPLETED
    assert calls == [f"{FURRY_GATEWAY_BASE_URL}/chat/completions"]

    def failing(endpoint: str, payload: dict[str, object]) -> bytes:
        raise RuntimeError("secret provider detail")

    result = invoke_furry_gateway_transport(provider_request, failing)
    assert result.status is ExchangeStatus.PROVIDER_ERROR
    assert result.raw_response_bytes is None


def test_adapter_discards_the_credential_and_enforces_identity() -> None:
    adapter = _adapter(lambda endpoint, payload: b"")
    for value in vars(adapter).values():
        assert not (isinstance(value, str) and FAKE_KEY in value)
    assert adapter.identity == GATEWAY_IDENTITY
    assert adapter.validated_route is load_approved_reasoner_route_v4()

    with pytest.raises(HostRouteSecretBoundaryError):
        FurryGatewayReasonerRoute(
            route=load_approved_reasoner_route_v4(),
            api_key="  ",
            identity=GATEWAY_IDENTITY,
        )
    with pytest.raises(HostRouteConfigurationError, match="identity"):
        _adapter(lambda endpoint, payload: b"", identity=SYNTHETIC_ROUTE_IDENTITY)
    with pytest.raises(HostRouteConfigurationError, match="transport"):
        _adapter(None)(_request())


def test_environment_loader_reads_only_the_gateway_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_FURRY_API_KEY, raising=False)
    with pytest.raises(HostRouteConfigurationError, match=ENV_FURRY_API_KEY):
        load_furry_route_environment()

    monkeypatch.setenv(ENV_FURRY_API_KEY, f"  {FAKE_KEY}  ")
    assert load_furry_route_environment() == {ENV_FURRY_API_KEY: FAKE_KEY}


# --- assembled engine integration (the headliner) ---------------------------


def test_assembled_engine_accepts_the_real_gateway_adapter() -> None:
    joined = _joined_input()
    timeline = rehearsal_timeline(joined)
    direction = rehearsal_direction(joined)
    assert direction.value in ("UP", "DOWN")  # fixture is non-neutral by construction

    content = _decision_response_bytes()
    engine = BoundedDecisionEngine(
        _adapter(lambda endpoint, payload: _envelope(content)), identity=GATEWAY_IDENTITY
    )

    outcome = engine.decide(joined, started_at=timeline.started_at)

    decision = outcome.decision
    assert decision.direction is direction
    assert decision.reasoner_exchange_sha256 is not None
    assert outcome.exchange.status is ExchangeStatus.COMPLETED
    assert outcome.exchange.model_config_sha256 == GATEWAY_IDENTITY.model_config_sha256()
