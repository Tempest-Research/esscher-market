from __future__ import annotations

import json
import socket
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from ringdown_market.cli import main as cli_main
from ringdown_market.data import (
    FakeSnapshotAdapters,
    HostConfigRejected,
    SnapshotRejected,
    SnapshotRejectionReason,
    assert_read_only_adapters,
    run_capture_request,
    validate_capture_host_config,
)
from ringdown_market.strategy.policy import (
    STRATEGY_POLICY_V1_SHA256,
    parse_frozen_strategy_policy_v1,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "configs" / "strategy_v1.json"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "strategy_v1_synthetic_capture_request.json"


def policy():
    return parse_frozen_strategy_policy_v1(POLICY_PATH.read_bytes())


def fixture_payload() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def render_request(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")


def capture(payload: dict[str, Any] | None = None):
    request = render_request(payload if payload is not None else fixture_payload())
    return run_capture_request(
        request,
        policy=policy(),
        expected_policy_sha256=STRATEGY_POLICY_V1_SHA256,
    )


def test_eligible_snapshot_compiles_with_features_and_receipts() -> None:
    snapshot = capture()
    assert snapshot.eligible is True
    assert snapshot.rejection_reasons == ()
    assert snapshot.payload["eligibility"] == "ELIGIBLE"
    assert snapshot.payload["policy_sha256"] == STRATEGY_POLICY_V1_SHA256

    feature_ids = {feature["feature_id"] for feature in snapshot.payload["features"]}
    assert feature_ids == {
        "earnings_numeric/v1",
        "guidance_statement/v1",
        "opening_return/v1",
        "market_opening_return/v1",
        "sector_opening_return/v1",
        "market_beta/v1",
        "sector_beta/v1",
    }
    actions = snapshot.payload["corporate_actions"]
    assert len(actions) == 1
    assert actions[0]["action_type"] == "SPLIT"
    assert actions[0]["factor"] == "2.0"


def test_identical_sources_and_policy_produce_byte_identical_snapshots() -> None:
    first = capture()
    second = capture()
    assert first.raw == second.raw
    assert first.sha256 == second.sha256


def test_post_cutoff_evidence_makes_snapshot_ineligible() -> None:
    payload = fixture_payload()
    payload["evidence"][1]["published_at"] = "2026-09-11T13:36:00Z"
    snapshot = capture(payload)
    assert snapshot.eligible is False
    assert SnapshotRejectionReason.POST_CUTOFF_EVIDENCE.value in snapshot.rejection_reasons
    assert snapshot.payload["features"] == []


def test_missing_bars_fail_closed() -> None:
    payload = fixture_payload()
    del payload["opening_bars"]["XLP"]
    snapshot = capture(payload)
    assert snapshot.eligible is False
    assert SnapshotRejectionReason.MISSING_BARS.value in snapshot.rejection_reasons


def test_unsynchronized_window_fails_closed() -> None:
    payload = fixture_payload()
    payload["opening_bars"]["XLP"] = payload["opening_bars"]["XLP"][:1]
    snapshot = capture(payload)
    assert snapshot.eligible is False
    assert SnapshotRejectionReason.UNSYNCHRONIZED_WINDOW.value in snapshot.rejection_reasons


def test_price_floor_enforces_universe_rule() -> None:
    payload = fixture_payload()
    payload["opening_bars"]["ACME"][0]["price"] = "5.00"
    snapshot = capture(payload)
    assert snapshot.eligible is False
    assert SnapshotRejectionReason.INELIGIBLE_UNIVERSE.value in snapshot.rejection_reasons


def test_stale_raw_observation_fails_closed() -> None:
    payload = fixture_payload()
    payload["opening_bars"]["ACME"][1]["raw_observed_at"] = "2026-09-11T13:36:30Z"
    snapshot = capture(payload)
    assert snapshot.eligible is False
    assert SnapshotRejectionReason.STALE_OBSERVATION.value in snapshot.rejection_reasons


def test_redistribution_violation_rejected_at_load() -> None:
    payload = fixture_payload()
    payload["evidence"][1]["raw_bytes_utf8"] = "not permitted bytes"
    from ringdown_market.data.capture import CaptureRequestRejected

    with pytest.raises(CaptureRequestRejected):
        capture(payload)


def test_symbol_change_without_destination_rejected() -> None:
    payload = fixture_payload()
    payload["corporate_actions"]["ACME"][0] = {
        "action_type": "SYMBOL_CHANGE",
        "effective_date": "2026-08-15",
        "symbol": "ACME",
        "factor": None,
        "to_symbol": None,
        "source_id": "synthetic-corporate-actions",
    }
    from ringdown_market.data.capture import CaptureRequestRejected

    with pytest.raises(CaptureRequestRejected):
        capture(payload)


def test_policy_hash_mismatch_fails_closed() -> None:
    request = render_request(fixture_payload())
    with pytest.raises(SnapshotRejected) as caught:
        run_capture_request(
            request,
            policy=policy(),
            expected_policy_sha256="0" * 64,
        )
    assert SnapshotRejectionReason.POLICY_HASH_MISMATCH.value in str(caught.value)


def test_frozen_cutoff_rule_enforced() -> None:
    payload = fixture_payload()
    payload["event"]["decision_cutoff"] = "2026-09-11T13:36:00Z"
    from ringdown_market.data.capture import CaptureRequestRejected

    with pytest.raises(CaptureRequestRejected):
        capture(payload)


def test_capture_runs_with_socket_disabled() -> None:
    def _deny_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch_socket = socket.socket
    socket.socket = _deny_socket  # type: ignore[assignment]
    try:
        snapshot = capture()
        assert snapshot.eligible is True
    finally:
        socket.socket = monkeypatch_socket


def test_fake_adapters_are_read_only() -> None:
    adapters = FakeSnapshotAdapters(
        evidence=(), opening_bars={}, estimation_series={}, corporate_actions={}
    )
    assert_read_only_adapters(adapters)

    class MutatingAdapter:
        def place_order(self) -> None: ...

    with pytest.raises(HostConfigRejected):
        assert_read_only_adapters(MutatingAdapter())


def test_host_config_rejects_credentials() -> None:
    with pytest.raises(HostConfigRejected):
        validate_capture_host_config({"adapter_registry": "FAKE", "api_key": "secret-value"})
    with pytest.raises(HostConfigRejected):
        validate_capture_host_config({"adapter_registry": "LIVE"})
    assert validate_capture_host_config({"adapter_registry": "FAKE"}) == "FAKE"


def test_cli_capture_snapshot_end_to_end(tmp_path: Path) -> None:
    host_config = tmp_path / "host-config.json"
    host_config.write_text(json.dumps({"adapter_registry": "FAKE"}), encoding="utf-8")
    output = tmp_path / "snapshot.json"

    exit_code = cli_main(
        [
            "capture-snapshot",
            "--host-config",
            str(host_config),
            "--policy",
            str(POLICY_PATH),
            "--input",
            str(FIXTURE_PATH),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    written = output.read_bytes()
    expected = capture()
    assert written == expected.raw
    payload = json.loads(written)
    assert payload["eligibility"] == "ELIGIBLE"

    unsafe_config = tmp_path / "unsafe.json"
    unsafe_config.write_text(
        json.dumps({"adapter_registry": "FAKE", "secret_token": "x"}), encoding="utf-8"
    )
    exit_code = cli_main(
        [
            "capture-snapshot",
            "--host-config",
            str(unsafe_config),
            "--policy",
            str(POLICY_PATH),
            "--input",
            str(FIXTURE_PATH),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 2


def test_cli_capture_rejects_tampered_policy(tmp_path: Path) -> None:
    host_config = tmp_path / "host-config.json"
    host_config.write_text(json.dumps({"adapter_registry": "FAKE"}), encoding="utf-8")
    tampered_policy = tmp_path / "policy.json"
    tampered_policy.write_bytes(
        POLICY_PATH.read_bytes().replace(b'"hold_minutes": 60', b'"hold_minutes": 61')
    )
    output = tmp_path / "snapshot.json"
    exit_code = cli_main(
        [
            "capture-snapshot",
            "--host-config",
            str(host_config),
            "--policy",
            str(tampered_policy),
            "--input",
            str(FIXTURE_PATH),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 3
    assert not output.exists()


def test_capture_request_schema_enforced() -> None:
    payload = fixture_payload()
    payload["schema"] = "esscher.other_request"
    from ringdown_market.data.capture import CaptureRequestRejected

    with pytest.raises(CaptureRequestRejected):
        capture(payload)


def test_missing_evidence_fails_closed() -> None:
    payload = deepcopy(fixture_payload())
    payload["evidence"] = []
    snapshot = capture(payload)
    assert snapshot.eligible is False
    assert SnapshotRejectionReason.MISSING_EVIDENCE.value in snapshot.rejection_reasons
