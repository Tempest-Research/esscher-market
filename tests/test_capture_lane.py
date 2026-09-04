"""Hermetic tests for the live capture lane (#68 prerequisite tooling).

No network: every test drives ``scripts/capture_lane.py`` /
``scripts/capture_replay.py`` through canned payloads and the real frozen
contracts (candidate-manifest parser, record dataclasses).  The lane's
fail-closed refusals are the assertions that matter: ambiguity, missing
publication time, and boundary-price collisions must reject, never guess.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import capture_lane as cl  # noqa: E402
import capture_replay as cr  # noqa: E402

from ringdown_market.strategy.contracts import parse_candidate_manifest  # noqa: E402

# --- dateline parsing --------------------------------------------------------


def test_dateline_with_time_yields_second_precision() -> None:
    text = (
        "ACME Corp Reports Q2 Results COLUMBUS, Ohio, September 8, 2026 06:30 AM ET -- Acme Corp..."
    )
    parsed = cl._parse_dateline(text)
    assert parsed is not None
    day, published = parsed
    assert day == date(2026, 9, 8)
    assert published is not None
    assert published.hour == 10 and published.tzinfo == UTC  # 06:30 ET -> 10:30 UTC
    assert published.minute == 30


def test_dateline_dash_separated_without_time_yields_date_only() -> None:
    text = (
        "NEWS RELEASE FOR IMMEDIATE RELEASE Medtronic reports first quarter fiscal 2027 "
        "results GALWAY, Ireland - September 1, 2026 - Medtronic plc (NYSE: MDT) announced..."
    )
    parsed = cl._parse_dateline(text)
    assert parsed is not None
    day, published = parsed
    assert day == date(2026, 9, 1)
    assert published is None


def test_dateline_abbreviated_month() -> None:
    text = "DUBLIN, Sept. 9, 2026 /PRNewswire/ -- Company reported results."
    parsed = cl._parse_dateline(text)
    assert parsed is not None
    assert parsed[0] == date(2026, 9, 9)


# --- monetary value parsing --------------------------------------------------


def test_value_parser_scales_and_skips_guidance_ranges() -> None:
    text = (
        "Key Highlights Revenue of $9.8 billion, increased 13.7%. "
        "Raising FY27 revenue guidance to $10.1 billion to $10.3 billion. "
        "GAAP diluted EPS of $1.14; guidance EPS of $5.94 to $6.00."
    )
    revenue = cl._find_quarter_value(text, cl.REVENUE_LABELS)
    assert revenue == "9800000000"
    eps = cl._find_quarter_value(text, cl.EPS_LABELS)
    assert eps == "1.14"


def test_value_parser_prefers_earliest_text_position_across_labels() -> None:
    text = (
        "Highlights: Revenue of $9.8 billion. Later prose mentions the extra fiscal "
        "week impact on net sales 27 results were impacted by timing."
    )
    assert cl._find_quarter_value(text, cl.REVENUE_LABELS) == "9800000000"


def test_value_parser_handles_commas_millions_and_parentheses() -> None:
    text = "Total revenue $1,234.5 million for the quarter. Prior loss ($(12.3) million)."
    assert cl._find_quarter_value(text, ("total revenue",)) == "1234500000"


# --- guidance extraction -----------------------------------------------------


def test_guidance_extracts_current_and_prior_from_same_release() -> None:
    text = (
        "Guidance The company today raised its FY27 organic revenue growth and EPS "
        "guidance. The company also raised its FY27 diluted non-GAAP EPS guidance to "
        "the new range of $5.94 to $6.00 versus the prior $5.90 to $6.00."
    )
    result = cl._extract_guidance(text)
    assert result["detected"] is True
    current = result["current"]
    prior = result["prior"]
    assert current is not None and prior is not None
    assert (current["eps_low"], current["eps_high"]) == ("5.94", "6.00")
    assert (prior["eps_low"], prior["eps_high"]) == ("5.90", "6.00")
    assert current["fiscal_period"] == "FY2027"
    assert current["revenue_low"] is None  # percentage guidance is not an absolute range


def test_guidance_ambiguity_fails_closed() -> None:
    text = (
        "Guidance: EPS in the range of $1.10 to $1.20. Separately the company sees "
        "EPS to $2.10 to $2.20 for the full year."
    )
    with pytest.raises(cl.CaptureLaneRejected) as caught:
        cl._extract_guidance(text)
    assert caught.value.reason == "GUIDANCE_EXTRACTION_AMBIGUOUS"


def test_guidance_keyword_without_parseable_range_fails_closed() -> None:
    text = "The company provided guidance for the coming year without specific figures."
    with pytest.raises(cl.CaptureLaneRejected) as caught:
        cl._extract_guidance(text)
    assert caught.value.reason == "GUIDANCE_EXTRACTION_UNRESOLVED"


def test_guidance_withdrawal_is_detected() -> None:
    text = "Outlook: the company announced it is withdrawing its prior guidance."
    result = cl._extract_guidance(text)
    assert result["detected"] is True
    assert result["current"] == {"withdrawn": True, "fiscal_period": None}


def test_guidance_absent_is_not_given() -> None:
    result = cl._extract_guidance(
        "The company reported quarterly results with no forward statements."
    )
    assert result == {"detected": False, "current": None, "prior": None}


# --- cohort classification ---------------------------------------------------


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_cohort_bmo_requires_same_day_pre_open_acceptance() -> None:
    session = date(2026, 9, 8)
    assert cl._classify_cohort(_dt("2026-09-08T10:47:19Z"), session, date(2026, 9, 4)) == "BMO"
    # accepted after the open is not a BMO publication proof
    assert cl._classify_cohort(_dt("2026-09-08T14:47:19Z"), session, date(2026, 9, 4)) is None


def test_cohort_amc_requires_prior_close_to_open_window() -> None:
    session = date(2026, 9, 8)  # Tuesday; prior session Friday (Monday holiday)
    assert cl._classify_cohort(_dt("2026-09-05T20:05:00Z"), session, date(2026, 9, 4)) == "AMC"
    # a Wednesday-evening filing reacts Thursday, never Tuesday
    assert cl._classify_cohort(_dt("2026-09-03T20:05:00Z"), session, date(2026, 9, 4)) is None


# --- same-microsecond consolidation ------------------------------------------


def _trade(ts: str, price: str, size: int, condition: str = "REGULAR_CONTINUOUS") -> dict:
    return {
        "observed_at": ts,
        "price": price,
        "sale_condition": condition,
        "session_id": "XNYS-2026-09-08",
        "size": size,
        "symbol": "MDT",
    }


def test_consolidation_preserves_volume_and_vwap() -> None:
    records = [
        _trade("2026-09-08T13:30:01.400000Z", "9.90", 50),
        _trade("2026-09-08T13:30:01.500000Z", "10.00", 100),
        _trade("2026-09-08T13:30:01.500000Z", "10.10", 300),
        _trade("2026-09-08T13:30:02.000000Z", "10.20", 200),
    ]
    merged, stats = cl._consolidate_same_microsecond(records)
    assert stats["merged_groups"] == 1 and stats["merged_prints"] == 2
    # the merge is interior (not first/last eligible) -> no boundary ambiguity
    assert stats["boundary_ambiguous"] is False
    assert len(merged) == 3
    interior = merged[1]
    assert interior["size"] == 400
    # vwap preserved: (10.00*100 + 10.10*300)/400 = 10.075
    assert Decimal(interior["price"]) == Decimal("10.075")


def test_consolidation_flags_boundary_price_ambiguity() -> None:
    records = [
        _trade("2026-09-08T13:30:01.500000Z", "10.00", 100),
        _trade("2026-09-08T13:30:01.500000Z", "10.10", 300),
    ]
    _merged, stats = cl._consolidate_same_microsecond(records)
    assert stats["boundary_ambiguous"] is True


def test_consolidation_ignores_auction_prints() -> None:
    records = [
        _trade("2026-09-08T13:30:00.000000Z", "10.00", 5000, "OPENING_AUCTION"),
        _trade("2026-09-08T13:30:01.500000Z", "10.00", 100),
    ]
    merged, stats = cl._consolidate_same_microsecond(records)
    assert len(merged) == 2
    assert stats["merged_groups"] == 0


# --- record validity filters -------------------------------------------------


def test_invalid_quotes_and_trades_are_filtered() -> None:
    assert (
        cl._quote_record({"bp": "0", "ap": "10.0", "t": "2026-09-08T13:34:59Z"}, "X", "S") is None
    )
    assert (
        cl._quote_record({"bp": "10.1", "ap": "10.0", "t": "2026-09-08T13:34:59Z"}, "X", "S")
        is None
    )
    good = cl._quote_record(
        {"bp": "10.0", "ap": "10.1", "t": "2026-09-08T13:34:59.123456789Z"}, "X", "S"
    )
    assert good is not None and good["observed_at"] == "2026-09-08T13:34:59.123456Z"
    assert (
        cl._trade_record({"p": "10.0", "s": 0, "t": "2026-09-08T13:30:01Z", "c": []}, "X", "S")
        is None
    )
    opening = cl._trade_record(
        {"p": "10.0", "s": 100, "t": "2026-09-08T13:30:00Z", "c": ["O"]}, "X", "S"
    )
    assert opening is not None and opening["sale_condition"] == "OPENING_AUCTION"


# --- SIC mapping --------------------------------------------------------------


def test_sector_mapping_only_uses_committed_ranges() -> None:
    assert cl._sector_for_sic("3845") == "HEALTH_CARE"
    assert cl._sector_for_sic(6021) == "FINANCIALS"
    assert cl._sector_for_sic("6531") == "REAL_ESTATE"  # narrower override wins
    assert cl._sector_for_sic("0100") is None  # ambiguous agriculture fails closed
    assert cl._sector_for_sic("9999") is None
    assert cl._sector_for_sic(None) is None


# --- freeze manifest against the real contract --------------------------------


def test_freeze_manifest_passes_the_frozen_candidate_contract(tmp_path: Path) -> None:
    discovery = {
        "schema": "esscher.capture_discovery",
        "schema_version": 1,
        "session_date": "2026-09-08",
        "events": [
            {
                "ticker": "MDT",
                "issuer": "Medtronic plc",
                "cik": "0001613103",
                "accession": "0001628280-26-059697",
                "cohort": "BMO",
                "exchange": "NYSE",
            },
            {
                "ticker": "ZZZ",
                "issuer": "Excluded Co",
                "cik": "0000000001",
                "accession": "0000000000-26-000001",
                "cohort": None,
                "exchange": "NASDAQ",
            },
        ],
    }
    screening = {
        "schema": "esscher.capture_screening",
        "schema_version": 1,
        "session_date": "2026-09-08",
        "results": [
            {"ticker": "MDT", "reasons": [], "eligible": True, "issuer": "Medtronic plc"},
            {
                "ticker": "ZZZ",
                "reasons": ["PRIMARY_EXCHANGE_NOT_XNYS", "TIMING_BUCKET_UNKNOWN"],
                "eligible": False,
                "issuer": "Excluded Co",
            },
        ],
    }
    discovery_path = tmp_path / "discovery.json"
    screening_path = tmp_path / "screening.json"
    out_path = tmp_path / "manifest.json"
    discovery_path.write_text(json.dumps(discovery))
    screening_path.write_text(json.dumps(screening))

    class Args:
        pass

    args = Args()
    args.discovery = str(discovery_path)
    args.screening = str(screening_path)
    args.frozen_at = "2026-09-04T20:15:00Z"
    args.out = str(out_path)
    assert cl.cmd_freeze_manifest(args) == 0

    raw = out_path.read_bytes().rstrip(b"\n")
    manifest = parse_candidate_manifest(raw)  # the real frozen contract validates
    assert manifest.candidate_id == "EARNINGS_RESIDUAL_CONTINUATION_V1"
    mdt = manifest.record("MDT-20260908-EARNINGS")
    assert mdt.eligibility.value == "ELIGIBLE" and mdt.cohort_id == "BMO"
    assert mdt.scheduled_at == datetime(2026, 9, 8, 13, 0, tzinfo=UTC)  # 09:00 ET > frozen_at
    zzz = manifest.record("ZZZ-20260908-EARNINGS")
    assert zzz.eligibility.value == "INELIGIBLE"
    assert zzz.reason_codes == ("PRIMARY_EXCHANGE_NOT_XNYS", "TIMING_BUCKET_UNKNOWN")


# --- replay adapters ------------------------------------------------------------


def _blob() -> dict:
    provenance = {
        "content_sha256": "a" * 64,
        "entitlement": "PUBLIC",
        "limitations": [],
        "published_at": "2026-09-08T04:00:00Z",
        "published_at_precision": "DATE",
        "publisher": "ISSUER_INVESTOR_RELATIONS_VIA_SEC_EDGAR",
        "redistribution_status": "REDISTRIBUTABLE",
        "retrieved_at": "2026-09-08T13:25:00Z",
        "source_class": "ISSUER_INVESTOR_RELATIONS",
    }
    calendar_provenance = {
        **provenance,
        "published_at": None,
        "source_class": "OFFICIAL_EXCHANGE_CALENDAR",
    }
    master_provenance = {
        **provenance,
        "source_class": "POINT_IN_TIME_SECURITY_MASTER",
        "entitlement": "ENTITLED",
    }
    return {
        "event_id": "MDT-20260908-EARNINGS",
        "sessions": [
            {
                "close_at": "2026-09-08T20:00:00Z",
                "exchange_mic": "XNYS",
                "full_regular": True,
                "open_at": "2026-09-08T13:30:00Z",
                "provenance": calendar_provenance,
                "session_date": "2026-09-08",
                "session_id": "XNYS-2026-09-08",
            }
        ],
        "security_master": {
            "active_at_freeze": True,
            "asof": "2026-09-04T20:15:00Z",
            "issuer": "Medtronic plc",
            "listed_option_exists": True,
            "primary_exchange_mic": "XNYS",
            "prior_regular_close": "90.65",
            "provenance": master_provenance,
            "sector": "HEALTH_CARE",
            "security_id": "CIK-0001613103",
            "security_type": "US_COMMON_STOCK",
            "ticker": "MDT",
        },
        "issuer_release": {
            "current_guidance": None,
            "current_quarter": {
                "eps_diluted": "1.14",
                "fiscal_period": "20260908-REPORT",
                "revenue": "9800000000",
            },
            "event_id": "MDT-20260908-EARNINGS",
            "prior_guidance": None,
            "provenance": provenance,
            "quarter_history": [
                {"eps_diluted": "1.00", "fiscal_period": f"FY-H{i}", "revenue": "9000000000"}
                for i in range(11)
            ],
            "report_fiscal_period": "20260908-REPORT",
            "ticker": "MDT",
        },
        "corporate_actions": [],
        "daily_bars": {
            "MDT": [
                {
                    "close": "90.65",
                    "session_date": "2026-09-04",
                    "session_id": "XNYS-2026-09-04",
                    "symbol": "MDT",
                    "valid": True,
                    "volume": 5000000,
                }
            ]
        },
        "reaction_trades": {
            "MDT": [
                {
                    "observed_at": "2026-09-08T13:30:01.000000Z",
                    "price": "91.00",
                    "sale_condition": "REGULAR_CONTINUOUS",
                    "session_id": "XNYS-2026-09-08",
                    "size": 100,
                    "symbol": "MDT",
                }
            ]
        },
        "reaction_quotes": {
            "MDT": [
                {
                    "ask": "91.02",
                    "bid": "91.00",
                    "observed_at": "2026-09-08T13:34:59.900000Z",
                    "session_id": "XNYS-2026-09-08",
                    "symbol": "MDT",
                }
            ]
        },
        "prior_window_trades": {
            "XNYS-2026-09-04": [
                {
                    "observed_at": "2026-09-04T13:30:30.000000Z",
                    "price": "90.00",
                    "sale_condition": "REGULAR_CONTINUOUS",
                    "session_id": "XNYS-2026-09-04",
                    "size": 15000,
                    "symbol": "MDT",
                }
            ]
        },
    }


def test_replay_evidence_source_serves_protocol_records() -> None:
    source = cr.LiveReplayEvidenceSource(_blob())
    sessions = source.sessions("XNYS", date(2026, 9, 1), date(2026, 9, 30))
    assert len(sessions) == 1 and sessions[0].full_regular is True
    master = source.security_master("MDT", datetime(2026, 9, 8, tzinfo=UTC))
    assert master.prior_regular_close == Decimal("90.65")
    with pytest.raises(ValueError):
        source.security_master("SPY", datetime(2026, 9, 8, tzinfo=UTC))
    release = source.issuer_release("MDT-20260908-EARNINGS")
    assert release is not None and len(release.quarter_history) == 11
    assert release.current_quarter.eps_diluted == Decimal("1.14")
    assert source.issuer_release("OTHER") is None
    assert source.sec_filing("MDT-20260908-EARNINGS") is None
    assert source.corporate_actions("MDT", date(2025, 1, 1), date(2026, 9, 8)) == ()


def test_replay_market_source_serves_protocol_records() -> None:
    source = cr.LiveReplayMarketDataSource(_blob())
    bars = source.daily_bars("MDT", date(2026, 1, 1), date(2026, 9, 30))
    assert len(bars) == 1 and bars[0].close == Decimal("90.65")
    reaction = source.window_trades("MDT", "XNYS-2026-09-08")
    assert len(reaction) == 1 and reaction[0].sale_condition == "REGULAR_CONTINUOUS"
    prior = source.window_trades("MDT", "XNYS-2026-09-04")
    assert len(prior) == 1 and prior[0].size == 15000
    quotes = source.window_quotes("MDT", "XNYS-2026-09-08")
    assert len(quotes) == 1 and quotes[0].ask >= quotes[0].bid
    assert source.daily_bars("SPY", date(2026, 1, 1), date(2026, 9, 30)) == ()


def test_capture_doors_build_configuration_from_feed_event_bytes() -> None:
    from ringdown_market.strategy.contracts import canonical_json_bytes
    from ringdown_market.strategy.policy import strategy_policy_sha256

    blob = _blob()
    manifest = {
        "candidate_id": cl.CANDIDATE_ID,
        "frozen_at": "2026-09-04T20:15:00Z",
        "manifest_id": "live-earnings-candidates-2026-09-08",
        "policy_sha256": strategy_policy_sha256(),
        "producer_build_sha256": cl.PRODUCER_BUILD_SHA256,
        "records": [
            {
                "cohort_id": "BMO",
                "eligibility": "ELIGIBLE",
                "event_id": "MDT-20260908-EARNINGS",
                "issuer": "Medtronic plc",
                "reason_codes": [],
                "scheduled_at": "2026-09-08T13:00:00Z",
                "security_id": "CIK-0001613103",
                "ticker": "MDT",
            }
        ],
        "schema": "esscher.candidate_manifest",
        "schema_version": 1,
        "selection_rule_id": cl.SELECTION_RULE_ID,
    }
    blob["candidate_manifest"] = manifest
    blob["capture_at"] = "2026-09-08T13:35:10Z"
    blob["market_publisher"] = "ALPACA_SIP_EQUITY_FEED"
    blob["market_entitlement"] = "ENTITLED"
    blob["market_redistribution"] = "NON_REDISTRIBUTABLE"
    blob["retrieval_pages"] = {"market-quotes": [3, 3], "market-trades": [2, 2]}

    class Event:
        evidence_manifest_bytes = canonical_json_bytes(blob)
        market_window_bytes = canonical_json_bytes({"daily_bars": {}})

    # overlapping keys must be refused by the door
    with pytest.raises(ValueError, match="overlap"):
        cr.LiveCaptureDoors().sources_for(Event())

    market_only = {
        "daily_bars": {},
        "prior_window_trades": {},
        "reaction_quotes": {},
        "reaction_trades": {},
    }
    evidence_blob = {k: v for k, v in blob.items() if k not in market_only}

    class GoodEvent:
        evidence_manifest_bytes = canonical_json_bytes(evidence_blob)
        market_window_bytes = canonical_json_bytes(market_only)

    capture, evidence_source, market_source = cr.LiveCaptureDoors().sources_for(GoodEvent())
    assert capture.event_id == "MDT-20260908-EARNINGS"
    assert capture.capture_at == datetime(2026, 9, 8, 13, 35, 10, tzinfo=UTC)
    assert capture.market_entitlement == "ENTITLED"
    assert capture.retrieval_pages["market-quotes"] == (3, 3)
    assert isinstance(evidence_source, cr.LiveReplayEvidenceSource)
    assert isinstance(market_source, cr.LiveReplayMarketDataSource)
