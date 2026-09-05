"""Issue #91: the owner-approved direct MiniMax-M3 route (V3).

Covers the contract, builder, adapter, and the assembled-engine lane.

Every test is offline: the transport is a fake returning the owner-probe-verified
provider envelope shape. The credential is a labelled fake and is asserted to be
discarded at construction. The headliner is the engine integration: the real
adapter drives the assembled BoundedDecisionEngine to an ACCEPTED decision with
policy-registry exchange identities - the first direct-provider lane wired for
the assembled engine in this repository.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta

import pytest

from esscher.contracts.reasoner_route import (
    MINIMAX_DIRECT_BASE_URL,
    MINIMAX_DIRECT_MODEL,
    MINIMAX_DIRECT_PROVIDER,
    ApprovalState,
    RouteCompatibilityState,
    RouteContractReason,
    RouteContractRejected,
    load_approved_reasoner_route_v2,
    load_approved_reasoner_route_v3,
    load_approved_reasoner_route_v5,
    load_current_approved_reasoner_route,
    packaged_route_descriptor_v3_bytes,
    validate_reasoner_route_v3,
)
from esscher.runtime.host_composition import rehearsal_direction, rehearsal_timeline
from esscher.strategy.contracts import (
    reasoner_output_schema_sha256,
    reasoner_policy_hashes,
    reasoner_system_prompt_bytes,
)
from esscher.strategy.engine import BoundedDecisionEngine
from esscher.strategy.host_route import (
    ENV_MINIMAX_API_KEY,
    HostRouteConfigurationError,
    HostRouteSecretBoundaryError,
    MinimaxM3ReasonerRoute,
    build_minimax_m3_request,
    load_minimax_route_environment,
    unwrap_minimax_response,
)
from esscher.strategy.models import ExchangeStatus
from esscher.strategy.reasoner import (
    SYNTHETIC_ROUTE_IDENTITY,
    ReasonerRouteRequest,
    RouteIdentity,
)
from test_paper_mcp_composition import _decision_response_bytes, _joined_input

FAKE_KEY = "host-owned-test-key-not-a-real-credential"
MINIMAX_IDENTITY = RouteIdentity(provider=MINIMAX_DIRECT_PROVIDER, model=MINIMAX_DIRECT_MODEL)
CANDIDATE = "EARNINGS_RESIDUAL_CONTINUATION_V1"


def _envelope(content: bytes, *, status_code: int = 0, reasoning: str | None = None) -> bytes:
    message: dict[str, object] = {"content": content.decode("utf-8"), "role": "assistant"}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return json.dumps(
        {
            "base_resp": {"status_code": status_code, "status_msg": "success"},
            "choices": [{"finish_reason": "stop", "message": message}],
            "model": MINIMAX_DIRECT_MODEL,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _adapter(
    transport=None, *, identity: RouteIdentity = MINIMAX_IDENTITY
) -> MinimaxM3ReasonerRoute:
    return MinimaxM3ReasonerRoute(
        route=load_approved_reasoner_route_v3(),
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


def test_v3_package_is_approved_compatible_and_eligible() -> None:
    route = load_approved_reasoner_route_v3()

    assert route.route_id == "ESSCHER_BOUNDED_REASONER_ROUTE_V3"
    assert route.provider == MINIMAX_DIRECT_PROVIDER
    assert route.model == MINIMAX_DIRECT_MODEL
    assert route.base_url == MINIMAX_DIRECT_BASE_URL
    assert route.approval_state is ApprovalState.APPROVED
    assert route.approver == "MS-Mesh"
    assert route.approved_at is not None
    assert route.compatibility_state is RouteCompatibilityState.COMPATIBLE
    assert route.evaluation_eligible is True
    assert route.provider_request_policy.response_format_type == "json_object"
    assert route.provider_request_policy.reasoning_effort == "disabled"
    assert route.provider_request_policy.output_schema_sha256 == reasoner_output_schema_sha256()


def test_v3_minimax_package_is_a_dormant_alternate() -> None:
    # The current route pivoted to V5 (qwen3.8-max-0902 via official DashScope)
    # after the V4 free-gateway route hit disclosed evening concurrency caps;
    # V3 remains packaged, loadable, and unchanged as a dormant alternate.
    assert load_current_approved_reasoner_route() is load_approved_reasoner_route_v5()
    v3 = load_approved_reasoner_route_v3()
    assert v3 is not load_current_approved_reasoner_route()
    assert v3.provider == "minimax_direct"
    kimi = load_approved_reasoner_route_v2()
    assert kimi.provider == "moonshot_direct"
    assert kimi is not load_current_approved_reasoner_route()


def test_v3_validation_rejects_lookalike_bytes() -> None:
    descriptor = packaged_route_descriptor_v3_bytes()
    tampered = descriptor.replace(b"MiniMax-M3", b"MiniMax-M2")

    with pytest.raises(RouteContractRejected) as drift:
        validate_reasoner_route_v3(tampered, tampered)
    assert drift.value.reason is RouteContractReason.HASH_MISMATCH

    with pytest.raises(RouteContractRejected):
        validate_reasoner_route_v3(descriptor, descriptor)


# --- builder ----------------------------------------------------------------


def test_builder_pins_the_probe_verified_wire_shape() -> None:
    provider_request = build_minimax_m3_request(load_approved_reasoner_route_v3(), _request())

    assert provider_request.endpoint == "https://api.minimax.chat/v1/chat/completions"
    payload = provider_request.payload
    assert payload["model"] == MINIMAX_DIRECT_MODEL
    assert payload["max_tokens"] == 512
    assert payload["temperature"] == 0
    assert payload["top_p"] == 1.0
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["tool_choice"] == "none"
    assert payload["response_format"] == {"type": "json_object"}
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
    ):
        assert omitted not in payload
    # Deterministic request identity; no secret material anywhere.
    again = build_minimax_m3_request(load_approved_reasoner_route_v3(), _request())
    assert again.request_sha256 == provider_request.request_sha256
    assert again.payload_bytes == provider_request.payload_bytes
    serialized = json.dumps(payload, sort_keys=True)
    assert FAKE_KEY not in serialized
    assert "sk-" not in serialized


def test_builder_rejects_forged_and_ablation_requests() -> None:
    real = load_approved_reasoner_route_v3()
    forged = dataclasses.replace(real)

    with pytest.raises(HostRouteConfigurationError, match="exact packaged V3"):
        build_minimax_m3_request(forged, _request())

    with pytest.raises(HostRouteConfigurationError, match="ablation"):
        build_minimax_m3_request(real, _request(ablate=True))


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
    assert exchange.model_config_sha256 == MINIMAX_IDENTITY.model_config_sha256()
    assert exchange.provider == MINIMAX_DIRECT_PROVIDER
    assert exchange.model == MINIMAX_DIRECT_MODEL
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

    def overload_transport(endpoint: str, payload: dict[str, object]) -> bytes:
        attempts.append(1)
        raise RuntimeError("529 overloaded")

    result = _adapter(overload_transport)(_request())
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
    assert unwrap_minimax_response(_envelope(content)) == content
    assert unwrap_minimax_response(_envelope(content, reasoning="think")) is None
    assert unwrap_minimax_response(_envelope(content, status_code=1004)) is None
    assert unwrap_minimax_response(b"{}") is None


def test_adapter_discards_the_credential_and_enforces_identity() -> None:
    adapter = _adapter(lambda endpoint, payload: b"")
    for value in vars(adapter).values():
        assert not (isinstance(value, str) and FAKE_KEY in value)
    assert adapter.identity == MINIMAX_IDENTITY
    assert adapter.validated_route is load_approved_reasoner_route_v3()

    with pytest.raises(HostRouteSecretBoundaryError):
        MinimaxM3ReasonerRoute(
            route=load_approved_reasoner_route_v3(),
            api_key="  ",
            identity=MINIMAX_IDENTITY,
        )
    with pytest.raises(HostRouteConfigurationError, match="identity"):
        _adapter(lambda endpoint, payload: b"", identity=SYNTHETIC_ROUTE_IDENTITY)
    with pytest.raises(HostRouteConfigurationError, match="transport"):
        _adapter(None)(_request())


def test_environment_loader_reads_only_the_minimax_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_MINIMAX_API_KEY, raising=False)
    with pytest.raises(HostRouteConfigurationError, match=ENV_MINIMAX_API_KEY):
        load_minimax_route_environment()

    monkeypatch.setenv(ENV_MINIMAX_API_KEY, f"  {FAKE_KEY}  ")
    assert load_minimax_route_environment() == {ENV_MINIMAX_API_KEY: FAKE_KEY}


# --- assembled engine integration (the headliner) ---------------------------


def test_assembled_engine_accepts_the_real_minimax_adapter() -> None:
    joined = _joined_input()
    timeline = rehearsal_timeline(joined)
    direction = rehearsal_direction(joined)
    assert direction.value in ("UP", "DOWN")  # fixture is non-neutral by construction

    content = _decision_response_bytes()
    engine = BoundedDecisionEngine(
        _adapter(lambda endpoint, payload: _envelope(content)), identity=MINIMAX_IDENTITY
    )

    outcome = engine.decide(joined, started_at=timeline.started_at)

    decision = outcome.decision
    assert decision.direction is direction
    assert decision.reasoner_exchange_sha256 is not None
    assert outcome.exchange.status is ExchangeStatus.COMPLETED
    assert outcome.exchange.model_config_sha256 == MINIMAX_IDENTITY.model_config_sha256()

    # The bounded engine keeps its duplicate-call fence with the real adapter.
    replay = engine.decide(joined, started_at=timeline.started_at + timedelta(seconds=1))
    assert replay.exchange.status is ExchangeStatus.CANCELED
    assert replay.exchange.error_code == "DUPLICATE_REASONER_CALL"
