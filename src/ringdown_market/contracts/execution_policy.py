"""Frozen execution-policy and official Alpaca MCP protocol identities."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

ALPACA_MCP_VERSION = "2.3.0"
ALPACA_MCP_COMMIT = "872abbf28dab6cdde7d341fc13ac139b8002d1d9"
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
ALPACA_MCP_PROTOCOL_SHA256 = _sha256_object(ALPACA_MCP_PROTOCOL)

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
    "execution_protocol_sha256": ALPACA_MCP_PROTOCOL_SHA256,
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
