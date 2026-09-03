"""Issue #90: read-only broker preflight receipt contract boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ringdown_market.contracts.broker_preflight import (
    PREFLIGHT_CLAIMS,
    BrokerPreflightReceipt,
    BrokerPreflightRejected,
    PreflightVerdict,
    broker_preflight_receipt_bytes,
    finalize_broker_preflight_receipt,
    parse_broker_preflight_receipt,
)
from ringdown_market.contracts.execution_policy import (
    ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256,
    ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256,
    ALPACA_MCP_V2_PROTOCOL_SHA256,
    ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256,
    ALPACA_MCP_V2_WHEEL_SHA256,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _passed_receipt(**overrides: object) -> BrokerPreflightReceipt:
    fields: dict[str, object] = {
        "receipt_id": "preflight-run-0001",
        "verdict": PreflightVerdict.PASSED,
        "reason_codes": (),
        "observed_at": NOW,
        "account_id_sha256": "ab" * 32,
        "account_class": "PAPER",
        "account_status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "options_enabled": True,
        "starting_equity": Decimal("100000"),
        "starting_equity_contract": Decimal("100000"),
        "starting_balance_satisfied": True,
        "account_query_succeeded": True,
        "orders_query_succeeded": True,
        "orders_page_count": 1,
        "open_order_count": 0,
        "orders_state_sha256": "cd" * 32,
        "positions_query_succeeded": True,
        "open_position_count": 0,
        "positions_state_sha256": "ef" * 32,
        "activities_query_succeeded": True,
        "activities_page_count": 2,
        "activities_state_sha256": "12" * 32,
        "is_flat": True,
        "runtime_code_revision": "0123456789abcdef0123456789abcdef01234567",
        "runtime_build_artifact_sha256": "34" * 32,
        "account_capability_id": "paper-capability-0001",
        "route_config_sha256": "56" * 32,
        "latency_profile_sha256": "78" * 32,
        "release_sha256": "9a" * 32,
        "environment": "PAPER",
        "adapter": "ALPACA_MCP",
        "adapter_version": "2.3.1",
        "distribution_type": "PYPI",
        "wheel_filename": "alpaca_mcp_server-2.3.1-py3-none-any.whl",
        "wheel_sha256": ALPACA_MCP_V2_WHEEL_SHA256,
        "sdist_filename": "alpaca_mcp_server-2.3.1.tar.gz",
        "sdist_sha256": "0b50c14c8bc62fa7a914606922c92d71819e669edef10db3b386810cc0b3cee1",
        "provenance_class": "PYPI_RELEASE_NO_GIT_TAG",
        "source_equivalent_version": "2.3.1",
        "source_equivalent_commit": "94a0d9a721b0e81c5c7bcd1063aaa8eeb8bed087",
        "fastmcp_version": "3.4.7",
        "fastmcp_spec": "fastmcp>=3.1.0,<4",
        "discovered_tool_count": 26,
        "required_tool_count": 8,
        "selected_schema_count": 6,
        "selected_schema_sha256": ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256,
        "readonly_extension_count": 2,
        "readonly_extension_schema_sha256": ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256,
        "host_operations_protocol_sha256": ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256,
        "execution_protocol_sha256": ALPACA_MCP_V2_PROTOCOL_SHA256,
        "tool_names": (
            "cancel_order_by_id",
            "get_account_info",
            "get_all_positions",
            "get_order_by_client_id",
            "get_order_by_id",
            "place_option_order",
        ),
        "readonly_extension_tool_names": ("get_account_activities", "get_orders"),
        "capability_sha256": "bc" * 32,
    }
    fields.update(overrides)
    return finalize_broker_preflight_receipt(BrokerPreflightReceipt(**fields))  # type: ignore[arg-type]


def test_passed_receipt_roundtrips_and_is_content_addressed() -> None:
    receipt = _passed_receipt()
    raw = broker_preflight_receipt_bytes(receipt)

    parsed = parse_broker_preflight_receipt(raw)

    assert parsed == receipt
    assert parsed.verdict is PreflightVerdict.PASSED
    assert parsed.reason_codes == ()
    assert parsed.receipt_sha256 == receipt.receipt_sha256
    assert broker_preflight_receipt_bytes(parsed) == raw
    assert json.loads(raw)["claims"] == list(PREFLIGHT_CLAIMS)


def test_rejected_receipt_requires_and_preserves_reason_codes() -> None:
    receipt = _passed_receipt(
        verdict=PreflightVerdict.REJECTED,
        reason_codes=("NON_FLAT_START",),
        is_flat=False,
        open_position_count=2,
        positions_state_sha256="ee" * 32,
    )

    parsed = parse_broker_preflight_receipt(broker_preflight_receipt_bytes(receipt))

    assert parsed.verdict is PreflightVerdict.REJECTED
    assert parsed.reason_codes == ("NON_FLAT_START",)
    assert parsed.is_flat is False


def test_passed_receipt_cannot_carry_reason_codes() -> None:
    receipt = _passed_receipt(reason_codes=("NON_FLAT_START",), is_flat=False)

    with pytest.raises(BrokerPreflightRejected, match="PASSED"):
        parse_broker_preflight_receipt(broker_preflight_receipt_bytes(receipt))


def test_rejected_receipt_without_reasons_fails() -> None:
    receipt = _passed_receipt(verdict=PreflightVerdict.REJECTED, reason_codes=())

    with pytest.raises(BrokerPreflightRejected, match="REJECTED"):
        parse_broker_preflight_receipt(broker_preflight_receipt_bytes(receipt))


def test_unknown_reason_code_fails_closed() -> None:
    receipt = _passed_receipt(
        verdict=PreflightVerdict.REJECTED, reason_codes=("SOMETHING_ELSE",), is_flat=False
    )

    with pytest.raises(BrokerPreflightRejected, match="unknown reason code"):
        parse_broker_preflight_receipt(broker_preflight_receipt_bytes(receipt))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wheel_sha256", "0" * 64),
        ("selected_schema_sha256", "0" * 64),
        ("readonly_extension_schema_sha256", "0" * 64),
        ("host_operations_protocol_sha256", "0" * 64),
        ("required_tool_count", 6),
        ("discovered_tool_count", 27),
        ("environment", "LIVE"),
        ("adapter_version", "2.3.0"),
        ("fastmcp_version", "3.4.6"),
    ],
)
def test_pinned_capability_identity_drift_fails_closed(field: str, value: object) -> None:
    receipt = _passed_receipt(**{field: value})

    with pytest.raises(BrokerPreflightRejected, match="pinned MCP identity"):
        parse_broker_preflight_receipt(broker_preflight_receipt_bytes(receipt))


def test_tool_selection_drift_fails_closed() -> None:
    without_open = _passed_receipt(
        tool_names=(
            "cancel_order_by_id",
            "get_account_info",
            "get_all_positions",
            "get_order_by_client_id",
            "get_order_by_id",
        ),
    )
    with pytest.raises(BrokerPreflightRejected, match="pinned six"):
        parse_broker_preflight_receipt(broker_preflight_receipt_bytes(without_open))

    swapped_extension = _passed_receipt(
        readonly_extension_tool_names=("get_account_activities_by_type", "get_orders"),
    )
    with pytest.raises(BrokerPreflightRejected, match="read-only pair"):
        parse_broker_preflight_receipt(broker_preflight_receipt_bytes(swapped_extension))


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"trading_blocked": True}, "blocked account"),
        ({"account_blocked": True}, "blocked account"),
        ({"options_enabled": False}, "options capability"),
        ({"starting_balance_satisfied": False}, "starting-balance"),
        (
            {"starting_equity": Decimal("99999"), "starting_balance_satisfied": True},
            "starting-balance",
        ),
        ({"orders_query_succeeded": False}, "read-only query"),
        ({"activities_query_succeeded": False}, "read-only query"),
        ({"orders_page_count": 0}, "paginated"),
        ({"activities_page_count": 0}, "paginated"),
        ({"is_flat": False}, "flat starting state"),
        ({"open_order_count": 1}, "flat starting state"),
        ({"account_class": "LIVE"}, "PAPER accounts"),
    ],
)
def test_passed_verdict_requires_every_readiness_fact(
    overrides: dict[str, object], match: str
) -> None:
    receipt = _passed_receipt(**overrides)

    with pytest.raises(BrokerPreflightRejected, match=match):
        parse_broker_preflight_receipt(broker_preflight_receipt_bytes(receipt))


def test_tampered_receipt_hash_fails_closed() -> None:
    receipt = _passed_receipt()
    payload = json.loads(broker_preflight_receipt_bytes(receipt))
    payload["receipt_sha256"] = "0" * 64

    with pytest.raises(BrokerPreflightRejected, match="HASH_MISMATCH"):
        parse_broker_preflight_receipt(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )


def test_unknown_and_missing_and_duplicate_fields_fail_closed() -> None:
    raw = broker_preflight_receipt_bytes(_passed_receipt())
    payload = json.loads(raw)

    extra = dict(payload) | {"surprise": 1}
    with pytest.raises(BrokerPreflightRejected, match="frozen schema"):
        parse_broker_preflight_receipt(
            json.dumps(extra, sort_keys=True, separators=(",", ":")).encode()
        )

    missing = {key: value for key, value in payload.items() if key != "capability_sha256"}
    with pytest.raises(BrokerPreflightRejected, match="missing"):
        parse_broker_preflight_receipt(
            json.dumps(missing, sort_keys=True, separators=(",", ":")).encode()
        )

    duplicated = raw.decode().replace('"is_flat":true', '"is_flat":true,"is_flat":true', 1)
    assert duplicated != raw.decode()
    with pytest.raises(BrokerPreflightRejected, match=r"duplicate field"):
        parse_broker_preflight_receipt(duplicated.encode())


def test_receipt_never_serializes_raw_account_or_secret_material() -> None:
    receipt = _passed_receipt()
    raw = broker_preflight_receipt_bytes(receipt).decode()

    for forbidden in ("sensitive", "api_key", "secret", "APCA", "password"):
        assert forbidden not in raw
    # The account identity exists only as a digest field.
    assert "account_id_sha256" in raw
    assert '"account_id"' not in raw


def test_non_canonical_decimal_text_fails_closed() -> None:
    receipt = _passed_receipt()
    payload = json.loads(broker_preflight_receipt_bytes(receipt))
    payload["starting_equity"] = "100000.000"
    payload["starting_equity_contract"] = "100000.000"

    with pytest.raises(BrokerPreflightRejected, match="not canonical"):
        parse_broker_preflight_receipt(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )


def test_release_binding_may_be_explicitly_absent() -> None:
    receipt = _passed_receipt(release_sha256=None)

    parsed = parse_broker_preflight_receipt(broker_preflight_receipt_bytes(receipt))

    assert parsed.release_sha256 is None


def test_claims_are_frozen() -> None:
    receipt = _passed_receipt()
    payload = json.loads(broker_preflight_receipt_bytes(receipt))
    payload["claims"] = ["PAPER_ONLY"]

    with pytest.raises(BrokerPreflightRejected, match="frozen preflight boundary"):
        parse_broker_preflight_receipt(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )


def test_naive_timestamp_fails_closed() -> None:
    receipt = replace(_passed_receipt(), observed_at=NOW.replace(tzinfo=None))

    with pytest.raises(BrokerPreflightRejected, match="timezone-aware"):
        broker_preflight_receipt_bytes(receipt)
