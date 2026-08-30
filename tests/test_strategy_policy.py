from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from importlib.resources import files
from pathlib import Path

import pytest

from ringdown_market.strategy import (
    load_strategy_policy,
    parse_strategy_policy,
    reasoner_policy_hashes,
    strategy_policy_bytes,
    strategy_policy_sha256,
)

ROOT = Path(__file__).parents[1]
POLICY_RESOURCE = "policies/accepted_event_policy_v1.json"
SYNTHETIC_BUNDLE_PATH = (
    ROOT / "tests" / "contract_fixtures" / "synthetic_strategy_development_v1.json"
)
EXPECTED_POLICY_SHA256 = "3234017de2fec6c33dce20508f483d649d4614130e76cdc6f57af8185e05d05e"
EXPECTED_REASONER_POLICY_HASHES = {
    "EARNINGS_RESIDUAL_CONTINUATION_V1": (
        "2270b06ce31d7f93034fd9d2f5fca6c44333599bf2045f7df6d5ec73acfb1e50",
        "d689e9c1a49bfbc02896164b0faa731bf61fd8aa9eb3d6436532cf5b488d555e",
        "08dd5302e8e03e01a7012acb59048329516e6a801f8b24827066f43430c04fa4",
    ),
    "MACRO_SPY_CONTINUATION_CHALLENGER_V1": (
        "6313a2f84e0b52c84eb7300cc7c0dbb246f95705bff8ec77bcac72edd4766def",
        "c2c02adc169766db6fd14319d0e7a9650d27a32e32640fd7aea9c46166c000a3",
        "08dd5302e8e03e01a7012acb59048329516e6a801f8b24827066f43430c04fa4",
    ),
}
EXPECTED_TOP_LEVEL_FIELDS = {
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
FORBIDDEN_IMPORT_PREFIXES = (
    "aiohttp",
    "alpaca",
    "http",
    "httpx",
    "mcp",
    "requests",
    "ringdown_market.contracts.execution_policy",
    "ringdown_market.execution",
    "ringdown_market.runtime",
    "socket",
    "subprocess",
    "urllib",
)
EARNINGS_CANDIDATE = "EARNINGS_RESIDUAL_CONTINUATION_V1"
MACRO_CANDIDATE = "MACRO_SPY_CONTINUATION_CHALLENGER_V1"
EXPECTED_CANDIDATE_IDS = (EARNINGS_CANDIDATE, MACRO_CANDIDATE)
EXPECTED_COHORT_IDS = {
    EARNINGS_CANDIDATE: ("BMO", "AMC"),
    MACRO_CANDIDATE: ("BLS_JOLTS", "BLS_EMPLOYMENT_SITUATION"),
}
EXPECTED_BASELINE_IDS = (
    "CASH_ALWAYS_UNCERTAIN",
    "PRICE_CONTINUATION",
    "PRICE_REVERSAL",
    "DETERMINISTIC_PARSER",
    "BOUNDED_LLM",
    "NO_TEXT_ABLATION",
    "OPPOSITE_LLM_PLACEBO",
    "SEEDED_RANDOM_PLACEBO_256",
)
EXPECTED_PARTITION_IDS = {
    EARNINGS_CANDIDATE: (
        "DEVELOPMENT_2020_2023",
        "VALIDATION_2024",
        "UNTOUCHED_2025_2026H1",
        "PROSPECTIVE_POST_FREEZE",
    ),
    MACRO_CANDIDATE: (
        "DEVELOPMENT_2016_2021",
        "VALIDATION_2022_2023",
        "UNTOUCHED_2024_2026H1",
        "PROSPECTIVE_POST_FREEZE",
    ),
}
EXPECTED_GATE_A_FACT_IDS = {
    "official_scoring_objective",
    "base_capital",
    "competition_horizon_and_deadline",
    "official_mark_source",
    "official_cost_treatment",
    "allowed_instruments",
    "leverage_rules",
    "drawdown_flatten_and_intervention_rules",
    "dedicated_paper_account_state",
    "equity_and_option_data_entitlements",
    "option_level_and_atomic_multileg_paper_capability",
    "account_reset_and_broker_state_assumptions",
}
EXPECTED_CLOCK_FIELDS = {
    "BMO": {
        "clock_id": "EARNINGS_BMO_CLOCK_V1",
        "observation_start": "09:30:00",
        "observation_end": "09:35:00",
        "evidence_cutoff": "09:35:15",
        "decision_cutoff": "09:36:05",
        "candidate_entry_deadline": "09:37:00",
    },
    "AMC": {
        "clock_id": "EARNINGS_AMC_CLOCK_V1",
        "observation_start": "09:30:00",
        "observation_end": "09:35:00",
        "evidence_cutoff": "09:35:15",
        "decision_cutoff": "09:36:05",
        "candidate_entry_deadline": "09:37:00",
    },
    "BLS_JOLTS": {
        "clock_id": "BLS_JOLTS_CLOCK_V1",
        "observation_start": "10:00:00",
        "observation_end": "10:15:00",
        "evidence_cutoff": "10:15:15",
        "decision_cutoff": "10:16:05",
        "candidate_entry_deadline": "10:17:00",
    },
    "BLS_EMPLOYMENT_SITUATION": {
        "clock_id": "BLS_EMPLOYMENT_SITUATION_CLOCK_V1",
        "observation_start": "09:30:00",
        "observation_end": "09:45:00",
        "evidence_cutoff": "09:45:15",
        "decision_cutoff": "09:46:05",
        "candidate_entry_deadline": "09:47:00",
    },
}
EXPECTED_PROHIBITED_LLM_CONTROLS = {
    "symbol_eligibility",
    "instrument",
    "contract",
    "strike",
    "expiry",
    "quantity",
    "price",
    "risk",
    "entry",
    "exit",
    "account",
    "broker_tool",
    "policy_or_prompt_mutation",
}
FORBIDDEN_EXECUTION_PARAMETER_KEYS = {
    "account",
    "account_id",
    "broker",
    "contract",
    "contracts",
    "debit",
    "dte",
    "expiry",
    "hold_seconds",
    "instrument",
    "limit_price",
    "maximum_loss_usd",
    "order",
    "package_type",
    "permit",
    "quantity",
    "risk_percentage",
    "strike",
    "width",
}


def _resource_bytes() -> bytes:
    return files("ringdown_market.strategy").joinpath(POLICY_RESOURCE).read_bytes()


def _payload() -> dict[str, object]:
    value = json.loads(strategy_policy_bytes())
    assert isinstance(value, dict)
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _values_for_key(value: object, key: str) -> tuple[object, ...]:
    found: list[object] = []
    if isinstance(value, Mapping):
        if key in value:
            found.append(value[key])
        for item in value.values():
            found.extend(_values_for_key(item, key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_values_for_key(item, key))
    return tuple(found)


def _mapping_records(value: object) -> tuple[Mapping[str, object], ...]:
    records: list[Mapping[str, object]] = []
    if isinstance(value, Mapping):
        records.append(value)
        for item in value.values():
            records.extend(_mapping_records(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            records.extend(_mapping_records(item))
    return tuple(records)


def _all_keys(value: object) -> set[str]:
    result: set[str] = set()
    for record in _mapping_records(value):
        result.update(record)
    return result


def _assert_deeply_immutable(value: object, *, path: str = "policy") -> None:
    if is_dataclass(value) and not isinstance(value, type):
        params = type(value).__dataclass_params__
        assert params.frozen, f"{path} dataclass must be frozen"
        for field in fields(value):
            _assert_deeply_immutable(getattr(value, field.name), path=f"{path}.{field.name}")
        return
    if isinstance(value, Mapping):
        assert not isinstance(value, dict), f"{path} exposes a mutable dict"
        for key, item in value.items():
            _assert_deeply_immutable(key, path=f"{path}.<key>")
            _assert_deeply_immutable(item, path=f"{path}.{key}")
        return
    if isinstance(value, (tuple, frozenset)):
        for index, item in enumerate(value):
            _assert_deeply_immutable(item, path=f"{path}[{index}]")
        return
    assert not isinstance(value, (bytearray, list, set)), f"{path} is mutable"


def test_packaged_policy_bytes_are_canonical_and_hash_bound() -> None:
    raw = strategy_policy_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    assert raw == _resource_bytes()
    assert raw == _canonical_bytes(json.loads(raw))
    assert not raw.endswith(b"\n")
    assert digest == EXPECTED_POLICY_SHA256
    assert digest == strategy_policy_sha256()
    assert load_strategy_policy().sha256 == digest


def test_synthetic_development_bundle_has_no_evidence_or_execution_claim() -> None:
    fixture = json.loads(SYNTHETIC_BUNDLE_PATH.read_bytes())

    assert fixture["schema"] == "esscher.synthetic_strategy_development_bundle"
    assert fixture["schema_version"] == 1
    assert fixture["data_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert fixture["claim_labels"] == [
        "NOT_ALPHA_EVIDENCE",
        "NOT_HISTORICAL_DATA",
        "NO_BROKER_EXECUTION",
        "NO_EXECUTION_AUTHORITY",
    ]


def test_policy_has_the_exact_frozen_top_level_shape() -> None:
    assert set(_payload()) == EXPECTED_TOP_LEVEL_FIELDS


def test_reasoner_policy_identities_match_the_frozen_registry() -> None:
    assert {
        candidate_id: reasoner_policy_hashes(candidate_id)
        for candidate_id in EXPECTED_CANDIDATE_IDS
    } == EXPECTED_REASONER_POLICY_HASHES


def test_loader_and_strict_parser_return_the_same_deeply_immutable_policy() -> None:
    loaded = load_strategy_policy()
    parsed = parse_strategy_policy(strategy_policy_bytes())

    assert parsed == loaded
    assert parsed.data == loaded.data
    _assert_deeply_immutable(loaded)


def test_policy_freezes_exact_candidates_cohorts_and_separate_clocks() -> None:
    policy = load_strategy_policy()

    assert policy.candidate_ids == EXPECTED_CANDIDATE_IDS
    for candidate_id, cohort_ids in EXPECTED_COHORT_IDS.items():
        assert policy.cohort_ids(candidate_id) == cohort_ids

    clock_records = [
        record
        for candidate_id in EXPECTED_CANDIDATE_IDS
        for record in _mapping_records(policy.candidate(candidate_id)["clocks"])
        if "cohort_id" in record and "clock_id" in record
    ]
    clocks_by_cohort = {
        cohort_id: {
            record["clock_id"] for record in clock_records if record["cohort_id"] == cohort_id
        }
        for cohort_ids in EXPECTED_COHORT_IDS.values()
        for cohort_id in cohort_ids
    }

    assert all(clocks_by_cohort.values())
    assert clocks_by_cohort["BMO"].isdisjoint(clocks_by_cohort["AMC"])
    assert clocks_by_cohort["BLS_JOLTS"].isdisjoint(clocks_by_cohort["BLS_EMPLOYMENT_SITUATION"])
    assert (
        set()
        .union(clocks_by_cohort["BMO"], clocks_by_cohort["AMC"])
        .isdisjoint(
            set().union(
                clocks_by_cohort["BLS_JOLTS"],
                clocks_by_cohort["BLS_EMPLOYMENT_SITUATION"],
            )
        )
    )


def test_gate_a_is_explicitly_unverified_and_blocks_assumptions() -> None:
    gate_a = load_strategy_policy().data["gate_a"]
    assert gate_a["overall_status"] == "UNVERIFIED"
    facts = gate_a["facts"]
    assert facts
    assert {fact["status"] for fact in facts} == {"UNVERIFIED"}


def test_policy_freezes_the_exact_baseline_families() -> None:
    baselines = load_strategy_policy().data["baselines"]

    assert set(_values_for_key(baselines, "baseline_id")) == set(EXPECTED_BASELINE_IDS)


def test_candidates_keep_distinct_chronological_partitions() -> None:
    policy = load_strategy_policy()

    for candidate_id, expected_ids in EXPECTED_PARTITION_IDS.items():
        partitions = policy.candidate(candidate_id)["partitions"]
        assert set(_values_for_key(partitions, "partition_id")) == set(expected_ids)


def test_expression_exit_and_risk_are_unselected_without_execution_constants() -> None:
    policy = load_strategy_policy()

    for candidate_id in EXPECTED_CANDIDATE_IDS:
        expression = policy.candidate(candidate_id)["expression_and_exit"]
        assert expression["production_allowed"] == ()
        assert expression["expression_status"] == "UNSELECTED"
        assert expression["exit_status"] == "UNSELECTED"
        assert expression["risk_status"] == "UNSELECTED"
        assert _all_keys(expression).isdisjoint(FORBIDDEN_EXECUTION_PARAMETER_KEYS)


@pytest.mark.parametrize("value", ["not bytes", bytearray(b"{}"), memoryview(b"{}"), None])
def test_policy_parser_requires_exact_immutable_bytes(value: object) -> None:
    with pytest.raises(ValueError):
        parse_strategy_policy(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff\xfe\x00",
        b"[]",
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
        b'{"schema_version":-Infinity}',
    ],
)
def test_policy_parser_rejects_invalid_documents_and_nonfinite_numbers(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_strategy_policy(raw)


def test_policy_parser_rejects_duplicate_fields() -> None:
    raw = strategy_policy_bytes()
    duplicate = b'{"schema":"duplicate",' + raw[1:]

    with pytest.raises(ValueError):
        parse_strategy_policy(duplicate)


def test_policy_parser_rejects_unknown_and_missing_fields() -> None:
    payload = _payload()
    payload["unexpected_authority"] = "BROKER"
    unknown = _canonical_bytes(payload)
    del payload["unexpected_authority"]
    del payload["schema"]
    missing = _canonical_bytes(payload)

    with pytest.raises(ValueError):
        parse_strategy_policy(unknown)
    with pytest.raises(ValueError):
        parse_strategy_policy(missing)


def test_policy_parser_rejects_nested_unknown_missing_and_boolean_version() -> None:
    payload = _payload()
    candidate = payload["candidates"][0]
    candidate["clocks"][0]["unexpected_clock_field"] = "09:31"
    nested_unknown = _canonical_bytes(payload)
    del candidate["clocks"][0]["unexpected_clock_field"]
    del candidate["clocks"][0]["evidence_cutoff"]
    nested_missing = _canonical_bytes(payload)
    payload = _payload()
    payload["schema_version"] = True
    boolean_version = _canonical_bytes(payload)

    for raw in (nested_unknown, nested_missing, boolean_version):
        with pytest.raises(ValueError):
            parse_strategy_policy(raw)


def test_policy_parser_rejects_noncanonical_equivalent_bytes() -> None:
    pretty = json.dumps(_payload(), indent=2, ensure_ascii=False).encode("utf-8")

    assert json.loads(pretty) == json.loads(strategy_policy_bytes())
    with pytest.raises(ValueError):
        parse_strategy_policy(pretty)


def test_policy_parser_rejects_post_freeze_mutation() -> None:
    payload = _payload()
    payload["policy_id"] = f"{payload['policy_id']}-mutated"

    with pytest.raises(ValueError):
        parse_strategy_policy(_canonical_bytes(payload))


def test_strategy_package_has_no_execution_runtime_network_or_broker_imports() -> None:
    strategy_root = ROOT / "src" / "ringdown_market" / "strategy"
    violations: list[str] = []
    for path in sorted(strategy_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)
            for module in imported:
                if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    violations.append(f"{path.relative_to(ROOT)} imports {module}")

    assert violations == []
