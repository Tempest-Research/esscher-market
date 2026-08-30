"""Regression contract for the read-only strategy snapshot collector."""

from __future__ import annotations

import ast
import copy
import socket
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ringdown_market.sourcedata.adjustments import (
    AdjustedClose,
    AdjustmentOutcome,
    adjust_series,
    split_factor,
)
from ringdown_market.sourcedata.betas import (
    estimate_betas,
    select_beta_window,
)
from ringdown_market.sourcedata.compiler import (
    CaptureConfiguration,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
    load_fixture,
)
from ringdown_market.sourcedata.interfaces import CorporateAction, DailyBar, SourceProvenance, Trade
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.receipts import (
    SourceReceipt,
    corporate_action_receipt_bytes,
    parse_corporate_action_receipt,
    parse_source_receipt,
    source_receipt_bytes,
)
from ringdown_market.sourcedata.windows import build_synchronized_window
from ringdown_market.strategy.models import FeatureStatus, ReleaseFamily, TimingBucket

EVENT_ID = "KR-2026Q2-EARNINGS"
PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "ringdown_market" / "sourcedata"


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _configuration(fixture, capture_at: str = "2026-09-11T13:35:10Z") -> CaptureConfiguration:
    return CaptureConfiguration(
        candidate_manifest_bytes=build_candidate_manifest(fixture),
        event_id=EVENT_ID,
        capture_at=_at(capture_at),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )


def _compile(fixture=None, capture_at: str = "2026-09-11T13:35:10Z"):
    fixture = fixture if fixture is not None else load_fixture()
    return compile_strategy_snapshot(
        _configuration(fixture, capture_at),
        FixtureEvidenceSource(fixture),
        FixtureMarketDataSource(fixture),
    )


@pytest.fixture(scope="module")
def compiled_snapshot():
    return _compile()


def _rejects(fixture, reason: CollectorReason, *, capture_at: str = "2026-09-11T13:35:10Z"):
    with pytest.raises(CollectorRejected) as caught:
        _compile(fixture, capture_at)
    assert caught.value.reason is reason
    return caught.value


def test_identical_inputs_produce_byte_identical_snapshots(compiled_snapshot) -> None:
    rerun = _compile()
    assert rerun.strategy_snapshot_bytes == compiled_snapshot.strategy_snapshot_bytes
    assert rerun.feature_receipt_bytes == compiled_snapshot.feature_receipt_bytes
    assert rerun.evidence_packet.packet_sha256 == compiled_snapshot.evidence_packet.packet_sha256


def test_compiled_bundle_passes_the_frozen_strategy_contract(compiled_snapshot) -> None:
    joined = compiled_strategy_input(compiled_snapshot)
    assert joined.snapshot.event_id == EVENT_ID
    assert joined.feature_receipt.feature_snapshot_at <= joined.snapshot.decision_cutoff_at
    assert joined.feature_receipt.created_at <= joined.snapshot.decision_cutoff_at
    assert joined.snapshot_sha256 != joined.feature_receipt_sha256


def test_snapshot_carries_all_policy_features_and_claim_labels(compiled_snapshot) -> None:
    features = compiled_snapshot.feature_receipt.features
    assert len(features) == 13
    statuses = {feature.feature_id: feature.status for feature in features}
    assert statuses["earnings.eps_consensus_surprise_pct.v1"] is FeatureStatus.UNAVAILABLE
    assert statuses["earnings.revenue_consensus_surprise_pct.v1"] is FeatureStatus.UNAVAILABLE
    assert statuses["earnings.eps_timeseries_sue.v1"] is FeatureStatus.PRESENT
    assert statuses["market.opening_residual_log_return.v1"] is FeatureStatus.PRESENT
    assert compiled_snapshot.snapshot.eligibility_reason_codes == ()
    assert compiled_snapshot.snapshot.health_reason_codes == ()


def test_capture_clock_matches_frozen_policy_times(compiled_snapshot) -> None:
    snapshot = compiled_snapshot.snapshot
    assert snapshot.observation_window_start_at == _at("2026-09-11T13:30:00Z")
    assert snapshot.observation_window_end_at == _at("2026-09-11T13:35:00Z")
    assert snapshot.evidence_cutoff_at == _at("2026-09-11T13:35:15Z")
    assert snapshot.decision_cutoff_at == _at("2026-09-11T13:36:05Z")
    assert snapshot.candidate_entry_deadline_at == _at("2026-09-11T13:37:00Z")


