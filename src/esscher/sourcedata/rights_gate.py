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

from esscher.contracts.gate_a import (
    PROGRAMME_CONTRACT_SHA256,
    programme_contract_bytes,
)
from esscher.contracts.source_matrix import (
    SOURCE_MATRIX_V1_SHA256,
    ClassRightsDecision,
    MatrixReason,
    MatrixRejected,
    SourceMatrix,
    load_source_matrix,
    parse_source_matrix,
    verify_upstream_bindings,
)
from esscher.contracts.source_matrix import (
    evaluate_capture_rights as evaluate_matrix_rights,
)
from esscher.sourcedata.reasons import CollectorReason, CollectorRejected
from esscher.strategy.policy import (
    ACCEPTED_EVENT_POLICY_V1_SHA256,
    parse_strategy_policy,
    strategy_policy_bytes,
)

EARNINGS_CANDIDATE_ID: Final = "EARNINGS_RESIDUAL_CONTINUATION_V1"
MACRO_CANDIDATE_ID: Final = "MACRO_SPY_CONTINUATION_CHALLENGER_V1"


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


def _required_classes_from_policy(policy_bytes: bytes, *, candidate_id: str) -> tuple[str, ...]:
    policy = parse_strategy_policy(policy_bytes)
    try:
        candidate = policy.candidate(candidate_id)
    except KeyError as error:
        raise CollectorRejected(
            CollectorReason.SOURCE_MATRIX_DRIFT,
            "candidate_id",
            f"candidate '{candidate_id}' is not present in the accepted policy",
        ) from error
    evidence = candidate["evidence"]
    required = evidence["required_source_classes"]
    assert isinstance(required, tuple)
    return required


def evaluate_capture_rights(
    *,
    candidate_id: str,
    matrix_bytes: bytes | None = None,
    satisfied_conditions: frozenset[str],
) -> CaptureRightsReport:
    """Evaluate the frozen source matrix against one exact accepted candidate.

    ``matrix_bytes=None`` loads the authenticated packaged matrix. The optional
    bytes seam exists only for deterministic tests and accepts the exact frozen
    matrix digest; no alternate matrix can become a production authority.
    Every matrix, including the packaged one, is rebound to both upstream
    contract bytes on every evaluation. Every failure fails closed.
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
            if matrix.sha256 != SOURCE_MATRIX_V1_SHA256:
                raise MatrixRejected(
                    MatrixReason.DIGEST_MISMATCH,
                    "source_matrix_sha256",
                    "supplied matrix bytes are not the frozen canonical source matrix",
                )
        verify_upstream_bindings(
            matrix, policy_bytes=policy_bytes, gate_a_contract_bytes=gate_a_bytes
        )
        required = _required_classes_from_policy(policy_bytes, candidate_id=candidate_id)
        decisions = evaluate_matrix_rights(
            matrix, required, satisfied_conditions=satisfied_conditions
        )
    except MatrixRejected as error:
        raise _map_matrix_error(error) from error
    return CaptureRightsReport(source_matrix_sha256=matrix.sha256, decisions=decisions)
