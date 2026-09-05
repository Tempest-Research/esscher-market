"""Fixture-only trusted-news observation values.

This module is deliberately a pure, offline contract.  It parses and
normalizes already-supplied fixture observations; it does not contain an
adapter, credential, network, browser, provider, account, or broker surface.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import NoReturn

from esscher.sourcedata.evidence import EvidenceEntry
from esscher.sourcedata.interfaces import SourceProvenance
from esscher.sourcedata.receipts import SourceReceipt
from esscher.strategy.contracts import canonical_json_bytes, sha256_bytes
from esscher.strategy.models import EvidenceRole

NEWS_OBSERVATION_SCHEMA = "esscher.news_observation"
NEWS_OBSERVATION_SCHEMA_VERSION = 1
NEWS_SOURCE_CLASS = "LICENSED_PERMITTED_NEWS"
UNTRUSTED_QUOTED_DATA = "UNTRUSTED_QUOTED_DATA"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_CANONICAL_URL = re.compile(
    r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?(?:/[^\s#]*)?(?:\?[^\s#]*)?$"
)

_OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "observation_id",
        "source_id",
        "source_policy_sha256",
        "publisher_id",
        "canonical_url",
        "provider_article_id",
        "publisher_published_at",
        "provider_available_at",
        "retrieved_at",
        "content_sha256",
        "raw_blob_sha256",
        "entitlement_status",
        "redistribution_status",
        "revision_of",
        "retrieval_status",
        "headline",
        "body",
    }
)
_REVISION_LINK_FIELDS = frozenset({"observation_id", "observation_sha256"})


class NewsObservationReason(StrEnum):
    """Stable reasons for rejecting fixture-only news observations."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    NON_CANONICAL_DOCUMENT = "NON_CANONICAL_DOCUMENT"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_HASH = "INVALID_HASH"
    INVALID_URL = "INVALID_URL"
    INVALID_CLOCK = "INVALID_CLOCK"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    BLOCKED_SOURCE = "BLOCKED_SOURCE"
    SOURCE_POLICY_MISMATCH = "SOURCE_POLICY_MISMATCH"
    SOURCE_METADATA_MISMATCH = "SOURCE_METADATA_MISMATCH"
    SOURCE_RIGHTS_MISMATCH = "SOURCE_RIGHTS_MISMATCH"
    ENTITLEMENT_NOT_FEASIBLE = "ENTITLEMENT_NOT_FEASIBLE"
    REDISTRIBUTION_MISSING = "REDISTRIBUTION_MISSING"
    REDISTRIBUTION_UNKNOWN = "REDISTRIBUTION_UNKNOWN"
    CLOCK_ORDER_INVALID = "CLOCK_ORDER_INVALID"
    AFTER_DECISION_CUTOFF = "AFTER_DECISION_CUTOFF"
    DUPLICATE_CONTENT = "DUPLICATE_CONTENT"
    RETRIEVAL_INCOMPLETE = "RETRIEVAL_INCOMPLETE"
    INVALID_REVISION_LINK = "INVALID_REVISION_LINK"
    DUPLICATE_OBSERVATION = "DUPLICATE_OBSERVATION"


