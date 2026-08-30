"""Durable SQLite WAL reservation ledger for the PAPER risk kernel.

One standard-library SQLite database is the source of truth for event identity,
account snapshots, reservations, permits, submissions, fills, positions,
reconciliations, and control state. A reservation and one-use permit binding are
persisted before any broker mutation. Raw broker payloads and account identifiers
never enter the ledger; only sanitized identities and hashes are stored.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .policy import RISK_POLICY_SHA256, RISK_POLICY_VERSION

LEDGER_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_snapshots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    equity TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations(
    reservation_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    package_sha256 TEXT NOT NULL UNIQUE,
    permit_id TEXT UNIQUE,
    max_loss TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    consumed_at TEXT,
    released_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('RESERVED', 'CONSUMED', 'RELEASED'))
);
CREATE TABLE IF NOT EXISTS submissions(
    client_order_id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    filled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions(
    symbol TEXT PRIMARY KEY,
    quantity TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliations(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_state(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('OPEN', 'ENTRY_DISABLED', 'CLOSE_ONLY', 'KILLED')),
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_entries(
    day TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY(day, event_id)
);
CREATE TABLE IF NOT EXISTS period_entries(
    event_id TEXT PRIMARY KEY,
    entered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS migrated_events(
    event_run_id TEXT PRIMARY KEY,
    lifecycle TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_states(
    event_run_id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    state TEXT NOT NULL,
    opening_client_order_id TEXT,
    closing_client_order_id TEXT,
    opened_at TEXT,
    close_due_at TEXT,
    updated_at TEXT NOT NULL,
    fail_code TEXT
);
CREATE TABLE IF NOT EXISTS lifecycle_ticks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_run_id TEXT NOT NULL,
    at TEXT NOT NULL,
    tick TEXT NOT NULL,
    UNIQUE(event_run_id, tick)
);
"""


class LedgerError(ValueError):
    """Raised when a durable ledger operation fails closed."""


class LedgerDuplicate(LedgerError):
    """Raised when a duplicate event or package identity is detected."""


class LedgerStateConflict(LedgerError):
    """Raised when a reservation transition contradicts ledger truth."""


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LedgerError("timestamps must be timezone-aware")
    if value.microsecond != 0:
        raise LedgerError("timestamps must use second precision")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class ReservationRecord:
    reservation_id: str
    event_id: str
    package_sha256: str
    permit_id: str | None
    max_loss: Decimal
    reserved_at: datetime
    status: str


