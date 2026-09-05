"""Deadline-aware application service with an identity-bound stage chain.

This module composes the real application path (frozen source compiler,
bounded decision engine, synthetic confirmation bridge, Gate D expression
compiler, V2 risk kernel, monitored PAPER lifecycle, synthetic in-memory
broker) behind one fail-closed stage chain for a single armed autonomous
window.  Every stage binds the SHA-256 of its identity-bound prerequisite
before running, every stage is measured against the derived stage budgets, and
deadline exhaustion fails closed before any new exposure while the bounded
close authority (lifecycle closer plus reconciler) remains callable.  Option
assignment, exercise, and expiry truth is reconciled through the frozen
option-event journal, whose atomic claims make entries, exits, and
conditional-event handling non-duplicable across retries and restarts.

Everything here is synthetic and deterministic: feeds are fixtures, the broker
is in-memory, clocks are injected, and every artifact is labelled
SYNTHETIC_FAKE / NOT_ALPHA_EVIDENCE / NOT_HISTORICAL_DATA.  No stage contacts
a provider, broker, account, or network.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Final, NoReturn

from esscher.application.autonomous_bridge import (
    RiskAbstentionRejected,
    SyntheticConfirmationAbstained,
)
from esscher.application.paper_pipeline import (
    ActivePaperLifecycle,
    CloseCriticalBinding,
    PaperPipelineRejected,
    PaperStrategyApplication,
    PreparedPaperLifecycle,
)
from esscher.autonomy.episodes import (
    GENESIS_SUMMARY_SHA256,
    DecisionEpisode,
    OutcomeEpisode,
    append_decision_episode,
    append_outcome_episode,
)
from esscher.contracts.execution_policy import ALPACA_MCP_PROTOCOL_SHA256
from esscher.execution.expression import PromotedExpressionPolicy
from esscher.lifecycle import (
    MULTI_LEG_ORDER_CLASS,
    PAPER_ACCOUNT_CLASS,
    LifecycleRejected,
    LifecycleState,
    MonitoredPaperLifecycle,
    issue_close_permit,
)
from esscher.lifecycle.broker import BrokerOutage
from esscher.risk import (
    RiskKernel,
    RiskLedger,
    RiskRejected,
    load_risk_policy_v2,
    risk_policy_v2_sha256,
)
from esscher.runtime.autonomous import (
    ActiveLifecycleIdentity,
    AutonomousClaimState,
    AutonomousOpportunity,
    AutonomousSessionStore,
    AutonomousStoreConflict,
    AutonomousWindow,
    DueWindowRequest,
    ReconciliationRequest,
    autonomous_session_arm_bytes,
)
from esscher.runtime.autonomous_host import (
    SyntheticBrokerTruth,
    ValidatedAutonomousHostAuthority,
    synthetic_broker_truth_bytes,
    synthetic_broker_truth_sha256,
)
from esscher.runtime.health_receipts import (
    CircuitState,
    OperationalHealthReceipt,
    SourceStaleness,
    build_operational_health_receipt,
    health_receipt_sha256,
)
from esscher.runtime.host_composition import (
    SYNTHETIC_CLOSE_DELAY,
    SYNTHETIC_CLOSE_LIMIT_PRICE,
    CompositionFeed,
    CompositionFeedEvent,
    CompositionRejected,
    SyntheticRehearsalClock,
    SyntheticRehearsalMutationGate,
    SyntheticRehearsalRoute,
    rehearsal_expression_snapshot,
    rehearsal_lifecycle_clocks,
    rehearsal_timeline,
    rejoin_composition_fixture,
    synthetic_promoted_expression_policy,
    terminal_flat_proof_sha256,
)
from esscher.runtime.host_fake_broker import (
    SYNTHETIC_PAPER_ACCOUNT_ID,
    SyntheticAccountTruthSource,
    SyntheticBrokerAmbiguousMutation,
    SyntheticPaperBroker,
)
from esscher.runtime.host_persistence import (
    HostPersistenceRejected,
    HostPersistenceSidecar,
)
from esscher.runtime.option_events import (
    NormalizedOptionEvent,
    OptionActivityCoverage,
    OptionEventConflict,
    OptionEventJournal,
    OptionEventReconciliationReceipt,
    OptionEventRejected,
    OptionLifecycleBinding,
    OptionPortfolioObservation,
    OptionReconciliationState,
    option_activity_coverage_bytes,
    option_portfolio_observation_bytes,
)
from esscher.runtime.stage_budgets import (
    StageBudgets,
    arm_window_set_sha256,
    stage_budgets_sha256,
    validate_stage_budgets_within_window,
)
from esscher.sourcedata import (
    CaptureConfiguration,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from esscher.sourcedata import capture as sourcedata_capture
from esscher.sourcedata.capture import (
    HOST_AUTHORIZATION_VALUE,
    HOST_AUTHORIZATION_VARIABLE,
)
from esscher.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
)
from esscher.sourcedata.reasons import CollectorRejected
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes
from esscher.strategy.reasoner import SYNTHETIC_ROUTE_IDENTITY

STAGE_RECEIPT_SCHEMA: Final = "esscher.application_stage_receipt"
STAGE_RECEIPT_SCHEMA_VERSION: Final = 1
SERVICE_TERMINAL_RECEIPT_SCHEMA: Final = "esscher.application_service_terminal_receipt"
SERVICE_TERMINAL_RECEIPT_SCHEMA_VERSION: Final = 1
EXPOSURE_STATE_SCHEMA: Final = "esscher.deterministic_exposure_state"
EXPOSURE_STATE_SCHEMA_VERSION: Final = 1
STAGE_OPEN_BINDING_SCHEMA: Final = "esscher.stage_open_binding"
STAGE_RECONCILIATION_BINDING_SCHEMA: Final = "esscher.stage_reconciliation_binding"
STAGE_TERMINAL_BINDING_SCHEMA: Final = "esscher.stage_terminal_binding"
SERVICE_CLAIMS: Final = ("SYNTHETIC_FAKE", "NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA")

OPTION_EVENT_JOURNAL_FILENAME: Final = "option_events.sqlite3"
CAPTURE_ARTIFACTS_DIRNAME: Final = "capture_artifacts"
FEED_FIXTURE_FILENAME: Final = "feed_fixture.json"
CAPTURE_IDENTITY_FILENAME: Final = "capture_identity.json"
EXPOSURE_ENTRY_KIND: Final = "EXPOSURE"
HOST_TRUTH_PHASE_WINDOW: Final = "WINDOW"
HOST_TRUTH_PHASE_CLOSE: Final = "CLOSE"
CLOSE_PERMIT_TTL: Final = timedelta(seconds=60)

DEFAULT_CAPTURE_CONDITIONS: Final = (
    "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
    "HUMAN_VERIFIED_CAPTURE",
    "PER_RECORD_PRIMARY_PROVENANCE",
)

STAGE_EVIDENCE_CAPTURE: Final = "EVIDENCE_CAPTURE"
STAGE_FEATURE_RECEIPT: Final = "FEATURE_RECEIPT"
STAGE_DECISION: Final = "DECISION"
STAGE_EXPRESSION: Final = "EXPRESSION"
STAGE_RISK: Final = "RISK"
STAGE_LIFECYCLE_OPEN: Final = "LIFECYCLE_OPEN"
STAGE_MONITORED_EXECUTION: Final = "MONITORED_EXECUTION"
STAGE_RECONCILIATION: Final = "RECONCILIATION"
STAGE_TERMINAL: Final = "TERMINAL"
STAGE_CLOSE_AUTHORITY: Final = "CLOSE_AUTHORITY"

STAGE_ORDER: Final = (
    STAGE_EVIDENCE_CAPTURE,
    STAGE_FEATURE_RECEIPT,
    STAGE_DECISION,
    STAGE_EXPRESSION,
    STAGE_RISK,
    STAGE_LIFECYCLE_OPEN,
    STAGE_MONITORED_EXECUTION,
    STAGE_RECONCILIATION,
    STAGE_TERMINAL,
)

STAGE_BUDGET_FIELDS: Final = {
    STAGE_EVIDENCE_CAPTURE: "market_data_ms",
    STAGE_FEATURE_RECEIPT: "market_data_ms",
    STAGE_DECISION: "reasoner_ms",
    STAGE_EXPRESSION: "reasoner_ms",
    STAGE_RISK: "reasoner_ms",
    STAGE_LIFECYCLE_OPEN: "broker_ms",
    STAGE_MONITORED_EXECUTION: "broker_ms",
    STAGE_RECONCILIATION: "broker_ms",
    STAGE_TERMINAL: "shutdown_reserve_ms",
    STAGE_CLOSE_AUTHORITY: "shutdown_reserve_ms",
}

REASON_DEADLINE_EXHAUSTED: Final = "DEADLINE_EXHAUSTED"
REASON_DEADLINE_EXHAUSTED_BEFORE_MUTATION: Final = "DEADLINE_EXHAUSTED_BEFORE_MUTATION"
REASON_MANUAL_RECONCILIATION_STICKY: Final = "MANUAL_RECONCILIATION_STICKY"
REASON_UPSTREAM_STOPPED: Final = "UPSTREAM_STOPPED"
REASON_CAPTURE_REJECTED: Final = "CAPTURE_REJECTED"
REASON_FEATURE_RECEIPT_REJECTED: Final = "FEATURE_RECEIPT_REJECTED"
REASON_DECISION_ABSTAINED: Final = "DECISION_ABSTAINED"
REASON_DECISION_REJECTED: Final = "DECISION_REJECTED"
REASON_DECISION_RISK_FROZEN: Final = "DECISION_RISK_FROZEN"
REASON_EXPRESSION_UNBOUND: Final = "EXPRESSION_UNBOUND"
REASON_RISK_APPROVAL_UNBOUND: Final = "RISK_APPROVAL_UNBOUND"
REASON_BUDGET_VIOLATION: Final = "BUDGET_VIOLATION"
REASON_OPEN_AMBIGUOUS_BROKER_STATE: Final = "OPEN_AMBIGUOUS_BROKER_STATE"
REASON_OPEN_FAILED: Final = "OPEN_FAILED"
REASON_EXECUTION_STATE_INVALID: Final = "EXECUTION_STATE_INVALID"
REASON_BROKER_TRUTH_INCOMPLETE: Final = "BROKER_TRUTH_INCOMPLETE"
REASON_BROKER_TRUTH_AMBIGUOUS: Final = "BROKER_TRUTH_AMBIGUOUS"
REASON_RECONCILIATION_MANUAL_REQUIRED: Final = "RECONCILIATION_MANUAL_REQUIRED"
REASON_RECONCILIATION_INPUT_REJECTED: Final = "RECONCILIATION_INPUT_REJECTED"
REASON_RECONCILIATION_STATE_CONFLICT: Final = "RECONCILIATION_STATE_CONFLICT"
REASON_CLOSE_FAILED: Final = "CLOSE_FAILED"

MANUAL_CIRCUIT_REASONS: Final = frozenset(
    {
        REASON_MANUAL_RECONCILIATION_STICKY,
        REASON_OPEN_AMBIGUOUS_BROKER_STATE,
        REASON_OPEN_FAILED,
        REASON_EXECUTION_STATE_INVALID,
        REASON_BROKER_TRUTH_INCOMPLETE,
        REASON_BROKER_TRUTH_AMBIGUOUS,
        REASON_RECONCILIATION_MANUAL_REQUIRED,
        REASON_RECONCILIATION_INPUT_REJECTED,
        REASON_RECONCILIATION_STATE_CONFLICT,
        REASON_CLOSE_FAILED,
    }
)
FROZEN_CIRCUIT_REASONS: Final = frozenset({REASON_DECISION_RISK_FROZEN})

_FALLBACK_STAGE_REASONS: Final = {
    STAGE_EVIDENCE_CAPTURE: REASON_CAPTURE_REJECTED,
    STAGE_FEATURE_RECEIPT: REASON_FEATURE_RECEIPT_REJECTED,
    STAGE_DECISION: REASON_DECISION_REJECTED,
    STAGE_EXPRESSION: REASON_EXPRESSION_UNBOUND,
    STAGE_RISK: REASON_RISK_APPROVAL_UNBOUND,
    STAGE_LIFECYCLE_OPEN: REASON_OPEN_FAILED,
    STAGE_MONITORED_EXECUTION: REASON_EXECUTION_STATE_INVALID,
    STAGE_RECONCILIATION: REASON_RECONCILIATION_STATE_CONFLICT,
    STAGE_TERMINAL: REASON_RECONCILIATION_STATE_CONFLICT,
}

STALENESS_FEED_CAPTURE: Final = "FEED_CAPTURE"
STALENESS_OPTION_OBSERVATION: Final = "OPTION_OBSERVATION"
STALENESS_BROKER_TRUTH: Final = "BROKER_TRUTH"

_ZERO_SHA256: Final = "0" * 64


class ApplicationServiceRejected(ValueError):
    """Raised when service construction, requests, or stage inputs are invalid."""


class ApplicationServiceStopped(RuntimeError):
    """Raised when one identity-bound stage stops the chain; nothing runs after."""

    def __init__(
        self,
        message: str,
        *,
        stage_receipt: StageReceipt,
        health_receipt: OperationalHealthReceipt,
        stage_chain: Sequence[StageReceipt] = (),
    ) -> None:
        super().__init__(message)
        self.stage_receipt = stage_receipt
        self.health_receipt = health_receipt
        self.stage_chain = tuple(stage_chain)


class RunDisposition(StrEnum):
    """The only window-run dispositions the service may report."""

    COMPLETED = "COMPLETED"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    STOPPED = "STOPPED"


class StageStatus(StrEnum):
    """The only stage outcomes representable in one stage receipt."""

    OK = "OK"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class _StageWorkFailure(Exception):
    """Internal typed carrier for one stage's deterministic failure reason."""

    def __init__(self, reason_code: str, *, degradation: Sequence[str] = ()) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.degradation = tuple(degradation)


