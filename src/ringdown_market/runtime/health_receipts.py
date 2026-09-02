"""Canonical operational health receipts for the deadline-aware service.

A health receipt is the closed operational picture of one application-service
run: per-stage observed latencies against the derived budgets, source
staleness against the frozen V2 risk-policy truth age, dependency degradation
reason codes, option-event reconciliation lag, duplicate suppression counts,
and the explicit circuit state.  Receipts are content-addressed, synthetic
labelled, and never claim alpha, historical, or broker-connectivity evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

OPERATIONAL_HEALTH_RECEIPT_SCHEMA: Final = "esscher.operational_health_receipt"
OPERATIONAL_HEALTH_RECEIPT_SCHEMA_VERSION: Final = 1
OPERATIONAL_HEALTH_CLAIMS: Final = ("SYNTHETIC_FAKE", "NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA")


class CircuitState(StrEnum):
    """The only operational circuit states the service may attest."""

    NOMINAL = "NOMINAL"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    FROZEN = "FROZEN"


class HealthReceiptRejected(ValueError):
    """Raised when an operational health receipt input is malformed."""


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HealthReceiptRejected("observed_at must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _digest(value: object, *, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise HealthReceiptRejected(f"{path} must be a lowercase SHA-256 digest")
    return value


def _run_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HealthReceiptRejected("run_id must be non-empty exact text")
    return value


def _bounded_code(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HealthReceiptRejected(f"{path} must be a non-empty reason code")
    return value


def _sorted_unique_codes(values: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise HealthReceiptRejected(f"{path} must be a sequence of reason codes")
    result = tuple(sorted({_bounded_code(item, path=path) for item in values}))
    return result


@dataclass(frozen=True, slots=True)
class SourceStaleness:
    """One source's observed truth age against the frozen policy maximum."""

    source_id: str
    age_seconds: int
    max_age_seconds: int
    stale: bool


def source_staleness_payload(value: SourceStaleness) -> dict[str, object]:
    """Return the validated canonical payload of one staleness observation."""

    if type(value) is not SourceStaleness:
        raise HealthReceiptRejected("staleness entry must be a SourceStaleness")
    if type(value.age_seconds) is not int or type(value.max_age_seconds) is not int:
        raise HealthReceiptRejected("staleness ages must be integers of seconds")
    if value.max_age_seconds <= 0:
        raise HealthReceiptRejected("staleness maximum age must be positive")
    if type(value.stale) is not bool:
        raise HealthReceiptRejected("staleness flag must be a boolean")
    if value.stale != (value.age_seconds > value.max_age_seconds):
        raise HealthReceiptRejected("staleness flag disagrees with the observed age")
    return {
        "age_seconds": value.age_seconds,
        "max_age_seconds": value.max_age_seconds,
        "source_id": _bounded_code(value.source_id, path="staleness.source_id"),
        "stale": value.stale,
    }


@dataclass(frozen=True, slots=True)
class OperationalHealthReceipt:
    """The closed operational health picture of one service run."""

    run_id: str
    arm_sha256: str
    observed_at: datetime
    budget_sha256: str
    stage_latencies: Mapping[str, int]
    budget_violations: tuple[str, ...]
    staleness: tuple[SourceStaleness, ...]
    dependency_degradation: tuple[str, ...]
    reconciliation_lag_ms: int | None
    duplicate_suppressions: int
    circuit_state: CircuitState
    claims: tuple[str, ...]

    def to_json_bytes(self) -> bytes:
        """Serialize this receipt as exact canonical JSON bytes."""

        return health_receipt_bytes(self)


