"""Matched Gate C baseline arms as pure outputs of one frozen snapshot.

Every arm consumes the same ``StrategyInput`` and decision cutoff and emits a
canonical ``esscher.baseline_signal/v1`` record with ``execution_authority``
always false.  Placebo and ablation arms are evaluation-only and can never
enter the production permit path.  Baseline IDs and order are read from the
frozen policy; no arm performs imputation, selection, or broker work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from esscher.alpha.models import Direction
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes
from esscher.strategy.engine import BoundedDecisionEngine, EngineOutcome
from esscher.strategy.models import (
    FeatureStatus,
    GuidanceDirection,
    StrategyInput,
)
from esscher.strategy.policy import load_strategy_policy
from esscher.strategy.reasoner import ReasonerRoute, RouteIdentity

_SIGNAL_SCHEMA: Final = "esscher.baseline_signal"
_BUNDLE_SCHEMA: Final = "esscher.gate_c_baseline_bundle"
_SCHEMA_VERSION: Final = 1
BASELINES_BUILD_SHA256: Final = sha256_bytes(
    canonical_json_bytes(
        {
            "producer": "esscher.strategy.baselines",
            "contract": _SIGNAL_SCHEMA,
            "version": _SCHEMA_VERSION,
        }
    )
)
_PARSER_ZSCORE_THRESHOLD: Final = Decimal("0.5")
_SEEDED_CONTROL_COUNT: Final = 256
_EARNINGS_CANDIDATE: Final = "EARNINGS_RESIDUAL_CONTINUATION_V1"
_MACRO_CANDIDATE: Final = "MACRO_SPY_CONTINUATION_CHALLENGER_V1"


@dataclass(frozen=True, slots=True)
class BaselineSignal:
    """One deterministic baseline arm output with no execution authority."""

    baseline_id: str
    event_id: str
    candidate_id: str
    cohort_id: str
    direction: Direction
    reason_codes: tuple[str, ...]
    policy_sha256: str
    strategy_snapshot_sha256: str
    feature_receipt_sha256: str
    producer_build_sha256: str
    evaluation_only: bool
    control_index: int | None = None


def baseline_signal_payload(value: BaselineSignal) -> dict[str, object]:
    return {
        "schema": _SIGNAL_SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "baseline_id": value.baseline_id,
        "control_index": value.control_index,
        "event_id": value.event_id,
        "candidate_id": value.candidate_id,
        "cohort_id": value.cohort_id,
        "direction": value.direction.value,
        "reason_codes": list(value.reason_codes),
        "policy_sha256": value.policy_sha256,
        "strategy_snapshot_sha256": value.strategy_snapshot_sha256,
        "feature_receipt_sha256": value.feature_receipt_sha256,
        "producer_build_sha256": value.producer_build_sha256,
        "execution_authority": False,
        "evaluation_only": value.evaluation_only,
    }


def baseline_signal_bytes(value: BaselineSignal) -> bytes:
    return canonical_json_bytes(baseline_signal_payload(value))


def baseline_signal_sha256(value: BaselineSignal) -> str:
    return sha256_bytes(baseline_signal_bytes(value))


def frozen_baseline_ids() -> tuple[str, ...]:
    baselines = load_strategy_policy().data["baselines"]
    return tuple(str(item["baseline_id"]) for item in baselines)


def _signal(
    strategy_input: StrategyInput,
    baseline_id: str,
    direction: Direction,
    reason_codes: tuple[str, ...],
    *,
    evaluation_only: bool,
    control_index: int | None = None,
) -> BaselineSignal:
    return BaselineSignal(
        baseline_id=baseline_id,
        event_id=strategy_input.snapshot.event_id,
        candidate_id=strategy_input.snapshot.candidate_id,
        cohort_id=strategy_input.snapshot.cohort_id,
        direction=direction,
        reason_codes=tuple(sorted(reason_codes)),
        policy_sha256=strategy_input.snapshot.policy_sha256,
        strategy_snapshot_sha256=strategy_input.snapshot_sha256,
        feature_receipt_sha256=strategy_input.feature_receipt_sha256,
        producer_build_sha256=BASELINES_BUILD_SHA256,
        evaluation_only=evaluation_only,
        control_index=control_index,
    )


def _decimal_feature(strategy_input: StrategyInput, feature_id: str) -> Decimal | None:
    feature = strategy_input.feature_by_id.get(feature_id)
    if (
        feature is None
        or feature.status is not FeatureStatus.PRESENT
        or not isinstance(feature.value, Decimal)
    ):
        return None
    return feature.value


def cash_signal(strategy_input: StrategyInput) -> BaselineSignal:
    return _signal(
        strategy_input,
        "CASH_ALWAYS_UNCERTAIN",
        Direction.UNCERTAIN,
        ("CASH_BASELINE_ABSTAINS",),
        evaluation_only=False,
    )


def price_continuation_signal(strategy_input: StrategyInput) -> BaselineSignal:
    policy = load_strategy_policy()
    candidate_id = strategy_input.snapshot.candidate_id
    if candidate_id == _EARNINGS_CANDIDATE:
        residual = _decimal_feature(strategy_input, "market.opening_residual_log_return.v1")
        if residual is None:
            return _signal(
                strategy_input,
                "PRICE_CONTINUATION",
                Direction.UNCERTAIN,
                ("CONFIRMATION_FEATURE_UNAVAILABLE",),
                evaluation_only=False,
            )
        epsilon = Decimal(str(policy.threshold(candidate_id, "opening_residual_epsilon")))
        passed = abs(residual) >= epsilon
        direction = Direction.UP if residual > 0 else Direction.DOWN
    else:
        zscore = _decimal_feature(strategy_input, "market.spy_event_zscore_60.v1")
        volume = _decimal_feature(strategy_input, "market.spy_event_volume_ratio_20.v1")
        vwap = _decimal_feature(strategy_input, "market.spy_event_vwap_distance_bps.v1")
        if zscore is None or volume is None or vwap is None:
            return _signal(
                strategy_input,
                "PRICE_CONTINUATION",
                Direction.UNCERTAIN,
                ("CONFIRMATION_FEATURE_UNAVAILABLE",),
                evaluation_only=False,
            )
        z_minimum = Decimal(str(policy.threshold(candidate_id, "event_zscore_min_abs")))
        volume_minimum = Decimal(str(policy.threshold(candidate_id, "event_volume_ratio_min")))
        passed = (
            volume >= volume_minimum
            and abs(zscore) >= z_minimum
            and ((zscore > 0 and vwap > 0) or (zscore < 0 and vwap < 0))
        )
        direction = Direction.UP if zscore > 0 else Direction.DOWN
    if not passed:
        return _signal(
            strategy_input,
            "PRICE_CONTINUATION",
            Direction.UNCERTAIN,
            ("CONTINUATION_THRESHOLD_NOT_MET",),
            evaluation_only=False,
        )
    return _signal(strategy_input, "PRICE_CONTINUATION", direction, (), evaluation_only=False)


def price_reversal_signal(strategy_input: StrategyInput) -> BaselineSignal:
    continuation = price_continuation_signal(strategy_input)
    if continuation.direction is Direction.UNCERTAIN:
        return _signal(
            strategy_input,
            "PRICE_REVERSAL",
            Direction.UNCERTAIN,
            continuation.reason_codes,
            evaluation_only=False,
        )
    reversed_direction = Direction.DOWN if continuation.direction is Direction.UP else Direction.UP
    return _signal(strategy_input, "PRICE_REVERSAL", reversed_direction, (), evaluation_only=False)


def _earnings_parser_votes(strategy_input: StrategyInput) -> tuple[int, bool]:
    votes = 0
    available = 0
    sue = _decimal_feature(strategy_input, "earnings.eps_timeseries_sue.v1")
    revenue = _decimal_feature(strategy_input, "earnings.revenue_yoy_pct.v1")
    guidance = strategy_input.feature_by_id.get("earnings.guidance_direction.v1")
    for value in (sue, revenue):
        if value is None:
            continue
        available += 1
        if value > 0:
            votes += 1
        elif value < 0:
            votes -= 1
    if (
        guidance is not None
        and guidance.status is FeatureStatus.PRESENT
        and isinstance(guidance.value, GuidanceDirection)
    ):
        available += 1
        if guidance.value is GuidanceDirection.RAISED:
            votes += 1
        elif guidance.value is GuidanceDirection.LOWERED:
            votes -= 1
    return votes, available >= 2


def _macro_parser_votes(strategy_input: StrategyInput) -> tuple[int, bool]:
    vector = strategy_input.feature_by_id.get("macro.consensus_surprise_vector.v1")
    if vector is None or vector.status is not FeatureStatus.PRESENT or not vector.components:
        return 0, False
    votes = 0
    for component in vector.components:
        if component.status is not FeatureStatus.PRESENT or not isinstance(
            component.value, Decimal
        ):
            continue
        if component.value >= _PARSER_ZSCORE_THRESHOLD:
            votes -= 1
        elif component.value <= -_PARSER_ZSCORE_THRESHOLD:
            votes += 1
    return votes, len(vector.components) >= 2


def deterministic_parser_signal(strategy_input: StrategyInput) -> BaselineSignal:
    if strategy_input.snapshot.candidate_id == _EARNINGS_CANDIDATE:
        votes, enough = _earnings_parser_votes(strategy_input)
    else:
        votes, enough = _macro_parser_votes(strategy_input)
    if not enough or abs(votes) < 2:
        return _signal(
            strategy_input,
            "DETERMINISTIC_PARSER",
            Direction.UNCERTAIN,
            ("PARSER_VOTES_INCONCLUSIVE",),
            evaluation_only=False,
        )
    direction = Direction.UP if votes > 0 else Direction.DOWN
    return _signal(strategy_input, "DETERMINISTIC_PARSER", direction, (), evaluation_only=False)


def bounded_llm_signal(strategy_input: StrategyInput, outcome: EngineOutcome) -> BaselineSignal:
    return _signal(
        strategy_input,
        "BOUNDED_LLM",
        outcome.decision.direction,
        outcome.decision.reason_codes,
        evaluation_only=False,
    )


def no_text_ablation_signal(
    strategy_input: StrategyInput, outcome: EngineOutcome
) -> BaselineSignal:
    return _signal(
        strategy_input,
        "NO_TEXT_ABLATION",
        outcome.decision.direction,
        outcome.decision.reason_codes,
        evaluation_only=True,
    )


def opposite_llm_placebo_signal(
    strategy_input: StrategyInput, bounded: BaselineSignal
) -> BaselineSignal:
    if bounded.direction is Direction.UNCERTAIN:
        return _signal(
            strategy_input,
            "OPPOSITE_LLM_PLACEBO",
            Direction.UNCERTAIN,
            bounded.reason_codes,
            evaluation_only=True,
        )
    inverted = Direction.DOWN if bounded.direction is Direction.UP else Direction.UP
    return _signal(
        strategy_input,
        "OPPOSITE_LLM_PLACEBO",
        inverted,
        (*bounded.reason_codes, "PLACEBO_OPPOSITE_INVERSION"),
        evaluation_only=True,
    )


def seeded_random_placebo_controls(
    strategy_input: StrategyInput, bounded: BaselineSignal
) -> tuple[BaselineSignal, ...]:
    controls: list[BaselineSignal] = []
    for counter in range(_SEEDED_CONTROL_COUNT):
        digest = sha256_bytes(
            canonical_json_bytes(
                {
                    "policy_sha256": strategy_input.snapshot.policy_sha256,
                    "event_id": strategy_input.snapshot.event_id,
                    "counter": f"{counter:03d}",
                }
            )
        )
        if bounded.direction is Direction.UNCERTAIN:
            direction = Direction.UNCERTAIN
            reasons: tuple[str, ...] = bounded.reason_codes
        else:
            direction = Direction.UP if int(digest, 16) % 2 == 0 else Direction.DOWN
            reasons = ("PLACEBO_SEEDED_CONTROL",)
        controls.append(
            _signal(
                strategy_input,
                "SEEDED_RANDOM_PLACEBO_256",
                direction,
                reasons,
                evaluation_only=True,
                control_index=counter,
            )
        )
    return tuple(controls)


@dataclass(frozen=True, slots=True)
class GateCBaselineBundle:
    """All matched arms plus seeded controls for one snapshot and cutoff."""

    signals: tuple[BaselineSignal, ...]
    controls: tuple[BaselineSignal, ...]

    @property
    def payload(self) -> dict[str, object]:
        return {
            "schema": _BUNDLE_SCHEMA,
            "schema_version": _SCHEMA_VERSION,
            "signals": [baseline_signal_payload(signal) for signal in self.signals],
            "controls": [baseline_signal_payload(control) for control in self.controls],
        }

    @property
    def bytes(self) -> bytes:
        return canonical_json_bytes(self.payload)


def compile_gate_c_signals(
    strategy_input: StrategyInput,
    *,
    route: ReasonerRoute,
    started_at: datetime,
    ablation_route: ReasonerRoute | None = None,
    identity: RouteIdentity | None = None,
) -> GateCBaselineBundle:
    """Run every frozen baseline arm for one snapshot and cutoff.

    The bounded-LLM arm and the text ablation arm each use a dedicated engine
    instance so the production call fence is never consumed by evaluation.
    """

    kwargs = {} if identity is None else {"identity": identity}
    bounded_engine = BoundedDecisionEngine(route, **kwargs)
    bounded_outcome = bounded_engine.decide(strategy_input, started_at=started_at)
    ablation_engine = BoundedDecisionEngine(
        route if ablation_route is None else ablation_route, **kwargs
    )
    ablation_outcome = ablation_engine.decide(
        strategy_input, started_at=started_at, ablate_text=True
    )

    bounded = bounded_llm_signal(strategy_input, bounded_outcome)
    signals = []
    for baseline_id in frozen_baseline_ids():
        if baseline_id == "CASH_ALWAYS_UNCERTAIN":
            signals.append(cash_signal(strategy_input))
        elif baseline_id == "PRICE_CONTINUATION":
            signals.append(price_continuation_signal(strategy_input))
        elif baseline_id == "PRICE_REVERSAL":
            signals.append(price_reversal_signal(strategy_input))
        elif baseline_id == "DETERMINISTIC_PARSER":
            signals.append(deterministic_parser_signal(strategy_input))
        elif baseline_id == "BOUNDED_LLM":
            signals.append(bounded)
        elif baseline_id == "NO_TEXT_ABLATION":
            signals.append(no_text_ablation_signal(strategy_input, ablation_outcome))
        elif baseline_id == "OPPOSITE_LLM_PLACEBO":
            signals.append(opposite_llm_placebo_signal(strategy_input, bounded))
    return GateCBaselineBundle(
        signals=tuple(signals),
        controls=seeded_random_placebo_controls(strategy_input, bounded),
    )