def test_capture_after_evidence_cutoff_fails_closed() -> None:
    _rejects(
        load_fixture(), CollectorReason.RETRIEVED_AFTER_CUTOFF, capture_at="2026-09-11T13:35:30Z"
    )


def test_capture_after_decision_cutoff_fails_closed() -> None:
    _rejects(
        load_fixture(), CollectorReason.RETRIEVED_AFTER_CUTOFF, capture_at="2026-09-11T13:37:00Z"
    )


def test_release_published_after_open_fails_closed() -> None:
    fixture = load_fixture()
    fixture = copy.deepcopy(fixture)
    fixture["issuer_release"]["provenance"]["published_at"] = "2026-09-11T13:45:00Z"
    _rejects(fixture, CollectorReason.PRIMARY_RELEASE_LATE)


def test_missing_primary_release_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["issuer_release"]["event_id"] = "OTHER-EVENT"
    _rejects(fixture, CollectorReason.PRIMARY_RELEASE_MISSING)


def test_publication_time_unknown_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["issuer_release"]["provenance"]["published_at"] = None
    _rejects(fixture, CollectorReason.PUBLICATION_TIME_UNKNOWN)


def test_unverified_entitlement_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["issuer_release"]["provenance"]["entitlement"] = "UNVERIFIED"
    fixture["issuer_release"]["provenance"]["limitations"] = ["RIGHTS_UNREVIEWED"]
    _rejects(fixture, CollectorReason.SOURCE_RIGHTS_UNVERIFIED)


def test_unpermitted_source_class_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["security_master"]["provenance"]["source_class"] = "UNREGISTERED_FEED"
    _rejects(fixture, CollectorReason.UNPERMITTED_SOURCE_CLASS)


def test_price_below_minimum_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["security_master"]["prior_regular_close"] = "9.99"
    _rejects(fixture, CollectorReason.PRICE_BELOW_MINIMUM)


def test_unknown_sector_mapping_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["security_master"]["sector"] = "CRYPTO_ASSETS"
    _rejects(fixture, CollectorReason.UNSUPPORTED_INPUT)


def test_ineligible_event_fails_closed() -> None:
    fixture = load_fixture()
    configuration = CaptureConfiguration(
        candidate_manifest_bytes=build_candidate_manifest(fixture),
        event_id="ZZZZ-2026Q3-EARNINGS",
        capture_at=_at("2026-09-11T13:35:10Z"),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )
    with pytest.raises(CollectorRejected) as caught:
        compile_strategy_snapshot(
            configuration, FixtureEvidenceSource(fixture), FixtureMarketDataSource(fixture)
        )
    assert caught.value.reason is CollectorReason.EVENT_NOT_CONFIRMED


def test_symbol_change_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["corporate_actions"].append(
        {
            "ticker": "KR",
            "action_type": "SYMBOL_CHANGE",
            "ex_date": "2026-05-01",
            "ratio_numerator": None,
            "ratio_denominator": None,
            "symbol_from": "KR",
            "symbol_to": "KRX",
            "provenance": fixture["corporate_actions"][0]["provenance"],
        }
    )
    _rejects(fixture, CollectorReason.CORPORATE_ACTION_UNRESOLVED)


def test_unverified_quote_entitlement_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["market_entitlement"] = "UNVERIFIED"
    _rejects(fixture, CollectorReason.SOURCE_RIGHTS_UNVERIFIED)


def test_quote_spread_requires_minimum_samples() -> None:
    from ringdown_market.sourcedata.interfaces import QuoteSample
    from ringdown_market.sourcedata.reasons import CollectorReason as _Reason
    from ringdown_market.sourcedata.windows import quote_spread_basis_points

    quotes = tuple(
        QuoteSample(
            symbol="KR",
            session_id="XNYS-2026-09-11",
            observed_at=_at(f"2026-09-11T13:34:{second:02d}Z"),
            bid=Decimal("62.10"),
            ask=Decimal("62.12"),
        )
        for second in range(1, 30)
    )
    with pytest.raises(CollectorRejected) as caught:
        quote_spread_basis_points(quotes, symbol="KR", window_end_at=_at("2026-09-11T13:35:00Z"))
    assert caught.value.reason is _Reason.MARKET_OBSERVATION_MISSING


