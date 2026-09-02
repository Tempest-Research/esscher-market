"""Hash-chained JSONL sidecar for autonomous host lifecycle rehydration.

The autonomous session store persists only sanitized lifecycle identities.
This sidecar is the companion durable record that lets a fresh process rebuild
the close-critical binding of an already-opened synthetic lifecycle: the exact
canonical permit bytes, exit-plan clock bytes, correlation identity, opening
order identity, and terminal-flat proof.  Every entry is content-addressed and
chained to its predecessor, and the file itself is labelled synthetic: it is
rehearsal state, not broker or alpha evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from ringdown_market.contracts.compiled_to_permit import canonical_permit_sha256
from ringdown_market.execution.models import (
    DataClass,
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    RunMode,
    VerticalType,
    debit_vertical_permit_bytes,
    debit_vertical_permit_id,
)
from ringdown_market.lifecycle.clocks import (
    LifecycleClocks,
    lifecycle_clocks_bytes,
    lifecycle_clocks_sha256,
    parse_lifecycle_clocks,
)
from ringdown_market.lifecycle.correlation import (
    CorrelationIdentity,
    correlation_payload,
    correlation_sha256,
)

HOST_PERSISTENCE_FILENAME = "host_persistence.jsonl"
HOST_PERSISTENCE_SCHEMA = "esscher.autonomous_host_persistence_entry"
HOST_PERSISTENCE_SCHEMA_VERSION = 1
HOST_PERSISTENCE_ACTIVE_KIND = "ACTIVE"
HOST_PERSISTENCE_TERMINAL_KIND = "TERMINAL"
HOST_PERSISTENCE_GENESIS_SHA256 = "0" * 64
HOST_PERSISTENCE_CLAIMS = ("SYNTHETIC_FAKE", "NOT_ALPHA_EVIDENCE")


class HostPersistenceRejected(ValueError):
    """Raised when sidecar state is malformed, non-canonical, or broken."""


@dataclass(frozen=True, slots=True)
class RehydratedActiveBundle:
    """Everything needed to rebuild one close-critical binding after restart."""

    session_id: str
    lifecycle_id: str
    permit: DebitVerticalPermit
    permit_sha256: str
    clocks: LifecycleClocks
    clocks_sha256: str
    correlation: CorrelationIdentity
    correlation_sha256: str
    open_order_id: str
    account_id: str
    application_identity_sha256: str
    opened_at: datetime
    decision_episode_id: str | None


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HostPersistenceRejected("recorded_at must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise HostPersistenceRejected("stored timestamp must be canonical UTC text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: object) -> bytes:
    if not isinstance(value, str):
        raise HostPersistenceRejected("stored sidecar bytes must be base64 text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as error:
        raise HostPersistenceRejected("stored sidecar bytes are not valid base64") from error


def _text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HostPersistenceRejected(f"{path} must be non-empty exact text")
    return value


def _digest(value: object, *, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise HostPersistenceRejected(f"{path} must be a lowercase SHA-256 digest")
    return value


def parse_debit_vertical_permit_bytes(raw: bytes) -> DebitVerticalPermit:
    """Rehydrate one exact canonical opening permit from stored bytes."""

    if type(raw) is not bytes:
        raise HostPersistenceRejected("permit bytes must be immutable bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HostPersistenceRejected("stored permit bytes are not valid JSON") from error
    if not isinstance(payload, dict):
        raise HostPersistenceRejected("stored permit must be a JSON object")
    if (
        payload.get("schema") != "ringdown.paper_execution_permit"
        or payload.get("schema_version") != 1
    ):
        raise HostPersistenceRejected("stored permit has an unsupported schema")
    try:
        raw_legs = payload["legs"]
        if not isinstance(raw_legs, list) or len(raw_legs) != 2:
            raise HostPersistenceRejected("stored permit must carry exactly two legs")
        legs = tuple(
            OptionLeg(
                symbol=str(leg["symbol"]),
                underlying=str(leg["underlying"]),
                expiry=date.fromisoformat(str(leg["expiry"])),
                option_type=OptionType(str(leg["option_type"])),
                strike=Decimal(str(leg["strike"])),
                side=OptionSide(str(leg["side"])),
                position_intent=PositionIntent(str(leg["position_intent"])),
                ratio_qty=int(leg["ratio_qty"]),
            )
            for leg in raw_legs
        )
        provisional = DebitVerticalPermit._from_frozen_decision(
            permit_id="UNBOUND",
            event_run_id=str(payload["event_run_id"]),
            policy_sha256=str(payload["policy_sha256"]),
            snapshot_sha256=str(payload["input_snapshot_sha256"]),
            decision_sha256=str(payload["decision_sha256"]),
            evidence_sha256=str(payload["evidence_sha256"]),
            protocol_sha256=str(payload["protocol_sha256"]),
            execution_protocol_sha256=str(payload["execution_protocol_sha256"]),
            issued_at=datetime.fromisoformat(str(payload["issued_at"])),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
            vertical_type=VerticalType(str(payload["vertical_type"])),
            quantity=int(payload["quantity"]),
            limit_price=Decimal(str(payload["limit_price"])),
            legs=legs,  # type: ignore[arg-type]
            run_mode=RunMode(str(payload["run_mode"])),
            data_class=DataClass(str(payload["data_class"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HostPersistenceRejected(f"stored permit cannot be rebuilt: {error}") from error
    rebuilt = DebitVerticalPermit._from_frozen_decision(
        **{
            **{
                field: getattr(provisional, field)
                for field in (
                    "event_run_id",
                    "policy_sha256",
                    "snapshot_sha256",
                    "decision_sha256",
                    "evidence_sha256",
                    "protocol_sha256",
                    "execution_protocol_sha256",
                    "issued_at",
                    "expires_at",
                    "vertical_type",
                    "quantity",
                    "limit_price",
                    "legs",
                    "run_mode",
                    "data_class",
                )
            },
            "permit_id": str(payload["permit_id"]),
        }
    )
    if debit_vertical_permit_bytes(rebuilt) != raw:
        raise HostPersistenceRejected("stored permit bytes are not canonical")
    if rebuilt.permit_id != debit_vertical_permit_id(rebuilt):
        raise HostPersistenceRejected("stored permit identity is not self-derived")
    return rebuilt


def _parse_correlation(payload: object) -> CorrelationIdentity:
    if not isinstance(payload, dict):
        raise HostPersistenceRejected("stored correlation must be an object")
    try:
        return CorrelationIdentity(
            event_run_id=str(payload["event_run_id"]),
            snapshot_sha256=str(payload["snapshot_sha256"]),
            decision_sha256=str(payload["decision_sha256"]),
            expression_sha256=str(payload["expression_sha256"]),
            reservation_id=str(payload["reservation_id"]),
            open_permit_id=str(payload["open_permit_id"]),
            close_permit_id=(
                None if payload["close_permit_id"] is None else str(payload["close_permit_id"])
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HostPersistenceRejected(f"stored correlation cannot be rebuilt: {error}") from error


class HostPersistenceSidecar:
    """Append-only hash-chained JSONL sidecar under one autonomous state dir."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        if self._path.is_symlink():
            raise HostPersistenceRejected("host persistence sidecar must be a real file")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """Return the sidecar file path."""

        return self._path

    def _prior_entry_sha256(self) -> str:
        entries = self._read_entries()
        if not entries:
            return HOST_PERSISTENCE_GENESIS_SHA256
        return entries[-1]["entry_sha256"]

    def _append(
        self, *, kind: str, lifecycle_id: str, session_id: str, payload: dict, recorded_at: datetime
    ) -> str:
        unsigned = {
            "schema": HOST_PERSISTENCE_SCHEMA,
            "schema_version": HOST_PERSISTENCE_SCHEMA_VERSION,
            "claims": list(HOST_PERSISTENCE_CLAIMS),
            "kind": kind,
            "lifecycle_id": _text(lifecycle_id, path="lifecycle_id"),
            "session_id": _text(session_id, path="session_id"),
            "recorded_at": _timestamp_text(recorded_at),
            "prior_entry_sha256": self._prior_entry_sha256(),
            "payload": payload,
        }
        entry_sha256 = _sha256(_canonical_json(unsigned))
        line = _canonical_json({**unsigned, "entry_sha256": entry_sha256})
        with self._path.open("ab") as handle:
            handle.write(line + b"\n")
            handle.flush()
        return entry_sha256

    def append_active(
        self,
        *,
        lifecycle_id: str,
        session_id: str,
        recorded_at: datetime,
        permit: DebitVerticalPermit,
        clocks: LifecycleClocks,
        correlation: CorrelationIdentity,
        open_order_id: str,
        account_id: str,
        application_identity_sha256: str,
        opened_at: datetime,
        decision_episode_id: str | None = None,
    ) -> str:
        """Persist one active-lifecycle bundle and return its entry identity."""

        permit_bytes = debit_vertical_permit_bytes(permit)
        clocks_bytes = lifecycle_clocks_bytes(clocks)
        payload = {
            "account_id": _text(account_id, path="account_id"),
            "application_identity_sha256": _digest(
                application_identity_sha256, path="application_identity_sha256"
            ),
            "clocks_base64": _b64encode(clocks_bytes),
            "clocks_sha256": lifecycle_clocks_sha256(clocks),
            "correlation_base64": _b64encode(_canonical_json(correlation_payload(correlation))),
            "correlation_sha256": correlation_sha256(correlation),
            "decision_episode_id": decision_episode_id,
            "open_order_id": _text(open_order_id, path="open_order_id"),
            "opened_at": _timestamp_text(opened_at),
            "permit_base64": _b64encode(permit_bytes),
            "permit_sha256": _sha256(permit_bytes),
        }
        return self._append(
            kind=HOST_PERSISTENCE_ACTIVE_KIND,
            lifecycle_id=lifecycle_id,
            session_id=session_id,
            payload=payload,
            recorded_at=recorded_at,
        )

    def append_terminal(
        self,
        *,
        lifecycle_id: str,
        session_id: str,
        recorded_at: datetime,
        terminal_flat_proof_sha256: str,
    ) -> str:
        """Persist one terminal-flat proof and return its entry identity."""

        payload = {
            "terminal_flat_proof_sha256": _digest(
                terminal_flat_proof_sha256, path="terminal_flat_proof_sha256"
            ),
        }
        return self._append(
            kind=HOST_PERSISTENCE_TERMINAL_KIND,
            lifecycle_id=lifecycle_id,
            session_id=session_id,
            payload=payload,
            recorded_at=recorded_at,
        )

    def _read_entries(self) -> list[dict]:
        if not self._path.exists():
            return []
        if self._path.is_symlink():
            raise HostPersistenceRejected("host persistence sidecar must be a real file")
        entries: list[dict] = []
        raw = self._path.read_bytes()
        for line in raw.split(b"\n"):
            if not line:
                continue
            try:
                entry = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise HostPersistenceRejected("sidecar entry is not valid JSON") from error
            if not isinstance(entry, dict):
                raise HostPersistenceRejected("sidecar entry must be a JSON object")
            entries.append(entry)
        return entries

    def _validated_entries(self) -> list[dict]:
        entries = self._read_entries()
        prior = HOST_PERSISTENCE_GENESIS_SHA256
        for index, entry in enumerate(entries):
            stored = entry.get("entry_sha256")
            if not isinstance(stored, str):
                raise HostPersistenceRejected(f"sidecar entry {index} has no identity")
            unsigned = {key: value for key, value in entry.items() if key != "entry_sha256"}
            if unsigned.get("prior_entry_sha256") != prior:
                raise HostPersistenceRejected(f"sidecar entry {index} breaks the hash chain")
            if _sha256(_canonical_json(unsigned)) != stored:
                raise HostPersistenceRejected(f"sidecar entry {index} hash is invalid")
            if unsigned.get("schema") != HOST_PERSISTENCE_SCHEMA:
                raise HostPersistenceRejected(f"sidecar entry {index} has an unsupported schema")
            prior = stored
        return entries

    def chain_valid(self) -> bool:
        """Verify every stored entry hash and predecessor link."""

        try:
            self._validated_entries()
        except HostPersistenceRejected:
            return False
        return True

    def terminal_flat_proof(self, lifecycle_id: str) -> str | None:
        """Return the stored terminal-flat proof for one lifecycle, if any."""

        for entry in reversed(self._validated_entries()):
            if entry.get("kind") != HOST_PERSISTENCE_TERMINAL_KIND:
                continue
            if entry.get("lifecycle_id") != lifecycle_id:
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                raise HostPersistenceRejected("terminal sidecar entry has no payload")
            return str(payload.get("terminal_flat_proof_sha256"))
        return None

    def is_terminal(self, lifecycle_id: str) -> bool:
        """True when a terminal-flat proof is durably recorded for the lifecycle."""

        return self.terminal_flat_proof(lifecycle_id) is not None

    def rehydrate(self, lifecycle_id: str) -> RehydratedActiveBundle | None:
        """Rebuild one active bundle from the sidecar, or None when absent."""

        for entry in reversed(self._validated_entries()):
            if entry.get("kind") != HOST_PERSISTENCE_ACTIVE_KIND:
                continue
            if entry.get("lifecycle_id") != lifecycle_id:
                continue
            return self._bundle(entry)
        return None

    def active_bundles(self, session_id: str) -> tuple[RehydratedActiveBundle, ...]:
        """Return every non-terminal active bundle for one session in chain order."""

        bundles: list[RehydratedActiveBundle] = []
        for entry in self._validated_entries():
            if entry.get("kind") != HOST_PERSISTENCE_ACTIVE_KIND:
                continue
            if entry.get("session_id") != session_id:
                continue
            lifecycle_id = entry.get("lifecycle_id")
            if not isinstance(lifecycle_id, str) or self.is_terminal(lifecycle_id):
                continue
            bundles.append(self._bundle(entry))
        return tuple(bundles)

    def _bundle(self, entry: dict) -> RehydratedActiveBundle:
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            raise HostPersistenceRejected("active sidecar entry has no payload")
        permit = parse_debit_vertical_permit_bytes(_b64decode(payload.get("permit_base64")))
        permit_sha256 = _digest(payload.get("permit_sha256"), path="payload.permit_sha256")
        if canonical_permit_sha256(permit) != permit_sha256:
            raise HostPersistenceRejected("stored permit hash does not bind the stored bytes")
        clocks = parse_lifecycle_clocks(_b64decode(payload.get("clocks_base64")))
        clocks_sha256 = _digest(payload.get("clocks_sha256"), path="payload.clocks_sha256")
        if lifecycle_clocks_sha256(clocks) != clocks_sha256:
            raise HostPersistenceRejected("stored clocks hash does not bind the stored bytes")
        correlation = _parse_correlation(
            json.loads(_b64decode(payload.get("correlation_base64")).decode("utf-8"))
        )
        stored_correlation_sha256 = _digest(
            payload.get("correlation_sha256"), path="payload.correlation_sha256"
        )
        if correlation_sha256(correlation) != stored_correlation_sha256:
            raise HostPersistenceRejected("stored correlation hash does not bind its payload")
        if correlation.open_permit_id != permit.permit_id:
            raise HostPersistenceRejected("stored correlation does not bind the stored permit")
        decision_episode_id = payload.get("decision_episode_id")
        return RehydratedActiveBundle(
            session_id=_text(entry.get("session_id"), path="entry.session_id"),
            lifecycle_id=_text(entry.get("lifecycle_id"), path="entry.lifecycle_id"),
            permit=permit,
            permit_sha256=permit_sha256,
            clocks=clocks,
            clocks_sha256=clocks_sha256,
            correlation=correlation,
            correlation_sha256=stored_correlation_sha256,
            open_order_id=_text(payload.get("open_order_id"), path="payload.open_order_id"),
            account_id=_text(payload.get("account_id"), path="payload.account_id"),
            application_identity_sha256=_digest(
                payload.get("application_identity_sha256"),
                path="payload.application_identity_sha256",
            ),
            opened_at=_parse_timestamp(payload.get("opened_at")),
            decision_episode_id=(None if decision_episode_id is None else str(decision_episode_id)),
        )


__all__ = [
    "HOST_PERSISTENCE_ACTIVE_KIND",
    "HOST_PERSISTENCE_CLAIMS",
    "HOST_PERSISTENCE_FILENAME",
    "HOST_PERSISTENCE_GENESIS_SHA256",
    "HOST_PERSISTENCE_SCHEMA",
    "HOST_PERSISTENCE_SCHEMA_VERSION",
    "HOST_PERSISTENCE_TERMINAL_KIND",
    "HostPersistenceRejected",
    "HostPersistenceSidecar",
    "RehydratedActiveBundle",
    "parse_debit_vertical_permit_bytes",
]
