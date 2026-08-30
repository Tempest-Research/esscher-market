from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from ringdown_market.passport import (
    FULL_TRACE_STAGES,
    GENESIS_PREV_SHA256,
    SliceInputs,
    TradePassport,
    VerdictReason,
    build_offline_causal_slice,
    compute_entry_sha256,
    parse_passport_bytes,
    verify_passport,
)
from ringdown_market.passport.chain import PassportChainError, PassportStage
from test_option_compiler import NEAR_EXPIRY, contract
from test_option_compiler import chain_bytes as build_chain

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "configs" / "strategy_v1.json"
CAPTURE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "strategy_v1_synthetic_capture_request.json"
REASONER_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "strategy_v1_synthetic_reasoner_outputs.json"


def option_chain_bytes() -> bytes:
    return build_chain(
        [
            contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), bid="1.10", ask="1.30"),
            contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
        ]
    )


def slice_inputs(tmp_path: Path) -> SliceInputs:
    return SliceInputs(
        policy_bytes=POLICY_PATH.read_bytes(),
        capture_request_bytes=CAPTURE_FIXTURE.read_bytes(),
        reasoner_outputs_bytes=REASONER_FIXTURE.read_bytes(),
        chain_bytes=option_chain_bytes(),
        ledger_path=tmp_path / "slice.db",
    )


def build_passport(tmp_path: Path) -> TradePassport:
    return build_offline_causal_slice(slice_inputs(tmp_path))


