"""Issue #90: read-only Alpaca activity acquisition, cursors, and typed mapping."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from esscher.contracts.execution_policy import (
    ALPACA_MCP_V2_PROTOCOL_SHA256,
)
from esscher.execution.host_mcp import (
    HostMcpEnvironment,
    HostMcpPaperSessionFactory,
    HostMcpSessionIdentity,
)
from esscher.runtime.option_events import (
    AssetClass,
    EvidenceClass,
    OptionEventKind,
    parse_option_activity_coverage,
    parse_option_portfolio_observation,
)
from esscher.sourcedata.alpaca_option_events import (
    ACTIVITY_MAPPING_V1_SHA256,
    ActivityAcquisitionReason,
    ActivityAcquisitionRejected,
    ActivityCursorJournal,
    ActivityNormalizationReason,
    ActivityPageRequest,
    McpAccountActivitySource,
    acquire_account_activities,
    build_activity_coverage,
    build_portfolio_observation,
    normalize_account_activities,
    summarize_orders_state,
)
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 9, 18, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)
FINGERPRINT = "aa" * 32
FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "alpaca_option_events"
    / "synthetic_broker_shaped_activities_v1.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture_pages() -> list[list[dict]]:
    return [list(page) for page in _fixture()["pages"]]


def _page_bytes(items: list[dict]) -> bytes:
    return canonical_json_bytes(items)


class FakeActivitySource:
    def __init__(
        self, pages: list[list[dict]] | list[bytes], *, fail_on_call: int | None = None
    ) -> None:
        self.pages: list[bytes] = [
            page if isinstance(page, bytes) else _page_bytes(page) for page in pages
        ]
        self.requests: list[ActivityPageRequest] = []
        self.fail_on_call = fail_on_call

    async def fetch_activity_page(self, request: ActivityPageRequest) -> bytes:
        self.requests.append(request)
        if self.fail_on_call is not None and len(self.requests) == self.fail_on_call:
            raise ActivityAcquisitionRejected(
                ActivityAcquisitionReason.SOURCE_UNAVAILABLE, "injected transport failure"
            )
        if not self.pages:
            raise AssertionError("source exhausted")
        return self.pages.pop(0)


def _acquire(source, *, journal=None, page_size=2, max_pages=200):
    return asyncio.run(
        acquire_account_activities(
            source,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            page_size=page_size,
            max_pages=max_pages,
            journal=journal,
            clock=lambda: NOW,
        )
    )


def _normalize(acquisition, evidence_class=EvidenceClass.SYNTHETIC_FIXTURE):
    return normalize_account_activities(
        acquisition,
        account_fingerprint_sha256=FINGERPRINT,
        execution_protocol_sha256=ALPACA_MCP_V2_PROTOCOL_SHA256,
        evidence_class=evidence_class,
    )


def test_fixture_binds_the_hashed_mapping_contract() -> None:
    provenance = _fixture()["provenance"]
    assert provenance["mapping_sha256"] == ACTIVITY_MAPPING_V1_SHA256
    assert provenance["source_id"] == "alpaca.account_activities.v2"
    assert provenance["class"] == "SYNTHETIC_BROKER_SHAPED"
    assert provenance["claim"] == "NOT_BROKER_CONNECTIVITY_EVIDENCE"


def test_fixture_acquires_three_pages_and_normalizes_semantically() -> None:
    source = FakeActivitySource(_fixture_pages())

    acquisition = _acquire(source)

    assert acquisition.complete is True
    assert acquisition.activity_count == 5
    assert len(acquisition.pages) == 3
    assert source.requests[0].page_token is None
    assert source.requests[1].page_token == "20260918110000000::opasn001::1"
    assert source.requests[2].page_token == "20260918130000000::opca0001::1"
    assert acquisition.last_activity_id == "20260918140000000::opexp001::1"
    assert acquisition.source_payload_sha256 == sha256_bytes(
        canonical_json_bytes(
            {
                "schema": "esscher.activity_acquisition_digest",
                "schema_version": 1,
                "window_start": "2026-09-18T00:00:00.000000Z",
                "window_end": "2026-09-19T00:00:00.000000Z",
                "page_sha256s": list(acquisition.page_sha256s),
                "activity_count": 5,
                "complete": True,
            }
        )
    )

    normalization = _normalize(acquisition)

    assert normalization.mapping_sha256 == ACTIVITY_MAPPING_V1_SHA256
    assert [event.kind for event in normalization.events] == [
        OptionEventKind.ASSIGNMENT,
        OptionEventKind.EXERCISE,
        OptionEventKind.EXPIRY,
    ]
    assert normalization.skipped_activity_ids == ("20260918100000000::fill0001::1",)
    assert [(route.reason, route.activity_id) for route in normalization.manual_routes] == [
        (ActivityNormalizationReason.UNMAPPABLE_ACTIVITY_TYPE, "20260918130000000::opca0001::1")
    ]
    assert normalization.duplicate_skip_count == 0


def test_mapped_economics_follow_the_hashed_contract_exactly() -> None:
    acquisition = _acquire(FakeActivitySource(_fixture_pages()))

    events = {event.kind: event for event in _normalize(acquisition).events}

    assignment = events[OptionEventKind.ASSIGNMENT]
    assert assignment.option_symbol == "AAPL260918C00061000"
    assert assignment.underlying_symbol == "AAPL"
    assert assignment.contracts == 1
    # Short-call assignment: deliver shares, receive strike cash.
    assert assignment.underlying_quantity_delta == Decimal(-100)
    assert assignment.cash_delta == Decimal(6100)

    exercise = events[OptionEventKind.EXERCISE]
    assert exercise.option_symbol == "AAPL260918P00060000"
    assert exercise.contracts == 2
    # Long-put exercise: deliver shares, receive strike cash (put flips sign).
    assert exercise.underlying_quantity_delta == Decimal(-200)
    assert exercise.cash_delta == Decimal(12000)

    expiry = events[OptionEventKind.EXPIRY]
    assert expiry.underlying_quantity_delta == Decimal(0)
    assert expiry.cash_delta == Decimal(0)
    assert expiry.effective_date.isoformat() == "2026-09-18"

    for event in events.values():
        assert event.account_fingerprint_sha256 == FINGERPRINT
        assert event.execution_protocol_sha256 == ALPACA_MCP_V2_PROTOCOL_SHA256
        assert event.evidence_class is EvidenceClass.SYNTHETIC_FIXTURE
        assert len(event.source_payload_sha256) == 64


def test_normalization_is_deterministic_and_replay_idempotent() -> None:
    first = _acquire(FakeActivitySource(_fixture_pages()))
    second = _acquire(FakeActivitySource(_fixture_pages()))

    assert first.source_payload_sha256 == second.source_payload_sha256
    assert first.page_sha256s == second.page_sha256s

    events_a = [event.to_json_bytes() for event in _normalize(first).events]
    events_b = [event.to_json_bytes() for event in _normalize(second).events]
    assert events_a == events_b


def test_coverage_binds_the_normalized_event_set_and_roundtrips() -> None:
    acquisition = _acquire(FakeActivitySource(_fixture_pages()))
    normalization = _normalize(acquisition)

    coverage = build_activity_coverage(
        acquisition,
        normalization,
        account_fingerprint_sha256=FINGERPRINT,
        execution_protocol_sha256=ALPACA_MCP_V2_PROTOCOL_SHA256,
        observed_at=WINDOW_END,
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
    )

    assert coverage.complete is True
    assert coverage.event_sha256s == tuple(
        sorted(event.event_sha256 for event in normalization.events)
    )
    parsed = parse_option_activity_coverage(coverage.to_json_bytes())
    assert parsed == coverage


def test_empty_window_acquires_one_empty_page_and_is_complete() -> None:
    acquisition = _acquire(FakeActivitySource([[]]), page_size=2)

    assert acquisition.complete is True
    assert acquisition.activity_count == 0
    normalization = _normalize(acquisition)
    assert normalization.events == ()
    assert normalization.manual_routes == ()


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda record: record.update(activity_type="XYZ"), "UNKNOWN_ACTIVITY_TYPE"),
        (lambda record: record.update(future_field=1), "UNKNOWN_ACTIVITY_FIELD"),
        (lambda record: record.pop("qty"), "MISSING_ACTIVITY_FIELD"),
        (lambda record: record.update(symbol="NOT_AN_OCC"), "SYMBOL_NOT_OCC"),
        (lambda record: record.update(qty="0"), "MALFORMED_QTY"),
        (lambda record: record.update(qty="abc"), "MALFORMED_QTY"),
        (lambda record: record.update(transaction_time="yesterday"), "MALFORMED_TIMESTAMP"),
        (
            lambda record: record.update(transaction_time="2026-09-17T23:00:00Z"),
            "OUT_OF_WINDOW",
        ),
        (lambda record: record.update(date="18-09-2026"), "MALFORMED_EFFECTIVE_DATE"),
    ],
)
def test_malformed_or_unknown_records_route_to_manual_never_guess(mutator, reason: str) -> None:
    record = {
        "activity_type": "OPASN",
        "date": "2026-09-18",
        "id": "20260918110000000::opasn001::1",
        "qty": "1",
        "symbol": "AAPL260918C00061000",
        "transaction_time": "2026-09-18T11:00:00Z",
    }
    mutator(record)
    acquisition = _acquire(FakeActivitySource([[record]]), page_size=10)

    normalization = _normalize(acquisition)

    assert normalization.events == ()
    assert [route.reason.value for route in normalization.manual_routes] == [reason]


def test_missing_id_fails_the_whole_page_at_acquisition() -> None:
    record = {
        "activity_type": "OPASN",
        "date": "2026-09-18",
        "qty": "1",
        "symbol": "AAPL260918C00061000",
        "transaction_time": "2026-09-18T11:00:00Z",
    }
    page = canonical_json_bytes([record])

    # The activity id is the pagination and dedup identity; a page without one
    # is untrustworthy as a whole and fails closed before normalization.
    with pytest.raises(ActivityAcquisitionRejected) as captured:
        _acquire(FakeActivitySource([page]), page_size=10)
    assert captured.value.reason is ActivityAcquisitionReason.INVALID_PAGE


def test_duplicate_activity_bytes_are_idempotent_and_conflicts_route_manual() -> None:
    record = {
        "activity_type": "OPEXP",
        "date": "2026-09-18",
        "id": "20260918140000000::opexp001::1",
        "qty": "1",
        "symbol": "AAPL260918C00061000",
        "transaction_time": "2026-09-18T14:00:00Z",
    }
    identical = _acquire(FakeActivitySource([[record, dict(record)]]), page_size=10)
    normalization = _normalize(identical)
    assert len(normalization.events) == 1
    assert normalization.duplicate_skip_count == 1
    assert normalization.manual_routes == ()

    contradicted = dict(record)
    contradicted["qty"] = "5"
    conflicting = _acquire(FakeActivitySource([[record, contradicted]]), page_size=10)
    conflict_normalization = _normalize(conflicting)
    assert len(conflict_normalization.events) == 1
    assert [route.reason for route in conflict_normalization.manual_routes] == [
        ActivityNormalizationReason.DUPLICATE_ACTIVITY_CONFLICT
    ]


def test_pagination_cycle_and_budget_fail_closed() -> None:
    full_page = [
        {
            "activity_type": "FILL",
            "id": f"fill::{index}",
            "symbol": "AAPL",
            "transaction_time": "2026-09-18T10:00:00Z",
        }
        for index in range(2)
    ]

    class CyclingSource(FakeActivitySource):
        async def fetch_activity_page(self, request: ActivityPageRequest) -> bytes:
            self.requests.append(request)
            return _page_bytes(full_page)

    with pytest.raises(ActivityAcquisitionRejected) as cycle:
        _acquire(CyclingSource([]), page_size=2)
    assert cycle.value.reason is ActivityAcquisitionReason.PAGINATION_CYCLE

    with pytest.raises(ActivityAcquisitionRejected) as budget:
        _acquire(CyclingSource([]), page_size=2, max_pages=2)
    assert budget.value.reason is ActivityAcquisitionReason.PAGINATION_BUDGET_EXHAUSTED


def test_invalid_window_and_page_size_fail_closed() -> None:
    source = FakeActivitySource(_fixture_pages())
    with pytest.raises(ActivityAcquisitionRejected) as window:
        asyncio.run(
            acquire_account_activities(
                source, window_start=WINDOW_END, window_end=WINDOW_START, page_size=2
            )
        )
    assert window.value.reason is ActivityAcquisitionReason.WINDOW_INCONSISTENT

    with pytest.raises(ActivityAcquisitionRejected) as size:
        asyncio.run(
            acquire_account_activities(
                FakeActivitySource(_fixture_pages()),
                window_start=WINDOW_START,
                window_end=WINDOW_END,
                page_size=101,
            )
        )
    assert size.value.reason is ActivityAcquisitionReason.PAGE_SIZE_OUT_OF_RANGE


def test_invalid_page_shapes_fail_closed() -> None:
    with pytest.raises(ActivityAcquisitionRejected) as not_array:
        _acquire(FakeActivitySource([b"{}"]), page_size=10)
    assert not_array.value.reason is ActivityAcquisitionReason.INVALID_PAGE

    with pytest.raises(ActivityAcquisitionRejected) as no_id:
        _acquire(FakeActivitySource([b'[{"activity_type":"FILL"}]']), page_size=10)
    assert no_id.value.reason is ActivityAcquisitionReason.INVALID_PAGE


def test_cursor_journal_resumes_after_failure_to_the_identical_acquisition(
    tmp_path: Path,
) -> None:
    pages = _fixture_pages()
    fresh = _acquire(FakeActivitySource(pages))

    journal_path = tmp_path / "activity_cursor.jsonl"
    failing = FakeActivitySource(pages, fail_on_call=3)
    with pytest.raises(ActivityAcquisitionRejected) as injected:
        _acquire(failing, journal=ActivityCursorJournal(journal_path))
    assert injected.value.reason is ActivityAcquisitionReason.SOURCE_UNAVAILABLE

    journal = ActivityCursorJournal(journal_path)
    assert journal.chain_valid(WINDOW_START, WINDOW_END)

    resumed_source = FakeActivitySource([pages[2]])
    resumed = _acquire(resumed_source, journal=journal)

    assert len(resumed_source.requests) == 1
    assert resumed_source.requests[0].page_token == "20260918130000000::opca0001::1"
    assert resumed.pages == fresh.pages
    assert resumed.page_sha256s == fresh.page_sha256s
    assert resumed.activity_count == fresh.activity_count == 5
    assert resumed.complete is True
    assert resumed.source_payload_sha256 == fresh.source_payload_sha256
    assert _normalize(resumed).events == _normalize(fresh).events


def test_tampered_or_foreign_cursor_journal_fails_closed(tmp_path: Path) -> None:
    pages = _fixture_pages()
    journal_path = tmp_path / "activity_cursor.jsonl"
    failing = FakeActivitySource(pages, fail_on_call=3)
    with pytest.raises(ActivityAcquisitionRejected):
        _acquire(failing, journal=ActivityCursorJournal(journal_path))

    tampered = journal_path.read_text(encoding="utf-8").replace(
        '"activities_in_page":2', '"activities_in_page":3', 1
    )
    journal_path.write_text(tampered, encoding="utf-8")
    with pytest.raises(ActivityAcquisitionRejected) as broken:
        _acquire(FakeActivitySource([pages[2]]), journal=ActivityCursorJournal(journal_path))
    assert broken.value.reason is ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN

    clean_path = tmp_path / "clean.jsonl"
    failing2 = FakeActivitySource(pages, fail_on_call=3)
    with pytest.raises(ActivityAcquisitionRejected):
        _acquire(failing2, journal=ActivityCursorJournal(clean_path))
    with pytest.raises(ActivityAcquisitionRejected) as foreign:
        asyncio.run(
            acquire_account_activities(
                FakeActivitySource([pages[2]]),
                window_start=WINDOW_START + timedelta(days=1),
                window_end=WINDOW_END + timedelta(days=1),
                page_size=2,
                journal=ActivityCursorJournal(clean_path),
                clock=lambda: NOW,
            )
        )
    assert foreign.value.reason is ActivityAcquisitionReason.CURSOR_CHAIN_BROKEN


def test_portfolio_observation_normalizes_positions_and_fails_closed() -> None:
    raw = canonical_json_bytes(
        [
            {"asset_class": "us_option", "qty": "1", "symbol": "AAPL260918C00061000"},
            {"asset_class": "us_option", "qty": "-2", "symbol": "AAPL260918P00060000"},
            {"asset_class": "us_equity", "qty": "100", "symbol": "AAPL"},
            {"asset_class": "us_equity", "qty": "0", "symbol": "ZZZ"},
        ]
    )

    observation = build_portfolio_observation(
        raw,
        account_fingerprint_sha256=FINGERPRINT,
        execution_protocol_sha256=ALPACA_MCP_V2_PROTOCOL_SHA256,
        observed_at=NOW,
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
    )

    assert [
        (position.asset_class, position.symbol, position.quantity)
        for position in observation.positions
    ] == [
        (AssetClass.EQUITY, "AAPL", Decimal(100)),
        (AssetClass.OPTION, "AAPL260918C00061000", Decimal(1)),
        (AssetClass.OPTION, "AAPL260918P00060000", Decimal(-2)),
    ]
    assert observation.source_payload_sha256 == sha256_bytes(raw)
    assert parse_option_portfolio_observation(observation.to_json_bytes()) == observation

    crypto = canonical_json_bytes([{"asset_class": "crypto", "qty": "1", "symbol": "BTCUSD"}])
    with pytest.raises(ActivityAcquisitionRejected, match="asset class"):
        build_portfolio_observation(
            crypto,
            account_fingerprint_sha256=FINGERPRINT,
            execution_protocol_sha256=ALPACA_MCP_V2_PROTOCOL_SHA256,
            observed_at=NOW,
            evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        )

    fractional = canonical_json_bytes(
        [{"asset_class": "us_option", "qty": "1.5", "symbol": "AAPL260918C00061000"}]
    )
    with pytest.raises(ActivityAcquisitionRejected, match="integral"):
        build_portfolio_observation(
            fractional,
            account_fingerprint_sha256=FINGERPRINT,
            execution_protocol_sha256=ALPACA_MCP_V2_PROTOCOL_SHA256,
            observed_at=NOW,
            evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        )


def test_orders_summary_counts_exposure_states_and_rejects_unknown() -> None:
    raw = canonical_json_bytes(
        [
            {"id": "1", "status": "new"},
            {"id": "2", "status": "partially_filled"},
            {"id": "3", "status": "filled"},
            {"id": "4", "status": "canceled"},
            {"id": "5", "status": "rejected"},
        ]
    )

    summary = summarize_orders_state(raw)

    assert summary.total_order_count == 5
    assert summary.open_order_count == 3
    assert summary.status_counts == (
        ("canceled", 1),
        ("filled", 1),
        ("new", 1),
        ("partially_filled", 1),
        ("rejected", 1),
    )
    assert summary.orders_state_sha256 == sha256_bytes(raw)

    unknown = canonical_json_bytes([{"id": "1", "status": "weird"}])
    with pytest.raises(ActivityAcquisitionRejected, match="unsupported status"):
        summarize_orders_state(unknown)


class FakeMcpHost:
    TOOLS = (
        "cancel_order_by_id",
        "get_account_activities",
        "get_account_info",
        "get_all_positions",
        "get_order_by_client_id",
        "get_order_by_id",
        "get_orders",
        "place_option_order",
    )

    def __init__(self, activities_response: object) -> None:
        self.activities_response = activities_response
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_tools(self) -> object:
        return list(self.TOOLS)

    async def call_tool(self, name: str, arguments) -> object:
        self.calls.append((name, dict(arguments)))
        if name == "get_account_info":
            return {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False}
        if name == "get_account_activities":
            return self.activities_response
        return {"ok": True}


def _prepared_session(host: FakeMcpHost):
    factory = HostMcpPaperSessionFactory(
        HostMcpSessionIdentity(environment=HostMcpEnvironment.PAPER), clock=lambda: NOW
    )
    return asyncio.run(factory.connect(host))


def test_mcp_source_requests_exact_readonly_pages_and_canonicalizes() -> None:
    page = _fixture()["pages"][0]
    host = FakeMcpHost(page)
    source = McpAccountActivitySource(_prepared_session(host))

    raw = asyncio.run(
        source.fetch_activity_page(
            ActivityPageRequest(
                window_start=WINDOW_START,
                window_end=WINDOW_END,
                page_size=50,
                page_token="token-1",
            )
        )
    )

    assert json.loads(raw) == page
    assert raw == canonical_json_bytes(page)
    activities_calls = [call for call in host.calls if call[0] == "get_account_activities"]
    assert activities_calls == [
        (
            "get_account_activities",
            {
                "after": "2026-09-18T00:00:00.000000Z",
                "until": "2026-09-19T00:00:00.000000Z",
                "direction": "asc",
                "page_size": 50,
                "page_token": "token-1",
            },
        )
    ]
    assert not ({name for name, _ in host.calls} & {"place_option_order", "cancel_order_by_id"})


def test_mcp_source_rejects_non_array_pages_and_requires_prepared_capability() -> None:
    host = FakeMcpHost({"not": "an array"})
    source = McpAccountActivitySource(_prepared_session(host))

    with pytest.raises(ActivityAcquisitionRejected) as invalid:
        asyncio.run(
            source.fetch_activity_page(
                ActivityPageRequest(
                    window_start=WINDOW_START, window_end=WINDOW_END, page_size=50, page_token=None
                )
            )
        )
    assert invalid.value.reason is ActivityAcquisitionReason.INVALID_PAGE

    with pytest.raises(ActivityAcquisitionRejected, match="factory-prepared"):
        McpAccountActivitySource(object())  # type: ignore[arg-type]
