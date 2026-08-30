from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ringdown_market.contracts.gate_a import (
    PROGRAMME_CONTRACT_SHA256,
    EntryState,
    GateAContractError,
    VerificationStatus,
    capability_receipt_bytes,
    evaluate_gate_a,
    load_programme_contract,
    parse_capability_receipt,
    parse_programme_contract,
    programme_contract_bytes,
)

FIXTURE = Path(__file__).parent / "contract_fixtures" / "gate_a_capability_unverified_v1.json"
NOW = datetime(2026, 8, 30, 20, 54, 4, tzinfo=UTC)
CAPABILITY_EVIDENCE = b'{"source":"sanitized-gate-a-observation","version":1}'
CAPABILITY_EVIDENCE_SHA256 = hashlib.sha256(CAPABILITY_EVIDENCE).hexdigest()
PRODUCER_BUILD_SHA256 = "a" * 64
EXPECTED_FACT_IDS = {
    "account_id_submission",
    "account_reset_assumptions",
    "allowed_instruments",
    "base_capital",
    "competition_horizon_and_deadline",
    "dedicated_submission_account",
    "development_account_policy",
    "drawdown_and_flatten_rules",
    "equity_data_entitlement",
    "judging_creativity_originality",
    "judging_pnl_performance",
    "judging_presentation_execution",
    "judging_technology_implementation",
    "judging_weights",
    "leverage_rules",
    "manual_intervention_rules",
    "official_cost_treatment",
    "official_mark_source",
    "option_data_entitlement",
    "option_level_and_multileg_capability",
    "originality_and_license",
    "paper_environment",
    "required_agent_interface",
    "required_trading_api",
    "required_written_evidence",
}
VERIFIED_CAPABILITY_VALUES = {
    "account_reset_state": "FRESH_NOT_RESET",
    "account_status": "ACTIVE",
    "dedicated_account_freshness": "FRESH_DEDICATED_ACCOUNT",
    "equity_market_data_feed": "IEX",
    "multi_leg_order_support": "SUPPORTED",
    "option_market_data_feed": "INDICATIVE",
    "option_trading_level": "3",
    "paper_endpoint_class": "PAPER",
    "required_mcp_tools": (
        "cancel_order_by_id|get_account_info|get_all_positions|"
        "get_order_by_client_id|get_order_by_id|place_option_order"
    ),
    "starting_balance": "100000.00 USD",
}
FORBIDDEN_IMPORTS = (
    "aiohttp",
    "alpaca",
    "httpx",
    "mcp",
    "requests",
    "ringdown_market.execution.host_mcp",
    "ringdown_market.execution.mcp",
    "socket",
    "subprocess",
    "urllib.request",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _verified_capability_payload() -> dict[str, object]:
    payload = json.loads(FIXTURE.read_bytes())
    payload["account_fingerprint_sha256"] = "f" * 64
    for observation in payload["observations"]:
        observation["status"] = "VERIFIED"
        observation["value"] = VERIFIED_CAPABILITY_VALUES[observation["capability_id"]]
        observation["evidence_sha256"] = CAPABILITY_EVIDENCE_SHA256
        observation["limitation"] = None
    return payload


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            keys.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_all_keys(item))
    return keys


def test_packaged_programme_contract_is_canonical_hash_bound_and_source_grounded() -> None:
    raw = programme_contract_bytes()
    payload = json.loads(raw)
    contract = load_programme_contract()

    assert raw == _canonical(payload)
    assert hashlib.sha256(raw).hexdigest() == PROGRAMME_CONTRACT_SHA256
    assert contract.sha256 == PROGRAMME_CONTRACT_SHA256
    assert contract.contract_id == "ESSCHER_GATE_A_PROGRAMME_V1"
    assert contract.retrieved_at == NOW
    assert contract.source_url == (
        "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon"
    )
    assert set(contract.facts_by_id) == EXPECTED_FACT_IDS
    assert contract.claim_labels == (
        "NO_BROKER_MUTATION",
        "NO_CREDENTIALS",
        "PAPER_ONLY",
        "SOURCE_GROUNDED",
    )


def test_verified_organizer_rules_carry_exact_quotes_and_unknowns_carry_limitations() -> None:
    contract = load_programme_contract()

    for fact in contract.facts:
        assert fact.source_url == contract.source_url
        if fact.status is VerificationStatus.VERIFIED:
            assert fact.value is not None
            assert fact.exact_quote is not None
            assert fact.limitation is None
        else:
            assert fact.value is None
            assert fact.limitation is not None

    assert contract.facts_by_id["base_capital"].value == "100000.00 USD"
    assert contract.facts_by_id["competition_horizon_and_deadline"].value == (
        "2026-09-04T15:00:00Z"
    )
    assert contract.facts_by_id["allowed_instruments"].value == "OPTIONS_REQUIRED"
    assert contract.facts_by_id["paper_environment"].value == "PAPER_ONLY"


