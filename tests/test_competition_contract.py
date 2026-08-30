from __future__ import annotations

import hashlib
import json

import pytest

from ringdown_market.contracts.competition import (
    CompetitionContractRejected,
    ValidatedCompetitionContract,
    validate_competition_contract,
)


def _fact(
    value: object,
    *source_ids: str,
    status: str = "CONFIRMED",
    limitations: list[str] | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "value": value,
        "source_ids": list(source_ids),
        "limitations": limitations or [],
    }


def _document() -> dict[str, object]:
    organizer_sources = ["organizer-event", "organizer-live"]
    return {
        "schema": "ringdown.competition_contract",
        "schema_version": 1,
        "contract_id": "alpaca-ai-trading-agents-hackathon-2026",
        "frozen_at": "2026-08-28T20:10:00Z",
        "sources": [
            {
                "source_id": "organizer-event",
                "source_url": (
                    "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon"
                ),
                "publisher": "lablab.ai",
                "verified_at": "2026-08-28T20:00:00Z",
                "content_sha256": "a" * 64,
                "verification_method": "HUMAN_BROWSER",
                "redistribution_status": "METADATA_AND_HASH_ONLY",
            },
            {
                "source_id": "organizer-live",
                "source_url": (
                    "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live"
                ),
                "publisher": "lablab.ai",
                "verified_at": "2026-08-28T20:05:00Z",
                "content_sha256": "b" * 64,
                "verification_method": "HUMAN_BROWSER",
                "redistribution_status": "METADATA_AND_HASH_ONLY",
            },
        ],
        "facts": {
            "submission_deadline": _fact("2026-09-04T15:00:00Z", *organizer_sources),
            "starting_equity_usd": _fact("100000.00", *organizer_sources),
            "alpaca_api_route": _fact("EITHER_MCP_OR_CLI", *organizer_sources),
            "options_required": _fact(True, *organizer_sources),
            "paper_account_id_required_for_judging": _fact(True, *organizer_sources),
            "positions_flat_at_submission": _fact(
                None,
                *organizer_sources,
                status="UNKNOWN",
                limitations=["The preserved organizer text did not settle flatness."],
            ),
            "minimum_trade_count": _fact(
                None,
                *organizer_sources,
                status="UNKNOWN",
                limitations=["The preserved organizer text stated no minimum."],
            ),
            "ai_assistance_disclosure_required": _fact(
                None,
                *organizer_sources,
                status="UNKNOWN",
                limitations=["The preserved organizer text did not settle disclosure."],
            ),
            "judging_dimensions": _fact(
                ["PNL", "IMPLEMENTATION", "ORIGINALITY", "PRESENTATION"],
                *organizer_sources,
            ),
        },
        "safety": {
            "run_mode": "PAPER",
            "credentials_forbidden": True,
            "private_account_identifiers_forbidden": True,
            "unknown_facts_fail_closed": True,
        },
    }


