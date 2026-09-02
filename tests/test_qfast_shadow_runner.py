"""Tests for the Q-FAST shadow runner, evidence validator, ledger, and adapter."""

from __future__ import annotations

import ast
import hashlib
import json
import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import ringdown_market.alpha.direction_receipts as receipts_module
import ringdown_market.alpha.evidence_validator as validator_module
import ringdown_market.alpha.fullstack_adapter as adapter_module
import ringdown_market.alpha.shadow_ledger as ledger_module
import ringdown_market.alpha.shadow_runner as runner_module
from ringdown_market.alpha.direction_receipts import (
    DirectionReceipt,
    DirectionReceiptRejected,
    direction_receipt_bytes,
    parse_direction_receipt,
)
from ringdown_market.alpha.evidence_validator import validate_evidence_configuration
from ringdown_market.alpha.fullstack_adapter import (
    PLAN_LABELS,
    ReplayPlanReason,
    build_shadow_replay_plan,
    parse_shadow_replay_plan,
    shadow_replay_plan_bytes,
)
from ringdown_market.alpha.models import Direction
from ringdown_market.alpha.shadow_ledger import (
    SHADOW_PNL_CLASS,
    ShadowLedgerRejection,
    append_shadow_episode_pair,
    record_shadow_run,
)
from ringdown_market.alpha.shadow_runner import (
    PromotionRecommendation,
    ShadowRunReason,
    run_shadow_evaluation,
)
from ringdown_market.contracts.execution_policy import RESEARCH_DECISION_PROTOCOL_SHA256
from ringdown_market.contracts.latency_profile import (
    packaged_latency_profile_bytes,
    validate_latency_profile,
)
from ringdown_market.panel.manifest import P0_CONTRACT_DEVELOPMENT_EVENT_IDS
from ringdown_market.risk.ledger import RiskLedger
from ringdown_market.strategy.policy import strategy_policy_sha256

