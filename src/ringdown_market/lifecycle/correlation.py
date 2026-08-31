"""Correlation identity for the monitored PAPER lifecycle.

One correlation identity links every artifact of a single lifecycle run:
snapshot, decision, expression, risk reservation, open/close permits, orders,
fills, and the Trade Passport. The identity is deterministic and canonical so
the passport chain and the durable ledger can be cross-checked without
ambiguity. It grants no authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

CORRELATION_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CorrelationIdentity:
    """One lifecycle run's correlation identity."""

    event_run_id: str
    snapshot_sha256: str
    decision_sha256: str
    expression_sha256: str
    reservation_id: str
    open_permit_id: str
    close_permit_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_run_id.strip():
            raise ValueError("event_run_id must be non-empty text")
        for field in ("snapshot_sha256", "decision_sha256", "expression_sha256"):
            if len(getattr(self, field)) != 64:
                raise ValueError(f"{field} must be a SHA-256 digest")
        if not self.reservation_id.strip() or not self.open_permit_id.strip():
            raise ValueError("reservation_id and open_permit_id must be non-empty")
        if self.close_permit_id is not None and not self.close_permit_id.strip():
            raise ValueError("close_permit_id must be non-empty when present")

    def with_close_permit(self, close_permit_id: str) -> CorrelationIdentity:
        return CorrelationIdentity(
            event_run_id=self.event_run_id,
            snapshot_sha256=self.snapshot_sha256,
            decision_sha256=self.decision_sha256,
            expression_sha256=self.expression_sha256,
            reservation_id=self.reservation_id,
            open_permit_id=self.open_permit_id,
            close_permit_id=close_permit_id,
        )


def correlation_payload(identity: CorrelationIdentity) -> dict[str, object]:
    """Return the canonical correlation payload for the passport chain."""

    return {
        "schema_version": CORRELATION_SCHEMA_VERSION,
        "event_run_id": identity.event_run_id,
        "snapshot_sha256": identity.snapshot_sha256,
        "decision_sha256": identity.decision_sha256,
        "expression_sha256": identity.expression_sha256,
        "reservation_id": identity.reservation_id,
        "open_permit_id": identity.open_permit_id,
        "close_permit_id": identity.close_permit_id,
    }


def correlation_sha256(identity: CorrelationIdentity) -> str:
    """Return the deterministic correlation hash for one lifecycle run."""

    return sha256_bytes(canonical_json_bytes(correlation_payload(identity)))
