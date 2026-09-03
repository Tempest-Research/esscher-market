"""Hash-chained append-only prospective signal ledger for frozen panel universes.

The ledger registers the complete frozen event universe before any outcome may
be accessed, appends bounded signals immutably, and separates read-only outcome
inspection into its own entry kind.  Once an outcome inspection is recorded, or
once an event's outcome window has closed, no later signal can be appended for
that event; the frozen event set can never be added to, reduced, or relabeled.
Every entry self-hashes over its predecessor, so tampering, reordering, and
truncation are detectable offline.  All timestamps are injected; the module
never reads a wall clock, a provider, an account, or a broker.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

from .models import Direction

PROSPECTIVE_LEDGER_ENTRY_SCHEMA: Final = "esscher.prospective_ledger_entry"
PROSPECTIVE_LEDGER_SCHEMA_VERSION: Final = 1
LEDGER_GENESIS_SHA256: Final = "0" * 64
LEDGER_CLAIMS: Final = ("NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION", "SYNTHETIC_FAKE")

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


class LedgerEntryKind(StrEnum):
    """The only entry kinds the prospective ledger may contain."""

    FREEZE = "FREEZE"
    SIGNAL = "SIGNAL"
    OUTCOME_INSPECTION = "OUTCOME_INSPECTION"


class ProspectiveLedgerReason(StrEnum):
    """Stable machine-readable fail-closed reasons for ledger operations."""

    FREEZE_REQUIRED = "FREEZE_REQUIRED"
    DUPLICATE_FREEZE = "DUPLICATE_FREEZE"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    EVENT_ADDED_AFTER_FREEZE = "EVENT_ADDED_AFTER_FREEZE"
    EVENT_REMOVED_AFTER_FREEZE = "EVENT_REMOVED_AFTER_FREEZE"
    EVENT_RELABELED_AFTER_FREEZE = "EVENT_RELABELED_AFTER_FREEZE"
    UNKNOWN_EVENT = "UNKNOWN_EVENT"
    DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"
    LATE_SIGNAL_AFTER_OUTCOME_WINDOW = "LATE_SIGNAL_AFTER_OUTCOME_WINDOW"
    SIGNAL_AFTER_OUTCOME_INSPECTION = "SIGNAL_AFTER_OUTCOME_INSPECTION"
    MALFORMED_REGISTRATION = "MALFORMED_REGISTRATION"
    MALFORMED_ENTRY = "MALFORMED_ENTRY"
    ENTRY_TAMPERED = "ENTRY_TAMPERED"
    CHAIN_BROKEN = "CHAIN_BROKEN"
    CLOCK_INVALID = "CLOCK_INVALID"


class ProspectiveLedgerRejected(ValueError):
    """Raised when a ledger operation would break the prospective contract."""

    def __init__(self, reason: ProspectiveLedgerReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_clock(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProspectiveLedgerRejected(
            ProspectiveLedgerReason.CLOCK_INVALID, f"{field} must be a timezone-aware instant"
        )
    return value.astimezone(UTC)


def _require_sha(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ProspectiveLedgerRejected(
            ProspectiveLedgerReason.MALFORMED_REGISTRATION, f"{field} must be a lowercase SHA-256"
        )
    return value


def _require_text(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProspectiveLedgerRejected(
            ProspectiveLedgerReason.MALFORMED_REGISTRATION, f"{field} must be non-empty text"
        )
    return value


@dataclass(frozen=True, slots=True)
class FrozenEventRegistration:
    """One frozen event as registered before any outcome access."""

    event_id: str
    sector: str
    decision_cutoff: datetime
    outcome_window_close: datetime
    source_manifest_sha256: str
    strategy_identity_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.event_id, field="event_id")
        _require_text(self.sector, field="sector")
        _require_clock(self.decision_cutoff, field="decision_cutoff")
        _require_clock(self.outcome_window_close, field="outcome_window_close")
        _require_sha(self.source_manifest_sha256, field="source_manifest_sha256")
        _require_sha(self.strategy_identity_sha256, field="strategy_identity_sha256")
        if self.outcome_window_close <= self.decision_cutoff:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.MALFORMED_REGISTRATION,
                f"event {self.event_id} outcome window must close after its decision cutoff",
            )

    def payload(self) -> dict[str, object]:
        """Return the canonical frozen-event payload embedded in the freeze entry."""

        return {
            "decision_cutoff": _iso(self.decision_cutoff),
            "event_id": self.event_id,
            "outcome_window_close": _iso(self.outcome_window_close),
            "sector": self.sector,
            "source_manifest_sha256": self.source_manifest_sha256,
            "strategy_identity_sha256": self.strategy_identity_sha256,
        }


def _freeze_event_payloads(events: Sequence[FrozenEventRegistration]) -> list[dict[str, object]]:
    seen: set[str] = set()
    payloads: list[dict[str, object]] = []
    for registration in events:
        if type(registration) is not FrozenEventRegistration:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.MALFORMED_REGISTRATION,
                "freeze entries require FrozenEventRegistration values",
            )
        if registration.event_id in seen:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.DUPLICATE_EVENT,
                f"event {registration.event_id} appears twice in one freeze entry",
            )
        seen.add(registration.event_id)
        payloads.append(registration.payload())
    if not payloads:
        raise ProspectiveLedgerRejected(
            ProspectiveLedgerReason.MALFORMED_REGISTRATION,
            "a freeze entry requires at least one event",
        )
    return payloads


def ledger_entry_unsigned_payload(
    kind: LedgerEntryKind,
    *,
    prior_entry_sha256: str,
    recorded_at: datetime,
    body: Mapping[str, object],
) -> dict[str, object]:
    """Return the unsigned canonical payload of one ledger entry."""

    return {
        "claims": list(LEDGER_CLAIMS),
        "kind": kind.value,
        "prior_entry_sha256": _require_sha(prior_entry_sha256, field="prior_entry_sha256"),
        "recorded_at": _iso(_require_clock(recorded_at, field="recorded_at")),
        "schema": PROSPECTIVE_LEDGER_ENTRY_SCHEMA,
        "schema_version": PROSPECTIVE_LEDGER_SCHEMA_VERSION,
        **body,
    }


def ledger_entry_bytes(unsigned: Mapping[str, object]) -> bytes:
    """Serialize one unsigned ledger entry deterministically."""

    return canonical_json_bytes(dict(unsigned))


def ledger_entry_sha256(unsigned: Mapping[str, object]) -> str:
    """Content-address one unsigned ledger entry."""

    return sha256_bytes(ledger_entry_bytes(unsigned))


def ledger_line_bytes(unsigned: Mapping[str, object]) -> bytes:
    """Return the exact append-only JSONL line for one unsigned entry."""

    digest = ledger_entry_sha256(unsigned)
    return canonical_json_bytes({**dict(unsigned), "entry_sha256": digest}) + b"\n"


def parse_ledger_line(raw: bytes, *, index: int) -> dict[str, object]:
    """Parse one JSONL ledger line or fail closed."""

    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProspectiveLedgerRejected(
            ProspectiveLedgerReason.MALFORMED_ENTRY,
            f"line {index} is not strict UTF-8 JSON: {error}",
        ) from error
    if not isinstance(record, dict):
        raise ProspectiveLedgerRejected(
            ProspectiveLedgerReason.MALFORMED_ENTRY, f"line {index} must be a JSON object"
        )
    return record


@dataclass(frozen=True, slots=True)
class LedgerChainVerification:
    """The deterministic result of one offline chain verification."""

    valid: bool
    entry_count: int
    head_sha256: str
    reason: ProspectiveLedgerReason | None
    detail: str | None


def verify_ledger_bytes(raw: bytes) -> LedgerChainVerification:
    """Verify one complete ledger file's hash chain from genesis offline."""

    lines = [line for line in raw.split(b"\n") if line]
    prior = LEDGER_GENESIS_SHA256
    head = LEDGER_GENESIS_SHA256
    for index, line in enumerate(lines):
        try:
            record = parse_ledger_line(line, index=index)
        except ProspectiveLedgerRejected as error:
            return LedgerChainVerification(False, index, head, error.reason, error.detail)
        digest = record.pop("entry_sha256", None)
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            return LedgerChainVerification(
                False,
                index,
                head,
                ProspectiveLedgerReason.ENTRY_TAMPERED,
                f"line {index} carries no valid entry_sha256",
            )
        if ledger_entry_sha256(record) != digest:
            return LedgerChainVerification(
                False,
                index,
                head,
                ProspectiveLedgerReason.ENTRY_TAMPERED,
                f"line {index} self-hash does not bind its content",
            )
        if record.get("prior_entry_sha256") != prior:
            return LedgerChainVerification(
                False,
                index,
                head,
                ProspectiveLedgerReason.CHAIN_BROKEN,
                f"line {index} does not bind predecessor {prior}",
            )
        if index == 0 and record.get("kind") != LedgerEntryKind.FREEZE.value:
            return LedgerChainVerification(
                False,
                index,
                head,
                ProspectiveLedgerReason.CHAIN_BROKEN,
                "the first ledger entry must be the universe freeze",
            )
        prior = digest
        head = digest
    return LedgerChainVerification(True, len(lines), head, None, None)