CUTOFF = datetime(2026, 8, 28, 13, 35, tzinfo=UTC)
POLICY_SHA = strategy_policy_sha256()
SNAPSHOT_PROTOCOL_SHA = hashlib.sha256(b"test-snapshot-protocol-83").hexdigest()
EVENT_IDS = ("SYN-QFAST-1", "SYN-QFAST-2", "SYN-QFAST-3", "SYN-QFAST-4")
DIRECTIONS = (Direction.UP, Direction.DOWN, Direction.UNCERTAIN, Direction.UP)
FORBIDDEN_MODULES = (
    "aiohttp",
    "alpaca",
    "http",
    "httpx",
    "mcp",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "webbrowser",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _rule_bytes() -> bytes:
    payload = {
        "schema": "ringdown.qfast_panel_selection_rule",
        "schema_version": 1,
        "rule_id": "SHADOW_RUNNER_TEST_V1",
        "frozen_at": "2026-08-28T12:00:00Z",
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
        "required_excluded_event_ids": sorted(P0_CONTRACT_DEVELOPMENT_EVENT_IDS),
        "claim_boundary": [
            "POINT_IN_TIME_EVENT_PANEL_CANDIDATE",
            "NOT_ALPHA_EVIDENCE",
            "NO_OUTCOME_VALUES",
            "NO_BROKER_EXECUTION",
        ],
    }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _manifest_bytes(rule_sha: str, *, limitations: list[str] | None = None) -> bytes:
    qualifiers = [
        "NOT_HISTORICAL_DATA",
        "NOT_ALPHA_EVIDENCE",
        "NO_BROKER_EXECUTION",
    ]
    limits = limitations if limitations is not None else qualifiers
    payload = {
        "schema": "ringdown.qfast_panel_manifest",
        "schema_version": 1,
        "panel_id": "SHADOW_RUNNER_TEST_PANEL_V1",
        "frozen_at": "2026-08-28T12:00:00Z",
        "selection_rule_sha256": rule_sha,
        "strategy_policy_sha256": POLICY_SHA,
        "snapshot_protocol_sha256": SNAPSHOT_PROTOCOL_SHA,
        "decision_protocol_sha256": RESEARCH_DECISION_PROTOCOL_SHA256,
        "data_class": "SYNTHETIC_CONTRACT_FIXTURE",
        "data_qualifiers": list(qualifiers),
        "hold_seconds": 3600,
        "required_latency_profile": "p95",
        "latency_profiles": {
            "zero": {"requested_latency_ms": 0, "measurement": None},
            "p95": {
                "requested_latency_ms": 30000,
                "measurement": {
                    "kind": "SYNTHETIC",
                    "publisher": "shadow runner test fixture",
                    "measured_at": "2026-08-28T12:00:00Z",
                    "content_sha256": _sha(b"synthetic-measurement"),
                },
            },
        },
        "eligible_events": [
            {"event_id": event_id, "evidence_manifest_sha256": None} for event_id in EVENT_IDS
        ],
        "excluded_events": [],
        "limitations": list(limits),
    }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _profile_bytes(*, p95_ms: int = 30000) -> bytes:
    payload = json.loads(packaged_latency_profile_bytes().decode("utf-8"))
    payload["p95_latency_ms"] = p95_ms
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _event_list_bytes(events: tuple[str, ...] = EVENT_IDS) -> bytes:
    sectors = ("FINANCIALS", "TECHNOLOGY")
    payload = {
        "schema": "ringdown.qfast_panel_event_list",
        "schema_version": 1,
        "events": [
            {"event_id": event_id, "sector": sectors[index % 2], "ticker": f"SYN{index}"}
            for index, event_id in enumerate(events)
        ],
    }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _scores(index: int) -> tuple[float, float, float]:
    return (0.4 - 0.3 * index, 0.1 * (index - 1), 0.5 - 0.2 * index)


def _receipt(
    index: int, event_id: str, directions: tuple[Direction, ...] = DIRECTIONS
) -> DirectionReceipt:
    price_only, fundamental, numeric = _scores(index)
    return DirectionReceipt(
        event_id=event_id,
        candidate_id="SHADOW_CANDIDATE_V1",
        direction=directions[index],
        reason_codes=(),
        decision_cutoff_at=CUTOFF,
        latest_evidence_at=CUTOFF - timedelta(hours=1),
        feature_snapshot_at=CUTOFF,
        market_beta=0.1,
        sector_beta=0.2,
        price_only_score=price_only,
        fundamental_score=fundamental,
        numeric_score=numeric,
        producer_kind=receipts_module.ProducerKind.SYNTHETIC,
        route_sha256=None,
        prompt_sha256=None,
        model_config_sha256=None,
        classification=("NOT_ALPHA_EVIDENCE", "NOT_HISTORICAL_DATA"),
        produced_at=CUTOFF + timedelta(minutes=1),
        decision_artifact_sha256=_sha(f"decision-{event_id}".encode()),
        limitations=("NOT_HISTORICAL_DATA",),
    )


def _receipt_bytes_list(directions: tuple[Direction, ...] = DIRECTIONS) -> list[bytes]:
    return [
        direction_receipt_bytes(_receipt(index, event_id, directions))
        for index, event_id in enumerate(EVENT_IDS)
    ]


def _path_payload(index: int) -> list[dict[str, object]]:
    stamps = [
        CUTOFF,
        CUTOFF + timedelta(seconds=30),
        CUTOFF + timedelta(hours=1),
        CUTOFF + timedelta(hours=1, seconds=30),
        CUTOFF + timedelta(hours=2),
        CUTOFF + timedelta(hours=2, seconds=30),
        CUTOFF + timedelta(hours=3, seconds=30),
    ]
    base = 100.0 + index
    points = []
    for offset, stamp in enumerate(stamps):
        drift = 1.0 + 0.001 * offset * (1 if index % 2 == 0 else -1)
        points.append(
            {
                "at": _iso(stamp),
                "stock": base * drift,
                "market": 100.0 * drift,
                "sector": 100.0 * drift,
            }
        )
    return points


def _bundle_bytes(manifest_sha: str, directions: tuple[Direction, ...] = DIRECTIONS) -> bytes:
    events = []
    for index, event_id in enumerate(EVENT_IDS):
        price_only, fundamental, numeric = _scores(index)
        events.append(
            {
                "decision": {
                    "event_id": event_id,
                    "issuer": f"SYNQ{index}",
                    "decision_cutoff": _iso(CUTOFF),
                    "latest_evidence_at": _iso(CUTOFF - timedelta(hours=1)),
                    "feature_snapshot_at": _iso(CUTOFF),
                    "opening_return": 0.0,
                    "market_opening_return": 0.0,
                    "sector_opening_return": 0.0,
                    "market_beta": 0.1,
                    "sector_beta": 0.2,
                    "price_only_score": price_only,
                    "fundamental_score": fundamental,
                    "numeric_score": numeric,
                    "candidate_signal": directions[index].value,
                },
                "path": _path_payload(index),
            }
        )
    payload = {
        "schema": "ringdown.qfast_panel_bundle",
        "schema_version": 1,
        "fixture_class": "SYNTHETIC_CONTRACT_FIXTURE",
        "limitations": ["NOT_HISTORICAL_DATA", "NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION"],
        "panel_manifest_sha256": manifest_sha,
        "events": events,
    }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


@pytest.fixture()
def config(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    import ringdown_market.panel.manifest as panel_manifest

    rule = _rule_bytes()
    manifest = _manifest_bytes(_sha(rule))
    monkeypatch.setattr(panel_manifest, "KNOWN_STRATEGY_POLICY_SHA256", frozenset({POLICY_SHA}))
    monkeypatch.setattr(
        panel_manifest, "KNOWN_SNAPSHOT_PROTOCOL_SHA256", frozenset({SNAPSHOT_PROTOCOL_SHA})
    )
    return {
        "rule": rule,
        "manifest": manifest,
        "bundle": _bundle_bytes(_sha(manifest)),
        "profile": _profile_bytes(),
        "event_list": _event_list_bytes(),
    }


def _run(config: dict[str, bytes], **overrides: object):
    kwargs: dict[str, object] = {
        "manifest_bytes": config["manifest"],
        "selection_rule_bytes": config["rule"],
        "bundle_bytes": config["bundle"],
        "receipt_bytes": _receipt_bytes_list(),
        "latency_profile_bytes": config["profile"],
        "expected_policy_sha256": POLICY_SHA,
        "event_list_bytes": config["event_list"],
    }
    kwargs.update(overrides)
    return run_shadow_evaluation(**kwargs)  # type: ignore[arg-type]


def test_synthetic_configuration_validates_and_runs(config: dict[str, bytes]) -> None:
    result = _run(config)

    assert result.accepted is True
    assert result.claim == "NOT_ALPHA_EVIDENCE"
    assert result.classification == "SYNTHETIC_RECEIPTS"
    assert result.validation.event_count == 4
    assert result.validation.sector_count == 2
    assert result.validation.source_status == "NOT_SUPPLIED"
    assert result.validation.rights_status == "RIGHTS_DECLARED"
    assert result.promotion_recommendation is PromotionRecommendation.REJECT_PROMOTION
    assert "latency_profile_not_measured" in result.promotion_reasons
    assert "synthetic_receipts_not_candidate_evidence" in result.promotion_reasons
    assert result.gate is not None
    assert set(result.reports) == {"zero", "p95"}


def test_reruns_are_byte_identical(config: dict[str, bytes]) -> None:
    first = _run(config)
    second = _run(config)

    assert first.bytes == second.bytes
    assert first.sha256 == second.sha256


def test_zero_and_p95_arms_share_the_same_denominator(config: dict[str, bytes]) -> None:
    result = _run(config)

    zero = result.reports["zero"]
    p95 = result.reports["p95"]
    assert zero.event_count == p95.event_count == 4
    for method in zero.metrics:
        assert zero.metrics[method].eligible_events == p95.metrics[method].eligible_events
        assert zero.metrics[method].admitted_events == p95.metrics[method].admitted_events


def test_abstentions_keep_zero_signed_residual(config: dict[str, bytes]) -> None:
    result = _run(config)

    abstained = result.evaluations["SYN-QFAST-3"]
    assert abstained.admitted is False
    assert abstained.signed_residual == 0.0
    admitted = result.evaluations["SYN-QFAST-1"]
    assert admitted.admitted is True


def test_baselines_derive_only_from_ex_ante_scores() -> None:
    receipt = _receipt(3, EVENT_IDS[3])

    directions = runner_module._method_directions(receipt)

    assert directions[runner_module.CANDIDATE_METHOD] is receipt.direction
    assert directions[runner_module.PRICE_ONLY_METHOD] is Direction.DOWN
    assert directions[runner_module.NUMERIC_METHOD] is Direction.DOWN
    assert directions[runner_module.CASH_METHOD] is Direction.UNCERTAIN


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"receipt_bytes": _receipt_bytes_list()[1:]}, "DECISION_SET_INCOMPLETE"),
        ({"expected_policy_sha256": _sha(b"other")}, "POLICY_MISMATCH"),
        ({"latency_profile_bytes": _profile_bytes(p95_ms=31000)}, "LATENCY_PROFILE_MISMATCH"),
        ({"event_list_bytes": _event_list_bytes(EVENT_IDS[:3])}, "EVENT_COUNT_MISMATCH"),
    ],
)
def test_validator_rejects_before_evaluation(
    config: dict[str, bytes], override: dict[str, object], reason: str
) -> None:
    result = _run(config, **override)

    assert result.accepted is False
    assert reason in result.rejection_reasons
    assert result.evaluations == {}
    assert result.reports == {}


