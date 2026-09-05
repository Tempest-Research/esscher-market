"""Immutable, direction-only strategy contract models.

These models deliberately stop before trade expression, account risk, permits, or
broker execution.  The strategy package is a pure research/data boundary.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from esscher.alpha.models import Direction

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_UNIT = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FEATURE_UNIT = re.compile(r"^[A-Z][A-Z0-9_|]{0,127}$")
_MAX_REASONER_SUMMARY_CHARS = 800
_MAX_NESTED_SUMMARY_CHARS = 400


def _require_normalized_text(
    value: str,
    field: str,
    *,
    maximum: int = 256,
) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value != unicodedata.normalize("NFC", value)
        or len(value) > maximum
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise ValueError(f"{field} must be bounded, normalized, non-empty text")


def _require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} must be a normalized identifier")


def _require_reason_code(value: str, field: str) -> None:
    if not isinstance(value, str) or _REASON_CODE.fullmatch(value) is None:
        raise ValueError(f"{field} must be an uppercase stable reason code")


def _require_feature_unit(value: str, field: str) -> None:
    if not isinstance(value, str) or _FEATURE_UNIT.fullmatch(value) is None:
        raise ValueError(f"{field} must be a normalized feature unit")


def _require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256")


def _require_utc(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")


def _require_sorted_unique(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple) or values != tuple(sorted(set(values))):
        raise ValueError(f"{field} must be a sorted unique tuple")


def _require_reason_codes(values: tuple[str, ...], field: str) -> None:
    _require_sorted_unique(values, field)
    for value in values:
        _require_reason_code(value, field)


def _require_identifiers(values: tuple[str, ...], field: str) -> None:
    _require_sorted_unique(values, field)
    for value in values:
        _require_identifier(value, field)


class EventCategory(StrEnum):
    """The two independently evaluated scheduled-event candidates."""

    SCHEDULED_EARNINGS = "SCHEDULED_EARNINGS"
    SCHEDULED_MACRO_RELEASE = "SCHEDULED_MACRO_RELEASE"


class TimingBucket(StrEnum):
    """Cohort-level event timing, never inferred from a date alone."""

    BEFORE_OPEN = "BEFORE_OPEN"
    AFTER_CLOSE = "AFTER_CLOSE"
    SCHEDULED_RELEASE = "SCHEDULED_RELEASE"


class ReleaseFamily(StrEnum):
    """Supported macro-release families; earnings snapshots use ``None``."""

    BLS_JOLTS = "BLS_JOLTS"
    BLS_EMPLOYMENT_SITUATION = "BLS_EMPLOYMENT_SITUATION"


class EligibilityState(StrEnum):
    """Ex-ante candidate admission state."""

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNRESOLVED = "UNRESOLVED"


class DataHealthState(StrEnum):
    """Deterministic health result for the complete decision packet."""

    VALID = "VALID"
    INVALID = "INVALID"


class EvidenceRole(StrEnum):
    """Decision-use role used to validate source-grounded citations."""

    ISSUER_PRIMARY = "ISSUER_PRIMARY"
    MACRO_PRIMARY = "MACRO_PRIMARY"
    SEC_FILING = "SEC_FILING"
    PERMITTED_NEWS = "PERMITTED_NEWS"
    ISSUER_MARKET = "ISSUER_MARKET"
    MARKET_PROXY = "MARKET_PROXY"
    SECTOR_PROXY = "SECTOR_PROXY"
    LIQUIDITY_VOLATILITY = "LIQUIDITY_VOLATILITY"

    @property
    def is_primary(self) -> bool:
        return self in {self.ISSUER_PRIMARY, self.MACRO_PRIMARY}

    @property
    def is_market(self) -> bool:
        return self in {
            self.ISSUER_MARKET,
            self.MARKET_PROXY,
            self.SECTOR_PROXY,
            self.LIQUIDITY_VOLATILITY,
        }


class FeatureStatus(StrEnum):
    """Explicit availability state; missing values are never imputed."""

    PRESENT = "PRESENT"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CONFLICTING = "CONFLICTING"


class FeatureUnit(StrEnum):
    """Frozen, machine-readable feature units."""

    RATIO = "RATIO"
    STANDARD_DEVIATIONS = "STANDARD_DEVIATIONS"
    Z_SCORE = "Z_SCORE"
    CATEGORY = "CATEGORY"
    LOG_RETURN = "LOG_RETURN"
    BASIS_POINTS = "BASIS_POINTS"
    MILLISECONDS = "MILLISECONDS"
    ANNUALIZED_LOG_RETURN_VOLATILITY = "ANNUALIZED_LOG_RETURN_VOLATILITY"
    COUNT = "COUNT"
    COUNT_THOUSANDS = "COUNT_THOUSANDS"
    PERCENT = "PERCENT"
    PERCENTAGE_POINTS = "PERCENTAGE_POINTS"
    USD = "USD"
    USD_PER_HOUR = "USD_PER_HOUR"
    VECTOR = "VECTOR"


class FeatureValueType(StrEnum):
    """Canonical JSON representation of one policy-defined feature value."""

    DECIMAL_STRING = "DECIMAL_STRING"
    INTEGER = "INTEGER"
    ENUM = "ENUM"
    DECIMAL_STRING_MAP = "DECIMAL_STRING_MAP"


class GuidanceDirection(StrEnum):
    """Categorical issuer-guidance feature values."""

    RAISED = "RAISED"
    LOWERED = "LOWERED"
    REITERATED = "REITERATED"
    MIXED = "MIXED"
    WITHDRAWN = "WITHDRAWN"
    NOT_GIVEN = "NOT_GIVEN"


class ExchangeStatus(StrEnum):
    """Outcome of the bounded hosted-reasoner call."""

    COMPLETED = "COMPLETED"
    TIMEOUT = "TIMEOUT"
    CANCELED = "CANCELED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class DecisionDisposition(StrEnum):
    """Whether a final direction survived deterministic validation."""

    ACCEPTED = "ACCEPTED"
    ABSTAINED = "ABSTAINED"
    REJECTED = "REJECTED"


class ReactionRelation(StrEnum):
    """Relation between reasoner direction and deterministic price confirmation."""

    CONTINUE = "CONTINUE"
    REVERSE = "REVERSE"
    NONE = "NONE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """One source identity available inside the immutable evidence packet.

    ``available_at`` is the source-availability/retrieval instant proven by the
    collector.  It is not a substitute for ``published_at`` when a publisher
    timestamp is required by policy.
    """

    evidence_id: str
    role: EvidenceRole
    source_class: str
    published_at: datetime | None
    available_at: datetime
    content_sha256: str

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "evidence_id")
        _require_reason_code(self.source_class, "source_class")
        if self.published_at is not None:
            _require_utc(self.published_at, "published_at")
        _require_utc(self.available_at, "available_at")
        _require_sha256(self.content_sha256, "content_sha256")


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """One ex-ante event retained in the complete candidate denominator."""

    event_id: str
    issuer: str
    security_id: str
    ticker: str
    cohort_id: str
    scheduled_at: datetime
    eligibility: EligibilityState
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("event_id", "security_id", "cohort_id"):
            _require_identifier(getattr(self, field), field)
        _require_normalized_text(self.issuer, "issuer")
        if self.ticker != self.ticker.strip().upper() or not self.ticker:
            raise ValueError("ticker must be normalized uppercase text")
        _require_utc(self.scheduled_at, "scheduled_at")
        _require_reason_codes(self.reason_codes, "reason_codes")
        if self.eligibility is EligibilityState.ELIGIBLE:
            if self.reason_codes:
                raise ValueError("eligible candidates cannot carry rejection reasons")
        elif not self.reason_codes:
            raise ValueError("excluded candidates require a stable reason code")


@dataclass(frozen=True, slots=True)
class CandidateManifest:
    """Frozen pre-outcome universe with every admitted and excluded event."""

    manifest_id: str
    candidate_id: str
    policy_sha256: str
    selection_rule_id: str
    producer_build_sha256: str
    frozen_at: datetime
    records: tuple[CandidateRecord, ...]

    def __post_init__(self) -> None:
        for field in ("manifest_id", "candidate_id", "selection_rule_id"):
            _require_identifier(getattr(self, field), field)
        for field in ("policy_sha256", "producer_build_sha256"):
            _require_sha256(getattr(self, field), field)
        _require_utc(self.frozen_at, "frozen_at")
        event_ids = tuple(record.event_id for record in self.records)
        if event_ids != tuple(sorted(set(event_ids))) or not event_ids:
            raise ValueError("candidate records must be non-empty, sorted, and unique by event ID")
        if any(record.scheduled_at <= self.frozen_at for record in self.records):
            raise ValueError("candidate events must be scheduled after the ex-ante freeze")

    def record(self, event_id: str) -> CandidateRecord:
        """Return one exact event record or reject discretionary insertion."""

        matches = tuple(record for record in self.records if record.event_id == event_id)
        if len(matches) != 1:
            raise ValueError("event is absent from the frozen candidate manifest")
        return matches[0]


@dataclass(frozen=True, slots=True)
class StrategySnapshot:
    """Canonical #27 event/evidence snapshot before reasoner inference."""

    event_id: str
    candidate_id: str
    cohort_id: str
    event_category: EventCategory
    issuer: str
    security_id: str
    ticker: str
    policy_sha256: str
    candidate_manifest_sha256: str
    producer_build_sha256: str
    created_at: datetime
    universe_frozen_at: datetime
    timing_bucket: TimingBucket
    release_family: ReleaseFamily | None
    event_published_at: datetime
    reaction_session_id: str
    reaction_session_open_at: datetime
    reaction_session_close_at: datetime
    observation_window_start_at: datetime
    observation_window_end_at: datetime
    evidence_cutoff_at: datetime
    decision_cutoff_at: datetime
    candidate_entry_deadline_at: datetime
    evidence_packet_sha256: str
    evidence_refs: tuple[EvidenceRef, ...]
    eligibility: EligibilityState
    eligibility_reason_codes: tuple[str, ...]
    data_health: DataHealthState
    health_reason_codes: tuple[str, ...]
    allowed_unknown_codes: tuple[str, ...]
    critical_unknown_codes: tuple[str, ...]
    prior_eligible_session_close_at: datetime | None = None

    def __post_init__(self) -> None:
        for field in ("event_id", "candidate_id", "cohort_id", "security_id"):
            _require_identifier(getattr(self, field), field)
        _require_normalized_text(self.issuer, "issuer")
        if self.ticker != self.ticker.strip().upper() or not self.ticker:
            raise ValueError("ticker must be normalized uppercase text")
        _require_identifier(self.reaction_session_id, "reaction_session_id")
        for field in (
            "policy_sha256",
            "candidate_manifest_sha256",
            "producer_build_sha256",
            "evidence_packet_sha256",
        ):
            _require_sha256(getattr(self, field), field)
        for field in (
            "created_at",
            "universe_frozen_at",
            "event_published_at",
            "reaction_session_open_at",
            "reaction_session_close_at",
            "observation_window_start_at",
            "observation_window_end_at",
            "evidence_cutoff_at",
            "decision_cutoff_at",
            "candidate_entry_deadline_at",
        ):
            _require_utc(getattr(self, field), field)
        if self.prior_eligible_session_close_at is not None:
            _require_utc(
                self.prior_eligible_session_close_at,
                "prior_eligible_session_close_at",
            )
        if self.reaction_session_close_at <= self.reaction_session_open_at:
            raise ValueError("reaction session close must be after its open")
        if not (
            self.reaction_session_open_at
            <= self.observation_window_start_at
            < self.observation_window_end_at
            <= self.reaction_session_close_at
        ):
            raise ValueError("observation window must be inside the reaction session")
        if not (
            self.observation_window_end_at
            <= self.evidence_cutoff_at
            <= self.decision_cutoff_at
            < self.candidate_entry_deadline_at
        ):
            raise ValueError("cutoff and entry clocks must be monotonically ordered")
        if self.event_published_at > self.evidence_cutoff_at:
            raise ValueError("event publication must be available by the evidence cutoff")
        if self.universe_frozen_at >= self.event_published_at:
            raise ValueError("universe must be frozen before the event publication")
        if self.created_at > self.decision_cutoff_at:
            raise ValueError("snapshot must be created no later than decision cutoff")
        evidence_ids = tuple(item.evidence_id for item in self.evidence_refs)
        if evidence_ids != tuple(sorted(set(evidence_ids))) or not evidence_ids:
            raise ValueError("evidence_refs must be non-empty, sorted, and unique by ID")
        for evidence in self.evidence_refs:
            if (
                evidence.published_at is not None
                and evidence.published_at > self.evidence_cutoff_at
            ):
                raise ValueError("evidence publication exceeds evidence cutoff")
            if evidence.available_at > self.evidence_cutoff_at:
                raise ValueError("evidence observation exceeds evidence cutoff")
        _require_reason_codes(self.eligibility_reason_codes, "eligibility_reason_codes")
        _require_reason_codes(self.health_reason_codes, "health_reason_codes")
        _require_reason_codes(self.allowed_unknown_codes, "allowed_unknown_codes")
        _require_reason_codes(self.critical_unknown_codes, "critical_unknown_codes")
        if not set(self.critical_unknown_codes) <= set(self.allowed_unknown_codes):
            raise ValueError("critical_unknown_codes must be allowed unknown codes")
        if self.eligibility is EligibilityState.ELIGIBLE:
            if self.eligibility_reason_codes:
                raise ValueError("eligible snapshots cannot carry eligibility rejection codes")
        elif not self.eligibility_reason_codes:
            raise ValueError("non-eligible snapshots require an eligibility reason code")
        if self.data_health is DataHealthState.VALID:
            if self.health_reason_codes:
                raise ValueError("valid snapshots cannot carry health rejection codes")
        elif not self.health_reason_codes:
            raise ValueError("invalid snapshots require a health reason code")
        if self.event_category is EventCategory.SCHEDULED_EARNINGS:
            if self.release_family is not None:
                raise ValueError("earnings snapshots cannot set release_family")
            if self.timing_bucket not in {TimingBucket.BEFORE_OPEN, TimingBucket.AFTER_CLOSE}:
                raise ValueError("earnings snapshots require BEFORE_OPEN or AFTER_CLOSE")
        else:
            if self.release_family is None:
                raise ValueError("macro snapshots require release_family")
            if self.timing_bucket is not TimingBucket.SCHEDULED_RELEASE:
                raise ValueError("macro snapshots require SCHEDULED_RELEASE timing")

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.evidence_refs)


