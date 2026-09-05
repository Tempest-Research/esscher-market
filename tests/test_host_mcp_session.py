from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import copy, deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

import esscher.execution.host_mcp as host_mcp
from esscher.contracts.execution_policy import (
    ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256,
    ALPACA_MCP_READONLY_EXTENSION_COUNT,
    ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256,
    ALPACA_MCP_V2_PROTOCOL_SHA256,
)
from esscher.execution.host_mcp import (
    HostMcpAccountError,
    HostMcpConfigurationError,
    HostMcpEnvironment,
    HostMcpMutationAmbiguous,
    HostMcpPaperSessionFactory,
    HostMcpSecretBoundaryError,
    HostMcpSessionIdentity,
    HostMcpUnavailable,
    PreparedHostMcpSession,
    _GuardedHostMcpSession,
)
from esscher.execution.lifecycle_mcp import LifecycleMcpPaperBroker
from esscher.execution.mcp import (
    ALPACA_MCP_VERSION,
    CANCEL_TOOL,
    OPEN_TOOL,
    ORDER_BY_ID_TOOL,
    POSITIONS_TOOL,
    READBACK_TOOL,
)

NOW = datetime(2026, 8, 29, 17, 0, tzinfo=UTC)
REQUIRED_TOOLS = {
    "get_account_info",
    OPEN_TOOL,
    READBACK_TOOL,
    ORDER_BY_ID_TOOL,
    CANCEL_TOOL,
    POSITIONS_TOOL,
    # Issue #90 read-only operational extension over the identical pinned artifact.
    "get_account_activities",
    "get_orders",
}
READONLY_EXTENSION_TOOLS = {"get_account_activities", "get_orders"}
MUTATING_TOOLS = {OPEN_TOOL, CANCEL_TOOL}


_DEFAULT_ACCOUNT = object()
_MODULE_API_MISSING = object()


class FakeHostSession:
    def __init__(
        self,
        *,
        tools: object = tuple(sorted(REQUIRED_TOOLS)),
        account: object = _DEFAULT_ACCOUNT,
        failures: Mapping[str, Exception] | None = None,
    ) -> None:
        self.tools = tools
        self.account = (
            {
                "id": "sensitive-account-id",
                "account_number": "sensitive-account-number",
                "status": "ACTIVE",
                "trading_blocked": False,
                "account_blocked": False,
            }
            if account is _DEFAULT_ACCOUNT
            else account
        )
        self.failures = dict(failures or {})
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.list_calls = 0

    async def list_tools(self) -> object:
        self.list_calls += 1
        failure = self.failures.get("list_tools")
        if failure is not None:
            raise failure
        return self.tools

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        self.calls.append((name, dict(arguments)))
        failure = self.failures.get(name)
        if failure is not None:
            raise failure
        if name == "get_account_info":
            return self.account
        return {"ok": True}


def paper_identity() -> HostMcpSessionIdentity:
    return HostMcpSessionIdentity(environment=HostMcpEnvironment.PAPER)


def connect(
    host: FakeHostSession,
    *,
    identity: HostMcpSessionIdentity | None = None,
):
    factory = HostMcpPaperSessionFactory(identity or paper_identity(), clock=lambda: NOW)
    return asyncio.run(factory.connect(host))


def test_session_identity_is_fixed_to_the_pinned_official_adapter() -> None:
    identity = paper_identity()

    assert identity.environment is HostMcpEnvironment.PAPER
    assert identity.adapter == "ALPACA_MCP"
    assert identity.adapter_version == ALPACA_MCP_VERSION
    assert identity.provenance_class == "PYPI_RELEASE_NO_GIT_TAG"
    assert identity.execution_protocol_sha256 == ALPACA_MCP_V2_PROTOCOL_SHA256
    assert set(identity.__dataclass_fields__) == {
        "environment",
        "adapter",
        "adapter_version",
        "distribution_type",
        "wheel_filename",
        "wheel_sha256",
        "sdist_filename",
        "sdist_sha256",
        "provenance_class",
        "source_equivalent_version",
        "source_equivalent_commit",
        "fastmcp_version",
        "fastmcp_spec",
        "discovered_tool_count",
        "selected_schema_count",
        "selected_schema_sha256",
        "tool_names",
        "execution_protocol_sha256",
        "readonly_extension_count",
        "readonly_extension_schema_sha256",
        "readonly_extension_tool_names",
        "host_operations_protocol_sha256",
    }


def test_non_paper_identity_fails_before_any_host_call() -> None:
    host = FakeHostSession()

    with pytest.raises(HostMcpConfigurationError, match="paper environment"):
        identity = HostMcpSessionIdentity(environment="LIVE")  # type: ignore[arg-type]
        connect(host, identity=identity)

    assert host.list_calls == 0
    assert host.calls == []


