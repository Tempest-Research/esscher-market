"""Host-owned Alpaca MCP session preflight and runtime guardrails."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from importlib import import_module
from typing import NoReturn, Protocol
from weakref import WeakKeyDictionary

from ringdown_market.contracts.execution_policy import (
    ACCOUNT_TOOL,
    ALPACA_MCP_V2_DISCOVERED_TOOL_COUNT,
    ALPACA_MCP_V2_DISTRIBUTION_TYPE,
    ALPACA_MCP_V2_FASTMCP_SPEC,
    ALPACA_MCP_V2_FASTMCP_VERSION,
    ALPACA_MCP_V2_PROTOCOL_SHA256,
    ALPACA_MCP_V2_PROVENANCE,
    ALPACA_MCP_V2_SDIST_FILENAME,
    ALPACA_MCP_V2_SDIST_SHA256,
    ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT,
    ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256,
    ALPACA_MCP_V2_SOURCE_EQUIVALENT_COMMIT,
    ALPACA_MCP_V2_SOURCE_EQUIVALENT_VERSION,
    ALPACA_MCP_V2_VERSION,
    ALPACA_MCP_V2_WHEEL_FILENAME,
    ALPACA_MCP_V2_WHEEL_SHA256,
)

from .mcp import (
    CANCEL_TOOL,
    OPEN_TOOL,
    ORDER_BY_ID_TOOL,
    POSITIONS_TOOL,
    READBACK_TOOL,
    McpPaperBroker,
    McpToolSession,
)

_REQUIRED_TOOLS = frozenset(
    {
        ACCOUNT_TOOL,
        OPEN_TOOL,
        READBACK_TOOL,
        ORDER_BY_ID_TOOL,
        CANCEL_TOOL,
        POSITIONS_TOOL,
    }
)
_RUNTIME_TOOLS = _REQUIRED_TOOLS
_MUTATING_TOOLS = frozenset({OPEN_TOOL, CANCEL_TOOL})
_SECRET_KEYS = frozenset(
    {
        "alpaca_api_key",
        "alpaca_secret_key",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "secret_key",
        "token",
    }
)


class HostMcpError(RuntimeError):
    """Base class for safe operator-facing host MCP failures."""


class HostMcpConfigurationError(HostMcpError):
    """Raised when host identity or capabilities violate the pinned contract."""


class HostMcpAccountError(HostMcpError):
    """Raised when read-only account preflight cannot prove mutation eligibility."""


class HostMcpUnavailable(HostMcpError):
    """Raised when a non-ambiguous host operation is unavailable."""


class HostMcpMutationAmbiguous(HostMcpError):
    """Raised when a timed-out mutation must be reconciled, never retried."""


class HostMcpSecretBoundaryError(HostMcpError):
    """Raised before secret-like application arguments can cross the host boundary."""


class HostMcpEnvironment(StrEnum):
    """Environment attested by the host that owns the official MCP process."""

    PAPER = "PAPER"


@dataclass(frozen=True, slots=True)
class HostMcpSessionIdentity:
    """Non-secret identity for the one pinned host-managed MCP session."""

    environment: HostMcpEnvironment
    adapter: str = field(default="ALPACA_MCP", init=False)
    adapter_version: str = field(default=ALPACA_MCP_V2_VERSION, init=False)
    distribution_type: str = field(default=ALPACA_MCP_V2_DISTRIBUTION_TYPE, init=False)
    wheel_filename: str = field(default=ALPACA_MCP_V2_WHEEL_FILENAME, init=False)
    wheel_sha256: str = field(default=ALPACA_MCP_V2_WHEEL_SHA256, init=False)
    sdist_filename: str = field(default=ALPACA_MCP_V2_SDIST_FILENAME, init=False)
    sdist_sha256: str = field(default=ALPACA_MCP_V2_SDIST_SHA256, init=False)
    provenance_class: str = field(default=ALPACA_MCP_V2_PROVENANCE, init=False)
    source_equivalent_version: str = field(
        default=ALPACA_MCP_V2_SOURCE_EQUIVALENT_VERSION, init=False
    )
    source_equivalent_commit: str = field(
        default=ALPACA_MCP_V2_SOURCE_EQUIVALENT_COMMIT, init=False
    )
    fastmcp_version: str = field(default=ALPACA_MCP_V2_FASTMCP_VERSION, init=False)
    fastmcp_spec: str = field(default=ALPACA_MCP_V2_FASTMCP_SPEC, init=False)
    discovered_tool_count: int = field(default=ALPACA_MCP_V2_DISCOVERED_TOOL_COUNT, init=False)
    selected_schema_count: int = field(default=ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT, init=False)
    selected_schema_sha256: str = field(default=ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256, init=False)
    tool_names: tuple[str, ...] = field(default=tuple(sorted(_REQUIRED_TOOLS)), init=False)
    execution_protocol_sha256: str = field(default=ALPACA_MCP_V2_PROTOCOL_SHA256, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.environment, HostMcpEnvironment):
            raise HostMcpConfigurationError(
                "host MCP session must attest the paper environment before any tool call"
            )


@dataclass(frozen=True, slots=True)
class HostMcpCapabilityObservation:
    """Sanitized result of capability and account preflight."""

    capability_sha256: str
    required_tool_count: int
    account_status: str
    trading_blocked: bool
    account_blocked: bool
    observed_at: datetime
    environment: HostMcpEnvironment = HostMcpEnvironment.PAPER
    adapter: str = "ALPACA_MCP"
    adapter_version: str = ALPACA_MCP_V2_VERSION
    distribution_type: str = ALPACA_MCP_V2_DISTRIBUTION_TYPE
    wheel_filename: str = ALPACA_MCP_V2_WHEEL_FILENAME
    wheel_sha256: str = ALPACA_MCP_V2_WHEEL_SHA256
    sdist_filename: str = ALPACA_MCP_V2_SDIST_FILENAME
    sdist_sha256: str = ALPACA_MCP_V2_SDIST_SHA256
    provenance_class: str = ALPACA_MCP_V2_PROVENANCE
    source_equivalent_version: str = ALPACA_MCP_V2_SOURCE_EQUIVALENT_VERSION
    source_equivalent_commit: str = ALPACA_MCP_V2_SOURCE_EQUIVALENT_COMMIT
    fastmcp_version: str = ALPACA_MCP_V2_FASTMCP_VERSION
    fastmcp_spec: str = ALPACA_MCP_V2_FASTMCP_SPEC
    discovered_tool_count: int = ALPACA_MCP_V2_DISCOVERED_TOOL_COUNT
    selected_schema_count: int = ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT
    selected_schema_sha256: str = ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256
    tool_names: tuple[str, ...] = tuple(sorted(_REQUIRED_TOOLS))
    execution_protocol_sha256: str = ALPACA_MCP_V2_PROTOCOL_SHA256


class HostManagedMcpSession(Protocol):
    """Host-owned normalized MCP client; credentials remain behind this object."""

    async def list_tools(self) -> object: ...

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object: ...


class PreparedHostMcpSession:
    """Opaque, factory-issued capability for one fully preflighted PAPER MCP door.

    The supported module API issues this capability only from the factory's
    preflighted ``connect`` path. The raw host session is intentionally not a
    public field. This is a provenance boundary for ordinary callers, not a claim
    to resist arbitrary in-process reflection or monkeypatching.
    """

    __slots__ = ("__weakref__",)

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("PreparedHostMcpSession instances must be factory-created")

    def __copy__(self) -> NoReturn:
        raise TypeError("PreparedHostMcpSession instances must be factory-created")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("PreparedHostMcpSession instances must be factory-created")

    @property
    def observation(self) -> HostMcpCapabilityObservation:
        """Return the validated, sanitized preflight observation only."""

        return self._validated_state().observation

    def broker(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> McpPaperBroker:
        """Create the legacy frozen-decision broker over the attested session."""

        return McpPaperBroker(self._validated_state().session, clock=clock)

    async def read_order(self, order_id: str) -> object:
        """Read one order through the guarded door without exposing its session."""

        if not isinstance(order_id, str) or not order_id:
            raise HostMcpConfigurationError("host MCP order ID must be non-empty text")
        return await self._validated_state().session.call_tool(
            ORDER_BY_ID_TOOL,
            {"order_id": order_id},
        )


def _tool_names(response: object) -> frozenset[str]:
    if isinstance(response, (str, bytes, Mapping)) or not isinstance(response, Sequence):
        raise HostMcpConfigurationError("host MCP capability listing was malformed")

    names: set[str] = set()
    for tool in response:
        if isinstance(tool, str):
            name = tool
        elif isinstance(tool, Mapping):
            name = tool.get("name")
        else:
            name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name:
            raise HostMcpConfigurationError("host MCP capability listing was malformed")
        names.add(name)
    return frozenset(names)


def _contains_secret_like(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                return True
            normalized = key.strip().lower().replace("-", "_")
            if normalized in _SECRET_KEYS or _contains_secret_like(nested):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_secret_like(item) for item in value)
    return False


def _capability_sha256(identity: HostMcpSessionIdentity) -> str:
    payload = {
        "adapter": identity.adapter,
        "adapter_version": identity.adapter_version,
        "discovered_tool_count": identity.discovered_tool_count,
        "distribution_type": identity.distribution_type,
        "environment": identity.environment.value,
        "execution_protocol_sha256": identity.execution_protocol_sha256,
        "fastmcp_spec": identity.fastmcp_spec,
        "fastmcp_version": identity.fastmcp_version,
        "provenance_class": identity.provenance_class,
        "sdist_filename": identity.sdist_filename,
        "sdist_sha256": identity.sdist_sha256,
        "selected_schema_count": identity.selected_schema_count,
        "selected_schema_sha256": identity.selected_schema_sha256,
        "source_equivalent_commit": identity.source_equivalent_commit,
        "source_equivalent_version": identity.source_equivalent_version,
        "tool_names": list(identity.tool_names),
        "wheel_filename": identity.wheel_filename,
        "wheel_sha256": identity.wheel_sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


class _GuardedHostMcpSession:
    def __init__(self, host: HostManagedMcpSession) -> None:
        self._host = host

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        if name not in _RUNTIME_TOOLS:
            raise HostMcpConfigurationError(f"host MCP tool is not allowed: {name}")
        if _contains_secret_like(arguments):
            raise HostMcpSecretBoundaryError(
                "secret-like application arguments cannot cross the host MCP boundary"
            )

        try:
            return await self._host.call_tool(name, arguments)
        except TimeoutError:
            if name in _MUTATING_TOOLS:
                raise HostMcpMutationAmbiguous(
                    "host MCP mutation timed out; read back deterministic identity and never retry"
                ) from None
            raise HostMcpUnavailable(f"host MCP read-only tool timed out: {name}") from None
        except Exception:
            raise HostMcpUnavailable(f"host MCP tool failed: {name}") from None


class HostMcpPaperSessionFactory:
    """Preflight one official host session and expose one guarded adapter door."""

    def __init__(
        self,
        identity: HostMcpSessionIdentity,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if identity.environment is not HostMcpEnvironment.PAPER:
            raise HostMcpConfigurationError(
                "host MCP session must attest the paper environment before any tool call"
            )
        self._identity = identity
        self._clock = clock

    async def _preflight(self, host: HostManagedMcpSession) -> HostMcpCapabilityObservation:
        try:
            available = _tool_names(await host.list_tools())
        except TimeoutError:
            raise HostMcpUnavailable("host MCP capability listing timed out") from None
        except HostMcpConfigurationError:
            raise
        except Exception:
            raise HostMcpUnavailable("host MCP capability listing failed") from None

        missing = sorted(_REQUIRED_TOOLS - available)
        if missing:
            raise HostMcpConfigurationError(
                "host MCP is missing required tools: " + ", ".join(missing)
            )

        try:
            account = await host.call_tool(ACCOUNT_TOOL, {})
        except TimeoutError:
            raise HostMcpUnavailable("host MCP account preflight timed out") from None
        except Exception:
            raise HostMcpUnavailable("host MCP account preflight failed") from None

        if not isinstance(account, Mapping):
            raise HostMcpAccountError("host MCP account preflight response was malformed")
        status = account.get("status")
        trading_blocked = account.get("trading_blocked")
        account_blocked = account.get("account_blocked")
        if (
            not isinstance(status, str)
            or not status
            or not isinstance(trading_blocked, bool)
            or not isinstance(account_blocked, bool)
        ):
            raise HostMcpAccountError("host MCP account preflight response was malformed")
        if status.upper() != "ACTIVE" or trading_blocked or account_blocked:
            raise HostMcpAccountError("host MCP paper account is not eligible for mutation")

        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise HostMcpConfigurationError("host MCP observation clock must be timezone-aware")
        return HostMcpCapabilityObservation(
            capability_sha256=_capability_sha256(self._identity),
            required_tool_count=len(_REQUIRED_TOOLS),
            account_status=status.upper(),
            trading_blocked=trading_blocked,
            account_blocked=account_blocked,
            observed_at=observed_at,
            environment=self._identity.environment,
            adapter=self._identity.adapter,
            adapter_version=self._identity.adapter_version,
            distribution_type=self._identity.distribution_type,
            wheel_filename=self._identity.wheel_filename,
            wheel_sha256=self._identity.wheel_sha256,
            sdist_filename=self._identity.sdist_filename,
            sdist_sha256=self._identity.sdist_sha256,
            provenance_class=self._identity.provenance_class,
            source_equivalent_version=self._identity.source_equivalent_version,
            source_equivalent_commit=self._identity.source_equivalent_commit,
            fastmcp_version=self._identity.fastmcp_version,
            fastmcp_spec=self._identity.fastmcp_spec,
            selected_schema_count=self._identity.selected_schema_count,
            selected_schema_sha256=self._identity.selected_schema_sha256,
            tool_names=self._identity.tool_names,
            execution_protocol_sha256=self._identity.execution_protocol_sha256,
            # The host's attestation is the registered discovery receipt.  The
            # fake/normalized list is only checked for the required lifecycle
            # names and must not silently redefine that receipt.
            discovered_tool_count=self._identity.discovered_tool_count,
        )

    async def smoke(self, host: HostManagedMcpSession) -> HostMcpCapabilityObservation:
        """Run capability and account checks without exposing a mutation session."""

        return await self._preflight(host)


def _wire_prepared_host_mcp_capability() -> None:
    """Close the mint and state registry over the only supported factory path."""

    global _install_lifecycle_mcp_broker_mint

    @dataclass(frozen=True, slots=True)
    class PreparedHostMcpState:
        session: McpToolSession
        observation: HostMcpCapabilityObservation

    states: WeakKeyDictionary[PreparedHostMcpSession, PreparedHostMcpState] = WeakKeyDictionary()
    lifecycle_broker_mint: Callable[..., object] | None = None

    def install_lifecycle_mcp_broker_mint(mint: Callable[..., object]) -> None:
        nonlocal lifecycle_broker_mint
        if lifecycle_broker_mint is not None:
            raise HostMcpConfigurationError("lifecycle MCP broker capability is already installed")
        lifecycle_broker_mint = mint

    def validated_state(prepared: PreparedHostMcpSession) -> PreparedHostMcpState:
        try:
            state = states[prepared]
        except KeyError:
            raise HostMcpConfigurationError("host MCP capability must be factory-created") from None
        observation = state.observation
        expected_identity = HostMcpSessionIdentity(HostMcpEnvironment.PAPER)
        if (
            not isinstance(observation, HostMcpCapabilityObservation)
            or observation.capability_sha256 != _capability_sha256(expected_identity)
            or type(observation.required_tool_count) is not int
            or observation.required_tool_count != len(_REQUIRED_TOOLS)
            or observation.account_status != "ACTIVE"
            or type(observation.trading_blocked) is not bool
            or observation.trading_blocked
            or type(observation.account_blocked) is not bool
            or observation.account_blocked
            or observation.environment is not HostMcpEnvironment.PAPER
            or observation.adapter != expected_identity.adapter
            or observation.adapter_version != expected_identity.adapter_version
            or observation.distribution_type != expected_identity.distribution_type
            or observation.wheel_filename != expected_identity.wheel_filename
            or observation.wheel_sha256 != expected_identity.wheel_sha256
            or observation.sdist_filename != expected_identity.sdist_filename
            or observation.sdist_sha256 != expected_identity.sdist_sha256
            or observation.provenance_class != expected_identity.provenance_class
            or observation.source_equivalent_version != expected_identity.source_equivalent_version
            or observation.source_equivalent_commit != expected_identity.source_equivalent_commit
            or observation.fastmcp_version != expected_identity.fastmcp_version
            or observation.fastmcp_spec != expected_identity.fastmcp_spec
            or observation.discovered_tool_count != expected_identity.discovered_tool_count
            or observation.selected_schema_count != expected_identity.selected_schema_count
            or observation.selected_schema_sha256 != expected_identity.selected_schema_sha256
            or observation.tool_names != expected_identity.tool_names
            or observation.execution_protocol_sha256 != expected_identity.execution_protocol_sha256
            or not isinstance(observation.observed_at, datetime)
            or observation.observed_at.tzinfo is None
            or observation.observed_at.utcoffset() is None
        ):
            raise HostMcpConfigurationError(
                "host MCP capability lacks a complete PAPER preflight attestation"
            )
        return state

    async def connect(
        factory: HostMcpPaperSessionFactory,
        host: HostManagedMcpSession,
    ) -> PreparedHostMcpSession:
        """Return the sole guarded runtime session after read-only preflight."""

        observation = await factory._preflight(host)
        prepared = object.__new__(PreparedHostMcpSession)
        states[prepared] = PreparedHostMcpState(
            session=_GuardedHostMcpSession(host),
            observation=observation,
        )
        return prepared

    def lifecycle_broker(
        prepared: PreparedHostMcpSession,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> object:
        """Create the monitored broker only through the attested factory capability."""

        if lifecycle_broker_mint is None:
            raise HostMcpConfigurationError("lifecycle MCP broker capability is unavailable")
        return lifecycle_broker_mint(prepared._validated_state().session, clock=clock)

    PreparedHostMcpSession._validated_state = validated_state  # type: ignore[attr-defined]
    PreparedHostMcpSession.lifecycle_broker = lifecycle_broker  # type: ignore[attr-defined]
    HostMcpPaperSessionFactory.connect = connect
    _install_lifecycle_mcp_broker_mint = install_lifecycle_mcp_broker_mint


_wire_prepared_host_mcp_capability()
del _wire_prepared_host_mcp_capability

_lifecycle_mcp = import_module("ringdown_market.execution.lifecycle_mcp")

del _lifecycle_mcp, import_module