FeatureScalar = Decimal | int | GuidanceDirection | str


@dataclass(frozen=True, slots=True)
class FeatureComponent:
    """One typed member of a vector-valued macro feature."""

    component_id: str
    status: FeatureStatus
    value: Decimal | int | None
    unit: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.component_id, "component_id")
        _require_feature_unit(self.unit, "unit")
        _require_identifiers(self.source_refs, "source_refs")
        if self.status is FeatureStatus.PRESENT:
            if not self.source_refs:
                raise ValueError("present feature components require source_refs")
            if self.value is None or isinstance(self.value, bool):
                raise ValueError("present feature components require a numeric value")
            if isinstance(self.value, Decimal) and not self.value.is_finite():
                raise ValueError("feature component values must be finite")
        elif self.value is not None:
            raise ValueError("non-present feature components require a null value")


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """One frozen deterministic feature and its exact evidence dependencies."""

    feature_id: str
    status: FeatureStatus
    value: FeatureScalar | None
    value_type: FeatureValueType
    unit: str
    observed_at: datetime | None
    source_refs: tuple[str, ...]
    components: tuple[FeatureComponent, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.feature_id, "feature_id")
        _require_feature_unit(self.unit, "unit")
        _require_identifiers(self.source_refs, "source_refs")
        if self.observed_at is not None:
            _require_utc(self.observed_at, "observed_at")
        component_ids = tuple(component.component_id for component in self.components)
        if component_ids != tuple(sorted(set(component_ids))):
            raise ValueError("feature components must be sorted and unique")
        if self.status is FeatureStatus.PRESENT:
            if not self.source_refs:
                raise ValueError("present features require source_refs")
            if self.value_type is FeatureValueType.DECIMAL_STRING_MAP:
                if self.value is not None or not self.components:
                    raise ValueError("present vector features require components and null value")
            else:
                if self.value is None or self.components:
                    raise ValueError("present scalar features require one scalar value")
                if isinstance(self.value, bool):
                    raise ValueError("boolean feature values are forbidden")
                if isinstance(self.value, Decimal) and not self.value.is_finite():
                    raise ValueError("feature values must be finite")
                if self.value_type is FeatureValueType.INTEGER:
                    if not isinstance(self.value, int) or self.value < 0:
                        raise ValueError("integer feature values must be nonnegative integers")
                elif self.value_type is FeatureValueType.ENUM:
                    if not isinstance(self.value, (GuidanceDirection, str)):
                        raise ValueError("categorical features require normalized text")
                    _require_reason_code(str(self.value), "categorical feature value")
                elif self.value_type is FeatureValueType.DECIMAL_STRING and not isinstance(
                    self.value, Decimal
                ):
                    raise ValueError("numeric feature values must use Decimal")
            if self.observed_at is None:
                raise ValueError("present features require observed_at")
        else:
            if self.value is not None or self.components or self.observed_at is not None:
                raise ValueError("non-present features require null value/time and no components")


