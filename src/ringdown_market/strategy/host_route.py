"""Direct-provider host boundaries with pure fake-transport paths.

The strategy package never opens a socket.  A host injects the transport; the
credential is validated once and discarded at adapter construction.  Two lanes
live here:

- the direct Moonshot Kimi K3 lane (V1 inert; V2 approved but its request seam
  awaits the V2 engine assembly), preserved unchanged;
- the direct MiniMax-M3 lane (V3, owner-approved 2026-09-04 after the Kimi
  entitlement was withdrawn): the first adapter wired for the assembled engine,
  carrying policy-registry exchange identities and probe-verified wire pins
  (thinking disabled, temperature 0, top_p 1.0, json_object response format).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from ringdown_market.contracts.reasoner_route import (
    DIRECT_BASE_URL,
    DIRECT_MODEL,
    DIRECT_PROVIDER,
    KIMI_EFFECTIVE_TEMPERATURE,
    KIMI_EFFECTIVE_TOP_P,
    KIMI_MAX_COMPLETION_TOKENS,
    KIMI_OMITTED_REQUEST_FIELDS,
    KIMI_REASONING_EFFORT,
    KIMI_RESPONSE_FORMAT_TYPE,
    KIMI_RESPONSE_SCHEMA_NAME,
    KIMI_RESPONSE_SCHEMA_NAME_V2,
    KIMI_TOOL_CHOICE,
    MINIMAX_DIRECT_BASE_URL,
    MINIMAX_DIRECT_MODEL,
    MINIMAX_DIRECT_PROVIDER,
    MINIMAX_EFFECTIVE_TEMPERATURE,
    MINIMAX_EFFECTIVE_TOP_P,
    MINIMAX_MAX_COMPLETION_TOKENS,
    MINIMAX_OMITTED_REQUEST_FIELDS,
    MINIMAX_REASONING_EFFORT,
    MINIMAX_RESPONSE_FORMAT_TYPE,
    MINIMAX_RESPONSE_SCHEMA_NAME,
    MINIMAX_TOOL_CHOICE,
    ProviderRequestPolicy,
    RouteCompatibilityState,
    ValidatedRoute,
    load_approved_reasoner_route,
    load_approved_reasoner_route_v2,
    load_approved_reasoner_route_v3,
)

from .contracts import (
    article_attribution_bytes,
    candidate_manifest_bytes,
    canonical_json_bytes,
    feature_receipt_bytes,
    reasoner_output_schema_payload,
    reasoner_output_schema_sha256,
    reasoner_output_schema_v2_payload,
    reasoner_output_schema_v2_sha256,
    reasoner_policy_hashes,
    reasoner_system_prompt_bytes,
    reasoner_system_prompt_sha256,
    reasoner_system_prompt_v2_bytes,
    reasoner_system_prompt_v2_sha256,
    sha256_bytes,
    strategy_snapshot_bytes,
    strategy_v2_context_payload,
    validate_strategy_v2_context,
)
from .models import ExchangeStatus, ReasonerExchange
from .reasoner import ReasonerRouteRequest, ReasonerRouteResult, RouteIdentity, deadline_for

ENV_API_KEY = "KIMI_API_KEY"
PRODUCER = "esscher.strategy.direct_kimi_host_route"


class HostRouteError(RuntimeError):
    """Base error for the host-managed direct Kimi route boundary."""


class HostRouteConfigurationError(HostRouteError):
    """Host environment is missing or malformed."""


class HostRouteInputIntegrityError(HostRouteError):
    """A supplied strategy input no longer binds its canonical data."""


class HostRouteNotApproved(HostRouteError):
    """The selected route is not operationally eligible under the current policy."""


class HostRouteSecretBoundaryError(HostRouteError):
    """A secret would cross the repository/application boundary."""


@dataclass(frozen=True, slots=True)
class KimiK3Request:
    """Exact canonical provider payload and its immutable request identity."""

    endpoint: str
    payload_bytes: bytes
    request_sha256: str
    route_sha256: str
    model_config_sha256: str
    prompt_sha256: str
    output_schema_sha256: str

    @property
    def payload(self) -> dict[str, object]:
        """Return a fresh decoded payload for a fake or host-owned transport."""

        payload = json.loads(self.payload_bytes)
        if not isinstance(payload, dict):  # Defensive: bytes are built canonically below.
            raise HostRouteInputIntegrityError("direct Kimi payload is not an object")
        return payload


@dataclass(frozen=True, slots=True)
class KimiTransportResult:
    """One non-retrying transport attempt with stable, non-secret failure detail."""

    status: ExchangeStatus
    error_code: str | None
    raw_response_bytes: bytes | None


# An alias, not a provider-specific parallel status taxonomy.  Existing exchange
# status values remain the stable externally recorded vocabulary.
KimiTransportStatus = ExchangeStatus


def load_route_environment() -> Mapping[str, str]:
    """Read only the direct-provider credential; all route values come from bytes."""

    value = os.environ.get(ENV_API_KEY)
    if not value or not value.strip():
        raise HostRouteConfigurationError(f"missing host environment {ENV_API_KEY}")
    return {ENV_API_KEY: value.strip()}


def _canonical_strategy_payload(
    strategy_input: object,
) -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    """Serialize and cross-check existing canonical input artifacts once."""

    try:
        candidate_manifest = strategy_input.candidate_manifest
        snapshot = strategy_input.snapshot
        feature_receipt = strategy_input.feature_receipt
        manifest_bytes = candidate_manifest_bytes(candidate_manifest)
        snapshot_bytes = strategy_snapshot_bytes(snapshot)
        feature_bytes = feature_receipt_bytes(feature_receipt)
        manifest_sha256 = sha256_bytes(manifest_bytes)
        snapshot_sha256 = sha256_bytes(snapshot_bytes)
        feature_sha256 = sha256_bytes(feature_bytes)
        supplied_manifest_sha256 = strategy_input.candidate_manifest_sha256
        supplied_snapshot_sha256 = strategy_input.snapshot_sha256
        supplied_feature_sha256 = strategy_input.feature_receipt_sha256
        evidence_packet_sha256 = snapshot.evidence_packet_sha256
        policy_sha256 = snapshot.policy_sha256
        candidate_id = snapshot.candidate_id
        event_id = snapshot.event_id
        if (
            supplied_manifest_sha256 != manifest_sha256
            or supplied_snapshot_sha256 != snapshot_sha256
            or supplied_feature_sha256 != feature_sha256
            or snapshot.candidate_manifest_sha256 != manifest_sha256
            or feature_receipt.strategy_snapshot_sha256 != snapshot_sha256
            or feature_receipt.created_at > snapshot.decision_cutoff_at
            or feature_receipt.feature_snapshot_at > snapshot.decision_cutoff_at
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise HostRouteInputIntegrityError(
            "strategy input identities do not bind canonical pre-cutoff data"
        ) from None
    return (
        json.loads(snapshot_bytes),
        json.loads(feature_bytes),
        {
            "candidate_id": candidate_id,
            "candidate_manifest_sha256": manifest_sha256,
            "event_id": event_id,
            "evidence_packet_sha256": evidence_packet_sha256,
            "feature_receipt_sha256": feature_sha256,
            "policy_sha256": policy_sha256,
            "strategy_snapshot_sha256": snapshot_sha256,
        },
    )


def _validate_direct_kimi_route(
    route: ValidatedRoute,
    *,
    v2: bool = False,
) -> ProviderRequestPolicy:
    """Defend a pure builder from a hand-forged or drifted packaged-route object."""

    expected_route = load_approved_reasoner_route_v2() if v2 else load_approved_reasoner_route()
    expected_schema_name = KIMI_RESPONSE_SCHEMA_NAME_V2 if v2 else KIMI_RESPONSE_SCHEMA_NAME
    expected_schema_sha256 = (
        reasoner_output_schema_v2_sha256() if v2 else reasoner_output_schema_sha256()
    )
    # Equality is insufficient: ValidatedRoute is intentionally public data, so
    # a caller could recreate matching fields. Cached package-object identity is
    # the capability proving this value came from the exact shipped byte pair.
    if route is not expected_route:
        raise HostRouteConfigurationError(
            "route is not the exact packaged descriptor and approval validation result"
        )
    if (
        route.provider != DIRECT_PROVIDER
        or route.model != DIRECT_MODEL
        or route.model_revision is not None
        or route.base_url != DIRECT_BASE_URL
    ):
        raise HostRouteConfigurationError("route identity is not the frozen direct Kimi K3 route")
    provider_request_policy = route.provider_request_policy
    if (
        provider_request_policy.reasoning_effort != KIMI_REASONING_EFFORT
        or provider_request_policy.max_completion_tokens != KIMI_MAX_COMPLETION_TOKENS
        or provider_request_policy.response_format_type != KIMI_RESPONSE_FORMAT_TYPE
        or provider_request_policy.output_schema_name != expected_schema_name
        or provider_request_policy.output_schema_sha256 != expected_schema_sha256
        or provider_request_policy.strict_json_schema is not True
        or provider_request_policy.tool_choice != KIMI_TOOL_CHOICE
        or provider_request_policy.effective_temperature != KIMI_EFFECTIVE_TEMPERATURE
        or provider_request_policy.effective_top_p != KIMI_EFFECTIVE_TOP_P
        or tuple(provider_request_policy.omitted_request_fields) != KIMI_OMITTED_REQUEST_FIELDS
    ):
        raise HostRouteConfigurationError(
            "route does not bind the current direct Kimi schema policy"
        )
    return provider_request_policy


def build_kimi_k3_request(route: ValidatedRoute, request: ReasonerRouteRequest) -> KimiK3Request:
    """Build the exact canonical direct-Kimi payload without a credential or network call."""

    provider_request_policy = _validate_direct_kimi_route(route)
    snapshot_payload, feature_payload, identities = _canonical_strategy_payload(
        request.strategy_input
    )
    candidate_id = identities["candidate_id"]
    prompt_bytes = reasoner_system_prompt_bytes(candidate_id)
    prompt_sha256 = reasoner_system_prompt_sha256(candidate_id)
    output_schema = reasoner_output_schema_payload()
    output_schema_sha256 = reasoner_output_schema_sha256()
    user_payload = {
        "feature_receipt": feature_payload,
        "identities": identities,
        "strategy_snapshot": snapshot_payload,
    }
    payload = {
        "max_completion_tokens": provider_request_policy.max_completion_tokens,
        "messages": [
            {"content": prompt_bytes.decode("utf-8"), "role": "system"},
            {"content": canonical_json_bytes(user_payload).decode("utf-8"), "role": "user"},
        ],
        "model": route.model,
        "reasoning_effort": provider_request_policy.reasoning_effort,
        "response_format": {
            "json_schema": {
                "name": provider_request_policy.output_schema_name,
                "schema": output_schema,
                "strict": provider_request_policy.strict_json_schema,
            },
            "type": provider_request_policy.response_format_type,
        },
        "tool_choice": provider_request_policy.tool_choice,
    }
    if any(field in payload for field in provider_request_policy.omitted_request_fields):
        raise HostRouteConfigurationError("direct Kimi payload includes a frozen omitted field")
    payload_bytes = canonical_json_bytes(payload)
    request_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "provider_payload": payload,
                "route_identity": {
                    "base_url": route.base_url,
                    "model": route.model,
                    "model_config_sha256": route.model_config_sha256,
                    "output_schema_sha256": output_schema_sha256,
                    "prompt_sha256": prompt_sha256,
                    "provider": route.provider,
                    "route_sha256": route.route_sha256,
                },
                "schema": "esscher.direct_kimi_request_identity",
                "schema_version": 1,
                "strategy_identities": identities,
            }
        )
    )
    return KimiK3Request(
        endpoint=f"{route.base_url}/chat/completions",
        payload_bytes=payload_bytes,
        request_sha256=request_sha256,
        route_sha256=route.route_sha256,
        model_config_sha256=route.model_config_sha256,
        prompt_sha256=prompt_sha256,
        output_schema_sha256=output_schema_sha256,
    )


_V2_FORBIDDEN_PAYLOAD_FIELDS = frozenset(
    {
        "account",
        "broker",
        "entry",
        "exit",
        "instrument",
        "order",
        "orders",
        "position",
        "position_size",
        "quantity",
        "risk_tier",
        "secret",
        "sizing",
        "token",
    }
)


def _safe_v2_snapshot_payload(snapshot: object) -> dict[str, object]:
    """Project a canonical snapshot without entry/order-like fields."""

    try:
        raw = json.loads(strategy_snapshot_bytes(snapshot))
    except (TypeError, ValueError):
        raise HostRouteInputIntegrityError("V2 snapshot cannot be canonically projected") from None
    if not isinstance(raw, dict):  # pragma: no cover - canonical contract assertion
        raise HostRouteInputIntegrityError("V2 snapshot payload is not an object")
    allowed = frozenset(
        {
            "allowed_unknown_codes",
            "candidate_id",
            "candidate_manifest_sha256",
            "cohort_id",
            "created_at",
            "critical_unknown_codes",
            "data_health",
            "decision_cutoff_at",
            "eligibility",
            "eligibility_reason_codes",
            "event_category",
            "event_id",
            "event_published_at",
            "evidence_cutoff_at",
            "evidence_packet_sha256",
            "evidence_refs",
            "health_reason_codes",
            "issuer",
            "observation_window_end_at",
            "observation_window_start_at",
            "policy_sha256",
            "prior_eligible_session_close_at",
            "producer_build_sha256",
            "reaction_session_close_at",
            "reaction_session_id",
            "reaction_session_open_at",
            "release_family",
            "schema",
            "schema_version",
            "security_id",
            "ticker",
            "timing_bucket",
            "universe_frozen_at",
        }
    )
    return {key: raw[key] for key in sorted(allowed) if key in raw}


def _safe_v2_episodic_summary_payload(summary: object) -> dict[str, object]:
    """Keep ledger-validated learning context while omitting broker truth fields."""

    from ringdown_market.autonomy.episodes import episodic_summary_bytes

    try:
        raw = json.loads(episodic_summary_bytes(summary))
    except (TypeError, ValueError):
        raise HostRouteInputIntegrityError(
            "V2 episodic summary cannot be canonically projected"
        ) from None
    if not isinstance(raw, dict):  # pragma: no cover - canonical contract assertion
        raise HostRouteInputIntegrityError("V2 episodic summary payload is not an object")
    summary_fields = frozenset(
        {
            "as_of",
            "candidate_filter_excluded_count",
            "candidate_ids",
            "completed_count",
            "limit",
            "model_config_sha256",
            "net_pnl",
            "policy_sha256",
            "realized_count",
            "reconciliation_failure_count",
            "route_failure_count",
            "schema",
            "schema_version",
        }
    )
    row_fields = frozenset(
        {
            "candidate_id",
            "compatibility",
            "decision_cutoff_at",
            "decision_sha256",
            "direction",
            "disposition",
            "episode_id",
            "episode_sha256",
            "event_id",
            "gross_pnl",
            "lifecycle_outcome",
            "model_config_sha256",
            "net_pnl",
            "occurred_at",
            "outcome_id",
            "outcome_unavailable_reason",
            "pnl_classification",
            "source_policy_sha256",
            "symbol",
        }
    )
    rows = raw.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise HostRouteInputIntegrityError("V2 episodic summary rows are not canonical objects")
    return {
        "body": {
            **{key: raw[key] for key in sorted(summary_fields) if key in raw},
            "rows": [{key: row[key] for key in sorted(row_fields) if key in row} for row in rows],
        },
        "summary_sha256": raw.get("summary_sha256"),
    }


def _safe_v2_news_payload(context: object) -> list[dict[str, object]]:
    """Render authorized #76 article text as explicitly untrusted quotations."""

    observations = context.news_observations
    hashes = context.news_observation_sha256
    if len(observations) != len(hashes):  # pragma: no cover - context validator asserts this
        raise HostRouteInputIntegrityError("V2 news identities do not align with observations")
    rendered: list[dict[str, object]] = []
    for observation, observation_sha256 in zip(observations, hashes, strict=True):
        rendered.append(
            {
                "body": observation.body,
                "canonical_url": observation.canonical_url,
                "classification": "UNTRUSTED_QUOTED_DATA",
                "content_sha256": observation.content_sha256,
                "headline": observation.headline,
                "observation_id": observation.observation_id,
                "observation_sha256": observation_sha256,
                "provider_article_id": observation.provider_article_id,
                "provider_available_at": observation.provider_available_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "source_id": observation.source_id,
                "source_policy_sha256": observation.source_policy_sha256,
            }
        )
    return rendered


