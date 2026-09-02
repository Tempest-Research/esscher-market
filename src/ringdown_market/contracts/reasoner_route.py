"""Frozen direct Moonshot Kimi K3 route contract and approval receipt.

The descriptor binds the one direct ``platform.kimi.ai``-compatible request
boundary: exact provider/model/base URL, one-call timeout policy, strict output
schema, omitted request parameters, and provider-fixed effective sampling.  It
never carries a credential, account, broker, order, or secret-bearing
application argument.  Owner selection and approval are distinct from V1
operational readiness: V1 remains fail-closed while its frozen caller decoding
cannot truthfully represent K3's provider-fixed sampling.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import NoReturn

from ..strategy.contracts import (
    canonical_json_bytes,
    reasoner_output_schema_sha256,
    reasoner_output_schema_v2_sha256,
    sha256_bytes,
)
from ..strategy.models import DecodingParameters
from ..strategy.policy import (
    StrategyPolicy,
    load_strategy_policy,
    load_strategy_policy_v2,
    strategy_policy_sha256,
    strategy_policy_v2_sha256,
)

ROUTE_SCHEMA = "esscher.reasoner_route"
APPROVAL_SCHEMA = "esscher.reasoner_route_approval"
SCHEMA_VERSION = 1

ADAPTER_KIND = "OPENAI_COMPATIBLE_CHAT_V1"
ROUTE_ID = "ESSCHER_BOUNDED_REASONER_ROUTE_V1"
DIRECT_PROVIDER = "moonshot_direct"
DIRECT_BASE_URL = "https://api.moonshot.ai/v1"
DIRECT_MODEL = "kimi-k3"
KIMI_REASONING_EFFORT = "low"
KIMI_MAX_COMPLETION_TOKENS = 512
KIMI_TOOL_CHOICE = "none"
KIMI_RESPONSE_FORMAT_TYPE = "json_schema"
KIMI_RESPONSE_SCHEMA_NAME = "esscher_reasoner_decision_v1"
KIMI_EFFECTIVE_TEMPERATURE = "1.0"
KIMI_EFFECTIVE_TOP_P = "0.95"
KIMI_OMITTED_REQUEST_FIELDS = (
    "temperature",
    "top_p",
    "seed",
    "max_tokens",
    "n",
    "presence_penalty",
    "frequency_penalty",
    "tools",
)

CLAIM_LABELS = (
    "NO_BROKER_AUTHORITY",
    "NO_ACCOUNT_AUTHORITY",
    "NO_SECRET_ARGUMENTS",
    "PAPER_ONLY",
)
APPROVAL_ID = "ESSCHER_ROUTE_APPROVAL_V1"
APPROVAL_SCOPE = "ESSCHER_V1_STRATEGY_EVALUATION"
APPROVAL_CLAUSES = (
    "The owner selected this exact direct Moonshot/Kimi K3 route; that approval does not make "
    "the V1 route operationally eligible.",
    "Each paid or live reasoner probe receives separate current approval before execution.",
    "The reasoner route cannot receive broker/account authority or secret-bearing "
    "application arguments.",
)

# V2 retains the direct provider boundary but truthfully represents K3's
# provider-fixed decoding in the caller/model identity. Its artifacts are
# deliberately separate so V1 remains loadable, immutable, and inert.
ROUTE_V2_SCHEMA_VERSION = 2
ROUTE_ID_V2 = "ESSCHER_BOUNDED_REASONER_ROUTE_V2"
KIMI_RESPONSE_SCHEMA_NAME_V2 = "esscher_reasoner_decision_v2"
APPROVAL_ID_V2 = "ESSCHER_ROUTE_APPROVAL_V2"
APPROVAL_SCOPE_V2 = "ESSCHER_V2_STRATEGY_EVALUATION"
APPROVAL_CLAUSES_V2 = (
    "The owner selected this exact direct Moonshot/Kimi K3 V2 route for prospective "
    "strategy evaluation; the route has no broker, account, order, sizing, or secret authority.",
    "Each paid or live reasoner probe receives separate current approval before execution.",
    "The route accepts only the exact packaged V2 descriptor and approval bytes.",
)

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "access_token",
        "auth",
        "authorization",
        "password",
        "private_key",
        "credential",
    }
)


class ApprovalState(StrEnum):
    """Owner approval state for a frozen reasoner route."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"


class RouteCompatibilityState(StrEnum):
    """Whether the selected provider semantics fit the accepted V1 event policy."""

    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"


