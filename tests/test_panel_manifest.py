from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import ringdown_market.panel.manifest as panel_manifest
from ringdown_market.contracts.execution_policy import RESEARCH_DECISION_PROTOCOL_SHA256
from ringdown_market.panel.manifest import (
    P0_EXCLUSION_REASON_CODE,
    PanelRejectionReason,
    validate_panel_manifest,
    validate_panel_selection_rule,
)

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC_RULE = FIXTURES / "synthetic_qfast_panel_selection_rule.json"
SYNTHETIC_MANIFEST = FIXTURES / "synthetic_qfast_panel_manifest.json"
DATA_RULE = Path(__file__).parents[1] / "data" / "qfast-panel" / "selection-rule-v1.json"
FROZEN_AT = "2026-08-28T12:00:00Z"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _rule_payload(**overrides: object) -> dict:
    payload = {
        "schema": "ringdown.qfast_panel_selection_rule",
        "schema_version": 1,
        "rule_id": "TEST_PANEL_RULE_V1",
        "frozen_at": FROZEN_AT,
        "decision_cutoff_policy": "SCHEDULED_EVENT_AT",
        "criteria": {
            "primary_source_evidence_required": True,
            "exact_publication_time_required": True,
            "evidence_retrieved_no_later_than_decision_cutoff": True,
            "synchronized_issuer_market_sector_windows_required": True,
            "regular_us_equity_session_required": True,
            "post_cutoff_paths_forbidden_at_freeze": True,
            "outcome_values_forbidden_at_freeze": True,
            "abstentions_retained_in_denominator": True,
            "p0_contract_development_events_excluded": True,
            "minimum_eligible_events": 20,
            "maximum_eligible_events": 30,
        },
        "required_excluded_event_ids": sorted(panel_manifest.P0_CONTRACT_DEVELOPMENT_EVENT_IDS),
        "claim_boundary": [
            "POINT_IN_TIME_EVENT_PANEL_CANDIDATE",
            "NOT_ALPHA_EVIDENCE",
            "NO_OUTCOME_VALUES",
            "NO_BROKER_EXECUTION",
        ],
    }
    payload.update(overrides)
    return payload


def _rule_bytes(payload: dict | None = None) -> bytes:
    return (json.dumps(payload or _rule_payload(), indent=2) + "\n").encode("utf-8")


def _latency_profiles(kind: str | None = "SYNTHETIC") -> dict:
    measurement = None
    if kind is not None:
        measurement = {
            "kind": kind,
            "publisher": "test latency publisher",
            "measured_at": FROZEN_AT,
            "content_sha256": _sha256(b"test-latency-measurement"),
        }
    return {
        "zero": {"requested_latency_ms": 0, "measurement": None},
        "p95": {"requested_latency_ms": 30_000, "measurement": measurement},
    }


def _eligible_events(count: int, *, data_class: str) -> list[dict]:
    real = data_class == "POINT_IN_TIME_EVENT_PANEL"
    return [
        {
            "event_id": f"PANEL-TEST-{index:03d}",
            "evidence_manifest_sha256": (
                _sha256(f"evidence:PANEL-TEST-{index:03d}".encode()) if real else None
            ),
        }
        for index in range(1, count + 1)
    ]


def _p0_exclusions() -> list[dict]:
    return [
        {
            "event_id": event_id,
            "reason_code": P0_EXCLUSION_REASON_CODE,
            "reason_detail": "Frozen P0 contract-development event.",
        }
        for event_id in sorted(panel_manifest.P0_CONTRACT_DEVELOPMENT_EVENT_IDS)
    ]


