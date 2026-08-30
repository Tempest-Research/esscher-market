"""One standard-library SQLite WAL ledger for the PAPER risk kernel.

The ledger is the single source of truth for candidate identity, policy/source
hashes, decisions, expressions, account snapshots, reservations, permits,
submissions, fills, positions, reconciliations, control state, evidence mode,
and ``NOT_RUN``. A reservation and one-use permit are persisted before any
mutation. Migrations are deterministic and recorded; existing attempt state
delegates here so there are no split-brain writes.

The ledger never contacts a broker, never mutates broker state, and never
reads a wall clock: every timestamp is an explicit input.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ringdown_market.risk.reasons import (
    ControlState,
    RiskReason,
    _reject,
)
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

SCHEMA_VERSION: int = 1

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS candidates (
        event_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        policy_sha256 TEXT NOT NULL,
        decision_sha256 TEXT NOT NULL,
        expression_sha256 TEXT NOT NULL,
        evidence_mode TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS reservations (
        reservation_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        amount TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('RESERVED','CONSUMED','RELEASED')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS permits (
        permit_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        reservation_id TEXT NOT NULL,
        permit_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ISSUED','SUBMITTED','FILLED','CANCELLED')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,
        permit_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        quantity TEXT NOT NULL,
        status TEXT NOT NULL,
        observed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS positions (
        underlying TEXT PRIMARY KEY,
        quantity TEXT NOT NULL,
        observed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS reconciliations (
        reconciliation_id TEXT PRIMARY KEY,
        result TEXT NOT NULL,
        detail TEXT,
        paper_pnl TEXT,
        shadow_pnl TEXT,
        observed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS account_snapshots (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        equity TEXT NOT NULL,
        observed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS control_state (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        state TEXT NOT NULL,
        reason TEXT,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS passport_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        prev_sha256 TEXT NOT NULL,
        event_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS not_run (
        event_id TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
    """,
}


def _timestamp(value: datetime) -> str:
    if value.tzinfo != UTC:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, "timestamp", "clock must be UTC")
    return value.isoformat().replace("+00:00", "Z")


