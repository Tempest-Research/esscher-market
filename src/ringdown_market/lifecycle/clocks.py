"""Frozen lifecycle exit-plan clocks.

The approved strategy release supplies the exact observation, entry, holding,
time-exit, and flattening clocks for one event. These clocks are never
hard-coded, inferred, or altered by model prose: when the exit plan is absent
or its source is unverified there is no fallback and the lifecycle fails
closed with ``EXIT_PLAN_UNVERIFIED``. Earnings BMO/AMC and macro clocks are
carried per-cohort and cannot be blended.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from ringdown_market.lifecycle.reasons import LifecycleReason, LifecycleRejected, _reject
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

LIFECYCLE_CLOCKS_SCHEMA: Final = "esscher.lifecycle_exit_plan"
LIFECYCLE_CLOCKS_SCHEMA_VERSION: Final = 1
_KNOWN_COHORTS: Final = frozenset({"BMO", "AMC", "BLS_JOLTS", "BLS_EMPLOYMENT_SITUATION"})

_CLOCK_FIELDS: Final = frozenset(
    {
        "schema",
        "schema_version",
        "event_run_id",
        "cohort_id",
        "policy_sha256",
        "source_sha256",
        "observation_window_start_at",
        "observation_window_end_at",
        "entry_deadline_at",
        "time_exit_at",
        "flattening_deadline_at",
    }
)


class _DuplicateFieldError(ValueError):
    pass


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise _reject(
        LifecycleReason.UNSUPPORTED_INPUT,
        "lifecycle_clocks",
        f"non-finite JSON constant {value} is forbidden",
    )


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str):
        raise _reject(LifecycleReason.UNSUPPORTED_INPUT, path, "must be ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _reject(LifecycleReason.UNSUPPORTED_INPUT, path, str(error)) from None
    if parsed.tzinfo != UTC:
        raise _reject(LifecycleReason.UNSUPPORTED_INPUT, path, "must be UTC")
    return parsed


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo != UTC:
        raise _reject(LifecycleReason.UNSUPPORTED_INPUT, "clock", "clock must be UTC")
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class LifecycleClocks:
    """One event's frozen exit-plan clocks, bound to its source."""

    event_run_id: str
    cohort_id: str
    policy_sha256: str
    source_sha256: str
    observation_window_start_at: datetime
    observation_window_end_at: datetime
    entry_deadline_at: datetime
    time_exit_at: datetime
    flattening_deadline_at: datetime

    def __post_init__(self) -> None:
        if not self.event_run_id.strip():
            raise ValueError("event_run_id must be non-empty text")
        if self.cohort_id not in _KNOWN_COHORTS:
            raise ValueError("cohort_id is not a registered cohort")
        for field in ("policy_sha256", "source_sha256"):
            if len(getattr(self, field)) != 64:
                raise ValueError(f"{field} must be a SHA-256 digest")
        ordered = (
            self.observation_window_start_at,
            self.observation_window_end_at,
            self.entry_deadline_at,
            self.time_exit_at,
            self.flattening_deadline_at,
        )
        for earlier, later in itertools.pairwise(ordered):
            if earlier >= later:
                raise ValueError("lifecycle clocks must be strictly ordered")

    @property
    def source_verified(self) -> bool:
        """The clocks are usable only when their source is bound.

        An all-zero source digest is the unbound placeholder and is treated as
        unverified; there is no fallback.
        """

        value = self.source_sha256
        if len(value) != 64 or value == "0" * 64:
            return False
        return all(c in "0123456789abcdef" for c in value)


def lifecycle_clocks_payload(value: LifecycleClocks) -> dict[str, object]:
    """Return the single versioned serialization for one exit plan."""

    return {
        "schema": LIFECYCLE_CLOCKS_SCHEMA,
        "schema_version": LIFECYCLE_CLOCKS_SCHEMA_VERSION,
        "event_run_id": value.event_run_id,
        "cohort_id": value.cohort_id,
        "policy_sha256": value.policy_sha256,
        "source_sha256": value.source_sha256,
        "observation_window_start_at": _timestamp_text(value.observation_window_start_at),
        "observation_window_end_at": _timestamp_text(value.observation_window_end_at),
        "entry_deadline_at": _timestamp_text(value.entry_deadline_at),
        "time_exit_at": _timestamp_text(value.time_exit_at),
        "flattening_deadline_at": _timestamp_text(value.flattening_deadline_at),
    }