def _utc(value: object, *, path: str) -> datetime:
    """Normalize one injected instant to aware UTC or fail closed."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ApplicationServiceRejected(f"{path} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    """Render one aware instant as canonical UTC text."""

    return _utc(value, path="timestamp").isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, path: str) -> datetime:
    """Parse one stored canonical UTC timestamp."""

    if not isinstance(value, str) or not value.endswith("Z"):
        raise ApplicationServiceRejected(f"{path} must be canonical UTC text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ApplicationServiceRejected(f"{path} must be canonical UTC text") from error
    return _utc(parsed, path=path)


def _digest(value: object, *, path: str) -> str:
    """Validate one lowercase SHA-256 digest."""

    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise ApplicationServiceRejected(f"{path} must be a lowercase SHA-256 digest")
    return value


def _text(value: object, *, path: str) -> str:
    """Validate one non-empty exact text value."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ApplicationServiceRejected(f"{path} must be non-empty exact text")
    return value


def _decimal_text(value: Decimal) -> str:
    """Render one finite Decimal as canonical text."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise ApplicationServiceRejected("exposure quantities must be finite Decimals")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _elapsed_ms(started: datetime, finished: datetime) -> int:
    """Return the exact whole-millisecond span between two instants."""

    delta = finished - started
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def _floor_seconds(delta: timedelta) -> int:
    """Return the floored whole-second count of one timedelta."""

    return delta // timedelta(seconds=1)


def _context_lag(context: Mapping[str, object]) -> int | None:
    """Return the recorded reconciliation lag, if one journal claim occurred."""

    lag = context.get("reconciliation_lag_ms")
    return None if lag is None else int(lag)


def _context_option_receipts(context: Mapping[str, object]) -> tuple[str, ...]:
    """Return the recorded option reconciliation receipt identities."""

    receipts = context.get("option_receipt_sha256s")
    if receipts is None:
        return ()
    return tuple(sorted(str(item) for item in receipts))  # type: ignore[union-attr]


@dataclass(frozen=True, slots=True)
class StageReceipt:
    """One content-addressed stage outcome bound to its prerequisite receipt."""

    stage: str
    prior_stage_sha256: str
    input_sha256: str
    output_sha256: str
    started_at: datetime
    finished_at: datetime
    budget_ms: int
    status: StageStatus
    reason_code: str | None

    def to_json_bytes(self) -> bytes:
        """Serialize this stage receipt as exact canonical JSON bytes."""

        return stage_receipt_bytes(self)


def stage_receipt_payload(value: StageReceipt) -> dict[str, object]:
    """Return the validated canonical payload of one stage receipt."""

    if type(value) is not StageReceipt:
        raise ApplicationServiceRejected("receipt must be a StageReceipt")
    if value.stage not in STAGE_BUDGET_FIELDS:
        raise ApplicationServiceRejected("stage receipt carries an unknown stage name")
    if not isinstance(value.status, StageStatus):
        raise ApplicationServiceRejected("stage receipt status is invalid")
    started = _utc(value.started_at, path="stage_receipt.started_at")
    finished = _utc(value.finished_at, path="stage_receipt.finished_at")
    if finished < started:
        raise ApplicationServiceRejected("stage receipt clock moved backwards")
    if type(value.budget_ms) is not int or value.budget_ms < 0:
        raise ApplicationServiceRejected("stage receipt budget must be a non-negative integer")
    reason = value.reason_code
    if reason is not None:
        reason = _text(reason, path="stage_receipt.reason_code")
    if value.status is StageStatus.OK and reason is not None:
        raise ApplicationServiceRejected("an OK stage receipt carries no reason code")
    if value.status is not StageStatus.OK and reason is None:
        raise ApplicationServiceRejected("a stopped stage receipt requires a reason code")
    return {
        "budget_ms": value.budget_ms,
        "finished_at": _timestamp_text(finished),
        "input_sha256": _digest(value.input_sha256, path="stage_receipt.input_sha256"),
        "output_sha256": _digest(value.output_sha256, path="stage_receipt.output_sha256"),
        "prior_stage_sha256": _digest(
            value.prior_stage_sha256,
            path="stage_receipt.prior_stage_sha256",
        ),
        "reason_code": reason,
        "schema": STAGE_RECEIPT_SCHEMA,
        "schema_version": STAGE_RECEIPT_SCHEMA_VERSION,
        "stage": value.stage,
        "started_at": _timestamp_text(started),
        "status": value.status.value,
    }


def stage_receipt_bytes(value: StageReceipt) -> bytes:
    """Serialize one stage receipt as exact canonical JSON bytes."""

    return canonical_json_bytes(stage_receipt_payload(value))


def stage_receipt_sha256(value: StageReceipt) -> str:
    """Content-address one stage receipt."""

    return sha256_bytes(stage_receipt_bytes(value))


def verify_stage_receipt_chain(
    receipts: Sequence[StageReceipt],
    *,
    arm_sha256: str,
) -> None:
    """Fail closed unless every receipt binds its exact prerequisite identity."""

    arm = _digest(arm_sha256, path="arm_sha256")
    if len(receipts) != len(STAGE_ORDER):
        raise ApplicationServiceRejected("stage chain must cover every ordered stage exactly once")
    prior = arm
    for index, receipt in enumerate(receipts):
        if receipt.stage != STAGE_ORDER[index]:
            raise ApplicationServiceRejected("stage chain is out of order")
        stage_receipt_bytes(receipt)
        if receipt.prior_stage_sha256 != prior:
            raise ApplicationServiceRejected(
                f"stage {receipt.stage} does not bind its prerequisite receipt identity"
            )
        prior = stage_receipt_sha256(receipt)


@dataclass(frozen=True, slots=True)
class WindowOptionActivityFeed:
    """Feed-provided option reconciliation inputs bound to one armed window."""

    window_id: str
    activation_observation: OptionPortfolioObservation
    current_observation: OptionPortfolioObservation
    activity_coverage: OptionActivityCoverage
    events: tuple[NormalizedOptionEvent, ...]
    expiration_session_date: date
    expiration_session_close: datetime
    expiration_activity_horizon: datetime
    calendar_sha256: str

    def __post_init__(self) -> None:
        """Validate every embedded canonical option-event contract."""

        _text(self.window_id, path="option_feed.window_id")
        if type(self.activation_observation) is not OptionPortfolioObservation:
            raise ApplicationServiceRejected("option_feed.activation_observation is invalid")
        if type(self.current_observation) is not OptionPortfolioObservation:
            raise ApplicationServiceRejected("option_feed.current_observation is invalid")
        if type(self.activity_coverage) is not OptionActivityCoverage:
            raise ApplicationServiceRejected("option_feed.activity_coverage is invalid")
        option_portfolio_observation_bytes(self.activation_observation)
        option_portfolio_observation_bytes(self.current_observation)
        option_activity_coverage_bytes(self.activity_coverage)
        if type(self.events) is not tuple:
            raise ApplicationServiceRejected("option_feed.events must be an immutable tuple")
        for event in self.events:
            if type(event) is not NormalizedOptionEvent:
                raise ApplicationServiceRejected("option_feed.events must be normalized events")
        if not isinstance(self.expiration_session_date, date) or isinstance(
            self.expiration_session_date, datetime
        ):
            raise ApplicationServiceRejected("option_feed.expiration_session_date must be a date")
        _utc(self.expiration_session_close, path="option_feed.expiration_session_close")
        _utc(self.expiration_activity_horizon, path="option_feed.expiration_activity_horizon")
        _digest(self.calendar_sha256, path="option_feed.calendar_sha256")


@dataclass(frozen=True, slots=True)
class ExposureState:
    """One deterministic recomputed exposure entry persisted in the sidecar."""

    session_id: str
    lifecycle_id: str
    observed_at: datetime
    open_reservation_total: Decimal
    option_long_quantity: Decimal
    option_short_quantity: Decimal
    underlying_quantity_delta: Decimal
    event_cash_delta: Decimal
    option_receipt_sha256: str | None
    exposure_sha256: str


def exposure_state_payload(value: ExposureState) -> dict[str, object]:
    """Return the canonical unsigned payload whose hash is the exposure identity."""

    if type(value) is not ExposureState:
        raise ApplicationServiceRejected("exposure must be an ExposureState")
    receipt_sha = value.option_receipt_sha256
    return {
        "claims": list(SERVICE_CLAIMS),
        "event_cash_delta": _decimal_text(value.event_cash_delta),
        "lifecycle_id": _text(value.lifecycle_id, path="exposure.lifecycle_id"),
        "observed_at": _timestamp_text(value.observed_at),
        "open_reservation_total": _decimal_text(value.open_reservation_total),
        "option_long_quantity": _decimal_text(value.option_long_quantity),
        "option_receipt_sha256": (
            None if receipt_sha is None else _digest(receipt_sha, path="exposure.receipt_sha256")
        ),
        "option_short_quantity": _decimal_text(value.option_short_quantity),
        "schema": EXPOSURE_STATE_SCHEMA,
        "schema_version": EXPOSURE_STATE_SCHEMA_VERSION,
        "session_id": _text(value.session_id, path="exposure.session_id"),
        "underlying_quantity_delta": _decimal_text(value.underlying_quantity_delta),
    }


def exposure_state_sha256(value: ExposureState) -> str:
    """Content-address one deterministic exposure state."""

    return sha256_bytes(canonical_json_bytes(exposure_state_payload(value)))


EXPOSURE_JOURNAL_FILENAME: Final = "exposure_state.jsonl"
EXPOSURE_JOURNAL_SCHEMA: Final = "esscher.exposure_journal_entry"
EXPOSURE_JOURNAL_SCHEMA_VERSION: Final = 1
EXPOSURE_JOURNAL_GENESIS: Final = "0" * 64


def _exposure_journal_path(state_dir: Path) -> Path:
    return state_dir / EXPOSURE_JOURNAL_FILENAME


def _exposure_journal_entries(path: Path) -> tuple[dict[str, object], ...]:
    """Read and verify the service-owned exposure hash chain."""

    if not path.is_file():
        return ()
    entries: list[dict[str, object]] = []
    prior = EXPOSURE_JOURNAL_GENESIS
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise ApplicationServiceRejected(
                f"exposure journal line {line_number} is invalid"
            ) from error
        if not isinstance(entry, dict):
            raise ApplicationServiceRejected(
                f"exposure journal line {line_number} is not an object"
            )
        if (
            entry.get("schema") != EXPOSURE_JOURNAL_SCHEMA
            or entry.get("schema_version") != EXPOSURE_JOURNAL_SCHEMA_VERSION
        ):
            raise ApplicationServiceRejected(
                f"exposure journal line {line_number} has an unsupported schema"
            )
        if entry.get("prior_entry_sha256") != prior:
            raise ApplicationServiceRejected(
                f"exposure journal line {line_number} breaks the hash chain"
            )
        unsigned = {key: value for key, value in entry.items() if key != "entry_sha256"}
        digest = sha256_bytes(canonical_json_bytes(unsigned))
        if entry.get("entry_sha256") != digest:
            raise ApplicationServiceRejected(f"exposure journal line {line_number} hash is invalid")
        prior = digest
        entries.append(entry)
    return tuple(entries)


def _exposure_journal_append(
    path: Path,
    *,
    session_id: str,
    lifecycle_id: str,
    recorded_at: datetime,
    payload: Mapping[str, object],
) -> str:
    """Append one tamper-evident exposure entry to the service-owned journal."""

    entries = _exposure_journal_entries(path)
    prior = entries[-1]["entry_sha256"] if entries else EXPOSURE_JOURNAL_GENESIS
    entry: dict[str, object] = {
        "schema": EXPOSURE_JOURNAL_SCHEMA,
        "schema_version": EXPOSURE_JOURNAL_SCHEMA_VERSION,
        "kind": EXPOSURE_ENTRY_KIND,
        "session_id": session_id,
        "lifecycle_id": lifecycle_id,
        "recorded_at": _timestamp_text(recorded_at),
        "prior_entry_sha256": prior,
        "payload": dict(payload),
    }
    digest = sha256_bytes(canonical_json_bytes(entry))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps({**entry, "entry_sha256": digest}, sort_keys=True, separators=(",", ":"))
            + "\n"
        )
    return digest


@dataclass(frozen=True, slots=True)
class WindowRunResult:
    """The closed result of one run_window call."""

    disposition: RunDisposition
    window_id: str
    opportunity_id: str
    lifecycle_id: str | None
    stage_receipts: tuple[StageReceipt, ...]
    health_receipt: OperationalHealthReceipt
    option_receipt_sha256s: tuple[str, ...]
    exposure_sha256: str | None


@dataclass(frozen=True, slots=True)
class CloseAuthorityResult:
    """The closed result of one bounded close-authority call."""

    lifecycle_id: str
    terminal_flat_proof_sha256: str
    broker_truth_sha256: str
    closed_at: datetime
    outcome_episode_id: str


@dataclass(frozen=True, slots=True)
class WindowTerminalRecord:
    """One window run bound into the service terminal receipt."""

    window_id: str
    opportunity_id: str
    disposition: RunDisposition
    stage_receipt_sha256s: tuple[str, ...]
    health_receipt_sha256: str
    option_receipt_sha256s: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CloseTerminalRecord:
    """One bounded close bound into the service terminal receipt."""

    lifecycle_id: str
    terminal_flat_proof_sha256: str
    broker_truth_sha256: str
    closed_at: datetime


@dataclass(frozen=True, slots=True)
class ServiceTerminalReceipt:
    """The canonical service receipt binding every stage, health, and option sha."""

    session_id: str
    arm_sha256: str
    windows: tuple[WindowTerminalRecord, ...]
    closes: tuple[CloseTerminalRecord, ...]
    terminal_receipt_sha256: str

    def to_json_bytes(self) -> bytes:
        """Serialize this terminal receipt as exact canonical JSON bytes."""

        return service_terminal_receipt_bytes(self)


def _window_terminal_payload(value: WindowTerminalRecord) -> dict[str, object]:
    """Return the validated canonical payload of one window terminal record."""

    if type(value) is not WindowTerminalRecord:
        raise ApplicationServiceRejected("window record must be a WindowTerminalRecord")
    if not isinstance(value.disposition, RunDisposition):
        raise ApplicationServiceRejected("window record disposition is invalid")
    return {
        "disposition": value.disposition.value,
        "health_receipt_sha256": _digest(
            value.health_receipt_sha256,
            path="window_record.health_receipt_sha256",
        ),
        "opportunity_id": _text(value.opportunity_id, path="window_record.opportunity_id"),
        "option_receipt_sha256s": [
            _digest(item, path="window_record.option_receipt_sha256s")
            for item in value.option_receipt_sha256s
        ],
        "stage_receipt_sha256s": [
            _digest(item, path="window_record.stage_receipt_sha256s")
            for item in value.stage_receipt_sha256s
        ],
        "window_id": _text(value.window_id, path="window_record.window_id"),
    }


def _close_terminal_payload(value: CloseTerminalRecord) -> dict[str, object]:
    """Return the validated canonical payload of one close terminal record."""

    if type(value) is not CloseTerminalRecord:
        raise ApplicationServiceRejected("close record must be a CloseTerminalRecord")
    return {
        "broker_truth_sha256": _digest(
            value.broker_truth_sha256,
            path="close_record.broker_truth_sha256",
        ),
        "closed_at": _timestamp_text(value.closed_at),
        "lifecycle_id": _text(value.lifecycle_id, path="close_record.lifecycle_id"),
        "terminal_flat_proof_sha256": _digest(
            value.terminal_flat_proof_sha256,
            path="close_record.terminal_flat_proof_sha256",
        ),
    }


def service_terminal_receipt_unsigned_payload(value: ServiceTerminalReceipt) -> dict[str, object]:
    """Return the validated canonical payload excluding the self hash."""

    if type(value) is not ServiceTerminalReceipt:
        raise ApplicationServiceRejected("receipt must be a ServiceTerminalReceipt")
    return {
        "arm_sha256": _digest(value.arm_sha256, path="terminal.arm_sha256"),
        "claims": list(SERVICE_CLAIMS),
        "closes": [_close_terminal_payload(item) for item in value.closes],
        "schema": SERVICE_TERMINAL_RECEIPT_SCHEMA,
        "schema_version": SERVICE_TERMINAL_RECEIPT_SCHEMA_VERSION,
        "session_id": _text(value.session_id, path="terminal.session_id"),
        "windows": [_window_terminal_payload(item) for item in value.windows],
    }


def service_terminal_receipt_bytes(value: ServiceTerminalReceipt) -> bytes:
    """Serialize one service terminal receipt as exact canonical JSON bytes."""

    unsigned = service_terminal_receipt_unsigned_payload(value)
    expected = sha256_bytes(canonical_json_bytes(unsigned))
    if value.terminal_receipt_sha256 != expected:
        raise ApplicationServiceRejected("terminal receipt hash does not bind its payload")
    return canonical_json_bytes({**unsigned, "terminal_receipt_sha256": expected})


def service_terminal_receipt_sha256(value: ServiceTerminalReceipt) -> str:
    """Content-address one service terminal receipt over its unsigned payload."""

    return sha256_bytes(canonical_json_bytes(service_terminal_receipt_unsigned_payload(value)))


class AutonomousApplicationService:
    """One deadline-aware, identity-bound application service per armed session."""

    def __init__(
        self,
        *,
        authority: ValidatedAutonomousHostAuthority,
        feed: CompositionFeed,
        broker: SyntheticPaperBroker,
        ledger: RiskLedger,
        sidecar: HostPersistenceSidecar,
        budgets: StageBudgets,
        clock: Callable[[], datetime],
        option_events: Sequence[WindowOptionActivityFeed] = (),
        application_factory: Callable[..., PaperStrategyApplication] | None = None,
        expression_policy: PromotedExpressionPolicy | None = None,
        reconciliation_fault: str | None = None,
        capture_conditions: Sequence[str] = DEFAULT_CAPTURE_CONDITIONS,
    ) -> None:
        """Validate the complete authority graph and open the durable journals."""

        if type(authority) is not ValidatedAutonomousHostAuthority:
            raise ApplicationServiceRejected("authority must be a validated host authority")
        if type(feed) is not CompositionFeed:
            raise ApplicationServiceRejected("feed must be a CompositionFeed")
        if type(broker) is not SyntheticPaperBroker:
            raise ApplicationServiceRejected("broker must be the synthetic PAPER broker")
        if type(ledger) is not RiskLedger:
            raise ApplicationServiceRejected("ledger must be a RiskLedger")
        if type(sidecar) is not HostPersistenceSidecar:
            raise ApplicationServiceRejected("sidecar must be a HostPersistenceSidecar")
        if type(budgets) is not StageBudgets:
            raise ApplicationServiceRejected("budgets must be a StageBudgets")
        if not callable(clock):
            raise ApplicationServiceRejected("clock must be an injected callable")
        if reconciliation_fault is not None and reconciliation_fault not in {
            "INCOMPLETE",
            "AMBIGUOUS",
        }:
            raise ApplicationServiceRejected("unsupported reconciliation fault")
        arm = authority.session_arm
        if authority.account_fingerprint_sha256 != broker.account_state_sha256():
            raise ApplicationServiceRejected("ACCOUNT_FINGERPRINT_MISMATCH")
        policy = load_risk_policy_v2()
        if risk_policy_v2_sha256(policy) != arm.risk_policy_sha256:
            raise ApplicationServiceRejected("RISK_POLICY_MISMATCH")
        if budgets.arm_window_sha256 != arm_window_set_sha256(arm):
            raise ApplicationServiceRejected("STAGE_BUDGETS_ARM_MISMATCH")
        validate_stage_budgets_within_window(budgets, arm)
        if not isinstance(option_events, (tuple, list)):
            raise ApplicationServiceRejected("option_events must be a sequence of window feeds")
        feeds: dict[str, WindowOptionActivityFeed] = {}
        window_ids = {window.window_id for window in arm.windows}
        for bundle in option_events:
            if type(bundle) is not WindowOptionActivityFeed:
                raise ApplicationServiceRejected(
                    "option_events entries must be WindowOptionActivityFeed values"
                )
            if bundle.window_id not in window_ids:
                raise ApplicationServiceRejected("option feed references an unarmed window")
            if bundle.window_id in feeds:
                raise ApplicationServiceRejected("option feeds must be unique per window")
            feeds[bundle.window_id] = bundle
        conditions = tuple(_text(item, path="capture_conditions") for item in capture_conditions)
        self._authority = authority
        self._feed = feed
        self._broker = broker
        self._ledger = ledger
        self._sidecar = sidecar
        self._budgets = budgets
        self._clock = clock
        self._option_feeds = feeds
        self._application_factory = application_factory
        self._expression_policy = expression_policy or synthetic_promoted_expression_policy()
        self._reconciliation_fault = reconciliation_fault
        self._capture_conditions = conditions
        self._risk_policy = policy
        self._rehearsal_clock = SyntheticRehearsalClock()
        self._broker.clock = self._rehearsal_clock.now
        self._journal = OptionEventJournal(authority.state_dir / OPTION_EVENT_JOURNAL_FILENAME)
        self._store = AutonomousSessionStore(authority.store_path)
        self._duplicate_suppressions = 0
        self._last_stage_clock: datetime | None = None
        self._window_runs: list[WindowTerminalRecord] = []
        self._closes: list[CloseTerminalRecord] = []

    def __enter__(self) -> AutonomousApplicationService:
        """Support deterministic context-managed lifetime."""

        return self

    def __exit__(self, *_: object) -> None:
        """Close the durable journal and session store."""

        self.close()

    def close(self) -> None:
        """Close the durable option-event journal and autonomous session store."""

        self._journal.close()
        self._store.close()

    @property
    def option_journal(self) -> OptionEventJournal:
        """Return the durable option-event journal owned by this service."""

        return self._journal

    @property
    def session_store(self) -> AutonomousSessionStore:
        """Return the durable autonomous session store owned by this service."""

        return self._store

    @property
    def budgets(self) -> StageBudgets:
        """Return the derived stage budgets bound to this service."""

        return self._budgets

    @property
    def duplicate_suppressions(self) -> int:
        """Return the cumulative duplicate window suppression count."""

        return self._duplicate_suppressions

    def _now(self) -> datetime:
        """Read the injected stage clock and enforce monotonic motion."""

        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ApplicationServiceRejected("injected clock must return timezone-aware values")
        normalized = value.astimezone(UTC)
        if self._last_stage_clock is not None and normalized < self._last_stage_clock:
            raise ApplicationServiceRejected("injected stage clock moved backwards")
        self._last_stage_clock = normalized
        return normalized

    def _window(self, requested: AutonomousWindow) -> AutonomousWindow:
        """Return the exact armed window matching one request window."""

        arm = self._authority.session_arm
        if type(requested) is not AutonomousWindow:
            raise ApplicationServiceRejected("request window must be an AutonomousWindow")
        matches = tuple(window for window in arm.windows if window.window_id == requested.window_id)
        if len(matches) != 1 or matches[0].window_sha256 != requested.window_sha256:
            raise ApplicationServiceRejected("request window is not the exact armed window")
        return matches[0]

    def _feed_event(self, window: AutonomousWindow) -> CompositionFeedEvent:
        """Return the sole feed event bound to one armed window."""

        matches = tuple(event for event in self._feed.events if event.window_id == window.window_id)
        if len(matches) != 1:
            raise ApplicationServiceRejected("window must carry exactly one feed event")
        event = matches[0]
        if event.candidate_id not in window.candidate_ids:
            raise ApplicationServiceRejected("feed event lane is not armed for this window")
        return event

    def _application(self, kernel: RiskKernel) -> PaperStrategyApplication:
        """Build the real application service or the injected factory override."""

        if self._application_factory is not None:
            return self._application_factory(kernel=kernel)
        return PaperStrategyApplication(
            reasoner_route=SyntheticRehearsalRoute(),
            expression_policy=self._expression_policy,
            risk_kernel=kernel,
            risk_policy_sha256=risk_policy_v2_sha256(),
            gate_d_report_sha256=self._expression_policy.gate_d_report_sha256,
            execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
            lifecycle_clocks=rehearsal_lifecycle_clocks,
            account_id=SYNTHETIC_PAPER_ACCOUNT_ID,
            route_identity=SYNTHETIC_ROUTE_IDENTITY,
        )

    def _rehearsal_current(self) -> datetime | None:
        """Return the current rehearsal instant, or None before it is first set."""

        try:
            return self._rehearsal_clock.now()
        except CompositionRejected:
            return None

    def _sticky_manual(self) -> bool:
        """Return the durable sticky manual-reconciliation gate for this account."""

        arm = self._authority.session_arm
        return self._journal.account_requires_manual_reconciliation(
            session_id=arm.session_id,
            account_fingerprint_sha256=arm.account_fingerprint_sha256,
        )

    def _circuit_for(self, reason: str | None) -> CircuitState:
        """Map one stopping reason plus durable state to the explicit circuit."""

        if reason is not None and reason in FROZEN_CIRCUIT_REASONS:
            return CircuitState.FROZEN
        if reason is not None and reason in MANUAL_CIRCUIT_REASONS:
            return CircuitState.MANUAL_REQUIRED
        if self._sticky_manual():
            return CircuitState.MANUAL_REQUIRED
        return CircuitState.NOMINAL

    def _staleness(
        self,
        *,
        observed_at: datetime,
        event: CompositionFeedEvent,
        bundle: WindowOptionActivityFeed | None,
    ) -> tuple[SourceStaleness, ...]:
        """Compute per-source truth ages against the frozen V2 policy maximum."""

        max_age = self._risk_policy.truth_max_age_seconds
        capture_age = _floor_seconds(observed_at - event.capture_at.astimezone(UTC))
        entries = [
            SourceStaleness(
                source_id=STALENESS_FEED_CAPTURE,
                age_seconds=capture_age,
                max_age_seconds=max_age,
                stale=capture_age > max_age,
            )
        ]
        if bundle is not None:
            option_age = _floor_seconds(observed_at - bundle.current_observation.observed_at)
            entries.append(
                SourceStaleness(
                    source_id=STALENESS_OPTION_OBSERVATION,
                    age_seconds=option_age,
                    max_age_seconds=max_age,
                    stale=option_age > max_age,
                )
            )
        entries.append(
            SourceStaleness(
                source_id=STALENESS_BROKER_TRUTH,
                age_seconds=0,
                max_age_seconds=max_age,
                stale=False,
            )
        )
        return tuple(entries)

    def _health(
        self,
        *,
        run_id: str,
        event: CompositionFeedEvent,
        observed_at: datetime,
        latencies: Mapping[str, int],
        violations: Sequence[str],
        degradation: Sequence[str],
        lag_ms: int | None,
        circuit: CircuitState,
    ) -> OperationalHealthReceipt:
        """Freeze one operational health receipt for a window run."""

        bundle = self._option_feeds.get(event.window_id)
        return build_operational_health_receipt(
            run_id=run_id,
            arm_sha256=self._authority.session_arm.arm_sha256,
            observed_at=observed_at,
            budget_sha256=stage_budgets_sha256(self._budgets),
            stage_latencies=latencies,
            budget_violations=tuple(violations),
            staleness=self._staleness(observed_at=observed_at, event=event, bundle=bundle),
            dependency_degradation=tuple(degradation),
            reconciliation_lag_ms=lag_ms,
            duplicate_suppressions=self._duplicate_suppressions,
            circuit_state=circuit,
        )

    def _receipt(
        self,
        *,
        stage: str,
        prior_sha256: str,
        input_sha256: str,
        output_sha256: str,
        started_at: datetime,
        finished_at: datetime,
        status: StageStatus,
        reason_code: str | None,
    ) -> StageReceipt:
        """Build one stage receipt against this service's derived budgets."""

        return StageReceipt(
            stage=stage,
            prior_stage_sha256=prior_sha256,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            started_at=started_at,
            finished_at=finished_at,
            budget_ms=int(getattr(self._budgets, STAGE_BUDGET_FIELDS[stage])),
            status=status,
            reason_code=reason_code,
        )

    def _skipped_tail(
        self,
        *,
        after_index: int,
        prior_sha256: str,
        now: datetime,
        first_reason: str | None = None,
    ) -> tuple[StageReceipt, ...]:
        """Build the deterministic SKIPPED tail of a stopped chain."""

        built: list[StageReceipt] = []
        prior = prior_sha256
        for offset, stage in enumerate(STAGE_ORDER[after_index:]):
            reason = (
                first_reason
                if offset == 0 and first_reason is not None
                else REASON_UPSTREAM_STOPPED
            )
            receipt = self._receipt(
                stage=stage,
                prior_sha256=prior,
                input_sha256=_ZERO_SHA256,
                output_sha256=_ZERO_SHA256,
                started_at=now,
                finished_at=now,
                status=StageStatus.SKIPPED,
                reason_code=reason,
            )
            built.append(receipt)
            prior = stage_receipt_sha256(receipt)
        return tuple(built)

    def _record_run(
        self,
        *,
        window: AutonomousWindow,
        event: CompositionFeedEvent,
        disposition: RunDisposition,
        receipts: Sequence[StageReceipt],
        health: OperationalHealthReceipt,
        option_receipt_sha256s: Sequence[str],
    ) -> None:
        """Bind one finished or stopped window run into the terminal receipt set."""

        self._window_runs.append(
            WindowTerminalRecord(
                window_id=window.window_id,
                opportunity_id=event.opportunity_id,
                disposition=disposition,
                stage_receipt_sha256s=tuple(stage_receipt_sha256(item) for item in receipts),
                health_receipt_sha256=health_receipt_sha256(health),
                option_receipt_sha256s=tuple(sorted(option_receipt_sha256s)),
            )
        )

    def run_window(self, *, request: DueWindowRequest) -> WindowRunResult:
        """Run the complete identity-bound chain for one armed due window."""

        if type(request) is not DueWindowRequest:
            raise ApplicationServiceRejected("request must be a DueWindowRequest")
        arm = self._authority.session_arm
        if autonomous_session_arm_bytes(request.arm) != autonomous_session_arm_bytes(arm):
            raise ApplicationServiceRejected("request arm does not match the validated authority")
        window = self._window(request.window)
        observed_at = _utc(request.observed_at, path="request.observed_at")
        event = self._feed_event(window)
        run_id = f"RUN-{event.opportunity_id}"
        context: dict[str, object] = {}

        deadline_passed = observed_at >= window.closes_at or observed_at >= arm.hard_flat_at
        sticky_manual = self._sticky_manual()
        if deadline_passed or sticky_manual:
            first_reason = (
                REASON_DEADLINE_EXHAUSTED
                if deadline_passed
                else REASON_MANUAL_RECONCILIATION_STICKY
            )
            now = self._now()
            skipped = self._skipped_tail(
                after_index=0,
                prior_sha256=arm.arm_sha256,
                now=now,
                first_reason=first_reason,
            )
            health = self._health(
                run_id=run_id,
                event=event,
                observed_at=observed_at,
                latencies={stage: 0 for stage in STAGE_ORDER},
                violations=(),
                degradation=(first_reason,),
                lag_ms=None,
                circuit=self._circuit_for(first_reason),
            )
            self._record_run(
                window=window,
                event=event,
                disposition=RunDisposition.STOPPED,
                receipts=skipped,
                health=health,
                option_receipt_sha256s=(),
            )
            raise ApplicationServiceStopped(
                f"window {window.window_id} failed closed with {first_reason}",
                stage_receipt=skipped[0],
                health_receipt=health,
                stage_chain=skipped,
            )

        opportunity = AutonomousOpportunity.for_window(
            arm=arm,
            window_id=window.window_id,
            opportunity_id=event.event_id,
            candidate_id=event.candidate_id,
            strategy_context_sha256=event.strategy_context_sha256(window.window_sha256),
        )
        claim = self._store.claim_opportunity(
            arm=arm,
            opportunity=opportunity,
            observed_at=observed_at,
        )
        if claim is not AutonomousClaimState.CLAIMED:
            self._duplicate_suppressions += 1
            health = self._health(
                run_id=run_id,
                event=event,
                observed_at=observed_at,
                latencies={},
                violations=(),
                degradation=(),
                lag_ms=None,
                circuit=self._circuit_for(None),
            )
            return WindowRunResult(
                disposition=RunDisposition.DUPLICATE_SUPPRESSED,
                window_id=window.window_id,
                opportunity_id=event.opportunity_id,
                lifecycle_id=None,
                stage_receipts=(),
                health_receipt=health,
                option_receipt_sha256s=(),
                exposure_sha256=None,
            )
        context["opportunity"] = opportunity
        context["observed_at"] = observed_at

        receipts: list[StageReceipt] = []
        latencies: dict[str, int] = {}
        violations: list[str] = []
        degradation: list[str] = []
        first_input_sha = event.strategy_context_sha256(window.window_sha256)

        def prior_sha() -> str:
            """Return the identity the next stage receipt must bind."""

            if not receipts:
                return arm.arm_sha256
            return stage_receipt_sha256(receipts[-1])

        def stop(failing: StageReceipt) -> NoReturn:
            """Freeze the stopped chain, emit the health receipt, and raise."""

            tail = self._skipped_tail(
                after_index=STAGE_ORDER.index(failing.stage) + 1,
                prior_sha256=stage_receipt_sha256(failing),
                now=failing.finished_at,
            )
            complete = (*receipts, failing, *tail)
            circuit = self._circuit_for(failing.reason_code)
            if failing.reason_code == REASON_BUDGET_VIOLATION and STAGE_ORDER.index(
                failing.stage
            ) >= STAGE_ORDER.index(STAGE_LIFECYCLE_OPEN):
                circuit = CircuitState.MANUAL_REQUIRED
            health = self._health(
                run_id=run_id,
                event=event,
                observed_at=observed_at,
                latencies={**latencies, **{item.stage: 0 for item in tail}},
                violations=violations,
                degradation=degradation,
                lag_ms=_context_lag(context),
                circuit=circuit,
            )
            self._record_run(
                window=window,
                event=event,
                disposition=RunDisposition.STOPPED,
                receipts=complete,
                health=health,
                option_receipt_sha256s=_context_option_receipts(context),
            )
            raise ApplicationServiceStopped(
                f"stage {failing.stage} stopped the identity-bound chain",
                stage_receipt=failing,
                health_receipt=health,
                stage_chain=complete,
            )

        def execute(stage: str, work: Callable[[], str]) -> None:
            """Run one stage inside its budget and identity binding."""

            started = self._now()
            input_sha = first_input_sha if not receipts else receipts[-1].output_sha256
            budget = int(getattr(self._budgets, STAGE_BUDGET_FIELDS[stage]))
            reason: str | None = None
            output_sha = _ZERO_SHA256
            try:
                output_sha = work()
            except _StageWorkFailure as failure:
                reason = failure.reason_code
                degradation.extend(failure.degradation)
            except ApplicationServiceRejected:
                raise
            except Exception:
                reason = _FALLBACK_STAGE_REASONS[stage]
            finished = self._now()
            elapsed = _elapsed_ms(started, finished)
            latencies[stage] = elapsed
            if elapsed > budget:
                violations.append(stage)
                if reason is None:
                    reason = REASON_BUDGET_VIOLATION
            if reason is not None:
                stop(
                    self._receipt(
                        stage=stage,
                        prior_sha256=prior_sha(),
                        input_sha256=input_sha,
                        output_sha256=_ZERO_SHA256,
                        started_at=started,
                        finished_at=finished,
                        status=StageStatus.FAILED,
                        reason_code=reason,
                    )
                )
            receipts.append(
                self._receipt(
                    stage=stage,
                    prior_sha256=prior_sha(),
                    input_sha256=input_sha,
                    output_sha256=output_sha,
                    started_at=started,
                    finished_at=finished,
                    status=StageStatus.OK,
                    reason_code=None,
                )
            )

        execute(STAGE_EVIDENCE_CAPTURE, lambda: self._work_evidence_capture(window, event, context))
        execute(STAGE_FEATURE_RECEIPT, lambda: self._work_feature_receipt(context))
        execute(STAGE_DECISION, lambda: self._work_decision(context))
        execute(STAGE_EXPRESSION, lambda: self._work_expression(context))
        execute(STAGE_RISK, lambda: self._work_risk(context))
        execute(
            STAGE_LIFECYCLE_OPEN,
            lambda: self._work_lifecycle_open(window, observed_at, context),
        )
        execute(
            STAGE_MONITORED_EXECUTION,
            lambda: self._work_monitored_execution(window, event, context),
        )
        execute(
            STAGE_RECONCILIATION, lambda: self._work_reconciliation(window, observed_at, context)
        )
        execute(STAGE_TERMINAL, lambda: self._work_terminal(window, receipts, context))

        exposure = context.get("exposure")
        exposure_sha = exposure.exposure_sha256 if isinstance(exposure, ExposureState) else None
        raw_lifecycle_id = context.get("lifecycle_id")
        health = self._health(
            run_id=run_id,
            event=event,
            observed_at=observed_at,
            latencies=latencies,
            violations=violations,
            degradation=degradation,
            lag_ms=_context_lag(context),
            circuit=self._circuit_for(None),
        )
        self._record_run(
            window=window,
            event=event,
            disposition=RunDisposition.COMPLETED,
            receipts=receipts,
            health=health,
            option_receipt_sha256s=_context_option_receipts(context),
        )
        return WindowRunResult(
            disposition=RunDisposition.COMPLETED,
            window_id=window.window_id,
            opportunity_id=event.opportunity_id,
            lifecycle_id=None if raw_lifecycle_id is None else str(raw_lifecycle_id),
            stage_receipts=tuple(receipts),
            health_receipt=health,
            option_receipt_sha256s=_context_option_receipts(context),
            exposure_sha256=exposure_sha,
        )

    def _work_evidence_capture(
        self,
        window: AutonomousWindow,
        event: CompositionFeedEvent,
        context: dict[str, object],
    ) -> str:
        """Capture the feed evidence through the public sourcedata capture path."""

        try:
            fixture = rejoin_composition_fixture(
                event.evidence_manifest_bytes,
                event.market_window_bytes,
            )
        except CompositionRejected as error:
            raise _StageWorkFailure(REASON_CAPTURE_REJECTED) from error
        capture_root = self._authority.state_dir / CAPTURE_ARTIFACTS_DIRNAME / window.window_id
        fixture_path = capture_root / FEED_FIXTURE_FILENAME
        capture_at_text = event.capture_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        argv = [
            "--event-id",
            event.event_id,
            "--capture-at",
            capture_at_text,
            "--output-dir",
            str(capture_root),
            "--fixture",
            str(fixture_path),
        ]
        for condition in self._capture_conditions:
            argv.extend(("--condition-satisfied", condition))
        try:
            capture_root.mkdir(parents=True, exist_ok=True)
            fixture_path.write_bytes(canonical_json_bytes(fixture))
        except (OSError, TypeError, ValueError) as error:
            raise _StageWorkFailure(REASON_CAPTURE_REJECTED) from error
        prior_authorization = os.environ.get(HOST_AUTHORIZATION_VARIABLE)
        os.environ[HOST_AUTHORIZATION_VARIABLE] = HOST_AUTHORIZATION_VALUE
        try:
            exit_code = sourcedata_capture.main(argv)
        except (CollectorRejected, KeyError, OSError, TypeError, ValueError) as error:
            raise _StageWorkFailure(REASON_CAPTURE_REJECTED) from error
        finally:
            if prior_authorization is None:
                os.environ.pop(HOST_AUTHORIZATION_VARIABLE, None)
            else:
                os.environ[HOST_AUTHORIZATION_VARIABLE] = prior_authorization
        if exit_code != 0 or not (capture_root / CAPTURE_IDENTITY_FILENAME).is_file():
            raise _StageWorkFailure(REASON_CAPTURE_REJECTED)
        try:
            capture = CaptureConfiguration(
                candidate_manifest_bytes=build_candidate_manifest(fixture),
                event_id=event.event_id,
                capture_at=event.capture_at,
                market_publisher=event.market_publisher,
                market_entitlement=event.market_entitlement,
                market_redistribution=event.market_redistribution,
            )
            evidence = FixtureEvidenceSource(fixture)
            market = FixtureMarketDataSource(fixture)
            probe = compile_strategy_snapshot(capture, evidence, market)
        except (CollectorRejected, AssertionError, KeyError, TypeError, ValueError) as error:
            raise _StageWorkFailure(REASON_CAPTURE_REJECTED) from error
        context.update(
            fixture=fixture,
            capture=capture,
            evidence=evidence,
            market=market,
            probe=probe,
        )
        return sha256_bytes(probe.strategy_snapshot_bytes)

    def _work_feature_receipt(self, context: dict[str, object]) -> str:
        """Join the compiled snapshot into the feature-receipt strategy input."""

        probe = context["probe"]
        try:
            joined = compiled_strategy_input(probe)
        except (CollectorRejected, KeyError, TypeError, ValueError) as error:
            raise _StageWorkFailure(REASON_FEATURE_RECEIPT_REJECTED) from error
        context["joined"] = joined
        return joined.feature_receipt_sha256

    def _work_decision(self, context: dict[str, object]) -> str:
        """Run the bounded decision stage through the real prepare_v2 path."""

        joined = context["joined"]
        try:
            timeline = rehearsal_timeline(joined)
        except CompositionRejected as error:
            raise _StageWorkFailure(REASON_DECISION_REJECTED) from error
        self._rehearsal_clock.set(timeline.authorization_at)
        kernel = RiskKernel(
            self._risk_policy,
            self._ledger,
            SyntheticAccountTruthSource(self._broker),
        )
        capture = context["capture"]
        evidence = context["evidence"]
        market = context["market"]
        try:
            kernel.startup_reconciliation(now=timeline.authorization_at)
            application = self._application(kernel)
            prepared: PreparedPaperLifecycle = application.prepare_v2(
                capture_configuration=capture,  # type: ignore[arg-type]
                evidence=evidence,  # type: ignore[arg-type]
                market=market,  # type: ignore[arg-type]
                expression_snapshot=lambda decision_sha256: rehearsal_expression_snapshot(
                    underlying=joined.snapshot.ticker,  # type: ignore[attr-defined]
                    decision_sha256=decision_sha256,
                    observation_clock_at=timeline.authorization_at,
                ),
                now=timeline.authorization_at,
                decision_started_at=timeline.started_at,
            )
        except SyntheticConfirmationAbstained as error:
            raise _StageWorkFailure(REASON_DECISION_ABSTAINED) from error
        except RiskAbstentionRejected as error:
            raise _StageWorkFailure(REASON_DECISION_RISK_FROZEN) from error
        except (PaperPipelineRejected, RiskRejected, CompositionRejected, TypeError, ValueError):
            raise _StageWorkFailure(REASON_DECISION_REJECTED) from None
        context.update(timeline=timeline, application=application, prepared=prepared)
        return sha256_bytes(prepared.engine_outcome.decision_bytes)

    def _work_expression(self, context: dict[str, object]) -> str:
        """Bind the Gate D expression receipt to the exact decision identity."""

        prepared = context["prepared"]
        assert isinstance(prepared, PreparedPaperLifecycle)
        decision_sha256 = sha256_bytes(prepared.engine_outcome.decision_bytes)
        if prepared.compiled_expression.decision_sha256 != decision_sha256:
            raise _StageWorkFailure(REASON_EXPRESSION_UNBOUND)
        return prepared.expression_sha256

    def _work_risk(self, context: dict[str, object]) -> str:
        """Bind the risk approval receipt to the exact canonical permit."""

        prepared = context["prepared"]
        assert isinstance(prepared, PreparedPaperLifecycle)
        approval = prepared.risk_approval
        if (
            approval.permit_id != prepared.permit.permit_id
            or approval.permit_sha256 != prepared.permit_sha256
        ):
            raise _StageWorkFailure(REASON_RISK_APPROVAL_UNBOUND)
        return prepared.permit_sha256

    def _work_lifecycle_open(
        self,
        window: AutonomousWindow,
        observed_at: datetime,
        context: dict[str, object],
    ) -> str:
        """Perform the sole broker-mutating opening under the injected clock."""

        arm = self._authority.session_arm
        if observed_at >= window.closes_at or observed_at >= arm.hard_flat_at:
            raise _StageWorkFailure(REASON_DEADLINE_EXHAUSTED_BEFORE_MUTATION)
        prepared = context["prepared"]
        application = context["application"]
        timeline = context["timeline"]
        assert isinstance(prepared, PreparedPaperLifecycle)
        assert isinstance(application, PaperStrategyApplication)
        self._rehearsal_clock.set(timeline.open_at)  # type: ignore[attr-defined]
        try:
            active = asyncio.run(
                application.open(
                    prepared=prepared,
                    broker=self._broker,
                    clock=self._rehearsal_clock.now,
                    mutation_gate=SyntheticRehearsalMutationGate(),
                )
            )
        except SyntheticBrokerAmbiguousMutation as error:
            raise _StageWorkFailure(REASON_OPEN_AMBIGUOUS_BROKER_STATE) from error
        except Exception as error:
            raise _StageWorkFailure(REASON_OPEN_FAILED) from error
        lifecycle_id = prepared.permit.permit_id
        decision_episode_id = f"dec-{lifecycle_id}"
        snapshot = prepared.strategy_input.snapshot
        decision = prepared.engine_outcome.decision
        exchange = prepared.engine_outcome.exchange
        opened_at = timeline.open_at  # type: ignore[attr-defined]
        open_opportunity = context["opportunity"]
        assert isinstance(open_opportunity, AutonomousOpportunity)
        try:
            append_decision_episode(
                self._ledger,
                DecisionEpisode(
                    episode_id=decision_episode_id,
                    event_id=snapshot.event_id,
                    candidate_id=snapshot.candidate_id,
                    symbol=snapshot.ticker,
                    occurred_at=decision.decision_at,
                    decision_cutoff_at=snapshot.decision_cutoff_at,
                    source_policy_sha256=snapshot.policy_sha256,
                    source_evidence_sha256=snapshot.evidence_packet_sha256,
                    source_feature_sha256=prepared.strategy_input.feature_receipt_sha256,
                    source_snapshot_sha256=prepared.strategy_input.snapshot_sha256,
                    prior_summary_sha256=GENESIS_SUMMARY_SHA256,
                    route_sha256=exchange.route_sha256,
                    prompt_sha256=exchange.prompt_sha256,
                    model_config_sha256=exchange.model_config_sha256,
                    exchange_sha256=decision.reasoner_exchange_sha256,
                    decision_sha256=prepared.compiled_expression.decision_sha256,
                    disposition=decision.disposition.value,
                    direction=decision.direction.value,
                    created_at=opened_at,
                    supersedes_episode_id=None,
                    supersedes_episode_sha256=None,
                ),
            )
            self._sidecar.append_active(
                lifecycle_id=lifecycle_id,
                session_id=arm.session_id,
                opportunity_id=open_opportunity.opportunity_id,
                opportunity_sha256=open_opportunity.opportunity_sha256,
                recorded_at=observed_at,
                permit=prepared.permit,
                clocks=prepared.lifecycle_clocks,
                correlation=prepared.correlation,
                open_order_id=active.open_order_id,
                account_id=application.account_id,
                application_identity_sha256=prepared.application_identity_sha256,
                opened_at=opened_at,
                decision_episode_id=decision_episode_id,
            )
        except Exception as error:
            raise _StageWorkFailure(REASON_OPEN_FAILED) from error
        context.update(active=active, lifecycle_id=lifecycle_id)
        return sha256_bytes(
            canonical_json_bytes(
                {
                    "claims": list(SERVICE_CLAIMS),
                    "lifecycle_id": lifecycle_id,
                    "open_order_id": active.open_order_id,
                    "open_state": str(active.open_state),
                    "schema": STAGE_OPEN_BINDING_SCHEMA,
                    "schema_version": 1,
                }
            )
        )

    def _work_monitored_execution(
        self,
        window: AutonomousWindow,
        event: CompositionFeedEvent,
        context: dict[str, object],
    ) -> str:
        """Attest the monitored execution identity of the opened lifecycle."""

        arm = self._authority.session_arm
        active = context["active"]
        prepared = context["prepared"]
        assert isinstance(active, ActivePaperLifecycle)
        assert isinstance(prepared, PreparedPaperLifecycle)
        if active.open_state is not LifecycleState.OPEN_FILLED:
            raise _StageWorkFailure(REASON_EXECUTION_STATE_INVALID)
        if self._broker.open_position_count() <= 0:
            raise _StageWorkFailure(REASON_EXECUTION_STATE_INVALID)
        try:
            derived_opportunity = context["opportunity"]
            assert isinstance(derived_opportunity, AutonomousOpportunity)
            identity = ActiveLifecycleIdentity.for_candidate(
                arm=arm,
                opportunity=derived_opportunity,
                lifecycle_id=prepared.permit.permit_id,
            )
        except ValueError as error:
            raise _StageWorkFailure(REASON_EXECUTION_STATE_INVALID) from error
        self._store.record_active_lifecycle(
            arm=arm,
            opportunity=derived_opportunity,
            lifecycle=identity,
            observed_at=_utc(context["observed_at"], path="context.observed_at"),
        )
        context["identity"] = identity
        return identity.lifecycle_sha256

    def _work_reconciliation(
        self,
        window: AutonomousWindow,
        observed_at: datetime,
        context: dict[str, object],
    ) -> str:
        """Attest synthetic broker truth and journal the option reconciliation."""

        arm = self._authority.session_arm
        prepared = context["prepared"]
        identity = context["identity"]
        assert isinstance(prepared, PreparedPaperLifecycle)
        assert isinstance(identity, ActiveLifecycleIdentity)
        lifecycle_id = prepared.permit.permit_id
        request = ReconciliationRequest(
            session_id=arm.session_id,
            arm_sha256=arm.arm_sha256,
            account_fingerprint_sha256=arm.account_fingerprint_sha256,
            execution_protocol_sha256=arm.execution_protocol_sha256,
            observed_at=observed_at,
            phase=HOST_TRUTH_PHASE_WINDOW,
            active_lifecycle_ids=(lifecycle_id,),
        )
        try:
            truth = SyntheticBrokerTruth.for_request(
                request,
                account_state_sha256=self._broker.account_state_sha256(),
                orders_state_sha256=self._broker.orders_state_sha256(),
                positions_state_sha256=self._broker.positions_state_sha256(),
                open_order_count=self._broker.open_order_count(),
                open_position_count=self._broker.open_position_count(),
                is_flat=self._broker.is_flat(),
            )
            truth_sha256 = synthetic_broker_truth_sha256(truth)
            synthetic_broker_truth_bytes(truth)
        except ValueError as error:
            raise _StageWorkFailure(REASON_RECONCILIATION_STATE_CONFLICT) from error
        if self._reconciliation_fault == "INCOMPLETE":
            raise _StageWorkFailure(REASON_BROKER_TRUTH_INCOMPLETE)
        if self._reconciliation_fault == "AMBIGUOUS":
            raise _StageWorkFailure(REASON_BROKER_TRUTH_AMBIGUOUS)
        bundle = self._option_feeds.get(window.window_id)
        if bundle is None:
            return truth_sha256
        try:
            binding = OptionLifecycleBinding.create(
                arm=arm,
                lifecycle=identity,
                permit=prepared.permit,
                reservation_id=prepared.risk_approval.reservation_id,
                activation_observation=bundle.activation_observation,
                expiration_session_date=bundle.expiration_session_date,
                expiration_session_close=bundle.expiration_session_close,
                expiration_activity_horizon=bundle.expiration_activity_horizon,
                calendar_sha256=bundle.calendar_sha256,
            )
        except (OptionEventRejected, ValueError) as error:
            raise _StageWorkFailure(REASON_RECONCILIATION_INPUT_REJECTED) from error
        claim_at = self._now()
        try:
            receipt = self._journal.record_reconciliation(
                binding=binding,
                current_observation=bundle.current_observation,
                activity_coverage=bundle.activity_coverage,
                events=bundle.events,
            )
        except OptionEventRejected as error:
            raise _StageWorkFailure(REASON_RECONCILIATION_INPUT_REJECTED) from error
        except OptionEventConflict as error:
            raise _StageWorkFailure(REASON_RECONCILIATION_STATE_CONFLICT) from error
        lag = (receipt.observed_at - claim_at) // timedelta(milliseconds=1)
        if lag < 0:
            raise _StageWorkFailure(REASON_RECONCILIATION_STATE_CONFLICT)
        context["reconciliation_lag_ms"] = lag
        recorded = context.setdefault("option_receipt_sha256s", [])
        assert isinstance(recorded, list)
        recorded.append(receipt.receipt_sha256)
        try:
            context["exposure"] = self._record_exposure(
                lifecycle_id=lifecycle_id,
                receipt=receipt,
                observed_at=observed_at,
            )
        except (HostPersistenceRejected, TypeError, ValueError) as error:
            raise _StageWorkFailure(REASON_RECONCILIATION_STATE_CONFLICT) from error
        if receipt.state is OptionReconciliationState.MANUAL_RECONCILIATION_REQUIRED:
            raise _StageWorkFailure(
                REASON_RECONCILIATION_MANUAL_REQUIRED,
                degradation=receipt.reason_codes,
            )
        return sha256_bytes(
            canonical_json_bytes(
                {
                    "broker_truth_sha256": truth_sha256,
                    "claims": list(SERVICE_CLAIMS),
                    "option_receipt_sha256": receipt.receipt_sha256,
                    "schema": STAGE_RECONCILIATION_BINDING_SCHEMA,
                    "schema_version": 1,
                }
            )
        )

    def _work_terminal(
        self,
        window: AutonomousWindow,
        receipts: Sequence[StageReceipt],
        context: dict[str, object],
    ) -> str:
        """Bind every prior stage receipt identity into the terminal stage."""

        arm = self._authority.session_arm
        stage_shas = tuple(stage_receipt_sha256(item) for item in receipts)
        if len(stage_shas) != len(STAGE_ORDER) - 1:
            raise _StageWorkFailure(REASON_RECONCILIATION_STATE_CONFLICT)
        return sha256_bytes(
            canonical_json_bytes(
                {
                    "arm_sha256": arm.arm_sha256,
                    "claims": list(SERVICE_CLAIMS),
                    "option_receipt_sha256s": sorted(_context_option_receipts(context)),
                    "schema": STAGE_TERMINAL_BINDING_SCHEMA,
                    "schema_version": 1,
                    "stage_receipt_sha256s": list(stage_shas),
                    "window_sha256": window.window_sha256,
                }
            )
        )

    def _record_exposure(
        self,
        *,
        lifecycle_id: str,
        receipt: OptionEventReconciliationReceipt,
        observed_at: datetime,
    ) -> ExposureState:
        """Recompute deterministic exposure and persist one canonical entry."""

        arm = self._authority.session_arm
        open_total = sum(
            (Decimal(str(row["amount"])) for row in self._ledger.v2_open_reservation_rows()),
            start=Decimal(0),
        )
        provisional = ExposureState(
            session_id=arm.session_id,
            lifecycle_id=lifecycle_id,
            observed_at=observed_at,
            open_reservation_total=open_total,
            option_long_quantity=receipt.long_option_quantity,
            option_short_quantity=receipt.short_option_quantity,
            underlying_quantity_delta=receipt.underlying_quantity_delta,
            event_cash_delta=receipt.event_cash_delta,
            option_receipt_sha256=receipt.receipt_sha256,
            exposure_sha256=_ZERO_SHA256,
        )
        payload = exposure_state_payload(provisional)
        digest = sha256_bytes(canonical_json_bytes(payload))
        state = replace(provisional, exposure_sha256=digest)
        _exposure_journal_append(
            _exposure_journal_path(self._authority.state_dir),
            session_id=arm.session_id,
            lifecycle_id=lifecycle_id,
            recorded_at=observed_at,
            payload={**payload, "exposure_sha256": digest},
        )
        return state

    def exposure_state(self, session_id: str) -> tuple[ExposureState, ...]:
        """Return every persisted deterministic exposure entry for one session."""

        session = _text(session_id, path="session_id")
        states: list[ExposureState] = []
        for entry in _exposure_journal_entries(_exposure_journal_path(self._authority.state_dir)):
            if entry.get("kind") != EXPOSURE_ENTRY_KIND:
                continue
            if entry.get("session_id") != session:
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                raise ApplicationServiceRejected("exposure sidecar entry has no payload")
            if payload.get("schema") != EXPOSURE_STATE_SCHEMA:
                raise ApplicationServiceRejected("exposure entry has an unsupported schema")
            unsigned = {key: value for key, value in payload.items() if key != "exposure_sha256"}
            digest = sha256_bytes(canonical_json_bytes(unsigned))
            if payload.get("exposure_sha256") != digest:
                raise ApplicationServiceRejected("stored exposure entry hash is invalid")
            receipt_sha = payload.get("option_receipt_sha256")
            states.append(
                ExposureState(
                    session_id=session,
                    lifecycle_id=str(entry.get("lifecycle_id")),
                    observed_at=_parse_timestamp(
                        payload.get("observed_at"),
                        path="exposure.observed_at",
                    ),
                    open_reservation_total=Decimal(str(payload["open_reservation_total"])),
                    option_long_quantity=Decimal(str(payload["option_long_quantity"])),
                    option_short_quantity=Decimal(str(payload["option_short_quantity"])),
                    underlying_quantity_delta=Decimal(str(payload["underlying_quantity_delta"])),
                    event_cash_delta=Decimal(str(payload["event_cash_delta"])),
                    option_receipt_sha256=None if receipt_sha is None else str(receipt_sha),
                    exposure_sha256=digest,
                )
            )
        return tuple(states)

    def _close_broker_truth(self, observed_at: datetime, lifecycle_ids: tuple[str, ...]) -> str:
        """Attest one synthetic close-phase broker truth and return its identity."""

        arm = self._authority.session_arm
        request = ReconciliationRequest(
            session_id=arm.session_id,
            arm_sha256=arm.arm_sha256,
            account_fingerprint_sha256=arm.account_fingerprint_sha256,
            execution_protocol_sha256=arm.execution_protocol_sha256,
            observed_at=observed_at,
            phase=HOST_TRUTH_PHASE_CLOSE,
            active_lifecycle_ids=lifecycle_ids,
        )
        try:
            truth = SyntheticBrokerTruth.for_request(
                request,
                account_state_sha256=self._broker.account_state_sha256(),
                orders_state_sha256=self._broker.orders_state_sha256(),
                positions_state_sha256=self._broker.positions_state_sha256(),
                open_order_count=self._broker.open_order_count(),
                open_position_count=self._broker.open_position_count(),
                is_flat=self._broker.is_flat(),
            )
            truth_sha256 = synthetic_broker_truth_sha256(truth)
            synthetic_broker_truth_bytes(truth)
        except ValueError as error:
            raise ApplicationServiceRejected("CLOSE_BROKER_TRUTH_INVALID") from error
        return truth_sha256

    def _stop_close(
        self,
        *,
        lifecycle_id: str,
        started_at: datetime,
        finished_at: datetime,
        input_sha256: str,
    ) -> NoReturn:
        """Fail closed on a bounded close failure with an explicit health receipt."""

        failing = self._receipt(
            stage=STAGE_CLOSE_AUTHORITY,
            prior_sha256=self._authority.session_arm.arm_sha256,
            input_sha256=input_sha256,
            output_sha256=_ZERO_SHA256,
            started_at=started_at,
            finished_at=finished_at,
            status=StageStatus.FAILED,
            reason_code=REASON_CLOSE_FAILED,
        )
        max_age = self._risk_policy.truth_max_age_seconds
        health = build_operational_health_receipt(
            run_id=f"CLOSE-{lifecycle_id}",
            arm_sha256=self._authority.session_arm.arm_sha256,
            observed_at=finished_at,
            budget_sha256=stage_budgets_sha256(self._budgets),
            stage_latencies={STAGE_CLOSE_AUTHORITY: _elapsed_ms(started_at, finished_at)},
            budget_violations=(),
            staleness=(
                SourceStaleness(
                    source_id=STALENESS_BROKER_TRUTH,
                    age_seconds=0,
                    max_age_seconds=max_age,
                    stale=False,
                ),
            ),
            dependency_degradation=(REASON_CLOSE_FAILED,),
            reconciliation_lag_ms=None,
            duplicate_suppressions=self._duplicate_suppressions,
            circuit_state=CircuitState.MANUAL_REQUIRED,
        )
        raise ApplicationServiceStopped(
            f"bounded close authority failed for lifecycle {lifecycle_id}",
            stage_receipt=failing,
            health_receipt=health,
            stage_chain=(failing,),
        )

    def close_authority(
        self,
        *,
        lifecycle_id: str,
        observed_at: datetime,
    ) -> CloseAuthorityResult:
        """Exercise the bounded close authority: lifecycle closer plus reconciler."""

        arm = self._authority.session_arm
        lifecycle = _text(lifecycle_id, path="lifecycle_id")
        observed = _utc(observed_at, path="observed_at")
        started = self._now()
        existing_proof = self._sidecar.terminal_flat_proof(lifecycle)
        if existing_proof is not None:
            truth_sha256 = self._close_broker_truth(observed, ())
            self._closes.append(
                CloseTerminalRecord(
                    lifecycle_id=lifecycle,
                    terminal_flat_proof_sha256=existing_proof,
                    broker_truth_sha256=truth_sha256,
                    closed_at=observed,
                )
            )
            return CloseAuthorityResult(
                lifecycle_id=lifecycle,
                terminal_flat_proof_sha256=existing_proof,
                broker_truth_sha256=truth_sha256,
                closed_at=observed,
                outcome_episode_id=f"out-{lifecycle}",
            )
        try:
            bundle = self._sidecar.rehydrate(lifecycle)
        except HostPersistenceRejected as error:
            raise ApplicationServiceRejected("CLOSE_BUNDLE_INVALID") from error
        if bundle is None or bundle.session_id != arm.session_id:
            raise ApplicationServiceRejected("CLOSE_BUNDLE_MISSING")
        active_identities = {
            identity.lifecycle_id: identity
            for identity in self._store.active_lifecycles(arm.session_id)
        }
        active_identity = active_identities.get(lifecycle)
        if active_identity is None:
            raise ApplicationServiceRejected("CLOSE_LIFECYCLE_NOT_ACTIVE")
        if (
            bundle.opportunity_id != active_identity.opportunity_id
            or bundle.opportunity_sha256 != active_identity.opportunity_sha256
        ):
            raise ApplicationServiceRejected("CLOSE_BUNDLE_OPPORTUNITY_MISMATCH")
        close_at = bundle.clocks.time_exit_at + SYNTHETIC_CLOSE_DELAY
        current = self._rehearsal_current()
        if current is not None and current > close_at:
            close_at = current
        self._rehearsal_clock.set(close_at)
        monitored = MonitoredPaperLifecycle(
            broker=self._broker,
            ledger=self._ledger,
            clocks=bundle.clocks,
            correlation=bundle.correlation,
            mutation_gate=SyntheticRehearsalMutationGate(),
            clock=self._rehearsal_clock.now,
            account_id=bundle.account_id,
            account_class=PAPER_ACCOUNT_CLASS,
            order_class=MULTI_LEG_ORDER_CLASS,
        )
        binding = CloseCriticalBinding(
            open_permit=bundle.permit,
            permit_sha256=bundle.permit_sha256,
            lifecycle_clocks=bundle.clocks,
            lifecycle_clocks_sha256=bundle.clocks_sha256,
            correlation=bundle.correlation,
            correlation_sha256=bundle.correlation_sha256,
            application_identity_sha256=bundle.application_identity_sha256,
            ledger=self._ledger,
            broker=self._broker,
            account_id=bundle.account_id,
            account_class=PAPER_ACCOUNT_CLASS,
            order_class=MULTI_LEG_ORDER_CLASS,
            open_order_id=bundle.open_order_id,
            lifecycle=monitored,
        )
        active = ActivePaperLifecycle(
            prepared=None,  # type: ignore[arg-type]
            lifecycle=monitored,
            open_order_id=bundle.open_order_id,
            open_state=LifecycleState.OPEN_FILLED,
            close_binding=binding,
        )
        close_permit = issue_close_permit(
            open_permit=bundle.permit,
            event_run_id=bundle.permit.event_run_id,
            policy_sha256=bundle.permit.policy_sha256,
            snapshot_sha256=bundle.permit.snapshot_sha256,
            issued_at=close_at,
            expires_at=close_at + CLOSE_PERMIT_TTL,
            limit_price=SYNTHETIC_CLOSE_LIMIT_PRICE,
        )
        kernel = RiskKernel(
            self._risk_policy,
            self._ledger,
            SyntheticAccountTruthSource(self._broker),
        )
        application = self._application(kernel)
        try:
            close_state, close_order_id = asyncio.run(
                application.close(active=active, close_permit=close_permit)
            )
        except (
            SyntheticBrokerAmbiguousMutation,
            LifecycleRejected,
            PaperPipelineRejected,
            BrokerOutage,
            RiskRejected,
        ):
            self._stop_close(
                lifecycle_id=lifecycle,
                started_at=started,
                finished_at=self._now(),
                input_sha256=bundle.permit_sha256,
            )
        if close_state is not LifecycleState.CLOSED_FLAT or close_order_id is None:
            self._stop_close(
                lifecycle_id=lifecycle,
                started_at=started,
                finished_at=self._now(),
                input_sha256=bundle.permit_sha256,
            )
        proof = terminal_flat_proof_sha256(
            session_id=arm.session_id,
            lifecycle_id=lifecycle,
            close_order_id=str(close_order_id),
            close_permit_id=close_permit.permit_id,
            closed_at=close_at,
            broker=self._broker,
        )
        outcome_episode_id = f"out-{lifecycle}"
        try:
            self._sidecar.append_terminal(
                lifecycle_id=lifecycle,
                session_id=arm.session_id,
                recorded_at=observed,
                terminal_flat_proof_sha256=proof,
            )
            append_outcome_episode(
                self._ledger,
                OutcomeEpisode(
                    outcome_id=outcome_episode_id,
                    decision_episode_id=bundle.decision_episode_id or f"dec-{lifecycle}",
                    event_id=bundle.permit.event_run_id,
                    open_permit_id=bundle.permit.permit_id,
                    close_permit_id=close_permit.permit_id,
                    open_order_id=bundle.open_order_id,
                    close_order_id=str(close_order_id),
                    terminal_at=close_at,
                    observed_at=close_at,
                    lifecycle_outcome="CLOSED",
                    pnl_classification="REALIZED",
                    gross_pnl="0",
                    net_pnl="0",
                    reconciliation_sha256=proof,
                    final_flat=True,
                    supersedes_outcome_id=None,
                    supersedes_outcome_sha256=None,
                    created_at=close_at,
                ),
            )
        except (HostPersistenceRejected, TypeError, ValueError) as error:
            raise ApplicationServiceRejected("CLOSE_PERSISTENCE_FAILED") from error
        self._store.record_lifecycle_terminal_flat(
            arm=arm,
            lifecycle=active_identity,
            terminal_flat_proof_sha256=proof,
            observed_at=close_at,
        )
        self._record_close_disposition(
            lifecycle_id=lifecycle,
            event_run_id=bundle.permit.event_run_id,
            proof=proof,
            observed_at=observed,
        )
        truth_sha256 = self._close_broker_truth(observed, ())
        self._closes.append(
            CloseTerminalRecord(
                lifecycle_id=lifecycle,
                terminal_flat_proof_sha256=proof,
                broker_truth_sha256=truth_sha256,
                closed_at=close_at,
            )
        )
        return CloseAuthorityResult(
            lifecycle_id=lifecycle,
            terminal_flat_proof_sha256=proof,
            broker_truth_sha256=truth_sha256,
            closed_at=close_at,
            outcome_episode_id=outcome_episode_id,
        )

    def _record_close_disposition(
        self,
        *,
        lifecycle_id: str,
        event_run_id: str,
        proof: str,
        observed_at: datetime,
    ) -> None:
        """Persist the terminal-flat disposition when the claim identity exists."""

        arm = self._authority.session_arm
        matches = tuple(event for event in self._feed.events if event.event_id == event_run_id)
        if len(matches) != 1:
            return
        event = matches[0]
        windows = tuple(window for window in arm.windows if window.window_id == event.window_id)
        if len(windows) != 1:
            return
        window = windows[0]
        opportunity = AutonomousOpportunity.for_window(
            arm=arm,
            window_id=window.window_id,
            opportunity_id=event.opportunity_id,
            candidate_id=event.candidate_id,
            strategy_context_sha256=event.strategy_context_sha256(window.window_sha256),
        )
        state = self._store.opportunity_state(arm.session_id, opportunity.opportunity_id)
        if state is None:
            return
        try:
            self._store.record_terminal_flat(
                arm=arm,
                opportunity=opportunity,
                terminal_flat_proof_sha256=proof,
                observed_at=observed_at,
            )
        except AutonomousStoreConflict as error:
            raise ApplicationServiceRejected("CLOSE_STORE_CONFLICT") from error

    def terminal_receipt(self) -> ServiceTerminalReceipt:
        """Bind every recorded stage, health, close, and option receipt identity."""

        arm = self._authority.session_arm
        provisional = ServiceTerminalReceipt(
            session_id=arm.session_id,
            arm_sha256=arm.arm_sha256,
            windows=tuple(self._window_runs),
            closes=tuple(self._closes),
            terminal_receipt_sha256=_ZERO_SHA256,
        )
        digest = service_terminal_receipt_sha256(provisional)
        return replace(provisional, terminal_receipt_sha256=digest)


