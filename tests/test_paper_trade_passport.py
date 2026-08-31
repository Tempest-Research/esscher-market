from __future__ import annotations

import json
import socket
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ringdown_market.contracts.execution_policy import (
    ALPACA_MCP_PROTOCOL_SHA256,
    PAPER_PERMIT_POLICY_SHA256,
    RESEARCH_DECISION_PROTOCOL_SHA256,
    paper_event_run_id,
)
from ringdown_market.execution.models import (
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    VerticalType,
    debit_vertical_permit_id,
)
from ringdown_market.execution.paper_demo import (
    PaperPnlClass,
    PaperPnlObservation,
    PaperReceiptBundle,
)
from ringdown_market.passport.chain import (
    PassportChainError,
    TradePassport,
    parse_passport_bytes,
)
from ringdown_market.passport.paper import (
    PaperPassportRejected,
    PaperPassportStage,
    PaperPassportVerdictReason,
    build_paper_trade_passport,
    verify_paper_trade_passport,
)

NOW = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
SOURCE_MANIFEST_SHA256 = "a" * 64
CAPABILITY_SHA256 = "b" * 64
OPEN_REQUEST_SHA256 = "c" * 64
CLOSE_REQUEST_SHA256 = "d" * 64
OPEN_ORDER_SHA256 = "e" * 64
CLOSE_ORDER_SHA256 = "f" * 64


def open_permit() -> DebitVerticalPermit:
    decision_sha256 = "1" * 64
    candidate = DebitVerticalPermit._from_frozen_decision(
        permit_id="UNBOUND",
        event_run_id=paper_event_run_id(decision_sha256),
        policy_sha256=PAPER_PERMIT_POLICY_SHA256,
        snapshot_sha256="2" * 64,
        decision_sha256=decision_sha256,
        evidence_sha256="3" * 64,
        protocol_sha256=RESEARCH_DECISION_PROTOCOL_SHA256,
        execution_protocol_sha256=ALPACA_MCP_PROTOCOL_SHA256,
        issued_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(seconds=30),
        vertical_type=VerticalType.BULL_CALL,
        quantity=1,
        limit_price=Decimal("1.25"),
        legs=(
            OptionLeg(
                symbol="NVDA260918C00180000",
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                strike=Decimal("180"),
                option_type=OptionType.CALL,
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            OptionLeg(
                symbol="NVDA260918C00185000",
                underlying="NVDA",
                expiry=date(2026, 9, 18),
                strike=Decimal("185"),
                option_type=OptionType.CALL,
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )
    return replace(candidate, permit_id=debit_vertical_permit_id(candidate))


def receipt(permit: DebitVerticalPermit, *, outcome: str = "CLOSED_FLAT") -> PaperReceiptBundle:
    is_closed = outcome == "CLOSED_FLAT"
    return PaperReceiptBundle(
        event_run_id=permit.event_run_id,
        open_permit_id=permit.permit_id,
        close_permit_id="rd-close-test",
        capability_sha256=CAPABILITY_SHA256,
        open_request_sha256=OPEN_REQUEST_SHA256,
        close_request_sha256=CLOSE_REQUEST_SHA256 if is_closed else None,
        open_order_sha256=OPEN_ORDER_SHA256,
        close_order_sha256=CLOSE_ORDER_SHA256 if is_closed else None,
        lifecycle_outcome=outcome,
        final_flat_observed_at=NOW,
        pnl=PaperPnlObservation(
            classification=(
                PaperPnlClass.PAPER_REALIZED_PNL if is_closed else PaperPnlClass.ZERO_NO_FILL
            ),
            gross_realized_pnl=Decimal("12.50") if is_closed else Decimal("0"),
            broker_fees=None,
            net_realized_pnl=None,
            open_filled_at=NOW - timedelta(minutes=60) if is_closed else None,
            close_filled_at=NOW if is_closed else None,
        ),
    )


def test_closed_flat_receipt_becomes_a_deterministic_verified_passport() -> None:
    permit = open_permit()
    paper_receipt = receipt(permit)

    first = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=paper_receipt,
    )
    second = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=paper_receipt,
    )

    assert first.payload_bytes() == second.payload_bytes()
    assert first.passport_sha256() == second.passport_sha256()
    verdict = verify_paper_trade_passport(first.payload_bytes())
    assert verdict.valid is True, verdict.details
    assert tuple(entry.stage for entry in first.entries) == (
        PaperPassportStage.SOURCE_EVIDENCE,
        PaperPassportStage.SNAPSHOT,
        PaperPassportStage.DECISION,
        PaperPassportStage.PERMIT,
        PaperPassportStage.OPEN_SUBMISSION,
        PaperPassportStage.CLOSE_SUBMISSION,
        PaperPassportStage.FINAL_FLAT_RECONCILIATION,
        PaperPassportStage.RESULT,
    )
    payload = json.loads(first.payload_bytes())
    assert "open_order_id" not in first.payload_bytes().decode("utf-8")
    assert payload["entries"][-1]["payload"]["classification"] == "PAPER_REALIZED_PNL"


def test_canceled_flat_receipt_omits_close_submission_but_remains_verifiable() -> None:
    permit = open_permit()
    passport = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=receipt(permit, outcome="CANCELED_FLAT"),
    )

    assert tuple(entry.stage for entry in passport.entries) == (
        PaperPassportStage.SOURCE_EVIDENCE,
        PaperPassportStage.SNAPSHOT,
        PaperPassportStage.DECISION,
        PaperPassportStage.PERMIT,
        PaperPassportStage.OPEN_SUBMISSION,
        PaperPassportStage.FINAL_FLAT_RECONCILIATION,
        PaperPassportStage.RESULT,
    )
    assert verify_paper_trade_passport(passport.payload_bytes()).valid is True


