"""Synthetic confirmation and derived-opportunity bridge for autonomous PAPER runs.

This module joins the frozen V1 confirmation contract to the V2 defined-risk
allocation surface without granting the reasoner any confirmation authority.
The confirmation rule is deterministic, content-addressed, and permanently
labelled synthetic: it proves rehearsal plumbing only and is not alpha
evidence.  The derived opportunity never accepts a caller-supplied risk tier
or readiness flag; both are computed from packaged policy and from the
deterministic confirmation result.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from esscher.alpha.models import Direction
from esscher.application.paper_pipeline import PaperPipelineRejected
from esscher.autonomy.universe import DefinedRiskOpportunity, RiskTier
from esscher.contracts.compiled_to_permit import canonical_permit_sha256
from esscher.execution.expression import CompiledExpression, compiled_expression_sha256
from esscher.execution.models import DebitVerticalPermit
from esscher.risk.kernel import RiskAbstentionV2
from esscher.risk.policy import RiskPolicyV2
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes
from esscher.strategy.models import FeatureStatus, StrategyDecision, StrategyInput
from esscher.strategy.policy import load_strategy_policy, strategy_policy_sha256

SYNTHETIC_CONFIRMATION_RULE_ID = "ESSCHER_SYNTHETIC_REHEARSAL_CONFIRMATION_V1"
SYNTHETIC_MAX_DEBIT_PER_CONTRACT = Decimal("2.00")
SYNTHETIC_CONFIRMATION_RULE_SCHEMA = "esscher.synthetic_confirmation_rule"
SYNTHETIC_OPPORTUNITY_SCHEMA = "esscher.synthetic_rehearsal_opportunity"

SYNTHETIC_CONFIRMATION_CONTINUATION = "SYNTHETIC_CONFIRMATION_CONTINUATION"
SYNTHETIC_CONFIRMATION_ABSTAINED = "SYNTHETIC_CONFIRMATION_ABSTAINED"
SYNTHETIC_CONFIRMATION_OPPOSED = "SYNTHETIC_CONFIRMATION_OPPOSED"
SYNTHETIC_CONFIRMATION_NEUTRAL = "SYNTHETIC_CONFIRMATION_NEUTRAL"

_EARNINGS_CANDIDATE = "EARNINGS_RESIDUAL_CONTINUATION_V1"
# The V3 delayed-capture demo candidate (owner-approved 2026-09-04, #68/#101)
# reuses the identical confirmation feature and epsilon threshold ids; only its
# policy generation (and therefore the threshold lookup source) differs.
_EARNINGS_CANDIDATE_V3 = "EARNINGS_RESIDUAL_CONTINUATION_V3"
_MACRO_CANDIDATE = "MACRO_SPY_CONTINUATION_CHALLENGER_V1"
_CONFIRMATION_FEATURE_IDS = {
    _EARNINGS_CANDIDATE: "market.opening_residual_log_return.v1",
    _EARNINGS_CANDIDATE_V3: "market.opening_residual_log_return.v1",
    _MACRO_CANDIDATE: "market.spy_event_zscore_60.v1",
}
_CONFIRMATION_EPSILON_THRESHOLD_IDS = {
    _EARNINGS_CANDIDATE: "opening_residual_epsilon",
    _EARNINGS_CANDIDATE_V3: "opening_residual_epsilon",
    _MACRO_CANDIDATE: "event_zscore_min_abs",
}


class SyntheticConfirmationRejected(ValueError):
    """Raised when the synthetic confirmation contract cannot be evaluated."""


class SyntheticConfirmationAbstained(PaperPipelineRejected):
    """A deterministic synthetic confirmation refused to confirm a direction."""

    def __init__(self, confirmation: SyntheticConfirmation) -> None:
        super().__init__(
            "synthetic confirmation did not confirm the engine direction: "
            f"{confirmation.reason_code}"
        )
        self.confirmation = confirmation


class RiskAbstentionRejected(PaperPipelineRejected):
    """A V2 risk abstention surfaced as a pipeline rejection carrying its codes."""

    def __init__(self, abstention: RiskAbstentionV2) -> None:
        codes = ", ".join(str(code) for code in abstention.reason_codes)
        super().__init__(f"risk kernel V2 abstained before mutation: {codes}")
        self.abstention = abstention


@dataclass(frozen=True, slots=True)
class SyntheticConfirmation:
    """One deterministic confirmation result with its content-addressed rule."""

    rule_id: str
    rule_sha256: str
    candidate_id: str
    direction: Direction
    confirmed: bool
    reason_code: str
    confirmation_value: Decimal | None
    epsilon: Decimal


def confirmation_epsilon_map() -> dict[str, str]:
    """Return the frozen per-candidate confirmation epsilons as canonical text.

    The map (and its content-addressed rule sha) stays bound to the V1 policy
    generation: candidates from other generations are skipped so historical
    rule digests remain byte-stable.
    """

    policy = load_strategy_policy()
    return {
        candidate_id: str(policy.threshold(candidate_id, threshold_id))
        for candidate_id, threshold_id in sorted(_CONFIRMATION_EPSILON_THRESHOLD_IDS.items())
        if candidate_id in policy.candidate_ids
    }


def confirmation_rule_sha256() -> str:
    """Content-address the synthetic confirmation rule and its frozen inputs."""

    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema": SYNTHETIC_CONFIRMATION_RULE_SCHEMA,
                "schema_version": 1,
                "rule_id": SYNTHETIC_CONFIRMATION_RULE_ID,
                "epsilons": confirmation_epsilon_map(),
                "policy_sha256": strategy_policy_sha256(),
            }
        )
    )


def confirmation_epsilon(candidate_id: str) -> Decimal:
    """Return the frozen confirmation epsilon for one V1 candidate lane."""

    threshold_id = _CONFIRMATION_EPSILON_THRESHOLD_IDS.get(candidate_id)
    if threshold_id is None:
        raise SyntheticConfirmationRejected(
            f"candidate {candidate_id} has no registered synthetic confirmation epsilon"
        )
    from esscher.strategy.policy import load_strategy_policy_v3

    policy = (
        load_strategy_policy_v3()
        if candidate_id == _EARNINGS_CANDIDATE_V3
        else load_strategy_policy()
    )
    raw = policy.threshold(candidate_id, threshold_id)
    try:
        epsilon = Decimal(str(raw))
    except (ArithmeticError, ValueError) as error:
        raise SyntheticConfirmationRejected(
            f"confirmation epsilon for {candidate_id} is not a finite decimal"
        ) from error
    if not epsilon.is_finite() or epsilon <= 0:
        raise SyntheticConfirmationRejected(
            f"confirmation epsilon for {candidate_id} must be positive and finite"
        )
    return epsilon


def confirmation_value(strategy_input: StrategyInput, candidate_id: str) -> Decimal | None:
    """Read the candidate's frozen confirmation feature value when present."""

    feature_id = _CONFIRMATION_FEATURE_IDS.get(candidate_id)
    if feature_id is None:
        raise SyntheticConfirmationRejected(
            f"candidate {candidate_id} has no registered confirmation feature"
        )
    feature = strategy_input.feature_by_id.get(feature_id)
    if (
        feature is None
        or feature.status is not FeatureStatus.PRESENT
        or not isinstance(feature.value, Decimal)
        or not feature.value.is_finite()
    ):
        return None
    return feature.value