def test_unverified_capability_fixture_is_canonical_sanitized_and_round_trips() -> None:
    raw = FIXTURE.read_bytes()
    payload = json.loads(raw)
    receipt = parse_capability_receipt(raw)

    assert raw == _canonical(payload)
    assert capability_receipt_bytes(receipt) == raw
    assert receipt.account_fingerprint_sha256 is None
    assert set(_all_keys(payload)).isdisjoint(
        {
            "account_id",
            "account_number",
            "api_key",
            "authorization",
            "credential",
            "password",
            "secret",
            "token",
        }
    )
    assert {item.status for item in receipt.observations} == {VerificationStatus.INACCESSIBLE}


def test_verified_capability_receipt_proves_account_surface_without_raw_identifier() -> None:
    receipt = parse_capability_receipt(_canonical(_verified_capability_payload()))

    assert receipt.account_fingerprint_sha256 == "f" * 64
    assert {
        item.capability_id: item.value for item in receipt.observations
    } == VERIFIED_CAPABILITY_VALUES
    assert all(item.evidence_sha256 == CAPABILITY_EVIDENCE_SHA256 for item in receipt.observations)


def test_verified_capabilities_resolve_delegated_facts_but_not_unpublished_rules() -> None:
    contract = load_programme_contract()
    receipt = parse_capability_receipt(_canonical(_verified_capability_payload()))

    decision = evaluate_gate_a(
        contract,
        receipt,
        evaluated_at=NOW,
        approved_producer_build_sha256=PRODUCER_BUILD_SHA256,
        capability_evidence={CAPABILITY_EVIDENCE_SHA256: CAPABILITY_EVIDENCE},
    )

    assert decision.entry_state is EntryState.ENTRY_DISABLED
    assert decision.reason_codes == (
        "COMPETITION_COST_TREATMENT_UNVERIFIED",
        "COMPETITION_FLATTEN_RULE_UNVERIFIED",
        "COMPETITION_LEVERAGE_UNVERIFIED",
        "COMPETITION_MARK_UNVERIFIED",
    )


def test_unknown_exposure_or_eligibility_facts_disable_entry() -> None:
    contract = load_programme_contract()
    receipt = parse_capability_receipt(FIXTURE.read_bytes())

    decision = evaluate_gate_a(contract, receipt, evaluated_at=NOW)

    assert decision.entry_state is EntryState.ENTRY_DISABLED
    assert decision.reason_codes == (
        "ACCOUNT_CAPABILITY_UNVERIFIED",
        "ACCOUNT_RESET_UNVERIFIED",
        "COMPETITION_COST_TREATMENT_UNVERIFIED",
        "COMPETITION_FLATTEN_RULE_UNVERIFIED",
        "COMPETITION_LEVERAGE_UNVERIFIED",
        "COMPETITION_MARK_UNVERIFIED",
        "DATA_ENTITLEMENT_UNVERIFIED",
        "DEDICATED_ACCOUNT_FRESHNESS_UNVERIFIED",
        "MULTILEG_CAPABILITY_UNVERIFIED",
        "OPTION_LEVEL_UNVERIFIED",
        "PAPER_ENDPOINT_UNVERIFIED",
        "REQUIRED_TOOLS_UNVERIFIED",
        "STARTING_BALANCE_UNVERIFIED",
    )


def test_unsupported_verified_capability_value_is_rejected() -> None:
    payload = _verified_capability_payload()
    payload["observations"][7]["value"] = "LIVE"

    with pytest.raises(GateAContractError, match="unsupported verified value"):
        parse_capability_receipt(_canonical(payload))


def test_stale_receipt_fails_closed() -> None:
    contract = load_programme_contract()
    payload = _verified_capability_payload()
    receipt = parse_capability_receipt(_canonical(payload))

    decision = evaluate_gate_a(
        contract,
        receipt,
        evaluated_at=receipt.expires_at + timedelta(microseconds=1),
        approved_producer_build_sha256=PRODUCER_BUILD_SHA256,
        capability_evidence={CAPABILITY_EVIDENCE_SHA256: CAPABILITY_EVIDENCE},
    )

    assert decision.entry_state is EntryState.ENTRY_DISABLED
    assert "CAPABILITY_RECEIPT_STALE" in decision.reason_codes


def test_verified_receipt_requires_authorized_producer_and_exact_evidence_bytes() -> None:
    contract = load_programme_contract()
    receipt = parse_capability_receipt(_canonical(_verified_capability_payload()))

    unauthorized = evaluate_gate_a(contract, receipt, evaluated_at=NOW)
    wrong_evidence = evaluate_gate_a(
        contract,
        receipt,
        evaluated_at=NOW,
        approved_producer_build_sha256=PRODUCER_BUILD_SHA256,
        capability_evidence={CAPABILITY_EVIDENCE_SHA256: b"wrong"},
    )

    assert "CAPABILITY_PRODUCER_UNAUTHORIZED" in unauthorized.reason_codes
    assert "CAPABILITY_EVIDENCE_UNAVAILABLE" in unauthorized.reason_codes
    assert "CAPABILITY_EVIDENCE_UNAVAILABLE" in wrong_evidence.reason_codes
    assert "CAPABILITY_PRODUCER_UNAUTHORIZED" not in wrong_evidence.reason_codes


