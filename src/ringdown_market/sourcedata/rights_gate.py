"""Capture-boundary source-rights gate for the snapshot collector.

The gate consumes the frozen source matrix from issue #41 and fails closed
before any capture when a required source class is BLOCKED, when a required
limitation condition is unmet, or when the matrix no longer binds the exact
accepted event policy and Gate A contract bytes. The gate grants no authority:
it only narrows which captures may proceed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from ringdown_market.contracts.gate_a import (
    PROGRAMME_CONTRACT_SHA256,
    programme_contract_bytes,
)
from ringdown_market.contracts.source_matrix import (
    ClassRightsDecision,
    MatrixReason,
    MatrixRejected,
    SourceMatrix,
    load_source_matrix,
    parse_source_matrix,
    verify_upstream_bindings,
)
from ringdown_market.contracts.source_matrix import (
    evaluate_capture_rights as evaluate_matrix_rights,
)
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.strategy.policy import (
    ACCEPTED_EVENT_POLICY_V1_SHA256,
    parse_strategy_policy,
    strategy_policy_bytes,
)

EARNINGS_CANDIDATE_ID: Final = "EARNINGS_RESIDUAL_CONTINUATION_V1"


@dataclass(frozen=True)
class CaptureRightsReport:
    source_matrix_sha256: str
    decisions: tuple[ClassRightsDecision, ...]


def _map_matrix_error(error: MatrixRejected) -> CollectorRejected:
    if error.reason == MatrixReason.SOURCE_RIGHTS_BLOCKED:
        return CollectorRejected(CollectorReason.SOURCE_RIGHTS_BLOCKED, error.path, error.detail)
    if error.reason == MatrixReason.SOURCE_RIGHTS_LIMITATION_UNMET:
        return CollectorRejected(
            CollectorReason.SOURCE_RIGHTS_LIMITATION_UNMET, error.path, error.detail
        )
    return CollectorRejected(CollectorReason.SOURCE_MATRIX_DRIFT, error.path, error.detail)


def _required_classes_from_policy(policy_bytes: bytes) -> tuple[str, ...]:
    policy = parse_strategy_policy(policy_bytes)
    candidate = policy.candidate(EARNINGS_CANDIDATE_ID)
    evidence = candidate["evidence"]
    required = evidence["required_source_classes"]
    assert isinstance(required, tuple)
    return required


def evaluate_capture_rights(
    *,
    matrix_bytes: bytes | None = None,
    satisfied_conditions: frozenset[str],
) -> CaptureRightsReport:
    """Evaluate the frozen source matrix against the frozen earnings lane.

    ``matrix_bytes=None`` loads the authenticated packaged matrix. Supplied
    bytes are parsed with the same strictness and must bind the identical
    upstream contract digests. Every failure fails closed.
    """

    policy_bytes = strategy_policy_bytes()
    if hashlib.sha256(policy_bytes).hexdigest() != ACCEPTED_EVENT_POLICY_V1_SHA256:
        raise CollectorRejected(
            CollectorReason.SOURCE_MATRIX_DRIFT,
            "policy_sha256",
            "packaged accepted event policy digest drift",
        )
    gate_a_bytes = programme_contract_bytes()
    if hashlib.sha256(gate_a_bytes).hexdigest() != PROGRAMME_CONTRACT_SHA256:
        raise CollectorRejected(
            CollectorReason.SOURCE_MATRIX_DRIFT,
            "gate_a_contract_sha256",
            "packaged Gate A contract digest drift",
        )
    try:
        if matrix_bytes is None:
            matrix: SourceMatrix = load_source_matrix()
        else:
            matrix = parse_source_matrix(matrix_bytes)
            verify_upstream_bindings(
                matrix, policy_bytes=policy_bytes, gate_a_contract_bytes=gate_a_bytes
            )
        required = _required_classes_from_policy(policy_bytes)
        decisions = evaluate_matrix_rights(
            matrix, required, satisfied_conditions=satisfied_conditions
        )
    except MatrixRejected as error:
        raise _map_matrix_error(error) from error
    return CaptureRightsReport(source_matrix_sha256=matrix.sha256, decisions=decisions)
