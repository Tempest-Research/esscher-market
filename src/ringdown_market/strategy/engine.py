"""Bounded decision engine over frozen strategy inputs.

The engine orchestrates the readable trace
``INPUT -> FEATURE -> REASONER/BASELINE -> VALIDATOR/VETO -> OUTPUT`` with
stable reason codes.  It never reads a wall clock, never retries, invokes the
injected route at most once per joined input, and records every preflight
abort, duplicate call, or provider failure as ``UNCERTAIN``.  It cannot choose
a security, instrument, contract, quantity, price, account, risk, entry, exit,
or broker action.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from ringdown_market.strategy.contracts import (
    canonical_json_bytes,
    reasoner_exchange_payload,
    reasoner_policy_hashes,
    sha256_bytes,
    strategy_decision_bytes,
    validate_reasoner_response,
    validate_strategy_decision,
)
from ringdown_market.strategy.models import (
    DataHealthState,
    EligibilityState,
    ExchangeStatus,
    ReasonerExchange,
    StrategyDecision,
    StrategyInput,
)
from ringdown_market.strategy.reasoner import (
    SYNTHETIC_ROUTE_IDENTITY,
    ReasonerRoute,
    ReasonerRouteRequest,
    RouteIdentity,
    deadline_for,
)

ENGINE_BUILD_SHA256: Final = sha256_bytes(
    canonical_json_bytes(
        {
            "producer": "esscher.strategy.bounded_decision_engine",
            "contract": "esscher.validated_decision",
            "version": 1,
        }
    )
)
_TRACE_SCHEMA: Final = "esscher.decision_trace"
_TRACE_SCHEMA_VERSION: Final = 1


class EngineReason(StrEnum):
    """Stable engine-local reason codes for aborts before or instead of a call."""

    PREFLIGHT_INELIGIBLE = "PREFLIGHT_INELIGIBLE"
    PREFLIGHT_DATA_HEALTH = "PREFLIGHT_DATA_HEALTH"
    START_BEFORE_FEATURE_RECEIPT = "START_BEFORE_FEATURE_RECEIPT"
    START_AFTER_DECISION_CUTOFF = "START_AFTER_DECISION_CUTOFF"
    DUPLICATE_REASONER_CALL = "DUPLICATE_REASONER_CALL"


@dataclass(frozen=True, slots=True)
class EngineOutcome:
    """One deterministic engine pass: decision, exchange receipt, and trace."""

    decision: StrategyDecision
    exchange: ReasonerExchange
    trace: Mapping[str, object]
    route_invoked: bool
    ablate_text: bool = False

    @property
    def decision_bytes(self) -> bytes:
        return strategy_decision_bytes(self.decision)

    @property
    def trace_bytes(self) -> bytes:
        return canonical_json_bytes(self.trace)


def decision_trace_payload(
    *,
    strategy_input: StrategyInput,
    decision: StrategyDecision,
    exchange: ReasonerExchange,
    route_invoked: bool,
    ablate_text: bool,
) -> dict[str, object]:
    """Build the complete canonical decision trace for an engine outcome."""

    snapshot = strategy_input.snapshot
    return {
        "schema": _TRACE_SCHEMA,
        "schema_version": _TRACE_SCHEMA_VERSION,
        "event_id": snapshot.event_id,
        "candidate_id": snapshot.candidate_id,
        "cohort_id": snapshot.cohort_id,
        "ablate_text": ablate_text,
        "stages": [
            {
                "stage": "INPUT",
                "policy_sha256": snapshot.policy_sha256,
                "candidate_manifest_sha256": strategy_input.candidate_manifest_sha256,
                "strategy_snapshot_sha256": strategy_input.snapshot_sha256,
                "feature_receipt_sha256": strategy_input.feature_receipt_sha256,
            },
            {
                "stage": "FEATURE",
                "data_health": snapshot.data_health.value,
                "health_reason_codes": list(snapshot.health_reason_codes),
                "eligibility": snapshot.eligibility.value,
                "feature_ids": [
                    feature.feature_id for feature in strategy_input.feature_receipt.features
                ],
                "feature_statuses": {
                    feature.feature_id: feature.status.value
                    for feature in strategy_input.feature_receipt.features
                },
            },
            {
                "stage": "REASONER",
                "route_invoked": route_invoked,
                "ablate_text": ablate_text,
                "reasoner_exchange_sha256": sha256_bytes(
                    canonical_json_bytes(reasoner_exchange_payload(exchange))
                ),
                "status": exchange.status.value,
                "error_code": exchange.error_code,
            },
            {
                "stage": "VALIDATOR",
                "disposition": decision.disposition.value,
                "reaction_relation": decision.reaction_relation.value,
                "reason_codes": list(decision.reason_codes),
            },
            {
                "stage": "OUTPUT",
                "direction": decision.direction.value,
                "reasoner_direction": (
                    None
                    if decision.reasoner_direction is None
                    else decision.reasoner_direction.value
                ),
                "decision_sha256": sha256_bytes(strategy_decision_bytes(decision)),
            },
        ],
    }


class BoundedDecisionEngine:
    """Injects one bounded reasoner route and applies the frozen validator."""

    def __init__(
        self,
        route: ReasonerRoute,
        *,
        identity: RouteIdentity = SYNTHETIC_ROUTE_IDENTITY,
    ) -> None:
        self._route = route
        self._identity = identity
        self._invoked_keys: set[tuple[str, ...]] = set()

    def _call_key(self, strategy_input: StrategyInput) -> tuple[str, ...]:
        route_sha256, prompt_sha256, output_schema_sha256 = reasoner_policy_hashes(
            strategy_input.snapshot.candidate_id
        )
        return (
            strategy_input.snapshot.event_id,
            strategy_input.snapshot_sha256,
            strategy_input.feature_receipt_sha256,
            route_sha256,
            prompt_sha256,
            output_schema_sha256,
            self._identity.model_config_sha256(),
        )

    def _aborted_exchange(
        self,
        strategy_input: StrategyInput,
        started_at: datetime,
        code: EngineReason,
    ) -> ReasonerExchange:
        snapshot = strategy_input.snapshot
        route_sha256, prompt_sha256, output_schema_sha256 = reasoner_policy_hashes(
            snapshot.candidate_id
        )
        deadline_at = deadline_for(strategy_input, started_at)
        effective_started = min(started_at, deadline_at)
        return ReasonerExchange(
            event_id=snapshot.event_id,
            candidate_id=snapshot.candidate_id,
            policy_sha256=snapshot.policy_sha256,
            strategy_snapshot_sha256=strategy_input.snapshot_sha256,
            feature_receipt_sha256=strategy_input.feature_receipt_sha256,
            evidence_packet_sha256=snapshot.evidence_packet_sha256,
            route_sha256=route_sha256,
            prompt_sha256=prompt_sha256,
            output_schema_sha256=output_schema_sha256,
            model_config_sha256=self._identity.model_config_sha256(),
            request_sha256=sha256_bytes(
                canonical_json_bytes({"aborted": code.value, "route_invoked": False})
            ),
            raw_response_sha256=None,
            provider=self._identity.provider,
            model=self._identity.model,
            model_revision=self._identity.model_revision,
            decoding=self._identity.decoding(),
            started_at=effective_started,
            responded_at=None,
            deadline_at=deadline_at,
            status=ExchangeStatus.CANCELED,
            error_code=code.value,
            producer_build_sha256=ENGINE_BUILD_SHA256,
            created_at=deadline_at,
        )

    def _preflight_code(
        self, strategy_input: StrategyInput, started_at: datetime
    ) -> EngineReason | None:
        snapshot = strategy_input.snapshot
        if snapshot.eligibility is not EligibilityState.ELIGIBLE:
            return EngineReason.PREFLIGHT_INELIGIBLE
        if snapshot.data_health is not DataHealthState.VALID:
            return EngineReason.PREFLIGHT_DATA_HEALTH
        if started_at < strategy_input.feature_receipt.created_at:
            return EngineReason.START_BEFORE_FEATURE_RECEIPT
        if started_at > snapshot.decision_cutoff_at:
            return EngineReason.START_AFTER_DECISION_CUTOFF
        return None

    def decide(
        self,
        strategy_input: StrategyInput,
        *,
        started_at: datetime,
        ablate_text: bool = False,
    ) -> EngineOutcome:
        """Run one bounded pass and return the validated decision with a trace."""

        key = self._call_key(strategy_input)
        preflight = self._preflight_code(strategy_input, started_at)
        duplicate = key in self._invoked_keys
        route_invoked = False

        if preflight is not None or duplicate:
            code = preflight if preflight is not None else EngineReason.DUPLICATE_REASONER_CALL
            exchange = self._aborted_exchange(strategy_input, started_at, code)
            decision = validate_strategy_decision(
                strategy_input,
                exchange,
                None,
                validator_build_sha256=ENGINE_BUILD_SHA256,
                reasoner_error_code=code.value,
            )
        else:
            result = self._route(
                ReasonerRouteRequest(
                    strategy_input=strategy_input,
                    started_at=started_at,
                    ablate_text=ablate_text,
                )
            )
            route_invoked = True
            self._invoked_keys.add(key)
            exchange = result.exchange
            decision = validate_reasoner_response(
                strategy_input,
                exchange,
                result.raw_response_bytes,
                validator_build_sha256=ENGINE_BUILD_SHA256,
            )

        trace = decision_trace_payload(
            strategy_input=strategy_input,
            decision=decision,
            exchange=exchange,
            route_invoked=route_invoked,
            ablate_text=ablate_text,
        )
        return EngineOutcome(
            decision=decision,
            exchange=exchange,
            trace=trace,
            route_invoked=route_invoked,
            ablate_text=ablate_text,
        )
