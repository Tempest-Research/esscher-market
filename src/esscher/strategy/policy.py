"""Strict loader for the accepted event-strategy research policy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from types import MappingProxyType
from typing import Any, Final, cast

type JsonScalar = str | int | bool | None
type FrozenJson = JsonScalar | tuple[FrozenJson, ...] | MappingProxyType

POLICY_RESOURCE_NAME: Final = "policies/accepted_event_policy_v1.json"
# Updated only when the canonical policy bytes are intentionally amended.
ACCEPTED_EVENT_POLICY_V1_SHA256: Final = (
    "afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca"
)

# V2 is a separate immutable policy package.  V1's resource name, bytes, and
# digest deliberately remain untouched so historical V1 evidence can continue
# to load against the policy that produced it.
POLICY_V2_RESOURCE_NAME: Final = "policies/accepted_event_policy_v2.json"
ACCEPTED_EVENT_POLICY_V2_SHA256: Final = (
    "a9785b25feca845d22bc78b248315141cf9a9f0da4bf449e5dc93caa104dc293"
)

_TOP_LEVEL_FIELDS = frozenset(
    {
        "amendment",
        "authority",
        "baselines",
        "candidates",
        "claims",
        "gate_a",
        "legacy_infrastructure",
        "policy_id",
        "policy_version",
        "reasoner",
        "schema",
        "schema_version",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {
        "activation",
        "candidate_id",
        "clocks",
        "confirmation",
        "data_health",
        "evidence",
        "exclusions",
        "expression_and_exit",
        "features",
        "hypothesis",
        "partitions",
        "sample_gates",
        "universe",
        "value_provenance",
    }
)
_EXPECTED_CANDIDATES = (
    "EARNINGS_RESIDUAL_CONTINUATION_V1",
    "MACRO_SPY_CONTINUATION_CHALLENGER_V1",
)
_EXPECTED_COHORTS = {
    "EARNINGS_RESIDUAL_CONTINUATION_V1": ("BMO", "AMC"),
    "MACRO_SPY_CONTINUATION_CHALLENGER_V1": (
        "BLS_JOLTS",
        "BLS_EMPLOYMENT_SITUATION",
    ),
}
_EXPECTED_RELEASE_FAMILIES = {
    "EARNINGS_RESIDUAL_CONTINUATION_V1": (None, None),
    "MACRO_SPY_CONTINUATION_CHALLENGER_V1": (
        "BLS_JOLTS",
        "BLS_EMPLOYMENT_SITUATION",
    ),
}
_EXPECTED_BASELINES = (
    "CASH_ALWAYS_UNCERTAIN",
    "PRICE_CONTINUATION",
    "PRICE_REVERSAL",
    "DETERMINISTIC_PARSER",
    "BOUNDED_LLM",
    "NO_TEXT_ABLATION",
    "OPPOSITE_LLM_PLACEBO",
    "SEEDED_RANDOM_PLACEBO_256",
)


class StrategyPolicyError(ValueError):
    """Raised when the packaged policy is not the exact accepted policy."""


def _reject_constant(value: str) -> Any:
    raise StrategyPolicyError(f"non-finite JSON number is forbidden: {value}")


def _reject_float(value: str) -> Any:
    raise StrategyPolicyError(
        f"JSON floating-point literals are forbidden; use canonical decimal strings: {value}"
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrategyPolicyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _expect_object(value: Any, path: str, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StrategyPolicyError(f"{path} must be an object")
    actual = frozenset(value)
    missing = sorted(fields - actual)
    unknown = sorted(actual - fields)
    if missing:
        raise StrategyPolicyError(f"{path} is missing fields: {', '.join(missing)}")
    if unknown:
        raise StrategyPolicyError(f"{path} has unknown fields: {', '.join(unknown)}")
    return value


def _expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise StrategyPolicyError(f"{path} must be an array")
    return value


def _expect_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise StrategyPolicyError(f"{path} must be a non-empty string")
    return value


def _expect_bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise StrategyPolicyError(f"{path} must be a boolean")
    return value


def _expect_int(value: Any, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise StrategyPolicyError(f"{path} must be an integer greater than or equal to {minimum}")
    return value


def _validate_rule_value(value: Any, path: str) -> None:
    if isinstance(value, str) or value is None or type(value) in {bool, int}:
        return
    _validate_string_list(value, path)


def _validate_string_list(value: Any, path: str, *, unique: bool = True) -> tuple[str, ...]:
    items = _expect_list(value, path)
    strings = tuple(_expect_string(item, f"{path}[{index}]") for index, item in enumerate(items))
    if unique and len(strings) != len(set(strings)):
        raise StrategyPolicyError(f"{path} must not contain duplicates")
    return strings


def _validate_records(
    value: Any,
    path: str,
    fields: frozenset[str],
) -> list[dict[str, Any]]:
    return [
        _expect_object(item, f"{path}[{index}]", fields)
        for index, item in enumerate(_expect_list(value, path))
    ]


def _validate_policy_shape(
    value: Any,
    *,
    top_level_fields: frozenset[str] = _TOP_LEVEL_FIELDS,
    expected_candidates: tuple[str, ...] = _EXPECTED_CANDIDATES,
    expected_cohorts: Mapping[str, tuple[str, ...]] = _EXPECTED_COHORTS,
    expected_release_families: Mapping[str, tuple[Any, ...]] = _EXPECTED_RELEASE_FAMILIES,
    expected_policy_version: int = 1,
    expected_schema_version: int = 1,
) -> dict[str, Any]:
    root = _expect_object(value, "$", top_level_fields)
    amendment = _expect_object(
        root["amendment"],
        "$.amendment",
        frozenset(
            {
                "nonsemantic_change",
                "prospective_reset",
                "runtime_self_amendment",
                "semantic_change",
            }
        ),
    )
    for field, item in amendment.items():
        _expect_string(item, f"$.amendment.{field}")
    authority = _expect_object(
        root["authority"],
        "$.authority",
        frozenset({"deterministic_controls", "llm_controls", "prohibited_llm_controls"}),
    )
    for field, item in authority.items():
        _validate_string_list(item, f"$.authority.{field}")
    baselines = _validate_records(
        root["baselines"],
        "$.baselines",
        frozenset({"baseline_id", "execution_authority", "rule", "scope"}),
    )
    baseline_ids = tuple(
        _expect_string(item["baseline_id"], f"$.baselines[{index}].baseline_id")
        for index, item in enumerate(baselines)
    )
    if baseline_ids != _EXPECTED_BASELINES:
        raise StrategyPolicyError("$.baselines does not contain the frozen ordered baseline set")
    for index, baseline in enumerate(baselines):
        _expect_bool(baseline["execution_authority"], f"$.baselines[{index}].execution_authority")
        if baseline["execution_authority"] is not False:
            raise StrategyPolicyError("baselines must never have execution authority")
        _expect_string(baseline["rule"], f"$.baselines[{index}].rule")
        _expect_string(baseline["scope"], f"$.baselines[{index}].scope")

    candidates = _validate_records(root["candidates"], "$.candidates", _CANDIDATE_FIELDS)
    candidate_ids = tuple(
        _expect_string(item["candidate_id"], f"$.candidates[{index}].candidate_id")
        for index, item in enumerate(candidates)
    )
    if candidate_ids != expected_candidates:
        raise StrategyPolicyError("$.candidates does not contain the frozen ordered candidate set")
    for index, candidate in enumerate(candidates):
        path = f"$.candidates[{index}]"
        _validate_candidate(
            candidate,
            path,
            expected_cohorts=expected_cohorts,
            expected_release_families=expected_release_families,
        )

    claims = _expect_object(
        root["claims"],
        "$.claims",
        frozenset({"allowed", "forbidden", "separation_rules"}),
    )
    for field, item in claims.items():
        _validate_string_list(item, f"$.claims.{field}")
    gate_a = _expect_object(
        root["gate_a"],
        "$.gate_a",
        frozenset({"entry_behavior_when_unverified", "facts", "overall_status", "owner_issue"}),
    )
    if gate_a["overall_status"] != "UNVERIFIED":
        raise StrategyPolicyError("$.gate_a.overall_status must remain UNVERIFIED")
    if gate_a["entry_behavior_when_unverified"] != "ENTRY_DISABLED":
        raise StrategyPolicyError("unverified Gate A must disable entry")
    _expect_string(gate_a["owner_issue"], "$.gate_a.owner_issue")
    facts = _validate_records(
        gate_a["facts"],
        "$.gate_a.facts",
        frozenset({"fact_id", "status", "value"}),
    )
    if any(fact["status"] != "UNVERIFIED" or fact["value"] is not None for fact in facts):
        raise StrategyPolicyError("every Gate A fact must be UNVERIFIED with a null value")
    fact_ids = tuple(
        _expect_string(fact["fact_id"], f"$.gate_a.facts[{index}].fact_id")
        for index, fact in enumerate(facts)
    )
    if len(fact_ids) != len(set(fact_ids)):
        raise StrategyPolicyError("$.gate_a.facts contains duplicate fact IDs")
    legacy = _expect_object(
        root["legacy_infrastructure"],
        "$.legacy_infrastructure",
        frozenset({"debit_vertical_bridge", "fixed_60_minute_hold", "rule", "status"}),
    )
    for field, item in legacy.items():
        _expect_string(item, f"$.legacy_infrastructure.{field}")
    reasoner = _expect_object(
        root["reasoner"],
        "$.reasoner",
        frozenset(
            {
                "additional_properties",
                "call_policy",
                "citation_requirements",
                "critical_unknown_codes",
                "direction_values",
                "forbidden_fields",
                "hash_bindings",
                "no_tools",
                "output_fields",
                "tolerated_unknown_codes",
            }
        ),
    )
    if reasoner["additional_properties"] is not False or reasoner["no_tools"] is not True:
        raise StrategyPolicyError("reasoner authority must remain fail-closed and tool-free")
    call_policy = _expect_object(
        reasoner["call_policy"],
        "$.reasoner.call_policy",
        frozenset(
            {"hard_timeout_seconds", "max_calls", "max_output_tokens", "retry_count", "temperature"}
        ),
    )
    _expect_int(
        call_policy["hard_timeout_seconds"],
        "$.reasoner.call_policy.hard_timeout_seconds",
        minimum=1,
    )
    _expect_int(call_policy["max_calls"], "$.reasoner.call_policy.max_calls", minimum=1)
    _expect_int(
        call_policy["max_output_tokens"], "$.reasoner.call_policy.max_output_tokens", minimum=1
    )
    _expect_int(call_policy["retry_count"], "$.reasoner.call_policy.retry_count")
    _expect_string(call_policy["temperature"], "$.reasoner.call_policy.temperature")
    if call_policy["max_calls"] != 1 or call_policy["retry_count"] != 0:
        raise StrategyPolicyError("reasoner must use exactly one call with no retry")
    _validate_string_list(reasoner["citation_requirements"], "$.reasoner.citation_requirements")
    directions = _validate_string_list(reasoner["direction_values"], "$.reasoner.direction_values")
    if directions != ("UP", "DOWN", "UNCERTAIN"):
        raise StrategyPolicyError("reasoner direction enum must be UP, DOWN, UNCERTAIN")
    _validate_string_list(reasoner["forbidden_fields"], "$.reasoner.forbidden_fields")
    _validate_string_list(reasoner["hash_bindings"], "$.reasoner.hash_bindings")
    tolerated = _validate_string_list(
        reasoner["tolerated_unknown_codes"], "$.reasoner.tolerated_unknown_codes"
    )
    critical = _validate_string_list(
        reasoner["critical_unknown_codes"], "$.reasoner.critical_unknown_codes"
    )
    if set(tolerated) & set(critical):
        raise StrategyPolicyError("reasoner unknown-code classes must be disjoint")
    output_fields = _validate_records(
        reasoner["output_fields"],
        "$.reasoner.output_fields",
        frozenset({"field_id", "required", "rule", "value_type"}),
    )
    output_field_ids = tuple(
        _expect_string(item["field_id"], f"$.reasoner.output_fields[{index}].field_id")
        for index, item in enumerate(output_fields)
    )
    if output_field_ids != (
        "decision",
        "evidence_ids",
        "contradictions",
        "unknowns",
        "strongest_falsifier",
        "summary",
    ):
        raise StrategyPolicyError("reasoner output fields do not match the frozen schema")
    for index, item in enumerate(output_fields):
        if (
            _expect_bool(item["required"], f"$.reasoner.output_fields[{index}].required")
            is not True
        ):
            raise StrategyPolicyError("every reasoner output field must be required")
        _expect_string(item["rule"], f"$.reasoner.output_fields[{index}].rule")
        _expect_string(item["value_type"], f"$.reasoner.output_fields[{index}].value_type")
    _expect_string(root["policy_id"], "$.policy_id")
    _expect_string(root["schema"], "$.schema")
    if type(root["schema_version"]) is not int or type(root["policy_version"]) is not int:
        raise StrategyPolicyError("policy/schema versions must be integers, not booleans")
    if (
        root["schema_version"] != expected_schema_version
        or root["policy_version"] != expected_policy_version
    ):
        raise StrategyPolicyError("unsupported policy/schema version")
    return root


def _validate_candidate(
    candidate: dict[str, Any],
    path: str,
    *,
    expected_cohorts: Mapping[str, tuple[str, ...]] = _EXPECTED_COHORTS,
    expected_release_families: Mapping[str, tuple[Any, ...]] = _EXPECTED_RELEASE_FAMILIES,
) -> None:
    candidate_id = cast(str, candidate["candidate_id"])
    activation = _expect_object(
        candidate["activation"],
        f"{path}.activation",
        frozenset({"mode", "requires", "status"}),
    )
    _expect_string(activation["mode"], f"{path}.activation.mode")
    _validate_string_list(activation["requires"], f"{path}.activation.requires")
    _expect_string(activation["status"], f"{path}.activation.status")
    clocks = _validate_records(
        candidate["clocks"],
        f"{path}.clocks",
        frozenset(
            {
                "candidate_entry_deadline",
                "clock_id",
                "cohort_id",
                "decision_cutoff",
                "evidence_cutoff",
                "event_rule",
                "observation_end",
                "observation_start",
                "reaction_session_rule",
                "release_family",
                "reasoner_hard_timeout_seconds",
                "timezone",
                "universe_freeze",
            }
        ),
    )
    cohort_ids = tuple(clock["cohort_id"] for clock in clocks)
    if cohort_ids != expected_cohorts[candidate_id]:
        raise StrategyPolicyError(f"{path}.clocks does not contain the frozen cohort order")
    clock_ids: list[str] = []
    for clock_index, clock in enumerate(clocks):
        clock_path = f"{path}.clocks[{clock_index}]"
        for field in (
            "candidate_entry_deadline",
            "clock_id",
            "cohort_id",
            "decision_cutoff",
            "evidence_cutoff",
            "event_rule",
            "observation_end",
            "observation_start",
            "reaction_session_rule",
            "timezone",
            "universe_freeze",
        ):
            _expect_string(clock[field], f"{clock_path}.{field}")
        _expect_int(
            clock["reasoner_hard_timeout_seconds"],
            f"{clock_path}.reasoner_hard_timeout_seconds",
            minimum=1,
        )
        release_family = clock["release_family"]
        if release_family is not None:
            _expect_string(release_family, f"{clock_path}.release_family")
        if release_family != expected_release_families[candidate_id][clock_index]:
            raise StrategyPolicyError(
                f"{clock_path}.release_family does not bind the frozen cohort family"
            )
        clock_ids.append(cast(str, clock["clock_id"]))
    if len(clock_ids) != len(set(clock_ids)):
        raise StrategyPolicyError(f"{path}.clocks contains duplicate clock IDs")
    confirmation = _expect_object(
        candidate["confirmation"],
        f"{path}.confirmation",
        frozenset({"confirmation_feature_id", "method", "states", "thresholds", "veto_rule"}),
    )
    thresholds = _validate_records(
        confirmation["thresholds"],
        f"{path}.confirmation.thresholds",
        frozenset({"threshold_id", "unit", "value"}),
    )
    threshold_ids: list[str] = []
    for threshold_index, threshold in enumerate(thresholds):
        threshold_path = f"{path}.confirmation.thresholds[{threshold_index}]"
        threshold_ids.append(
            _expect_string(threshold["threshold_id"], f"{threshold_path}.threshold_id")
        )
        _expect_string(threshold["unit"], f"{threshold_path}.unit")
        _validate_rule_value(threshold["value"], f"{threshold_path}.value")
    if len(threshold_ids) != len(set(threshold_ids)):
        raise StrategyPolicyError(f"{path}.confirmation.thresholds contains duplicates")
    states = _validate_string_list(confirmation["states"], f"{path}.confirmation.states")
    if states != ("CONTINUE", "REVERSE", "NONE", "NOT_APPLICABLE"):
        raise StrategyPolicyError(f"{path}.confirmation.states is not the frozen state set")
    _expect_string(
        confirmation["confirmation_feature_id"],
        f"{path}.confirmation.confirmation_feature_id",
    )
    _expect_string(confirmation["method"], f"{path}.confirmation.method")
    _expect_string(confirmation["veto_rule"], f"{path}.confirmation.veto_rule")
    data_health = _expect_object(
        candidate["data_health"],
        f"{path}.data_health",
        frozenset({"failure_codes", "required_status", "rules"}),
    )
    _validate_string_list(data_health["failure_codes"], f"{path}.data_health.failure_codes")
    if data_health["required_status"] != "PASS":
        raise StrategyPolicyError(f"{path}.data_health.required_status must be PASS")
    _validate_rule_records(data_health["rules"], f"{path}.data_health.rules")
    evidence = _expect_object(
        candidate["evidence"],
        f"{path}.evidence",
        frozenset({"permitted_source_classes", "required_source_classes", "rules"}),
    )
    _validate_string_list(
        evidence["permitted_source_classes"], f"{path}.evidence.permitted_source_classes"
    )
    _validate_string_list(
        evidence["required_source_classes"], f"{path}.evidence.required_source_classes"
    )
    _validate_string_list(evidence["rules"], f"{path}.evidence.rules")
    _validate_string_list(candidate["exclusions"], f"{path}.exclusions")
    expression = _expect_object(
        candidate["expression_and_exit"],
        f"{path}.expression_and_exit",
        frozenset(
            {
                "expression_candidates",
                "expression_status",
                "exit_status",
                "production_allowed",
                "research_entry_grid",
                "research_exit_grid",
                "risk_status",
            }
        ),
    )
    if (
        expression["production_allowed"] != []
        or expression["expression_status"] != "UNSELECTED"
        or expression["exit_status"] != "UNSELECTED"
        or expression["risk_status"] != "UNSELECTED"
    ):
        raise StrategyPolicyError(f"{path}.expression_and_exit grants premature authority")
    _validate_string_list(
        expression["expression_candidates"], f"{path}.expression_and_exit.expression_candidates"
    )
    _validate_string_list(
        expression["production_allowed"], f"{path}.expression_and_exit.production_allowed"
    )
    _validate_string_list(
        expression["research_entry_grid"], f"{path}.expression_and_exit.research_entry_grid"
    )
    _validate_string_list(
        expression["research_exit_grid"], f"{path}.expression_and_exit.research_exit_grid"
    )
    features = _validate_records(
        candidate["features"],
        f"{path}.features",
        frozenset(
            {
                "definition",
                "feature_id",
                "required",
                "required_if",
                "status_values",
                "unit",
                "value_type",
            }
        ),
    )
    feature_ids = tuple(feature["feature_id"] for feature in features)
    if len(feature_ids) != len(set(feature_ids)):
        raise StrategyPolicyError(f"{path}.features contains duplicate feature IDs")
    for feature_index, feature in enumerate(features):
        feature_path = f"{path}.features[{feature_index}]"
        _expect_string(feature["definition"], f"{feature_path}.definition")
        _expect_string(feature["feature_id"], f"{feature_path}.feature_id")
        _expect_bool(feature["required"], f"{feature_path}.required")
        _expect_string(feature["required_if"], f"{feature_path}.required_if")
        _expect_string(feature["unit"], f"{feature_path}.unit")
        _expect_string(feature["value_type"], f"{feature_path}.value_type")
        _validate_string_list(
            feature["status_values"],
            f"{feature_path}.status_values",
        )
        if tuple(feature["status_values"]) != (
            "PRESENT",
            "UNAVAILABLE",
            "NOT_APPLICABLE",
            "CONFLICTING",
        ):
            raise StrategyPolicyError(f"{feature_path}.status_values is not the frozen status set")
    if confirmation["confirmation_feature_id"] not in feature_ids:
        raise StrategyPolicyError(f"{path}.confirmation feature is absent from the feature set")
    _expect_string(candidate["hypothesis"], f"{path}.hypothesis")
    _validate_partitions(candidate["partitions"], f"{path}.partitions")
    _validate_sample_gates(candidate["sample_gates"], f"{path}.sample_gates")
    universe = _expect_object(
        candidate["universe"],
        f"{path}.universe",
        frozenset({"candidate_retention", "ranking", "rules"}),
    )
    _validate_rule_records(universe["rules"], f"{path}.universe.rules")
    provenance = _expect_object(
        candidate["value_provenance"],
        f"{path}.value_provenance",
        frozenset({"evidence_backed", "owner_selected"}),
    )
    _validate_string_list(provenance["evidence_backed"], f"{path}.value_provenance.evidence_backed")
    _validate_string_list(provenance["owner_selected"], f"{path}.value_provenance.owner_selected")


def _validate_rule_records(value: Any, path: str) -> None:
    records = _validate_records(
        value,
        path,
        frozenset({"operator", "rule_id", "unit", "value"}),
    )
    rule_ids = tuple(record["rule_id"] for record in records)
    if len(rule_ids) != len(set(rule_ids)):
        raise StrategyPolicyError(f"{path} contains duplicate rule IDs")


def _validate_partitions(value: Any, path: str) -> None:
    records = _validate_records(
        value,
        path,
        frozenset({"end_exclusive", "partition_id", "start_inclusive"}),
    )
    partition_ids = tuple(record["partition_id"] for record in records)
    if len(partition_ids) != len(set(partition_ids)):
        raise StrategyPolicyError(f"{path} contains duplicate partition IDs")


def _validate_sample_gates(value: Any, path: str) -> None:
    gates = _expect_object(
        value,
        path,
        frozenset({"bootstrap", "partition_minimums", "promotion_metrics", "qfast"}),
    )
    _expect_object(
        gates["bootstrap"],
        f"{path}.bootstrap",
        frozenset({"confidence_level", "method", "resamples"}),
    )
    _validate_records(
        gates["partition_minimums"],
        f"{path}.partition_minimums",
        frozenset({"cohort_id", "development", "prospective", "untouched", "validation"}),
    )
    _validate_records(
        gates["promotion_metrics"],
        f"{path}.promotion_metrics",
        frozenset({"metric_id", "operator", "scope", "unit", "value"}),
    )
    _expect_object(
        gates["qfast"],
        f"{path}.qfast",
        frozenset({"claim", "maximum_events", "minimum_events", "status_on_pass"}),
    )


def _deep_freeze(value: Any) -> FrozenJson:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return cast(JsonScalar, value)


@dataclass(frozen=True, slots=True)
class StrategyPolicy:
    """Deeply immutable view of the exact packaged strategy policy."""

    data: MappingProxyType
    sha256: str

    @property
    def schema(self) -> str:
        return cast(str, self.data["schema"])

    @property
    def schema_version(self) -> int:
        return cast(int, self.data["schema_version"])

    @property
    def policy_id(self) -> str:
        return cast(str, self.data["policy_id"])

    @property
    def policy_version(self) -> int:
        return cast(int, self.data["policy_version"])

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        candidates = cast(tuple[MappingProxyType, ...], self.data["candidates"])
        return tuple(cast(str, candidate["candidate_id"]) for candidate in candidates)

    @property
    def reasoner_unknown_codes(self) -> tuple[str, ...]:
        reasoner = cast(MappingProxyType, self.data["reasoner"])
        tolerated = cast(tuple[str, ...], reasoner["tolerated_unknown_codes"])
        critical = cast(tuple[str, ...], reasoner["critical_unknown_codes"])
        return tolerated + critical

    def candidate(self, candidate_id: str) -> MappingProxyType:
        candidates = cast(tuple[MappingProxyType, ...], self.data["candidates"])
        for candidate in candidates:
            if candidate["candidate_id"] == candidate_id:
                return candidate
        raise KeyError(candidate_id)

    def cohort_ids(self, candidate_id: str) -> tuple[str, ...]:
        clocks = cast(tuple[MappingProxyType, ...], self.candidate(candidate_id)["clocks"])
        return tuple(cast(str, clock["cohort_id"]) for clock in clocks)

    def features(self, candidate_id: str) -> tuple[MappingProxyType, ...]:
        return cast(tuple[MappingProxyType, ...], self.candidate(candidate_id)["features"])

    def feature_ids(self, candidate_id: str) -> tuple[str, ...]:
        return tuple(cast(str, feature["feature_id"]) for feature in self.features(candidate_id))

    def threshold(self, candidate_id: str, threshold_id: str) -> JsonScalar:
        candidate = self.candidate(candidate_id)
        confirmation = cast(MappingProxyType, candidate["confirmation"])
        thresholds = cast(tuple[MappingProxyType, ...], confirmation["thresholds"])
        matches = [item for item in thresholds if item["threshold_id"] == threshold_id]
        if len(matches) != 1:
            raise KeyError(threshold_id)
        return cast(JsonScalar, matches[0]["value"])


def strategy_policy_bytes() -> bytes:
    """Return the exact packaged canonical policy bytes."""

    package_root = resources.files("esscher.strategy")
    return package_root.joinpath(POLICY_RESOURCE_NAME).read_bytes()


def strategy_policy_sha256() -> str:
    """Verify and return the registered digest of the packaged policy bytes."""

    actual = hashlib.sha256(strategy_policy_bytes()).hexdigest()
    if actual != ACCEPTED_EVENT_POLICY_V1_SHA256:
        raise StrategyPolicyError(
            "accepted strategy policy bytes do not match the immutable registry digest"
        )
    return actual


def parse_strategy_policy(raw: bytes) -> StrategyPolicy:
    """Authenticate, strictly validate, and deeply freeze exact policy bytes."""

    if type(raw) is not bytes:
        raise StrategyPolicyError("accepted strategy policy input must be immutable bytes")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ACCEPTED_EVENT_POLICY_V1_SHA256:
        raise StrategyPolicyError(
            "accepted strategy policy bytes do not match the immutable registry digest"
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise StrategyPolicyError("accepted strategy policy must be valid UTF-8") from error
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except json.JSONDecodeError as error:
        raise StrategyPolicyError("accepted strategy policy is not valid JSON") from error
    validated = _validate_policy_shape(parsed)
    canonical = json.dumps(
        validated,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if raw != canonical:
        raise StrategyPolicyError("accepted strategy policy bytes are not canonical JSON")
    frozen = _deep_freeze(validated)
    if not isinstance(frozen, MappingProxyType):  # pragma: no cover - root is validated above
        raise StrategyPolicyError("accepted strategy policy root must be an object")
    return StrategyPolicy(data=frozen, sha256=digest)


def load_strategy_policy() -> StrategyPolicy:
    """Load the exact packaged accepted policy as a deeply immutable object."""

    return parse_strategy_policy(strategy_policy_bytes())


# V3 is the owner-approved DELAYED-CAPTURE DEMO lane (2026-09-04, MS-Mesh,
# issue #68/#101): the identical frozen 09:30:00-09:35:00 ET opening-reaction
# signal window, features, evidence rules, and validation math as V1, with only
# the capture/decision/entry clocks shifted (evidence 09:51:00, decision
# 09:51:50, entry 10:01:00 ET) because the host's Alpaca Basic data plan serves
# SIP trades/quotes only older than fifteen minutes and backdating retrieved_at
# is forbidden.  Entry lands just after the first armed 10:00 ET scan window so
# wall-clock processing satisfies the risk-truth freshness bound.  V1 and V2
# bytes/digests remain untouched; every V3 artifact carries the
# DELAYED_EXECUTION_DEMO / NOT_THE_VALIDATED_LANE labels, and the demo lane
# makes no claim that the delayed entry preserves the V1 execution profile.
POLICY_V3_RESOURCE_NAME: Final = "policies/accepted_event_policy_v3.json"
# Updated only when the canonical V3 policy bytes are intentionally amended.
ACCEPTED_EVENT_POLICY_V3_SHA256: Final = (
    "c3425c8dc259970966addcff9f21949b8fd71a006443a70392cacc868054ed7b"
)
POLICY_V3_ID: Final = "ESSCHER_ACCEPTED_EVENT_POLICY_V3"
CANDIDATE_V3_EARNINGS: Final = "EARNINGS_RESIDUAL_CONTINUATION_V3"

_TOP_LEVEL_FIELDS_V3 = _TOP_LEVEL_FIELDS | {"delayed_capture_disclosure"}
_EXPECTED_CANDIDATES_V3 = (CANDIDATE_V3_EARNINGS,)
_EXPECTED_COHORTS_V3: Mapping[str, tuple[str, ...]] = {
    CANDIDATE_V3_EARNINGS: ("BMO", "AMC"),
}
_EXPECTED_RELEASE_FAMILIES_V3: Mapping[str, tuple[Any, ...]] = {
    CANDIDATE_V3_EARNINGS: (None, None),
}
_DELAYED_DISCLOSURE_FIELDS = frozenset(
    {"data_plan_constraint", "labels", "reason", "signal_window_unchanged"}
)
_REQUIRED_V3_LABELS = ("DELAYED_EXECUTION_DEMO", "NOT_THE_VALIDATED_LANE")


def _validate_v3_policy_shape(value: Any) -> dict[str, Any]:
    root = _validate_policy_shape(
        value,
        top_level_fields=_TOP_LEVEL_FIELDS_V3,
        expected_candidates=_EXPECTED_CANDIDATES_V3,
        expected_cohorts=_EXPECTED_COHORTS_V3,
        expected_release_families=_EXPECTED_RELEASE_FAMILIES_V3,
        expected_policy_version=3,
        expected_schema_version=3,
    )
    if root["policy_id"] != POLICY_V3_ID:
        raise StrategyPolicyError("$.policy_id must be the V3 delayed-demo policy identity")
    if root["schema"] != "ringdown.accepted_event_strategy_policy":
        raise StrategyPolicyError("$.schema must remain the accepted event policy schema")
    disclosure = _expect_object(
        root["delayed_capture_disclosure"],
        "$.delayed_capture_disclosure",
        _DELAYED_DISCLOSURE_FIELDS,
    )
    for field in ("data_plan_constraint", "reason", "signal_window_unchanged"):
        _expect_string(disclosure[field], f"$.delayed_capture_disclosure.{field}")
    labels = _validate_string_list(disclosure["labels"], "$.delayed_capture_disclosure.labels")
    for required in _REQUIRED_V3_LABELS:
        if required not in labels:
            raise StrategyPolicyError("delayed capture disclosure must carry the demo labels")
    return root


def strategy_policy_v3_bytes() -> bytes:
    """Return the exact packaged canonical V3 delayed-demo policy bytes."""

    package_root = resources.files("esscher.strategy")
    return package_root.joinpath(POLICY_V3_RESOURCE_NAME).read_bytes()


def strategy_policy_v3_sha256() -> str:
    """Verify and return the registered digest of the packaged V3 policy bytes."""

    actual = hashlib.sha256(strategy_policy_v3_bytes()).hexdigest()
    if actual != ACCEPTED_EVENT_POLICY_V3_SHA256:
        raise StrategyPolicyError(
            "V3 delayed-demo policy bytes do not match the immutable registry digest"
        )
    return actual


def parse_strategy_policy_v3(raw: bytes) -> StrategyPolicy:
    """Authenticate, strictly validate, and deeply freeze exact V3 policy bytes."""

    if type(raw) is not bytes:
        raise StrategyPolicyError("V3 strategy policy input must be immutable bytes")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ACCEPTED_EVENT_POLICY_V3_SHA256:
        raise StrategyPolicyError(
            "V3 delayed-demo policy bytes do not match the immutable registry digest"
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise StrategyPolicyError("V3 strategy policy must be valid UTF-8") from error
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except json.JSONDecodeError as error:
        raise StrategyPolicyError("V3 strategy policy is not valid JSON") from error
    validated = _validate_v3_policy_shape(parsed)
    canonical = json.dumps(
        validated,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if raw != canonical:
        raise StrategyPolicyError("V3 strategy policy bytes are not canonical JSON")
    frozen = _deep_freeze(validated)
    if not isinstance(frozen, MappingProxyType):  # pragma: no cover - root validated above
        raise StrategyPolicyError("V3 strategy policy root must be an object")
    return StrategyPolicy(data=frozen, sha256=digest)


def load_strategy_policy_v3() -> StrategyPolicy:
    """Load the exact packaged V3 delayed-demo policy as a deeply immutable object."""

    return parse_strategy_policy_v3(strategy_policy_v3_bytes())


_V2_TOP_LEVEL_FIELDS = frozenset(
    {
        "authority",
        "candidates",
        "claims",
        "policy_id",
        "policy_version",
        "reasoner",
        "schema",
        "schema_version",
    }
)
_V2_CANDIDATE_FIELDS = frozenset(
    {
        "candidate_id",
        "confirmation",
        "critical_unknown_codes",
        "evidence",
        "features",
        "lane",
        "requirements",
    }
)
_V2_EXPECTED_CANDIDATES: Final = (
    "EARNINGS_RESIDUAL_CONTINUATION_V2",
    "MARKET_ANCHOR_INTRADAY_CONTINUATION_V1",
    "LIQUID_STOCK_CATALYST_CONTINUATION_V1",
)
_V2_EXPECTED_LANES: Final = (
    "EARNINGS_RESIDUAL_CONTINUATION",
    "MARKET_ANCHOR_INTRADAY_CONTINUATION",
    "LIQUID_STOCK_CATALYST_CONTINUATION",
)
_V2_OUTPUT_FIELDS: Final = (
    "decision",
    "evidence_ids",
    "contradictions",
    "unknowns",
    "strongest_falsifier",
    "summary",
)


def _validate_v2_policy_shape(value: Any) -> dict[str, Any]:
    """Validate the closed prospective V2 policy without widening V1 parsing."""

    root = _expect_object(value, "$", _V2_TOP_LEVEL_FIELDS)
    if root["schema"] != "esscher.strategy_policy" or root["schema_version"] != 2:
        raise StrategyPolicyError("V2 policy must use esscher.strategy_policy schema version 2")
    if root["policy_id"] != "ESSCHER_AUTONOMOUS_STRATEGY_POLICY_V2" or root["policy_version"] != 2:
        raise StrategyPolicyError("V2 policy identity or version differs from the accepted package")

    authority = _expect_object(
        root["authority"],
        "$.authority",
        frozenset({"deterministic_controls", "llm_controls", "prohibited_llm_controls"}),
    )
    for field, item in authority.items():
        _validate_string_list(item, f"$.authority.{field}")
    if "self_confidence" not in authority["prohibited_llm_controls"]:
        raise StrategyPolicyError("V2 prohibits reasoner self-confidence as an authority field")

    claims = _expect_object(
        root["claims"],
        "$.claims",
        frozenset({"prospective_contest_policy", "validated_alpha_or_profitability"}),
    )
    if (
        _expect_bool(claims["prospective_contest_policy"], "$.claims.prospective_contest_policy")
        is not True
        or _expect_bool(
            claims["validated_alpha_or_profitability"], "$.claims.validated_alpha_or_profitability"
        )
        is not False
    ):
        raise StrategyPolicyError("V2 must remain prospective policy, not validated alpha evidence")

    candidates = _validate_records(root["candidates"], "$.candidates", _V2_CANDIDATE_FIELDS)
    candidate_ids = tuple(
        _expect_string(item["candidate_id"], f"$.candidates[{index}].candidate_id")
        for index, item in enumerate(candidates)
    )
    if candidate_ids != _V2_EXPECTED_CANDIDATES:
        raise StrategyPolicyError(
            "V2 policy does not contain the frozen ordered three-lane candidate set"
        )
    for index, candidate in enumerate(candidates):
        path = f"$.candidates[{index}]"
        if _expect_string(candidate["lane"], f"{path}.lane") != _V2_EXPECTED_LANES[index]:
            raise StrategyPolicyError(f"{path}.lane differs from the approved autonomous lane")
        critical_unknowns = _validate_string_list(
            candidate["critical_unknown_codes"], f"{path}.critical_unknown_codes"
        )
        if not critical_unknowns:
            raise StrategyPolicyError(f"{path}.critical_unknown_codes must be non-empty")
        features = _validate_string_list(candidate["features"], f"{path}.features")
        if not features:
            raise StrategyPolicyError(f"{path}.features must be non-empty")
        evidence = _expect_object(
            candidate["evidence"],
            f"{path}.evidence",
            frozenset({"required_source_classes", "vocabulary"}),
        )
        if not _validate_string_list(
            evidence["required_source_classes"], f"{path}.evidence.required_source_classes"
        ):
            raise StrategyPolicyError(f"{path}.evidence.required_source_classes must be non-empty")
        if not _validate_string_list(evidence["vocabulary"], f"{path}.evidence.vocabulary"):
            raise StrategyPolicyError(f"{path}.evidence.vocabulary must be non-empty")
        confirmation = _expect_object(
            candidate["confirmation"],
            f"{path}.confirmation",
            frozenset({"deterministic_downstream_only", "rule", "validated_alpha_claim"}),
        )
        if (
            _expect_bool(
                confirmation["deterministic_downstream_only"],
                f"{path}.confirmation.deterministic_downstream_only",
            )
            is not True
            or _expect_bool(
                confirmation["validated_alpha_claim"], f"{path}.confirmation.validated_alpha_claim"
            )
            is not False
        ):
            raise StrategyPolicyError(
                f"{path}.confirmation must remain deterministic and unvalidated"
            )
        _expect_string(confirmation["rule"], f"{path}.confirmation.rule")
        requirements = _expect_object(
            candidate["requirements"],
            f"{path}.requirements",
            frozenset(
                {
                    "requires_complete_authorized_benzinga_news",
                    "requires_decision_ready_universe",
                    "symbol_allowlist",
                }
            ),
        )
        allowlist = _validate_string_list(
            requirements["symbol_allowlist"], f"{path}.requirements.symbol_allowlist"
        )
        if allowlist != tuple(sorted(allowlist)):
            raise StrategyPolicyError(f"{path}.requirements.symbol_allowlist must be sorted")
        requires_universe = _expect_bool(
            requirements["requires_decision_ready_universe"],
            f"{path}.requirements.requires_decision_ready_universe",
        )
        requires_news = _expect_bool(
            requirements["requires_complete_authorized_benzinga_news"],
            f"{path}.requirements.requires_complete_authorized_benzinga_news",
        )
        if index == 1 and allowlist != ("QQQ", "SPY"):
            raise StrategyPolicyError("market-anchor V2 lane permits exactly QQQ and SPY")
        if index != 1 and allowlist:
            raise StrategyPolicyError("only the market-anchor V2 lane may have a symbol allowlist")
        if index == 2 and (not requires_universe or not requires_news):
            raise StrategyPolicyError(
                "catalyst V2 lane requires decision-ready universe and authorized news"
            )
        if index != 2 and (requires_universe or requires_news):
            raise StrategyPolicyError(
                "only catalyst V2 lane may require universe or authorized news"
            )

    reasoner = _expect_object(
        root["reasoner"],
        "$.reasoner",
        frozenset(
            {
                "call_policy",
                "critical_unknown_codes",
                "direction_values",
                "forbidden_output_fields",
                "news_text_rule",
                "output_fields",
                "tolerated_unknown_codes",
            }
        ),
    )
    call_policy = _expect_object(
        reasoner["call_policy"],
        "$.reasoner.call_policy",
        frozenset({"hard_timeout_seconds", "max_calls", "max_completion_tokens", "retry_count"}),
    )
    if (
        _expect_int(
            call_policy["hard_timeout_seconds"],
            "$.reasoner.call_policy.hard_timeout_seconds",
            minimum=1,
        )
        != 8
        or _expect_int(call_policy["max_calls"], "$.reasoner.call_policy.max_calls", minimum=1) != 1
        or _expect_int(call_policy["retry_count"], "$.reasoner.call_policy.retry_count") != 0
        or _expect_int(
            call_policy["max_completion_tokens"],
            "$.reasoner.call_policy.max_completion_tokens",
            minimum=1,
        )
        != 512
    ):
        raise StrategyPolicyError(
            "V2 reasoner call policy must remain eight-second, one-call, no-retry, 512-token"
        )
    if _validate_string_list(reasoner["direction_values"], "$.reasoner.direction_values") != (
        "UP",
        "DOWN",
        "UNCERTAIN",
    ):
        raise StrategyPolicyError("V2 reasoner direction enum must be UP, DOWN, UNCERTAIN")
    if (
        _validate_string_list(reasoner["output_fields"], "$.reasoner.output_fields")
        != _V2_OUTPUT_FIELDS
    ):
        raise StrategyPolicyError("V2 reasoner output must use the strict six-field schema")
    forbidden = _validate_string_list(
        reasoner["forbidden_output_fields"], "$.reasoner.forbidden_output_fields"
    )
    if "self_confidence" not in forbidden:
        raise StrategyPolicyError("V2 reasoner output must not contain self-confidence")
    _expect_string(reasoner["news_text_rule"], "$.reasoner.news_text_rule")
    tolerated = _validate_string_list(
        reasoner["tolerated_unknown_codes"], "$.reasoner.tolerated_unknown_codes"
    )
    critical = _validate_string_list(
        reasoner["critical_unknown_codes"], "$.reasoner.critical_unknown_codes"
    )
    if set(tolerated) & set(critical):
        raise StrategyPolicyError("V2 unknown-code classes must be disjoint")
    return root


def strategy_policy_v2_bytes() -> bytes:
    """Return the exact canonical bytes of the separate accepted V2 package."""

    package_root = resources.files("esscher.strategy")
    return package_root.joinpath(POLICY_V2_RESOURCE_NAME).read_bytes()


def strategy_policy_v2_sha256() -> str:
    """Verify and return the immutable V2 package digest."""

    actual = hashlib.sha256(strategy_policy_v2_bytes()).hexdigest()
    if actual != ACCEPTED_EVENT_POLICY_V2_SHA256:
        raise StrategyPolicyError(
            "accepted V2 policy bytes do not match the immutable registry digest"
        )
    return actual


def parse_strategy_policy_v2(raw: bytes) -> StrategyPolicy:
    """Authenticate, strictly validate, and deeply freeze exact V2 bytes."""

    if type(raw) is not bytes:
        raise StrategyPolicyError("accepted V2 policy input must be immutable bytes")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ACCEPTED_EVENT_POLICY_V2_SHA256:
        raise StrategyPolicyError(
            "accepted V2 policy bytes do not match the immutable registry digest"
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise StrategyPolicyError("accepted V2 policy must be valid UTF-8") from error
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except json.JSONDecodeError as error:
        raise StrategyPolicyError("accepted V2 policy is not valid JSON") from error
    validated = _validate_v2_policy_shape(parsed)
    canonical = json.dumps(
        validated,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if raw != canonical:
        raise StrategyPolicyError("accepted V2 policy bytes are not canonical JSON")
    frozen = _deep_freeze(validated)
    if not isinstance(frozen, MappingProxyType):  # pragma: no cover - root is validated above
        raise StrategyPolicyError("accepted V2 policy root must be an object")
    return StrategyPolicy(data=frozen, sha256=digest)


def load_strategy_policy_v2() -> StrategyPolicy:
    """Load the exact immutable autonomous-lane V2 policy package."""

    return parse_strategy_policy_v2(strategy_policy_v2_bytes())