class NewsObservationRejected(ValueError):
    """A deterministic fail-closed news-contract rejection."""

    def __init__(self, reason: NewsObservationReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


@dataclass(frozen=True, slots=True)
class NewsRevisionLink:
    """Immutable identity binding a revision to its prior observation bytes."""

    observation_id: str
    observation_sha256: str


@dataclass(frozen=True, slots=True)
class NewsSourceAuthorization:
    """Host-owned binding of one source ID to one approved policy and verdict."""

    source_id: str
    source_policy_sha256: str
    verdict: str
    publisher_ids: tuple[str, ...]
    canonical_url_prefixes: tuple[str, ...]
    redistribution_status: str


@dataclass(frozen=True, slots=True)
class NewsObservation:
    """One supplied point-in-time news observation; never a live-provider result."""

    observation_id: str
    source_id: str
    source_policy_sha256: str
    publisher_id: str
    canonical_url: str
    provider_article_id: str
    publisher_published_at: datetime
    provider_available_at: datetime
    retrieved_at: datetime
    content_sha256: str
    raw_blob_sha256: str
    entitlement_status: str
    redistribution_status: str
    revision_of: NewsRevisionLink | None
    retrieval_status: str
    headline: str
    body: str


@dataclass(frozen=True, slots=True)
class UntrustedQuotedText:
    """Article text retained solely as inert, quoted fixture data."""

    text: str
    classification: str = UNTRUSTED_QUOTED_DATA

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("untrusted quoted text must be text")
        if self.classification != UNTRUSTED_QUOTED_DATA:
            raise ValueError("quoted text classification is fixed")


@dataclass(frozen=True, slots=True)
class NormalizedNewsEvidence:
    """News data plus an existing evidence receipt, without decision authority."""

    evidence_entry: EvidenceEntry
    observation_id: str
    observation_sha256: str
    source_policy_sha256: str
    raw_blob_sha256: str
    provider_article_id: str
    canonical_url: str
    provider_available_at: datetime
    revision_of: NewsRevisionLink | None
    headline: UntrustedQuotedText
    body: UntrustedQuotedText

    @property
    def receipt(self) -> SourceReceipt:
        """Expose the existing compatible receipt without duplicating it."""

        return self.evidence_entry.receipt


class _DuplicateFieldError(ValueError):
    pass


def _reject(reason: NewsObservationReason, path: str, detail: str) -> NoReturn:
    raise NewsObservationRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _invalid_float(value: str) -> NoReturn:
    raise ValueError(f"JSON numeric literal {value} is forbidden")


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")


def _decode(raw: bytes) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(NewsObservationReason.INVALID_DOCUMENT, "bytes", "input must be immutable bytes")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_invalid_float,
            parse_constant=_invalid_constant,
        )
    except _DuplicateFieldError as error:
        _reject(NewsObservationReason.DUPLICATE_FIELD, "document", f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(NewsObservationReason.INVALID_DOCUMENT, "document", str(error))
    if not isinstance(payload, Mapping):
        _reject(NewsObservationReason.INVALID_DOCUMENT, "document", "root must be an object")
    return payload


def _strict_object(value: object, *, path: str, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(NewsObservationReason.INVALID_DOCUMENT, path, "must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    if missing:
        _reject(
            NewsObservationReason.MISSING_FIELD,
            f"{path}.{missing[0]}",
            "required field is missing",
        )
    unknown = sorted(keys - fields)
    if unknown:
        _reject(
            NewsObservationReason.UNKNOWN_FIELD,
            f"{path}.{unknown[0]}",
            "field is not part of the news observation schema",
        )
    return value


def _identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _reject(NewsObservationReason.INVALID_IDENTIFIER, path, "must be a normalized identifier")
    return value


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(NewsObservationReason.INVALID_HASH, path, "must be a lowercase SHA-256 digest")
    return value


def _text(value: object, *, path: str) -> str:
    if not isinstance(value, str):
        _reject(NewsObservationReason.INVALID_DOCUMENT, path, "must be text")
    return value


def _canonical_url(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _CANONICAL_URL.fullmatch(value) is None:
        _reject(NewsObservationReason.INVALID_URL, path, "must be an absolute canonical HTTPS URL")
    return value


def _timestamp_text(value: datetime, *, path: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is not UTC:
        _reject(NewsObservationReason.INVALID_CLOCK, path, "must be a UTC datetime")
    result = value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if result.endswith(".000000Z"):
        return result.removesuffix(".000000Z") + "Z"
    prefix, fraction = result[:-1].split(".", maxsplit=1)
    return f"{prefix}.{fraction.rstrip('0')}Z"


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        _reject(
            NewsObservationReason.INVALID_CLOCK,
            path,
            "must be a canonical UTC timestamp ending in Z",
        )
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00").astimezone(UTC)
    except ValueError as error:
        _reject(NewsObservationReason.INVALID_CLOCK, path, str(error))
    if _timestamp_text(parsed, path=path) != value:
        _reject(NewsObservationReason.INVALID_CLOCK, path, "timestamp is not canonical")
    return parsed


def news_content_bytes(headline: str, body: str) -> bytes:
    """Return the canonical content bytes bound by ``content_sha256``."""

    return canonical_json_bytes(
        {
            "body": _text(body, path="body"),
            "headline": _text(headline, path="headline"),
        }
    )


def news_content_sha256(headline: str, body: str) -> str:
    """Return the deterministic identity of the supplied headline and body text."""

    return sha256_bytes(news_content_bytes(headline, body))


def _revision_link_payload(value: NewsRevisionLink | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, NewsRevisionLink):
        _reject(
            NewsObservationReason.INVALID_REVISION_LINK,
            "revision_of",
            "must be a revision link or null",
        )
    return {
        "observation_id": _identifier(value.observation_id, path="revision_of.observation_id"),
        "observation_sha256": _sha256(
            value.observation_sha256,
            path="revision_of.observation_sha256",
        ),
    }


def _validate_shape(observation: NewsObservation) -> None:
    if not isinstance(observation, NewsObservation):
        _reject(NewsObservationReason.INVALID_DOCUMENT, "observation", "must be a NewsObservation")
    _identifier(observation.observation_id, path="observation_id")
    _identifier(observation.source_id, path="source_id")
    _sha256(observation.source_policy_sha256, path="source_policy_sha256")
    _identifier(observation.publisher_id, path="publisher_id")
    _canonical_url(observation.canonical_url, path="canonical_url")
    _identifier(observation.provider_article_id, path="provider_article_id")
    _timestamp_text(observation.publisher_published_at, path="publisher_published_at")
    _timestamp_text(observation.provider_available_at, path="provider_available_at")
    _timestamp_text(observation.retrieved_at, path="retrieved_at")
    _sha256(observation.content_sha256, path="content_sha256")
    _sha256(observation.raw_blob_sha256, path="raw_blob_sha256")
    _text(observation.entitlement_status, path="entitlement_status")
    _text(observation.redistribution_status, path="redistribution_status")
    _revision_link_payload(observation.revision_of)
    _text(observation.retrieval_status, path="retrieval_status")
    _text(observation.headline, path="headline")
    _text(observation.body, path="body")
    if observation.content_sha256 != news_content_sha256(observation.headline, observation.body):
        _reject(
            NewsObservationReason.CONTENT_HASH_MISMATCH,
            "content_sha256",
            "does not bind the canonical headline/body bytes",
        )


def news_observation_payload(observation: NewsObservation) -> dict[str, object]:
    """Return the complete strict JSON object for one news observation."""

    _validate_shape(observation)
    return {
        "schema": NEWS_OBSERVATION_SCHEMA,
        "schema_version": NEWS_OBSERVATION_SCHEMA_VERSION,
        "observation_id": observation.observation_id,
        "source_id": observation.source_id,
        "source_policy_sha256": observation.source_policy_sha256,
        "publisher_id": observation.publisher_id,
        "canonical_url": observation.canonical_url,
        "provider_article_id": observation.provider_article_id,
        "publisher_published_at": _timestamp_text(
            observation.publisher_published_at, path="publisher_published_at"
        ),
        "provider_available_at": _timestamp_text(
            observation.provider_available_at, path="provider_available_at"
        ),
        "retrieved_at": _timestamp_text(observation.retrieved_at, path="retrieved_at"),
        "content_sha256": observation.content_sha256,
        "raw_blob_sha256": observation.raw_blob_sha256,
        "entitlement_status": observation.entitlement_status,
        "redistribution_status": observation.redistribution_status,
        "revision_of": _revision_link_payload(observation.revision_of),
        "retrieval_status": observation.retrieval_status,
        "headline": observation.headline,
        "body": observation.body,
    }


def news_observation_bytes(observation: NewsObservation) -> bytes:
    """Serialize one observation to its sole canonical UTF-8 JSON form."""

    return canonical_json_bytes(news_observation_payload(observation))


def news_observation_sha256(observation: NewsObservation) -> str:
    """Return the SHA-256 identity of the canonical observation bytes."""

    return sha256_bytes(news_observation_bytes(observation))


def _parse_revision_link(value: object) -> NewsRevisionLink | None:
    if value is None:
        return None
    payload = _strict_object(value, path="revision_of", fields=_REVISION_LINK_FIELDS)
    return NewsRevisionLink(
        observation_id=_identifier(payload["observation_id"], path="revision_of.observation_id"),
        observation_sha256=_sha256(
            payload["observation_sha256"],
            path="revision_of.observation_sha256",
        ),
    )


def parse_news_observation(raw: bytes) -> NewsObservation:
    """Parse only exact canonical observation bytes and reject unknown fields."""

    payload = _strict_object(_decode(raw), path="document", fields=_OBSERVATION_FIELDS)
    if (
        payload["schema"] != NEWS_OBSERVATION_SCHEMA
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != NEWS_OBSERVATION_SCHEMA_VERSION
    ):
        _reject(
            NewsObservationReason.UNSUPPORTED_SCHEMA,
            "document",
            f"expected {NEWS_OBSERVATION_SCHEMA}/v{NEWS_OBSERVATION_SCHEMA_VERSION}",
        )
    observation = NewsObservation(
        observation_id=_identifier(payload["observation_id"], path="observation_id"),
        source_id=_identifier(payload["source_id"], path="source_id"),
        source_policy_sha256=_sha256(payload["source_policy_sha256"], path="source_policy_sha256"),
        publisher_id=_identifier(payload["publisher_id"], path="publisher_id"),
        canonical_url=_canonical_url(payload["canonical_url"], path="canonical_url"),
        provider_article_id=_identifier(payload["provider_article_id"], path="provider_article_id"),
        publisher_published_at=_timestamp(
            payload["publisher_published_at"], path="publisher_published_at"
        ),
        provider_available_at=_timestamp(
            payload["provider_available_at"],
            path="provider_available_at",
        ),
        retrieved_at=_timestamp(payload["retrieved_at"], path="retrieved_at"),
        content_sha256=_sha256(payload["content_sha256"], path="content_sha256"),
        raw_blob_sha256=_sha256(payload["raw_blob_sha256"], path="raw_blob_sha256"),
        entitlement_status=_text(payload["entitlement_status"], path="entitlement_status"),
        redistribution_status=_text(payload["redistribution_status"], path="redistribution_status"),
        revision_of=_parse_revision_link(payload["revision_of"]),
        retrieval_status=_text(payload["retrieval_status"], path="retrieval_status"),
        headline=_text(payload["headline"], path="headline"),
        body=_text(payload["body"], path="body"),
    )
    _validate_shape(observation)
    if raw != news_observation_bytes(observation):
        _reject(
            NewsObservationReason.NON_CANONICAL_DOCUMENT,
            "document",
            "bytes do not match the sole canonical serialization",
        )
    return observation


def _source_authorization(
    observation: NewsObservation,
    source_authorizations: Mapping[str, NewsSourceAuthorization],
) -> NewsSourceAuthorization:
    if not isinstance(source_authorizations, Mapping):
        _reject(
            NewsObservationReason.INVALID_DOCUMENT,
            "source_authorizations",
            "must be a mapping of source IDs to host-owned authorizations",
        )
    configured = source_authorizations.get(observation.source_id)
    if configured is None:
        _reject(NewsObservationReason.UNKNOWN_SOURCE, "source_id", "source is not authorized")
    if not isinstance(configured, NewsSourceAuthorization):
        _reject(
            NewsObservationReason.INVALID_DOCUMENT,
            f"source_authorizations.{observation.source_id}",
            "must be a NewsSourceAuthorization",
        )
    _identifier(configured.source_id, path="source_authorization.source_id")
    _sha256(
        configured.source_policy_sha256,
        path="source_authorization.source_policy_sha256",
    )
    _text(configured.verdict, path="source_authorization.verdict")
    if (
        not isinstance(configured.publisher_ids, tuple)
        or not configured.publisher_ids
        or len(configured.publisher_ids) != len(set(configured.publisher_ids))
    ):
        _reject(
            NewsObservationReason.INVALID_DOCUMENT,
            "source_authorization.publisher_ids",
            "must be a non-empty unique tuple",
        )
    for index, publisher_id in enumerate(configured.publisher_ids):
        _identifier(publisher_id, path=f"source_authorization.publisher_ids[{index}]")
    if (
        not isinstance(configured.canonical_url_prefixes, tuple)
        or not configured.canonical_url_prefixes
        or len(configured.canonical_url_prefixes) != len(set(configured.canonical_url_prefixes))
    ):
        _reject(
            NewsObservationReason.INVALID_DOCUMENT,
            "source_authorization.canonical_url_prefixes",
            "must be a non-empty unique tuple",
        )
    for index, prefix in enumerate(configured.canonical_url_prefixes):
        _canonical_url(prefix, path=f"source_authorization.canonical_url_prefixes[{index}]")
        if not prefix.endswith("/"):
            _reject(
                NewsObservationReason.INVALID_DOCUMENT,
                f"source_authorization.canonical_url_prefixes[{index}]",
                "must end with a path separator",
            )
    if configured.redistribution_status not in {
        "REDISTRIBUTABLE",
        "NON_REDISTRIBUTABLE",
    }:
        _reject(
            NewsObservationReason.INVALID_DOCUMENT,
            "source_authorization.redistribution_status",
            "must be a registered redistribution status",
        )
    if configured.source_id != observation.source_id:
        _reject(
            NewsObservationReason.UNKNOWN_SOURCE,
            "source_authorization.source_id",
            "authorization identity differs from the observation source",
        )
    if configured.source_policy_sha256 != observation.source_policy_sha256:
        _reject(
            NewsObservationReason.SOURCE_POLICY_MISMATCH,
            "source_policy_sha256",
            "observation policy differs from the host-approved source policy",
        )
    if observation.publisher_id not in configured.publisher_ids or not any(
        observation.canonical_url.startswith(prefix) for prefix in configured.canonical_url_prefixes
    ):
        _reject(
            NewsObservationReason.SOURCE_METADATA_MISMATCH,
            "publisher_id",
            "publisher or canonical URL is outside the host-approved source policy",
        )
    if configured.redistribution_status != observation.redistribution_status:
        _reject(
            NewsObservationReason.SOURCE_RIGHTS_MISMATCH,
            "redistribution_status",
            "observation rights differ from the host-approved source policy",
        )
    if configured.verdict != "FEASIBLE":
        _reject(
            NewsObservationReason.BLOCKED_SOURCE,
            "source_authorization.verdict",
            "source is not FEASIBLE in the host-approved policy",
        )
    return configured


def _validate_statuses(observation: NewsObservation) -> None:
    if observation.entitlement_status != "FEASIBLE":
        _reject(
            NewsObservationReason.ENTITLEMENT_NOT_FEASIBLE,
            "entitlement_status",
            "fixture observations require the exact FEASIBLE status",
        )
    if (
        not isinstance(observation.redistribution_status, str)
        or not observation.redistribution_status
    ):
        _reject(
            NewsObservationReason.REDISTRIBUTION_MISSING,
            "redistribution_status",
            "must be explicitly declared",
        )
    if observation.redistribution_status == "UNKNOWN":
        _reject(
            NewsObservationReason.REDISTRIBUTION_UNKNOWN,
            "redistribution_status",
            "unknown redistribution status fails closed",
        )
    if observation.redistribution_status not in {"REDISTRIBUTABLE", "NON_REDISTRIBUTABLE"}:
        _reject(
            NewsObservationReason.REDISTRIBUTION_UNKNOWN,
            "redistribution_status",
            "is not a registered redistribution status",
        )
    if observation.retrieval_status != "COMPLETE":
        _reject(
            NewsObservationReason.RETRIEVAL_INCOMPLETE,
            "retrieval_status",
            "partial or unknown pagination never enters normalized evidence",
        )


def _batch_items(
    observation: NewsObservation, input_batch: Sequence[NewsObservation] | None
) -> tuple[NewsObservation, ...]:
    if input_batch is None:
        return (observation,)
    if isinstance(input_batch, (str, bytes)):
        _reject(NewsObservationReason.INVALID_DOCUMENT, "input_batch", "must be a sequence")
    items = tuple(input_batch)
    if observation not in items:
        items = (observation, *items)
    for index, item in enumerate(items):
        if not isinstance(item, NewsObservation):
            _reject(
                NewsObservationReason.INVALID_DOCUMENT,
                f"input_batch[{index}]",
                "must contain only NewsObservation values",
            )
    return items


def _validate_batch_relationships(items: Sequence[NewsObservation]) -> None:
    ids: dict[str, NewsObservation] = {}
    content_hashes: set[str] = set()
    for item in items:
        if item.observation_id in ids:
            _reject(
                NewsObservationReason.DUPLICATE_OBSERVATION,
                "input_batch.observation_id",
                f"duplicate observation_id {item.observation_id}",
            )
        ids[item.observation_id] = item
        if item.content_sha256 in content_hashes:
            _reject(
                NewsObservationReason.DUPLICATE_CONTENT,
                "input_batch.content_sha256",
                "the same article content cannot enter a batch twice",
            )
        content_hashes.add(item.content_sha256)

    for item in items:
        link = item.revision_of
        if link is None:
            continue
        prior = ids.get(link.observation_id)
        if prior is None:
            _reject(
                NewsObservationReason.INVALID_REVISION_LINK,
                "revision_of.observation_id",
                "the linked prior observation is absent from this batch",
            )
        if link.observation_id == item.observation_id:
            _reject(
                NewsObservationReason.INVALID_REVISION_LINK,
                "revision_of.observation_id",
                "a revision cannot link to itself",
            )
        if link.observation_sha256 != news_observation_sha256(prior):
            _reject(
                NewsObservationReason.INVALID_REVISION_LINK,
                "revision_of.observation_sha256",
                "does not match the linked prior canonical observation",
            )

    for item in items:
        visited: set[str] = set()
        current = item
        while current.revision_of is not None:
            next_id = current.revision_of.observation_id
            if next_id in visited:
                _reject(
                    NewsObservationReason.INVALID_REVISION_LINK,
                    "revision_of",
                    "revision links must not form a cycle",
                )
            visited.add(next_id)
            current = ids[next_id]


def create_news_revision(
    prior: NewsObservation,
    *,
    observation_id: str,
    headline: str,
    body: str,
    raw_blob_sha256: str,
    publisher_published_at: datetime | None = None,
    provider_available_at: datetime | None = None,
    retrieved_at: datetime | None = None,
) -> NewsObservation:
    """Create a new immutable revision; it never alters ``prior`` in place."""

    _validate_shape(prior)
    _identifier(observation_id, path="observation_id")
    if observation_id == prior.observation_id:
        _reject(
            NewsObservationReason.INVALID_REVISION_LINK,
            "observation_id",
            "a revision must receive a new observation_id",
        )
    _sha256(raw_blob_sha256, path="raw_blob_sha256")
    return NewsObservation(
        observation_id=observation_id,
        source_id=prior.source_id,
        source_policy_sha256=prior.source_policy_sha256,
        publisher_id=prior.publisher_id,
        canonical_url=prior.canonical_url,
        provider_article_id=prior.provider_article_id,
        publisher_published_at=(
            prior.publisher_published_at
            if publisher_published_at is None
            else publisher_published_at
        ),
        provider_available_at=(
            prior.provider_available_at if provider_available_at is None else provider_available_at
        ),
        retrieved_at=prior.retrieved_at if retrieved_at is None else retrieved_at,
        content_sha256=news_content_sha256(headline, body),
        raw_blob_sha256=raw_blob_sha256,
        entitlement_status=prior.entitlement_status,
        redistribution_status=prior.redistribution_status,
        revision_of=NewsRevisionLink(
            observation_id=prior.observation_id,
            observation_sha256=news_observation_sha256(prior),
        ),
        retrieval_status=prior.retrieval_status,
        headline=headline,
        body=body,
    )


def validate_news_observation(
    observation: NewsObservation,
    source_authorizations: Mapping[str, NewsSourceAuthorization],
    decision_cutoff_at: datetime,
    *,
    input_batch: Sequence[NewsObservation] | None = None,
) -> None:
    """Fail closed unless one fixture observation is valid at the decision cutoff."""

    if not isinstance(observation, NewsObservation):
        _reject(NewsObservationReason.INVALID_DOCUMENT, "observation", "must be a NewsObservation")
    _validate_statuses(observation)
    _validate_shape(observation)
    _timestamp_text(decision_cutoff_at, path="decision_cutoff_at")
    _source_authorization(observation, source_authorizations)
    if not (
        observation.publisher_published_at
        <= observation.provider_available_at
        <= observation.retrieved_at
    ):
        _reject(
            NewsObservationReason.CLOCK_ORDER_INVALID,
            "publisher_published_at",
            "publisher, availability, and retrieval clocks must be ordered",
        )
    if observation.provider_available_at > decision_cutoff_at:
        _reject(
            NewsObservationReason.AFTER_DECISION_CUTOFF,
            "provider_available_at",
            "provider availability is after the decision cutoff",
        )
    if observation.retrieved_at > decision_cutoff_at:
        _reject(
            NewsObservationReason.AFTER_DECISION_CUTOFF,
            "retrieved_at",
            "retrieval is after the decision cutoff",
        )
    batch = _batch_items(observation, input_batch)
    if observation.revision_of is not None and input_batch is None:
        _reject(
            NewsObservationReason.INVALID_REVISION_LINK,
            "revision_of",
            "validation of a revision requires its prior record in input_batch",
        )
    _validate_batch_relationships(batch)


def _normalize_one(observation: NewsObservation) -> NormalizedNewsEvidence:
    provenance = SourceProvenance(
        source_class=NEWS_SOURCE_CLASS,
        publisher=observation.publisher_id,
        content_sha256=observation.content_sha256,
        published_at=observation.publisher_published_at,
        published_at_precision="MICROSECOND",
        retrieved_at=observation.retrieved_at,
        entitlement="ENTITLED",
        redistribution_status=observation.redistribution_status,
        limitations=("FIXTURE_ONLY", "LIVE_NEWS_UNVERIFIED_BLOCKED"),
    )
    evidence_entry = EvidenceEntry(
        evidence_id=f"NEWS:{observation.observation_id}",
        role=EvidenceRole.PERMITTED_NEWS,
        receipt=SourceReceipt.from_provenance(
            f"NEWS_RECEIPT:{observation.observation_id}", provenance
        ),
        pages_retrieved=1,
        pages_total=1,
    )
    return NormalizedNewsEvidence(
        evidence_entry=evidence_entry,
        observation_id=observation.observation_id,
        observation_sha256=news_observation_sha256(observation),
        source_policy_sha256=observation.source_policy_sha256,
        raw_blob_sha256=observation.raw_blob_sha256,
        provider_article_id=observation.provider_article_id,
        canonical_url=observation.canonical_url,
        provider_available_at=observation.provider_available_at,
        revision_of=observation.revision_of,
        headline=UntrustedQuotedText(observation.headline),
        body=UntrustedQuotedText(observation.body),
    )


def normalize_news_observations(
    observations: Sequence[NewsObservation],
    source_authorizations: Mapping[str, NewsSourceAuthorization],
    decision_cutoff_at: datetime,
) -> tuple[NormalizedNewsEvidence, ...]:
    """Validate and deterministically adapt fixture observations to evidence receipts.

    Headline and body remain ``UntrustedQuotedText``.  This function has no
    instruction, tool, authority, risk, order, model, or network behaviour.
    """

    if isinstance(observations, (str, bytes)):
        _reject(NewsObservationReason.INVALID_DOCUMENT, "observations", "must be a sequence")
    items = tuple(observations)
    for observation in items:
        validate_news_observation(
            observation,
            source_authorizations,
            decision_cutoff_at,
            input_batch=items,
        )
    sorted_items = sorted(items, key=lambda item: item.observation_id)
    return tuple(_normalize_one(item) for item in sorted_items)