def confirm_engine_outcome(
    decision: StrategyDecision,
    strategy_input: StrategyInput,
) -> SyntheticConfirmation:
    """Apply the deterministic synthetic confirmation rule to one decision."""

    candidate_id = decision.candidate_id
    if candidate_id != strategy_input.snapshot.candidate_id:
        raise SyntheticConfirmationRejected(
            "decision candidate does not match the joined strategy input"
        )
    epsilon = confirmation_epsilon(candidate_id)
    rule_sha256 = confirmation_rule_sha256()
    direction = decision.direction

    def result(
        *,
        confirmed: bool,
        reason_code: str,
        value: Decimal | None,
    ) -> SyntheticConfirmation:
        return SyntheticConfirmation(
            rule_id=SYNTHETIC_CONFIRMATION_RULE_ID,
            rule_sha256=rule_sha256,
            candidate_id=candidate_id,
            direction=direction,
            confirmed=confirmed,
            reason_code=reason_code,
            confirmation_value=value,
            epsilon=epsilon,
        )

    if direction is Direction.UNCERTAIN:
        return result(confirmed=False, reason_code=SYNTHETIC_CONFIRMATION_ABSTAINED, value=None)
    value = confirmation_value(strategy_input, candidate_id)
    if value is None or abs(value) < epsilon:
        return result(confirmed=False, reason_code=SYNTHETIC_CONFIRMATION_NEUTRAL, value=value)
    if direction is Direction.UP and value >= epsilon:
        return result(confirmed=True, reason_code=SYNTHETIC_CONFIRMATION_CONTINUATION, value=value)
    if direction is Direction.DOWN and value <= -epsilon:
        return result(confirmed=True, reason_code=SYNTHETIC_CONFIRMATION_CONTINUATION, value=value)
    return result(confirmed=False, reason_code=SYNTHETIC_CONFIRMATION_OPPOSED, value=value)


