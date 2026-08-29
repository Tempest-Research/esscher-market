from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from ringdown_market.execution.paper_demo import (
    FilePaperAttemptStore,
    PaperDemoApproval,
    PaperDemoPlan,
    run_paper_demo,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight or run one explicitly approved Esscher PAPER proof."
    )
    parser.add_argument(
        "--host-plan",
        required=True,
        help="host-owned module:function returning PaperDemoPlan",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-output", type=Path)
    mode.add_argument("--execute-paper", action="store_true")
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument("--attempt-store", type=Path)
    parser.add_argument("--receipt-output", type=Path)
    return parser


def _load_factory(reference: str) -> Callable[[], PaperDemoPlan | Awaitable[PaperDemoPlan]]:
    if reference.count(":") != 1:
        raise ValueError("--host-plan must use module:function")
    module_name, attribute = reference.split(":", 1)
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("--host-plan target is not callable")
    return factory


async def _build_plan(reference: str) -> PaperDemoPlan:
    value = _load_factory(reference)()
    plan = await value if inspect.isawaitable(value) else value
    if not isinstance(plan, PaperDemoPlan):
        raise TypeError("--host-plan must return PaperDemoPlan")
    return plan


def _write_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value + b"\n")


async def _run(args: argparse.Namespace) -> int:
    if args.preflight_output is not None:
        if any((args.approval_file, args.attempt_store, args.receipt_output)):
            raise ValueError("preflight accepts only --host-plan and --preflight-output")
        plan = await _build_plan(args.host_plan)
        _write_new(args.preflight_output, plan.approval_template_json_bytes())
        return 0

    if not all((args.approval_file, args.attempt_store, args.receipt_output)):
        raise ValueError(
            "--execute-paper requires --approval-file, --attempt-store, and --receipt-output"
        )
    approval = PaperDemoApproval.from_json_bytes(args.approval_file.read_bytes())
    plan = await _build_plan(args.host_plan)
    bundle = await run_paper_demo(
        prepared=plan.prepared,
        open_permit=plan.open_permit,
        close_permit=plan.close_permit,
        approval=approval,
        attempt_store=FilePaperAttemptStore(args.attempt_store),
    )
    _write_new(args.receipt_output, bundle.to_json_bytes())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