@dataclass(frozen=True, slots=True)
class FeatureReceipt:
    """Canonical deterministic feature artifact, separate from source collection."""

    event_id: str
    candidate_id: str
    cohort_id: str
    policy_sha256: str
    strategy_snapshot_sha256: str
    producer_build_sha256: str
    created_at: datetime
    feature_snapshot_at: datetime
    decision_cutoff_at: datetime
    maximum_public_timestamp: datetime
    data_health: DataHealthState
    health_reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    lineage_receipt_sha256: str | None
    features: tuple[FeatureValue, ...]

    def __post_init__(self) -> None:
        for field in ("event_id", "candidate_id", "cohort_id"):
            _require_identifier(getattr(self, field), field)
        for field in ("policy_sha256", "strategy_snapshot_sha256", "producer_build_sha256"):
            _require_sha256(getattr(self, field), field)
        if self.lineage_receipt_sha256 is not None:
            _require_sha256(self.lineage_receipt_sha256, "lineage_receipt_sha256")
        _require_utc(self.created_at, "created_at")
        _require_utc(self.feature_snapshot_at, "feature_snapshot_at")
        _require_utc(self.decision_cutoff_at, "decision_cutoff_at")
        _require_utc(self.maximum_public_timestamp, "maximum_public_timestamp")
        if self.created_at < self.feature_snapshot_at:
            raise ValueError("feature receipt cannot be created before its snapshot time")
        if self.feature_snapshot_at > self.decision_cutoff_at:
            raise ValueError("feature snapshot cannot exceed the decision cutoff")
        if self.maximum_public_timestamp > self.decision_cutoff_at:
            raise ValueError("public evidence cannot exceed the decision cutoff")
        _require_reason_codes(self.health_reason_codes, "health_reason_codes")
        if self.data_health is DataHealthState.VALID:
            if self.health_reason_codes:
                raise ValueError("valid receipts cannot carry health rejection codes")
        elif not self.health_reason_codes:
            raise ValueError("invalid receipts require a health reason code")
        if not self.evidence_ids or tuple(sorted(set(self.evidence_ids))) != self.evidence_ids:
            raise ValueError("evidence_ids must be non-empty, sorted, and unique")
        feature_ids = tuple(feature.feature_id for feature in self.features)
        if feature_ids != tuple(sorted(set(feature_ids))) or not feature_ids:
            raise ValueError("features must be non-empty, sorted, and unique by ID")


