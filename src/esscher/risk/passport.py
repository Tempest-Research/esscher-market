"""Append-only Trade Passport events and a deterministic verifier.

The passport is a hash-linked chain covering candidates, abstentions,
rejections, decisions, reservations, orders, fills, closes, and
reconciliation. The verifier recomputes every link deterministically; any gap,
reorder, or tamper fails with ``PASSPORT_VERIFICATION_FAILED``. The passport
never grants authority — it only records truth.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from esscher.risk.reasons import RiskReason, _reject
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes

GENESIS_SHA256 = "0" * 64


class PassportEventType(StrEnum):
    """The Trade Passport event vocabulary."""

    CANDIDATE_FROZEN = "CANDIDATE_FROZEN"
    DECISION_MADE = "DECISION_MADE"
    ABSTAINED = "ABSTAINED"
    REJECTED = "REJECTED"
    EXPRESSION_COMPILED = "EXPRESSION_COMPILED"
    RISK_APPROVED = "RISK_APPROVED"
    RESERVATION_HELD = "RESERVATION_HELD"
    RESERVATION_RELEASED = "RESERVATION_RELEASED"
    PERMIT_ISSUED = "PERMIT_ISSUED"
    ORDER_INTENDED = "ORDER_INTENDED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    FILL_OBSERVED = "FILL_OBSERVED"
    POSITION_OBSERVED = "POSITION_OBSERVED"
    RECONCILED = "RECONCILED"
    CONTROL_STATE_CHANGED = "CONTROL_STATE_CHANGED"
    NOT_RUN = "NOT_RUN"


def passport_event_sha256(
    *,
    event_type: str,
    payload: Mapping[str, object],
    prev_sha256: str,
    created_at: str,
) -> str:
    """Return the deterministic hash of one passport link."""

    canonical = canonical_json_bytes(
        {
            "event_type": event_type,
            "payload": dict(payload),
            "prev_sha256": prev_sha256,
            "created_at": created_at,
        }
    )
    return sha256_bytes(canonical)


def verify_passport(events: Sequence[Mapping[str, object]]) -> int:
    """Verify the whole passport chain; return the verified event count.

    Raises ``PASSPORT_VERIFICATION_FAILED`` on any gap, reorder, or tamper.
    """

    expected_prev = GENESIS_SHA256
    for index, event in enumerate(events):
        event_type = event.get("event_type")
        payload = event.get("payload")
        prev_sha256 = event.get("prev_sha256")
        event_sha256 = event.get("event_sha256")
        created_at = event.get("created_at")
        if (
            not isinstance(event_type, str)
            or not isinstance(prev_sha256, str)
            or not isinstance(event_sha256, str)
            or not isinstance(created_at, str)
        ):
            raise _reject(
                RiskReason.PASSPORT_VERIFICATION_FAILED,
                f"passport[{index}]",
                "passport event fields are malformed",
            )
        if prev_sha256 != expected_prev:
            raise _reject(
                RiskReason.PASSPORT_VERIFICATION_FAILED,
                f"passport[{index}].prev_sha256",
                "passport chain is broken (previous hash mismatch)",
            )
        recomputed = passport_event_sha256(
            event_type=event_type,
            payload=payload if isinstance(payload, Mapping) else {},
            prev_sha256=prev_sha256,
            created_at=created_at,
        )
        if recomputed != event_sha256:
            raise _reject(
                RiskReason.PASSPORT_VERIFICATION_FAILED,
                f"passport[{index}].event_sha256",
                "passport link hash mismatch (tamper detected)",
            )
        expected_prev = event_sha256
    return len(events)