class ProspectiveLedger:
    """One append-only prospective signal ledger over a frozen event universe."""

    def __init__(self, path: str | Path) -> None:
        """Open or restart the ledger file; restarts replay the stored chain."""

        self._path = Path(path)
        self._entries: list[dict[str, object]] = []
        self._frozen: dict[str, FrozenEventRegistration] = {}
        self._freeze_order: tuple[str, ...] = ()
        self._freeze_payload: list[dict[str, object]] | None = None
        self._signals: dict[str, dict[str, object]] = {}
        self._inspections: dict[str, list[dict[str, object]]] = {}
        self._head = LEDGER_GENESIS_SHA256
        if self._path.is_file():
            self._replay(self._path.read_bytes())

    @property
    def path(self) -> Path:
        """Return the ledger file owned by this instance."""

        return self._path

    @property
    def head_sha256(self) -> str:
        """Return the sha of the latest entry, or genesis when empty."""

        return self._head

    @property
    def frozen_event_ids(self) -> tuple[str, ...]:
        """Return the frozen event IDs in registration order."""

        return self._freeze_order

    def entries(self) -> tuple[dict[str, object], ...]:
        """Return every stored entry payload including its self hash."""

        return tuple(dict(entry) for entry in self._entries)

    def signals(self) -> Mapping[str, Mapping[str, object]]:
        """Return the append-only signal state keyed by event ID."""

        return {event_id: dict(body) for event_id, body in self._signals.items()}

    def inspections(self, event_id: str) -> tuple[Mapping[str, object], ...]:
        """Return every recorded outcome inspection for one event."""

        return tuple(dict(item) for item in self._inspections.get(event_id, ()))

    def verify_chain(self) -> LedgerChainVerification:
        """Re-verify the on-disk chain from genesis; never mutates state."""

        raw = self._path.read_bytes() if self._path.is_file() else b""
        return verify_ledger_bytes(raw)

    def create_freeze_entry(
        self,
        *,
        events: Sequence[FrozenEventRegistration],
        recorded_at: datetime,
    ) -> str:
        """Register the complete frozen universe before any outcome access."""

        payloads = _freeze_event_payloads(events)
        if self._freeze_payload is None:
            return self._append(
                LedgerEntryKind.FREEZE,
                recorded_at=recorded_at,
                body={"events": payloads},
            )
        frozen_by_id = {item["event_id"]: item for item in self._freeze_payload}
        proposed_by_id = {item["event_id"]: item for item in payloads}
        added = sorted(set(proposed_by_id) - set(frozen_by_id))
        removed = sorted(set(frozen_by_id) - set(proposed_by_id))
        if added:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.EVENT_ADDED_AFTER_FREEZE,
                f"events {added} cannot join after the universe freeze",
            )
        if removed:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.EVENT_REMOVED_AFTER_FREEZE,
                f"events {removed} cannot leave after the universe freeze",
            )
        relabeled = sorted(
            event_id
            for event_id in frozen_by_id
            if frozen_by_id[event_id] != proposed_by_id[event_id]
        )
        if relabeled:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.EVENT_RELABELED_AFTER_FREEZE,
                f"events {relabeled} cannot be relabeled after the universe freeze",
            )
        candidate = ledger_entry_unsigned_payload(
            LedgerEntryKind.FREEZE,
            prior_entry_sha256=LEDGER_GENESIS_SHA256,
            recorded_at=recorded_at,
            body={"events": payloads},
        )
        stored = {key: value for key, value in self._entries[0].items() if key != "entry_sha256"}
        if ledger_entry_bytes(candidate) != ledger_entry_bytes(stored):
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.DUPLICATE_FREEZE,
                "the universe freeze is already recorded with different metadata",
            )
        return str(self._entries[0]["entry_sha256"])

    def append_signal(
        self,
        *,
        event_id: str,
        direction: Direction,
        decision_sha256: str,
        receipt_sha256: str,
        observed_at: datetime,
    ) -> str:
        """Append one bounded prospective signal; appends are immutable."""

        self._require_frozen()
        registration = self._frozen.get(event_id)
        if registration is None:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.UNKNOWN_EVENT,
                f"event {event_id} is not part of the frozen universe",
            )
        if event_id in self._signals:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.DUPLICATE_SIGNAL,
                f"event {event_id} already carries an appended signal",
            )
        if self._inspections.get(event_id):
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.SIGNAL_AFTER_OUTCOME_INSPECTION,
                f"event {event_id} outcome was inspected before this signal append",
            )
        observed = _require_clock(observed_at, field="observed_at")
        if observed > registration.outcome_window_close:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.LATE_SIGNAL_AFTER_OUTCOME_WINDOW,
                f"signal for {event_id} was observed after its outcome window closed",
            )
        if not isinstance(direction, Direction):
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.MALFORMED_REGISTRATION,
                "direction must be a bounded Direction value",
            )
        body = {
            "decision_sha256": _require_sha(decision_sha256, field="decision_sha256"),
            "direction": direction.value,
            "event_id": event_id,
            "observed_at": _iso(observed),
            "receipt_sha256": _require_sha(receipt_sha256, field="receipt_sha256"),
        }
        digest = self._append(LedgerEntryKind.SIGNAL, recorded_at=observed, body=body)
        self._signals[event_id] = body
        return digest

    def inspect_outcome(
        self,
        *,
        event_id: str,
        outcome_sha256: str,
        inspected_at: datetime,
    ) -> str:
        """Record one read-only outcome inspection; never mutates signals."""

        self._require_frozen()
        if event_id not in self._frozen:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.UNKNOWN_EVENT,
                f"event {event_id} is not part of the frozen universe",
            )
        inspected = _require_clock(inspected_at, field="inspected_at")
        body = {
            "event_id": event_id,
            "inspected_at": _iso(inspected),
            "outcome_sha256": _require_sha(outcome_sha256, field="outcome_sha256"),
        }
        digest = self._append(LedgerEntryKind.OUTCOME_INSPECTION, recorded_at=inspected, body=body)
        self._inspections.setdefault(event_id, []).append(body)
        return digest

    def _require_frozen(self) -> None:
        if self._freeze_payload is None:
            raise ProspectiveLedgerRejected(
                ProspectiveLedgerReason.FREEZE_REQUIRED,
                "the frozen universe must be registered before any other entry",
            )

    def _append(
        self,
        kind: LedgerEntryKind,
        *,
        recorded_at: datetime,
        body: Mapping[str, object],
    ) -> str:
        unsigned = ledger_entry_unsigned_payload(
            kind,
            prior_entry_sha256=self._head,
            recorded_at=recorded_at,
            body=body,
        )
        line = ledger_line_bytes(unsigned)
        digest = ledger_entry_sha256(unsigned)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as handle:
            handle.write(line)
        self._entries.append({**unsigned, "entry_sha256": digest})
        self._head = digest
        if kind is LedgerEntryKind.FREEZE:
            events = body["events"]
            assert isinstance(events, list)
            self._freeze_payload = [dict(item) for item in events]
            self._freeze_order = tuple(str(item["event_id"]) for item in events)
            self._frozen = {
                str(item["event_id"]): _registration_from_payload(item) for item in events
            }
        return digest

    def _replay(self, raw: bytes) -> None:
        verification = verify_ledger_bytes(raw)
        if not verification.valid:
            reason = verification.reason or ProspectiveLedgerReason.CHAIN_BROKEN
            raise ProspectiveLedgerRejected(reason, verification.detail or "stored chain invalid")
        lines = [line for line in raw.split(b"\n") if line]
        for index, line in enumerate(lines):
            record = parse_ledger_line(line, index=index)
            digest = str(record["entry_sha256"])
            unsigned = {key: value for key, value in record.items() if key != "entry_sha256"}
            self._entries.append(record)
            kind = LedgerEntryKind(str(unsigned["kind"]))
            if kind is LedgerEntryKind.FREEZE:
                events = unsigned["events"]
                assert isinstance(events, list)
                self._freeze_payload = [dict(item) for item in events]
                self._freeze_order = tuple(str(item["event_id"]) for item in events)
                self._frozen = {
                    str(item["event_id"]): _registration_from_payload(item) for item in events
                }
            elif kind is LedgerEntryKind.SIGNAL:
                body = {
                    "decision_sha256": unsigned["decision_sha256"],
                    "direction": unsigned["direction"],
                    "event_id": unsigned["event_id"],
                    "observed_at": unsigned["observed_at"],
                    "receipt_sha256": unsigned["receipt_sha256"],
                }
                self._signals[str(unsigned["event_id"])] = body
            else:
                body = {
                    "event_id": unsigned["event_id"],
                    "inspected_at": unsigned["inspected_at"],
                    "outcome_sha256": unsigned["outcome_sha256"],
                }
                self._inspections.setdefault(str(unsigned["event_id"]), []).append(body)
            self._head = digest


