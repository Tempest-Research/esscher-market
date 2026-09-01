from __future__ import annotations

import json
from datetime import date

import pytest

from ringdown_market.contracts.security_lineage import (
    ACTION_TYPES,
    CROSS_SERIES_RULE,
    LINEAGE_ID,
    OPTION_ADJUSTMENT_AUTHORITY,
    PRICE_ADJUSTING_ACTIONS,
    RECORD_ONLY_ACTIONS,
    SCHEMA_ID,
    SECURITY_LINEAGE_V1_SHA256,
    LineageReason,
    LineageRejected,
    load_security_lineage,
    parse_security_lineage,
    resolve_lineage,
    security_lineage_bytes,
    verify_lineage_upstream_bindings,
)
from ringdown_market.contracts.source_matrix import source_matrix_bytes
from ringdown_market.strategy.policy import strategy_policy_bytes

P0_EVENTS = (
    "KR-2026Q2-EARNINGS",
    "GIS-2027Q1-EARNINGS",
    "MU-2026Q4-EARNINGS",
    "NKE-2027Q1-EARNINGS",
)


def _raw_dict() -> dict:
    return json.loads(security_lineage_bytes().decode("utf-8"))


def _mutated_bytes(mutate) -> bytes:
    payload = _raw_dict()
    mutate(payload)
    return json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")


def test_packaged_lineage_digest_matches_frozen_constant() -> None:
    import hashlib

    assert hashlib.sha256(security_lineage_bytes()).hexdigest() == SECURITY_LINEAGE_V1_SHA256


def test_packaged_lineage_file_matches_packaged_bytes() -> None:
    from pathlib import Path

    packaged = (
        Path(__file__).parent.parent
        / "src"
        / "ringdown_market"
        / "contracts"
        / "policies"
        / "security_lineage_v1.json"
    )
    assert packaged.read_bytes() == security_lineage_bytes()


def test_acceptance_identities_are_cik_rooted_and_ticker_is_attribute() -> None:
    lineage = load_security_lineage()
    assert lineage.lineage_id == LINEAGE_ID
    for issuer in lineage.issuers:
        assert len(issuer.issuer_id) == 10 and issuer.issuer_id.isdigit()
        assert issuer.names and issuer.tickers
    for chain in lineage.chains:
        issuer = lineage.issuers_by_id()[chain.issuer_id]
        assert any(entry.ticker == chain.ticker_at_cutoff for entry in issuer.tickers)


def test_acceptance_identity_survives_symbol_change_under_one_cik() -> None:
    def mutate(payload: dict) -> None:
        kroger = payload["issuers"][0]
        kroger["tickers"] = [
            {
                "ticker": "KROLD",
                "security_id": "0000056873:COMMON",
                "valid_from": "2000-01-01",
                "valid_to": "2026-01-01",
                "provenance": kroger["tickers"][0]["provenance"],
            },
            {
                "ticker": "KR",
                "security_id": "0000056873:COMMON",
                "valid_from": "2026-01-02",
                "valid_to": None,
                "provenance": kroger["tickers"][0]["provenance"],
            },
        ]
        payload["actions"].append(
            {
                "action_id": "KR-SYMBOL-CHANGE-20260102",
                "issuer_id": "0000056873",
                "action_type": "SYMBOL_CHANGE",
                "ex_date": "2026-01-02",
                "ratio_numerator": None,
                "ratio_denominator": None,
                "symbol_from": "KROLD",
                "symbol_to": "KR",
                "successor_issuer_id": None,
                "memo_id": None,
                "provenance": kroger["tickers"][0]["provenance"],
            }
        )

    lineage = parse_security_lineage(_mutated_bytes(mutate))
    resolution = resolve_lineage(lineage, "KR-2026Q2-EARNINGS")
    assert resolution.issuer_id == "0000056873"
    assert resolution.ticker_at_cutoff == "KR"
    assert any(
        action.action_type == "SYMBOL_CHANGE" and action.symbol_from == "KROLD"
        for action in resolution.actions
    )


def test_acceptance_snapshot_carries_status_history_actions_and_adjustments() -> None:
    lineage = load_security_lineage()
    resolution = resolve_lineage(lineage, "MU-2026Q4-EARNINGS")
    assert resolution.active_at_cutoff is True
    assert any(action.action_type == "SPLIT" for action in resolution.actions)
    assert resolution.option_adjustments
    assert all(adjustment.memo_id for adjustment in resolution.option_adjustments)
    for listing in lineage.listings:
        if listing.listed_to is not None:
            assert listing.delisting_reason