def test_missing_required_tool_fails_startup_without_account_or_mutation() -> None:
    host = FakeHostSession(tools=tuple(sorted(REQUIRED_TOOLS - {READBACK_TOOL})))

    with pytest.raises(HostMcpConfigurationError, match=READBACK_TOOL):
        connect(host)

    assert host.list_calls == 1
    assert host.calls == []


def test_capability_probe_accepts_tool_descriptors_and_ignores_extra_tools() -> None:
    tools = [{"name": name} for name in sorted(REQUIRED_TOOLS | {"close_all_positions"})]
    prepared = connect(FakeHostSession(tools=tools))

    assert prepared.observation.required_tool_count == len(REQUIRED_TOOLS)
    assert len(prepared.observation.capability_sha256) == 64


def test_malformed_tool_listing_is_redacted() -> None:
    host = FakeHostSession(tools=[{"name": "get_account_info"}, {"secret": "do-not-emit"}])

    with pytest.raises(HostMcpConfigurationError) as captured:
        connect(host)

    assert "do-not-emit" not in str(captured.value)
    assert host.calls == []


def test_account_preflight_emits_only_sanitized_observation() -> None:
    prepared = connect(FakeHostSession())
    observation = prepared.observation

    assert observation.account_status == "ACTIVE"
    assert observation.account_blocked is False
    assert observation.trading_blocked is False
    assert observation.observed_at == NOW
    assert "account" not in observation.__dataclass_fields__
    assert "sensitive-account" not in repr(observation)


def test_prepared_session_is_factory_only_and_does_not_expose_raw_session() -> None:
    prepared = connect(FakeHostSession())

    assert not hasattr(prepared, "session")
    with pytest.raises(TypeError, match="factory-created"):
        PreparedHostMcpSession()  # type: ignore[call-arg]


def test_lifecycle_broker_cannot_treat_a_raw_host_as_a_factory_capability() -> None:
    raw_host = FakeHostSession()

    with pytest.raises((TypeError, HostMcpConfigurationError), match="factory-created"):
        broker = LifecycleMcpPaperBroker(raw_host)
        asyncio.run(broker.read_account())

    assert raw_host.calls == []


def test_module_api_cannot_mint_a_capability_for_a_raw_host_session() -> None:
    trusted = connect(FakeHostSession())
    raw_host = FakeHostSession()
    mint = getattr(PreparedHostMcpSession, "_from_preflight", _MODULE_API_MISSING)
    token = getattr(host_mcp, "_PREPARED_HOST_MCP_FACTORY_CAPABILITY", _MODULE_API_MISSING)
    wire = getattr(host_mcp, "_wire_prepared_host_mcp_capability", _MODULE_API_MISSING)

    if callable(mint) and token is not _MODULE_API_MISSING:
        forged = mint(
            session=raw_host,
            observation=replace(trusted.observation),
            factory_capability=token,
        )
        assert asyncio.run(forged.read_order("order-1")) == {"ok": True}

    assert raw_host.calls == []
    assert mint is _MODULE_API_MISSING
    assert token is _MODULE_API_MISSING
    assert wire is _MODULE_API_MISSING


def test_module_api_cannot_inject_a_raw_host_session_into_prepared_state() -> None:
    trusted = connect(FakeHostSession())
    raw_host = FakeHostSession()
    forged = object.__new__(PreparedHostMcpSession)
    state_type = getattr(host_mcp, "_PreparedHostMcpState", _MODULE_API_MISSING)
    states = getattr(host_mcp, "_PREPARED_HOST_MCP_STATES", _MODULE_API_MISSING)

    if state_type is not _MODULE_API_MISSING and states is not _MODULE_API_MISSING:
        states[forged] = state_type(
            session=raw_host,
            observation=replace(trusted.observation),
        )
        assert asyncio.run(forged.read_order("order-1")) == {"ok": True}
    else:
        with pytest.raises(HostMcpConfigurationError, match="factory-created"):
            asyncio.run(forged.read_order("order-1"))

    assert raw_host.calls == []
    assert state_type is _MODULE_API_MISSING
    assert states is _MODULE_API_MISSING


def test_object_new_prepared_session_is_not_a_registered_capability() -> None:
    raw_host = FakeHostSession()
    forged = object.__new__(PreparedHostMcpSession)

    with pytest.raises(HostMcpConfigurationError, match="factory-created"):
        asyncio.run(forged.read_order("order-1"))

    assert raw_host.calls == []


def test_factory_connect_preflights_and_keeps_its_runtime_session_guarded() -> None:
    host = FakeHostSession()
    prepared = connect(host)

    assert host.list_calls == 1
    assert host.calls == [("get_account_info", {})]
    host.calls.clear()

    with pytest.raises(HostMcpConfigurationError, match="not allowed"):
        asyncio.run(prepared._validated_state().session.call_tool("close_all_positions", {}))

    assert host.calls == []
    assert asyncio.run(prepared.read_order("order-1")) == {"ok": True}
    assert host.calls == [(ORDER_BY_ID_TOOL, {"order_id": "order-1"})]


