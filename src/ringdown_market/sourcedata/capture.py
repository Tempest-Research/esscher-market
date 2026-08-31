"""Inert capture command for the strategy snapshot collector.

The command never contains credentials and never starts a network, broker,
or MCP session. It runs only with explicit host authorization and replays the
frozen synthetic adapters in this slice; the live read-only boundary is not
pinned and fails closed until a separate recorded gate pins the exact Alpaca
MCP server version and tool schemas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ringdown_market.contracts.source_matrix import CONDITIONS
from ringdown_market.sourcedata.compiler import (
    CaptureConfiguration,
    CompiledSnapshot,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
    load_fixture,
)
from ringdown_market.sourcedata.lineage_gate import (
    evaluate_lineage,
    lineage_receipt_bytes,
)
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.receipts import (
    corporate_action_receipt_bytes,
    source_receipt_bytes,
)
from ringdown_market.sourcedata.rights_gate import evaluate_capture_rights

HOST_AUTHORIZATION_VARIABLE = "ESSCHER_CAPTURE_AUTHORIZED"
HOST_AUTHORIZATION_VALUE = "yes"


def _configuration(
    args: argparse.Namespace, fixture, lineage_receipt_sha256: str | None
) -> CaptureConfiguration:
    capture_at = datetime.fromisoformat(args.capture_at.replace("Z", "+00:00")).astimezone(UTC)
    return CaptureConfiguration(
        candidate_manifest_bytes=build_candidate_manifest(fixture),
        event_id=args.event_id,
        capture_at=capture_at,
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
        lineage_receipt_sha256=lineage_receipt_sha256,
    )


def run_capture(configuration: CaptureConfiguration) -> CompiledSnapshot:
    """Run one offline capture over the frozen synthetic adapters."""

    fixture = load_fixture()
    evidence = FixtureEvidenceSource(fixture)
    market = FixtureMarketDataSource(fixture)
    return compile_strategy_snapshot(configuration, evidence, market)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ringdown_market.sourcedata.capture",
        description="Compile one deterministic point-in-time strategy snapshot offline.",
    )
    parser.add_argument("--event-id", required=True, help="frozen candidate event ID")
    parser.add_argument(
        "--capture-at",
        required=True,
        help="explicit host retrieval clock (UTC ISO-8601)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="existing directory receiving the canonical artifacts",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="optional alternate frozen fixture path",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="request the live read-only boundary (not pinned in this slice)",
    )
    parser.add_argument(
        "--source-matrix",
        type=Path,
        default=None,
        help="optional alternate source-matrix path (defaults to the packaged frozen matrix)",
    )
    parser.add_argument(
        "--lineage",
        type=Path,
        default=None,
        help="optional alternate security-lineage path (defaults to the packaged frozen lineage)",
    )
    parser.add_argument(
        "--condition-satisfied",
        dest="conditions_satisfied",
        action="append",
        default=[],
        metavar="CONDITION",
        help="declare one frozen source-matrix condition as satisfied for this capture",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; fails closed without explicit host authorization."""

    args = _build_parser().parse_args(argv)
    if os.environ.get(HOST_AUTHORIZATION_VARIABLE) != HOST_AUTHORIZATION_VALUE:
        print(
            str(
                CollectorRejected(
                    CollectorReason.HOST_CONFIGURATION_MISSING,
                    HOST_AUTHORIZATION_VARIABLE,
                    "capture requires explicit host authorization"
                    f" ({HOST_AUTHORIZATION_VARIABLE}={HOST_AUTHORIZATION_VALUE})",
                )
            ),
            file=sys.stderr,
        )
        return 2
    if args.live:
        print(
            str(
                CollectorRejected(
                    CollectorReason.LIVE_BOUNDARY_NOT_PINNED,
                    "live",
                    "the official Alpaca MCP read-only server version and tool"
                    " schemas must be pinned before any live capture",
                )
            ),
            file=sys.stderr,
        )
        return 2
    matrix_bytes: bytes | None = None
    if args.source_matrix is not None:
        if not args.source_matrix.is_file():
            print(
                str(
                    CollectorRejected(
                        CollectorReason.SOURCE_MATRIX_MISSING,
                        "source_matrix",
                        f"source matrix path does not exist: {args.source_matrix}",
                    )
                ),
                file=sys.stderr,
            )
            return 2
        matrix_bytes = args.source_matrix.read_bytes()
    satisfied: set[str] = set()
    for condition in args.conditions_satisfied:
        if condition not in CONDITIONS:
            print(
                str(
                    CollectorRejected(
                        CollectorReason.SOURCE_RIGHTS_LIMITATION_UNMET,
                        "condition_satisfied",
                        f"unknown source-matrix condition '{condition}'",
                    )
                ),
                file=sys.stderr,
            )
            return 2
        satisfied.add(condition)
    try:
        rights_report = evaluate_capture_rights(
            matrix_bytes=matrix_bytes, satisfied_conditions=frozenset(satisfied)
        )
    except CollectorRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    lineage_bytes: bytes | None = None
    if args.lineage is not None:
        if not args.lineage.is_file():
            print(
                str(
                    CollectorRejected(
                        CollectorReason.LINEAGE_MISSING,
                        "lineage",
                        f"security lineage path does not exist: {args.lineage}",
                    )
                ),
                file=sys.stderr,
            )
            return 2
        lineage_bytes = args.lineage.read_bytes()
    try:
        lineage_report = evaluate_lineage(event_id=args.event_id, lineage_bytes=lineage_bytes)
    except CollectorRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    lineage_receipt_digest = hashlib.sha256(
        lineage_receipt_bytes(lineage_report.resolution)
    ).hexdigest()
    fixture = load_fixture(args.fixture)
    try:
        configuration = _configuration(args, fixture, lineage_receipt_digest)
        compiled = run_capture(configuration)
        joined = compiled_strategy_input(compiled)
    except CollectorRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    output_dir = args.output_dir
    if not output_dir.exists() or not output_dir.is_dir():
        print(
            str(
                CollectorRejected(
                    CollectorReason.UNSUPPORTED_INPUT,
                    "output_dir",
                    "output directory must already exist",
                )
            ),
            file=sys.stderr,
        )
        return 2
    if args.output_dir.joinpath("capture.json").is_symlink():
        print(
            str(
                CollectorRejected(
                    CollectorReason.UNSUPPORTED_INPUT,
                    "output_dir",
                    "output paths must not be symbolic links",
                )
            ),
            file=sys.stderr,
        )
        return 2
    joined_identity = {
        "snapshot_sha256": joined.snapshot_sha256,
        "feature_receipt_sha256": joined.feature_receipt_sha256,
        "candidate_manifest_sha256": joined.candidate_manifest_sha256,
        "source_matrix_sha256": rights_report.source_matrix_sha256,
        "security_lineage_sha256": lineage_report.security_lineage_sha256,
    }
    output_dir.joinpath("strategy_snapshot.json").write_bytes(compiled.strategy_snapshot_bytes)
    output_dir.joinpath("feature_receipt.json").write_bytes(compiled.feature_receipt_bytes)
    output_dir.joinpath("candidate_manifest.json").write_bytes(compiled.candidate_manifest_bytes)
    receipts = b"".join(
        source_receipt_bytes(receipt) + b"\n" for receipt in compiled.source_receipts
    )
    output_dir.joinpath("source_receipts.jsonl").write_bytes(receipts)
    action_receipts = b"".join(
        corporate_action_receipt_bytes(receipt) + b"\n" for receipt in compiled.action_receipts
    )
    output_dir.joinpath("corporate_action_receipts.jsonl").write_bytes(action_receipts)
    output_dir.joinpath("lineage_receipts.jsonl").write_bytes(
        lineage_receipt_bytes(lineage_report.resolution) + b"\n"
    )
    output_dir.joinpath("capture_identity.json").write_bytes(
        json.dumps(joined_identity, sort_keys=True, indent=1).encode("utf-8") + b"\n"
    )
    print(f"captured {joined.snapshot.event_id}: snapshot_sha256={joined.snapshot_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