def _split_action(ticker: str, ex_date: str) -> CorporateAction:
    return CorporateAction(
        ticker=ticker,
        action_type="SPLIT",
        ex_date=datetime.fromisoformat(ex_date).date(),
        ratio_numerator=2,
        ratio_denominator=1,
        symbol_from=None,
        symbol_to=None,
        provenance=SourceProvenance(
            source_class="CORPORATE_ACTION_RECORD",
            publisher="SYNTHETIC_SECURITY_MASTER_FEED",
            content_sha256="c" * 64,
            published_at=None,
            published_at_precision="DATE",
            retrieved_at=_at("2026-09-11T11:00:00Z"),
            entitlement="ENTITLED",
            redistribution_status="NON_REDISTRIBUTABLE",
            limitations=("LICENSED_REFERENCE_DATA",),
        ),
    )


def test_split_adjustment_halves_pre_ex_date_prices() -> None:
    action = _split_action("KR", "2026-03-13")
    bars = (
        DailyBar(
            "KR", "XNYS-2026-03-12", datetime(2026, 3, 12).date(), Decimal("120.00"), 1000, True
        ),
        DailyBar(
            "KR", "XNYS-2026-03-13", datetime(2026, 3, 13).date(), Decimal("60.00"), 1000, True
        ),
    )
    receipt_id = "action-kr-2026-03-13-split"
    from ringdown_market.sourcedata.receipts import CorporateActionReceipt

    receipt = CorporateActionReceipt.from_action(receipt_id, action, source_receipt_id="source")
    outcome = adjust_series(bars, (action,), ticker="KR", receipts_by_action={action: receipt})
    assert outcome.series[0].adjusted_close == Decimal("60.00")
    assert outcome.series[1].adjusted_close == Decimal("60.00")
    assert outcome.applied_receipt_ids == (receipt_id,)
    assert split_factor(action) == Decimal("0.5")


def test_conflicting_splits_fail_closed() -> None:
    first = _split_action("KR", "2026-03-13")
    second = _split_action("KR", "2026-03-13")
    bars = (
        DailyBar("KR", "XNYS-2026-03-12", datetime(2026, 3, 12).date(), Decimal("120.00"), 1, True),
        DailyBar("KR", "XNYS-2026-03-13", datetime(2026, 3, 13).date(), Decimal("60.00"), 1, True),
    )
    with pytest.raises(CollectorRejected) as caught:
        adjust_series(bars, (first, second), ticker="KR", receipts_by_action={})
    assert caught.value.reason is CollectorReason.MATERIAL_SOURCE_CONFLICT


def test_split_without_receipt_fails_closed() -> None:
    action = _split_action("KR", "2026-03-13")
    bars = (
        DailyBar("KR", "XNYS-2026-03-12", datetime(2026, 3, 12).date(), Decimal("120.00"), 1, True),
        DailyBar("KR", "XNYS-2026-03-13", datetime(2026, 3, 13).date(), Decimal("60.00"), 1, True),
    )
    with pytest.raises(CollectorRejected) as caught:
        adjust_series(bars, (action,), ticker="KR", receipts_by_action={})
    assert caught.value.reason is CollectorReason.CORPORATE_ACTION_UNRESOLVED


def _trade(symbol: str, observed_at: str, price: str = "100.00") -> Trade:
    return Trade(
        symbol=symbol,
        session_id="XNYS-2026-09-11",
        observed_at=_at(observed_at),
        price=Decimal(price),
        size=100,
        sale_condition="REGULAR_CONTINUOUS",
    )


def test_window_rejects_missing_observations() -> None:
    with pytest.raises(CollectorRejected) as caught:
        build_synchronized_window(
            {"KR": ()},
            {},
            session_id="XNYS-2026-09-11",
            symbols=("KR",),
            window_start_at=_at("2026-09-11T13:30:00Z"),
            window_end_at=_at("2026-09-11T13:35:00Z"),
            require_quotes=False,
        )
    assert caught.value.reason is CollectorReason.MARKET_OBSERVATION_MISSING