def _manifest_payload(
    rule_bytes: bytes,
    *,
    data_class: str = "SYNTHETIC_CONTRACT_FIXTURE",
    eligible_count: int = 4,
    **overrides: object,
) -> dict:
    qualifiers = (
        ["INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE", "NO_OUTCOME_VALUES", "NO_BROKER_EXECUTION"]
        if data_class == "POINT_IN_TIME_EVENT_PANEL"
        else ["NOT_HISTORICAL_DATA", "NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION"]
    )
    payload = {
        "schema": "ringdown.qfast_panel_manifest",
        "schema_version": 1,
        "panel_id": "TEST_PANEL_V1",
        "frozen_at": FROZEN_AT,
        "selection_rule_sha256": _sha256(rule_bytes),
        "strategy_policy_sha256": _sha256(b"test-strategy-policy"),
        "snapshot_protocol_sha256": _sha256(b"test-snapshot-protocol"),
        "decision_protocol_sha256": RESEARCH_DECISION_PROTOCOL_SHA256,
        "data_class": data_class,
        "data_qualifiers": qualifiers,
        "hold_seconds": 3600,
        "required_latency_profile": "p95",
        "latency_profiles": _latency_profiles(
            kind="HOST_MEASURED" if data_class == "POINT_IN_TIME_EVENT_PANEL" else "SYNTHETIC"
        ),
        "eligible_events": _eligible_events(eligible_count, data_class=data_class),
        "excluded_events": [],
        "limitations": qualifiers,
    }
    payload.update(overrides)
    return payload


def _manifest_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _assert_rejected(
    manifest: bytes,
    rule: bytes,
    reason: PanelRejectionReason,
) -> panel_manifest.PanelRejected:
    with pytest.raises(panel_manifest.PanelRejected) as caught:
        validate_panel_manifest(manifest, rule)
    assert caught.value.reason is reason
    return caught.value


def test_committed_panel_selection_rule_validates() -> None:
    validate_panel_selection_rule(DATA_RULE.read_bytes())


def test_synthetic_fixture_rule_validates() -> None:
    validate_panel_selection_rule(SYNTHETIC_RULE.read_bytes())


def test_synthetic_fixture_manifest_validates() -> None:
    validated = validate_panel_manifest(
        SYNTHETIC_MANIFEST.read_bytes(), SYNTHETIC_RULE.read_bytes()
    )

    assert validated.panel_id == "SYNTHETIC_QFAST_PANEL_TEST_V1"
    assert validated.data_class == "SYNTHETIC_CONTRACT_FIXTURE"
    assert validated.eligible_event_ids == (
        "SYN-QFAST-1",
        "SYN-QFAST-2",
        "SYN-QFAST-3",
        "SYN-QFAST-4",
    )
    assert tuple(excluded.event_id for excluded in validated.excluded_events) == (
        "SYN-QFAST-EXCLUDED-1",
        "SYN-QFAST-EXCLUDED-2",
    )
    assert validated.minimum_events == 4
    assert validated.latency_profiles == {"zero": 0, "p95": 30_000}


def test_unknown_manifest_field_fails_closed() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, extra_field=1)

    caught = _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.UNKNOWN_FIELD)

    assert caught.path.endswith("extra_field")


def test_missing_manifest_field_fails_closed() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule)
    del payload["limitations"]

    caught = _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.MISSING_FIELD)

    assert caught.path.endswith("limitations")


def test_duplicate_json_key_fails_closed() -> None:
    rule = _rule_bytes()
    raw = b'{"schema": "ringdown.qfast_panel_manifest", "schema": "ringdown.qfast_panel_manifest"}'

    _assert_rejected(raw, rule, PanelRejectionReason.DUPLICATE_FIELD)


def test_unsupported_manifest_schema_fails_closed() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, schema="ringdown.something_else")

    _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.UNSUPPORTED_SCHEMA)


def test_manifest_must_bind_supplied_rule_bytes() -> None:
    rule = _rule_bytes()
    other_rule = _rule_bytes(_rule_payload(rule_id="OTHER_RULE_V1"))
    payload = _manifest_payload(other_rule)

    caught = _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.HASH_MISMATCH)

    assert caught.path.endswith("selection_rule_sha256")


def test_manifest_freeze_must_equal_rule_freeze() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, frozen_at="2026-08-28T13:00:00Z")

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.POINT_IN_TIME_VIOLATION
    )

    assert caught.path.endswith("frozen_at")


