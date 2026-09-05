"""Structured, provider-neutral bounded reasoner routes.

The route protocol is the only external reasoning boundary of the decision
engine.  Routes are injected, bounded by the frozen route/prompt/output-schema
and model-config hashes, carry no broker or account authority, and have no
transparent fallback: every failure mode is recorded as a stable exchange
status and reason code, never as a guessed direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from esscher.alpha.models import Direction
from esscher.strategy.contracts import (
    canonical_json_bytes,
    reasoner_model_config_sha256,
    reasoner_policy_hashes,
    sha256_bytes,
)
from esscher.strategy.models import (
    DecodingParameters,
    ExchangeStatus,
    FeatureStatus,
    ReasonerExchange,
    StrategyInput,
)
from esscher.strategy.policy import load_strategy_policy

FAKE_PROVIDER: Final = "esscher.synthetic_provider"
FAKE_MODEL: Final = "deterministic-fake-v1"
FAKE_SEED: Final = 7
_EARNINGS_CANDIDATE: Final = "EARNINGS_RESIDUAL_CONTINUATION_V1"
_MACRO_CANDIDATE: Final = "MACRO_SPY_CONTINUATION_CHALLENGER_V1"
_EARNINGS_CONFIRMATION: Final = "market.opening_residual_log_return.v1"
_MACRO_CONFIRMATION: Final = "market.spy_event_zscore_60.v1"


class FakeFailure(StrEnum):
    """Injectable deterministic failure modes for the fake route."""

    NONE = "NONE"
    TIMEOUT = "TIMEOUT"
    CANCELED = "CANCELED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    LATE_RESPONSE = "LATE_RESPONSE"
    MALFORMED_JSON = "MALFORMED_JSON"
    HOSTILE_FIELDS = "HOSTILE_FIELDS"
    RAW_HASH_DRIFT = "RAW_HASH_DRIFT"


@dataclass(frozen=True, slots=True)
class RouteIdentity:
    """Configured provider/model identity bound into every exchange receipt."""

    provider: str
    model: str
    model_revision: str | None = None

    def decoding(self) -> DecodingParameters:
        call_policy = load_strategy_policy().data["reasoner"]["call_policy"]
        return DecodingParameters(
            temperature=Decimal(str(call_policy["temperature"])),
            top_p=Decimal("1"),
            max_output_tokens=int(call_policy["max_output_tokens"]),
            seed=FAKE_SEED,
        )

    def model_config_sha256(self) -> str:
        return reasoner_model_config_sha256(
            provider=self.provider,
            model=self.model,
            model_revision=self.model_revision,
            decoding=self.decoding(),
        )


SYNTHETIC_ROUTE_IDENTITY: Final = RouteIdentity(provider=FAKE_PROVIDER, model=FAKE_MODEL)


@dataclass(frozen=True, slots=True)
class ReasonerRouteRequest:
    """One bounded reasoner invocation request with supplied clocks only."""

    strategy_input: StrategyInput
    started_at: datetime
    ablate_text: bool = False


@dataclass(frozen=True, slots=True)
class ReasonerRouteResult:
    """Immutable exchange receipt plus the exact raw provider bytes."""

    exchange: ReasonerExchange
    raw_response_bytes: bytes | None


@runtime_checkable
class ReasonerRoute(Protocol):
    """Injected structured reasoner boundary; implementations must be bounded."""

    def __call__(self, request: ReasonerRouteRequest) -> ReasonerRouteResult: ...


def hard_timeout_seconds() -> int:
    return int(load_strategy_policy().data["reasoner"]["call_policy"]["hard_timeout_seconds"])


def deadline_for(strategy_input: StrategyInput, started_at: datetime) -> datetime:
    return min(
        started_at + timedelta(seconds=hard_timeout_seconds()),
        strategy_input.snapshot.decision_cutoff_at,
    )


def _numeric_direction(strategy_input: StrategyInput) -> Direction:
    feature_id = (
        _EARNINGS_CONFIRMATION
        if strategy_input.snapshot.candidate_id == _EARNINGS_CANDIDATE
        else _MACRO_CONFIRMATION
    )
    feature = strategy_input.feature_by_id.get(feature_id)
    if (
        feature is None
        or feature.status is not FeatureStatus.PRESENT
        or not isinstance(feature.value, Decimal)
        or feature.value == 0
    ):
        return Direction.UNCERTAIN
    return Direction.UP if feature.value > 0 else Direction.DOWN


def _cited_evidence_ids(strategy_input: StrategyInput) -> tuple[str, ...]:
    primary = sorted(
        ref.evidence_id for ref in strategy_input.snapshot.evidence_refs if ref.role.is_primary
    )
    market = sorted(
        ref.evidence_id for ref in strategy_input.snapshot.evidence_refs if ref.role.is_market
    )
    if not primary or not market:
        return ()
    return tuple(sorted({primary[0], market[0]}))


def fake_decision_payload(strategy_input: StrategyInput, *, ablate_text: bool) -> dict[str, object]:
    """Deterministic synthetic reasoner output derived from frozen features."""

    cited = _cited_evidence_ids(strategy_input)
    direction = _numeric_direction(strategy_input)
    if not cited:
        direction = Direction.UNCERTAIN
    market_id = cited[-1] if cited else None
    summary = (
        "Numeric-only ablation: structured feature records and data-health facts only."
        if ablate_text
        else "The synchronized opening reaction and primary evidence agree on direction."
    )
    return {
        "contradictions": [],
        "decision": direction.value,
        "evidence_ids": list(cited),
        "strongest_falsifier": (
            None
            if market_id is None
            else {
                "evidence_id": market_id,
                "summary": "The confirmed reaction could fade after the decision cutoff.",
            }
        ),
        "summary": summary,
        "unknowns": [],
    }


class DeterministicFakeReasoner:
    """Offline fake route with injectable failure modes; no network or broker."""

    def __init__(
        self,
        failure: FakeFailure = FakeFailure.NONE,
        identity: RouteIdentity = SYNTHETIC_ROUTE_IDENTITY,
    ) -> None:
        self._failure = failure
        self._identity = identity

    def __call__(self, request: ReasonerRouteRequest) -> ReasonerRouteResult:
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
                    {
                        "producer": "esscher.strategy.deterministic_fake_reasoner",
                        "contract": "esscher.reasoner_exchange",
                        "version": 1,
                    }
                )
            ),
            "created_at": deadline_at,
        }

        failure = self._failure
        if failure in (FakeFailure.TIMEOUT, FakeFailure.CANCELED, FakeFailure.PROVIDER_ERROR):
            status = {
                FakeFailure.TIMEOUT: ExchangeStatus.TIMEOUT,
                FakeFailure.CANCELED: ExchangeStatus.CANCELED,
                FakeFailure.PROVIDER_ERROR: ExchangeStatus.PROVIDER_ERROR,
            }[failure]
            exchange = ReasonerExchange(
                **common,
                raw_response_sha256=None,
                responded_at=None,
                status=status,
                error_code={
                    ExchangeStatus.TIMEOUT: "REASONER_TIMEOUT",
                    ExchangeStatus.CANCELED: "REASONER_CANCELED",
                    ExchangeStatus.PROVIDER_ERROR: "REASONER_PROVIDER_ERROR",
                }[status],
            )
            return ReasonerRouteResult(exchange=exchange, raw_response_bytes=None)

        payload = fake_decision_payload(strategy_input, ablate_text=request.ablate_text)
        if failure is FakeFailure.HOSTILE_FIELDS:
            payload = {**payload, "quantity": 1, "strike": 100}
        if failure is FakeFailure.MALFORMED_JSON:
            raw = b"{not canonical json"
        else:
            raw = canonical_json_bytes(payload)
        raw_sha256 = sha256_bytes(raw)
        if failure is FakeFailure.RAW_HASH_DRIFT:
            raw_sha256 = sha256_bytes(b"drifted provider bytes")
        responded_at = (
            deadline_at + timedelta(seconds=1)
            if failure is FakeFailure.LATE_RESPONSE
            else started_at + timedelta(seconds=min(5, hard_timeout_seconds() - 1))
        )
        exchange = ReasonerExchange(
            **common,
            raw_response_sha256=raw_sha256,
            responded_at=responded_at,
            status=ExchangeStatus.COMPLETED,
            error_code=None,
        )
        return ReasonerRouteResult(exchange=exchange, raw_response_bytes=raw)
