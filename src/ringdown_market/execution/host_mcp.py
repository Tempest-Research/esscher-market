"""Host-owned Alpaca MCP session preflight and runtime guardrails."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import NoReturn, Protocol
from weakref import WeakKeyDictionary

from ringdown_market.contracts.execution_policy import ACCOUNT_TOOL

from .mcp import (
    ALPACA_MCP_COMMIT,
    ALPACA_MCP_VERSION,
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
    adapter_version: str = field(default=ALPACA_MCP_VERSION, init=False)
    adapter_commit: str = field(default=ALPACA_MCP_COMMIT, init=False)

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
    adapter_version: str = ALPACA_MCP_VERSION
    adapter_commit: str = ALPACA_MCP_COMMIT


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

    def lifecycle_broker(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> object:
        """Create the monitored-lifecycle adapter only from an attested capability."""

        # Local import avoids a module cycle: the adapter maps host errors into
        # lifecycle broker failures but receives no credential/session factory.
        from ringdown_market.execution.lifecycle_mcp import LifecycleMcpPaperBroker

        return LifecycleMcpPaperBroker(self._validated_state().session, clock=clock)

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
        "adapter_commit": identity.adapter_commit,
        "adapter_version": identity.adapter_version,
        "environment": identity.environment.value,
        "required_tools": sorted(_REQUIRED_TOOLS),
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
        )

    async def smoke(self, host: HostManagedMcpSession) -> HostMcpCapabilityObservation:
        """Run capability and account checks without exposing a mutation session."""

        return await self._preflight(host)


def _wire_prepared_host_mcp_capability() -> None:
    """Close the mint and state registry over the only supported factory path."""

    @dataclass(frozen=True, slots=True)
    class PreparedHostMcpState:
        session: McpToolSession
        observation: HostMcpCapabilityObservation

    states: WeakKeyDictionary[PreparedHostMcpSession, PreparedHostMcpState] = WeakKeyDictionary()

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
            or observation.adapter_commit != expected_identity.adapter_commit
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

    PreparedHostMcpSession._validated_state = validated_state  # type: ignore[attr-defined]
    HostMcpPaperSessionFactory.connect = connect


_wire_prepared_host_mcp_capability()
del _wire_prepared_host_mcp_capability
