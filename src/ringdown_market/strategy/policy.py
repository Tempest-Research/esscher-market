"""Frozen Esscher v1 strategy policy identity, contract, and strict parser."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

STRATEGY_POLICY_SCHEMA = "esscher.strategy_policy"
STRATEGY_POLICY_SCHEMA_VERSION = 1
STRATEGY_POLICY_VERSION = "esscher-strategy-v1"
STRATEGY_POLICY_V1_SHA256 = "fb3eb4dc0e8898a6cea1ad159611623c8cad16143a6dc71ad4179c610a72ac10"

_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TIME_OF_DAY = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")

_BASELINE_NAMES = (
    "always_abstain",
    "gap_continue",
    "gap_reverse",
    "price_only",
    "fundamental_rule",
    "no_text_ablation",
)


class PolicyRejectionReason(StrEnum):
    """Stable fail-closed reasons a strategy policy document cannot be accepted."""

    DUPLICATE_KEY = "DUPLICATE_KEY"
    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_VALUE = "INVALID_VALUE"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    UNFROZEN_POLICY = "UNFROZEN_POLICY"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"


class StrategyPolicyRejected(ValueError):
    """Raised when strategy policy bytes fail the frozen contract."""

    def __init__(self, reason: PolicyRejectionReason, path: str, detail: str) -> None:
        super().__init__(f"{reason.value} at {path}: {detail}")
        self.reason = reason
        self.path = path
        self.detail = detail


def _reject(reason: PolicyRejectionReason, path: str, detail: str) -> None:
    raise StrategyPolicyRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            _reject(PolicyRejectionReason.DUPLICATE_KEY, key, "duplicate JSON key")
        payload[key] = value
    return payload


def _invalid_constant(value: str) -> None:
    _reject(PolicyRejectionReason.NON_FINITE_VALUE, value, "non-finite JSON constant")


def _decode_document(raw: bytes, *, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except UnicodeDecodeError as error:
        _reject(PolicyRejectionReason.INVALID_DOCUMENT, label, f"not valid UTF-8: {error}")
    except json.JSONDecodeError as error:
        _reject(PolicyRejectionReason.INVALID_DOCUMENT, label, f"invalid JSON: {error.msg}")
    if not isinstance(payload, dict):
        _reject(PolicyRejectionReason.INVALID_TYPE, label, "top-level document must be an object")
    return payload


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected an object")
    payload = dict(value)
    for key in payload:
        if key not in fields:
            _reject(PolicyRejectionReason.UNKNOWN_FIELD, f"{path}.{key}", "unknown field")
    for key in fields:
        if key not in payload:
            _reject(PolicyRejectionReason.MISSING_FIELD, f"{path}.{key}", "missing required field")
    return payload


def _nonempty_text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected non-empty text")
    return value


def _boolean(value: object, *, path: str) -> bool:
    if not isinstance(value, bool):
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected a boolean")
    return value


def _integer(value: object, *, path: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected an integer")
    if minimum is not None and value < minimum:
        _reject(PolicyRejectionReason.INVALID_VALUE, path, f"must be at least {minimum}")
    return value


def _money(value: object, *, path: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.match(value):
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected canonical decimal text")
    try:
        return Decimal(value)
    except InvalidOperation:
        _reject(PolicyRejectionReason.INVALID_VALUE, path, "not a finite decimal")


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.match(value):
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected UTC second-precision timestamp")
    try:
        parsed = datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        _reject(PolicyRejectionReason.INVALID_VALUE, path, "timestamp does not exist")
    if parsed > datetime.now(UTC):
        _reject(PolicyRejectionReason.FUTURE_TIMESTAMP, path, "timestamp is in the future")
    return parsed


def _time_of_day(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _TIME_OF_DAY.match(value):
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected HH:MM:SS time of day")
    return value


def _string_tuple(
    value: object, *, path: str, allowed: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected a non-empty list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            _reject(
                PolicyRejectionReason.INVALID_TYPE, f"{path}[{index}]", "expected non-empty text"
            )
        if item in items:
            _reject(PolicyRejectionReason.INVALID_VALUE, f"{path}[{index}]", "duplicate entry")
        items.append(item)
    if allowed is not None and tuple(items) != allowed:
        _reject(PolicyRejectionReason.INVALID_VALUE, path, f"must be exactly {list(allowed)}")
    return tuple(items)


@dataclass(frozen=True, slots=True)
class PolicyHypothesis:
    id: str
    statement: str
    target: str
    not_predicted: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicyUniverse:
    listing: str
    optionable_required: bool
    minimum_price: Decimal
    scheduled_only: bool
    earnings_timing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicyTiming:
    timezone: str
    observation_window_start: str
    observation_window_end: str
    decision_cutoff_rule: str
    valid_signal_by: str
    no_open_submission_after: str
    close_all_positions_by: str


@dataclass(frozen=True, slots=True)
class PolicyFeature:
    feature_id: str
    availability_rule: str
    permitted_source_classes: tuple[str, ...]
    definition: str


@dataclass(frozen=True, slots=True)
class PolicyInformationSet:
    permitted_source_kinds: tuple[str, ...]
    prohibited_sources: tuple[str, ...]
    unknowns_must_be_explicit: bool
    imputation_forbidden: bool
    features: tuple[PolicyFeature, ...]


@dataclass(frozen=True, slots=True)
class PolicyBeta:
    version: str
    market_proxy: str
    sector_proxy_policy: str
    estimation_data_cutoff_rule: str
    post_event_data_forbidden: bool
    reestimation_after_outcome_forbidden: bool


@dataclass(frozen=True, slots=True)
class PolicyAbstentionRule:
    code: str
    condition: str


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outputs: tuple[str, ...]
    abstain_output: str
    confidence_authorizes_trade: bool
    prose_controls_arithmetic: bool
    no_fallback_signal: bool
    reaction_relation_values: tuple[str, ...]
    reaction_relation_computed_by: str
    abstention_rules: tuple[PolicyAbstentionRule, ...]


@dataclass(frozen=True, slots=True)
class PolicyReasoner:
    role: str
    prohibited_authorities: tuple[str, ...]
    route_count: int
    transparent_fallback: bool
    invalid_output_disposition: str
    bound_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicyExpression:
    kind: str
    quantity: int
    dte_min_days: int
    dte_max_days: int
    allowed_widths_usd: tuple[Decimal, ...]
    selection_authority: str


@dataclass(frozen=True, slots=True)
class PolicyExit:
    hold_minutes: int
    hold_anchor: str
    model_exit: bool
    profit_take: bool
    stop_loss: bool
    close_style: str
    close_all_positions_by_rule: str


@dataclass(frozen=True, slots=True)
class PolicyResidualConvention:
    return_type: str
    formula: str
    window: str
    unit_risk: str
    abstention_signed_return: float


@dataclass(frozen=True, slots=True)
class PolicyEvidenceRequirements:
    panel_min_events: int
    panel_max_events: int
    qfast_minimum_events: int
    qfast_reject_reasons: tuple[str, ...]
    latency_required_profile: str
    zero_latency_and_p95_reported_separately: bool
    historical_confirmation_required_before_paper_mutation: bool
    prospective_shadow_required_before_paper_mutation: bool
    threshold_disposition_when_unmet: str


@dataclass(frozen=True, slots=True)
class PolicyEventSets:
    development_event_ids: tuple[str, ...]
    development_events_excluded_from_confirmation_panel: bool
    confirmation_panel: str
    prospective_events: str
    separation_before_outcome_inspection: bool


@dataclass(frozen=True, slots=True)
class PolicyBoundaries:
    execution_mode: str
    broker_boundary: str
    real_money_mode: bool
    claim: str
    data_qualifiers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyPolicy:
    schema: str
    schema_version: int
    policy_id: str
    policy_version: str
    frozen: bool
    frozen_at: datetime
    product_name: str
    machine_package: str
    hypothesis: PolicyHypothesis
    universe: PolicyUniverse
    timing: PolicyTiming
    information_set: PolicyInformationSet
    beta_policy: PolicyBeta
    decision: PolicyDecision
    reasoner: PolicyReasoner
    expression: PolicyExpression
    exit: PolicyExit
    baselines: tuple[str, ...]
    residual_convention: PolicyResidualConvention
    evidence_requirements: PolicyEvidenceRequirements
    event_sets: PolicyEventSets
    boundaries: PolicyBoundaries
    sha256: str


def strategy_policy_sha256(raw: bytes) -> str:
    """Return the SHA-256 of exact strategy policy bytes."""

    if not isinstance(raw, bytes) or not raw:
        raise ValueError("strategy policy bytes must be non-empty bytes")
    return hashlib.sha256(raw).hexdigest()


def _parse_hypothesis(value: object, *, path: str) -> PolicyHypothesis:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset({"id", "statement", "target", "not_predicted"}),
    )
    return PolicyHypothesis(
        id=_nonempty_text(payload["id"], path=f"{path}.id"),
        statement=_nonempty_text(payload["statement"], path=f"{path}.statement"),
        target=_nonempty_text(payload["target"], path=f"{path}.target"),
        not_predicted=_string_tuple(payload["not_predicted"], path=f"{path}.not_predicted"),
    )


def _parse_universe(value: object, *, path: str) -> PolicyUniverse:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "listing",
                "optionable_required",
                "minimum_price_usd",
                "scheduled_only",
                "earnings_timing",
            }
        ),
    )
    listing = _nonempty_text(payload["listing"], path=f"{path}.listing")
    if listing != "US_LISTED_COMMON_EQUITY":
        _reject(PolicyRejectionReason.INVALID_VALUE, f"{path}.listing", "unsupported listing")
    minimum_price = _money(payload["minimum_price_usd"], path=f"{path}.minimum_price_usd")
    if minimum_price < Decimal("10.00"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.minimum_price_usd",
            "universe price floor is frozen at 10.00",
        )
    return PolicyUniverse(
        listing=listing,
        optionable_required=_boolean(
            payload["optionable_required"], path=f"{path}.optionable_required"
        ),
        minimum_price=minimum_price,
        scheduled_only=_boolean(payload["scheduled_only"], path=f"{path}.scheduled_only"),
        earnings_timing=_string_tuple(
            payload["earnings_timing"],
            path=f"{path}.earnings_timing",
            allowed=("BMO", "AMC"),
        ),
    )


def _parse_timing(value: object, *, path: str) -> PolicyTiming:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "timezone",
                "observation_window_start",
                "observation_window_end",
                "decision_cutoff_rule",
                "valid_signal_by",
                "no_open_submission_after",
                "close_all_positions_by",
            }
        ),
    )
    timezone_name = _nonempty_text(payload["timezone"], path=f"{path}.timezone")
    if timezone_name != "America/New_York":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.timezone",
            "timing is frozen to America/New_York",
        )
    start = _time_of_day(
        payload["observation_window_start"], path=f"{path}.observation_window_start"
    )
    end = _time_of_day(payload["observation_window_end"], path=f"{path}.observation_window_end")
    valid_by = _time_of_day(payload["valid_signal_by"], path=f"{path}.valid_signal_by")
    no_submit_after = _time_of_day(
        payload["no_open_submission_after"], path=f"{path}.no_open_submission_after"
    )
    close_by = _time_of_day(
        payload["close_all_positions_by"], path=f"{path}.close_all_positions_by"
    )
    if not start < end < valid_by < no_submit_after <= close_by:
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            path,
            "timing bounds must satisfy start < end < valid_signal_by "
            "< no_open_submission_after <= close_all_positions_by",
        )
    cutoff_rule = _nonempty_text(
        payload["decision_cutoff_rule"], path=f"{path}.decision_cutoff_rule"
    )
    if cutoff_rule != "OBSERVATION_WINDOW_END":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.decision_cutoff_rule",
            "decision cutoff is frozen at the observation window end",
        )
    return PolicyTiming(
        timezone=timezone_name,
        observation_window_start=start,
        observation_window_end=end,
        decision_cutoff_rule=cutoff_rule,
        valid_signal_by=valid_by,
        no_open_submission_after=no_submit_after,
        close_all_positions_by=close_by,
    )


def _parse_features(value: object, *, path: str) -> tuple[PolicyFeature, ...]:
    if not isinstance(value, list) or not value:
        _reject(PolicyRejectionReason.INVALID_TYPE, path, "expected a non-empty feature list")
    features: list[PolicyFeature] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        payload = _strict_object(
            item,
            path=item_path,
            fields=frozenset(
                {"feature_id", "availability_rule", "permitted_source_classes", "definition"}
            ),
        )
        feature_id = _nonempty_text(payload["feature_id"], path=f"{item_path}.feature_id")
        if feature_id in seen:
            _reject(
                PolicyRejectionReason.INVALID_VALUE, f"{item_path}.feature_id", "duplicate feature"
            )
        seen.add(feature_id)
        features.append(
            PolicyFeature(
                feature_id=feature_id,
                availability_rule=_nonempty_text(
                    payload["availability_rule"], path=f"{item_path}.availability_rule"
                ),
                permitted_source_classes=_string_tuple(
                    payload["permitted_source_classes"],
                    path=f"{item_path}.permitted_source_classes",
                ),
                definition=_nonempty_text(payload["definition"], path=f"{item_path}.definition"),
            )
        )
    return tuple(features)


def _parse_information_set(value: object, *, path: str) -> PolicyInformationSet:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "permitted_source_kinds",
                "prohibited_sources",
                "unknowns_must_be_explicit",
                "imputation_forbidden",
                "features",
            }
        ),
    )
    permitted = _string_tuple(
        payload["permitted_source_kinds"], path=f"{path}.permitted_source_kinds"
    )
    if set(permitted) != {"ISSUER_PRIMARY", "SEC_OFFICIAL", "OFFICIAL_MARKET_DATA"}:
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.permitted_source_kinds",
            "permitted source kinds are frozen",
        )
    if not _boolean(payload["unknowns_must_be_explicit"], path=f"{path}.unknowns_must_be_explicit"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.unknowns_must_be_explicit",
            "unknowns must remain explicit",
        )
    if not _boolean(payload["imputation_forbidden"], path=f"{path}.imputation_forbidden"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.imputation_forbidden",
            "imputation must remain forbidden",
        )
    return PolicyInformationSet(
        permitted_source_kinds=permitted,
        prohibited_sources=_string_tuple(
            payload["prohibited_sources"], path=f"{path}.prohibited_sources"
        ),
        unknowns_must_be_explicit=True,
        imputation_forbidden=True,
        features=_parse_features(payload["features"], path=f"{path}.features"),
    )


def _parse_beta_policy(value: object, *, path: str) -> PolicyBeta:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "version",
                "market_proxy",
                "sector_proxy_policy",
                "estimation_data_cutoff_rule",
                "post_event_data_forbidden",
                "reestimation_after_outcome_forbidden",
            }
        ),
    )
    if not _boolean(payload["post_event_data_forbidden"], path=f"{path}.post_event_data_forbidden"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.post_event_data_forbidden",
            "post-event beta data must remain forbidden",
        )
    if not _boolean(
        payload["reestimation_after_outcome_forbidden"],
        path=f"{path}.reestimation_after_outcome_forbidden",
    ):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.reestimation_after_outcome_forbidden",
            "beta re-estimation after outcomes must remain forbidden",
        )
    market_proxy = _nonempty_text(payload["market_proxy"], path=f"{path}.market_proxy")
    if market_proxy != "SPY":
        _reject(
            PolicyRejectionReason.INVALID_VALUE, f"{path}.market_proxy", "market proxy is frozen"
        )
    return PolicyBeta(
        version=_nonempty_text(payload["version"], path=f"{path}.version"),
        market_proxy=market_proxy,
        sector_proxy_policy=_nonempty_text(
            payload["sector_proxy_policy"], path=f"{path}.sector_proxy_policy"
        ),
        estimation_data_cutoff_rule=_nonempty_text(
            payload["estimation_data_cutoff_rule"], path=f"{path}.estimation_data_cutoff_rule"
        ),
        post_event_data_forbidden=True,
        reestimation_after_outcome_forbidden=True,
    )


def _parse_decision(value: object, *, path: str) -> PolicyDecision:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "outputs",
                "abstain_output",
                "confidence_authorizes_trade",
                "prose_controls_arithmetic",
                "no_fallback_signal",
                "reaction_relation_values",
                "reaction_relation_computed_by",
                "abstention_rules",
            }
        ),
    )
    outputs = _string_tuple(
        payload["outputs"], path=f"{path}.outputs", allowed=("UP", "DOWN", "UNCERTAIN")
    )
    if payload["abstain_output"] != "UNCERTAIN":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.abstain_output",
            "abstention must be UNCERTAIN",
        )
    if _boolean(payload["confidence_authorizes_trade"], path=f"{path}.confidence_authorizes_trade"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.confidence_authorizes_trade",
            "self-reported confidence must never authorize a trade",
        )
    if _boolean(payload["prose_controls_arithmetic"], path=f"{path}.prose_controls_arithmetic"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.prose_controls_arithmetic",
            "prose must never control market arithmetic",
        )
    if not _boolean(payload["no_fallback_signal"], path=f"{path}.no_fallback_signal"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.no_fallback_signal",
            "a fallback signal must never exist",
        )
    relations = _string_tuple(
        payload["reaction_relation_values"],
        path=f"{path}.reaction_relation_values",
        allowed=("CONTINUE", "REVERSE", "NONE"),
    )
    computed_by = _nonempty_text(
        payload["reaction_relation_computed_by"], path=f"{path}.reaction_relation_computed_by"
    )
    if computed_by != "DETERMINISTIC_CODE":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.reaction_relation_computed_by",
            "reaction_relation must be computed by deterministic code",
        )
    rules_value = payload["abstention_rules"]
    if not isinstance(rules_value, list) or not rules_value:
        _reject(
            PolicyRejectionReason.INVALID_TYPE,
            f"{path}.abstention_rules",
            "expected abstention rules",
        )
    rules: list[PolicyAbstentionRule] = []
    seen: set[str] = set()
    for index, item in enumerate(rules_value):
        item_path = f"{path}.abstention_rules[{index}]"
        rule = _strict_object(item, path=item_path, fields=frozenset({"code", "condition"}))
        code = _nonempty_text(rule["code"], path=f"{item_path}.code")
        if code in seen:
            _reject(
                PolicyRejectionReason.INVALID_VALUE,
                f"{item_path}.code",
                "duplicate abstention code",
            )
        seen.add(code)
        rules.append(
            PolicyAbstentionRule(
                code=code,
                condition=_nonempty_text(rule["condition"], path=f"{item_path}.condition"),
            )
        )
    return PolicyDecision(
        outputs=outputs,
        abstain_output="UNCERTAIN",
        confidence_authorizes_trade=False,
        prose_controls_arithmetic=False,
        no_fallback_signal=True,
        reaction_relation_values=relations,
        reaction_relation_computed_by=computed_by,
        abstention_rules=tuple(rules),
    )


def _parse_reasoner(value: object, *, path: str) -> PolicyReasoner:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "role",
                "prohibited_authorities",
                "route_count",
                "transparent_fallback",
                "invalid_output_disposition",
                "bound_by",
            }
        ),
    )
    route_count = _integer(payload["route_count"], path=f"{path}.route_count", minimum=1)
    if route_count != 1:
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.route_count",
            "exactly one route is frozen",
        )
    if _boolean(payload["transparent_fallback"], path=f"{path}.transparent_fallback"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.transparent_fallback",
            "a transparent fallback must never exist",
        )
    if payload["invalid_output_disposition"] != "UNCERTAIN":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.invalid_output_disposition",
            "invalid reasoner output must become UNCERTAIN",
        )
    prohibited = _string_tuple(
        payload["prohibited_authorities"], path=f"{path}.prohibited_authorities"
    )
    if set(prohibited) != {"CONTRACT_SELECTION", "SIZING", "ENTRY", "EXIT"}:
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.prohibited_authorities",
            "reasoner prohibited authorities are frozen",
        )
    return PolicyReasoner(
        role=_nonempty_text(payload["role"], path=f"{path}.role"),
        prohibited_authorities=prohibited,
        route_count=1,
        transparent_fallback=False,
        invalid_output_disposition="UNCERTAIN",
        bound_by=_string_tuple(payload["bound_by"], path=f"{path}.bound_by"),
    )


def _parse_expression(value: object, *, path: str) -> PolicyExpression:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "kind",
                "quantity",
                "dte_min_days",
                "dte_max_days",
                "allowed_widths_usd",
                "selection_authority",
            }
        ),
    )
    if payload["kind"] != "DEBIT_VERTICAL":
        _reject(
            PolicyRejectionReason.INVALID_VALUE, f"{path}.kind", "v1 expresses one debit vertical"
        )
    quantity = _integer(payload["quantity"], path=f"{path}.quantity", minimum=1)
    if quantity != 1:
        _reject(
            PolicyRejectionReason.INVALID_VALUE, f"{path}.quantity", "quantity is frozen at one"
        )
    dte_min = _integer(payload["dte_min_days"], path=f"{path}.dte_min_days", minimum=1)
    dte_max = _integer(payload["dte_max_days"], path=f"{path}.dte_max_days", minimum=1)
    if not 7 <= dte_min <= dte_max <= 21:
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            path,
            "expiry is frozen within 7 to 21 calendar days",
        )
    widths_value = payload["allowed_widths_usd"]
    if not isinstance(widths_value, list) or not widths_value:
        _reject(PolicyRejectionReason.INVALID_TYPE, f"{path}.allowed_widths_usd", "expected widths")
    widths = tuple(
        _money(item, path=f"{path}.allowed_widths_usd[{index}]")
        for index, item in enumerate(widths_value)
    )
    if widths != (Decimal("2.50"), Decimal("5.00")):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.allowed_widths_usd",
            "widths are frozen at 2.50 and 5.00",
        )
    selection_authority = _nonempty_text(
        payload["selection_authority"], path=f"{path}.selection_authority"
    )
    if selection_authority != "DETERMINISTIC_OPTION_COMPILER_ONLY":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.selection_authority",
            "only the deterministic option compiler may select contracts",
        )
    return PolicyExpression(
        kind="DEBIT_VERTICAL",
        quantity=1,
        dte_min_days=dte_min,
        dte_max_days=dte_max,
        allowed_widths_usd=widths,
        selection_authority=selection_authority,
    )


def _parse_exit(value: object, *, path: str) -> PolicyExit:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "hold_minutes",
                "hold_anchor",
                "model_exit",
                "profit_take",
                "stop_loss",
                "close_style",
                "close_all_positions_by_rule",
            }
        ),
    )
    hold_minutes = _integer(payload["hold_minutes"], path=f"{path}.hold_minutes", minimum=1)
    if hold_minutes != 60:
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.hold_minutes",
            "hold is frozen at 60 minutes",
        )
    if payload["hold_anchor"] != "RECONCILED_OPENING_FILL":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.hold_anchor",
            "hold must anchor at the reconciled opening fill",
        )
    for field in ("model_exit", "profit_take", "stop_loss"):
        if _boolean(payload[field], path=f"{path}.{field}"):
            _reject(
                PolicyRejectionReason.INVALID_VALUE,
                f"{path}.{field}",
                "v1 has no model exit, profit-take, or stop-loss",
            )
    if payload["close_style"] != "ATOMIC_MULTI_LEG":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.close_style",
            "close must be atomic multi-leg",
        )
    return PolicyExit(
        hold_minutes=60,
        hold_anchor="RECONCILED_OPENING_FILL",
        model_exit=False,
        profit_take=False,
        stop_loss=False,
        close_style="ATOMIC_MULTI_LEG",
        close_all_positions_by_rule=_nonempty_text(
            payload["close_all_positions_by_rule"], path=f"{path}.close_all_positions_by_rule"
        ),
    )


def _parse_residual_convention(value: object, *, path: str) -> PolicyResidualConvention:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {"return_type", "formula", "window", "unit_risk", "abstention_signed_return"}
        ),
    )
    abstention_return = payload["abstention_signed_return"]
    if isinstance(abstention_return, bool) or not isinstance(abstention_return, (int, float)):
        _reject(
            PolicyRejectionReason.INVALID_TYPE,
            f"{path}.abstention_signed_return",
            "expected a number",
        )
    abstention_float = float(abstention_return)
    if abstention_float != 0.0:
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.abstention_signed_return",
            "abstentions must remain zero signed return",
        )
    return PolicyResidualConvention(
        return_type=_nonempty_text(payload["return_type"], path=f"{path}.return_type"),
        formula=_nonempty_text(payload["formula"], path=f"{path}.formula"),
        window=_nonempty_text(payload["window"], path=f"{path}.window"),
        unit_risk=_nonempty_text(payload["unit_risk"], path=f"{path}.unit_risk"),
        abstention_signed_return=abstention_float,
    )


def _parse_evidence_requirements(value: object, *, path: str) -> PolicyEvidenceRequirements:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "panel_min_events",
                "panel_max_events",
                "qfast_minimum_events",
                "qfast_reject_reasons",
                "latency_required_profile",
                "zero_latency_and_p95_reported_separately",
                "historical_confirmation_required_before_paper_mutation",
                "prospective_shadow_required_before_paper_mutation",
                "threshold_disposition_when_unmet",
            }
        ),
    )
    panel_min = _integer(payload["panel_min_events"], path=f"{path}.panel_min_events", minimum=20)
    panel_max = _integer(
        payload["panel_max_events"], path=f"{path}.panel_max_events", minimum=panel_min
    )
    qfast_minimum = _integer(
        payload["qfast_minimum_events"], path=f"{path}.qfast_minimum_events", minimum=20
    )
    if not _boolean(
        payload["zero_latency_and_p95_reported_separately"],
        path=f"{path}.zero_latency_and_p95_reported_separately",
    ):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.zero_latency_and_p95_reported_separately",
            "zero-latency and p95 results must stay separate",
        )
    for field in (
        "historical_confirmation_required_before_paper_mutation",
        "prospective_shadow_required_before_paper_mutation",
    ):
        if not _boolean(payload[field], path=f"{path}.{field}"):
            _reject(
                PolicyRejectionReason.INVALID_VALUE,
                f"{path}.{field}",
                "evidence gates before PAPER mutation are frozen",
            )
    if payload["latency_required_profile"] != "p95":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.latency_required_profile",
            "latency gate profile is frozen at p95",
        )
    return PolicyEvidenceRequirements(
        panel_min_events=panel_min,
        panel_max_events=panel_max,
        qfast_minimum_events=qfast_minimum,
        qfast_reject_reasons=_string_tuple(
            payload["qfast_reject_reasons"], path=f"{path}.qfast_reject_reasons"
        ),
        latency_required_profile="p95",
        zero_latency_and_p95_reported_separately=True,
        historical_confirmation_required_before_paper_mutation=True,
        prospective_shadow_required_before_paper_mutation=True,
        threshold_disposition_when_unmet=_nonempty_text(
            payload["threshold_disposition_when_unmet"],
            path=f"{path}.threshold_disposition_when_unmet",
        ),
    )


def _parse_event_sets(value: object, *, path: str) -> PolicyEventSets:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {
                "development_event_ids",
                "development_events_excluded_from_confirmation_panel",
                "confirmation_panel",
                "prospective_events",
                "separation_before_outcome_inspection",
            }
        ),
    )
    if not _boolean(
        payload["development_events_excluded_from_confirmation_panel"],
        path=f"{path}.development_events_excluded_from_confirmation_panel",
    ):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.development_events_excluded_from_confirmation_panel",
            "development events must stay excluded from the confirmation panel",
        )
    if not _boolean(
        payload["separation_before_outcome_inspection"],
        path=f"{path}.separation_before_outcome_inspection",
    ):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.separation_before_outcome_inspection",
            "event sets must be separated before outcomes are inspected",
        )
    return PolicyEventSets(
        development_event_ids=_string_tuple(
            payload["development_event_ids"], path=f"{path}.development_event_ids"
        ),
        development_events_excluded_from_confirmation_panel=True,
        confirmation_panel=_nonempty_text(
            payload["confirmation_panel"], path=f"{path}.confirmation_panel"
        ),
        prospective_events=_nonempty_text(
            payload["prospective_events"], path=f"{path}.prospective_events"
        ),
        separation_before_outcome_inspection=True,
    )


def _parse_boundaries(value: object, *, path: str) -> PolicyBoundaries:
    payload = _strict_object(
        value,
        path=path,
        fields=frozenset(
            {"execution_mode", "broker_boundary", "real_money_mode", "claim", "data_qualifiers"}
        ),
    )
    if payload["execution_mode"] != "PAPER_ONLY":
        _reject(
            PolicyRejectionReason.INVALID_VALUE, f"{path}.execution_mode", "execution is PAPER_ONLY"
        )
    if payload["broker_boundary"] != "OFFICIAL_ALPACA_MCP_ONLY":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.broker_boundary",
            "only the official Alpaca MCP boundary is permitted",
        )
    if _boolean(payload["real_money_mode"], path=f"{path}.real_money_mode"):
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.real_money_mode",
            "a real-money mode must never exist",
        )
    if payload["claim"] != "NOT_ALPHA_EVIDENCE":
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            f"{path}.claim",
            "claim must remain NOT_ALPHA_EVIDENCE",
        )
    return PolicyBoundaries(
        execution_mode="PAPER_ONLY",
        broker_boundary="OFFICIAL_ALPACA_MCP_ONLY",
        real_money_mode=False,
        claim="NOT_ALPHA_EVIDENCE",
        data_qualifiers=_string_tuple(
            payload["data_qualifiers"],
            path=f"{path}.data_qualifiers",
            allowed=("INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE"),
        ),
    )


def parse_strategy_policy(raw: bytes, *, expected_sha256: str) -> StrategyPolicy:
    """Parse exact frozen strategy policy bytes; any drift fails closed."""

    if not isinstance(raw, (bytes, bytearray)):
        raise StrategyPolicyRejected(
            PolicyRejectionReason.INVALID_DOCUMENT, "policy", "policy bytes are required"
        )
    raw = bytes(raw)
    sha256 = strategy_policy_sha256(raw)
    if sha256 != expected_sha256:
        _reject(
            PolicyRejectionReason.POLICY_HASH_MISMATCH,
            "policy",
            "strategy policy bytes do not match the expected frozen identity",
        )
    payload = _strict_object(
        _decode_document(raw, label="policy"),
        path="policy",
        fields=frozenset(
            {
                "schema",
                "schema_version",
                "policy_id",
                "policy_version",
                "frozen",
                "frozen_at",
                "product_name",
                "machine_package",
                "hypothesis",
                "universe",
                "timing",
                "information_set",
                "beta_policy",
                "decision",
                "reasoner",
                "expression",
                "exit",
                "baselines",
                "residual_convention",
                "evidence_requirements",
                "event_sets",
                "boundaries",
            }
        ),
    )
    if payload["schema"] != STRATEGY_POLICY_SCHEMA:
        _reject(PolicyRejectionReason.UNSUPPORTED_SCHEMA, "policy.schema", "unsupported schema")
    if payload["schema_version"] != STRATEGY_POLICY_SCHEMA_VERSION:
        _reject(
            PolicyRejectionReason.UNSUPPORTED_SCHEMA,
            "policy.schema_version",
            "unsupported schema version",
        )
    frozen = payload["frozen"]
    if not isinstance(frozen, bool) or not frozen:
        _reject(
            PolicyRejectionReason.UNFROZEN_POLICY,
            "policy.frozen",
            "only a frozen policy is accepted",
        )
    policy_version = _nonempty_text(payload["policy_version"], path="policy.policy_version")
    if policy_version != STRATEGY_POLICY_VERSION:
        _reject(
            PolicyRejectionReason.INVALID_VALUE,
            "policy.policy_version",
            "unknown strategy policy version",
        )
    baselines = _string_tuple(payload["baselines"], path="policy.baselines")
    if baselines != _BASELINE_NAMES:
        _reject(PolicyRejectionReason.INVALID_VALUE, "policy.baselines", "baselines are frozen")
    return StrategyPolicy(
        schema=STRATEGY_POLICY_SCHEMA,
        schema_version=STRATEGY_POLICY_SCHEMA_VERSION,
        policy_id=_nonempty_text(payload["policy_id"], path="policy.policy_id"),
        policy_version=policy_version,
        frozen=True,
        frozen_at=_timestamp(payload["frozen_at"], path="policy.frozen_at"),
        product_name=_nonempty_text(payload["product_name"], path="policy.product_name"),
        machine_package=_nonempty_text(payload["machine_package"], path="policy.machine_package"),
        hypothesis=_parse_hypothesis(payload["hypothesis"], path="policy.hypothesis"),
        universe=_parse_universe(payload["universe"], path="policy.universe"),
        timing=_parse_timing(payload["timing"], path="policy.timing"),
        information_set=_parse_information_set(
            payload["information_set"], path="policy.information_set"
        ),
        beta_policy=_parse_beta_policy(payload["beta_policy"], path="policy.beta_policy"),
        decision=_parse_decision(payload["decision"], path="policy.decision"),
        reasoner=_parse_reasoner(payload["reasoner"], path="policy.reasoner"),
        expression=_parse_expression(payload["expression"], path="policy.expression"),
        exit=_parse_exit(payload["exit"], path="policy.exit"),
        baselines=baselines,
        residual_convention=_parse_residual_convention(
            payload["residual_convention"], path="policy.residual_convention"
        ),
        evidence_requirements=_parse_evidence_requirements(
            payload["evidence_requirements"], path="policy.evidence_requirements"
        ),
        event_sets=_parse_event_sets(payload["event_sets"], path="policy.event_sets"),
        boundaries=_parse_boundaries(payload["boundaries"], path="policy.boundaries"),
        sha256=sha256,
    )


def parse_frozen_strategy_policy_v1(raw: bytes) -> StrategyPolicy:
    """Parse bytes that must be exactly the frozen Esscher v1 policy."""

    return parse_strategy_policy(raw, expected_sha256=STRATEGY_POLICY_V1_SHA256)