def test_acceptance_cash_dividends_are_recorded_never_adjusting() -> None:
    lineage = load_security_lineage()
    resolution = resolve_lineage(lineage, "KR-2026Q2-EARNINGS")
    dividends = tuple(
        action for action in resolution.actions if action.action_type == "CASH_DIVIDEND"
    )
    assert dividends
    assert all(
        dividend.ratio_numerator is None and dividend.ratio_denominator is None
        for dividend in dividends
    )
    assert lineage.adjustment_policy.record_only_actions == ("CASH_DIVIDEND",)


def test_acceptance_event_rescheduling_retains_supersession_and_final_schedule() -> None:
    def mutate(payload: dict) -> None:
        chain = payload["chains"][0]
        chain["provenance_links"] = [
            chain["provenance_links"][0],
            {
                "source_class": "SYNTHETIC_FIXTURE",
                "record_id": "ESSCHER_LINEAGE_RESCHEDULE_SUPERSEDED",
                "retrieved_at": "2026-08-30T22:47:00Z",
                "content_sha256": None,
            },
        ]

    lineage = parse_security_lineage(_mutated_bytes(mutate))
    resolution = resolve_lineage(lineage, "KR-2026Q2-EARNINGS")
    assert resolution.active_at_cutoff is True
    chain = lineage.chains_by_event()["KR-2026Q2-EARNINGS"]
    assert len(chain.provenance_links) == 2
    assert {link.record_id for link in chain.provenance_links} == {
        "ESSCHER_LINEAGE_DEV_SLICE",
        "ESSCHER_LINEAGE_RESCHEDULE_SUPERSEDED",
    }


def test_acceptance_universe_reconstructs_asof_for_every_p0_event() -> None:
    lineage = load_security_lineage()
    for event_id in P0_EVENTS:
        resolution = resolve_lineage(lineage, event_id)
        assert resolution.active_at_cutoff is True
        assert resolution.issuer_id.isdigit()


def test_acceptance_adjustment_policy_is_explicit_and_frozen() -> None:
    lineage = load_security_lineage()
    policy = lineage.adjustment_policy
    assert policy.price_adjusting_actions == PRICE_ADJUSTING_ACTIONS
    assert policy.record_only_actions == RECORD_ONLY_ACTIONS
    assert policy.option_adjustment_authority == OPTION_ADJUSTMENT_AUTHORITY
    assert policy.cross_series_rule == CROSS_SERIES_RULE

    def mutate(payload: dict) -> None:
        payload["adjustment_policy"]["price_adjusting_actions"] = ["SPLIT", "CASH_DIVIDEND"]

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(mutate))
    assert error.value.reason == LineageReason.MALFORMED_VALUE


def test_strict_parser_rejects_unknown_missing_duplicate_fields() -> None:
    def unknown(payload: dict) -> None:
        payload["issuers"][0]["extra"] = 1

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(unknown))
    assert error.value.reason == LineageReason.UNKNOWN_FIELD

    def missing(payload: dict) -> None:
        payload["listings"][0].pop("delisting_reason")

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(missing))
    assert error.value.reason in {LineageReason.MISSING_FIELD, LineageReason.MALFORMED_VALUE}

    raw = security_lineage_bytes().decode("utf-8")
    tampered = raw.replace(
        '"identity_rule": "CIK_ROOTED_IDENTITY"',
        '"identity_rule": "CIK_ROOTED_IDENTITY", "identity_rule": "CIK_ROOTED_IDENTITY"',
        1,
    )
    assert tampered != raw
    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(tampered.encode("utf-8"))
    assert error.value.reason == LineageReason.DUPLICATE_FIELD


@pytest.mark.parametrize("timestamp", ["2026-08-30T22:47:00+01:00", "2026-08-30T22:47:00"])
def test_lineage_timestamp_requires_explicit_zero_offset_utc(timestamp: str) -> None:
    def mutate(payload: dict) -> None:
        payload["actions"][0]["provenance"]["retrieved_at"] = timestamp

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(mutate))
    assert error.value.reason == LineageReason.MALFORMED_VALUE


def test_identity_rule_is_fail_closed() -> None:
    def mutate(payload: dict) -> None:
        payload["identity_rule"] = "TICKER_ROOTED_IDENTITY"

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(mutate))
    assert error.value.reason == LineageReason.MALFORMED_VALUE


