"""Read-only PAPER broker preflight for the production host (#90, PRD PR-3).

One repeatable, no-mutation command proves that the current host can support
the exact release: PAPER endpoint/account identity, the required options
capability, the pinned MCP tool/schema provenance, read-only order/position/
activity queries with correct pagination, a flat starting state, the approved
route configuration, and the frozen latency-profile identity.  The outcome is
one canonical, redacted, content-addressed receipt claiming
``NO_BROKER_MUTATION``; every mismatch produces a reason-coded rejection
receipt instead of an exception, and any mutation attempt through this path is
impossible by construction because only the guarded read-only door is used.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import NoReturn

from ringdown_market.contracts.broker_preflight import (
    BrokerPreflightReceipt,
    PreflightVerdict,
    broker_preflight_receipt_bytes,
    finalize_broker_preflight_receipt,
    parse_broker_preflight_receipt,
)
from ringdown_market.contracts.execution_policy import (
    ACCOUNT_TOOL,
    ORDERS_TOOL,
    POSITIONS_TOOL,
)
from ringdown_market.contracts.latency_profile import load_latency_profile
from ringdown_market.contracts.reasoner_route import load_current_approved_reasoner_route
from ringdown_market.execution.host_mcp import HostMcpError, PreparedHostMcpSession
from ringdown_market.lifecycle.broker import PAPER_ACCOUNT_CLASS
from ringdown_market.runtime.paper_mcp_composition import (
    PaperMcpCompositionReason,
    PaperMcpCompositionRejected,
    _nonzero_position_count,
    _readonly_canonical_bytes,
)
from ringdown_market.sourcedata.alpaca_option_events import (
    AccountActivitySource,
    ActivityAcquisitionRejected,
    McpAccountActivitySource,
    acquire_account_activities,
    summarize_orders_state,
)
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

PREFLIGHT_ACTIVITIES_LOOKBACK = timedelta(days=1)
PREFLIGHT_ACTIVITIES_PAGE_SIZE = 100
PREFLIGHT_ACTIVITIES_MAX_PAGES = 5
PREFLIGHT_ORDERS_PAGE_LIMIT = 100
PREFLIGHT_ORDERS_MAX_PAGES = 5


class PaperPreflightReason(StrEnum):
    """Structural reasons the preflight command itself cannot run."""

    SESSION_NOT_PREPARED = "SESSION_NOT_PREPARED"
    EXPECTATIONS_INVALID = "EXPECTATIONS_INVALID"


class PaperPreflightRejected(ValueError):
    """A structural preflight failure; broker mismatches are receipt reasons."""

    def __init__(self, reason: PaperPreflightReason, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}")


def _reject(reason: PaperPreflightReason, detail: str) -> NoReturn:
    raise PaperPreflightRejected(reason, detail)


@dataclass(frozen=True, slots=True)
class BrokerPreflightExpectations:
    """The exact host/release facts the preflight must re-observe."""

    account_id: str
    account_fingerprint_sha256: str
    starting_equity_contract: Decimal
    account_capability_id: str
    runtime_code_revision: str
    runtime_build_artifact_sha256: str
    route_config_sha256: str | None = None
    latency_profile_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("account_id", "account_capability_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 128:
                _reject(
                    PaperPreflightReason.EXPECTATIONS_INVALID,
                    f"{name} must be a sanitized bounded identifier",
                )
        for name in (
            "account_fingerprint_sha256",
            "runtime_build_artifact_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64:
                _reject(
                    PaperPreflightReason.EXPECTATIONS_INVALID, f"{name} must be a SHA-256 digest"
                )
        revision = self.runtime_code_revision
        if (
            not isinstance(revision, str)
            or len(revision) not in (40, 64)
            or any(character not in "0123456789abcdef" for character in revision)
        ):
            _reject(
                PaperPreflightReason.EXPECTATIONS_INVALID,
                "runtime_code_revision must be a lowercase hex revision",
            )
        equity = self.starting_equity_contract
        if not isinstance(equity, Decimal) or not equity.is_finite() or equity <= 0:
            _reject(
                PaperPreflightReason.EXPECTATIONS_INVALID,
                "starting_equity_contract must be a finite positive Decimal",
            )


class _PaginationCycle(Exception):
    """The open-order probe repeated a continuation token."""


class _PaginationBudgetExhausted(Exception):
    """The open-order probe exceeded its bounded page budget."""


async def _paginated_open_orders(
    prepared: PreparedHostMcpSession,
) -> tuple[list[object], int]:
    """Read every open order through ascending id-cursor pagination.

    Completion is a short page; a repeated cursor is a cycle and an overrun is
    budget exhaustion.  Both become a `PAGINATION_INCOMPLETE` receipt reason
    instead of a guessed-complete order set.
    """

    items: list[object] = []
    pages = 0
    page_token: str | None = None
    while True:
        if pages >= PREFLIGHT_ORDERS_MAX_PAGES:
            raise _PaginationBudgetExhausted()
        arguments: dict[str, object] = {
            "status": "open",
            "direction": "asc",
            "limit": PREFLIGHT_ORDERS_PAGE_LIMIT,
        }
        if page_token is not None:
            arguments["after_order_id"] = page_token
        raw = await _readonly_canonical_bytes(prepared, ORDERS_TOOL, arguments)
        decoded = json.loads(raw.decode("utf-8"))
        if isinstance(decoded, (str, bytes)) or not isinstance(decoded, list):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                "orders page must be a list",
            )
        for item in decoded:
            if not isinstance(item, Mapping):
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "order record must be an object",
                )
        pages += 1
        items.extend(decoded)
        if len(decoded) < PREFLIGHT_ORDERS_PAGE_LIMIT:
            return items, pages
        last = decoded[-1]
        last_id = last.get("id") if isinstance(last, Mapping) else None
        if not isinstance(last_id, str) or not last_id:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                "orders page lacks a continuation id",
            )
        if last_id == page_token:
            raise _PaginationCycle()
        page_token = last_id


async def run_broker_preflight(
    prepared: PreparedHostMcpSession,
    *,
    expectations: BrokerPreflightExpectations,
    receipt_id: str,
    clock: Callable[[], datetime],
    activity_source: AccountActivitySource | None = None,
    activities_lookback: timedelta = PREFLIGHT_ACTIVITIES_LOOKBACK,
) -> BrokerPreflightReceipt:
    """Run the complete read-only preflight and return its canonical receipt.

    Only ``readonly_call`` is ever used, so the receipt's ``NO_BROKER_MUTATION``
    claim is enforced by construction rather than by observation.
    """

    if type(prepared) is not PreparedHostMcpSession:
        _reject(
            PaperPreflightReason.SESSION_NOT_PREPARED,
            "preflight requires a factory-prepared host MCP session",
        )
    observation = prepared.observation
    observed_at = clock()
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        _reject(PaperPreflightReason.EXPECTATIONS_INVALID, "clock must return aware UTC time")
    observed_at = observed_at.astimezone(UTC)

    reasons: list[str] = []
    account_id_sha256 = "0" * 64
    account_class = ""
    account_status = ""
    trading_blocked = True
    account_blocked = True
    options_enabled = False
    starting_equity = Decimal(0)
    account_query_succeeded = False
    try:
        account_raw = await _readonly_canonical_bytes(prepared, ACCOUNT_TOOL, {})
        payload = json.loads(account_raw.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE, "account payload"
            )
        account_query_succeeded = True
        raw_class = payload.get("account_class")
        raw_status = payload.get("status")
        raw_id = payload.get("id")
        account_class = raw_class if isinstance(raw_class, str) else ""
        account_status = raw_status if isinstance(raw_status, str) else ""
        trading_blocked = bool(payload.get("trading_blocked", True))
        account_blocked = bool(payload.get("account_blocked", True))
        options_enabled = payload.get("options_enabled") is True
        if isinstance(raw_id, str) and raw_id:
            account_id_sha256 = sha256_bytes(raw_id.encode("utf-8"))
        equity_value = payload.get("equity")
        try:
            starting_equity = Decimal(str(equity_value))
        except (InvalidOperation, TypeError, ValueError):
            starting_equity = Decimal(0)
        if account_class != PAPER_ACCOUNT_CLASS:
            reasons.append("NON_PAPER_ENDPOINT")
        if account_status != "ACTIVE":
            reasons.append("ACCOUNT_INACTIVE")
        if trading_blocked:
            reasons.append("TRADING_BLOCKED")
        if account_blocked:
            reasons.append("ACCOUNT_BLOCKED")
        if not options_enabled:
            reasons.append("OPTIONS_CAPABILITY_MISSING")
        if raw_id != expectations.account_id:
            reasons.append("ACCOUNT_MISMATCH")
        if sha256_bytes(account_raw) != expectations.account_fingerprint_sha256:
            reasons.append("ACCOUNT_MISMATCH")
        if starting_equity != expectations.starting_equity_contract:
            reasons.append("STARTING_BALANCE_MISMATCH")
    except (HostMcpError, PaperMcpCompositionRejected, ArithmeticError):
        reasons.append("ACCOUNT_QUERY_FAILED")

    orders_query_succeeded = False
    orders_page_count = 0
    open_order_count = 0
    orders_state_sha256: str | None = None
    try:
        combined_items, orders_page_count = await _paginated_open_orders(prepared)
        summary = summarize_orders_state(canonical_json_bytes(combined_items))
        orders_query_succeeded = True
        open_order_count = summary.open_order_count
        orders_state_sha256 = summary.orders_state_sha256
    except _PaginationCycle:
        orders_query_succeeded = True
        reasons.append("PAGINATION_INCOMPLETE")
    except _PaginationBudgetExhausted:
        orders_query_succeeded = True
        reasons.append("PAGINATION_INCOMPLETE")
    except (HostMcpError, PaperMcpCompositionRejected, ActivityAcquisitionRejected):
        reasons.append("ORDERS_QUERY_FAILED")

    positions_query_succeeded = False
    open_position_count = 0
    positions_state_sha256: str | None = None
    try:
        positions_raw = await _readonly_canonical_bytes(prepared, POSITIONS_TOOL, {})
        open_position_count = _nonzero_position_count(positions_raw)
        positions_query_succeeded = True
        positions_state_sha256 = sha256_bytes(positions_raw)
    except (HostMcpError, PaperMcpCompositionRejected, ArithmeticError):
        reasons.append("POSITIONS_QUERY_FAILED")

    activities_query_succeeded = False
    activities_page_count = 0
    activities_state_sha256: str | None = None
    try:
        source = activity_source or McpAccountActivitySource(prepared)
        acquisition = await acquire_account_activities(
            source,
            window_start=observed_at - activities_lookback,
            window_end=observed_at,
            page_size=PREFLIGHT_ACTIVITIES_PAGE_SIZE,
            max_pages=PREFLIGHT_ACTIVITIES_MAX_PAGES,
            clock=clock,
        )
        activities_query_succeeded = acquisition.complete
        activities_page_count = len(acquisition.pages)
        activities_state_sha256 = acquisition.source_payload_sha256
        if not acquisition.complete:
            reasons.append("PAGINATION_INCOMPLETE")
    except ActivityAcquisitionRejected as error:
        activities_page_count = 0
        if error.reason.value == "PAGINATION_BUDGET_EXHAUSTED":
            reasons.append("PAGINATION_INCOMPLETE")
        else:
            reasons.append("ACTIVITIES_QUERY_FAILED")
    except (HostMcpError, PaperMcpCompositionRejected):
        reasons.append("ACTIVITIES_QUERY_FAILED")

    is_flat = open_order_count == 0 and open_position_count == 0
    if not is_flat:
        reasons.append("NON_FLAT_START")

    approved_route = load_current_approved_reasoner_route()
    route_config_sha256 = expectations.route_config_sha256 or approved_route.route_sha256
    if route_config_sha256 != approved_route.route_sha256:
        reasons.append("ROUTE_MISMATCH")
    packaged_profile_sha256 = load_latency_profile().content_sha256
    latency_profile_sha256 = expectations.latency_profile_sha256 or packaged_profile_sha256
    if latency_profile_sha256 != packaged_profile_sha256:
        reasons.append("LATENCY_PROFILE_MISMATCH")

    verdict = PreflightVerdict.REJECTED if reasons else PreflightVerdict.PASSED
    receipt = BrokerPreflightReceipt(
        receipt_id=receipt_id,
        verdict=verdict,
        reason_codes=tuple(sorted(set(reasons))),
        observed_at=observed_at,
        account_id_sha256=account_id_sha256,
        account_class=account_class or "UNKNOWN",
        account_status=account_status or "UNKNOWN",
        trading_blocked=trading_blocked,
        account_blocked=account_blocked,
        options_enabled=options_enabled,
        starting_equity=starting_equity,
        starting_equity_contract=expectations.starting_equity_contract,
        starting_balance_satisfied=(starting_equity == expectations.starting_equity_contract),
        account_query_succeeded=account_query_succeeded,
        orders_query_succeeded=orders_query_succeeded,
        orders_page_count=orders_page_count,
        open_order_count=open_order_count,
        orders_state_sha256=orders_state_sha256,
        positions_query_succeeded=positions_query_succeeded,
        open_position_count=open_position_count,
        positions_state_sha256=positions_state_sha256,
        activities_query_succeeded=activities_query_succeeded,
        activities_page_count=activities_page_count,
        activities_state_sha256=activities_state_sha256,
        is_flat=is_flat,
        runtime_code_revision=expectations.runtime_code_revision,
        runtime_build_artifact_sha256=expectations.runtime_build_artifact_sha256,
        account_capability_id=expectations.account_capability_id,
        route_config_sha256=route_config_sha256,
        latency_profile_sha256=latency_profile_sha256,
        release_sha256=None,
        environment=observation.environment.value,
        adapter=observation.adapter,
        adapter_version=observation.adapter_version,
        distribution_type=observation.distribution_type,
        wheel_filename=observation.wheel_filename,
        wheel_sha256=observation.wheel_sha256,
        sdist_filename=observation.sdist_filename,
        sdist_sha256=observation.sdist_sha256,
        provenance_class=observation.provenance_class,
        source_equivalent_version=observation.source_equivalent_version,
        source_equivalent_commit=observation.source_equivalent_commit,
        fastmcp_version=observation.fastmcp_version,
        fastmcp_spec=observation.fastmcp_spec,
        discovered_tool_count=observation.discovered_tool_count,
        required_tool_count=observation.required_tool_count,
        selected_schema_count=observation.selected_schema_count,
        selected_schema_sha256=observation.selected_schema_sha256,
        readonly_extension_count=observation.readonly_extension_count,
        readonly_extension_schema_sha256=observation.readonly_extension_schema_sha256,
        host_operations_protocol_sha256=observation.host_operations_protocol_sha256,
        execution_protocol_sha256=observation.execution_protocol_sha256,
        tool_names=observation.tool_names,
        readonly_extension_tool_names=observation.readonly_extension_tool_names,
        capability_sha256=observation.capability_sha256,
    )
    finalized = finalize_broker_preflight_receipt(receipt)
    # Round-trip through the frozen contract parser so an internally
    # inconsistent receipt can never be emitted.
    return parse_broker_preflight_receipt(broker_preflight_receipt_bytes(finalized))


def run_broker_preflight_sync(
    prepared: PreparedHostMcpSession,
    *,
    expectations: BrokerPreflightExpectations,
    receipt_id: str,
    clock: Callable[[], datetime],
    activity_source: AccountActivitySource | None = None,
    activities_lookback: timedelta = PREFLIGHT_ACTIVITIES_LOOKBACK,
) -> BrokerPreflightReceipt:
    """Synchronous wrapper used by the CLI and host tooling."""

    return asyncio.run(
        run_broker_preflight(
            prepared,
            expectations=expectations,
            receipt_id=receipt_id,
            clock=clock,
            activity_source=activity_source,
            activities_lookback=activities_lookback,
        )
    )


def preflight_receipt_artifact_path(output_root: object, receipt_id: str) -> Path:
    """The PRD artifact location: <root>/<receipt-id>/preflight-receipt.json."""

    return Path(str(output_root)) / receipt_id / "preflight-receipt.json"


__all__ = [
    "PREFLIGHT_ACTIVITIES_LOOKBACK",
    "PREFLIGHT_ACTIVITIES_MAX_PAGES",
    "PREFLIGHT_ACTIVITIES_PAGE_SIZE",
    "BrokerPreflightExpectations",
    "PaperPreflightReason",
    "PaperPreflightRejected",
    "preflight_receipt_artifact_path",
    "run_broker_preflight",
    "run_broker_preflight_sync",
]
