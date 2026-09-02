"""Synthetic rehearsal composition for the frozen autonomous PAPER coordinator.

This module deliberately stops at a typed host boundary.  Backends report
observations and outcomes; the existing :mod:`runtime.autonomous` coordinator
remains the sole candidate loop and the sole writer of autonomous session
state.  This is a synthetic-only scaffold: its closed execution classification
does not admit a live or operational backend, and its receipt records that the
no-broker-execution claim rests on the host plan's explicit attestation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol
from zoneinfo import ZoneInfo

from ringdown_market.contracts.strategy_release import (
    ArmRecord,
    PromotionStatus,
    ReleaseLog,
    ReleaseLogRejected,
    StrategyRelease,
    StrategyReleaseRejected,
    evaluate_release,
    parse_arm_record,
    parse_strategy_release,
    strategy_release_bytes,
)
from ringdown_market.runtime.autonomous import (
    AutonomousArmRejected,
    AutonomousDisposition,
    AutonomousOpportunity,
    AutonomousSessionArm,
    AutonomousSessionCoordinator,
    AutonomousSessionPorts,
    AutonomousSessionStore,
    AutonomousStoreConflict,
    CandidateProcessingRequest,
    CandidateProcessingResult,
    DueWindowRequest,
    LifecycleCloseRequest,
    LifecycleCloseResult,
    MutationState,
    ReconciliationReceipt,
    ReconciliationRequest,
    autonomous_session_arm_bytes,
    parse_autonomous_session_arm,
)

AUTONOMOUS_HOST_RECEIPT_SCHEMA = "esscher.autonomous_host_receipt"
AUTONOMOUS_HOST_RECEIPT_SCHEMA_VERSION = 1
SYNTHETIC_BROKER_TRUTH_SCHEMA = "esscher.synthetic_broker_truth"
SYNTHETIC_BROKER_TRUTH_SCHEMA_VERSION = 1
AUTONOMOUS_HOST_STATE_FILENAME = "autonomous.sqlite3"
AUTONOMOUS_HOST_CLAIMS = (
    "SYNTHETIC_FAKE",
    "NOT_HISTORICAL_DATA",
    "NOT_ALPHA_EVIDENCE",
    "HOST_PLAN_ATTESTS_NO_BROKER_EXECUTION",
)
AUTONOMOUS_HOST_CLAIM_BASIS = "HOST_PLAN_ATTESTATION"

_DIGEST = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_BOUNDED_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}")
_MANUAL_REASON_CODES = frozenset(
    {
        "ARM_IDENTITY_MISMATCH",
        "CLAIM_RECOVERY_UNKNOWN",
        "HARD_FLAT_UNRESOLVED",
        "LATE_WINDOW",
        "PARTIAL_FILL",
        "PORT_EXCEPTION",
        "PORT_OUTPUT_INVALID",
        "PROVIDER_TIMEOUT_BEFORE_MUTATION",
        "PROVIDER_TIMEOUT_UNKNOWN_MUTATION",
        "RECONCILIATION_FAILED",
        "RECONCILIATION_IDENTITY_MISMATCH",
        "RECONCILIATION_INCOMPLETE",
        "RISK_FREEZE",
        "UNEXPECTED_ACTIVE_LIFECYCLE",
        "UNKNOWN_BROKER_STATE",
        "WINDOW_EXPIRED",
        "WINDOW_NOT_DUE",
    }
)
_EASTERN = ZoneInfo("America/New_York")


class AutonomousHostRejected(ValueError):
    """Raised when host authority, configuration, or a synthetic plan is invalid."""


class AutonomousHostBusy(RuntimeError):
    """Raised when another process owns the autonomous state-directory lock."""


class AutonomousHostBackendRejected(RuntimeError):
    """A typed backend observation could not prove the requested fact."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class AutonomousHostDisposition(StrEnum):
    TERMINAL = "TERMINAL"
    MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"
    INCOMPLETE = "INCOMPLETE"


class HostExecutionClass(StrEnum):
    """The only execution boundary admitted by this rehearsal scaffold."""

    SYNTHETIC_FAKE = "SYNTHETIC_FAKE"


class HostReconciliationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    AMBIGUOUS = "AMBIGUOUS"


class HostCandidateDisposition(StrEnum):
    ABSTAINED = "ABSTAINED"
    REJECTED_BEFORE_MUTATION = "REJECTED_BEFORE_MUTATION"
    ACTIVE = "ACTIVE"
    TERMINAL_FLAT = "TERMINAL_FLAT"
    MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"


class HostLifecycleDisposition(StrEnum):
    TERMINAL_FLAT = "TERMINAL_FLAT"
    MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise AutonomousHostRejected(f"{path} must be a lowercase SHA-256 digest")
    return value


