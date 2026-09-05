"""One-shot scheduled-event runner over the sole approved PAPER pipeline."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from esscher.execution.host_mcp import HostMcpEnvironment, HostMcpError
from esscher.execution.mcp import (
    BrokerResponseError,
    PaperLifecycleManualRequired,
    PaperLifecycleNotFlat,
    PermitNotExecutable,
)
from esscher.execution.models import DataClass, RunMode
from esscher.execution.paper_demo import (
    PAPER_PNL_DECIMAL_TEXT_MAX_LENGTH,
    PAPER_PNL_UNAVAILABLE_REASONS,
    FilePaperAttemptStore,
    PaperDemoApproval,
    PaperDemoNotApproved,
    PaperDemoPlan,
    run_paper_demo,
)

_EXPECTED_CLAIMS = ("PAPER_OPERATIONAL_RESULT", "INDICATIVE_DATA")
_RESULT_CLAIMS = (*_EXPECTED_CLAIMS, "NOT_ALPHA_EVIDENCE")
_TERMINAL_LIFECYCLES = frozenset({"CANCELED_FLAT", "CLOSED_FLAT"})
_STATE_LIFECYCLES = _TERMINAL_LIFECYCLES | {"RECONCILING", "MANUAL_RECONCILIATION"}
_MANUAL_FAILURE_CODES = frozenset(
    {
        "AMBIGUOUS_OR_PARTIAL_BROKER_STATE",
        "DURABLE_STATE_INVALID",
        "DUE_WINDOW_EXPIRED_DURING_RECONCILIATION",
        "RESTART_PLAN_INVALID_OR_EXPIRED",
        "TERMINAL_RECEIPT_INVALID",
    }
)


class ScheduledManifestRejected(RuntimeError):
    """Raised before broker mutation when a scheduled manifest is invalid."""


class ScheduledManualReconciliationRequired(RuntimeError):
    """Raised after sanitized state records a broker-authority stop."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "AMBIGUOUS_OR_PARTIAL_BROKER_STATE",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


class ScheduledEventOverlap(RuntimeError):
    """Raised when another non-terminal event owns the P0 runtime."""


