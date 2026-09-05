"""Strict Strategy V2 direction-receipt seam tests."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from esscher.contracts.reasoner_route import load_approved_reasoner_route_v2
from esscher.risk.ledger import RiskLedger
from esscher.strategy import (
    DIRECTION_ONLY_UNCONFIRMED_AUTHORITY,
    StrategyV2DirectionDecision,
    StrategyV2DirectionDecisionRejected,
    StrategyV2DirectionState,
    parse_strategy_v2_direction_decision,
    strategy_v2_direction_decision_bytes,
    strategy_v2_direction_decision_sha256,
    validate_strategy_v2_direction_decision,
)
from esscher.strategy.host_route import KimiTransportResult, build_kimi_k3_v2_request
from esscher.strategy.models import Direction, ExchangeStatus, StrategyDecision
from test_strategy_v2_route import _context


def _raw_response(*, direction: str, evidence_id: str) -> bytes:
    return json.dumps(
        {
            "decision": direction,
            "evidence_ids": [evidence_id],
            "contradictions": [],
            "unknowns": [],
            "strongest_falsifier": {
                "evidence_id": evidence_id,
                "summary": "This same source would falsify the proposed direction.",
            },
            "summary": "A direction-only proposal with no sizing or order authority.",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate(tmp_path, *, direction: str = "UP") -> StrategyV2DirectionDecision:
    route = load_approved_reasoner_route_v2()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        context = _context(ledger, route)
        request = build_kimi_k3_v2_request(route, context, ledger=ledger)
        started_at = context.snapshot.evidence_cutoff_at
        responded_at = started_at + timedelta(seconds=1)
        return validate_strategy_v2_direction_decision(
            route=route,
            context=context,
            request=request,
            transport=KimiTransportResult(
                status=ExchangeStatus.COMPLETED,
                error_code=None,
                raw_response_bytes=_raw_response(
                    direction=direction,
                    evidence_id=context.snapshot.evidence_refs[0].evidence_id,
                ),
            ),
            ledger=ledger,
            started_at=started_at,
            responded_at=responded_at,
            deadline_at=context.snapshot.decision_cutoff_at,
            producer_identity="test-v2-direction-receipt",
            producer_build_sha256="b" * 64,
        )
    finally:
        ledger.close()


def test_v2_direction_receipt_proposes_exact_up_without_v1_authority(tmp_path) -> None:
    receipt = _validate(tmp_path)

    assert receipt.authority == DIRECTION_ONLY_UNCONFIRMED_AUTHORITY
    assert receipt.state is StrategyV2DirectionState.PROPOSED_UNCONFIRMED
    assert receipt.direction is Direction.UP
    assert receipt.reasoner_direction is Direction.UP
    assert receipt.reason_codes == ()
    assert not isinstance(receipt, StrategyDecision)
    receipt_bytes = strategy_v2_direction_decision_bytes(receipt)
    assert parse_strategy_v2_direction_decision(receipt_bytes) == receipt
    assert strategy_v2_direction_decision_sha256(receipt) == strategy_v2_direction_decision_sha256(
        receipt
    )


def test_v2_direction_receipt_parser_rejects_forged_raw_hash(tmp_path) -> None:
    receipt = _validate(tmp_path)
    payload = json.loads(strategy_v2_direction_decision_bytes(receipt))
    payload["raw_response_sha256"] = "c" * 64
    forged = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    with pytest.raises(StrategyV2DirectionDecisionRejected, match="raw response hash"):
        parse_strategy_v2_direction_decision(forged)


def test_v2_direction_receipt_parser_rejects_forged_semantic_hash(tmp_path) -> None:
    receipt = _validate(tmp_path)
    payload = json.loads(strategy_v2_direction_decision_bytes(receipt))
    payload["reasoner_decision_sha256"] = "d" * 64
    forged = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    with pytest.raises(StrategyV2DirectionDecisionRejected, match="semantic response hash"):
        parse_strategy_v2_direction_decision(forged)


def test_v2_direction_receipt_rejects_response_after_context_cutoff(tmp_path) -> None:
    route = load_approved_reasoner_route_v2()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        context = _context(ledger, route)
        request = build_kimi_k3_v2_request(route, context, ledger=ledger)
        receipt = validate_strategy_v2_direction_decision(
            route=route,
            context=context,
            request=request,
            transport=KimiTransportResult(
                status=ExchangeStatus.COMPLETED,
                error_code=None,
                raw_response_bytes=_raw_response(
                    direction="UP",
                    evidence_id=context.snapshot.evidence_refs[0].evidence_id,
                ),
            ),
            ledger=ledger,
            started_at=context.snapshot.evidence_cutoff_at,
            responded_at=context.snapshot.decision_cutoff_at + timedelta(seconds=1),
            deadline_at=context.snapshot.decision_cutoff_at + timedelta(seconds=2),
            producer_identity="test-v2-direction-receipt",
            producer_build_sha256="b" * 64,
        )
    finally:
        ledger.close()

    assert receipt.state is StrategyV2DirectionState.REJECTED
    assert "LATE_RESPONSE" in receipt.reason_codes


@pytest.mark.parametrize(
    ("status", "error_code", "raw_bytes", "expected_reason"),
    [
        (ExchangeStatus.TIMEOUT, None, None, "REASONER_TIMEOUT"),
        (ExchangeStatus.PROVIDER_ERROR, None, None, "REASONER_PROVIDER_ERROR"),
        (ExchangeStatus.CANCELED, None, None, "REASONER_CANCELED"),
        (ExchangeStatus.COMPLETED, "upstream_5xx", None, "REASONER_TRANSPORT_ERROR"),
        (ExchangeStatus.COMPLETED, None, None, "REASONER_RESPONSE_MISSING"),
        (
            ExchangeStatus.COMPLETED,
            None,
            b'{"decision": 1}',
            "REASONER_SCHEMA_INVALID",
        ),
    ],
)
def test_v2_direction_receipt_fails_closed_for_transport_and_missing_output(
    tmp_path,
    status,
    error_code,
    raw_bytes,
    expected_reason,
) -> None:
    route = load_approved_reasoner_route_v2()
    ledger = RiskLedger(tmp_path / "ledger.sqlite3")
    try:
        context = _context(ledger, route)
        request = build_kimi_k3_v2_request(route, context, ledger=ledger)
        started_at = context.snapshot.evidence_cutoff_at
        receipt = validate_strategy_v2_direction_decision(
            route=route,
            context=context,
            request=request,
            transport=KimiTransportResult(
                status=status,
                error_code=error_code,
                raw_response_bytes=raw_bytes,
            ),
            ledger=ledger,
            started_at=started_at,
            responded_at=started_at + timedelta(seconds=1),
            deadline_at=context.snapshot.decision_cutoff_at,
            producer_identity="test-v2-direction-receipt",
            producer_build_sha256="b" * 64,
        )
    finally:
        ledger.close()

    assert receipt.state is StrategyV2DirectionState.REJECTED
    assert receipt.direction is Direction.UNCERTAIN
    assert expected_reason in receipt.reason_codes