def _reject_v2_forbidden_payload_fields(value: object) -> None:
    """Prevent data-only V2 context from growing an execution/secret argument surface."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise HostRouteInputIntegrityError("V2 payload keys must be text")
            normalized = key.strip().lower().replace("-", "_")
            if normalized in _V2_FORBIDDEN_PAYLOAD_FIELDS:
                raise HostRouteInputIntegrityError(
                    f"V2 provider payload must not contain {normalized!r} fields"
                )
            _reject_v2_forbidden_payload_fields(nested)
    elif isinstance(value, list):
        for item in value:
            _reject_v2_forbidden_payload_fields(item)


def _canonical_strategy_v2_payload(
    context: object, *, ledger: object, route: ValidatedRoute
) -> dict[str, object]:
    """Validate durable V2 context immediately before any provider payload exists."""

    try:
        validated = validate_strategy_v2_context(context, ledger=ledger)
    except Exception:
        raise HostRouteInputIntegrityError(
            "V2 context does not bind current canonical, ledger-validated inputs"
        ) from None
    summary = validated.episodic_summary
    if (
        getattr(summary, "policy_sha256", None) != validated.policy_sha256
        or getattr(summary, "model_config_sha256", None) != route.model_config_sha256
        or validated.snapshot.candidate_id not in getattr(summary, "candidate_ids", ())
    ):
        raise HostRouteInputIntegrityError(
            "V2 episodic summary is not compatible with this policy, route, and candidate"
        )
    try:
        from ringdown_market.autonomy.universe import universe_scan_bytes

        candidate_manifest_sha256 = validated.candidate_manifest_sha256
        feature_receipt = json.loads(feature_receipt_bytes(validated.feature_receipt))
        universe_scan = (
            None
            if validated.universe_scan is None
            else json.loads(universe_scan_bytes(validated.universe_scan))
        )
        article_attributions = [
            json.loads(article_attribution_bytes(item)) for item in validated.article_attributions
        ]
    except (AttributeError, TypeError, ValueError):
        raise HostRouteInputIntegrityError(
            "V2 context artifacts cannot be canonically projected"
        ) from None
    payload = {
        "artifacts": {
            "article_attributions": article_attributions,
            "candidate_manifest_sha256": candidate_manifest_sha256,
            "episodic_summary": _safe_v2_episodic_summary_payload(summary),
            "feature_receipt": feature_receipt,
            "strategy_snapshot": _safe_v2_snapshot_payload(validated.snapshot),
            "universe_scan": universe_scan,
            "untrusted_news": _safe_v2_news_payload(validated),
        },
        "identities": strategy_v2_context_payload(validated),
    }
    _reject_v2_forbidden_payload_fields(payload)
    return payload


def build_kimi_k3_v2_request(
    route: ValidatedRoute,
    context: object,
    *,
    ledger: object,
) -> KimiK3Request:
    """Build a V2 K3 request only after exact route and ledger-context validation.

    This is a pure constructor: it reads no credential and invokes no provider,
    account, broker, or order interface.
    """

    provider_request_policy = _validate_direct_kimi_route(route, v2=True)
    user_payload = _canonical_strategy_v2_payload(context, ledger=ledger, route=route)
    candidate_id = user_payload["identities"]["candidate_id"]
    if not isinstance(candidate_id, str):  # pragma: no cover - validated context invariant
        raise HostRouteInputIntegrityError("V2 context candidate identity is not text")
    prompt_bytes = reasoner_system_prompt_v2_bytes(candidate_id)
    prompt_sha256 = reasoner_system_prompt_v2_sha256(candidate_id)
    output_schema = reasoner_output_schema_v2_payload()
    output_schema_sha256 = reasoner_output_schema_v2_sha256()
    payload = {
        "max_completion_tokens": provider_request_policy.max_completion_tokens,
        "messages": [
            {"content": prompt_bytes.decode("utf-8"), "role": "system"},
            {"content": canonical_json_bytes(user_payload).decode("utf-8"), "role": "user"},
        ],
        "model": route.model,
        "reasoning_effort": provider_request_policy.reasoning_effort,
        "response_format": {
            "json_schema": {
                "name": provider_request_policy.output_schema_name,
                "schema": output_schema,
                "strict": provider_request_policy.strict_json_schema,
            },
            "type": provider_request_policy.response_format_type,
        },
        "tool_choice": provider_request_policy.tool_choice,
    }
    if any(field in payload for field in provider_request_policy.omitted_request_fields):
        raise HostRouteConfigurationError("direct Kimi payload includes a frozen omitted field")
    payload_bytes = canonical_json_bytes(payload)
    identities = user_payload["identities"]
    request_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "provider_payload": payload,
                "route_identity": {
                    "base_url": route.base_url,
                    "model": route.model,
                    "model_config_sha256": route.model_config_sha256,
                    "output_schema_sha256": output_schema_sha256,
                    "prompt_sha256": prompt_sha256,
                    "provider": route.provider,
                    "route_sha256": route.route_sha256,
                },
                "schema": "esscher.direct_kimi_v2_request_identity",
                "schema_version": 2,
                "strategy_v2_context_sha256": identities["context_sha256"],
                "strategy_v2_identities": identities,
            }
        )
    )
    return KimiK3Request(
        endpoint=f"{route.base_url}/chat/completions",
        payload_bytes=payload_bytes,
        request_sha256=request_sha256,
        route_sha256=route.route_sha256,
        model_config_sha256=route.model_config_sha256,
        prompt_sha256=prompt_sha256,
        output_schema_sha256=output_schema_sha256,
    )


def invoke_kimi_k3_transport(
    request: KimiK3Request,
    transport: Callable[[str, dict[str, object]], bytes],
) -> KimiTransportResult:
    """Invoke exactly once with no retry and no provider exception text escape."""

    return _invoke_direct_transport_once(request.endpoint, request.payload, transport)


def _invoke_direct_transport_once(
    endpoint: str,
    payload: dict[str, object],
    transport: Callable[[str, dict[str, object]], bytes],
) -> KimiTransportResult:
    """One non-retrying direct-provider call with typed, non-secret failures."""

    try:
        raw_response = transport(endpoint, payload)
    except TimeoutError:
        return KimiTransportResult(
            status=ExchangeStatus.TIMEOUT,
            error_code="REASONER_TIMEOUT",
            raw_response_bytes=None,
        )
    except Exception:
        return KimiTransportResult(
            status=ExchangeStatus.PROVIDER_ERROR,
            error_code="REASONER_PROVIDER_ERROR",
            raw_response_bytes=None,
        )
    if type(raw_response) is not bytes:
        return KimiTransportResult(
            status=ExchangeStatus.PROVIDER_ERROR,
            error_code="REASONER_PROVIDER_ERROR",
            raw_response_bytes=None,
        )
    return KimiTransportResult(
        status=ExchangeStatus.COMPLETED,
        error_code=None,
        raw_response_bytes=raw_response,
    )


# ---------------------------------------------------------------------------
# Direct MiniMax-M3 lane (issue #91 governance pivot, owner: MS-Mesh).
#
# This is the first direct-provider adapter wired for the assembled engine:
# its exchange identities come from the frozen policy registry (route, prompt,
# and output-schema hashes) and the configured RouteIdentity (model config),
# exactly as BoundedDecisionEngine expects, while the request identity binds
# the real V3 route artifact and the exact provider payload.  The credential
# is host-owned, validated once, and discarded; it never enters a payload,
# receipt, or error string.
# ---------------------------------------------------------------------------

ENV_MINIMAX_API_KEY = "MINIMAX_API_KEY"
PRODUCER_MINIMAX = "esscher.strategy.direct_minimax_host_route"
MINIMAX_PRODUCER_BUILD_SHA256 = sha256_bytes(
    canonical_json_bytes(
        {"contract": "esscher.reasoner_exchange", "producer": PRODUCER_MINIMAX, "version": 1}
    )
)


def load_minimax_route_environment() -> Mapping[str, str]:
    """Read only the direct MiniMax credential; all route values come from bytes."""

    value = os.environ.get(ENV_MINIMAX_API_KEY)
    if not value or not value.strip():
        raise HostRouteConfigurationError(f"missing host environment {ENV_MINIMAX_API_KEY}")
    return {ENV_MINIMAX_API_KEY: value.strip()}


@dataclass(frozen=True, slots=True)
class MinimaxM3Request:
    """Exact canonical MiniMax payload and its immutable request identity."""

    endpoint: str
    payload_bytes: bytes
    request_sha256: str
    route_sha256: str
    model_config_sha256: str
    prompt_sha256: str
    output_schema_sha256: str

    @property
    def payload(self) -> dict[str, object]:
        """Return a fresh decoded payload for a fake or host-owned transport."""

        payload = json.loads(self.payload_bytes)
        if not isinstance(payload, dict):  # Defensive: bytes are built canonically below.
            raise HostRouteInputIntegrityError("direct MiniMax payload is not an object")
        return payload


def _validate_direct_minimax_route(route: ValidatedRoute) -> ProviderRequestPolicy:
    """Defend the MiniMax builder from a hand-forged or drifted packaged route."""

    expected_route = load_approved_reasoner_route_v3()
    # Cached package-object identity is the capability proving this value came
    # from the exact shipped V3 byte pair; field equality alone is forgeable.
    if route is not expected_route:
        raise HostRouteConfigurationError(
            "route is not the exact packaged V3 descriptor and approval validation result"
        )
    if (
        route.provider != MINIMAX_DIRECT_PROVIDER
        or route.model != MINIMAX_DIRECT_MODEL
        or route.model_revision is not None
        or route.base_url != MINIMAX_DIRECT_BASE_URL
    ):
        raise HostRouteConfigurationError(
            "route identity is not the frozen direct MiniMax-M3 route"
        )
    policy = route.provider_request_policy
    if (
        policy.reasoning_effort != MINIMAX_REASONING_EFFORT
        or policy.max_completion_tokens != MINIMAX_MAX_COMPLETION_TOKENS
        or policy.response_format_type != MINIMAX_RESPONSE_FORMAT_TYPE
        or policy.output_schema_name != MINIMAX_RESPONSE_SCHEMA_NAME
        or policy.output_schema_sha256 != reasoner_output_schema_sha256()
        or policy.strict_json_schema is not False
        or policy.tool_choice != MINIMAX_TOOL_CHOICE
        or policy.effective_temperature != MINIMAX_EFFECTIVE_TEMPERATURE
        or policy.effective_top_p != MINIMAX_EFFECTIVE_TOP_P
        or tuple(policy.omitted_request_fields) != MINIMAX_OMITTED_REQUEST_FIELDS
    ):
        raise HostRouteConfigurationError(
            "route does not bind the current direct MiniMax schema policy"
        )
    return policy


def build_minimax_m3_request(
    route: ValidatedRoute, request: ReasonerRouteRequest
) -> MinimaxM3Request:
    """Build the exact canonical direct-MiniMax payload without a credential or network call.

    The wire shape is the owner-probe-verified V3 parameter set: frozen system
    prompt, canonical strategy user payload, ``temperature=0``, ``top_p=1.0``,
    ``max_tokens`` from the frozen policy, ``thinking`` disabled, ``tool_choice``
    none, and ``response_format=json_object`` (the provider markdown-fences
    ``json_schema`` output, so strictness is enforced client-side by the frozen
    six-field validator).
    """

    provider_request_policy = _validate_direct_minimax_route(route)
    if request.ablate_text:
        # Ablation arms belong to the offline fake route; silently ignoring the
        # flag on a live provider call would misreport the exchange identity.
        raise HostRouteConfigurationError(
            "text ablation is not implemented for the direct MiniMax lane"
        )
    snapshot_payload, feature_payload, identities = _canonical_strategy_payload(
        request.strategy_input
    )
    candidate_id = identities["candidate_id"]
    prompt_bytes = reasoner_system_prompt_bytes(candidate_id)
    prompt_sha256 = reasoner_system_prompt_sha256(candidate_id)
    output_schema_sha256 = reasoner_output_schema_sha256()
    user_payload = {
        "feature_receipt": feature_payload,
        "identities": identities,
        "strategy_snapshot": snapshot_payload,
    }
    payload = {
        "max_tokens": provider_request_policy.max_completion_tokens,
        "messages": [
            {"content": prompt_bytes.decode("utf-8"), "role": "system"},
            {"content": canonical_json_bytes(user_payload).decode("utf-8"), "role": "user"},
        ],
        "model": route.model,
        "response_format": {"type": provider_request_policy.response_format_type},
        "temperature": 0,
        "thinking": {"type": "disabled"},
        "tool_choice": provider_request_policy.tool_choice,
        "top_p": 1.0,
    }
    if any(field in payload for field in provider_request_policy.omitted_request_fields):
        raise HostRouteConfigurationError("direct MiniMax payload includes a frozen omitted field")
    payload_bytes = canonical_json_bytes(payload)
    request_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "ablate_text": request.ablate_text,
                "provider_payload": payload,
                "route_identity": {
                    "base_url": route.base_url,
                    "model": route.model,
                    "model_config_sha256": route.model_config_sha256,
                    "output_schema_sha256": output_schema_sha256,
                    "prompt_sha256": prompt_sha256,
                    "provider": route.provider,
                    "route_sha256": route.route_sha256,
                },
                "schema": "esscher.direct_minimax_request_identity",
                "schema_version": 1,
                "strategy_identities": identities,
            }
        )
    )
    return MinimaxM3Request(
        endpoint=f"{route.base_url}/chat/completions",
        payload_bytes=payload_bytes,
        request_sha256=request_sha256,
        route_sha256=route.route_sha256,
        model_config_sha256=route.model_config_sha256,
        prompt_sha256=prompt_sha256,
        output_schema_sha256=output_schema_sha256,
    )


def invoke_minimax_m3_transport(
    request: MinimaxM3Request,
    transport: Callable[[str, dict[str, object]], bytes],
) -> KimiTransportResult:
    """Invoke exactly once with no retry and no provider exception text escape."""

    return _invoke_direct_transport_once(request.endpoint, request.payload, transport)


def unwrap_minimax_response(raw_response_bytes: bytes) -> bytes | None:
    """Extract the decision JSON text from one provider envelope, or None.

    Envelope contract (owner-probe-verified): ``base_resp.status_code == 0``
    when present, and ``choices[0].message.content`` holding the strict
    six-field decision JSON as text.  Anything else - overload (529 surfaces
    through the transport as an exception), fenced output, missing choices -
    is a typed provider error, never a guess and never a retry.
    """

    try:
        envelope = json.loads(raw_response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, dict):
        return None
    base_resp = envelope.get("base_resp")
    if base_resp is not None and (
        not isinstance(base_resp, dict) or base_resp.get("status_code") != 0
    ):
        return None
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    if message.get("reasoning_content"):
        # Thinking output leaked despite the disabled pin: refuse rather than
        # forward ambiguous bytes to the frozen decision validator.
        return None
    return content.encode("utf-8")


class MinimaxM3ReasonerRoute:
    """Owner-approved direct MiniMax-M3 adapter for the assembled engine lane.

    The exchange carries the frozen policy-registry identities (route, prompt,
    output schema) and the configured RouteIdentity model-config hash that
    BoundedDecisionEngine validates, while raw_response_bytes are the unwrapped
    strict decision JSON text.  One call, no retries, no fallback: any failure
    is a typed TIMEOUT/PROVIDER_ERROR exchange and the engine abstains.
    """

    def __init__(
        self,
        *,
        route: ValidatedRoute,
        api_key: str,
        identity: RouteIdentity,
        transport: Callable[[str, dict[str, object]], bytes] | None = None,
    ) -> None:
        if (
            not route.evaluation_eligible
            or route.compatibility_state is not RouteCompatibilityState.COMPATIBLE
        ):
            reason = route.compatibility_reason_code or "ROUTE_NOT_EVALUATION_ELIGIBLE"
            raise HostRouteNotApproved(f"direct MiniMax route is inert: {reason}")
        if not isinstance(api_key, str) or not api_key.strip():
            raise HostRouteSecretBoundaryError("route requires a host-owned credential")
        # Validate and deliberately discard the key: authentication belongs to
        # the host transport process; payloads, arguments, and receipts never
        # carry it.
        _validate_direct_minimax_route(route)
        if (
            type(identity) is not RouteIdentity
            or identity.provider != route.provider
            or identity.model != route.model
            or identity.model_revision != route.model_revision
        ):
            raise HostRouteConfigurationError(
                "route identity must exactly match the frozen direct MiniMax route"
            )
        self._route = route
        self._identity = identity
        self._transport = transport

    @property
    def validated_route(self) -> ValidatedRoute:
        """The frozen validated V3 route this adapter is bound to (credential-free)."""

        return self._route

    @property
    def identity(self) -> RouteIdentity:
        """The configured provider/model identity bound into every exchange."""

        return self._identity

    def __call__(self, request: ReasonerRouteRequest) -> ReasonerRouteResult:
        if self._transport is None:
            raise HostRouteConfigurationError(
                "no transport supplied; a host-owned transport is required outside the "
                "strategy package"
            )
        provider_request = build_minimax_m3_request(self._route, request)
        snapshot = request.strategy_input.snapshot
        route_sha256, prompt_sha256, output_schema_sha256 = reasoner_policy_hashes(
            snapshot.candidate_id
        )
        started_at = request.started_at
        deadline_at = deadline_for(request.strategy_input, started_at)
        transport_started = time.monotonic()
        transport_result = invoke_minimax_m3_transport(provider_request, self._transport)
        responded_at = started_at + timedelta(seconds=time.monotonic() - transport_started)
        common = {
            "event_id": snapshot.event_id,
            "candidate_id": snapshot.candidate_id,
            "policy_sha256": snapshot.policy_sha256,
            "strategy_snapshot_sha256": request.strategy_input.snapshot_sha256,
            "feature_receipt_sha256": request.strategy_input.feature_receipt_sha256,
            "evidence_packet_sha256": snapshot.evidence_packet_sha256,
            "route_sha256": route_sha256,
            "prompt_sha256": prompt_sha256,
            "output_schema_sha256": output_schema_sha256,
            "model_config_sha256": self._identity.model_config_sha256(),
            "request_sha256": provider_request.request_sha256,
            "provider": self._identity.provider,
            "model": self._identity.model,
            "model_revision": self._identity.model_revision,
            "decoding": self._identity.decoding(),
            "started_at": started_at,
            "deadline_at": deadline_at,
            "producer_build_sha256": MINIMAX_PRODUCER_BUILD_SHA256,
            "created_at": deadline_at,
        }
        content: bytes | None = None
        if (
            transport_result.status is ExchangeStatus.COMPLETED
            and responded_at <= deadline_at
            and transport_result.raw_response_bytes is not None
        ):
            content = unwrap_minimax_response(transport_result.raw_response_bytes)
        if content is not None:
            return ReasonerRouteResult(
                exchange=ReasonerExchange(
                    **common,
                    raw_response_sha256=sha256_bytes(content),
                    responded_at=responded_at,
                    status=ExchangeStatus.COMPLETED,
                    error_code=None,
                ),
                raw_response_bytes=content,
            )
        if transport_result.status is ExchangeStatus.TIMEOUT or responded_at > deadline_at:
            status = ExchangeStatus.TIMEOUT
            error_code = "REASONER_TIMEOUT"
        else:
            status = ExchangeStatus.PROVIDER_ERROR
            error_code = "REASONER_PROVIDER_ERROR"
        return ReasonerRouteResult(
            exchange=ReasonerExchange(
                **common,
                raw_response_sha256=None,
                responded_at=None,
                status=status,
                error_code=error_code,
            ),
            raw_response_bytes=None,
        )


class OpenAiCompatibleReasonerRoute:
    """Future host adapter; blocked until the validated route is evaluation eligible."""

    def __init__(
        self,
        *,
        route: ValidatedRoute,
        api_key: str,
        transport: Callable[[str, dict[str, object]], bytes] | None = None,
    ) -> None:
        if (
            not route.evaluation_eligible
            or route.compatibility_state is not RouteCompatibilityState.COMPATIBLE
        ):
            reason = route.compatibility_reason_code or "ROUTE_NOT_EVALUATION_ELIGIBLE"
            raise HostRouteNotApproved(f"direct Kimi route is inert: {reason}")
        if not isinstance(api_key, str) or not api_key.strip():
            raise HostRouteSecretBoundaryError("route requires a host-owned credential")
        # Validate and deliberately discard the key.  A future host transport owns
        # authentication; strategy payloads, arguments, and receipts never do.
        # Issue #90: the adapter also accepts the exact packaged approved V2
        # route object so the production composition can carry the owner-approved
        # binding; the V1 request seam below remains closed for it until the V2
        # engine assembly lands, and any call fails closed rather than falling
        # back to another provider, model, or schema.
        _validate_direct_kimi_route(route, v2=route is load_approved_reasoner_route_v2())
        self._route = route
        self._transport = transport

    @property
    def validated_route(self) -> ValidatedRoute:
        """The frozen validated route this adapter is bound to (credential-free).

        Issue #90: the production composition authenticates the exact approved
        direct-Kimi route through this read-only view before any decision; the
        credential itself was validated and discarded at construction and never
        leaves the host transport.
        """

        return self._route

    def __call__(self, request: ReasonerRouteRequest) -> ReasonerRouteResult:
        if self._transport is None:
            raise HostRouteConfigurationError(
                "no transport supplied; a host-owned transport is required outside the "
                "strategy package"
            )
        provider_request = build_kimi_k3_request(self._route, request)
        started_at = request.started_at
        deadline_at = deadline_for(request.strategy_input, started_at)
        transport_started = time.monotonic()
        transport_result = invoke_kimi_k3_transport(provider_request, self._transport)
        responded_at = started_at + timedelta(seconds=time.monotonic() - transport_started)
        common = {
            "event_id": request.strategy_input.snapshot.event_id,
            "candidate_id": request.strategy_input.snapshot.candidate_id,
            "policy_sha256": request.strategy_input.snapshot.policy_sha256,
            "strategy_snapshot_sha256": request.strategy_input.snapshot_sha256,
            "feature_receipt_sha256": request.strategy_input.feature_receipt_sha256,
            "evidence_packet_sha256": request.strategy_input.snapshot.evidence_packet_sha256,
            "route_sha256": provider_request.route_sha256,
            "prompt_sha256": provider_request.prompt_sha256,
            "output_schema_sha256": provider_request.output_schema_sha256,
            "model_config_sha256": provider_request.model_config_sha256,
            "request_sha256": provider_request.request_sha256,
            "provider": self._route.provider,
            "model": self._route.model,
            "model_revision": self._route.model_revision,
            "decoding": self._route.caller_decoding,
            "started_at": started_at,
            "deadline_at": deadline_at,
            "producer_build_sha256": sha256_bytes(
                canonical_json_bytes(
                    {"contract": "esscher.reasoner_exchange", "producer": PRODUCER, "version": 1}
                )
            ),
            "created_at": deadline_at,
        }
        if (
            transport_result.status is ExchangeStatus.COMPLETED
            and responded_at <= deadline_at
            and transport_result.raw_response_bytes is not None
        ):
            raw_response = transport_result.raw_response_bytes
            return ReasonerRouteResult(
                exchange=ReasonerExchange(
                    **common,
                    raw_response_sha256=sha256_bytes(raw_response),
                    responded_at=responded_at,
                    status=ExchangeStatus.COMPLETED,
                    error_code=None,
                ),
                raw_response_bytes=raw_response,
            )
        if transport_result.status is ExchangeStatus.TIMEOUT or responded_at > deadline_at:
            status = ExchangeStatus.TIMEOUT
            error_code = "REASONER_TIMEOUT"
        else:
            status = ExchangeStatus.PROVIDER_ERROR
            error_code = "REASONER_PROVIDER_ERROR"
        return ReasonerRouteResult(
            exchange=ReasonerExchange(
                **common,
                raw_response_sha256=None,
                responded_at=None,
                status=status,
                error_code=error_code,
            ),
            raw_response_bytes=None,
        )


__all__ = [
    "ENV_API_KEY",
    "ENV_MINIMAX_API_KEY",
    "MINIMAX_PRODUCER_BUILD_SHA256",
    "PRODUCER_MINIMAX",
    "HostRouteConfigurationError",
    "HostRouteError",
    "HostRouteInputIntegrityError",
    "HostRouteNotApproved",
    "HostRouteSecretBoundaryError",
    "KimiK3Request",
    "KimiTransportResult",
    "KimiTransportStatus",
    "MinimaxM3ReasonerRoute",
    "MinimaxM3Request",
    "OpenAiCompatibleReasonerRoute",
    "build_kimi_k3_request",
    "build_kimi_k3_v2_request",
    "build_minimax_m3_request",
    "invoke_kimi_k3_transport",
    "invoke_minimax_m3_transport",
    "load_minimax_route_environment",
    "load_route_environment",
    "unwrap_minimax_response",
]
