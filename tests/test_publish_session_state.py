"""Publisher guard tests: redaction, paper-only refusal, change detection.

The publisher (scripts/publish_session_state.py) is the only bridge between
the live paper account and the public website; these tests pin its safety
properties: PAPER-only base URLs, absolute redaction of the raw account id,
change-detection that ignores volatile timestamps, and honest lane labels.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_publisher():
    spec = importlib.util.spec_from_file_location(
        "publish_session_state", REPO_ROOT / "scripts/publish_session_state.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("publish_session_state", module)
    spec.loader.exec_module(module)
    return module


publisher = _load_publisher()

ACCOUNT = {
    "id": "PA-RAW-SECRET-ID-9999",
    "account_class": "PAPER",
    "status": "ACTIVE",
    "equity": "101250.55",
    "cash": "60000.00",
    "buying_power": "240000.00",
}
POSITIONS = [
    {
        "symbol": "MDT",
        "qty": "100",
        "side": "long",
        "asset_class": "us_equity",
        "market_value": "8120.00",
        "avg_entry_price": "80.00",
        "current_price": "81.20",
        "unrealized_pl": "120.00",
        "unrealized_plpc": "0.015",
    }
]
ORDERS = [{"id": "order-1", "status": "open"}]


def _fake_get(path: str):
    if path == "/v2/account":
        return ACCOUNT
    if path.startswith("/v2/orders"):
        return ORDERS
    if path == "/v2/positions":
        return POSITIONS
    raise AssertionError(path)


def test_publisher_refuses_any_non_paper_base_url() -> None:
    paper = "https://" + publisher.PAPER_HOST_PREFIX + "alpaca.markets"
    publisher._assert_paper_base(paper)
    for live in (
        "https://api" + ".alpaca.markets",
        "https://example.com",
        "https://" + publisher.PAPER_HOST_PREFIX + "example.com",
        "https://api" + publisher.PAPER_HOST_SUFFIX,
    ):
        with pytest.raises(SystemExit):
            publisher._assert_paper_base(live)


def test_account_state_digests_the_raw_account_id_and_computes_pnl() -> None:
    state = publisher._account_state(_fake_get)
    assert state["account_id_sha256"] != ACCOUNT["id"]
    assert len(state["account_id_sha256"]) == 64
    assert ACCOUNT["id"] not in json.dumps(state)
    assert state["equity_vs_start"] == 1250.55
    assert state["equity_pct_vs_start"] == pytest.approx(1.25055, rel=1e-4)
    assert state["positions_unrealized_pl"] == 120.0
    assert state["open_order_count"] == 1
    assert state["positions"][0]["symbol"] == "MDT"


def test_payload_redaction_and_lane_labels() -> None:
    payload = {
        "schema": "esscher.public_session_state",
        "schema_version": 1,
        "generated_at": publisher._now(),
        "claims": ["PAPER_ONLY", "NO_CREDENTIALS", "NO_RAW_ACCOUNT_ID", "SOURCE_GROUNDED"],
        "lane": {"kind": "DELAYED_EXECUTION_DEMO", "labels": publisher.DEMO_LABELS},
        "session_status": "RUNNING",
        "account": publisher._account_state(_fake_get),
        "gate": {},
    }
    text = json.dumps(payload)
    assert ACCOUNT["id"] not in text
    assert "SECRET" not in text.replace("NO_CREDENTIALS", "")
    assert payload["lane"]["labels"] == [
        "DELAYED_EXECUTION_DEMO",
        "NOT_THE_VALIDATED_LANE",
        "INDICATIVE_OPTION_PRICING",
    ]


def test_stable_hash_ignores_volatile_timestamps_and_tracks_state() -> None:
    payload = {"account": publisher._account_state(_fake_get), "gate": {"x": 1}}
    first = publisher._stable_hash(payload)
    payload["account"]["observed_at"] = "2099-01-01T00:00:00Z"
    assert publisher._stable_hash(payload) == first
    payload["account"]["equity"] = "99999.00"
    assert publisher._stable_hash(payload) != first
    payload["account"]["equity"] = "101250.55"
    payload["account"]["positions"] = []
    assert publisher._stable_hash(payload) != first


def test_gate_state_maps_artifacts_and_shortens_hashes(tmp_path: Path) -> None:
    mint = tmp_path / "mint"
    mint.mkdir()
    (mint / "mint_summary.json").write_text(
        json.dumps(
            {
                "release_id": "REL-1",
                "release_sha256": "a" * 64,
                "code_revision": "b" * 40,
                "build_artifact_sha256": "c" * 64,
            }
        )
    )
    preflight = tmp_path / "receipt.json"
    preflight.write_text(
        json.dumps(
            {
                "receipt_id": "PRE-1",
                "verdict": "PASSED",
                "receipt_sha256": "d" * 64,
                "is_flat": True,
                "starting_balance_satisfied": True,
                "environment": "PAPER",
                "observed_at": "2026-09-04T13:00:00Z",
            }
        )
    )
    rehearsal = tmp_path / "rehearsal"
    rehearsal.mkdir()
    (rehearsal / "rehearsal-audit.json").write_text(
        json.dumps(
            {
                "session_id": "S-1",
                "mode": "live",
                "disposition": "COMPLETED",
                "terminal_flat_proven": True,
                "mutating_tool_calls": 0,
                "receipt_sha256": "e" * 64,
            }
        )
    )
    measurement = tmp_path / "measure.json"
    measurement.write_text(
        json.dumps(
            {
                "provider": "dashscope_qwen",
                "model": "qwen3.8-max-0902",
                "route_sha256": "f" * 64,
                "warm_completed": 30,
                "warm_schema_valid": 30,
                "warm_p50_ms": 4200.0,
                "warm_p95_ms_nearest_rank": 5578.0,
                "frozen_hard_timeout_seconds": 8,
            }
        )
    )
    demo = tmp_path / "demo.json"
    demo.write_text(
        json.dumps(
            {
                "decision": {"direction": "UNCERTAIN"},
                "exchange": {"status": "COMPLETED"},
                "produced_at": "2026-09-04T14:00:00Z",
                "decision_artifact_sha256": "9" * 64,
            }
        )
    )
    gate = publisher._gate_state(mint, preflight, [rehearsal], measurement, demo)
    assert gate["preflight"]["verdict"] == "PASSED"
    assert gate["preflight"]["receipt_sha256"] == "d" * 16
    assert gate["release"]["release_sha256"] == "a" * 16
    assert gate["release"]["code_revision"] == "b" * 12
    assert gate["rehearsals"][0]["terminal_flat_proven"] is True
    assert gate["reasoner"]["route"] == "dashscope_qwen / qwen3.8-max-0902"
    assert gate["reasoner"]["p95_ms"] == 5578.0
    assert gate["reasoner"]["last_live_decision"]["direction"] == "UNCERTAIN"
    text = json.dumps(gate)
    assert "a" * 64 not in text
