from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from esscher.contracts.strategy_release import (
    EXPECTED_LANE_BINDINGS,
    ArmRecord,
    PromotionReason,
    PromotionStatus,
    ReleaseLog,
    ReleaseLogReason,
    ReleaseLogRejected,
    RevocationReason,
    StrategyRelease,
    StrategyReleaseRejected,
    arm_record_bytes,
    current_semantic_ids,
    evaluate_release,
    parse_arm_record,
    parse_strategy_release,
    strategy_release_bytes,
)

NOW = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


def release(*, predecessor: str | None = None, version: int = 1) -> StrategyRelease:
    return StrategyRelease(
        release_id=f"ESSCHER_PAPER_{version}",
        release_version=version,
        created_at=NOW + timedelta(seconds=version),
        mode="PAPER",
        code_revision="a" * 40,
        build_artifact_sha256="b" * 64,
        evidence_report_sha256="c" * 64,
        security_report_sha256="d" * 64,
        evidence_qualified=True,
        security_passed=True,
        lane_bindings=EXPECTED_LANE_BINDINGS,
        supersedes_release_sha256=predecessor,
        **current_semantic_ids(),
    )


def arm(**changes: object) -> ArmRecord:
    values: dict[str, object] = {
        "arm_id": "PAPER_ARM_1",
        "release_sha256": release().release_sha256,
        "account_capability_id": "PAPER_ACCOUNT_CAPABILITY_1",
        "source_ids": ("ALPACA_MCP", "BENZINGA"),
        "starts_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "ledger_id": "PAPER_LEDGER_1",
        "process_id": "PAPER_PROCESS_1",
        "flatten_authority": True,
        "recovery_authority": True,
    }
    values.update(changes)
    return ArmRecord(**values)


def test_release_round_trip_tamper_and_duplicate_key_rejection() -> None:
    value = release()
    raw = strategy_release_bytes(value)
    assert parse_strategy_release(raw) == value
    changed = json.loads(raw)
    changed["code_revision"] = "f" * 40
    with pytest.raises(StrategyReleaseRejected, match="self-hash mismatch"):
        parse_strategy_release(json.dumps(changed, separators=(",", ":")).encode())
    duplicate = raw.replace(b'"release_id":', b'"release_id":"other","release_id":')
    with pytest.raises(StrategyReleaseRejected, match="strict UTF-8 JSON"):
        parse_strategy_release(duplicate)


def test_current_semantics_and_reports_promote() -> None:
    decision = evaluate_release(release())
    assert decision.status is PromotionStatus.PROMOTED
    assert decision.reason is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"reasoner_model": "other-model"}, PromotionReason.SEMANTIC_ID_MISMATCH),
        ({"evidence_qualified": False}, PromotionReason.EVIDENCE_UNQUALIFIED),
        ({"security_passed": False}, PromotionReason.SECURITY_FAILED),
    ],
)
def test_invalid_semantics_or_report_flags_reject(
    change: dict[str, object], reason: PromotionReason
) -> None:
    assert evaluate_release(replace(release(), **change)).reason is reason


def test_promote_replay_reopen_and_predecessor_rules(tmp_path) -> None:
    path = tmp_path / "release-log.sqlite3"
    first = release()
    with ReleaseLog(path) as log:
        assert log.promote(first, evaluate_release(first)) == first
        assert log.promote(first, evaluate_release(first)) == first
        assert log.load_exact(first.release_sha256) == first
    with ReleaseLog(path) as log:
        assert log.load_exact(first.release_sha256) == first
        second = release(version=2)
        with pytest.raises(ReleaseLogRejected) as missing:
            log.promote(second, evaluate_release(second))
        assert missing.value.reason is ReleaseLogReason.PREDECESSOR_MISMATCH
        second = replace(second, supersedes_release_sha256=first.release_sha256)
        log.promote(second, evaluate_release(second))
        with pytest.raises(ReleaseLogRejected) as rejected:
            log.load_exact(first.release_sha256)
        assert rejected.value.reason is ReleaseLogReason.SUPERSEDED
        assert log.load_exact(second.release_sha256) == second


