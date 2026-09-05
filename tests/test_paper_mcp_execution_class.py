"""Issue #90: explicit PAPER_MCP execution class admission and fail-closed separation."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from esscher.runtime.autonomous import ReconciliationRequest, ReconciliationStatus
from esscher.runtime.autonomous_host import (
    AUTONOMOUS_HOST_CLAIMS,
    AUTONOMOUS_HOST_PAPER_MCP_CLAIM_BASIS,
    AUTONOMOUS_HOST_PAPER_MCP_CLAIMS,
    AUTONOMOUS_HOST_PAPER_MCP_DATA_CLASS,
    AutonomousHostBackendRejected,
    AutonomousHostDisposition,
    AutonomousHostPlan,
    AutonomousHostReceipt,
    AutonomousHostRejected,
    HostCandidateOutcome,
    HostExecutionClass,
    HostLifecycleOutcome,
    HostReconciliationAdapter,
    HostReconciliationObservation,
    PaperMcpBrokerTruth,
    SyntheticBrokerTruth,
    _canonical_json,
    _receipt_payload,
    _sha256,
    _validate_plan,
    broker_truth_record_sha256,
    paper_mcp_broker_truth_bytes,
    paper_mcp_broker_truth_sha256,
    synthetic_broker_truth_bytes,
    synthetic_broker_truth_sha256,
)

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)
BINDING = "b1" * 32


class SyntheticBackends:
    """Plain backends without any production binding."""

    def __init__(self, truth: SyntheticBrokerTruth | PaperMcpBrokerTruth) -> None:
        self.truth = truth
        self.reconciliation_calls: list[ReconciliationRequest] = []

    def observe_reconciliation(self, request: ReconciliationRequest):
        self.reconciliation_calls.append(request)
        return HostReconciliationObservation.complete(request, broker_truth=self.truth)

    def observe_due_window(self, request):
        return ()

    def process_candidate(self, request) -> HostCandidateOutcome:
        raise NotImplementedError

    def close_lifecycle(self, request) -> HostLifecycleOutcome:
        raise NotImplementedError


class ProductionBackends(SyntheticBackends):
    """Backends carrying the package production binding digest."""

    def __init__(
        self, truth: SyntheticBrokerTruth | PaperMcpBrokerTruth, binding: str = BINDING
    ) -> None:
        super().__init__(truth)
        self.production_binding_sha256 = binding


def _request(phase: str = "CHECKPOINT") -> ReconciliationRequest:
    return ReconciliationRequest(
        session_id="SESSION-1",
        arm_sha256="a1" * 32,
        account_fingerprint_sha256="a2" * 32,
        execution_protocol_sha256="a3" * 32,
        observed_at=NOW,
        phase=phase,
        active_lifecycle_ids=(),
    )


def _truth(cls, request: ReconciliationRequest):
    return cls.for_request(
        request,
        account_state_sha256="c1" * 32,
        orders_state_sha256="c2" * 32,
        positions_state_sha256="c3" * 32,
        open_order_count=0,
        open_position_count=0,
        is_flat=True,
    )


def _plan(backends, execution_class=HostExecutionClass.SYNTHETIC_FAKE) -> AutonomousHostPlan:
    return AutonomousHostPlan(
        execution_class=execution_class,
        reconciliation_backend=backends,
        collector_backend=backends,
        candidate_backend=backends,
        lifecycle_backend=backends,
    )


def test_execution_classes_are_explicit_and_disjoint() -> None:
    assert HostExecutionClass.SYNTHETIC_FAKE.value == "SYNTHETIC_FAKE"
    assert HostExecutionClass.PAPER_MCP.value == "PAPER_MCP"
    assert len(HostExecutionClass) == 2


def test_synthetic_plan_without_production_bindings_is_admitted() -> None:
    request = _request()
    backends = SyntheticBackends(_truth(SyntheticBrokerTruth, request))

    assert _validate_plan(_plan(backends)) is not None


def test_synthetic_plan_cannot_smuggle_a_production_backend() -> None:
    request = _request()
    production = ProductionBackends(_truth(SyntheticBrokerTruth, request))
    plan = _plan(SyntheticBackends(_truth(SyntheticBrokerTruth, request)))
    smuggled = replace(plan, candidate_backend=production)

    with pytest.raises(AutonomousHostRejected, match="cannot carry production"):
        _validate_plan(smuggled)


def test_paper_mcp_plan_requires_the_identical_production_binding_on_every_backend() -> None:
    request = _request()
    truth = _truth(PaperMcpBrokerTruth, request)
    backends = ProductionBackends(truth)

    assert _validate_plan(_plan(backends, HostExecutionClass.PAPER_MCP)) is not None

    divergent = ProductionBackends(truth, binding="d1" * 32)
    mixed = replace(_plan(backends, HostExecutionClass.PAPER_MCP), lifecycle_backend=divergent)
    with pytest.raises(AutonomousHostRejected, match="identical production binding"):
        _validate_plan(mixed)


def test_paper_mcp_plan_rejects_unbound_and_invalid_backends() -> None:
    request = _request()
    truth = _truth(PaperMcpBrokerTruth, request)
    unbound = replace(
        _plan(ProductionBackends(truth), HostExecutionClass.PAPER_MCP),
        collector_backend=SyntheticBackends(truth),
    )
    with pytest.raises(AutonomousHostRejected, match="package production composition"):
        _validate_plan(unbound)

    class InvalidBinding(ProductionBackends):
        def __init__(self) -> None:
            super().__init__(truth)
            self.production_binding_sha256 = "not-a-digest"

    with pytest.raises(AutonomousHostRejected, match="binding digest is invalid"):
        _validate_plan(_plan(InvalidBinding(), HostExecutionClass.PAPER_MCP))


def test_unknown_execution_class_fails_closed() -> None:
    request = _request()
    backends = SyntheticBackends(_truth(SyntheticBrokerTruth, request))
    forged = replace(_plan(backends), execution_class="LIVE_MONEY")

    with pytest.raises(AutonomousHostRejected, match="unknown execution class"):
        _validate_plan(forged)


def test_truth_twins_are_structurally_equal_but_cryptographically_separated() -> None:
    request = _request()
    synthetic = _truth(SyntheticBrokerTruth, request)
    production = _truth(PaperMcpBrokerTruth, request)

    synthetic_payload = json.loads(synthetic_broker_truth_bytes(synthetic))
    production_payload = json.loads(paper_mcp_broker_truth_bytes(production))

    assert synthetic_payload["schema"] == "esscher.synthetic_broker_truth"
    assert production_payload["schema"] == "esscher.paper_mcp_broker_truth"
    assert synthetic_payload != production_payload
    assert synthetic_broker_truth_sha256(synthetic) != paper_mcp_broker_truth_sha256(production)


def test_broker_truth_dispatch_rejects_foreign_types() -> None:
    request = _request()
    synthetic = _truth(SyntheticBrokerTruth, request)
    production = _truth(PaperMcpBrokerTruth, request)

    assert broker_truth_record_sha256(synthetic) == synthetic_broker_truth_sha256(synthetic)
    assert broker_truth_record_sha256(production) == paper_mcp_broker_truth_sha256(production)
    with pytest.raises(AutonomousHostRejected, match="unsupported"):
        broker_truth_record_sha256(object())


def test_production_truth_keeps_the_flatness_invariants() -> None:
    request = _request()
    contradictory = PaperMcpBrokerTruth.for_request(
        request,
        account_state_sha256="c1" * 32,
        orders_state_sha256="c2" * 32,
        positions_state_sha256="c3" * 32,
        open_order_count=1,
        open_position_count=0,
        is_flat=True,
    )

    with pytest.raises(AutonomousHostRejected, match="flatness disagrees"):
        paper_mcp_broker_truth_sha256(contradictory)


def test_reconciliation_adapter_enforces_the_truth_class_of_its_plan() -> None:
    request = _request()
    synthetic_truth = _truth(SyntheticBrokerTruth, request)
    production_truth = _truth(PaperMcpBrokerTruth, request)

    production_adapter = HostReconciliationAdapter(
        SyntheticBackends(synthetic_truth), execution_class=HostExecutionClass.PAPER_MCP
    )
    with pytest.raises(AutonomousHostBackendRejected) as smuggled:
        production_adapter.reconcile_with_truth(request)
    assert smuggled.value.reason_code == "PORT_OUTPUT_INVALID"

    synthetic_adapter = HostReconciliationAdapter(ProductionBackends(production_truth))
    with pytest.raises(AutonomousHostBackendRejected) as forged:
        synthetic_adapter.reconcile_with_truth(request)
    assert forged.value.reason_code == "PORT_OUTPUT_INVALID"

    matched = HostReconciliationAdapter(
        ProductionBackends(production_truth), execution_class=HostExecutionClass.PAPER_MCP
    )
    receipt, truth_sha = matched.reconcile_with_truth(request)
    assert receipt.status is ReconciliationStatus.COMPLETE
    assert truth_sha == paper_mcp_broker_truth_sha256(production_truth)


def _receipt(execution_class: HostExecutionClass, **overrides: object) -> AutonomousHostReceipt:
    fields: dict[str, object] = {
        "release_sha256": "11" * 32,
        "arm_record_sha256": "12" * 32,
        "session_arm_sha256": "13" * 32,
        "runtime_build_artifact_sha256": "14" * 32,
        "runtime_code_revision": "0123456789abcdef0123456789abcdef01234567",
        "account_capability_id": "capability-1",
        "account_fingerprint_sha256": "15" * 32,
        "execution_class": execution_class,
        "session_id": "SESSION-1",
        "disposition": AutonomousHostDisposition.INCOMPLETE,
        "observed_at": NOW,
        "requested_timeline_count": 1,
        "processed_opportunity_ids": (),
        "disposition_counts": {},
        "active_lifecycle_ids": (),
        "manual_reasons": (),
        "final_summary_sha256": None,
        "reconciliation_phase": "CHECKPOINT",
        "reconciliation_broker_truth_sha256": None,
        "terminal_flat_proven": False,
        "receipt_sha256": "0" * 64,
    }
    fields.update(overrides)
    return AutonomousHostReceipt(**fields)  # type: ignore[arg-type]


def test_receipt_payloads_carry_class_specific_claims_and_data_classes() -> None:
    synthetic_payload = _receipt_payload(_receipt(HostExecutionClass.SYNTHETIC_FAKE))
    assert tuple(synthetic_payload["claims"]) == AUTONOMOUS_HOST_CLAIMS
    assert synthetic_payload["claim_basis"] == "HOST_PLAN_ATTESTATION"
    assert synthetic_payload["data_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert synthetic_payload["execution_class"] == "SYNTHETIC_FAKE"

    production_payload = _receipt_payload(_receipt(HostExecutionClass.PAPER_MCP))
    assert tuple(production_payload["claims"]) == AUTONOMOUS_HOST_PAPER_MCP_CLAIMS
    assert production_payload["claim_basis"] == AUTONOMOUS_HOST_PAPER_MCP_CLAIM_BASIS
    assert production_payload["data_class"] == AUTONOMOUS_HOST_PAPER_MCP_DATA_CLASS
    assert production_payload["execution_class"] == "PAPER_MCP"
    assert production_payload["run_mode"] == "PAPER"
    assert "SYNTHETIC_FAKE" not in production_payload["claims"]


def test_paper_mcp_receipt_self_hash_roundtrips() -> None:
    draft = _receipt(HostExecutionClass.PAPER_MCP)
    sealed = replace(draft, receipt_sha256=_sha256(_canonical_json(_receipt_payload(draft))))

    payload = json.loads(sealed.to_json_bytes())
    assert payload["execution_class"] == "PAPER_MCP"
    assert payload["receipt_sha256"] == sealed.receipt_sha256


def test_production_manual_reason_vocabulary_is_admitted() -> None:
    receipt = _receipt(
        HostExecutionClass.PAPER_MCP,
        disposition=AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED,
        manual_reasons=("BLOCKED_RETRY_BUDGET_EXHAUSTED", "PREFLIGHT_NOT_PASSED"),
    )

    payload = _receipt_payload(receipt)
    assert payload["manual_reasons"] == [
        "BLOCKED_RETRY_BUDGET_EXHAUSTED",
        "PREFLIGHT_NOT_PASSED",
    ]