def lifecycle_clocks_bytes(value: LifecycleClocks) -> bytes:
    """Serialize one exit plan to deterministic canonical bytes."""

    return canonical_json_bytes(lifecycle_clocks_payload(value))


def lifecycle_clocks_sha256(value: LifecycleClocks) -> str:
    return sha256_bytes(lifecycle_clocks_bytes(value))


def parse_lifecycle_clocks(raw: bytes) -> LifecycleClocks:
    """Strictly parse canonical exit-plan bytes, failing closed."""

    if type(raw) is not bytes:
        raise _reject(LifecycleReason.UNSUPPORTED_INPUT, "lifecycle_clocks", "input must be bytes")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except _DuplicateFieldError as error:
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT, "lifecycle_clocks", f"duplicate JSON field {error}"
        ) from None
    except LifecycleRejected:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _reject(LifecycleReason.UNSUPPORTED_INPUT, "lifecycle_clocks", str(error)) from None
    if not isinstance(payload, dict):
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT, "lifecycle_clocks", "root must be an object"
        )
    actual = frozenset(payload)
    missing = sorted(_CLOCK_FIELDS - actual)
    unknown = sorted(actual - _CLOCK_FIELDS)
    if missing or unknown:
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT,
            "lifecycle_clocks",
            f"field mismatch; missing={missing} unknown={unknown}",
        )
    if payload["schema"] != LIFECYCLE_CLOCKS_SCHEMA:
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT, "lifecycle_clocks.schema", "unsupported schema"
        )
    if payload["schema_version"] != LIFECYCLE_CLOCKS_SCHEMA_VERSION:
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT,
            "lifecycle_clocks.schema_version",
            "unsupported schema version",
        )
    if payload["cohort_id"] not in _KNOWN_COHORTS:
        raise _reject(
            LifecycleReason.EXIT_PLAN_COHORT_MISMATCH,
            "lifecycle_clocks.cohort_id",
            "cohort is not a registered cohort",
        )
    try:
        result = LifecycleClocks(
            event_run_id=str(payload["event_run_id"]),
            cohort_id=str(payload["cohort_id"]),
            policy_sha256=str(payload["policy_sha256"]),
            source_sha256=str(payload["source_sha256"]),
            observation_window_start_at=_timestamp(
                payload["observation_window_start_at"],
                path="lifecycle_clocks.observation_window_start_at",
            ),
            observation_window_end_at=_timestamp(
                payload["observation_window_end_at"],
                path="lifecycle_clocks.observation_window_end_at",
            ),
            entry_deadline_at=_timestamp(
                payload["entry_deadline_at"], path="lifecycle_clocks.entry_deadline_at"
            ),
            time_exit_at=_timestamp(payload["time_exit_at"], path="lifecycle_clocks.time_exit_at"),
            flattening_deadline_at=_timestamp(
                payload["flattening_deadline_at"], path="lifecycle_clocks.flattening_deadline_at"
            ),
        )
    except ValueError as error:
        raise _reject(
            LifecycleReason.EXIT_PLAN_CLOCKS_MISORDERED, "lifecycle_clocks", str(error)
        ) from None
    if lifecycle_clocks_bytes(result) != raw:
        raise _reject(
            LifecycleReason.UNSUPPORTED_INPUT,
            "lifecycle_clocks",
            "exit-plan bytes are not canonical",
        )
    if not result.source_verified:
        raise _reject(
            LifecycleReason.EXIT_PLAN_UNVERIFIED,
            "lifecycle_clocks.source_sha256",
            "exit-plan clocks are unverified; no fallback is permitted",
        )
    return result
