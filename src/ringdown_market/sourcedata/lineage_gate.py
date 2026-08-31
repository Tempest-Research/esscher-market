"""Capture-boundary lineage gate for the snapshot collector.

The gate consumes the frozen point-in-time security lineage from issue #42
and fails closed before any capture when the event chain is missing, when
identity links contradict each other, when a ticker at cutoff belongs to
another issuer, or when listed options lack the OCC option adjustment for a
split. The gate grants no authority: it only narrows which captures may
proceed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

from ringdown_market.contracts.security_lineage import (
    LineageReason,
    LineageRejected,
    LineageResolution,
    SecurityLineage,
    load_security_lineage,
    parse_security_lineage,
    resolve_lineage,
    verify_lineage_upstream_bindings,
)
from ringdown_market.contracts.source_matrix import (
    SOURCE_MATRIX_V1_SHA256,
    source_matrix_bytes,
)
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.strategy.policy import (
    ACCEPTED_EVENT_POLICY_V1_SHA256,
    strategy_policy_bytes,
)

LINEAGE_RECEIPT_SCHEMA: Final = "esscher.lineage_receipt"


@dataclass(frozen=True)
class LineageGateReport:
    security_lineage_sha256: str
    resolution: LineageResolution


def _map_lineage_error(error: LineageRejected) -> CollectorRejected:
    mapping = {
        LineageReason.LINEAGE_MISSING: CollectorReason.LINEAGE_MISSING,
        LineageReason.LINEAGE_CONFLICT: CollectorReason.LINEAGE_CONFLICT,
        LineageReason.SYMBOL_REUSE_DETECTED: CollectorReason.SYMBOL_REUSE_DETECTED,
        LineageReason.OPTION_ADJUSTMENT_CONFLICT: CollectorReason.OPTION_ADJUSTMENT_CONFLICT,
        LineageReason.OPTION_ADJUSTMENT_UNRESOLVED: CollectorReason.OPTION_ADJUSTMENT_UNRESOLVED,
    }
    reason = mapping.get(error.reason, CollectorReason.LINEAGE_DRIFT)
    return CollectorRejected(reason, error.path, error.detail)


def lineage_receipt_bytes(resolution: LineageResolution) -> bytes:
    """Serialize one lineage resolution as canonical receipt bytes."""

    payload = {
        "schema": LINEAGE_RECEIPT_SCHEMA,
        "schema_version": 1,
        "event_id": resolution.event_id,
        "issuer_id": resolution.issuer_id,
        "security_id": resolution.security_id,
        "listing_id": resolution.listing_id,
        "ticker_at_cutoff": resolution.ticker_at_cutoff,
        "active_at_cutoff": resolution.active_at_cutoff,
        "action_ids": sorted(action.action_id for action in resolution.actions),
        "option_adjustment_ids": sorted(
            adjustment.action_id for adjustment in resolution.option_adjustments
        ),
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def evaluate_lineage(
    *,
    event_id: str,
    lineage_bytes: bytes | None = None,
    matrix_bytes: bytes | None = None,
) -> LineageGateReport:
    """Evaluate the frozen lineage for one event or fail closed.

    ``lineage_bytes=None`` loads the authenticated packaged lineage. Supplied
    lineage bytes are an internal deterministic-test seam. ``matrix_bytes``
    allows the capture boundary to pass its one selected matrix to both gates,
    but it must be byte-identical to the packaged canonical matrix. Every
    failure fails closed.
    """

    policy_bytes = strategy_policy_bytes()
    if hashlib.sha256(policy_bytes).hexdigest() != ACCEPTED_EVENT_POLICY_V1_SHA256:
        raise CollectorRejected(
            CollectorReason.LINEAGE_DRIFT,
            "policy_sha256",
            "packaged accepted event policy digest drift",
        )
    selected_matrix_bytes = source_matrix_bytes() if matrix_bytes is None else matrix_bytes
    if hashlib.sha256(selected_matrix_bytes).hexdigest() != SOURCE_MATRIX_V1_SHA256:
        raise CollectorRejected(
            CollectorReason.LINEAGE_DRIFT,
            "source_matrix_sha256",
            "supplied source matrix bytes are not the frozen canonical source matrix",
        )
    try:
        if lineage_bytes is None:
            lineage: SecurityLineage = load_security_lineage()
        else:
            lineage = parse_security_lineage(lineage_bytes)
        verify_lineage_upstream_bindings(
            lineage,
            policy_bytes=policy_bytes,
            source_matrix_bytes=selected_matrix_bytes,
        )
        resolution = resolve_lineage(lineage, event_id)
        if not resolution.active_at_cutoff:
            raise LineageRejected(
                LineageReason.LINEAGE_MISSING,
                f"{event_id}.listing.{resolution.listing_id}",
                "listing terminated before the decision cutoff; no current-survivor fallback",
            )
    except LineageRejected as error:
        raise _map_lineage_error(error) from error
    return LineageGateReport(security_lineage_sha256=lineage.sha256, resolution=resolution)
