from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from esscher.contracts.research_to_permit import (
    ALPACA_MCP_PROTOCOL_SHA256,
    PAPER_PERMIT_POLICY_SHA256,
    PAPER_PERMIT_POLICY_VERSION,
    PAPER_PERMIT_TTL_SECONDS,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    DecisionPermitRejected,
    PermitRejectionReason,
    map_frozen_decision_to_permit,
)
from esscher.execution.mcp import build_open_order_call
from esscher.execution.models import (
    DataClass,
    OptionSide,
    PositionIntent,
    RunMode,
    debit_vertical_permit_bytes,
)

FIXTURE_PATH = Path(__file__).parent / "contract_fixtures" / "frozen_research_decision_v1.json"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def contract_parts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    decision = deepcopy(fixture["decision_template"])
    decision.update(
        {
            "protocol_sha256": RESEARCH_DECISION_PROTOCOL_SHA256,
            "policy_version": PAPER_PERMIT_POLICY_VERSION,
            "policy_sha256": PAPER_PERMIT_POLICY_SHA256,
        }
    )
    return decision, deepcopy(fixture["evidence_manifest"]), deepcopy(fixture["input_snapshot"])


def render_contract(
    decision: dict[str, Any],
    evidence: dict[str, Any],
    inputs: dict[str, Any],
    *,
    pretty_decision: bool = False,
) -> tuple[bytes, bytes, bytes]:
    evidence_bytes = canonical_bytes(evidence)
    input_bytes = canonical_bytes(inputs)
    decision["evidence_manifest_sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
    decision["input_snapshot_sha256"] = hashlib.sha256(input_bytes).hexdigest()
    decision_bytes = (
        json.dumps(decision, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if pretty_decision
        else canonical_bytes(decision)
    )
    return decision_bytes, evidence_bytes, input_bytes


def accepted_contract_bytes(*, pretty_decision: bool = False) -> tuple[bytes, bytes, bytes]:
    return render_contract(*contract_parts(), pretty_decision=pretty_decision)


def map_contract(contract: tuple[bytes, bytes, bytes]):
    decision_bytes, evidence_bytes, input_bytes = contract
    return map_frozen_decision_to_permit(
        decision_bytes,
        evidence_manifest_bytes=evidence_bytes,
        input_snapshot_bytes=input_bytes,
        policy_version=PAPER_PERMIT_POLICY_VERSION,
    )


def assert_rejected(
    contract: tuple[bytes, bytes, bytes],
    reason: PermitRejectionReason,
    *,
    policy_version: str = PAPER_PERMIT_POLICY_VERSION,
) -> DecisionPermitRejected:
    decision_bytes, evidence_bytes, input_bytes = contract
    with pytest.raises(DecisionPermitRejected) as caught:
        map_frozen_decision_to_permit(
            decision_bytes,
            evidence_manifest_bytes=evidence_bytes,
            input_snapshot_bytes=input_bytes,
            policy_version=policy_version,
        )
    assert caught.value.reason is reason
    return caught.value


def test_maps_identical_frozen_bytes_and_policy_to_identical_immutable_paper_permit() -> None:
    contract = accepted_contract_bytes()
    decision_bytes, evidence_bytes, input_bytes = contract

    first = map_contract(contract)
    second = map_contract(contract)

    assert first == second
    assert debit_vertical_permit_bytes(first) == debit_vertical_permit_bytes(second)
    assert first.permit_id == second.permit_id
    assert first.permit_id.startswith("rd-permit-")
    assert first.decision_sha256 == hashlib.sha256(decision_bytes).hexdigest()
    assert first.evidence_sha256 == hashlib.sha256(evidence_bytes).hexdigest()
    assert first.snapshot_sha256 == hashlib.sha256(input_bytes).hexdigest()
    assert first.protocol_sha256 == RESEARCH_DECISION_PROTOCOL_SHA256
    assert first.execution_protocol_sha256 == ALPACA_MCP_PROTOCOL_SHA256
    assert first.protocol_sha256 != first.execution_protocol_sha256
    assert first.policy_sha256 == PAPER_PERMIT_POLICY_SHA256
    assert first.run_mode is RunMode.PAPER
    assert first.data_class is DataClass.INDICATIVE_DATA
    assert first.quantity == 1
    assert first.maximum_loss == 125
    assert first.expires_at.timestamp() - first.issued_at.timestamp() == PAPER_PERMIT_TTL_SECONDS
    assert [leg.side for leg in first.legs] == [OptionSide.BUY, OptionSide.SELL]
    assert [leg.position_intent for leg in first.legs] == [
        PositionIntent.BUY_TO_OPEN,
        PositionIntent.SELL_TO_OPEN,
    ]
    with pytest.raises(FrozenInstanceError):
        first.permit_id = "changed"  # type: ignore[misc]


def test_exact_decision_bytes_not_only_parsed_values_bind_permit_identity() -> None:
    canonical = map_contract(accepted_contract_bytes())
    pretty = map_contract(accepted_contract_bytes(pretty_decision=True))

    assert canonical != pretty
    assert canonical.decision_sha256 != pretty.decision_sha256
    assert canonical.permit_id != pretty.permit_id
    assert debit_vertical_permit_bytes(canonical) != debit_vertical_permit_bytes(pretty)


def test_permit_bytes_expose_every_required_identity_and_no_live_mode() -> None:
    permit = map_contract(accepted_contract_bytes())

    payload = json.loads(debit_vertical_permit_bytes(permit))

    assert payload["schema"] == "ringdown.paper_execution_permit"
    assert payload["schema_version"] == 1
    assert payload["decision_sha256"] == permit.decision_sha256
    assert payload["evidence_sha256"] == permit.evidence_sha256
    assert payload["input_snapshot_sha256"] == permit.snapshot_sha256
    assert payload["protocol_sha256"] == RESEARCH_DECISION_PROTOCOL_SHA256
    assert payload["execution_protocol_sha256"] == ALPACA_MCP_PROTOCOL_SHA256
    assert payload["policy_sha256"] == PAPER_PERMIT_POLICY_SHA256
    assert payload["run_mode"] == "PAPER"
    assert "LIVE" not in debit_vertical_permit_bytes(permit).decode("utf-8")


def test_bridge_signature_has_no_broker_market_data_position_or_credential_dependency() -> None:
    parameters = set(inspect.signature(map_frozen_decision_to_permit).parameters)

    assert parameters == {
        "decision_bytes",
        "evidence_manifest_bytes",
        "input_snapshot_bytes",
        "policy_version",
    }


def test_rejects_evidence_record_published_after_decision_cutoff() -> None:
    decision, evidence, inputs = contract_parts()
    evidence["records"][1]["published_at"] = "2026-08-28T13:36:00Z"

    caught = assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.POINT_IN_TIME_VIOLATION,
    )

    assert caught.path.endswith("published_at")


@pytest.mark.parametrize("field", ["accepted_at", "retrieved_at", "source_observed_at"])
def test_rejects_evidence_availability_or_collection_after_cutoff(field: str) -> None:
    decision, evidence, inputs = contract_parts()
    evidence["records"][1][field] = "2026-08-28T13:35:01Z"

    caught = assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.POINT_IN_TIME_VIOLATION,
    )

    assert caught.path.endswith(field)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latest_evidence_at", "2026-08-28T13:35:01Z"),
        ("feature_snapshot_at", "2026-08-28T13:35:01Z"),
    ],
)
def test_rejects_post_cutoff_decision_inputs(field: str, value: str) -> None:
    decision, evidence, inputs = contract_parts()
    decision[field] = value

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.POINT_IN_TIME_VIOLATION,
    )


