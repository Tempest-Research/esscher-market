"""Read-only broker preflight receipt contract (issue #90, PRD PR-3).

One canonical, content-addressed receipt records that a read-only preflight of
the official Alpaca MCP PAPER boundary completed without any broker mutation.
The receipt is redacted by construction: it binds the account identity only as
a SHA-256 digest, never a raw account identifier, and never a credential,
order payload, position payload, or provider secret.  A passing receipt proves
read-only readiness for an authorized PAPER host; it is not evidence that a
broker session, order, fill, flatness-at-close, or P&L ever occurred.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import NoReturn

from ringdown_market.contracts.execution_policy import (
    ACCOUNT_TOOL,
    ACTIVITIES_TOOL,
    ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256,
    ALPACA_MCP_READONLY_EXTENSION_COUNT,
    ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256,
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
    CANCEL_TOOL,
    OPEN_TOOL,
    ORDER_BY_ID_TOOL,
    ORDERS_TOOL,
    POSITIONS_TOOL,
    READBACK_TOOL,
)

from ..strategy.contracts import canonical_json_bytes, sha256_bytes

PREFLIGHT_RECEIPT_SCHEMA = "esscher.broker_preflight_receipt"
PREFLIGHT_RECEIPT_SCHEMA_VERSION = 1
PREFLIGHT_CLAIMS = ("NO_BROKER_MUTATION", "NO_CREDENTIALS", "PAPER_ONLY")
PREFLIGHT_REQUIRED_TOOL_COUNT = 8


class PreflightVerdict(StrEnum):
    """The only two outcomes a read-only preflight receipt may record."""

    PASSED = "PASSED"
    REJECTED = "REJECTED"


PREFLIGHT_REASON_CODES = frozenset(
    {
        "ACCOUNT_BLOCKED",
        "ACCOUNT_INACTIVE",
        "ACCOUNT_MISMATCH",
        "ACCOUNT_QUERY_FAILED",
        "ACTIVITIES_QUERY_FAILED",
        "BUILD_MISMATCH",
        "LATENCY_PROFILE_MISMATCH",
        "MISSING_TOOL",
        "NON_FLAT_START",
        "NON_PAPER_ENDPOINT",
        "OPTIONS_CAPABILITY_MISSING",
        "ORDERS_QUERY_FAILED",
        "PAGINATION_INCOMPLETE",
        "POSITIONS_QUERY_FAILED",
        "PROVENANCE_MISMATCH",
        "RELEASE_MISMATCH",
        "ROUTE_MISMATCH",
        "SCHEMA_DRIFT",
        "STARTING_BALANCE_MISMATCH",
        "TRADING_BLOCKED",
    }
)

_PASS_REQUIRED_QUERY_FLAGS = (
    "account_query_succeeded",
    "orders_query_succeeded",
    "positions_query_succeeded",
    "activities_query_succeeded",
)


class BrokerPreflightRejected(ValueError):
    """A deterministic validation failure for broker preflight receipts."""

    def __init__(self, reason: str, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason} at {path}: {detail}")


def _reject(path: str, detail: str) -> NoReturn:
    raise BrokerPreflightRejected("INVALID_RECEIPT", path, detail)


class _DuplicateFieldError(ValueError):
    pass


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _decode(raw: bytes, *, path: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(path, "receipt artifacts must be bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except _DuplicateFieldError as error:
        _reject(path, f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(path, str(error))
    if not isinstance(value, Mapping):
        _reject(path, "root must be an object")
    return value


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(f"{path}.{missing[0]}", "required field is missing")
    unknown = sorted(keys - fields)
    if unknown:
        _reject(f"{path}.{unknown[0]}", "field is not part of the frozen schema")
    return value


def _text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(path, "must be non-empty text")
    return value


def _bounded_identifier(value: object, *, path: str) -> str:
    text = _text(value, path=path)
    if len(text) > 128:
        _reject(path, "identifier exceeds 128 characters")
    return text


def _digest(value: object, *, path: str, nullable: bool = False) -> str | None:
    if value is None:
        if nullable:
            return None
        _reject(path, "must be a SHA-256 digest")
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        _reject(path, "must be a lowercase hex SHA-256 digest")
    return value


def _required_digest(value: object, *, path: str) -> str:
    digest = _digest(value, path=path)
    if digest is None:
        _reject(path, "must be a SHA-256 digest")
    return digest


def _revision(value: object, *, path: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) not in (40, 64)
        or any(c not in "0123456789abcdef" for c in value)
    ):
        _reject(path, "must be a lowercase hex Git or content revision")
    return value


def _boolean(value: object, *, path: str) -> bool:
    if not isinstance(value, bool):
        _reject(path, "must be a boolean")
    return value


def _integer(value: object, *, path: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        _reject(path, f"must be an integer of at least {minimum}")
    return value


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str):
        _reject(path, "must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _reject(path, "must be an ISO-8601 UTC timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _reject(path, "timestamp must be timezone-aware UTC")
    return parsed.astimezone(UTC)


def _timestamp_text(value: datetime, *, path: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _reject(path, "observed_at must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_text(value: Decimal, *, path: str) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        _reject(path, "must be a finite Decimal")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _decimal_value(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str):
        _reject(path, "must be canonical decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _reject(path, "must be canonical decimal text")
    if not parsed.is_finite():
        _reject(path, "must be a finite decimal")
    if _decimal_text(parsed, path=path) != value:
        _reject(path, "decimal text is not canonical")
    return parsed


def _string_tuple(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        _reject(path, "must be an array of non-empty strings")
    items = tuple(value)
    if items != tuple(sorted(set(items))):
        _reject(path, "must be sorted and unique")
    return items


@dataclass(frozen=True, slots=True)
class BrokerPreflightReceipt:
    """One redacted, content-addressed read-only preflight outcome."""

    receipt_id: str
    verdict: PreflightVerdict
    reason_codes: tuple[str, ...]
    observed_at: datetime
    # Account identity is bound only as digests and sanitized facts.
    account_id_sha256: str
    account_class: str
    account_status: str
    trading_blocked: bool
    account_blocked: bool
    options_enabled: bool
    starting_equity: Decimal
    starting_equity_contract: Decimal
    starting_balance_satisfied: bool
    # Read-only query outcomes and their canonical state digests.
    account_query_succeeded: bool
    orders_query_succeeded: bool
    orders_page_count: int
    open_order_count: int
    orders_state_sha256: str | None
    positions_query_succeeded: bool
    open_position_count: int
    positions_state_sha256: str | None
    activities_query_succeeded: bool
    activities_page_count: int
    activities_state_sha256: str | None
    is_flat: bool
    # Host/build/route/release bindings required by PRD PR-3.
    runtime_code_revision: str
    runtime_build_artifact_sha256: str
    account_capability_id: str
    route_config_sha256: str
    latency_profile_sha256: str
    release_sha256: str | None
    # Pinned MCP capability identity copied from the validated observation.
    environment: str
    adapter: str
    adapter_version: str
    distribution_type: str
    wheel_filename: str
    wheel_sha256: str
    sdist_filename: str
    sdist_sha256: str
    provenance_class: str
    source_equivalent_version: str
    source_equivalent_commit: str
    fastmcp_version: str
    fastmcp_spec: str
    discovered_tool_count: int
    required_tool_count: int
    selected_schema_count: int
    selected_schema_sha256: str
    readonly_extension_count: int
    readonly_extension_schema_sha256: str
    host_operations_protocol_sha256: str
    execution_protocol_sha256: str
    tool_names: tuple[str, ...]
    readonly_extension_tool_names: tuple[str, ...]
    capability_sha256: str
    receipt_sha256: str = ""


_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "claims",
        "receipt_id",
        "verdict",
        "reason_codes",
        "observed_at",
        "account_id_sha256",
        "account_class",
        "account_status",
        "trading_blocked",
        "account_blocked",
        "options_enabled",
        "starting_equity",
        "starting_equity_contract",
        "starting_balance_satisfied",
        "account_query_succeeded",
        "orders_query_succeeded",
        "orders_page_count",
        "open_order_count",
        "orders_state_sha256",
        "positions_query_succeeded",
        "open_position_count",
        "positions_state_sha256",
        "activities_query_succeeded",
        "activities_page_count",
        "activities_state_sha256",
        "is_flat",
        "runtime_code_revision",
        "runtime_build_artifact_sha256",
        "account_capability_id",
        "route_config_sha256",
        "latency_profile_sha256",
        "release_sha256",
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
        "required_tool_count",
        "selected_schema_count",
        "selected_schema_sha256",
        "readonly_extension_count",
        "readonly_extension_schema_sha256",
        "host_operations_protocol_sha256",
        "execution_protocol_sha256",
        "tool_names",
        "readonly_extension_tool_names",
        "capability_sha256",
        "receipt_sha256",
    }
)

# Pinned provenance every receipt must carry verbatim; anything else is schema
# drift or a forged receipt and fails closed at parse time.
_PINNED_CAPABILITY_VALUES: tuple[tuple[str, object], ...] = (
    ("environment", "PAPER"),
    ("adapter", "ALPACA_MCP"),
    ("adapter_version", ALPACA_MCP_V2_VERSION),
    ("distribution_type", ALPACA_MCP_V2_DISTRIBUTION_TYPE),
    ("wheel_filename", ALPACA_MCP_V2_WHEEL_FILENAME),
    ("wheel_sha256", ALPACA_MCP_V2_WHEEL_SHA256),
    ("sdist_filename", ALPACA_MCP_V2_SDIST_FILENAME),
    ("sdist_sha256", ALPACA_MCP_V2_SDIST_SHA256),
    ("provenance_class", ALPACA_MCP_V2_PROVENANCE),
    ("source_equivalent_version", ALPACA_MCP_V2_SOURCE_EQUIVALENT_VERSION),
    ("source_equivalent_commit", ALPACA_MCP_V2_SOURCE_EQUIVALENT_COMMIT),
    ("fastmcp_version", ALPACA_MCP_V2_FASTMCP_VERSION),
    ("fastmcp_spec", ALPACA_MCP_V2_FASTMCP_SPEC),
    ("discovered_tool_count", ALPACA_MCP_V2_DISCOVERED_TOOL_COUNT),
    ("required_tool_count", PREFLIGHT_REQUIRED_TOOL_COUNT),
    ("selected_schema_count", ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT),
    ("selected_schema_sha256", ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256),
    ("readonly_extension_count", ALPACA_MCP_READONLY_EXTENSION_COUNT),
    ("readonly_extension_schema_sha256", ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256),
    ("host_operations_protocol_sha256", ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256),
    ("execution_protocol_sha256", ALPACA_MCP_V2_PROTOCOL_SHA256),
)
_PINNED_TOOL_NAMES = tuple(
    sorted(
        (
            ACCOUNT_TOOL,
            OPEN_TOOL,
            READBACK_TOOL,
            ORDER_BY_ID_TOOL,
            CANCEL_TOOL,
            POSITIONS_TOOL,
        )
    )
)
_PINNED_EXTENSION_TOOL_NAMES = tuple(sorted((ACTIVITIES_TOOL, ORDERS_TOOL)))


def broker_preflight_unsigned_payload(receipt: BrokerPreflightReceipt) -> dict[str, object]:
    """Serialize one receipt without its self hash."""

    return {
        "schema": PREFLIGHT_RECEIPT_SCHEMA,
        "schema_version": PREFLIGHT_RECEIPT_SCHEMA_VERSION,
        "claims": list(PREFLIGHT_CLAIMS),
        "receipt_id": receipt.receipt_id,
        "verdict": receipt.verdict.value,
        "reason_codes": list(receipt.reason_codes),
        "observed_at": _timestamp_text(receipt.observed_at, path="observed_at"),
        "account_id_sha256": receipt.account_id_sha256,
        "account_class": receipt.account_class,
        "account_status": receipt.account_status,
        "trading_blocked": receipt.trading_blocked,
        "account_blocked": receipt.account_blocked,
        "options_enabled": receipt.options_enabled,
        "starting_equity": _decimal_text(receipt.starting_equity, path="starting_equity"),
        "starting_equity_contract": _decimal_text(
            receipt.starting_equity_contract, path="starting_equity_contract"
        ),
        "starting_balance_satisfied": receipt.starting_balance_satisfied,
        "account_query_succeeded": receipt.account_query_succeeded,
        "orders_query_succeeded": receipt.orders_query_succeeded,
        "orders_page_count": receipt.orders_page_count,
        "open_order_count": receipt.open_order_count,
        "orders_state_sha256": receipt.orders_state_sha256,
        "positions_query_succeeded": receipt.positions_query_succeeded,
        "open_position_count": receipt.open_position_count,
        "positions_state_sha256": receipt.positions_state_sha256,
        "activities_query_succeeded": receipt.activities_query_succeeded,
        "activities_page_count": receipt.activities_page_count,
        "activities_state_sha256": receipt.activities_state_sha256,
        "is_flat": receipt.is_flat,
        "runtime_code_revision": receipt.runtime_code_revision,
        "runtime_build_artifact_sha256": receipt.runtime_build_artifact_sha256,
        "account_capability_id": receipt.account_capability_id,
        "route_config_sha256": receipt.route_config_sha256,
        "latency_profile_sha256": receipt.latency_profile_sha256,
        "release_sha256": receipt.release_sha256,
        "environment": receipt.environment,
        "adapter": receipt.adapter,
        "adapter_version": receipt.adapter_version,
        "distribution_type": receipt.distribution_type,
        "wheel_filename": receipt.wheel_filename,
        "wheel_sha256": receipt.wheel_sha256,
        "sdist_filename": receipt.sdist_filename,
        "sdist_sha256": receipt.sdist_sha256,
        "provenance_class": receipt.provenance_class,
        "source_equivalent_version": receipt.source_equivalent_version,
        "source_equivalent_commit": receipt.source_equivalent_commit,
        "fastmcp_version": receipt.fastmcp_version,
        "fastmcp_spec": receipt.fastmcp_spec,
        "discovered_tool_count": receipt.discovered_tool_count,
        "required_tool_count": receipt.required_tool_count,
        "selected_schema_count": receipt.selected_schema_count,
        "selected_schema_sha256": receipt.selected_schema_sha256,
        "readonly_extension_count": receipt.readonly_extension_count,
        "readonly_extension_schema_sha256": receipt.readonly_extension_schema_sha256,
        "host_operations_protocol_sha256": receipt.host_operations_protocol_sha256,
        "execution_protocol_sha256": receipt.execution_protocol_sha256,
        "tool_names": list(receipt.tool_names),
        "readonly_extension_tool_names": list(receipt.readonly_extension_tool_names),
        "capability_sha256": receipt.capability_sha256,
    }


def broker_preflight_receipt_bytes(receipt: BrokerPreflightReceipt) -> bytes:
    """Return canonical receipt bytes including the content-addressed self hash."""

    unsigned = broker_preflight_unsigned_payload(receipt)
    return canonical_json_bytes(
        {**unsigned, "receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}
    )


def broker_preflight_receipt_sha256(receipt: BrokerPreflightReceipt) -> str:
    """Derive the receipt's content address from its unsigned payload."""

    return sha256_bytes(canonical_json_bytes(broker_preflight_unsigned_payload(receipt)))


