"""Issue #43: deterministic feature-receipt boundary tests.

Every acceptance criterion maps to named fail-closed tests: strict AC1
bindings, preregistered-feature enforcement, stable input-failure reasons,
BMO/AMC/macro clock separation, byte determinism, and negative capability
proofs (no network, LLM, account, order, position, or broker surface).
"""

from __future__ import annotations

import ast
import copy
import json
import socket
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from esscher.sourcedata import compiler as compiler_module
from esscher.sourcedata.compiler import (
    EARNINGS_CANDIDATE,
    CaptureConfiguration,
    compile_strategy_snapshot,
)
from esscher.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
    load_fixture,
)
from esscher.sourcedata.reasons import CollectorReason, CollectorRejected
from esscher.strategy.contracts import (
    FEATURE_RECEIPT_SCHEMA,
    StrategyContractRejected,
    feature_receipt_bytes,
    parse_feature_receipt,
)
from esscher.strategy.models import (
    FeatureReceipt,
    FeatureStatus,
    FeatureValue,
    FeatureValueType,
)
from esscher.strategy.policy import load_strategy_policy

EVENT_ID = "KR-2026Q2-EARNINGS"
CAPTURE_AT = "2026-09-11T13:35:10Z"
LINEAGE_SHA = "a" * 64


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _configuration(fixture, *, capture_at: str = CAPTURE_AT, lineage_sha: str | None = None):
    return CaptureConfiguration(
        candidate_manifest_bytes=build_candidate_manifest(fixture),
        event_id=EVENT_ID,
        capture_at=_at(capture_at),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
        lineage_receipt_sha256=lineage_sha,
    )


def _compile(fixture=None, *, capture_at: str = CAPTURE_AT, lineage_sha: str | None = None):
    fixture = fixture if fixture is not None else load_fixture()
    return compile_strategy_snapshot(
        _configuration(fixture, capture_at=capture_at, lineage_sha=lineage_sha),
        FixtureEvidenceSource(fixture),
        FixtureMarketDataSource(fixture),
    )


@pytest.fixture(scope="module")
def compiled():
    return _compile()


# ---------------------------------------------------------------------------
# AC1: strict receipt bindings
# ---------------------------------------------------------------------------


def test_ac1_receipt_binds_snapshot_policy_build_cutoff_evidence_and_health(compiled) -> None:
    receipt = compiled.feature_receipt
    from esscher.strategy.contracts import sha256_bytes

    assert receipt.strategy_snapshot_sha256 == sha256_bytes(compiled.strategy_snapshot_bytes)
    assert receipt.policy_sha256 == compiled.snapshot.policy_sha256
    assert receipt.producer_build_sha256 == compiled.snapshot.producer_build_sha256
    assert receipt.decision_cutoff_at == compiled.snapshot.decision_cutoff_at
    assert receipt.data_health is compiled.snapshot.data_health
    assert receipt.health_reason_codes == compiled.snapshot.health_reason_codes
    assert receipt.cohort_id == compiled.snapshot.cohort_id
    feature_ids = tuple(feature.feature_id for feature in receipt.features)
    assert feature_ids == tuple(sorted(feature_ids))
    for feature in receipt.features:
        if feature.status is FeatureStatus.PRESENT:
            assert isinstance(feature.value, (Decimal, int)) or feature.value is not None
            assert feature.unit


def test_ac1_maximum_public_timestamp_is_max_of_evidence_publications(compiled) -> None:
    published = tuple(
        receipt.published_at
        for receipt in compiled.evidence_packet.receipts
        if receipt.published_at is not None
    )
    assert published
    assert compiled.feature_receipt.maximum_public_timestamp == max(published)
    assert compiled.feature_receipt.maximum_public_timestamp <= (
        compiled.feature_receipt.decision_cutoff_at
    )


def test_ac1_evidence_ids_bind_packet_and_lineage_identity() -> None:
    baseline = _compile()
    packet_ids = set(baseline.evidence_packet.evidence_ids())
    assert packet_ids <= set(baseline.feature_receipt.evidence_ids)
    assert baseline.feature_receipt.lineage_receipt_sha256 is None
    bound = _compile(lineage_sha=LINEAGE_SHA)
    assert bound.feature_receipt.lineage_receipt_sha256 == LINEAGE_SHA
    assert f"LINEAGE_RECEIPT:{LINEAGE_SHA}" in bound.feature_receipt.evidence_ids


def test_ac1_receipt_round_trips_through_strict_parser(compiled) -> None:
    raw = compiled.feature_receipt_bytes
    parsed = parse_feature_receipt(raw)
    assert parsed == compiled.feature_receipt
    assert feature_receipt_bytes(parsed) == raw
    payload = json.loads(raw.decode("utf-8"))
    assert payload["schema"] == FEATURE_RECEIPT_SCHEMA


