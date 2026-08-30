"""Pure Esscher v1 decision engine over frozen point-in-time snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from ringdown_market.alpha.baselines import BaselineName
from ringdown_market.alpha.models import Direction
from ringdown_market.data.snapshot import SNAPSHOT_SCHEMA, SNAPSHOT_SCHEMA_VERSION

from .decisions import (
    TRACE_STAGES,
    AbstentionReason,
    ReactionRelation,
    StrategyDecision,
    StrategyDecisionState,
)
from .policy import STRATEGY_POLICY_VERSION, StrategyPolicy
from .reasoner import (
    Reasoner,
    ReasonerOutputRejected,
    ReasonerRoute,
    parse_reasoner_output,
)

_REQUIRED_FEATURE_IDS = (
    "earnings_numeric/v1",
    "guidance_statement/v1",
    "opening_return/v1",
    "market_opening_return/v1",
    "sector_opening_return/v1",
    "market_beta/v1",
    "sector_beta/v1",
)
_NUMERIC_FEATURE_IDS = (
    "opening_return/v1",
    "market_opening_return/v1",
    "sector_opening_return/v1",
    "market_beta/v1",
    "sector_beta/v1",
)


class EngineRejectionReason(StrEnum):
    """Stable reasons the engine refuses to process a snapshot at all."""

    INVALID_SNAPSHOT_DOCUMENT = "INVALID_SNAPSHOT_DOCUMENT"
    UNSUPPORTED_SNAPSHOT_SCHEMA = "UNSUPPORTED_SNAPSHOT_SCHEMA"


class EngineRejected(ValueError):
    """Raised for structural snapshot violations the engine cannot represent."""

    def __init__(self, reason: EngineRejectionReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class SnapshotView:
    """Validated read-only view of one snapshot payload."""

    payload: Mapping[str, object]
    sha256: str
    event_id: str
    issuer: str
    ticker: str
    decision_cutoff: datetime
    eligible: bool
    rejection_reasons: tuple[str, ...]
    evidence_ids: frozenset[str]
    features: Mapping[str, Mapping[str, object]]


def _parse_snapshot_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise EngineRejected(
            EngineRejectionReason.INVALID_SNAPSHOT_DOCUMENT, "snapshot timestamp must be text"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise EngineRejected(
            EngineRejectionReason.INVALID_SNAPSHOT_DOCUMENT, "snapshot timestamp is invalid"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EngineRejected(
            EngineRejectionReason.INVALID_SNAPSHOT_DOCUMENT, "snapshot timestamp must be aware"
        )
    return parsed


def view_snapshot(snapshot_bytes: bytes) -> SnapshotView:
    """Parse snapshot bytes into a validated read-only view."""

    if not isinstance(snapshot_bytes, (bytes, bytearray)) or not snapshot_bytes:
        raise EngineRejected(
            EngineRejectionReason.INVALID_SNAPSHOT_DOCUMENT, "snapshot bytes are required"
        )
    try:
        payload = json.loads(bytes(snapshot_bytes).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EngineRejected(
            EngineRejectionReason.INVALID_SNAPSHOT_DOCUMENT, "snapshot is not valid JSON"
        ) from None
    if not isinstance(payload, Mapping):
        raise EngineRejected(
            EngineRejectionReason.INVALID_SNAPSHOT_DOCUMENT, "snapshot must be an object"
        )
    if (
        payload.get("schema") != SNAPSHOT_SCHEMA
        or payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
    ):
        raise EngineRejected(
            EngineRejectionReason.UNSUPPORTED_SNAPSHOT_SCHEMA, "unsupported snapshot schema"
        )
    features_value = payload.get("features")
    features: dict[str, Mapping[str, object]] = {}
    if isinstance(features_value, list):
        for feature in features_value:
            if isinstance(feature, Mapping) and isinstance(feature.get("feature_id"), str):
                features[feature["feature_id"]] = feature
    evidence_value = payload.get("evidence")
    evidence_ids: set[str] = set()
    if isinstance(evidence_value, list):
        for record in evidence_value:
            if isinstance(record, Mapping) and isinstance(record.get("evidence_id"), str):
                evidence_ids.add(record["evidence_id"])
    reasons_value = payload.get("rejection_reasons")
    reasons: tuple[str, ...] = (
        tuple(str(item) for item in reasons_value) if isinstance(reasons_value, list) else ()
    )
    return SnapshotView(
        payload=payload,
        sha256=hashlib.sha256(bytes(snapshot_bytes)).hexdigest(),
        event_id=str(payload.get("event_id", "")),
        issuer=str(payload.get("issuer", "")),
        ticker=str(payload.get("ticker", "")),
        decision_cutoff=_parse_snapshot_timestamp(payload.get("decision_cutoff")),
        eligible=payload.get("eligibility") == "ELIGIBLE",
        rejection_reasons=reasons,
        evidence_ids=frozenset(evidence_ids),
        features=features,
    )


def _feature_decimal(view: SnapshotView, feature_id: str) -> Decimal | None:
    feature = view.features.get(feature_id)
    if not isinstance(feature, Mapping):
        return None
    value_text = feature.get("value_text")
    if not isinstance(value_text, str):
        return None
    try:
        return Decimal(value_text)
    except ArithmeticError:
        return None


def compute_opening_residual(view: SnapshotView) -> Decimal | None:
    """Compute the frozen opening residual from snapshot features only."""

    opening = _feature_decimal(view, "opening_return/v1")
    market = _feature_decimal(view, "market_opening_return/v1")
    sector = _feature_decimal(view, "sector_opening_return/v1")
    market_beta = _feature_decimal(view, "market_beta/v1")
    sector_beta = _feature_decimal(view, "sector_beta/v1")
    if None in (opening, market, sector, market_beta, sector_beta):
        return None
    return opening - market_beta * market - sector_beta * sector


def build_snapshot_baselines(opening_residual: Decimal | None) -> dict[BaselineName, Direction]:
    """Frozen baseline directions derivable from the strategy information set.

    Score-based baselines abstain because their score features are not part of the
    frozen v1 information set; abstentions remain in the denominator with zero return.
    """

    def _sign(value: Decimal) -> Direction:
        if value > 0:
            return Direction.UP
        if value < 0:
            return Direction.DOWN
        return Direction.UNCERTAIN

    gap = _sign(opening_residual) if opening_residual is not None else Direction.UNCERTAIN
    reverse = {
        Direction.UP: Direction.DOWN,
        Direction.DOWN: Direction.UP,
        Direction.UNCERTAIN: Direction.UNCERTAIN,
    }[gap]
    return {
        BaselineName.ALWAYS_ABSTAIN: Direction.UNCERTAIN,
        BaselineName.GAP_CONTINUE: gap,
        BaselineName.GAP_REVERSE: reverse,
        BaselineName.PRICE_ONLY: Direction.UNCERTAIN,
        BaselineName.FUNDAMENTAL_RULE: Direction.UNCERTAIN,
        BaselineName.NO_TEXT_ABLATION: Direction.UNCERTAIN,
    }


def _reaction_relation(direction: Direction, residual: Decimal | None) -> ReactionRelation:
    if direction is Direction.UNCERTAIN or residual is None or residual == 0:
        return ReactionRelation.NONE
    positive_residual = residual > 0
    positive_direction = direction is Direction.UP
    return (
        ReactionRelation.CONTINUE
        if positive_residual == positive_direction
        else ReactionRelation.REVERSE
    )


def _valid_signal_deadline(policy: StrategyPolicy, cutoff: datetime) -> datetime:
    start = policy.timing.observation_window_end
    deadline = policy.timing.valid_signal_by

    def _seconds(value: str) -> int:
        hours, minutes, seconds = (int(part) for part in value.split(":"))
        return hours * 3600 + minutes * 60 + seconds

    allowance = _seconds(deadline) - _seconds(start)
    if allowance <= 0:
        allowance += 24 * 3600
    return cutoff + timedelta(seconds=allowance)


class DecisionEngine:
    """One-route deterministic engine; abstains instead of falling back."""

    def __init__(
        self,
        *,
        policy: StrategyPolicy,
        route: ReasonerRoute,
        reasoner: Reasoner,
    ) -> None:
        self._policy = policy
        self._route = route
        self._reasoner = reasoner
        self._decided: set[tuple[str, str, str]] = set()

    @property
    def policy(self) -> StrategyPolicy:
        return self._policy

    @property
    def route(self) -> ReasonerRoute:
        return self._route

    def _abstain(
        self,
        *,
        view: SnapshotView,
        reasons: tuple[AbstentionReason, ...],
        decided_at: datetime,
        deadline: datetime,
        output_sha256: str,
        opening_residual: Decimal | None,
    ) -> StrategyDecision:
        return StrategyDecision(
            event_id=view.event_id,
            issuer=view.issuer,
            ticker=view.ticker,
            decision_cutoff=view.decision_cutoff,
            feature_snapshot_at=view.decision_cutoff,
            decided_at=min(decided_at, deadline),
            decision_deadline=deadline,
            direction=Direction.UNCERTAIN,
            decision_state=StrategyDecisionState.ABSTAIN,
            abstention_reasons=reasons,
            reaction_relation=ReactionRelation.NONE,
            opening_residual=opening_residual or Decimal("0"),
            evidence_citations=(),
            strongest_falsifier=None,
            snapshot_sha256=view.sha256,
            policy_version=self._policy.policy_version,
            policy_sha256=self._policy.sha256,
            route_sha256=self._route.sha256,
            reasoner_output_sha256=output_sha256,
            trace_stages=TRACE_STAGES,
        )

    def generate_decision(self, *, snapshot_bytes: bytes, decided_at: datetime) -> StrategyDecision:
        """Generate one source-attributable decision or explicit abstention."""

        view = view_snapshot(snapshot_bytes)
        deadline = _valid_signal_deadline(self._policy, view.decision_cutoff)
        residual = compute_opening_residual(view)
        identity = (view.event_id, self._policy.sha256, view.sha256)

        if view.payload.get("policy_sha256") != self._policy.sha256:
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.POLICY_HASH_MISMATCH,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256="0" * 64,
                opening_residual=residual,
            )
        if view.payload.get("policy_version") != STRATEGY_POLICY_VERSION:
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.POLICY_HASH_MISMATCH,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256="0" * 64,
                opening_residual=residual,
            )
        if identity in self._decided:
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.DUPLICATE_DECISION,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256="0" * 64,
                opening_residual=residual,
            )

        if not view.eligible:
            self._decided.add(identity)
            reason = (
                AbstentionReason.STALE_INPUT
                if any(
                    code in view.rejection_reasons
                    for code in ("POST_CUTOFF_EVIDENCE", "STALE_OBSERVATION")
                )
                else AbstentionReason.MISSING_EVIDENCE
            )
            return self._abstain(
                view=view,
                reasons=(reason,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256="0" * 64,
                opening_residual=residual,
            )
        if set(_REQUIRED_FEATURE_IDS) - set(view.features):
            self._decided.add(identity)
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.MISSING_EVIDENCE,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256="0" * 64,
                opening_residual=residual,
            )
        if residual is None:
            self._decided.add(identity)
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.STALE_INPUT,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256="0" * 64,
                opening_residual=None,
            )

        if decided_at > deadline:
            self._decided.add(identity)
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.LATE_REASONER_OUTPUT,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256="0" * 64,
                opening_residual=residual,
            )

        try:
            raw_output = self._reasoner.reason(view.payload)
            output = parse_reasoner_output(raw_output)
        except ReasonerOutputRejected:
            self._decided.add(identity)
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.INVALID_REASONER_OUTPUT,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256="0" * 64,
                opening_residual=residual,
            )

        if output.direction is not Direction.UNCERTAIN and not output.citations:
            self._decided.add(identity)
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.INVALID_REASONER_OUTPUT,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256=output.raw_sha256,
                opening_residual=residual,
            )
        unknown_evidence = {
            citation.evidence_id
            for citation in output.citations
            if citation.evidence_id not in view.evidence_ids
        }
        if output.falsifier is not None and output.falsifier.evidence_id not in view.evidence_ids:
            unknown_evidence.add(output.falsifier.evidence_id)
        if unknown_evidence:
            self._decided.add(identity)
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.UNBOUNDED_FALSIFIER,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256=output.raw_sha256,
                opening_residual=residual,
            )

        self._decided.add(identity)
        if output.direction is Direction.UNCERTAIN:
            return self._abstain(
                view=view,
                reasons=(AbstentionReason.INVALID_REASONER_OUTPUT,),
                decided_at=decided_at,
                deadline=deadline,
                output_sha256=output.raw_sha256,
                opening_residual=residual,
            )

        return StrategyDecision(
            event_id=view.event_id,
            issuer=view.issuer,
            ticker=view.ticker,
            decision_cutoff=view.decision_cutoff,
            feature_snapshot_at=view.decision_cutoff,
            decided_at=decided_at,
            decision_deadline=deadline,
            direction=output.direction,
            decision_state=StrategyDecisionState.APPROVED,
            abstention_reasons=(),
            reaction_relation=_reaction_relation(output.direction, residual),
            opening_residual=residual,
            evidence_citations=output.citations,
            strongest_falsifier=output.falsifier,
            snapshot_sha256=view.sha256,
            policy_version=self._policy.policy_version,
            policy_sha256=self._policy.sha256,
            route_sha256=self._route.sha256,
            reasoner_output_sha256=output.raw_sha256,
            trace_stages=TRACE_STAGES,
        )