@dataclass(frozen=True, slots=True)
class StrategyInput:
    """Validated immutable join of exact snapshot and feature-receipt bytes."""

    candidate_manifest: CandidateManifest
    snapshot: StrategySnapshot
    feature_receipt: FeatureReceipt
    candidate_manifest_sha256: str
    snapshot_sha256: str
    feature_receipt_sha256: str

    def __post_init__(self) -> None:
        for field in (
            "candidate_manifest_sha256",
            "snapshot_sha256",
            "feature_receipt_sha256",
        ):
            _require_sha256(getattr(self, field), field)
        receipt = self.feature_receipt
        snapshot = self.snapshot
        manifest = self.candidate_manifest
        if snapshot.candidate_manifest_sha256 != self.candidate_manifest_sha256:
            raise ValueError("snapshot does not bind the supplied candidate manifest")
        if manifest.candidate_id != snapshot.candidate_id:
            raise ValueError("candidate manifest does not match the strategy snapshot")
        if manifest.policy_sha256 != snapshot.policy_sha256:
            raise ValueError("candidate manifest policy does not match the strategy snapshot")
        if manifest.frozen_at != snapshot.universe_frozen_at:
            raise ValueError("candidate manifest freeze does not match the strategy snapshot")
        candidate = manifest.record(snapshot.event_id)
        for field in ("issuer", "security_id", "ticker", "cohort_id", "eligibility"):
            if getattr(candidate, field) != getattr(snapshot, field):
                raise ValueError(f"candidate manifest {field} does not match strategy snapshot")
        if candidate.reason_codes != snapshot.eligibility_reason_codes:
            raise ValueError("candidate manifest reason codes do not match strategy snapshot")
        if receipt.strategy_snapshot_sha256 != self.snapshot_sha256:
            raise ValueError("feature receipt does not bind the supplied strategy snapshot")
        for field in ("event_id", "candidate_id", "cohort_id", "policy_sha256"):
            if getattr(receipt, field) != getattr(snapshot, field):
                raise ValueError(f"feature receipt {field} does not match strategy snapshot")
        if receipt.feature_snapshot_at > snapshot.decision_cutoff_at:
            raise ValueError("feature snapshot exceeds decision cutoff")
        if receipt.created_at > snapshot.decision_cutoff_at:
            raise ValueError("feature receipt was created after decision cutoff")
        evidence_by_id = {item.evidence_id: item for item in snapshot.evidence_refs}
        known_evidence = set(evidence_by_id)
        for feature in receipt.features:
            if not set(feature.source_refs) <= known_evidence:
                raise ValueError(f"feature {feature.feature_id} has an unknown source reference")
            if (
                feature.observed_at is not None
                and feature.observed_at > snapshot.evidence_cutoff_at
            ):
                raise ValueError(f"feature {feature.feature_id} was observed after evidence cutoff")
            if feature.status is FeatureStatus.PRESENT:
                assert feature.observed_at is not None
                if any(
                    evidence_by_id[source_ref].available_at > feature.observed_at
                    for source_ref in feature.source_refs
                ):
                    raise ValueError(
                        f"feature {feature.feature_id} cites evidence unavailable when observed"
                    )
            for component in feature.components:
                if not set(component.source_refs) <= known_evidence:
                    raise ValueError(
                        "feature component "
                        f"{component.component_id} has an unknown source reference"
                    )
                if component.status is FeatureStatus.PRESENT:
                    assert feature.observed_at is not None
                    if any(
                        evidence_by_id[source_ref].available_at > feature.observed_at
                        for source_ref in component.source_refs
                    ):
                        raise ValueError(
                            "feature component "
                            f"{component.component_id} cites evidence unavailable when observed"
                        )

    @property
    def feature_by_id(self) -> dict[str, FeatureValue]:
        """Return a defensive lookup copy for deterministic validation."""

        return {feature.feature_id: feature for feature in self.feature_receipt.features}


