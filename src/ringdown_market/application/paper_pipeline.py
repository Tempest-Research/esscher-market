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
from ringdown_market.contracts.execution_policy import ALPACA_MCP_PROTOCOL_SHA256
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
from ringdown_market.execution.host_mcp import (
    HostMcpConfigurationError,
    PreparedHostMcpSession,
)
from ringdown_market.execution.models import ClosePermit, DebitVerticalPermit
from ringdown_market.lifecycle import (
    MULTI_LEG_ORDER_CLASS,
    PAPER_ACCOUNT_CLASS,
    ClosedMutationGate,
    CorrelationIdentity,
    LifecycleClocks,
    MonitoredPaperLifecycle,
    correlation_sha256,
    lifecycle_clocks_sha256,
)
from ringdown_market.lifecycle.broker import PaperBroker
from ringdown_market.lifecycle.worker import MutationGate
from ringdown_market.risk import RiskApproval, RiskKernel, RiskLedger
from ringdown_market.sourcedata import (
    CaptureConfiguration,
    CompiledSnapshot,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.evidence import evidence_packet_sha256
from ringdown_market.sourcedata.interfaces import EvidenceSource, MarketDataSource
from ringdown_market.strategy import DecisionDisposition, StrategyInput
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes
from ringdown_market.strategy.engine import (
    BoundedDecisionEngine,
    EngineOutcome,
    decision_trace_payload,
)
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
    trace_sha256: str
    application_identity_sha256: str
    compiled_expression: CompiledExpression
    expression_sha256: str
    permit: DebitVerticalPermit
    permit_sha256: str
    risk_approval: RiskApproval
    correlation: CorrelationIdentity
    lifecycle_clocks: LifecycleClocks
    lifecycle_clocks_sha256: str

    def __post_init__(self) -> None:
        if self.engine_outcome.decision.disposition is not DecisionDisposition.ACCEPTED:
            raise PaperPipelineRejected("only an accepted decision can prepare a paper lifecycle")
        if self.trace_sha256 != sha256_bytes(self.engine_outcome.trace_bytes):
            raise PaperPipelineRejected("prepared decision trace hash is not canonical")
        if (
            not isinstance(self.application_identity_sha256, str)
            or len(self.application_identity_sha256) != 64
        ):
            raise PaperPipelineRejected("prepared application identity is not a SHA-256 digest")
        if self.permit_sha256 != canonical_permit_sha256(self.permit):
            raise PaperPipelineRejected("prepared permit hash is not canonical")
        if self.lifecycle_clocks_sha256 != lifecycle_clocks_sha256(self.lifecycle_clocks):
            raise PaperPipelineRejected("prepared lifecycle clocks hash is not canonical")
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


@dataclass(frozen=True, slots=True)
class CloseCriticalBinding:
    """The immutable execution graph that may reduce an already-open PAPER position."""

    open_permit: DebitVerticalPermit
    permit_sha256: str
    lifecycle_clocks: LifecycleClocks
    lifecycle_clocks_sha256: str
    correlation: CorrelationIdentity
    correlation_sha256: str
    application_identity_sha256: str
    ledger: RiskLedger
    broker: PaperBroker
    account_id: str
    account_class: str
    order_class: str
    open_order_id: str
    lifecycle: MonitoredPaperLifecycle


@dataclass(frozen=True, slots=True)
class ActivePaperLifecycle:
    """One opened lifecycle plus its independent risk-reducing close binding."""

    prepared: PreparedPaperLifecycle
    lifecycle: MonitoredPaperLifecycle
    open_order_id: str
    open_state: object
    close_binding: CloseCriticalBinding


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
        if self.order_class != MULTI_LEG_ORDER_CLASS:
            raise ValueError("PaperStrategyApplication accepts only the MULTI_LEG order class")
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

    @property
    def application_identity_sha256(self) -> str:
        """Hash every application-local term that must not be replayed across accounts."""

        permit_ttl_microseconds = (
            self.permit_ttl.days * 86_400_000_000
            + self.permit_ttl.seconds * 1_000_000
            + self.permit_ttl.microseconds
        )
        return sha256_bytes(
            canonical_json_bytes(
                {
                    "schema": "esscher.paper_application_identity",
                    "schema_version": 1,
                    "account_id": self.account_id,
                    "account_class": self.account_class,
                    "order_class": self.order_class,
                    "risk_policy_sha256": self.risk_policy_sha256,
                    "expression_policy_sha256": promoted_expression_policy_sha256(
                        self.expression_policy
                    ),
                    "gate_d_report_sha256": self.gate_d_report_sha256,
                    "permit_protocol_sha256": PAPER_PIPELINE_PERMIT_PROTOCOL_SHA256,
                    "execution_protocol_sha256": self.execution_protocol_sha256,
                    "permit_ttl_microseconds": permit_ttl_microseconds,
                }
            )
        )

    def _bridge_constants(self) -> PermitBridgeConstants:
        return PermitBridgeConstants(
            risk_policy_sha256=self.risk_policy_sha256,
            permit_protocol_sha256=PAPER_PIPELINE_PERMIT_PROTOCOL_SHA256,
            execution_protocol_sha256=self.execution_protocol_sha256,
            gate_d_report_sha256=self.gate_d_report_sha256,
        )

    @staticmethod
    def _utc(value: datetime, path: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PaperPipelineRejected(f"{path} must be timezone-aware")
        return value.astimezone(UTC)

    def _validate_prepared(self, prepared: PreparedPaperLifecycle) -> None:
        """Rebuild every opening-critical edge before any broker-capable object exists."""

        if not isinstance(prepared, PreparedPaperLifecycle):
            raise PaperPipelineRejected("prepared lifecycle has an invalid type")
        if self.risk_kernel.policy_sha256 != self.risk_policy_sha256:
            raise PaperPipelineRejected("application risk policy no longer matches its risk kernel")
        if prepared.application_identity_sha256 != self.application_identity_sha256:
            raise PaperPipelineRejected(
                "prepared.application_identity_sha256 does not bind this application configuration"
            )

        source_snapshot = prepared.source_snapshot
        try:
            packet_sha256 = evidence_packet_sha256(source_snapshot.evidence_packet)
        except Exception as error:
            raise PaperPipelineRejected(
                "prepared.source_snapshot.evidence_packet cannot be canonically rebuilt"
            ) from error
        if packet_sha256 != source_snapshot.evidence_packet.packet_sha256:
            raise PaperPipelineRejected(
                "prepared.source_snapshot.evidence_packet.packet_sha256 does not match its receipts"
            )
        if source_snapshot.source_receipts != source_snapshot.evidence_packet.receipts:
            raise PaperPipelineRejected(
                "prepared.source_snapshot.source_receipts do not match the evidence packet"
            )
        if source_snapshot.snapshot.evidence_packet_sha256 != packet_sha256:
            raise PaperPipelineRejected(
                "prepared.source_snapshot.snapshot does not bind the evidence packet"
            )
        try:
            rebuilt_input = compiled_strategy_input(source_snapshot)
        except Exception as error:
            raise PaperPipelineRejected(
                "prepared.source_snapshot cannot be canonically rejoined"
            ) from error
        if (
            source_snapshot.snapshot != rebuilt_input.snapshot
            or source_snapshot.feature_receipt != rebuilt_input.feature_receipt
            or rebuilt_input != prepared.strategy_input
        ):
            raise PaperPipelineRejected(
                "prepared.strategy_input drifts from the canonical source artifacts"
            )

        outcome = prepared.engine_outcome
        if outcome.decision.disposition is not DecisionDisposition.ACCEPTED:
            raise PaperPipelineRejected("prepared decision is no longer accepted")
        if outcome.ablate_text:
            raise PaperPipelineRejected(
                "prepared.engine_outcome.ablate_text must be False for executable PAPER entry"
            )
        if not outcome.route_invoked:
            raise PaperPipelineRejected(
                "prepared.engine_outcome.route_invoked must be True for accepted PAPER entry"
            )
        try:
            canonical_trace = decision_trace_payload(
                strategy_input=prepared.strategy_input,
                decision=outcome.decision,
                exchange=outcome.exchange,
                route_invoked=outcome.route_invoked,
                ablate_text=outcome.ablate_text,
            )
            trace_sha256 = sha256_bytes(canonical_json_bytes(canonical_trace))
        except Exception as error:
            raise PaperPipelineRejected(
                "prepared.engine_outcome.trace cannot be canonically rebuilt"
            ) from error
        if prepared.trace_sha256 != trace_sha256 or outcome.trace_bytes != canonical_json_bytes(
            canonical_trace
        ):
            raise PaperPipelineRejected(
                "prepared.engine_outcome.trace is not the canonical engine output"
            )

        try:
            expression_policy_sha256 = promoted_expression_policy_sha256(self.expression_policy)
            expression_sha256 = compiled_expression_sha256(prepared.compiled_expression)
        except Exception as error:
            raise PaperPipelineRejected(
                "prepared.compiled_expression cannot be canonically rebuilt"
            ) from error
        if prepared.compiled_expression.policy_sha256 != expression_policy_sha256:
            raise PaperPipelineRejected(
                "prepared.compiled_expression.policy_sha256 is not the promoted expression policy"
            )
        if prepared.expression_sha256 != expression_sha256:
            raise PaperPipelineRejected("prepared.expression_sha256 is not canonical")

        try:
            expected_permit = build_debit_vertical_permit(
                compiled=prepared.compiled_expression,
                strategy_input=prepared.strategy_input,
                decision=outcome.decision,
                constants=self._bridge_constants(),
                issued_at=prepared.permit.issued_at,
                expires_at=prepared.permit.expires_at,
            )
        except Exception as error:
            raise PaperPipelineRejected("prepared.permit cannot be canonically rebuilt") from error
        if expected_permit != prepared.permit:
            raise PaperPipelineRejected(
                "prepared.permit does not equal its canonical expression-to-permit binding"
            )
        if prepared.permit_sha256 != canonical_permit_sha256(prepared.permit):
            raise PaperPipelineRejected("prepared.permit_sha256 is not canonical")
        if (
            prepared.risk_approval.permit_id != prepared.permit.permit_id
            or prepared.risk_approval.permit_sha256 != prepared.permit_sha256
            or prepared.correlation.event_run_id != prepared.permit.event_run_id
            or prepared.correlation.snapshot_sha256 != prepared.permit.snapshot_sha256
            or prepared.correlation.decision_sha256 != prepared.permit.decision_sha256
            or prepared.correlation.expression_sha256 != prepared.expression_sha256
            or prepared.correlation.reservation_id != prepared.risk_approval.reservation_id
            or prepared.correlation.open_permit_id != prepared.permit.permit_id
            or prepared.correlation.close_permit_id is not None
        ):
            raise PaperPipelineRejected("prepared identity graph is not causally joined")

        try:
            expected_clocks = self.lifecycle_clocks(
                snapshot=source_snapshot,
                expression=prepared.compiled_expression,
                permit=expected_permit,
            )
        except Exception as error:
            raise PaperPipelineRejected(
                "prepared.lifecycle_clocks cannot be canonically rebuilt"
            ) from error
        expected_clocks_sha256 = lifecycle_clocks_sha256(expected_clocks)
        if (
            prepared.lifecycle_clocks != expected_clocks
            or prepared.lifecycle_clocks_sha256 != expected_clocks_sha256
            or lifecycle_clocks_sha256(prepared.lifecycle_clocks) != expected_clocks_sha256
        ):
            raise PaperPipelineRejected(
                "prepared.lifecycle_clocks are not canonical lifecycle clocks for this permit"
            )

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
        if outcome.ablate_text or not outcome.route_invoked:
            raise PaperPipelineRejected(
                "executable PAPER entry requires a non-ablated decision from an invoked route"
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
        bridge = self._bridge_constants()
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
            trace_sha256=sha256_bytes(outcome.trace_bytes),
            application_identity_sha256=self.application_identity_sha256,
            compiled_expression=compiled,
            expression_sha256=expression_sha,
            permit=permit,
            permit_sha256=permit_sha,
            risk_approval=approval,
            correlation=correlation,
            lifecycle_clocks=clocks,
            lifecycle_clocks_sha256=lifecycle_clocks_sha256(clocks),
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

        self._validate_prepared(prepared)
        lifecycle = self._lifecycle(
            prepared=prepared,
            broker=broker,
            mutation_gate=mutation_gate,
            clock=clock,
        )
        state, order_id = await lifecycle.open(prepared.permit)
        close_binding = CloseCriticalBinding(
            open_permit=prepared.permit,
            permit_sha256=prepared.permit_sha256,
            lifecycle_clocks=prepared.lifecycle_clocks,
            lifecycle_clocks_sha256=prepared.lifecycle_clocks_sha256,
            correlation=prepared.correlation,
            correlation_sha256=correlation_sha256(prepared.correlation),
            application_identity_sha256=prepared.application_identity_sha256,
            ledger=self.risk_kernel.ledger,
            broker=broker,
            account_id=self.account_id,
            account_class=self.account_class,
            order_class=self.order_class,
            open_order_id=order_id,
            lifecycle=lifecycle,
        )
        return ActivePaperLifecycle(
            prepared=prepared,
            lifecycle=lifecycle,
            open_order_id=order_id,
            open_state=state,
            close_binding=close_binding,
        )

    async def open_host(
        self,
        *,
        prepared: PreparedPaperLifecycle,
        host_session: PreparedHostMcpSession,
        clock: Callable[[], datetime],
        mutation_gate: MutationGate | None = None,
    ) -> ActivePaperLifecycle:
        """Open only through one factory-issued, fully attested MCP capability."""

        if prepared.permit.execution_protocol_sha256 != self.execution_protocol_sha256:
            raise PaperPipelineRejected(
                "prepared permit does not bind this application's official execution protocol"
            )
        if not isinstance(host_session, PreparedHostMcpSession):
            raise PaperPipelineRejected("host MCP capability must be factory-created")
        try:
            broker = host_session.lifecycle_broker(clock=clock)
        except HostMcpConfigurationError as error:
            raise PaperPipelineRejected(
                "host MCP capability must be factory-created and retain a complete PAPER "
                "preflight attestation"
            ) from error
        return await self.open(
            prepared=prepared,
            broker=broker,
            clock=clock,
            mutation_gate=mutation_gate,
        )

    def _validate_active_for_close(self, active: ActivePaperLifecycle) -> CloseCriticalBinding:
        """Allow only the original bound graph to reduce an already-open position.

        This deliberately does not rebuild ``active.prepared`` or consult mutable
        receiving-application configuration.  Research provenance must be
        quarantined separately from a risk-reducing close.
        """

        if not isinstance(active, ActivePaperLifecycle):
            raise PaperPipelineRejected("active lifecycle has an invalid type")
        binding = active.close_binding
        if active.lifecycle is not binding.lifecycle:
            raise PaperPipelineRejected(
                "active.close_binding.lifecycle: active lifecycle was substituted after open"
            )
        if active.open_order_id != binding.open_order_id:
            raise PaperPipelineRejected(
                "active.close_binding.open_order_id does not match the opened active lifecycle"
            )
        if binding.permit_sha256 != canonical_permit_sha256(binding.open_permit):
            raise PaperPipelineRejected("active.close_binding.permit_sha256 is not canonical")
        if binding.lifecycle_clocks_sha256 != lifecycle_clocks_sha256(binding.lifecycle_clocks):
            raise PaperPipelineRejected(
                "active.close_binding.lifecycle_clocks_sha256 is not canonical"
            )
        if (
            not isinstance(binding.application_identity_sha256, str)
            or len(binding.application_identity_sha256) != 64
        ):
            raise PaperPipelineRejected(
                "active.close_binding.application_identity_sha256 is invalid"
            )
        if (
            binding.correlation_sha256 != correlation_sha256(binding.correlation)
            or binding.open_permit.permit_id != binding.correlation.open_permit_id
            or binding.open_permit.event_run_id != binding.correlation.event_run_id
            or binding.open_permit.snapshot_sha256 != binding.correlation.snapshot_sha256
            or binding.open_permit.decision_sha256 != binding.correlation.decision_sha256
            or binding.correlation.close_permit_id is not None
        ):
            raise PaperPipelineRejected(
                "active.close_binding permit and correlation are not causally joined"
            )

        lifecycle = binding.lifecycle
        if lifecycle.broker is not binding.broker:
            raise PaperPipelineRejected(
                "active.close_binding.lifecycle broker does not match the opened active lifecycle"
            )
        if lifecycle.ledger is not binding.ledger:
            raise PaperPipelineRejected(
                "active.close_binding.lifecycle ledger does not match the opened active lifecycle"
            )
        if (
            lifecycle.clocks != binding.lifecycle_clocks
            or lifecycle_clocks_sha256(lifecycle.clocks) != binding.lifecycle_clocks_sha256
        ):
            raise PaperPipelineRejected(
                "active.close_binding.lifecycle clocks do not match the opened active lifecycle"
            )
        if lifecycle.correlation != binding.correlation:
            raise PaperPipelineRejected(
                "active.close_binding.lifecycle correlation differs from active lifecycle"
            )
        if (
            lifecycle.account_id != binding.account_id
            or lifecycle.account_class != binding.account_class
            or lifecycle.order_class != binding.order_class
        ):
            raise PaperPipelineRejected(
                "active.close_binding.lifecycle account differs from the opened active lifecycle"
            )
        return binding

    async def close(
        self,
        *,
        active: ActivePaperLifecycle,
        close_permit: ClosePermit,
    ) -> tuple[object, str | None]:
        """Perform one explicit monitored close through the same already-bound lifecycle."""

        binding = self._validate_active_for_close(active)
        return await binding.lifecycle.close(
            binding.open_permit,
            close_permit,
            open_order_id=binding.open_order_id,
        )


__all__ = [
    "PAPER_PIPELINE_PERMIT_PROTOCOL_SHA256",
    "ActivePaperLifecycle",
    "CloseCriticalBinding",
    "PaperPipelineRejected",
    "PaperStrategyApplication",
    "PreparedPaperLifecycle",
]
