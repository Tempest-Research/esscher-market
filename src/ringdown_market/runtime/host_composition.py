"""Composition of real application services behind the autonomous host ports.

This module wires the four ``runtime.autonomous_host`` backend protocols to the
real services: the frozen source compiler, the bounded decision engine, the
deterministic synthetic confirmation bridge, the Gate D expression compiler,
the canonical permit bridge, the V2 risk kernel, ``PaperStrategyApplication``,
the monitored lifecycle worker, and the synthetic in-memory broker.  Nothing
here contacts a provider, broker, account, or network: every route, quote,
fill, and clock is synthetic, deterministic, and labelled SYNTHETIC_FAKE /
NOT_ALPHA_EVIDENCE.  The autonomous coordinator remains the sole candidate
loop and the sole writer of autonomous session state.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ringdown_market.alpha.models import Direction
from ringdown_market.application.autonomous_bridge import (
    RiskAbstentionRejected,
    SyntheticConfirmationAbstained,
    confirmation_epsilon,
    confirmation_value,
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
from ringdown_market.contracts.execution_policy import ALPACA_MCP_PROTOCOL_SHA256
from ringdown_market.execution.expression import (
    EXECUTABLE_DATA,
    CompiledExpression,
    ExpressionKind,
    ExpressionMarketSnapshot,
    FeedIdentity,
    OptionContractObservation,
    PackageObservation,
    PromotedExpressionPolicy,
    ShareObservation,
    TwoSidedQuote,
)
from ringdown_market.execution.models import DebitVerticalPermit
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
from ringdown_market.risk import (
    RiskKernel,
    RiskLedger,
    RiskRejected,
    load_risk_policy_v2,
    risk_policy_v2_sha256,
)
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
    HostReconciliationStatus,
    SyntheticBrokerTruth,
    ValidatedAutonomousHostAuthority,
)
from ringdown_market.runtime.host_fake_broker import (
    SYNTHETIC_PAPER_ACCOUNT_ID,
    SyntheticAccountTruthSource,
    SyntheticBrokerAmbiguousMutation,
    SyntheticPaperBroker,
)
from ringdown_market.runtime.host_persistence import (
    HOST_PERSISTENCE_FILENAME,
    HostPersistenceRejected,
    HostPersistenceSidecar,
)
from ringdown_market.sourcedata import (
    CaptureConfiguration,
    CompiledSnapshot,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
)
from ringdown_market.sourcedata.reasons import CollectorRejected
from ringdown_market.strategy.contracts import (
    canonical_json_bytes,
    reasoner_policy_hashes,
    sha256_bytes,
)
from ringdown_market.strategy.models import (
    ExchangeStatus,
    FeatureStatus,
    ReasonerExchange,
    StrategyInput,
)
from ringdown_market.strategy.reasoner import (
    SYNTHETIC_ROUTE_IDENTITY,
    ReasonerRouteRequest,
    ReasonerRouteResult,
    RouteIdentity,
    deadline_for,
)

SYNTHETIC_REHEARSAL_CLAIMS = ("SYNTHETIC_FAKE", "NOT_ALPHA_EVIDENCE")
SYNTHETIC_GATE_D_REPORT_SHA256 = sha256_bytes(
    canonical_json_bytes(
        {
            "schema": "esscher.synthetic_gate_d_report_marker",
            "schema_version": 1,
            "claims": list(SYNTHETIC_REHEARSAL_CLAIMS),
        }
    )
)
SYNTHETIC_ROUTE_BUILD_SHA256 = sha256_bytes(
    canonical_json_bytes(
        {
            "producer": "esscher.runtime.synthetic_rehearsal_route",
            "contract": "esscher.reasoner_exchange",
            "version": 1,
        }
    )
)
SYNTHETIC_ROUTE_SUMMARY = (
    "SYNTHETIC_FAKE rehearsal route: the direction is the deterministic sign of the "
    "frozen confirmation feature against its policy epsilon. NOT_ALPHA_EVIDENCE."
)
SYNTHETIC_FALSIFIER_SUMMARY = (
    "The synthetic confirmation could fade after the decision cutoff; NOT_ALPHA_EVIDENCE."
)
SYNTHETIC_CLOSE_LIMIT_PRICE = Decimal("-0.20")
SYNTHETIC_HOLD_INTERVAL = timedelta(minutes=20)
SYNTHETIC_FLATTENING_INTERVAL = timedelta(minutes=50)
SYNTHETIC_CLOSE_DELAY = timedelta(seconds=5)
SYNTHETIC_DECISION_LATENCY = timedelta(seconds=10)
SYNTHETIC_PERMIT_MARGIN = timedelta(seconds=55)

EARNINGS_LANE_V2 = "EARNINGS_RESIDUAL_CONTINUATION_V2"
MARKET_ANCHOR_LANE_V2 = "MARKET_ANCHOR_INTRADAY_CONTINUATION_V1"
CATALYST_LANE_V2 = "LIQUID_STOCK_CATALYST_CONTINUATION_V1"

MARKET_FIXTURE_KEYS = ("daily_bars", "prior_window_trades", "reaction_quotes", "reaction_trades")

_EQUITY_FEED = FeedIdentity(
    "SYNTHETIC_SIP_EQUITY_FEED", "read_only_equity_quote", "equity_quote.v1", "1"
)
_OPTION_FEED = FeedIdentity(
    "SYNTHETIC_OPTION_SNAPSHOT_FEED", "read_only_option_chain", "option_chain_snapshot.v1", "1"
)
_PACKAGE_FEED = FeedIdentity(
    "SYNTHETIC_PACKAGE_FEED", "read_only_package_quote", "package_quote.v1", "1"
)
_LONG_CALL_STRIKE = Decimal("61")
_SHORT_CALL_STRIKE = Decimal("62")
_LONG_PUT_STRIKE = Decimal("60")
_SHORT_PUT_STRIKE = Decimal("59")
_PACKAGE_NET_BID = Decimal("0.0195")
_PACKAGE_NET_ASK = Decimal("0.02")


class CompositionRejected(ValueError):
    """Raised when the synthetic composition cannot drive the real services."""


class SyntheticRehearsalMutationGate:
    """The rehearsal gate: open only inside the fully synthetic host plan."""

    def mutation_permitted(self) -> bool:
        return True


def synthetic_promoted_expression_policy() -> PromotedExpressionPolicy:
    """Return the deterministic synthetic promoted debit-vertical policy."""

    return PromotedExpressionPolicy(
        policy_id="SYNTHETIC_REHEARSAL_PROMOTED_EXPRESSION_V1",
        version="v1",
        gate_d_report_sha256=SYNTHETIC_GATE_D_REPORT_SHA256,
        expression_kind=ExpressionKind.DEBIT_VERTICAL,
        objective="AFTER_COST_EXPECTED_EDGE_VS_CASH",
        evidence_threshold=Decimal("0"),
        evidence_min_events=1,
        operational_loss_budget=Decimal("500"),
        quote_max_age_ms=5000,
        cross_leg_skew_max_ms=1000,
        spread_max_bps=Decimal("500"),
        min_quote_size=1,
        min_dte=7,
        max_dte=21,
        delta_min=Decimal("0.20"),
        delta_max=Decimal("0.60"),
        width_min=Decimal("0.5"),
        width_max=Decimal("10"),
        liquidity_min_open_interest=50,
    )


def rehearsal_direction(strategy_input: StrategyInput) -> Direction:
    """Return the deterministic confirmation-sign direction for one input."""

    candidate_id = strategy_input.snapshot.candidate_id
    epsilon = confirmation_epsilon(candidate_id)
    feature_id = _confirmation_feature_id(candidate_id)
    feature = strategy_input.feature_by_id.get(feature_id)
    if (
        feature is None
        or feature.status is not FeatureStatus.PRESENT
        or not isinstance(feature.value, Decimal)
    ):
        return Direction.UNCERTAIN
    value = confirmation_value(strategy_input, candidate_id)
    if value is None or abs(value) < epsilon:
        return Direction.UNCERTAIN
    return Direction.UP if value > 0 else Direction.DOWN


def _confirmation_feature_id(candidate_id: str) -> str:
    feature_ids = {
        "EARNINGS_RESIDUAL_CONTINUATION_V1": "market.opening_residual_log_return.v1",
        "MACRO_SPY_CONTINUATION_CHALLENGER_V1": "market.spy_event_zscore_60.v1",
    }
    feature_id = feature_ids.get(candidate_id)
    if feature_id is None:
        raise CompositionRejected(f"candidate {candidate_id} has no confirmation feature")
    return feature_id


def _cited_evidence_ids(strategy_input: StrategyInput) -> tuple[str, ...]:
    primary = sorted(
        ref.evidence_id for ref in strategy_input.snapshot.evidence_refs if ref.role.is_primary
    )
    market = sorted(
        ref.evidence_id for ref in strategy_input.snapshot.evidence_refs if ref.role.is_market
    )
    if not primary or not market:
        return ()
    return tuple(sorted({primary[0], market[0]}))


class SyntheticRehearsalRoute:
    """Deterministic offline reasoner route bound to the confirmation-sign rule."""

    def __init__(self, identity: RouteIdentity = SYNTHETIC_ROUTE_IDENTITY) -> None:
        self._identity = identity

    def __call__(self, request: ReasonerRouteRequest) -> ReasonerRouteResult:
        strategy_input = request.strategy_input
        snapshot = strategy_input.snapshot
        started_at = request.started_at
        deadline_at = deadline_for(strategy_input, started_at)
        route_sha256, prompt_sha256, output_schema_sha256 = reasoner_policy_hashes(
            snapshot.candidate_id
        )
        direction = rehearsal_direction(strategy_input)
        cited = _cited_evidence_ids(strategy_input)
        if direction is not Direction.UNCERTAIN and not cited:
            direction = Direction.UNCERTAIN
        market_id = cited[-1] if cited else None
        payload = {
            "contradictions": [],
            "decision": direction.value,
            "evidence_ids": list(cited),
            "strongest_falsifier": (
                None
                if market_id is None
                else {"evidence_id": market_id, "summary": SYNTHETIC_FALSIFIER_SUMMARY}
            ),
            "summary": SYNTHETIC_ROUTE_SUMMARY,
            "unknowns": [],
        }
        raw = canonical_json_bytes(payload)
        request_sha256 = sha256_bytes(
            canonical_json_bytes(
                {
                    "ablate_text": request.ablate_text,
                    "candidate_id": snapshot.candidate_id,
                    "event_id": snapshot.event_id,
                    "feature_receipt_sha256": strategy_input.feature_receipt_sha256,
                    "model_config_sha256": self._identity.model_config_sha256(),
                    "output_schema_sha256": output_schema_sha256,
                    "policy_sha256": snapshot.policy_sha256,
                    "prompt_sha256": prompt_sha256,
                    "route_sha256": route_sha256,
                    "strategy_snapshot_sha256": strategy_input.snapshot_sha256,
                }
            )
        )
        exchange = ReasonerExchange(
            event_id=snapshot.event_id,
            candidate_id=snapshot.candidate_id,
            policy_sha256=snapshot.policy_sha256,
            strategy_snapshot_sha256=strategy_input.snapshot_sha256,
            feature_receipt_sha256=strategy_input.feature_receipt_sha256,
            evidence_packet_sha256=snapshot.evidence_packet_sha256,
            route_sha256=route_sha256,
            prompt_sha256=prompt_sha256,
            output_schema_sha256=output_schema_sha256,
            model_config_sha256=self._identity.model_config_sha256(),
            request_sha256=request_sha256,
            raw_response_sha256=sha256_bytes(raw),
            provider=self._identity.provider,
            model=self._identity.model,
            model_revision=self._identity.model_revision,
            decoding=self._identity.decoding(),
            started_at=started_at,
            responded_at=started_at + timedelta(seconds=1),
            deadline_at=deadline_at,
            status=ExchangeStatus.COMPLETED,
            error_code=None,
            producer_build_sha256=SYNTHETIC_ROUTE_BUILD_SHA256,
            created_at=deadline_at,
        )
        return ReasonerRouteResult(exchange=exchange, raw_response_bytes=raw)


@dataclass(frozen=True, slots=True)
class CompositionFeedEvent:
    """One synthetic feed event bound to one due window and lane."""

    event_id: str
    window_id: str
    candidate_id: str
    evidence_manifest_bytes: bytes
    market_window_bytes: bytes
    capture_at: datetime
    market_publisher: str
    market_entitlement: str
    market_redistribution: str

    def __post_init__(self) -> None:
        for name in ("event_id", "window_id", "candidate_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise CompositionRejected(f"{name} must be non-empty exact text")
        for name in ("evidence_manifest_bytes", "market_window_bytes"):
            raw = getattr(self, name)
            if type(raw) is not bytes:
                raise CompositionRejected(f"{name} must be immutable bytes")
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CompositionRejected(f"{name} must be canonical JSON") from error
            if not isinstance(decoded, dict):
                raise CompositionRejected(f"{name} must be a JSON object")
        if not isinstance(self.capture_at, datetime) or self.capture_at.tzinfo is None:
            raise CompositionRejected("capture_at must be timezone-aware")
        if self.market_entitlement not in {"ENTITLED", "UNVERIFIED"}:
            raise CompositionRejected("market_entitlement must be ENTITLED or UNVERIFIED")
        if self.market_redistribution not in {"REDISTRIBUTABLE", "NON_REDISTRIBUTABLE"}:
            raise CompositionRejected("market_redistribution must be an explicit status")

    @property
    def opportunity_id(self) -> str:
        """Return the deterministic autonomous opportunity identity."""

        return f"OPP-{self.event_id}-{self.window_id}"

    def strategy_context_sha256(self, window_sha256: str) -> str:
        """Content-address the event, its evidence receipts, and the window."""

        return sha256_bytes(
            canonical_json_bytes(
                {
                    "schema": "esscher.synthetic_composition_context",
                    "schema_version": 1,
                    "claims": list(SYNTHETIC_REHEARSAL_CLAIMS),
                    "event_id": self.event_id,
                    "evidence_manifest_sha256": sha256_bytes(self.evidence_manifest_bytes),
                    "market_window_sha256": sha256_bytes(self.market_window_bytes),
                    "window_sha256": window_sha256,
                }
            )
        )


@dataclass(frozen=True, slots=True)
class CompositionFeed:
    """The complete synthetic observation feed for one rehearsal session."""

    events: tuple[CompositionFeedEvent, ...]

    def __post_init__(self) -> None:
        if type(self.events) is not tuple:
            raise CompositionRejected("feed events must be an immutable tuple")
        identities = tuple(event.opportunity_id for event in self.events)
        if len(identities) != len(set(identities)):
            raise CompositionRejected("feed contains duplicate opportunity identities")

    def event_for_opportunity(self, opportunity_id: str) -> CompositionFeedEvent | None:
        """Return the sole feed event bound to one opportunity identity."""

        for event in self.events:
            if event.opportunity_id == opportunity_id:
                return event
        return None


def split_composition_fixture(fixture: Mapping[str, object]) -> tuple[bytes, bytes]:
    """Split one sourcedata fixture into evidence-manifest and market bytes."""

    evidence = {key: value for key, value in fixture.items() if key not in MARKET_FIXTURE_KEYS}
    market = {key: fixture[key] for key in MARKET_FIXTURE_KEYS if key in fixture}
    if not evidence or not market:
        raise CompositionRejected("composition fixture must carry evidence and market sections")
    return canonical_json_bytes(evidence), canonical_json_bytes(market)


def rejoin_composition_fixture(evidence_bytes: bytes, market_bytes: bytes) -> dict[str, object]:
    """Rebuild the fixture mapping consumed by the sourcedata fake loaders."""

    try:
        evidence = json.loads(evidence_bytes.decode("utf-8"))
        market = json.loads(market_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompositionRejected("feed event bytes are not valid JSON") from error
    if not isinstance(evidence, dict) or not isinstance(market, dict):
        raise CompositionRejected("feed event bytes must be JSON objects")
    overlap = sorted(set(evidence) & set(market))
    if overlap:
        raise CompositionRejected(f"feed event sections overlap: {overlap}")
    return {**evidence, **market}


@dataclass(frozen=True, slots=True)
class RehearsalTimeline:
    """Deterministic fixture-domain clocks for one candidate rehearsal."""

    started_at: datetime
    authorization_at: datetime
    open_at: datetime


def rehearsal_timeline(strategy_input: StrategyInput) -> RehearsalTimeline:
    """Derive the synthetic decision, authorization, and opening clocks."""

    snapshot = strategy_input.snapshot
    started_at = max(
        strategy_input.feature_receipt.created_at,
        snapshot.decision_cutoff_at - SYNTHETIC_DECISION_LATENCY,
    )
    responded_at = started_at + timedelta(seconds=1)
    deadline_at = min(
        started_at + timedelta(seconds=8),
        snapshot.decision_cutoff_at,
    )
    authorization_at = snapshot.candidate_entry_deadline_at - SYNTHETIC_PERMIT_MARGIN
    open_at = authorization_at + timedelta(seconds=1)
    permit_expires_at = min(
        authorization_at + timedelta(seconds=60),
        snapshot.candidate_entry_deadline_at,
    )
    if (
        started_at > snapshot.decision_cutoff_at
        or responded_at > deadline_at
        or authorization_at < responded_at
        or open_at >= permit_expires_at
    ):
        raise CompositionRejected(
            "fixture clocks cannot host a deterministic synthetic rehearsal timeline"
        )
    return RehearsalTimeline(
        started_at=started_at,
        authorization_at=authorization_at,
        open_at=open_at,
    )


def rehearsal_lifecycle_clocks(
    *,
    snapshot: CompiledSnapshot,
    expression: CompiledExpression,
    permit: DebitVerticalPermit,
) -> LifecycleClocks:
    """Build deterministic exit-plan clocks from the frozen snapshot deadlines."""

    source = snapshot.snapshot
    entry_deadline = source.candidate_entry_deadline_at
    return LifecycleClocks(
        event_run_id=permit.event_run_id,
        cohort_id=source.cohort_id,
        policy_sha256=permit.policy_sha256,
        source_sha256=source.evidence_packet_sha256,
        observation_window_start_at=source.observation_window_start_at,
        observation_window_end_at=source.observation_window_end_at,
        entry_deadline_at=entry_deadline,
        time_exit_at=entry_deadline + SYNTHETIC_HOLD_INTERVAL,
        flattening_deadline_at=entry_deadline + SYNTHETIC_FLATTENING_INTERVAL,
    )


def _occ_symbol(underlying: str, expiry: date, option_type: str, strike: Decimal) -> str:
    code = {"CALL": "C", "PUT": "P"}.get(option_type)
    if code is None:
        raise CompositionRejected(f"unsupported synthetic option type {option_type}")
    return f"{underlying}{expiry:%y%m%d}{code}{int(strike * 1000):08d}"


def rehearsal_expression_snapshot(
    *,
    underlying: str,
    decision_sha256: str,
    observation_clock_at: datetime,
) -> ExpressionMarketSnapshot:
    """Build the deterministic synthetic Gate D market snapshot."""

    quote_at = observation_clock_at - timedelta(seconds=2)
    expiry = (observation_clock_at.astimezone(UTC) + timedelta(days=7)).date()

    def quote(bid: str, ask: str) -> TwoSidedQuote:
        return TwoSidedQuote(
            bid=Decimal(bid),
            ask=Decimal(ask),
            bid_size=100,
            ask_size=100,
            observed_at=quote_at,
        )

    def contract(
        option_type: str,
        strike: Decimal,
        bid: str,
        ask: str,
        delta: str,
    ) -> OptionContractObservation:
        return OptionContractObservation(
            symbol=_occ_symbol(underlying, expiry, option_type, strike),
            underlying=underlying,
            expiry=expiry,
            option_type=option_type,
            strike=strike,
            quote=quote(bid, ask),
            feed=_OPTION_FEED,
            data_class=EXECUTABLE_DATA,
            open_interest=200,
            reported_delta=Decimal(delta),
        )

    long_call = contract("CALL", _LONG_CALL_STRIKE, "0.80", "0.84", "0.45")
    short_call = contract("CALL", _SHORT_CALL_STRIKE, "0.46", "0.48", "0.30")
    short_put = contract("PUT", _SHORT_PUT_STRIKE, "0.44", "0.46", "-0.30")
    long_put = contract("PUT", _LONG_PUT_STRIKE, "0.78", "0.82", "-0.45")
    chain = tuple(sorted((long_call, short_call, long_put, short_put), key=lambda c: c.symbol))

    def package(long_leg, short_leg) -> PackageObservation:
        return PackageObservation(
            package_id=f"{long_leg.symbol}+{short_leg.symbol}",
            legs=(long_leg.symbol, short_leg.symbol),
            net_bid=_PACKAGE_NET_BID,
            net_ask=_PACKAGE_NET_ASK,
            size=10,
            observed_at=quote_at,
            feed=_PACKAGE_FEED,
            data_class=EXECUTABLE_DATA,
        )

    packages = tuple(
        sorted(
            (package(long_call, short_call), package(long_put, short_put)),
            key=lambda observation: observation.package_id,
        )
    )
    return ExpressionMarketSnapshot(
        snapshot_id=f"synthetic-rehearsal-{decision_sha256[:16]}",
        underlying=underlying,
        observation_clock_at=observation_clock_at.astimezone(UTC),
        decision_sha256=decision_sha256,
        share=ShareObservation(
            symbol=underlying,
            quote=quote("61.40", "61.44"),
            feed=_EQUITY_FEED,
            data_class=EXECUTABLE_DATA,
        ),
        chain=chain,
        packages=packages,
        borrow_locate=None,
    )


def terminal_flat_proof_sha256(
    *,
    session_id: str,
    lifecycle_id: str,
    close_order_id: str,
    close_permit_id: str,
    closed_at: datetime,
    broker: SyntheticPaperBroker,
) -> str:
    """Content-address one synthetic terminal-flat proof over broker state."""

    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema": "esscher.synthetic_terminal_flat_proof",
                "schema_version": 1,
                "claims": list(SYNTHETIC_REHEARSAL_CLAIMS),
                "session_id": session_id,
                "lifecycle_id": lifecycle_id,
                "close_order_id": close_order_id,
                "close_permit_id": close_permit_id,
                "closed_at": closed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "account_state_sha256": broker.account_state_sha256(),
                "orders_state_sha256": broker.orders_state_sha256(),
                "positions_state_sha256": broker.positions_state_sha256(),
                "is_flat": broker.is_flat(),
            }
        )
    )


class SyntheticRehearsalClock:
    """Injected monotonic rehearsal clock; wall time is never read."""

    def __init__(self) -> None:
        self._current: datetime | None = None

    def now(self) -> datetime:
        """Return the current rehearsal instant."""

        if self._current is None:
            raise CompositionRejected("rehearsal clock was never set")
        return self._current

    def set(self, value: datetime) -> None:
        """Advance the rehearsal clock; backwards motion fails closed."""

        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CompositionRejected("rehearsal clock values must be timezone-aware")
        normalized = value.astimezone(UTC)
        if self._current is not None and normalized < self._current:
            raise CompositionRejected("rehearsal clock must advance monotonically")
        self._current = normalized


def _rehearsal_application(
    state: CompositionState,
    kernel: RiskKernel,
) -> PaperStrategyApplication:
    """Build the real application service or the injected factory override."""

    if state.application_factory is not None:
        return state.application_factory(kernel=kernel)
    return PaperStrategyApplication(
        reasoner_route=SyntheticRehearsalRoute(),
        expression_policy=state.expression_policy,
        risk_kernel=kernel,
        risk_policy_sha256=risk_policy_v2_sha256(),
        gate_d_report_sha256=state.expression_policy.gate_d_report_sha256,
        execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
        lifecycle_clocks=rehearsal_lifecycle_clocks,
        account_id=SYNTHETIC_PAPER_ACCOUNT_ID,
        route_identity=SYNTHETIC_ROUTE_IDENTITY,
    )


@dataclass
class CompositionState:
    """Shared deterministic services behind the four host backends."""

    feed: CompositionFeed
    ledger: RiskLedger
    broker: SyntheticPaperBroker
    sidecar: HostPersistenceSidecar
    clock: SyntheticRehearsalClock
    expression_policy: PromotedExpressionPolicy
    application_factory: Callable[..., PaperStrategyApplication] | None = None
    reconciliation_fault: str | None = None
    emitted: set[str] = field(default_factory=set)
    candidate_calls: int = 0
    lifecycle_calls: int = 0
    reconciliation_calls: int = 0
    collector_calls: int = 0
    orphan_cancels: int = 0


class CompositionReconciliationBackend:
    """Observe the synthetic broker and attest structured broker truth.

    Before attesting, the backend resolves orphaned working orders through the
    risk-reducing cancel path only.  An ambiguous synthetic submission leaves a
    working order with no attributable lifecycle, and the arm's flatten
    authority permits cancelling it; this backend never submits a new order.
    """

    def __init__(self, state: CompositionState) -> None:
        self._state = state

    def _resolve_orphaned_working_orders(self) -> None:
        broker = self._state.broker
        for order_id in broker.working_order_ids():
            asyncio.run(broker.cancel_order(order_id))
            self._state.orphan_cancels += 1

    def observe_reconciliation(
        self, request: ReconciliationRequest
    ) -> HostReconciliationObservation:
        self._state.reconciliation_calls += 1
        self._resolve_orphaned_working_orders()
        broker = self._state.broker
        truth = SyntheticBrokerTruth.for_request(
            request,
            account_state_sha256=broker.account_state_sha256(),
            orders_state_sha256=broker.orders_state_sha256(),
            positions_state_sha256=broker.positions_state_sha256(),
            open_order_count=broker.open_order_count(),
            open_position_count=broker.open_position_count(),
            is_flat=broker.is_flat(),
        )
        observation = HostReconciliationObservation.complete(request, broker_truth=truth)
        fault = self._state.reconciliation_fault
        if fault == "INCOMPLETE":
            return replace(observation, status=HostReconciliationStatus.INCOMPLETE)
        if fault == "AMBIGUOUS":
            return replace(observation, status=HostReconciliationStatus.AMBIGUOUS)
        return observation


class CompositionDueWindowBackend:
    """Emit each feed event for its due window exactly once per backend."""

    def __init__(self, state: CompositionState) -> None:
        self._state = state

    def observe_due_window(self, request: DueWindowRequest) -> tuple[HostCandidateObservation, ...]:
        self._state.collector_calls += 1
        observations: list[HostCandidateObservation] = []
        for event in self._state.feed.events:
            if event.window_id != request.window.window_id:
                continue
            if event.candidate_id not in request.window.candidate_ids:
                continue
            if event.opportunity_id in self._state.emitted:
                continue
            self._state.emitted.add(event.opportunity_id)
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


class CompositionCandidateBackend:
    """Drive the real source-to-permit pipeline and one synthetic opening."""

    def __init__(self, state: CompositionState) -> None:
        self._state = state

    def process_candidate(self, request: CandidateProcessingRequest) -> HostCandidateOutcome:
        state = self._state
        state.candidate_calls += 1
        event = state.feed.event_for_opportunity(request.opportunity.opportunity_id)
        if event is None or event.candidate_id != request.opportunity.candidate_id:
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="PORT_OUTPUT_INVALID"
            )
        fixture = rejoin_composition_fixture(
            event.evidence_manifest_bytes, event.market_window_bytes
        )
        capture = CaptureConfiguration(
            candidate_manifest_bytes=build_candidate_manifest(fixture),
            event_id=str(fixture["event_id"]),
            capture_at=event.capture_at,
            market_publisher=event.market_publisher,
            market_entitlement=event.market_entitlement,
            market_redistribution=event.market_redistribution,
        )
        evidence = FixtureEvidenceSource(fixture)
        market = FixtureMarketDataSource(fixture)
        try:
            probe = compile_strategy_snapshot(capture, evidence, market)
            joined = compiled_strategy_input(probe)
            timeline = rehearsal_timeline(joined)
        except (CollectorRejected, CompositionRejected, ValueError, TypeError):
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="PORT_OUTPUT_INVALID"
            )
        state.clock.set(timeline.authorization_at)
        kernel = RiskKernel(
            load_risk_policy_v2(),
            state.ledger,
            SyntheticAccountTruthSource(state.broker),
        )
        kernel.startup_reconciliation(now=timeline.authorization_at)
        application = _rehearsal_application(state, kernel)
        try:
            prepared = application.prepare_v2(
                capture_configuration=capture,
                evidence=evidence,
                market=market,
                expression_snapshot=lambda decision_sha256: rehearsal_expression_snapshot(
                    underlying=joined.snapshot.ticker,
                    decision_sha256=decision_sha256,
                    observation_clock_at=timeline.authorization_at,
                ),
                now=timeline.authorization_at,
                decision_started_at=timeline.started_at,
            )
        except SyntheticConfirmationAbstained:
            return HostCandidateOutcome.abstained(request, reason_code="PORT_OUTPUT_INVALID")
        except RiskAbstentionRejected:
            return HostCandidateOutcome.abstained(request, reason_code="RISK_FREEZE")
        except (PaperPipelineRejected, RiskRejected):
            return HostCandidateOutcome.rejected_before_mutation(
                request, reason_code="PORT_OUTPUT_INVALID", freeze=False
            )
        state.clock.set(timeline.open_at)
        try:
            active = asyncio.run(
                application.open(
                    prepared=prepared,
                    broker=state.broker,
                    clock=state.clock.now,
                    mutation_gate=SyntheticRehearsalMutationGate(),
                )
            )
        except SyntheticBrokerAmbiguousMutation:
            return HostCandidateOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        except Exception:
            return HostCandidateOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code="UNKNOWN_BROKER_STATE",
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
        return HostCandidateOutcome.active(request, lifecycle_id=lifecycle_id)


class CompositionLifecycleBackend:
    """Rehydrate one durable active bundle and drive the real monitored close."""

    def __init__(self, state: CompositionState) -> None:
        self._state = state

    def close_lifecycle(self, request: LifecycleCloseRequest) -> HostLifecycleOutcome:
        state = self._state
        state.lifecycle_calls += 1
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
        if bundle is None or bundle.session_id != request.arm.session_id:
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.UNKNOWN,
                reason_code="CLAIM_RECOVERY_UNKNOWN",
            )
        close_at = bundle.clocks.time_exit_at + SYNTHETIC_CLOSE_DELAY
        state.clock.set(close_at)
        lifecycle = MonitoredPaperLifecycle(
            broker=state.broker,
            ledger=state.ledger,
            clocks=bundle.clocks,
            correlation=bundle.correlation,
            mutation_gate=SyntheticRehearsalMutationGate(),
            clock=state.clock.now,
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
            lifecycle=lifecycle,
        )
        active = ActivePaperLifecycle(
            prepared=None,  # type: ignore[arg-type]
            lifecycle=lifecycle,
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
            expires_at=close_at + timedelta(seconds=60),
            limit_price=SYNTHETIC_CLOSE_LIMIT_PRICE,
        )
        kernel = RiskKernel(
            load_risk_policy_v2(),
            state.ledger,
            SyntheticAccountTruthSource(state.broker),
        )
        application = _rehearsal_application(state, kernel)
        try:
            close_state, close_order_id = asyncio.run(
                application.close(active=active, close_permit=close_permit)
            )
        except SyntheticBrokerAmbiguousMutation:
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        except (LifecycleRejected, PaperPipelineRejected, BrokerOutage, RiskRejected):
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        if close_state is not LifecycleState.CLOSED_FLAT or close_order_id is None:
            return HostLifecycleOutcome.manual_reconciliation_required(
                request,
                mutation_state=MutationState.PARTIAL,
                reason_code="UNKNOWN_BROKER_STATE",
            )
        proof = terminal_flat_proof_sha256(
            session_id=request.arm.session_id,
            lifecycle_id=lifecycle_id,
            close_order_id=close_order_id,
            close_permit_id=close_permit.permit_id,
            closed_at=close_at,
            broker=state.broker,
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
        return HostLifecycleOutcome.terminal_flat(request, terminal_flat_proof_sha256=proof)


class CompositionPlanFactory:
    """Delayed plan factory binding one feed, ledger, and synthetic broker."""

    def __init__(
        self,
        feed: CompositionFeed,
        *,
        ledger: RiskLedger,
        broker: SyntheticPaperBroker,
        application_factory: Callable[..., PaperStrategyApplication] | None = None,
        expression_policy: PromotedExpressionPolicy | None = None,
        reconciliation_fault: str | None = None,
    ) -> None:
        if not isinstance(feed, CompositionFeed):
            raise AutonomousHostRejected("composition feed must be a CompositionFeed")
        if not isinstance(broker, SyntheticPaperBroker):
            raise AutonomousHostRejected("composition broker must be the synthetic PAPER broker")
        if reconciliation_fault is not None and reconciliation_fault not in {
            "INCOMPLETE",
            "AMBIGUOUS",
        }:
            raise AutonomousHostRejected("unsupported reconciliation fault")
        self._feed = feed
        self._ledger = ledger
        self._broker = broker
        self._application_factory = application_factory
        self._expression_policy = expression_policy
        self._reconciliation_fault = reconciliation_fault
        self.states: list[CompositionState] = []

    def __call__(self, authority: ValidatedAutonomousHostAuthority) -> AutonomousHostPlan:
        if authority.account_fingerprint_sha256 != self._broker.account_state_sha256():
            raise AutonomousHostRejected("ACCOUNT_FINGERPRINT_MISMATCH")
        clock = SyntheticRehearsalClock()
        self._broker.clock = clock.now
        state = CompositionState(
            feed=self._feed,
            ledger=self._ledger,
            broker=self._broker,
            sidecar=HostPersistenceSidecar(authority.state_dir / HOST_PERSISTENCE_FILENAME),
            clock=clock,
            expression_policy=self._expression_policy or synthetic_promoted_expression_policy(),
            application_factory=self._application_factory,
            reconciliation_fault=self._reconciliation_fault,
        )
        self.states.append(state)
        return AutonomousHostPlan(
            execution_class=HostExecutionClass.SYNTHETIC_FAKE,
            reconciliation_backend=CompositionReconciliationBackend(state),
            collector_backend=CompositionDueWindowBackend(state),
            candidate_backend=CompositionCandidateBackend(state),
            lifecycle_backend=CompositionLifecycleBackend(state),
        )


def composition_plan_factory(
    feed: CompositionFeed,
    *,
    ledger: RiskLedger,
    broker: SyntheticPaperBroker,
    application_factory: Callable[..., PaperStrategyApplication] | None = None,
    expression_policy: PromotedExpressionPolicy | None = None,
    reconciliation_fault: str | None = None,
) -> CompositionPlanFactory:
    """Bind the synthetic feed to a delayed, fingerprint-checked plan factory."""

    return CompositionPlanFactory(
        feed,
        ledger=ledger,
        broker=broker,
        application_factory=application_factory,
        expression_policy=expression_policy,
        reconciliation_fault=reconciliation_fault,
    )


__all__ = [
    "CATALYST_LANE_V2",
    "EARNINGS_LANE_V2",
    "MARKET_ANCHOR_LANE_V2",
    "MARKET_FIXTURE_KEYS",
    "SYNTHETIC_CLOSE_LIMIT_PRICE",
    "SYNTHETIC_GATE_D_REPORT_SHA256",
    "SYNTHETIC_PAPER_ACCOUNT_ID",
    "SYNTHETIC_REHEARSAL_CLAIMS",
    "CompositionCandidateBackend",
    "CompositionDueWindowBackend",
    "CompositionFeed",
    "CompositionFeedEvent",
    "CompositionLifecycleBackend",
    "CompositionPlanFactory",
    "CompositionReconciliationBackend",
    "CompositionRejected",
    "CompositionState",
    "RehearsalTimeline",
    "SyntheticRehearsalClock",
    "SyntheticRehearsalMutationGate",
    "SyntheticRehearsalRoute",
    "composition_plan_factory",
    "rehearsal_direction",
    "rehearsal_expression_snapshot",
    "rehearsal_lifecycle_clocks",
    "rehearsal_timeline",
    "rejoin_composition_fixture",
    "split_composition_fixture",
    "synthetic_promoted_expression_policy",
    "terminal_flat_proof_sha256",
]
