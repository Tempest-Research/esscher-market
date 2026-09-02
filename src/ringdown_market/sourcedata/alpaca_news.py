"""Private-use, fail-closed adapter for the official Alpaca MCP news tool.

This module owns retrieval only.  It has no network, credential, account,
broker, model, instruction, or order surface: a caller injects the one-tool
session.  Article text is retained only as ``UntrustedQuotedText``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from inspect import isawaitable
from typing import Protocol

from ringdown_market.sourcedata.news import (
    NewsObservation,
    NewsSourceAuthorization,
    NormalizedNewsEvidence,
    UntrustedQuotedText,
    create_news_revision,
    news_content_sha256,
    news_observation_sha256,
    normalize_news_observations,
    validate_news_observation,
)
from ringdown_market.strategy.contracts import canonical_json_bytes

TOOL_NAME = "get_news"
SOURCE_ID = "ALPACA_BENZINGA_NEWS"
PUBLISHER_ID = "BENZINGA"
SOURCE_URL_PREFIX = "https://www.benzinga.com/"
_URL = re.compile(r"^https://www\.benzinga\.com/[^\s#]+$")
REDISTRIBUTION_STATUS = "NON_REDISTRIBUTABLE"
SOURCE_POLICY_BYTES = canonical_json_bytes(
    {
        "provider_available_at": "RETRIEVAL_TIME",
        "publisher_ids": [PUBLISHER_ID],
        "redistribution_status": REDISTRIBUTION_STATUS,
        "retention": "HOST_CONTROLLED_PRIVATE_USE",
        "schema": "esscher.alpaca_news_source_policy",
        "schema_version": 1,
        "source_id": SOURCE_ID,
        "url_prefixes": [SOURCE_URL_PREFIX],
        "usage": "PRIVATE_PAPER_DECISION_EVIDENCE_ONLY",
    }
)
SOURCE_POLICY_SHA256 = sha256(SOURCE_POLICY_BYTES).hexdigest()
ARTICLE_FIELDS = frozenset(
    {
        "author",
        "content",
        "created_at",
        "headline",
        "id",
        "images",
        "source",
        "summary",
        "symbols",
        "updated_at",
        "url",
    }
)


class AlpacaNewsToolSession(Protocol):
    def call(self, tool_name: str, arguments: Mapping[str, object]) -> Awaitable[object]: ...


class RetrievalStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class AlpacaNewsRequest:
    symbols: tuple[str, ...]
    start_at: datetime
    end_at: datetime
    decision_cutoff_at: datetime
    retrieved_at: datetime
    page_size: int
    max_pages: int
    source_policy_sha256: str
    source_id: str = SOURCE_ID
    publisher_id: str = PUBLISHER_ID
    canonical_url_prefix: str = SOURCE_URL_PREFIX
    sort: str = "desc"
    include_content: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.symbols, tuple) or not self.symbols:
            raise ValueError("symbols must be sorted and unique")
        if any(
            not isinstance(s, str) or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) is None
            for s in self.symbols
        ):
            raise ValueError("symbols must be normalized symbols")
        object.__setattr__(self, "symbols", tuple(sorted(set(self.symbols))))
        for value in (self.start_at, self.end_at, self.decision_cutoff_at, self.retrieved_at):
            _clock(value)
        if (
            not self.start_at <= self.end_at <= self.decision_cutoff_at
            or self.retrieved_at > self.decision_cutoff_at
        ):
            raise ValueError("request clocks exceed cutoff or are unordered")
        if type(self.page_size) is not int or self.page_size < 1 or self.page_size > 50:
            raise ValueError("invalid page size")
        if type(self.max_pages) is not int or self.max_pages < 1:
            raise ValueError("invalid max_pages")
        if (
            not isinstance(self.source_policy_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_policy_sha256) is None
            or self.source_policy_sha256 != SOURCE_POLICY_SHA256
        ):
            raise ValueError("invalid source policy hash")
        if (
            self.source_id != SOURCE_ID
            or self.publisher_id != PUBLISHER_ID
            or self.canonical_url_prefix != SOURCE_URL_PREFIX
            or self.sort != "desc"
            or self.include_content is not True
        ):
            raise ValueError("source policy/request identity is not frozen")


@dataclass(frozen=True, slots=True)
class ArticleAttribution:
    """Public routing metadata bound to one normalized observation identity."""

    provider_article_id: str
    symbols: tuple[str, ...]
    observation_id: str
    observation_sha256: str

    def __post_init__(self) -> None:
        if not self.provider_article_id or not self.observation_id:
            raise ValueError("article attribution identities must be non-empty")
        if not self.symbols or self.symbols != tuple(sorted(set(self.symbols))):
            raise ValueError("article attribution symbols must be sorted and unique")
        if re.fullmatch(r"[0-9a-f]{64}", self.observation_sha256) is None:
            raise ValueError("article attribution hash must be SHA-256")


@dataclass(frozen=True, slots=True)
class AlpacaNewsRetrieval:
    status: RetrievalStatus
    reason: str
    request_sha256: str
    page_sha256: tuple[str, ...]
    article_sha256: tuple[str, ...]
    observation_sha256: tuple[str, ...]
    pages_retrieved: int
    articles_retrieved: int
    retrieved_at: datetime
    article_attributions: tuple[ArticleAttribution, ...]
    observations: tuple[NormalizedNewsEvidence, ...]
    evidence: tuple[NormalizedNewsEvidence, ...]
    sanitized_receipt: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.status is RetrievalStatus.INCOMPLETE and (
            self.article_attributions or self.observations or self.evidence
        ):
            raise ValueError("incomplete retrieval cannot carry evidence")


def _clock(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is not UTC:
        raise ValueError("clock must be timezone-aware UTC")
    return value.astimezone(UTC)


def _digest(value: object) -> str:
    _reject_floats(value)
    return sha256(canonical_json_bytes(value)).hexdigest()


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise ValueError("floats are forbidden")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_floats(item)


def _strict_fields(value: Mapping[str, object], expected: frozenset[str], path: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{path} fields are not exact")


def _article(
    raw: object, request: AlpacaNewsRequest
) -> tuple[NewsObservation, str, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        raise ValueError("article wrapper must be an object")
    _strict_fields(raw, ARTICLE_FIELDS, "article")
    _reject_floats(raw)
    ident = raw["id"]
    if type(ident) is int and ident > 0:
        article_id = str(ident)
    elif isinstance(ident, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", ident):
        article_id = ident
    else:
        raise ValueError("malformed article ID")
    if (
        raw["source"] != "benzinga"
        or not isinstance(raw["headline"], str)
        or not raw["headline"]
        or not isinstance(raw["content"], str)
        or not raw["content"]
    ):
        raise ValueError("source or article text is invalid")
    for field in ("author", "summary", "url"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise ValueError(f"{field} is invalid")
    if not isinstance(raw["images"], list):
        raise ValueError("images must be a list")
    symbols = raw["symbols"]
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(
            not isinstance(s, str) or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) is None
            for s in symbols
        )
    ):
        raise ValueError("malformed symbols")
    normalized_symbols = tuple(sorted(set(symbols)))
    created, updated = _parse_clock(raw["created_at"]), _parse_clock(raw["updated_at"])
    if (
        created > updated
        or updated > request.decision_cutoff_at
        or created < request.start_at
        or created > request.end_at
    ):
        raise ValueError("article clock exceeds cutoff")
    if not isinstance(raw["url"], str) or _URL.fullmatch(raw["url"]) is None:
        raise ValueError("article URL is outside source policy")
    raw_hash = _digest(raw)
    observation_id = (
        "NEWS-" + _digest({"source": request.source_id, "id": article_id, "raw": raw_hash})[:48]
    )
    observation = NewsObservation(
        observation_id=observation_id,
        source_id=request.source_id,
        source_policy_sha256=request.source_policy_sha256,
        publisher_id=request.publisher_id,
        canonical_url=raw["url"],
        provider_article_id=article_id,
        publisher_published_at=created,
        provider_available_at=request.retrieved_at,
        retrieved_at=request.retrieved_at,
        content_sha256=news_content_sha256(raw["headline"], raw["content"]),
        raw_blob_sha256=raw_hash,
        entitlement_status="FEASIBLE",
        redistribution_status=REDISTRIBUTION_STATUS,
        revision_of=None,
        retrieval_status="COMPLETE",
        headline=raw["headline"],
        body=raw["content"],
    )
    return observation, raw_hash, normalized_symbols


def _parse_clock(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value
    ):
        raise ValueError("clock must be canonical UTC text")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("malformed clock") from error
    canonical = parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if canonical.endswith(".000000Z"):
        canonical = canonical.removesuffix(".000000Z") + "Z"
    else:
        prefix, fraction = canonical[:-1].split(".")
        canonical = f"{prefix}.{fraction.rstrip('0')}Z"
    if canonical != value:
        raise ValueError("noncanonical clock")
    return parsed


def _args(request: AlpacaNewsRequest, token: str | None) -> dict[str, object]:
    result: dict[str, object] = {
        "symbols": ",".join(request.symbols),
        "start": request.start_at.isoformat().replace("+00:00", "Z"),
        "end": request.end_at.isoformat().replace("+00:00", "Z"),
        "sort": "desc",
        "include_content": True,
        "exclude_contentless": True,
        "limit": request.page_size,
    }
    if token is not None:
        result["page_token"] = token
    return result


async def retrieve_alpaca_news(
    session: AlpacaNewsToolSession,
    request: AlpacaNewsRequest,
    *,
    prior_observations: Mapping[str, NewsObservation] | None = None,
) -> AlpacaNewsRetrieval:
    request_hash = _digest(
        {
            "symbols": request.symbols,
            "start": request.start_at.isoformat(),
            "end": request.end_at.isoformat(),
            "cutoff": request.decision_cutoff_at.isoformat(),
            "retrieved_at": request.retrieved_at.isoformat(),
            "page_size": request.page_size,
            "max_pages": request.max_pages,
            "source_id": request.source_id,
            "publisher_id": request.publisher_id,
            "policy": request.source_policy_sha256,
        }
    )
    pages: list[str] = []
    observations: list[NewsObservation] = []
    attributions_by_observation_id: dict[str, ArticleAttribution] = {}
    validation_priors: list[NewsObservation] = []
    articles: list[str] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    reason = "COMPLETE"
    try:
        for _ in range(request.max_pages):
            response = session.call(TOOL_NAME, _args(request, token))
            response = await response if isawaitable(response) else response
            if (
                not isinstance(response, Mapping)
                or set(response) != {"news", "next_page_token"}
                or not isinstance(response["news"], list)
            ):
                raise ValueError("malformed provider wrapper")
            next_token = response["next_page_token"]
            if next_token is not None and (
                not isinstance(next_token, str) or not next_token or next_token in seen_tokens
            ):
                raise ValueError("malformed or repeated page token")
            page_hash = _digest(response)
            pages.append(page_hash)
            for raw in response["news"]:
                observation, raw_hash, symbols = _article(raw, request)
                prior = (
                    None
                    if prior_observations is None
                    else prior_observations.get(observation.provider_article_id)
                )
                if prior is not None and not isinstance(prior, NewsObservation):
                    raise ValueError("prior observation mapping contains an invalid value")
                if prior is not None and prior.raw_blob_sha256 != observation.raw_blob_sha256:
                    validation_priors.append(prior)
                    observation = create_news_revision(
                        prior,
                        observation_id=observation.observation_id,
                        headline=observation.headline,
                        body=observation.body,
                        raw_blob_sha256=observation.raw_blob_sha256,
                        publisher_published_at=observation.publisher_published_at,
                        provider_available_at=observation.provider_available_at,
                        retrieved_at=observation.retrieved_at,
                    )
                if observation.provider_article_id in {x.provider_article_id for x in observations}:
                    raise ValueError("duplicate article ID")
                observations.append(observation)
                observation_hash = news_observation_sha256(observation)
                attributions_by_observation_id[observation.observation_id] = ArticleAttribution(
                    provider_article_id=observation.provider_article_id,
                    symbols=symbols,
                    observation_id=observation.observation_id,
                    observation_sha256=observation_hash,
                )
                articles.append(raw_hash)
            if next_token is None:
                break
            seen_tokens.add(next_token)
            token = next_token
        else:
            raise ValueError("pagination exceeded max_pages")
        if token is not None and not pages:
            raise ValueError("missing final page")
        authorization = {
            request.source_id: NewsSourceAuthorization(
                request.source_id,
                request.source_policy_sha256,
                "FEASIBLE",
                (request.publisher_id,),
                (request.canonical_url_prefix,),
                REDISTRIBUTION_STATUS,
            )
        }
        batch = tuple(observations)
        validation_batch = tuple(validation_priors) + batch
        for observation in batch:
            validate_news_observation(
                observation,
                authorization,
                request.decision_cutoff_at,
                input_batch=validation_batch,
            )
        normalized_all = normalize_news_observations(
            validation_batch, authorization, request.decision_cutoff_at
        )
        current_ids = {item.observation_id for item in batch}
        normalized = tuple(item for item in normalized_all if item.observation_id in current_ids)
        normalized_attributions = tuple(
            attributions_by_observation_id[item.observation_id] for item in normalized
        )
        observation_hashes = tuple(item.observation_sha256 for item in normalized)
        attribution_receipt = [
            {
                "observation_id": item.observation_id,
                "observation_sha256": item.observation_sha256,
                "provider_article_id": item.provider_article_id,
                "symbols": list(item.symbols),
            }
            for item in normalized_attributions
        ]
        return AlpacaNewsRetrieval(
            status=RetrievalStatus.COMPLETE,
            reason=reason,
            request_sha256=request_hash,
            page_sha256=tuple(pages),
            article_sha256=tuple(articles),
            observation_sha256=observation_hashes,
            pages_retrieved=len(pages),
            articles_retrieved=len(batch),
            retrieved_at=request.retrieved_at,
            article_attributions=normalized_attributions,
            observations=normalized,
            evidence=normalized,
            sanitized_receipt={
                "schema": "esscher.alpaca_news_receipt",
                "status": "COMPLETE",
                "request_sha256": request_hash,
                "page_sha256": pages,
                "article_sha256": articles,
                "pages_retrieved": len(pages),
                "articles_retrieved": len(batch),
                "retrieved_at": request.retrieved_at.isoformat().replace("+00:00", "Z"),
                "source_id": request.source_id,
                "publisher_id": request.publisher_id,
                "source_policy_sha256": request.source_policy_sha256,
                "redistribution_status": REDISTRIBUTION_STATUS,
                "article_attributions": attribution_receipt,
            },
        )
    except Exception as error:
        return AlpacaNewsRetrieval(
            status=RetrievalStatus.INCOMPLETE,
            reason=type(error).__name__.upper(),
            request_sha256=request_hash,
            page_sha256=tuple(pages),
            article_sha256=(),
            observation_sha256=(),
            pages_retrieved=len(pages),
            articles_retrieved=0,
            retrieved_at=request.retrieved_at,
            article_attributions=(),
            observations=(),
            evidence=(),
            sanitized_receipt={
                "schema": "esscher.alpaca_news_receipt",
                "status": "INCOMPLETE",
                "reason": type(error).__name__.upper(),
                "request_sha256": request_hash,
                "pages_retrieved": len(pages),
                "articles_retrieved": 0,
            },
        )


__all__ = [
    "SOURCE_POLICY_BYTES",
    "SOURCE_POLICY_SHA256",
    "TOOL_NAME",
    "AlpacaNewsRequest",
    "AlpacaNewsRetrieval",
    "AlpacaNewsToolSession",
    "ArticleAttribution",
    "RetrievalStatus",
    "UntrustedQuotedText",
    "retrieve_alpaca_news",
]
