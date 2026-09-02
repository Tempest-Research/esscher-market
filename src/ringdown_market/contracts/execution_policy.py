"""Frozen execution-policy and official Alpaca MCP protocol identities."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

# V1 is intentionally frozen: these values and the derived protocol digest are
# audit/replay history, not an eligible current-session identity.
ALPACA_MCP_V1_VERSION = "2.3.0"
ALPACA_MCP_V1_COMMIT = "872abbf28dab6cdde7d341fc13ac139b8002d1d9"
ALPACA_MCP_VERSION = ALPACA_MCP_V1_VERSION
ALPACA_MCP_COMMIT = ALPACA_MCP_V1_COMMIT

# Current runnable artifact.  2.3.1 has no Git tag or release commit; the
# historical 2.3.0 commit is retained only as a source-equivalence reference.
ALPACA_MCP_V2_VERSION = "2.3.1"
ALPACA_MCP_V2_PROVENANCE = "PYPI_RELEASE_NO_GIT_TAG"
ALPACA_MCP_V2_SOURCE_EQUIVALENT_VERSION = "2.3.0"
ALPACA_MCP_V2_SOURCE_EQUIVALENT_COMMIT = "556be1d1746162b3c1680e262385cb0c23f0e32d"
ALPACA_MCP_V2_DISTRIBUTION_TYPE = "PYPI"
ALPACA_MCP_V2_WHEEL_FILENAME = "alpaca_mcp_server-2.3.1-py3-none-any.whl"
ALPACA_MCP_V2_WHEEL_SHA256 = "f271f4fb58057fe0ad9851587bf2a55019ebfbf387227809c62c17305722bf95"
ALPACA_MCP_V2_SDIST_FILENAME = "alpaca_mcp_server-2.3.1.tar.gz"
ALPACA_MCP_V2_SDIST_SHA256 = "53d84696fff9337cfc0a3725e8d23eaa201963c96a8a1832d4e1a6b18b000fd8"
ALPACA_MCP_V2_FASTMCP_VERSION = "3.4.7"
ALPACA_MCP_V2_FASTMCP_SPEC = "fastmcp>=3.1.0,<4"
ALPACA_MCP_V2_DISCOVERED_TOOL_COUNT = 55
ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT = 17
ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256 = (
    "5af24a66731a455f7f8df313bf05def65c65f5dfa66cf7ce791a2d3a665f40b3"
)
ACCOUNT_TOOL = "get_account_info"
OPEN_TOOL = "place_option_order"
READBACK_TOOL = "get_order_by_client_id"
ORDER_BY_ID_TOOL = "get_order_by_id"
CANCEL_TOOL = "cancel_order_by_id"
POSITIONS_TOOL = "get_all_positions"

PAPER_PERMIT_POLICY_VERSION = "paper-debit-vertical/v1"
PAPER_PERMIT_TTL_SECONDS = 60
PAPER_PERMIT_MAXIMUM_LOSS = Decimal("500.00")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_object(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


ALPACA_MCP_PROTOCOL = {
    "schema": "ringdown.alpaca_mcp_protocol",
    "schema_version": 1,
    "adapter": "ALPACA_MCP",
    "adapter_version": ALPACA_MCP_VERSION,
    "adapter_commit": ALPACA_MCP_COMMIT,
    "run_mode": "PAPER",
    "data_class": "INDICATIVE_DATA",
    "tools": [
        OPEN_TOOL,
        READBACK_TOOL,
        ORDER_BY_ID_TOOL,
        CANCEL_TOOL,
        POSITIONS_TOOL,
    ],
}
ALPACA_MCP_V1_PROTOCOL_SHA256 = _sha256_object(ALPACA_MCP_PROTOCOL)

ALPACA_MCP_V2_PROTOCOL = {
    "schema": "ringdown.alpaca_mcp_protocol",
    "schema_version": 2,
    "adapter": "ALPACA_MCP",
    "adapter_version": ALPACA_MCP_V2_VERSION,
    "distribution_type": ALPACA_MCP_V2_DISTRIBUTION_TYPE,
    "distribution_filename": ALPACA_MCP_V2_WHEEL_FILENAME,
    "wheel_sha256": ALPACA_MCP_V2_WHEEL_SHA256,
    "sdist_filename": ALPACA_MCP_V2_SDIST_FILENAME,
    "sdist_sha256": ALPACA_MCP_V2_SDIST_SHA256,
    "provenance_class": ALPACA_MCP_V2_PROVENANCE,
    "source_equivalent_version": ALPACA_MCP_V2_SOURCE_EQUIVALENT_VERSION,
    "source_equivalent_commit": ALPACA_MCP_V2_SOURCE_EQUIVALENT_COMMIT,
    "runtime": {
        "name": "FastMCP",
        "version": ALPACA_MCP_V2_FASTMCP_VERSION,
        "spec": ALPACA_MCP_V2_FASTMCP_SPEC,
    },
    "discovered_tool_count": ALPACA_MCP_V2_DISCOVERED_TOOL_COUNT,
    "selected_schema_count": ALPACA_MCP_V2_SELECTED_SCHEMA_COUNT,
    "selected_schema_sha256": ALPACA_MCP_V2_SELECTED_SCHEMA_SHA256,
    "run_mode": "PAPER",
    "data_class": "INDICATIVE_DATA",
    "tools": [
        ACCOUNT_TOOL,
        OPEN_TOOL,
        READBACK_TOOL,
        ORDER_BY_ID_TOOL,
        CANCEL_TOOL,
        POSITIONS_TOOL,
    ],
}
ALPACA_MCP_V2_PROTOCOL_SHA256 = _sha256_object(ALPACA_MCP_V2_PROTOCOL)
# Public execution identity is always the current V2 artifact.  V1 remains
# available under an explicitly historical name for replay parsing only.
ALPACA_MCP_PROTOCOL = ALPACA_MCP_V2_PROTOCOL
ALPACA_MCP_PROTOCOL_SHA256 = ALPACA_MCP_V2_PROTOCOL_SHA256
ALPACA_MCP_CURRENT_PROTOCOL = ALPACA_MCP_V2_PROTOCOL
ALPACA_MCP_CURRENT_PROTOCOL_SHA256 = ALPACA_MCP_V2_PROTOCOL_SHA256

RESEARCH_DECISION_PROTOCOL = {
    "schema": "ringdown.research_decision_protocol",
    "schema_version": 1,
    "decision_schema": "ringdown.frozen_research_decision",
    "evidence_schema": "ringdown.point_in_time_evidence_manifest",
    "input_schema": "ringdown.feature_input_snapshot",
    "artifact_schema_version": 1,
    "approved_decision_state": "APPROVED",
    "approved_eligibility": "ELIGIBLE",
    "approved_qfast_status": "NOT_REJECTED_SMALL_SAMPLE",
    "approved_qlatency_status": "NOT_REJECTED_SMALL_SAMPLE",
    "claim": "NOT_ALPHA_EVIDENCE",
    "research_data_class": "POINT_IN_TIME_EVENT_PANEL",
    "data_qualifiers": ["INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE"],
    "hash_representation": "raw_bytes",
    "feature_dependency_check": "ELIGIBLE",
    "freeze_rule": "frozen_at_equals_decision_cutoff",
}
RESEARCH_DECISION_PROTOCOL_SHA256 = _sha256_object(RESEARCH_DECISION_PROTOCOL)

PAPER_PERMIT_POLICY = {
    "schema": "ringdown.paper_execution_permit_policy",
    "schema_version": 1,
    "policy_version": PAPER_PERMIT_POLICY_VERSION,
    "run_mode": "PAPER",
    "data_class": "INDICATIVE_DATA",
    "strategy_kind": "DEBIT_VERTICAL",
    "allowed_vertical_types": ["BEAR_PUT", "BULL_CALL"],
    "quantity": 1,
    "maximum_loss_usd": "500.00",
    "permit_ttl_seconds": PAPER_PERMIT_TTL_SECONDS,
    "research_protocol_sha256": RESEARCH_DECISION_PROTOCOL_SHA256,
    "execution_protocol_sha256": ALPACA_MCP_V2_PROTOCOL_SHA256,
}
PAPER_PERMIT_POLICY_SHA256 = _sha256_object(PAPER_PERMIT_POLICY)


def paper_execution_permit_id(*, authorization_sha256: str) -> str:
    """Derive the registered opening-permit ID from its immutable authorization bytes."""

    identity = {
        "schema": "ringdown.paper_execution_permit_identity",
        "schema_version": 1,
        "authorization_sha256": authorization_sha256,
    }
    return f"rd-permit-{_sha256_object(identity)[:32]}"


def paper_event_run_id(decision_sha256: str) -> str:
    """Derive the immutable runtime correlation ID from exact decision bytes."""

    return f"rd-event-{decision_sha256[:32]}"
