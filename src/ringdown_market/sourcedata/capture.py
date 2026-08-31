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
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ringdown_market.contracts.source_matrix import CONDITIONS
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
from ringdown_market.sourcedata.feasibility import feasibility_manifest_bytes
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.receipts import (
    corporate_action_receipt_bytes,
    source_receipt_bytes,
)
from ringdown_market.sourcedata.rights_gate import evaluate_capture_rights

HOST_AUTHORIZATION_VARIABLE = "ESSCHER_CAPTURE_AUTHORIZED"
HOST_AUTHORIZATION_VALUE = "yes"
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")


def _capture_timestamp(value: str) -> datetime:
    """Parse an explicit zero-offset UTC capture clock without host-local coercion."""

    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "capture_at",
            "capture time must use an explicit UTC Z or +00:00 offset",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "capture_at",
            "capture time must be an ISO-8601 UTC timestamp",
        ) from error
    return parsed.astimezone(UTC)


def _configuration(args: argparse.Namespace, fixture, manifest_builder) -> CaptureConfiguration:
    capture_at = _capture_timestamp(args.capture_at)
    return CaptureConfiguration(
        candidate_manifest_bytes=manifest_builder(fixture),
        event_id=args.event_id,
        capture_at=capture_at,
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
    )


def run_capture(configuration: CaptureConfiguration, candidate: str, fixture) -> CompiledSnapshot:
    """Run one offline capture over the frozen synthetic adapters."""

    if candidate == MACRO_CANDIDATE:
        evidence = FixtureMacroEvidenceSource(fixture)
        macro = FixtureMacroReleaseSource(fixture)
        market = FixtureMacroMarketDataSource(fixture)
        return compile_macro_snapshot(configuration, evidence.sessions, macro, market)
    evidence = FixtureEvidenceSource(fixture)
    market = FixtureMarketDataSource(fixture)
    return compile_strategy_snapshot(configuration, evidence, market)


def _build_feasibility(candidate: str, fixture, compiled, capture_at):
    """Build the candidate-specific Gate B feasibility manifest."""

    from ringdown_market.sourcedata.fakes import load_feasibility_declarations
    from ringdown_market.sourcedata.feasibility import build_feasibility_for_candidate
    from ringdown_market.strategy.policy import load_strategy_policy

    fallback = MACRO_CANDIDATE if candidate == EARNINGS_CANDIDATE else None
    return build_feasibility_for_candidate(
        policy=load_strategy_policy(),
        candidate_id=candidate,
        declarations=load_feasibility_declarations(fixture),
        source_receipts=compiled.source_receipts,
        evaluated_at=capture_at,
        producer_build_sha256=compiled.snapshot.producer_build_sha256,
        fallback_candidate_id=fallback,
    )


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
        required=True,
        help="explicit frozen development fixture path (never loaded from the package)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="request the live read-only boundary (not pinned in this slice)",
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
    candidate = MACRO_CANDIDATE if args.event_id.startswith("BLS-") else EARNINGS_CANDIDATE
    try:
        rights_report = evaluate_capture_rights(
            candidate_id=candidate,
            satisfied_conditions=frozenset(satisfied),
        )
    except CollectorRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    if candidate == MACRO_CANDIDATE:
        fixture = load_macro_fixture(args.fixture)
        manifest_builder = build_macro_candidate_manifest
    else:
        fixture = load_fixture(args.fixture)
        manifest_builder = build_candidate_manifest
    try:
        configuration = _configuration(args, fixture, manifest_builder)
        compiled = run_capture(configuration, candidate, fixture)
        joined = compiled_strategy_input(compiled)
        feasibility_manifest = _build_feasibility(
            candidate, fixture, compiled, configuration.capture_at
        )
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
    }
    output_dir.joinpath("strategy_snapshot.json").write_bytes(compiled.strategy_snapshot_bytes)
    output_dir.joinpath("feature_receipt.json").write_bytes(compiled.feature_receipt_bytes)
    output_dir.joinpath("candidate_manifest.json").write_bytes(compiled.candidate_manifest_bytes)
    output_dir.joinpath("data_feasibility_manifest.json").write_bytes(
        feasibility_manifest_bytes(feasibility_manifest)
    )
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