def test_identity_mismatch_is_rejected_before_a_passport_is_emitted() -> None:
    permit = open_permit()

    with pytest.raises(PaperPassportRejected, match="event run"):
        build_paper_trade_passport(
            source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            permit=permit,
            receipt=replace(receipt(permit), event_run_id="wrong-event"),
        )


def test_tampered_permit_entry_breaks_the_hash_chain() -> None:
    permit = open_permit()
    passport = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=receipt(permit),
    )
    document = json.loads(passport.payload_bytes())
    document["entries"][3]["payload"]["permit_id"] = "tampered"
    tampered = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

    verdict = verify_paper_trade_passport(tampered)

    assert verdict.valid is False
    assert PaperPassportVerdictReason.LINKAGE_BROKEN in verdict.reasons


def test_duplicate_json_fields_are_rejected_before_chain_verification() -> None:
    permit = open_permit()
    passport = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=receipt(permit),
    )
    encoded = passport.payload_bytes().decode("utf-8")
    duplicate = encoded.replace(
        ',"head_sha256":',
        f',"head_sha256":"{"0" * 64}","head_sha256":',
        1,
    ).encode("utf-8")

    verdict = verify_paper_trade_passport(duplicate)

    assert verdict.valid is False
    assert PaperPassportVerdictReason.PARSE_ERROR in verdict.reasons


def test_generic_chain_parser_rejects_duplicate_envelope_fields() -> None:
    permit = open_permit()
    passport = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=receipt(permit),
    )
    encoded = passport.payload_bytes().decode("utf-8")
    duplicate_prefix = ',"head_sha256":"' + ("0" * 64) + '","head_sha256":'
    duplicate = encoded.replace(',"head_sha256":', duplicate_prefix, 1).encode("utf-8")

    with pytest.raises(PassportChainError, match="duplicate JSON field"):
        parse_passport_bytes(duplicate)


def test_builder_rejects_permit_with_a_nonderivative_event_run_id() -> None:
    original = open_permit()
    forged = replace(original, event_run_id="rd-event-" + ("f" * 32), permit_id="UNBOUND")
    forged = replace(forged, permit_id=debit_vertical_permit_id(forged))

    with pytest.raises(PaperPassportRejected, match="event run"):
        build_paper_trade_passport(
            source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            permit=forged,
            receipt=receipt(forged),
        )


def test_verifier_rejects_a_relinked_nonderivative_event_run_id() -> None:
    permit = open_permit()
    passport = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=receipt(permit),
    )
    forged_event_run_id = "rd-event-" + ("f" * 32)
    relinked = TradePassport()
    for entry in passport.entries:
        payload = dict(entry.payload)
        if entry.stage in {
            PaperPassportStage.PERMIT,
            PaperPassportStage.OPEN_SUBMISSION,
            PaperPassportStage.CLOSE_SUBMISSION,
            PaperPassportStage.FINAL_FLAT_RECONCILIATION,
            PaperPassportStage.RESULT,
        }:
            payload["event_run_id"] = forged_event_run_id
        relinked.append(stage=entry.stage, at=entry.at, payload=payload)

    verdict = verify_paper_trade_passport(relinked.payload_bytes())

    assert verdict.valid is False
    assert PaperPassportVerdictReason.BINDING_INVALID in verdict.reasons


def test_passport_bytes_are_deterministic_and_adapter_stays_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permit = open_permit()
    terminal_receipt = receipt(permit)

    def fail_if_network_is_opened(*args: object, **kwargs: object) -> object:
        raise AssertionError("passport adapter must not open a network socket")

    monkeypatch.setattr(socket, "socket", fail_if_network_is_opened)
    first = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=terminal_receipt,
    )
    second = build_paper_trade_passport(
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        permit=permit,
        receipt=terminal_receipt,
    )

    assert first.payload_bytes() == second.payload_bytes()
    assert verify_paper_trade_passport(first.payload_bytes()).valid is True
