from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta

import pytest

from ringdown_market.runtime.autonomous import (
    AutonomousArmRejected,
    AutonomousClaimState,
    AutonomousDisposition,
    AutonomousOpportunity,
    AutonomousSessionArm,
    AutonomousSessionCoordinator,
    AutonomousSessionPorts,
    AutonomousSessionStore,
    AutonomousStoreConflict,
    CandidateProcessingResult,
    LifecycleCloseResult,
    MutationState,
    ReconciliationReceipt,
    autonomous_session_arm_bytes,
    parse_autonomous_session_arm,
)

V2_CANDIDATES = (
    "EARNINGS_RESIDUAL_CONTINUATION_V2",
    "MARKET_ANCHOR_INTRADAY_CONTINUATION_V1",
    "LIQUID_STOCK_CATALYST_CONTINUATION_V1",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _arm(*, release_code_sha256: str = "a" * 64, account_fingerprint_sha256: str = "b" * 64):
    return AutonomousSessionArm.for_trading_date(
        session_id="ESSCHER-20260901",
        session_date=date(2026, 9, 1),
        release_code_sha256=release_code_sha256,
        account_fingerprint_sha256=account_fingerprint_sha256,
    )


def _opportunity(
    arm: AutonomousSessionArm,
    *,
    opportunity_id: str = "OPPORTUNITY-ONE",
    strategy_context_sha256: str = "c" * 64,
    candidate_id: str | None = None,
) -> AutonomousOpportunity:
    return AutonomousOpportunity.for_window(
        arm=arm,
        window_id=arm.windows[0].window_id,
        opportunity_id=opportunity_id,
        strategy_context_sha256=strategy_context_sha256,
        candidate_id=candidate_id,
    )


def test_arm_is_closed_canonical_paper_only_and_semantically_bound_to_frozen_contracts() -> None:
    arm = _arm()

    raw = autonomous_session_arm_bytes(arm)
    payload = json.loads(raw)

    assert raw == _canonical(payload)
    assert parse_autonomous_session_arm(raw) == arm
    assert payload["arm_sha256"] == arm.arm_sha256
    assert payload["mode"] == "PAPER"
    assert "trade_count_cap" not in payload
    assert (
        tuple(tuple(window["candidate_ids"]) for window in payload["windows"])
        == (V2_CANDIDATES,) * 6
    )

    with pytest.raises(AutonomousArmRejected):
        parse_autonomous_session_arm(raw + b"\n")

    forged = dict(payload)
    forged["mode"] = "LIVE"
    forged["arm_sha256"] = hashlib.sha256(
        _canonical({key: value for key, value in forged.items() if key != "arm_sha256"})
    ).hexdigest()
    with pytest.raises(AutonomousArmRejected):
        parse_autonomous_session_arm(_canonical(forged))

    forged = dict(payload)
    forged["trade_count_cap"] = 1
    forged["arm_sha256"] = hashlib.sha256(
        _canonical({key: value for key, value in forged.items() if key != "arm_sha256"})
    ).hexdigest()
    with pytest.raises(AutonomousArmRejected):
        parse_autonomous_session_arm(_canonical(forged))

    forged = dict(payload)
    forged["owner_policy_sha256"] = "0" * 64
    forged["arm_sha256"] = hashlib.sha256(
        _canonical({key: value for key, value in forged.items() if key != "arm_sha256"})
    ).hexdigest()
    with pytest.raises(AutonomousArmRejected):
        parse_autonomous_session_arm(_canonical(forged))


def test_sqlite_store_persists_arm_windows_and_claims_one_exact_opportunity_once(tmp_path) -> None:
    arm = _arm()
    opportunity = _opportunity(arm)
    state_path = tmp_path / "coordinator.sqlite3"
    store = AutonomousSessionStore(state_path)

    assert (
        store.claim_opportunity(
            arm=arm,
            opportunity=opportunity,
            observed_at=arm.windows[0].opens_at,
        )
        is AutonomousClaimState.CLAIMED
    )
    store.record_terminal_flat(
        arm=arm,
        opportunity=opportunity,
        terminal_flat_proof_sha256="d" * 64,
        observed_at=arm.windows[0].opens_at,
    )
    store.close()

    restarted = AutonomousSessionStore(state_path)
    assert restarted.load_arm(arm.session_id) == arm
    assert restarted.window_ids(arm.session_id) == tuple(window.window_id for window in arm.windows)
    assert (
        restarted.opportunity_state(arm.session_id, opportunity.opportunity_id) == "TERMINAL_FLAT"
    )
    assert (
        restarted.claim_opportunity(
            arm=arm,
            opportunity=opportunity,
            observed_at=arm.windows[0].opens_at,
        )
        is AutonomousClaimState.ALREADY_RECORDED
    )
    with pytest.raises(AutonomousStoreConflict):
        restarted.claim_opportunity(
            arm=arm,
            opportunity=_opportunity(arm, strategy_context_sha256="e" * 64),
            observed_at=arm.windows[0].opens_at,
        )
    restarted.close()

    concurrent_path = tmp_path / "concurrent.sqlite3"

    def claim_once() -> AutonomousClaimState:
        concurrent_store = AutonomousSessionStore(concurrent_path)
        try:
            return concurrent_store.claim_opportunity(
                arm=arm,
                opportunity=opportunity,
                observed_at=arm.windows[0].opens_at,
            )
        finally:
            concurrent_store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = tuple(executor.map(lambda _: claim_once(), range(2)))

    assert claims.count(AutonomousClaimState.CLAIMED) == 1
    assert claims.count(AutonomousClaimState.IN_PROGRESS) == 1


class _CompleteReconciler:
    def __init__(self) -> None:
        self.calls = []

    def reconcile(self, request):
        self.calls.append(request)
        return ReconciliationReceipt.complete(request=request)


class _StaticCollector:
    def __init__(self, opportunities: tuple[AutonomousOpportunity, ...]) -> None:
        self.opportunities = opportunities
        self.calls = []

    def collect_due(self, request):
        self.calls.append(request)
        return self.opportunities


class _TerminalProcessor:
    def __init__(self, *, abstain_id: str) -> None:
        self.abstain_id = abstain_id
        self.calls: list[str] = []

    def process(self, request):
        opportunity_id = request.opportunity.opportunity_id
        self.calls.append(opportunity_id)
        if opportunity_id == self.abstain_id:
            return CandidateProcessingResult.abstained(
                request=request,
                reason_code="PROVIDER_TIMEOUT_BEFORE_MUTATION",
            )
        return CandidateProcessingResult.terminal_flat(
            request=request,
            terminal_flat_proof_sha256=(opportunity_id[-2:] * 32),
        )


class _NoopCloser:
    def close_and_reconcile(self, request):
        raise AssertionError(f"unexpected hard-flat call for {request.lifecycle_id}")


def test_coordinator_processes_every_due_three_lane_candidate_without_a_count_cap_or_replay(
    tmp_path,
) -> None:
    arm = _arm()
    candidates = (
        _opportunity(
            arm,
            opportunity_id="OPPORTUNITY-01",
            strategy_context_sha256="01" * 32,
        ),
        _opportunity(
            arm,
            opportunity_id="OPPORTUNITY-02",
            strategy_context_sha256="02" * 32,
            candidate_id=V2_CANDIDATES[1],
        ),
        _opportunity(
            arm,
            opportunity_id="OPPORTUNITY-03",
            strategy_context_sha256="03" * 32,
            candidate_id=V2_CANDIDATES[2],
        ),
        _opportunity(
            arm,
            opportunity_id="OPPORTUNITY-04",
            strategy_context_sha256="04" * 32,
        ),
        _opportunity(
            arm,
            opportunity_id="OPPORTUNITY-05",
            strategy_context_sha256="05" * 32,
            candidate_id=V2_CANDIDATES[2],
        ),
    )
    reconciler = _CompleteReconciler()
    collector = _StaticCollector((candidates[2], candidates[0], candidates[0], *candidates[1:]))
    processor = _TerminalProcessor(abstain_id="OPPORTUNITY-03")
    ports = AutonomousSessionPorts(
        reconciler=reconciler,
        collector=collector,
        processor=processor,
        lifecycle_closer=_NoopCloser(),
    )
    state_path = tmp_path / "session.sqlite3"
    store = AutonomousSessionStore(state_path)
    coordinator = AutonomousSessionCoordinator(
        arm=arm,
        store=store,
        ports=ports,
        release_code_sha256=arm.release_code_sha256,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
    )

    first = coordinator.run(observed_at=arm.windows[0].opens_at)

    assert processor.calls == [opportunity.opportunity_id for opportunity in candidates]
    assert first.disposition_counts[AutonomousDisposition.TERMINAL_FLAT] == 4
    assert first.disposition_counts[AutonomousDisposition.ABSTAINED] == 1
    assert first.disposition_counts[AutonomousDisposition.ACTIVE] == 0
    assert first.manual_reasons == ()

    repeated = coordinator.run(observed_at=arm.windows[0].opens_at)
    assert repeated.processed_opportunity_ids == ()
    assert processor.calls == [opportunity.opportunity_id for opportunity in candidates]
    store.close()

    restarted_store = AutonomousSessionStore(state_path)
    restarted = AutonomousSessionCoordinator(
        arm=arm,
        store=restarted_store,
        ports=ports,
        release_code_sha256=arm.release_code_sha256,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
    )
    after_restart = restarted.run(observed_at=arm.windows[0].opens_at)

    assert after_restart.processed_opportunity_ids == ()
    assert processor.calls == [opportunity.opportunity_id for opportunity in candidates]
    restarted_store.close()


class _UnknownMutationProcessor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def process(self, request):
        self.calls.append(request.opportunity.opportunity_id)
        return CandidateProcessingResult.manual_reconciliation_required(
            request=request,
            mutation_state=MutationState.UNKNOWN,
            reason_code="PROVIDER_TIMEOUT_UNKNOWN_MUTATION",
        )


def test_unknown_or_partial_mutation_stops_later_new_exposure_and_persists_manual_state(
    tmp_path,
) -> None:
    arm = _arm()
    first = _opportunity(
        arm,
        opportunity_id="OPPORTUNITY-01-UNKNOWN",
        strategy_context_sha256="09" * 32,
    )
    later = _opportunity(
        arm,
        opportunity_id="OPPORTUNITY-02-LATER",
        strategy_context_sha256="08" * 32,
        candidate_id=V2_CANDIDATES[1],
    )
    processor = _UnknownMutationProcessor()
    ports = AutonomousSessionPorts(
        reconciler=_CompleteReconciler(),
        collector=_StaticCollector((first, later)),
        processor=processor,
        lifecycle_closer=_NoopCloser(),
    )
    store = AutonomousSessionStore(tmp_path / "manual.sqlite3")
    coordinator = AutonomousSessionCoordinator(
        arm=arm,
        store=store,
        ports=ports,
        release_code_sha256=arm.release_code_sha256,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
    )

    result = coordinator.run(observed_at=arm.windows[0].opens_at)

    assert processor.calls == ["OPPORTUNITY-01-UNKNOWN"]
    assert result.disposition_counts[AutonomousDisposition.MANUAL_RECONCILIATION_REQUIRED] == 1
    assert result.manual_reasons == ("PROVIDER_TIMEOUT_UNKNOWN_MUTATION",)

    repeated = coordinator.run(observed_at=arm.windows[0].opens_at)
    assert repeated.processed_opportunity_ids == ()
    assert processor.calls == ["OPPORTUNITY-01-UNKNOWN"]
    store.close()


class _ActiveProcessor:
    def __init__(self, lifecycle_ids: dict[str, str]) -> None:
        self.lifecycle_ids = lifecycle_ids
        self.calls: list[str] = []

    def process(self, request):
        opportunity_id = request.opportunity.opportunity_id
        self.calls.append(opportunity_id)
        return CandidateProcessingResult.active(
            request=request,
            lifecycle_id=self.lifecycle_ids[opportunity_id],
        )


class _TerminalCloser:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def close_and_reconcile(self, request):
        self.calls.append(request.lifecycle.lifecycle_id)
        return LifecycleCloseResult.terminal_flat(
            request=request,
            terminal_flat_proof_sha256=(request.lifecycle.lifecycle_id[-2:] * 32),
        )


def test_hard_flat_closes_every_active_lifecycle_in_deterministic_order_and_finalizes(
    tmp_path,
) -> None:
    arm = _arm()
    opportunities = tuple(
        _opportunity(
            arm,
            opportunity_id=f"OPPORTUNITY-ACTIVE-{index:02d}",
            strategy_context_sha256=f"{index:02d}" * 32,
            candidate_id=V2_CANDIDATES[index - 1],
        )
        for index in range(1, 4)
    )
    lifecycle_ids = {
        "OPPORTUNITY-ACTIVE-01": "LIFECYCLE-03",
        "OPPORTUNITY-ACTIVE-02": "LIFECYCLE-01",
        "OPPORTUNITY-ACTIVE-03": "LIFECYCLE-02",
    }
    closer = _TerminalCloser()
    processor = _ActiveProcessor(lifecycle_ids)
    ports = AutonomousSessionPorts(
        reconciler=_CompleteReconciler(),
        collector=_StaticCollector(opportunities),
        processor=processor,
        lifecycle_closer=closer,
    )
    store = AutonomousSessionStore(tmp_path / "hard-flat.sqlite3")
    coordinator = AutonomousSessionCoordinator(
        arm=arm,
        store=store,
        ports=ports,
        release_code_sha256=arm.release_code_sha256,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
    )

    opened = coordinator.run(observed_at=arm.windows[0].opens_at)
    assert opened.disposition_counts[AutonomousDisposition.ACTIVE] == 3
    assert processor.calls == [opportunity.opportunity_id for opportunity in opportunities]

    flattened = coordinator.run(observed_at=arm.hard_flat_at)

    assert closer.calls == ["LIFECYCLE-01", "LIFECYCLE-02", "LIFECYCLE-03"]
    assert flattened.disposition_counts[AutonomousDisposition.ACTIVE] == 0
    assert flattened.disposition_counts[AutonomousDisposition.TERMINAL_FLAT] == 3
    summary = store.final_summary(arm.session_id)
    assert summary is not None
    assert summary.terminal_flat_proven is True
    assert summary.manual_reasons == ()

    after_flat = coordinator.run(observed_at=arm.hard_flat_at)
    assert after_flat.processed_opportunity_ids == ()
    assert processor.calls == [opportunity.opportunity_id for opportunity in opportunities]
    store.close()


class _OmittingActiveReconciler:
    def __init__(self) -> None:
        self.calls = []

    def reconcile(self, request):
        self.calls.append(request)
        assert request.active_lifecycle_ids
        return replace(
            ReconciliationReceipt.complete(request=request),
            active_lifecycle_ids=(),
        )


def test_reconciliation_that_omits_a_persisted_active_lifecycle_fails_closed(tmp_path) -> None:
    arm = _arm()
    opportunity = _opportunity(arm, opportunity_id="OPPORTUNITY-ACTIVE-OMITTED")
    store = AutonomousSessionStore(tmp_path / "omitted-active.sqlite3")
    opening_ports = AutonomousSessionPorts(
        reconciler=_CompleteReconciler(),
        collector=_StaticCollector((opportunity,)),
        processor=_ActiveProcessor({opportunity.opportunity_id: "LIFECYCLE-OMITTED"}),
        lifecycle_closer=_NoopCloser(),
    )
    AutonomousSessionCoordinator(
        arm=arm,
        store=store,
        ports=opening_ports,
        release_code_sha256=arm.release_code_sha256,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
    ).run(observed_at=arm.windows[0].opens_at)

    omitting = _OmittingActiveReconciler()
    collector = _StaticCollector(())
    result = AutonomousSessionCoordinator(
        arm=arm,
        store=store,
        ports=AutonomousSessionPorts(
            reconciler=omitting,
            collector=collector,
            processor=_TerminalProcessor(abstain_id="NEVER"),
            lifecycle_closer=_NoopCloser(),
        ),
        release_code_sha256=arm.release_code_sha256,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
    ).run(observed_at=arm.windows[0].opens_at + timedelta(minutes=1))

    assert collector.calls == []
    assert result.manual_reasons == ("RECONCILIATION_IDENTITY_MISMATCH",)
    store.close()


def test_restart_with_an_in_progress_claim_freezes_instead_of_reprocessing_or_claiming_flat(
    tmp_path,
) -> None:
    arm = _arm()
    opportunity = _opportunity(arm, opportunity_id="OPPORTUNITY-CRASHED-CLAIM")
    state_path = tmp_path / "crashed-claim.sqlite3"
    store = AutonomousSessionStore(state_path)
    assert (
        store.claim_opportunity(
            arm=arm,
            opportunity=opportunity,
            observed_at=arm.windows[0].opens_at,
        )
        is AutonomousClaimState.CLAIMED
    )
    store.close()

    restarted = AutonomousSessionStore(state_path)
    reconciler = _CompleteReconciler()
    collector = _StaticCollector((opportunity,))
    processor = _TerminalProcessor(abstain_id="NEVER")
    result = AutonomousSessionCoordinator(
        arm=arm,
        store=restarted,
        ports=AutonomousSessionPorts(
            reconciler=reconciler,
            collector=collector,
            processor=processor,
            lifecycle_closer=_NoopCloser(),
        ),
        release_code_sha256=arm.release_code_sha256,
        account_fingerprint_sha256=arm.account_fingerprint_sha256,
    ).run(observed_at=arm.windows[0].opens_at + timedelta(minutes=1))

    assert reconciler.calls == []
    assert collector.calls == []
    assert processor.calls == []
    assert result.manual_reasons == ("CLAIM_RECOVERY_UNKNOWN",)
    restarted.close()
