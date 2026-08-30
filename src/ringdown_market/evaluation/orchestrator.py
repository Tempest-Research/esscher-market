"""Read-only shadow orchestration over the exact merged frozen stack."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ringdown_market.execution.option_compiler import compile_option_package_from_decision
from ringdown_market.risk.kernel import PackageRiskRequest, RiskVerdict, evaluate_package
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.risk.policy import RiskLimits
from ringdown_market.risk.truth import AccountTruth, OrderTruth, PositionTruth
from ringdown_market.strategy.decisions import strategy_decision_sha256
from ringdown_market.strategy.engine import DecisionEngine
from ringdown_market.strategy.reasoner import ReasonerRoute

from .shadow import SampleClass, ShadowEventRecord, ShadowLedger, ShadowStage, StageOutcome


@dataclass(frozen=True, slots=True)
class ShadowRunInputs:
    """All injected inputs for one shadow run; nothing reaches a broker."""

    snapshot_bytes: bytes
    chain_bytes: bytes
    decision_cutoff: datetime
    recorded_at: datetime
    account: AccountTruth | None
    positions: tuple[PositionTruth, ...]
    open_orders: tuple[OrderTruth, ...]
    realized_loss_today: Decimal = Decimal("0")


def run_shadow_event(
    *,
    event_id: str,
    sample_class: SampleClass,
    development_event_ids: frozenset[str],
    inputs: ShadowRunInputs,
    engine: DecisionEngine,
    route: ReasonerRoute,
    chain_underlying_ticker: str,
    risk_cap: Decimal,
    ledger: RiskLedger,
    limits: RiskLimits,
    shadow_ledger: ShadowLedger,
) -> ShadowEventRecord:
    """Run one event through the frozen pipeline; every stage outcome is retained."""

    if sample_class is not SampleClass.DEVELOPMENT and event_id in development_event_ids:
        raise ValueError("development events can never be recorded as confirmation/prospective")

    stages: list[StageOutcome] = []
    snapshot_sha = hashlib.sha256(inputs.snapshot_bytes).hexdigest()
    stages.append(
        StageOutcome(
            stage=ShadowStage.SNAPSHOT,
            disposition="PASS",
            reasons=(),
            identity_sha256=snapshot_sha,
        )
    )

    decision = engine.generate_decision(
        snapshot_bytes=inputs.snapshot_bytes, decided_at=inputs.recorded_at
    )
    if decision.is_abstention:
        stages.append(
            StageOutcome(
                stage=ShadowStage.DECISION,
                disposition="ABSTAIN",
                reasons=tuple(reason.value for reason in decision.abstention_reasons),
                identity_sha256=None,
            )
        )
        record = ShadowEventRecord(
            event_id=event_id,
            sample_class=sample_class,
            snapshot_sha256=snapshot_sha,
            recorded_at=inputs.recorded_at,
            stages=tuple(stages),
        )
        shadow_ledger.record(record)
        return record

    decision_bytes_identity = strategy_decision_sha256(decision)
    stages.append(
        StageOutcome(
            stage=ShadowStage.DECISION,
            disposition="PASS",
            reasons=(),
            identity_sha256=decision_bytes_identity,
        )
    )

    package_result = compile_option_package_from_decision(
        decision_direction=decision.direction,
        decision_ticker=chain_underlying_ticker,
        chain_bytes=inputs.chain_bytes,
        decision_cutoff=inputs.decision_cutoff,
        risk_cap=risk_cap,
    )
    if package_result.is_no_package:
        stages.append(
            StageOutcome(
                stage=ShadowStage.PACKAGE,
                disposition="NO_PACKAGE",
                reasons=package_result.reasons,
                identity_sha256=None,
            )
        )
        record = ShadowEventRecord(
            event_id=event_id,
            sample_class=sample_class,
            snapshot_sha256=snapshot_sha,
            recorded_at=inputs.recorded_at,
            stages=tuple(stages),
        )
        shadow_ledger.record(record)
        return record

    package = package_result.package
    stages.append(
        StageOutcome(
            stage=ShadowStage.PACKAGE,
            disposition="PASS",
            reasons=(),
            identity_sha256=package.sha256,
        )
    )

    payload = package.strategy_payload
    long_leg = payload["long_leg"]
    short_leg = payload["short_leg"]
    risk_request = PackageRiskRequest(
        event_id=event_id,
        package_sha256=package.sha256,
        max_loss=package.debit * Decimal(100),
        order_type="LIMIT",
        long_symbols=(str(long_leg["symbol"]),),
        short_symbols=(str(short_leg["symbol"]),),
        long_quantities=(Decimal(payload["quantity"]),),
        short_quantities=(Decimal(payload["quantity"]),),
    )
    verdict: RiskVerdict = evaluate_package(
        risk_request,
        ledger=ledger,
        limits=limits,
        account=inputs.account,
        positions=inputs.positions,
        open_orders=inputs.open_orders,
        now=inputs.recorded_at,
        realized_loss_today=inputs.realized_loss_today,
    )
    if not verdict.approved:
        stages.append(
            StageOutcome(
                stage=ShadowStage.RISK,
                disposition="REJECTED",
                reasons=(verdict.reason.value,) if verdict.reason else (),
                identity_sha256=None,
            )
        )
        record = ShadowEventRecord(
            event_id=event_id,
            sample_class=sample_class,
            snapshot_sha256=snapshot_sha,
            recorded_at=inputs.recorded_at,
            stages=tuple(stages),
        )
        shadow_ledger.record(record)
        return record

    stages.append(
        StageOutcome(
            stage=ShadowStage.RISK,
            disposition="PASS",
            reasons=(),
            identity_sha256=verdict.reservation.permit_binding if verdict.reservation else None,
        )
    )
    stages.append(
        StageOutcome(
            stage=ShadowStage.EXIT,
            disposition="SHADOW_HOLD_SCHEDULED",
            reasons=("BROKER_FREE_SHADOW",),
            identity_sha256=None,
        )
    )
    record = ShadowEventRecord(
        event_id=event_id,
        sample_class=sample_class,
        snapshot_sha256=snapshot_sha,
        recorded_at=inputs.recorded_at,
        stages=tuple(stages),
    )
    shadow_ledger.record(record)
    return record


__all__ = [
    "ShadowRunInputs",
    "run_shadow_event",
]
