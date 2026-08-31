"""Read-only point-in-time strategy snapshot collection for Esscher v1.

This package consumes the frozen accepted event policy and produces canonical
strategy snapshots and feature receipts from permitted primary evidence and
synchronized equity market data. It never mutates broker state, never infers
missing facts, and never ships raw licensed payloads.
"""

from ringdown_market.sourcedata.compiler import (
    PRODUCER_BUILD_SHA256,
    CaptureClocks,
    CaptureConfiguration,
    CompiledSnapshot,
    compile_strategy_snapshot,
    compiled_strategy_input,
    derive_clocks,
)
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.rights_gate import CaptureRightsReport, evaluate_capture_rights

__all__ = [
    "PRODUCER_BUILD_SHA256",
    "CaptureClocks",
    "CaptureConfiguration",
    "CaptureRightsReport",
    "CollectorReason",
    "CollectorRejected",
    "CompiledSnapshot",
    "compile_strategy_snapshot",
    "compiled_strategy_input",
    "derive_clocks",
    "evaluate_capture_rights",
]