def test_manifest_tamper_fails_closed(config: dict[str, bytes]) -> None:
    tampered = bytearray(config["manifest"])
    tampered[10] = ord("X")
    result = _run(config, manifest_bytes=bytes(tampered))

    assert result.accepted is False
    assert "MANIFEST_INVALID" in result.rejection_reasons


def test_invalid_universe_fails_closed(config: dict[str, bytes]) -> None:
    result = _run(config, universe_manifest_bytes=(b"{not a manifest",))

    assert result.accepted is False
    assert "SOURCE_STATUS_UNVERIFIED" in result.rejection_reasons
    assert result.validation.source_status == "NOT_SUPPLIED"


def test_receipt_bundle_mismatch_is_rejected(config: dict[str, bytes]) -> None:
    flipped = replace(_receipt(0, EVENT_IDS[0]), direction=Direction.DOWN)
    raws = [direction_receipt_bytes(flipped), *_receipt_bytes_list()[1:]]
    result = _run(config, receipt_bytes=raws)

    assert result.accepted is False
    assert ShadowRunReason.RECEIPT_BUNDLE_MISMATCH.value in result.rejection_reasons


def test_synthetic_receipt_requires_not_alpha_label() -> None:
    receipt = _receipt(0, EVENT_IDS[0])
    with pytest.raises(ValueError):
        replace(receipt, classification=("NOT_HISTORICAL_DATA",))