def _revision(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _REVISION.fullmatch(value) is None:
        raise AutonomousHostRejected(f"{path} must be a lowercase Git revision")
    return value


def _identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AutonomousHostRejected(f"{path} must be non-empty exact text")
    return value


def _bounded_identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _BOUNDED_IDENTIFIER.fullmatch(value) is None:
        raise AutonomousHostRejected(f"{path} must be a bounded identifier")
    return value


def _manual_reason_code(value: object, *, path: str) -> str:
    if not isinstance(value, str) or value not in _MANUAL_REASON_CODES:
        raise AutonomousHostRejected(f"{path} must be an allowlisted reason code")
    return value


def _utc(value: object, *, path: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AutonomousHostRejected(f"{path} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    rendered = value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if rendered.endswith(".000000Z"):
        return rendered.replace(".000000Z", "Z")
    prefix, fraction = rendered[:-1].split(".", maxsplit=1)
    return f"{prefix}.{fraction.rstrip('0')}Z"


def _canonical_identifiers(value: object, *, path: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise AutonomousHostRejected(f"{path} must be an immutable tuple")
    result = tuple(_identifier(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    if not result or result != tuple(sorted(set(result))):
        raise AutonomousHostRejected(f"{path} must be non-empty, sorted, and unique")
    return result


def _canonical_bounded_identity_set(value: object, *, path: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise AutonomousHostRejected(f"{path} must be an immutable tuple")
    result = tuple(
        _bounded_identifier(item, path=f"{path}[{index}]") for index, item in enumerate(value)
    )
    if result != tuple(sorted(set(result))):
        raise AutonomousHostRejected(f"{path} must be sorted and unique")
    return result


def _canonical_manual_reason_codes(value: object, *, path: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise AutonomousHostRejected(f"{path} must be an immutable tuple")
    result = tuple(
        _manual_reason_code(item, path=f"{path}[{index}]") for index, item in enumerate(value)
    )
    if result != tuple(sorted(set(result))):
        raise AutonomousHostRejected(f"{path} must be sorted and unique")
    return result


def _canonical_disposition_counts(value: object, *, path: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise AutonomousHostRejected(f"{path} must be a mapping")
    allowed = {item.value for item in AutonomousDisposition}
    if any(not isinstance(key, str) or key not in allowed for key in value):
        raise AutonomousHostRejected(f"{path} contains an unsupported disposition")
    counts: dict[str, int] = {}
    for disposition in AutonomousDisposition:
        count = value.get(disposition.value, 0)
        if type(count) is not int or count < 0:
            raise AutonomousHostRejected(f"{path} contains an invalid count")
        counts[disposition.value] = count
    return counts


@dataclass(frozen=True, slots=True)
class AutonomousHostAuthorityInput:
    """Canonical authority bytes plus identities observed by the running host."""

    release_bytes: bytes
    arm_record_bytes: bytes
    session_arm_bytes: bytes
    release_log_path: Path
    release_sha256: str
    runtime_build_artifact_sha256: str
    runtime_code_revision: str
    account_capability_id: str
    account_fingerprint_sha256: str
    source_ids: tuple[str, ...]
    ledger_id: str
    process_id: str
    state_dir: Path


@dataclass(frozen=True, slots=True)
class ValidatedAutonomousHostAuthority:
    """Fully parsed authority graph safe to provide to a delayed plan factory."""

    release: StrategyRelease
    arm_record: ArmRecord
    session_arm: AutonomousSessionArm
    release_sha256: str
    arm_record_sha256: str
    session_arm_sha256: str
    runtime_build_artifact_sha256: str
    runtime_code_revision: str
    account_capability_id: str
    account_fingerprint_sha256: str
    source_ids: tuple[str, ...]
    ledger_id: str
    process_id: str
    release_log_path: Path
    state_dir: Path
    store_path: Path


def _validate_existing_state_paths(state_dir: Path) -> None:
    if state_dir.is_symlink() or (state_dir.exists() and not state_dir.is_dir()):
        raise AutonomousHostRejected("autonomous state_dir must be a real directory")
    store_path = state_dir / AUTONOMOUS_HOST_STATE_FILENAME
    if store_path.is_symlink() or (store_path.exists() and not store_path.is_file()):
        raise AutonomousHostRejected("autonomous session state must be a real file")


def validate_autonomous_host_authority(
    value: AutonomousHostAuthorityInput,
) -> ValidatedAutonomousHostAuthority:
    """Validate the entire release/arm/runtime graph without invoking a backend."""

    if type(value) is not AutonomousHostAuthorityInput:
        raise AutonomousHostRejected("authority_input must be AutonomousHostAuthorityInput")
    for name in ("release_bytes", "arm_record_bytes", "session_arm_bytes"):
        if type(getattr(value, name)) is not bytes:
            raise AutonomousHostRejected(f"{name} must be exact bytes")
    try:
        release = parse_strategy_release(value.release_bytes)
        arm_record = parse_arm_record(value.arm_record_bytes)
        session_arm = parse_autonomous_session_arm(value.session_arm_bytes)
    except (StrategyReleaseRejected, AutonomousArmRejected, TypeError, ValueError) as error:
        raise AutonomousHostRejected("host authority bytes are invalid or non-canonical") from error

    release_sha256 = _digest(value.release_sha256, path="release_sha256")
    build_sha256 = _digest(
        value.runtime_build_artifact_sha256,
        path="runtime_build_artifact_sha256",
    )
    revision = _revision(value.runtime_code_revision, path="runtime_code_revision")
    capability_id = _identifier(value.account_capability_id, path="account_capability_id")
    account_fingerprint = _digest(
        value.account_fingerprint_sha256,
        path="account_fingerprint_sha256",
    )
    source_ids = _canonical_identifiers(value.source_ids, path="source_ids")
    ledger_id = _identifier(value.ledger_id, path="ledger_id")
    process_id = _identifier(value.process_id, path="process_id")
    if not isinstance(value.state_dir, Path):
        raise AutonomousHostRejected("state_dir must be a pathlib.Path")
    if not isinstance(value.release_log_path, Path):
        raise AutonomousHostRejected("release_log_path must be a pathlib.Path")
    if (
        value.release_log_path.is_symlink()
        or not value.release_log_path.exists()
        or not value.release_log_path.is_file()
    ):
        raise AutonomousHostRejected("release_log_path must be an existing real file")
    _validate_existing_state_paths(value.state_dir)
    store_path = value.state_dir / AUTONOMOUS_HOST_STATE_FILENAME
    try:
        release_log_resolved = value.release_log_path.resolve(strict=True)
        store_resolved = store_path.resolve(strict=False)
        aliases_store = release_log_resolved == store_resolved or (
            store_path.exists() and value.release_log_path.samefile(store_path)
        )
    except OSError as error:
        raise AutonomousHostRejected(
            "release log and autonomous state paths cannot be resolved safely"
        ) from error
    if aliases_store:
        raise AutonomousHostRejected(
            "release log and autonomous session state must be distinct files"
        )

    promotion = evaluate_release(release)
    if promotion.status is not PromotionStatus.PROMOTED:
        raise AutonomousHostRejected("strategy release is not promoted by current package policy")
    try:
        with ReleaseLog(value.release_log_path) as release_log:
            logged_release = release_log.load_exact(release_sha256)
    except (
        OSError,
        ReleaseLogRejected,
        sqlite3.Error,
        StrategyReleaseRejected,
        TypeError,
        ValueError,
    ) as error:
        raise AutonomousHostRejected(
            "strategy release is not the exact current release-log entry"
        ) from error
    if strategy_release_bytes(logged_release) != value.release_bytes:
        raise AutonomousHostRejected("release bytes differ from the exact release-log entry")
    if release.release_sha256 != release_sha256 or arm_record.release_sha256 != release_sha256:
        raise AutonomousHostRejected("release identity does not match the armed release")
    if (
        release.build_artifact_sha256 != build_sha256
        or session_arm.release_code_sha256 != build_sha256
    ):
        raise AutonomousHostRejected(
            "runtime build identity does not match release and session arm"
        )
    if release.code_revision != revision:
        raise AutonomousHostRejected("runtime code revision does not match the release")
    if (
        arm_record.account_capability_id != capability_id
        or session_arm.account_fingerprint_sha256 != account_fingerprint
    ):
        raise AutonomousHostRejected("account capability or fingerprint does not match the arm")
    if (
        arm_record.source_ids != source_ids
        or arm_record.ledger_id != ledger_id
        or arm_record.process_id != process_id
    ):
        raise AutonomousHostRejected("source, ledger, or process identity does not match the arm")
    if arm_record.arm_id != session_arm.session_id:
        raise AutonomousHostRejected("arm record does not identify the autonomous session")
    if (
        arm_record.starts_at.astimezone(UTC) != session_arm.starts_at
        or arm_record.expires_at.astimezone(UTC) != session_arm.hard_flat_at
    ):
        raise AutonomousHostRejected("arm record clocks do not exactly bind the session deadline")

    session_date = session_arm.starts_at.astimezone(_EASTERN).date()
    rebuilt = AutonomousSessionArm.for_trading_date(
        session_id=session_arm.session_id,
        session_date=session_date,
        release_code_sha256=build_sha256,
        account_fingerprint_sha256=account_fingerprint,
    )
    if autonomous_session_arm_bytes(rebuilt) != value.session_arm_bytes:
        raise AutonomousHostRejected("session arm is not the deterministic frozen session rebuild")

    return ValidatedAutonomousHostAuthority(
        release=release,
        arm_record=arm_record,
        session_arm=session_arm,
        release_sha256=release_sha256,
        arm_record_sha256=arm_record.arm_sha256,
        session_arm_sha256=session_arm.arm_sha256,
        runtime_build_artifact_sha256=build_sha256,
        runtime_code_revision=revision,
        account_capability_id=capability_id,
        account_fingerprint_sha256=account_fingerprint,
        source_ids=source_ids,
        ledger_id=ledger_id,
        process_id=process_id,
        release_log_path=value.release_log_path,
        state_dir=value.state_dir,
        store_path=store_path,
    )


@dataclass(frozen=True, slots=True)
class SyntheticBrokerTruth:
    """Sanitized broker-shaped truth for a synthetic rehearsal checkpoint."""

    session_id: str
    session_arm_sha256: str
    account_fingerprint_sha256: str
    execution_protocol_sha256: str
    observed_at: datetime
    phase: str
    active_lifecycle_ids: tuple[str, ...]
    account_state_sha256: str
    orders_state_sha256: str
    positions_state_sha256: str
    open_order_count: int
    open_position_count: int
    is_flat: bool

    @classmethod
    def for_request(
        cls,
        request: ReconciliationRequest,
        *,
        account_state_sha256: str,
        orders_state_sha256: str,
        positions_state_sha256: str,
        open_order_count: int,
        open_position_count: int,
        is_flat: bool,
    ) -> SyntheticBrokerTruth:
        return cls(
            session_id=request.session_id,
            session_arm_sha256=request.arm_sha256,
            account_fingerprint_sha256=request.account_fingerprint_sha256,
            execution_protocol_sha256=request.execution_protocol_sha256,
            observed_at=request.observed_at,
            phase=request.phase,
            active_lifecycle_ids=request.active_lifecycle_ids,
            account_state_sha256=account_state_sha256,
            orders_state_sha256=orders_state_sha256,
            positions_state_sha256=positions_state_sha256,
            open_order_count=open_order_count,
            open_position_count=open_position_count,
            is_flat=is_flat,
        )


def _synthetic_broker_truth_payload(value: SyntheticBrokerTruth) -> dict[str, object]:
    if type(value) is not SyntheticBrokerTruth:
        raise AutonomousHostRejected("broker truth must be SyntheticBrokerTruth")
    active_ids = _canonical_bounded_identity_set(
        value.active_lifecycle_ids,
        path="broker_truth.active_lifecycle_ids",
    )
    for name in ("open_order_count", "open_position_count"):
        count = getattr(value, name)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise AutonomousHostRejected(f"broker_truth.{name} must be a non-negative integer")
    if type(value.is_flat) is not bool:
        raise AutonomousHostRejected("broker_truth.is_flat must be a boolean")
    expected_flat = (
        not active_ids and value.open_order_count == 0 and value.open_position_count == 0
    )
    if value.is_flat != expected_flat:
        raise AutonomousHostRejected(
            "broker truth flatness disagrees with active identities and open counts"
        )
    if active_ids and value.open_position_count == 0:
        raise AutonomousHostRejected(
            "active synthetic lifecycles require a nonzero open position count"
        )
    return {
        "account_fingerprint_sha256": _digest(
            value.account_fingerprint_sha256,
            path="broker_truth.account_fingerprint_sha256",
        ),
        "account_state_sha256": _digest(
            value.account_state_sha256,
            path="broker_truth.account_state_sha256",
        ),
        "active_lifecycle_ids": list(active_ids),
        "execution_protocol_sha256": _digest(
            value.execution_protocol_sha256,
            path="broker_truth.execution_protocol_sha256",
        ),
        "is_flat": value.is_flat,
        "observed_at": _timestamp_text(_utc(value.observed_at, path="broker_truth.observed_at")),
        "open_order_count": value.open_order_count,
        "open_position_count": value.open_position_count,
        "orders_state_sha256": _digest(
            value.orders_state_sha256,
            path="broker_truth.orders_state_sha256",
        ),
        "phase": _identifier(value.phase, path="broker_truth.phase"),
        "positions_state_sha256": _digest(
            value.positions_state_sha256,
            path="broker_truth.positions_state_sha256",
        ),
        "schema": SYNTHETIC_BROKER_TRUTH_SCHEMA,
        "schema_version": SYNTHETIC_BROKER_TRUTH_SCHEMA_VERSION,
        "session_arm_sha256": _digest(
            value.session_arm_sha256,
            path="broker_truth.session_arm_sha256",
        ),
        "session_id": _identifier(value.session_id, path="broker_truth.session_id"),
    }


def synthetic_broker_truth_bytes(value: SyntheticBrokerTruth) -> bytes:
    """Return the canonical bytes whose identity is computed by the host core."""

    return _canonical_json(_synthetic_broker_truth_payload(value))


def synthetic_broker_truth_sha256(value: SyntheticBrokerTruth) -> str:
    """Content-address one strict synthetic broker-truth fact."""

    return _sha256(synthetic_broker_truth_bytes(value))


@dataclass(frozen=True, slots=True)
class HostReconciliationObservation:
    session_id: str
    arm_sha256: str
    account_fingerprint_sha256: str
    execution_protocol_sha256: str
    observed_at: datetime
    phase: str
    active_lifecycle_ids: tuple[str, ...]
    status: HostReconciliationStatus
    broker_truth: SyntheticBrokerTruth

    @classmethod
    def complete(
        cls,
        request: ReconciliationRequest,
        *,
        broker_truth: SyntheticBrokerTruth,
    ) -> HostReconciliationObservation:
        return cls(
            session_id=request.session_id,
            arm_sha256=request.arm_sha256,
            account_fingerprint_sha256=request.account_fingerprint_sha256,
            execution_protocol_sha256=request.execution_protocol_sha256,
            observed_at=request.observed_at,
            phase=request.phase,
            active_lifecycle_ids=request.active_lifecycle_ids,
            status=HostReconciliationStatus.COMPLETE,
            broker_truth=broker_truth,
        )


@dataclass(frozen=True, slots=True)
class HostCandidateObservation:
    session_id: str
    window_id: str
    window_sha256: str
    opportunity_id: str
    candidate_id: str
    strategy_context_sha256: str

    @classmethod
    def for_window(
        cls,
        request: DueWindowRequest,
        *,
        opportunity_id: str,
        candidate_id: str,
        strategy_context_sha256: str,
    ) -> HostCandidateObservation:
        return cls(
            session_id=request.arm.session_id,
            window_id=request.window.window_id,
            window_sha256=request.window.window_sha256,
            opportunity_id=opportunity_id,
            candidate_id=candidate_id,
            strategy_context_sha256=strategy_context_sha256,
        )


@dataclass(frozen=True, slots=True)
class HostCandidateOutcome:
    session_id: str
    opportunity_id: str
    opportunity_sha256: str
    observed_at: datetime
    disposition: HostCandidateDisposition
    mutation_state: MutationState
    freeze: bool = False
    lifecycle_id: str | None = None
    terminal_flat_proof_sha256: str | None = None
    reason_code: str | None = None

    @classmethod
    def abstained(
        cls,
        request: CandidateProcessingRequest,
        *,
        reason_code: str,
    ) -> HostCandidateOutcome:
        return cls._base(
            request,
            disposition=HostCandidateDisposition.ABSTAINED,
            mutation_state=MutationState.NOT_ATTEMPTED,
            reason_code=reason_code,
        )

    @classmethod
    def rejected_before_mutation(
        cls,
        request: CandidateProcessingRequest,
        *,
        reason_code: str,
        freeze: bool = False,
    ) -> HostCandidateOutcome:
        return cls._base(
            request,
            disposition=HostCandidateDisposition.REJECTED_BEFORE_MUTATION,
            mutation_state=MutationState.NOT_ATTEMPTED,
            reason_code=reason_code,
            freeze=freeze,
        )

    @classmethod
    def active(
        cls,
        request: CandidateProcessingRequest,
        *,
        lifecycle_id: str,
    ) -> HostCandidateOutcome:
        return cls._base(
            request,
            disposition=HostCandidateDisposition.ACTIVE,
            mutation_state=MutationState.CONFIRMED,
            lifecycle_id=lifecycle_id,
        )

    @classmethod
    def terminal_flat(
        cls,
        request: CandidateProcessingRequest,
        *,
        terminal_flat_proof_sha256: str,
    ) -> HostCandidateOutcome:
        return cls._base(
            request,
            disposition=HostCandidateDisposition.TERMINAL_FLAT,
            mutation_state=MutationState.CONFIRMED,
            terminal_flat_proof_sha256=terminal_flat_proof_sha256,
        )

    @classmethod
    def manual_reconciliation_required(
        cls,
        request: CandidateProcessingRequest,
        *,
        mutation_state: MutationState,
        reason_code: str,
    ) -> HostCandidateOutcome:
        return cls._base(
            request,
            disposition=HostCandidateDisposition.MANUAL_RECONCILIATION_REQUIRED,
            mutation_state=mutation_state,
            reason_code=reason_code,
        )

    @classmethod
    def _base(
        cls,
        request: CandidateProcessingRequest,
        *,
        disposition: HostCandidateDisposition,
        mutation_state: MutationState,
        freeze: bool = False,
        lifecycle_id: str | None = None,
        terminal_flat_proof_sha256: str | None = None,
        reason_code: str | None = None,
    ) -> HostCandidateOutcome:
        return cls(
            session_id=request.arm.session_id,
            opportunity_id=request.opportunity.opportunity_id,
            opportunity_sha256=request.opportunity.opportunity_sha256,
            observed_at=request.observed_at,
            disposition=disposition,
            mutation_state=mutation_state,
            freeze=freeze,
            lifecycle_id=lifecycle_id,
            terminal_flat_proof_sha256=terminal_flat_proof_sha256,
            reason_code=reason_code,
        )


@dataclass(frozen=True, slots=True)
class HostLifecycleOutcome:
    session_id: str
    lifecycle_id: str
    lifecycle_sha256: str
    observed_at: datetime
    disposition: HostLifecycleDisposition
    mutation_state: MutationState
    terminal_flat_proof_sha256: str | None = None
    reason_code: str | None = None

    @classmethod
    def terminal_flat(
        cls,
        request: LifecycleCloseRequest,
        *,
        terminal_flat_proof_sha256: str,
    ) -> HostLifecycleOutcome:
        return cls(
            session_id=request.arm.session_id,
            lifecycle_id=request.lifecycle.lifecycle_id,
            lifecycle_sha256=request.lifecycle.lifecycle_sha256,
            observed_at=request.observed_at,
            disposition=HostLifecycleDisposition.TERMINAL_FLAT,
            mutation_state=MutationState.CONFIRMED,
            terminal_flat_proof_sha256=terminal_flat_proof_sha256,
        )

    @classmethod
    def manual_reconciliation_required(
        cls,
        request: LifecycleCloseRequest,
        *,
        mutation_state: MutationState,
        reason_code: str,
    ) -> HostLifecycleOutcome:
        return cls(
            session_id=request.arm.session_id,
            lifecycle_id=request.lifecycle.lifecycle_id,
            lifecycle_sha256=request.lifecycle.lifecycle_sha256,
            observed_at=request.observed_at,
            disposition=HostLifecycleDisposition.MANUAL_RECONCILIATION_REQUIRED,
            mutation_state=mutation_state,
            reason_code=reason_code,
        )


class HostReconciliationBackend(Protocol):
    def observe_reconciliation(
        self,
        request: ReconciliationRequest,
    ) -> HostReconciliationObservation: ...


class HostDueWindowBackend(Protocol):
    def observe_due_window(
        self,
        request: DueWindowRequest,
    ) -> tuple[HostCandidateObservation, ...]: ...


class HostCandidateBackend(Protocol):
    def process_candidate(self, request: CandidateProcessingRequest) -> HostCandidateOutcome: ...


class HostLifecycleBackend(Protocol):
    def close_lifecycle(self, request: LifecycleCloseRequest) -> HostLifecycleOutcome: ...


@dataclass(frozen=True, slots=True)
class AutonomousHostPlan:
    execution_class: HostExecutionClass
    reconciliation_backend: HostReconciliationBackend
    collector_backend: HostDueWindowBackend
    candidate_backend: HostCandidateBackend
    lifecycle_backend: HostLifecycleBackend


class HostReconciliationAdapter:
    def __init__(self, backend: HostReconciliationBackend) -> None:
        self._backend = backend

    def reconcile(self, request: ReconciliationRequest) -> ReconciliationReceipt:
        receipt, _ = self.reconcile_with_truth(request)
        return receipt

    def reconcile_with_truth(
        self,
        request: ReconciliationRequest,
    ) -> tuple[ReconciliationReceipt, str]:
        observation = self._backend.observe_reconciliation(request)
        if type(observation) is not HostReconciliationObservation:
            raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
        if observation.status is HostReconciliationStatus.INCOMPLETE:
            raise AutonomousHostBackendRejected("RECONCILIATION_INCOMPLETE")
        if observation.status is HostReconciliationStatus.AMBIGUOUS:
            raise AutonomousHostBackendRejected("RECONCILIATION_FAILED")
        active_ids = observation.active_lifecycle_ids
        if type(active_ids) is not tuple or active_ids != tuple(sorted(set(active_ids))):
            raise AutonomousHostBackendRejected("RECONCILIATION_IDENTITY_MISMATCH")
        if (
            observation.session_id != request.session_id
            or observation.arm_sha256 != request.arm_sha256
            or observation.account_fingerprint_sha256 != request.account_fingerprint_sha256
            or observation.execution_protocol_sha256 != request.execution_protocol_sha256
            or observation.observed_at != request.observed_at
            or observation.phase != request.phase
            or active_ids != request.active_lifecycle_ids
            or observation.status is not HostReconciliationStatus.COMPLETE
        ):
            raise AutonomousHostBackendRejected("RECONCILIATION_IDENTITY_MISMATCH")
        truth = observation.broker_truth
        try:
            broker_truth_sha256 = synthetic_broker_truth_sha256(truth)
        except AutonomousHostRejected as error:
            raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID") from error
        if (
            truth.session_id != request.session_id
            or truth.session_arm_sha256 != request.arm_sha256
            or truth.account_fingerprint_sha256 != request.account_fingerprint_sha256
            or truth.execution_protocol_sha256 != request.execution_protocol_sha256
            or truth.observed_at != request.observed_at
            or truth.phase != request.phase
            or truth.active_lifecycle_ids != request.active_lifecycle_ids
        ):
            raise AutonomousHostBackendRejected("RECONCILIATION_IDENTITY_MISMATCH")
        if truth.is_flat != (not request.active_lifecycle_ids):
            raise AutonomousHostBackendRejected("RECONCILIATION_INCOMPLETE")
        if request.phase == "FINAL" and (
            truth.active_lifecycle_ids
            or truth.open_order_count != 0
            or truth.open_position_count != 0
            or not truth.is_flat
        ):
            raise AutonomousHostBackendRejected("RECONCILIATION_INCOMPLETE")
        return ReconciliationReceipt.complete(request=request), broker_truth_sha256


class HostDueWindowCollectorAdapter:
    def __init__(self, backend: HostDueWindowBackend) -> None:
        self._backend = backend

    def collect_due(self, request: DueWindowRequest) -> tuple[AutonomousOpportunity, ...]:
        observations = self._backend.observe_due_window(request)
        if type(observations) is not tuple:
            raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
        result: list[AutonomousOpportunity] = []
        for observation in observations:
            if type(observation) is not HostCandidateObservation:
                raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
            if (
                observation.session_id != request.arm.session_id
                or observation.window_id != request.window.window_id
                or observation.window_sha256 != request.window.window_sha256
            ):
                raise AutonomousHostBackendRejected("ARM_IDENTITY_MISMATCH")
            result.append(
                AutonomousOpportunity.for_window(
                    arm=request.arm,
                    window_id=request.window.window_id,
                    opportunity_id=observation.opportunity_id,
                    candidate_id=observation.candidate_id,
                    strategy_context_sha256=observation.strategy_context_sha256,
                )
            )
        return tuple(result)


def _validate_candidate_outcome_shape(outcome: HostCandidateOutcome) -> None:
    if type(outcome.freeze) is not bool:
        raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
    if outcome.reason_code is not None and (
        not isinstance(outcome.reason_code, str) or not outcome.reason_code
    ):
        raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
    if outcome.lifecycle_id is not None and (
        not isinstance(outcome.lifecycle_id, str) or not outcome.lifecycle_id
    ):
        raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
    if outcome.terminal_flat_proof_sha256 is not None:
        try:
            _digest(
                outcome.terminal_flat_proof_sha256,
                path="candidate.terminal_flat_proof_sha256",
            )
        except AutonomousHostRejected as error:
            raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID") from error

    if outcome.disposition is HostCandidateDisposition.ABSTAINED:
        valid = (
            outcome.mutation_state is MutationState.NOT_ATTEMPTED
            and not outcome.freeze
            and outcome.lifecycle_id is None
            and outcome.terminal_flat_proof_sha256 is None
            and outcome.reason_code is not None
        )
    elif outcome.disposition is HostCandidateDisposition.REJECTED_BEFORE_MUTATION:
        valid = (
            outcome.mutation_state is MutationState.NOT_ATTEMPTED
            and outcome.lifecycle_id is None
            and outcome.terminal_flat_proof_sha256 is None
            and outcome.reason_code is not None
        )
    elif outcome.disposition is HostCandidateDisposition.ACTIVE:
        valid = (
            outcome.mutation_state is MutationState.CONFIRMED
            and not outcome.freeze
            and outcome.lifecycle_id is not None
            and outcome.terminal_flat_proof_sha256 is None
            and outcome.reason_code is None
        )
    elif outcome.disposition is HostCandidateDisposition.TERMINAL_FLAT:
        valid = (
            outcome.mutation_state is MutationState.CONFIRMED
            and not outcome.freeze
            and outcome.lifecycle_id is None
            and outcome.terminal_flat_proof_sha256 is not None
            and outcome.reason_code is None
        )
    elif outcome.disposition is HostCandidateDisposition.MANUAL_RECONCILIATION_REQUIRED:
        valid = (
            outcome.mutation_state in {MutationState.UNKNOWN, MutationState.PARTIAL}
            and not outcome.freeze
            and outcome.lifecycle_id is None
            and outcome.terminal_flat_proof_sha256 is None
            and outcome.reason_code is not None
        )
    else:
        valid = False
    if not valid:
        raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")


class HostCandidateProcessorAdapter:
    def __init__(self, backend: HostCandidateBackend) -> None:
        self._backend = backend

    def process(self, request: CandidateProcessingRequest) -> CandidateProcessingResult:
        outcome = self._backend.process_candidate(request)
        if type(outcome) is not HostCandidateOutcome:
            raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
        _validate_candidate_outcome_shape(outcome)
        if (
            outcome.session_id != request.arm.session_id
            or outcome.opportunity_id != request.opportunity.opportunity_id
            or outcome.opportunity_sha256 != request.opportunity.opportunity_sha256
            or outcome.observed_at != request.observed_at
        ):
            raise AutonomousHostBackendRejected("ARM_IDENTITY_MISMATCH")
        if outcome.disposition is HostCandidateDisposition.ABSTAINED:
            return CandidateProcessingResult.abstained(
                request=request,
                reason_code=outcome.reason_code or "PORT_OUTPUT_INVALID",
            )
        if outcome.disposition is HostCandidateDisposition.REJECTED_BEFORE_MUTATION:
            return CandidateProcessingResult.rejected_before_mutation(
                request=request,
                reason_code=outcome.reason_code or "PORT_OUTPUT_INVALID",
                freeze=outcome.freeze,
            )
        if outcome.disposition is HostCandidateDisposition.ACTIVE:
            assert outcome.lifecycle_id is not None
            return CandidateProcessingResult.active(
                request=request,
                lifecycle_id=outcome.lifecycle_id,
            )
        if outcome.disposition is HostCandidateDisposition.TERMINAL_FLAT:
            assert outcome.terminal_flat_proof_sha256 is not None
            return CandidateProcessingResult.terminal_flat(
                request=request,
                terminal_flat_proof_sha256=outcome.terminal_flat_proof_sha256,
            )
        if outcome.disposition is HostCandidateDisposition.MANUAL_RECONCILIATION_REQUIRED:
            return CandidateProcessingResult.manual_reconciliation_required(
                request=request,
                mutation_state=outcome.mutation_state,
                reason_code=outcome.reason_code or "PORT_OUTPUT_INVALID",
            )
        raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")


class HostLifecycleCloserAdapter:
    def __init__(self, backend: HostLifecycleBackend) -> None:
        self._backend = backend

    def close_and_reconcile(self, request: LifecycleCloseRequest) -> LifecycleCloseResult:
        outcome = self._backend.close_lifecycle(request)
        if type(outcome) is not HostLifecycleOutcome:
            raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
        if outcome.terminal_flat_proof_sha256 is not None:
            try:
                _digest(
                    outcome.terminal_flat_proof_sha256,
                    path="lifecycle.terminal_flat_proof_sha256",
                )
            except AutonomousHostRejected as error:
                raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID") from error
        if outcome.disposition is HostLifecycleDisposition.TERMINAL_FLAT:
            valid = (
                outcome.mutation_state is MutationState.CONFIRMED
                and outcome.terminal_flat_proof_sha256 is not None
                and outcome.reason_code is None
            )
        elif outcome.disposition is HostLifecycleDisposition.MANUAL_RECONCILIATION_REQUIRED:
            valid = (
                outcome.mutation_state in {MutationState.UNKNOWN, MutationState.PARTIAL}
                and outcome.terminal_flat_proof_sha256 is None
                and isinstance(outcome.reason_code, str)
                and bool(outcome.reason_code)
            )
        else:
            valid = False
        if not valid:
            raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")
        if (
            outcome.session_id != request.arm.session_id
            or outcome.lifecycle_id != request.lifecycle.lifecycle_id
            or outcome.lifecycle_sha256 != request.lifecycle.lifecycle_sha256
            or outcome.observed_at != request.observed_at
        ):
            raise AutonomousHostBackendRejected("ARM_IDENTITY_MISMATCH")
        if outcome.disposition is HostLifecycleDisposition.TERMINAL_FLAT:
            assert outcome.terminal_flat_proof_sha256 is not None
            return LifecycleCloseResult.terminal_flat(
                request=request,
                terminal_flat_proof_sha256=outcome.terminal_flat_proof_sha256,
            )
        if outcome.disposition is HostLifecycleDisposition.MANUAL_RECONCILIATION_REQUIRED:
            return LifecycleCloseResult.manual_reconciliation_required(
                request=request,
                mutation_state=outcome.mutation_state,
                reason_code=outcome.reason_code or "HARD_FLAT_UNRESOLVED",
            )
        raise AutonomousHostBackendRejected("PORT_OUTPUT_INVALID")


def _validate_plan(value: object) -> AutonomousHostPlan:
    if type(value) is not AutonomousHostPlan:
        raise AutonomousHostRejected("plan_factory must return AutonomousHostPlan")
    if value.execution_class is not HostExecutionClass.SYNTHETIC_FAKE:
        raise AutonomousHostRejected(
            "autonomous host plan must attest the closed SYNTHETIC_FAKE execution class"
        )
    required = (
        (value.reconciliation_backend, "observe_reconciliation"),
        (value.collector_backend, "observe_due_window"),
        (value.candidate_backend, "process_candidate"),
        (value.lifecycle_backend, "close_lifecycle"),
    )
    if any(not callable(getattr(backend, method, None)) for backend, method in required):
        raise AutonomousHostRejected("autonomous host plan contains an invalid backend")
    return value


@contextmanager
def _state_directory_lock(root: Path) -> Iterator[None]:
    _validate_existing_state_paths(root)
    root.mkdir(parents=True, exist_ok=True)
    _validate_existing_state_paths(root)
    lock_path = root / ".autonomous-host.lock"
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        raise AutonomousHostRejected("autonomous host lock must be a real file")
    handle = lock_path.open("a+b")
    if os.fstat(handle.fileno()).st_size == 0:
        handle.seek(0)
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    acquired = False
    try:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise AutonomousHostBusy(
                "another autonomous host invocation owns the state directory"
            ) from None
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _validate_timeline(
    value: object,
    *,
    arm: AutonomousSessionArm,
) -> tuple[datetime, ...]:
    if type(value) is not tuple or not value:
        raise AutonomousHostRejected("observation_timeline must be a non-empty immutable tuple")
    timeline = tuple(
        _utc(item, path=f"observation_timeline[{index}]") for index, item in enumerate(value)
    )
    if timeline != tuple(sorted(set(timeline))):
        raise AutonomousHostRejected("observation_timeline must be strictly increasing and unique")
    if timeline[0] < arm.starts_at or timeline[-1] > arm.hard_flat_at:
        raise AutonomousHostRejected("observation_timeline must remain inside the armed session")
    return timeline


@dataclass(frozen=True, slots=True)
class AutonomousHostReceipt:
    release_sha256: str
    arm_record_sha256: str
    session_arm_sha256: str
    runtime_build_artifact_sha256: str
    runtime_code_revision: str
    account_capability_id: str
    account_fingerprint_sha256: str
    execution_class: HostExecutionClass
    session_id: str
    disposition: AutonomousHostDisposition
    observed_at: datetime
    requested_timeline_count: int
    processed_opportunity_ids: tuple[str, ...]
    disposition_counts: Mapping[str, int]
    active_lifecycle_ids: tuple[str, ...]
    manual_reasons: tuple[str, ...]
    final_summary_sha256: str | None
    reconciliation_phase: str
    reconciliation_broker_truth_sha256: str | None
    terminal_flat_proven: bool
    receipt_sha256: str

    @classmethod
    def create(
        cls,
        *,
        authority: ValidatedAutonomousHostAuthority,
        disposition: AutonomousHostDisposition,
        observed_at: datetime,
        requested_timeline_count: int,
        processed_opportunity_ids: tuple[str, ...],
        disposition_counts: Mapping[str, int],
        active_lifecycle_ids: tuple[str, ...],
        manual_reasons: tuple[str, ...],
        final_summary_sha256: str | None,
        reconciliation_phase: str,
        reconciliation_broker_truth_sha256: str | None,
        terminal_flat_proven: bool,
    ) -> AutonomousHostReceipt:
        counts = MappingProxyType(
            _canonical_disposition_counts(
                disposition_counts,
                path="receipt.disposition_counts",
            )
        )
        processed_ids = _canonical_bounded_identity_set(
            processed_opportunity_ids,
            path="receipt.processed_opportunity_ids",
        )
        active_ids = _canonical_bounded_identity_set(
            active_lifecycle_ids,
            path="receipt.active_lifecycle_ids",
        )
        reason_codes = _canonical_manual_reason_codes(
            manual_reasons,
            path="receipt.manual_reasons",
        )
        draft = cls(
            release_sha256=authority.release_sha256,
            arm_record_sha256=authority.arm_record_sha256,
            session_arm_sha256=authority.session_arm_sha256,
            runtime_build_artifact_sha256=authority.runtime_build_artifact_sha256,
            runtime_code_revision=authority.runtime_code_revision,
            account_capability_id=authority.account_capability_id,
            account_fingerprint_sha256=authority.account_fingerprint_sha256,
            execution_class=HostExecutionClass.SYNTHETIC_FAKE,
            session_id=authority.session_arm.session_id,
            disposition=disposition,
            observed_at=_utc(observed_at, path="receipt.observed_at"),
            requested_timeline_count=requested_timeline_count,
            processed_opportunity_ids=processed_ids,
            disposition_counts=counts,
            active_lifecycle_ids=active_ids,
            manual_reasons=reason_codes,
            final_summary_sha256=final_summary_sha256,
            reconciliation_phase=reconciliation_phase,
            reconciliation_broker_truth_sha256=reconciliation_broker_truth_sha256,
            terminal_flat_proven=terminal_flat_proven,
            receipt_sha256="0" * 64,
        )
        return cls(
            release_sha256=draft.release_sha256,
            arm_record_sha256=draft.arm_record_sha256,
            session_arm_sha256=draft.session_arm_sha256,
            runtime_build_artifact_sha256=draft.runtime_build_artifact_sha256,
            runtime_code_revision=draft.runtime_code_revision,
            account_capability_id=draft.account_capability_id,
            account_fingerprint_sha256=draft.account_fingerprint_sha256,
            execution_class=draft.execution_class,
            session_id=draft.session_id,
            disposition=draft.disposition,
            observed_at=draft.observed_at,
            requested_timeline_count=draft.requested_timeline_count,
            processed_opportunity_ids=draft.processed_opportunity_ids,
            disposition_counts=draft.disposition_counts,
            active_lifecycle_ids=draft.active_lifecycle_ids,
            manual_reasons=draft.manual_reasons,
            final_summary_sha256=draft.final_summary_sha256,
            reconciliation_phase=draft.reconciliation_phase,
            reconciliation_broker_truth_sha256=(draft.reconciliation_broker_truth_sha256),
            terminal_flat_proven=draft.terminal_flat_proven,
            receipt_sha256=_sha256(_canonical_json(_receipt_payload(draft))),
        )

    def to_json_bytes(self) -> bytes:
        payload = _receipt_payload(self)
        expected = _sha256(_canonical_json(payload))
        if self.receipt_sha256 != expected:
            raise AutonomousHostRejected("autonomous host receipt self-hash is invalid")
        return _canonical_json({**payload, "receipt_sha256": expected})


def _receipt_payload(value: AutonomousHostReceipt) -> dict[str, object]:
    if type(value) is not AutonomousHostReceipt:
        raise AutonomousHostRejected("receipt must be AutonomousHostReceipt")
    if type(value.disposition) is not AutonomousHostDisposition:
        raise AutonomousHostRejected("receipt disposition is unsupported")
    if type(value.requested_timeline_count) is not int or value.requested_timeline_count <= 0:
        raise AutonomousHostRejected("receipt requested_timeline_count must be positive")
    if type(value.terminal_flat_proven) is not bool:
        raise AutonomousHostRejected("receipt terminal_flat_proven must be boolean")
    _identifier(value.session_id, path="receipt.session_id")
    processed_ids = _canonical_bounded_identity_set(
        value.processed_opportunity_ids,
        path="receipt.processed_opportunity_ids",
    )
    active_ids = _canonical_bounded_identity_set(
        value.active_lifecycle_ids,
        path="receipt.active_lifecycle_ids",
    )
    manual_reasons = _canonical_manual_reason_codes(
        value.manual_reasons,
        path="receipt.manual_reasons",
    )
    for name in (
        "release_sha256",
        "arm_record_sha256",
        "session_arm_sha256",
        "runtime_build_artifact_sha256",
        "account_fingerprint_sha256",
    ):
        _digest(getattr(value, name), path=f"receipt.{name}")
    _revision(value.runtime_code_revision, path="receipt.runtime_code_revision")
    _identifier(value.account_capability_id, path="receipt.account_capability_id")
    for name in ("final_summary_sha256", "reconciliation_broker_truth_sha256"):
        item = getattr(value, name)
        if item is not None:
            _digest(item, path=f"receipt.{name}")
    if value.execution_class is not HostExecutionClass.SYNTHETIC_FAKE:
        raise AutonomousHostRejected("receipt execution_class must remain SYNTHETIC_FAKE")
    if value.reconciliation_phase not in {"CHECKPOINT", "FINAL"}:
        raise AutonomousHostRejected("receipt reconciliation_phase is unsupported")
    if value.reconciliation_phase == "CHECKPOINT" and value.final_summary_sha256 is not None:
        raise AutonomousHostRejected("checkpoint receipt cannot contain a final summary")
    if value.reconciliation_phase == "FINAL" and value.final_summary_sha256 is None:
        raise AutonomousHostRejected("final receipt requires a final summary")
    is_terminal = value.disposition is AutonomousHostDisposition.TERMINAL
    if is_terminal:
        if (
            value.reconciliation_phase != "FINAL"
            or value.reconciliation_broker_truth_sha256 is None
            or not value.terminal_flat_proven
            or active_ids
            or manual_reasons
        ):
            raise AutonomousHostRejected(
                "terminal receipt requires final flat truth and no active or manual state"
            )
    elif value.terminal_flat_proven:
        raise AutonomousHostRejected("only a terminal receipt may assert terminal flatness")
    if value.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED:
        if not manual_reasons:
            raise AutonomousHostRejected(
                "manual-required receipt must contain a stable manual reason"
            )
    elif manual_reasons:
        raise AutonomousHostRejected("manual reasons require the manual-required disposition")
    if (
        value.disposition is AutonomousHostDisposition.INCOMPLETE
        and value.final_summary_sha256 is not None
    ):
        raise AutonomousHostRejected("incomplete receipt cannot contain a final summary")
    counts = _canonical_disposition_counts(
        value.disposition_counts,
        path="receipt.disposition_counts",
    )
    return {
        "active_lifecycle_ids": list(active_ids),
        "account_capability_id": value.account_capability_id,
        "account_fingerprint_sha256": value.account_fingerprint_sha256,
        "arm_record_sha256": value.arm_record_sha256,
        "claim_basis": AUTONOMOUS_HOST_CLAIM_BASIS,
        "claims": list(AUTONOMOUS_HOST_CLAIMS),
        "data_class": "SYNTHETIC_CONTRACT_FIXTURE",
        "disposition": value.disposition.value,
        "disposition_counts": counts,
        "execution_class": value.execution_class.value,
        "final_summary_sha256": value.final_summary_sha256,
        "manual_reasons": list(manual_reasons),
        "requested_timeline_count": value.requested_timeline_count,
        "observed_at": _timestamp_text(value.observed_at),
        "processed_opportunity_ids": list(processed_ids),
        "reconciliation_broker_truth_sha256": (value.reconciliation_broker_truth_sha256),
        "reconciliation_phase": value.reconciliation_phase,
        "release_sha256": value.release_sha256,
        "run_mode": "PAPER",
        "runtime_build_artifact_sha256": value.runtime_build_artifact_sha256,
        "runtime_code_revision": value.runtime_code_revision,
        "schema": AUTONOMOUS_HOST_RECEIPT_SCHEMA,
        "schema_version": AUTONOMOUS_HOST_RECEIPT_SCHEMA_VERSION,
        "session_arm_sha256": value.session_arm_sha256,
        "session_id": value.session_id,
        "terminal_flat_proven": value.terminal_flat_proven,
    }


HostPlanFactory = Callable[[ValidatedAutonomousHostAuthority], AutonomousHostPlan]


@dataclass(frozen=True, slots=True)
class AutonomousHostInvocation:
    """Complete delayed invocation returned by an operator-owned selector."""

    authority_input: AutonomousHostAuthorityInput
    observation_timeline: tuple[datetime, ...]
    plan_factory: HostPlanFactory


def run_autonomous_host_command(
    *,
    authority_input: AutonomousHostAuthorityInput,
    plan_factory: HostPlanFactory,
    observation_timeline: tuple[datetime, ...],
) -> AutonomousHostReceipt:
    """Validate, lock, run the timeline, and reconcile its final or checkpoint state."""

    authority = validate_autonomous_host_authority(authority_input)
    timeline = _validate_timeline(observation_timeline, arm=authority.session_arm)
    if not callable(plan_factory):
        raise AutonomousHostRejected("plan_factory must be callable")

    with (
        _state_directory_lock(authority.state_dir),
        AutonomousSessionStore(authority.store_path) as store,
    ):
        try:
            store.ensure_arm(authority.session_arm)
        except AutonomousStoreConflict as error:
            raise AutonomousHostRejected(
                "durable autonomous state conflicts with the validated session arm"
            ) from error
        existing_summary = store.final_summary(authority.session_arm.session_id)
        plan = _validate_plan(plan_factory(authority))
        reconciler = HostReconciliationAdapter(plan.reconciliation_backend)
        ports = AutonomousSessionPorts(
            reconciler=reconciler,
            collector=HostDueWindowCollectorAdapter(plan.collector_backend),
            processor=HostCandidateProcessorAdapter(plan.candidate_backend),
            lifecycle_closer=HostLifecycleCloserAdapter(plan.lifecycle_backend),
        )
        processed: set[str] = set()
        coordinator = AutonomousSessionCoordinator(
            arm=authority.session_arm,
            store=store,
            ports=ports,
            release_code_sha256=authority.runtime_build_artifact_sha256,
            account_fingerprint_sha256=authority.account_fingerprint_sha256,
        )
        if existing_summary is None:
            for observed_at in timeline:
                try:
                    result = coordinator.run(observed_at=observed_at)
                except Exception:
                    store.mark_manual_reconciliation_required(
                        arm=authority.session_arm,
                        reason_code="PORT_EXCEPTION",
                        observed_at=observed_at,
                    )
                    continue
                processed.update(result.processed_opportunity_ids)

        summary = store.final_summary(authority.session_arm.session_id)
        reconciliation_phase = "FINAL" if summary is not None else "CHECKPOINT"
        reconciliation_at = summary.finalized_at if summary is not None else timeline[-1]
        reconciliation_request = ReconciliationRequest(
            session_id=authority.session_arm.session_id,
            arm_sha256=authority.session_arm.arm_sha256,
            account_fingerprint_sha256=authority.account_fingerprint_sha256,
            execution_protocol_sha256=authority.session_arm.execution_protocol_sha256,
            observed_at=reconciliation_at,
            phase=reconciliation_phase,
            active_lifecycle_ids=store.active_lifecycle_ids(authority.session_arm.session_id),
        )
        reconciliation_broker_truth_sha256: str | None = None
        try:
            (
                _,
                reconciliation_broker_truth_sha256,
            ) = reconciler.reconcile_with_truth(reconciliation_request)
        except AutonomousHostBackendRejected as error:
            store.mark_manual_reconciliation_required(
                arm=authority.session_arm,
                reason_code=error.reason_code,
                observed_at=reconciliation_at,
            )
        except Exception:
            store.mark_manual_reconciliation_required(
                arm=authority.session_arm,
                reason_code="RECONCILIATION_FAILED",
                observed_at=reconciliation_at,
            )

        summary = store.final_summary(authority.session_arm.session_id)
        active_ids = store.active_lifecycle_ids(authority.session_arm.session_id)
        manual_reasons = store.manual_reasons(authority.session_arm.session_id)
        session_state = store.session_state(authority.session_arm.session_id)
        if manual_reasons or session_state == "MANUAL_RECONCILIATION_REQUIRED":
            disposition = AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED
        elif (
            summary is not None
            and summary.terminal_flat_proven
            and not active_ids
            and reconciliation_phase == "FINAL"
            and reconciliation_broker_truth_sha256 is not None
        ):
            disposition = AutonomousHostDisposition.TERMINAL
        else:
            disposition = AutonomousHostDisposition.INCOMPLETE
        terminal_flat_proven = (
            disposition is AutonomousHostDisposition.TERMINAL
            and summary is not None
            and summary.terminal_flat_proven
        )
        return AutonomousHostReceipt.create(
            authority=authority,
            disposition=disposition,
            observed_at=reconciliation_at,
            requested_timeline_count=len(timeline),
            processed_opportunity_ids=tuple(sorted(processed)),
            disposition_counts=store.disposition_counts(authority.session_arm.session_id),
            active_lifecycle_ids=active_ids,
            manual_reasons=manual_reasons,
            final_summary_sha256=(None if summary is None else summary.summary_sha256),
            reconciliation_phase=reconciliation_phase,
            reconciliation_broker_truth_sha256=reconciliation_broker_truth_sha256,
            terminal_flat_proven=terminal_flat_proven,
        )


def run_autonomous_host_invocation(
    invocation: AutonomousHostInvocation,
) -> AutonomousHostReceipt:
    if type(invocation) is not AutonomousHostInvocation:
        raise AutonomousHostRejected("invocation must be AutonomousHostInvocation")
    return run_autonomous_host_command(
        authority_input=invocation.authority_input,
        plan_factory=invocation.plan_factory,
        observation_timeline=invocation.observation_timeline,
    )


__all__ = [
    "AUTONOMOUS_HOST_CLAIMS",
    "AUTONOMOUS_HOST_CLAIM_BASIS",
    "AUTONOMOUS_HOST_RECEIPT_SCHEMA",
    "AUTONOMOUS_HOST_RECEIPT_SCHEMA_VERSION",
    "AUTONOMOUS_HOST_STATE_FILENAME",
    "SYNTHETIC_BROKER_TRUTH_SCHEMA",
    "SYNTHETIC_BROKER_TRUTH_SCHEMA_VERSION",
    "AutonomousHostAuthorityInput",
    "AutonomousHostBackendRejected",
    "AutonomousHostBusy",
    "AutonomousHostDisposition",
    "AutonomousHostInvocation",
    "AutonomousHostPlan",
    "AutonomousHostReceipt",
    "AutonomousHostRejected",
    "HostCandidateBackend",
    "HostCandidateDisposition",
    "HostCandidateObservation",
    "HostCandidateOutcome",
    "HostCandidateProcessorAdapter",
    "HostDueWindowBackend",
    "HostDueWindowCollectorAdapter",
    "HostExecutionClass",
    "HostLifecycleBackend",
    "HostLifecycleCloserAdapter",
    "HostLifecycleDisposition",
    "HostLifecycleOutcome",
    "HostPlanFactory",
    "HostReconciliationAdapter",
    "HostReconciliationBackend",
    "HostReconciliationObservation",
    "HostReconciliationStatus",
    "SyntheticBrokerTruth",
    "ValidatedAutonomousHostAuthority",
    "run_autonomous_host_command",
    "run_autonomous_host_invocation",
    "synthetic_broker_truth_bytes",
    "synthetic_broker_truth_sha256",
    "validate_autonomous_host_authority",
]