@pytest.mark.parametrize(
    ("decision_state", "direction"),
    [("ABSTAIN", "UNCERTAIN"), ("APPROVED", "UNCERTAIN")],
)
def test_abstention_or_uncertain_direction_cannot_produce_permit(
    decision_state: str,
    direction: str,
) -> None:
    decision, evidence, inputs = contract_parts()
    decision["decision_state"] = decision_state
    decision["direction"] = direction

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.INELIGIBLE_DECISION,
    )


@pytest.mark.parametrize("status", ["REJECTED", "INSUFFICIENT_DATA"])
def test_qfast_rejection_or_insufficient_data_cannot_produce_permit(status: str) -> None:
    decision, evidence, inputs = contract_parts()
    decision["qfast_status"] = status

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.QFAST_REJECTED,
    )


@pytest.mark.parametrize("status", ["SHADOW_ONLY", "INSUFFICIENT_DATA"])
def test_qlatency_failure_cannot_produce_permit(status: str) -> None:
    decision, evidence, inputs = contract_parts()
    decision["qlatency_status"] = status

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.QLATENCY_REJECTED,
    )


@pytest.mark.parametrize("eligibility", ["UNAVAILABLE", "UNRESOLVED"])
def test_ineligible_research_state_cannot_produce_permit(eligibility: str) -> None:
    decision, evidence, inputs = contract_parts()
    decision["eligibility"] = eligibility

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.INELIGIBLE_DECISION,
    )


