from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from ringdown_market.alpha.models import Direction
from ringdown_market.execution.option_compiler import (
    DTE_MAX_DAYS,
    DTE_MIN_DAYS,
    FROZEN_WIDTHS,
    OPTION_CHAIN_SNAPSHOT_SCHEMA,
    OptionCompilerRejected,
    PackageRejectionReason,
    compile_option_package,
    compile_option_package_from_decision,
    parse_option_chain_snapshot,
)

AS_OF = datetime(2026, 9, 11, 13, 36, 0, tzinfo=UTC)
ENTRY = date(2026, 9, 11)
NEAR_EXPIRY = date(2026, 9, 18)
FAR_EXPIRY = date(2026, 9, 25)


def occ(underlying: str, expiry: date, option_type: str, strike: Decimal) -> str:
    type_code = "C" if option_type == "CALL" else "P"
    return f"{underlying}{expiry:%y%m%d}{type_code}{int(strike * 1000):08d}"


def contract(
    underlying: str,
    expiry: date,
    option_type: str,
    strike: Decimal,
    *,
    bid: str = "0.50",
    ask: str = "0.60",
    bid_size: int = 5,
    ask_size: int = 5,
    quoted_at: datetime | None = None,
    symbol_override: str | None = None,
    strike_override: str | None = None,
    expiry_override: str | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol_override or occ(underlying, expiry, option_type, strike),
        "option_type": option_type,
        "strike": strike_override if strike_override is not None else str(strike),
        "expiry": expiry_override or expiry.isoformat(),
        "quote": {
            "bid": bid,
            "ask": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "quoted_at": (quoted_at or AS_OF).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


def chain_bytes(contracts: list[dict[str, Any]], *, underlying: str = "ACME") -> bytes:
    payload = {
        "schema": OPTION_CHAIN_SNAPSHOT_SCHEMA,
        "schema_version": 1,
        "underlying": underlying,
        "as_of": AS_OF.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_id": "synthetic-option-source",
        "contracts": contracts,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def bull_chain() -> list[dict[str, Any]]:
    return [
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), bid="1.10", ask="1.30"),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("105.00"), bid="0.20", ask="0.30"),
    ]


def parse(bull_chain_bytes: bytes):
    return parse_option_chain_snapshot(bull_chain_bytes)


def test_up_compiles_bull_call_with_frozen_geometry() -> None:
    chain = parse(chain_bytes(bull_chain()))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package is False
    package = result.package
    assert package is not None
    payload = package.strategy_payload
    assert payload["kind"] == "DEBIT_VERTICAL"
    assert payload["vertical_type"] == "BULL_CALL"
    assert payload["quantity"] == 1
    assert payload["underlying"] == "ACME"
    assert payload["expiry"] == NEAR_EXPIRY.isoformat()
    assert payload["long_leg"]["symbol"] == occ("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"))
    assert payload["short_leg"]["symbol"] == occ("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"))
    assert package.debit == Decimal("0.80")
    assert package.width == Decimal("2.50")
    assert isinstance(package.debit, Decimal)