def test_route_bound_receipt_requires_hashes() -> None:
    receipt = _receipt(0, EVENT_IDS[0])
    with pytest.raises(ValueError):
        replace(receipt, producer_kind=receipts_module.ProducerKind.ROUTE_BOUND)


def test_receipt_parse_rejects_unknown_fields() -> None:
    payload = json.loads(direction_receipt_bytes(_receipt(0, EVENT_IDS[0])))
    payload["outcome_hint"] = 1.0
    with pytest.raises(DirectionReceiptRejected):
        parse_direction_receipt(json.dumps(payload).encode("utf-8"))


def test_shadow_ledger_append_restart_and_idempotency(
    config: dict[str, bytes], tmp_path: Path
) -> None:
    result = _run(config)
    ledger = RiskLedger(tmp_path / "shadow.db")
    costs = {event_id: Decimal("0.5") for event_id in EVENT_IDS}

    appended = record_shadow_run(
        ledger,
        result,
        candidate_id="SHADOW_CANDIDATE_V1",
        policy_sha256=POLICY_SHA,
        evidence_sha256=result.validation.manifest_sha256,
        feature_sha256=result.validation.selection_rule_sha256,
        snapshot_sha256=result.sha256,
        costs=costs,
    )
    replay = record_shadow_run(
        ledger,
        result,
        candidate_id="SHADOW_CANDIDATE_V1",
        policy_sha256=POLICY_SHA,
        evidence_sha256=result.validation.manifest_sha256,
        feature_sha256=result.validation.selection_rule_sha256,
        snapshot_sha256=result.sha256,
        costs=costs,
    )
    ledger.close()

    reopened = RiskLedger(tmp_path / "shadow.db")
    decisions = reopened.decision_episode_rows()
    outcomes = reopened.outcome_episode_rows()
    reopened.close()

    assert appended == 4
    assert replay == 0
    assert len(decisions) == 4
    assert len(outcomes) == 4
    assert all(row["pnl_classification"] == SHADOW_PNL_CLASS for row in outcomes)
    assert all(row["final_flat"] in (1, True) for row in outcomes)