def _registration_from_payload(payload: Mapping[str, object]) -> FrozenEventRegistration:
    return FrozenEventRegistration(
        event_id=str(payload["event_id"]),
        sector=str(payload["sector"]),
        decision_cutoff=datetime.fromisoformat(
            str(payload["decision_cutoff"]).replace("Z", "+00:00")
        ),
        outcome_window_close=datetime.fromisoformat(
            str(payload["outcome_window_close"]).replace("Z", "+00:00")
        ),
        source_manifest_sha256=str(payload["source_manifest_sha256"]),
        strategy_identity_sha256=str(payload["strategy_identity_sha256"]),
    )


__all__ = [
    "LEDGER_CLAIMS",
    "LEDGER_GENESIS_SHA256",
    "PROSPECTIVE_LEDGER_ENTRY_SCHEMA",
    "PROSPECTIVE_LEDGER_SCHEMA_VERSION",
    "FrozenEventRegistration",
    "LedgerChainVerification",
    "LedgerEntryKind",
    "ProspectiveLedger",
    "ProspectiveLedgerReason",
    "ProspectiveLedgerRejected",
    "ledger_entry_bytes",
    "ledger_entry_sha256",
    "ledger_entry_unsigned_payload",
    "ledger_line_bytes",
    "parse_ledger_line",
    "verify_ledger_bytes",
]