__all__ = [
    "CAPTURE_ARTIFACTS_DIRNAME",
    "CLOSE_PERMIT_TTL",
    "DEFAULT_CAPTURE_CONDITIONS",
    "EXPOSURE_ENTRY_KIND",
    "EXPOSURE_STATE_SCHEMA",
    "EXPOSURE_STATE_SCHEMA_VERSION",
    "FEED_FIXTURE_FILENAME",
    "FROZEN_CIRCUIT_REASONS",
    "HOST_TRUTH_PHASE_CLOSE",
    "HOST_TRUTH_PHASE_WINDOW",
    "MANUAL_CIRCUIT_REASONS",
    "OPTION_EVENT_JOURNAL_FILENAME",
    "REASON_BROKER_TRUTH_AMBIGUOUS",
    "REASON_BROKER_TRUTH_INCOMPLETE",
    "REASON_BUDGET_VIOLATION",
    "REASON_CAPTURE_REJECTED",
    "REASON_CLOSE_FAILED",
    "REASON_DEADLINE_EXHAUSTED",
    "REASON_DEADLINE_EXHAUSTED_BEFORE_MUTATION",
    "REASON_DECISION_ABSTAINED",
    "REASON_DECISION_REJECTED",
    "REASON_DECISION_RISK_FROZEN",
    "REASON_EXECUTION_STATE_INVALID",
    "REASON_EXPRESSION_UNBOUND",
    "REASON_FEATURE_RECEIPT_REJECTED",
    "REASON_MANUAL_RECONCILIATION_STICKY",
    "REASON_OPEN_AMBIGUOUS_BROKER_STATE",
    "REASON_OPEN_FAILED",
    "REASON_RECONCILIATION_INPUT_REJECTED",
    "REASON_RECONCILIATION_MANUAL_REQUIRED",
    "REASON_RECONCILIATION_STATE_CONFLICT",
    "REASON_RISK_APPROVAL_UNBOUND",
    "REASON_UPSTREAM_STOPPED",
    "SERVICE_CLAIMS",
    "SERVICE_TERMINAL_RECEIPT_SCHEMA",
    "SERVICE_TERMINAL_RECEIPT_SCHEMA_VERSION",
    "STAGE_BUDGET_FIELDS",
    "STAGE_CLOSE_AUTHORITY",
    "STAGE_DECISION",
    "STAGE_EVIDENCE_CAPTURE",
    "STAGE_EXPRESSION",
    "STAGE_FEATURE_RECEIPT",
    "STAGE_LIFECYCLE_OPEN",
    "STAGE_MONITORED_EXECUTION",
    "STAGE_ORDER",
    "STAGE_RECEIPT_SCHEMA",
    "STAGE_RECEIPT_SCHEMA_VERSION",
    "STAGE_RECONCILIATION",
    "STAGE_RISK",
    "STAGE_TERMINAL",
    "STALENESS_BROKER_TRUTH",
    "STALENESS_FEED_CAPTURE",
    "STALENESS_OPTION_OBSERVATION",
    "ApplicationServiceRejected",
    "ApplicationServiceStopped",
    "AutonomousApplicationService",
    "CloseAuthorityResult",
    "CloseTerminalRecord",
    "ExposureState",
    "RunDisposition",
    "ServiceTerminalReceipt",
    "StageReceipt",
    "StageStatus",
    "WindowOptionActivityFeed",
    "WindowRunResult",
    "WindowTerminalRecord",
    "exposure_state_payload",
    "exposure_state_sha256",
    "service_terminal_receipt_bytes",
    "service_terminal_receipt_sha256",
    "service_terminal_receipt_unsigned_payload",
    "stage_receipt_bytes",
    "stage_receipt_payload",
    "stage_receipt_sha256",
    "verify_stage_receipt_chain",
]