def test_shadow_ledger_cutoff_violation_raises(config: dict[str, bytes]) -> None:
    result = _run(config)
    receipt = result.receipts[0]
    evaluation = result.evaluations[receipt.event_id]
    early = replace(evaluation, entry_at=receipt.decision_cutoff_at - timedelta(seconds=1))
    ledger_args = {
        "receipt": receipt,
        "evaluation": early,
        "symbol": "SYN0",
        "candidate_id": "SHADOW_CANDIDATE_V1",
        "policy_sha256": POLICY_SHA,
        "evidence_sha256": result.validation.manifest_sha256,
        "feature_sha256": result.validation.selection_rule_sha256,
        "snapshot_sha256": result.sha256,
        "costs": Decimal("0"),
    }
    import ringdown_market.risk.ledger as _unused  # noqa: F401  (documentation anchor)

    with pytest.raises(ShadowLedgerRejection):
        append_shadow_episode_pair(_LedgerStub(), **ledger_args)


class _LedgerStub:
    def append_decision_episode(self, **kwargs: object) -> bool:
        raise AssertionError("ledger must not be touched on cutoff violation")

    def append_outcome_episode(self, **kwargs: object) -> bool:
        raise AssertionError("ledger must not be touched on cutoff violation")


def test_replay_plan_roundtrip_and_flatness(config: dict[str, bytes]) -> None:
    result = _run(config)
    plan = build_shadow_replay_plan(
        result,
        candidate_id="SHADOW_CANDIDATE_V1",
        policy_sha256=POLICY_SHA,
        evidence_sha256=result.validation.manifest_sha256,
        feature_sha256=result.validation.selection_rule_sha256,
        snapshot_sha256=result.sha256,
        costs={event_id: Decimal("0.25") for event_id in EVENT_IDS},
    )
    raw = shadow_replay_plan_bytes(plan)

    parsed = parse_shadow_replay_plan(raw)

    assert parsed.labels == PLAN_LABELS
    assert len(parsed.events) == 4
    assert all(event.final_flat is True for event in parsed.events)
    assert parsed.report_sha256 == result.sha256

    tampered = json.loads(raw)
    tampered["events"][0]["final_flat"] = False
    with pytest.raises(adapter_module.ReplayPlanRejected) as caught:
        parse_shadow_replay_plan(json.dumps(tampered).encode("utf-8"))
    assert caught.value.reason is ReplayPlanReason.NOT_TERMINAL_FLAT


def test_rejected_runs_produce_no_replay_plan(config: dict[str, bytes]) -> None:
    result = _run(config, receipt_bytes=_receipt_bytes_list()[1:])

    with pytest.raises(adapter_module.ReplayPlanRejected):
        build_shadow_replay_plan(
            result,
            candidate_id="SHADOW_CANDIDATE_V1",
            policy_sha256=POLICY_SHA,
            evidence_sha256=result.validation.manifest_sha256,
            feature_sha256=result.validation.selection_rule_sha256,
            snapshot_sha256=result.sha256,
            costs={},
        )


def test_new_modules_import_no_network_or_broker_capability() -> None:
    paths = [
        Path(module.__file__)
        for module in (
            receipts_module,
            validator_module,
            runner_module,
            ledger_module,
            adapter_module,
        )
    ]
    paths.append(Path("scripts/run_qfast_shadow_evidence.py"))
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                root = name.split(".", 1)[0]
                assert root not in FORBIDDEN_MODULES, f"{path.name} imports {name}"


def test_full_run_performs_no_network(
    config: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted during shadow evaluation")

    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)

    result = _run(config)

    assert result.accepted is True