def test_offline_causal_slice_builds_a_verified_passport(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    verdict = verify_passport(passport.payload_bytes())
    assert verdict.valid is True, verdict.details
    assert verdict.reasons == ()
    assert len(passport.entries) == len(FULL_TRACE_STAGES)
    assert tuple(entry.stage for entry in passport.entries) == FULL_TRACE_STAGES
    result_payload = passport.entries[-1].payload
    assert result_payload["classification"] == "PAPER_PNL_UNAVAILABLE"
    assert "PAPER_OPERATIONAL_RESULT" in result_payload["claims"]
    assert "NOT_ALPHA_EVIDENCE" in result_payload["claims"]


def test_slice_is_byte_deterministic(tmp_path: Path) -> None:
    first = build_offline_causal_slice(slice_inputs(tmp_path))
    second_inputs = slice_inputs(tmp_path)
    second = build_offline_causal_slice(
        SliceInputs(
            policy_bytes=second_inputs.policy_bytes,
            capture_request_bytes=second_inputs.capture_request_bytes,
            reasoner_outputs_bytes=second_inputs.reasoner_outputs_bytes,
            chain_bytes=second_inputs.chain_bytes,
            ledger_path=tmp_path / "slice-second.db",
        )
    )
    assert first.payload_bytes() == second.payload_bytes()
    assert first.passport_sha256() == second.passport_sha256()


def test_every_stage_binds_its_parent_hashes(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    payloads = {entry.stage: entry.payload for entry in passport.entries}
    snapshot_sha = payloads[PassportStage.SNAPSHOT]["snapshot_sha256"]
    decision_sha = payloads[PassportStage.DECISION]["decision_sha256"]
    package_sha = payloads[PassportStage.PACKAGE]["package_sha256"]
    permit_binding = payloads[PassportStage.RISK_RESERVATION]["permit_binding"]

    assert payloads[PassportStage.DECISION]["snapshot_sha256"] == snapshot_sha
    assert payloads[PassportStage.PACKAGE]["decision_sha256"] == decision_sha
    assert payloads[PassportStage.RISK_RESERVATION]["package_sha256"] == package_sha
    assert payloads[PassportStage.PERMIT]["permit_binding"] == permit_binding
    assert payloads[PassportStage.PERMIT]["decision_sha256"] == decision_sha
    assert payloads[PassportStage.OPEN_SUBMISSION]["permit_binding"] == permit_binding
    assert (
        payloads[PassportStage.OPEN_FILL]["client_order_id"]
        == (payloads[PassportStage.OPEN_SUBMISSION]["client_order_id"])
    )
    assert (
        payloads[PassportStage.CLOSE_FILL]["client_order_id"]
        == (payloads[PassportStage.CLOSE_SUBMISSION]["client_order_id"])
    )
    assert payloads[PassportStage.FINAL_FLAT_RECONCILIATION]["flat_observed"] is True
    assert payloads[PassportStage.FINAL_FLAT_RECONCILIATION]["position_symbols"] == []


def test_chain_linkage_and_genesis(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    entries = parse_passport_bytes(passport.payload_bytes())
    assert entries[0].prev_sha256 == GENESIS_PREV_SHA256
    for index in range(1, len(entries)):
        previous = entries[index - 1]
        expected = compute_entry_sha256(
            sequence=previous.sequence,
            stage=previous.stage,
            at=previous.at,
            payload=previous.payload,
            prev_sha256=previous.prev_sha256,
        )
        assert entries[index].prev_sha256 == expected


def _tamper(passport: TradePassport, *, entry_index: int, key: str, value: object) -> bytes:
    document = json.loads(passport.payload_bytes().decode("utf-8"))
    document["entries"][entry_index]["payload"][key] = value
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def test_tampered_middle_entry_breaks_linkage(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    tampered = _tamper(passport, entry_index=2, key="direction", value="DOWN")
    verdict = verify_passport(tampered)
    assert verdict.valid is False
    assert VerdictReason.LINKAGE_BROKEN in verdict.reasons


def test_tampered_final_entry_breaks_head_anchor(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    tampered = _tamper(passport, entry_index=12, key="classification", value="PAPER_REALIZED_PNL")
    verdict = verify_passport(tampered)
    assert verdict.valid is False
    assert VerdictReason.HEAD_MISMATCH in verdict.reasons


def test_deleted_stage_breaks_verification(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    document = json.loads(passport.payload_bytes().decode("utf-8"))
    del document["entries"][5]
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    verdict = verify_passport(raw)
    assert verdict.valid is False
    assert (
        VerdictReason.MISSING_STAGE in verdict.reasons
        or VerdictReason.LINKAGE_BROKEN in verdict.reasons
    )


def test_reordered_stages_break_verification(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    document = json.loads(passport.payload_bytes().decode("utf-8"))
    entries = document["entries"]
    entries[8], entries[9] = entries[9], entries[8]
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    verdict = verify_passport(raw)
    assert verdict.valid is False


def test_unflat_reconciliation_fails_verification(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    document = json.loads(passport.payload_bytes().decode("utf-8"))
    document["entries"][11]["payload"]["flat_observed"] = False
    document["entries"][11]["payload"]["position_symbols"] = ["ACME260918C00100000"]
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    verdict = verify_passport(raw)
    assert verdict.valid is False
    assert (
        VerdictReason.FLATNESS_UNPROVEN in verdict.reasons
        or VerdictReason.LINKAGE_BROKEN in verdict.reasons
        or VerdictReason.HEAD_MISMATCH in verdict.reasons
    )


def test_empty_passport_fails_closed() -> None:
    verdict = verify_passport(b"{}")
    assert verdict.valid is False
    assert verdict.reasons == (VerdictReason.EMPTY_PASSPORT,)


def test_parse_rejects_unknown_stage() -> None:
    document = {
        "schema": "esscher.trade_passport",
        "schema_version": 1,
        "entries": [
            {
                "schema": "esscher.trade_passport",
                "schema_version": 1,
                "sequence": 0,
                "stage": "NOT_A_STAGE",
                "at": "2026-09-11T13:36:00Z",
                "payload": {},
                "prev_sha256": GENESIS_PREV_SHA256,
            }
        ],
        "head_sha256": "0" * 64,
    }
    raw = json.dumps(document).encode("utf-8")
    with pytest.raises(PassportChainError):
        parse_passport_bytes(raw)


def test_hand_authored_decision_cannot_satisfy_bindings(tmp_path: Path) -> None:
    passport = build_passport(tmp_path)
    tampered = _tamper(
        passport,
        entry_index=2,
        key="snapshot_sha256",
        value="9" * 64,
    )
    verdict = verify_passport(tampered)
    assert verdict.valid is False
