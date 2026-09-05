"""Read-only Alpaca account-activity acquisition and typed option-event mapping.

Issue #90 / PRD PR-2 acquisition seam.  This module is the *host adapter* the
option-event reconciliation contract (`runtime/option_events.py`) explicitly
reserved: it acquires paginated raw account-activity pages through the guarded
read-only MCP door, persists an append-only acquisition cursor, and maps raw
provider activity codes to semantic `NormalizedOptionEvent` values through one
exact, source-versioned, content-addressed mapping contract.

Boundaries:

- read-only by construction: only the read-only door of a factory-prepared
  PAPER MCP session is consumed, so no mutation tool can ever be reached;
- credentials never enter this module: the host owns the MCP process and its
  secrets, and raw payloads are consumed as canonical bytes;
- no guessing: unknown activity types, unknown fields, non-OCC symbols,
  contradictory duplicates, and unmappable corporate actions are routed to
  explicit manual reconciliation records, never silently dropped or inferred;
- deterministic: identical raw pages normalize to byte-identical events, and
  the acquisition digest binds every page hash, so replays are idempotent.

This ingestion path produces `HOST_NORMALIZED_BROKER_INPUT`-class observations
only when the composition attests them; repository validation proves structure
and correlation, never that Alpaca supplied the bytes, and nothing here is
broker-connectivity, fill, flatness, or alpha evidence.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import NoReturn, Protocol

from esscher.contracts.execution_policy import ACTIVITIES_TOOL
from esscher.execution.host_mcp import (
    HostMcpError,
    PreparedHostMcpSession,
)
from esscher.lifecycle.broker import OPEN_WORKING_STATES, BrokerOrderState
from esscher.runtime.option_events import (
    AssetClass,
    EvidenceClass,
    NormalizedOptionEvent,
    OptionActivityCoverage,
    OptionEventKind,
    OptionEventStatus,
    OptionPortfolioObservation,
    PortfolioPosition,
)
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes

ACTIVITY_SOURCE_ID = "alpaca.account_activities.v2"
ACTIVITY_PAGE_MAX_SIZE = 100
ACTIVITY_CURSOR_FILENAME = "activity_cursor.jsonl"
ACTIVITY_CURSOR_SCHEMA = "esscher.activity_cursor_entry"
ACTIVITY_CURSOR_SCHEMA_VERSION = 1
ACQUISITION_DIGEST_SCHEMA = "esscher.activity_acquisition_digest"
ACQUISITION_DIGEST_SCHEMA_VERSION = 1

_OCC_SYMBOL = re.compile(
    r"^(?P<root>[A-Z]{1,6})(?P<year>\d{2})(?P<month>\d{2})(?P<day>\d{2})"
    r"(?P<option_type>[CP])(?P<strike>\d{8})$"
)
_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The complete activity-type vocabulary published by the pinned 2.3.1
# `get_account_activities` input schema.  The mapping contract is closed over
# exactly this set; any other code is unknown and routes to manual.
KNOWN_ACTIVITY_TYPES = frozenset(
    {
        "ACATC",
        "ACATS",
        "CFEE",
        "CGD",
        "CSD",
        "CSW",
        "DIV",
        "DIVCGL",
        "DIVCGS",
        "DIVFEE",
        "DIVFT",
        "DIVNRA",
        "DIVROC",
        "DIVTW",
        "DIVTXEX",
        "FEE",
        "FILL",
        "FOPT",
        "INT",
        "INTNRA",
        "INTTW",
        "JNL",
        "JNLC",
        "JNLS",
        "MA",
        "MISC",
        "NC",
        "OCT",
        "OPASN",
        "OPCA",
        "OPCSH",
        "OPEXC",
        "OPEXP",
        "OPTRD",
        "PTC",
        "PTR",
        "REO",
        "REORG",
        "SPIN",
        "SPLIT",
        "TRANS",
    }
)

# Exact semantic mapping: assignment happens to short writers, exercise to long
# holders, and expiration terminates a position without cash or share flow.
MAPPED_ACTIVITY_TYPES: Mapping[str, OptionEventKind] = {
    "OPASN": OptionEventKind.ASSIGNMENT,
    "OPEXC": OptionEventKind.EXERCISE,
    "OPEXP": OptionEventKind.EXPIRY,
}

# Known option corporate actions change contract identity.  Their replacement
# provenance cannot be reconstructed from the activity record alone, so they
# always route to manual reconciliation instead of being guessed.
MANUAL_ACTIVITY_TYPES = frozenset({"OPCA", "OPCSH"})

SKIPPED_ACTIVITY_TYPES = frozenset(
    KNOWN_ACTIVITY_TYPES - set(MAPPED_ACTIVITY_TYPES) - MANUAL_ACTIVITY_TYPES
)

# Closed raw-field contract per activity record (source-versioned).
REQUIRED_ACTIVITY_FIELDS = frozenset({"activity_type", "id", "transaction_time"})
MAPPED_ACTIVITY_EXTRA_FIELDS = frozenset({"date", "qty", "symbol"})
ALLOWED_ACTIVITY_FIELDS = frozenset(
    REQUIRED_ACTIVITY_FIELDS
    | MAPPED_ACTIVITY_EXTRA_FIELDS
    | {
        "cash",
        "description",
        "net_amount",
        "order_id",
        "per_share",
        "price",
        "shares",
        "side",
        "type",
    }
)

ACTIVITY_MAPPING_V1: Mapping[str, object] = {
    "schema": "esscher.alpaca_activity_mapping",
    "schema_version": 1,
    "source_id": ACTIVITY_SOURCE_ID,
    "adapter_version": "2.3.1",
    "activity_tool": ACTIVITIES_TOOL,
    "mapped_activity_types": {
        key: value.value for key, value in sorted(MAPPED_ACTIVITY_TYPES.items())
    },
    "manual_activity_types": sorted(MANUAL_ACTIVITY_TYPES),
    "skipped_activity_types": sorted(SKIPPED_ACTIVITY_TYPES),
    "required_activity_fields": sorted(REQUIRED_ACTIVITY_FIELDS),
    "mapped_activity_extra_fields": sorted(MAPPED_ACTIVITY_EXTRA_FIELDS),
    "allowed_activity_fields": sorted(ALLOWED_ACTIVITY_FIELDS),
    "economics": {
        "multiplier": 100,
        "exercise_side": "LONG_HOLDER",
        "assignment_side": "SHORT_WRITER",
        "call_share_delta_sign_by_kind": {"ASSIGNMENT": -1, "EXERCISE": 1, "EXPIRY": 0},
        "put_share_delta_flips_sign": True,
        "cash_delta_rule": "CASH_DELTA_EQUALS_NEGATIVE_SHARE_DELTA_TIMES_STRIKE",
    },
    "claim": "HOST_NORMALIZATION_CONTRACT_NOT_BROKER_EVIDENCE",
}
ACTIVITY_MAPPING_V1_SHA256 = sha256_bytes(canonical_json_bytes(dict(ACTIVITY_MAPPING_V1)))


class ActivityAcquisitionReason(StrEnum):
    """Stable reasons an acquisition or cursor operation fails closed."""

    INVALID_PAGE = "INVALID_PAGE"
    WINDOW_INCONSISTENT = "WINDOW_INCONSISTENT"
    PAGE_SIZE_OUT_OF_RANGE = "PAGE_SIZE_OUT_OF_RANGE"
    PAGINATION_CYCLE = "PAGINATION_CYCLE"
    PAGINATION_BUDGET_EXHAUSTED = "PAGINATION_BUDGET_EXHAUSTED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    CURSOR_CONFLICT = "CURSOR_CONFLICT"
    CURSOR_CHAIN_BROKEN = "CURSOR_CHAIN_BROKEN"


class ActivityNormalizationReason(StrEnum):
    """Stable reasons one raw activity routes to manual reconciliation."""

    UNKNOWN_ACTIVITY_TYPE = "UNKNOWN_ACTIVITY_TYPE"
    UNMAPPABLE_ACTIVITY_TYPE = "UNMAPPABLE_ACTIVITY_TYPE"
    UNKNOWN_ACTIVITY_FIELD = "UNKNOWN_ACTIVITY_FIELD"
    MISSING_ACTIVITY_FIELD = "MISSING_ACTIVITY_FIELD"
    MALFORMED_ACTIVITY_ID = "MALFORMED_ACTIVITY_ID"
    MALFORMED_QTY = "MALFORMED_QTY"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    MALFORMED_EFFECTIVE_DATE = "MALFORMED_EFFECTIVE_DATE"
    SYMBOL_NOT_OCC = "SYMBOL_NOT_OCC"
    OUT_OF_WINDOW = "OUT_OF_WINDOW"
    DUPLICATE_ACTIVITY_CONFLICT = "DUPLICATE_ACTIVITY_CONFLICT"


class ActivityAcquisitionRejected(ValueError):
    """A deterministic acquisition or cursor failure."""

    def __init__(self, reason: ActivityAcquisitionReason, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}")


def _reject(reason: ActivityAcquisitionReason, detail: str) -> NoReturn:
    raise ActivityAcquisitionRejected(reason, detail)


@dataclass(frozen=True, slots=True)
class ActivityPageRequest:
    """One read-only activities page request for a declared UTC window."""

    window_start: datetime
    window_end: datetime
    page_size: int
    page_token: str | None


@dataclass(frozen=True, slots=True)
class ActivityManualRoute:
    """One raw activity that must reach manual reconciliation, never a guess."""

    reason: ActivityNormalizationReason
    source_payload_sha256: str
    activity_id: str | None


class AccountActivitySource(Protocol):
    """Host-owned read-only activities door returning canonical page bytes."""

    async def fetch_activity_page(self, request: ActivityPageRequest) -> bytes: ...


def _validate_page_items(raw: object) -> list[Mapping[str, object]]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        _reject(ActivityAcquisitionReason.INVALID_PAGE, "activities page must be a JSON array")
    items: list[Mapping[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            _reject(ActivityAcquisitionReason.INVALID_PAGE, "activity record must be an object")
        if not all(isinstance(key, str) for key in item):
            _reject(ActivityAcquisitionReason.INVALID_PAGE, "activity field names must be text")
        items.append(item)
    return items


class McpAccountActivitySource:
    """Activities source over the guarded read-only door of a prepared session.

    The MCP tool result is normalized to canonical JSON array bytes so page
    digests are deterministic regardless of transport representation.
    """

    def __init__(self, prepared: PreparedHostMcpSession) -> None:
        if type(prepared) is not PreparedHostMcpSession:
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.SOURCE_UNAVAILABLE,
                "activities source requires a factory-prepared host MCP session",
            )
        self._prepared = prepared

    async def fetch_activity_page(self, request: ActivityPageRequest) -> bytes:
        arguments: dict[str, object] = {
            "after": _timestamp_text(request.window_start),
            "until": _timestamp_text(request.window_end),
            "direction": "asc",
            "page_size": request.page_size,
        }
        if request.page_token is not None:
            arguments["page_token"] = request.page_token
        try:
            response = await self._prepared.readonly_call(ACTIVITIES_TOOL, arguments)
        except HostMcpError as error:
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.SOURCE_UNAVAILABLE,
                f"activities page is unavailable: {type(error).__name__}",
            ) from error
        _validate_page_items(response)
        return canonical_json_bytes(response)


@dataclass(frozen=True, slots=True)
class ActivityAcquisition:
    """One complete paginated acquisition over an exact UTC window."""

    window_start: datetime
    window_end: datetime
    pages: tuple[bytes, ...]
    page_sha256s: tuple[str, ...]
    activity_count: int
    complete: bool
    last_activity_id: str | None
    source_payload_sha256: str


def acquisition_source_payload_sha256(acquisition: ActivityAcquisition) -> str:
    """Bind the acquisition digest over every page hash and the exact window."""

    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema": ACQUISITION_DIGEST_SCHEMA,
                "schema_version": ACQUISITION_DIGEST_SCHEMA_VERSION,
                "window_start": _timestamp_text(acquisition.window_start),
                "window_end": _timestamp_text(acquisition.window_end),
                "page_sha256s": list(acquisition.page_sha256s),
                "activity_count": acquisition.activity_count,
                "complete": acquisition.complete,
            }
        )
    )


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _reject(ActivityAcquisitionReason.WINDOW_INCONSISTENT, "window bounds must be aware UTC")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _CursorResumeState:
    page_index: int
    page_token: str | None
    last_activity_id: str | None
    page_sha256s: tuple[str, ...]
    pages: tuple[bytes, ...]
    activity_count: int
    complete: bool


class ActivityCursorJournal:
    """Append-only, hash-chained acquisition cursor enabling replay-safe restart.

    One journal belongs to exactly one acquisition window.  Every page fetch
    appends one fsynced entry binding the page digest, running activity count,
    and continuation token.  A restarted acquisition resumes from the last
    committed page instead of re-trusting memory, and any broken chain or
    foreign window fails closed before a fetch.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def _entries(self) -> tuple[dict[str, object], ...]:
        if not self._path.exists():
            return ()
        entries: list[dict[str, object]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise ActivityAcquisitionRejected(
                    ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN, "cursor entry is not valid JSON"
                ) from error
            if not isinstance(entry, dict):
                raise ActivityAcquisitionRejected(
                    ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN, "cursor entry must be an object"
                )
            entries.append(entry)
        return tuple(entries)

    def chain_valid(self, window_start: datetime, window_end: datetime) -> bool:
        expected_prior = "0" * 64
        for index, entry in enumerate(self._entries()):
            if entry.get("schema") != ACTIVITY_CURSOR_SCHEMA:
                return False
            if entry.get("schema_version") != ACTIVITY_CURSOR_SCHEMA_VERSION:
                return False
            if entry.get("page_index") != index:
                return False
            if entry.get("window_start") != _timestamp_text(window_start):
                return False
            if entry.get("window_end") != _timestamp_text(window_end):
                return False
            if entry.get("prior_entry_sha256") != expected_prior:
                return False
            unsigned = {key: value for key, value in entry.items() if key != "entry_sha256"}
            digest = sha256_bytes(canonical_json_bytes(unsigned))
            if entry.get("entry_sha256") != digest:
                return False
            expected_prior = digest
        return True

    def resume_state(
        self, window_start: datetime, window_end: datetime
    ) -> _CursorResumeState | None:
        entries = self._entries()
        if not entries:
            return None
        if not self.chain_valid(window_start, window_end):
            _reject(
                ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN,
                "cursor journal chain is invalid for the requested window",
            )
        page_sha256s: list[str] = []
        pages: list[bytes] = []
        activity_count = 0
        last_id: str | None = None
        complete = False
        for entry in entries:
            page_sha = entry.get("page_sha256")
            page_b64 = entry.get("page_base64")
            if not isinstance(page_sha, str) or not isinstance(page_b64, str):
                _reject(
                    ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN,
                    "cursor entry is missing its page binding",
                )
            raw = base64.b64decode(page_b64)
            if sha256_bytes(raw) != page_sha:
                _reject(
                    ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN,
                    "cursor page bytes do not match the recorded digest",
                )
            page_sha256s.append(page_sha)
            pages.append(raw)
            count = entry.get("activities_in_page")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                _reject(
                    ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN,
                    "cursor entry has an invalid activity count",
                )
            activity_count += count
            entry_id = entry.get("last_activity_id")
            if entry_id is not None and not isinstance(entry_id, str):
                _reject(
                    ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN,
                    "cursor entry has an invalid continuation token",
                )
            if isinstance(entry_id, str):
                last_id = entry_id
            if entry.get("complete") is True:
                complete = True
        if complete:
            return _CursorResumeState(
                page_index=len(pages),
                page_token=None,
                last_activity_id=last_id,
                page_sha256s=tuple(page_sha256s),
                pages=tuple(pages),
                activity_count=activity_count,
                complete=True,
            )
        return _CursorResumeState(
            page_index=len(pages),
            page_token=last_id,
            last_activity_id=last_id,
            page_sha256s=tuple(page_sha256s),
            pages=tuple(pages),
            activity_count=activity_count,
            complete=False,
        )

    def append_page(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        page_index: int,
        page: bytes,
        activities_in_page: int,
        last_activity_id: str | None,
        complete: bool,
        fetched_at: datetime,
    ) -> str:
        entries = self._entries()
        if len(entries) != page_index:
            _reject(
                ActivityAcquisitionReason.CURSOR_CONFLICT,
                "cursor append index does not continue the journal",
            )
        prior = "0" * 64 if not entries else str(entries[-1].get("entry_sha256", ""))
        unsigned = {
            "schema": ACTIVITY_CURSOR_SCHEMA,
            "schema_version": ACTIVITY_CURSOR_SCHEMA_VERSION,
            "window_start": _timestamp_text(window_start),
            "window_end": _timestamp_text(window_end),
            "page_index": page_index,
            "page_sha256": sha256_bytes(page),
            "page_base64": base64.b64encode(page).decode("ascii"),
            "activities_in_page": activities_in_page,
            "last_activity_id": last_activity_id,
            "complete": complete,
            "fetched_at": _timestamp_text(fetched_at),
            "prior_entry_sha256": prior,
        }
        digest = sha256_bytes(canonical_json_bytes(unsigned))
        line = json.dumps(
            {**unsigned, "entry_sha256": digest}, sort_keys=True, separators=(",", ":")
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return digest


async def acquire_account_activities(
    source: AccountActivitySource,
    *,
    window_start: datetime,
    window_end: datetime,
    page_size: int = ACTIVITY_PAGE_MAX_SIZE,
    max_pages: int = 200,
    journal: ActivityCursorJournal | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ActivityAcquisition:
    """Acquire every activity page for one exact window, cursor-durably.

    Pagination follows the pinned tool contract: ascending order, ``after`` /
    ``until`` window bounds, ``page_token`` continuation by last activity id,
    and completion when a page returns fewer than ``page_size`` records.  A
    repeated token is a cycle and a budget overrun is exhaustion; both fail
    closed instead of guessing completeness.
    """

    if _window_invalid(window_start, window_end):
        _reject(
            ActivityAcquisitionReason.WINDOW_INCONSISTENT,
            "acquisition window must be ordered and timezone-aware",
        )
    if (
        not isinstance(page_size, int)
        or isinstance(page_size, bool)
        or not (1 <= page_size <= ACTIVITY_PAGE_MAX_SIZE)
    ):
        _reject(
            ActivityAcquisitionReason.PAGE_SIZE_OUT_OF_RANGE,
            f"page size must be within 1..{ACTIVITY_PAGE_MAX_SIZE}",
        )
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
        _reject(
            ActivityAcquisitionReason.PAGINATION_BUDGET_EXHAUSTED,
            "max_pages must be a positive budget",
        )

    pages: list[bytes] = []
    page_sha256s: list[str] = []
    activity_count = 0
    page_token: str | None = None
    page_index = 0
    complete = False
    last_activity_id: str | None = None
    if journal is not None:
        resume = journal.resume_state(window_start, window_end)
        if resume is not None:
            pages = list(resume.pages)
            page_sha256s = list(resume.page_sha256s)
            activity_count = resume.activity_count
            page_token = resume.page_token
            last_activity_id = resume.last_activity_id
            page_index = resume.page_index
            complete = resume.complete

    seen_tokens: set[str] = set()
    while not complete:
        if page_index >= max_pages:
            _reject(
                ActivityAcquisitionReason.PAGINATION_BUDGET_EXHAUSTED,
                f"acquisition exceeded the {max_pages}-page budget before completion",
            )
        if page_token is not None:
            if page_token in seen_tokens:
                _reject(
                    ActivityAcquisitionReason.PAGINATION_CYCLE,
                    "activities pagination repeated a page token",
                )
            seen_tokens.add(page_token)
        request = ActivityPageRequest(
            window_start=window_start,
            window_end=window_end,
            page_size=page_size,
            page_token=page_token,
        )
        raw = await source.fetch_activity_page(request)
        if type(raw) is not bytes:
            _reject(ActivityAcquisitionReason.INVALID_PAGE, "activities page must be bytes")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            _reject(ActivityAcquisitionReason.INVALID_PAGE, f"activities page is invalid: {error}")
        items = _validate_page_items(decoded)
        page_last_id: str | None = None
        for item in items:
            raw_id = item.get("id")
            if not isinstance(raw_id, str) or not raw_id:
                _reject(
                    ActivityAcquisitionReason.INVALID_PAGE,
                    "every activity record must carry a non-empty id",
                )
            page_last_id = raw_id
        page_complete = len(items) < page_size
        activity_count += len(items)
        pages.append(raw)
        page_sha256s.append(sha256_bytes(raw))
        if journal is not None:
            fetched_at = datetime.now(UTC) if clock is None else _clock_now(clock)
            journal.append_page(
                window_start=window_start,
                window_end=window_end,
                page_index=page_index,
                page=raw,
                activities_in_page=len(items),
                last_activity_id=page_last_id,
                complete=page_complete,
                fetched_at=fetched_at,
            )
        page_index += 1
        complete = page_complete
        if page_last_id is not None:
            last_activity_id = page_last_id
        if not complete:
            page_token = page_last_id

    acquisition = ActivityAcquisition(
        window_start=window_start,
        window_end=window_end,
        pages=tuple(pages),
        page_sha256s=tuple(page_sha256s),
        activity_count=activity_count,
        complete=complete,
        last_activity_id=last_activity_id,
        source_payload_sha256="",
    )
    return replace(
        acquisition,
        source_payload_sha256=acquisition_source_payload_sha256(acquisition),
    )


def _clock_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _reject(ActivityAcquisitionReason.WINDOW_INCONSISTENT, "clock must return aware UTC time")
    return value.astimezone(UTC)


def _window_invalid(window_start: datetime, window_end: datetime) -> bool:
    for bound in (window_start, window_end):
        if not isinstance(bound, datetime) or bound.tzinfo is None or bound.utcoffset() is None:
            return True
    return window_end < window_start


@dataclass(frozen=True, slots=True)
class ActivityNormalization:
    """The complete typed outcome of one acquisition's raw pages."""

    events: tuple[NormalizedOptionEvent, ...]
    skipped_activity_ids: tuple[str, ...]
    manual_routes: tuple[ActivityManualRoute, ...]
    duplicate_skip_count: int
    mapping_sha256: str


def _occ_terms(symbol: str) -> tuple[str, date, str, Decimal] | None:
    match = _OCC_SYMBOL.fullmatch(symbol)
    if match is None:
        return None
    try:
        expiry = date(
            2000 + int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None
    strike = Decimal(match.group("strike")) / Decimal(1000)
    if not strike.is_finite() or strike <= 0:
        return None
    return match.group("root"), expiry, match.group("option_type"), strike


def _positive_int_qty(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _manual(reason: ActivityNormalizationReason, raw: Mapping[str, object]) -> ActivityManualRoute:
    payload_sha = sha256_bytes(canonical_json_bytes(dict(raw)))
    raw_id = raw.get("id")
    activity_id = raw_id if isinstance(raw_id, str) and raw_id else None
    return ActivityManualRoute(
        reason=reason, source_payload_sha256=payload_sha, activity_id=activity_id
    )


def normalize_account_activities(
    acquisition: ActivityAcquisition,
    *,
    account_fingerprint_sha256: str,
    execution_protocol_sha256: str,
    evidence_class: EvidenceClass,
) -> ActivityNormalization:
    """Map every acquired raw activity through the hash-bound v1 contract.

    Known non-lifecycle codes are skipped deterministically; mapped codes become
    semantic events with OCC-derived economics matching the pure reducer's
    expectations; everything unknown, unmappable, malformed, contradictory, or
    out-of-window becomes an explicit manual route.  Nothing is ever dropped
    without a record, and identical pages always normalize identically.
    """

    if not isinstance(evidence_class, EvidenceClass):
        raise ActivityAcquisitionRejected(
            ActivityAcquisitionReason.INVALID_PAGE, "evidence_class must be an EvidenceClass"
        )
    events: list[NormalizedOptionEvent] = []
    skipped: list[str] = []
    manual: list[ActivityManualRoute] = []
    duplicates = 0
    seen_ids: dict[str, str] = {}
    for raw_page in acquisition.pages:
        decoded = json.loads(raw_page.decode("utf-8"))
        for raw in _validate_page_items(decoded):
            payload_sha = sha256_bytes(canonical_json_bytes(dict(raw)))
            raw_id = raw.get("id")
            if not isinstance(raw_id, str) or not raw_id:
                manual.append(
                    ActivityManualRoute(
                        reason=ActivityNormalizationReason.MALFORMED_ACTIVITY_ID,
                        source_payload_sha256=payload_sha,
                        activity_id=None,
                    )
                )
                continue
            activity_type = raw.get("activity_type")
            if not isinstance(activity_type, str):
                manual.append(_manual(ActivityNormalizationReason.UNKNOWN_ACTIVITY_TYPE, raw))
                continue
            unknown_fields = set(raw) - ALLOWED_ACTIVITY_FIELDS
            if unknown_fields:
                manual.append(_manual(ActivityNormalizationReason.UNKNOWN_ACTIVITY_FIELD, raw))
                continue
            missing = REQUIRED_ACTIVITY_FIELDS - set(raw)
            if missing:
                manual.append(_manual(ActivityNormalizationReason.MISSING_ACTIVITY_FIELD, raw))
                continue
            if activity_type not in KNOWN_ACTIVITY_TYPES:
                manual.append(_manual(ActivityNormalizationReason.UNKNOWN_ACTIVITY_TYPE, raw))
                continue
            if activity_type in MANUAL_ACTIVITY_TYPES:
                manual.append(_manual(ActivityNormalizationReason.UNMAPPABLE_ACTIVITY_TYPE, raw))
                continue
            if activity_type in SKIPPED_ACTIVITY_TYPES:
                if raw_id in seen_ids and seen_ids[raw_id] != payload_sha:
                    manual.append(
                        _manual(ActivityNormalizationReason.DUPLICATE_ACTIVITY_CONFLICT, raw)
                    )
                    continue
                seen_ids.setdefault(raw_id, payload_sha)
                if raw_id not in skipped:
                    skipped.append(raw_id)
                else:
                    duplicates += 1
                continue

            kind = MAPPED_ACTIVITY_TYPES[activity_type]
            missing_mapped = MAPPED_ACTIVITY_EXTRA_FIELDS - set(raw)
            if missing_mapped:
                manual.append(_manual(ActivityNormalizationReason.MISSING_ACTIVITY_FIELD, raw))
                continue
            observed_at = _parse_timestamp(raw.get("transaction_time"))
            if observed_at is None:
                manual.append(_manual(ActivityNormalizationReason.MALFORMED_TIMESTAMP, raw))
                continue
            if not (acquisition.window_start <= observed_at <= acquisition.window_end):
                manual.append(_manual(ActivityNormalizationReason.OUT_OF_WINDOW, raw))
                continue
            effective_text = raw.get("date")
            if not isinstance(effective_text, str) or not _DATE_TEXT.fullmatch(effective_text):
                manual.append(_manual(ActivityNormalizationReason.MALFORMED_EFFECTIVE_DATE, raw))
                continue
            try:
                effective_date = date.fromisoformat(effective_text)
            except ValueError:
                manual.append(_manual(ActivityNormalizationReason.MALFORMED_EFFECTIVE_DATE, raw))
                continue
            symbol = raw.get("symbol")
            if not isinstance(symbol, str):
                manual.append(_manual(ActivityNormalizationReason.SYMBOL_NOT_OCC, raw))
                continue
            terms = _occ_terms(symbol)
            if terms is None:
                manual.append(_manual(ActivityNormalizationReason.SYMBOL_NOT_OCC, raw))
                continue
            underlying, _expiry, option_type, strike = terms
            contracts = _positive_int_qty(raw.get("qty"))
            if contracts is None:
                manual.append(_manual(ActivityNormalizationReason.MALFORMED_QTY, raw))
                continue

            if raw_id in seen_ids:
                if seen_ids[raw_id] != payload_sha:
                    manual.append(
                        _manual(ActivityNormalizationReason.DUPLICATE_ACTIVITY_CONFLICT, raw)
                    )
                    continue
                duplicates += 1
                continue
            seen_ids[raw_id] = payload_sha

            if kind is OptionEventKind.EXPIRY:
                share_delta = Decimal(0)
            else:
                share_delta = Decimal(contracts) * Decimal(100)
                long_holder = kind is OptionEventKind.EXERCISE
                if not long_holder:
                    share_delta = -share_delta
                if option_type == "P":
                    share_delta = -share_delta
            cash_delta = -(share_delta * strike)
            events.append(
                NormalizedOptionEvent.create(
                    activity_id=raw_id,
                    kind=kind,
                    status=OptionEventStatus.EXECUTED,
                    option_symbol=symbol,
                    contracts=contracts,
                    effective_date=effective_date,
                    observed_at=observed_at,
                    account_fingerprint_sha256=account_fingerprint_sha256,
                    execution_protocol_sha256=execution_protocol_sha256,
                    underlying_symbol=underlying,
                    underlying_quantity_delta=share_delta,
                    cash_delta=cash_delta,
                    replacement_symbol=None,
                    source_payload_sha256=payload_sha,
                    evidence_class=evidence_class,
                )
            )
    return ActivityNormalization(
        events=tuple(sorted(events, key=lambda event: event.activity_id)),
        skipped_activity_ids=tuple(sorted(set(skipped))),
        manual_routes=tuple(manual),
        duplicate_skip_count=duplicates,
        mapping_sha256=ACTIVITY_MAPPING_V1_SHA256,
    )


def build_activity_coverage(
    acquisition: ActivityAcquisition,
    normalization: ActivityNormalization,
    *,
    account_fingerprint_sha256: str,
    execution_protocol_sha256: str,
    observed_at: datetime,
    evidence_class: EvidenceClass,
) -> OptionActivityCoverage:
    """Bind one complete acquisition to its normalized event hash set."""

    return OptionActivityCoverage.create(
        account_fingerprint_sha256=account_fingerprint_sha256,
        execution_protocol_sha256=execution_protocol_sha256,
        window_start=acquisition.window_start,
        window_end=acquisition.window_end,
        observed_at=observed_at,
        complete=acquisition.complete,
        event_sha256s=tuple(event.event_sha256 for event in normalization.events),
        source_payload_sha256=acquisition.source_payload_sha256,
        evidence_class=evidence_class,
    )


_POSITION_REQUIRED_FIELDS = frozenset({"asset_class", "qty", "symbol"})
_ASSET_CLASS_BY_RAW = {"us_option": AssetClass.OPTION, "us_equity": AssetClass.EQUITY}


def build_portfolio_observation(
    raw_positions: bytes,
    *,
    account_fingerprint_sha256: str,
    execution_protocol_sha256: str,
    observed_at: datetime,
    evidence_class: EvidenceClass,
) -> OptionPortfolioObservation:
    """Normalize one raw `get_all_positions` payload into a typed observation.

    Zero-quantity rows are omitted; option quantities must be integral; an
    unknown asset class fails closed instead of being reinterpreted.
    """

    if type(raw_positions) is not bytes:
        raise ActivityAcquisitionRejected(
            ActivityAcquisitionReason.INVALID_PAGE, "positions payload must be bytes"
        )
    try:
        decoded = json.loads(raw_positions.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ActivityAcquisitionRejected(
            ActivityAcquisitionReason.INVALID_PAGE, f"positions payload is invalid: {error}"
        ) from error
    items = _validate_page_items(decoded)
    positions: list[PortfolioPosition] = []
    for item in items:
        missing = _POSITION_REQUIRED_FIELDS - set(item)
        if missing:
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.INVALID_PAGE,
                "position record is missing required fields",
            )
        raw_class = item.get("asset_class")
        if not isinstance(raw_class, str) or raw_class not in _ASSET_CLASS_BY_RAW:
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.INVALID_PAGE,
                "position record carries an unsupported asset class",
            )
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.INVALID_PAGE, "position record lacks a symbol"
            )
        try:
            quantity = Decimal(str(item.get("qty")))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.INVALID_PAGE, "position record has an invalid quantity"
            ) from error
        if not quantity.is_finite() or quantity == 0:
            continue
        asset_class = _ASSET_CLASS_BY_RAW[raw_class]
        if asset_class is AssetClass.OPTION and quantity != quantity.to_integral_value():
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.INVALID_PAGE,
                "option position quantity must be integral",
            )
        positions.append(
            PortfolioPosition(asset_class=asset_class, symbol=symbol, quantity=quantity)
        )
    return OptionPortfolioObservation.create(
        account_fingerprint_sha256=account_fingerprint_sha256,
        execution_protocol_sha256=execution_protocol_sha256,
        observed_at=observed_at,
        positions=positions,
        source_payload_sha256=sha256_bytes(raw_positions),
        evidence_class=evidence_class,
    )


# Working states can still be cancelled through the risk-reducing path; the
# exposure set additionally includes filled orders, whose truth lives in the
# position book rather than the cancel path.
WORKING_ORDER_STATES = frozenset(OPEN_WORKING_STATES) | frozenset(
    {BrokerOrderState.PARTIALLY_FILLED}
)
_EXPOSURE_ORDER_STATES = WORKING_ORDER_STATES | frozenset({BrokerOrderState.FILLED})
_ALL_ORDER_STATES = frozenset(
    {
        BrokerOrderState.NEW,
        BrokerOrderState.ACCEPTED,
        BrokerOrderState.PENDING_NEW,
        BrokerOrderState.ACCEPTED_FOR_BIDDING,
        BrokerOrderState.HELD,
        BrokerOrderState.CALCULATED,
        BrokerOrderState.PARTIALLY_FILLED,
        BrokerOrderState.FILLED,
        BrokerOrderState.CANCELED,
        BrokerOrderState.EXPIRED,
        BrokerOrderState.REJECTED,
    }
)


@dataclass(frozen=True, slots=True)
class OrdersStateSummary:
    """Sanitized open-order truth for preflight, reconciliation, and recovery."""

    total_order_count: int
    open_order_count: int
    status_counts: tuple[tuple[str, int], ...]
    orders_state_sha256: str
    working_orders: tuple[tuple[str, str | None, str], ...] = ()


def summarize_orders_state(raw_orders: bytes) -> OrdersStateSummary:
    """Summarize one raw `get_orders` payload; unknown statuses fail closed.

    ``working_orders`` lists ``(order_id, client_order_id)`` pairs for every
    order in an exposure state (working or filled), which reconciliation and
    restart recovery use to bind broker truth to durable local identities.
    """

    if type(raw_orders) is not bytes:
        raise ActivityAcquisitionRejected(
            ActivityAcquisitionReason.INVALID_PAGE, "orders payload must be bytes"
        )
    try:
        decoded = json.loads(raw_orders.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ActivityAcquisitionRejected(
            ActivityAcquisitionReason.INVALID_PAGE, f"orders payload is invalid: {error}"
        ) from error
    items = _validate_page_items(decoded)
    counts: dict[str, int] = {}
    open_count = 0
    working: list[tuple[str, str | None, str]] = []
    for item in items:
        status = item.get("status")
        if not isinstance(status, str) or status not in _ALL_ORDER_STATES:
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.INVALID_PAGE,
                "order record carries an unsupported status",
            )
        counts[status] = counts.get(status, 0) + 1
        if status in _EXPOSURE_ORDER_STATES:
            open_count += 1
            order_id = item.get("id")
            if not isinstance(order_id, str) or not order_id:
                raise ActivityAcquisitionRejected(
                    ActivityAcquisitionReason.INVALID_PAGE,
                    "exposure order record lacks an order id",
                )
            client_order_id = item.get("client_order_id")
            if client_order_id is not None and (
                not isinstance(client_order_id, str) or not client_order_id
            ):
                raise ActivityAcquisitionRejected(
                    ActivityAcquisitionReason.INVALID_PAGE,
                    "exposure order record has an invalid client order id",
                )
            working.append((order_id, client_order_id, status))
    return OrdersStateSummary(
        total_order_count=len(items),
        open_order_count=open_count,
        status_counts=tuple(sorted(counts.items())),
        orders_state_sha256=sha256_bytes(raw_orders),
        working_orders=tuple(sorted(working)),
    )


__all__ = [
    "ACQUISITION_DIGEST_SCHEMA",
    "ACQUISITION_DIGEST_SCHEMA_VERSION",
    "ACTIVITY_CURSOR_FILENAME",
    "ACTIVITY_CURSOR_SCHEMA",
    "ACTIVITY_CURSOR_SCHEMA_VERSION",
    "ACTIVITY_MAPPING_V1",
    "ACTIVITY_MAPPING_V1_SHA256",
    "ACTIVITY_PAGE_MAX_SIZE",
    "ACTIVITY_SOURCE_ID",
    "WORKING_ORDER_STATES",
    "AccountActivitySource",
    "ActivityAcquisition",
    "ActivityAcquisitionReason",
    "ActivityAcquisitionRejected",
    "ActivityCursorJournal",
    "ActivityManualRoute",
    "ActivityNormalization",
    "ActivityNormalizationReason",
    "ActivityPageRequest",
    "McpAccountActivitySource",
    "OrdersStateSummary",
    "acquire_account_activities",
    "acquisition_source_payload_sha256",
    "build_activity_coverage",
    "build_portfolio_observation",
    "normalize_account_activities",
    "summarize_orders_state",
]