def test_referential_integrity_fails_closed() -> None:
    def orphan_security(payload: dict) -> None:
        payload["securities"][0]["issuer_id"] = "0009999999"

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(orphan_security))
    assert error.value.reason == LineageReason.REFERENTIAL_INTEGRITY

    def orphan_listing(payload: dict) -> None:
        payload["listings"][0]["security_id"] = "0009999999:GHOST"

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(orphan_listing))
    assert error.value.reason == LineageReason.REFERENTIAL_INTEGRITY

    def orphan_action(payload: dict) -> None:
        payload["actions"][0]["issuer_id"] = "0009999999"

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(orphan_action))
    assert error.value.reason == LineageReason.REFERENTIAL_INTEGRITY


def test_overlapping_listing_periods_fail_closed() -> None:
    def mutate(payload: dict) -> None:
        duplicate = dict(payload["listings"][0])
        duplicate["listing_id"] = "LST-KR-XNYS-DUPLICATE"
        duplicate["exchange_mic"] = "XNAS"
        payload["listings"].append(duplicate)

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(mutate))
    assert error.value.reason == LineageReason.LINEAGE_CONFLICT


def test_conflicting_splits_and_adjustments_fail_closed() -> None:
    def conflicting_split(payload: dict) -> None:
        extra = dict(payload["actions"][0])
        extra["action_id"] = "MU-SPLIT-20210419-CONFLICT"
        extra["ratio_numerator"] = 3
        payload["actions"].append(extra)

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(conflicting_split))
    assert error.value.reason == LineageReason.LINEAGE_CONFLICT

    def conflicting_adjustment(payload: dict) -> None:
        extra = dict(payload["actions"][1])
        extra["action_id"] = "MU-OPTADJ-20210419-CONFLICT"
        extra["memo_id"] = "SYN-OCC-MEMO-CONFLICT"
        payload["actions"].append(extra)

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(conflicting_adjustment))
    assert error.value.reason == LineageReason.OPTION_ADJUSTMENT_CONFLICT


def test_missing_chain_fails_closed_with_stable_code() -> None:
    lineage = load_security_lineage()
    with pytest.raises(LineageRejected) as error:
        resolve_lineage(lineage, "GHOST-2099Q1-EARNINGS")
    assert error.value.reason == LineageReason.LINEAGE_MISSING


def test_delisted_listing_reports_inactive_at_cutoff() -> None:
    def mutate(payload: dict) -> None:
        payload["listings"][0]["listed_to"] = "2026-01-15"
        payload["listings"][0]["delisting_reason"] = "VOLUNTARY_DELISTING"

    lineage = parse_security_lineage(_mutated_bytes(mutate))
    resolution = resolve_lineage(lineage, "KR-2026Q2-EARNINGS")
    assert resolution.active_at_cutoff is False


def test_symbol_reuse_fails_closed() -> None:
    def mutate(payload: dict) -> None:
        ghost = {
            "issuer_id": "0009999999",
            "names": [
                {
                    "name": "Ghost Issuer",
                    "effective_from": "2000-01-01T00:00:00Z",
                    "provenance": payload["issuers"][0]["names"][0]["provenance"],
                }
            ],
            "tickers": [
                {
                    "ticker": "KR",
                    "security_id": "0009999999:COMMON",
                    "valid_from": "2000-01-01",
                    "valid_to": None,
                    "provenance": payload["issuers"][0]["tickers"][0]["provenance"],
                }
            ],
        }
        payload["issuers"].append(ghost)
        payload["securities"].append(
            {
                "security_id": "0009999999:COMMON",
                "issuer_id": "0009999999",
                "security_type": "US_COMMON_STOCK",
                "provenance": payload["securities"][0]["provenance"],
            }
        )
        kroger = payload["issuers"][0]
        kroger["tickers"][0]["valid_to"] = "2025-01-01"

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(mutate))
    assert error.value.reason == LineageReason.SYMBOL_REUSE_DETECTED


def test_split_without_option_adjustment_fails_closed_when_optionable() -> None:
    def mutate(payload: dict) -> None:
        payload["actions"] = [
            action for action in payload["actions"] if action["action_type"] == "SPLIT"
        ]

    lineage = parse_security_lineage(_mutated_bytes(mutate))
    with pytest.raises(LineageRejected) as error:
        resolve_lineage(lineage, "MU-2026Q4-EARNINGS")
    assert error.value.reason == LineageReason.OPTION_ADJUSTMENT_UNRESOLVED


def test_duplicate_event_chain_fails_closed() -> None:
    def mutate(payload: dict) -> None:
        payload["chains"].append(dict(payload["chains"][0]))

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(mutate))
    assert error.value.reason == LineageReason.DUPLICATE_FIELD


