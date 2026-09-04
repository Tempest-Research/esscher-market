"""Host evidence run for issue #67: the complete Q-FAST lane over the frozen universe.

Drives the merged evidence machinery end-to-end on the frozen untouched 23-event
/ 7-sector universe with the promoted HOST_MEASURED latency profile:

1. source-health gate over all 23 frozen point-in-time evidence manifests;
2. append-only prospective ledger: freeze every event BEFORE any signal, then
   append all 23 signals, then hash-chain + byte verification;
3. the panel evidence report (zero + p95 arms over the identical denominator,
   abstentions retained, four PnL conventions, baselines and perturbation
   controls) without a bound release - the promotion recommendation must be an
   explicit REJECTED with reasons;
4. the full-stack shadow comparison sha-linking every panel row to #66-shaped
   service receipts.

Honest boundaries: the direction receipts and outcome bundle are SYNTHETIC
rehearsal artifacts (the raw evidence bytes and 82-bar price paths stay
host-side under METADATA_AND_HASH_ONLY on the original collection host), so the
run permanently claims NOT_ALPHA_EVIDENCE and can never recommend promotion.
Route-bound receipt generation over the 23 events requires that host-side data
and remains explicitly blocked; the live-route bridge is demonstrated
separately by scripts/generate_route_bound_receipt.py on the excluded
contract-development fixture event.

Usage:
    uv run python scripts/run_qfast_panel_evidence.py --out out/qfast-panel-evidence
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from ringdown_market.alpha.fullstack_adapter import (  # noqa: E402
    build_shadow_replay_plan,
    shadow_replay_plan_bytes,
)
from ringdown_market.alpha.fullstack_shadow_comparison import compare_fullstack  # noqa: E402
from ringdown_market.alpha.prospective_ledger import (  # noqa: E402
    ProspectiveLedger,
    verify_ledger_bytes,
)
from ringdown_market.alpha.qfast_panel_reports import (  # noqa: E402
    run_qfast_panel,
    run_source_health_gate,
)
from ringdown_market.alpha.shadow_runner import run_shadow_evaluation  # noqa: E402
from ringdown_market.contracts.latency_profile import (  # noqa: E402
    load_latency_profile,
    packaged_latency_profile_bytes,
)
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes  # noqa: E402
from test_qfast_panel_evidence import (  # noqa: E402
    FREEZE_RECORDED_AT,
    POLICY_SHA,
    _direction_map,
    _receipt_bytes_and_bundle,
    _registrations,
    _service_receipts,
    _universe,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("out/qfast-panel-evidence"))
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    profile = load_latency_profile()
    if profile.kind.value != "HOST_MEASURED":
        print(f"refusing: packaged profile kind is {profile.kind.value}", file=sys.stderr)
        return 2
    universe = _universe()
    receipts, bundle = _receipt_bytes_and_bundle()
    directions = _direction_map()

    # 1) source-health gate over the frozen 23 manifests
    gate = run_source_health_gate(
        universe.source_manifests,
        event_list_bytes=universe.event_list_bytes,
        selection_rule_bytes=universe.universe_rule_bytes,
    )
    (out / "source_health_gate.json").write_bytes(gate.bytes)
    print(
        f"source health gate: healthy={gate.healthy} events={len(gate.statuses)} sha={gate.sha256}"
    )
    if not gate.healthy:
        print("refusing: source health gate is not healthy", file=sys.stderr)
        return 3

    # 2) prospective ledger: freeze strictly before any signal.  The frozen
    # universe is historical (July-August 2026); the ledger's prospective
    # semantics only admit signals inside each event's outcome window, so this
    # rehearsal uses the fixture-schedule timestamps (freeze at the universe
    # freeze instant, signals at cutoff+5m) exactly as the contract tests do.
    # The real wall instant of THIS run is recorded in the summary (run_at) and
    # the limitations disclose the rehearsal timestamps.
    ledger_path = out / "prospective_ledger.jsonl"
    if ledger_path.exists():
        ledger_path.unlink()
    ledger = ProspectiveLedger(ledger_path)
    ledger.create_freeze_entry(events=_registrations(), recorded_at=FREEZE_RECORDED_AT)
    for event_id in universe.event_ids:
        receipt_bytes = next(raw for raw in receipts if json.loads(raw)["event_id"] == event_id)
        ledger.append_signal(
            event_id=event_id,
            direction=directions[event_id],
            decision_sha256=sha256_bytes(
                canonical_json_bytes(
                    {"direction": directions[event_id].value, "event_id": event_id}
                )
            ),
            receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
            observed_at=universe.cutoffs[event_id] + timedelta(minutes=5),
        )
    chain = ledger.verify_chain()
    ledger_raw = ledger_path.read_bytes()
    bytes_verification = verify_ledger_bytes(ledger_raw)
    ledger_sha = hashlib.sha256(ledger_raw).hexdigest()
    print(
        f"prospective ledger: entries={chain.entry_count} chain_verified={chain.valid} "
        f"bytes_verified={bytes_verification.valid} head={chain.head_sha256} sha={ledger_sha}"
    )
    if not (chain.valid and bytes_verification.valid):
        print("refusing: prospective ledger verification failed", file=sys.stderr)
        return 3

    # 3) panel evidence report (no release bound -> explicit rejection)
    report = run_qfast_panel(
        manifest_bytes=universe.panel_manifest_bytes,
        selection_rule_bytes=universe.panel_rule_bytes,
        bundle_bytes=bundle,
        receipt_bytes=receipts,
        latency_profile_bytes=packaged_latency_profile_bytes(),
        expected_policy_sha256=POLICY_SHA,
        event_list_bytes=universe.event_list_bytes,
        source_manifest_bytes_by_event=universe.source_manifests,
        universe_selection_rule_bytes=universe.universe_rule_bytes,
        window_bytes=universe.windows,
    )
    (out / "panel_report.json").write_bytes(report.bytes)
    promotion = report.promotion
    abstained = sum(1 for row in report.rows.values() if row.direction == "UNCERTAIN")
    print(
        f"panel report: status={report.status.value} rows={len(report.rows)} "
        f"abstentions={abstained}"
    )
    zero_events = report.shadow.reports["zero"].event_count
    p95_events = report.shadow.reports["p95"].event_count
    print(f"  zero arm events={zero_events} p95 arm events={p95_events}")
    print(
        f"  promotion={promotion.recommendation.value} "
        f"release={promotion.release_sha256} reasons={list(promotion.reasons)}"
    )
    print(f"  report sha={report.sha256}")
    if promotion.release_sha256 is not None:
        print("refusing: synthetic receipts must never bind a release", file=sys.stderr)
        return 3

    # 4) full-stack shadow comparison over #66-shaped service receipts
    comparison = compare_fullstack(report, _service_receipts(report))
    (out / "fullstack_comparison.json").write_bytes(comparison.bytes)
    comparison_sha = hashlib.sha256(comparison.bytes).hexdigest()
    print(f"fullstack comparison: sha={comparison_sha}")

    # 5) shadow lane: the deterministic zero/p95 shadow evaluation and (when
    # accepted) the shadow replay plan, mirroring the exact input set that
    # run_qfast_panel feeds the shadow runner (source verification over the 23
    # universe manifests is carried by the health gate above, exactly as in the
    # panel report; the shadow validator only receives the panel rule).
    shadow = run_shadow_evaluation(
        manifest_bytes=universe.panel_manifest_bytes,
        selection_rule_bytes=universe.panel_rule_bytes,
        bundle_bytes=bundle,
        receipt_bytes=receipts,
        latency_profile_bytes=packaged_latency_profile_bytes(),
        expected_policy_sha256=POLICY_SHA,
        event_list_bytes=universe.event_list_bytes,
        window_bytes=universe.windows,
    )
    (out / "qfast_shadow_report.json").write_bytes(shadow.bytes)
    (out / "evidence_validation.json").write_bytes(shadow.validation.bytes())
    plan_sha = ""
    if shadow.accepted:
        plan = build_shadow_replay_plan(
            shadow,
            candidate_id=shadow.receipts[0].candidate_id,
            policy_sha256=POLICY_SHA,
            evidence_sha256=shadow.validation.manifest_sha256,
            feature_sha256=shadow.validation.selection_rule_sha256,
            snapshot_sha256=shadow.sha256,
            costs={event: Decimal("0.000000000000") for event in shadow.symbols},
        )
        plan_bytes = shadow_replay_plan_bytes(plan)
        (out / "shadow_replay_plan.json").write_bytes(plan_bytes)
        plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    shadow_promotion = shadow.promotion_recommendation.value
    validation = shadow.validation
    print(
        f"shadow lane: accepted={shadow.accepted} classification={shadow.classification} "
        f"promotion={shadow_promotion} reasons={list(shadow.promotion_reasons)}"
    )
    print(
        f"  validation: events={validation.event_count} sectors={validation.sector_count} "
        f"source={validation.source_status} clocks={validation.clock_status} "
        f"rights={validation.rights_status} rejections={list(shadow.rejection_reasons)}"
    )
    print(f"  shadow report sha={shadow.sha256}")
    if plan_sha:
        print(f"  replay plan sha={plan_sha}")

    summary = {
        "schema": "esscher.qfast_evidence_run_summary",
        "schema_version": 1,
        "claims": ["NOT_ALPHA_EVIDENCE", "NO_BROKER_EXECUTION", "NO_CREDENTIALS", "PAPER_ONLY"],
        "run_at": _now().isoformat().replace("+00:00", "Z"),
        "latency_profile": {
            "kind": profile.kind.value,
            "p95_latency_ms": profile.p95_latency_ms,
            "observed_samples": profile.observed_samples,
            "content_sha256": profile.content_sha256,
        },
        "strategy_policy_sha256": POLICY_SHA,
        "universe": {
            "event_count": len(universe.event_ids),
            "event_ids": list(universe.event_ids),
            "panel_manifest_sha256": hashlib.sha256(universe.panel_manifest_bytes).hexdigest(),
        },
        "source_health_gate_sha256": gate.sha256,
        "prospective_ledger_sha256": ledger_sha,
        "prospective_ledger_entry_count": chain.entry_count,
        "prospective_ledger_head_sha256": chain.head_sha256,
        "prospective_ledger_chain_verified": chain.valid,
        "panel_report_sha256": report.sha256,
        "panel_report_status": report.status.value,
        "promotion_recommendation": promotion.recommendation.value,
        "promotion_reasons": list(promotion.reasons),
        "fullstack_comparison_sha256": comparison_sha,
        "shadow_report_sha256": shadow.sha256,
        "shadow_accepted": shadow.accepted,
        "shadow_classification": shadow.classification,
        "shadow_source_status": shadow.validation.source_status,
        "shadow_clock_status": shadow.validation.clock_status,
        "shadow_promotion_recommendation": shadow.promotion_recommendation.value,
        "shadow_promotion_reasons": list(shadow.promotion_reasons),
        "shadow_replay_plan_sha256": plan_sha or None,
        "limitations": [
            "DIRECTION_RECEIPTS_ARE_SYNTHETIC_REHEARSAL_ARTIFACTS",
            "OUTCOME_PATHS_ARE_SYNTHETIC_HOST_SIDE_RAW_DATA_UNAVAILABLE",
            "LEDGER_TIMESTAMPS_ARE_FIXTURE_SCHEDULE_REHEARSAL_VALUES",
            "ROUTE_BOUND_GENERATION_OVER_THE_23_EVENTS_REMAINS_BLOCKED_ON_THE_ORIGINAL_COLLECTION_HOST",
            "NOT_ALPHA_EVIDENCE",
        ],
    }
    summary_bytes = canonical_json_bytes(summary)
    (out / "evidence_run_summary.json").write_bytes(summary_bytes + b"\n")
    print(f"evidence run summary sha={hashlib.sha256(summary_bytes).hexdigest()}")
    print(f"artifacts written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
