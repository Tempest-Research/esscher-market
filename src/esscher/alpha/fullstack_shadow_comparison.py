"""Canonical full-stack shadow comparison against #66 application-service receipts.

The comparison binds every panel evidence row to the deadline-aware application
service's content-addressed receipts (stage receipt shas, operational health
receipt sha, and service terminal receipt sha), reports per-event agreement of
direction and disposition, and records every divergence as an explicit
machine-readable finding.  It is evidence only: nothing here mutates a ledger,
touches a broker, or performs a network, provider, or account call, and every
report is labelled synthetic and NOT_ALPHA_EVIDENCE.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from esscher.runtime.autonomous_application_service import (
    STAGE_ORDER,
    RunDisposition,
    ServiceTerminalReceipt,
    WindowRunResult,
    service_terminal_receipt_sha256,
    stage_receipt_sha256,
)
from esscher.runtime.health_receipts import (
    health_receipt_sha256,
)
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes

from .models import Direction
from .qfast_panel_reports import PANEL_REPORT_CLAIM, PanelEvidenceReport

FULLSTACK_COMPARISON_SCHEMA: Final = "esscher.qfast_fullstack_comparison"
FULLSTACK_COMPARISON_SCHEMA_VERSION: Final = 1
COMPARISON_LABELS: Final = ("NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION", "SYNTHETIC_FAKE")

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


class ComparisonFindingCode(StrEnum):
    """Stable machine-readable divergence codes for the comparison report."""

    DIRECTION_DIVERGENCE = "DIRECTION_DIVERGENCE"
    DISPOSITION_DIVERGENCE = "DISPOSITION_DIVERGENCE"
    MISSING_SERVICE_RECEIPTS = "MISSING_SERVICE_RECEIPTS"
    UNKNOWN_SERVICE_EVENT = "UNKNOWN_SERVICE_EVENT"
    STAGE_CHAIN_INCOMPLETE = "STAGE_CHAIN_INCOMPLETE"
    MALFORMED_RECEIPT_SHA = "MALFORMED_RECEIPT_SHA"


@dataclass(frozen=True, slots=True)
class ComparisonFinding:
    """One explicit divergence between the panel and the service stack."""

    code: ComparisonFindingCode
    event_id: str | None
    detail: str

    def payload(self) -> dict[str, object]:
        """Return the canonical finding payload."""

        return {"code": self.code.value, "detail": self.detail, "event_id": self.event_id}


@dataclass(frozen=True, slots=True)
class ServiceEventReceipts:
    """The #66 receipt identities the comparison links for one panel event."""

    event_id: str
    window_id: str
    stage_receipt_sha256s: tuple[str, ...]
    health_receipt_sha256: str
    terminal_receipt_sha256: str
    direction: str
    disposition: str

    def __post_init__(self) -> None:
        if not self.event_id or not self.event_id.strip():
            raise ValueError("event_id must be non-empty text")
        if not self.window_id or not self.window_id.strip():
            raise ValueError("window_id must be non-empty text")
        if self.direction not in {item.value for item in Direction}:
            raise ValueError("direction must be a bounded Direction value")
        if self.disposition not in {item.value for item in RunDisposition}:
            raise ValueError("disposition must be a bounded RunDisposition value")
        for digest in (
            *self.stage_receipt_sha256s,
            self.health_receipt_sha256,
            self.terminal_receipt_sha256,
        ):
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise ValueError("receipt identities must be lowercase SHA-256 digests")


def service_event_receipts_from_window_run(
    *,
    event_id: str,
    window_result: WindowRunResult,
    terminal_receipt: ServiceTerminalReceipt,
    direction: Direction,
) -> ServiceEventReceipts:
    """Bind one completed #66 window run into the comparison contract."""

    return ServiceEventReceipts(
        event_id=event_id,
        window_id=window_result.window_id,
        stage_receipt_sha256s=tuple(
            stage_receipt_sha256(item) for item in window_result.stage_receipts
        ),
        health_receipt_sha256=health_receipt_sha256(window_result.health_receipt),
        terminal_receipt_sha256=service_terminal_receipt_sha256(terminal_receipt),
        direction=direction.value,
        disposition=window_result.disposition.value,
    )


@dataclass(frozen=True, slots=True)
class FullstackComparisonReport:
    """The complete deterministic result of one full-stack comparison."""

    panel_report_sha256: str
    rows: Mapping[str, Mapping[str, object]]
    findings: tuple[ComparisonFinding, ...]
    payload: Mapping[str, object]

    @property
    def bytes(self) -> bytes:
        """Serialize the comparison deterministically."""

        return canonical_json_bytes(self.payload)

    @property
    def sha256(self) -> str:
        """Content-address the comparison report."""

        return sha256_bytes(self.bytes)


