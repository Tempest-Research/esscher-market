"""Pre-outcome validation of the frozen Q-FAST evidence configuration.

The validator reports exact event count, sector count, manifest identity,
source/clock/rights status, and every rejection reason before any outcome or
price path may be accessed.  It never inspects outcomes and never fills a gap
with a plausible value: any missing or mismatched configuration element is a
machine-readable rejection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from esscher.contracts.latency_profile import (
    LatencyProfileRejected,
    ValidatedLatencyProfile,
    validate_latency_profile,
)
from esscher.panel.manifest import (
    PanelRejected,
    validate_panel_manifest,
)
from esscher.panel.universe import validate_panel_universe
from esscher.panel.windows import validate_market_window_set

from .direction_receipts import (
    DirectionReceipt,
    DirectionReceiptRejected,
    parse_direction_receipt_set,
)

SOURCE_VERIFIED: Final = "PRIMARY_SOURCE_VERIFIED"
SOURCE_NOT_SUPPLIED: Final = "NOT_SUPPLIED"
CLOCKS_VERIFIED: Final = "SYNCHRONIZED_CLOCKS_VERIFIED"
CLOCKS_NOT_SUPPLIED: Final = "NOT_SUPPLIED"
RIGHTS_DECLARED: Final = "RIGHTS_DECLARED"
RIGHTS_UNVERIFIED: Final = "RIGHTS_UNVERIFIED"


class EvidenceRejectionReason(StrEnum):
    """Stable machine-readable rejection codes for the evidence configuration."""

    MANIFEST_INVALID = "MANIFEST_INVALID"
    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    SOURCE_STATUS_UNVERIFIED = "SOURCE_STATUS_UNVERIFIED"
    CLOCK_STATUS_INVALID = "CLOCK_STATUS_INVALID"
    RIGHTS_STATUS_UNVERIFIED = "RIGHTS_STATUS_UNVERIFIED"
    EVENT_COUNT_MISMATCH = "EVENT_COUNT_MISMATCH"
    SECTOR_COUNT_MISMATCH = "SECTOR_COUNT_MISMATCH"
    DECISION_SET_INCOMPLETE = "DECISION_SET_INCOMPLETE"
    RECEIPT_INVALID = "RECEIPT_INVALID"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    LATENCY_PROFILE_MISMATCH = "LATENCY_PROFILE_MISMATCH"


def _reject(
    reasons: list[str], details: list[str], reason: EvidenceRejectionReason, detail: str
) -> None:
    reasons.append(reason.value)
    details.append(f"{reason.value}: {detail}")


@dataclass(frozen=True, slots=True)
class EvidenceValidationReport:
    """The complete pre-outcome validation state of one evidence configuration."""

    accepted: bool
    event_count: int
    sector_count: int | None
    manifest_sha256: str
    selection_rule_sha256: str
    source_status: str
    clock_status: str
    rights_status: str
    latency_profile_kind: str | None
    rejection_reasons: tuple[str, ...]
    rejection_details: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "schema": "esscher.qfast_evidence_validation",
            "schema_version": 1,
            "accepted": self.accepted,
            "event_count": self.event_count,
            "sector_count": self.sector_count,
            "manifest_sha256": self.manifest_sha256,
            "selection_rule_sha256": self.selection_rule_sha256,
            "source_status": self.source_status,
            "clock_status": self.clock_status,
            "rights_status": self.rights_status,
            "latency_profile_kind": self.latency_profile_kind,
            "rejection_reasons": list(self.rejection_reasons),
            "rejection_details": list(self.rejection_details),
        }

    def bytes(self) -> bytes:
        return json.dumps(
            self.payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_evidence_configuration(
    *,
    manifest_bytes: bytes,
    selection_rule_bytes: bytes,
    receipt_bytes: Sequence[bytes],
    latency_profile_bytes: bytes,
    expected_policy_sha256: str,
    event_list_bytes: bytes | None = None,
    universe_manifest_bytes: Sequence[bytes] = (),
    window_bytes: Sequence[bytes] = (),
) -> EvidenceValidationReport:
    """Validate one frozen evidence configuration or report every rejection."""

    reasons: list[str] = []
    details: list[str] = []

    try:
        manifest = validate_panel_manifest(manifest_bytes, selection_rule_bytes)
    except PanelRejected as error:
        _reject(
            reasons,
            details,
            EvidenceRejectionReason.MANIFEST_INVALID,
            f"{error.reason.value} at {error.path}: {error.detail}",
        )
        return EvidenceValidationReport(
            accepted=False,
            event_count=0,
            sector_count=None,
            manifest_sha256=_sha256(manifest_bytes),
            selection_rule_sha256=_sha256(selection_rule_bytes),
            source_status=SOURCE_NOT_SUPPLIED,
            clock_status=CLOCKS_NOT_SUPPLIED,
            rights_status=RIGHTS_UNVERIFIED,
            latency_profile_kind=None,
            rejection_reasons=tuple(reasons),
            rejection_details=tuple(details),
        )

    source_status = SOURCE_NOT_SUPPLIED
    if universe_manifest_bytes:
        try:
            validate_panel_universe(
                selection_rule_bytes,
                _require_bytes(event_list_bytes, reasons, details),
                universe_manifest_bytes,
            )
            source_status = SOURCE_VERIFIED
        except (PanelRejected, UnicodeDecodeError, json.JSONDecodeError) as error:
            source_status = SOURCE_NOT_SUPPLIED
            _reject(
                reasons,
                details,
                EvidenceRejectionReason.SOURCE_STATUS_UNVERIFIED,
                str(error),
            )

    clock_status = CLOCKS_NOT_SUPPLIED
    if window_bytes:
        try:
            validate_market_window_set(window_bytes, manifest.eligible_event_ids)
            clock_status = CLOCKS_VERIFIED
        except (PanelRejected, UnicodeDecodeError, json.JSONDecodeError) as error:
            _reject(
                reasons,
                details,
                EvidenceRejectionReason.CLOCK_STATUS_INVALID,
                str(error),
            )

    rights_status = RIGHTS_DECLARED if manifest.limitations else RIGHTS_UNVERIFIED
    if rights_status is RIGHTS_UNVERIFIED:
        _reject(
            reasons,
            details,
            EvidenceRejectionReason.RIGHTS_STATUS_UNVERIFIED,
            "panel manifest carries no rights limitation declarations",
        )

    sector_count: int | None = None
    if event_list_bytes is not None:
        try:
            event_list = json.loads(event_list_bytes.decode("utf-8"))
            sectors = {event["sector"] for event in event_list["events"]}
            sector_count = len(sectors)
            if len(event_list["events"]) != len(manifest.eligible_event_ids):
                _reject(
                    reasons,
                    details,
                    EvidenceRejectionReason.EVENT_COUNT_MISMATCH,
                    "event list count differs from the frozen panel manifest",
                )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            _reject(
                reasons,
                details,
                EvidenceRejectionReason.INVALID_DOCUMENT,
                f"event list unreadable: {error}",
            )
    if sector_count is None:
        _reject(
            reasons,
            details,
            EvidenceRejectionReason.SECTOR_COUNT_MISMATCH,
            "sector count unavailable without the frozen event list",
        )

    receipts: Mapping[str, DirectionReceipt] = {}
    try:
        receipts = parse_direction_receipt_set(receipt_bytes)
    except DirectionReceiptRejected as error:
        _reject(
            reasons,
            details,
            EvidenceRejectionReason.RECEIPT_INVALID,
            f"{error.reason.value} at {error.path}: {error.detail}",
        )
    if receipts:
        eligible = set(manifest.eligible_event_ids)
        missing = sorted(eligible - set(receipts))
        extra = sorted(set(receipts) - eligible)
        if missing or extra:
            _reject(
                reasons,
                details,
                EvidenceRejectionReason.DECISION_SET_INCOMPLETE,
                f"missing decisions: {missing or 'none'}; unexpected decisions: {extra or 'none'}",
            )

    if manifest.strategy_policy_sha256 != expected_policy_sha256:
        _reject(
            reasons,
            details,
            EvidenceRejectionReason.POLICY_MISMATCH,
            "panel manifest strategy policy differs from the expected frozen policy",
        )

    profile: ValidatedLatencyProfile | None = None
    try:
        profile = validate_latency_profile(latency_profile_bytes)
    except LatencyProfileRejected as error:
        _reject(
            reasons,
            details,
            EvidenceRejectionReason.LATENCY_PROFILE_MISMATCH,
            f"{error.reason.value} at {error.path}: {error.detail}",
        )
    if profile is not None:
        if profile.policy_sha256 != expected_policy_sha256:
            _reject(
                reasons,
                details,
                EvidenceRejectionReason.POLICY_MISMATCH,
                "latency profile binds a different strategy policy",
            )
        if profile.p95_latency_ms != manifest.latency_profiles.get("p95"):
            _reject(
                reasons,
                details,
                EvidenceRejectionReason.LATENCY_PROFILE_MISMATCH,
                "p95 latency differs from the frozen panel manifest profile",
            )
        if manifest.latency_profiles.get("zero") != 0:
            _reject(
                reasons,
                details,
                EvidenceRejectionReason.LATENCY_PROFILE_MISMATCH,
                "the zero-latency profile must be exactly zero milliseconds",
            )

    return EvidenceValidationReport(
        accepted=not reasons,
        event_count=len(manifest.eligible_event_ids),
        sector_count=sector_count,
        manifest_sha256=manifest.panel_manifest_sha256,
        selection_rule_sha256=manifest.selection_rule_sha256,
        source_status=source_status,
        clock_status=clock_status,
        rights_status=rights_status,
        latency_profile_kind=profile.kind.value if profile is not None else None,
        rejection_reasons=tuple(reasons),
        rejection_details=tuple(details),
    )


def _require_bytes(value: bytes | None, reasons: list[str], details: list[str]) -> bytes:
    if value is None:
        _reject(
            reasons,
            details,
            EvidenceRejectionReason.SOURCE_STATUS_UNVERIFIED,
            "universe validation requires the frozen event list",
        )
        raise TypeError("event list required with universe manifests")
    return value
