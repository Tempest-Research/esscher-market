from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ringdown_market.contracts.execution_policy import (
    ALPACA_MCP_V2_PROTOCOL,
    ALPACA_MCP_V2_PROTOCOL_SHA256,
)
from ringdown_market.contracts.gate_a import (
    EntryState,
    evaluate_gate_a,
    load_programme_contract,
    parse_capability_receipt,
    parse_current_capability_receipt,
)
from ringdown_market.execution.host_mcp import (
    HostMcpEnvironment,
    HostMcpPaperSessionFactory,
    HostMcpSessionIdentity,
)
from ringdown_market.execution.mcp import OpenOrderReceipt

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).parent


def _v2_payload() -> dict[str, object]:
    return {
        "schema": "esscher.gate_a_capability_receipt",
        "schema_version": 2,
        "receipt_id": "CURRENT_V2_RECEIPT",
        "observed_at": "2026-09-01T12:00:00Z",
        "expires_at": "2026-09-01T12:01:00Z",
        "environment": "PAPER",
        "adapter": "ALPACA_MCP",
        "adapter_version": "2.3.1",
        "distribution_type": "PYPI",
        "wheel_filename": "alpaca_mcp_server-2.3.1-py3-none-any.whl",
        "wheel_sha256": "f271f4fb58057fe0ad9851587bf2a55019ebfbf387227809c62c17305722bf95",
        "sdist_filename": "alpaca_mcp_server-2.3.1.tar.gz",
        "sdist_sha256": "53d84696fff9337cfc0a3725e8d23eaa201963c96a8a1832d4e1a6b18b000fd8",
        "provenance_class": "PYPI_RELEASE_NO_GIT_TAG",
        "source_equivalent_version": "2.3.0",
        "source_equivalent_commit": "556be1d1746162b3c1680e262385cb0c23f0e32d",
        "fastmcp_version": "3.4.7",
        "fastmcp_spec": "fastmcp>=3.1.0,<4",
        "discovered_tool_count": 55,
        "selected_schema_count": 17,
        "selected_schema_sha256": (
            "5af24a66731a455f7f8df313bf05def65c65f5dfa66cf7ce791a2d3a665f40b3"
        ),
        "tool_names": [
            "cancel_order_by_id",
            "get_account_info",
            "get_all_positions",
            "get_order_by_client_id",
            "get_order_by_id",
            "place_option_order",
        ],
        "programme_contract_sha256": (
            "40c2e780c684bdde671b028dbdd8c9b13268e659c24e98a2d452ff7c8692f955"
        ),
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class Fake:
    async def list_tools(self):
        return _v2_payload()["tool_names"]

    async def call_tool(self, name, arguments):
        assert name == "get_account_info"
        return {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False}


def test_v2_host_factory_accepts_one_valid_fake_attestation() -> None:
    identity = HostMcpSessionIdentity(environment=HostMcpEnvironment.PAPER)
    factory = HostMcpPaperSessionFactory(identity, clock=lambda: NOW)
    prepared = asyncio.run(factory.connect(Fake()))
    assert prepared.observation.adapter_version == "2.3.1"
    assert prepared.observation.wheel_sha256 == _v2_payload()["wheel_sha256"]


@pytest.mark.parametrize(
    "field",
    [
        "adapter_version",
        "wheel_sha256",
        "sdist_sha256",
        "provenance_class",
        "source_equivalent_version",
        "source_equivalent_commit",
        "fastmcp_version",
        "fastmcp_spec",
        "discovered_tool_count",
        "selected_schema_count",
        "selected_schema_sha256",
        "tool_names",
        "environment",
    ],
)
def test_v2_receipt_rejects_one_field_mutations(field: str) -> None:
    payload = _v2_payload()
    original = payload[field]
    if isinstance(original, str):
        payload[field] = original + "x"
    elif isinstance(original, int):
        payload[field] = original + 1
    else:
        payload[field] = []
    with pytest.raises(ValueError):
        parse_capability_receipt(_canonical(payload))


def test_v1_history_is_readable_but_current_gate_rejects_it() -> None:
    raw = (ROOT / "contract_fixtures" / "gate_a_capability_unverified_v1.json").read_bytes()
    receipt = parse_capability_receipt(raw)
    assert receipt.__class__.__name__ == "CapabilityReceipt"
    decision = evaluate_gate_a(load_programme_contract(), receipt, evaluated_at=NOW)
    assert decision.entry_state is EntryState.ENTRY_DISABLED


def test_v2_receipt_is_authenticated_by_gate_a_without_v1_producer_fields() -> None:
    receipt = parse_current_capability_receipt(_canonical(_v2_payload()))
    decision = evaluate_gate_a(load_programme_contract(), receipt, evaluated_at=NOW)
    assert decision.capability_receipt_sha256 == receipt.sha256
    assert decision.entry_state is EntryState.ENTRY_DISABLED


def test_v2_protocol_is_current_and_v1_protocol_is_not_accepted_for_new_execution() -> None:
    assert (
        hashlib.sha256(_canonical(ALPACA_MCP_V2_PROTOCOL)).hexdigest()
        == ALPACA_MCP_V2_PROTOCOL_SHA256
    )
    assert ALPACA_MCP_V2_PROTOCOL["schema_version"] == 2
    assert "adapter_commit" not in ALPACA_MCP_V2_PROTOCOL


def test_current_host_and_order_receipts_never_claim_a_231_git_commit() -> None:
    identity = HostMcpSessionIdentity(environment=HostMcpEnvironment.PAPER)
    prepared = asyncio.run(HostMcpPaperSessionFactory(identity, clock=lambda: NOW).connect(Fake()))

    for contract in (identity, prepared.observation):
        fields = contract.__dataclass_fields__
        assert "adapter_commit" not in fields
        assert contract.adapter_version == "2.3.1"
        assert contract.provenance_class == "PYPI_RELEASE_NO_GIT_TAG"
        assert contract.wheel_sha256 == _v2_payload()["wheel_sha256"]
        assert contract.sdist_sha256 == _v2_payload()["sdist_sha256"]
        assert contract.selected_schema_sha256 == _v2_payload()["selected_schema_sha256"]
        assert contract.execution_protocol_sha256 == ALPACA_MCP_V2_PROTOCOL_SHA256

    assert "adapter_commit" not in OpenOrderReceipt.__dataclass_fields__
    assert "execution_protocol_sha256" in OpenOrderReceipt.__dataclass_fields__

    minimal_v1_style = _canonical(
        {
            "adapter": identity.adapter,
            "adapter_commit": "872abbf28dab6cdde7d341fc13ac139b8002d1d9",
            "adapter_version": identity.adapter_version,
            "environment": identity.environment.value,
            "required_tools": sorted(_v2_payload()["tool_names"]),
        }
    )
    assert prepared.observation.capability_sha256 != hashlib.sha256(minimal_v1_style).hexdigest()
