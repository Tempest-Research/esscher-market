from __future__ import annotations

import json

import pytest

from ringdown_market.contracts.reasoner_route import (
    ApprovalState,
    RouteContractReason,
    RouteContractRejected,
    load_approved_reasoner_route,
    packaged_route_approval_bytes,
    packaged_route_descriptor_bytes,
    validate_reasoner_route,
)


def _descriptor() -> dict:
    return json.loads(packaged_route_descriptor_bytes())


def _receipt() -> dict:
    return json.loads(packaged_route_approval_bytes())


def _bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def test_packaged_route_validates_as_pending_and_ineligible() -> None:
    route = load_approved_reasoner_route()

    assert route.provider == "dashscope"
    assert route.model == "kimi-k3"
    assert route.approval_state is ApprovalState.PENDING
    assert route.evaluation_eligible is False


def test_approved_receipt_with_named_approver_becomes_eligible() -> None:
    receipt = _receipt()
    receipt["approval_state"] = "APPROVED"
    receipt["approver"] = "bbeennyy860-cyber"
    receipt["approved_at"] = "2026-09-01T12:00:00Z"

    route = validate_reasoner_route(packaged_route_descriptor_bytes(), _bytes(receipt))

    assert route.approval_state is ApprovalState.APPROVED
    assert route.evaluation_eligible is True


def test_approved_receipt_without_approver_fails_closed() -> None:
    receipt = _receipt()
    receipt["approval_state"] = "APPROVED"
    receipt["approver"] = None
    receipt["approved_at"] = None

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(packaged_route_descriptor_bytes(), _bytes(receipt))

    assert caught.value.reason is RouteContractReason.APPROVAL_MISSING


def test_revoked_route_never_authorizes_evaluation() -> None:
    receipt = _receipt()
    receipt["approval_state"] = "REVOKED"

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(packaged_route_descriptor_bytes(), _bytes(receipt))

    assert caught.value.reason is RouteContractReason.APPROVAL_REVOKED


def test_receipt_not_bound_to_descriptor_fails_closed() -> None:
    descriptor = _descriptor()
    descriptor["model"] = "a-different-model"

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.HASH_MISMATCH


def test_receipt_identity_drift_fails_closed() -> None:
    receipt = _receipt()
    receipt["provider"] = "another-provider"

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(packaged_route_descriptor_bytes(), _bytes(receipt))

    assert caught.value.reason is RouteContractReason.IDENTITY_MISMATCH


def test_descriptor_policy_drift_fails_closed() -> None:
    descriptor = _descriptor()
    descriptor["policy_sha256"] = "0" * 64

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.POLICY_MISMATCH


def test_descriptor_call_policy_drift_fails_closed() -> None:
    descriptor = _descriptor()
    descriptor["call_policy"]["retry_count"] = 1

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.POLICY_MISMATCH


def test_descriptor_broker_authority_fails_closed() -> None:
    descriptor = _descriptor()
    descriptor["authority"]["broker"] = True

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.AUTHORITY_VIOLATION


def test_descriptor_account_authority_fails_closed() -> None:
    descriptor = _descriptor()
    descriptor["authority"]["account"] = True

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.AUTHORITY_VIOLATION


def test_descriptor_missing_field_fails_closed() -> None:
    descriptor = _descriptor()
    del descriptor["cost_ceiling"]

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.MISSING_FIELD


def test_receipt_missing_field_fails_closed() -> None:
    receipt = _receipt()
    del receipt["scope"]

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(packaged_route_descriptor_bytes(), _bytes(receipt))

    assert caught.value.reason is RouteContractReason.MISSING_FIELD


def test_descriptor_unknown_field_fails_closed() -> None:
    descriptor = _descriptor()
    descriptor["extra_field"] = "x"

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.UNKNOWN_FIELD


def test_descriptor_secret_argument_fails_closed() -> None:
    descriptor = _descriptor()
    descriptor["application_arguments"] = {"api_key": "not-allowed"}

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.SECRET_ARGUMENT


def test_descriptor_paid_purchase_fails_closed() -> None:
    descriptor = _descriptor()
    descriptor["cost_ceiling"]["paid_provider_purchase"] = True

    with pytest.raises(RouteContractRejected) as caught:
        validate_reasoner_route(_bytes(descriptor), packaged_route_approval_bytes())

    assert caught.value.reason is RouteContractReason.AUTHORITY_VIOLATION
