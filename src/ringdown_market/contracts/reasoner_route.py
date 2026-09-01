"""Frozen host-managed reasoner route contract and approval receipts.

The route descriptor freezes the provider-neutral identity of the bounded
reasoner boundary: adapter kind, provider/model identity, decoding, call
policy, authority denials, and cost ceiling.  The approval receipt records the
owner approval state for exactly that descriptor.  Neither artifact carries
credentials, broker authority, or secret-bearing application arguments, and a
pending or revoked approval can never authorize strategy evaluation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from importlib import resources
from typing import NoReturn

from ..strategy.contracts import (
    reasoner_model_config_sha256,
    reasoner_policy_hashes,
    sha256_bytes,
)
from ..strategy.models import DecodingParameters
from ..strategy.policy import load_strategy_policy, strategy_policy_sha256

ROUTE_SCHEMA = "esscher.reasoner_route"
APPROVAL_SCHEMA = "esscher.reasoner_route_approval"
SCHEMA_VERSION = 1

ADAPTER_KIND = "OPENAI_COMPATIBLE_CHAT_V1"
ROUTE_ID = "ESSCHER_BOUNDED_REASONER_ROUTE_V1"

CLAIM_LABELS = (
    "NO_BROKER_AUTHORITY",
    "NO_ACCOUNT_AUTHORITY",
    "NO_SECRET_ARGUMENTS",
    "PAPER_ONLY",
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
class ValidatedRoute:
    """A validated route descriptor plus its approval state."""

    route_id: str
    provider: str
    model: str
    model_revision: str | None
    adapter_kind: str
    base_url: str
    route_sha256: str
    model_config_sha256: str
    approval_state: ApprovalState
    approver: str | None
    approved_at: datetime | None
    evaluation_eligible: bool


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
    if not isinstance(value, str):
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "must be a timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        _reject(RouteContractReason.INVALID_DOCUMENT, path, str(error))


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        _reject(RouteContractReason.INVALID_DOCUMENT, path, "must be SHA-256")
    return value.lower()


def _integer(value: object, *, path: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        _reject(
            RouteContractReason.INVALID_DOCUMENT,
            path,
            f"must be an integer of at least {minimum}",
        )
    return value


def _decoding_from(value: object, *, path: str) -> DecodingParameters:
    from decimal import Decimal

    payload = _strict_object(
        value,
        path=path,
        fields=frozenset({"temperature", "top_p", "max_output_tokens", "seed"}),
    )
    return DecodingParameters(
        temperature=Decimal(str(payload["temperature"])),
        top_p=Decimal(str(payload["top_p"])),
        max_output_tokens=_integer(
            payload["max_output_tokens"], path=f"{path}.max_output_tokens", minimum=1
        ),
        seed=_integer(payload["seed"], path=f"{path}.seed", minimum=0)
        if payload["seed"] is not None
        else None,
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


def route_descriptor_bytes(payload: Mapping[str, object]) -> bytes:
    """Serialize a route descriptor canonically."""

    from ..strategy.contracts import canonical_json_bytes

    return canonical_json_bytes(payload)


def validate_reasoner_route(
    descriptor_bytes: bytes,
    receipt_bytes: bytes,
) -> ValidatedRoute:
    """Validate a frozen route descriptor against its approval receipt."""

    descriptor = _strict_object(
        _decode(descriptor_bytes, path="route_descriptor"),
        path="route_descriptor",
        fields=_DESCRIPTOR_FIELDS,
    )
    if descriptor["schema"] != ROUTE_SCHEMA or descriptor["schema_version"] != SCHEMA_VERSION:
        _reject(
            RouteContractReason.UNSUPPORTED_SCHEMA,
            "route_descriptor",
            "unsupported reasoner route schema or version",
        )
    if descriptor["route_id"] != ROUTE_ID:
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
    if not base_url.startswith("https://"):
        _reject(
            RouteContractReason.AUTHORITY_VIOLATION,
            "route_descriptor.base_url",
            "route base url must be public HTTPS",
        )
    if _sha256(descriptor["policy_sha256"], path="route_descriptor.policy_sha256") != (
        strategy_policy_sha256()
    ):
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "route_descriptor.policy_sha256",
            "route is not bound to the current frozen strategy policy",
        )
    decoding = _decoding_from(descriptor["decoding"], path="route_descriptor.decoding")
    policy = load_strategy_policy()
    call_policy_raw = policy.data["reasoner"]["call_policy"]
    expected_timeout = int(call_policy_raw["hard_timeout_seconds"])
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
            "call policy must equal the frozen policy call policy",
        )
    if decoding.max_output_tokens != int(call_policy_raw["max_output_tokens"]):
        _reject(
            RouteContractReason.POLICY_MISMATCH,
            "route_descriptor.decoding.max_output_tokens",
            "decoding token ceiling must equal the frozen call policy",
        )
    candidates = descriptor["candidate_ids"]
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        _reject(
            RouteContractReason.INVALID_DOCUMENT,
            "route_descriptor.candidate_ids",
            "candidate ids must be a list",
        )
    for candidate_id in candidates:
        _text(candidate_id, path="route_descriptor.candidate_ids")
        reasoner_policy_hashes(str(candidate_id))
    authority = _strict_object(
        descriptor["authority"], path="route_descriptor.authority", fields=_AUTHORITY_FIELDS
    )
    for field in ("broker", "account", "secret_arguments"):
        _boolean_false(authority[field], path=f"route_descriptor.authority.{field}")
    arguments = descriptor["application_arguments"]
    if _contains_secret_like(arguments):
        _reject(
            RouteContractReason.SECRET_ARGUMENT,
            "route_descriptor.application_arguments",
            "application arguments must not carry secrets",
        )
    cost = _strict_object(
        descriptor["cost_ceiling"], path="route_descriptor.cost_ceiling", fields=_COST_FIELDS
    )
    if cost["paid_provider_purchase"] is not False:
        _reject(
            RouteContractReason.AUTHORITY_VIOLATION,
            "route_descriptor.cost_ceiling.paid_provider_purchase",
            "the frozen route must not authorize provider purchase",
        )
    _integer(
        cost["max_output_tokens_per_call"],
        path="route_descriptor.cost_ceiling.max_output_tokens_per_call",
        minimum=1,
    )
    _integer(
        cost["probe_budget_calls"],
        path="route_descriptor.cost_ceiling.probe_budget_calls",
        minimum=0,
    )
    _text(cost["entitlement_note"], path="route_descriptor.cost_ceiling.entitlement_note")
    labels = descriptor["claim_labels"]
    if not isinstance(labels, Sequence) or tuple(labels) != CLAIM_LABELS:
        _reject(
            RouteContractReason.IDENTITY_MISMATCH,
            "route_descriptor.claim_labels",
            "claim labels must equal the frozen route claim set",
        )

    receipt = _strict_object(
        _decode(receipt_bytes, path="approval_receipt"),
        path="approval_receipt",
        fields=_RECEIPT_FIELDS,
    )
    if receipt["schema"] != APPROVAL_SCHEMA or receipt["schema_version"] != SCHEMA_VERSION:
        _reject(
            RouteContractReason.UNSUPPORTED_SCHEMA,
            "approval_receipt",
            "unsupported approval receipt schema or version",
        )
    if _sha256(receipt["route_sha256"], path="approval_receipt.route_sha256") != sha256_bytes(
        descriptor_bytes
    ):
        _reject(
            RouteContractReason.HASH_MISMATCH,
            "approval_receipt.route_sha256",
            "approval receipt is not bound to the supplied descriptor bytes",
        )
    expected_model_config = reasoner_model_config_sha256(
        provider=provider, model=model, model_revision=model_revision, decoding=decoding
    )
    if (
        _sha256(receipt["model_config_sha256"], path="approval_receipt.model_config_sha256")
        != expected_model_config
    ):
        _reject(
            RouteContractReason.HASH_MISMATCH,
            "approval_receipt.model_config_sha256",
            "model config hash does not match the descriptor identity",
        )
    for field in ("provider", "model", "adapter_kind", "base_url"):
        if receipt[field] != descriptor[field]:
            _reject(
                RouteContractReason.IDENTITY_MISMATCH,
                f"approval_receipt.{field}",
                "approval receipt identity differs from the descriptor",
            )
    if receipt["model_revision"] != model_revision:
        _reject(
            RouteContractReason.IDENTITY_MISMATCH,
            "approval_receipt.model_revision",
            "approval receipt model revision differs from the descriptor",
        )
    state = ApprovalState(str(receipt["approval_state"]))
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
    return ValidatedRoute(
        route_id=ROUTE_ID,
        provider=provider,
        model=model,
        model_revision=model_revision,
        adapter_kind=ADAPTER_KIND,
        base_url=base_url,
        route_sha256=sha256_bytes(descriptor_bytes),
        model_config_sha256=expected_model_config,
        approval_state=state,
        approver=approver,
        approved_at=approved_at,
        evaluation_eligible=state is ApprovalState.APPROVED,
    )


def packaged_route_descriptor_bytes() -> bytes:
    return (
        resources.files("ringdown_market.contracts")
        .joinpath("policies/reasoner_route_v1.json")
        .read_bytes()
    )


def packaged_route_approval_bytes() -> bytes:
    return (
        resources.files("ringdown_market.contracts")
        .joinpath("policies/reasoner_route_approval_v1.json")
        .read_bytes()
    )


def load_approved_reasoner_route() -> ValidatedRoute:
    """Validate the packaged frozen route descriptor and approval receipt."""

    return validate_reasoner_route(
        packaged_route_descriptor_bytes(), packaged_route_approval_bytes()
    )


__all__ = [
    "ADAPTER_KIND",
    "APPROVAL_SCHEMA",
    "CLAIM_LABELS",
    "ROUTE_ID",
    "ROUTE_SCHEMA",
    "ApprovalState",
    "RouteContractReason",
    "RouteContractRejected",
    "ValidatedRoute",
    "load_approved_reasoner_route",
    "packaged_route_approval_bytes",
    "packaged_route_descriptor_bytes",
    "route_descriptor_bytes",
    "validate_reasoner_route",
]