@dataclass(frozen=True, slots=True)
class Contradiction:
    """Two exact pieces of evidence whose claims conflict."""

    evidence_ids: tuple[str, str]
    summary: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.evidence_ids, tuple)
            or len(self.evidence_ids) != 2
            or self.evidence_ids[0] >= self.evidence_ids[1]
        ):
            raise ValueError("contradiction evidence IDs must be two distinct sorted IDs")
        for evidence_id in self.evidence_ids:
            _require_identifier(evidence_id, "contradiction evidence_id")
        _require_normalized_text(
            self.summary,
            "contradiction summary",
            maximum=_MAX_NESTED_SUMMARY_CHARS,
        )


@dataclass(frozen=True, slots=True)
class Falsifier:
    """The strongest source-grounded fact against a directional conclusion."""

    evidence_id: str
    summary: str

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "falsifier evidence_id")
        _require_normalized_text(
            self.summary,
            "falsifier summary",
            maximum=_MAX_NESTED_SUMMARY_CHARS,
        )


@dataclass(frozen=True, slots=True)
class ReasonerDecision:
    """Strictly parsed but still untrusted hosted-reasoner output."""

    decision: Direction
    evidence_ids: tuple[str, ...]
    contradictions: tuple[Contradiction, ...]
    unknowns: tuple[str, ...]
    strongest_falsifier: Falsifier | None
    summary: str

    def __post_init__(self) -> None:
        _require_identifiers(self.evidence_ids, "reasoner evidence_ids")
        _require_reason_codes(self.unknowns, "reasoner unknowns")
        if len(self.evidence_ids) > 16:
            raise ValueError("reasoner evidence_ids cannot exceed 16 items")
        if len(self.contradictions) > 8:
            raise ValueError("reasoner contradictions cannot exceed 8 items")
        if len(self.unknowns) > 16:
            raise ValueError("reasoner unknowns cannot exceed 16 items")
        contradiction_keys = tuple(item.evidence_ids for item in self.contradictions)
        if contradiction_keys != tuple(sorted(set(contradiction_keys))):
            raise ValueError("contradictions must be sorted and unique")
        _require_normalized_text(
            self.summary,
            "reasoner summary",
            maximum=_MAX_REASONER_SUMMARY_CHARS,
        )