def test_window_rejects_late_start() -> None:
    with pytest.raises(CollectorRejected) as caught:
        build_synchronized_window(
            {"KR": (_trade("KR", "2026-09-11T13:30:20Z"), _trade("KR", "2026-09-11T13:34:55Z"))},
            {},
            session_id="XNYS-2026-09-11",
            symbols=("KR",),
            window_start_at=_at("2026-09-11T13:30:00Z"),
            window_end_at=_at("2026-09-11T13:35:00Z"),
            require_quotes=False,
        )
    assert caught.value.reason is CollectorReason.MARKET_OBSERVATION_MISSING


def test_window_rejects_stale_end() -> None:
    with pytest.raises(CollectorRejected) as caught:
        build_synchronized_window(
            {"KR": (_trade("KR", "2026-09-11T13:30:02Z"), _trade("KR", "2026-09-11T13:34:00Z"))},
            {},
            session_id="XNYS-2026-09-11",
            symbols=("KR",),
            window_start_at=_at("2026-09-11T13:30:00Z"),
            window_end_at=_at("2026-09-11T13:35:00Z"),
            require_quotes=False,
        )
    assert caught.value.reason is CollectorReason.MARKET_OBSERVATION_STALE


def test_window_rejects_asynchronous_endpoints() -> None:
    with pytest.raises(CollectorRejected) as caught:
        build_synchronized_window(
            {
                "KR": (_trade("KR", "2026-09-11T13:30:02Z"), _trade("KR", "2026-09-11T13:34:58Z")),
                "SPY": (
                    _trade("SPY", "2026-09-11T13:30:03Z"),
                    _trade("SPY", "2026-09-11T13:34:50Z"),
                ),
            },
            {},
            session_id="XNYS-2026-09-11",
            symbols=("KR", "SPY"),
            window_start_at=_at("2026-09-11T13:30:00Z"),
            window_end_at=_at("2026-09-11T13:35:00Z"),
            require_quotes=False,
        )
    assert caught.value.reason is CollectorReason.MARKET_OBSERVATION_ASYNCHRONOUS


def test_window_rejects_duplicate_observations() -> None:
    with pytest.raises(CollectorRejected) as caught:
        build_synchronized_window(
            {
                "KR": (
                    _trade("KR", "2026-09-11T13:30:02Z"),
                    _trade("KR", "2026-09-11T13:30:02Z", price="101.00"),
                    _trade("KR", "2026-09-11T13:34:55Z"),
                )
            },
            {},
            session_id="XNYS-2026-09-11",
            symbols=("KR",),
            window_start_at=_at("2026-09-11T13:30:00Z"),
            window_end_at=_at("2026-09-11T13:35:00Z"),
            require_quotes=False,
        )
    assert caught.value.reason is CollectorReason.DUPLICATE_OBSERVATION


def _series(symbol: str, closes: tuple[str, ...], dates: tuple) -> AdjustmentOutcome:
    return AdjustmentOutcome(
        symbol=symbol,
        series=tuple(
            AdjustedClose(
                session_id=f"S{index}",
                session_date=dates[index],
                adjusted_close=Decimal(close),
                volume=1000,
                valid=True,
            )
            for index, close in enumerate(closes)
        ),
        applied_receipt_ids=(),
        split_factors_by_date={},
    )


def test_select_beta_window_requires_full_history() -> None:
    from datetime import date, timedelta

    dates = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(100))
    with pytest.raises(CollectorRejected) as caught:
        select_beta_window(dates, dates[-1])
    assert caught.value.reason is CollectorReason.BETA_INSUFFICIENT_OBSERVATIONS


def test_beta_out_of_bounds_fails_closed() -> None:
    from datetime import date, timedelta

    total = 300
    dates = tuple(date(2025, 1, 1) + timedelta(days=index) for index in range(total))
    reaction = dates[-1]

    def closes_from_returns(steps: tuple[Decimal, ...]) -> tuple[str, ...]:
        closes = [Decimal(100)]
        for step in steps:
            closes.append(closes[-1] * (Decimal(1) + step))
        return tuple(str(value) for value in closes)

    market_steps = tuple(
        Decimal("0.001") if index % 2 == 0 else Decimal("0.0005") for index in range(total - 1)
    )
    stock_steps = tuple(step * 5 for step in market_steps)
    sector_steps = tuple(
        Decimal("0.0004") if index % 3 == 0 else Decimal("0.0003") for index in range(total - 1)
    )
    market = _series("SPY", closes_from_returns(market_steps), dates)
    stock = _series("KR", closes_from_returns(stock_steps), dates)
    sector = _series("XLP", closes_from_returns(sector_steps), dates)
    with pytest.raises(CollectorRejected) as caught:
        estimate_betas(
            stock,
            market,
            sector,
            session_dates=dates,
            reaction_session_date=reaction,
        )
    assert caught.value.reason is CollectorReason.BETA_OUT_OF_BOUNDS


