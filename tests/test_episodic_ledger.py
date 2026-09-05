"""Contract tests for append-only, cutoff-safe episodic PAPER memory."""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from esscher.autonomy.episodes import (
    BrokerTruthSnapshot,
    DecisionEpisode,
    EpisodeRejected,
    EpisodicReason,
    OutcomeEpisode,
    append_broker_truth_snapshot,
    append_decision_episode,
    append_outcome_episode,
    broker_truth_snapshot_bytes,
    build_episodic_summary,
    decision_episode_bytes,
    decision_episode_sha256,
    episodic_summary_bytes,
    outcome_episode_sha256,
    parse_broker_truth_snapshot,
    parse_decision_episode,
    parse_episodic_summary,
)
from esscher.risk import ledger as ledger_module
from esscher.risk.ledger import SCHEMA_VERSION, RiskLedger

H = "a" * 64
H2 = "b" * 64
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _decision(**changes: object) -> DecisionEpisode:
    value = DecisionEpisode(
        episode_id="decision-1",
        event_id="event-1",
        candidate_id="candidate-1",
        symbol="KR",
        occurred_at=NOW,
        decision_cutoff_at=NOW + timedelta(minutes=1),
        source_policy_sha256=H,
        source_evidence_sha256=H,
        source_feature_sha256=H,
        source_snapshot_sha256=H,
        prior_summary_sha256=H,
        route_sha256=H,
        prompt_sha256=H,
        model_config_sha256=H,
        exchange_sha256=H,
        decision_sha256=H,
        disposition="ACCEPTED",
        direction="UP",
        created_at=NOW + timedelta(seconds=1),
        supersedes_episode_id=None,
        supersedes_episode_sha256=None,
    )
    return replace(value, **changes)


def _outcome(**changes: object) -> OutcomeEpisode:
    value = OutcomeEpisode(
        outcome_id="outcome-1",
        decision_episode_id="decision-1",
        event_id="event-1",
        open_permit_id="permit-open-1",
        close_permit_id="permit-close-1",
        open_order_id="order-open-1",
        close_order_id="order-close-1",
        terminal_at=NOW + timedelta(minutes=2),
        observed_at=NOW + timedelta(minutes=3),
        lifecycle_outcome="CLOSED",
        pnl_classification="REALIZED",
        gross_pnl="5",
        net_pnl="4",
        reconciliation_sha256=H,
        final_flat=True,
        supersedes_outcome_id=None,
        supersedes_outcome_sha256=None,
        created_at=NOW + timedelta(minutes=4),
    )
    return replace(value, **changes)


def _snapshot(**changes: object) -> BrokerTruthSnapshot:
    value = BrokerTruthSnapshot(
        snapshot_id="snapshot-1",
        observed_at=NOW + timedelta(minutes=3),
        account_sha256=H,
        orders_sha256=H,
        positions_sha256=H,
        equity="1000",
        open_exposure="0",
        is_flat=True,
        created_at=NOW + timedelta(minutes=4),
        supersedes_snapshot_id=None,
        supersedes_snapshot_sha256=None,
    )
    return replace(value, **changes)


def test_v5_migration_creates_append_only_episode_and_v2_reservation_tables(tmp_path) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    try:
        tables = {
            str(row["name"])
            for row in ledger._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "decision_episodes",
            "outcome_episodes",
            "broker_truth_snapshots",
            "v2_reservations",
        } <= tables
        assert SCHEMA_VERSION == 5
    finally:
        ledger.close()


def test_public_episodic_module_is_available() -> None:
    episodes = importlib.import_module("esscher.autonomy.episodes")
    assert callable(episodes.build_episodic_summary)