def test_validator_reports_statuses_without_universe(config: dict[str, bytes]) -> None:
    report = validate_evidence_configuration(
        manifest_bytes=config["manifest"],
        selection_rule_bytes=config["rule"],
        receipt_bytes=_receipt_bytes_list(),
        latency_profile_bytes=config["profile"],
        expected_policy_sha256=POLICY_SHA,
        event_list_bytes=config["event_list"],
    )

    assert report.accepted is True
    assert report.source_status == "NOT_SUPPLIED"
    assert report.clock_status == "NOT_SUPPLIED"
    assert report.latency_profile_kind == "PREREGISTERED"


def test_latency_profile_validation_still_standalone(config: dict[str, bytes]) -> None:
    profile = validate_latency_profile(config["profile"])

    assert profile.p95_latency_ms == 30000
    assert profile.kind.value == "PREREGISTERED"


OPPOSITE_DIRECTIONS = (Direction.DOWN, Direction.UP, Direction.UNCERTAIN, Direction.UP)


def test_one_terminal_disposition_per_eligible_event(
    config: dict[str, bytes], tmp_path: Path
) -> None:
    result = _run(config)
    ledger = RiskLedger(tmp_path / "disposition.db")
    record_shadow_run(
        ledger,
        result,
        candidate_id="SHADOW_CANDIDATE_V1",
        policy_sha256=POLICY_SHA,
        evidence_sha256=result.validation.manifest_sha256,
        feature_sha256=result.validation.selection_rule_sha256,
        snapshot_sha256=result.sha256,
        costs={event_id: Decimal("0") for event_id in EVENT_IDS},
    )
    decisions = ledger.decision_episode_rows()
    outcomes = ledger.outcome_episode_rows()
    truths = ledger.broker_truth_snapshot_rows()
    ledger.close()

    assert sorted(result.evaluations) == sorted(EVENT_IDS)
    assert sorted(row["event_id"] for row in decisions) == sorted(EVENT_IDS)
    assert len({row["event_id"] for row in decisions}) == len(EVENT_IDS)
    abstained = [row for row in decisions if row["event_id"] == "SYN-QFAST-3"]
    assert len(abstained) == 1
    assert abstained[0]["direction"] == "UNCERTAIN"
    assert abstained[0]["disposition"] == "SHADOW_ABSTAINED"
    assert truths == []
    assert len(outcomes) == len(EVENT_IDS)


def test_abstention_and_cash_arms_are_identical_across_latencies(
    config: dict[str, bytes],
) -> None:
    result = _run(config)

    zero = result.reports["zero"]
    p95 = result.reports["p95"]
    assert zero.metrics[runner_module.CASH_METHOD].mean_all == 0.0
    assert p95.metrics[runner_module.CASH_METHOD].mean_all == 0.0
    assert zero.metrics[runner_module.CASH_METHOD].admitted_events == 0
    assert p95.metrics[runner_module.CASH_METHOD].admitted_events == 0
    assert result.evaluations["SYN-QFAST-3"].signed_residual == 0.0


def test_baselines_are_immune_to_candidate_direction_flips(
    config: dict[str, bytes],
) -> None:
    base = _run(config)
    flipped = _run(
        config,
        receipt_bytes=_receipt_bytes_list(OPPOSITE_DIRECTIONS),
        bundle_bytes=_bundle_bytes(_sha(config["manifest"]), OPPOSITE_DIRECTIONS),
    )

    for method in (runner_module.PRICE_ONLY_METHOD, runner_module.NUMERIC_METHOD):
        assert (
            base.reports["p95"].metrics[method].mean_all
            == flipped.reports["p95"].metrics[method].mean_all
        )
    assert (
        base.reports["p95"].metrics[runner_module.CANDIDATE_METHOD].mean_all
        != flipped.reports["p95"].metrics[runner_module.CANDIDATE_METHOD].mean_all
    )