@dataclass(frozen=True, slots=True)
class DecodingParameters:
    """Provider-neutral bounded decoding settings retained as exchange evidence."""

    temperature: Decimal
    top_p: Decimal
    max_output_tokens: int
    seed: int | None

    def __post_init__(self) -> None:
        if not self.temperature.is_finite() or self.temperature < 0:
            raise ValueError("temperature must be a finite nonnegative Decimal")
        if not self.top_p.is_finite() or not (Decimal(0) < self.top_p <= Decimal(1)):
            raise ValueError("top_p must be in (0, 1]")
        if isinstance(self.max_output_tokens, bool) or self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        if self.seed is not None and isinstance(self.seed, bool):
            raise ValueError("seed must be an integer or null")


@dataclass(frozen=True, slots=True)
class ReasonerExchange:
    """Immutable request/response receipt for one bounded external reasoner call."""

    event_id: str
    candidate_id: str
    policy_sha256: str
    strategy_snapshot_sha256: str
    feature_receipt_sha256: str
    evidence_packet_sha256: str
    route_sha256: str
    prompt_sha256: str
    output_schema_sha256: str
    model_config_sha256: str
    request_sha256: str
    raw_response_sha256: str | None
    provider: str
    model: str
    model_revision: str | None
    decoding: DecodingParameters
    started_at: datetime
    responded_at: datetime | None
    deadline_at: datetime
    status: ExchangeStatus
    error_code: str | None
    producer_build_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field in ("event_id", "candidate_id"):
            _require_identifier(getattr(self, field), field)
        for field in (
            "policy_sha256",
            "strategy_snapshot_sha256",
            "feature_receipt_sha256",
            "evidence_packet_sha256",
            "route_sha256",
            "prompt_sha256",
            "output_schema_sha256",
            "model_config_sha256",
            "request_sha256",
            "producer_build_sha256",
        ):
            _require_sha256(getattr(self, field), field)
        if self.raw_response_sha256 is not None:
            _require_sha256(self.raw_response_sha256, "raw_response_sha256")
        _require_normalized_text(self.provider, "provider")
        _require_normalized_text(self.model, "model")
        if self.model_revision is not None:
            _require_normalized_text(self.model_revision, "model_revision")
        for field in ("started_at", "deadline_at", "created_at"):
            _require_utc(getattr(self, field), field)
        if self.responded_at is not None:
            _require_utc(self.responded_at, "responded_at")
        if self.deadline_at < self.started_at:
            raise ValueError("reasoner deadline cannot precede start")
        if self.responded_at is not None and self.responded_at < self.started_at:
            raise ValueError("reasoner response cannot precede start")
        if self.created_at < self.started_at:
            raise ValueError("exchange receipt cannot be created before reasoner start")
        if self.status is ExchangeStatus.COMPLETED:
            if (
                self.raw_response_sha256 is None
                or self.responded_at is None
                or self.error_code is not None
            ):
                raise ValueError("completed exchanges require response hash/time and no error")
        else:
            if self.error_code is None:
                raise ValueError("failed exchanges require a stable error code")
            _require_reason_code(self.error_code, "error_code")


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    """Validator-owned direction or abstention with no execution authority."""

    event_id: str
    security_id: str
    candidate_id: str
    cohort_id: str
    policy_sha256: str
    candidate_manifest_sha256: str
    strategy_snapshot_sha256: str
    feature_receipt_sha256: str
    reasoner_exchange_sha256: str
    reasoner_decision_sha256: str | None
    producer_build_sha256: str
    decision_at: datetime
    reasoner_direction: Direction | None
    direction: Direction
    disposition: DecisionDisposition
    reaction_relation: ReactionRelation
    evidence_ids: tuple[str, ...]
    contradictions: tuple[Contradiction, ...]
    unknowns: tuple[str, ...]
    strongest_falsifier: Falsifier | None
    reason_codes: tuple[str, ...]
    summary: str | None

    def __post_init__(self) -> None:
        for field in ("event_id", "security_id", "candidate_id", "cohort_id"):
            _require_identifier(getattr(self, field), field)
        for field in (
            "policy_sha256",
            "candidate_manifest_sha256",
            "strategy_snapshot_sha256",
            "feature_receipt_sha256",
            "reasoner_exchange_sha256",
            "producer_build_sha256",
        ):
            _require_sha256(getattr(self, field), field)
        if self.reasoner_decision_sha256 is not None:
            _require_sha256(self.reasoner_decision_sha256, "reasoner_decision_sha256")
        _require_utc(self.decision_at, "decision_at")
        _require_identifiers(self.evidence_ids, "decision evidence_ids")
        _require_reason_codes(self.unknowns, "decision unknowns")
        _require_reason_codes(self.reason_codes, "decision reason_codes")
        contradiction_keys = tuple(item.evidence_ids for item in self.contradictions)
        if contradiction_keys != tuple(sorted(set(contradiction_keys))):
            raise ValueError("decision contradictions must be sorted and unique")
        if self.summary is not None:
            _require_normalized_text(
                self.summary,
                "decision summary",
                maximum=_MAX_REASONER_SUMMARY_CHARS,
            )
        if self.disposition is DecisionDisposition.ACCEPTED:
            if (
                self.direction is Direction.UNCERTAIN
                or self.reasoner_direction is not self.direction
                or self.reaction_relation is not ReactionRelation.CONTINUE
                or self.reason_codes
            ):
                raise ValueError("accepted decisions require a confirmed unchanged direction")
        elif self.direction is not Direction.UNCERTAIN or not self.reason_codes:
            raise ValueError("abstained/rejected decisions require UNCERTAIN and reason codes")
        if self.disposition is DecisionDisposition.ABSTAINED and (
            self.reasoner_direction is not Direction.UNCERTAIN
            or self.reaction_relation is not ReactionRelation.NOT_APPLICABLE
        ):
            raise ValueError("abstention must originate from reasoner UNCERTAIN")