def test_clean_v4_database_migrates_to_v5_without_rewriting_legacy_rows(tmp_path) -> None:
    path = tmp_path / "legacy-v4.sqlite3"
    connection = sqlite3.connect(path)
    try:
        for version in (1, 2, 3, 4):
            connection.executescript(ledger_module._MIGRATIONS[version])
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, "2026-09-01T12:00:00Z"),
            )
        connection.execute(
            "INSERT INTO positions (underlying, quantity, observed_at) VALUES (?, ?, ?)",
            ("KR", "1", "2026-09-01T12:00:00Z"),
        )
        connection.execute(
            (
                "INSERT INTO reconciliations "
                "(reconciliation_id, result, detail, paper_pnl, shadow_pnl, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            ("reconciliation-1", "MATCHED", "legacy", "1.25", "1.25", "2026-09-01T12:00:00Z"),
        )
        connection.execute(
            (
                "INSERT INTO candidates "
                "(event_id, candidate_id, policy_sha256, decision_sha256, expression_sha256, "
                "evidence_mode, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            ("event-1", "candidate-1", H, H, H, "OBSERVED", "2026-09-01T12:00:00Z"),
        )
        connection.execute(
            (
                "INSERT INTO reservations "
                "(reservation_id, event_id, amount, state, created_at, updated_at, underlying) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                "reservation-1",
                "event-1",
                "10",
                "RESERVED",
                "2026-09-01T12:00:00Z",
                "2026-09-01T12:00:00Z",
                "KR",
            ),
        )
        connection.execute(
            (
                "INSERT INTO passport_events "
                "(event_type, payload, prev_sha256, event_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?)"
            ),
            ("LEGACY", "{}", "0" * 64, H, "2026-09-01T12:00:00Z"),
        )
        connection.execute(
            (
                "INSERT INTO decision_episodes "
                "(episode_id, event_id, candidate_id, symbol, occurred_at, decision_cutoff_at, "
                "source_policy_sha256, source_evidence_sha256, source_feature_sha256, "
                "source_snapshot_sha256, prior_summary_sha256, route_sha256, prompt_sha256, "
                "model_config_sha256, exchange_sha256, decision_sha256, disposition, direction, "
                "created_at, supersedes_episode_id, supersedes_episode_sha256, "
                "payload_sha256, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                "episode-1",
                "event-1",
                "candidate-1",
                "KR",
                "2026-09-01T12:00:00Z",
                "2026-09-01T12:00:00Z",
                H,
                H,
                H,
                H,
                H,
                H,
                H,
                H,
                H,
                H,
                "ABSTAIN",
                "NONE",
                "2026-09-01T12:00:00Z",
                None,
                None,
                H,
                b"{}",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    ledger = RiskLedger(path)
    try:
        assert ledger.schema_version() == 5
        assert tuple(
            ledger._conn.execute(
                "SELECT underlying, quantity, observed_at FROM positions WHERE underlying='KR'"
            ).fetchone()
        ) == ("KR", "1", "2026-09-01T12:00:00Z")
        assert tuple(
            ledger._conn.execute(
                "SELECT result, detail, paper_pnl, shadow_pnl FROM reconciliations "
                "WHERE reconciliation_id='reconciliation-1'"
            ).fetchone()
        ) == ("MATCHED", "legacy", "1.25", "1.25")
        assert tuple(
            ledger._conn.execute(
                "SELECT candidate_id, policy_sha256 FROM candidates WHERE event_id='event-1'"
            ).fetchone()
        ) == ("candidate-1", H)
        assert tuple(
            ledger._conn.execute(
                "SELECT amount, state, underlying FROM reservations WHERE event_id='event-1'"
            ).fetchone()
        ) == ("10", "RESERVED", "KR")
        assert tuple(
            ledger._conn.execute(
                "SELECT event_type, payload FROM passport_events WHERE event_type='LEGACY'"
            ).fetchone()
        ) == ("LEGACY", "{}")
        assert tuple(
            ledger._conn.execute(
                "SELECT episode_id, payload FROM decision_episodes WHERE episode_id='episode-1'"
            ).fetchone()
        ) == ("episode-1", b"{}")
        assert ledger.migrate(now=NOW + timedelta(minutes=1)) == SCHEMA_VERSION
        assert [
            int(row["version"])
            for row in ledger._conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ] == [1, 2, 3, 4, 5]
        assert tuple(
            ledger._conn.execute(
                "SELECT amount, state, underlying FROM reservations WHERE event_id='event-1'"
            ).fetchone()
        ) == ("10", "RESERVED", "KR")
        assert tuple(
            ledger._conn.execute(
                "SELECT event_type, payload FROM passport_events WHERE event_type='LEGACY'"
            ).fetchone()
        ) == ("LEGACY", "{}")
        assert tuple(
            ledger._conn.execute(
                "SELECT episode_id, payload FROM decision_episodes WHERE episode_id='episode-1'"
            ).fetchone()
        ) == ("episode-1", b"{}")
    finally:
        ledger.close()


def test_decision_append_exact_replay_and_conflict_survives_reopen(tmp_path) -> None:
    path = tmp_path / "risk.sqlite3"
    decision = _decision()
    ledger = RiskLedger(path)
    try:
        assert append_decision_episode(ledger, decision) is True
        assert append_decision_episode(ledger, decision) is False
    finally:
        ledger.close()

    reopened = RiskLedger(path)
    try:
        assert append_decision_episode(reopened, decision) is False
        with pytest.raises(EpisodeRejected) as rejected:
            append_decision_episode(reopened, _decision(decision_sha256=H2))
        assert rejected.value.reason is EpisodicReason.IDENTITY_CONFLICT
    finally:
        reopened.close()


def test_outcome_correction_appends_and_binds_exact_prior_identity(tmp_path) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    try:
        decision = _decision()
        original = _outcome()
        assert append_decision_episode(ledger, decision) is True
        assert append_outcome_episode(ledger, original) is True

        correction = _outcome(
            outcome_id="outcome-2",
            gross_pnl="4",
            net_pnl="3",
            supersedes_outcome_id=original.outcome_id,
            supersedes_outcome_sha256=outcome_episode_sha256(original),
        )
        assert append_outcome_episode(ledger, correction) is True
        assert [str(row["outcome_id"]) for row in ledger.outcome_episode_rows()] == [
            "outcome-1",
            "outcome-2",
        ]

        with pytest.raises(EpisodeRejected) as rejected:
            append_outcome_episode(
                ledger,
                _outcome(
                    outcome_id="outcome-3",
                    supersedes_outcome_id=original.outcome_id,
                    supersedes_outcome_sha256=H2,
                ),
            )
        assert rejected.value.reason is EpisodicReason.CORRECTION_MISMATCH
    finally:
        ledger.close()


def test_canonical_contract_rejects_unknown_duplicate_hash_decimal_and_naive_clock() -> None:
    decision = _decision()
    raw = decision_episode_bytes(decision)
    assert parse_decision_episode(raw) == decision

    unknown_payload = json.loads(raw)
    unknown_payload["unknown"] = "forbidden"
    with pytest.raises(EpisodeRejected) as unknown:
        parse_decision_episode(_canonical_json(unknown_payload))
    assert unknown.value.reason is EpisodicReason.UNKNOWN_FIELD

    duplicate = raw.replace(b'"episode_id":', b'"episode_id":"duplicate","episode_id":', 1)
    with pytest.raises(EpisodeRejected) as duplicate_field:
        parse_decision_episode(duplicate)
    assert duplicate_field.value.reason is EpisodicReason.DUPLICATE_FIELD

    with pytest.raises(EpisodeRejected) as malformed_hash:
        decision_episode_bytes(_decision(source_policy_sha256="F" * 64))
    assert malformed_hash.value.reason is EpisodicReason.INVALID_HASH

    with pytest.raises(EpisodeRejected) as naive_clock:
        decision_episode_bytes(_decision(occurred_at=NOW.replace(tzinfo=None)))
    assert naive_clock.value.reason is EpisodicReason.INVALID_CLOCK

    snapshot_payload = json.loads(broker_truth_snapshot_bytes(_snapshot()))
    snapshot_payload["equity"] = "01"
    with pytest.raises(EpisodeRejected) as malformed_decimal:
        parse_broker_truth_snapshot(_canonical_json(snapshot_payload))
    assert malformed_decimal.value.reason is EpisodicReason.INVALID_DECIMAL

    snapshot_payload["equity"] = 1.25
    with pytest.raises(EpisodeRejected):
        parse_broker_truth_snapshot(_canonical_json(snapshot_payload))

    with pytest.raises(EpisodeRejected) as incompatible_state:
        decision_episode_bytes(_decision(direction="UNCERTAIN"))
    assert incompatible_state.value.reason is EpisodicReason.INVALID_STATE


def test_decision_identity_is_the_hash_of_its_strict_canonical_bytes() -> None:
    decision = _decision()
    assert decision_episode_sha256(decision) != H
    assert len(decision_episode_sha256(decision)) == 64


def _summary(
    ledger: RiskLedger,
    *,
    as_of: datetime = NOW + timedelta(minutes=10),
    candidate_ids: tuple[str, ...] = (),
):
    return build_episodic_summary(
        ledger,
        as_of=as_of,
        policy_sha256=H,
        model_config_sha256=H,
        candidate_ids=candidate_ids,
    )


def test_outcomes_after_or_observed_after_cutoff_are_invisible_not_realized(tmp_path) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    as_of = NOW + timedelta(minutes=10)
    try:
        terminal_late_decision = _decision(
            episode_id="decision-terminal-late",
            event_id="event-terminal-late",
            candidate_id="candidate-terminal-late",
        )
        observed_late_decision = _decision(
            episode_id="decision-observed-late",
            event_id="event-observed-late",
            candidate_id="candidate-observed-late",
        )
        assert append_decision_episode(ledger, terminal_late_decision)
        assert append_decision_episode(ledger, observed_late_decision)
        assert append_outcome_episode(
            ledger,
            _outcome(
                outcome_id="outcome-terminal-late",
                decision_episode_id=terminal_late_decision.episode_id,
                event_id=terminal_late_decision.event_id,
                terminal_at=as_of + timedelta(seconds=1),
                observed_at=as_of + timedelta(minutes=1),
                created_at=as_of + timedelta(minutes=1),
            ),
        )
        assert append_outcome_episode(
            ledger,
            _outcome(
                outcome_id="outcome-observed-late",
                decision_episode_id=observed_late_decision.episode_id,
                event_id=observed_late_decision.event_id,
                terminal_at=as_of - timedelta(seconds=1),
                observed_at=as_of + timedelta(seconds=1),
                created_at=as_of + timedelta(seconds=1),
            ),
        )

        summary = _summary(ledger, as_of=as_of)
        rows = {row.episode_id: row for row in summary.rows}
        assert rows[terminal_late_decision.episode_id].outcome_unavailable_reason == "NO_OUTCOME"
        assert rows[observed_late_decision.episode_id].outcome_unavailable_reason == "NO_OUTCOME"
        assert rows[terminal_late_decision.episode_id].outcome_id is None
        assert rows[observed_late_decision.episode_id].outcome_id is None
        assert rows[terminal_late_decision.episode_id].lifecycle_outcome is None
        assert rows[observed_late_decision.episode_id].lifecycle_outcome is None
        assert rows[terminal_late_decision.episode_id].net_pnl is None
        assert rows[observed_late_decision.episode_id].net_pnl is None
        assert summary.completed_count == 0
        assert summary.realized_count == 0
        assert summary.net_pnl == "0"
    finally:
        ledger.close()


@pytest.mark.parametrize(
    ("lifecycle_outcome", "reason"),
    [
        ("OPEN", "OUTCOME_OPEN"),
        ("PARTIAL", "OUTCOME_PARTIAL"),
        ("MANUAL_REQUIRED", "OUTCOME_MANUAL_REQUIRED"),
    ],
)
def test_open_partial_and_manual_outcomes_are_explicitly_unavailable(
    tmp_path, lifecycle_outcome: str, reason: str
) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    try:
        decision = _decision()
        outcome = _outcome(
            lifecycle_outcome=lifecycle_outcome,
            pnl_classification="UNAVAILABLE",
            gross_pnl=None,
            net_pnl=None,
            final_flat=False,
        )
        assert append_decision_episode(ledger, decision)
        assert append_outcome_episode(ledger, outcome)

        row = _summary(ledger).rows[0]
        assert row.lifecycle_outcome == lifecycle_outcome
        assert row.outcome_unavailable_reason == reason
        assert row.gross_pnl is None
        assert row.net_pnl is None
    finally:
        ledger.close()


def _populate_determinism_fixture(ledger: RiskLedger, *, reverse: bool) -> None:
    first = _decision(
        episode_id="decision-first",
        event_id="event-first",
        candidate_id="candidate-first",
    )
    later = _decision(
        episode_id="decision-later",
        event_id="event-later",
        candidate_id="candidate-later",
        occurred_at=NOW + timedelta(minutes=4),
        decision_cutoff_at=NOW + timedelta(minutes=5),
        created_at=NOW + timedelta(minutes=4, seconds=1),
    )
    first_outcome = _outcome(
        outcome_id="outcome-first",
        decision_episode_id=first.episode_id,
        event_id=first.event_id,
    )
    later_outcome = _outcome(
        outcome_id="outcome-later",
        decision_episode_id=later.episode_id,
        event_id=later.event_id,
        terminal_at=NOW + timedelta(minutes=6),
        observed_at=NOW + timedelta(minutes=7),
        created_at=NOW + timedelta(minutes=8),
    )
    decisions = (later, first) if reverse else (first, later)
    outcomes = (later_outcome, first_outcome) if reverse else (first_outcome, later_outcome)
    for decision in decisions:
        assert append_decision_episode(ledger, decision)
    for outcome in outcomes:
        assert append_outcome_episode(ledger, outcome)
    assert append_broker_truth_snapshot(
        ledger,
        _snapshot(
            snapshot_id="snapshot-flat",
            observed_at=NOW + timedelta(minutes=8),
            created_at=NOW + timedelta(minutes=9),
        ),
    )


def test_summary_bytes_are_stable_across_insertion_order_and_reopen(tmp_path) -> None:
    first_path = tmp_path / "first.sqlite3"
    first = RiskLedger(first_path)
    try:
        _populate_determinism_fixture(first, reverse=False)
        original = episodic_summary_bytes(_summary(first))
    finally:
        first.close()

    reopened = RiskLedger(first_path)
    try:
        assert episodic_summary_bytes(_summary(reopened)) == original
    finally:
        reopened.close()

    second = RiskLedger(tmp_path / "second.sqlite3")
    try:
        _populate_determinism_fixture(second, reverse=True)
        assert episodic_summary_bytes(_summary(second)) == original
    finally:
        second.close()


def test_candidate_filter_and_policy_model_incompatibility_are_bounded_and_labelled(
    tmp_path,
) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    try:
        compatible = _decision()
        wrong_policy = _decision(
            episode_id="decision-policy-mismatch",
            event_id="event-policy-mismatch",
            candidate_id="candidate-policy-mismatch",
            source_policy_sha256=H2,
        )
        wrong_model = _decision(
            episode_id="decision-model-mismatch",
            event_id="event-model-mismatch",
            candidate_id="candidate-model-mismatch",
            model_config_sha256=H2,
        )
        excluded_candidate = _decision(
            episode_id="decision-candidate-excluded",
            event_id="event-candidate-excluded",
            candidate_id="candidate-excluded",
        )
        for decision in (compatible, wrong_policy, wrong_model, excluded_candidate):
            assert append_decision_episode(ledger, decision)

        summary = _summary(
            ledger,
            candidate_ids=(
                compatible.candidate_id,
                wrong_policy.candidate_id,
                wrong_model.candidate_id,
            ),
        )
        rows = {row.episode_id: row for row in summary.rows}
        assert excluded_candidate.episode_id not in rows
        assert summary.candidate_filter_excluded_count == 1
        assert rows[wrong_policy.episode_id].compatibility == "POLICY_MISMATCH"
        assert rows[wrong_policy.episode_id].outcome_unavailable_reason == "POLICY_MISMATCH"
        assert rows[wrong_model.episode_id].compatibility == "MODEL_CONFIG_MISMATCH"
        assert rows[wrong_model.episode_id].outcome_unavailable_reason == "MODEL_CONFIG_MISMATCH"
    finally:
        ledger.close()


def test_broker_truth_snapshots_are_append_only_and_latest_pre_cutoff_is_deterministic(
    tmp_path,
) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    try:
        older = _snapshot(snapshot_id="snapshot-older")
        latest = _snapshot(
            snapshot_id="snapshot-latest",
            observed_at=NOW + timedelta(minutes=4),
            created_at=NOW + timedelta(minutes=5),
            account_sha256=H2,
        )
        assert append_broker_truth_snapshot(ledger, latest)
        assert append_broker_truth_snapshot(ledger, older)
        assert append_broker_truth_snapshot(ledger, latest) is False
        with pytest.raises(EpisodeRejected) as rejected:
            append_broker_truth_snapshot(ledger, replace(latest, equity="999"))
        assert rejected.value.reason is EpisodicReason.IDENTITY_CONFLICT

        summary = _summary(ledger)
        assert summary.latest_broker_truth_snapshot_id == latest.snapshot_id
        assert len(summary.latest_broker_truth_snapshot_sha256) == 64
    finally:
        ledger.close()


def test_summary_bytes_are_strict_and_later_payload_tampering_is_not_hidden_by_a_new_hash(
    tmp_path,
) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    try:
        earlier = _decision(episode_id="decision-earlier", event_id="event-earlier")
        later = _decision(
            episode_id="decision-later",
            event_id="event-later",
            occurred_at=NOW + timedelta(minutes=4),
            decision_cutoff_at=NOW + timedelta(minutes=5),
            created_at=NOW + timedelta(minutes=4, seconds=1),
        )
        assert append_decision_episode(ledger, earlier)
        assert append_decision_episode(ledger, later)
        summary = _summary(ledger)
        assert parse_episodic_summary(episodic_summary_bytes(summary)) == summary

        altered = decision_episode_bytes(replace(later, direction="DOWN", decision_sha256=H2))
        ledger._conn.execute(
            "UPDATE decision_episodes SET payload=?, payload_sha256=? WHERE episode_id=?",
            (altered, hashlib.sha256(altered).hexdigest(), later.episode_id),
        )
        with pytest.raises(EpisodeRejected) as rejected:
            _summary(ledger)
        assert rejected.value.reason is EpisodicReason.STORED_RECORD_INVALID
    finally:
        ledger.close()


def test_future_appends_and_corrections_cannot_change_a_past_summary(tmp_path) -> None:
    from esscher.autonomy.episodes import broker_truth_snapshot_sha256

    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    as_of = NOW + timedelta(minutes=10)
    try:
        decision = _decision()
        snapshot = _snapshot()
        assert append_decision_episode(ledger, decision)
        assert append_broker_truth_snapshot(ledger, snapshot)
        baseline = episodic_summary_bytes(_summary(ledger, as_of=as_of))

        assert append_outcome_episode(
            ledger,
            _outcome(
                terminal_at=as_of + timedelta(seconds=1),
                observed_at=as_of + timedelta(minutes=1),
                created_at=as_of + timedelta(minutes=1),
            ),
        )
        assert episodic_summary_bytes(_summary(ledger, as_of=as_of)) == baseline

        assert append_decision_episode(
            ledger,
            replace(
                decision,
                episode_id="decision-future-correction",
                created_at=as_of + timedelta(minutes=1),
                supersedes_episode_id=decision.episode_id,
                supersedes_episode_sha256=decision_episode_sha256(decision),
            ),
        )
        assert episodic_summary_bytes(_summary(ledger, as_of=as_of)) == baseline

        assert append_broker_truth_snapshot(
            ledger,
            replace(
                snapshot,
                snapshot_id="snapshot-future-correction",
                observed_at=as_of + timedelta(minutes=1),
                created_at=as_of + timedelta(minutes=2),
                supersedes_snapshot_id=snapshot.snapshot_id,
                supersedes_snapshot_sha256=broker_truth_snapshot_sha256(snapshot),
            ),
        )
        assert episodic_summary_bytes(_summary(ledger, as_of=as_of)) == baseline
    finally:
        ledger.close()


def test_summary_aggregates_all_eligible_history_beyond_display_limit(tmp_path) -> None:
    ledger = RiskLedger(tmp_path / "risk.sqlite3")
    last_terminal = NOW
    try:
        for index in range(17):
            shift = timedelta(minutes=index * 2)
            decision = _decision(
                episode_id=f"decision-{index:02d}",
                event_id=f"event-{index:02d}",
                candidate_id=f"candidate-{index:02d}",
                occurred_at=NOW + shift,
                decision_cutoff_at=NOW + shift + timedelta(minutes=1),
                created_at=NOW + shift + timedelta(seconds=1),
            )
            outcome = _outcome(
                outcome_id=f"outcome-{index:02d}",
                decision_episode_id=decision.episode_id,
                event_id=decision.event_id,
                terminal_at=NOW + shift + timedelta(minutes=1, seconds=1),
                observed_at=NOW + shift + timedelta(minutes=1, seconds=2),
                created_at=NOW + shift + timedelta(minutes=1, seconds=3),
            )
            last_terminal = outcome.terminal_at
            assert append_decision_episode(ledger, decision)
            assert append_outcome_episode(ledger, outcome)
        assert append_broker_truth_snapshot(
            ledger,
            _snapshot(
                snapshot_id="snapshot-all-flat",
                observed_at=last_terminal + timedelta(minutes=1),
                created_at=last_terminal + timedelta(minutes=1, seconds=1),
            ),
        )

        summary = build_episodic_summary(
            ledger,
            as_of=last_terminal + timedelta(minutes=2),
            policy_sha256=H,
            model_config_sha256=H,
            limit=16,
        )
        assert len(summary.rows) == 16
        assert summary.completed_count == 17
        assert summary.realized_count == 17
        assert summary.net_pnl == "68"
    finally:
        ledger.close()