class RiskLedger:
    """Transactional reservation and lifecycle ledger over one SQLite file."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = str(db_path)
        self._connection = sqlite3.connect(self._path, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(_SCHEMA)
        self._seed_meta()

    def _seed_meta(self) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(LEDGER_SCHEMA_VERSION),),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('risk_policy_version', ?)",
                (RISK_POLICY_VERSION,),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('risk_policy_sha256', ?)",
                (RISK_POLICY_SHA256,),
            )

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        cursor = self._connection.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            yield cursor
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()
        finally:
            cursor.close()

    def record_account_snapshot(
        self, *, equity: Decimal, observed_at: datetime, raw_sha256: str
    ) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO account_snapshots(observed_at, equity, raw_sha256) VALUES (?, ?, ?)",
                (_utc_text(observed_at), str(equity), raw_sha256),
            )

    def reserve(
        self,
        *,
        reservation_id: str,
        event_id: str,
        package_sha256: str,
        max_loss: Decimal,
        now: datetime,
    ) -> None:
        """Persist one reservation before any mutation; duplicates fail closed."""

        if not reservation_id.strip() or not event_id.strip():
            raise LedgerError("reservation and event identity are required")
        if not isinstance(max_loss, Decimal) or not max_loss.is_finite() or max_loss <= 0:
            raise LedgerError("max_loss must be a positive finite Decimal")
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO reservations(reservation_id, event_id, package_sha256, "
                    "max_loss, reserved_at, status) VALUES (?, ?, ?, ?, ?, 'RESERVED')",
                    (reservation_id, event_id, package_sha256, str(max_loss), _utc_text(now)),
                )
        except sqlite3.IntegrityError as error:
            raise LedgerDuplicate(
                f"duplicate reservation for event {event_id} and package {package_sha256[:12]}"
            ) from error

    def bind_permit(self, *, reservation_id: str, permit_id: str, now: datetime) -> None:
        """Bind one one-use permit to one reservation; reuse fails closed."""

        if not permit_id.strip():
            raise LedgerError("permit identity is required")
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    "UPDATE reservations SET permit_id = ? "
                    "WHERE reservation_id = ? AND status = 'RESERVED' AND permit_id IS NULL",
                    (permit_id, reservation_id),
                )
                if cursor.rowcount != 1:
                    raise LedgerStateConflict(
                        "permit binding requires an unbound RESERVED reservation"
                    )
        except sqlite3.IntegrityError as error:
            raise LedgerStateConflict("permit identity is already bound") from error

    def consume(self, *, reservation_id: str, now: datetime) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "UPDATE reservations SET status = 'CONSUMED', consumed_at = ? "
                "WHERE reservation_id = ? AND status = 'RESERVED'",
                (_utc_text(now), reservation_id),
            )
            if cursor.rowcount != 1:
                raise LedgerStateConflict("only RESERVED reservations can be consumed")

    def release(self, *, reservation_id: str, now: datetime) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "UPDATE reservations SET status = 'RELEASED', released_at = ? "
                "WHERE reservation_id = ? AND status = 'RESERVED'",
                (_utc_text(now), reservation_id),
            )
            if cursor.rowcount != 1:
                raise LedgerStateConflict("only RESERVED reservations can be released")

    def record_entry(self, *, event_id: str, now: datetime) -> None:
        """Record one new entry atomically against day and period budgets."""

        day = now.astimezone(UTC).date().isoformat()
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO daily_entries(day, event_id) VALUES (?, ?)",
                    (day, event_id),
                )
                cursor.execute(
                    "INSERT INTO period_entries(event_id, entered_at) VALUES (?, ?)",
                    (event_id, _utc_text(now)),
                )
        except sqlite3.IntegrityError as error:
            raise LedgerDuplicate(f"duplicate entry for event {event_id}") from error

    def entries_on_day(self, day: str) -> int:
        cursor = self._connection.execute(
            "SELECT COUNT(*) FROM daily_entries WHERE day = ?", (day,)
        )
        return int(cursor.fetchone()[0])

    def entries_in_period(self) -> int:
        cursor = self._connection.execute("SELECT COUNT(*) FROM period_entries")
        return int(cursor.fetchone()[0])

    def reservation(self, reservation_id: str) -> ReservationRecord | None:
        cursor = self._connection.execute(
            "SELECT reservation_id, event_id, package_sha256, permit_id, max_loss, "
            "reserved_at, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ReservationRecord(
            reservation_id=row[0],
            event_id=row[1],
            package_sha256=row[2],
            permit_id=row[3],
            max_loss=Decimal(row[4]),
            reserved_at=datetime.fromisoformat(row[5]).astimezone(UTC),
            status=row[6],
        )

    def open_reservations(self) -> tuple[ReservationRecord, ...]:
        cursor = self._connection.execute(
            "SELECT reservation_id, event_id, package_sha256, permit_id, max_loss, "
            "reserved_at, status FROM reservations WHERE status IN ('RESERVED', 'CONSUMED') "
            "ORDER BY reserved_at"
        )
        return tuple(
            ReservationRecord(
                reservation_id=row[0],
                event_id=row[1],
                package_sha256=row[2],
                permit_id=row[3],
                max_loss=Decimal(row[4]),
                reserved_at=datetime.fromisoformat(row[5]).astimezone(UTC),
                status=row[6],
            )
            for row in cursor.fetchall()
        )

    def reserved_loss(self) -> Decimal:
        cursor = self._connection.execute(
            "SELECT max_loss FROM reservations WHERE status IN ('RESERVED', 'CONSUMED')"
        )
        total = Decimal("0")
        for (value,) in cursor.fetchall():
            total += Decimal(value)
        return total

    def record_position(self, *, symbol: str, quantity: Decimal, observed_at: datetime) -> None:
        """Upsert one broker-observed position truth."""

        if not symbol.strip():
            raise LedgerError("symbol must be non-empty")
        if not isinstance(quantity, Decimal) or not quantity.is_finite():
            raise LedgerError("quantity must be a finite Decimal")
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO positions(symbol, quantity, observed_at) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET quantity = excluded.quantity, "
                "observed_at = excluded.observed_at",
                (symbol, str(quantity), _utc_text(observed_at)),
            )

    def position_symbols(self) -> frozenset[str]:
        cursor = self._connection.execute(
            "SELECT symbol FROM positions WHERE CAST(quantity AS REAL) != 0"
        )
        return frozenset(row[0] for row in cursor.fetchall())

    def record_realized_loss(self, *, day: str, amount: Decimal, now: datetime) -> None:
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            raise LedgerError("realized_loss must be a non-negative finite Decimal")
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO reconciliations(at, outcome, detail) VALUES (?, ?, ?)",
                (_utc_text(now), "REALIZED_LOSS", f"{day}:{amount}"),
            )

    def record_reconciliation(self, *, outcome: str, detail: str, now: datetime) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO reconciliations(at, outcome, detail) VALUES (?, ?, ?)",
                (_utc_text(now), outcome, detail),
            )

    def set_control_state(self, *, state: str, reason: str, now: datetime) -> None:
        if state not in {"OPEN", "ENTRY_DISABLED", "CLOSE_ONLY", "KILLED"}:
            raise LedgerError("unknown control state")
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO control_state(at, state, reason) VALUES (?, ?, ?)",
                (_utc_text(now), state, reason),
            )

    def current_control_state(self) -> tuple[str, str]:
        cursor = self._connection.execute(
            "SELECT state, reason FROM control_state ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None:
            return "OPEN", "initial"
        return row[0], row[1]

    def record_migrated_event(
        self, *, event_run_id: str, lifecycle: str, updated_at: datetime
    ) -> None:
        """Idempotently delegate one existing file-store event identity to the ledger."""

        try:
            with self._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO migrated_events(event_run_id, lifecycle, updated_at) "
                    "VALUES (?, ?, ?)",
                    (event_run_id, lifecycle, _utc_text(updated_at)),
                )
        except sqlite3.IntegrityError:
            return

    def migrated_events(self) -> tuple[str, ...]:
        cursor = self._connection.execute(
            "SELECT event_run_id FROM migrated_events ORDER BY event_run_id"
        )
        return tuple(row[0] for row in cursor.fetchall())

    def set_lifecycle_state(
        self,
        *,
        event_run_id: str,
        reservation_id: str,
        state: str,
        updated_at: datetime,
        opening_client_order_id: str | None = None,
        closing_client_order_id: str | None = None,
        opened_at: datetime | None = None,
        close_due_at: datetime | None = None,
        fail_code: str | None = None,
    ) -> None:
        """Persist one lifecycle transition before any downstream side effect."""

        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO lifecycle_states(event_run_id, reservation_id, state, "
                "opening_client_order_id, closing_client_order_id, opened_at, close_due_at, "
                "updated_at, fail_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(event_run_id) DO UPDATE SET "
                "state = excluded.state, "
                "opening_client_order_id = excluded.opening_client_order_id, "
                "closing_client_order_id = excluded.closing_client_order_id, "
                "opened_at = excluded.opened_at, "
                "close_due_at = excluded.close_due_at, "
                "updated_at = excluded.updated_at, "
                "fail_code = excluded.fail_code",
                (
                    event_run_id,
                    reservation_id,
                    state,
                    opening_client_order_id,
                    closing_client_order_id,
                    _utc_text(opened_at) if opened_at else None,
                    _utc_text(close_due_at) if close_due_at else None,
                    _utc_text(updated_at),
                    fail_code,
                ),
            )

    def lifecycle_state(self, event_run_id: str) -> dict[str, object] | None:
        cursor = self._connection.execute(
            "SELECT event_run_id, reservation_id, state, opening_client_order_id, "
            "closing_client_order_id, opened_at, close_due_at, updated_at, fail_code "
            "FROM lifecycle_states WHERE event_run_id = ?",
            (event_run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        def _timestamp(value: str | None) -> datetime | None:
            if value is None:
                return None
            return datetime.fromisoformat(value).astimezone(UTC)

        return {
            "event_run_id": row[0],
            "reservation_id": row[1],
            "state": row[2],
            "opening_client_order_id": row[3],
            "closing_client_order_id": row[4],
            "opened_at": _timestamp(row[5]),
            "close_due_at": _timestamp(row[6]),
            "updated_at": _timestamp(row[7]),
            "fail_code": row[8],
        }

    def record_lifecycle_tick(self, *, event_run_id: str, tick: str, at: datetime) -> bool:
        """Record one tick idempotently; returns False when the tick already ran."""

        try:
            with self._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO lifecycle_ticks(event_run_id, at, tick) VALUES (?, ?, ?)",
                    (event_run_id, _utc_text(at), tick),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def lifecycle_ticks(self, event_run_id: str) -> tuple[str, ...]:
        cursor = self._connection.execute(
            "SELECT tick FROM lifecycle_ticks WHERE event_run_id = ? ORDER BY id",
            (event_run_id,),
        )
        return tuple(row[0] for row in cursor.fetchall())
