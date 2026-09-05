from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from esscher.contracts.gate_a import programme_contract_bytes
from esscher.contracts.source_matrix import (
    CONDITIONS,
    MATRIX_ID,
    REQUIRED_CATEGORY_ORDER,
    SCHEMA_ID,
    SOURCE_MATRIX_V1_SHA256,
    MatrixReason,
    MatrixRejected,
    evaluate_capture_rights,
    load_source_matrix,
    parse_source_matrix,
    source_matrix_bytes,
    source_matrix_sha256,
    verify_upstream_bindings,
)
from esscher.strategy.policy import strategy_policy_bytes

MATRIX_PATH = (
    Path(__file__).parent.parent
    / "src"
    / "esscher"
    / "contracts"
    / "policies"
    / "source_matrix_v1.json"
)
REQUIRED_SOURCE_FIELDS = {
    "endpoint",
    "identifiers",
    "publisher_availability_clock",
    "timestamp_precision",
    "revision_policy",
    "depth",
    "adjustment_policy",
    "completeness",
    "entitlement",
    "retention_redistribution",
    "rate_limits",
}
FULL_DEV_CONDITIONS = frozenset(
    {
        "HUMAN_VERIFIED_CAPTURE",
        "PER_RECORD_PRIMARY_PROVENANCE",
        "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
    }
)


def _raw_dict() -> dict:
    return json.loads(source_matrix_bytes().decode("utf-8"))


def _mutated_bytes(mutate) -> bytes:
    payload = _raw_dict()
    mutate(payload)
    return json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")


def _mutate_source(payload: dict, source_id: str, mutate) -> None:
    for source in payload["sources"]:
        if source["source_id"] == source_id:
            mutate(source)
            return
    raise AssertionError(f"source {source_id} not found in fixture")


def test_packaged_matrix_digest_matches_frozen_constant() -> None:
    assert source_matrix_sha256() == SOURCE_MATRIX_V1_SHA256
    assert hashlib.sha256(source_matrix_bytes()).hexdigest() == SOURCE_MATRIX_V1_SHA256


def test_packaged_matrix_file_matches_packaged_bytes() -> None:
    assert MATRIX_PATH.read_bytes() == source_matrix_bytes()


def test_acceptance_matrix_covers_all_nine_required_categories() -> None:
    matrix = load_source_matrix()
    assert matrix.categories == REQUIRED_CATEGORY_ORDER
    covered = {source.category for source in matrix.sources}
    assert covered == set(REQUIRED_CATEGORY_ORDER)


def test_acceptance_every_source_records_all_required_fields() -> None:
    matrix = load_source_matrix()
    for source in matrix.sources:
        for field in REQUIRED_SOURCE_FIELDS:
            value = getattr(source, field)
            assert isinstance(value, str) and value.strip(), (
                f"{source.source_id}.{field} must be a non-empty record"
            )
        assert source.verdict in {"FEASIBLE", "FEASIBLE_WITH_LIMITATIONS", "BLOCKED"}
        assert source.evidence, f"{source.source_id} requires evidence"


def test_acceptance_every_required_source_class_is_covered() -> None:
    matrix = load_source_matrix()
    policy = json.loads(strategy_policy_bytes().decode("utf-8"))
    for candidate in policy["candidates"]:
        for source_class in candidate["evidence"]["required_source_classes"]:
            assert matrix.sources_for_class(source_class), (
                f"required class {source_class} has no matrix source"
            )
    for source_class in ("LICENSED_POINT_IN_TIME_CONSENSUS", "LICENSED_PERMITTED_NEWS"):
        assert matrix.sources_for_class(source_class)


def test_acceptance_rights_ambiguity_is_blocked() -> None:
    matrix = load_source_matrix()
    for source in matrix.sources:
        if source.entitlement == "AMBIGUOUS" or source.retention_redistribution == "AMBIGUOUS":
            assert source.verdict == "BLOCKED", source.source_id


def test_acceptance_no_paid_plan_selected_without_human_approval() -> None:
    matrix = load_source_matrix()
    paid = [source for source in matrix.sources if source.paid_plan_required]
    assert paid, "the matrix must surface the paid-plan sources"
    for source in paid:
        assert source.human_approval is None, source.source_id
        assert source.verdict == "BLOCKED", source.source_id
        assert "HUMAN_APPROVAL_FOR_PAID_PLAN" in source.conditions, source.source_id