def test_down_compiles_bear_put_buying_higher_strike() -> None:
    contracts = [
        contract("ACME", NEAR_EXPIRY, "PUT", Decimal("102.50"), bid="0.80", ask="0.90"),
        contract("ACME", NEAR_EXPIRY, "PUT", Decimal("100.00"), bid="0.40", ask="0.50"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.DOWN, chain=chain, entry_date=ENTRY)
    package = result.package
    assert package is not None
    assert package.strategy_payload["vertical_type"] == "BEAR_PUT"
    assert package.strategy_payload["long_leg"]["strike"] == "102.50"
    assert package.strategy_payload["short_leg"]["strike"] == "100.00"
    assert package.debit == Decimal("0.50")


def test_uncertain_direction_yields_no_package() -> None:
    chain = parse(chain_bytes(bull_chain()))
    result = compile_option_package(direction=Direction.UNCERTAIN, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert result.reasons == (PackageRejectionReason.UNCERTAIN_DECISION.value,)


@pytest.mark.parametrize("dte", [DTE_MIN_DAYS, DTE_MAX_DAYS])
def test_dte_boundaries_included(dte: int) -> None:
    expiry = ENTRY + timedelta(days=dte)
    contracts = [
        contract("ACME", expiry, "CALL", Decimal("100.00"), bid="1.10", ask="1.30"),
        contract("ACME", expiry, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.package is not None
    assert result.package.expiry == expiry


@pytest.mark.parametrize("dte", [DTE_MIN_DAYS - 1, DTE_MAX_DAYS + 1])
def test_dte_outside_bounds_rejected(dte: int) -> None:
    expiry = ENTRY + timedelta(days=dte)
    contracts = [
        contract("ACME", expiry, "CALL", Decimal("100.00"), bid="1.10", ask="1.30"),
        contract("ACME", expiry, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.NO_ELIGIBLE_EXPIRY.value in result.reasons


def test_ranking_prefers_earlier_expiry_then_narrower_width_then_lower_strike() -> None:
    contracts = [
        *bull_chain(),
        contract("ACME", FAR_EXPIRY, "CALL", Decimal("97.50"), bid="1.40", ask="1.60"),
        contract("ACME", FAR_EXPIRY, "CALL", Decimal("100.00"), bid="1.10", ask="1.30"),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("97.50"), bid="1.40", ask="1.60"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    package = result.package
    assert package is not None
    assert package.expiry == NEAR_EXPIRY
    assert package.width == FROZEN_WIDTHS[0]
    assert package.strategy_payload["long_leg"]["strike"] == "97.50"


def test_stale_quote_fails_closed() -> None:
    contracts = [
        contract(
            "ACME",
            NEAR_EXPIRY,
            "CALL",
            Decimal("100.00"),
            bid="1.10",
            ask="1.30",
            quoted_at=AS_OF - timedelta(seconds=3),
        ),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.STALE_QUOTE.value in result.reasons


def test_future_quote_timestamp_fails_closed() -> None:
    contracts = [
        contract(
            "ACME",
            NEAR_EXPIRY,
            "CALL",
            Decimal("100.00"),
            bid="1.10",
            ask="1.30",
            quoted_at=AS_OF + timedelta(seconds=1),
        ),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.STALE_QUOTE.value in result.reasons


def test_quote_skew_and_staleness_reported_together() -> None:
    contracts = [
        contract(
            "ACME",
            NEAR_EXPIRY,
            "CALL",
            Decimal("100.00"),
            bid="1.10",
            ask="1.30",
            quoted_at=AS_OF - timedelta(seconds=2),
        ),
        contract(
            "ACME",
            NEAR_EXPIRY,
            "CALL",
            Decimal("102.50"),
            bid="0.50",
            ask="0.60",
            quoted_at=AS_OF + timedelta(seconds=1),
        ),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.QUOTE_SKEW_EXCEEDED.value in result.reasons
    assert PackageRejectionReason.STALE_QUOTE.value in result.reasons


def test_wide_spread_fails_closed() -> None:
    contracts = [
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), bid="1.00", ask="1.70"),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.SPREAD_EXCEEDED.value in result.reasons


def test_crossed_quote_fails_closed() -> None:
    contracts = [
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), bid="1.50", ask="1.20"),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.CROSSED_QUOTE.value in result.reasons


def test_zero_size_fails_closed() -> None:
    contracts = [
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), bid_size=0),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50")),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.ZERO_QUOTE_SIZE.value in result.reasons


def test_non_positive_debit_fails_closed() -> None:
    contracts = [
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), bid="0.30", ask="0.40"),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.50", ask="0.60"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.DEBIT_NOT_POSITIVE.value in result.reasons


def test_debit_exceeding_width_fails_closed() -> None:
    contracts = [
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), bid="2.80", ask="3.00"),
        contract("ACME", NEAR_EXPIRY, "CALL", Decimal("102.50"), bid="0.40", ask="0.50"),
    ]
    chain = parse(chain_bytes(contracts))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.is_no_package
    assert PackageRejectionReason.DEBIT_EXCEEDS_WIDTH.value in result.reasons


def test_debit_exceeding_risk_cap_fails_closed() -> None:
    chain = parse(chain_bytes(bull_chain()))
    result = compile_option_package(
        direction=Direction.UP,
        chain=chain,
        entry_date=ENTRY,
        risk_cap=Decimal("50.00"),
    )
    assert result.is_no_package
    assert PackageRejectionReason.DEBIT_EXCEEDS_RISK_CAP.value in result.reasons


def test_malformed_symbol_rejected_at_parse() -> None:
    contracts = [contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), symbol_override="BAD")]
    with pytest.raises(OptionCompilerRejected):
        parse(chain_bytes(contracts))


def test_symbol_contract_mismatch_rejected_at_parse() -> None:
    contracts = [contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"), strike_override="105.00")]
    with pytest.raises(OptionCompilerRejected):
        parse(chain_bytes(contracts))


def test_duplicate_contract_rejected_at_parse() -> None:
    one = contract("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"))
    with pytest.raises(OptionCompilerRejected):
        parse(chain_bytes([one, dict(one)]))


def test_unknown_field_rejected_at_parse() -> None:
    payload = json.loads(chain_bytes(bull_chain()))
    payload["tuning"] = True
    raw = json.dumps(payload).encode("utf-8")
    with pytest.raises(OptionCompilerRejected):
        parse(raw)


def test_unsupported_schema_rejected_at_parse() -> None:
    payload = json.loads(chain_bytes(bull_chain()))
    payload["schema"] = "esscher.other_chain"
    raw = json.dumps(payload).encode("utf-8")
    with pytest.raises(OptionCompilerRejected):
        parse(raw)


def test_compiler_boundary_turns_malformed_chain_into_no_package() -> None:
    result = compile_option_package_from_decision(
        decision_direction=Direction.UP,
        decision_ticker="ACME",
        chain_bytes=b'{"schema":"esscher.other_chain"}',
        decision_cutoff=datetime(2026, 9, 11, 13, 35, 0, tzinfo=UTC),
    )
    assert result.is_no_package
    assert result.reasons == (PackageRejectionReason.CHAIN_DOCUMENT_REJECTED.value,)


def test_chain_underlying_mismatch_yields_no_package() -> None:
    result = compile_option_package_from_decision(
        decision_direction=Direction.UP,
        decision_ticker="OTHER",
        chain_bytes=chain_bytes(bull_chain()),
        decision_cutoff=datetime(2026, 9, 11, 13, 35, 0, tzinfo=UTC),
    )
    assert result.is_no_package
    assert result.reasons == (PackageRejectionReason.CHAIN_DOCUMENT_REJECTED.value,)


def test_cutoff_outside_registered_utc_window_yields_no_package() -> None:
    result = compile_option_package_from_decision(
        decision_direction=Direction.UP,
        decision_ticker="ACME",
        chain_bytes=chain_bytes(bull_chain()),
        decision_cutoff=datetime(2026, 9, 11, 23, 35, 0, tzinfo=UTC),
    )
    assert result.is_no_package
    assert result.reasons == (PackageRejectionReason.ENTRY_DATE_UNRESOLVED.value,)


def test_identical_inputs_produce_identical_package_bytes() -> None:
    raw = chain_bytes(bull_chain())
    first = compile_option_package_from_decision(
        decision_direction=Direction.UP,
        decision_ticker="ACME",
        chain_bytes=raw,
        decision_cutoff=datetime(2026, 9, 11, 13, 35, 0, tzinfo=UTC),
    )
    second = compile_option_package_from_decision(
        decision_direction=Direction.UP,
        decision_ticker="ACME",
        chain_bytes=raw,
        decision_cutoff=datetime(2026, 9, 11, 13, 35, 0, tzinfo=UTC),
    )
    assert first.package is not None and second.package is not None
    assert first.package.raw == second.package.raw
    assert first.package.sha256 == second.package.sha256


def test_compiler_emits_no_market_order_or_quantity_above_one() -> None:
    chain = parse(chain_bytes(bull_chain()))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    payload = result.package.strategy_payload if result.package else {}
    assert payload.get("quantity") == 1
    assert payload.get("kind") == "DEBIT_VERTICAL"
    assert payload["long_leg"]["option_type"] == payload["short_leg"]["option_type"]


def test_compiled_package_flows_through_permit_boundary() -> None:
    from test_research_to_permit import contract_parts, map_contract, render_contract

    chain = parse(chain_bytes(bull_chain()))
    result = compile_option_package(direction=Direction.UP, chain=chain, entry_date=ENTRY)
    assert result.package is not None

    decision, evidence, inputs = contract_parts()
    decision["direction"] = "UP"
    decision["strategy"] = json.loads(result.package.raw)
    contract = render_contract(decision, evidence, inputs)
    permit = map_contract(contract)
    assert permit.vertical_type.value == "BULL_CALL"
    assert permit.quantity == 1
    assert permit.legs[0].symbol == occ("ACME", NEAR_EXPIRY, "CALL", Decimal("100.00"))


def test_compiler_module_imports_no_broker_machinery() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "ringdown_market"
        / "execution"
        / "option_compiler.py"
    )
    text = source.read_text(encoding="utf-8")
    for marker in ("host_mcp", "execution.mcp", "paper_demo", "McpPaperBroker"):
        assert marker not in text