@dataclass(frozen=True, slots=True)
class StrategyV2Context:
    """Host-validated V2 join including ledger-backed memory and news identities.

    This is data, not an authority token.  The host must call
    ``contracts.validate_strategy_v2_context`` with its trusted ledger
    immediately before building a request, so public construction of this
    frozen dataclass cannot promote a structurally plausible context.
    """

    candidate_manifest: CandidateManifest
    snapshot: StrategySnapshot
    feature_receipt: FeatureReceipt
    episodic_summary: object
    universe_scan: object | None
    news_observations: tuple[object, ...]
    article_attributions: tuple[object, ...]
    policy_sha256: str
    candidate_manifest_sha256: str
    strategy_snapshot_sha256: str
    feature_receipt_sha256: str
    episodic_summary_sha256: str
    universe_scan_sha256: str | None
    news_source_policy_sha256: str | None
    news_observation_sha256: tuple[str, ...]
    article_attribution_sha256: tuple[str, ...]
    context_sha256: str


class StrategyV2DirectionState(StrEnum):
    """The deliberately non-confirming states of a V2 Kimi direction receipt."""

    PROPOSED_UNCONFIRMED = "PROPOSED_UNCONFIRMED"
    ABSTAINED = "ABSTAINED"
    REJECTED = "REJECTED"


DIRECTION_ONLY_UNCONFIRMED_AUTHORITY = "DIRECTION_ONLY_UNCONFIRMED"