def test_receipt_round_trip_is_exact() -> None:
    fixture = load_fixture()
    compiled = _compile(fixture)
    for receipt in compiled.source_receipts:
        assert parse_source_receipt(source_receipt_bytes(receipt)) == receipt
    for receipt in compiled.action_receipts:
        assert parse_corporate_action_receipt(corporate_action_receipt_bytes(receipt)) == receipt


def test_receipt_parser_rejects_tampered_bytes() -> None:
    receipt = SourceReceipt.from_provenance(
        "source-test",
        SourceProvenance(
            source_class="OFFICIAL_EXCHANGE_CALENDAR",
            publisher="SYNTHETIC_OFFICIAL_CALENDAR",
            content_sha256="a" * 64,
            published_at=None,
            published_at_precision="DATE",
            retrieved_at=_at("2026-09-11T11:00:00Z"),
            entitlement="PUBLIC",
            redistribution_status="REDISTRIBUTABLE",
            limitations=(),
        ),
    )
    raw = source_receipt_bytes(receipt)
    tampered = raw.replace(b"PUBLIC", b"public")
    with pytest.raises(CollectorRejected):
        parse_source_receipt(tampered)


def test_collector_package_has_no_network_surface() -> None:
    forbidden_roots = {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "ssl"}
    forbidden_names = {
        "connect",
        "urlopen",
        "create_connection",
        "getaddrinfo",
        "MCPClient",
        "TradingClient",
    }
    for source_file in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden_roots, f"{source_file.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_roots, f"{source_file.name} imports {node.module}"
            elif isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_names, f"{source_file.name} uses {node.attr}"


def test_capture_runs_with_socket_disabled(compiled_snapshot) -> None:
    def deny_socket(*args, **kwargs):
        raise AssertionError("collector attempted network access")

    original = socket.socket
    socket.socket = deny_socket  # type: ignore[misc]
    try:
        rerun = _compile()
    finally:
        socket.socket = original  # type: ignore[misc]
    assert rerun.strategy_snapshot_bytes == compiled_snapshot.strategy_snapshot_bytes


def test_adapter_protocols_expose_only_read_methods() -> None:
    from ringdown_market.sourcedata.interfaces import EvidenceSource, MarketDataSource

    allowed = {
        "sessions",
        "security_master",
        "issuer_release",
        "sec_filing",
        "corporate_actions",
        "daily_bars",
        "window_trades",
        "window_quotes",
    }
    for protocol in (EvidenceSource, MarketDataSource):
        members = {
            name
            for name in dir(protocol)
            if not name.startswith("_") and callable(getattr(protocol, name, None))
        }
        assert members <= allowed, f"{protocol.__name__} exposes unexpected methods"


