"""Owner-approved contract clarification (#91): provider-output ordering.

The frozen prompt and output schema communicate uniqueness and cardinality to
the reasoner but never an ordering requirement; ordering is Esscher's internal
canonical form.  These tests pin the clarified behavior: provider-output arrays
are canonicalized by sorting (hash-stable), duplicates remain a hard rejection,
wrong field names and malformed JSON still fail closed, and durable internal
artifacts remain strict (covered by the full existing suite, which pins
sorted-and-unique rejection for every machine-generated parse path).
"""

from __future__ import annotations

import pytest

from ringdown_market.strategy.contracts import (
    StrategyContractRejected,
    parse_reasoner_decision,
    reasoner_decision_bytes,
)


def _decision_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contradictions": [],
        "decision": "UP",
        "evidence_ids": ["ev-market-1", "ev-primary-1"],
        "strongest_falsifier": {"evidence_id": "ev-market-1", "summary": "Reaction can fade."},
        "summary": "Primary catalyst confirmed by the market reaction.",
        "unknowns": [],
    }
    payload.update(overrides)
    return payload


def _raw(payload: dict[str, object]) -> bytes:
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_unsorted_provider_evidence_ids_are_canonicalized_not_rejected() -> None:
    unsorted_parse = parse_reasoner_decision(
        _raw(_decision_payload(evidence_ids=["ev-primary-1", "ev-market-1"]))
    )
    sorted_parse = parse_reasoner_decision(
        _raw(_decision_payload(evidence_ids=["ev-market-1", "ev-primary-1"]))
    )

    assert unsorted_parse.evidence_ids == ("ev-market-1", "ev-primary-1")
    assert unsorted_parse.evidence_ids == sorted_parse.evidence_ids
    # Hash stability: ordering at the provider never changes semantic identity.
    assert reasoner_decision_bytes(unsorted_parse) == reasoner_decision_bytes(sorted_parse)


def test_unsorted_unknowns_and_contradiction_ids_are_canonicalized() -> None:
    decision = parse_reasoner_decision(
        _raw(
            _decision_payload(
                unknowns=["ZULU_CODE", "ALPHA_CODE"],
                contradictions=[
                    {
                        "evidence_ids": ["ev-market-1", "ev-primary-1"],
                        "summary": "Guidance conflicts with the opening reaction.",
                    }
                ],
            )
        )
    )

    assert decision.unknowns == ("ALPHA_CODE", "ZULU_CODE")
    assert decision.contradictions[0].evidence_ids == ("ev-market-1", "ev-primary-1")


def test_duplicate_provider_evidence_ids_still_fail_closed() -> None:
    with pytest.raises(StrategyContractRejected, match="must be unique"):
        parse_reasoner_decision(
            _raw(_decision_payload(evidence_ids=["ev-primary-1", "ev-primary-1"]))
        )


def test_wrong_field_names_and_malformed_json_still_fail_closed() -> None:
    with pytest.raises(StrategyContractRejected, match="evidence_ids"):
        parse_reasoner_decision(
            _raw(
                _decision_payload(
                    contradictions=[
                        {
                            "evidence_id_a": "ev-market-1",
                            "evidence_id_b": "ev-primary-1",
                            "summary": "Wrong shape is still wrong.",
                        }
                    ]
                )
            )
        )

    with pytest.raises(StrategyContractRejected):
        parse_reasoner_decision(b"{not json at all")
