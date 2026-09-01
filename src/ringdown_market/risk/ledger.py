"""One standard-library SQLite WAL ledger for the PAPER risk kernel.

The ledger is the sole durable authority for candidate identity, immutable
abstentions, reservations, permits, submitted broker order identities, fills,
control state, and the append-only passport. It performs no broker/network
operation and accepts explicit UTC timestamps only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ringdown_market.execution.models import (
    DebitVerticalPermit,
    debit_vertical_permit_bytes,
    debit_vertical_permit_id,
)
from ringdown_market.risk.passport import GENESIS_SHA256, PassportEventType
from ringdown_market.risk.reasons import ControlState, RiskReason, _reject
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

SCHEMA_VERSION: int = 3

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
    2: """
    ALTER TABLE reservations ADD COLUMN underlying TEXT NOT NULL DEFAULT '';
    CREATE TABLE IF NOT EXISTS submissions (
        permit_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        broker_order_id TEXT NOT NULL UNIQUE,
        submitted_at TEXT NOT NULL,
        FOREIGN KEY(permit_id) REFERENCES permits(permit_id)
    );
    CREATE INDEX IF NOT EXISTS submissions_broker_order_id_idx
        ON submissions(broker_order_id);
    """,
    3: """
    CREATE TABLE IF NOT EXISTS lifecycle_intents (
        permit_id TEXT PRIMARY KEY,
        phase TEXT NOT NULL CHECK(phase IN ('OPEN','CLOSE')),
        event_id TEXT NOT NULL,
        open_permit_id TEXT NOT NULL,
        reservation_id TEXT NOT NULL,
        correlation_sha256 TEXT NOT NULL,
        policy_sha256 TEXT NOT NULL,
        snapshot_sha256 TEXT NOT NULL,
        account_id TEXT NOT NULL,
        account_class TEXT NOT NULL,
        order_class TEXT NOT NULL,
        client_order_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        request_json TEXT NOT NULL,
        broker_order_id TEXT UNIQUE,
        state TEXT NOT NULL CHECK(state IN ('INTENDED','SUBMITTED','RECONCILED')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(event_id, phase)
    );
    CREATE INDEX IF NOT EXISTS lifecycle_intents_event_id_idx
        ON lifecycle_intents(event_id);
    """,
}


def _timestamp(value: object, path: str = "timestamp") -> str:
    if not isinstance(value, datetime):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be a datetime")
    if value.tzinfo is not UTC:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be UTC")
    return value.isoformat().replace("+00:00", "Z")


def _identifier(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be non-empty exact text")
    return value


def _underlying(value: object, path: str) -> str:
    text = _identifier(value, path)
    if text != text.upper():
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be normalized uppercase text")
    return text


def _sha256(value: object, path: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be a lowercase SHA-256 hex digest")
    return value


def _finite_decimal(value: object, path: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be a finite Decimal")
    return value


def _amount(value: object, path: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise _reject(
            RiskReason.EXPOSURE_NOT_CALCULABLE,
            path,
            "must be a finite non-negative Decimal",
        )
    return value


def _positive_limit(value: object, path: str) -> Decimal:
    result = _amount(value, path)
    if result <= 0:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be positive")
    return result


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _reject(RiskReason.UNSUPPORTED_INPUT, path, "must be a positive integer")
    return value


class RiskLedger:
    """A deterministic SQLite WAL ledger for the PAPER risk kernel."""

    def __init__(self, path: str | Path) -> None:
        location = Path(path)
        location.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(location)
        self._conn = sqlite3.connect(self._path, isolation_level=None, timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # -- migration -----------------------------------------------------------

    def migrate(self, *, now: datetime | None = None) -> int:
        """Apply every pending migration deterministically and idempotently."""

        applied_at = _timestamp(now) if now is not None else "MIGRATION"
        applied = (
            {
                int(row["version"])
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
        return (
            self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    def schema_version(self) -> int:
        if not self._table_exists("schema_migrations"):
            return 0
        row = self._conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return int(row["version"] or 0)

    def close(self) -> None:
        self._conn.close()

    def _begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def _rollback(self) -> None:
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")

    # -- canonical append-only passport -------------------------------------

    def _append_passport(
        self, *, event_type: str, payload: Mapping[str, object], now: datetime
    ) -> str:
        event = _identifier(event_type, "passport.event_type")
        created = _timestamp(now, "passport.created_at")
        previous = self._conn.execute(
            "SELECT event_sha256 FROM passport_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous["event_sha256"]) if previous is not None else GENESIS_SHA256
        payload_dict = dict(payload)
        canonical = canonical_json_bytes(
            {
                "event_type": event,
                "payload": payload_dict,
                "prev_sha256": previous_hash,
                "created_at": created,
            }
        )
        event_hash = sha256_bytes(canonical)
        self._conn.execute(
            (
                "INSERT INTO passport_events (event_type, payload, prev_sha256, "
                "event_sha256, created_at) VALUES (?, ?, ?, ?, ?)"
            ),
            (
                event,
                canonical_json_bytes(payload_dict).decode("utf-8"),
                previous_hash,
                event_hash,
                created,
            ),
        )
        return event_hash

    def append_passport(
        self, *, event_type: str, payload: Mapping[str, object], now: datetime
    ) -> str:
        """Append one hash-linked passport event outside a wider transaction."""

        return self._append_passport(event_type=event_type, payload=payload, now=now)

    def passport_events(self) -> list[Mapping[str, object]]:
        rows = self._conn.execute(
            "SELECT seq, event_type, payload, prev_sha256, event_sha256, created_at "
            "FROM passport_events ORDER BY seq"
        ).fetchall()
        events: list[Mapping[str, object]] = []
        for row in rows:
            record = dict(row)
            try:
                record["payload"] = json.loads(str(record["payload"]))
            except (TypeError, ValueError) as error:
                raise _reject(
                    RiskReason.PASSPORT_VERIFICATION_FAILED,
                    "passport.payload",
                    f"stored payload is malformed: {error}",
                ) from None
            events.append(record)
        return events

    # -- immutable candidates and abstentions -------------------------------

    def candidate_for_event(self, event_id: str) -> Mapping[str, object] | None:
        event = _identifier(event_id, "candidate.event_id")
        row = self._conn.execute(
            "SELECT event_id, candidate_id, policy_sha256, decision_sha256, expression_sha256, "
            "evidence_mode, created_at FROM candidates WHERE event_id=?",
            (event,),
        ).fetchone()
        return None if row is None else dict(row)

    def _candidate_exists(self, event_id: str) -> bool:
        return (
            self._conn.execute("SELECT 1 FROM candidates WHERE event_id=?", (event_id,)).fetchone()
            is not None
        )

    def _not_run_exists(self, event_id: str) -> bool:
        return (
            self._conn.execute("SELECT 1 FROM not_run WHERE event_id=?", (event_id,)).fetchone()
            is not None
        )

    def _insert_candidate(
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
            "INSERT INTO candidates (event_id, candidate_id, policy_sha256, decision_sha256, "
            "expression_sha256, evidence_mode, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                candidate_id,
                policy_sha256,
                decision_sha256,
                expression_sha256,
                evidence_mode,
                _timestamp(now, "candidate.created_at"),
            ),
        )

    def _validated_candidate(
        self,
        *,
        event_id: object,
        candidate_id: object,
        policy_sha256: object,
        decision_sha256: object,
        expression_sha256: object,
        evidence_mode: object,
        now: object,
    ) -> tuple[str, str, str, str, str, str, datetime]:
        event = _identifier(event_id, "candidate.event_id")
        candidate = _identifier(candidate_id, "candidate.candidate_id")
        policy = _sha256(policy_sha256, "candidate.policy_sha256")
        decision = _sha256(decision_sha256, "candidate.decision_sha256")
        expression = _sha256(expression_sha256, "candidate.expression_sha256")
        mode = _identifier(evidence_mode, "candidate.evidence_mode")
        created = _timestamp(now, "candidate.created_at")
        return (
            event,
            candidate,
            policy,
            decision,
            expression,
            mode,
            datetime.fromisoformat(created.replace("Z", "+00:00")),
        )

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
        """Persist one immutable candidate without creating a passport event.

        Kernel callers should use ``freeze_candidate`` so candidate evidence and
        its passport receipt are atomic. This low-level method exists for
        deterministic migration/import only.
        """

        event, candidate, policy, decision, expression, mode, created = self._validated_candidate(
            event_id=event_id,
            candidate_id=candidate_id,
            policy_sha256=policy_sha256,
            decision_sha256=decision_sha256,
            expression_sha256=expression_sha256,
            evidence_mode=evidence_mode,
            now=now,
        )
        self._begin()
        try:
            if self._candidate_exists(event):
                raise _reject(
                    RiskReason.IMMUTABLE_EVENT_REPLAY,
                    f"candidate.{event}",
                    "candidate identity is first-write immutable",
                )
            if self._not_run_exists(event):
                raise _reject(
                    RiskReason.NOT_RUN_EVENT, f"candidate.{event}", "event is marked NOT_RUN"
                )
            self._insert_candidate(
                event_id=event,
                candidate_id=candidate,
                policy_sha256=policy,
                decision_sha256=decision,
                expression_sha256=expression,
                evidence_mode=mode,
                now=created,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def freeze_candidate(
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
        """Atomically persist an immutable candidate and its passport receipt."""

        event, candidate, policy, decision, expression, mode, created = self._validated_candidate(
            event_id=event_id,
            candidate_id=candidate_id,
            policy_sha256=policy_sha256,
            decision_sha256=decision_sha256,
            expression_sha256=expression_sha256,
            evidence_mode=evidence_mode,
            now=now,
        )
        self._begin()
        try:
            if self._candidate_exists(event):
                raise _reject(
                    RiskReason.IMMUTABLE_EVENT_REPLAY,
                    f"candidate.{event}",
                    "candidate identity is first-write immutable",
                )
            if self._not_run_exists(event):
                raise _reject(
                    RiskReason.NOT_RUN_EVENT, f"candidate.{event}", "event is marked NOT_RUN"
                )
            self._insert_candidate(
                event_id=event,
                candidate_id=candidate,
                policy_sha256=policy,
                decision_sha256=decision,
                expression_sha256=expression,
                evidence_mode=mode,
                now=created,
            )
            self._append_passport(
                event_type=PassportEventType.CANDIDATE_FROZEN.value,
                payload={
                    "event_id": event,
                    "candidate_id": candidate,
                    "risk_policy_sha256": policy,
                    "decision_sha256": decision,
                    "compiled_expression_sha256": expression,
                    "evidence_mode": mode,
                },
                now=created,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def not_run_reason(self, event_id: str) -> str | None:
        event = _identifier(event_id, "not_run.event_id")
        row = self._conn.execute("SELECT reason FROM not_run WHERE event_id=?", (event,)).fetchone()
        return None if row is None else str(row["reason"])

    def _insert_not_run(self, *, event_id: str, reason: str, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO not_run (event_id, reason, created_at) VALUES (?, ?, ?)",
            (event_id, reason, _timestamp(now, "not_run.created_at")),
        )

    def record_not_run(self, *, event_id: str, reason: str, now: datetime) -> None:
        """Record a first-write immutable abstention without a passport event."""

        self._record_not_run(event_id=event_id, reason=reason, now=now, append_passport=False)

    def mark_not_run(self, *, event_id: str, reason: str, now: datetime) -> None:
        """Atomically record a first-write immutable abstention and receipt."""

        self._record_not_run(event_id=event_id, reason=reason, now=now, append_passport=True)

    def _record_not_run(
        self, *, event_id: str, reason: str, now: datetime, append_passport: bool
    ) -> None:
        event = _identifier(event_id, "not_run.event_id")
        detail = _identifier(reason, "not_run.reason")
        created = datetime.fromisoformat(
            _timestamp(now, "not_run.created_at").replace("Z", "+00:00")
        )
        self._begin()
        try:
            if self._not_run_exists(event):
                raise _reject(
                    RiskReason.IMMUTABLE_EVENT_REPLAY,
                    f"not_run.{event}",
                    "NOT_RUN is first-write immutable",
                )
            reservation = self._reservation_for_event(event)
            if reservation is not None:
                raise _reject(
                    RiskReason.EVENT_LIFECYCLE_INVALID,
                    f"not_run.{event}",
                    "an event with a reservation cannot become NOT_RUN",
                )
            self._insert_not_run(event_id=event, reason=detail, now=created)
            if append_passport:
                self._append_passport(
                    event_type=PassportEventType.NOT_RUN.value,
                    payload={"event_id": event, "reason": detail},
                    now=created,
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    # -- reservations, permits, and exposure --------------------------------

    def _reservation_for_event(self, event_id: str) -> Mapping[str, object] | None:
        row = self._conn.execute(
            "SELECT reservation_id, event_id, amount, state, underlying, created_at, updated_at "
            "FROM reservations WHERE event_id=?",
            (event_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def reservation_for_event(self, event_id: str) -> Mapping[str, object] | None:
        return self._reservation_for_event(_identifier(event_id, "reservation.event_id"))

    def reservation_state(self, event_id: str) -> str | None:
        reservation = self.reservation_for_event(event_id)
        return None if reservation is None else str(reservation["state"])

    def _insert_reservation(
        self,
        *,
        reservation_id: str,
        event_id: str,
        amount: Decimal,
        underlying: str,
        now: datetime,
    ) -> None:
        created = _timestamp(now, "reservation.created_at")
        self._conn.execute(
            (
                "INSERT INTO reservations (reservation_id, event_id, amount, state, "
                "created_at, updated_at, underlying) VALUES (?, ?, ?, 'RESERVED', ?, ?, ?)"
            ),
            (reservation_id, event_id, str(amount), created, created, underlying),
        )

    def reserve(self, *, event_id: str, amount: Decimal, underlying: str, now: datetime) -> str:
        """Persist one standalone reservation for migration-level tests only."""

        event = _identifier(event_id, "reservation.event_id")
        value = _amount(amount, f"reservation.{event}.amount")
        symbol = _underlying(underlying, f"reservation.{event}.underlying")
        created = datetime.fromisoformat(
            _timestamp(now, "reservation.created_at").replace("Z", "+00:00")
        )
        reservation_id = f"rsv-{event}"
        self._begin()
        try:
            if self._not_run_exists(event):
                raise _reject(
                    RiskReason.NOT_RUN_EVENT, f"reservation.{event}", "event is marked NOT_RUN"
                )
            if self._reservation_for_event(event) is not None:
                raise _reject(
                    RiskReason.DUPLICATE_EVENT_RESERVATION,
                    f"reservation.{event}",
                    "a reservation already exists for this event",
                )
            self._insert_reservation(
                reservation_id=reservation_id,
                event_id=event,
                amount=value,
                underlying=symbol,
                now=created,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise
        return reservation_id

    def _transition_reservation(
        self, *, event_id: str, from_state: str, to_state: str, now: datetime
    ) -> None:
        event = _identifier(event_id, "reservation.event_id")
        updated = _timestamp(now, "reservation.updated_at")
        self._begin()
        try:
            reservation = self._reservation_for_event(event)
            if reservation is None or str(reservation["state"]) != from_state:
                current = None if reservation is None else str(reservation["state"])
                raise _reject(
                    RiskReason.RESERVATION_NOT_RELEASED,
                    f"reservation.{event}",
                    f"expected {from_state}, found {current}",
                )
            self._conn.execute(
                "UPDATE reservations SET state=?, updated_at=? WHERE event_id=?",
                (to_state, updated, event),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def consume_reservation(self, *, event_id: str, now: datetime) -> None:
        self._transition_reservation(
            event_id=event_id, from_state="RESERVED", to_state="CONSUMED", now=now
        )

    def release_reservation(self, *, event_id: str, now: datetime) -> None:
        self._transition_reservation(
            event_id=event_id, from_state="RESERVED", to_state="RELEASED", now=now
        )

    def _open_reservation_rows(self, *, underlying: str | None = None) -> list[sqlite3.Row]:
        if underlying is None:
            return self._conn.execute(
                "SELECT amount FROM reservations WHERE state IN ('RESERVED', 'CONSUMED')"
            ).fetchall()
        return self._conn.execute(
            (
                "SELECT amount FROM reservations WHERE state IN ('RESERVED', 'CONSUMED') "
                "AND underlying=?"
            ),
            (underlying,),
        ).fetchall()

    def _sum_open_reservations(self, *, underlying: str | None = None) -> Decimal:
        total = Decimal(0)
        for row in self._open_reservation_rows(underlying=underlying):
            raw = str(row["amount"])
            try:
                value = Decimal(raw)
            except (ArithmeticError, ValueError) as error:
                raise _reject(
                    RiskReason.EXPOSURE_NOT_CALCULABLE,
                    "reservations.amount",
                    f"stored amount is malformed: {error}",
                ) from None
            total += _amount(value, "reservations.amount")
        return total

    def open_reservation_total(self) -> Decimal:
        """Include RESERVED and consumed-but-not-broker-flat exposure."""

        return self._sum_open_reservations()

    def open_reservation_total_for_underlying(self, underlying: str) -> Decimal:
        return self._sum_open_reservations(underlying=_underlying(underlying, "underlying"))

    def open_reservation_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM reservations WHERE state IN ('RESERVED', 'CONSUMED')"
        ).fetchone()
        return int(row["count"])

    def entries_today(self, *, now: datetime) -> int:
        day = _timestamp(now, "entries_today.now")[:10]
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM reservations WHERE substr(created_at, 1, 10)=?", (day,)
        ).fetchone()
        return int(row["count"])

    def _permit_for_event(self, event_id: str) -> Mapping[str, object] | None:
        row = self._conn.execute(
            (
                "SELECT permit_id, event_id, reservation_id, permit_sha256, state, "
                "created_at, updated_at FROM permits WHERE event_id=?"
            ),
            (event_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def permit_for_event(self, event_id: str) -> Mapping[str, object] | None:
        return self._permit_for_event(_identifier(event_id, "permit.event_id"))

    def _permit(self, permit_id: str) -> Mapping[str, object] | None:
        row = self._conn.execute(
            (
                "SELECT permit_id, event_id, reservation_id, permit_sha256, state, "
                "created_at, updated_at FROM permits WHERE permit_id=?"
            ),
            (permit_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def permit_state(self, permit_id: str) -> str | None:
        permit = self._permit(_identifier(permit_id, "permit.permit_id"))
        return None if permit is None else str(permit["state"])

    def _insert_permit(
        self,
        *,
        permit_id: str,
        event_id: str,
        reservation_id: str,
        permit_sha256: str,
        now: datetime,
    ) -> None:
        created = _timestamp(now, "permit.created_at")
        self._conn.execute(
            (
                "INSERT INTO permits (permit_id, event_id, reservation_id, permit_sha256, "
                "state, created_at, updated_at) VALUES (?, ?, ?, ?, 'ISSUED', ?, ?)"
            ),
            (permit_id, event_id, reservation_id, permit_sha256, created, created),
        )

    def record_permit(
        self,
        *,
        permit_id: str,
        event_id: str,
        reservation_id: str,
        permit_sha256: str,
        now: datetime,
    ) -> None:
        """Record an ISSUED permit only for its exact RESERVED reservation."""

        permit = _identifier(permit_id, "permit.permit_id")
        event = _identifier(event_id, "permit.event_id")
        reservation_id = _identifier(reservation_id, "permit.reservation_id")
        digest = _sha256(permit_sha256, "permit.permit_sha256")
        created = datetime.fromisoformat(
            _timestamp(now, "permit.created_at").replace("Z", "+00:00")
        )
        self._begin()
        try:
            reservation = self._reservation_for_event(event)
            if (
                reservation is None
                or str(reservation["reservation_id"]) != reservation_id
                or str(reservation["state"]) != "RESERVED"
                or self._permit_for_event(event) is not None
            ):
                raise _reject(
                    RiskReason.EVENT_LIFECYCLE_INVALID,
                    f"permit.{permit}",
                    "permit must own the event's one RESERVED reservation",
                )
            self._insert_permit(
                permit_id=permit,
                event_id=event,
                reservation_id=reservation_id,
                permit_sha256=digest,
                now=created,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def _transition_permit_in_transaction(
        self, *, permit_id: str, expected: str, target: str, now: datetime
    ) -> Mapping[str, object]:
        permit = self._permit(permit_id)
        if permit is None:
            raise _reject(
                RiskReason.PERMIT_LIFECYCLE_INVALID,
                f"permit.{permit_id}",
                "permit does not exist",
            )
        if str(permit["state"]) != expected:
            raise _reject(
                RiskReason.PERMIT_LIFECYCLE_INVALID,
                f"permit.{permit_id}",
                f"expected {expected}, found {permit['state']}",
            )
        self._conn.execute(
            "UPDATE permits SET state=?, updated_at=? WHERE permit_id=?",
            (target, _timestamp(now, "permit.updated_at"), permit_id),
        )
        return permit

    def update_permit_state(self, *, permit_id: str, to_state: str, now: datetime) -> None:
        """Enforce the narrow permit state machine for low-level migration tests."""

        permit = _identifier(permit_id, "permit.permit_id")
        target = _identifier(to_state, "permit.to_state")
        # Terminal states require ``reconcile_observed_order`` so no generic
        # caller can assert a fill/cancel without an identity-bound broker fact.
        allowed = {"ISSUED": {"SUBMITTED"}}
        self._begin()
        try:
            current = self._permit(permit)
            if current is None or target not in allowed.get(str(current["state"]), set()):
                raise _reject(
                    RiskReason.PERMIT_LIFECYCLE_INVALID,
                    f"permit.{permit}",
                    f"illegal transition to {target}",
                )
            self._transition_permit_in_transaction(
                permit_id=permit,
                expected=str(current["state"]),
                target=target,
                now=now,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def record_lifecycle_intent(
        self,
        *,
        permit_id: str,
        phase: str,
        event_id: str,
        open_permit_id: str,
        reservation_id: str,
        correlation_sha256: str,
        policy_sha256: str,
        snapshot_sha256: str,
        account_id: str,
        account_class: str,
        order_class: str,
        client_order_id: str,
        request_sha256: str,
        request_json: str,
        now: datetime,
    ) -> None:
        """Durably claim one lifecycle permit before its broker mutation.

        The unique permit key closes the process-crash replay gap between a
        permit's original risk approval and a broker acknowledgement. It records
        no broker result and therefore cannot be mistaken for a fill claim.
        """

        permit = _identifier(permit_id, "lifecycle_intent.permit_id")
        phase_text = _identifier(phase, "lifecycle_intent.phase")
        if phase_text not in {"OPEN", "CLOSE"}:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "lifecycle_intent.phase",
                "must be OPEN or CLOSE",
            )
        event = _identifier(event_id, "lifecycle_intent.event_id")
        open_permit = _identifier(open_permit_id, "lifecycle_intent.open_permit_id")
        reservation = _identifier(reservation_id, "lifecycle_intent.reservation_id")
        correlation = _sha256(correlation_sha256, "lifecycle_intent.correlation_sha256")
        policy = _sha256(policy_sha256, "lifecycle_intent.policy_sha256")
        snapshot = _sha256(snapshot_sha256, "lifecycle_intent.snapshot_sha256")
        account = _identifier(account_id, "lifecycle_intent.account_id")
        account_type = _identifier(account_class, "lifecycle_intent.account_class")
        order_type = _identifier(order_class, "lifecycle_intent.order_class")
        client_order = _identifier(client_order_id, "lifecycle_intent.client_order_id")
        request_hash = _sha256(request_sha256, "lifecycle_intent.request_sha256")
        if not isinstance(request_json, str):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "lifecycle_intent.request_json",
                "must be canonical JSON text",
            )
        try:
            request_payload = json.loads(request_json)
        except (TypeError, ValueError) as error:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "lifecycle_intent.request_json",
                f"must be valid canonical JSON: {error}",
            ) from None
        if (
            not isinstance(request_payload, Mapping)
            or canonical_json_bytes(request_payload).decode("utf-8") != request_json
            or sha256_bytes(canonical_json_bytes(request_payload)) != request_hash
        ):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "lifecycle_intent.request_json",
                "must match its canonical request hash",
            )
        expected_request_fields = {
            "permit_id": permit,
            "open_permit_id": open_permit,
            "phase": phase_text,
            "event_run_id": event,
            "reservation_id": reservation,
            "correlation_sha256": correlation,
            "policy_sha256": policy,
            "snapshot_sha256": snapshot,
            "account_id": account,
            "account_class": account_type,
            "order_class": order_type,
            "client_order_id": client_order,
        }
        if any(
            request_payload.get(field) != expected
            for field, expected in expected_request_fields.items()
        ):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "lifecycle_intent.request_json",
                "request identity differs from durable intent columns",
            )
        created = datetime.fromisoformat(
            _timestamp(now, "lifecycle_intent.created_at").replace("Z", "+00:00")
        )
        self._begin()
        try:
            created_text = _timestamp(created, "lifecycle_intent.created_at")
            self._conn.execute(
                (
                    "INSERT INTO lifecycle_intents (permit_id, phase, event_id, open_permit_id, "
                    "reservation_id, correlation_sha256, policy_sha256, snapshot_sha256, "
                    "account_id, "
                    "account_class, order_class, client_order_id, request_sha256, request_json, "
                    "state, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'INTENDED', ?, ?)"
                ),
                (
                    permit,
                    phase_text,
                    event,
                    open_permit,
                    reservation,
                    correlation,
                    policy,
                    snapshot,
                    account,
                    account_type,
                    order_type,
                    client_order,
                    request_hash,
                    request_json,
                    created_text,
                    created_text,
                ),
            )
            self._append_passport(
                event_type=PassportEventType.ORDER_INTENDED.value,
                payload={**dict(request_payload), "request_sha256": request_hash},
                now=created,
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise _reject(
                RiskReason.PERMIT_LIFECYCLE_INVALID,
                f"lifecycle_intent.{permit}",
                f"duplicate lifecycle intent: {error}",
            ) from None
        except Exception:
            self._rollback()
            raise

    def lifecycle_intent_for_permit(self, permit_id: str) -> Mapping[str, object] | None:
        """Return one immutable lifecycle intent and its current durable state."""

        permit = _identifier(permit_id, "lifecycle_intent.permit_id")
        row = self._conn.execute(
            (
                "SELECT permit_id, phase, event_id, open_permit_id, reservation_id, "
                "correlation_sha256, policy_sha256, snapshot_sha256, account_id, account_class, "
                "order_class, client_order_id, request_sha256, request_json, broker_order_id, "
                "state, created_at, updated_at "
                "FROM lifecycle_intents WHERE permit_id=?"
            ),
            (permit,),
        ).fetchone()
        return None if row is None else dict(row)

    def lifecycle_intent_for_event_phase(
        self, event_id: str, phase: str
    ) -> Mapping[str, object] | None:
        """Return the one durable OPEN or CLOSE intent for one lifecycle event."""

        event = _identifier(event_id, "lifecycle_intent.event_id")
        phase_text = _identifier(phase, "lifecycle_intent.phase")
        if phase_text not in {"OPEN", "CLOSE"}:
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "lifecycle_intent.phase",
                "must be OPEN or CLOSE",
            )
        row = self._conn.execute(
            (
                "SELECT permit_id, phase, event_id, open_permit_id, reservation_id, "
                "correlation_sha256, policy_sha256, snapshot_sha256, account_id, account_class, "
                "order_class, client_order_id, request_sha256, request_json, broker_order_id, "
                "state, created_at, updated_at "
                "FROM lifecycle_intents WHERE event_id=? AND phase=?"
            ),
            (event, phase_text),
        ).fetchone()
        return None if row is None else dict(row)

    def bind_lifecycle_intent(
        self,
        *,
        permit_id: str,
        phase: str,
        broker_order_id: str,
        now: datetime,
    ) -> None:
        """Bind an acknowledged broker order to exactly one prior intent."""

        permit = _identifier(permit_id, "lifecycle_intent.permit_id")
        phase_text = _identifier(phase, "lifecycle_intent.phase")
        order = _identifier(broker_order_id, "lifecycle_intent.broker_order_id")
        updated = datetime.fromisoformat(
            _timestamp(now, "lifecycle_intent.updated_at").replace("Z", "+00:00")
        )
        self._begin()
        try:
            intent = self.lifecycle_intent_for_permit(permit)
            if (
                intent is None
                or str(intent["phase"]) != phase_text
                or str(intent["state"]) != "INTENDED"
            ):
                raise _reject(
                    RiskReason.PERMIT_LIFECYCLE_INVALID,
                    f"lifecycle_intent.{permit}",
                    "an unbound matching durable intent is required",
                )
            self._conn.execute(
                "UPDATE lifecycle_intents SET broker_order_id=?, state='SUBMITTED', updated_at=? "
                "WHERE permit_id=?",
                (order, _timestamp(updated, "lifecycle_intent.updated_at"), permit),
            )
            try:
                request_payload = json.loads(str(intent["request_json"]))
            except (TypeError, ValueError) as error:
                raise _reject(
                    RiskReason.PERMIT_LIFECYCLE_INVALID,
                    f"lifecycle_intent.{permit}.request_json",
                    f"stored request payload is malformed: {error}",
                ) from None
            if not isinstance(request_payload, Mapping):
                raise _reject(
                    RiskReason.PERMIT_LIFECYCLE_INVALID,
                    f"lifecycle_intent.{permit}.request_json",
                    "stored request payload is not an object",
                )
            self._append_passport(
                event_type=PassportEventType.ORDER_SUBMITTED.value,
                payload={
                    **dict(request_payload),
                    "request_sha256": str(intent["request_sha256"]),
                    "broker_order_id": order,
                },
                now=updated,
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise _reject(
                RiskReason.PERMIT_LIFECYCLE_INVALID,
                f"lifecycle_intent.{permit}",
                f"duplicate lifecycle broker order: {error}",
            ) from None
        except Exception:
            self._rollback()
            raise

    def mark_lifecycle_intent_reconciled(self, *, permit_id: str, now: datetime) -> None:
        """Mark a previously bound lifecycle intent reconciled after broker proof."""

        permit = _identifier(permit_id, "lifecycle_intent.permit_id")
        updated = datetime.fromisoformat(
            _timestamp(now, "lifecycle_intent.updated_at").replace("Z", "+00:00")
        )
        self._begin()
        try:
            intent = self.lifecycle_intent_for_permit(permit)
            if intent is None or str(intent["state"]) != "SUBMITTED":
                raise _reject(
                    RiskReason.PERMIT_LIFECYCLE_INVALID,
                    f"lifecycle_intent.{permit}",
                    "only a bound lifecycle intent can be reconciled",
                )
            self._conn.execute(
                "UPDATE lifecycle_intents SET state='RECONCILED', updated_at=? WHERE permit_id=?",
                (_timestamp(updated, "lifecycle_intent.updated_at"), permit),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def record_submission(
        self,
        *,
        event_id: str,
        permit_id: str,
        broker_order_id: str,
        now: datetime,
        append_passport: bool = True,
    ) -> None:
        """Bind an ISSUED permit to exactly one external broker order identity."""

        event = _identifier(event_id, "submission.event_id")
        permit_id = _identifier(permit_id, "submission.permit_id")
        order_id = _identifier(broker_order_id, "submission.broker_order_id")
        submitted = datetime.fromisoformat(
            _timestamp(now, "submission.submitted_at").replace("Z", "+00:00")
        )
        self._begin()
        try:
            permit = self._permit(permit_id)
            if permit is None or str(permit["event_id"]) != event:
                raise _reject(
                    RiskReason.EVENT_LIFECYCLE_INVALID,
                    f"submission.{permit_id}",
                    "permit does not own this event",
                )
            reservation = self._reservation_for_event(event)
            if (
                reservation is None
                or str(reservation["reservation_id"]) != str(permit["reservation_id"])
                or str(reservation["state"]) != "RESERVED"
            ):
                raise _reject(
                    RiskReason.EVENT_LIFECYCLE_INVALID,
                    f"submission.{permit_id}",
                    "permit reservation is not RESERVED",
                )
            self._transition_permit_in_transaction(
                permit_id=permit_id,
                expected="ISSUED",
                target="SUBMITTED",
                now=submitted,
            )
            self._conn.execute(
                "INSERT INTO submissions (permit_id, event_id, broker_order_id, submitted_at) "
                "VALUES (?, ?, ?, ?)",
                (permit_id, event, order_id, _timestamp(submitted, "submission.submitted_at")),
            )
            if append_passport:
                self._append_passport(
                    event_type=PassportEventType.ORDER_SUBMITTED.value,
                    payload={
                        "event_id": event,
                        "permit_id": permit_id,
                        "broker_order_id": order_id,
                    },
                    now=submitted,
                )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise _reject(
                RiskReason.PERMIT_LIFECYCLE_INVALID,
                f"submission.{permit_id}",
                f"duplicate or invalid submission: {error}",
            ) from None
        except Exception:
            self._rollback()
            raise

    def _submission_for_permit(self, permit_id: str) -> Mapping[str, object] | None:
        row = self._conn.execute(
            (
                "SELECT permit_id, event_id, broker_order_id, submitted_at "
                "FROM submissions WHERE permit_id=?"
            ),
            (permit_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def submission_for_permit(self, permit_id: str) -> Mapping[str, object] | None:
        """Return the durable broker-order identity for one submitted permit."""

        return self._submission_for_permit(_identifier(permit_id, "submission.permit_id"))

    def record_fill(
        self,
        *,
        fill_id: str,
        permit_id: str,
        event_id: str,
        quantity: Decimal,
        status: str,
        observed_at: datetime,
    ) -> None:
        """Record a broker-observed fill identity without a terminal transition."""

        fill = _identifier(fill_id, "fill.fill_id")
        permit = _identifier(permit_id, "fill.permit_id")
        event = _identifier(event_id, "fill.event_id")
        value = _amount(quantity, "fill.quantity")
        state = _identifier(status, "fill.status")
        observed = datetime.fromisoformat(
            _timestamp(observed_at, "fill.observed_at").replace("Z", "+00:00")
        )
        self._begin()
        try:
            owner = self._permit(permit)
            if owner is None or str(owner["event_id"]) != event:
                raise _reject(
                    RiskReason.EVENT_LIFECYCLE_INVALID, f"fill.{fill}", "permit/event mismatch"
                )
            self._conn.execute(
                "INSERT INTO fills (fill_id, permit_id, event_id, quantity, status, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fill, permit, event, str(value), state, _timestamp(observed, "fill.observed_at")),
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise _reject(
                RiskReason.PERMIT_LIFECYCLE_INVALID,
                f"fill.{fill}",
                f"fill is already recorded: {error}",
            ) from None
        except Exception:
            self._rollback()
            raise

    def fills_for_permit(self, permit_id: str) -> list[Mapping[str, object]]:
        permit = _identifier(permit_id, "fill.permit_id")
        rows = self._conn.execute(
            (
                "SELECT fill_id, quantity, status, observed_at FROM fills "
                "WHERE permit_id=? ORDER BY rowid"
            ),
            (permit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def reconcile_observed_order(
        self,
        *,
        event_id: str,
        permit_id: str,
        broker_order_id: str,
        status: str,
        filled_quantity: Decimal,
        observed_at: datetime,
        now: datetime,
    ) -> None:
        """Terminally reconcile one fresh, identity-bound broker order observation."""

        event = _identifier(event_id, "reconcile.event_id")
        permit_id = _identifier(permit_id, "reconcile.permit_id")
        order_id = _identifier(broker_order_id, "reconcile.broker_order_id")
        observed_status = _identifier(status, "reconcile.status")
        quantity = _amount(filled_quantity, "reconcile.filled_quantity")
        observed = datetime.fromisoformat(
            _timestamp(observed_at, "reconcile.observed_at").replace("Z", "+00:00")
        )
        current = datetime.fromisoformat(_timestamp(now, "reconcile.now").replace("Z", "+00:00"))
        self._begin()
        try:
            permit = self._permit(permit_id)
            if permit is None or str(permit["event_id"]) != event:
                raise _reject(
                    RiskReason.EVENT_LIFECYCLE_INVALID,
                    f"reconcile.{permit_id}",
                    "permit does not own this event",
                )
            submission = self._submission_for_permit(permit_id)
            if (
                submission is None
                or str(submission["event_id"]) != event
                or str(submission["broker_order_id"]) != order_id
            ):
                raise _reject(
                    RiskReason.CONTRADICTORY_TRUTH,
                    f"reconcile.{permit_id}",
                    "observed order is not the permit's submitted broker order",
                )
            if str(permit["state"]) != "SUBMITTED":
                raise _reject(
                    RiskReason.PERMIT_LIFECYCLE_INVALID,
                    f"reconcile.{permit_id}",
                    f"expected SUBMITTED, found {permit['state']}",
                )
            reservation = self._reservation_for_event(event)
            if (
                reservation is None
                or str(reservation["reservation_id"]) != str(permit["reservation_id"])
                or str(reservation["state"]) != "RESERVED"
            ):
                raise _reject(
                    RiskReason.EVENT_LIFECYCLE_INVALID,
                    f"reconcile.{permit_id}",
                    "permit reservation is not RESERVED",
                )
            if observed_status == "FILLED":
                if quantity <= 0:
                    raise _reject(
                        RiskReason.CONTRADICTORY_TRUTH,
                        f"reconcile.{permit_id}",
                        "FILLED broker order must have positive filled quantity",
                    )
                terminal = "FILLED"
                reservation_state = "CONSUMED"
                passport_event = PassportEventType.FILL_OBSERVED.value
            elif observed_status == "CANCELED" and quantity == 0:
                terminal = "CANCELLED"
                reservation_state = "RELEASED"
                passport_event = PassportEventType.RESERVATION_RELEASED.value
            elif observed_status == "PARTIALLY_FILLED" or quantity > 0:
                raise _reject(
                    RiskReason.PARTIAL_FILL_STATE,
                    f"reconcile.{permit_id}",
                    "partial/ambiguous broker order cannot release a reservation",
                )
            else:
                raise _reject(
                    RiskReason.CONTRADICTORY_TRUTH,
                    f"reconcile.{permit_id}",
                    f"unsupported terminal order status {observed_status}",
                )
            self._transition_permit_in_transaction(
                permit_id=permit_id,
                expected="SUBMITTED",
                target=terminal,
                now=current,
            )
            self._conn.execute(
                "UPDATE reservations SET state=?, updated_at=? WHERE event_id=?",
                (reservation_state, _timestamp(current, "reservation.updated_at"), event),
            )
            self._conn.execute(
                "INSERT INTO fills (fill_id, permit_id, event_id, quantity, status, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    permit_id,
                    event,
                    str(quantity),
                    observed_status,
                    _timestamp(observed, "reconcile.observed_at"),
                ),
            )
            self._append_passport(
                event_type=passport_event,
                payload={
                    "event_id": event,
                    "permit_id": permit_id,
                    "broker_order_id": order_id,
                    "status": observed_status,
                    "filled_quantity": str(quantity),
                },
                now=current,
            )
            self._append_passport(
                event_type=PassportEventType.RECONCILED.value,
                payload={"event_id": event, "permit_id": permit_id, "result": terminal},
                now=current,
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise _reject(
                RiskReason.PERMIT_LIFECYCLE_INVALID,
                f"reconcile.{permit_id}",
                f"replayed broker order observation: {error}",
            ) from None
        except Exception:
            self._rollback()
            raise

    def release_consumed_after_flat(self, *, event_id: str, permit_id: str, now: datetime) -> None:
        """Release only a FILLED reservation after a separate broker-flat proof."""

        event = _identifier(event_id, "flat.event_id")
        permit_id = _identifier(permit_id, "flat.permit_id")
        current = datetime.fromisoformat(_timestamp(now, "flat.now").replace("Z", "+00:00"))
        self._begin()
        try:
            permit = self._permit(permit_id)
            reservation = self._reservation_for_event(event)
            if (
                permit is None
                or str(permit["event_id"]) != event
                or str(permit["state"]) != "FILLED"
                or reservation is None
                or str(reservation["reservation_id"]) != str(permit["reservation_id"])
                or str(reservation["state"]) != "CONSUMED"
            ):
                raise _reject(
                    RiskReason.EVENT_LIFECYCLE_INVALID,
                    f"flat.{permit_id}",
                    "only a FILLED permit's CONSUMED reservation can become flat",
                )
            self._conn.execute(
                "UPDATE reservations SET state='RELEASED', updated_at=? WHERE event_id=?",
                (_timestamp(current, "reservation.updated_at"), event),
            )
            self._append_passport(
                event_type=PassportEventType.RECONCILED.value,
                payload={"event_id": event, "permit_id": permit_id, "result": "FLAT"},
                now=current,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def reserve_and_issue_permit(
        self,
        *,
        event_id: str,
        underlying: str,
        candidate_id: str,
        risk_policy_sha256: str,
        decision_sha256: str,
        expression_sha256: str,
        amount: Decimal,
        aggregate_limit: Decimal,
        max_open_expressions: int,
        max_entries_per_day: int,
        permit: DebitVerticalPermit,
        now: datetime,
    ) -> tuple[str, str, str]:
        """Atomically recheck budgets and persist reservation, permit, and receipts.

        The ``BEGIN IMMEDIATE`` scope is deliberately wider than the insert: all
        concurrent callers see candidate/not-run state, aggregate exposure,
        expression count, and entry count from one serializable local view.
        """

        event = _identifier(event_id, "authorization.event_id")
        symbol = _underlying(underlying, "authorization.underlying")
        candidate = _identifier(candidate_id, "authorization.candidate_id")
        policy = _sha256(risk_policy_sha256, "authorization.risk_policy_sha256")
        decision = _sha256(decision_sha256, "authorization.decision_sha256")
        expression = _sha256(expression_sha256, "authorization.expression_sha256")
        exposure = _amount(amount, "authorization.amount")
        aggregate = _positive_limit(aggregate_limit, "authorization.aggregate_limit")
        max_open = _positive_int(max_open_expressions, "authorization.max_open_expressions")
        max_entries = _positive_int(max_entries_per_day, "authorization.max_entries_per_day")
        if not isinstance(permit, DebitVerticalPermit):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "authorization.permit",
                "must be an exact DebitVerticalPermit",
            )
        if permit.permit_id != debit_vertical_permit_id(permit):
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "authorization.permit_id",
                "must equal the canonical permit identity",
            )
        issued_permit_id = permit.permit_id
        permit_sha256 = sha256_bytes(debit_vertical_permit_bytes(permit))
        created = datetime.fromisoformat(
            _timestamp(now, "authorization.now").replace("Z", "+00:00")
        )

        self._begin()
        try:
            if self._not_run_exists(event):
                raise _reject(RiskReason.NOT_RUN_EVENT, f"event.{event}", "event is marked NOT_RUN")
            frozen = self.candidate_for_event(event)
            if frozen is None:
                raise _reject(
                    RiskReason.UNSUPPORTED_INPUT,
                    f"candidate.{event}",
                    "a matching immutable candidate was not frozen",
                )
            if frozen["policy_sha256"] != policy:
                raise _reject(
                    RiskReason.POLICY_HASH_MISMATCH,
                    f"candidate.{event}.policy_sha256",
                    "frozen candidate does not bind the active policy",
                )
            for field, expected in {
                "candidate_id": candidate,
                "decision_sha256": decision,
                "expression_sha256": expression,
            }.items():
                if frozen[field] != expected:
                    raise _reject(
                        RiskReason.UNSUPPORTED_INPUT,
                        f"candidate.{event}.{field}",
                        "authorization identity differs from the frozen candidate",
                    )
            if self._reservation_for_event(event) is not None:
                raise _reject(
                    RiskReason.DUPLICATE_EVENT_RESERVATION,
                    f"reservation.{event}",
                    "a reservation already exists for this event",
                )
            if self.entries_today(now=created) >= max_entries:
                raise _reject(
                    RiskReason.ENTRY_COUNT_LIMIT_REACHED,
                    "entries_today",
                    "entry count has reached the policy limit",
                )
            if self.open_reservation_count() >= max_open:
                raise _reject(
                    RiskReason.EXPRESSION_LIMIT_REACHED,
                    "open_expressions",
                    "open expression count has reached the policy limit",
                )
            held = self._sum_open_reservations()
            if held + exposure > aggregate:
                raise _reject(
                    RiskReason.BUDGET_EXCEEDED,
                    "aggregate_exposure",
                    "aggregate exposure exceeds the policy limit",
                )

            reservation_id = f"rsv-{event}"
            self._insert_reservation(
                reservation_id=reservation_id,
                event_id=event,
                amount=exposure,
                underlying=symbol,
                now=created,
            )
            self._insert_permit(
                permit_id=issued_permit_id,
                event_id=event,
                reservation_id=reservation_id,
                permit_sha256=permit_sha256,
                now=created,
            )
            payload = {
                "event_id": event,
                "candidate_id": candidate,
                "reservation_id": reservation_id,
                "permit_id": issued_permit_id,
                "permit_sha256": permit_sha256,
                "risk_policy_sha256": policy,
                "decision_sha256": decision,
                "compiled_expression_sha256": expression,
                "exposure": str(exposure),
            }
            self._append_passport(
                event_type=PassportEventType.RESERVATION_HELD.value,
                payload=payload,
                now=created,
            )
            self._append_passport(
                event_type=PassportEventType.PERMIT_ISSUED.value,
                payload=payload,
                now=created,
            )
            self._conn.execute("COMMIT")
            return reservation_id, issued_permit_id, permit_sha256
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise _reject(
                RiskReason.DUPLICATE_EVENT_RESERVATION,
                f"reservation.{event}",
                f"duplicate or inconsistent reservation: {error}",
            ) from None
        except Exception:
            self._rollback()
            raise

    # -- snapshots and reconciliation records -------------------------------

    def record_position(self, *, underlying: str, quantity: Decimal, observed_at: datetime) -> None:
        symbol = _underlying(underlying, "position.underlying")
        if not isinstance(quantity, Decimal) or not quantity.is_finite():
            raise _reject(
                RiskReason.UNSUPPORTED_INPUT,
                "position.quantity",
                "must be a finite Decimal",
            )
        self._conn.execute(
            "INSERT OR REPLACE INTO positions (underlying, quantity, observed_at) VALUES (?, ?, ?)",
            (symbol, str(quantity), _timestamp(observed_at, "position.observed_at")),
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
        self._conn.execute(
            (
                "INSERT INTO reconciliations (reconciliation_id, result, detail, paper_pnl, "
                "shadow_pnl, observed_at) VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (
                _identifier(reconciliation_id, "reconciliation.id"),
                _identifier(result, "reconciliation.result"),
                detail,
                paper_pnl,
                shadow_pnl,
                _timestamp(observed_at, "reconciliation.observed_at"),
            ),
        )

    def record_account_snapshot(self, *, equity: Decimal, now: datetime) -> None:
        value = _finite_decimal(equity, "account.equity")
        self._conn.execute(
            "INSERT INTO account_snapshots (equity, observed_at) VALUES (?, ?)",
            (str(value), _timestamp(now, "account.observed_at")),
        )

    def intraday_peak_equity(self, *, now: datetime) -> Decimal:
        day = _timestamp(now, "intraday_peak.now")[:10]
        rows = self._conn.execute(
            "SELECT equity FROM account_snapshots WHERE substr(observed_at, 1, 10)=?", (day,)
        ).fetchall()
        peak: Decimal | None = None
        for row in rows:
            raw = str(row["equity"])
            try:
                value = Decimal(raw)
            except (ArithmeticError, ValueError) as error:
                raise _reject(
                    RiskReason.EXPOSURE_NOT_CALCULABLE,
                    "account_snapshots.equity",
                    f"stored equity is malformed: {error}",
                ) from None
            value = _finite_decimal(value, "account_snapshots.equity")
            if peak is None or value > peak:
                peak = value
        return Decimal(0) if peak is None else peak

    # -- control state -------------------------------------------------------

    def set_control_state(self, *, state: ControlState, reason: str | None, now: datetime) -> None:
        if not isinstance(state, ControlState):
            raise _reject(RiskReason.UNSUPPORTED_INPUT, "control_state", "must be a ControlState")
        self._conn.execute(
            (
                "INSERT OR REPLACE INTO control_state (id, state, reason, updated_at) "
                "VALUES (1, ?, ?, ?)"
            ),
            (state.value, reason, _timestamp(now, "control_state.updated_at")),
        )

    def set_control_state_with_passport(
        self,
        *,
        state: ControlState,
        reason: str | None,
        event_payload: Mapping[str, object],
        now: datetime,
    ) -> None:
        """Persist state and its passport receipt in one transaction."""

        if not isinstance(state, ControlState):
            raise _reject(RiskReason.UNSUPPORTED_INPUT, "control_state", "must be a ControlState")
        self._begin()
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO control_state (id, state, reason, updated_at) "
                "VALUES (1, ?, ?, ?)",
                (state.value, reason, _timestamp(now, "control_state.updated_at")),
            )
            self._append_passport(
                event_type=PassportEventType.CONTROL_STATE_CHANGED.value,
                payload=event_payload,
                now=now,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def get_control_state(self) -> tuple[ControlState, str | None]:
        row = self._conn.execute("SELECT state, reason FROM control_state WHERE id=1").fetchone()
        if row is None:
            return ControlState.ENTRY_DISABLED, "startup reconciliation is required"
        try:
            return ControlState(str(row["state"])), row["reason"]
        except ValueError as error:
            raise _reject(
                RiskReason.CONTRADICTORY_TRUTH,
                "control_state.state",
                f"stored state is malformed: {error}",
            ) from None
