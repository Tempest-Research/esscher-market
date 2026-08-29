"""Host-owned Alpaca MCP session preflight and runtime guardrails."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

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

ACCOUNT_TOOL = "get_account_info"
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
_RUNTIME_TOOLS = _REQUIRED_TOOLS - {ACCOUNT_TOOL}
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


@dataclass(frozen=True, slots=True)
class PreparedHostMcpSession:
    """Only the guarded session and its sanitized startup observation."""

    session: McpToolSession
    observation: HostMcpCapabilityObservation

    def broker(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> McpPaperBroker:
        """Create the sole production broker over the preflighted host session."""

        return McpPaperBroker(self.session, clock=clock)


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

    async def connect(self, host: HostManagedMcpSession) -> PreparedHostMcpSession:
        """Return the sole guarded runtime session after read-only preflight."""

        observation = await self._preflight(host)
        return PreparedHostMcpSession(
            session=_GuardedHostMcpSession(host),
            observation=observation,
        )