class RiskLedger:
    """A deterministic SQLite WAL ledger for the PAPER risk kernel."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # -- migration -----------------------------------------------------------

    def migrate(self, *, now: datetime | None = None) -> int:
        """Apply pending migrations deterministically; return schema version."""

        applied_at = _timestamp(now) if now is not None else "MIGRATION"
        applied = (
            {
                row["version"]
                for row in self._conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            if self._table_exists("schema_migrations")
            else set()
        )
        for version in sorted(_MIGRATIONS):
            if version in applied:
                continue
            self._conn.executescript(_MIGRATIONS[version])
            self._conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, applied_at),
            )
        return self.schema_version()

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def schema_version(self) -> int:
        if not self._table_exists("schema_migrations"):
            return 0
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    def close(self) -> None:
        self._conn.close()

    # -- candidates ----------------------------------------------------------

    def record_candidate(
        self,
        *,
        event_id: str,
        candidate_id: str,
        policy_sha256: str,
        decision_sha256: str,
        expression_sha256: str,
        evidence_mode: str,
        now: datetime,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO candidates (event_id, candidate_id, policy_sha256,"
            " decision_sha256, expression_sha256, evidence_mode, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                candidate_id,
                policy_sha256,
                decision_sha256,
                expression_sha256,
                evidence_mode,
                _timestamp(now),
            ),
        )

    # -- reservations (transactional, one per event) -------------------------

    def reserve(self, *, event_id: str, amount: Decimal, now: datetime) -> str:
        """Persist a reservation before any mutation; one per event."""

        reservation_id = f"rsv-{event_id}"
        amount_text = str(amount)
        created = _timestamp(now)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO reservations (reservation_id, event_id, amount, state,"
                " created_at, updated_at) VALUES (?, ?, ?, 'RESERVED', ?, ?)",
                (reservation_id, event_id, amount_text, created, created),
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self._conn.execute("ROLLBACK")
            raise _reject(
                RiskReason.DUPLICATE_EVENT_RESERVATION,
                f"reservation.{event_id}",
                f"a reservation already exists for this event ({error})",
            ) from None
        return reservation_id

    def reservation_state(self, event_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT state FROM reservations WHERE event_id=?", (event_id,)
        ).fetchone()
        return None if row is None else str(row["state"])

    def _transition_reservation(self, *, event_id: str, to_state: str, now: datetime) -> None:
        updated = _timestamp(now)
        self._conn.execute("BEGIN IMMEDIATE")
        row = self._conn.execute(
            "SELECT state FROM reservations WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            self._conn.execute("ROLLBACK")
            raise _reject(
                RiskReason.RESERVATION_NOT_RELEASED,
                f"reservation.{event_id}",
                "no reservation exists for this event",
            )
        current = str(row["state"])
        if current != "RESERVED":
            self._conn.execute("ROLLBACK")
            raise _reject(
                RiskReason.RESERVATION_NOT_RELEASED,
                f"reservation.{event_id}",
                f"reservation is {current}, not RESERVED",
            )
        self._conn.execute(
            "UPDATE reservations SET state=?, updated_at=? WHERE event_id=?",
            (to_state, updated, event_id),
        )
        self._conn.execute("COMMIT")

    def consume_reservation(self, *, event_id: str, now: datetime) -> None:
        """Consume a reservation only after fill reconciliation."""

        self._transition_reservation(event_id=event_id, to_state="CONSUMED", now=now)

    def release_reservation(self, *, event_id: str, now: datetime) -> None:
        """Release a reservation only after cancel reconciliation."""

        self._transition_reservation(event_id=event_id, to_state="RELEASED", now=now)

    def open_reservation_total(self) -> Decimal:
        rows = self._conn.execute(
            "SELECT amount FROM reservations WHERE state='RESERVED'"
        ).fetchall()
        return sum((Decimal(str(row["amount"])) for row in rows), Decimal(0))

    # -- permits -------------------------------------------------------------

    def record_permit(
        self,
        *,
        permit_id: str,
        event_id: str,
        reservation_id: str,
        permit_sha256: str,
        now: datetime,
    ) -> None:
        created = _timestamp(now)
        try:
            self._conn.execute(
                "INSERT INTO permits (permit_id, event_id, reservation_id, permit_sha256,"
                " state, created_at, updated_at) VALUES (?, ?, ?, ?, 'ISSUED', ?, ?)",
                (permit_id, event_id, reservation_id, permit_sha256, created, created),
            )
        except sqlite3.IntegrityError as error:
            raise _reject(
                RiskReason.DUPLICATE_EVENT_RESERVATION,
                f"permit.{permit_id}",
                str(error),
            ) from None

    def permit_state(self, permit_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT state FROM permits WHERE permit_id=?", (permit_id,)
        ).fetchone()
        return None if row is None else str(row["state"])

    def update_permit_state(self, *, permit_id: str, to_state: str, now: datetime) -> None:
        self._conn.execute(
            "UPDATE permits SET state=?, updated_at=? WHERE permit_id=?",
            (to_state, _timestamp(now), permit_id),
        )

    # -- fills / positions / reconciliations ---------------------------------

    def record_fill(
        self,
        *,
        fill_id: str,
        permit_id: str,
        event_id: str,
        quantity: str,
        status: str,
        observed_at: datetime,
    ) -> None:
        self._conn.execute(
            "INSERT INTO fills (fill_id, permit_id, event_id, quantity, status, observed_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fill_id, permit_id, event_id, quantity, status, _timestamp(observed_at)),
        )

    def fills_for_permit(self, permit_id: str) -> list[Mapping[str, object]]:
        rows = self._conn.execute(
            "SELECT fill_id, quantity, status, observed_at FROM fills WHERE permit_id=?"
            " ORDER BY rowid",
            (permit_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_position(self, *, underlying: str, quantity: str, observed_at: datetime) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO positions (underlying, quantity, observed_at) VALUES (?, ?, ?)",
            (underlying, quantity, _timestamp(observed_at)),
        )

    def record_reconciliation(
        self,
        *,
        reconciliation_id: str,
        result: str,
        detail: str | None,
        observed_at: datetime,
        paper_pnl: str | None = None,
        shadow_pnl: str | None = None,
    ) -> None:
        """Record one reconciliation; broker PAPER PnL and conservative shadow
        PnL are stored as separate fields and remain separate claims."""

        self._conn.execute(
            "INSERT INTO reconciliations (reconciliation_id, result, detail, paper_pnl,"
            " shadow_pnl, observed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (reconciliation_id, result, detail, paper_pnl, shadow_pnl, _timestamp(observed_at)),
        )

    # -- account snapshots (daily-loss truth) --------------------------------

    def record_account_snapshot(self, *, equity: Decimal, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO account_snapshots (equity, observed_at) VALUES (?, ?)",
            (str(equity), _timestamp(now)),
        )

    def intraday_peak_equity(self, *, now: datetime) -> Decimal:
        """Return the peak observed equity on the same UTC day as ``now``."""

        day_prefix = _timestamp(now)[:10]
        rows = self._conn.execute(
            "SELECT equity FROM account_snapshots WHERE substr(observed_at, 1, 10)=?",
            (day_prefix,),
        ).fetchall()
        peak = Decimal(0)
        for row in rows:
            value = Decimal(str(row["equity"]))
            if value > peak:
                peak = value
        return peak

    # -- entry / expression counters -----------------------------------------

    def open_reservation_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM reservations WHERE state='RESERVED'"
        ).fetchone()
        return int(row["n"])

    def entries_today(self, *, now: datetime) -> int:
        """Count reservations created on the same UTC day as ``now``."""

        day_prefix = _timestamp(now)[:10]
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM reservations WHERE substr(created_at, 1, 10)=?",
            (day_prefix,),
        ).fetchone()
        return int(row["n"])

    # -- control state -------------------------------------------------------

    def set_control_state(self, *, state: ControlState, reason: str | None, now: datetime) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO control_state (id, state, reason, updated_at)"
            " VALUES (1, ?, ?, ?)",
            (state.value, reason, _timestamp(now)),
        )

    def get_control_state(self) -> tuple[ControlState, str | None]:
        row = self._conn.execute("SELECT state, reason FROM control_state WHERE id=1").fetchone()
        if row is None:
            return ControlState.ACTIVE, None
        return ControlState(str(row["state"])), row["reason"]

    # -- passport (append-only hash chain) -----------------------------------

    def append_passport(
        self, *, event_type: str, payload: Mapping[str, object], now: datetime
    ) -> str:
        created = _timestamp(now)
        prev = self._conn.execute(
            "SELECT event_sha256 FROM passport_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_sha256 = str(prev["event_sha256"]) if prev is not None else "0" * 64
        payload_dict = dict(payload)
        canonical = canonical_json_bytes(
            {
                "event_type": event_type,
                "payload": payload_dict,
                "prev_sha256": prev_sha256,
                "created_at": created,
            }
        )
        event_sha256 = sha256_bytes(canonical)
        self._conn.execute(
            "INSERT INTO passport_events (event_type, payload, prev_sha256, event_sha256,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (
                event_type,
                canonical_json_bytes(payload_dict).decode("utf-8"),
                prev_sha256,
                event_sha256,
                created,
            ),
        )
        return event_sha256

    def passport_events(self) -> list[Mapping[str, object]]:
        rows = self._conn.execute(
            "SELECT seq, event_type, payload, prev_sha256, event_sha256, created_at"
            " FROM passport_events ORDER BY seq"
        ).fetchall()
        events = []
        for row in rows:
            record = dict(row)
            record["payload"] = json.loads(str(record["payload"]))
            events.append(record)
        return events

    # -- not run -------------------------------------------------------------

    def record_not_run(self, *, event_id: str, reason: str, now: datetime) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO not_run (event_id, reason, created_at) VALUES (?, ?, ?)",
            (event_id, reason, _timestamp(now)),
        )

    def not_run_reason(self, event_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT reason FROM not_run WHERE event_id=?", (event_id,)
        ).fetchone()
        return None if row is None else str(row["reason"])
