"""One explicit fail-closed vertical pipeline for the permanent PAPER boundary.

The service joins the otherwise isolated source compiler, bounded decision
engine, Gate-D expression compiler, canonical permit bridge, risk ledger, and
monitored lifecycle.  ``prepare`` has no broker mutation.  ``open`` and
``close`` are explicit async operations and remain closed unless their caller
supplies an approval gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ringdown_market.contracts.compiled_to_permit import (
    PermitBridgeConstants,
    build_debit_vertical_permit,
    canonical_permit_sha256,
)
from ringdown_market.contracts.execution_policy import (
    ALPACA_MCP_COMMIT,
    ALPACA_MCP_PROTOCOL_SHA256,
    ALPACA_MCP_VERSION,
)
from ringdown_market.execution.expression import (
    COMPILED,
    NO_PACKAGE,
    CompiledExpression,
    ExpressionMarketSnapshot,
    PromotedExpressionPolicy,
    compile_or_no_package,
    compiled_expression_sha256,
    promoted_expression_policy_sha256,
)
from ringdown_market.execution.host_mcp import HostMcpEnvironment, PreparedHostMcpSession
from ringdown_market.execution.models import ClosePermit, DebitVerticalPermit
from ringdown_market.lifecycle import (
    MULTI_LEG_ORDER_CLASS,
    PAPER_ACCOUNT_CLASS,
    ClosedMutationGate,
    CorrelationIdentity,
    LifecycleClocks,
    MonitoredPaperLifecycle,
)
from ringdown_market.lifecycle.broker import PaperBroker
from ringdown_market.lifecycle.worker import MutationGate
from ringdown_market.risk import RiskApproval, RiskKernel
from ringdown_market.sourcedata import (
    CaptureConfiguration,
    CompiledSnapshot,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.interfaces import EvidenceSource, MarketDataSource
from ringdown_market.strategy import DecisionDisposition, StrategyInput
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes
from ringdown_market.strategy.engine import BoundedDecisionEngine, EngineOutcome
from ringdown_market.strategy.reasoner import ReasonerRoute, RouteIdentity

PAPER_PIPELINE_PERMIT_PROTOCOL_SHA256 = sha256_bytes(
    canonical_json_bytes(
        {
            "schema": "esscher.compiled_paper_permit_bridge",
            "schema_version": 1,
            "boundary": "PAPER_ONLY",
            "decision_schema": "esscher.validated_decision",
            "expression_schema": "esscher.compiled_expression",
            "risk_binding": "canonical_debit_vertical_permit_bytes",
        }
    )
)


class PaperPipelineRejected(RuntimeError):
    """Raised when one pipeline stage refuses to advance to the next stage."""


class ExpressionSnapshotFactory(Protocol):
    """Build the Gate-D market snapshot only after the decision hash is known."""

    def __call__(self, decision_sha256: str) -> ExpressionMarketSnapshot: ...


class LifecycleClockFactory(Protocol):
    """Build a verified exit-plan clock contract for one prepared permit."""

    def __call__(
        self,
        *,
        snapshot: CompiledSnapshot,
        expression: CompiledExpression,
        permit: DebitVerticalPermit,
    ) -> LifecycleClocks: ...


@dataclass(frozen=True, slots=True)
class PreparedPaperLifecycle:
    """All immutable identities required before a later, explicitly gated open."""

    source_snapshot: CompiledSnapshot
    strategy_input: StrategyInput
    engine_outcome: EngineOutcome
    compiled_expression: CompiledExpression
    expression_sha256: str
    permit: DebitVerticalPermit
    permit_sha256: str
    risk_approval: RiskApproval
    correlation: CorrelationIdentity
    lifecycle_clocks: LifecycleClocks

    def __post_init__(self) -> None:
        if self.engine_outcome.decision.disposition is not DecisionDisposition.ACCEPTED:
            raise PaperPipelineRejected("only an accepted decision can prepare a paper lifecycle")
        if self.permit_sha256 != canonical_permit_sha256(self.permit):
            raise PaperPipelineRejected("prepared permit hash is not canonical")
        if (
            self.risk_approval.permit_id != self.permit.permit_id
            or self.risk_approval.permit_sha256 != self.permit_sha256
        ):
            raise PaperPipelineRejected("risk approval does not preserve the exact prepared permit")
        if (
            self.correlation.event_run_id != self.permit.event_run_id
            or self.correlation.snapshot_sha256 != self.permit.snapshot_sha256
            or self.correlation.decision_sha256 != self.permit.decision_sha256
            or self.correlation.expression_sha256 != self.expression_sha256
            or self.correlation.reservation_id != self.risk_approval.reservation_id
            or self.correlation.open_permit_id != self.permit.permit_id
        ):
            raise PaperPipelineRejected("prepared correlation identity is not causally joined")


@dataclass(slots=True)
class ActivePaperLifecycle:
    """One opened lifecycle and the order identity needed for explicit closing."""

    prepared: PreparedPaperLifecycle
    lifecycle: MonitoredPaperLifecycle
    open_order_id: str
    open_state: object


@dataclass(slots=True)
class PaperStrategyApplication:
    """Compose the new vertical route without granting autonomous mutation authority."""

    reasoner_route: ReasonerRoute
    expression_policy: PromotedExpressionPolicy
    risk_kernel: RiskKernel
    risk_policy_sha256: str
    gate_d_report_sha256: str
    execution_protocol_sha256: str
    lifecycle_clocks: LifecycleClockFactory
    account_id: str
    permit_ttl: timedelta = timedelta(seconds=60)
    account_class: str = PAPER_ACCOUNT_CLASS
    order_class: str = MULTI_LEG_ORDER_CLASS
    route_identity: RouteIdentity | None = None
    _engine: BoundedDecisionEngine = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.permit_ttl <= timedelta(0):
            raise ValueError("permit_ttl must be positive")
        for name in (
            "risk_policy_sha256",
            "gate_d_report_sha256",
            "execution_protocol_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"{name} must be a SHA-256 digest")
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise ValueError("account_id must be non-empty exact text")
        if self.account_class != PAPER_ACCOUNT_CLASS:
            raise ValueError("PaperStrategyApplication accepts only the PAPER account class")
        if self.execution_protocol_sha256 != ALPACA_MCP_PROTOCOL_SHA256:
            raise ValueError(
                "execution_protocol_sha256 must bind the frozen official Alpaca MCP protocol"
            )
        if self.risk_policy_sha256 != self.risk_kernel.policy_sha256:
            raise ValueError("risk_policy_sha256 must match the active risk-kernel policy")
        self._engine = (
            BoundedDecisionEngine(self.reasoner_route)
            if self.route_identity is None
            else BoundedDecisionEngine(self.reasoner_route, identity=self.route_identity)
        )

    @staticmethod
    def _utc(value: datetime, path: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PaperPipelineRejected(f"{path} must be timezone-aware")
        return value.astimezone(UTC)

    def prepare(
        self,
        *,
        capture_configuration: CaptureConfiguration,
        evidence: EvidenceSource,
        market: MarketDataSource,
        expression_snapshot: ExpressionMarketSnapshot | ExpressionSnapshotFactory,
        now: datetime,
        decision_started_at: datetime | None = None,
        ablate_text: bool = False,
    ) -> PreparedPaperLifecycle:
        """Run all non-mutating stages and produce one risk-approved lifecycle plan."""

        current = self._utc(now, "now")
        decision_started = (
            current
            if decision_started_at is None
            else self._utc(decision_started_at, "decision_started_at")
        )
        if decision_started > current:
            raise PaperPipelineRejected("decision_started_at cannot postdate Gate D compilation")
        source_snapshot = compile_strategy_snapshot(capture_configuration, evidence, market)
        strategy_input = compiled_strategy_input(source_snapshot)
        outcome = self._engine.decide(
            strategy_input,
            started_at=decision_started,
            ablate_text=ablate_text,
        )
        if outcome.decision.disposition is not DecisionDisposition.ACCEPTED:
            raise PaperPipelineRejected(
                "bounded decision engine did not produce an accepted direction-only decision"
            )
        gate_d_snapshot = (
            expression_snapshot(sha256_bytes(outcome.decision_bytes))
            if callable(expression_snapshot)
            else expression_snapshot
        )
        if not isinstance(gate_d_snapshot, ExpressionMarketSnapshot):
            raise PaperPipelineRejected("Gate D snapshot factory returned an invalid snapshot")
        expression_policy_sha = promoted_expression_policy_sha256(self.expression_policy)
        status, compiled_or_reason = compile_or_no_package(
            decision=outcome.decision,
            decision_bytes=outcome.decision_bytes,
            snapshot=gate_d_snapshot,
            policy=self.expression_policy,
            policy_sha256=expression_policy_sha,
            gate_d_report_sha256=self.gate_d_report_sha256,
            compiled_at=current,
        )
        if status != COMPILED:
            assert status == NO_PACKAGE
            raise PaperPipelineRejected(
                f"Gate D produced no executable package: {compiled_or_reason}"
            )
        if not isinstance(compiled_or_reason, CompiledExpression):
            raise PaperPipelineRejected("Gate D returned an invalid compiled-expression object")
        compiled = compiled_or_reason
        if compiled.event_id != strategy_input.snapshot.event_id:
            raise PaperPipelineRejected("Gate D event identity did not match the strategy input")
        bridge = PermitBridgeConstants(
            risk_policy_sha256=self.risk_policy_sha256,
            permit_protocol_sha256=PAPER_PIPELINE_PERMIT_PROTOCOL_SHA256,
            execution_protocol_sha256=self.execution_protocol_sha256,
            gate_d_report_sha256=self.gate_d_report_sha256,
        )
        deadline = strategy_input.snapshot.candidate_entry_deadline_at
        expires_at = min(current + self.permit_ttl, deadline)
        permit = build_debit_vertical_permit(
            compiled=compiled,
            strategy_input=strategy_input,
            decision=outcome.decision,
            constants=bridge,
            issued_at=current,
            expires_at=expires_at,
        )
        self.risk_kernel.freeze_candidate(
            event_id=compiled.event_id,
            candidate_id=strategy_input.snapshot.candidate_id,
            compiled=compiled,
            evidence_mode="PAPER_PIPELINE",
            now=current,
        )
        approval = self.risk_kernel.authorize_entry(
            event_id=compiled.event_id,
            underlying=permit.underlying,
            candidate_id=strategy_input.snapshot.candidate_id,
            compiled=compiled,
            permit=permit,
            now=current,
        )
        permit_sha = canonical_permit_sha256(permit)
        expression_sha = compiled_expression_sha256(compiled)
        if approval.permit_id != permit.permit_id or approval.permit_sha256 != permit_sha:
            raise PaperPipelineRejected(
                "risk ledger approval did not bind the exact canonical permit"
            )
        clocks = self.lifecycle_clocks(
            snapshot=source_snapshot,
            expression=compiled,
            permit=permit,
        )
        correlation = CorrelationIdentity(
            event_run_id=permit.event_run_id,
            snapshot_sha256=permit.snapshot_sha256,
            decision_sha256=permit.decision_sha256,
            expression_sha256=expression_sha,
            reservation_id=approval.reservation_id,
            open_permit_id=permit.permit_id,
        )
        return PreparedPaperLifecycle(
            source_snapshot=source_snapshot,
            strategy_input=strategy_input,
            engine_outcome=outcome,
            compiled_expression=compiled,
            expression_sha256=expression_sha,
            permit=permit,
            permit_sha256=permit_sha,
            risk_approval=approval,
            correlation=correlation,
            lifecycle_clocks=clocks,
        )

    def _lifecycle(
        self,
        *,
        prepared: PreparedPaperLifecycle,
        broker: PaperBroker,
        mutation_gate: MutationGate | None,
        clock: Callable[[], datetime],
    ) -> MonitoredPaperLifecycle:
        return MonitoredPaperLifecycle(
            broker=broker,
            ledger=self.risk_kernel.ledger,
            clocks=prepared.lifecycle_clocks,
            correlation=prepared.correlation,
            mutation_gate=mutation_gate or ClosedMutationGate(),
            clock=clock,
            account_id=self.account_id,
            account_class=self.account_class,
            order_class=self.order_class,
        )

    async def open(
        self,
        *,
        prepared: PreparedPaperLifecycle,
        broker: PaperBroker,
        clock: Callable[[], datetime],
        mutation_gate: MutationGate | None = None,
    ) -> ActivePaperLifecycle:
        """Perform the explicitly approved opening; absent a gate this fails closed."""

        lifecycle = self._lifecycle(
            prepared=prepared,
            broker=broker,
            mutation_gate=mutation_gate,
            clock=clock,
        )
        state, order_id = await lifecycle.open(prepared.permit)
        return ActivePaperLifecycle(
            prepared=prepared,
            lifecycle=lifecycle,
            open_order_id=order_id,
            open_state=state,
        )

    async def open_host(
        self,
        *,
        prepared: PreparedPaperLifecycle,
        host_session: PreparedHostMcpSession,
        clock: Callable[[], datetime],
        mutation_gate: MutationGate | None = None,
    ) -> ActivePaperLifecycle:
        """Open only through the preflighted host-managed lifecycle MCP adapter."""

        if prepared.permit.execution_protocol_sha256 != self.execution_protocol_sha256:
            raise PaperPipelineRejected(
                "prepared permit does not bind this application's official execution protocol"
            )
        observation = host_session.observation
        if (
            observation.environment is not HostMcpEnvironment.PAPER
            or observation.adapter != "ALPACA_MCP"
            or observation.adapter_version != ALPACA_MCP_VERSION
            or observation.adapter_commit != ALPACA_MCP_COMMIT
        ):
            raise PaperPipelineRejected(
                "host MCP session does not attest the pinned PAPER protocol"
            )
        return await self.open(
            prepared=prepared,
            broker=host_session.lifecycle_broker(clock=clock),
            clock=clock,
            mutation_gate=mutation_gate,
        )

    async def close(
        self,
        *,
        active: ActivePaperLifecycle,
        close_permit: ClosePermit,
    ) -> tuple[object, str | None]:
        """Perform one explicit monitored close through the same already-bound lifecycle."""

        return await active.lifecycle.close(
            active.prepared.permit,
            close_permit,
            open_order_id=active.open_order_id,
        )


__all__ = [
    "PAPER_PIPELINE_PERMIT_PROTOCOL_SHA256",
    "ActivePaperLifecycle",
    "PaperPipelineRejected",
    "PaperStrategyApplication",
    "PreparedPaperLifecycle",
]
