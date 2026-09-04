"""Production PAPER_MCP composition over the guarded official Alpaca MCP door.

Issue #90 / PRD PR-2: this module composes the real decision, risk, and
lifecycle services with the factory-prepared official Alpaca MCP PAPER session
behind the four autonomous-host backend ports.  It is the production twin of
the synthetic rehearsal composition, with these hard boundaries:

- the frozen ``SYNTHETIC_FAKE`` rehearsal composition is untouched and this
  module never imports the synthetic broker, synthetic clocks, or the sourcedata
  fixture loaders; a plan can never mix the two classes (the host runner
  rejects any mixed or unbound backend set);
- the reasoner is only the exact packaged, owner-approved direct-provider route
  (current: the V5 DashScope qwen lane) bound into the armed session; there is
  no provider, model, or synthetic-route fallback of any kind;
- every broker interaction travels the guarded session: mutations only through
  the factory-issued lifecycle broker (claim-first, readback-first on
  ambiguity, never retried), reads through the read-only door;
- restart recovery establishes broker truth before any new mutation is
  possible: candidate processing is refused until one complete STARTUP
  reconciliation has observed the account, orders, and positions;
- every non-terminal failure enters one durable blocked-state journal with a
  bounded retry budget; exhaustion escalates to manual reconciliation instead
  of silently retrying;
- credentials never enter this module: the host owns the MCP process and the
  route transport, and the account is bound only through digests.

A ``PAPER_MCP`` host receipt produced through this composition is a PAPER
operational result of the repository software.  It is not broker-readiness
proof, release approval, fill-quality evidence, judged P&L, or alpha; those
gates belong to #91 and #68.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from ringdown_market.application.autonomous_bridge import (
    RiskAbstentionRejected,
    SyntheticConfirmationAbstained,
)
from ringdown_market.application.paper_pipeline import (
    ActivePaperLifecycle,
    CloseCriticalBinding,
    PaperPipelineRejected,
    PaperStrategyApplication,
)
from ringdown_market.autonomy.episodes import (
    GENESIS_SUMMARY_SHA256,
    DecisionEpisode,
    OutcomeEpisode,
    append_decision_episode,
    append_outcome_episode,
)
from ringdown_market.contracts.execution_policy import (
    ACCOUNT_TOOL,
    ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256,
    ALPACA_MCP_PROTOCOL_SHA256,
    ALPACA_MCP_V2_PROTOCOL_SHA256,
    ORDERS_TOOL,
    POSITIONS_TOOL,
)
from ringdown_market.contracts.latency_profile import (
    LatencyProfileRejected,
    load_latency_profile,
)
from ringdown_market.contracts.reasoner_route import load_current_approved_reasoner_route
from ringdown_market.execution.expression import ExpressionMarketSnapshot, PromotedExpressionPolicy
from ringdown_market.execution.host_mcp import (
    HostMcpEnvironment,
    HostMcpError,
    HostMcpMutationAmbiguous,
    PreparedHostMcpSession,
)
from ringdown_market.execution.lifecycle_mcp import LifecycleMcpPaperBroker
from ringdown_market.lifecycle import (
    MULTI_LEG_ORDER_CLASS,
    PAPER_ACCOUNT_CLASS,
    LifecycleClocks,
    LifecycleRejected,
    LifecycleState,
    MonitoredPaperLifecycle,
    issue_close_permit,
)
from ringdown_market.lifecycle.broker import BrokerOutage
from ringdown_market.lifecycle.reasons import LifecycleReason
from ringdown_market.risk import (
    RiskKernel,
    RiskLedger,
    RiskRejected,
    load_risk_policy_v2,
    risk_policy_v2_sha256,
)
from ringdown_market.risk.snapshots import AccountSnapshot, OrderSnapshot, PositionSnapshot
from ringdown_market.runtime.autonomous import (
    CandidateProcessingRequest,
    DueWindowRequest,
    LifecycleCloseRequest,
    MutationState,
    ReconciliationRequest,
)
from ringdown_market.runtime.autonomous_host import (
    AutonomousHostPlan,
    AutonomousHostRejected,
    HostCandidateObservation,
    HostCandidateOutcome,
    HostExecutionClass,
    HostLifecycleOutcome,
    HostReconciliationObservation,
    PaperMcpBrokerTruth,
    ValidatedAutonomousHostAuthority,
)
from ringdown_market.runtime.host_persistence import (
    HOST_PERSISTENCE_FILENAME,
    HostPersistenceRejected,
    HostPersistenceSidecar,
)
from ringdown_market.runtime.option_events import EvidenceClass
from ringdown_market.sourcedata import (
    CaptureConfiguration,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.alpaca_option_events import (
    WORKING_ORDER_STATES,
    AccountActivitySource,
    ActivityAcquisitionRejected,
    ActivityCursorJournal,
    McpAccountActivitySource,
    acquire_account_activities,
    normalize_account_activities,
    summarize_orders_state,
)
from ringdown_market.sourcedata.interfaces import EvidenceSource, MarketDataSource
from ringdown_market.sourcedata.reasons import CollectorRejected
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes
from ringdown_market.strategy.host_route import (
    FurryGatewayReasonerRoute,
    HostRouteError,
    MinimaxM3ReasonerRoute,
    QwenDashScopeReasonerRoute,
)
from ringdown_market.strategy.reasoner import ReasonerRoute, RouteIdentity

PAPER_MCP_COMPOSITION_CLAIMS = (
    "PAPER_OPERATIONAL_RESULT",
    "NOT_ALPHA_EVIDENCE",
    "NO_CREDENTIALS_RECORDED",
)
PAPER_MCP_TERMINAL_FLAT_PROOF_SCHEMA = "esscher.paper_mcp_terminal_flat_proof"
PAPER_MCP_PRODUCTION_BINDING_SCHEMA = "esscher.paper_mcp_production_binding"
BLOCKED_STATE_FILENAME = "paper_mcp_blocked_state.jsonl"
BLOCKED_STATE_SCHEMA = "esscher.paper_mcp_blocked_state_entry"
BLOCKED_STATE_SCHEMA_VERSION = 1
ACTIVITY_CURSOR_DIRNAME = "activity_cursors"
DEFAULT_RETRY_BUDGET = 3
PAPER_MCP_CLOSE_PERMIT_TTL = timedelta(seconds=60)
PRODUCTION_DECISION_LATENCY = timedelta(seconds=10)
PRODUCTION_PERMIT_MARGIN = timedelta(seconds=55)
PRODUCTION_OPEN_DELAY = timedelta(seconds=1)
PRODUCTION_CLOSE_SEPARATION = timedelta(seconds=1)

EARNINGS_LANE_V2 = "EARNINGS_RESIDUAL_CONTINUATION_V2"
# Accepted source-candidate generations per autonomous lane.  The V3
# delayed-capture demo candidate (owner-approved 2026-09-04, #68/#101) rides
# the same autonomous lane: identical signal window and validation math, only
# the capture/decision/entry clocks shift.
_SOURCE_CANDIDATE_BY_AUTONOMOUS_LANE = {
    EARNINGS_LANE_V2: (
        "EARNINGS_RESIDUAL_CONTINUATION_V1",
        "EARNINGS_RESIDUAL_CONTINUATION_V3",
    ),
}

_OCC_ROOT = re.compile(r"^([A-Z]{1,6})\d")


class PaperMcpCompositionReason(StrEnum):
    """Stable reasons the production composition refuses to build or run."""

    DOOR_INVALID = "DOOR_INVALID"
    ROUTE_NOT_APPROVED = "ROUTE_NOT_APPROVED"
    SESSION_NOT_PREPARED = "SESSION_NOT_PREPARED"
    NON_PAPER_ACCOUNT = "NON_PAPER_ACCOUNT"
    ACCOUNT_FINGERPRINT_MISMATCH = "ACCOUNT_FINGERPRINT_MISMATCH"
    BROKER_TRUTH_UNAVAILABLE = "BROKER_TRUTH_UNAVAILABLE"


class PaperMcpCompositionRejected(ValueError):
    """A fail-closed production composition rejection; never a fake fallback."""

    def __init__(self, reason: PaperMcpCompositionReason, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}")


@dataclass(frozen=True, slots=True)
class PaperMcpFeedEvent:
    """One host-captured evidence/market observation bound to a due window."""

    window_id: str
    candidate_id: str
    evidence_manifest_bytes: bytes
    market_window_bytes: bytes
    capture_at: datetime
    market_publisher: str
    market_entitlement: str
    market_redistribution: str

    def __post_init__(self) -> None:
        for name in ("window_id", "candidate_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.DOOR_INVALID, f"feed event {name} must be text"
                )
        for name in ("evidence_manifest_bytes", "market_window_bytes"):
            if type(getattr(self, name)) is not bytes:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.DOOR_INVALID, f"feed event {name} must be bytes"
                )
        if not isinstance(self.capture_at, datetime) or self.capture_at.tzinfo is None:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "feed event capture_at must be aware UTC"
            )

    @property
    def event_digest_sha256(self) -> str:
        return sha256_bytes(
            canonical_json_bytes(
                {
                    "candidate_id": self.candidate_id,
                    "capture_at": self.capture_at.astimezone(UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                    "evidence_manifest_sha256": sha256_bytes(self.evidence_manifest_bytes),
                    "market_publisher": self.market_publisher,
                    "market_window_sha256": sha256_bytes(self.market_window_bytes),
                    "window_id": self.window_id,
                }
            )
        )

    @property
    def opportunity_id(self) -> str:
        return f"OPP-{self.event_digest_sha256[:40]}"

    def strategy_context_sha256(self, window_sha256: str) -> str:
        return sha256_bytes(
            canonical_json_bytes(
                {
                    "candidate_id": self.candidate_id,
                    "event_digest_sha256": self.event_digest_sha256,
                    "opportunity_id": self.opportunity_id,
                    "window_sha256": window_sha256,
                }
            )
        )


@dataclass(frozen=True, slots=True)
class PaperMcpFeed:
    """The complete host-captured observation feed for one armed session."""

    events: tuple[PaperMcpFeedEvent, ...]

    def __post_init__(self) -> None:
        if type(self.events) is not tuple:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "feed events must be an immutable tuple"
            )
        identities = tuple(event.opportunity_id for event in self.events)
        if len(identities) != len(set(identities)):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID,
                "feed contains duplicate opportunity identities",
            )

    def event_for_opportunity(self, opportunity_id: str) -> PaperMcpFeedEvent | None:
        for event in self.events:
            if event.opportunity_id == opportunity_id:
                return event
        return None


class CaptureSourceDoor(Protocol):
    """Host-owned captured source bytes behind the real sourcedata protocols."""

    def sources_for(
        self, event: PaperMcpFeedEvent
    ) -> tuple[CaptureConfiguration, EvidenceSource, MarketDataSource]: ...


@dataclass(frozen=True, slots=True)
class ProductionTimeline:
    """Policy-derived decision clocks for one production candidate.

    The instants are pure functions of the captured snapshot's frozen policy
    deadlines (decision cutoff, entry deadline) exactly as the frozen V1/V2
    clocks prescribe; the wall clock enters through the coordinator's due
    window and the host clock door, never through these derivations.
    """

    started_at: datetime
    authorization_at: datetime
    open_at: datetime


def production_timeline(strategy_input: object) -> ProductionTimeline:
    snapshot = getattr(strategy_input, "snapshot", None)
    feature_receipt = getattr(strategy_input, "feature_receipt", None)
    if snapshot is None or feature_receipt is None:
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.DOOR_INVALID,
            "production timeline requires a compiled strategy input",
        )
    started_at = max(
        feature_receipt.created_at,
        snapshot.decision_cutoff_at - PRODUCTION_DECISION_LATENCY,
    )
    authorization_at = snapshot.candidate_entry_deadline_at - PRODUCTION_PERMIT_MARGIN
    open_at = authorization_at + PRODUCTION_OPEN_DELAY
    permit_expires_at = min(
        authorization_at + timedelta(seconds=60),
        snapshot.candidate_entry_deadline_at,
    )
    if (
        started_at > snapshot.decision_cutoff_at
        or authorization_at < started_at
        or open_at >= permit_expires_at
    ):
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.DOOR_INVALID,
            "captured snapshot deadlines cannot host a production decision timeline",
        )
    return ProductionTimeline(
        started_at=started_at, authorization_at=authorization_at, open_at=open_at
    )


class ExpressionSnapshotDoor(Protocol):
    """Host-owned captured option-chain observation for Gate D expression."""

    def snapshot_for(
        self, *, underlying: str, decision_sha256: str, observed_at: datetime
    ) -> ExpressionMarketSnapshot: ...


LifecycleClockFactory = Callable[..., LifecycleClocks]


class PaperMcpMutationGate:
    """The production mutation gate: closed unless the session is armed.

    ``mutation_permitted`` is only true when the operator composed the doors
    for an explicitly armed PAPER session whose read-only preflight passed.
    The no-mutation rehearsal (#91 PR-5) runs the identical composition with
    this gate closed: the full decision path executes and emits its would-be
    permit, and no order tool is ever called.
    """

    def __init__(self, permitted: bool) -> None:
        if type(permitted) is not bool:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "mutation gate flag must be a boolean"
            )
        self._permitted = permitted

    def mutation_permitted(self) -> bool:
        return self._permitted


@dataclass(frozen=True, slots=True)
class PaperMcpHostDoors:
    """The narrow host-owned doors the production composition consumes.

    The host supplies: one factory-prepared MCP session capability; the exact
    owner-approved direct-provider route adapter (``approved_route`` - current:
    the qwen3.8-max-0902 V5 DashScope lane; the deepseek V4 gateway and
    MiniMax-M3 V3 adapters remain accepted while their packages are dormant
    alternates), which must also be the
    identical object wired as the engine reasoner door
    (``reasoner is approved_route`` - no synthetic or drifted double can front
    the production composition) with its exact ``reasoner_identity``; captured
    feed/source/expression doors; the promoted expression policy; an exit-plan
    clock factory; a wall clock; a risk ledger; the close economics from the
    armed risk envelope; and the mutation authorization.  Nothing else about
    the composition is host-controlled, and no door can weaken the
    approved-route binding checks.
    """

    prepared_session: PreparedHostMcpSession
    approved_route: FurryGatewayReasonerRoute | MinimaxM3ReasonerRoute
    reasoner: ReasonerRoute
    reasoner_identity: RouteIdentity
    feed: PaperMcpFeed
    capture_sources: CaptureSourceDoor
    expression_snapshots: ExpressionSnapshotDoor
    lifecycle_clocks: LifecycleClockFactory
    expression_policy: PromotedExpressionPolicy
    clock: Callable[[], datetime]
    ledger: RiskLedger
    close_limit_price: Decimal
    account_id: str
    mutation_permitted: bool = False
    retry_budget: int = DEFAULT_RETRY_BUDGET
    activity_source: AccountActivitySource | None = None


class BlockedStateJournal:
    """One durable blocked-state record per failing subject, with a budget.

    Every non-terminal production failure appends one hash-chained entry.  A
    subject whose BLOCKED entry count reaches the retry budget flips to
    EXHAUSTED durably; exhaustion escalates to manual reconciliation and can
    never be cleared by retrying, restart, or elapsed time.
    """

    def __init__(self, path: Path | str, *, retry_budget: int = DEFAULT_RETRY_BUDGET) -> None:
        if not isinstance(retry_budget, int) or isinstance(retry_budget, bool) or retry_budget < 1:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "retry budget must be a positive integer"
            )
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._retry_budget = retry_budget

    @property
    def path(self) -> Path:
        return self._path

    @property
    def retry_budget(self) -> int:
        return self._retry_budget

    def _entries(self) -> tuple[dict[str, object], ...]:
        if not self._path.exists():
            return ()
        entries: list[dict[str, object]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "blocked-state journal is corrupted",
                ) from error
            if not isinstance(entry, dict):
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "blocked-state journal is corrupted",
                )
            entries.append(entry)
        expected_prior = "0" * 64
        for entry in entries:
            if entry.get("prior_entry_sha256") != expected_prior:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "blocked-state journal chain is broken",
                )
            unsigned = {key: value for key, value in entry.items() if key != "entry_sha256"}
            expected_prior = sha256_bytes(canonical_json_bytes(unsigned))
            if entry.get("entry_sha256") != expected_prior:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "blocked-state journal chain is broken",
                )
        return tuple(entries)

    def _subject_entries(self, subject_id: str) -> tuple[dict[str, object], ...]:
        return tuple(
            entry
            for entry in self._entries()
            if entry.get("subject_id") == subject_id and entry.get("state") != "RESOLVED"
        )

    def is_exhausted(self, subject_id: str) -> bool:
        return any(entry.get("state") == "EXHAUSTED" for entry in self._subject_entries(subject_id))

    def record_failure(self, subject_id: str, reason_code: str, observed_at: datetime) -> bool:
        """Append one BLOCKED entry; return True when the budget is exhausted."""

        prior_entries = self._subject_entries(subject_id)
        attempts = sum(1 for entry in prior_entries if entry.get("state") == "BLOCKED") + 1
        exhausted = any(entry.get("state") == "EXHAUSTED" for entry in prior_entries)
        state = "EXHAUSTED" if (exhausted or attempts >= self._retry_budget) else "BLOCKED"
        self._append(subject_id, state, reason_code, attempts, observed_at)
        return state == "EXHAUSTED"

    def resolve(self, subject_id: str, observed_at: datetime) -> None:
        prior = self._subject_entries(subject_id)
        if not prior:
            return
        attempts = sum(1 for entry in prior if entry.get("state") == "BLOCKED")
        self._append(subject_id, "RESOLVED", "RESOLVED", attempts, observed_at)

    def _append(
        self, subject_id: str, state: str, reason_code: str, attempts: int, observed_at: datetime
    ) -> str:
        entries = self._entries()
        prior = "0" * 64 if not entries else str(entries[-1].get("entry_sha256", ""))
        unsigned = {
            "schema": BLOCKED_STATE_SCHEMA,
            "schema_version": BLOCKED_STATE_SCHEMA_VERSION,
            "subject_id": subject_id,
            "state": state,
            "reason_code": reason_code,
            "attempts": attempts,
            "retry_budget": self._retry_budget,
            "observed_at": _utc_text(observed_at),
            "prior_entry_sha256": prior,
        }
        digest = sha256_bytes(canonical_json_bytes(unsigned))
        line = json.dumps(
            {**unsigned, "entry_sha256": digest}, sort_keys=True, separators=(",", ":")
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return digest


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.DOOR_INVALID, "clock values must be aware UTC"
        )
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


async def _readonly_canonical_bytes(
    prepared: PreparedHostMcpSession, tool: str, arguments: Mapping[str, object]
) -> bytes:
    response = await prepared.readonly_call(tool, arguments)
    if isinstance(response, (str, bytes)):
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
            f"read-only {tool} response was not structured JSON",
        )
    try:
        return canonical_json_bytes(response)
    except (TypeError, ValueError) as error:
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
            f"read-only {tool} response was not canonicalizable",
        ) from error


def _account_payload(raw: bytes) -> Mapping[str, object]:
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE, "account payload must be an object"
        )
    account_class = payload.get("account_class")
    if account_class != PAPER_ACCOUNT_CLASS:
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.NON_PAPER_ACCOUNT,
            "account payload does not attest the PAPER account class",
        )
    status = payload.get("status")
    if status != "ACTIVE" or payload.get("trading_blocked") or payload.get("account_blocked"):
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
            "account is not an unblocked ACTIVE PAPER account",
        )
    return payload


def account_fingerprint_from_payload(raw_account_bytes: bytes) -> str:
    """The production account fingerprint: digest of the canonical account truth."""

    payload = _account_payload(raw_account_bytes)
    return sha256_bytes(canonical_json_bytes(dict(payload)))


def _position_items(raw: bytes) -> list[Mapping[str, object]]:
    items = json.loads(raw.decode("utf-8"))
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE, "positions payload must be a list"
        )
    result: list[Mapping[str, object]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                "position record must be an object",
            )
        result.append(item)
    return result


def _nonzero_position_count(raw: bytes) -> int:
    count = 0
    for item in _position_items(raw):
        try:
            quantity = Decimal(str(item.get("qty")))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                "position record has an invalid quantity",
            ) from error
        if not quantity.is_finite():
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                "position record has a non-finite quantity",
            )
        if quantity != 0:
            count += 1
    return count


def _decimal_field(item: Mapping[str, object], name: str) -> Decimal:
    try:
        value = Decimal(str(item.get(name)))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
            f"broker payload has an invalid {name}",
        ) from error
    if not value.is_finite():
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
            f"broker payload has a non-finite {name}",
        )
    return value


class PaperMcpAccountTruthSource:
    """Risk-kernel account truth from cached read-only MCP observations."""

    def __init__(self, doors: PaperMcpHostDoors) -> None:
        self._doors = doors
        self._account_raw: bytes | None = None
        self._orders_raw: bytes | None = None
        self._positions_raw: bytes | None = None

    def refresh(self) -> None:
        self._account_raw = asyncio.run(
            _readonly_canonical_bytes(self._doors.prepared_session, ACCOUNT_TOOL, {})
        )
        self._orders_raw = asyncio.run(
            _readonly_canonical_bytes(
                self._doors.prepared_session, ORDERS_TOOL, {"status": "all", "nested": True}
            )
        )
        self._positions_raw = asyncio.run(
            _readonly_canonical_bytes(self._doors.prepared_session, POSITIONS_TOOL, {})
        )

    def _loaded(self) -> tuple[bytes, bytes, bytes]:
        if self._account_raw is None or self._orders_raw is None or self._positions_raw is None:
            self.refresh()
        assert self._account_raw is not None
        assert self._orders_raw is not None
        assert self._positions_raw is not None
        return self._account_raw, self._orders_raw, self._positions_raw

    def account(self) -> AccountSnapshot:
        account_raw, _, _ = self._loaded()
        payload = _account_payload(account_raw)
        cash_value = payload.get("cash")
        return AccountSnapshot(
            equity=_decimal_field(payload, "equity"),
            buying_power=_decimal_field(payload, "buying_power"),
            currency=str(payload.get("currency", "USD")),
            observed_at=self.broker_clock(),
            cash=None if cash_value is None else _decimal_field(payload, "cash"),
        )

    def positions(self) -> tuple[PositionSnapshot, ...]:
        _, _, positions_raw = self._loaded()
        observed_at = self.broker_clock()
        snapshots: list[PositionSnapshot] = []
        for item in _position_items(positions_raw):
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "position record lacks a symbol",
                )
            quantity = _decimal_field(item, "qty")
            if quantity == 0:
                continue
            asset_class = item.get("asset_class")
            if asset_class == "us_option":
                match = _OCC_ROOT.match(symbol)
                underlying = match.group(1) if match else symbol
            elif asset_class == "us_equity":
                underlying = symbol
            else:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "position record carries an unsupported asset class",
                )
            snapshots.append(
                PositionSnapshot(
                    underlying=underlying,
                    quantity=quantity,
                    market_value=_decimal_field(item, "market_value"),
                    observed_at=observed_at,
                )
            )
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.underlying))

    def orders(self) -> tuple[OrderSnapshot, ...]:
        _, orders_raw, _ = self._loaded()
        observed_at = self.broker_clock()
        items = json.loads(orders_raw.decode("utf-8"))
        if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE, "orders payload must be a list"
            )
        snapshots: list[OrderSnapshot] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "order record must be an object",
                )
            order_id = item.get("id")
            status = item.get("status")
            if not isinstance(order_id, str) or not isinstance(status, str):
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "order record lacks identity or status",
                )
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                legs = item.get("legs")
                if (
                    isinstance(legs, Sequence)
                    and not isinstance(legs, (str, bytes))
                    and legs
                    and isinstance(legs[0], Mapping)
                    and isinstance(legs[0].get("symbol"), str)
                ):
                    symbol = legs[0]["symbol"]
                else:
                    raise PaperMcpCompositionRejected(
                        PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                        "order record lacks a symbol and usable legs",
                    )
            filled_raw = item.get("filled_qty")
            try:
                filled = Decimal("0") if filled_raw is None else Decimal(str(filled_raw))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "order record has an invalid filled quantity",
                ) from error
            snapshots.append(
                OrderSnapshot(
                    order_id=order_id,
                    symbol=symbol,
                    status=status,
                    filled_quantity=filled,
                    observed_at=observed_at,
                )
            )
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.order_id))

    def broker_clock(self) -> datetime:
        value = self._doors.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "clock must return aware UTC time"
            )
        return value.astimezone(UTC)


@dataclass
class PaperMcpCompositionState:
    """Shared production services behind the four host backends."""

    doors: PaperMcpHostDoors
    authority: ValidatedAutonomousHostAuthority
    binding_sha256: str
    broker: LifecycleMcpPaperBroker
    sidecar: HostPersistenceSidecar
    blocked: BlockedStateJournal
    truth_source: PaperMcpAccountTruthSource
    gate: PaperMcpMutationGate
    broker_truth_established: bool = False
    emitted: set[str] = field(default_factory=set)
    candidate_calls: int = 0
    lifecycle_calls: int = 0
    reconciliation_calls: int = 0
    collector_calls: int = 0
    orphan_cancels: int = 0

    @property
    def ledger(self) -> RiskLedger:
        """The host-supplied durable risk ledger shared by every backend."""

        return self.doors.ledger


def _lifecycle_manual_reason(error: LifecycleRejected) -> str:
    """Map one post-submission lifecycle rejection to its stable manual reason."""

    if error.reason in (
        LifecycleReason.OPEN_ORDER_PARTIAL,
        LifecycleReason.CLOSE_ORDER_PARTIAL,
    ):
        return "PARTIAL_FILL"
    return "UNKNOWN_BROKER_STATE"


def paper_mcp_terminal_flat_proof_sha256(
    *,
    session_id: str,
    lifecycle_id: str,
    close_order_id: str,
    close_permit_id: str,
    closed_at: datetime,
    account_state_sha256: str,
    orders_state_sha256: str,
    positions_state_sha256: str,
    open_order_count: int,
    open_position_count: int,
    is_flat: bool,
    activity_event_sha256s: Sequence[str],
) -> str:
    """Content-address one production terminal-flat proof over MCP read truth."""

    if not is_flat or open_order_count or open_position_count:
        raise PaperMcpCompositionRejected(
            PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
            "terminal flat proof requires observed flat broker truth",
        )
    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema": PAPER_MCP_TERMINAL_FLAT_PROOF_SCHEMA,
                "schema_version": 1,
                "claims": list(PAPER_MCP_COMPOSITION_CLAIMS),
                "session_id": session_id,
                "lifecycle_id": lifecycle_id,
                "close_order_id": close_order_id,
                "close_permit_id": close_permit_id,
                "closed_at": _utc_text(closed_at),
                "account_state_sha256": account_state_sha256,
                "orders_state_sha256": orders_state_sha256,
                "positions_state_sha256": positions_state_sha256,
                "open_order_count": open_order_count,
                "open_position_count": open_position_count,
                "is_flat": is_flat,
                "activity_event_sha256s": sorted(set(activity_event_sha256s)),
            }
        )
    )


class PaperMcpReconciliationBackend:
    """Observe the real PAPER broker through the read-only door.

    STARTUP observations establish broker truth before any mutation is
    possible; orphaned working orders are resolved exclusively through the
    risk-reducing cancel path; every read failure fails closed into the
    coordinator's manual reconciliation instead of guessing.
    """

    def __init__(self, state: PaperMcpCompositionState) -> None:
        self._state = state

    @property
    def production_binding_sha256(self) -> str:
        return self._state.binding_sha256

    def observe_reconciliation(
        self, request: ReconciliationRequest
    ) -> HostReconciliationObservation:
        state = self._state
        state.reconciliation_calls += 1
        prepared = state.doors.prepared_session
        try:
            account_raw = asyncio.run(_readonly_canonical_bytes(prepared, ACCOUNT_TOOL, {}))
            orders_raw = asyncio.run(
                _readonly_canonical_bytes(prepared, ORDERS_TOOL, {"status": "open"})
            )
            positions_raw = asyncio.run(_readonly_canonical_bytes(prepared, POSITIONS_TOOL, {}))
            _account_payload(account_raw)
            orders = summarize_orders_state(orders_raw)
            open_positions = _nonzero_position_count(positions_raw)
        except (HostMcpError, PaperMcpCompositionRejected, ActivityAcquisitionRejected) as error:
            state.blocked.record_failure(
                f"reconciliation::{request.session_id}::{request.phase}",
                "BROKER_TRUTH_UNAVAILABLE",
                request.observed_at,
            )
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                f"read-only broker observation failed: {type(error).__name__}",
            ) from error

        bound_open_order_ids = {
            bundle.open_order_id for bundle in state.sidecar.active_bundles(request.session_id)
        }
        cancelled_orphan = False
        for order_id, _client_order_id, status in orders.working_orders:
            if status not in WORKING_ORDER_STATES:
                continue
            if order_id in bound_open_order_ids:
                continue
            try:
                asyncio.run(prepared.risk_reducing_cancel(order_id))
                state.orphan_cancels += 1
                cancelled_orphan = True
            except (BrokerOutage, HostMcpError):
                state.blocked.record_failure(
                    f"orphan::{order_id}", "UNKNOWN_BROKER_STATE", request.observed_at
                )
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    "orphaned working order could not be resolved risk-reducing",
                ) from None
        if cancelled_orphan:
            # Truth is attested only after the risk-reducing resolution so the
            # receipt can never bind a stale pre-cancel broker state.
            try:
                account_raw = asyncio.run(_readonly_canonical_bytes(prepared, ACCOUNT_TOOL, {}))
                orders_raw = asyncio.run(
                    _readonly_canonical_bytes(prepared, ORDERS_TOOL, {"status": "open"})
                )
                positions_raw = asyncio.run(_readonly_canonical_bytes(prepared, POSITIONS_TOOL, {}))
                _account_payload(account_raw)
                orders = summarize_orders_state(orders_raw)
                open_positions = _nonzero_position_count(positions_raw)
            except (
                HostMcpError,
                PaperMcpCompositionRejected,
                ActivityAcquisitionRejected,
            ) as error:
                state.blocked.record_failure(
                    f"reconciliation::{request.session_id}::{request.phase}",
                    "BROKER_TRUTH_UNAVAILABLE",
                    request.observed_at,
                )
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                    f"post-resolution broker observation failed: {type(error).__name__}",
                ) from error

        truth = PaperMcpBrokerTruth.for_request(
            request,
            account_state_sha256=sha256_bytes(account_raw),
            orders_state_sha256=sha256_bytes(orders_raw),
            positions_state_sha256=sha256_bytes(positions_raw),
            open_order_count=orders.open_order_count,
            open_position_count=open_positions,
            is_flat=(orders.open_order_count == 0 and open_positions == 0),
        )
        if request.phase == "STARTUP":
            state.broker_truth_established = True
        return HostReconciliationObservation.complete(request, broker_truth=truth)


class PaperMcpDueWindowBackend:
    """Emit each host-captured feed event for its due window exactly once."""

    def __init__(self, state: PaperMcpCompositionState) -> None:
        self._state = state

    @property
    def production_binding_sha256(self) -> str:
        return self._state.binding_sha256

    def observe_due_window(self, request: DueWindowRequest) -> tuple[HostCandidateObservation, ...]:
        state = self._state
        state.collector_calls += 1
        observations: list[HostCandidateObservation] = []
        for event in state.doors.feed.events:
            if event.window_id != request.window.window_id:
                continue
            if event.candidate_id not in request.window.candidate_ids:
                continue
            if event.opportunity_id in state.emitted:
                continue
            state.emitted.add(event.opportunity_id)
            observations.append(
                HostCandidateObservation.for_window(
                    request,
                    opportunity_id=event.opportunity_id,
                    candidate_id=event.candidate_id,
                    strategy_context_sha256=event.strategy_context_sha256(
                        request.window.window_sha256
                    ),
                )
            )
        return tuple(observations)


class PaperMcpCandidateBackend:
    """Drive the real source-to-permit pipeline and one production opening."""

    def __init__(self, state: PaperMcpCompositionState) -> None:
        self._state = state

    @property
    def production_binding_sha256(self) -> str:
        return self._state.binding_sha256

    def process_candidate(self, request: CandidateProcessingRequest) -> HostCandidateOutcome:
        state = self._state
        state.candidate_calls += 1
        doors = state.doors
        opportunity_id = request.opportunity.opportunity_id
        if state.blocked.is_exhausted(opportunity_id):
            return HostCandidateOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code="BLOCKED_RETRY_BUDGET_EXHAUSTED",
            )
        if not state.broker_truth_established:
            # Recovery invariant: broker truth is established before mutation.
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="RECONCILIATION_INCOMPLETE"
            )
        event = doors.feed.event_for_opportunity(opportunity_id)
        if event is None or event.candidate_id != request.opportunity.candidate_id:
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="PORT_OUTPUT_INVALID"
            )
        expected_source_candidates = _SOURCE_CANDIDATE_BY_AUTONOMOUS_LANE.get(event.candidate_id)
        if expected_source_candidates is None:
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="PORT_OUTPUT_INVALID"
            )
        try:
            capture, evidence, market = doors.capture_sources.sources_for(event)
            probe = compile_strategy_snapshot(capture, evidence, market)
            joined = compiled_strategy_input(probe)
            if joined.snapshot.candidate_id not in expected_source_candidates:
                raise PaperMcpCompositionRejected(
                    PaperMcpCompositionReason.DOOR_INVALID,
                    "source candidate does not match the autonomous lane",
                )
        except (
            CollectorRejected,
            PaperMcpCompositionRejected,
            ValueError,
            TypeError,
        ):
            state.blocked.record_failure(opportunity_id, "PORT_OUTPUT_INVALID", request.observed_at)
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="PORT_OUTPUT_INVALID"
            )

        timeline = production_timeline(joined)
        state.truth_source.refresh()
        kernel = RiskKernel(load_risk_policy_v2(), state.ledger, state.truth_source)
        kernel.startup_reconciliation(now=timeline.authorization_at)
        application = PaperStrategyApplication(
            reasoner_route=doors.reasoner,
            expression_policy=doors.expression_policy,
            risk_kernel=kernel,
            risk_policy_sha256=risk_policy_v2_sha256(),
            gate_d_report_sha256=doors.expression_policy.gate_d_report_sha256,
            execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
            lifecycle_clocks=doors.lifecycle_clocks,
            account_id=doors.account_id,
            route_identity=doors.reasoner_identity,
        )
        try:
            prepared = application.prepare_v2(
                capture_configuration=capture,
                evidence=evidence,
                market=market,
                expression_snapshot=lambda decision_sha256: doors.expression_snapshots.snapshot_for(
                    underlying=joined.snapshot.ticker,
                    decision_sha256=decision_sha256,
                    observed_at=timeline.authorization_at,
                ),
                now=timeline.authorization_at,
                decision_started_at=timeline.started_at,
            )
        except SyntheticConfirmationAbstained:
            return HostCandidateOutcome.abstained(request, reason_code="PORT_OUTPUT_INVALID")
        except RiskAbstentionRejected:
            return HostCandidateOutcome.abstained(request, reason_code="RISK_FREEZE")
        except (PaperPipelineRejected, RiskRejected, HostRouteError):
            state.blocked.record_failure(opportunity_id, "PORT_OUTPUT_INVALID", request.observed_at)
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="PORT_OUTPUT_INVALID", freeze=False
            )
        if not state.gate.mutation_permitted():
            # No-mutation rehearsal (PR-5): the would-be permit exists and is
            # recorded through the rejected-before-mutation outcome; no order
            # tool is called and no exposure is created.
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="MUTATION_GATE_CLOSED", freeze=False
            )
        try:
            active = asyncio.run(
                application.open(
                    prepared=prepared,
                    broker=state.broker,
                    clock=lambda: timeline.open_at,
                    mutation_gate=state.gate,
                )
            )
        except (HostMcpMutationAmbiguous, BrokerOutage):
            state.blocked.record_failure(
                opportunity_id, "UNKNOWN_BROKER_STATE", request.observed_at
            )
            return HostCandidateOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        except LifecycleRejected as error:
            # The opening was already submitted when the monitored lifecycle
            # rejects; broker state is unknown-to-partial and must never be
            # classified as a pre-mutation rejection. A partial fill keeps its
            # own stable manual reason.
            reason_code = _lifecycle_manual_reason(error)
            state.blocked.record_failure(opportunity_id, reason_code, request.observed_at)
            return HostCandidateOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code=reason_code,
            )
        except Exception:
            state.blocked.record_failure(
                opportunity_id, "UNKNOWN_BROKER_STATE", request.observed_at
            )
            return HostCandidateOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code="UNKNOWN_BROKER_STATE",
            )

        if active.open_state is not LifecycleState.OPEN_FILLED:
            # A submitted opening that is not provably filled (partial fill,
            # canceled, or unknown state) is never adopted as an active
            # lifecycle; it freezes into manual reconciliation with its stable
            # reason, and the residual working order is left for the
            # risk-reducing reconciliation cancel path.
            partial = active.open_state is LifecycleState.OPEN_PARTIAL
            reason_code = "PARTIAL_FILL" if partial else "UNKNOWN_BROKER_STATE"
            state.blocked.record_failure(opportunity_id, reason_code, request.observed_at)
            return HostCandidateOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL if partial else MutationState.UNKNOWN,
                reason_code=reason_code,
            )

        lifecycle_id = prepared.permit.permit_id
        decision_episode_id = f"dec-{lifecycle_id}"
        snapshot = joined.snapshot
        decision = prepared.engine_outcome.decision
        append_decision_episode(
            state.ledger,
            DecisionEpisode(
                episode_id=decision_episode_id,
                event_id=snapshot.event_id,
                candidate_id=snapshot.candidate_id,
                symbol=snapshot.ticker,
                occurred_at=decision.decision_at,
                decision_cutoff_at=snapshot.decision_cutoff_at,
                source_policy_sha256=snapshot.policy_sha256,
                source_evidence_sha256=snapshot.evidence_packet_sha256,
                source_feature_sha256=joined.feature_receipt_sha256,
                source_snapshot_sha256=joined.snapshot_sha256,
                prior_summary_sha256=GENESIS_SUMMARY_SHA256,
                route_sha256=prepared.engine_outcome.exchange.route_sha256,
                prompt_sha256=prepared.engine_outcome.exchange.prompt_sha256,
                model_config_sha256=prepared.engine_outcome.exchange.model_config_sha256,
                exchange_sha256=decision.reasoner_exchange_sha256,
                decision_sha256=prepared.compiled_expression.decision_sha256,
                disposition=decision.disposition.value,
                direction=decision.direction.value,
                created_at=timeline.open_at,
                supersedes_episode_id=None,
                supersedes_episode_sha256=None,
            ),
        )
        state.sidecar.append_active(
            lifecycle_id=lifecycle_id,
            session_id=request.arm.session_id,
            opportunity_id=request.opportunity.opportunity_id,
            opportunity_sha256=request.opportunity.opportunity_sha256,
            recorded_at=request.observed_at,
            permit=prepared.permit,
            clocks=prepared.lifecycle_clocks,
            correlation=prepared.correlation,
            open_order_id=active.open_order_id,
            account_id=application.account_id,
            application_identity_sha256=prepared.application_identity_sha256,
            opened_at=timeline.open_at,
            decision_episode_id=decision_episode_id,
        )
        state.blocked.resolve(opportunity_id, request.observed_at)
        return HostCandidateOutcome.active(request, lifecycle_id=lifecycle_id)


class PaperMcpLifecycleBackend:
    """Rehydrate one durable bundle, close through MCP, and prove flat truth."""

    def __init__(self, state: PaperMcpCompositionState) -> None:
        self._state = state

    @property
    def production_binding_sha256(self) -> str:
        return self._state.binding_sha256

    def close_lifecycle(self, request: LifecycleCloseRequest) -> HostLifecycleOutcome:
        state = self._state
        state.lifecycle_calls += 1
        doors = state.doors
        lifecycle_id = request.lifecycle.lifecycle_id
        existing_proof = state.sidecar.terminal_flat_proof(lifecycle_id)
        if existing_proof is not None:
            return HostLifecycleOutcome.terminal_flat(
                request, terminal_flat_proof_sha256=existing_proof
            )
        try:
            bundle = state.sidecar.rehydrate(lifecycle_id)
        except HostPersistenceRejected:
            bundle = None
        if (
            bundle is None
            or bundle.session_id != request.arm.session_id
            or bundle.opportunity_id != request.lifecycle.opportunity_id
            or bundle.opportunity_sha256 != request.lifecycle.opportunity_sha256
        ):
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code="CLAIM_RECOVERY_UNKNOWN",
            )
        # The close instant is the coordinator's hard-flat observation time; it
        # can never precede the durable opening instant of this lifecycle.
        close_at = max(request.observed_at, bundle.opened_at + PRODUCTION_CLOSE_SEPARATION)
        monitored = MonitoredPaperLifecycle(
            broker=state.broker,
            ledger=state.ledger,
            clocks=bundle.clocks,
            correlation=bundle.correlation,
            mutation_gate=state.gate,
            clock=lambda: close_at,
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
            ledger=state.ledger,
            broker=state.broker,
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
            expires_at=close_at + PAPER_MCP_CLOSE_PERMIT_TTL,
            limit_price=doors.close_limit_price,
        )
        state.truth_source.refresh()
        kernel = RiskKernel(load_risk_policy_v2(), state.ledger, state.truth_source)
        application = PaperStrategyApplication(
            reasoner_route=doors.reasoner,
            expression_policy=doors.expression_policy,
            risk_kernel=kernel,
            risk_policy_sha256=risk_policy_v2_sha256(),
            gate_d_report_sha256=doors.expression_policy.gate_d_report_sha256,
            execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
            lifecycle_clocks=doors.lifecycle_clocks,
            account_id=doors.account_id,
            route_identity=doors.reasoner_identity,
        )
        try:
            close_state, close_order_id = asyncio.run(
                application.close(active=active, close_permit=close_permit)
            )
        except (HostMcpMutationAmbiguous, BrokerOutage):
            state.blocked.record_failure(lifecycle_id, "UNKNOWN_BROKER_STATE", request.observed_at)
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        except LifecycleRejected as error:
            reason_code = _lifecycle_manual_reason(error)
            state.blocked.record_failure(lifecycle_id, reason_code, request.observed_at)
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code=reason_code,
            )
        except (PaperPipelineRejected, RiskRejected):
            state.blocked.record_failure(lifecycle_id, "UNKNOWN_BROKER_STATE", request.observed_at)
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        if close_state is not LifecycleState.CLOSED_FLAT or close_order_id is None:
            state.blocked.record_failure(lifecycle_id, "UNKNOWN_BROKER_STATE", request.observed_at)
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="UNKNOWN_BROKER_STATE",
            )

        # Broker-truth-first terminal evidence: ingest the lifecycle's activity
        # window, route anything unknown or contradictory to manual, and read
        # fresh flat truth before the terminal proof may exist.
        prepared = doors.prepared_session
        activity_source = doors.activity_source or McpAccountActivitySource(prepared)
        cursor_journal = ActivityCursorJournal(
            state.authority.state_dir / ACTIVITY_CURSOR_DIRNAME / f"{lifecycle_id}.jsonl"
        )
        try:
            acquisition = asyncio.run(
                acquire_account_activities(
                    activity_source,
                    window_start=bundle.opened_at,
                    window_end=close_at,
                    journal=cursor_journal,
                    clock=doors.clock,
                )
            )
        except ActivityAcquisitionRejected:
            state.blocked.record_failure(
                f"activities::{lifecycle_id}", "ACTIVITY_MANUAL_ROUTE", request.observed_at
            )
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="ACTIVITY_MANUAL_ROUTE",
            )
        normalization = normalize_account_activities(
            acquisition,
            account_fingerprint_sha256=state.authority.account_fingerprint_sha256,
            execution_protocol_sha256=ALPACA_MCP_V2_PROTOCOL_SHA256,
            evidence_class=EvidenceClass.HOST_NORMALIZED_BROKER_INPUT,
        )
        if normalization.manual_routes:
            state.blocked.record_failure(
                f"activities::{lifecycle_id}", "ACTIVITY_MANUAL_ROUTE", request.observed_at
            )
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="ACTIVITY_MANUAL_ROUTE",
            )
        try:
            account_raw = asyncio.run(_readonly_canonical_bytes(prepared, ACCOUNT_TOOL, {}))
            orders_raw = asyncio.run(
                _readonly_canonical_bytes(prepared, ORDERS_TOOL, {"status": "open"})
            )
            positions_raw = asyncio.run(_readonly_canonical_bytes(prepared, POSITIONS_TOOL, {}))
            _account_payload(account_raw)
            orders = summarize_orders_state(orders_raw)
            open_positions = _nonzero_position_count(positions_raw)
        except (HostMcpError, PaperMcpCompositionRejected, ActivityAcquisitionRejected):
            state.blocked.record_failure(lifecycle_id, "UNKNOWN_BROKER_STATE", request.observed_at)
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        if orders.open_order_count or open_positions:
            state.blocked.record_failure(lifecycle_id, "UNKNOWN_BROKER_STATE", request.observed_at)
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        proof = paper_mcp_terminal_flat_proof_sha256(
            session_id=request.arm.session_id,
            lifecycle_id=lifecycle_id,
            close_order_id=close_order_id,
            close_permit_id=close_permit.permit_id,
            closed_at=close_at,
            account_state_sha256=sha256_bytes(account_raw),
            orders_state_sha256=sha256_bytes(orders_raw),
            positions_state_sha256=sha256_bytes(positions_raw),
            open_order_count=orders.open_order_count,
            open_position_count=open_positions,
            is_flat=True,
            activity_event_sha256s=tuple(event.event_sha256 for event in normalization.events),
        )
        state.sidecar.append_terminal(
            lifecycle_id=lifecycle_id,
            session_id=request.arm.session_id,
            recorded_at=request.observed_at,
            terminal_flat_proof_sha256=proof,
        )
        decision_episode_id = bundle.decision_episode_id or f"dec-{lifecycle_id}"
        append_outcome_episode(
            state.ledger,
            OutcomeEpisode(
                outcome_id=f"out-{lifecycle_id}",
                decision_episode_id=decision_episode_id,
                event_id=bundle.permit.event_run_id,
                open_permit_id=bundle.permit.permit_id,
                close_permit_id=close_permit.permit_id,
                open_order_id=bundle.open_order_id,
                close_order_id=close_order_id,
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
        state.blocked.resolve(lifecycle_id, request.observed_at)
        return HostLifecycleOutcome.terminal_flat(request, terminal_flat_proof_sha256=proof)


def doors_state_dir(state: PaperMcpCompositionState) -> Path:
    """The validated authority state directory owning durable production journals."""

    return state.authority.state_dir


class PaperMcpPlanFactory:
    """Delayed, release-bound production plan factory for one armed session.

    Unlike the synthetic scaffold's trusted ``module:function`` host plan, this
    factory is package-owned code: the host supplies only the narrow doors, and
    the plan refuses to exist unless the approved route, the prepared PAPER
    session capability, and the observed account fingerprint all bind exactly
    to the validated authority.
    """

    def __init__(self, doors: PaperMcpHostDoors) -> None:
        if type(doors) is not PaperMcpHostDoors:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "doors must be PaperMcpHostDoors"
            )
        if type(doors.prepared_session) is not PreparedHostMcpSession:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.SESSION_NOT_PREPARED,
                "production composition requires a factory-prepared host MCP session",
            )
        if type(doors.approved_route) not in (
            QwenDashScopeReasonerRoute,
            FurryGatewayReasonerRoute,
            MinimaxM3ReasonerRoute,
        ):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.ROUTE_NOT_APPROVED,
                "production composition requires an owner-approved direct-provider "
                "host route adapter",
            )
        if doors.reasoner is not doors.approved_route:
            # No seam between the approved binding and the engine door: the
            # reasoner the engine calls must be the exact approved adapter
            # object, so a synthetic or drifted double can never front the
            # production composition.
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.ROUTE_NOT_APPROVED,
                "engine reasoner door must be the identical approved route adapter",
            )
        if (
            type(doors.reasoner_identity) is not RouteIdentity
            or doors.reasoner_identity != doors.approved_route.identity
        ):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID,
                "reasoner identity must exactly match the approved adapter identity",
            )
        if type(doors.feed) is not PaperMcpFeed:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "feed must be a PaperMcpFeed"
            )
        if type(doors.ledger) is not RiskLedger:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "ledger must be a RiskLedger"
            )
        if type(doors.expression_policy) is not PromotedExpressionPolicy:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID,
                "production composition requires an explicitly promoted expression policy",
            )
        if not callable(doors.clock) or not callable(doors.capture_sources.sources_for):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID, "clock and capture doors must be callable"
            )
        if not callable(doors.expression_snapshots.snapshot_for) or not callable(
            doors.lifecycle_clocks
        ):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID,
                "expression and lifecycle-clock doors must be callable",
            )
        if (
            not isinstance(doors.account_id, str)
            or not doors.account_id
            or len(doors.account_id) > 128
        ):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID,
                "account_id must be a sanitized bounded identifier",
            )
        if (
            not isinstance(doors.close_limit_price, Decimal)
            or not doors.close_limit_price.is_finite()
        ):
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.DOOR_INVALID,
                "close_limit_price must be a finite Decimal from the armed risk envelope",
            )
        observation = doors.prepared_session.observation
        if observation.environment is not HostMcpEnvironment.PAPER:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.SESSION_NOT_PREPARED,
                "prepared session does not attest the PAPER environment",
            )
        self._doors = doors

    def __call__(self, authority: ValidatedAutonomousHostAuthority) -> AutonomousHostPlan:
        doors = self._doors
        approved = load_current_approved_reasoner_route()
        route = doors.approved_route.validated_route
        if (
            route.route_sha256 != approved.route_sha256
            or route.model_config_sha256 != approved.model_config_sha256
            or authority.session_arm.reasoner_route_sha256 != approved.route_sha256
            or authority.session_arm.reasoner_model_config_sha256 != approved.model_config_sha256
        ):
            raise AutonomousHostRejected("ROUTE_NOT_APPROVED")
        observation = doors.prepared_session.observation
        broker = doors.prepared_session.lifecycle_broker(clock=doors.clock)
        if type(broker) is not LifecycleMcpPaperBroker:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.SESSION_NOT_PREPARED,
                "lifecycle broker was not factory-issued",
            )
        try:
            account_raw = asyncio.run(
                _readonly_canonical_bytes(doors.prepared_session, ACCOUNT_TOOL, {})
            )
            account_payload = _account_payload(account_raw)
            fingerprint = account_fingerprint_from_payload(account_raw)
        except (HostMcpError, PaperMcpCompositionRejected) as error:
            raise AutonomousHostRejected("ACCOUNT_TRUTH_UNAVAILABLE") from error
        if fingerprint != authority.account_fingerprint_sha256:
            raise AutonomousHostRejected("ACCOUNT_FINGERPRINT_MISMATCH")
        observed_account_id = account_payload.get("id")
        if not isinstance(observed_account_id, str) or not observed_account_id:
            raise PaperMcpCompositionRejected(
                PaperMcpCompositionReason.BROKER_TRUTH_UNAVAILABLE,
                "account payload lacks an account identity",
            )
        if observed_account_id != doors.account_id:
            # The lifecycle worker binds every broker truth read to this exact
            # identity; a door that names a different account fails closed here
            # instead of at the first post-mutation truth check.
            raise AutonomousHostRejected("ACCOUNT_IDENTITY_MISMATCH")
        # The frozen p95 latency profile must load and validate; a stale or
        # hash-mismatched packaged profile blocks the composition instead of
        # degrading silently (PRD PR-2: composition is impossible unless the
        # latency profile validates).
        try:
            latency_profile = load_latency_profile()
        except LatencyProfileRejected as error:
            raise AutonomousHostRejected("LATENCY_PROFILE_STALE") from error
        binding_sha256 = sha256_bytes(
            canonical_json_bytes(
                {
                    "schema": PAPER_MCP_PRODUCTION_BINDING_SCHEMA,
                    "schema_version": 1,
                    "session_arm_sha256": authority.session_arm_sha256,
                    "release_sha256": authority.release_sha256,
                    "runtime_build_artifact_sha256": authority.runtime_build_artifact_sha256,
                    "capability_sha256": observation.capability_sha256,
                    "host_operations_protocol_sha256": ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256,
                    "route_sha256": route.route_sha256,
                    "model_config_sha256": route.model_config_sha256,
                    "latency_profile_sha256": latency_profile.content_sha256,
                    "account_fingerprint_sha256": fingerprint,
                    "mutation_permitted": doors.mutation_permitted,
                }
            )
        )
        state = PaperMcpCompositionState(
            doors=doors,
            authority=authority,
            binding_sha256=binding_sha256,
            broker=broker,
            sidecar=HostPersistenceSidecar(authority.state_dir / HOST_PERSISTENCE_FILENAME),
            blocked=BlockedStateJournal(
                authority.state_dir / BLOCKED_STATE_FILENAME, retry_budget=doors.retry_budget
            ),
            truth_source=PaperMcpAccountTruthSource(doors),
            gate=PaperMcpMutationGate(doors.mutation_permitted),
        )
        return AutonomousHostPlan(
            execution_class=HostExecutionClass.PAPER_MCP,
            reconciliation_backend=PaperMcpReconciliationBackend(state),
            collector_backend=PaperMcpDueWindowBackend(state),
            candidate_backend=PaperMcpCandidateBackend(state),
            lifecycle_backend=PaperMcpLifecycleBackend(state),
        )


def paper_mcp_plan_factory(doors: PaperMcpHostDoors) -> PaperMcpPlanFactory:
    """Bind the host doors to a delayed, authority-checked production plan factory."""

    return PaperMcpPlanFactory(doors)


__all__ = [
    "ACTIVITY_CURSOR_DIRNAME",
    "BLOCKED_STATE_FILENAME",
    "BLOCKED_STATE_SCHEMA",
    "BLOCKED_STATE_SCHEMA_VERSION",
    "DEFAULT_RETRY_BUDGET",
    "PAPER_MCP_COMPOSITION_CLAIMS",
    "PAPER_MCP_PRODUCTION_BINDING_SCHEMA",
    "PAPER_MCP_TERMINAL_FLAT_PROOF_SCHEMA",
    "AccountActivitySource",
    "BlockedStateJournal",
    "CaptureSourceDoor",
    "ExpressionSnapshotDoor",
    "LifecycleClockFactory",
    "PaperMcpAccountTruthSource",
    "PaperMcpCandidateBackend",
    "PaperMcpCompositionReason",
    "PaperMcpCompositionRejected",
    "PaperMcpCompositionState",
    "PaperMcpDueWindowBackend",
    "PaperMcpFeed",
    "PaperMcpFeedEvent",
    "PaperMcpHostDoors",
    "PaperMcpLifecycleBackend",
    "PaperMcpMutationGate",
    "PaperMcpPlanFactory",
    "PaperMcpReconciliationBackend",
    "account_fingerprint_from_payload",
    "doors_state_dir",
    "paper_mcp_plan_factory",
    "paper_mcp_terminal_flat_proof_sha256",
]