def test_action_vocabulary_is_frozen() -> None:
    def mutate(payload: dict) -> None:
        payload["actions"][0]["action_type"] = "REVERSE_SPLITISH"

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(mutate))
    assert error.value.reason == LineageReason.MALFORMED_VALUE
    assert (
        frozenset(
            {"SPLIT", "CASH_DIVIDEND", "SYMBOL_CHANGE", "MERGER", "SPINOFF", "OPTION_ADJUSTMENT"}
        )
        == ACTION_TYPES
    )


def test_merger_requires_successor_and_terminates_listing() -> None:
    def mutate(payload: dict) -> None:
        payload["issuers"].append(
            {
                "issuer_id": "0009999998",
                "names": [
                    {
                        "name": "Successor Corp",
                        "effective_from": "2026-02-01T00:00:00Z",
                        "provenance": payload["issuers"][0]["names"][0]["provenance"],
                    }
                ],
                "tickers": [
                    {
                        "ticker": "SCC",
                        "security_id": "0009999998:COMMON",
                        "valid_from": "2026-02-01",
                        "valid_to": None,
                        "provenance": payload["issuers"][0]["tickers"][0]["provenance"],
                    }
                ],
            }
        )
        payload["securities"].append(
            {
                "security_id": "0009999998:COMMON",
                "issuer_id": "0009999998",
                "security_type": "US_COMMON_STOCK",
                "provenance": payload["securities"][0]["provenance"],
            }
        )
        payload["actions"].append(
            {
                "action_id": "KR-MERGER-20260201",
                "issuer_id": "0000056873",
                "action_type": "MERGER",
                "ex_date": "2026-02-01",
                "ratio_numerator": None,
                "ratio_denominator": None,
                "symbol_from": None,
                "symbol_to": None,
                "successor_issuer_id": "0009999998",
                "memo_id": None,
                "provenance": payload["issuers"][0]["names"][0]["provenance"],
            }
        )
        payload["listings"][0]["listed_to"] = "2026-02-01"
        payload["listings"][0]["delisting_reason"] = "MERGER_INTO_SUCCESSOR"

    lineage = parse_security_lineage(_mutated_bytes(mutate))
    resolution = resolve_lineage(lineage, "KR-2026Q2-EARNINGS")
    assert resolution.active_at_cutoff is False
    merger = next(a for a in resolution.actions if a.action_type == "MERGER")
    assert merger.successor_issuer_id == "0009999998"

    def orphan_successor(payload: dict) -> None:
        mutate(payload)
        payload["actions"][-1]["successor_issuer_id"] = "0001111111"

    with pytest.raises(LineageRejected) as error:
        parse_security_lineage(_mutated_bytes(orphan_successor))
    assert error.value.reason == LineageReason.REFERENTIAL_INTEGRITY


def test_upstream_binding_fails_closed_on_drift() -> None:
    lineage = load_security_lineage()
    verify_lineage_upstream_bindings(
        lineage,
        policy_bytes=strategy_policy_bytes(),
        source_matrix_bytes=source_matrix_bytes(),
    )

    def mutate(payload: dict) -> None:
        payload["policy_sha256"] = "0" * 64

    drifted = parse_security_lineage(_mutated_bytes(mutate))
    with pytest.raises(LineageRejected) as error:
        verify_lineage_upstream_bindings(
            drifted,
            policy_bytes=strategy_policy_bytes(),
            source_matrix_bytes=source_matrix_bytes(),
        )
    assert error.value.reason == LineageReason.UPSTREAM_CONTRACT_DRIFT

    def matrix_drift(payload: dict) -> None:
        payload["source_matrix_sha256"] = "1" * 64

    drifted_matrix = parse_security_lineage(_mutated_bytes(matrix_drift))
    with pytest.raises(LineageRejected) as error:
        verify_lineage_upstream_bindings(
            drifted_matrix,
            policy_bytes=strategy_policy_bytes(),
            source_matrix_bytes=source_matrix_bytes(),
        )
    assert error.value.reason == LineageReason.UPSTREAM_CONTRACT_DRIFT


def test_schema_constants_are_frozen() -> None:
    assert SCHEMA_ID == "esscher.security_lineage"
    assert PRICE_ADJUSTING_ACTIONS == ("SPLIT",)
    assert RECORD_ONLY_ACTIONS == ("CASH_DIVIDEND",)
    assert CROSS_SERIES_RULE == "ISSUER_ACTIONS_NEVER_ADJUST_MARKET_OR_SECTOR_SERIES"
    assert date.fromisoformat("2026-06-01") == date(2026, 6, 1)