def test_ac1_parser_rejects_unknown_missing_and_duplicate_fields(compiled) -> None:
    payload = json.loads(compiled.feature_receipt_bytes.decode("utf-8"))
    unknown = dict(payload)
    unknown["discretionary_extra"] = 1
    with pytest.raises(StrategyContractRejected):
        parse_feature_receipt(json.dumps(unknown, sort_keys=True).encode("utf-8"))
    missing = dict(payload)
    missing.pop("maximum_public_timestamp")
    with pytest.raises(StrategyContractRejected):
        parse_feature_receipt(json.dumps(missing, sort_keys=True).encode("utf-8"))
    raw = compiled.feature_receipt_bytes.decode("utf-8")
    duplicated = raw.replace(
        '"event_id":"KR-2026Q2-EARNINGS"',
        '"event_id":"KR-2026Q2-EARNINGS","event_id":"KR-2026Q2-EARNINGS"',
        1,
    )
    assert duplicated != raw
    with pytest.raises(StrategyContractRejected):
        parse_feature_receipt(duplicated.encode("utf-8"))


def test_ac1_receipt_model_rejects_late_public_evidence() -> None:
    from datetime import timedelta

    receipt = _compile().feature_receipt
    with pytest.raises(ValueError):
        FeatureReceipt(
            event_id=receipt.event_id,
            candidate_id=receipt.candidate_id,
            cohort_id=receipt.cohort_id,
            policy_sha256=receipt.policy_sha256,
            strategy_snapshot_sha256=receipt.strategy_snapshot_sha256,
            producer_build_sha256=receipt.producer_build_sha256,
            created_at=receipt.created_at,
            feature_snapshot_at=receipt.feature_snapshot_at,
            decision_cutoff_at=receipt.decision_cutoff_at,
            maximum_public_timestamp=receipt.decision_cutoff_at + timedelta(seconds=1),
            data_health=receipt.data_health,
            health_reason_codes=receipt.health_reason_codes,
            evidence_ids=receipt.evidence_ids,
            lineage_receipt_sha256=None,
            features=receipt.features,
        )


def test_ac1_configuration_rejects_malformed_lineage_identity() -> None:
    fixture = load_fixture()
    with pytest.raises(CollectorRejected) as caught:
        _configuration(fixture, lineage_sha="not-a-sha")
    assert caught.value.reason is CollectorReason.UNSUPPORTED_INPUT


# ---------------------------------------------------------------------------
# AC2: only preregistered features; no hidden imputation
# ---------------------------------------------------------------------------


def test_ac2_compiled_features_exactly_match_preregistered_registry(compiled) -> None:
    policy = load_strategy_policy()
    registered = tuple(
        sorted(str(item["feature_id"]) for item in policy.candidate(EARNINGS_CANDIDATE)["features"])
    )
    compiled_ids = tuple(feature.feature_id for feature in compiled.feature_receipt.features)
    assert compiled_ids == registered
    assert len(compiled_ids) == 13


def test_ac2_macro_registry_is_preregistered_and_disjoint() -> None:
    policy = load_strategy_policy()
    earnings_ids = {
        str(item["feature_id"]) for item in policy.candidate(EARNINGS_CANDIDATE)["features"]
    }
    macro_ids = {
        str(item["feature_id"])
        for item in policy.candidate("MACRO_SPY_CONTINUATION_CHALLENGER_V1")["features"]
    }
    assert len(macro_ids) == 20
    assert earnings_ids.isdisjoint(macro_ids)
    assert all(feature_id.endswith(".v1") for feature_id in earnings_ids | macro_ids)


def test_ac2_discretionary_feature_fails_closed(monkeypatch) -> None:
    original = compiler_module.build_earnings_features

    def injected(inputs):
        features = list(original(inputs))
        features.append(
            FeatureValue(
                feature_id="zz.discretionary_indicator.v1",
                status=FeatureStatus.UNAVAILABLE,
                value=None,
                value_type=FeatureValueType.DECIMAL_STRING,
                unit="RATIO",
                observed_at=None,
                source_refs=(),
            )
        )
        return tuple(sorted(features, key=lambda item: item.feature_id))

    monkeypatch.setattr(compiler_module, "build_earnings_features", injected)
    with pytest.raises(CollectorRejected) as caught:
        _compile()
    assert caught.value.reason is CollectorReason.FEATURE_REGISTRY_MISMATCH


def test_ac2_unavailable_features_are_never_imputed(compiled) -> None:
    consensus = next(
        feature
        for feature in compiled.feature_receipt.features
        if feature.feature_id == "earnings.eps_consensus_surprise_pct.v1"
    )
    assert consensus.status is FeatureStatus.UNAVAILABLE
    assert consensus.value is None
    assert consensus.observed_at is None


# ---------------------------------------------------------------------------
# AC3: bad inputs fail closed with stable reasons
# ---------------------------------------------------------------------------