def test_p0_event_can_never_be_eligible() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule)
    payload["eligible_events"][0]["event_id"] = "KR-2026Q2-EARNINGS"

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.P0_EVENT_IN_PANEL
    )

    assert caught.path.endswith("eligible_events[0].event_id")


def test_real_panel_requires_the_twenty_event_floor() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, data_class="POINT_IN_TIME_EVENT_PANEL", eligible_count=19)

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.PANEL_SIZE_VIOLATION
    )

    assert caught.path.endswith("eligible_events")


def test_real_panel_cannot_exceed_the_thirty_event_ceiling() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, data_class="POINT_IN_TIME_EVENT_PANEL", eligible_count=31)

    _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.PANEL_SIZE_VIOLATION)


def test_synthetic_panel_requires_at_least_two_events() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, eligible_count=1)

    _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.PANEL_SIZE_VIOLATION)


def test_real_panel_requires_host_measured_p95() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, data_class="POINT_IN_TIME_EVENT_PANEL", eligible_count=20)
    payload["latency_profiles"] = _latency_profiles(kind="SYNTHETIC")

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.LATENCY_PROFILE_NOT_MEASURED
    )

    assert caught.path.endswith("latency_profiles.p95.measurement.kind")


def test_missing_p95_measurement_fails_closed() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule)
    payload["latency_profiles"] = _latency_profiles(kind=None)

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.LATENCY_PROFILE_NOT_MEASURED
    )

    assert caught.path.endswith("latency_profiles.p95.measurement")


def test_zero_profile_cannot_carry_measurement() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule)
    payload["latency_profiles"]["zero"]["measurement"] = {
        "kind": "SYNTHETIC",
        "publisher": "test",
        "measured_at": FROZEN_AT,
        "content_sha256": _sha256(b"zero"),
    }

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.LATENCY_PROFILE_NOT_MEASURED
    )

    assert caught.path.endswith("latency_profiles.zero.measurement")


def test_real_panel_fails_closed_until_strategy_policy_merges() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, data_class="POINT_IN_TIME_EVENT_PANEL", eligible_count=20)

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.UPSTREAM_CONTRACT_MISSING
    )

    assert caught.path.endswith("strategy_policy_sha256")


def test_real_panel_fails_closed_until_snapshot_protocol_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, data_class="POINT_IN_TIME_EVENT_PANEL", eligible_count=20)
    monkeypatch.setattr(
        panel_manifest,
        "KNOWN_STRATEGY_POLICY_SHA256",
        frozenset({payload["strategy_policy_sha256"]}),
    )

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.UPSTREAM_CONTRACT_MISSING
    )

    assert caught.path.endswith("snapshot_protocol_sha256")


def test_real_manifest_validates_once_upstream_contracts_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(
        rule,
        data_class="POINT_IN_TIME_EVENT_PANEL",
        eligible_count=20,
        excluded_events=_p0_exclusions(),
    )
    monkeypatch.setattr(
        panel_manifest,
        "KNOWN_STRATEGY_POLICY_SHA256",
        frozenset({payload["strategy_policy_sha256"]}),
    )
    monkeypatch.setattr(
        panel_manifest,
        "KNOWN_SNAPSHOT_PROTOCOL_SHA256",
        frozenset({payload["snapshot_protocol_sha256"]}),
    )

    validated = validate_panel_manifest(_manifest_bytes(payload), rule)

    assert validated.minimum_events == 20
    assert len(validated.eligible_event_ids) == 20
    assert len(validated.excluded_events) == 4


def test_decision_protocol_must_match_merged_contract() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, decision_protocol_sha256=_sha256(b"not-the-protocol"))

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.UPSTREAM_CONTRACT_MISSING
    )

    assert caught.path.endswith("decision_protocol_sha256")


def test_synthetic_manifest_requires_synthetic_qualifiers() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, data_qualifiers=["NOT_ALPHA_EVIDENCE"])

    _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH)


