"""Fail-closed competition and account-capability facts for release gating."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
_IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])?$")
_MONEY = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")
_DIMENSION = re.compile(r"^[A-Z][A-Z0-9_]*$")

_ROOT_FIELDS = frozenset(
    {"schema", "schema_version", "contract_id", "frozen_at", "sources", "facts", "safety"}
)
_SOURCE_FIELDS = frozenset(
    {
        "source_id",
        "source_url",
        "publisher",
        "verified_at",
        "content_sha256",
        "verification_method",
        "redistribution_status",
    }
)
_FACT_FIELDS = frozenset({"status", "value", "source_ids", "limitations"})
_SAFETY_FIELDS = frozenset(
    {
        "run_mode",
        "credentials_forbidden",
        "private_account_identifiers_forbidden",
        "unknown_facts_fail_closed",
    }
)
_FACT_NAMES = frozenset(
    {
        "submission_deadline",
        "starting_equity_usd",
        "alpaca_api_route",
        "options_required",
        "paper_account_id_required_for_judging",
        "positions_flat_at_submission",
        "minimum_trade_count",
        "ai_assistance_disclosure_required",
        "judging_dimensions",
    }
)
_VERIFICATION_METHODS = frozenset(
    {"HUMAN_BROWSER", "AUTOMATED_FETCH", "PINNED_REPOSITORY_SNAPSHOT"}
)


class CompetitionContractRejected(ValueError):
    """A deterministic competition-contract validation failure."""


@dataclass(frozen=True, slots=True)
class ValidatedCompetitionContract:
    """Identity and release state of a validated competition contract."""

    contract_id: str
    contract_sha256: str
    blocking_unknowns: tuple[str, ...]
    release_ready: bool
    permit_eligible: bool = False


class _DuplicateFieldError(ValueError):
    pass


def _reject(path: str, detail: str) -> None:
    raise CompetitionContractRejected(f"{path}: {detail}")


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _decode(raw: bytes) -> Mapping[str, object]:
    if type(raw) is not bytes:
        raise CompetitionContractRejected("contract input must be immutable bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except _DuplicateFieldError as error:
        raise CompetitionContractRejected(f"duplicate field {error}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompetitionContractRejected(f"invalid JSON: {error}") from error
    if not isinstance(value, Mapping):
        _reject("contract", "root must be an object")
    return value


def _strict_object(
    value: object,
    *,
    path: str,
    fields: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(f"{path}.{missing[0]}", "required field is missing")
    unknown = sorted(keys - fields)
    if unknown:
        _reject(f"{path}.{unknown[0]}", "field is not part of the frozen schema")
    return value


def _text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(path, "must be non-empty trimmed text")
    return value


def _identifier(value: object, *, path: str) -> str:
    text = _text(value, path=path)
    if not _IDENTIFIER.fullmatch(text):
        _reject(path, "must be a lowercase hyphenated identifier")
    return text


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        _reject(path, "must be an explicit UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as error:
        _reject(path, str(error))


def _https_url(value: object, *, path: str) -> str:
    url = _text(value, path=path)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        _reject(path, "must be a public HTTPS URL without credentials")
    return url


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _reject(path, "must be lowercase SHA-256")
    return value


def _text_list(value: object, *, path: str, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        _reject(path, "must be a list")
    result = tuple(_text(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        _reject(path, "values must be unique")
    return result


def _validate_sources(
    value: object,
    *,
    frozen_at: datetime,
) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        _reject("sources", "must be a non-empty list")
    source_ids: set[str] = set()
    for index, candidate in enumerate(value):
        path = f"sources[{index}]"
        source = _strict_object(candidate, path=path, fields=_SOURCE_FIELDS)
        source_id = _identifier(source["source_id"], path=f"{path}.source_id")
        if source_id in source_ids:
            _reject(f"{path}.source_id", "source IDs must be unique")
        source_ids.add(source_id)
        _https_url(source["source_url"], path=f"{path}.source_url")
        _text(source["publisher"], path=f"{path}.publisher")
        verified_at = _timestamp(source["verified_at"], path=f"{path}.verified_at")
        if verified_at > frozen_at:
            _reject(f"{path}.verified_at", "cannot postdate contract freeze")
        _sha256(source["content_sha256"], path=f"{path}.content_sha256")
        verification_method = source["verification_method"]
        if (
            not isinstance(verification_method, str)
            or verification_method not in _VERIFICATION_METHODS
        ):
            _reject(f"{path}.verification_method", "unsupported verification method")
        if source["redistribution_status"] != "METADATA_AND_HASH_ONLY":
            _reject(
                f"{path}.redistribution_status",
                "raw organizer source bytes cannot be redistributed",
            )
    return frozenset(source_ids)


def _validate_fact_value(name: str, value: object, *, path: str) -> None:
    if name == "submission_deadline":
        _timestamp(value, path=path)
        return
    if name == "starting_equity_usd":
        if not isinstance(value, str) or not _MONEY.fullmatch(value) or value == "0.00":
            _reject(path, "must be a positive two-decimal USD string")
        return
    if name == "alpaca_api_route":
        if value != "EITHER_MCP_OR_CLI":
            _reject(path, "must match the confirmed organizer route")
        return
    if name in {
        "options_required",
        "paper_account_id_required_for_judging",
        "positions_flat_at_submission",
        "ai_assistance_disclosure_required",
    }:
        if type(value) is not bool:
            _reject(path, "must be a boolean")
        return
    if name == "minimum_trade_count":
        if type(value) is not int or value < 0:
            _reject(path, "must be a non-negative integer")
        return
    if name == "judging_dimensions":
        dimensions = _text_list(value, path=path, nonempty=True)
        if any(not _DIMENSION.fullmatch(item) for item in dimensions):
            _reject(path, "dimensions must be uppercase identifiers")
        return
    _reject(path, "unsupported competition fact")


def _validate_facts(
    value: object,
    *,
    known_source_ids: frozenset[str],
) -> tuple[str, ...]:
    facts = _strict_object(value, path="facts", fields=_FACT_NAMES)
    blocking_unknowns: list[str] = []
    for name in sorted(_FACT_NAMES):
        path = f"facts.{name}"
        fact = _strict_object(facts[name], path=path, fields=_FACT_FIELDS)
        status = fact["status"]
        if not isinstance(status, str) or status not in {"CONFIRMED", "UNKNOWN"}:
            _reject(f"{path}.status", "must be CONFIRMED or UNKNOWN")
        source_ids = _text_list(fact["source_ids"], path=f"{path}.source_ids", nonempty=True)
        for index, source_id in enumerate(source_ids):
            if source_id not in known_source_ids:
                _reject(f"{path}.source_ids[{index}]", "references an unknown source")
        limitations = _text_list(fact["limitations"], path=f"{path}.limitations")
        if status == "UNKNOWN":
            if fact["value"] is not None:
                _reject(f"{path}.value", "UNKNOWN facts must use null")
            if not limitations:
                _reject(f"{path}.limitations", "UNKNOWN facts require a limitation")
            blocking_unknowns.append(name)
        else:
            if fact["value"] is None:
                _reject(f"{path}.value", "CONFIRMED facts require a value")
            _validate_fact_value(name, fact["value"], path=f"{path}.value")
    return tuple(blocking_unknowns)


def _validate_safety(value: object) -> None:
    safety = _strict_object(value, path="safety", fields=_SAFETY_FIELDS)
    expected: dict[str, object] = {
        "run_mode": "PAPER",
        "credentials_forbidden": True,
        "private_account_identifiers_forbidden": True,
        "unknown_facts_fail_closed": True,
    }
    for field, required in expected.items():
        if safety[field] != required or type(safety[field]) is not type(required):
            _reject(f"safety.{field}", f"must remain {required!r}")


def validate_competition_contract(raw: bytes) -> ValidatedCompetitionContract:
    """Validate immutable organizer facts without granting execution authority."""

    payload = _strict_object(_decode(raw), path="contract", fields=_ROOT_FIELDS)
    schema_version = payload["schema_version"]
    if (
        payload["schema"] != "ringdown.competition_contract"
        or type(schema_version) is not int
        or schema_version != 1
    ):
        _reject("contract.schema_version", "unsupported schema or version")
    contract_id = _identifier(payload["contract_id"], path="contract.contract_id")
    frozen_at = _timestamp(payload["frozen_at"], path="contract.frozen_at")
    source_ids = _validate_sources(payload["sources"], frozen_at=frozen_at)
    blocking_unknowns = _validate_facts(payload["facts"], known_source_ids=source_ids)
    _validate_safety(payload["safety"])
    return ValidatedCompetitionContract(
        contract_id=contract_id,
        contract_sha256=hashlib.sha256(raw).hexdigest(),
        blocking_unknowns=blocking_unknowns,
        release_ready=not blocking_unknowns,
    )
