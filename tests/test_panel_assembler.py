from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

import ringdown_market.panel.manifest as panel_manifest
from ringdown_market.contracts.execution_policy import RESEARCH_DECISION_PROTOCOL_SHA256
from ringdown_market.panel.assembler import PANEL_REPORT_SCHEMA, assemble_panel_report
from ringdown_market.panel.manifest import P0_EXCLUSION_REASON_CODE, PanelRejectionReason

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC_RULE = FIXTURES / "synthetic_qfast_panel_selection_rule.json"
SYNTHETIC_MANIFEST = FIXTURES / "synthetic_qfast_panel_manifest.json"
SYNTHETIC_BUNDLE = FIXTURES / "synthetic_qfast_panel_bundle.json"
FROZEN_AT = "2026-08-28T12:00:00Z"


def _fixture_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _assert_rejected(
    manifest: bytes,
    rule: bytes,
    bundle: bytes,
    reason: PanelRejectionReason,
) -> panel_manifest.PanelRejected:
    with pytest.raises(panel_manifest.PanelRejected) as caught:
        assemble_panel_report(manifest, rule, bundle)
    assert caught.value.reason is reason
    return caught.value


def test_synthetic_panel_report_is_deterministic_and_fully_labeled() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    bundle = _fixture_bytes(SYNTHETIC_BUNDLE)

    first = assemble_panel_report(manifest, rule, bundle)
    second = assemble_panel_report(manifest, rule, bundle)

    assert first == second
    report = json.loads(first)
    assert report["schema"] == PANEL_REPORT_SCHEMA
    assert report["product_name"] == "Esscher"
    assert report["mode"] == "OFFLINE_RESEARCH"
    assert report["data_class"] == "SYNTHETIC_CONTRACT_FIXTURE"
    assert report["claims"] == ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"]
    assert report["limitations"] == sorted(report["limitations"])
    assert report["eligible_event_count"] == 4
    assert report["abstained_events"] == 1
    assert report["input_sha256"] == _sha256(bundle)
    assert len(report["protocol_sha256"]) == 64
    assert report["panel_manifest_sha256"] == _sha256(manifest)
    assert report["decision_protocol_sha256"] == RESEARCH_DECISION_PROTOCOL_SHA256
    assert [excluded["event_id"] for excluded in report["excluded_events"]] == [
        "SYN-QFAST-EXCLUDED-1",
        "SYN-QFAST-EXCLUDED-2",
    ]
    assert all(
        excluded["reason_code"] and excluded["reason_detail"]
        for excluded in report["excluded_events"]
    )

    evaluation = report["evaluation_report"]
    assert evaluation["event_count"] == 4
    profiles = evaluation["latency_profiles"]
    assert set(profiles) == {"zero", "p95"}
    assert profiles["zero"]["requested_latency_ms"] == 0
    assert profiles["p95"]["requested_latency_ms"] == 30_000
    candidate = profiles["zero"]["qfast"]["metrics"]["ringdown"]
    assert candidate["eligible_events"] == 4
    assert candidate["admitted_events"] == 3
    assert candidate["coverage"] == pytest.approx(0.75)
    assert profiles["zero"]["qfast"]["claim"] == "NOT_ALPHA_EVIDENCE"
    assert profiles["zero"]["qfast"]["strongest_baseline"] is not None
    assert profiles["zero"]["qfast"]["leave_best_out_mean"] is not None
    assert evaluation["latency_gate"]["required_profile"] == "p95"


def test_input_hash_binds_exact_bundle_bytes_not_content() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    bundle = _fixture_bytes(SYNTHETIC_BUNDLE)

    baseline = json.loads(assemble_panel_report(manifest, rule, bundle))
    reserialized = (json.dumps(json.loads(bundle), indent=3) + "\n").encode("utf-8")
    moved = json.loads(assemble_panel_report(manifest, rule, reserialized))

    assert reserialized != bundle
    assert baseline["input_sha256"] != moved["input_sha256"]
    assert moved["input_sha256"] == _sha256(reserialized)


def test_bundle_must_bind_the_supplied_manifest_bytes() -> None:
    rule = _fixture_bytes(SYNTHETIC_RULE)
    bundle = _fixture_bytes(SYNTHETIC_BUNDLE)
    manifest = (json.dumps(json.loads(_fixture_bytes(SYNTHETIC_MANIFEST)), indent=3)).encode()

    caught = _assert_rejected(manifest, rule, bundle, PanelRejectionReason.HASH_MISMATCH)

    assert caught.path.endswith("panel_manifest_sha256")


def test_bundle_event_order_must_match_the_frozen_universe() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    payload = json.loads(_fixture_bytes(SYNTHETIC_BUNDLE))
    payload["events"][0], payload["events"][1] = payload["events"][1], payload["events"][0]
    bundle = json.dumps(payload).encode("utf-8")

    caught = _assert_rejected(manifest, rule, bundle, PanelRejectionReason.IDENTITY_MISMATCH)

    assert caught.path.endswith("events[0].decision.event_id")