def test_future_dated_programme_or_capability_evidence_fails_closed() -> None:
    contract = load_programme_contract()
    receipt = parse_capability_receipt(FIXTURE.read_bytes())

    decision = evaluate_gate_a(
        contract,
        receipt,
        evaluated_at=NOW - timedelta(microseconds=1),
    )

    assert "PROGRAMME_CONTRACT_FROM_FUTURE" in decision.reason_codes
    assert "CAPABILITY_RECEIPT_FROM_FUTURE" in decision.reason_codes


def test_capability_receipt_must_bind_programme_and_verified_account_fingerprint() -> None:
    contract = load_programme_contract()
    payload = _verified_capability_payload()
    payload["account_fingerprint_sha256"] = None

    with pytest.raises(GateAContractError, match="account fingerprint"):
        parse_capability_receipt(_canonical(payload))

    payload = json.loads(FIXTURE.read_bytes())
    payload["programme_contract_sha256"] = "d" * 64
    receipt = parse_capability_receipt(_canonical(payload))
    decision = evaluate_gate_a(contract, receipt, evaluated_at=NOW)
    assert "PROGRAMME_CONTRACT_MISMATCH" in decision.reason_codes


def test_evaluator_reauthenticates_objects_constructed_without_parsers() -> None:
    contract = load_programme_contract()
    receipt = parse_capability_receipt(FIXTURE.read_bytes())

    forged_contract = replace(contract, source_url="https://example.invalid")
    forged_receipt = replace(receipt, adapter="UNPINNED_ADAPTER")

    contract_result = evaluate_gate_a(forged_contract, receipt, evaluated_at=NOW)
    receipt_result = evaluate_gate_a(contract, forged_receipt, evaluated_at=NOW)

    assert "PROGRAMME_CONTRACT_MISMATCH" in contract_result.reason_codes
    assert receipt_result.reason_codes == ("CAPABILITY_RECEIPT_INVALID",)
    assert receipt_result.entry_state is EntryState.ENTRY_DISABLED


@pytest.mark.parametrize(
    "value",
    ["not bytes", bytearray(b"{}"), memoryview(b"{}"), None],
)
def test_parsers_require_immutable_bytes(value: object) -> None:
    with pytest.raises(GateAContractError):
        parse_programme_contract(value)  # type: ignore[arg-type]
    with pytest.raises(GateAContractError):
        parse_capability_receipt(value)  # type: ignore[arg-type]


def test_programme_parser_rejects_unknown_duplicate_noncanonical_and_mutated_bytes() -> None:
    payload = json.loads(programme_contract_bytes())
    payload["unexpected"] = True
    with pytest.raises(GateAContractError, match="unknown field"):
        parse_programme_contract(_canonical(payload))

    duplicate = b'{"schema":"duplicate",' + programme_contract_bytes()[1:]
    with pytest.raises(GateAContractError, match="duplicate field"):
        parse_programme_contract(duplicate)

    pretty = json.dumps(json.loads(programme_contract_bytes()), indent=2).encode()
    with pytest.raises(GateAContractError, match="canonical JSON"):
        parse_programme_contract(pretty)

    payload = json.loads(programme_contract_bytes())
    payload["contract_id"] = "ESSCHER_GATE_A_PROGRAMME_MUTATED"
    with pytest.raises(GateAContractError, match="registered digest"):
        parse_programme_contract(_canonical(payload))


def test_capability_parser_rejects_unknown_nonfinite_and_raw_account_fields() -> None:
    payload = json.loads(FIXTURE.read_bytes())
    payload["account_id"] = "must-not-enter-contract"
    with pytest.raises(GateAContractError, match="unknown field"):
        parse_capability_receipt(_canonical(payload))

    raw = FIXTURE.read_bytes().replace(b'"schema_version":1', b'"schema_version":NaN')
    with pytest.raises(GateAContractError, match="non-finite"):
        parse_capability_receipt(raw)

    payload = json.loads(FIXTURE.read_bytes())
    payload["observations"][0]["limitation"] = "token=must-not-enter-receipt"
    with pytest.raises(GateAContractError, match="forbidden secret"):
        parse_capability_receipt(_canonical(payload))


@pytest.mark.parametrize("target", ["programme", "capability"])
def test_boolean_schema_versions_are_rejected(target: str) -> None:
    if target == "programme":
        payload = json.loads(programme_contract_bytes())
        payload["schema_version"] = True
        parser = parse_programme_contract
    else:
        payload = json.loads(FIXTURE.read_bytes())
        payload["schema_version"] = True
        parser = parse_capability_receipt

    with pytest.raises(GateAContractError, match="schema/version"):
        parser(_canonical(payload))


def test_gate_a_module_has_no_network_broker_or_mutation_dependency() -> None:
    path = Path(__file__).parents[1] / "src" / "ringdown_market" / "contracts" / "gate_a.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)

    assert not any(module.startswith(prefix) for module in imported for prefix in FORBIDDEN_IMPORTS)