def derived_risk_tier(policy_v2: RiskPolicyV2) -> RiskTier:
    """Map the owner-approved first V2 tier onto its closed RiskTier constant."""

    if not isinstance(policy_v2, RiskPolicyV2):
        raise SyntheticConfirmationRejected("derived opportunities require a RiskPolicyV2")
    if not policy_v2.risk_tiers:
        raise SyntheticConfirmationRejected("V2 risk policy contains no owner-approved tiers")
    fraction = policy_v2.risk_tiers[0]
    for tier in RiskTier:
        if tier.fraction == fraction:
            return tier
    raise SyntheticConfirmationRejected(
        "the first V2 policy tier is not an owner-approved RiskTier constant"
    )


def synthetic_opportunity_id(
    *,
    decision: StrategyDecision,
    compiled: CompiledExpression,
    permit: DebitVerticalPermit,
    confirmation: SyntheticConfirmation,
) -> str:
    """Derive the deterministic synthetic opportunity identity for one decision."""

    payload = {
        "schema": SYNTHETIC_OPPORTUNITY_SCHEMA,
        "schema_version": 1,
        "rule_id": SYNTHETIC_CONFIRMATION_RULE_ID,
        "rule_sha256": confirmation.rule_sha256,
        "confirmation_reason_code": confirmation.reason_code,
        "event_id": decision.event_id,
        "candidate_id": decision.candidate_id,
        "decision_sha256": compiled.decision_sha256,
        "expression_sha256": compiled_expression_sha256(compiled),
        "permit_sha256": canonical_permit_sha256(permit),
    }
    return "opp-" + sha256_bytes(canonical_json_bytes(payload))


def derived_opportunity(
    *,
    decision: StrategyDecision,
    compiled: CompiledExpression,
    permit: DebitVerticalPermit,
    policy_v2: RiskPolicyV2,
    confirmation: SyntheticConfirmation,
) -> DefinedRiskOpportunity:
    """Derive the only defined-risk opportunity identity this bridge may size."""

    return DefinedRiskOpportunity(
        opportunity_id=synthetic_opportunity_id(
            decision=decision,
            compiled=compiled,
            permit=permit,
            confirmation=confirmation,
        ),
        decision_id=compiled.decision_sha256,
        expression_id=compiled_expression_sha256(compiled),
        underlying=permit.underlying,
        risk_tier=derived_risk_tier(policy_v2),
        max_debit_per_contract=SYNTHETIC_MAX_DEBIT_PER_CONTRACT,
        decision_ready=confirmation.confirmed,
    )


__all__ = [
    "SYNTHETIC_CONFIRMATION_ABSTAINED",
    "SYNTHETIC_CONFIRMATION_CONTINUATION",
    "SYNTHETIC_CONFIRMATION_NEUTRAL",
    "SYNTHETIC_CONFIRMATION_OPPOSED",
    "SYNTHETIC_CONFIRMATION_RULE_ID",
    "SYNTHETIC_CONFIRMATION_RULE_SCHEMA",
    "SYNTHETIC_MAX_DEBIT_PER_CONTRACT",
    "SYNTHETIC_OPPORTUNITY_SCHEMA",
    "RiskAbstentionRejected",
    "SyntheticConfirmation",
    "SyntheticConfirmationAbstained",
    "SyntheticConfirmationRejected",
    "confirm_engine_outcome",
    "confirmation_epsilon",
    "confirmation_epsilon_map",
    "confirmation_rule_sha256",
    "confirmation_value",
    "derived_opportunity",
    "derived_risk_tier",
    "synthetic_opportunity_id",
]