def build_operational_health_receipt(
    *,
    run_id: str,
    arm_sha256: str,
    observed_at: datetime,
    budget_sha256: str,
    stage_latencies: Mapping[str, int],
    budget_violations: tuple[str, ...] = (),
    staleness: tuple[SourceStaleness, ...] = (),
    dependency_degradation: tuple[str, ...] = (),
    reconciliation_lag_ms: int | None = None,
    duplicate_suppressions: int = 0,
    circuit_state: CircuitState = CircuitState.NOMINAL,
    claims: tuple[str, ...] = OPERATIONAL_HEALTH_CLAIMS,
) -> OperationalHealthReceipt:
    """Validate every field and freeze one operational health receipt."""

    if not isinstance(stage_latencies, Mapping):
        raise HealthReceiptRejected("stage_latencies must be a mapping")
    latencies: dict[str, int] = {}
    for stage, latency in stage_latencies.items():
        name = _bounded_code(stage, path="stage_latencies.stage")
        if type(latency) is not int or latency < 0:
            raise HealthReceiptRejected("stage latencies must be non-negative integers")
        latencies[name] = latency
    if reconciliation_lag_ms is not None and (
        type(reconciliation_lag_ms) is not int or reconciliation_lag_ms < 0
    ):
        raise HealthReceiptRejected("reconciliation_lag_ms must be None or a non-negative int")
    if type(duplicate_suppressions) is not int or duplicate_suppressions < 0:
        raise HealthReceiptRejected("duplicate_suppressions must be a non-negative integer")
    if not isinstance(circuit_state, CircuitState):
        raise HealthReceiptRejected("circuit_state must be a CircuitState")
    if tuple(claims) != OPERATIONAL_HEALTH_CLAIMS:
        raise HealthReceiptRejected("claims must equal the frozen synthetic health claim set")
    if not isinstance(staleness, tuple):
        raise HealthReceiptRejected("staleness must be an immutable tuple")
    source_ids = tuple(entry.source_id for entry in staleness)
    if len(source_ids) != len(set(source_ids)):
        raise HealthReceiptRejected("staleness source identities must be unique")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise HealthReceiptRejected("observed_at must be a timezone-aware datetime")
    return OperationalHealthReceipt(
        run_id=_run_id(run_id),
        arm_sha256=_digest(arm_sha256, path="arm_sha256"),
        observed_at=observed_at.astimezone(UTC),
        budget_sha256=_digest(budget_sha256, path="budget_sha256"),
        stage_latencies=MappingProxyType(dict(sorted(latencies.items()))),
        budget_violations=_sorted_unique_codes(budget_violations, path="budget_violations"),
        staleness=staleness,
        dependency_degradation=_sorted_unique_codes(
            dependency_degradation,
            path="dependency_degradation",
        ),
        reconciliation_lag_ms=reconciliation_lag_ms,
        duplicate_suppressions=duplicate_suppressions,
        circuit_state=circuit_state,
        claims=tuple(claims),
    )


def _reject_observed_at(value: object) -> datetime:
    raise HealthReceiptRejected("observed_at must be a timezone-aware datetime")


def health_receipt_payload(value: OperationalHealthReceipt) -> dict[str, object]:
    """Return the validated canonical payload of one health receipt."""

    if type(value) is not OperationalHealthReceipt:
        raise HealthReceiptRejected("receipt must be an OperationalHealthReceipt")
    return {
        "arm_sha256": _digest(value.arm_sha256, path="receipt.arm_sha256"),
        "budget_sha256": _digest(value.budget_sha256, path="receipt.budget_sha256"),
        "budget_violations": list(
            _sorted_unique_codes(value.budget_violations, path="receipt.budget_violations")
        ),
        "circuit_state": value.circuit_state.value
        if isinstance(value.circuit_state, CircuitState)
        else _reject_circuit(value.circuit_state),
        "claims": list(value.claims),
        "dependency_degradation": list(
            _sorted_unique_codes(
                value.dependency_degradation,
                path="receipt.dependency_degradation",
            )
        ),
        "duplicate_suppressions": value.duplicate_suppressions,
        "observed_at": _timestamp_text(value.observed_at),
        "reconciliation_lag_ms": value.reconciliation_lag_ms,
        "run_id": _run_id(value.run_id),
        "schema": OPERATIONAL_HEALTH_RECEIPT_SCHEMA,
        "schema_version": OPERATIONAL_HEALTH_RECEIPT_SCHEMA_VERSION,
        "stage_latencies": dict(value.stage_latencies),
        "staleness": [source_staleness_payload(entry) for entry in value.staleness],
    }


def _reject_circuit(value: object) -> str:
    raise HealthReceiptRejected("circuit_state must be a CircuitState")


def health_receipt_bytes(value: OperationalHealthReceipt) -> bytes:
    """Serialize one health receipt as exact canonical JSON bytes."""

    if tuple(value.claims) != OPERATIONAL_HEALTH_CLAIMS:
        raise HealthReceiptRejected("claims must equal the frozen synthetic health claim set")
    return canonical_json_bytes(health_receipt_payload(value))


def health_receipt_sha256(value: OperationalHealthReceipt) -> str:
    """Content-address one operational health receipt."""

    return sha256_bytes(health_receipt_bytes(value))


__all__ = [
    "OPERATIONAL_HEALTH_CLAIMS",
    "OPERATIONAL_HEALTH_RECEIPT_SCHEMA",
    "OPERATIONAL_HEALTH_RECEIPT_SCHEMA_VERSION",
    "CircuitState",
    "HealthReceiptRejected",
    "OperationalHealthReceipt",
    "SourceStaleness",
    "build_operational_health_receipt",
    "health_receipt_bytes",
    "health_receipt_payload",
    "health_receipt_sha256",
    "source_staleness_payload",
]