def test_synthetic_events_cannot_claim_evidence_provenance() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule)
    payload["eligible_events"][0]["evidence_manifest_sha256"] = _sha256(b"invented")

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH
    )

    assert caught.path.endswith("eligible_events[0].evidence_manifest_sha256")


def test_rule_claim_boundary_cannot_be_weakened() -> None:
    rule_payload = _rule_payload(claim_boundary=["NOT_ALPHA_EVIDENCE"])

    with pytest.raises(panel_manifest.PanelRejected) as caught:
        validate_panel_selection_rule(_rule_bytes(rule_payload))

    assert caught.value.reason is PanelRejectionReason.CLAIM_BOUNDARY_MISMATCH


def test_rule_criteria_cannot_be_disabled() -> None:
    rule_payload = _rule_payload()
    rule_payload["criteria"]["abstentions_retained_in_denominator"] = False

    with pytest.raises(panel_manifest.PanelRejected) as caught:
        validate_panel_selection_rule(_rule_bytes(rule_payload))

    assert caught.value.reason is PanelRejectionReason.SELECTION_RULE_VIOLATION


def test_rule_event_bounds_are_frozen() -> None:
    rule_payload = _rule_payload()
    rule_payload["criteria"]["minimum_eligible_events"] = 19

    with pytest.raises(panel_manifest.PanelRejected) as caught:
        validate_panel_selection_rule(_rule_bytes(rule_payload))

    assert caught.value.reason is PanelRejectionReason.SELECTION_RULE_VIOLATION


def test_rule_must_keep_all_p0_exclusions() -> None:
    rule_payload = _rule_payload(required_excluded_event_ids=["KR-2026Q2-EARNINGS"])

    with pytest.raises(panel_manifest.PanelRejected) as caught:
        validate_panel_selection_rule(_rule_bytes(rule_payload))

    assert caught.value.reason is PanelRejectionReason.SELECTION_RULE_VIOLATION


def test_event_cannot_be_eligible_and_excluded() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(
        rule,
        excluded_events=[
            {
                "event_id": "PANEL-TEST-001",
                "reason_code": "SOME_REASON",
                "reason_detail": "detail",
            }
        ],
    )

    _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.IDENTITY_MISMATCH)


def test_real_manifest_must_keep_p0_exclusions_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, data_class="POINT_IN_TIME_EVENT_PANEL", eligible_count=20)
    monkeypatch.setattr(
        panel_manifest,
        "KNOWN_STRATEGY_POLICY_SHA256",
        frozenset({payload["strategy_policy_sha256"]}),
    )
    monkeypatch.setattr(
        panel_manifest,
        "KNOWN_SNAPSHOT_PROTOCOL_SHA256",
        frozenset({payload["snapshot_protocol_sha256"]}),
    )

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.SELECTION_RULE_VIOLATION
    )

    assert caught.path.endswith("excluded_events")


def test_p0_exclusion_requires_the_frozen_reason_code() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(
        rule,
        excluded_events=[
            {
                "event_id": "KR-2026Q2-EARNINGS",
                "reason_code": "SOME_OTHER_CODE",
                "reason_detail": "detail",
            }
        ],
    )

    caught = _assert_rejected(
        _manifest_bytes(payload), rule, PanelRejectionReason.SELECTION_RULE_VIOLATION
    )

    assert caught.path.endswith("excluded_events[0].reason_code")


def test_latency_profiles_must_stay_separated() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule)
    payload["latency_profiles"] = {"p95": {"requested_latency_ms": 30_000, "measurement": None}}

    _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.SELECTION_RULE_VIOLATION)


def test_required_latency_profile_is_p95() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, required_latency_profile="zero")

    _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.SELECTION_RULE_VIOLATION)


def test_hold_seconds_must_be_positive() -> None:
    rule = _rule_bytes()
    payload = _manifest_payload(rule, hold_seconds=0)

    _assert_rejected(_manifest_bytes(payload), rule, PanelRejectionReason.INVALID_DOCUMENT)