def test_acceptance_three_to_five_dev_only_golden_bundles_registered() -> None:
    matrix = load_source_matrix()
    assert 3 <= len(matrix.bundles) <= 5
    bundles_dir = Path(__file__).parent.parent / "data" / "source-feasibility" / "golden-bundles"
    registered = {bundle.lower().replace("_", "-") for bundle in matrix.bundles}
    on_disk = {path.name for path in bundles_dir.iterdir() if path.is_dir()}
    assert registered == on_disk


def test_rights_consistency_rejects_ambiguous_entitlement_with_feasible_verdict() -> None:
    def mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "ALPACA_NEWS_BENZINGA",
            lambda source: source.__setitem__("verdict", "FEASIBLE"),
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(mutate))
    assert error.value.reason == MatrixReason.RIGHTS_AMBIGUOUS


def test_rights_consistency_rejects_unapproved_paid_plan_with_feasible_verdict() -> None:
    def mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "LICENSED_PIT_CONSENSUS_VENDORS",
            lambda source: (
                source.__setitem__("verdict", "FEASIBLE_WITH_LIMITATIONS"),
                source.__setitem__("entitlement", "VERIFIED_LICENSED"),
                source.__setitem__("retention_redistribution", "RETENTION_ONLY_HASH_RECEIPTS"),
            ),
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(mutate))
    assert error.value.reason == MatrixReason.PAID_PLAN_UNAPPROVED


def test_strict_parser_rejects_unknown_fields() -> None:
    def mutate(payload: dict) -> None:
        _mutate_source(
            payload, "BLS_RELEASE_SCHEDULE", lambda source: source.__setitem__("extra", 1)
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(mutate))
    assert error.value.reason == MatrixReason.UNKNOWN_FIELD


def test_strict_parser_rejects_missing_required_fields() -> None:
    def mutate(payload: dict) -> None:
        _mutate_source(payload, "BLS_RELEASE_SCHEDULE", lambda source: source.pop("rate_limits"))

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(mutate))
    assert error.value.reason == MatrixReason.MISSING_FIELD


def test_strict_parser_rejects_duplicate_json_keys() -> None:
    raw = source_matrix_bytes().decode("utf-8")
    tampered = raw.replace(
        '"matrix_id": "ESSCHER_SOURCE_MATRIX_V1"',
        '"matrix_id": "ESSCHER_SOURCE_MATRIX_V1", "matrix_id": "ESSCHER_SOURCE_MATRIX_V1"',
        1,
    )
    assert tampered != raw
    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(tampered.encode("utf-8"))
    assert error.value.reason == MatrixReason.DUPLICATE_FIELD


def test_strict_parser_rejects_direct_alpaca_hosts_in_endpoints() -> None:
    def mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "ALPACA_EQUITY_HISTORICAL_BARS",
            lambda source: source.__setitem__(
                "endpoint", "https://data.alpaca.markets/v2/stocks/bars"
            ),
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(mutate))
    assert error.value.reason == MatrixReason.MALFORMED_VALUE


def test_strict_parser_rejects_unknown_verdict_and_condition() -> None:
    def verdict_mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "BLS_RELEASE_SCHEDULE",
            lambda source: source.__setitem__("verdict", "PROBABLY_FINE"),
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(verdict_mutate))
    assert error.value.reason == MatrixReason.MALFORMED_VALUE

    def condition_mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "BLS_RELEASE_SCHEDULE",
            lambda source: source.__setitem__("conditions", ["SOME_UNKNOWN_CONDITION"]),
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(condition_mutate))
    assert error.value.reason == MatrixReason.MALFORMED_VALUE


def test_strict_parser_rejects_empty_or_invalid_evidence() -> None:
    def empty_mutate(payload: dict) -> None:
        _mutate_source(
            payload, "BLS_RELEASE_SCHEDULE", lambda source: source.__setitem__("evidence", [])
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(empty_mutate))
    assert error.value.reason == MatrixReason.EVIDENCE_MISSING

    def unknown_bundle_mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "BLS_RELEASE_SCHEDULE",
            lambda source: source.__setitem__(
                "evidence",
                [
                    {
                        "kind": "BUNDLE",
                        "reference": "GB9_NOT_REGISTERED",
                        "retrieved_at": None,
                        "content_sha256": None,
                        "quote": None,
                    }
                ],
            ),
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(unknown_bundle_mutate))
    assert error.value.reason == MatrixReason.MALFORMED_VALUE

    def document_without_time_mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "BLS_RELEASE_SCHEDULE",
            lambda source: source.__setitem__(
                "evidence",
                [
                    {
                        "kind": "DOCUMENT",
                        "reference": "https://www.bls.gov/schedule/news_release/jolts.htm",
                        "retrieved_at": None,
                        "content_sha256": None,
                        "quote": None,
                    }
                ],
            ),
        )

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_mutated_bytes(document_without_time_mutate))
    assert error.value.reason == MatrixReason.MISSING_FIELD


