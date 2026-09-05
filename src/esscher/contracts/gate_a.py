"""Strict Gate A organizer-fact and sanitized capability contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib import resources
from types import MappingProxyType
from typing import NoReturn
from urllib.parse import urlsplit

from esscher.contracts.execution_policy import (
    ACCOUNT_TOOL,
    ALPACA_MCP_COMMIT,
    ALPACA_MCP_V2_DISCOVERED_TOOL_COUNT,
    ALPACA_MCP_V2_DISTRIBUTION_TYPE,
    ALPACA_MCP_V2_FASTMCP_SPEC,
    ALPACA_MCP_V2_FASTMCP_VERSION,
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
    ALPACA_MCP_VERSION,
    CANCEL_TOOL,
    OPEN_TOOL,
    ORDER_BY_ID_TOOL,
    POSITIONS_TOOL,
    READBACK_TOOL,
)

PROGRAMME_SCHEMA = "esscher.gate_a_programme_contract"
CAPABILITY_SCHEMA = "esscher.gate_a_capability_receipt"
SCHEMA_VERSION = 1
PROGRAMME_RESOURCE = "policies/gate_a_programme_v1.json"
PROGRAMME_CONTRACT_SHA256 = "40c2e780c684bdde671b028dbdd8c9b13268e659c24e98a2d452ff7c8692f955"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_LOWER_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_SENSITIVE_TEXT = re.compile(
    r"(?:account[_ -]?(?:id|number)|api[_ -]?key|authorization|credential|"
    r"password|private[_ -]?key|secret|token)",
    flags=re.IGNORECASE,
)

_PROGRAMME_FIELDS = frozenset(
    {
        "claim_labels",
        "contract_id",
        "contract_version",
        "facts",
        "retrieved_at",
        "schema",
        "schema_version",
        "snapshot_representation",
        "source_url",
    }
)
_FACT_FIELDS = frozenset(
    {
        "affects_entry",
        "exact_quote",
        "fact_id",
        "limitation",
        "source_url",
        "status",
        "value",
    }
)
_CAPABILITY_FIELDS = frozenset(
    {
        "account_fingerprint_sha256",
        "adapter",
        "adapter_commit",
        "adapter_version",
        "claim_labels",
        "expires_at",
        "observations",
        "observed_at",
        "producer_build_sha256",
        "programme_contract_sha256",
        "receipt_id",
        "schema",
        "schema_version",
    }
)
_CAPABILITY_V2_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "receipt_id",
        "observed_at",
        "expires_at",
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
        "programme_contract_sha256",
    }
)
_V2_TOOLS = tuple(
    sorted({ACCOUNT_TOOL, OPEN_TOOL, READBACK_TOOL, ORDER_BY_ID_TOOL, CANCEL_TOOL, POSITIONS_TOOL})
)

_OBSERVATION_FIELDS = frozenset(
    {"capability_id", "evidence_sha256", "limitation", "status", "value"}
)

_PROGRAMME_FACT_IDS = (
    "judging_pnl_performance",
    "judging_technology_implementation",
    "judging_creativity_originality",
    "judging_presentation_execution",
    "judging_weights",
    "competition_horizon_and_deadline",
    "base_capital",
    "official_mark_source",
    "official_cost_treatment",
    "allowed_instruments",
    "required_trading_api",
    "required_agent_interface",
    "paper_environment",
    "leverage_rules",
    "drawdown_and_flatten_rules",
    "manual_intervention_rules",
    "dedicated_submission_account",
    "development_account_policy",
    "equity_data_entitlement",
    "option_data_entitlement",
    "option_level_and_multileg_capability",
    "account_reset_assumptions",
    "account_id_submission",
    "required_written_evidence",
    "originality_and_license",
)
_CAPABILITY_IDS = (
    "account_reset_state",
    "account_status",
    "dedicated_account_freshness",
    "equity_market_data_feed",
    "multi_leg_order_support",
    "option_market_data_feed",
    "option_trading_level",
    "paper_endpoint_class",
    "required_mcp_tools",
    "starting_balance",
)
_PROGRAMME_REASON_CODES = {
    "account_reset_assumptions": "ACCOUNT_RESET_UNVERIFIED",
    "drawdown_and_flatten_rules": "COMPETITION_FLATTEN_RULE_UNVERIFIED",
    "equity_data_entitlement": "DATA_ENTITLEMENT_UNVERIFIED",
    "leverage_rules": "COMPETITION_LEVERAGE_UNVERIFIED",
    "official_cost_treatment": "COMPETITION_COST_TREATMENT_UNVERIFIED",
    "official_mark_source": "COMPETITION_MARK_UNVERIFIED",
    "option_data_entitlement": "DATA_ENTITLEMENT_UNVERIFIED",
    "option_level_and_multileg_capability": "OPTION_LEVEL_UNVERIFIED",
}
_CAPABILITY_REASON_CODES = {
    "account_reset_state": "ACCOUNT_RESET_UNVERIFIED",
    "account_status": "ACCOUNT_CAPABILITY_UNVERIFIED",
    "dedicated_account_freshness": "DEDICATED_ACCOUNT_FRESHNESS_UNVERIFIED",
    "equity_market_data_feed": "DATA_ENTITLEMENT_UNVERIFIED",
    "multi_leg_order_support": "MULTILEG_CAPABILITY_UNVERIFIED",
    "option_market_data_feed": "DATA_ENTITLEMENT_UNVERIFIED",
    "option_trading_level": "OPTION_LEVEL_UNVERIFIED",
    "paper_endpoint_class": "PAPER_ENDPOINT_UNVERIFIED",
    "required_mcp_tools": "REQUIRED_TOOLS_UNVERIFIED",
    "starting_balance": "STARTING_BALANCE_UNVERIFIED",
}
_EXPECTED_CAPABILITY_VALUES = {
    "account_reset_state": "FRESH_NOT_RESET",
    "account_status": "ACTIVE",
    "dedicated_account_freshness": "FRESH_DEDICATED_ACCOUNT",
    "multi_leg_order_support": "SUPPORTED",
    "option_trading_level": "3",
    "paper_endpoint_class": "PAPER",
    "required_mcp_tools": (
        "cancel_order_by_id|get_account_info|get_all_positions|"
        "get_order_by_client_id|get_order_by_id|place_option_order"
    ),
    "starting_balance": "100000.00 USD",
}
_ALLOWED_CAPABILITY_VALUES = {
    "equity_market_data_feed": frozenset({"IEX", "SIP"}),
    "option_market_data_feed": frozenset({"INDICATIVE", "OPRA"}),
}
_CAPABILITY_DELEGATED_FACT_IDS = frozenset(
    {
        "account_reset_assumptions",
        "equity_data_entitlement",
        "option_data_entitlement",
        "option_level_and_multileg_capability",
    }
)
_PROGRAMME_CLAIMS = (
    "NO_BROKER_MUTATION",
    "NO_CREDENTIALS",
    "PAPER_ONLY",
    "SOURCE_GROUNDED",
)
_CAPABILITY_CLAIMS = (
    "NO_BROKER_MUTATION",
    "NO_CREDENTIALS",
    "NO_RAW_ACCOUNT_IDENTIFIER",
    "PAPER_ONLY",
)


class GateAContractError(ValueError):
    """Raised when Gate A bytes are malformed, mutable, or unsupported."""


class VerificationStatus(StrEnum):
    """Permitted evidence status for an external fact or observation."""

    VERIFIED = "VERIFIED"
    CONTRADICTORY = "CONTRADICTORY"
    INACCESSIBLE = "INACCESSIBLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EntryState(StrEnum):
    """Whether Gate A has enough truth to permit downstream entry."""

    ELIGIBLE = "ELIGIBLE"
    ENTRY_DISABLED = "ENTRY_DISABLED"


@dataclass(frozen=True, slots=True)
class ProgrammeFact:
    fact_id: str
    status: VerificationStatus
    value: str | None
    affects_entry: bool
    source_url: str
    exact_quote: str | None
    limitation: str | None


@dataclass(frozen=True, slots=True)
class ProgrammeContract:
    contract_id: str
    contract_version: int
    retrieved_at: datetime
    source_url: str
    snapshot_representation: str
    claim_labels: tuple[str, ...]
    facts: tuple[ProgrammeFact, ...]
    sha256: str

    @property
    def facts_by_id(self) -> Mapping[str, ProgrammeFact]:
        return MappingProxyType({fact.fact_id: fact for fact in self.facts})


@dataclass(frozen=True, slots=True)
class CapabilityObservation:
    capability_id: str
    status: VerificationStatus
    value: str | None
    evidence_sha256: str | None
    limitation: str | None


@dataclass(frozen=True, slots=True)
class CapabilityReceipt:
    receipt_id: str
    programme_contract_sha256: str
    producer_build_sha256: str
    observed_at: datetime
    expires_at: datetime
    adapter: str
    adapter_version: str
    adapter_commit: str
    account_fingerprint_sha256: str | None
    claim_labels: tuple[str, ...]
    observations: tuple[CapabilityObservation, ...]
    sha256: str

    @property
    def observations_by_id(self) -> Mapping[str, CapabilityObservation]:
        return MappingProxyType({item.capability_id: item for item in self.observations})


@dataclass(frozen=True, slots=True)
class CurrentCapabilityReceipt:
    """Closed V2 artifact/runtime attestation; deliberately has no adapter commit."""

    receipt_id: str
    programme_contract_sha256: str
    observed_at: datetime
    expires_at: datetime
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
    selected_schema_count: int
    selected_schema_sha256: str
    tool_names: tuple[str, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class GateADecision:
    programme_contract_sha256: str
    capability_receipt_sha256: str
    evaluated_at: datetime
    entry_state: EntryState
    reason_codes: tuple[str, ...]


class _DuplicateFieldError(ValueError):
    pass


def _reject(message: str) -> NoReturn:
    raise GateAContractError(message)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant {value}")


def _reject_float(value: str) -> NoReturn:
    raise ValueError(f"floating-point JSON literal {value} is forbidden")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _decode(raw: bytes, *, label: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(f"{label} must be immutable bytes")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except _DuplicateFieldError as error:
        _reject(f"{label} contains duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(f"{label} is not strict JSON: {error}")
    if not isinstance(value, Mapping):
        _reject(f"{label} root must be an object")
    if raw != _canonical_json_bytes(value):
        _reject(f"{label} must use canonical JSON bytes")
    return value


def _strict_object(value: object, *, path: str, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(f"{path} must be an object")
    actual = set(value)
    missing = sorted(fields - actual)
    unknown = sorted(actual - fields)
    if missing:
        _reject(f"{path} missing field {missing[0]}")
    if unknown:
        _reject(f"{path} has unknown field {unknown[0]}")
    return value


def _text(value: object, *, path: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(f"{path} must be normalized non-empty text")
    return value


def _capability_text(value: object, *, path: str, nullable: bool = False) -> str | None:
    text = _text(value, path=path, nullable=nullable)
    if text is not None and _SENSITIVE_TEXT.search(text):
        _reject(f"{path} contains forbidden secret or raw account-identifier text")
    return text


def _identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _reject(f"{path} must be an uppercase stable identifier")
    return value


def _lower_identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _LOWER_IDENTIFIER.fullmatch(value) is None:
        _reject(f"{path} must be a lowercase stable identifier")
    return value


def _sha256(value: object, *, path: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(f"{path} must be a lowercase SHA-256")
    return value


def _git_commit(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _GIT_COMMIT.fullmatch(value) is None:
        _reject(f"{path} must be a full lowercase Git commit")
    return value


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        _reject(f"{path} must be an explicit UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00").astimezone(UTC)
    except ValueError as error:
        _reject(f"{path} is invalid: {error}")
    if _timestamp_text(parsed) != value:
        _reject(f"{path} is not canonical")
    return parsed


def _url(value: object, *, path: str) -> str:
    text = _text(value, path=path)
    assert text is not None
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        _reject(f"{path} must be a public HTTPS URL without credentials")
    return text


def _status(value: object, *, path: str) -> VerificationStatus:
    if not isinstance(value, str):
        _reject(f"{path} must be a verification status")
    try:
        return VerificationStatus(value)
    except ValueError:
        _reject(f"{path} has unknown status {value}")


def _string_array(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _reject(f"{path} must be an array")
    items = tuple(_identifier(item, path=f"{path}[]") for item in value)
    if items != tuple(sorted(set(items))):
        _reject(f"{path} must be sorted and unique")
    return items


def _validate_evidence_state(
    *,
    status: VerificationStatus,
    value: str | None,
    proof: str | None,
    limitation: str | None,
    path: str,
) -> None:
    if status is VerificationStatus.VERIFIED:
        if value is None or proof is None or limitation is not None:
            _reject(f"{path} VERIFIED state requires value/proof and no limitation")
    elif value is not None or limitation is None:
        _reject(f"{path} non-VERIFIED state requires null value and a limitation")


def _parse_programme_fact(value: object, *, path: str) -> ProgrammeFact:
    payload = _strict_object(value, path=path, fields=_FACT_FIELDS)
    status = _status(payload["status"], path=f"{path}.status")
    fact_value = _text(payload["value"], path=f"{path}.value", nullable=True)
    exact_quote = _text(payload["exact_quote"], path=f"{path}.exact_quote", nullable=True)
    limitation = _text(payload["limitation"], path=f"{path}.limitation", nullable=True)
    _validate_evidence_state(
        status=status,
        value=fact_value,
        proof=exact_quote,
        limitation=limitation,
        path=path,
    )
    affects_entry = payload["affects_entry"]
    if type(affects_entry) is not bool:
        _reject(f"{path}.affects_entry must be a boolean")
    return ProgrammeFact(
        fact_id=_lower_identifier(payload["fact_id"], path=f"{path}.fact_id"),
        status=status,
        value=fact_value,
        affects_entry=affects_entry,
        source_url=_url(payload["source_url"], path=f"{path}.source_url"),
        exact_quote=exact_quote,
        limitation=limitation,
    )


def programme_contract_bytes() -> bytes:
    """Return exact packaged organizer-contract bytes."""

    return resources.files("esscher.contracts").joinpath(PROGRAMME_RESOURCE).read_bytes()


def parse_programme_contract(raw: bytes) -> ProgrammeContract:
    """Parse and authenticate the one registered Gate A organizer contract."""

    digest = hashlib.sha256(raw).hexdigest() if type(raw) is bytes else ""
    payload = _strict_object(
        _decode(raw, label="programme contract"), path="$", fields=_PROGRAMME_FIELDS
    )
    if (
        payload["schema"] != PROGRAMME_SCHEMA
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        _reject("programme contract schema/version is unsupported")
    if payload["contract_version"] != 1 or isinstance(payload["contract_version"], bool):
        _reject("programme contract version is unsupported")
    facts_value = payload["facts"]
    if not isinstance(facts_value, list):
        _reject("$.facts must be an array")
    facts = tuple(
        _parse_programme_fact(item, path=f"$.facts[{index}]")
        for index, item in enumerate(facts_value)
    )
    if tuple(item.fact_id for item in facts) != _PROGRAMME_FACT_IDS:
        _reject("programme contract does not contain the registered ordered fact set")
    claims = _string_array(payload["claim_labels"], path="$.claim_labels")
    if claims != _PROGRAMME_CLAIMS:
        _reject("programme contract claim labels do not match the registered boundary")
    if digest != PROGRAMME_CONTRACT_SHA256:
        _reject("programme contract bytes do not match the registered digest")
    return ProgrammeContract(
        contract_id=_identifier(payload["contract_id"], path="$.contract_id"),
        contract_version=1,
        retrieved_at=_timestamp(payload["retrieved_at"], path="$.retrieved_at"),
        source_url=_url(payload["source_url"], path="$.source_url"),
        snapshot_representation=_identifier(
            payload["snapshot_representation"], path="$.snapshot_representation"
        ),
        claim_labels=claims,
        facts=facts,
        sha256=digest,
    )


def load_programme_contract() -> ProgrammeContract:
    """Load the exact packaged Gate A organizer contract."""

    return parse_programme_contract(programme_contract_bytes())


def _parse_capability_observation(value: object, *, path: str) -> CapabilityObservation:
    payload = _strict_object(value, path=path, fields=_OBSERVATION_FIELDS)
    status = _status(payload["status"], path=f"{path}.status")
    item_value = _capability_text(payload["value"], path=f"{path}.value", nullable=True)
    evidence_sha256 = _sha256(
        payload["evidence_sha256"], path=f"{path}.evidence_sha256", nullable=True
    )
    limitation = _capability_text(payload["limitation"], path=f"{path}.limitation", nullable=True)
    _validate_evidence_state(
        status=status,
        value=item_value,
        proof=evidence_sha256,
        limitation=limitation,
        path=path,
    )
    return CapabilityObservation(
        capability_id=_lower_identifier(payload["capability_id"], path=f"{path}.capability_id"),
        status=status,
        value=item_value,
        evidence_sha256=evidence_sha256,
        limitation=limitation,
    )


def parse_current_capability_receipt(raw: bytes) -> CurrentCapabilityReceipt:
    """Parse the exact V2 current-session artifact attestation."""
    digest = hashlib.sha256(raw).hexdigest() if type(raw) is bytes else ""
    payload = _strict_object(
        _decode(raw, label="current capability receipt"), path="$", fields=_CAPABILITY_V2_FIELDS
    )
    if (
        payload["schema"] != CAPABILITY_SCHEMA
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 2
    ):
        _reject("current capability receipt schema/version is unsupported")
    expected = {
        "environment": "PAPER",
        "adapter": "ALPACA_MCP",
        "adapter_version": ALPACA_MCP_V2_VERSION,
        "distribution_type": ALPACA_MCP_V2_DISTRIBUTION_TYPE,
        "wheel_filename": ALPACA_MCP_V2_WHEEL_FILENAME,
        "wheel_sha256": ALPACA_MCP_V2_WHEEL_SHA256,
        "sdist_filename": ALPACA_MCP_V2_SDIST_FILENAME,
        "sdist_sha256": ALPACA_MCP_V2_SDIST_SHA256,
        "provenance_class": ALPACA_MCP_V2_PROVENANCE,
        "source_equivalent_version": ALPACA_MCP_V2_SOURCE_EQUIVALENT_VERSION,
        "source_equivalent_commit": ALPACA_MCP_V2_SOURCE_EQUIVALENT_COMMIT,
        "fastmcp_version": ALPACA_MCP_V2_FASTMCP_VERSION,
        "fastmcp_spec": ALPACA_MCP_V2_FASTMCP_SPEC,
        "discovered_tool_count": ALPACA_MCP_V2_DISCOVERED_TOOL_COUNT,
        "selected_schema_count": ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT,
        "selected_schema_sha256": ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256,
    }
    for field, value in expected.items():
        if payload[field] != value:
            _reject(f"current capability receipt has unsupported {field}")
    tools = payload["tool_names"]
    if not isinstance(tools, list) or tuple(tools) != _V2_TOOLS:
        _reject("current capability receipt toolset does not match the PAPER lifecycle")
    observed_at = _timestamp(payload["observed_at"], path="$.observed_at")
    expires_at = _timestamp(payload["expires_at"], path="$.expires_at")
    if expires_at <= observed_at:
        _reject("current capability receipt must expire after observation")
    return CurrentCapabilityReceipt(
        receipt_id=_identifier(payload["receipt_id"], path="$.receipt_id"),
        programme_contract_sha256=_sha256(
            payload["programme_contract_sha256"], path="$.programme_contract_sha256"
        ),
        observed_at=observed_at,
        expires_at=expires_at,
        environment=payload["environment"],
        adapter=payload["adapter"],
        adapter_version=payload["adapter_version"],
        distribution_type=payload["distribution_type"],
        wheel_filename=payload["wheel_filename"],
        wheel_sha256=payload["wheel_sha256"],
        sdist_filename=payload["sdist_filename"],
        sdist_sha256=payload["sdist_sha256"],
        provenance_class=payload["provenance_class"],
        source_equivalent_version=payload["source_equivalent_version"],
        source_equivalent_commit=payload["source_equivalent_commit"],
        fastmcp_version=payload["fastmcp_version"],
        fastmcp_spec=payload["fastmcp_spec"],
        discovered_tool_count=payload["discovered_tool_count"],
        selected_schema_count=payload["selected_schema_count"],
        selected_schema_sha256=payload["selected_schema_sha256"],
        tool_names=tuple(tools),
        sha256=digest,
    )


def capability_v2_receipt_bytes(value: CurrentCapabilityReceipt) -> bytes:
    """Serialize a V2 receipt canonically; no derived or Git-commit claim is added."""
    return _canonical_json_bytes(
        {
            key: getattr(value, key)
            for key in _CAPABILITY_V2_FIELDS
            if key != "schema" and key != "schema_version"
        }
        | {"schema": CAPABILITY_SCHEMA, "schema_version": 2}
    )


parse_current_capability_receipt.__doc__ = (
    "Parse the exact V2 current-session artifact attestation."
)


def parse_capability_receipt(raw: bytes) -> CapabilityReceipt | CurrentCapabilityReceipt:
    """Parse V1 history or V2 current capability receipts."""
    if type(raw) is bytes:
        try:
            peek = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            peek = None
        if isinstance(peek, Mapping) and peek.get("schema_version") == 2:
            return parse_current_capability_receipt(raw)
    return _parse_v1_capability_receipt(raw)


def _parse_v1_capability_receipt(raw: bytes) -> CapabilityReceipt:
    """Strictly parse a canonical sanitized account-capability receipt."""

    digest = hashlib.sha256(raw).hexdigest() if type(raw) is bytes else ""
    payload = _strict_object(
        _decode(raw, label="capability receipt"), path="$", fields=_CAPABILITY_FIELDS
    )
    if (
        payload["schema"] != CAPABILITY_SCHEMA
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        _reject("capability receipt schema/version is unsupported")
    observations_value = payload["observations"]
    if not isinstance(observations_value, list):
        _reject("$.observations must be an array")
    observations = tuple(
        _parse_capability_observation(item, path=f"$.observations[{index}]")
        for index, item in enumerate(observations_value)
    )
    if tuple(item.capability_id for item in observations) != _CAPABILITY_IDS:
        _reject("capability receipt does not contain the registered ordered observation set")
    for item in observations:
        if item.status is not VerificationStatus.VERIFIED:
            continue
        expected = _EXPECTED_CAPABILITY_VALUES.get(item.capability_id)
        allowed = _ALLOWED_CAPABILITY_VALUES.get(item.capability_id)
        if expected is not None and item.value != expected:
            _reject(f"capability {item.capability_id} has unsupported verified value")
        if allowed is not None and item.value not in allowed:
            _reject(f"capability {item.capability_id} has unsupported verified value")
    claims = _string_array(payload["claim_labels"], path="$.claim_labels")
    if claims != _CAPABILITY_CLAIMS:
        _reject("capability receipt claim labels do not match the registered boundary")
    observed_at = _timestamp(payload["observed_at"], path="$.observed_at")
    expires_at = _timestamp(payload["expires_at"], path="$.expires_at")
    if expires_at <= observed_at:
        _reject("capability receipt must expire after observation")
    adapter = _text(payload["adapter"], path="$.adapter")
    adapter_version = _text(payload["adapter_version"], path="$.adapter_version")
    adapter_commit = _git_commit(payload["adapter_commit"], path="$.adapter_commit")
    if (
        adapter != "ALPACA_MCP"
        or adapter_version != ALPACA_MCP_VERSION
        or adapter_commit != ALPACA_MCP_COMMIT
    ):
        _reject("capability receipt does not bind the pinned Alpaca MCP protocol")
    account_fingerprint = _sha256(
        payload["account_fingerprint_sha256"],
        path="$.account_fingerprint_sha256",
        nullable=True,
    )
    if (
        any(item.status is VerificationStatus.VERIFIED for item in observations)
        and account_fingerprint is None
    ):
        _reject("verified account capabilities require a sanitized account fingerprint")
    return CapabilityReceipt(
        receipt_id=_identifier(payload["receipt_id"], path="$.receipt_id"),
        programme_contract_sha256=_sha256(
            payload["programme_contract_sha256"], path="$.programme_contract_sha256"
        ),
        producer_build_sha256=_sha256(
            payload["producer_build_sha256"], path="$.producer_build_sha256"
        ),
        observed_at=observed_at,
        expires_at=expires_at,
        adapter=adapter,
        adapter_version=adapter_version,
        adapter_commit=adapter_commit,
        account_fingerprint_sha256=account_fingerprint,
        claim_labels=claims,
        observations=observations,
        sha256=digest,
    )


def capability_receipt_bytes(value: CapabilityReceipt) -> bytes:
    """Serialize a capability receipt to canonical bytes without its derived digest."""

    return _canonical_json_bytes(
        {
            "account_fingerprint_sha256": value.account_fingerprint_sha256,
            "adapter": value.adapter,
            "adapter_commit": value.adapter_commit,
            "adapter_version": value.adapter_version,
            "claim_labels": list(value.claim_labels),
            "expires_at": _timestamp_text(value.expires_at),
            "observations": [
                {
                    "capability_id": item.capability_id,
                    "evidence_sha256": item.evidence_sha256,
                    "limitation": item.limitation,
                    "status": item.status.value,
                    "value": item.value,
                }
                for item in value.observations
            ],
            "observed_at": _timestamp_text(value.observed_at),
            "producer_build_sha256": value.producer_build_sha256,
            "programme_contract_sha256": value.programme_contract_sha256,
            "receipt_id": value.receipt_id,
            "schema": CAPABILITY_SCHEMA,
            "schema_version": SCHEMA_VERSION,
        }
    )


def _timestamp_text(value: datetime) -> str:
    text = value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if text.endswith(".000000Z"):
        return text.replace(".000000Z", "Z")
    prefix, fraction = text[:-1].split(".", maxsplit=1)
    return f"{prefix}.{fraction.rstrip('0')}Z"


def evaluate_gate_a(
    programme: ProgrammeContract,
    capability: CapabilityReceipt | CurrentCapabilityReceipt,
    *,
    evaluated_at: datetime,
    approved_producer_build_sha256: str | None = None,
    capability_evidence: Mapping[str, bytes] | None = None,
) -> GateADecision:
    """Return entry eligibility without reading broker, account, or wall-clock state."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise GateAContractError("evaluated_at must be timezone-aware")
    evaluated_at = evaluated_at.astimezone(UTC)
    reasons: set[str] = set()
    registered_programme = load_programme_contract()
    if programme != registered_programme:
        reasons.add("PROGRAMME_CONTRACT_MISMATCH")
    programme = registered_programme
    try:
        if isinstance(capability, CurrentCapabilityReceipt):
            authenticated_capability = parse_current_capability_receipt(
                capability_v2_receipt_bytes(capability)
            )
        else:
            authenticated_capability = parse_capability_receipt(
                capability_receipt_bytes(capability)
            )
    except (AttributeError, GateAContractError, TypeError, ValueError):
        authenticated_capability = None
        reasons.add("CAPABILITY_RECEIPT_INVALID")
    if authenticated_capability is None:
        return GateADecision(
            programme_contract_sha256=programme.sha256,
            capability_receipt_sha256=capability.sha256,
            evaluated_at=evaluated_at,
            entry_state=EntryState.ENTRY_DISABLED,
            reason_codes=tuple(sorted(reasons)),
        )
    if authenticated_capability != capability:
        reasons.add("CAPABILITY_RECEIPT_INVALID")
    capability = authenticated_capability

    if capability.programme_contract_sha256 != programme.sha256:
        reasons.add("PROGRAMME_CONTRACT_MISMATCH")
    if programme.retrieved_at > evaluated_at:
        reasons.add("PROGRAMME_CONTRACT_FROM_FUTURE")
    if capability.observed_at > evaluated_at:
        reasons.add("CAPABILITY_RECEIPT_FROM_FUTURE")
    if evaluated_at >= capability.expires_at:
        reasons.add("CAPABILITY_RECEIPT_STALE")
    verified_observations = (
        tuple(
            item for item in capability.observations if item.status is VerificationStatus.VERIFIED
        )
        if isinstance(capability, CapabilityReceipt)
        else ()
    )
    if verified_observations:
        if (
            approved_producer_build_sha256 is None
            or _SHA256.fullmatch(approved_producer_build_sha256) is None
            or approved_producer_build_sha256 != capability.producer_build_sha256
        ):
            reasons.add("CAPABILITY_PRODUCER_UNAUTHORIZED")
        supplied_evidence = capability_evidence or {}
        for item in verified_observations:
            evidence = supplied_evidence.get(item.evidence_sha256 or "")
            if (
                type(evidence) is not bytes
                or hashlib.sha256(evidence).hexdigest() != item.evidence_sha256
            ):
                reasons.add("CAPABILITY_EVIDENCE_UNAVAILABLE")
    for fact in programme.facts:
        if (
            fact.affects_entry
            and fact.fact_id not in _CAPABILITY_DELEGATED_FACT_IDS
            and fact.status is not VerificationStatus.VERIFIED
        ):
            reasons.add(_PROGRAMME_REASON_CODES.get(fact.fact_id, "PROGRAMME_FACT_UNVERIFIED"))
    if isinstance(capability, CapabilityReceipt):
        for item in capability.observations:
            if item.status is not VerificationStatus.VERIFIED:
                reasons.add(_CAPABILITY_REASON_CODES[item.capability_id])
                continue
            expected = _EXPECTED_CAPABILITY_VALUES.get(item.capability_id)
            if expected is not None and item.value != expected:
                reasons.add(_CAPABILITY_REASON_CODES[item.capability_id])
            allowed = _ALLOWED_CAPABILITY_VALUES.get(item.capability_id)
            if allowed is not None and item.value not in allowed:
                reasons.add(_CAPABILITY_REASON_CODES[item.capability_id])
    entry_state = EntryState.ELIGIBLE if not reasons else EntryState.ENTRY_DISABLED
    return GateADecision(
        programme_contract_sha256=programme.sha256,
        capability_receipt_sha256=capability.sha256,
        evaluated_at=evaluated_at,
        entry_state=entry_state,
        reason_codes=tuple(sorted(reasons)),
    )