def test_weak_candidate_is_rejected_and_gate_forces_shadow_only(
    config: dict[str, bytes],
) -> None:
    result = _run(
        config,
        receipt_bytes=_receipt_bytes_list(OPPOSITE_DIRECTIONS),
        bundle_bytes=_bundle_bytes(_sha(config["manifest"]), OPPOSITE_DIRECTIONS),
    )

    assert result.accepted is True
    assert result.reports["p95"].status.value == "REJECTED"
    assert result.gate is not None
    assert result.gate.status.value == "SHADOW_ONLY"
    assert "latency_gate_SHADOW_ONLY" in result.promotion_reasons
    assert result.claim == "NOT_ALPHA_EVIDENCE"
    assert result.promotion_recommendation is PromotionRecommendation.REJECT_PROMOTION


def test_invalid_windows_fail_closed(config: dict[str, bytes]) -> None:
    result = _run(config, window_bytes=(b"{not a window",))

    assert result.accepted is False
    assert "CLOCK_STATUS_INVALID" in result.rejection_reasons
    assert result.validation.clock_status == "NOT_SUPPLIED"


def test_receipt_missing_feature_field_rejected_before_evaluation(
    config: dict[str, bytes],
) -> None:
    payload = json.loads(direction_receipt_bytes(_receipt(0, EVENT_IDS[0])))
    payload.pop("numeric_score")
    raws = [json.dumps(payload).encode("utf-8"), *_receipt_bytes_list()[1:]]
    result = _run(config, receipt_bytes=raws)

    assert result.accepted is False
    assert "RECEIPT_INVALID" in result.rejection_reasons
    assert result.evaluations == {}


def test_fake_replay_integration_scenario_reaches_terminal_flatness(
    config: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted during fake replay")

    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)

    result = _run(config)
    plan_payload = build_shadow_replay_plan(
        result,
        candidate_id="SHADOW_CANDIDATE_V1",
        policy_sha256=POLICY_SHA,
        evidence_sha256=result.validation.manifest_sha256,
        feature_sha256=result.validation.selection_rule_sha256,
        snapshot_sha256=result.sha256,
        costs={event_id: Decimal("0.10") for event_id in EVENT_IDS},
    )
    plan = parse_shadow_replay_plan(shadow_replay_plan_bytes(plan_payload))

    replayed = 0
    for event in plan.events:
        entry = datetime.fromisoformat(event.entry_at.replace("Z", "+00:00"))
        exit_at = datetime.fromisoformat(event.exit_at.replace("Z", "+00:00"))
        assert entry < exit_at
        assert event.lifecycle_outcome == ledger_module.SHADOW_LIFECYCLE
        assert event.pnl_classification == ledger_module.SHADOW_PNL_CLASS
        assert event.final_flat is True
        assert event.direction in {"UP", "DOWN", "UNCERTAIN"}
        replayed += 1

    assert replayed == len(EVENT_IDS)


def test_offline_command_writes_canonical_artifacts(
    config: dict[str, bytes], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.util
    import sys

    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()
    for index, event_id in enumerate(EVENT_IDS):
        (receipts_dir / f"{event_id}.json").write_bytes(
            direction_receipt_bytes(_receipt(index, event_id))
        )
    (tmp_path / "rule.json").write_bytes(config["rule"])
    (tmp_path / "manifest.json").write_bytes(config["manifest"])
    (tmp_path / "bundle.json").write_bytes(config["bundle"])
    (tmp_path / "event-list.json").write_bytes(config["event_list"])
    (tmp_path / "profile.json").write_bytes(config["profile"])
    out = tmp_path / "out"

    script = Path("scripts/run_qfast_shadow_evidence.py").resolve()
    spec = importlib.util.spec_from_file_location("run_qfast_shadow_evidence", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_qfast_shadow_evidence"] = module
    spec.loader.exec_module(module)
    code = module.main(
        [
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--selection-rule",
            str(tmp_path / "rule.json"),
            "--bundle",
            str(tmp_path / "bundle.json"),
            "--receipts-dir",
            str(receipts_dir),
            "--event-list",
            str(tmp_path / "event-list.json"),
            "--latency-profile",
            str(tmp_path / "profile.json"),
            "--out",
            str(out),
        ]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert (out / "qfast_shadow_report.json").is_file()
    assert (out / "evidence_validation.json").is_file()
    assert (out / "shadow_replay_plan.json").is_file()
    assert "accepted=True" in captured.out
    assert "report_sha256=" in captured.out