def test_promote_serializes_racing_successors(tmp_path) -> None:
    path = tmp_path / "release-log.sqlite3"
    predecessor = release()
    with ReleaseLog(path) as log:
        log.promote(predecessor, evaluate_release(predecessor))
    candidates = (
        replace(release(version=2), supersedes_release_sha256=predecessor.release_sha256),
        replace(release(version=3), supersedes_release_sha256=predecessor.release_sha256),
    )
    start = threading.Barrier(len(candidates))
    results: list[StrategyRelease | ReleaseLogReason | BaseException | None] = [None, None]

    def promote(index: int, value: StrategyRelease) -> None:
        with ReleaseLog(path) as log:
            start.wait()
            try:
                results[index] = log.promote(value, evaluate_release(value))
            except ReleaseLogRejected as error:
                results[index] = error.reason
            except BaseException as error:
                results[index] = error

    threads = [
        threading.Thread(target=promote, args=(index, value))
        for index, value in enumerate(candidates)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(result in candidates for result in results) == 1
    assert ReleaseLogReason.PREDECESSOR_MISMATCH in results
    with ReleaseLog(path) as log:
        loaded = []
        for candidate in candidates:
            try:
                loaded.append(log.load_exact(candidate.release_sha256))
            except ReleaseLogRejected as error:
                assert error.reason is ReleaseLogReason.NOT_FOUND
    assert len(loaded) == 1


def test_non_string_code_revision_rejects_without_raw_exception() -> None:
    decision = evaluate_release(replace(release(), code_revision=1))
    assert decision.status is PromotionStatus.REJECTED
    assert decision.reason is PromotionReason.INVALID_REFERENCE


def test_load_exact_rejects_text_storage_as_invalid(tmp_path) -> None:
    path = tmp_path / "release-log.sqlite3"
    value = release()
    with ReleaseLog(path) as log:
        log.promote(value, evaluate_release(value))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER releases_no_update")
        connection.execute(
            "UPDATE strategy_releases SET release_json = ? WHERE release_sha256 = ?",
            (strategy_release_bytes(value).decode("ascii"), value.release_sha256),
        )
    with ReleaseLog(path) as log, pytest.raises(ReleaseLogRejected) as rejected:
        log.load_exact(value.release_sha256)
    assert rejected.value.reason is ReleaseLogReason.STORED_RELEASE_INVALID


def test_promote_replay_rejects_text_storage_as_invalid(tmp_path) -> None:
    path = tmp_path / "release-log.sqlite3"
    value = release()
    with ReleaseLog(path) as log:
        log.promote(value, evaluate_release(value))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER releases_no_update")
        connection.execute(
            "UPDATE strategy_releases SET release_json = ? WHERE release_sha256 = ?",
            (strategy_release_bytes(value).decode("ascii"), value.release_sha256),
        )
    with ReleaseLog(path) as log, pytest.raises(ReleaseLogRejected) as rejected:
        log.promote(value, evaluate_release(value))
    assert rejected.value.reason is ReleaseLogReason.STORED_RELEASE_INVALID


def test_revoke_is_idempotent_but_conflicts_and_blocks_load(tmp_path) -> None:
    value = release()
    with ReleaseLog(tmp_path / "release-log.sqlite3") as log:
        log.promote(value, evaluate_release(value))
        log.revoke(
            value.release_sha256,
            reason=RevocationReason.SECURITY,
            operator_id="OPERATOR_1",
            revoked_at=NOW,
        )
        log.revoke(
            value.release_sha256,
            reason=RevocationReason.SECURITY,
            operator_id="OPERATOR_1",
            revoked_at=NOW,
        )
        with pytest.raises(ReleaseLogRejected) as conflict:
            log.revoke(
                value.release_sha256,
                reason=RevocationReason.OPERATOR,
                operator_id="OPERATOR_1",
                revoked_at=NOW,
            )
        assert conflict.value.reason is ReleaseLogReason.REVOCATION_CONFLICT
        with pytest.raises(ReleaseLogRejected) as rejected:
            log.load_exact(value.release_sha256)
        assert rejected.value.reason is ReleaseLogReason.REVOKED


def test_release_log_has_no_latest_api() -> None:
    assert not hasattr(ReleaseLog, "latest")
    assert not hasattr(ReleaseLog, "load_latest")


def test_arm_round_trip_and_invalid_ttl_or_authority_reject() -> None:
    value = arm()
    assert parse_arm_record(arm_record_bytes(value)) == value
    with pytest.raises(StrategyReleaseRejected, match="expiry"):
        arm_record_bytes(arm(expires_at=NOW))
    with pytest.raises(StrategyReleaseRejected, match="flatten and recovery"):
        arm_record_bytes(arm(flatten_authority=False))
