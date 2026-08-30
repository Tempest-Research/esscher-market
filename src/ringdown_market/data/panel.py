"""Untouched confirmation panel contract and deterministic Q-FAST report builder.

The panel is frozen before outcomes are inspected. Development events are
excluded by identity, abstentions remain in the eligible denominator, and a
panel below the preregistered floor reports INSUFFICIENT_DATA instead of
admitting synthetic or post-hoc assembled events.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ringdown_market.alpha.qfast import PanelRow, QFastReport, evaluate_latency_gate, run_qfast
from ringdown_market.strategy.policy import STRATEGY_POLICY_V1_SHA256

CONFIRMATION_PANEL_SCHEMA = "esscher.confirmation_panel_event_list"
CONFIRMATION_PANEL_SCHEMA_VERSION = 1
MINIMUM_ELIGIBLE_EVENTS = 20
MAXIMUM_ELIGIBLE_EVENTS = 30
REQUIRED_LATENCY_PROFILE = "p95"
ZERO_LATENCY_PROFILE = "zero"


class PanelRejectionReason(StrEnum):
    """Stable reasons a confirmation panel document cannot be accepted."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_VALUE = "INVALID_VALUE"
    DEVELOPMENT_EVENT_ADMITTED = "DEVELOPMENT_EVENT_ADMITTED"
    PANEL_SIZE_EXCEEDED = "PANEL_SIZE_EXCEEDED"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"


class PanelRejected(ValueError):
    """Raised when a confirmation panel fails the frozen contract."""

    def __init__(self, reason: PanelRejectionReason, path: str, detail: str) -> None:
        super().__init__(f"{reason.value} at {path}: {detail}")
        self.reason = reason
        self.path = path
        self.detail = detail


def _reject(reason: PanelRejectionReason, path: str, detail: str) -> None:
    raise PanelRejected(reason, path, detail)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


_PANEL_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "list_id",
        "frozen_at",
        "selection_rule_sha256",
        "policy_sha256",
        "panel_status",
        "event_ids",
        "events",
        "excluded_events",
        "stop_conditions",
        "limitations_note",
        "post_cutoff_paths",
        "outcome_fields",
    }
)

_EXCLUDED_FIELDS = frozenset({"event_id", "reason"})


@dataclass(frozen=True, slots=True)
class ConfirmationPanel:
    """One validated confirmation panel manifest view."""

    list_id: str
    event_ids: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]
    stop_conditions: tuple[str, ...]
    panel_status: str
    sha256: str
    selection_rule_sha256: str

    @property
    def eligible_count(self) -> int:
        return len(self.event_ids)