def test_ac3_stale_capture_clock_fails_closed() -> None:
    with pytest.raises(CollectorRejected) as caught:
        _compile(capture_at="2026-09-11T14:00:00Z")
    assert caught.value.reason is CollectorReason.RETRIEVED_AFTER_CUTOFF


def test_ac3_missing_feature_dependency_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["issuer_release"]["quarter_history"] = fixture["issuer_release"]["quarter_history"][:1]
    with pytest.raises(CollectorRejected) as caught:
        _compile(fixture)
    assert caught.value.reason is CollectorReason.FEATURE_DEPENDENCY_MISSING


def test_ac3_non_finite_feature_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    for quarter in fixture["issuer_release"]["quarter_history"]:
        quarter["eps_diluted"] = "1.00"
    fixture["issuer_release"]["current_quarter"]["eps_diluted"] = "1.00"
    with pytest.raises(CollectorRejected) as caught:
        _compile(fixture)
    assert caught.value.reason is CollectorReason.NON_FINITE_FEATURE


def test_ac3_adjustment_mismatch_fails_closed() -> None:
    from datetime import date as _date

    from esscher.sourcedata.adjustments import adjust_series
    from esscher.sourcedata.interfaces import (
        CorporateAction,
        DailyBar,
        SourceProvenance,
    )

    def split_action() -> CorporateAction:
        return CorporateAction(
            ticker="KR",
            action_type="SPLIT",
            ex_date=_date(2026, 3, 13),
            ratio_numerator=2,
            ratio_denominator=1,
            symbol_from=None,
            symbol_to=None,
            provenance=SourceProvenance(
                source_class="CORPORATE_ACTION_RECORD",
                publisher="SYNTHETIC_SECURITY_MASTER_FEED",
                content_sha256="c" * 64,
                published_at=None,
                published_at_precision="DATE",
                retrieved_at=_at("2026-09-11T11:00:00Z"),
                entitlement="ENTITLED",
                redistribution_status="NON_REDISTRIBUTABLE",
                limitations=("LICENSED_REFERENCE_DATA",),
            ),
        )

    bars = (
        DailyBar("KR", "XNYS-2026-03-12", _date(2026, 3, 12), Decimal("120.00"), 1, True),
        DailyBar("KR", "XNYS-2026-03-13", _date(2026, 3, 13), Decimal("60.00"), 1, True),
    )
    with pytest.raises(CollectorRejected) as caught:
        adjust_series(bars, (split_action(), split_action()), ticker="KR", receipts_by_action={})
    assert caught.value.reason is CollectorReason.ADJUSTMENT_POLICY_VIOLATION


def test_ac3_stable_reason_vocabulary_is_registered() -> None:
    required = {
        "FEATURE_DEPENDENCY_MISSING",
        "NON_FINITE_FEATURE",
        "MARKET_OBSERVATION_STALE",
        "MARKET_OBSERVATION_ASYNCHRONOUS",
        "ADJUSTMENT_POLICY_VIOLATION",
        "UNSUPPORTED_INPUT",
        "FEATURE_REGISTRY_MISMATCH",
        "MAXIMUM_PUBLIC_TIMESTAMP_AFTER_CUTOFF",
    }
    have = {member.name for member in CollectorReason}
    assert required <= have


# ---------------------------------------------------------------------------
# AC4: BMO, AMC, and macro clocks cannot be mixed
# ---------------------------------------------------------------------------


def test_ac4_receipt_cohort_matches_manifest_and_frozen_vocabulary(compiled) -> None:
    fixture = load_fixture()
    record = fixture["candidate_manifest"]["records"][0]
    assert compiled.feature_receipt.cohort_id == record["cohort_id"]
    assert compiled.feature_receipt.cohort_id in {"BMO", "AMC"}


def test_ac4_flipping_cohort_clock_fails_closed() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["candidate_manifest"]["records"][0]["cohort_id"] = "AMC"
    with pytest.raises(CollectorRejected) as caught:
        _compile(fixture)
    assert caught.value.reason is CollectorReason.CLOCK_MISMATCH


def test_ac4_macro_cohort_cannot_drive_earnings_clocks() -> None:
    fixture = copy.deepcopy(load_fixture())
    fixture["candidate_manifest"]["records"][0]["cohort_id"] = "BLS_JOLTS"
    with pytest.raises(CollectorRejected) as caught:
        _compile(fixture)
    assert caught.value.reason is CollectorReason.TIMING_BUCKET_UNKNOWN


# ---------------------------------------------------------------------------
# AC5: byte determinism
# ---------------------------------------------------------------------------


def test_ac5_identical_inputs_produce_byte_identical_receipts(compiled) -> None:
    rerun = _compile()
    assert rerun.feature_receipt_bytes == compiled.feature_receipt_bytes
    assert rerun.strategy_snapshot_bytes == compiled.strategy_snapshot_bytes


