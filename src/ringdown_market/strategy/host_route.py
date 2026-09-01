"""Host-managed bounded reasoner route adapter boundary.

The adapter is the only external reasoning boundary.  It is inert unless the
packaged approval receipt is APPROVED and the host supplies configuration via
environment variables; credentials never enter repository artifacts, exchange
receipts, or application arguments.  The adapter enforces the frozen call
policy client-side (one call, zero retries, hard timeout, bounded decoding) and
records an immutable exchange receipt identical in shape to the deterministic
fake.  No live call is made unless a transport is explicitly provided.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from datetime import timedelta

from .contracts import (
    canonical_json_bytes,
    reasoner_policy_hashes,
    sha256_bytes,
)
from .models import ExchangeStatus, ReasonerExchange
from .reasoner import (
    ReasonerRouteRequest,
    ReasonerRouteResult,
    RouteIdentity,
    deadline_for,
)

ENV_API_KEY = "ESSCHER_REASONER_API_KEY"
ENV_BASE_URL = "ESSCHER_REASONER_BASE_URL"
ENV_MODEL = "ESSCHER_REASONER_MODEL"

PRODUCER = "esscher.strategy.host_reasoner_route"


class HostRouteError(RuntimeError):
    """Base error for the host-managed reasoner route boundary."""


class HostRouteConfigurationError(HostRouteError):
    """Host environment is missing or malformed."""


class HostRouteNotApproved(HostRouteError):
    """The packaged approval receipt is not APPROVED."""


class HostRouteSecretBoundaryError(HostRouteError):
    """A secret would cross the repository/application boundary."""


def load_route_environment() -> Mapping[str, str]:
    """Read host configuration; fail closed when absent or unsafe."""

    values: dict[str, str] = {}
    for name in (ENV_API_KEY, ENV_BASE_URL, ENV_MODEL):
        value = os.environ.get(name)
        if not value or not value.strip():
            raise HostRouteConfigurationError(f"missing host environment {name}")
        values[name] = value.strip()
    base_url = values[ENV_BASE_URL]
    if not base_url.startswith("https://"):
        raise HostRouteConfigurationError("host base url must be public HTTPS")
    return values


class OpenAiCompatibleReasonerRoute:
    """Bounded OpenAI-compatible reasoner route with an injectable transport."""

    def __init__(
        self,
        *,
        identity: RouteIdentity,
        base_url: str,
        api_key: str,
        evaluation_eligible: bool,
        transport: Callable[[Mapping[str, object]], bytes] | None = None,
    ) -> None:
        if not evaluation_eligible:
            raise HostRouteNotApproved(
                "the packaged approval receipt is not APPROVED; route is inert"
            )
        if not api_key or not api_key.strip():
            raise HostRouteSecretBoundaryError("route requires a host-owned credential")
        self._identity = identity
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    def __call__(self, request: ReasonerRouteRequest) -> ReasonerRouteResult:
        if self._transport is None:
            raise HostRouteConfigurationError(
                "no transport supplied; a live provider call requires a "
                "host-approved transport outside the strategy package"
            )
        strategy_input = request.strategy_input
        started_at = request.started_at
        deadline_at = deadline_for(strategy_input, started_at)
        route_sha256, prompt_sha256, output_schema_sha256 = reasoner_policy_hashes(
            strategy_input.snapshot.candidate_id
        )
        decoding = self._identity.decoding()
        request_sha256 = sha256_bytes(
            canonical_json_bytes(
                {
                    "ablate_text": request.ablate_text,
                    "candidate_id": strategy_input.snapshot.candidate_id,
                    "event_id": strategy_input.snapshot.event_id,
                    "feature_receipt_sha256": strategy_input.feature_receipt_sha256,
                    "model_config_sha256": self._identity.model_config_sha256(),
                    "output_schema_sha256": output_schema_sha256,
                    "policy_sha256": strategy_input.snapshot.policy_sha256,
                    "prompt_sha256": prompt_sha256,
                    "route_sha256": route_sha256,
                    "strategy_snapshot_sha256": strategy_input.snapshot_sha256,
                }
            )
        )
        common = {
            "event_id": strategy_input.snapshot.event_id,
            "candidate_id": strategy_input.snapshot.candidate_id,
            "policy_sha256": strategy_input.snapshot.policy_sha256,
            "strategy_snapshot_sha256": strategy_input.snapshot_sha256,
            "feature_receipt_sha256": strategy_input.feature_receipt_sha256,
            "evidence_packet_sha256": strategy_input.snapshot.evidence_packet_sha256,
            "route_sha256": route_sha256,
            "prompt_sha256": prompt_sha256,
            "output_schema_sha256": output_schema_sha256,
            "model_config_sha256": self._identity.model_config_sha256(),
            "request_sha256": request_sha256,
            "provider": self._identity.provider,
            "model": self._identity.model,
            "model_revision": self._identity.model_revision,
            "decoding": decoding,
            "started_at": started_at,
            "deadline_at": deadline_at,
            "producer_build_sha256": sha256_bytes(
                canonical_json_bytes(
                    {"producer": PRODUCER, "contract": "esscher.reasoner_exchange", "version": 1}
                )
            ),
            "created_at": deadline_at,
        }

        transport = self._transport
        payload = {
            "model": self._identity.model,
            "messages": [{"role": "user", "content": _prompt_content(strategy_input)}],
            "temperature": float(decoding.temperature),
            "top_p": float(decoding.top_p),
            "max_tokens": decoding.max_output_tokens,
            "seed": decoding.seed,
        }
        monotonic_start = time.monotonic()
        try:
            raw = transport(payload)
        except Exception:
            raw = None
        elapsed = time.monotonic() - monotonic_start
        responded_at = started_at + timedelta(seconds=elapsed)

        if raw is None or responded_at > deadline_at:
            return ReasonerRouteResult(
                exchange=ReasonerExchange(
                    **common,
                    raw_response_sha256=None,
                    responded_at=None,
                    status=ExchangeStatus.TIMEOUT,
                    error_code="REASONER_TIMEOUT",
                ),
                raw_response_bytes=None,
            )
        return ReasonerRouteResult(
            exchange=ReasonerExchange(
                **common,
                raw_response_sha256=sha256_bytes(raw),
                responded_at=responded_at,
                status=ExchangeStatus.COMPLETED,
                error_code=None,
            ),
            raw_response_bytes=raw,
        )


def _prompt_content(strategy_input: object) -> str:
    return canonical_json_bytes(
        {
            "candidate_id": strategy_input.snapshot.candidate_id,
            "feature_receipt_sha256": strategy_input.feature_receipt_sha256,
            "strategy_snapshot_sha256": strategy_input.snapshot_sha256,
        }
    ).decode("utf-8")


__all__ = [
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_MODEL",
    "HostRouteConfigurationError",
    "HostRouteError",
    "HostRouteNotApproved",
    "HostRouteSecretBoundaryError",
    "OpenAiCompatibleReasonerRoute",
    "load_route_environment",
]