@dataclass(frozen=True, slots=True)
class StrategyV2DirectionDecision:
    """A canonical V2 direction receipt with no risk or execution authority.

    This intentionally is not a :class:`StrategyDecision`.  A directional
    outcome is only a proposal; confirmation, expression compilation, risk,
    permit issuance, and submission remain separate future boundaries.
    """

    authority: str
    state: StrategyV2DirectionState
    event_id: str
    security_id: str
    candidate_id: str
    cohort_id: str
    policy_sha256: str
    candidate_manifest_sha256: str
    strategy_snapshot_sha256: str
    feature_receipt_sha256: str
    episodic_summary_sha256: str
    context_sha256: str
    route_sha256: str
    model_config_sha256: str
    prompt_sha256: str
    output_schema_sha256: str
    request_sha256: str
    raw_response_bytes: bytes | None
    raw_response_sha256: str | None
    reasoner_decision_sha256: str | None
    transport_status: ExchangeStatus
    started_at: datetime
    responded_at: datetime | None
    deadline_at: datetime
    decision_at: datetime
    producer_identity: str
    producer_build_sha256: str
    reasoner_direction: Direction | None
    direction: Direction
    allowed_citation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    contradictions: tuple[Contradiction, ...]
    unknowns: tuple[str, ...]
    strongest_falsifier: Falsifier | None
    reason_codes: tuple[str, ...]
    summary: str | None

    def __post_init__(self) -> None:
        if self.authority != DIRECTION_ONLY_UNCONFIRMED_AUTHORITY:
            raise ValueError("V2 direction receipt has no confirmation authority")
        if not isinstance(self.state, StrategyV2DirectionState):
            raise ValueError("V2 direction receipt state must be closed")
        for field in (
            "event_id",
            "security_id",
            "candidate_id",
            "cohort_id",
            "producer_identity",
        ):
            _require_identifier(getattr(self, field), field)
        for field in (
            "policy_sha256",
            "candidate_manifest_sha256",
            "strategy_snapshot_sha256",
            "feature_receipt_sha256",
            "episodic_summary_sha256",
            "context_sha256",
            "route_sha256",
            "model_config_sha256",
            "prompt_sha256",
            "output_schema_sha256",
            "request_sha256",
            "producer_build_sha256",
        ):
            _require_sha256(getattr(self, field), field)
        if self.raw_response_bytes is None:
            if self.raw_response_sha256 is not None:
                raise ValueError("raw response hash requires exact raw response bytes")
        elif type(self.raw_response_bytes) is not bytes:
            raise ValueError("raw response bytes must be exact bytes or absent")
        elif self.raw_response_sha256 != hashlib.sha256(self.raw_response_bytes).hexdigest():
            raise ValueError("raw response hash does not bind exact raw response bytes")
        for field in ("raw_response_sha256", "reasoner_decision_sha256"):
            value = getattr(self, field)
            if value is not None:
                _require_sha256(value, field)
        if not isinstance(self.transport_status, ExchangeStatus):
            raise ValueError("V2 direction receipt transport status must be closed")
        for field in ("started_at", "deadline_at", "decision_at"):
            _require_utc(getattr(self, field), field)
        if self.responded_at is not None:
            _require_utc(self.responded_at, "responded_at")
        if self.deadline_at < self.started_at:
            raise ValueError("V2 direction deadline cannot precede start")
        if self.responded_at is not None and self.responded_at < self.started_at:
            raise ValueError("V2 direction response cannot precede start")
        if self.decision_at != (self.responded_at or self.deadline_at):
            raise ValueError("V2 direction decision clock must bind response or deadline")
        _require_identifiers(self.allowed_citation_ids, "allowed_citation_ids")
        _require_identifiers(self.evidence_ids, "evidence_ids")
        _require_reason_codes(self.unknowns, "unknowns")
        _require_reason_codes(self.reason_codes, "reason_codes")
        contradiction_keys = tuple(item.evidence_ids for item in self.contradictions)
        if contradiction_keys != tuple(sorted(set(contradiction_keys))):
            raise ValueError("V2 direction contradictions must be sorted and unique")
        if self.summary is not None:
            _require_normalized_text(
                self.summary,
                "V2 direction summary",
                maximum=_MAX_REASONER_SUMMARY_CHARS,
            )
        cited = set(self.evidence_ids)
        for contradiction in self.contradictions:
            cited.update(contradiction.evidence_ids)
        if self.strongest_falsifier is not None:
            cited.add(self.strongest_falsifier.evidence_id)
        if self.state is StrategyV2DirectionState.PROPOSED_UNCONFIRMED:
            if (
                self.direction not in {Direction.UP, Direction.DOWN}
                or self.reasoner_direction is not self.direction
                or self.reason_codes
                or not self.evidence_ids
                or self.raw_response_sha256 is None
                or self.reasoner_decision_sha256 is None
                or not cited <= set(self.allowed_citation_ids)
            ):
                raise ValueError("direction proposals require exact, cited, parsed provider output")
        elif self.direction is not Direction.UNCERTAIN or not self.reason_codes:
            raise ValueError("V2 abstentions and rejections require UNCERTAIN plus stable reasons")
        if self.state is StrategyV2DirectionState.ABSTAINED and (
            self.reasoner_direction is not Direction.UNCERTAIN
            or self.reason_codes != ("REASONER_UNCERTAIN",)
        ):
            raise ValueError("only an otherwise-valid model UNCERTAIN is an abstention")