def test_capture_command_requires_host_authorization(tmp_path: Path, monkeypatch) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.delenv("ESSCHER_CAPTURE_AUTHORIZED", raising=False)
    exit_code = main(
        [
            "--event-id",
            EVENT_ID,
            "--capture-at",
            "2026-09-11T13:35:10Z",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2


def test_capture_command_rejects_unpinned_live_boundary(tmp_path: Path, monkeypatch) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    exit_code = main(
        [
            "--event-id",
            EVENT_ID,
            "--capture-at",
            "2026-09-11T13:35:10Z",
            "--output-dir",
            str(tmp_path),
            "--live",
        ]
    )
    assert exit_code == 2


def _amc_fixture():
    fixture = copy.deepcopy(load_fixture())
    fixture["candidate_manifest"]["records"][0]["cohort_id"] = "AMC"
    fixture["candidate_manifest"]["records"][0]["scheduled_at"] = "2026-09-10T21:00:00Z"
    fixture["issuer_release"]["provenance"]["published_at"] = "2026-09-10T21:00:00Z"
    return fixture


def test_amc_cohort_uses_distinct_bucket_and_next_session(compiled_snapshot) -> None:
    compiled = _compile(_amc_fixture())
    assert compiled.snapshot.timing_bucket is TimingBucket.AFTER_CLOSE
    assert compiled.snapshot.reaction_session_id == "XNYS-2026-09-11"
    assert compiled.snapshot.event_published_at == _at("2026-09-10T21:00:00Z")
    assert compiled.strategy_snapshot_bytes != compiled_snapshot.strategy_snapshot_bytes
    joined = compiled_strategy_input(compiled)
    assert joined.snapshot.timing_bucket is TimingBucket.AFTER_CLOSE


def test_amc_release_before_prior_session_close_fails_closed() -> None:
    fixture = _amc_fixture()
    fixture["issuer_release"]["provenance"]["published_at"] = "2026-09-10T18:00:00Z"
    _rejects(fixture, CollectorReason.PRIMARY_RELEASE_LATE)


def test_bmo_and_amc_reaction_session_selection_is_distinct() -> None:
    from datetime import date

    from ringdown_market.sourcedata.compiler import _reaction_session, derive_clocks
    from ringdown_market.strategy.policy import load_strategy_policy

    fixture = load_fixture()
    evidence = FixtureEvidenceSource(fixture)
    policy = load_strategy_policy()
    bmo_session = _reaction_session(
        evidence,
        cohort_id="BMO",
        scheduled_at=_at("2026-09-11T11:00:00Z"),
        exchange_mic="XNYS",
    )
    amc_session = _reaction_session(
        evidence,
        cohort_id="AMC",
        scheduled_at=_at("2026-09-10T21:00:00Z"),
        exchange_mic="XNYS",
    )
    assert bmo_session.session_date == date(2026, 9, 11)
    assert amc_session.session_date == date(2026, 9, 11)
    bmo_clocks = derive_clocks(policy, cohort_id="BMO", reaction_session=bmo_session)
    amc_clocks = derive_clocks(policy, cohort_id="AMC", reaction_session=amc_session)
    assert bmo_clocks.evidence_cutoff_at == amc_clocks.evidence_cutoff_at
    assert (
        bmo_clocks.observation_window_start_at
        == amc_clocks.observation_window_start_at
        == _at("2026-09-11T13:30:00Z")
    )


def test_partial_retrieval_fails_closed() -> None:
    fixture = load_fixture()
    configuration = CaptureConfiguration(
        candidate_manifest_bytes=build_candidate_manifest(fixture),
        event_id=EVENT_ID,
        capture_at=_at("2026-09-11T13:35:10Z"),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
        retrieval_pages={"earnings-release": (1, 3)},
    )
    with pytest.raises(CollectorRejected) as caught:
        compile_strategy_snapshot(
            configuration, FixtureEvidenceSource(fixture), FixtureMarketDataSource(fixture)
        )
    assert caught.value.reason is CollectorReason.PAGINATION_INCOMPLETE


def test_invalid_pagination_configuration_fails_closed() -> None:
    fixture = load_fixture()
    with pytest.raises(CollectorRejected) as caught:
        CaptureConfiguration(
            candidate_manifest_bytes=build_candidate_manifest(fixture),
            event_id=EVENT_ID,
            capture_at=_at("2026-09-11T13:35:10Z"),
            market_publisher=str(fixture["market_publisher"]),
            market_entitlement=str(fixture["market_entitlement"]),
            market_redistribution=str(fixture["market_redistribution"]),
            retrieval_pages={"calendar": (0, 1)},
        )
    assert caught.value.reason is CollectorReason.UNSUPPORTED_INPUT


def test_duplicate_source_record_fails_closed() -> None:
    from ringdown_market.sourcedata.evidence import EvidenceEntry, build_evidence_packet
    from ringdown_market.strategy.models import EvidenceRole

    def entry(evidence_id: str) -> EvidenceEntry:
        return EvidenceEntry(
            evidence_id=evidence_id,
            role=EvidenceRole.LIQUIDITY_VOLATILITY,
            receipt=SourceReceipt.from_provenance(
                f"source-{evidence_id}",
                SourceProvenance(
                    source_class="OFFICIAL_EXCHANGE_CALENDAR",
                    publisher="SYNTHETIC_OFFICIAL_CALENDAR",
                    content_sha256="d" * 64,
                    published_at=None,
                    published_at_precision="DATE",
                    retrieved_at=_at("2026-09-11T11:00:00Z"),
                    entitlement="ENTITLED",
                    redistribution_status="REDISTRIBUTABLE",
                    limitations=(),
                ),
            ),
        )

    with pytest.raises(CollectorRejected) as caught:
        build_evidence_packet(
            [entry("calendar-a"), entry("calendar-b")],
            evidence_cutoff_at=_at("2026-09-11T13:35:15Z"),
            permitted_source_classes=("OFFICIAL_EXCHANGE_CALENDAR",),
            required_source_classes=("OFFICIAL_EXCHANGE_CALENDAR",),
        )
    assert caught.value.reason is CollectorReason.DUPLICATE_SOURCE_RECORD


def test_capture_command_writes_canonical_artifacts(tmp_path: Path, monkeypatch) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    exit_code = main(
        [
            "--event-id",
            EVENT_ID,
            "--capture-at",
            "2026-09-11T13:35:10Z",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 0
    names = {item.name for item in tmp_path.iterdir()}
    assert {
        "strategy_snapshot.json",
        "feature_receipt.json",
        "candidate_manifest.json",
        "source_receipts.jsonl",
        "corporate_action_receipts.jsonl",
        "capture_identity.json",
    } <= names
    snapshot_bytes = (tmp_path / "strategy_snapshot.json").read_bytes()
    assert snapshot_bytes == compiled_strategy_input_bytes(snapshot_bytes, tmp_path)


def compiled_strategy_input_bytes(snapshot_bytes: bytes, tmp_path: Path) -> bytes:
    from ringdown_market.strategy.contracts import parse_strategy_snapshot, strategy_snapshot_bytes

    return strategy_snapshot_bytes(parse_strategy_snapshot(snapshot_bytes))


MACRO_EVENT_ID = "BLS-JOLTS-2026-07"


def _macro_fixture():
    from ringdown_market.sourcedata.fakes import load_macro_fixture

    return load_macro_fixture()


def _macro_configuration(fixture, capture_at: str = "2026-09-09T14:15:10Z"):
    from ringdown_market.sourcedata.fakes import build_macro_candidate_manifest

    return CaptureConfiguration(
        candidate_manifest_bytes=build_macro_candidate_manifest(fixture),
        event_id=MACRO_EVENT_ID,
        capture_at=_at(capture_at),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )


def _compile_macro(fixture=None, capture_at: str = "2026-09-09T14:15:10Z"):
    from ringdown_market.sourcedata.compiler import compile_macro_snapshot
    from ringdown_market.sourcedata.fakes import (
        FixtureMacroEvidenceSource,
        FixtureMacroMarketDataSource,
        FixtureMacroReleaseSource,
    )

    fixture = fixture if fixture is not None else _macro_fixture()
    evidence = FixtureMacroEvidenceSource(fixture)
    macro = FixtureMacroReleaseSource(fixture)
    market = FixtureMacroMarketDataSource(fixture)
    return compile_macro_snapshot(
        _macro_configuration(fixture, capture_at), evidence.sessions, macro, market
    )


def _rejects_macro(fixture, reason: CollectorReason, capture_at: str = "2026-09-09T14:15:10Z"):
    from ringdown_market.sourcedata.compiler import compile_macro_snapshot
    from ringdown_market.sourcedata.fakes import (
        FixtureMacroEvidenceSource,
        FixtureMacroMarketDataSource,
        FixtureMacroReleaseSource,
    )

    evidence = FixtureMacroEvidenceSource(fixture)
    macro = FixtureMacroReleaseSource(fixture)
    market = FixtureMacroMarketDataSource(fixture)
    with pytest.raises(CollectorRejected) as caught:
        compile_macro_snapshot(
            _macro_configuration(fixture, capture_at), evidence.sessions, macro, market
        )
    assert caught.value.reason is reason
    return caught.value


@pytest.fixture(scope="module")
def compiled_macro_snapshot():
    return _compile_macro()


def test_macro_identical_inputs_produce_byte_identical_snapshots(compiled_macro_snapshot) -> None:
    rerun = _compile_macro()
    assert rerun.strategy_snapshot_bytes == compiled_macro_snapshot.strategy_snapshot_bytes
    assert rerun.feature_receipt_bytes == compiled_macro_snapshot.feature_receipt_bytes
    assert (
        rerun.evidence_packet.packet_sha256 == compiled_macro_snapshot.evidence_packet.packet_sha256
    )


def test_macro_snapshot_passes_the_frozen_strategy_contract(compiled_macro_snapshot) -> None:
    from ringdown_market.strategy.models import EventCategory, TimingBucket

    joined = compiled_strategy_input(compiled_macro_snapshot)
    assert joined.snapshot.event_id == MACRO_EVENT_ID
    assert joined.snapshot.event_category is EventCategory.SCHEDULED_MACRO_RELEASE
    assert joined.snapshot.timing_bucket is TimingBucket.SCHEDULED_RELEASE
    assert joined.snapshot.release_family is ReleaseFamily.BLS_JOLTS
    assert joined.feature_receipt.feature_snapshot_at <= joined.snapshot.decision_cutoff_at


def test_macro_snapshot_carries_all_policy_features(compiled_macro_snapshot) -> None:
    features = compiled_macro_snapshot.feature_receipt.features
    assert len(features) == 20
    by_id = {feature.feature_id: feature for feature in features}
    assert by_id["macro.jolts.job_openings.v1"].status is FeatureStatus.PRESENT
    assert by_id["macro.employment.nonfarm_payrolls.v1"].status is FeatureStatus.NOT_APPLICABLE
    assert by_id["macro.consensus_surprise_vector.v1"].status is FeatureStatus.UNAVAILABLE
    assert by_id["macro.revision_vector.v1"].status is FeatureStatus.PRESENT
    revision = by_id["macro.revision_vector.v1"]
    assert len(revision.components) == 1
    assert revision.components[0].component_id == "2026-06.job_openings"


def test_macro_base_fields_use_first_vintage_not_revised_values(compiled_macro_snapshot) -> None:
    features = compiled_macro_snapshot.feature_receipt.features
    by_id = {feature.feature_id: feature for feature in features}
    assert by_id["macro.jolts.job_openings.v1"].value == Decimal("7200000")


def test_macro_release_missing_fails_closed() -> None:
    fixture = copy.deepcopy(_macro_fixture())
    fixture["release"]["reference_period"] = "2099-01"
    _rejects_macro(fixture, CollectorReason.OFFICIAL_RELEASE_MISSING)


def test_macro_release_late_fails_closed() -> None:
    fixture = copy.deepcopy(_macro_fixture())
    fixture["release"]["published_at"] = "2026-09-09T14:15:16Z"
    _rejects_macro(fixture, CollectorReason.OFFICIAL_RELEASE_LATE)


def test_macro_schedule_not_frozen_fails_closed() -> None:
    fixture = copy.deepcopy(_macro_fixture())
    fixture["candidate_manifest"]["records"][0]["scheduled_at"] = "2026-09-09T15:00:00Z"
    _rejects_macro(fixture, CollectorReason.SCHEDULE_NOT_FROZEN)


def test_macro_revision_conflict_fails_closed() -> None:
    fixture = copy.deepcopy(_macro_fixture())
    conflicting = copy.deepcopy(fixture["revisions"][0])
    conflicting["revised_value"] = "9999999"
    fixture["revisions"].append(conflicting)
    _rejects_macro(fixture, CollectorReason.REVISION_FIELD_CONFLICTING)


def test_macro_insufficient_normalization_history_fails_closed() -> None:
    fixture = copy.deepcopy(_macro_fixture())
    prior = fixture["spy_prior_window_trades"]
    keep = sorted(prior.keys())[-40:]
    fixture["spy_prior_window_trades"] = {key: prior[key] for key in keep}
    _rejects_macro(fixture, CollectorReason.INSUFFICIENT_NORMALIZATION_HISTORY)


def test_macro_capture_command_writes_canonical_artifacts(tmp_path: Path, monkeypatch) -> None:
    from ringdown_market.sourcedata.capture import main

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    exit_code = main(
        [
            "--event-id",
            MACRO_EVENT_ID,
            "--capture-at",
            "2026-09-09T14:15:10Z",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 0
    names = {item.name for item in tmp_path.iterdir()}
    assert {"strategy_snapshot.json", "feature_receipt.json", "candidate_manifest.json"} <= names
    snapshot_bytes = (tmp_path / "strategy_snapshot.json").read_bytes()
    assert snapshot_bytes == compiled_strategy_input_bytes(snapshot_bytes, tmp_path)