class _PlanFactory(Protocol):
    def __call__(self) -> object: ...


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ScheduledManifestRejected(f"scheduled manifest {field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ScheduledManifestRejected(
            f"scheduled manifest {field} must be a valid ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScheduledManifestRejected(f"scheduled manifest {field} must be timezone-aware")
    return parsed


def _required_text(payload: dict[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ScheduledManifestRejected(f"scheduled manifest {field} must be normalized text")
    return value


def _require_sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ScheduledManifestRejected(
            f"terminal receipt {field} must be a lowercase SHA-256 digest"
        )
    return value


def _canonical_decimal_text(
    value: object,
    field: str,
    *,
    optional: bool = False,
) -> Decimal | None:
    if value is None:
        if optional:
            return None
        raise ScheduledManifestRejected(f"terminal receipt {field} must be decimal text")
    if not isinstance(value, str) or not 1 <= len(value) <= PAPER_PNL_DECIMAL_TEXT_MAX_LENGTH:
        raise ScheduledManifestRejected(f"terminal receipt {field} must be bounded decimal text")
    unsigned = value[1:] if value.startswith("-") else value
    whole, separator, fraction = unsigned.partition(".")
    if (
        not whole
        or not whole.isascii()
        or not whole.isdecimal()
        or (len(whole) > 1 and whole.startswith("0"))
        or (separator and (not fraction or not fraction.isascii() or not fraction.isdecimal()))
        or "." in fraction
    ):
        raise ScheduledManifestRejected(f"terminal receipt {field} is not canonical decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ScheduledManifestRejected(
            f"terminal receipt {field} is not canonical decimal text"
        ) from error
    if not parsed.is_finite() or format(parsed.normalize(), "f") != value:
        raise ScheduledManifestRejected(f"terminal receipt {field} is not canonical decimal text")
    return parsed


def _canonical_receipt_datetime(
    value: object,
    field: str,
) -> datetime:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ScheduledManifestRejected(
            f"terminal receipt {field} must be a bounded ISO-8601 string"
        )
    parsed = _aware_datetime(value, f"receipt {field}")
    if parsed.isoformat() != value:
        raise ScheduledManifestRejected(f"terminal receipt {field} must be canonical ISO-8601")
    return parsed


def _validate_terminal_paper_pnl(
    pnl: object,
    *,
    lifecycle: object,
    final_flat_observed_at: datetime,
) -> None:
    required = {
        "classification",
        "gross_realized_pnl",
        "broker_fees",
        "net_realized_pnl",
        "open_filled_at",
        "close_filled_at",
        "unavailable_reason",
    }
    if not isinstance(pnl, dict) or set(pnl) != required:
        raise ScheduledManifestRejected("terminal receipt PAPER P&L fields are invalid")

    classification = pnl["classification"]
    if classification == "ZERO_NO_FILL":
        fees = _canonical_decimal_text(pnl["broker_fees"], "broker_fees", optional=True)
        if (
            lifecycle != "CANCELED_FLAT"
            or pnl["gross_realized_pnl"] != "0"
            or (fees is not None and fees < 0)
            or pnl["net_realized_pnl"] is not None
            or pnl["open_filled_at"] is not None
            or pnl["close_filled_at"] is not None
            or pnl["unavailable_reason"] is not None
        ):
            raise ScheduledManifestRejected("terminal ZERO_NO_FILL PAPER P&L is invalid")
        return

    if classification == "PAPER_REALIZED_PNL":
        if lifecycle != "CLOSED_FLAT" or pnl["unavailable_reason"] is not None:
            raise ScheduledManifestRejected("terminal realized PAPER P&L lifecycle is invalid")
        gross = _canonical_decimal_text(pnl["gross_realized_pnl"], "gross_realized_pnl")
        fees = _canonical_decimal_text(pnl["broker_fees"], "broker_fees", optional=True)
        net = _canonical_decimal_text(pnl["net_realized_pnl"], "net_realized_pnl", optional=True)
        if fees is not None and fees < 0:
            raise ScheduledManifestRejected("terminal receipt broker_fees cannot be negative")
        if (fees is None) != (net is None) or (fees is not None and net != gross - fees):
            raise ScheduledManifestRejected("terminal receipt net PAPER P&L is inconsistent")
        opened_at = _canonical_receipt_datetime(pnl["open_filled_at"], "open_filled_at")
        closed_at = _canonical_receipt_datetime(pnl["close_filled_at"], "close_filled_at")
        if closed_at < opened_at or final_flat_observed_at < closed_at:
            raise ScheduledManifestRejected(
                "terminal receipt PAPER fill timestamps are inconsistent"
            )
        return

    if classification == "PAPER_PNL_UNAVAILABLE":
        if any(
            pnl[field] is not None
            for field in (
                "gross_realized_pnl",
                "broker_fees",
                "net_realized_pnl",
                "open_filled_at",
                "close_filled_at",
            )
        ):
            raise ScheduledManifestRejected("unavailable PAPER P&L cannot contain guessed values")
        reason = pnl["unavailable_reason"]
        if not isinstance(reason, str) or reason not in PAPER_PNL_UNAVAILABLE_REASONS:
            raise ScheduledManifestRejected("terminal receipt unavailable_reason is not allowed")
        return

    raise ScheduledManifestRejected("terminal receipt PAPER P&L classification is invalid")


def _verified_terminal_receipt(
    receipt: object,
    *,
    state: dict[str, object],
) -> dict[str, object]:
    required = {
        "schema",
        "schema_version",
        "run_mode",
        "data_class",
        "claims",
        "event_run_id",
        "open_permit_id",
        "close_permit_id",
        "capability_sha256",
        "open_request_sha256",
        "close_request_sha256",
        "open_order_sha256",
        "close_order_sha256",
        "lifecycle_outcome",
        "final_flat_observed_at",
        "paper_pnl",
        "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ScheduledManifestRejected("terminal receipt fields do not match schema v1")
    if (
        receipt["schema"] != "ringdown.paper_receipt_bundle"
        or type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 1
        or receipt["run_mode"] != "PAPER"
        or receipt["data_class"] != "INDICATIVE_DATA"
        or receipt["claims"] != ["PAPER_OPERATIONAL_OBSERVATION", "NOT_ALPHA_EVIDENCE"]
    ):
        raise ScheduledManifestRejected("terminal receipt boundary is invalid")
    _require_sha256(receipt["capability_sha256"], "capability_sha256")
    _require_sha256(receipt["open_request_sha256"], "open_request_sha256")
    _require_sha256(receipt["open_order_sha256"], "open_order_sha256")
    lifecycle = receipt["lifecycle_outcome"]
    if lifecycle == "CANCELED_FLAT":
        if receipt["close_request_sha256"] is not None or receipt["close_order_sha256"] is not None:
            raise ScheduledManifestRejected(
                "canceled-flat terminal receipt cannot contain closing digests"
            )
    elif lifecycle == "CLOSED_FLAT":
        _require_sha256(receipt["close_request_sha256"], "close_request_sha256")
        _require_sha256(receipt["close_order_sha256"], "close_order_sha256")
    else:
        raise ScheduledManifestRejected("terminal receipt lifecycle is invalid")
    _require_sha256(receipt["receipt_sha256"], "receipt_sha256")
    unsigned = dict(receipt)
    receipt_sha256 = unsigned.pop("receipt_sha256")
    if receipt_sha256 != hashlib.sha256(_canonical_json(unsigned)).hexdigest():
        raise ScheduledManifestRejected("terminal receipt integrity check failed")
    if (
        receipt["event_run_id"] != state["event_run_id"]
        or receipt["open_permit_id"] != state["open_permit_id"]
        or receipt["close_permit_id"] != state["close_permit_id"]
        or receipt["capability_sha256"] != state["capability_sha256"]
        or receipt["lifecycle_outcome"] != state["lifecycle"]
    ):
        raise ScheduledManifestRejected("terminal receipt identity does not match event state")
    final_flat_observed_at = _canonical_receipt_datetime(
        receipt["final_flat_observed_at"],
        "final_flat_observed_at",
    )
    _validate_terminal_paper_pnl(
        receipt["paper_pnl"],
        lifecycle=lifecycle,
        final_flat_observed_at=final_flat_observed_at,
    )
    return receipt


def _verified_state_payload(payload: object) -> dict[str, object]:
    required = {
        "schema",
        "schema_version",
        "run_mode",
        "data_class",
        "claims",
        "event_run_id",
        "manifest_sha256",
        "open_permit_id",
        "close_permit_id",
        "capability_sha256",
        "lifecycle",
        "updated_at",
        "failure_code",
        "receipt",
        "state_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ScheduledManifestRejected("scheduled event state fields do not match schema v1")
    if (
        payload["schema"] != "ringdown.scheduled_event_state"
        or payload["schema_version"] != 1
        or payload["run_mode"] != "PAPER"
        or payload["data_class"] != "INDICATIVE_DATA"
        or payload["claims"] != list(_RESULT_CLAIMS)
    ):
        raise ScheduledManifestRejected("scheduled event state boundary is invalid")
    lifecycle = payload["lifecycle"]
    if not isinstance(lifecycle, str) or lifecycle not in _STATE_LIFECYCLES:
        raise ScheduledManifestRejected("scheduled event state lifecycle is invalid")
    state_sha256 = payload["state_sha256"]
    unsigned = dict(payload)
    del unsigned["state_sha256"]
    expected_sha256 = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    if state_sha256 != expected_sha256:
        raise ScheduledManifestRejected("scheduled event state integrity check failed")
    _aware_datetime(payload["updated_at"], "state updated_at")
    if lifecycle in _TERMINAL_LIFECYCLES:
        _verified_terminal_receipt(payload["receipt"], state=payload)
    if lifecycle not in _TERMINAL_LIFECYCLES and payload["receipt"] is not None:
        raise ScheduledManifestRejected("non-terminal scheduled state cannot contain a receipt")
    if lifecycle == "MANUAL_RECONCILIATION":
        if payload["failure_code"] not in _MANUAL_FAILURE_CODES:
            raise ScheduledManifestRejected("manual scheduled state failure code is invalid")
    elif payload["failure_code"] is not None:
        raise ScheduledManifestRejected("scheduled event state has an unexpected failure code")
    return payload


@dataclass(frozen=True, slots=True)
class ScheduledEventManifest:
    """Strict operator-approved identity and due window for one frozen event."""

    event_run_id: str
    open_permit_id: str
    close_permit_id: str
    capability_sha256: str
    approved_at: datetime
    not_before: datetime
    expires_at: datetime
    manifest_sha256: str

    @classmethod
    def from_json_bytes(cls, value: bytes) -> ScheduledEventManifest:
        try:
            payload = json.loads(
                value,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise ScheduledManifestRejected("scheduled manifest is not strict JSON") from error
        required = {
            "schema",
            "schema_version",
            "event_run_id",
            "open_permit_id",
            "close_permit_id",
            "capability_sha256",
            "run_mode",
            "data_class",
            "approved_at",
            "not_before",
            "expires_at",
            "claims",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ScheduledManifestRejected("scheduled manifest fields do not match schema v1")
        if (
            payload["schema"] != "ringdown.scheduled_event_manifest"
            or payload["schema_version"] != 1
        ):
            raise ScheduledManifestRejected("scheduled manifest schema is unsupported")
        if payload["run_mode"] != "PAPER":
            raise ScheduledManifestRejected("scheduled manifest run_mode must be PAPER")
        if payload["data_class"] != "INDICATIVE_DATA":
            raise ScheduledManifestRejected("scheduled manifest data_class must be INDICATIVE_DATA")
        if payload["claims"] != list(_EXPECTED_CLAIMS):
            raise ScheduledManifestRejected(
                "scheduled manifest claims do not match the PAPER boundary"
            )

        capability_sha256 = _required_text(payload, "capability_sha256")
        if len(capability_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in capability_sha256
        ):
            raise ScheduledManifestRejected(
                "scheduled manifest capability_sha256 must be a lowercase SHA-256 digest"
            )
        approved_at = _aware_datetime(payload["approved_at"], "approved_at")
        not_before = _aware_datetime(payload["not_before"], "not_before")
        expires_at = _aware_datetime(payload["expires_at"], "expires_at")
        if approved_at > not_before or not_before >= expires_at:
            raise ScheduledManifestRejected("scheduled manifest due window is invalid")

        return cls(
            event_run_id=_required_text(payload, "event_run_id"),
            open_permit_id=_required_text(payload, "open_permit_id"),
            close_permit_id=_required_text(payload, "close_permit_id"),
            capability_sha256=capability_sha256,
            approved_at=approved_at,
            not_before=not_before,
            expires_at=expires_at,
            manifest_sha256=hashlib.sha256(value).hexdigest(),
        )

    def require_due(self, observed_at: datetime) -> None:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ScheduledManifestRejected("runner clock must be timezone-aware")
        if observed_at < self.not_before or observed_at >= self.expires_at:
            raise ScheduledManifestRejected("scheduled manifest is outside its due window")


class FileScheduledEventStore:
    """One atomic sanitized state record per event_run_id."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _event_key(event_run_id: str) -> str:
        return hashlib.sha256(event_run_id.encode("utf-8")).hexdigest()

    def state_path(self, event_run_id: str) -> Path:
        return self.root / f"event-{self._event_key(event_run_id)}.json"

    def attempt_store(self, event_run_id: str) -> FilePaperAttemptStore:
        return FilePaperAttemptStore(self.root / ".attempts" / self._event_key(event_run_id))

    def _validate_existing_root(self) -> None:
        if self.root.is_symlink() or (self.root.exists() and not self.root.is_dir()):
            raise ScheduledManifestRejected("scheduled state store must be a real directory")

    @contextmanager
    def run_lock(self) -> Iterator[None]:
        """Hold the cross-process P0 one-shot lock; OS release survives process death."""

        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ScheduledManifestRejected("scheduled state store must be a real directory")
        handle = (self.root / ".one-shot.lock").open("a+b")
        if os.fstat(handle.fileno()).st_size == 0:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        acquired = False
        try:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                raise ScheduledEventOverlap(
                    "another one-shot scheduled invocation is already running"
                ) from None
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def read(self, event_run_id: str) -> dict[str, object] | None:
        self._validate_existing_root()
        path = self.state_path(event_run_id)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise ScheduledManifestRejected("scheduled event state must be a real file")
        try:
            payload = json.loads(
                path.read_bytes(),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise ScheduledManifestRejected("scheduled event state is not strict JSON") from error
        return _verified_state_payload(payload)

    def active_event_ids(self, *, excluding: str) -> tuple[str, ...]:
        self._validate_existing_root()
        if not self.root.exists():
            return ()
        active: list[str] = []
        for path in sorted(self.root.glob("event-*.json")):
            if path.is_symlink() or not path.is_file():
                raise ScheduledManifestRejected("scheduled event state must be a real file")
            try:
                payload = json.loads(
                    path.read_bytes(),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_json_constant,
                )
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
                raise ScheduledManifestRejected(
                    "scheduled event state is not strict JSON"
                ) from error
            payload = _verified_state_payload(payload)
            event_run_id = payload["event_run_id"]
            lifecycle = payload["lifecycle"]
            if not isinstance(event_run_id, str) or not isinstance(lifecycle, str):
                raise ScheduledManifestRejected("scheduled event state identity is invalid")
            if path != self.state_path(event_run_id):
                raise ScheduledManifestRejected("scheduled event state path identity is invalid")
            if event_run_id != excluding and lifecycle not in _TERMINAL_LIFECYCLES:
                active.append(event_run_id)
        return tuple(active)

    def write(
        self,
        *,
        manifest: ScheduledEventManifest,
        lifecycle: str,
        updated_at: datetime,
        receipt: dict[str, object] | None,
        failure_code: str | None = None,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ScheduledManifestRejected("scheduled state store must be a real directory")
        payload = {
            "schema": "ringdown.scheduled_event_state",
            "schema_version": 1,
            "run_mode": "PAPER",
            "data_class": "INDICATIVE_DATA",
            "claims": list(_RESULT_CLAIMS),
            "event_run_id": manifest.event_run_id,
            "manifest_sha256": manifest.manifest_sha256,
            "open_permit_id": manifest.open_permit_id,
            "close_permit_id": manifest.close_permit_id,
            "capability_sha256": manifest.capability_sha256,
            "lifecycle": lifecycle,
            "updated_at": updated_at.isoformat(),
            "failure_code": failure_code,
            "receipt": receipt,
        }
        payload["state_sha256"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
        _verified_state_payload(payload)
        target = self.state_path(manifest.event_run_id)
        temporary = self.root / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(_canonical_json(payload) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ScheduledRunError:
    """Deterministic sanitized CLI error; never includes broker or exception details."""

    event_run_id: str | None
    manifest_sha256: str
    disposition: str
    lifecycle: str
    error_code: str
    broker_mutation: str
    observed_at: datetime

    def to_json_bytes(self) -> bytes:
        return _canonical_json(
            {
                "schema": "ringdown.scheduled_run_error",
                "schema_version": 1,
                "run_mode": "PAPER",
                "data_class": "INDICATIVE_DATA",
                "claims": list(_RESULT_CLAIMS),
                "event_run_id": self.event_run_id,
                "manifest_sha256": self.manifest_sha256,
                "disposition": self.disposition,
                "lifecycle": self.lifecycle,
                "error_code": self.error_code,
                "broker_mutation": self.broker_mutation,
                "observed_at": self.observed_at.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class ScheduledRunResult:
    """Sanitized one-shot result suitable for terminal output and static fixtures."""

    event_run_id: str
    manifest_sha256: str
    disposition: str
    lifecycle: str
    broker_mutation: str
    observed_at: datetime
    receipt: dict[str, object] | None = None

    def to_json_bytes(self) -> bytes:
        return _canonical_json(
            {
                "schema": "ringdown.scheduled_run_result",
                "schema_version": 1,
                "run_mode": "PAPER",
                "data_class": "INDICATIVE_DATA",
                "claims": list(_RESULT_CLAIMS),
                "event_run_id": self.event_run_id,
                "manifest_sha256": self.manifest_sha256,
                "disposition": self.disposition,
                "lifecycle": self.lifecycle,
                "broker_mutation": self.broker_mutation,
                "observed_at": self.observed_at.isoformat(),
                "receipt": self.receipt,
            }
        )


def _existing_state_result(
    manifest: ScheduledEventManifest,
    store: FileScheduledEventStore,
    *,
    observed_at: datetime,
    persist_invalid_state: bool = False,
) -> ScheduledRunResult | None:
    try:
        state = store.read(manifest.event_run_id)
    except ScheduledManifestRejected:
        if persist_invalid_state:
            store.write(
                manifest=manifest,
                lifecycle="MANUAL_RECONCILIATION",
                updated_at=observed_at,
                receipt=None,
                failure_code="DURABLE_STATE_INVALID",
            )
        raise ScheduledManualReconciliationRequired(
            "scheduled event state integrity is invalid; manual reconciliation required",
            error_code="DURABLE_STATE_INVALID",
        ) from None
    if state is not None and (
        state["event_run_id"] != manifest.event_run_id
        or state["manifest_sha256"] != manifest.manifest_sha256
        or state["open_permit_id"] != manifest.open_permit_id
        or state["close_permit_id"] != manifest.close_permit_id
        or state["capability_sha256"] != manifest.capability_sha256
    ):
        raise ScheduledManualReconciliationRequired(
            "scheduled event state identity does not match the manifest; "
            "manual reconciliation required",
            error_code="DURABLE_STATE_IDENTITY_MISMATCH",
        )
    if state is not None and state["lifecycle"] == "MANUAL_RECONCILIATION":
        raise ScheduledManualReconciliationRequired(
            "scheduled event remains stopped for manual reconciliation",
            error_code=str(state["failure_code"]),
        )
    if state is not None and state["lifecycle"] in _TERMINAL_LIFECYCLES:
        receipt = state["receipt"]
        if not isinstance(receipt, dict):
            raise ScheduledManifestRejected("terminal scheduled state omitted its receipt")
        return ScheduledRunResult(
            event_run_id=manifest.event_run_id,
            manifest_sha256=manifest.manifest_sha256,
            disposition="TERMINAL_NOOP",
            lifecycle=str(state["lifecycle"]),
            broker_mutation="NOT_ATTEMPTED",
            observed_at=observed_at,
            receipt=receipt,
        )
    try:
        active_event_ids = store.active_event_ids(excluding=manifest.event_run_id)
    except ScheduledManifestRejected:
        if persist_invalid_state:
            store.write(
                manifest=manifest,
                lifecycle="MANUAL_RECONCILIATION",
                updated_at=observed_at,
                receipt=None,
                failure_code="DURABLE_STATE_INVALID",
            )
        raise ScheduledManualReconciliationRequired(
            "scheduled event state integrity is invalid; manual reconciliation required",
            error_code="DURABLE_STATE_INVALID",
        ) from None
    if active_event_ids:
        raise ScheduledEventOverlap(
            "overlapping active event rejected; finish manual or terminal reconciliation first"
        )
    return None


async def _build_plan(plan_factory: _PlanFactory) -> PaperDemoPlan:
    value = plan_factory()
    plan = await value if inspect.isawaitable(value) else value
    if not isinstance(plan, PaperDemoPlan):
        raise ScheduledManifestRejected("scheduled host plan factory must return PaperDemoPlan")
    return plan


def _validate_plan(
    manifest: ScheduledEventManifest,
    plan: PaperDemoPlan,
    *,
    observed_at: datetime,
) -> None:
    if (
        plan.open_permit.event_run_id != manifest.event_run_id
        or plan.close_permit.event_run_id != manifest.event_run_id
    ):
        raise ScheduledManifestRejected("scheduled manifest event_run_id does not match the plan")
    if plan.open_permit.permit_id != manifest.open_permit_id:
        raise ScheduledManifestRejected("scheduled manifest does not bind the opening permit")
    if plan.close_permit.permit_id != manifest.close_permit_id:
        raise ScheduledManifestRejected("scheduled manifest does not bind the closing permit")
    if plan.prepared.observation.capability_sha256 != manifest.capability_sha256:
        raise ScheduledManifestRejected("scheduled manifest does not bind the capability proof")
    if (
        plan.prepared.observation.environment is not HostMcpEnvironment.PAPER
        or plan.open_permit.run_mode is not RunMode.PAPER
        or plan.close_permit.run_mode is not RunMode.PAPER
    ):
        raise ScheduledManifestRejected("scheduled plan must remain PAPER")
    if (
        plan.open_permit.data_class is not DataClass.INDICATIVE_DATA
        or plan.close_permit.data_class is not DataClass.INDICATIVE_DATA
    ):
        raise ScheduledManifestRejected("scheduled plan must remain INDICATIVE_DATA")
    for label, permit in (
        ("opening", plan.open_permit),
        ("closing", plan.close_permit),
    ):
        if observed_at < permit.issued_at:
            raise ScheduledManifestRejected(f"scheduled {label} permit is not active yet")
        if observed_at >= permit.expires_at:
            raise ScheduledManifestRejected(f"scheduled {label} permit expired")
    plan.approval_template_json_bytes(observed_at=observed_at)


async def _run_armed(
    *,
    manifest: ScheduledEventManifest,
    store: FileScheduledEventStore,
    plan_factory: _PlanFactory,
    clock: Callable[[], datetime],
    observed_at: datetime,
) -> ScheduledRunResult:
    with store.run_lock():
        existing = _existing_state_result(
            manifest,
            store,
            observed_at=observed_at,
            persist_invalid_state=True,
        )
        if existing is not None:
            return existing
        manifest.require_due(observed_at)
        restart_state = store.read(manifest.event_run_id)
        is_restart = restart_state is not None and restart_state["lifecycle"] == "RECONCILING"
        try:
            plan = await _build_plan(plan_factory)
            _validate_plan(manifest, plan, observed_at=observed_at)
        except ScheduledManifestRejected:
            if not is_restart:
                raise
            store.write(
                manifest=manifest,
                lifecycle="MANUAL_RECONCILIATION",
                updated_at=observed_at,
                receipt=None,
                failure_code="RESTART_PLAN_INVALID_OR_EXPIRED",
            )
            raise ScheduledManualReconciliationRequired(
                "scheduled restart plan is invalid or expired; manual reconciliation required",
                error_code="RESTART_PLAN_INVALID_OR_EXPIRED",
            ) from None
        store.write(
            manifest=manifest,
            lifecycle="RECONCILING",
            updated_at=observed_at,
            receipt=None,
        )
        try:
            bundle = await run_paper_demo(
                prepared=plan.prepared,
                open_permit=plan.open_permit,
                close_permit=plan.close_permit,
                approval=PaperDemoApproval(
                    permit_id=manifest.open_permit_id,
                    capability_sha256=manifest.capability_sha256,
                    environment=HostMcpEnvironment.PAPER,
                    approved_at=manifest.approved_at,
                    expires_at=manifest.expires_at,
                ),
                attempt_store=store.attempt_store(manifest.event_run_id),
                clock=clock,
            )
        except (
            BrokerResponseError,
            HostMcpError,
            PaperDemoNotApproved,
            PaperLifecycleManualRequired,
            PaperLifecycleNotFlat,
            PermitNotExecutable,
        ):
            store.write(
                manifest=manifest,
                lifecycle="MANUAL_RECONCILIATION",
                updated_at=clock(),
                receipt=None,
                failure_code="AMBIGUOUS_OR_PARTIAL_BROKER_STATE",
            )
            raise ScheduledManualReconciliationRequired(
                "scheduled event stopped: manual reconciliation required"
            ) from None
        try:
            receipt = json.loads(
                bundle.to_json_bytes(),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(receipt, dict):
                raise ScheduledManifestRejected("paper receipt serialization must be an object")
            result = ScheduledRunResult(
                event_run_id=manifest.event_run_id,
                manifest_sha256=manifest.manifest_sha256,
                disposition="EXECUTED_TO_TERMINAL",
                lifecycle=bundle.lifecycle_outcome,
                broker_mutation="BOUNDED_PAPER_PIPELINE",
                observed_at=bundle.final_flat_observed_at,
                receipt=receipt,
            )
            store.write(
                manifest=manifest,
                lifecycle=result.lifecycle,
                updated_at=result.observed_at,
                receipt=receipt,
            )
        except (
            AttributeError,
            ScheduledManifestRejected,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ):
            store.write(
                manifest=manifest,
                lifecycle="MANUAL_RECONCILIATION",
                updated_at=clock(),
                receipt=None,
                failure_code="TERMINAL_RECEIPT_INVALID",
            )
            raise ScheduledManualReconciliationRequired(
                "terminal PAPER receipt was invalid; manual reconciliation required",
                error_code="TERMINAL_RECEIPT_INVALID",
            ) from None
        return result


async def run_scheduled_event_command(
    *,
    manifest_bytes: bytes,
    state_dir: Path,
    plan_factory: _PlanFactory,
    dry_run: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ScheduledRunResult:
    """Validate and run at most one approved event through the bounded PAPER pipeline."""

    manifest = ScheduledEventManifest.from_json_bytes(manifest_bytes)
    observed_at = clock()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ScheduledManifestRejected("runner clock must be timezone-aware")
    store = FileScheduledEventStore(state_dir)
    try:
        existing = _existing_state_result(manifest, store, observed_at=observed_at)
    except ScheduledManualReconciliationRequired as error:
        if dry_run or error.error_code != "DURABLE_STATE_INVALID":
            raise
        with store.run_lock():
            existing = _existing_state_result(
                manifest,
                store,
                observed_at=observed_at,
                persist_invalid_state=True,
            )
    if existing is not None:
        return existing
    try:
        manifest.require_due(observed_at)
    except ScheduledManifestRejected:
        state = store.read(manifest.event_run_id)
        if state is None or state["lifecycle"] != "RECONCILING":
            raise
        stopped = ScheduledManualReconciliationRequired(
            "scheduled event due window expired during reconciliation; "
            "manual reconciliation required",
            error_code="DUE_WINDOW_EXPIRED_DURING_RECONCILIATION",
        )
        if dry_run:
            raise stopped from None
        with store.run_lock():
            raced = _existing_state_result(
                manifest,
                store,
                observed_at=observed_at,
                persist_invalid_state=True,
            )
            if raced is not None:
                return raced
            store.write(
                manifest=manifest,
                lifecycle="MANUAL_RECONCILIATION",
                updated_at=observed_at,
                receipt=None,
                failure_code="DUE_WINDOW_EXPIRED_DURING_RECONCILIATION",
            )
        raise stopped from None
    if dry_run:
        plan = await _build_plan(plan_factory)
        _validate_plan(manifest, plan, observed_at=observed_at)
        return ScheduledRunResult(
            event_run_id=manifest.event_run_id,
            manifest_sha256=manifest.manifest_sha256,
            disposition="DRY_RUN_VALIDATED",
            lifecycle="VALIDATED",
            broker_mutation="NOT_ATTEMPTED",
            observed_at=observed_at,
        )
    return await _run_armed(
        manifest=manifest,
        store=store,
        plan_factory=plan_factory,
        clock=clock,
        observed_at=observed_at,
    )