def finalize_broker_preflight_receipt(receipt: BrokerPreflightReceipt) -> BrokerPreflightReceipt:
    """Return the same receipt with its content-addressed self hash bound."""

    return replace(receipt, receipt_sha256=broker_preflight_receipt_sha256(receipt))


def parse_broker_preflight_receipt(raw: bytes) -> BrokerPreflightReceipt:
    """Strictly parse and authenticate one read-only preflight receipt."""

    payload = _strict_object(
        _decode(raw, path="preflight_receipt"),
        path="preflight_receipt",
        fields=_RECEIPT_FIELDS,
    )
    if (
        payload["schema"] != PREFLIGHT_RECEIPT_SCHEMA
        or payload["schema_version"] != PREFLIGHT_RECEIPT_SCHEMA_VERSION
    ):
        _reject("preflight_receipt", "unsupported preflight receipt schema or version")
    claims = payload["claims"]
    if not isinstance(claims, list) or tuple(claims) != PREFLIGHT_CLAIMS:
        _reject("preflight_receipt.claims", "claims must equal the frozen preflight boundary")

    verdict_value = payload["verdict"]
    if not isinstance(verdict_value, str) or verdict_value not in set(PreflightVerdict):
        _reject("preflight_receipt.verdict", "verdict must be PASSED or REJECTED")
    verdict = PreflightVerdict(verdict_value)
    reason_codes = _string_tuple(payload["reason_codes"], path="preflight_receipt.reason_codes")
    for code in reason_codes:
        if code not in PREFLIGHT_REASON_CODES:
            _reject("preflight_receipt.reason_codes", f"unknown reason code {code}")
    if verdict is PreflightVerdict.PASSED and reason_codes:
        _reject("preflight_receipt.reason_codes", "a PASSED receipt cannot carry reason codes")
    if verdict is PreflightVerdict.REJECTED and not reason_codes:
        _reject("preflight_receipt.reason_codes", "a REJECTED receipt must carry reason codes")

    for field, expected in _PINNED_CAPABILITY_VALUES:
        if payload[field] != expected:
            _reject(f"preflight_receipt.{field}", "receipt does not match the pinned MCP identity")
    tool_names = _string_tuple(payload["tool_names"], path="preflight_receipt.tool_names")
    if tool_names != _PINNED_TOOL_NAMES:
        _reject("preflight_receipt.tool_names", "tool selection does not match the pinned six")
    extension_names = _string_tuple(
        payload["readonly_extension_tool_names"],
        path="preflight_receipt.readonly_extension_tool_names",
    )
    if extension_names != _PINNED_EXTENSION_TOOL_NAMES:
        _reject(
            "preflight_receipt.readonly_extension_tool_names",
            "extension selection does not match the pinned read-only pair",
        )

    account_class = _text(payload["account_class"], path="preflight_receipt.account_class")
    if account_class != "PAPER":
        _reject("preflight_receipt.account_class", "preflight receipts only admit PAPER accounts")

    trading_blocked = _boolean(payload["trading_blocked"], path="preflight_receipt.trading_blocked")
    account_blocked = _boolean(payload["account_blocked"], path="preflight_receipt.account_blocked")
    options_enabled = _boolean(payload["options_enabled"], path="preflight_receipt.options_enabled")
    starting_equity = _decimal_value(
        payload["starting_equity"], path="preflight_receipt.starting_equity"
    )
    starting_equity_contract = _decimal_value(
        payload["starting_equity_contract"], path="preflight_receipt.starting_equity_contract"
    )
    starting_balance_satisfied = _boolean(
        payload["starting_balance_satisfied"], path="preflight_receipt.starting_balance_satisfied"
    )
    query_flags = {
        flag: _boolean(payload[flag], path=f"preflight_receipt.{flag}")
        for flag in _PASS_REQUIRED_QUERY_FLAGS
    }
    orders_page_count = _integer(
        payload["orders_page_count"], path="preflight_receipt.orders_page_count", minimum=0
    )
    open_order_count = _integer(
        payload["open_order_count"], path="preflight_receipt.open_order_count", minimum=0
    )
    positions_query_succeeded = query_flags["positions_query_succeeded"]
    open_position_count = _integer(
        payload["open_position_count"], path="preflight_receipt.open_position_count", minimum=0
    )
    activities_page_count = _integer(
        payload["activities_page_count"], path="preflight_receipt.activities_page_count", minimum=0
    )
    is_flat = _boolean(payload["is_flat"], path="preflight_receipt.is_flat")

    if verdict is PreflightVerdict.PASSED:
        if not all(query_flags.values()):
            _reject(
                "preflight_receipt", "a PASSED receipt requires every read-only query to succeed"
            )
        if orders_page_count < 1 or activities_page_count < 1:
            _reject("preflight_receipt", "a PASSED receipt requires completed paginated queries")
        if trading_blocked or account_blocked:
            _reject("preflight_receipt", "a PASSED receipt cannot admit a blocked account")
        if not options_enabled:
            _reject("preflight_receipt", "a PASSED receipt requires the options capability")
        if not starting_balance_satisfied or starting_equity != starting_equity_contract:
            _reject("preflight_receipt", "a PASSED receipt requires the starting-balance contract")
        if not is_flat or open_order_count != 0 or open_position_count != 0:
            _reject("preflight_receipt", "a PASSED receipt requires a flat starting state")

    observed_at = _timestamp(payload["observed_at"], path="preflight_receipt.observed_at")
    receipt = BrokerPreflightReceipt(
        receipt_id=_bounded_identifier(payload["receipt_id"], path="preflight_receipt.receipt_id"),
        verdict=verdict,
        reason_codes=reason_codes,
        observed_at=observed_at,
        account_id_sha256=_required_digest(
            payload["account_id_sha256"], path="preflight_receipt.account_id_sha256"
        ),
        account_class=account_class,
        account_status=_text(payload["account_status"], path="preflight_receipt.account_status"),
        trading_blocked=trading_blocked,
        account_blocked=account_blocked,
        options_enabled=options_enabled,
        starting_equity=starting_equity,
        starting_equity_contract=starting_equity_contract,
        starting_balance_satisfied=starting_balance_satisfied,
        account_query_succeeded=query_flags["account_query_succeeded"],
        orders_query_succeeded=query_flags["orders_query_succeeded"],
        orders_page_count=orders_page_count,
        open_order_count=open_order_count,
        orders_state_sha256=_digest(
            payload["orders_state_sha256"],
            path="preflight_receipt.orders_state_sha256",
            nullable=True,
        ),
        positions_query_succeeded=positions_query_succeeded,
        open_position_count=open_position_count,
        positions_state_sha256=_digest(
            payload["positions_state_sha256"],
            path="preflight_receipt.positions_state_sha256",
            nullable=True,
        ),
        activities_query_succeeded=query_flags["activities_query_succeeded"],
        activities_page_count=activities_page_count,
        activities_state_sha256=_digest(
            payload["activities_state_sha256"],
            path="preflight_receipt.activities_state_sha256",
            nullable=True,
        ),
        is_flat=is_flat,
        runtime_code_revision=_revision(
            payload["runtime_code_revision"], path="preflight_receipt.runtime_code_revision"
        ),
        runtime_build_artifact_sha256=_required_digest(
            payload["runtime_build_artifact_sha256"],
            path="preflight_receipt.runtime_build_artifact_sha256",
        ),
        account_capability_id=_bounded_identifier(
            payload["account_capability_id"], path="preflight_receipt.account_capability_id"
        ),
        route_config_sha256=_required_digest(
            payload["route_config_sha256"], path="preflight_receipt.route_config_sha256"
        ),
        latency_profile_sha256=_required_digest(
            payload["latency_profile_sha256"], path="preflight_receipt.latency_profile_sha256"
        ),
        release_sha256=_digest(
            payload["release_sha256"], path="preflight_receipt.release_sha256", nullable=True
        ),
        environment=str(payload["environment"]),
        adapter=str(payload["adapter"]),
        adapter_version=str(payload["adapter_version"]),
        distribution_type=str(payload["distribution_type"]),
        wheel_filename=str(payload["wheel_filename"]),
        wheel_sha256=str(payload["wheel_sha256"]),
        sdist_filename=str(payload["sdist_filename"]),
        sdist_sha256=str(payload["sdist_sha256"]),
        provenance_class=str(payload["provenance_class"]),
        source_equivalent_version=str(payload["source_equivalent_version"]),
        source_equivalent_commit=str(payload["source_equivalent_commit"]),
        fastmcp_version=str(payload["fastmcp_version"]),
        fastmcp_spec=str(payload["fastmcp_spec"]),
        discovered_tool_count=int(payload["discovered_tool_count"]),  # type: ignore[arg-type]
        required_tool_count=int(payload["required_tool_count"]),  # type: ignore[arg-type]
        selected_schema_count=int(payload["selected_schema_count"]),  # type: ignore[arg-type]
        selected_schema_sha256=str(payload["selected_schema_sha256"]),
        readonly_extension_count=int(payload["readonly_extension_count"]),  # type: ignore[arg-type]
        readonly_extension_schema_sha256=str(payload["readonly_extension_schema_sha256"]),
        host_operations_protocol_sha256=str(payload["host_operations_protocol_sha256"]),
        execution_protocol_sha256=str(payload["execution_protocol_sha256"]),
        tool_names=tool_names,
        readonly_extension_tool_names=extension_names,
        capability_sha256=_required_digest(
            payload["capability_sha256"], path="preflight_receipt.capability_sha256"
        ),
        receipt_sha256=_required_digest(
            payload["receipt_sha256"], path="preflight_receipt.receipt_sha256"
        ),
    )
    if receipt.receipt_sha256 != broker_preflight_receipt_sha256(receipt):
        raise BrokerPreflightRejected(
            "HASH_MISMATCH",
            "preflight_receipt.receipt_sha256",
            "receipt hash does not match payload",
        )
    return receipt


__all__ = [
    "PREFLIGHT_CLAIMS",
    "PREFLIGHT_REASON_CODES",
    "PREFLIGHT_RECEIPT_SCHEMA",
    "PREFLIGHT_RECEIPT_SCHEMA_VERSION",
    "BrokerPreflightReceipt",
    "BrokerPreflightRejected",
    "PreflightVerdict",
    "broker_preflight_receipt_bytes",
    "broker_preflight_receipt_sha256",
    "broker_preflight_unsigned_payload",
    "finalize_broker_preflight_receipt",
    "parse_broker_preflight_receipt",
]
