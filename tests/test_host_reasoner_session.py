from __future__ import annotations

import socket
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ringdown_market.strategy.host_route import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    HostRouteConfigurationError,
    HostRouteNotApproved,
    HostRouteSecretBoundaryError,
    OpenAiCompatibleReasonerRoute,
    load_route_environment,
)
from ringdown_market.strategy.models import ExchangeStatus
from ringdown_market.strategy.policy import strategy_policy_sha256
from ringdown_market.strategy.reasoner import (
    ReasonerRouteRequest,
    RouteIdentity,
)

IDENTITY = RouteIdentity(provider="dashscope", model="kimi-k3")


def _strategy_input() -> SimpleNamespace:
    snapshot = SimpleNamespace(
        candidate_id="EARNINGS_RESIDUAL_CONTINUATION_V1",
        event_id="TEST-2026Q1-EARNINGS",
        policy_sha256=strategy_policy_sha256(),
        snapshot_sha256="1" * 64,
        evidence_packet_sha256="2" * 64,
        decision_cutoff_at=datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
    )
    return SimpleNamespace(
        snapshot=snapshot,
        snapshot_sha256="1" * 64,
        feature_receipt_sha256="3" * 64,
    )


def test_load_route_environment_fails_closed_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (ENV_API_KEY, ENV_BASE_URL, ENV_MODEL):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(HostRouteConfigurationError):
        load_route_environment()


def test_load_route_environment_rejects_non_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_API_KEY, "host-owned")
    monkeypatch.setenv(ENV_BASE_URL, "http://insecure.example")
    monkeypatch.setenv(ENV_MODEL, "kimi-k3")

    with pytest.raises(HostRouteConfigurationError):
        load_route_environment()


def test_route_refuses_construction_when_not_approved() -> None:
    with pytest.raises(HostRouteNotApproved):
        OpenAiCompatibleReasonerRoute(
            identity=IDENTITY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="host-owned",
            evaluation_eligible=False,
        )


def test_route_refuses_empty_credential() -> None:
    with pytest.raises(HostRouteSecretBoundaryError):
        OpenAiCompatibleReasonerRoute(
            identity=IDENTITY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="  ",
            evaluation_eligible=True,
        )


def test_route_with_fake_transport_records_completed_exchange() -> None:
    calls: list[dict] = []

    def transport(payload: dict) -> bytes:
        calls.append(payload)
        return b'{"decision":"UP"}'

    route = OpenAiCompatibleReasonerRoute(
        identity=IDENTITY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="host-owned",
        evaluation_eligible=True,
        transport=transport,
    )
    request = ReasonerRouteRequest(
        strategy_input=_strategy_input(),
        started_at=datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
    )

    result = route(request)

    assert len(calls) == 1
    assert calls[0]["model"] == "kimi-k3"
    assert result.exchange.status is ExchangeStatus.COMPLETED
    assert result.exchange.provider == "dashscope"
    assert result.exchange.model == "kimi-k3"
    assert result.raw_response_bytes == b'{"decision":"UP"}'


def test_route_makes_no_network_calls_with_injected_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _deny(*args: object, **kwargs: object) -> object:
        raise AssertionError("route must not touch the network")

    monkeypatch.setattr(socket, "socket", _deny)
    monkeypatch.setattr(socket, "create_connection", _deny)
    monkeypatch.setattr(socket, "getaddrinfo", _deny)

    route = OpenAiCompatibleReasonerRoute(
        identity=IDENTITY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="host-owned",
        evaluation_eligible=True,
        transport=lambda payload: b'{"decision":"DOWN"}',
    )
    request = ReasonerRouteRequest(
        strategy_input=_strategy_input(),
        started_at=datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
    )

    result = route(request)

    assert result.exchange.status is ExchangeStatus.COMPLETED