def _bytes(document: dict[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_valid_contract_reports_blocking_unknowns_and_raw_byte_identity() -> None:
    raw = _bytes(_document())

    validated = validate_competition_contract(raw)

    assert validated.contract_id == "alpaca-ai-trading-agents-hackathon-2026"
    assert validated.contract_sha256 == hashlib.sha256(raw).hexdigest()
    assert validated.blocking_unknowns == (
        "ai_assistance_disclosure_required",
        "minimum_trade_count",
        "positions_flat_at_submission",
    )
    assert validated.facts_complete is False
    assert not hasattr(validated, "release_ready")
    assert validated.permit_eligible is False


def _rejected(document: dict[str, object]) -> CompetitionContractRejected:
    with pytest.raises(CompetitionContractRejected) as caught:
        validate_competition_contract(_bytes(document))
    return caught.value


def test_all_required_facts_confirmed_is_contract_complete_without_release_authority() -> None:
    document = _document()
    facts = document["facts"]
    assert isinstance(facts, dict)
    facts["positions_flat_at_submission"] = _fact(True, "organizer-event", "organizer-live")
    facts["minimum_trade_count"] = _fact(0, "organizer-event", "organizer-live")
    facts["ai_assistance_disclosure_required"] = _fact(True, "organizer-event", "organizer-live")

    validated = validate_competition_contract(_bytes(document))

    assert validated.facts_complete is True
    assert not hasattr(validated, "release_ready")
    assert validated.blocking_unknowns == ()
    assert validated.permit_eligible is False


def test_validated_contract_cannot_be_constructed_with_permit_authority() -> None:
    with pytest.raises(TypeError, match="permit_eligible"):
        ValidatedCompetitionContract(
            contract_id="alpaca-ai-trading-agents-hackathon-2026",
            contract_sha256="a" * 64,
            blocking_unknowns=(),
            facts_complete=True,
            permit_eligible=True,  # type: ignore[call-arg]
        )


def test_unknown_fact_cannot_smuggle_a_value() -> None:
    document = _document()
    facts = document["facts"]
    assert isinstance(facts, dict)
    fact = facts["minimum_trade_count"]
    assert isinstance(fact, dict)
    fact["value"] = 1

    caught = _rejected(document)

    assert "facts.minimum_trade_count.value" in str(caught)


def test_unknown_fact_requires_an_explicit_limitation() -> None:
    document = _document()
    facts = document["facts"]
    assert isinstance(facts, dict)
    fact = facts["positions_flat_at_submission"]
    assert isinstance(fact, dict)
    fact["limitations"] = []

    caught = _rejected(document)

    assert "facts.positions_flat_at_submission.limitations" in str(caught)


def test_confirmed_fact_requires_a_known_source_and_value() -> None:
    document = _document()
    facts = document["facts"]
    assert isinstance(facts, dict)
    fact = facts["options_required"]
    assert isinstance(fact, dict)
    fact["source_ids"] = ["missing-source"]

    caught = _rejected(document)

    assert "facts.options_required.source_ids[0]" in str(caught)


def test_duplicate_json_field_is_rejected() -> None:
    raw = b'{"schema":"ringdown.competition_contract","schema":"other"}'

    with pytest.raises(CompetitionContractRejected, match="duplicate field schema"):
        validate_competition_contract(raw)


def test_schema_and_root_fields_are_exact() -> None:
    document = _document()
    document["paper_account_id"] = "private-id-must-not-exist"

    caught = _rejected(document)

    assert "paper_account_id" in str(caught)


@pytest.mark.parametrize(
    ("level", "field", "expected_path"),
    [
        ("root", "contract_id", "contract.contract_id"),
        ("source", "publisher", "sources[0].publisher"),
        ("facts", "options_required", "facts.options_required"),
        ("fact", "status", "facts.options_required.status"),
        ("safety", "run_mode", "safety.run_mode"),
    ],
)
def test_missing_required_fields_fail_closed(
    level: str,
    field: str,
    expected_path: str,
) -> None:
    document = _document()
    if level == "root":
        target = document
    elif level == "source":
        sources = document["sources"]
        assert isinstance(sources, list)
        target = sources[0]
    elif level == "facts":
        target = document["facts"]
    elif level == "fact":
        facts = document["facts"]
        assert isinstance(facts, dict)
        target = facts["options_required"]
    else:
        target = document["safety"]
    assert isinstance(target, dict)
    del target[field]

    caught = _rejected(document)

    assert expected_path in str(caught)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verification_method", "SCREENSHOT"),
        ("redistribution_status", "FULL_SOURCE_BYTES"),
    ],
)
def test_source_provenance_guards_fail_closed(field: str, value: str) -> None:
    document = _document()
    sources = document["sources"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    source[field] = value

    caught = _rejected(document)

    assert f"sources[0].{field}" in str(caught)


def test_duplicate_source_ids_fail_closed() -> None:
    document = _document()
    sources = document["sources"]
    assert isinstance(sources, list)
    first = sources[0]
    second = sources[1]
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    second["source_id"] = first["source_id"]

    caught = _rejected(document)

    assert "sources[1].source_id" in str(caught)


def test_empty_sources_fail_closed() -> None:
    document = _document()
    document["sources"] = []

    caught = _rejected(document)

    assert "sources" in str(caught)


@pytest.mark.parametrize("raw", [b"[]", b"{not-json", b"\xff"])
def test_non_object_or_invalid_json_bytes_fail_closed(raw: bytes) -> None:
    with pytest.raises(CompetitionContractRejected):
        validate_competition_contract(raw)


def test_unsupported_schema_version_is_rejected() -> None:
    document = _document()
    document["schema_version"] = 2

    caught = _rejected(document)

    assert "unsupported schema" in str(caught)


@pytest.mark.parametrize("schema_version", [True, False, 1.0, "1"])
def test_schema_version_requires_exact_integer_one(schema_version: object) -> None:
    document = _document()
    document["schema_version"] = schema_version

    caught = _rejected(document)

    assert "contract.schema_version" in str(caught)


def test_unhashable_source_method_is_a_structured_rejection() -> None:
    document = _document()
    sources = document["sources"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    source["verification_method"] = []

    caught = _rejected(document)

    assert "sources[0].verification_method" in str(caught)


def test_unhashable_fact_status_is_a_structured_rejection() -> None:
    document = _document()
    facts = document["facts"]
    assert isinstance(facts, dict)
    fact = facts["options_required"]
    assert isinstance(fact, dict)
    fact["status"] = []

    caught = _rejected(document)

    assert "facts.options_required.status" in str(caught)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("submission_deadline", "2026-09-04T15:00:00+00:00"),
        ("starting_equity_usd", 100000),
        ("alpaca_api_route", "REST"),
        ("options_required", "yes"),
        ("paper_account_id_required_for_judging", 1),
        ("judging_dimensions", ["PNL", "PNL"]),
    ],
)
def test_confirmed_fact_values_are_typed(field: str, value: object) -> None:
    document = _document()
    facts = document["facts"]
    assert isinstance(facts, dict)
    fact = facts[field]
    assert isinstance(fact, dict)
    fact["value"] = value

    caught = _rejected(document)

    assert f"facts.{field}.value" in str(caught)