@pytest.mark.parametrize("copier", (copy, deepcopy))
def test_prepared_session_cannot_be_copied(copier) -> None:
    prepared = connect(FakeHostSession())

    with pytest.raises(TypeError, match="factory-created"):
        copier(prepared)


@pytest.mark.parametrize("use_guarded_session", (False, True))
def test_prepared_session_rejects_raw_or_different_guarded_session_with_copied_observation(
    use_guarded_session: bool,
) -> None:
    trusted_host = FakeHostSession()
    prepared = connect(trusted_host)
    trusted_host.calls.clear()
    different_host = FakeHostSession()
    if use_guarded_session:
        supplied_session = _GuardedHostMcpSession(different_host)
    else:
        supplied_session = different_host

    with pytest.raises(TypeError, match="factory-created"):
        PreparedHostMcpSession(
            session=supplied_session,
            observation=replace(prepared.observation),
        )  # type: ignore[call-arg]

    assert trusted_host.calls == []
    assert different_host.calls == []


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("capability_sha256", "0" * 64),
        ("required_tool_count", 0),
        ("account_status", "SUSPENDED"),
        ("trading_blocked", True),
        ("account_blocked", True),
        ("environment", "PAPER"),
        ("adapter", "UNPINNED_ADAPTER"),
        ("adapter_version", "untrusted"),
        ("distribution_type", "untrusted"),
        ("wheel_filename", "untrusted"),
        ("wheel_sha256", "0" * 64),
        ("sdist_filename", "untrusted"),
        ("sdist_sha256", "0" * 64),
        ("provenance_class", "untrusted"),
        ("source_equivalent_version", "untrusted"),
        ("source_equivalent_commit", "0" * 40),
        ("fastmcp_version", "untrusted"),
        ("fastmcp_spec", "untrusted"),
        ("discovered_tool_count", 0),
        ("selected_schema_count", 0),
        ("selected_schema_sha256", "0" * 64),
        ("tool_names", ()),
        ("execution_protocol_sha256", "0" * 64),
        ("readonly_extension_count", 0),
        ("readonly_extension_schema_sha256", "0" * 64),
        ("readonly_extension_tool_names", ()),
        ("host_operations_protocol_sha256", "0" * 64),
        ("observed_at", NOW.replace(tzinfo=None)),
    ),
)
def test_module_api_cannot_inject_tampered_prepared_attestation(
    field_name: str,
    value: object,
) -> None:
    trusted = connect(FakeHostSession())
    raw_host = FakeHostSession()
    forged = object.__new__(PreparedHostMcpSession)
    state_type = getattr(host_mcp, "_PreparedHostMcpState", _MODULE_API_MISSING)
    states = getattr(host_mcp, "_PREPARED_HOST_MCP_STATES", _MODULE_API_MISSING)

    if state_type is not _MODULE_API_MISSING and states is not _MODULE_API_MISSING:
        states[forged] = state_type(
            session=raw_host,
            observation=replace(trusted.observation, **{field_name: value}),
        )

    with pytest.raises(HostMcpConfigurationError, match=r"factory-created|attestation"):
        asyncio.run(forged.read_order("order-1"))

    assert raw_host.calls == []
    assert state_type is _MODULE_API_MISSING
    assert states is _MODULE_API_MISSING


def test_blocked_or_inactive_account_fails_before_runtime_calls() -> None:
    host = FakeHostSession(
        account={
            "status": "ACTIVE",
            "trading_blocked": True,
            "account_blocked": False,
            "account_number": "must-not-leak",
        }
    )

    with pytest.raises(HostMcpAccountError, match="not eligible") as captured:
        connect(host)

    assert "must-not-leak" not in str(captured.value)
    assert [name for name, _ in host.calls] == ["get_account_info"]


@pytest.mark.parametrize(
    "account",
    [
        [],
        {"status": "ACTIVE", "trading_blocked": "false", "account_blocked": False},
        {"status": "", "trading_blocked": False, "account_blocked": False},
    ],
)
def test_malformed_account_response_fails_closed(account: object) -> None:
    host = FakeHostSession(account=account)

    with pytest.raises(HostMcpAccountError, match="malformed"):
        connect(host)

    assert [name for name, _ in host.calls] == ["get_account_info"]


def test_preflight_timeout_is_typed_and_redacted() -> None:
    host = FakeHostSession(failures={"list_tools": TimeoutError("token=do-not-emit")})

    with pytest.raises(HostMcpUnavailable, match="capability listing timed out") as captured:
        connect(host)

    assert "do-not-emit" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert host.calls == []


