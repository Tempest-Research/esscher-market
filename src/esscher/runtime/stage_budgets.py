"""Deadline-aware per-stage latency budgets derived from frozen contracts.

This module turns the owner-preregistered p95 execution-latency profile and one
armed autonomous session into an explicit, content-addressed budget set consumed
by the deadline-aware application service.  Every value is derived from frozen
package contracts; nothing here reads a wall clock, contacts a provider, or
creates broker, account, or network capability.  Budgets are rehearsal bounds
for the synthetic PAPER stack and are labelled accordingly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from esscher.contracts.latency_profile import ValidatedLatencyProfile
from esscher.contracts.reasoner_route import packaged_route_descriptor_v2_bytes
from esscher.runtime.autonomous import (
    AutonomousSessionArm,
    autonomous_session_arm_bytes,
)
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes

STAGE_BUDGETS_SCHEMA: Final = "esscher.stage_budgets"
STAGE_BUDGETS_SCHEMA_VERSION: Final = 1
ARM_WINDOW_SET_SCHEMA: Final = "esscher.arm_window_set"
ARM_WINDOW_SET_SCHEMA_VERSION: Final = 1
STAGE_BUDGET_CLAIMS: Final = ("SYNTHETIC_FAKE", "NOT_ALPHA_EVIDENCE")

FROZEN_REASONER_RETRY_COUNT: Final = 0
"""Documented fallback for the frozen one-call/no-retry reasoner call policy.

The packaged V2 route descriptor pins ``call_policy.retry_count`` to zero and
the contract validator rejects any other value, so the derived retry backoff is
always zero milliseconds.  The fallback constant exists only so budget
derivation stays deterministic if the packaged descriptor bytes ever become
unreadable; it never authorizes a retry.
"""


class StageBudgetsRejected(ValueError):
    """Raised when stage budgets are malformed or exceed the armed window."""


def _timestamp_text(value: datetime) -> str:
    """Render one timezone-aware instant as canonical UTC text."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StageBudgetsRejected("window clocks must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _window_payload(arm: AutonomousSessionArm, index: int) -> dict[str, object]:
    window = arm.windows[index]
    return {
        "candidate_ids": list(window.candidate_ids),
        "closes_at": _timestamp_text(window.closes_at),
        "opens_at": _timestamp_text(window.opens_at),
        "window_id": window.window_id,
    }


def arm_window_set_sha256(arm: AutonomousSessionArm) -> str:
    """Content-address one arm's complete frozen window schedule."""

    autonomous_session_arm_bytes(arm)
    payload = {
        "arm_sha256": arm.arm_sha256,
        "schema": ARM_WINDOW_SET_SCHEMA,
        "schema_version": ARM_WINDOW_SET_SCHEMA_VERSION,
        "windows": [_window_payload(arm, index) for index in range(len(arm.windows))],
    }
    return sha256_bytes(canonical_json_bytes(payload))