def parse_confirmation_panel(raw: bytes) -> ConfirmationPanel:
    """Parse strict confirmation-panel bytes; drift and leakage fail closed."""

    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise PanelRejected(
            PanelRejectionReason.INVALID_DOCUMENT, "panel", "panel bytes are required"
        )
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _reject(PanelRejectionReason.INVALID_DOCUMENT, "panel", f"invalid JSON: {error}")
    if not isinstance(payload, Mapping):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, "panel", "expected an object")
    for key in payload:
        if key not in _PANEL_FIELDS:
            _reject(PanelRejectionReason.UNKNOWN_FIELD, f"panel.{key}", "unknown field")
    for key in _PANEL_FIELDS:
        if key not in payload:
            _reject(PanelRejectionReason.MISSING_FIELD, f"panel.{key}", "missing required field")

    if payload["schema"] != CONFIRMATION_PANEL_SCHEMA:
        _reject(PanelRejectionReason.UNSUPPORTED_SCHEMA, "panel.schema", "unsupported schema")
    if payload["schema_version"] != CONFIRMATION_PANEL_SCHEMA_VERSION:
        _reject(
            PanelRejectionReason.UNSUPPORTED_SCHEMA,
            "panel.schema_version",
            "unsupported schema version",
        )
    if payload["policy_sha256"] != STRATEGY_POLICY_V1_SHA256:
        _reject(
            PanelRejectionReason.POLICY_HASH_MISMATCH,
            "panel.policy_sha256",
            "panel must bind the frozen Esscher v1 policy",
        )
    if payload["post_cutoff_paths"] != [] or payload["outcome_fields"] != []:
        _reject(
            PanelRejectionReason.INVALID_VALUE,
            "panel",
            "the frozen panel carries no post-cutoff paths or outcome fields",
        )

    list_id = payload["list_id"]
    if not isinstance(list_id, str) or not list_id.strip():
        _reject(PanelRejectionReason.INVALID_VALUE, "panel.list_id", "expected non-empty text")
    event_ids_value = payload["event_ids"]
    if not isinstance(event_ids_value, list):
        _reject(PanelRejectionReason.INVALID_VALUE, "panel.event_ids", "expected a list")
    event_ids: list[str] = []
    for index, item in enumerate(event_ids_value):
        if not isinstance(item, str) or not item.strip():
            _reject(
                PanelRejectionReason.INVALID_VALUE, f"panel.event_ids[{index}]", "expected text"
            )
        if item in event_ids:
            _reject(
                PanelRejectionReason.INVALID_VALUE, f"panel.event_ids[{index}]", "duplicate event"
            )
        event_ids.append(item)
    if len(event_ids) > MAXIMUM_ELIGIBLE_EVENTS:
        _reject(
            PanelRejectionReason.PANEL_SIZE_EXCEEDED,
            "panel.event_ids",
            f"panel exceeds {MAXIMUM_ELIGIBLE_EVENTS} events",
        )

    excluded_value = payload["excluded_events"]
    if not isinstance(excluded_value, list):
        _reject(PanelRejectionReason.INVALID_VALUE, "panel.excluded_events", "expected a list")
    excluded: list[tuple[str, str]] = []
    excluded_ids: set[str] = set()
    for index, item in enumerate(excluded_value):
        path = f"panel.excluded_events[{index}]"
        if not isinstance(item, Mapping):
            _reject(PanelRejectionReason.INVALID_VALUE, path, "expected an object")
        for key in item:
            if key not in _EXCLUDED_FIELDS:
                _reject(PanelRejectionReason.UNKNOWN_FIELD, f"{path}.{key}", "unknown field")
        for key in _EXCLUDED_FIELDS:
            if key not in item:
                _reject(PanelRejectionReason.MISSING_FIELD, f"{path}.{key}", "missing field")
        event_id = item["event_id"]
        reason = item["reason"]
        if not isinstance(event_id, str) or not isinstance(reason, str):
            _reject(PanelRejectionReason.INVALID_VALUE, path, "expected text identity")
        excluded.append((event_id, reason))
        excluded_ids.add(event_id)

    for event_id in event_ids:
        if event_id in excluded_ids:
            _reject(
                PanelRejectionReason.DEVELOPMENT_EVENT_ADMITTED,
                "panel.event_ids",
                f"excluded event {event_id} cannot be admitted",
            )

    stop_conditions_value = payload["stop_conditions"]
    if not isinstance(stop_conditions_value, list) or not all(
        isinstance(item, str) for item in stop_conditions_value
    ):
        _reject(PanelRejectionReason.INVALID_VALUE, "panel.stop_conditions", "expected text list")
    panel_status = payload["panel_status"]
    if not isinstance(panel_status, str) or not panel_status.strip():
        _reject(PanelRejectionReason.INVALID_VALUE, "panel.panel_status", "expected text")
    if len(event_ids) < MINIMUM_ELIGIBLE_EVENTS and panel_status != "COLLECTION_INCOMPLETE":
        _reject(
            PanelRejectionReason.INVALID_VALUE,
            "panel.panel_status",
            "a panel below the floor must declare COLLECTION_INCOMPLETE",
        )
    selection_rule_sha256 = payload["selection_rule_sha256"]
    if not isinstance(selection_rule_sha256, str) or len(selection_rule_sha256) != 64:
        _reject(
            PanelRejectionReason.INVALID_VALUE,
            "panel.selection_rule_sha256",
            "expected a sha256 hex digest",
        )
    return ConfirmationPanel(
        list_id=list_id,
        event_ids=tuple(event_ids),
        excluded=tuple(excluded),
        stop_conditions=tuple(str(item) for item in stop_conditions_value),
        panel_status=panel_status,
        sha256=hashlib.sha256(bytes(raw)).hexdigest(),
        selection_rule_sha256=selection_rule_sha256,
    )


def build_panel_report(
    panel: ConfirmationPanel,
    *,
    profile_rows: Mapping[str, Sequence[PanelRow]],
    minimum_events: int = MINIMUM_ELIGIBLE_EVENTS,
) -> dict[str, object]:
    """Build the deterministic zero-latency and p95 Q-FAST report for one panel."""

    if REQUIRED_LATENCY_PROFILE not in profile_rows or ZERO_LATENCY_PROFILE not in profile_rows:
        _reject(
            PanelRejectionReason.INVALID_VALUE,
            "profile_rows",
            "zero and p95 latency profiles are both required",
        )
    profiles: dict[str, QFastReport] = {}
    for name in sorted(profile_rows):
        profiles[name] = run_qfast(profile_rows[name], minimum_events=minimum_events)
    latency_gate = evaluate_latency_gate(profiles, required_profile=REQUIRED_LATENCY_PROFILE)
    return {
        "schema": "esscher.confirmation_panel_report",
        "schema_version": 1,
        "panel_list_id": panel.list_id,
        "panel_sha256": panel.sha256,
        "selection_rule_sha256": panel.selection_rule_sha256,
        "policy_sha256": STRATEGY_POLICY_V1_SHA256,
        "eligible_events": panel.eligible_count,
        "excluded_events": [
            {"event_id": event_id, "reason": reason} for event_id, reason in panel.excluded
        ],
        "panel_status": panel.panel_status,
        "stop_conditions": list(panel.stop_conditions),
        "profiles": {
            name: {
                "status": report.status.value,
                "claim": report.claim,
                "event_count": report.event_count,
                "strongest_baseline": report.strongest_baseline,
                "reject_reasons": list(report.reject_reasons),
            }
            for name, report in sorted(profiles.items())
        },
        "latency_gate": {
            "status": latency_gate.status.value,
            "required_profile": latency_gate.required_profile,
            "qfast_status": latency_gate.qfast_status.value,
        },
        "claim": "NOT_ALPHA_EVIDENCE",
        "data_qualifiers": ["INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE"],
    }


def panel_report_bytes(report: Mapping[str, object]) -> bytes:
    """Serialize one panel report to deterministic canonical bytes."""

    return _canonical_json_bytes(dict(report))


def panel_report_sha256(report: Mapping[str, object]) -> str:
    """Return the SHA-256 of the deterministic report bytes."""

    return hashlib.sha256(panel_report_bytes(report)).hexdigest()
