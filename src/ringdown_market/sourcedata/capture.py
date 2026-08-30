"""Inert capture command for the strategy snapshot collector.

The command never contains credentials and never starts a network, broker,
or MCP session. It runs only with explicit host authorization and replays the
frozen synthetic adapters in this slice; the live read-only boundary is not
pinned and fails closed until a separate recorded gate pins the exact Alpaca
MCP server version and tool schemas.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ringdown_market.sourcedata.compiler import (
    EARNINGS_CANDIDATE,
    MACRO_CANDIDATE,
    CaptureConfiguration,
    CompiledSnapshot,
    compile_macro_snapshot,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMacroEvidenceSource,
    FixtureMacroMarketDataSource,
    FixtureMacroReleaseSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
    build_macro_candidate_manifest,
    load_fixture,
    load_macro_fixture,
)
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.receipts import (
    corporate_action_receipt_bytes,
    source_receipt_bytes,
)

HOST_AUTHORIZATION_VARIABLE = "ESSCHER_CAPTURE_AUTHORIZED"
HOST_AUTHORIZATION_VALUE = "yes"


def _configuration(args: argparse.Namespace, fixture, manifest_builder) -> CaptureConfiguration:
    capture_at = datetime.fromisoformat(args.capture_at.replace("Z", "+00:00")).astimezone(UTC)
    return CaptureConfiguration(
        candidate_manifest_bytes=manifest_builder(fixture),
        event_id=args.event_id,
        capture_at=capture_at,
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )


def run_capture(configuration: CaptureConfiguration, candidate: str) -> CompiledSnapshot:
    """Run one offline capture over the frozen synthetic adapters."""

    if candidate == MACRO_CANDIDATE:
        fixture = load_macro_fixture()
        evidence = FixtureMacroEvidenceSource(fixture)
        macro = FixtureMacroReleaseSource(fixture)
        market = FixtureMacroMarketDataSource(fixture)
        return compile_macro_snapshot(configuration, evidence.sessions, macro, market)
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
    fixture = load_fixture(args.fixture)
    candidate = MACRO_CANDIDATE if args.event_id.startswith("BLS-") else EARNINGS_CANDIDATE
    if candidate == MACRO_CANDIDATE:
        fixture = load_macro_fixture(args.fixture)
        manifest_builder = build_macro_candidate_manifest
    else:
        manifest_builder = build_candidate_manifest
    try:
        configuration = _configuration(args, fixture, manifest_builder)
        compiled = run_capture(configuration, candidate)
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
    output_dir.joinpath("capture_identity.json").write_bytes(
        json.dumps(joined_identity, sort_keys=True, indent=1).encode("utf-8") + b"\n"
    )
    print(f"captured {joined.snapshot.event_id}: snapshot_sha256={joined.snapshot_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