def test_bundle_missing_event_fails_closed() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    payload = json.loads(_fixture_bytes(SYNTHETIC_BUNDLE))
    payload["events"] = payload["events"][:3]
    bundle = json.dumps(payload).encode("utf-8")

    _assert_rejected(manifest, rule, bundle, PanelRejectionReason.IDENTITY_MISMATCH)


def test_unknown_bundle_field_fails_closed() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    payload = json.loads(_fixture_bytes(SYNTHETIC_BUNDLE))
    payload["extra"] = True
    bundle = json.dumps(payload).encode("utf-8")

    _assert_rejected(manifest, rule, bundle, PanelRejectionReason.UNKNOWN_FIELD)


def test_post_cutoff_evidence_never_enters_the_panel() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    payload = json.loads(_fixture_bytes(SYNTHETIC_BUNDLE))
    payload["events"][0]["decision"]["latest_evidence_at"] = "2026-08-28T14:00:00+00:00"
    bundle = json.dumps(payload).encode("utf-8")

    caught = _assert_rejected(manifest, rule, bundle, PanelRejectionReason.POINT_IN_TIME_VIOLATION)

    assert caught.path.endswith("events[0]")


def test_post_cutoff_feature_snapshot_never_enters_the_panel() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    payload = json.loads(_fixture_bytes(SYNTHETIC_BUNDLE))
    payload["events"][0]["decision"]["feature_snapshot_at"] = "2026-08-28T13:35:01+00:00"
    bundle = json.dumps(payload).encode("utf-8")

    _assert_rejected(manifest, rule, bundle, PanelRejectionReason.POINT_IN_TIME_VIOLATION)


def test_missing_exit_price_fails_closed() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    payload = json.loads(_fixture_bytes(SYNTHETIC_BUNDLE))
    payload["events"][0]["path"] = payload["events"][0]["path"][:2]
    bundle = json.dumps(payload).encode("utf-8")

    caught = _assert_rejected(manifest, rule, bundle, PanelRejectionReason.MISSING_PRICE_POINT)

    assert caught.path.endswith("events")


def test_nonpositive_price_fails_closed() -> None:
    manifest = _fixture_bytes(SYNTHETIC_MANIFEST)
    rule = _fixture_bytes(SYNTHETIC_RULE)
    payload = json.loads(_fixture_bytes(SYNTHETIC_BUNDLE))
    payload["events"][0]["path"][0]["stock"] = 0.0
    bundle = json.dumps(payload).encode("utf-8")

    _assert_rejected(manifest, rule, bundle, PanelRejectionReason.INVALID_DOCUMENT)


def test_real_panel_assembly_fails_closed_while_upstream_issues_unmerged() -> None:
    manifest_payload, rule_bytes, bundle_payload = _real_panel_inputs()
    manifest = (json.dumps(manifest_payload, indent=2) + "\n").encode("utf-8")
    bundle_payload["panel_manifest_sha256"] = _sha256(manifest)
    bundle = (json.dumps(bundle_payload, indent=2) + "\n").encode("utf-8")

    caught = _assert_rejected(
        manifest, rule_bytes, bundle, PanelRejectionReason.UPSTREAM_CONTRACT_MISSING
    )

    assert caught.path.endswith("strategy_policy_sha256")


def test_real_panel_qualifier_gate_fires_before_upstream_registry() -> None:
    rule = _fixture_bytes(SYNTHETIC_RULE)
    manifest_payload = json.loads(_fixture_bytes(SYNTHETIC_MANIFEST))
    manifest_payload["data_class"] = "POINT_IN_TIME_EVENT_PANEL"
    manifest = json.dumps(manifest_payload).encode("utf-8")

    caught = _assert_rejected(manifest, rule, b"{}", PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH)

    assert caught.path.endswith("data_qualifiers")


def _decision_payload(event_id: str, signal: str) -> dict:
    return {
        "event_id": event_id,
        "issuer": f"ISSUER-{event_id}",
        "decision_cutoff": "2026-08-28T13:35:00+00:00",
        "latest_evidence_at": "2026-08-28T12:00:00+00:00",
        "feature_snapshot_at": "2026-08-28T13:35:00+00:00",
        "opening_return": 0.0,
        "market_opening_return": 0.0,
        "sector_opening_return": 0.0,
        "market_beta": 0.0,
        "sector_beta": 0.0,
        "price_only_score": 0.0,
        "fundamental_score": 0.0,
        "numeric_score": 0.0,
        "candidate_signal": signal,
    }


def _path_payload(signal: str) -> list[dict]:
    stamps = [
        "2026-08-28T13:35:00+00:00",
        "2026-08-28T13:35:30+00:00",
        "2026-08-28T14:35:00+00:00",
        "2026-08-28T14:35:30+00:00",
    ]
    if signal == "UP":
        prices = [99.0, 100.0, 101.0, 102.0]
    elif signal == "DOWN":
        prices = [101.0, 100.0, 99.0, 98.0]
    else:
        prices = [100.0, 100.0, 100.0, 100.0]
    return [
        {"at": stamp, "stock": price, "market": 100.0, "sector": 100.0}
        for stamp, price in zip(stamps, prices, strict=True)
    ]