def test_unknown_decision_state_fails_closed() -> None:
    decision, evidence, inputs = contract_parts()
    decision["decision_state"] = "EXECUTE_ANYWAY"

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.UNKNOWN_STATE,
    )


@pytest.mark.parametrize("artifact", ["evidence", "input"])
def test_exact_artifact_hash_mismatch_fails_closed(artifact: str) -> None:
    decision_bytes, evidence_bytes, input_bytes = accepted_contract_bytes()
    if artifact == "evidence":
        evidence_bytes += b"\n"
    else:
        input_bytes += b"\n"

    assert_rejected(
        (decision_bytes, evidence_bytes, input_bytes),
        PermitRejectionReason.HASH_MISMATCH,
    )


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("protocol_sha256", PermitRejectionReason.PROTOCOL_MISMATCH),
        ("policy_sha256", PermitRejectionReason.POLICY_MISMATCH),
    ],
)
def test_claimed_protocol_and_policy_hashes_must_match_immutable_registry(
    field: str,
    reason: PermitRejectionReason,
) -> None:
    decision, evidence, inputs = contract_parts()
    decision[field] = "0" * 64

    assert_rejected(render_contract(decision, evidence, inputs), reason)


def test_unknown_policy_version_has_no_mutable_fallback() -> None:
    contract = accepted_contract_bytes()

    assert_rejected(
        contract,
        PermitRejectionReason.POLICY_MISMATCH,
        policy_version="paper-debit-vertical/latest",
    )


@pytest.mark.parametrize("document", ["decision", "evidence", "input", "strategy", "feature"])
def test_unknown_fields_are_rejected_at_every_contract_layer(document: str) -> None:
    decision, evidence, inputs = contract_parts()
    if document == "decision":
        decision["post_cutoff_outcome"] = "WIN"
    elif document == "evidence":
        evidence["mutable_fallback"] = True
    elif document == "input":
        inputs["market_data"] = {"read_at_runtime": True}
    elif document == "strategy":
        decision["strategy"]["run_mode"] = "LIVE"
    else:
        inputs["features"][0]["future_value"] = "1.0"

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.UNKNOWN_FIELD,
    )


def test_duplicate_json_fields_are_rejected() -> None:
    decision_bytes, evidence_bytes, input_bytes = accepted_contract_bytes()
    duplicate = decision_bytes[:-1] + b',"schema_version":1}'

    assert_rejected(
        (duplicate, evidence_bytes, input_bytes),
        PermitRejectionReason.DUPLICATE_FIELD,
    )


def test_mutable_bytearray_contract_is_rejected() -> None:
    decision_bytes, evidence_bytes, input_bytes = accepted_contract_bytes()

    with pytest.raises(DecisionPermitRejected) as caught:
        map_frozen_decision_to_permit(  # type: ignore[arg-type]
            bytearray(decision_bytes),
            evidence_manifest_bytes=evidence_bytes,
            input_snapshot_bytes=input_bytes,
            policy_version=PAPER_PERMIT_POLICY_VERSION,
        )

    assert caught.value.reason is PermitRejectionReason.INVALID_DOCUMENT


@pytest.mark.parametrize("artifact", ["evidence", "input"])
def test_all_artifact_inputs_must_be_immutable_bytes(artifact: str) -> None:
    decision_bytes, evidence_bytes, input_bytes = accepted_contract_bytes()
    if artifact == "evidence":
        evidence_bytes = bytearray(evidence_bytes)  # type: ignore[assignment]
    else:
        input_bytes = "not bytes"  # type: ignore[assignment]

    with pytest.raises(DecisionPermitRejected) as caught:
        map_frozen_decision_to_permit(
            decision_bytes,
            evidence_manifest_bytes=evidence_bytes,  # type: ignore[arg-type]
            input_snapshot_bytes=input_bytes,  # type: ignore[arg-type]
            policy_version=PAPER_PERMIT_POLICY_VERSION,
        )

    assert caught.value.reason is PermitRejectionReason.INVALID_DOCUMENT


def test_empty_evidence_manifest_fails_with_explicit_missing_evidence_reason() -> None:
    decision, evidence, inputs = contract_parts()
    evidence["records"] = []

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.MISSING_EVIDENCE,
    )


def test_data_only_evidence_manifest_v2_cannot_generate_a_permit() -> None:
    decision, evidence, inputs = contract_parts()
    evidence["schema_version"] = 2

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.UNSUPPORTED_SCHEMA,
    )