def test_guarded_session_enforces_the_runtime_tool_allowlist() -> None:
    host = FakeHostSession()
    guarded = _GuardedHostMcpSession(host)

    with pytest.raises(HostMcpConfigurationError, match="not allowed"):
        asyncio.run(guarded.call_tool("close_all_positions", {}))

    assert host.calls == []


def test_secret_like_runtime_arguments_are_rejected_before_host_call() -> None:
    host = FakeHostSession()
    guarded = _GuardedHostMcpSession(host)

    with pytest.raises(HostMcpSecretBoundaryError, match="secret-like"):
        asyncio.run(
            guarded.call_tool(
                OPEN_TOOL,
                {"client_order_id": "rd-open-safe", "metadata": {"api_key": "do-not-emit"}},
            )
        )

    assert host.calls == []


def test_mutation_timeout_is_typed_as_ambiguous_without_leaking_details() -> None:
    host = FakeHostSession(failures={OPEN_TOOL: TimeoutError("secret broker detail")})
    guarded = _GuardedHostMcpSession(host)

    with pytest.raises(HostMcpMutationAmbiguous, match="read back") as captured:
        asyncio.run(
            guarded.call_tool(
                OPEN_TOOL,
                {"client_order_id": "rd-open-safe"},
            )
        )

    assert "secret broker detail" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert [name for name, _ in host.calls] == [OPEN_TOOL]


def test_read_only_runtime_timeout_is_unavailable_not_ambiguous() -> None:
    host = FakeHostSession(failures={READBACK_TOOL: TimeoutError("private transport detail")})
    guarded = _GuardedHostMcpSession(host)

    with pytest.raises(HostMcpUnavailable, match="timed out") as captured:
        asyncio.run(
            guarded.call_tool(
                READBACK_TOOL,
                {"client_order_id": "rd-open-safe"},
            )
        )

    assert "private transport detail" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_read_only_capability_smoke_never_calls_a_mutating_tool() -> None:
    host = FakeHostSession()
    factory = HostMcpPaperSessionFactory(paper_identity(), clock=lambda: NOW)

    observation = asyncio.run(factory.smoke(host))

    assert observation.account_status == "ACTIVE"
    assert [name for name, _ in host.calls] == ["get_account_info"]
    assert not ({name for name, _ in host.calls} & {OPEN_TOOL, CANCEL_TOOL})


def test_runtime_result_is_forwarded_without_copying_sensitive_account_data() -> None:
    host = FakeHostSession()
    guarded = _GuardedHostMcpSession(host)
    response: Any = asyncio.run(
        guarded.call_tool(READBACK_TOOL, {"client_order_id": "rd-open-safe"})
    )

    assert response == {"ok": True}


def test_extension_identity_binds_the_hashed_readonly_selection() -> None:
    identity = paper_identity()

    assert identity.readonly_extension_count == ALPACA_MCP_READONLY_EXTENSION_COUNT == 2
    assert identity.readonly_extension_schema_sha256 == ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256
    assert identity.readonly_extension_tool_names == tuple(sorted(READONLY_EXTENSION_TOOLS))
    assert identity.host_operations_protocol_sha256 == ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256
    assert not (set(identity.readonly_extension_tool_names) & set(identity.tool_names))
    assert not (set(identity.readonly_extension_tool_names) & MUTATING_TOOLS)


def test_missing_readonly_extension_tool_fails_preflight_closed() -> None:
    host = FakeHostSession(tools=tuple(sorted(REQUIRED_TOOLS - {"get_orders"})))

    with pytest.raises(HostMcpConfigurationError, match="missing required tools: get_orders"):
        connect(host)

    assert host.calls == []


def test_readonly_door_admits_extension_reads_and_rejects_every_mutating_tool() -> None:
    host = FakeHostSession()
    prepared = connect(host)
    host.calls.clear()

    orders: Any = asyncio.run(prepared.readonly_call("get_orders", {"status": "open"}))
    activities: Any = asyncio.run(prepared.readonly_call("get_account_activities", {}))

    assert orders == {"ok": True}
    assert activities == {"ok": True}
    assert [name for name, _ in host.calls] == ["get_orders", "get_account_activities"]

    for tool in (*sorted(MUTATING_TOOLS), "close_all_positions", "unknown_tool", ""):
        with pytest.raises(HostMcpConfigurationError, match="read-only door"):
            asyncio.run(prepared.readonly_call(tool, {}))

    assert [name for name, _ in host.calls] == ["get_orders", "get_account_activities"]


def test_readonly_door_keeps_the_secret_boundary() -> None:
    host = FakeHostSession()
    prepared = connect(host)
    host.calls.clear()

    with pytest.raises(HostMcpSecretBoundaryError, match="secret-like"):
        asyncio.run(prepared.readonly_call("get_orders", {"api_key": "do-not-emit"}))

    assert host.calls == []
