"""Offline causal slice: source bytes to final-flat fake-broker Trade Passport.

This is work-order step 12 of the accepted plan: prove the complete frozen
pipeline end to end with synthetic evidence and a fake broker, producing one
independently readable, hash-linked passport. No network and no broker mutation
are reachable from this path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ringdown_market.data.capture import run_capture_request
from ringdown_market.execution.option_compiler import compile_option_package_from_decision
from ringdown_market.risk.kernel import PackageRiskRequest, evaluate_package
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.risk.policy import RISK_POLICY_SHA256, build_frozen_limits
from ringdown_market.risk.truth import AccountTruth
from ringdown_market.runtime.lifecycle import FakeLifecycleBroker, LifecycleState, LifecycleWorker
from ringdown_market.strategy.decisions import strategy_decision_sha256
from ringdown_market.strategy.engine import DecisionEngine
from ringdown_market.strategy.policy import (
    STRATEGY_POLICY_V1_SHA256,
    parse_frozen_strategy_policy_v1,
)
from ringdown_market.strategy.reasoner import FakeReasoner, ReasonerRoute

from .chain import PassportStage, TradePassport

SLICE_NOW = datetime(2026, 9, 11, 13, 36, 0, tzinfo=UTC)
SLICE_ROUTE = ReasonerRoute(
    route_id="esscher-v1-offline-slice",
    prompt_sha256="c1" * 32,
    output_schema_sha256="d2" * 32,
)


@dataclass(frozen=True, slots=True)
class SliceInputs:
    """Explicit host-supplied inputs for one offline causal slice."""

    policy_bytes: bytes
    capture_request_bytes: bytes
    reasoner_outputs_bytes: bytes
    chain_bytes: bytes
    ledger_path: Path
    risk_cap: Decimal = Decimal("500.00")


class SliceRejected(ValueError):
    """Raised when the frozen pipeline cannot complete the offline slice."""


def _fake_clock(start: datetime):
    state = {"now": start}

    def clock() -> datetime:
        return state["now"]

    def advance(**kwargs: int) -> None:
        state["now"] = state["now"] + timedelta(**kwargs)

    return clock, advance


def build_offline_causal_slice(inputs: SliceInputs) -> TradePassport:
    """Run the frozen stack end to end and return one verified-ready passport."""

    policy = parse_frozen_strategy_policy_v1(inputs.policy_bytes)
    if policy.sha256 != STRATEGY_POLICY_V1_SHA256:
        raise SliceRejected("slice policy bytes do not match the frozen policy identity")

    reasoner_payload = json.loads(inputs.reasoner_outputs_bytes.decode("utf-8"))
    cases = reasoner_payload.get("outputs")
    if not isinstance(cases, list) or not cases:
        raise SliceRejected("reasoner fixture carries no outputs")
    reasoner_output: dict[str, object] = {
        "schema": reasoner_payload.get("reasoner_output_schema"),
        "schema_version": reasoner_payload.get("reasoner_output_schema_version"),
    }
    for key in ("direction", "confidence", "citations", "falsifier"):
        if key in cases[0]:
            reasoner_output[key] = cases[0][key]

    snapshot = run_capture_request(
        inputs.capture_request_bytes,
        policy=policy,
        expected_policy_sha256=STRATEGY_POLICY_V1_SHA256,
    )
    if not snapshot.eligible:
        raise SliceRejected(f"slice snapshot is ineligible: {snapshot.rejection_reasons}")

    passport = TradePassport()
    evidence_payload = snapshot.payload.get("evidence")
    if not isinstance(evidence_payload, list) or not evidence_payload:
        raise SliceRejected("slice snapshot carries no evidence records")
    passport.append(
        stage=PassportStage.SOURCE_EVIDENCE,
        at=SLICE_NOW,
        payload={
            "evidence_ids": [record["evidence_id"] for record in evidence_payload],
            "source_sha256s": [record["content_sha256"] for record in evidence_payload],
            "redistribution": [record["redistribution_note"] for record in evidence_payload],
        },
    )
    passport.append(
        stage=PassportStage.SNAPSHOT,
        at=SLICE_NOW,
        payload={
            "snapshot_sha256": snapshot.sha256,
            "strategy_policy_sha256": policy.sha256,
            "event_id": snapshot.payload["event_id"],
            "schema": snapshot.payload["schema"],
            "eligibility": snapshot.payload["eligibility"],
        },
    )

    engine = DecisionEngine(
        policy=policy, route=SLICE_ROUTE, reasoner=FakeReasoner(reasoner_output)
    )
    decision = engine.generate_decision(snapshot_bytes=snapshot.raw, decided_at=SLICE_NOW)
    if decision.is_abstention:
        raise SliceRejected(
            f"slice decision abstained: {[reason.value for reason in decision.abstention_reasons]}"
        )
    decision_sha = strategy_decision_sha256(decision)
    passport.append(
        stage=PassportStage.DECISION,
        at=SLICE_NOW,
        payload={
            "decision_sha256": decision_sha,
            "decision_bytes_sha256": strategy_decision_sha256(decision),
            "snapshot_sha256": decision.snapshot_sha256,
            "policy_sha256": decision.policy_sha256,
            "route_sha256": decision.route_sha256,
            "reasoner_output_sha256": decision.reasoner_output_sha256,
            "direction": decision.direction.value,
            "reaction_relation": decision.reaction_relation.value,
            "decision_state": decision.decision_state.value,
        },
    )

    package_result = compile_option_package_from_decision(
        decision_direction=decision.direction,
        decision_ticker=decision.ticker,
        chain_bytes=inputs.chain_bytes,
        decision_cutoff=decision.decision_cutoff,
        risk_cap=inputs.risk_cap,
    )
    if package_result.is_no_package or package_result.package is None:
        raise SliceRejected(f"slice package rejected: {package_result.reasons}")
    package = package_result.package
    passport.append(
        stage=PassportStage.PACKAGE,
        at=SLICE_NOW,
        payload={
            "package_sha256": package.sha256,
            "decision_sha256": decision_sha,
            "vertical_type": package.strategy_payload["vertical_type"],
            "debit": str(package.debit),
            "width": str(package.width),
            "expiry": package.expiry.isoformat(),
            "long_symbol": package.strategy_payload["long_leg"]["symbol"],
            "short_symbol": package.strategy_payload["short_leg"]["symbol"],
            "data_class": "INDICATIVE_DATA",
        },
    )

    ledger = RiskLedger(inputs.ledger_path)
    account = AccountTruth(equity=Decimal("100000.00"), observed_at=SLICE_NOW, raw_sha256="f" * 64)
    event_id = str(snapshot.payload["event_id"])
    risk_request = PackageRiskRequest(
        event_id=event_id,
        package_sha256=package.sha256,
        max_loss=package.debit * Decimal(100),
        order_type="LIMIT",
        long_symbols=(str(package.strategy_payload["long_leg"]["symbol"]),),
        short_symbols=(str(package.strategy_payload["short_leg"]["symbol"]),),
        long_quantities=(Decimal(1),),
        short_quantities=(Decimal(1),),
    )
    verdict = evaluate_package(
        risk_request,
        ledger=ledger,
        limits=build_frozen_limits(),
        account=account,
        positions=(),
        open_orders=(),
        now=SLICE_NOW,
    )
    if not verdict.approved or verdict.reservation is None:
        raise SliceRejected(f"slice risk rejection: {verdict.reason}")
    reservation = verdict.reservation
    passport.append(
        stage=PassportStage.RISK_RESERVATION,
        at=SLICE_NOW,
        payload={
            "reservation_id": reservation.reservation_id,
            "package_sha256": package.sha256,
            "risk_policy_sha256": RISK_POLICY_SHA256,
            "permit_binding": reservation.permit_binding,
            "max_loss": str(reservation.max_loss),
        },
    )
    passport.append(
        stage=PassportStage.PERMIT,
        at=SLICE_NOW,
        payload={
            "permit_binding": reservation.permit_binding,
            "decision_sha256": decision_sha,
            "package_sha256": package.sha256,
            "one_use": True,
            "run_mode": "PAPER",
        },
    )

    leg_symbols = frozenset(
        {
            str(package.strategy_payload["long_leg"]["symbol"]),
            str(package.strategy_payload["short_leg"]["symbol"]),
        }
    )
    broker = FakeLifecycleBroker(leg_symbols=leg_symbols)
    clock, advance = _fake_clock(SLICE_NOW)
    worker = LifecycleWorker(ledger=ledger, broker=broker, clock=clock)
    event_run_id = f"rd-slice-{decision_sha[:32]}"
    worker.begin(event_run_id=event_run_id, reservation_id=reservation.reservation_id, now=clock())

    state = worker.tick(event_run_id)
    if state is not LifecycleState.OPEN_SUBMITTED:
        raise SliceRejected(f"slice open submission failed: {state.value}")
    open_client_order_id = f"open-{reservation.reservation_id}"
    passport.append(
        stage=PassportStage.OPEN_SUBMISSION,
        at=clock(),
        payload={
            "client_order_id": open_client_order_id,
            "permit_binding": reservation.permit_binding,
            "submit_once": True,
        },
    )

    state = worker.tick(event_run_id)
    if state not in (LifecycleState.OPEN_SUBMITTED, LifecycleState.OPEN_FILLED):
        raise SliceRejected(f"slice open polling failed: {state.value}")
    state = worker.tick(event_run_id) if state is LifecycleState.OPEN_SUBMITTED else state
    if state is not LifecycleState.OPEN_FILLED:
        raise SliceRejected(f"slice open fill failed: {state.value}")
    record = ledger.lifecycle_state(event_run_id)
    if record is None or record["opened_at"] is None:
        raise SliceRejected("slice lifecycle lost the reconciled opening fill")
    opened_at = record["opened_at"]
    passport.append(
        stage=PassportStage.OPEN_FILL,
        at=opened_at,
        payload={
            "client_order_id": open_client_order_id,
            "filled": True,
            "opened_at": opened_at.isoformat().replace("+00:00", "Z"),
            "fill_proof": "BROKER_READBACK",
        },
    )

    state = worker.tick(event_run_id)
    if state is not LifecycleState.HOLDING:
        raise SliceRejected(f"slice hold failed: {state.value}")
    close_due_at = record["close_due_at"]
    passport.append(
        stage=PassportStage.HOLD,
        at=opened_at,
        payload={
            "opened_at": opened_at.isoformat().replace("+00:00", "Z"),
            "close_due_at": close_due_at.isoformat().replace("+00:00", "Z"),
            "hold_minutes": 60,
            "hold_anchor": "RECONCILED_OPENING_FILL",
            "model_exit": False,
        },
    )

    while clock() < close_due_at:
        advance(minutes=1)
    state = worker.tick(event_run_id)
    if state is not LifecycleState.CLOSE_DUE:
        raise SliceRejected(f"slice close due failed: {state.value}")
    state = worker.tick(event_run_id)
    if state is not LifecycleState.CLOSE_SUBMITTED:
        raise SliceRejected(f"slice close submission failed: {state.value}")
    close_client_order_id = f"close-{reservation.reservation_id}"
    passport.append(
        stage=PassportStage.CLOSE_SUBMISSION,
        at=clock(),
        payload={
            "client_order_id": close_client_order_id,
            "opened_at": opened_at.isoformat().replace("+00:00", "Z"),
            "atomic_multi_leg": True,
        },
    )

    state = worker.tick(event_run_id)
    if state not in (LifecycleState.CLOSE_SUBMITTED, LifecycleState.CLOSED_FLAT):
        raise SliceRejected(f"slice close polling failed: {state.value}")
    state = worker.tick(event_run_id) if state is LifecycleState.CLOSE_SUBMITTED else state
    if state is not LifecycleState.CLOSED_FLAT:
        raise SliceRejected(f"slice close fill failed: {state.value}")
    passport.append(
        stage=PassportStage.CLOSE_FILL,
        at=clock(),
        payload={
            "client_order_id": close_client_order_id,
            "filled": True,
            "opened_at": opened_at.isoformat().replace("+00:00", "Z"),
        },
    )

    final_positions = broker.position_symbols()
    passport.append(
        stage=PassportStage.FINAL_FLAT_RECONCILIATION,
        at=clock(),
        payload={
            "flat_observed": len(final_positions) == 0,
            "position_symbols": sorted(final_positions),
            "authority": "BROKER_POSITION_TRUTH",
        },
    )
    passport.append(
        stage=PassportStage.RESULT,
        at=clock(),
        payload={
            "classification": "PAPER_PNL_UNAVAILABLE",
            "claims": ["PAPER_OPERATIONAL_RESULT", "NOT_ALPHA_EVIDENCE", "INDICATIVE_DATA"],
            "pnl_note": "exact matched fill economics require broker fill readback",
        },
    )
    ledger.close()
    return passport


def slice_payload_json(passport: TradePassport) -> Mapping[str, object]:
    """Return the canonical JSON-ready passport payload."""

    return json.loads(passport.payload_bytes().decode("utf-8"))
