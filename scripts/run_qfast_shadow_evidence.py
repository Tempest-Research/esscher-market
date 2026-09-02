"""Offline Q-FAST shadow-evidence runner command.

Reads the frozen panel configuration, direction receipts, and latency profile
from disk, runs the deterministic shadow evaluation, and writes canonical
artifacts to an ignored output directory.  No network, provider, account, or
broker access is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

from ringdown_market.alpha.fullstack_adapter import (
    build_shadow_replay_plan,
    shadow_replay_plan_bytes,
)
from ringdown_market.alpha.shadow_ledger import SHADOW_PNL_CLASS
from ringdown_market.alpha.shadow_runner import run_shadow_evaluation
from ringdown_market.contracts.latency_profile import packaged_latency_profile_bytes
from ringdown_market.strategy.policy import strategy_policy_sha256

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COST_DECIMAL = "0.000000000000"


def _read(path: Path) -> bytes:
    if not path.is_file():
        raise SystemExit(f"input file does not exist: {path}")
    return path.read_bytes()


def _directory_bytes(path: Path | None) -> tuple[bytes, ...]:
    if path is None:
        return ()
    if not path.is_dir():
        raise SystemExit(f"input directory does not exist: {path}")
    return tuple(sorted(child.read_bytes() for child in path.glob("*.json")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_qfast_shadow_evidence",
        description="Run the deterministic Q-FAST shadow evaluation offline.",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--selection-rule", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    parser.add_argument("--event-list", type=Path, default=None)
    parser.add_argument("--universe-dir", type=Path, default=None)
    parser.add_argument("--windows-dir", type=Path, default=None)
    parser.add_argument("--latency-profile", type=Path, default=None)
    parser.add_argument("--policy-sha", type=str, default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "qfast-shadow")
    args = parser.parse_args(argv)

    profile_bytes = (
        _read(args.latency_profile)
        if args.latency_profile is not None
        else packaged_latency_profile_bytes()
    )
    policy_sha = args.policy_sha or strategy_policy_sha256()

    result = run_shadow_evaluation(
        manifest_bytes=_read(args.manifest),
        selection_rule_bytes=_read(args.selection_rule),
        bundle_bytes=_read(args.bundle),
        receipt_bytes=_directory_bytes(args.receipts_dir),
        latency_profile_bytes=profile_bytes,
        expected_policy_sha256=policy_sha,
        event_list_bytes=_read(args.event_list) if args.event_list else None,
        universe_manifest_bytes=_directory_bytes(args.universe_dir),
        window_bytes=_directory_bytes(args.windows_dir),
    )

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "qfast_shadow_report.json").write_bytes(result.bytes)
    (args.out / "evidence_validation.json").write_bytes(result.validation.bytes())
    plan_sha = ""
    if result.accepted:
        plan = build_shadow_replay_plan(
            result,
            candidate_id=result.receipts[0].candidate_id,
            policy_sha256=policy_sha,
            evidence_sha256=result.validation.manifest_sha256,
            feature_sha256=result.validation.selection_rule_sha256,
            snapshot_sha256=result.sha256,
            costs={event: Decimal(DEFAULT_COST_DECIMAL) for event in result.symbols},
        )
        plan_bytes = shadow_replay_plan_bytes(plan)
        (args.out / "shadow_replay_plan.json").write_bytes(plan_bytes)
        plan_sha = hashlib.sha256(plan_bytes).hexdigest()

    print(f"accepted={result.accepted}")
    print(f"claim={result.claim}")
    print(f"classification={result.classification}")
    print(f"events={result.validation.event_count} sectors={result.validation.sector_count}")
    print(f"source={result.validation.source_status} clocks={result.validation.clock_status}")
    print(f"rights={result.validation.rights_status}")
    print(
        f"promotion={result.promotion_recommendation.value} "
        f"reasons={list(result.promotion_reasons)}"
    )
    print(f"pnl_classification={SHADOW_PNL_CLASS}")
    print(f"report_sha256={result.sha256}")
    if plan_sha:
        print(f"replay_plan_sha256={plan_sha}")
    for reason in result.rejection_reasons:
        print(f"rejection={reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