def _real_panel_inputs() -> tuple[dict, bytes, dict]:
    rule = _rule_payload_for_real()
    rule_bytes = (json.dumps(rule, indent=2) + "\n").encode("utf-8")
    eligible = [
        {
            "event_id": f"PANEL-TEST-{index:03d}",
            "evidence_manifest_sha256": _sha256(f"evidence:{index}".encode()),
        }
        for index in range(1, 21)
    ]
    qualifiers = [
        "INDICATIVE_DATA",
        "NOT_ALPHA_EVIDENCE",
        "NO_OUTCOME_VALUES",
        "NO_BROKER_EXECUTION",
    ]
    measurement = {
        "kind": "HOST_MEASURED",
        "publisher": "test host latency measurement",
        "measured_at": FROZEN_AT,
        "content_sha256": _sha256(b"host-measured-p95"),
    }
    manifest_payload = {
        "schema": "ringdown.qfast_panel_manifest",
        "schema_version": 1,
        "panel_id": "TEST_REAL_PANEL_V1",
        "frozen_at": FROZEN_AT,
        "selection_rule_sha256": _sha256(rule_bytes),
        "strategy_policy_sha256": _sha256(b"test-strategy-policy"),
        "snapshot_protocol_sha256": _sha256(b"test-snapshot-protocol"),
        "decision_protocol_sha256": RESEARCH_DECISION_PROTOCOL_SHA256,
        "data_class": "POINT_IN_TIME_EVENT_PANEL",
        "data_qualifiers": qualifiers,
        "hold_seconds": 3600,
        "required_latency_profile": "p95",
        "latency_profiles": {
            "zero": {"requested_latency_ms": 0, "measurement": None},
            "p95": {"requested_latency_ms": 30_000, "measurement": measurement},
        },
        "eligible_events": eligible,
        "excluded_events": [
            {
                "event_id": event_id,
                "reason_code": P0_EXCLUSION_REASON_CODE,
                "reason_detail": "Frozen P0 contract-development event.",
            }
            for event_id in sorted(panel_manifest.P0_CONTRACT_DEVELOPMENT_EVENT_IDS)
        ],
        "limitations": qualifiers,
    }
    return (
        manifest_payload,
        rule_bytes,
        {
            "schema": "ringdown.qfast_panel_bundle",
            "schema_version": 1,
            "fixture_class": "POINT_IN_TIME_EVENT_PANEL",
            "limitations": qualifiers,
            "panel_manifest_sha256": "",
            "events": [
                {
                    "decision": _decision_payload(
                        event["event_id"],
                        ("UP", "DOWN", "UNCERTAIN")[index % 3],
                    ),
                    "path": _path_payload(("UP", "DOWN", "UNCERTAIN")[index % 3]),
                }
                for index, event in enumerate(eligible)
            ],
        },
    )


def _rule_payload_for_real() -> dict:
    return json.loads(_fixture_bytes(SYNTHETIC_RULE))


def test_real_panel_assembles_once_upstream_contracts_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_payload, rule_bytes, bundle_payload = _real_panel_inputs()
    monkeypatch.setattr(
        panel_manifest,
        "KNOWN_STRATEGY_POLICY_SHA256",
        frozenset({manifest_payload["strategy_policy_sha256"]}),
    )
    monkeypatch.setattr(
        panel_manifest,
        "KNOWN_SNAPSHOT_PROTOCOL_SHA256",
        frozenset({manifest_payload["snapshot_protocol_sha256"]}),
    )
    manifest = (json.dumps(manifest_payload, indent=2) + "\n").encode("utf-8")
    bundle_payload["panel_manifest_sha256"] = _sha256(manifest)
    bundle = (json.dumps(bundle_payload, indent=2) + "\n").encode("utf-8")

    first = assemble_panel_report(manifest, rule_bytes, bundle)
    second = assemble_panel_report(manifest, rule_bytes, bundle)

    assert first == second
    report = json.loads(first)
    assert report["data_class"] == "POINT_IN_TIME_EVENT_PANEL"
    assert report["eligible_event_count"] == 20
    assert len(report["excluded_events"]) == 4
    assert {excluded["event_id"] for excluded in report["excluded_events"]} == (
        panel_manifest.P0_CONTRACT_DEVELOPMENT_EVENT_IDS
    )
    evaluation = report["evaluation_report"]
    assert evaluation["data_class"] == "POINT_IN_TIME_EVENT_PANEL"
    assert evaluation["event_count"] == 20
    for profile in evaluation["latency_profiles"].values():
        assert profile["qfast"]["event_count"] == 20
        assert profile["qfast"]["status"] != "INSUFFICIENT_DATA"
        assert profile["qfast"]["claim"] == "NOT_ALPHA_EVIDENCE"


def test_panel_assembly_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("panel assembly must not touch the network")

    monkeypatch.setattr(socket, "socket", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", _deny_network)

    report = assemble_panel_report(
        _fixture_bytes(SYNTHETIC_MANIFEST),
        _fixture_bytes(SYNTHETIC_RULE),
        _fixture_bytes(SYNTHETIC_BUNDLE),
    )

    assert json.loads(report)["schema"] == PANEL_REPORT_SCHEMA