def test_ac5_receipt_bytes_change_with_lineage_identity() -> None:
    baseline = _compile()
    bound = _compile(lineage_sha=LINEAGE_SHA)
    assert bound.feature_receipt_bytes != baseline.feature_receipt_bytes


def test_ac5_receipt_bytes_change_with_capture_clock() -> None:
    baseline = _compile()
    shifted = _compile(capture_at="2026-09-11T13:35:09Z")
    assert shifted.feature_receipt_bytes != baseline.feature_receipt_bytes


# ---------------------------------------------------------------------------
# AC6: negative capability proofs
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORT_PREFIXES = (
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "openai",
    "anthropic",
    "transformers",
    "alpaca",
)


def _package_modules(package_name: str) -> list[Path]:
    package_root = Path(compiler_module.__file__).resolve().parent.parent / package_name
    return sorted(package_root.rglob("*.py"))


def test_ac6_compiler_boundary_has_no_network_llm_or_broker_imports() -> None:
    offenders: list[str] = []
    for module_path in _package_modules("sourcedata") + _package_modules("strategy"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(
                    name == forbidden or name.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_IMPORT_PREFIXES
                ):
                    offenders.append(f"{module_path.name}: {name}")
    assert offenders == []


def test_ac6_receipt_compilation_runs_with_socket_disabled(monkeypatch) -> None:
    def deny(*args, **kwargs):
        raise RuntimeError("network is disabled for feature compilation")

    monkeypatch.setattr(socket, "socket", deny)
    compiled_offline = _compile()
    assert compiled_offline.feature_receipt_bytes == _compile().feature_receipt_bytes


def test_ac6_receipt_carries_no_execution_authority_fields(compiled) -> None:
    payload = json.loads(compiled.feature_receipt_bytes.decode("utf-8"))
    forbidden = {
        "quantity",
        "price",
        "order",
        "account",
        "permit",
        "broker",
        "instrument",
        "contract",
        "entry",
        "exit",
    }
    assert forbidden.isdisjoint(payload.keys())


def test_ac1_macro_compiler_binds_complete_feature_provenance_without_hidden_fields() -> None:
    """Direct macro compilation has the same explicit receipt boundary as earnings."""

    from esscher.sourcedata.compiler import compile_macro_snapshot
    from esscher.sourcedata.fakes import (
        FixtureMacroEvidenceSource,
        FixtureMacroMarketDataSource,
        FixtureMacroReleaseSource,
        build_macro_candidate_manifest,
        load_macro_fixture,
    )
    from esscher.strategy.contracts import sha256_bytes

    fixture = load_macro_fixture()
    configuration = CaptureConfiguration(
        candidate_manifest_bytes=build_macro_candidate_manifest(fixture),
        event_id="BLS-JOLTS-2026-07",
        capture_at=_at("2026-09-09T14:15:10Z"),
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
        lineage_receipt_sha256=LINEAGE_SHA,
    )
    evidence = FixtureMacroEvidenceSource(fixture)
    compiled_macro = compile_macro_snapshot(
        configuration,
        evidence.sessions,
        FixtureMacroReleaseSource(fixture),
        FixtureMacroMarketDataSource(fixture),
    )
    receipt = compiled_macro.feature_receipt
    payload = json.loads(compiled_macro.feature_receipt_bytes.decode("utf-8"))

    assert receipt.strategy_snapshot_sha256 == sha256_bytes(compiled_macro.strategy_snapshot_bytes)
    assert receipt.policy_sha256 == compiled_macro.snapshot.policy_sha256
    assert receipt.producer_build_sha256 == compiled_macro.snapshot.producer_build_sha256
    assert receipt.decision_cutoff_at == compiled_macro.snapshot.decision_cutoff_at
    assert receipt.maximum_public_timestamp == max(
        item.published_at
        for item in compiled_macro.evidence_packet.receipts
        if item.published_at is not None
    )
    assert receipt.lineage_receipt_sha256 == LINEAGE_SHA
    assert receipt.evidence_ids == tuple(sorted(set(receipt.evidence_ids)))
    assert f"LINEAGE_RECEIPT:{LINEAGE_SHA}" in receipt.evidence_ids
    assert parse_feature_receipt(compiled_macro.feature_receipt_bytes) == receipt
    assert set(payload) == {
        "candidate_id",
        "cohort_id",
        "created_at",
        "data_health",
        "decision_cutoff_at",
        "event_id",
        "evidence_ids",
        "feature_snapshot_at",
        "features",
        "health_reason_codes",
        "lineage_receipt_sha256",
        "maximum_public_timestamp",
        "policy_sha256",
        "producer_build_sha256",
        "schema",
        "schema_version",
        "strategy_snapshot_sha256",
    }
