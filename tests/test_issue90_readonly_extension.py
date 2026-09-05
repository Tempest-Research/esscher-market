"""Issue #90: the read-only operational extension over the pinned 2.3.1 artifact.

The frozen V2 mutation protocol, its six-tool selection, and every artifact
hash from the #84 provenance repair remain byte-identical.  The extension adds
exactly two read-only tools (open-order listing and account activities) whose
runtime schemas were re-derived from the pinned wheel through FastMCP
``list_tools`` in an isolated venv, then canonically hashed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from esscher.contracts.execution_policy import (
    ACTIVITIES_TOOL,
    ALPACA_MCP_HOST_OPERATIONS_PROTOCOL,
    ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256,
    ALPACA_MCP_READONLY_EXTENSION_CANONICALIZATION,
    ALPACA_MCP_READONLY_EXTENSION_COUNT,
    ALPACA_MCP_READONLY_EXTENSION_RECEIPT_SHA256,
    ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256,
    ALPACA_MCP_READONLY_EXTENSION_TOOLS,
    ALPACA_MCP_V2_PROTOCOL,
    ALPACA_MCP_V2_PROTOCOL_SHA256,
    ALPACA_MCP_V2_SELECTED_SCHEMA_CANONICALIZATION,
    ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT,
    ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256,
    ALPACA_MCP_V2_WHEEL_SHA256,
    ORDERS_TOOL,
)

ROOT = Path(__file__).parent
EXTENSION_PATH = ROOT / "contract_fixtures" / "alpaca_mcp_v2_3_1_readonly_extension.json"
BASE_PROVENANCE_PATH = ROOT / "contract_fixtures" / "alpaca_mcp_v2_3_1_provenance.json"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _extension_receipt() -> dict[str, object]:
    return json.loads(EXTENSION_PATH.read_text(encoding="utf-8"))


def test_frozen_v2_contract_values_are_untouched_by_the_extension() -> None:
    assert ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT == 6
    assert (
        ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256
        == "8df3ec51f2468b6ed938763c3fb8a2d9d8ce2390c18225fe3c9e6d2e3a894787"
    )
    assert (
        hashlib.sha256(_canonical(ALPACA_MCP_V2_PROTOCOL)).hexdigest()
        == ALPACA_MCP_V2_PROTOCOL_SHA256
    )


def test_extension_receipt_is_self_consistent_and_pinned() -> None:
    receipt = _extension_receipt()
    unsigned = dict(receipt)
    del unsigned["receipt_sha256"]
    assert hashlib.sha256(_canonical(unsigned)).hexdigest() == receipt["receipt_sha256"]
    assert receipt["receipt_sha256"] == ALPACA_MCP_READONLY_EXTENSION_RECEIPT_SHA256

    schemas = receipt["extension_tool_schemas"]
    assert isinstance(schemas, list)
    assert len(schemas) == receipt["extension_tool_schema_count"] == 2
    assert (
        hashlib.sha256(_canonical(schemas)).hexdigest()
        == receipt["extension_tool_schema_sha256"]
        == ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256
    )
    assert (
        [entry["name"] for entry in schemas]
        == sorted([ACTIVITIES_TOOL, ORDERS_TOOL])
        == list(ALPACA_MCP_READONLY_EXTENSION_TOOLS)
    )
    assert (
        receipt["extension_tool_schema_canonicalization"]
        == ALPACA_MCP_READONLY_EXTENSION_CANONICALIZATION
        == ALPACA_MCP_V2_SELECTED_SCHEMA_CANONICALIZATION
    )
    assert receipt["extension_tool_schema_count"] == ALPACA_MCP_READONLY_EXTENSION_COUNT


def test_extension_binds_the_identical_pinned_artifact_and_base_receipt() -> None:
    receipt = _extension_receipt()
    base = json.loads(BASE_PROVENANCE_PATH.read_text(encoding="utf-8"))

    assert receipt["adapter_version"] == base["adapter_version"] == "2.3.1"
    assert receipt["provenance_class"] == base["provenance_class"]
    assert receipt["wheel_sha256"] == base["wheel"]["sha256"] == ALPACA_MCP_V2_WHEEL_SHA256
    assert receipt["base_selection_receipt_sha256"] == base["receipt_sha256"]
    assert receipt["base_selected_tool_schema_sha256"] == base["selected_tool_schema_sha256"]

    derivation = receipt["derivation"]
    assert isinstance(derivation, dict)
    runtime = base["runtime"]
    assert derivation["fastmcp_version"] == runtime["fastmcp_version"]
    assert derivation["fastmcp_spec"] == runtime["fastmcp_spec"]
    assert derivation["server_toolsets"] == runtime["server_toolsets"]
    assert derivation["discovered_tool_count"] == runtime["discovered_tool_count"] == 26


def test_extension_selection_is_read_only_and_disjoint_from_the_mutation_six() -> None:
    receipt = _extension_receipt()
    base = json.loads(BASE_PROVENANCE_PATH.read_text(encoding="utf-8"))
    base_names = {entry["name"] for entry in base["selected_tool_schemas"]}
    extension_names = {entry["name"] for entry in receipt["extension_tool_schemas"]}

    assert not (base_names & extension_names)
    assert extension_names == {ACTIVITIES_TOOL, ORDERS_TOOL}
    # The frozen mutation tools are never part of the extension selection.
    assert not (extension_names & {"place_option_order", "cancel_order_by_id"})


def test_host_operations_protocol_is_self_hashed_and_never_redefines_v2() -> None:
    protocol = ALPACA_MCP_HOST_OPERATIONS_PROTOCOL
    assert (
        hashlib.sha256(_canonical(protocol)).hexdigest()
        == ALPACA_MCP_HOST_OPERATIONS_PROTOCOL_SHA256
    )
    assert protocol["schema"] == "ringdown.alpaca_mcp_host_operations_protocol"
    assert protocol["schema_version"] == 1
    assert protocol["mutation_protocol_sha256"] == ALPACA_MCP_V2_PROTOCOL_SHA256
    assert protocol["selected_schema_count"] == ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT
    assert protocol["selected_schema_sha256"] == ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256
    assert protocol["readonly_extension_count"] == ALPACA_MCP_READONLY_EXTENSION_COUNT
    assert (
        protocol["readonly_extension_schema_sha256"] == ALPACA_MCP_READONLY_EXTENSION_SCHEMA_SHA256
    )
    assert (
        protocol["readonly_extension_receipt_sha256"]
        == ALPACA_MCP_READONLY_EXTENSION_RECEIPT_SHA256
    )
    assert protocol["readonly_extension_tools"] == list(ALPACA_MCP_READONLY_EXTENSION_TOOLS)
    assert protocol["run_mode"] == "PAPER"
    assert protocol["data_class"] == "INDICATIVE_DATA"