def test_upstream_binding_rejects_contract_drift() -> None:
    matrix = load_source_matrix()

    def mutate(payload: dict) -> None:
        payload["policy_sha256"] = "0" * 64

    drifted = parse_source_matrix(_mutated_bytes(mutate))
    with pytest.raises(MatrixRejected) as error:
        verify_upstream_bindings(
            drifted,
            policy_bytes=strategy_policy_bytes(),
            gate_a_contract_bytes=programme_contract_bytes(),
        )
    assert error.value.reason == MatrixReason.UPSTREAM_CONTRACT_DRIFT

    with pytest.raises(MatrixRejected) as error:
        verify_upstream_bindings(
            matrix,
            policy_bytes=strategy_policy_bytes() + b" ",
            gate_a_contract_bytes=programme_contract_bytes(),
        )
    assert error.value.reason == MatrixReason.UPSTREAM_CONTRACT_DRIFT


def test_upstream_binding_accepts_frozen_contracts() -> None:
    matrix = load_source_matrix()
    verify_upstream_bindings(
        matrix,
        policy_bytes=strategy_policy_bytes(),
        gate_a_contract_bytes=programme_contract_bytes(),
    )
    assert matrix.matrix_id == MATRIX_ID
    assert matrix.sha256 == SOURCE_MATRIX_V1_SHA256


def test_capture_rights_pass_with_satisfied_dev_conditions() -> None:
    matrix = load_source_matrix()
    required = (
        "OFFICIAL_EXCHANGE_CALENDAR",
        "POINT_IN_TIME_SECURITY_MASTER",
        "ISSUER_INVESTOR_RELATIONS",
        "LICENSED_SIP_EQUITY_TRADES",
        "CORPORATE_ACTION_RECORD",
    )
    decisions = evaluate_capture_rights(matrix, required, satisfied_conditions=FULL_DEV_CONDITIONS)
    assert tuple(decision.source_class for decision in decisions) == required
    assert all(decision.verdict != "BLOCKED" for decision in decisions)


def test_capture_rights_block_required_class_covered_only_by_blocked_sources() -> None:
    def mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "ALPACA_EQUITY_HISTORICAL_BARS",
            lambda source: (
                source.__setitem__("verdict", "BLOCKED"),
                source.__setitem__("conditions", []),
            ),
        )

    blocked_matrix = parse_source_matrix(_mutated_bytes(mutate))
    with pytest.raises(MatrixRejected) as error:
        evaluate_capture_rights(
            blocked_matrix,
            ("LICENSED_SIP_EQUITY_TRADES",),
            satisfied_conditions=FULL_DEV_CONDITIONS,
        )
    assert error.value.reason == MatrixReason.SOURCE_RIGHTS_BLOCKED


def test_capture_rights_reject_unmet_limitation_conditions() -> None:
    matrix = load_source_matrix()
    with pytest.raises(MatrixRejected) as error:
        evaluate_capture_rights(
            matrix,
            ("OFFICIAL_EXCHANGE_CALENDAR",),
            satisfied_conditions=frozenset(),
        )
    assert error.value.reason == MatrixReason.SOURCE_RIGHTS_LIMITATION_UNMET


def test_capture_rights_reject_uncovered_required_class() -> None:
    def mutate(payload: dict) -> None:
        _mutate_source(
            payload,
            "ALPACA_EQUITY_HISTORICAL_BARS",
            lambda source: source.__setitem__("source_classes", ["UNRELATED_CLASS_ID"]),
        )

    incomplete = parse_source_matrix(_mutated_bytes(mutate))
    with pytest.raises(MatrixRejected) as error:
        evaluate_capture_rights(
            incomplete,
            ("LICENSED_SIP_EQUITY_TRADES",),
            satisfied_conditions=FULL_DEV_CONDITIONS,
        )
    assert error.value.reason == MatrixReason.MATRIX_INCOMPLETE


def test_conditions_registry_is_frozen() -> None:
    assert (
        frozenset(
            {
                "HUMAN_VERIFIED_CAPTURE",
                "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
                "GATE_A_OPTION_ENTITLEMENT_RECEIPT",
                "PER_RECORD_PRIMARY_PROVENANCE",
                "HUMAN_APPROVAL_FOR_PAID_PLAN",
            }
        )
        == CONDITIONS
    )
    assert SCHEMA_ID == "esscher.source_matrix"