class RouteCompatibilityReason(StrEnum):
    """Stable non-operational reason codes for a validated selected route."""

    FROZEN_POLICY_DECODING_INCOMPATIBLE = "FROZEN_POLICY_DECODING_INCOMPATIBLE"


class RouteContractReason(StrEnum):
    """Stable machine-readable reasons for rejecting route artifacts."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    HASH_MISMATCH = "HASH_MISMATCH"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    AUTHORITY_VIOLATION = "AUTHORITY_VIOLATION"
    SECRET_ARGUMENT = "SECRET_ARGUMENT"
    APPROVAL_MISSING = "APPROVAL_MISSING"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVAL_REVOKED = "APPROVAL_REVOKED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


class RouteContractRejected(ValueError):
    """A deterministic validation failure for reasoner route artifacts."""

    def __init__(self, reason: RouteContractReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


@dataclass(frozen=True, slots=True)
class ProviderRequestPolicy:
    """Typed direct Kimi request settings bound by the frozen descriptor."""

    reasoning_effort: str
    max_completion_tokens: int
    response_format_type: str
    output_schema_name: str
    output_schema_sha256: str
    strict_json_schema: bool
    tool_choice: str
    effective_temperature: str
    effective_top_p: str
    omitted_request_fields: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        """Return the exact semantic object included in the model-config hash."""

        return {
            "effective_decoding": {
                "temperature": self.effective_temperature,
                "top_p": self.effective_top_p,
            },
            "max_completion_tokens": self.max_completion_tokens,
            "omitted_request_fields": list(self.omitted_request_fields),
            "reasoning_effort": self.reasoning_effort,
            "response_format": {
                "json_schema": {
                    "name": self.output_schema_name,
                    "schema_sha256": self.output_schema_sha256,
                    "strict": self.strict_json_schema,
                },
                "type": self.response_format_type,
            },
            "tool_choice": self.tool_choice,
        }


@dataclass(frozen=True, slots=True)
class ValidatedRoute:
    """A validated selected route, including its compatibility and approval state."""

    route_id: str
    provider: str
    model: str
    model_revision: str | None
    adapter_kind: str
    base_url: str
    caller_decoding: DecodingParameters
    provider_request_policy: ProviderRequestPolicy
    route_sha256: str
    model_config_sha256: str
    approval_state: ApprovalState
    approver: str | None
    approved_at: datetime | None
    compatibility_state: RouteCompatibilityState
    compatibility_reason_code: str | None
    evaluation_eligible: bool


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    """Closed, version-specific descriptor and approval expectations."""

    schema_version: int
    route_id: str
    approval_id: str
    approval_scope: str
    approval_clauses: tuple[str, ...]
    policy_loader: Callable[[], StrategyPolicy]
    policy_sha256_loader: Callable[[], str]
    output_schema_name: str
    output_schema_sha256_loader: Callable[[], str]
    caller_temperature: Decimal | None
    caller_top_p: Decimal
    caller_seed: int | None


_V1_ROUTE_SPEC = _RouteSpec(
    schema_version=SCHEMA_VERSION,
    route_id=ROUTE_ID,
    approval_id=APPROVAL_ID,
    approval_scope=APPROVAL_SCOPE,
    approval_clauses=APPROVAL_CLAUSES,
    policy_loader=load_strategy_policy,
    policy_sha256_loader=strategy_policy_sha256,
    output_schema_name=KIMI_RESPONSE_SCHEMA_NAME,
    output_schema_sha256_loader=reasoner_output_schema_sha256,
    caller_temperature=None,
    caller_top_p=Decimal("1"),
    caller_seed=7,
)
_V2_ROUTE_SPEC = _RouteSpec(
    schema_version=ROUTE_V2_SCHEMA_VERSION,
    route_id=ROUTE_ID_V2,
    approval_id=APPROVAL_ID_V2,
    approval_scope=APPROVAL_SCOPE_V2,
    approval_clauses=APPROVAL_CLAUSES_V2,
    policy_loader=load_strategy_policy_v2,
    policy_sha256_loader=strategy_policy_v2_sha256,
    output_schema_name=KIMI_RESPONSE_SCHEMA_NAME_V2,
    output_schema_sha256_loader=reasoner_output_schema_v2_sha256,
    caller_temperature=Decimal(KIMI_EFFECTIVE_TEMPERATURE),
    caller_top_p=Decimal(KIMI_EFFECTIVE_TOP_P),
    caller_seed=None,
)


class _DuplicateFieldError(ValueError):
    pass


def _reject(reason: RouteContractReason, path: str, detail: str) -> NoReturn:
    raise RouteContractRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _decode(raw: bytes, *, path: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "artifacts must be bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except _DuplicateFieldError as error:
        _reject(RouteContractReason.DUPLICATE_FIELD, path, f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(RouteContractReason.INVALID_DOCUMENT, path, str(error))
    if not isinstance(value, Mapping):
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "root must be an object")
    return value


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(
            RouteContractReason.MISSING_FIELD,
            f"{path}.{missing[0]}",
            "required field is missing",
        )
    unknown = sorted(keys - fields)
    if unknown:
        _reject(
            RouteContractReason.UNKNOWN_FIELD,
            f"{path}.{unknown[0]}",
            "field is not part of the frozen schema",
        )
    return value


def _text(value: object, *, path: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "must be non-empty text")
    return value


def _boolean_false(value: object, *, path: str) -> None:
    if value is not False:
        _reject(
            RouteContractReason.AUTHORITY_VIOLATION,
            path,
            "the reasoner route must deny this authority",
        )


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


def _timestamp(value: object, *, path: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        _reject(RouteContractReason.INVALID_DOCUMENT, path, str(error))
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "must be UTC")
    return parsed


def _sha256(value: object, *, path: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "must be lowercase SHA-256")
    return value


def _integer(value: object, *, path: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        _reject(
            RouteContractReason.INVALID_DOCUMENT,
            path,
            f"must be an integer of at least {minimum}",
        )
    return value


def _decoding_from(value: object, *, path: str) -> DecodingParameters:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset({"temperature", "top_p", "max_output_tokens", "seed"}),
    )
    try:
        temperature = Decimal(str(payload["temperature"]))
        top_p = Decimal(str(payload["top_p"]))
    except InvalidOperation:
        _reject(
            RouteContractReason.INVALID_DOCUMENT, path, "decoding values must be finite decimals"
        )
    if not temperature.is_finite() or not top_p.is_finite():
        _reject(
            RouteContractReason.INVALID_DOCUMENT, path, "decoding values must be finite decimals"
        )
    return DecodingParameters(
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=_integer(
            payload["max_output_tokens"], path=f"{path}.max_output_tokens", minimum=1
        ),
        seed=_integer(payload["seed"], path=f"{path}.seed", minimum=0)
        if payload["seed"] is not None
        else None,
    )


def _caller_decoding_payload(value: DecodingParameters) -> dict[str, object]:
    return {
        "max_output_tokens": value.max_output_tokens,
        "seed": value.seed,
        "temperature": format(value.temperature, "f"),
        "top_p": format(value.top_p, "f"),
    }


def direct_kimi_model_config_sha256(
    *,
    provider: str,
    model: str,
    model_revision: str | None,
    base_url: str,
    caller_decoding: DecodingParameters,
    provider_request_policy: ProviderRequestPolicy,
    schema_version: int = SCHEMA_VERSION,
) -> str:
    """Hash direct Kimi identity plus effective provider request semantics.

    This intentionally differs from the legacy V1 generic model-config hash:
    the direct route binds base URL, strict schema, reasoning effort, tool
    choice, omitted fields, and provider-fixed effective temperature/top-p.
    """

    return sha256_bytes(
        canonical_json_bytes(
            {
                "base_url": base_url,
                "caller_decoding": _caller_decoding_payload(caller_decoding),
                "model": model,
                "model_revision": model_revision,
                "provider": provider,
                "provider_request_policy": provider_request_policy.payload(),
                "schema": "esscher.direct_kimi_reasoner_model_config",
                "schema_version": schema_version,
            }
        )
    )


_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "route_id",
        "adapter_kind",
        "provider",
        "model",
        "model_revision",
        "base_url",
        "decoding",
        "provider_request_policy",
        "call_policy",
        "candidate_ids",
        "authority",
        "application_arguments",
        "cost_ceiling",
        "claim_labels",
        "policy_sha256",
    }
)
_AUTHORITY_FIELDS = frozenset({"broker", "account", "secret_arguments"})
_CALL_POLICY_FIELDS = frozenset({"hard_timeout_seconds", "max_calls", "retry_count"})
_COST_FIELDS = frozenset(
    {
        "max_output_tokens_per_call",
        "probe_budget_calls",
        "paid_provider_purchase",
        "entitlement_note",
    }
)
_PROVIDER_REQUEST_POLICY_FIELDS = frozenset(
    {
        "reasoning_effort",
        "max_completion_tokens",
        "response_format",
        "tool_choice",
        "effective_decoding",
        "omitted_request_fields",
    }
)
_RESPONSE_FORMAT_FIELDS = frozenset({"type", "json_schema"})
_RESPONSE_JSON_SCHEMA_FIELDS = frozenset({"name", "strict", "schema_sha256"})
_EFFECTIVE_DECODING_FIELDS = frozenset({"temperature", "top_p"})
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "approval_id",
        "approval_state",
        "approver",
        "approved_at",
        "provider",
        "model",
        "model_revision",
        "adapter_kind",
        "base_url",
        "route_sha256",
        "model_config_sha256",
        "cost_ceiling",
        "scope",
        "clauses",
    }
)


def _provider_request_policy_from(
    value: object,
    *,
    path: str,
    spec: _RouteSpec,
) -> ProviderRequestPolicy:
    payload = _strict_object(value, path=path, fields=_PROVIDER_REQUEST_POLICY_FIELDS)
    reasoning_effort = _text(payload["reasoning_effort"], path=f"{path}.reasoning_effort")
    max_completion_tokens = _integer(
        payload["max_completion_tokens"], path=f"{path}.max_completion_tokens", minimum=1
    )
    response_format = _strict_object(
        payload["response_format"],
        path=f"{path}.response_format",
        fields=_RESPONSE_FORMAT_FIELDS,
    )
    response_format_type = _text(response_format["type"], path=f"{path}.response_format.type")
    json_schema = _strict_object(
        response_format["json_schema"],
        path=f"{path}.response_format.json_schema",
        fields=_RESPONSE_JSON_SCHEMA_FIELDS,
    )
    output_schema_name = _text(json_schema["name"], path=f"{path}.response_format.json_schema.name")
    strict = json_schema["strict"]
    if type(strict) is not bool:
        _reject(
            RouteContractReason.INVALID_DOCUMENT,
            f"{path}.response_format.json_schema.strict",
            "must be boolean",
        )
    output_schema_sha256 = _sha256(
        json_schema["schema_sha256"], path=f"{path}.response_format.json_schema.schema_sha256"
    )
    tool_choice = _text(payload["tool_choice"], path=f"{path}.tool_choice")
    effective_decoding = _strict_object(
        payload["effective_decoding"],
        path=f"{path}.effective_decoding",
        fields=_EFFECTIVE_DECODING_FIELDS,
    )
    effective_temperature = _text(
        effective_decoding["temperature"], path=f"{path}.effective_decoding.temperature"
    )
    effective_top_p = _text(effective_decoding["top_p"], path=f"{path}.effective_decoding.top_p")
    omitted = payload["omitted_request_fields"]
    if not isinstance(omitted, list) or not all(isinstance(item, str) for item in omitted):
        _reject(
            RouteContractReason.INVALID_DOCUMENT,
            f"{path}.omitted_request_fields",
            "must be a string list",
        )
    omitted_request_fields = tuple(omitted)

    expected = {
        "reasoning_effort": KIMI_REASONING_EFFORT,
        "max_completion_tokens": KIMI_MAX_COMPLETION_TOKENS,
        "response_format_type": KIMI_RESPONSE_FORMAT_TYPE,
        "output_schema_name": spec.output_schema_name,
        "output_schema_sha256": spec.output_schema_sha256_loader(),
        "strict": True,
        "tool_choice": KIMI_TOOL_CHOICE,
        "effective_temperature": KIMI_EFFECTIVE_TEMPERATURE,
        "effective_top_p": KIMI_EFFECTIVE_TOP_P,
        "omitted_request_fields": KIMI_OMITTED_REQUEST_FIELDS,
    }
    actual = {
        "reasoning_effort": reasoning_effort,
        "max_completion_tokens": max_completion_tokens,
        "response_format_type": response_format_type,
        "output_schema_name": output_schema_name,
        "output_schema_sha256": output_schema_sha256,
        "strict": strict,
        "tool_choice": tool_choice,
        "effective_temperature": effective_temperature,
        "effective_top_p": effective_top_p,
        "omitted_request_fields": omitted_request_fields,
    }
    for field, expected_value in expected.items():
        if actual[field] != expected_value:
            _reject(
                RouteContractReason.POLICY_MISMATCH,
                f"{path}.{field}",
                "provider request policy differs from the frozen direct Kimi contract",
            )
    return ProviderRequestPolicy(
        reasoning_effort=reasoning_effort,
        max_completion_tokens=max_completion_tokens,
        response_format_type=response_format_type,
        output_schema_name=output_schema_name,
        output_schema_sha256=output_schema_sha256,
        strict_json_schema=strict,
        tool_choice=tool_choice,
        effective_temperature=effective_temperature,
        effective_top_p=effective_top_p,
        omitted_request_fields=omitted_request_fields,
    )


def _compatibility(
    *,
    caller_decoding: DecodingParameters,
    provider_request_policy: ProviderRequestPolicy,
) -> tuple[RouteCompatibilityState, RouteCompatibilityReason | None]:
    """Return the V1 compatibility state without manufacturing a compatible route."""

    effective_temperature = Decimal(provider_request_policy.effective_temperature)
    effective_top_p = Decimal(provider_request_policy.effective_top_p)
    if (
        caller_decoding.temperature != effective_temperature
        or caller_decoding.top_p != effective_top_p
    ):
        return (
            RouteCompatibilityState.INCOMPATIBLE,
            RouteCompatibilityReason.FROZEN_POLICY_DECODING_INCOMPATIBLE,
        )
    return RouteCompatibilityState.COMPATIBLE, None


def route_descriptor_bytes(payload: Mapping[str, object]) -> bytes:
    """Serialize a route descriptor canonically."""

    return canonical_json_bytes(payload)


def _validate_reasoner_route(
    descriptor_bytes: bytes,
    receipt_bytes: bytes,
    *,
    spec: _RouteSpec,
) -> ValidatedRoute:
    """Validate one versioned direct-Kimi descriptor against its approval receipt."""

    descriptor = _strict_object(
        _decode(descriptor_bytes, path="route_descriptor"),
        path="route_descriptor",
        fields=_DESCRIPTOR_FIELDS,
    )
    if descriptor["schema"] != ROUTE_SCHEMA or descriptor["schema_version"] != spec.schema_version:
        _reject(
            RouteContractReason.UNSUPPORTED_SCHEMA,
            "route_descriptor",
            "unsupported reasoner route schema or version",
        )
    if descriptor["route_id"] != spec.route_id:
        _reject(
            RouteContractReason.IDENTITY_MISMATCH,
            "route_descriptor.route_id",
            "route identity differs from the frozen route id",
        )
    if descriptor["adapter_kind"] != ADAPTER_KIND:
        _reject(
            RouteContractReason.IDENTITY_MISMATCH,
            "route_descriptor.adapter_kind",
            "adapter kind differs from the frozen adapter",
        )
    provider = _text(descriptor["provider"], path="route_descriptor.provider")
    model = _text(descriptor["model"], path="route_descriptor.model")
    model_revision = _text(
        descriptor["model_revision"], path="route_descriptor.model_revision", nullable=True
    )
    base_url = _text(descriptor["base_url"], path="route_descriptor.base_url")
    if (
        provider != DIRECT_PROVIDER
        or model != DIRECT_MODEL
        or model_revision is not None
        or base_url != DIRECT_BASE_URL
    ):
        _reject(
            RouteContractReason.IDENTITY_MISMATCH,
            "route_descriptor",
            "provider, model, revision, or base URL differs from the frozen direct Kimi identity",
        )
    if _sha256(descriptor["policy_sha256"], path="route_descriptor.policy_sha256") != (
        spec.policy_sha256_loader()
    ):
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "route_descriptor.policy_sha256",
            "route is not bound to the current frozen strategy policy",
        )

    decoding = _decoding_from(descriptor["decoding"], path="route_descriptor.decoding")
    policy = spec.policy_loader()
    call_policy_raw = policy.data["reasoner"]["call_policy"]
    expected_timeout = int(call_policy_raw["hard_timeout_seconds"])
    token_field = (
        "max_completion_tokens"
        if spec.schema_version == ROUTE_V2_SCHEMA_VERSION
        else "max_output_tokens"
    )
    expected_tokens = int(call_policy_raw[token_field])
    if spec.caller_temperature is None:
        try:
            expected_temperature = Decimal(str(call_policy_raw["temperature"]))
        except InvalidOperation:
            _reject(
                RouteContractReason.POLICY_MISMATCH,
                "strategy_policy.reasoner.call_policy.temperature",
                "frozen caller temperature is invalid",
            )
    else:
        expected_temperature = spec.caller_temperature
    if (
        decoding.temperature != expected_temperature
        or decoding.top_p != spec.caller_top_p
        or decoding.max_output_tokens != expected_tokens
        or decoding.seed != spec.caller_seed
    ):
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "route_descriptor.decoding",
            "caller decoding differs from the frozen policy/provider semantics",
        )
    provider_request_policy = _provider_request_policy_from(
        descriptor["provider_request_policy"],
        path="route_descriptor.provider_request_policy",
        spec=spec,
    )

    call_policy = _strict_object(
        descriptor["call_policy"],
        path="route_descriptor.call_policy",
        fields=_CALL_POLICY_FIELDS,
    )
    if (
        _integer(
            call_policy["hard_timeout_seconds"],
            path="route_descriptor.call_policy.hard_timeout_seconds",
            minimum=1,
        )
        != expected_timeout
        or _integer(
            call_policy["max_calls"], path="route_descriptor.call_policy.max_calls", minimum=1
        )
        != 1
        or _integer(
            call_policy["retry_count"], path="route_descriptor.call_policy.retry_count", minimum=0
        )
        != 0
    ):
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "route_descriptor.call_policy",
            "call policy differs from the frozen one-call/no-retry policy",
        )

    candidates = descriptor["candidate_ids"]
    if not isinstance(candidates, list) or not all(isinstance(item, str) for item in candidates):
        _reject(
            RouteContractReason.INVALID_DOCUMENT, "route_descriptor.candidate_ids", "must be a list"
        )
    expected_candidates = tuple(str(item["candidate_id"]) for item in policy.data["candidates"])
    if tuple(candidates) != expected_candidates:
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "route_descriptor.candidate_ids",
            "candidate ids differ from the frozen strategy policy",
        )

    authority = _strict_object(
        descriptor["authority"], path="route_descriptor.authority", fields=_AUTHORITY_FIELDS
    )
    for key in sorted(_AUTHORITY_FIELDS):
        _boolean_false(authority[key], path=f"route_descriptor.authority.{key}")
    application_arguments = descriptor["application_arguments"]
    if not isinstance(application_arguments, Mapping):
        _reject(
            RouteContractReason.AUTHORITY_VIOLATION,
            "route_descriptor.application_arguments",
            "application arguments must remain empty",
        )
    if _contains_secret_like(application_arguments):
        _reject(
            RouteContractReason.SECRET_ARGUMENT,
            "route_descriptor.application_arguments",
            "secret-like values are forbidden",
        )
    if application_arguments:
        _reject(
            RouteContractReason.AUTHORITY_VIOLATION,
            "route_descriptor.application_arguments",
            "application arguments must remain empty",
        )

    cost = _strict_object(
        descriptor["cost_ceiling"], path="route_descriptor.cost_ceiling", fields=_COST_FIELDS
    )
    if (
        _integer(
            cost["max_output_tokens_per_call"],
            path="route_descriptor.cost_ceiling.max_output_tokens_per_call",
            minimum=1,
        )
        != provider_request_policy.max_completion_tokens
    ):
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "route_descriptor.cost_ceiling.max_output_tokens_per_call",
            "cost ceiling must match the frozen direct Kimi completion limit",
        )
    if (
        _integer(
            cost["probe_budget_calls"],
            path="route_descriptor.cost_ceiling.probe_budget_calls",
            minimum=0,
        )
        != 0
        or cost["paid_provider_purchase"] is not False
        or _text(cost["entitlement_note"], path="route_descriptor.cost_ceiling.entitlement_note")
        is None
    ):
        _reject(
            RouteContractReason.AUTHORITY_VIOLATION,
            "route_descriptor.cost_ceiling",
            "route cannot authorize a provider purchase or probe budget",
        )
    claim_labels = descriptor["claim_labels"]
    if not isinstance(claim_labels, list) or tuple(claim_labels) != CLAIM_LABELS:
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "route_descriptor.claim_labels",
            "claim labels differ from the frozen boundary",
        )

    expected_model_config = direct_kimi_model_config_sha256(
        provider=provider,
        model=model,
        model_revision=model_revision,
        base_url=base_url,
        caller_decoding=decoding,
        provider_request_policy=provider_request_policy,
        schema_version=spec.schema_version,
    )
    receipt = _strict_object(
        _decode(receipt_bytes, path="approval_receipt"),
        path="approval_receipt",
        fields=_RECEIPT_FIELDS,
    )
    if receipt["schema"] != APPROVAL_SCHEMA or receipt["schema_version"] != spec.schema_version:
        _reject(
            RouteContractReason.UNSUPPORTED_SCHEMA,
            "approval_receipt",
            "unsupported route approval schema or version",
        )
    if receipt["approval_id"] != spec.approval_id or receipt["scope"] != spec.approval_scope:
        _reject(
            RouteContractReason.IDENTITY_MISMATCH,
            "approval_receipt",
            "approval identity or scope differs from the frozen direct route",
        )
    clauses = receipt["clauses"]
    if not isinstance(clauses, list) or tuple(clauses) != spec.approval_clauses:
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "approval_receipt.clauses",
            "approval clauses differ from the frozen direct route receipt",
        )
    if receipt["cost_ceiling"] != descriptor["cost_ceiling"]:
        _reject(
            RouteContractReason.IDENTITY_MISMATCH,
            "approval_receipt.cost_ceiling",
            "approval cost ceiling differs from the descriptor",
        )
    if _sha256(receipt["route_sha256"], path="approval_receipt.route_sha256") != sha256_bytes(
        descriptor_bytes
    ):
        _reject(
            RouteContractReason.HASH_MISMATCH,
            "approval_receipt.route_sha256",
            "approval receipt is not bound to the supplied descriptor bytes",
        )
    if (
        _sha256(receipt["model_config_sha256"], path="approval_receipt.model_config_sha256")
        != expected_model_config
    ):
        _reject(
            RouteContractReason.HASH_MISMATCH,
            "approval_receipt.model_config_sha256",
            "approval receipt does not bind direct Kimi provider request semantics",
        )
    for field, expected in (
        ("provider", provider),
        ("model", model),
        ("model_revision", model_revision),
        ("adapter_kind", ADAPTER_KIND),
        ("base_url", base_url),
    ):
        if receipt[field] != expected:
            _reject(
                RouteContractReason.IDENTITY_MISMATCH,
                f"approval_receipt.{field}",
                "approval receipt identity differs from the descriptor",
            )
    try:
        state = ApprovalState(str(receipt["approval_state"]))
    except ValueError:
        _reject(
            RouteContractReason.INVALID_DOCUMENT,
            "approval_receipt.approval_state",
            "unknown approval state",
        )
    approver = _text(receipt["approver"], path="approval_receipt.approver", nullable=True)
    approved_at = _timestamp(
        receipt["approved_at"], path="approval_receipt.approved_at", nullable=True
    )
    if state is ApprovalState.REVOKED:
        _reject(
            RouteContractReason.APPROVAL_REVOKED,
            "approval_receipt.approval_state",
            "a revoked route can never authorize evaluation",
        )
    if state is ApprovalState.APPROVED and (approver is None or approved_at is None):
        _reject(
            RouteContractReason.APPROVAL_MISSING,
            "approval_receipt.approver",
            "an approved route requires a named approver and approval instant",
        )
    compatibility_state, compatibility_reason = _compatibility(
        caller_decoding=decoding, provider_request_policy=provider_request_policy
    )
    return ValidatedRoute(
        route_id=spec.route_id,
        provider=provider,
        model=model,
        model_revision=model_revision,
        adapter_kind=ADAPTER_KIND,
        base_url=base_url,
        caller_decoding=decoding,
        provider_request_policy=provider_request_policy,
        route_sha256=sha256_bytes(descriptor_bytes),
        model_config_sha256=expected_model_config,
        approval_state=state,
        approver=approver,
        approved_at=approved_at,
        compatibility_state=compatibility_state,
        compatibility_reason_code=compatibility_reason.value if compatibility_reason else None,
        evaluation_eligible=(
            state is ApprovalState.APPROVED
            and compatibility_state is RouteCompatibilityState.COMPATIBLE
        ),
    )


def validate_reasoner_route(descriptor_bytes: bytes, receipt_bytes: bytes) -> ValidatedRoute:
    """Validate a V1 direct-Kimi route without changing its inert semantics."""

    return _validate_reasoner_route(descriptor_bytes, receipt_bytes, spec=_V1_ROUTE_SPEC)


def packaged_route_descriptor_bytes() -> bytes:
    """Return the exact packaged V1 descriptor bytes."""

    return (
        resources.files("ringdown_market.contracts")
        .joinpath("policies/reasoner_route_v1.json")
        .read_bytes()
    )


def packaged_route_approval_bytes() -> bytes:
    """Return the exact packaged V1 approval bytes."""

    return (
        resources.files("ringdown_market.contracts")
        .joinpath("policies/reasoner_route_approval_v1.json")
        .read_bytes()
    )


@lru_cache(maxsize=1)
def load_approved_reasoner_route() -> ValidatedRoute:
    """Validate and retain the exact packaged V1 route as an inert object."""

    return validate_reasoner_route(
        packaged_route_descriptor_bytes(), packaged_route_approval_bytes()
    )


def packaged_route_descriptor_v2_bytes() -> bytes:
    """Return the exact packaged V2 descriptor bytes."""

    return (
        resources.files("ringdown_market.contracts")
        .joinpath("policies/reasoner_route_v2.json")
        .read_bytes()
    )


def packaged_route_approval_v2_bytes() -> bytes:
    """Return the exact packaged V2 approval receipt bytes."""

    return (
        resources.files("ringdown_market.contracts")
        .joinpath("policies/reasoner_route_approval_v2.json")
        .read_bytes()
    )


def _require_exact_v2_package(descriptor_bytes: bytes, receipt_bytes: bytes) -> None:
    """Reject semantic lookalikes: V2 eligibility belongs only to shipped bytes."""

    if type(descriptor_bytes) is not bytes or type(receipt_bytes) is not bytes:
        _reject(
            RouteContractReason.INVALID_DOCUMENT,
            "route_descriptor",
            "V2 descriptor and approval inputs must be immutable bytes",
        )
    if descriptor_bytes != packaged_route_descriptor_v2_bytes():
        _reject(
            RouteContractReason.HASH_MISMATCH,
            "route_descriptor",
            "V2 evaluation eligibility requires the exact packaged descriptor bytes",
        )
    if receipt_bytes != packaged_route_approval_v2_bytes():
        _reject(
            RouteContractReason.HASH_MISMATCH,
            "approval_receipt",
            "V2 evaluation eligibility requires the exact packaged approval bytes",
        )


def validate_reasoner_route_v2(descriptor_bytes: bytes, receipt_bytes: bytes) -> ValidatedRoute:
    """Strictly load only the immutable V2 K3 route package."""

    _require_exact_v2_package(descriptor_bytes, receipt_bytes)
    return load_approved_reasoner_route_v2()


@lru_cache(maxsize=1)
def load_approved_reasoner_route_v2() -> ValidatedRoute:
    """Validate the exact packaged V2 route and retain its unforgeable object identity."""

    descriptor_bytes = packaged_route_descriptor_v2_bytes()
    receipt_bytes = packaged_route_approval_v2_bytes()
    return _validate_reasoner_route(descriptor_bytes, receipt_bytes, spec=_V2_ROUTE_SPEC)


__all__ = [
    "ADAPTER_KIND",
    "APPROVAL_CLAUSES",
    "APPROVAL_CLAUSES_V2",
    "APPROVAL_ID",
    "APPROVAL_ID_V2",
    "APPROVAL_SCHEMA",
    "APPROVAL_SCOPE",
    "APPROVAL_SCOPE_V2",
    "CLAIM_LABELS",
    "DIRECT_BASE_URL",
    "DIRECT_MODEL",
    "DIRECT_PROVIDER",
    "KIMI_EFFECTIVE_TEMPERATURE",
    "KIMI_EFFECTIVE_TOP_P",
    "KIMI_MAX_COMPLETION_TOKENS",
    "KIMI_OMITTED_REQUEST_FIELDS",
    "KIMI_REASONING_EFFORT",
    "KIMI_RESPONSE_FORMAT_TYPE",
    "KIMI_RESPONSE_SCHEMA_NAME",
    "KIMI_RESPONSE_SCHEMA_NAME_V2",
    "KIMI_TOOL_CHOICE",
    "ROUTE_ID",
    "ROUTE_ID_V2",
    "ROUTE_SCHEMA",
    "ROUTE_V2_SCHEMA_VERSION",
    "ApprovalState",
    "ProviderRequestPolicy",
    "RouteCompatibilityReason",
    "RouteCompatibilityState",
    "RouteContractReason",
    "RouteContractRejected",
    "ValidatedRoute",
    "direct_kimi_model_config_sha256",
    "load_approved_reasoner_route",
    "load_approved_reasoner_route_v2",
    "packaged_route_approval_bytes",
    "packaged_route_approval_v2_bytes",
    "packaged_route_descriptor_bytes",
    "packaged_route_descriptor_v2_bytes",
    "route_descriptor_bytes",
    "validate_reasoner_route",
    "validate_reasoner_route_v2",
]