@pytest.mark.parametrize("value", [True, False, 1.0])
def test_minimum_trade_count_rejects_boolean_and_float_aliases(value: object) -> None:
    document = _document()
    facts = document["facts"]
    assert isinstance(facts, dict)
    facts["minimum_trade_count"] = _fact(
        value,
        "organizer-event",
        "organizer-live",
    )

    caught = _rejected(document)

    assert "facts.minimum_trade_count.value" in str(caught)


def test_source_must_be_public_https_and_precede_freeze() -> None:
    document = _document()
    sources = document["sources"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    source["source_url"] = "https://user:password@example.com/rules"
    source["verified_at"] = "2026-08-28T20:11:00Z"

    caught = _rejected(document)

    assert "sources[0].source_url" in str(caught)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/rules?account_id=fake-placeholder",
        "https://example.com/rules?api_key=fake-placeholder",
        "https://example.com/rules#token=fake-placeholder",
        "https://example.com/rules?empty=&other=fake-placeholder#",
        "https://localhost/rules",
        "https://LOCALHOST/rules",
        "https://internal.localhost/rules",
        "https://127.0.0.1/rules",
        "https://[::1]/rules",
        "https://[::ffff:127.0.0.1]/rules",
        "https://10.0.0.1/rules",
        "https://192.168.1.1/rules",
        "https://169.254.0.1/rules",
        "https://[fe80::1]/rules",
        "https://[fd00::1]/rules",
        "https://:443/rules",
    ],
)
def test_source_url_rejects_metadata_bearing_or_non_public_hosts(url: str) -> None:
    document = _document()
    sources = document["sources"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    source["source_url"] = url

    caught = _rejected(document)

    assert "sources[0].source_url" in str(caught)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/rules",
        "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon",
        "https://8.8.8.8/rules",
    ],
)
def test_source_url_accepts_public_https_hosts(url: str) -> None:
    document = _document()
    sources = document["sources"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    source["source_url"] = url

    validated = validate_competition_contract(_bytes(document))

    assert validated.contract_id == "alpaca-ai-trading-agents-hackathon-2026"
    assert validated.permit_eligible is False


def test_source_verification_cannot_postdate_freeze() -> None:
    document = _document()
    sources = document["sources"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    source["verified_at"] = "2026-08-28T20:11:00Z"

    caught = _rejected(document)

    assert "sources[0].verified_at" in str(caught)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_mode", "LIVE"),
        ("credentials_forbidden", False),
        ("private_account_identifiers_forbidden", False),
        ("unknown_facts_fail_closed", False),
    ],
)
def test_safety_invariants_cannot_be_weakened(field: str, value: object) -> None:
    document = _document()
    safety = document["safety"]
    assert isinstance(safety, dict)
    safety[field] = value

    caught = _rejected(document)

    assert f"safety.{field}" in str(caught)


def test_non_bytes_input_is_rejected() -> None:
    with pytest.raises(CompetitionContractRejected, match="immutable bytes"):
        validate_competition_contract({})  # type: ignore[arg-type]
