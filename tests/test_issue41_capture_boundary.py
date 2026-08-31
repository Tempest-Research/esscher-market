"""Adversarial regressions for the clean issue-41 capture boundary."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from ringdown_market.contracts.source_matrix import (
    CONDITIONS,
    HumanApprovalDecision,
    MatrixReason,
    MatrixRejected,
    parse_source_matrix,
    source_matrix_bytes,
)
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.rights_gate import (
    EARNINGS_CANDIDATE_ID,
    MACRO_CANDIDATE_ID,
    evaluate_capture_rights,
)
from ringdown_market.strategy.policy import load_strategy_policy

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "sourcedata" / "synthetic_snapshot_inputs_v1.json"
FULL_DEV_CONDITIONS = frozenset(
    {
        "HUMAN_VERIFIED_CAPTURE",
        "PER_RECORD_PRIMARY_PROVENANCE",
        "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
    }
)


def _payload() -> dict:
    return json.loads(source_matrix_bytes().decode("utf-8"))


def _bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")


def _source(payload: dict, source_id: str) -> dict:
    for source in payload["sources"]:
        if source["source_id"] == source_id:
            return source
    raise AssertionError(f"missing frozen source {source_id}")


def _record_valid_paid_plan_approval(payload: dict) -> dict:
    source = _source(payload, "LICENSED_PIT_CONSENSUS_VENDORS")
    source["verdict"] = "FEASIBLE_WITH_LIMITATIONS"
    source["entitlement"] = "VERIFIED_LICENSED"
    source["retention_redistribution"] = "RETENTION_ONLY_HASH_RECEIPTS"
    source["human_approval"] = {
        "approved_by": "BEN_APPROVER",
        "approved_at": payload["decided_at"],
        "decision": "APPROVED",
    }
    return source


@pytest.mark.parametrize("timestamp", ["2026-07-01T12:00:00", "2026-07-01T13:00:00+01:00"])
def test_matrix_requires_explicit_zero_offset_utc_decision_time(timestamp: str) -> None:
    payload = _payload()
    payload["decided_at"] = timestamp

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_bytes(payload))

    assert error.value.reason is MatrixReason.MALFORMED_VALUE
    assert error.value.path == "source_matrix.decided_at"


@pytest.mark.parametrize("timestamp", ["2026-07-01T12:00:00", "2026-07-01T13:00:00+01:00"])
def test_matrix_requires_explicit_zero_offset_utc_evidence_time(timestamp: str) -> None:
    payload = _payload()
    _source(payload, "BLS_RELEASE_SCHEDULE")["evidence"][0]["retrieved_at"] = timestamp

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_bytes(payload))

    assert error.value.reason is MatrixReason.MALFORMED_VALUE
    assert error.value.path.endswith("evidence[0].retrieved_at")


def test_paid_plan_approval_uses_exact_enum_and_recorded_identity() -> None:
    payload = _payload()
    _record_valid_paid_plan_approval(payload)

    matrix = parse_source_matrix(_bytes(payload))
    source = matrix.sources_by_id()["LICENSED_PIT_CONSENSUS_VENDORS"]
    assert source.human_approval is not None
    assert source.human_approval.decision is HumanApprovalDecision.APPROVED
    assert source.human_approval.approved_by == "BEN_APPROVER"


@pytest.mark.parametrize(
    ("field", "value"),
    [("decision", "DENIED"), ("approved_by", "ben")],
)
def test_paid_plan_approval_rejects_noncanonical_state_or_identity(field: str, value: str) -> None:
    payload = _payload()
    approval = _record_valid_paid_plan_approval(payload)["human_approval"]
    approval[field] = value

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_bytes(payload))

    assert error.value.reason is MatrixReason.MALFORMED_VALUE


def test_paid_plan_approval_cannot_postdate_matrix_decision() -> None:
    payload = _payload()
    approval = _record_valid_paid_plan_approval(payload)["human_approval"]
    approval["approved_at"] = "2099-01-01T00:00:00Z"

    with pytest.raises(MatrixRejected) as error:
        parse_source_matrix(_bytes(payload))

    assert error.value.reason is MatrixReason.PAID_PLAN_UNAPPROVED


def test_alternate_matrix_bytes_are_rejected_before_source_selection() -> None:
    alternate_bytes = b"\n" + source_matrix_bytes()

    with pytest.raises(CollectorRejected) as error:
        evaluate_capture_rights(
            candidate_id=EARNINGS_CANDIDATE_ID,
            matrix_bytes=alternate_bytes,
            satisfied_conditions=FULL_DEV_CONDITIONS,
        )

    assert error.value.reason is CollectorReason.SOURCE_MATRIX_DRIFT


def test_rights_gate_uses_the_exact_selected_candidate_source_classes() -> None:
    policy = load_strategy_policy()
    macro_required = tuple(
        policy.candidate(MACRO_CANDIDATE_ID)["evidence"]["required_source_classes"]
    )
    earnings_required = tuple(
        policy.candidate(EARNINGS_CANDIDATE_ID)["evidence"]["required_source_classes"]
    )

    report = evaluate_capture_rights(
        candidate_id=MACRO_CANDIDATE_ID,
        satisfied_conditions=frozenset(CONDITIONS),
    )

    assert tuple(decision.source_class for decision in report.decisions) == macro_required
    assert macro_required != earnings_required


def test_packaged_matrix_is_rebound_after_an_accepted_policy_change(monkeypatch) -> None:
    import ringdown_market.sourcedata.rights_gate as rights_gate

    amended_policy_bytes = b"accepted-policy-with-a-new-registered-digest"
    monkeypatch.setattr(rights_gate, "strategy_policy_bytes", lambda: amended_policy_bytes)
    monkeypatch.setattr(
        rights_gate,
        "ACCEPTED_EVENT_POLICY_V1_SHA256",
        hashlib.sha256(amended_policy_bytes).hexdigest(),
    )
    # The real parser would have been upgraded alongside the accepted policy.
    # Keep candidate extraction stable so this checks the mandatory matrix rebind.
    monkeypatch.setattr(rights_gate, "parse_strategy_policy", lambda _: load_strategy_policy())

    with pytest.raises(CollectorRejected) as error:
        rights_gate.evaluate_capture_rights(
            candidate_id=EARNINGS_CANDIDATE_ID,
            satisfied_conditions=FULL_DEV_CONDITIONS,
        )

    assert error.value.reason is CollectorReason.SOURCE_MATRIX_DRIFT


def _capture_args(fixture_path: Path, output_dir: Path) -> list[str]:
    return [
        "--event-id",
        "KR-2026Q2-EARNINGS",
        "--fixture",
        str(fixture_path),
        "--capture-at",
        "2026-09-11T13:35:10Z",
        "--output-dir",
        str(output_dir),
        "--condition-satisfied",
        "HUMAN_VERIFIED_CAPTURE",
        "--condition-satisfied",
        "PER_RECORD_PRIMARY_PROVENANCE",
        "--condition-satisfied",
        "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
    ]


def test_capture_command_requires_an_explicit_fixture(tmp_path: Path, monkeypatch) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--event-id",
                "KR-2026Q2-EARNINGS",
                "--capture-at",
                "2026-09-11T13:35:10Z",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert error.value.code == 2


@pytest.mark.parametrize(
    "value",
    (
        "2026-09-11T13:35:10",
        "2026-09-11T13:35:10+01:00",
        "2026-09-11T13:35:10-00:00",
    ),
)
def test_capture_clock_requires_explicit_zero_offset_utc(value: str) -> None:
    from ringdown_market.sourcedata.capture import _capture_timestamp

    with pytest.raises(CollectorRejected) as error:
        _capture_timestamp(value)
    assert error.value.reason == CollectorReason.UNSUPPORTED_INPUT


def test_capture_command_threads_the_explicit_fixture_to_adapters(
    tmp_path: Path, monkeypatch
) -> None:
    from ringdown_market.sourcedata.capture import main

    fixture = copy.deepcopy(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
    fixture["security_master"]["prior_regular_close"] = "0.01"
    explicit_fixture = tmp_path / "explicit-fixture.json"
    explicit_fixture.write_text(json.dumps(fixture), encoding="utf-8")

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    exit_code = main(_capture_args(explicit_fixture, tmp_path))

    assert exit_code == 2
    assert not (tmp_path / "strategy_snapshot.json").exists()