def _linked(
    receipts: ServiceEventReceipts,
    *,
    panel_direction: str,
    panel_admitted: bool,
) -> tuple[dict[str, object], list[ComparisonFinding]]:
    findings: list[ComparisonFinding] = []
    if len(receipts.stage_receipt_sha256s) != len(STAGE_ORDER):
        findings.append(
            ComparisonFinding(
                ComparisonFindingCode.STAGE_CHAIN_INCOMPLETE,
                receipts.event_id,
                f"expected {len(STAGE_ORDER)} stage receipts, linked "
                f"{len(receipts.stage_receipt_sha256s)}",
            )
        )
    if receipts.direction != panel_direction:
        findings.append(
            ComparisonFinding(
                ComparisonFindingCode.DIRECTION_DIVERGENCE,
                receipts.event_id,
                f"panel direction {panel_direction} differs from service direction "
                f"{receipts.direction}",
            )
        )
    service_acted = receipts.disposition == RunDisposition.COMPLETED.value
    if service_acted != panel_admitted:
        findings.append(
            ComparisonFinding(
                ComparisonFindingCode.DISPOSITION_DIVERGENCE,
                receipts.event_id,
                f"panel admission {panel_admitted} differs from service disposition "
                f"{receipts.disposition}",
            )
        )
    row = {
        "direction_agreement": receipts.direction == panel_direction,
        "disposition": receipts.disposition,
        "disposition_agreement": service_acted == panel_admitted,
        "health_receipt_sha256": receipts.health_receipt_sha256,
        "panel_direction": panel_direction,
        "service_direction": receipts.direction,
        "stage_receipt_sha256s": list(receipts.stage_receipt_sha256s),
        "terminal_receipt_sha256": receipts.terminal_receipt_sha256,
        "window_id": receipts.window_id,
    }
    return row, findings


def compare_fullstack(
    panel_report: PanelEvidenceReport,
    receipts_by_event: Mapping[str, ServiceEventReceipts],
) -> FullstackComparisonReport:
    """Compare one panel evidence report against linked #66 service receipts."""

    if type(panel_report) is not PanelEvidenceReport:
        raise ValueError("panel_report must be a PanelEvidenceReport")
    findings: list[ComparisonFinding] = []
    rows: dict[str, Mapping[str, object]] = {}
    for event_id in sorted(panel_report.rows):
        row = panel_report.rows[event_id]
        receipts = receipts_by_event.get(event_id)
        if receipts is None:
            findings.append(
                ComparisonFinding(
                    ComparisonFindingCode.MISSING_SERVICE_RECEIPTS,
                    event_id,
                    "no application-service receipts were linked for this panel event",
                )
            )
            rows[event_id] = {
                "linked": False,
                "panel_admitted": row.admitted,
                "panel_direction": row.direction,
                "panel_row_sha256": row.row_sha256,
            }
            continue
        if receipts.event_id != event_id:
            raise ValueError(f"receipts for {event_id} declare event {receipts.event_id}")
        linked_row, linked_findings = _linked(
            receipts, panel_direction=row.direction, panel_admitted=row.admitted
        )
        findings.extend(linked_findings)
        rows[event_id] = {
            "linked": True,
            "panel_admitted": row.admitted,
            "panel_direction": row.direction,
            "panel_row_sha256": row.row_sha256,
            **linked_row,
        }
    for event_id in sorted(receipts_by_event):
        if event_id not in panel_report.rows:
            findings.append(
                ComparisonFinding(
                    ComparisonFindingCode.UNKNOWN_SERVICE_EVENT,
                    event_id,
                    "service receipts reference an event outside the frozen panel",
                )
            )
    ordered = sorted(findings, key=lambda item: (item.code.value, item.event_id or ""))
    payload = {
        "claim": PANEL_REPORT_CLAIM,
        "event_count": len(panel_report.rows),
        "events": {event_id: dict(rows[event_id]) for event_id in sorted(rows)},
        "findings": [finding.payload() for finding in ordered],
        "labels": list(COMPARISON_LABELS),
        "linked_event_count": sum(1 for row in rows.values() if row["linked"]),
        "panel_report_sha256": panel_report.sha256,
        "panel_report_status": panel_report.status.value,
        "schema": FULLSTACK_COMPARISON_SCHEMA,
        "schema_version": FULLSTACK_COMPARISON_SCHEMA_VERSION,
    }
    return FullstackComparisonReport(
        panel_report_sha256=panel_report.sha256,
        rows=rows,
        findings=tuple(ordered),
        payload=payload,
    )


__all__ = [
    "COMPARISON_LABELS",
    "FULLSTACK_COMPARISON_SCHEMA",
    "FULLSTACK_COMPARISON_SCHEMA_VERSION",
    "ComparisonFinding",
    "ComparisonFindingCode",
    "FullstackComparisonReport",
    "ServiceEventReceipts",
    "compare_fullstack",
    "service_event_receipts_from_window_run",
]
