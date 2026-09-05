"""Offline-only, hash-linked passport adapter for sanitized PAPER receipts.

This module is an observer: it never opens a session, calls an MCP tool, stores
credentials, or adds execution authority.  It can only bind already-created,
validated PAPER permits and terminal receipt bytes into the retained append-only
Trade Passport chain.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from esscher.contracts.execution_policy import paper_event_run_id
from esscher.execution.models import (
    DataClass,
    DebitVerticalPermit,
    RunMode,
    debit_vertical_permit_id,
)
from esscher.execution.paper_demo import (
    PaperPnlClass,
    PaperReceiptBundle,
)

from .chain import (
    GENESIS_PREV_SHA256,
    PASSPORT_SCHEMA,
    PASSPORT_SCHEMA_VERSION,
    PassportChainError,
    PassportStage,
    TradePassport,
    parse_passport_bytes,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# The legacy source stage vocabulary remains stable.  The narrower current
# adapter emits only stages backed by current, sanitized PAPER artifacts.
PaperPassportStage = PassportStage


class PaperPassportRejected(ValueError):
    """Raised before a passport exists when bindings are absent or inconsistent."""


class PaperPassportVerdictReason(StrEnum):
    EMPTY_PASSPORT = "EMPTY_PASSPORT"
    PARSE_ERROR = "PARSE_ERROR"
    LINKAGE_BROKEN = "LINKAGE_BROKEN"
    STAGE_SEQUENCE_INVALID = "STAGE_SEQUENCE_INVALID"
    BINDING_INVALID = "BINDING_INVALID"


@dataclass(frozen=True, slots=True)
class PaperPassportVerification:
    """Pure verification result; it never contacts a broker or a provider."""

    valid: bool
    reasons: tuple[PaperPassportVerdictReason, ...]
    details: tuple[str, ...]


_CANCELED_STAGES = (
    PassportStage.SOURCE_EVIDENCE,
    PassportStage.SNAPSHOT,
    PassportStage.DECISION,
    PassportStage.PERMIT,
    PassportStage.OPEN_SUBMISSION,
    PassportStage.FINAL_FLAT_RECONCILIATION,
    PassportStage.RESULT,
)
_CLOSED_STAGES = (
    PassportStage.SOURCE_EVIDENCE,
    PassportStage.SNAPSHOT,
    PassportStage.DECISION,
    PassportStage.PERMIT,
    PassportStage.OPEN_SUBMISSION,
    PassportStage.CLOSE_SUBMISSION,
    PassportStage.FINAL_FLAT_RECONCILIATION,
    PassportStage.RESULT,
)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _require_sha256(value: object, name: str) -> str:
    if not _is_sha256(value):
        raise PaperPassportRejected(f"{name} must be a lowercase SHA-256")
    return str(value)


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperPassportRejected(f"{name} must be non-empty text")
    return value


def _utc_second(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PaperPassportRejected(f"{name} must be timezone-aware")
    if value.microsecond != 0:
        raise PaperPassportRejected(f"{name} must use whole-second precision")
    return value.astimezone(UTC)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PaperPassportRejected(f"{name} must be an object")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise PaperPassportRejected(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _canonical_receipt_payload(receipt: PaperReceiptBundle) -> Mapping[str, object]:
    try:
        payload = json.loads(receipt.to_json_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PaperPassportRejected("PAPER receipt cannot be serialized canonically") from error
    payload = _mapping(payload, "PAPER receipt")
    if payload.get("receipt_sha256") != receipt.receipt_sha256:
        raise PaperPassportRejected("PAPER receipt hash does not bind its exact payload")
    return payload


def _validate_inputs(
    *,
    source_manifest_sha256: str,
    permit: DebitVerticalPermit,
    receipt: PaperReceiptBundle,
) -> tuple[Mapping[str, object], datetime]:
    _require_sha256(source_manifest_sha256, "source_manifest_sha256")
    for name, value in (
        ("permit.policy_sha256", permit.policy_sha256),
        ("permit.protocol_sha256", permit.protocol_sha256),
        ("permit.execution_protocol_sha256", permit.execution_protocol_sha256),
        ("permit.snapshot_sha256", permit.snapshot_sha256),
        ("permit.decision_sha256", permit.decision_sha256),
        ("permit.evidence_sha256", permit.evidence_sha256),
    ):
        _require_sha256(value, name)
    if permit.run_mode is not RunMode.PAPER:
        raise PaperPassportRejected("permit must remain PAPER")
    if permit.data_class is not DataClass.INDICATIVE_DATA:
        raise PaperPassportRejected("permit must remain INDICATIVE_DATA")
    if permit.permit_id != debit_vertical_permit_id(permit):
        raise PaperPassportRejected("permit ID does not bind its frozen authorization")
    if permit.event_run_id != paper_event_run_id(permit.decision_sha256):
        raise PaperPassportRejected("permit event run does not bind its frozen decision")

    terminal_at = _utc_second(receipt.final_flat_observed_at, "receipt.final_flat_observed_at")
    if receipt.event_run_id != permit.event_run_id:
        raise PaperPassportRejected("receipt event run does not match permit event run")
    if receipt.open_permit_id != permit.permit_id:
        raise PaperPassportRejected("receipt open permit does not match permit")
    _require_text(receipt.close_permit_id, "receipt.close_permit_id")

    if receipt.lifecycle_outcome == "CLOSED_FLAT":
        if not receipt.close_request_sha256 or not receipt.close_order_sha256:
            raise PaperPassportRejected(
                "CLOSED_FLAT receipt requires closing request and order hashes"
            )
        if receipt.pnl.classification not in {
            PaperPnlClass.PAPER_REALIZED_PNL,
            PaperPnlClass.PAPER_PNL_UNAVAILABLE,
        }:
            raise PaperPassportRejected("CLOSED_FLAT receipt has an invalid PAPER P&L class")
    elif receipt.lifecycle_outcome == "CANCELED_FLAT":
        if receipt.close_request_sha256 is not None or receipt.close_order_sha256 is not None:
            raise PaperPassportRejected("CANCELED_FLAT receipt cannot claim a closing order")
        if receipt.pnl.classification is not PaperPnlClass.ZERO_NO_FILL:
            raise PaperPassportRejected("CANCELED_FLAT receipt must report ZERO_NO_FILL")
    else:
        raise PaperPassportRejected(
            "receipt lifecycle must be a broker-confirmed flat terminal state"
        )

    for name, value in (
        ("receipt.capability_sha256", receipt.capability_sha256),
        ("receipt.open_request_sha256", receipt.open_request_sha256),
        ("receipt.open_order_sha256", receipt.open_order_sha256),
        ("receipt.close_request_sha256", receipt.close_request_sha256),
        ("receipt.close_order_sha256", receipt.close_order_sha256),
    ):
        if value is not None:
            _require_sha256(value, name)
    return _canonical_receipt_payload(receipt), terminal_at


def build_paper_trade_passport(
    *,
    source_manifest_sha256: str,
    permit: DebitVerticalPermit,
    receipt: PaperReceiptBundle,
) -> TradePassport:
    """Bind a sanitized, terminal PAPER receipt into an offline Trade Passport.

    The caller supplies a previously validated permit and a terminal receipt.
    This function does not produce a permit, place/cancel/replace an order, or
    reach an MCP, broker, network, credential, or account boundary.
    """

    receipt_payload, terminal_at = _validate_inputs(
        source_manifest_sha256=source_manifest_sha256,
        permit=permit,
        receipt=receipt,
    )
    receipt_sha256 = _require_sha256(receipt_payload.get("receipt_sha256"), "receipt_sha256")
    passport = TradePassport()
    passport.append(
        stage=PassportStage.SOURCE_EVIDENCE,
        at=terminal_at,
        payload={"source_manifest_sha256": source_manifest_sha256},
    )
    passport.append(
        stage=PassportStage.SNAPSHOT,
        at=terminal_at,
        payload={
            "source_manifest_sha256": source_manifest_sha256,
            "snapshot_sha256": permit.snapshot_sha256,
            "evidence_sha256": permit.evidence_sha256,
        },
    )
    passport.append(
        stage=PassportStage.DECISION,
        at=terminal_at,
        payload={
            "decision_sha256": permit.decision_sha256,
            "snapshot_sha256": permit.snapshot_sha256,
            "protocol_sha256": permit.protocol_sha256,
            "direction_authority": "BOUNDED_RESEARCH_ONLY",
        },
    )
    passport.append(
        stage=PassportStage.PERMIT,
        at=terminal_at,
        payload={
            "permit_id": permit.permit_id,
            "event_run_id": permit.event_run_id,
            "decision_sha256": permit.decision_sha256,
            "snapshot_sha256": permit.snapshot_sha256,
            "policy_sha256": permit.policy_sha256,
            "execution_protocol_sha256": permit.execution_protocol_sha256,
            "run_mode": permit.run_mode.value,
            "data_class": permit.data_class.value,
        },
    )
    passport.append(
        stage=PassportStage.OPEN_SUBMISSION,
        at=terminal_at,
        payload={
            "event_run_id": permit.event_run_id,
            "permit_id": permit.permit_id,
            "open_request_sha256": receipt.open_request_sha256,
            "open_order_sha256": receipt.open_order_sha256,
            "receipt_sha256": receipt_sha256,
        },
    )
    if receipt.lifecycle_outcome == "CLOSED_FLAT":
        passport.append(
            stage=PassportStage.CLOSE_SUBMISSION,
            at=terminal_at,
            payload={
                "event_run_id": permit.event_run_id,
                "open_permit_id": permit.permit_id,
                "close_permit_id": receipt.close_permit_id,
                "close_request_sha256": receipt.close_request_sha256,
                "close_order_sha256": receipt.close_order_sha256,
                "receipt_sha256": receipt_sha256,
            },
        )
    passport.append(
        stage=PassportStage.FINAL_FLAT_RECONCILIATION,
        at=terminal_at,
        payload={
            "event_run_id": permit.event_run_id,
            "lifecycle_outcome": receipt.lifecycle_outcome,
            "final_flat_observed_at": terminal_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "authority": "SANITIZED_BROKER_RECEIPT",
            "receipt_sha256": receipt_sha256,
        },
    )
    passport.append(
        stage=PassportStage.RESULT,
        at=terminal_at,
        payload={
            "event_run_id": permit.event_run_id,
            "receipt_sha256": receipt_sha256,
            "classification": receipt.pnl.classification.value,
            "paper_pnl": _mapping(receipt_payload.get("paper_pnl"), "receipt.paper_pnl"),
            "claims": ["PAPER_OPERATIONAL_OBSERVATION", "NOT_ALPHA_EVIDENCE"],
        },
    )
    return passport


def _invalid(reason: PaperPassportVerdictReason, detail: str) -> PaperPassportVerification:
    return PaperPassportVerification(valid=False, reasons=(reason,), details=(detail,))


def _entry_payload(entries: tuple[object, ...], index: int) -> Mapping[str, object]:
    try:
        entry = entries[index]
        payload = entry.payload
    except IndexError as error:
        raise PaperPassportRejected("passport is missing an expected entry") from error
    return _mapping(payload, f"passport entry {index} payload")


def verify_paper_trade_passport(raw: bytes) -> PaperPassportVerification:
    """Verify chain linkage and current-PAPER bindings without external authority."""

    try:
        document = json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, PaperPassportRejected) as error:
        return _invalid(PaperPassportVerdictReason.PARSE_ERROR, str(error))
    try:
        entries = parse_passport_bytes(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, PassportChainError, ValueError) as error:
        return _invalid(PaperPassportVerdictReason.PARSE_ERROR, str(error))
    if not isinstance(document, Mapping):
        return _invalid(
            PaperPassportVerdictReason.PARSE_ERROR, "passport document is not an object"
        )
    if not entries:
        return _invalid(PaperPassportVerdictReason.EMPTY_PASSPORT, "passport has no entries")
    if set(document) != {"schema", "schema_version", "entries", "head_sha256"}:
        return _invalid(PaperPassportVerdictReason.PARSE_ERROR, "passport fields are not exact")
    if (
        document.get("schema") != PASSPORT_SCHEMA
        or document.get("schema_version") != PASSPORT_SCHEMA_VERSION
        or not _is_sha256(document.get("head_sha256"))
        or not isinstance(document.get("entries"), list)
    ):
        return _invalid(PaperPassportVerdictReason.PARSE_ERROR, "passport envelope is invalid")
    for index, item in enumerate(document["entries"]):
        if not isinstance(item, Mapping) or set(item) != {
            "schema",
            "schema_version",
            "sequence",
            "stage",
            "at",
            "payload",
            "prev_sha256",
        }:
            return _invalid(
                PaperPassportVerdictReason.PARSE_ERROR,
                f"passport entry {index} fields are not exact",
            )
        if (
            item.get("schema") != PASSPORT_SCHEMA
            or item.get("schema_version") != PASSPORT_SCHEMA_VERSION
        ):
            return _invalid(
                PaperPassportVerdictReason.PARSE_ERROR,
                f"passport entry {index} schema is invalid",
            )

    expected_prev = GENESIS_PREV_SHA256
    for index, entry in enumerate(entries):
        if entry.sequence != index or entry.prev_sha256 != expected_prev:
            return _invalid(
                PaperPassportVerdictReason.LINKAGE_BROKEN, "sequence or predecessor hash broke"
            )
        expected_prev = entry.entry_sha256
    if document.get("head_sha256") != expected_prev:
        return _invalid(
            PaperPassportVerdictReason.LINKAGE_BROKEN, "head hash does not bind final entry"
        )

    try:
        final_flat = _entry_payload(entries, -2)
        outcome = final_flat.get("lifecycle_outcome")
        expected_stages = _CLOSED_STAGES if outcome == "CLOSED_FLAT" else _CANCELED_STAGES
        if tuple(entry.stage for entry in entries) != expected_stages:
            return _invalid(
                PaperPassportVerdictReason.STAGE_SEQUENCE_INVALID,
                "terminal lifecycle does not match the deterministic stage sequence",
            )
        source = _entry_payload(entries, 0)
        snapshot = _entry_payload(entries, 1)
        decision = _entry_payload(entries, 2)
        permit = _entry_payload(entries, 3)
        opening = _entry_payload(entries, 4)
        reconciliation = _entry_payload(entries, -2)
        result = _entry_payload(entries, -1)

        source_sha = _require_sha256(source.get("source_manifest_sha256"), "source manifest")
        if (
            snapshot.get("source_manifest_sha256") != source_sha
            or not _is_sha256(snapshot.get("snapshot_sha256"))
            or not _is_sha256(snapshot.get("evidence_sha256"))
            or decision.get("snapshot_sha256") != snapshot.get("snapshot_sha256")
            or not _is_sha256(decision.get("decision_sha256"))
            or not _is_sha256(decision.get("protocol_sha256"))
            or decision.get("direction_authority") != "BOUNDED_RESEARCH_ONLY"
        ):
            raise PaperPassportRejected(
                "source, snapshot, and direction-only decision binding is invalid"
            )
        if (
            permit.get("decision_sha256") != decision.get("decision_sha256")
            or permit.get("snapshot_sha256") != snapshot.get("snapshot_sha256")
            or permit.get("run_mode") != "PAPER"
            or permit.get("data_class") != "INDICATIVE_DATA"
            or not _is_sha256(permit.get("policy_sha256"))
            or not _is_sha256(permit.get("execution_protocol_sha256"))
            or not isinstance(permit.get("permit_id"), str)
            or not isinstance(permit.get("event_run_id"), str)
        ):
            raise PaperPassportRejected("permit binding is invalid")
        permit_event_run_id = _require_text(permit.get("event_run_id"), "permit event run")
        permit_decision_sha256 = _require_sha256(permit.get("decision_sha256"), "permit decision")
        if permit_event_run_id != paper_event_run_id(permit_decision_sha256):
            raise PaperPassportRejected("permit event run does not bind its frozen decision")
        receipt_sha = _require_sha256(opening.get("receipt_sha256"), "opening receipt")
        if (
            opening.get("event_run_id") != permit.get("event_run_id")
            or opening.get("permit_id") != permit.get("permit_id")
            or not _is_sha256(opening.get("open_request_sha256"))
            or not _is_sha256(opening.get("open_order_sha256"))
        ):
            raise PaperPassportRejected("opening binding is invalid")
        if outcome == "CLOSED_FLAT":
            closing = _entry_payload(entries, 5)
            if (
                closing.get("event_run_id") != permit.get("event_run_id")
                or closing.get("open_permit_id") != permit.get("permit_id")
                or closing.get("receipt_sha256") != receipt_sha
                or not isinstance(closing.get("close_permit_id"), str)
                or not _is_sha256(closing.get("close_request_sha256"))
                or not _is_sha256(closing.get("close_order_sha256"))
            ):
                raise PaperPassportRejected("closing binding is invalid")
        classification = result.get("classification")
        paper_pnl = result.get("paper_pnl")
        if (
            reconciliation.get("event_run_id") != permit.get("event_run_id")
            or reconciliation.get("receipt_sha256") != receipt_sha
            or reconciliation.get("authority") != "SANITIZED_BROKER_RECEIPT"
            or reconciliation.get("lifecycle_outcome") != outcome
            or reconciliation.get("final_flat_observed_at")
            != entries[-2].at.strftime("%Y-%m-%dT%H:%M:%SZ")
            or result.get("event_run_id") != permit.get("event_run_id")
            or result.get("receipt_sha256") != receipt_sha
            or result.get("claims") != ["PAPER_OPERATIONAL_OBSERVATION", "NOT_ALPHA_EVIDENCE"]
            or classification not in {member.value for member in PaperPnlClass}
            or not isinstance(paper_pnl, Mapping)
            or paper_pnl.get("classification") != classification
        ):
            raise PaperPassportRejected("terminal receipt binding is invalid")
        if outcome == "CANCELED_FLAT" and classification != PaperPnlClass.ZERO_NO_FILL.value:
            raise PaperPassportRejected("CANCELED_FLAT passport cannot claim realized PAPER P&L")
        if outcome == "CLOSED_FLAT" and classification not in {
            PaperPnlClass.PAPER_REALIZED_PNL.value,
            PaperPnlClass.PAPER_PNL_UNAVAILABLE.value,
        }:
            raise PaperPassportRejected("CLOSED_FLAT passport has an invalid PAPER P&L class")
    except PaperPassportRejected as error:
        return _invalid(PaperPassportVerdictReason.BINDING_INVALID, str(error))
    return PaperPassportVerification(valid=True, reasons=(), details=())