def test_missing_publication_instant_fails_with_explicit_missing_evidence_reason() -> None:
    decision, evidence, inputs = contract_parts()
    evidence["records"][0]["published_at"] = None

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.MISSING_EVIDENCE,
    )


def test_stale_evidence_freeze_cannot_be_reused_for_new_decision() -> None:
    decision, evidence, inputs = contract_parts()
    evidence["frozen_at"] = "2026-08-28T13:34:59Z"

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.STALE_EVIDENCE,
    )


def test_generated_or_unregistered_evidence_source_kind_fails_closed() -> None:
    decision, evidence, inputs = contract_parts()
    evidence["records"][0]["source_kind"] = "GENERATED"

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.UNKNOWN_STATE,
    )


def test_unregistered_publication_timestamp_type_fails_closed() -> None:
    decision, evidence, inputs = contract_parts()
    evidence["records"][0]["published_at_type"] = "retrieval_time"

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.UNKNOWN_STATE,
    )


def test_synthetic_contract_class_cannot_be_promoted_to_execution_permit() -> None:
    decision, evidence, inputs = contract_parts()
    decision["data_class"] = "SYNTHETIC_CONTRACT_FIXTURE"

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.CLAIM_BOUNDARY_MISMATCH,
    )


def test_unknown_feature_source_reference_fails_closed() -> None:
    decision, evidence, inputs = contract_parts()
    inputs["features"][0]["source_refs"] = ["missing-record"]

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.MISSING_EVIDENCE,
    )


def test_feature_source_max_must_equal_exact_referenced_evidence_time() -> None:
    decision, evidence, inputs = contract_parts()
    inputs["features"][0]["source_max_public_at"] = "2026-08-28T13:29:59Z"

    assert_rejected(
        render_contract(decision, evidence, inputs),
        PermitRejectionReason.PROVENANCE_MISMATCH,
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"kind": "SINGLE_LEG"}, PermitRejectionReason.UNSUPPORTED_STRATEGY),
        ({"vertical_type": "BEAR_PUT"}, PermitRejectionReason.UNSUPPORTED_STRATEGY),
        ({"quantity": 2}, PermitRejectionReason.UNSUPPORTED_STRATEGY),
        ({"limit_price": "5.01"}, PermitRejectionReason.RISK_LIMIT_EXCEEDED),
    ],
)
def test_unsupported_shape_direction_or_paper_risk_fails_closed(
    mutation: dict[str, object],
    reason: PermitRejectionReason,
) -> None:
    decision, evidence, inputs = contract_parts()
    decision["strategy"].update(mutation)

    assert_rejected(render_contract(decision, evidence, inputs), reason)


@pytest.mark.parametrize(
    "field",
    ["decision_sha256", "evidence_sha256", "protocol_sha256", "execution_protocol_sha256"],
)
def test_official_mcp_compilation_rejects_unregistered_permit_identity(field: str) -> None:
    permit = map_contract(accepted_contract_bytes())
    changed = replace(permit, **{field: "0" * 64})

    with pytest.raises(ValueError, match="frozen decision permit"):
        build_open_order_call(changed)


def test_mapped_permit_cannot_be_reconstructed_with_an_unregistered_authorization() -> None:
    permit = map_contract(accepted_contract_bytes())

    with pytest.raises(ValueError, match="research decision bridge"):
        replace(permit, _bridge_authorization=object())


def test_official_mcp_client_identity_is_bound_to_decision_provenance() -> None:
    permit = map_contract(accepted_contract_bytes())
    changed = replace(permit, decision_sha256="0" * 64)

    first = build_open_order_call(permit)
    with pytest.raises(ValueError, match="frozen decision permit"):
        build_open_order_call(changed)
    assert first.tool == "place_option_order"


@pytest.mark.parametrize(
    "changed",
    [
        {"limit_price": Decimal("1.30")},
        {"expires_at_delta": timedelta(seconds=1)},
    ],
)
def test_official_mcp_rejects_permit_terms_mutated_after_frozen_mapping(
    changed: dict[str, object],
) -> None:
    permit = map_contract(accepted_contract_bytes())
    if "expires_at_delta" in changed:
        mutated = replace(permit, expires_at=permit.expires_at + changed["expires_at_delta"])
    else:
        mutated = replace(permit, **changed)

    with pytest.raises(ValueError, match="frozen decision permit"):
        build_open_order_call(mutated)
