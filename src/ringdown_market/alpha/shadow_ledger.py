"""Append-only shadow records over the existing risk-ledger episode API.

Shadow episodes bind signal, theoretical expression, fake lifecycle result,
costs, clocks, P&L class, and final-flat state without touching the ledger
schema and without any broker or account access.  Broker truth and shadow
theoretical P&L stay in separate classifications and separate episodes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from ringdown_market.risk.ledger import RiskLedger

from .direction_receipts import DirectionReceipt, direction_receipt_bytes
from .evaluation import EventEvaluation
from .shadow_runner import ShadowRunResult

SHADOW_PNL_CLASS: Final = "SHADOW_THEORETICAL"
SHADOW_LIFECYCLE: Final = "SHADOW_THEORETICAL_FLAT"
SHADOW_GENESIS_SUMMARY_SHA: Final = hashlib.sha256(b"ESSCHER_SHADOW_GENESIS_SUMMARY").hexdigest()
SHADOW_UNBOUND_DIGEST: Final = hashlib.sha256(b"ESSCHER_SHADOW_UNBOUND_ROUTE").hexdigest()
_QUANTUM: Final = Decimal("0.000000000001")


class ShadowLedgerReason(StrEnum):
    CUTOFF_VIOLATION = "CUTOFF_VIOLATION"
    MISSING_EVALUATION = "MISSING_EVALUATION"


class ShadowLedgerRejection(ValueError):
    """Raised when a shadow record would violate the cutoff or identity rules."""

    def __init__(self, reason: ShadowLedgerReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _theoretical_pnl(evaluation: EventEvaluation) -> Decimal:
    return Decimal(f"{evaluation.signed_residual:.12f}").quantize(_QUANTUM)


def shadow_decision_episode(
    *,
    receipt: DirectionReceipt,
    evaluation: EventEvaluation,
    symbol: str,
    candidate_id: str,
    policy_sha256: str,
    evidence_sha256: str,
    feature_sha256: str,
    snapshot_sha256: str,
) -> tuple[Mapping[str, object], bytes, str]:
    """Build one canonical shadow decision episode row contract."""

    payload = json.dumps(
        {
            "schema": "esscher.shadow_decision_episode",
            "schema_version": 1,
            "event_id": receipt.event_id,
            "direction": receipt.direction.value,
            "reason_codes": list(receipt.reason_codes),
            "classification": list(receipt.classification),
            "producer_kind": receipt.producer_kind.value,
            "decision_receipt_sha256": hashlib.sha256(direction_receipt_bytes(receipt)).hexdigest(),
            "theoretical_expression": {
                "entry_at": _iso(evaluation.entry_at),
                "exit_at": _iso(evaluation.exit_at),
                "actual_latency_ms": evaluation.actual_latency_ms,
                "residual_return": evaluation.residual_return,
                "signed_residual": evaluation.signed_residual,
                "admitted": evaluation.admitted,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    payload_sha = hashlib.sha256(payload).hexdigest()
    values = {
        "episode_id": payload_sha,
        "event_id": receipt.event_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "occurred_at": _iso(evaluation.entry_at),
        "decision_cutoff_at": _iso(receipt.decision_cutoff_at),
        "source_policy_sha256": policy_sha256,
        "source_evidence_sha256": evidence_sha256,
        "source_feature_sha256": feature_sha256,
        "source_snapshot_sha256": snapshot_sha256,
        "prior_summary_sha256": SHADOW_GENESIS_SUMMARY_SHA,
        "route_sha256": receipt.route_sha256 or SHADOW_UNBOUND_DIGEST,
        "prompt_sha256": receipt.prompt_sha256 or SHADOW_UNBOUND_DIGEST,
        "model_config_sha256": receipt.model_config_sha256 or SHADOW_UNBOUND_DIGEST,
        "exchange_sha256": SHADOW_UNBOUND_DIGEST,
        "decision_sha256": hashlib.sha256(direction_receipt_bytes(receipt)).hexdigest(),
        "disposition": "SHADOW_ACCEPTED" if evaluation.admitted else "SHADOW_ABSTAINED",
        "direction": receipt.direction.value,
        "created_at": _iso(receipt.produced_at),
        "supersedes_episode_id": None,
        "supersedes_episode_sha256": None,
    }
    return values, payload, payload_sha


def shadow_outcome_episode(
    *,
    decision_episode_id: str,
    event_id: str,
    evaluation: EventEvaluation,
    costs: Decimal,
    receipt: DirectionReceipt,
) -> tuple[Mapping[str, object], bytes, str]:
    """Build one canonical shadow outcome episode row contract."""

    gross = _theoretical_pnl(evaluation)
    net = (gross - costs).quantize(_QUANTUM)
    payload = json.dumps(
        {
            "schema": "esscher.shadow_outcome_episode",
            "schema_version": 1,
            "event_id": event_id,
            "decision_episode_id": decision_episode_id,
            "lifecycle_outcome": SHADOW_LIFECYCLE,
            "pnl_classification": SHADOW_PNL_CLASS,
            "gross_pnl": str(gross),
            "net_pnl": str(net),
            "costs": str(costs),
            "terminal_at": _iso(evaluation.exit_at),
            "final_flat": True,
            "broker_truth": False,
            "classification": list(receipt.classification),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    payload_sha = hashlib.sha256(payload).hexdigest()
    values = {
        "outcome_id": payload_sha,
        "decision_episode_id": decision_episode_id,
        "event_id": event_id,
        "open_permit_id": None,
        "close_permit_id": None,
        "open_order_id": None,
        "close_order_id": None,
        "terminal_at": _iso(evaluation.exit_at),
        "observed_at": _iso(evaluation.exit_at),
        "lifecycle_outcome": SHADOW_LIFECYCLE,
        "pnl_classification": SHADOW_PNL_CLASS,
        "gross_pnl": str(gross),
        "net_pnl": str(net),
        "reconciliation_sha256": payload_sha,
        "final_flat": True,
        "supersedes_outcome_id": None,
        "supersedes_outcome_sha256": None,
        "created_at": _iso(evaluation.exit_at),
    }
    return values, payload, payload_sha


def append_shadow_episode_pair(
    ledger: RiskLedger,
    *,
    receipt: DirectionReceipt,
    evaluation: EventEvaluation,
    symbol: str,
    candidate_id: str,
    policy_sha256: str,
    evidence_sha256: str,
    feature_sha256: str,
    snapshot_sha256: str,
    costs: Decimal,
) -> tuple[bool, bool]:
    """Append one shadow decision+outcome pair; identical replays are idempotent."""

    if evaluation.entry_at < receipt.decision_cutoff_at:
        raise ShadowLedgerRejection(
            ShadowLedgerReason.CUTOFF_VIOLATION,
            f"shadow entry for {receipt.event_id} precedes the decision cutoff",
        )
    decision_values, decision_payload, decision_sha = shadow_decision_episode(
        receipt=receipt,
        evaluation=evaluation,
        symbol=symbol,
        candidate_id=candidate_id,
        policy_sha256=policy_sha256,
        evidence_sha256=evidence_sha256,
        feature_sha256=feature_sha256,
        snapshot_sha256=snapshot_sha256,
    )
    outcome_values, outcome_payload, outcome_sha = shadow_outcome_episode(
        decision_episode_id=decision_values["episode_id"],
        event_id=receipt.event_id,
        evaluation=evaluation,
        costs=costs,
        receipt=receipt,
    )
    decision_appended = ledger.append_decision_episode(
        values=decision_values, payload=decision_payload, payload_sha256=decision_sha
    )
    outcome_appended = ledger.append_outcome_episode(
        values=outcome_values, payload=outcome_payload, payload_sha256=outcome_sha
    )
    return decision_appended, outcome_appended


def record_shadow_run(
    ledger: RiskLedger,
    result: ShadowRunResult,
    *,
    candidate_id: str,
    policy_sha256: str,
    evidence_sha256: str,
    feature_sha256: str,
    snapshot_sha256: str,
    costs: Mapping[str, Decimal],
) -> int:
    """Record every evaluated event of an accepted shadow run; returns appends."""

    if not result.accepted:
        raise ShadowLedgerRejection(
            ShadowLedgerReason.MISSING_EVALUATION,
            "rejected shadow runs produce no ledger records",
        )
    appended = 0
    for receipt in result.receipts:
        evaluation = result.evaluations.get(receipt.event_id)
        if evaluation is None:
            raise ShadowLedgerRejection(
                ShadowLedgerReason.MISSING_EVALUATION,
                f"no evaluation for event {receipt.event_id}",
            )
        decision_appended, _ = append_shadow_episode_pair(
            ledger,
            receipt=receipt,
            evaluation=evaluation,
            symbol=result.symbols[receipt.event_id],
            candidate_id=candidate_id,
            policy_sha256=policy_sha256,
            evidence_sha256=evidence_sha256,
            feature_sha256=feature_sha256,
            snapshot_sha256=snapshot_sha256,
            costs=costs.get(receipt.event_id, Decimal("0")),
        )
        appended += 1 if decision_appended else 0
    return appended
