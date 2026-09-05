"""Production wall-clock scheduler for one armed PAPER session (#90).

The synthetic host runner consumes a complete observation timeline up front and
never waits; this scheduler is the production owner of time.  It derives the
observation timeline from the armed session's frozen windows, waits for each
observation instant through an injected clock and a single injected sleep per
point (never a busy loop), and drives the unchanged host runner once per
timeline prefix.  Each invocation is restart-safe by construction: the durable
session store, sidecar, and blocked-state journals make repeated prefixes
idempotent, so a process killed between points resumes by re-running the
scheduler against the same state directory.

Deterministic stop conditions: a manual-reconciliation receipt stops the
schedule immediately (no further exposure is ever processed), and a terminal
receipt ends the session.  Clocks and sleeps are injectable, so every behavior
here is tested with a fake clock and no wall time is consumed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from esscher.runtime.autonomous import AutonomousSessionArm
from esscher.runtime.autonomous_host import (
    AutonomousHostAuthorityInput,
    AutonomousHostDisposition,
    AutonomousHostReceipt,
    AutonomousHostRejected,
    HostPlanFactory,
    run_autonomous_host_command,
)
from esscher.runtime.paper_mcp_composition import PaperMcpHostDoors


def _aware_utc(value: datetime, *, path: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AutonomousHostRejected(f"{path} must be timezone-aware UTC")
    return value.astimezone(UTC)


def session_observation_timeline(arm: AutonomousSessionArm) -> tuple[datetime, ...]:
    """Derive the production observation timeline from the armed windows.

    One observation per window opening plus the hard-flat boundary, in exact
    chronological order; the arm validator has already guaranteed containment.
    """

    points = {window.opens_at for window in arm.windows}
    points.add(arm.hard_flat_at)
    return tuple(sorted(_aware_utc(point, path="timeline") for point in points))


def wait_until(
    target: datetime,
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> None:
    """Wait for one observation instant with exactly one sleep when ahead.

    A clock already at or past the target waits zero times; lateness is never
    compensated by skipping - the runner's own window validation decides what a
    late observation means.
    """

    now = _aware_utc(clock(), path="scheduler clock")
    remaining = (_aware_utc(target, path="scheduler target") - now).total_seconds()
    if remaining > 0:
        sleep(remaining)


@dataclass(frozen=True, slots=True)
class PaperSessionInvocation:
    """Complete host-supplied invocation for one scheduled production session.

    The host module owns credentials and captured inputs; the CLI cross-checks
    that ``authority_input`` binds exactly the release/arm/state paths passed on
    the command line and that ``ledger_path`` is the declared ledger, so the
    selector can never substitute a different authority.
    """

    authority_input: AutonomousHostAuthorityInput
    doors: PaperMcpHostDoors
    ledger_path: Path
    scheduler_clock: Callable[[], datetime] | None = None
    scheduler_sleep: Callable[[float], None] | None = None


def run_paper_session(
    *,
    authority_input: AutonomousHostAuthorityInput,
    plan_factory: HostPlanFactory,
    timeline: Sequence[datetime],
    clock: Callable[[], datetime],
    sleep: Callable[[float], None] = time.sleep,
) -> AutonomousHostReceipt:
    """Drive one armed session across its timeline and return the last receipt.

    Each point re-runs the unmodified host runner over the timeline prefix;
    durable state makes the replay idempotent.  Manual reconciliation stops the
    schedule without processing any later window; a terminal receipt ends it.
    """

    points = tuple(_aware_utc(point, path="timeline") for point in timeline)
    if not points:
        raise AutonomousHostRejected("session timeline must contain at least one observation")
    if points != tuple(sorted(points)):
        raise AutonomousHostRejected("session timeline must be chronological")
    receipt: AutonomousHostReceipt | None = None
    for index, point in enumerate(points):
        wait_until(point, clock=clock, sleep=sleep)
        receipt = run_autonomous_host_command(
            authority_input=authority_input,
            plan_factory=plan_factory,
            observation_timeline=points[: index + 1],
        )
        if receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED:
            return receipt
        if receipt.disposition is AutonomousHostDisposition.TERMINAL:
            return receipt
    assert receipt is not None  # timeline is non-empty by validation above
    return receipt


__all__ = [
    "PaperSessionInvocation",
    "run_paper_session",
    "session_observation_timeline",
    "wait_until",
]