def frozen_reasoner_retry_count() -> int:
    """Return the frozen reasoner retry count read from the packaged contract."""

    try:
        descriptor = json.loads(packaged_route_descriptor_v2_bytes().decode("utf-8"))
        call_policy = descriptor["call_policy"]
        retry_count = call_policy["retry_count"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return FROZEN_REASONER_RETRY_COUNT
    if type(retry_count) is not int or retry_count < 0:
        return FROZEN_REASONER_RETRY_COUNT
    return retry_count


@dataclass(frozen=True, slots=True)
class StageBudgets:
    """Explicit per-stage millisecond budgets bound to one profile and arm."""

    reasoner_ms: int
    market_data_ms: int
    broker_ms: int
    retry_backoff_ms: int
    shutdown_reserve_ms: int
    profile_sha256: str
    arm_window_sha256: str


def _validate_budget_value(value: object, *, path: str) -> int:
    if type(value) is not int or value < 0:
        raise StageBudgetsRejected(f"{path} must be a non-negative integer of milliseconds")
    return value


def _validate_digest(value: object, *, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise StageBudgetsRejected(f"{path} must be a lowercase SHA-256 digest")
    return value


def derive_stage_budgets(
    *,
    profile: ValidatedLatencyProfile,
    arm: AutonomousSessionArm,
) -> StageBudgets:
    """Derive the deterministic per-stage budgets for one profile and arm."""

    if type(profile) is not ValidatedLatencyProfile:
        raise StageBudgetsRejected("profile must be a ValidatedLatencyProfile")
    autonomous_session_arm_bytes(arm)
    p95 = _validate_budget_value(profile.p95_latency_ms, path="profile.p95_latency_ms")
    retry_backoff_ms = _validate_budget_value(
        frozen_reasoner_retry_count() * p95,
        path="retry_backoff_ms",
    )
    return StageBudgets(
        reasoner_ms=p95,
        market_data_ms=p95,
        broker_ms=p95,
        retry_backoff_ms=retry_backoff_ms,
        shutdown_reserve_ms=p95,
        profile_sha256=_validate_digest(profile.content_sha256, path="profile.content_sha256"),
        arm_window_sha256=arm_window_set_sha256(arm),
    )


def stage_budgets_payload(value: StageBudgets) -> dict[str, object]:
    """Return the validated canonical payload of one budget set."""

    if type(value) is not StageBudgets:
        raise StageBudgetsRejected("budgets must be a StageBudgets")
    return {
        "arm_window_sha256": _validate_digest(
            value.arm_window_sha256,
            path="budgets.arm_window_sha256",
        ),
        "broker_ms": _validate_budget_value(value.broker_ms, path="budgets.broker_ms"),
        "claims": list(STAGE_BUDGET_CLAIMS),
        "market_data_ms": _validate_budget_value(
            value.market_data_ms,
            path="budgets.market_data_ms",
        ),
        "profile_sha256": _validate_digest(value.profile_sha256, path="budgets.profile_sha256"),
        "reasoner_ms": _validate_budget_value(value.reasoner_ms, path="budgets.reasoner_ms"),
        "retry_backoff_ms": _validate_budget_value(
            value.retry_backoff_ms,
            path="budgets.retry_backoff_ms",
        ),
        "schema": STAGE_BUDGETS_SCHEMA,
        "schema_version": STAGE_BUDGETS_SCHEMA_VERSION,
        "shutdown_reserve_ms": _validate_budget_value(
            value.shutdown_reserve_ms,
            path="budgets.shutdown_reserve_ms",
        ),
    }


def stage_budgets_bytes(value: StageBudgets) -> bytes:
    """Serialize one budget set as exact canonical JSON bytes."""

    return canonical_json_bytes(stage_budgets_payload(value))


def stage_budgets_sha256(value: StageBudgets) -> str:
    """Content-address one budget set over its canonical payload."""

    return sha256_bytes(stage_budgets_bytes(value))


def window_duration_ms(arm: AutonomousSessionArm, index: int) -> int:
    """Return one armed window's exact duration in milliseconds."""

    window = arm.windows[index]
    opens_at = window.opens_at.astimezone(UTC)
    closes_at = window.closes_at.astimezone(UTC)
    if closes_at <= opens_at:
        raise StageBudgetsRejected("window must close after it opens")
    delta = closes_at - opens_at
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def per_window_budget_total_ms(value: StageBudgets) -> int:
    """Return the milliseconds one window run must reserve across all stages."""

    stage_budgets_payload(value)
    return (
        value.market_data_ms
        + value.reasoner_ms
        + value.broker_ms
        + value.retry_backoff_ms
        + value.shutdown_reserve_ms
    )


def validate_stage_budgets_within_window(
    budgets: StageBudgets,
    arm: AutonomousSessionArm,
) -> None:
    """Fail closed when the per-window budget total exceeds any armed window."""

    autonomous_session_arm_bytes(arm)
    stage_budgets_payload(budgets)
    if budgets.arm_window_sha256 != arm_window_set_sha256(arm):
        raise StageBudgetsRejected("stage budgets are not bound to this armed window schedule")
    total = per_window_budget_total_ms(budgets)
    shortest = min(window_duration_ms(arm, index) for index in range(len(arm.windows)))
    if total > shortest:
        raise StageBudgetsRejected(
            "per-window stage budgets exceed the shortest armed window length"
        )


__all__ = [
    "ARM_WINDOW_SET_SCHEMA",
    "ARM_WINDOW_SET_SCHEMA_VERSION",
    "FROZEN_REASONER_RETRY_COUNT",
    "STAGE_BUDGETS_SCHEMA",
    "STAGE_BUDGETS_SCHEMA_VERSION",
    "STAGE_BUDGET_CLAIMS",
    "StageBudgets",
    "StageBudgetsRejected",
    "arm_window_set_sha256",
    "derive_stage_budgets",
    "frozen_reasoner_retry_count",
    "per_window_budget_total_ms",
    "stage_budgets_bytes",
    "stage_budgets_payload",
    "stage_budgets_sha256",
    "validate_stage_budgets_within_window",
    "window_duration_ms",
]
