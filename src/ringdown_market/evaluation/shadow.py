"""Attributable prospective shadow ledger over the exact frozen Esscher v1 stack.

The orchestrator runs the frozen strategy, option compiler, and risk kernel end
to end without order authority, retaining every eligible event, abstention,
rejection, package decision, risk result, hypothetical hold, and limitation in
immutable records. Outcomes can never alter a frozen threshold, feature,
universe, baseline, contract-selection rule, or exit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ringdown_market.data.panel import ConfirmationPanel, panel_report_bytes
from ringdown_market.risk.policy import RISK_POLICY_SHA256, RISK_POLICY_VERSION
from ringdown_market.strategy.policy import STRATEGY_POLICY_VERSION, StrategyPolicy

SHADOW_REPORT_SCHEMA = "esscher.shadow_ledger_report"
SHADOW_REPORT_SCHEMA_VERSION = 1


class SampleClass(StrEnum):
    """Mechanical separation of event samples; never reassigned after recording."""

    DEVELOPMENT = "DEVELOPMENT"
    CONFIRMATION = "CONFIRMATION"
    PROSPECTIVE = "PROSPECTIVE"


class ShadowStage(StrEnum):
    """Frozen pipeline stages retained per shadow record."""

    SNAPSHOT = "SNAPSHOT"
    DECISION = "DECISION"
    PACKAGE = "PACKAGE"
    RISK = "RISK"
    EXIT = "EXIT"


class ShadowLedgerError(ValueError):
    """Raised when a shadow record violates the immutable ledger contract."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """One immutable stage result with stable disposition and reason codes."""

    stage: ShadowStage
    disposition: str
    reasons: tuple[str, ...]
    identity_sha256: str | None


@dataclass(frozen=True, slots=True)
class ShadowEventRecord:
    """One immutable event record spanning every frozen pipeline stage."""

    event_id: str
    sample_class: SampleClass
    snapshot_sha256: str
    recorded_at: datetime
    stages: tuple[StageOutcome, ...]

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ShadowLedgerError("event_id must be non-empty")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ShadowLedgerError("recorded_at must be timezone-aware")
        seen: set[ShadowStage] = set()
        for outcome in self.stages:
            if outcome.stage in seen:
                raise ShadowLedgerError(f"duplicate stage {outcome.stage.value}")
            seen.add(outcome.stage)

    @property
    def terminal_disposition(self) -> str:
        for outcome in reversed(self.stages):
            if outcome.disposition not in {"PASS", "NOT_RUN"}:
                return outcome.disposition
        return "COMPLETE"


class ShadowLedger:
    """Append-only ledger; recorded identities can never be overwritten."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ShadowEventRecord] = {}

    def record(self, record: ShadowEventRecord) -> None:
        identity = (record.event_id, record.sample_class.value)
        if identity in self._records:
            raise ShadowLedgerError(
                f"event {record.event_id} already recorded for {record.sample_class.value}"
            )
        self._records[identity] = record

    def records(self) -> tuple[ShadowEventRecord, ...]:
        return tuple(
            self._records[key] for key in sorted(self._records, key=lambda item: (item[0], item[1]))
        )

    def events_for(self, sample_class: SampleClass) -> tuple[ShadowEventRecord, ...]:
        return tuple(record for record in self.records() if record.sample_class is sample_class)


def _stage_payload(outcome: StageOutcome) -> dict[str, object]:
    return {
        "stage": outcome.stage.value,
        "disposition": outcome.disposition,
        "reasons": list(outcome.reasons),
        "identity_sha256": outcome.identity_sha256,
    }


def _record_payload(record: ShadowEventRecord) -> dict[str, object]:
    return {
        "event_id": record.event_id,
        "sample_class": record.sample_class.value,
        "snapshot_sha256": record.snapshot_sha256,
        "recorded_at": record.recorded_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stages": [_stage_payload(outcome) for outcome in record.stages],
        "terminal_disposition": record.terminal_disposition,
    }


def build_shadow_report(
    ledger: ShadowLedger,
    *,
    policy: StrategyPolicy,
    panel: ConfirmationPanel,
    historical_report: Mapping[str, object] | None,
    threshold_met: bool,
    limitations: Sequence[str],
) -> bytes:
    """Build the deterministic canonical shadow report with all policy hashes."""

    records = ledger.records()
    coverage_by_class = {sample.value: len(ledger.events_for(sample)) for sample in SampleClass}
    abstentions = sum(
        1
        for record in records
        for outcome in record.stages
        if outcome.stage is ShadowStage.DECISION and outcome.disposition == "ABSTAIN"
    )
    failures = sum(
        1
        for record in records
        if record.terminal_disposition in {"REJECTED", "NO_PACKAGE", "FAILED", "MANUAL_REQUIRED"}
    )
    historical_bytes = (
        panel_report_bytes(historical_report) if historical_report is not None else b""
    )
    report: dict[str, object] = {
        "schema": SHADOW_REPORT_SCHEMA,
        "schema_version": SHADOW_REPORT_SCHEMA_VERSION,
        "policy_version": STRATEGY_POLICY_VERSION,
        "policy_sha256": policy.sha256,
        "risk_policy_version": RISK_POLICY_VERSION,
        "risk_policy_sha256": RISK_POLICY_SHA256,
        "panel_list_id": panel.list_id,
        "panel_sha256": panel.sha256,
        "selection_rule_sha256": panel.selection_rule_sha256,
        "historical_report_sha256": hashlib.sha256(historical_bytes).hexdigest(),
        "records": [_record_payload(record) for record in records],
        "coverage_by_class": coverage_by_class,
        "abstention_count": abstentions,
        "failure_count": failures,
        "eligible_event_count": panel.eligible_count,
        "exclusion_count": len(panel.excluded),
        "preregistered_threshold": {
            "required": True,
            "met": threshold_met,
            "disposition": "MET" if threshold_met else "NOT_MET",
        },
        "limitations": list(limitations),
        "claim": "NOT_ALPHA_EVIDENCE",
        "data_qualifiers": ["INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION"],
    }
    return _canonical_json_bytes(report)


def shadow_report_sha256(report_bytes: bytes) -> str:
    """Return the SHA-256 of canonical shadow report bytes."""

    return hashlib.sha256(report_bytes).hexdigest()
