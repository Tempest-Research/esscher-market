"""Shared fail-closed market-observation validation for Gate D.

The compiler and the read-only tournament consume the same immutable market
snapshot. These helpers keep their pinned-feed, freshness, quote-quality,
package, and borrow/locate boundaries identical without adding any execution
or account authority.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from ringdown_market.execution.expression.observations import (
    EXECUTABLE_DATA,
    INDICATIVE_DATA,
    BorrowLocateEvidence,
    ExpressionMarketSnapshot,
    FeedIdentity,
    PackageObservation,
    TwoSidedQuote,
)
from ringdown_market.execution.expression.policy import PromotedExpressionPolicy
from ringdown_market.execution.expression.reasons import (
    ExpressionReason,
    ExpressionRejected,
)

# Pinned read-only feed identities. Unknown feeds fail closed; a feed identity
# is never inferred.  The ALPACA_* identities are the honest live-door feeds
# (owner-approved demo lane, #68/#101): pinning an identity does not relax the
# data-class gate - Basic-plan feeds must still arrive labeled INDICATIVE_DATA
# and pass only under a promoted policy that explicitly allows indicative data.
PINNED_FEEDS = frozenset(
    {
        ("SYNTHETIC_SIP_EQUITY_FEED", "read_only_equity_quote", "equity_quote.v1", "1"),
        (
            "SYNTHETIC_OPTION_SNAPSHOT_FEED",
            "read_only_option_chain",
            "option_chain_snapshot.v1",
            "1",
        ),
        (
            "SYNTHETIC_PACKAGE_FEED",
            "read_only_package_quote",
            "package_quote.v1",
            "1",
        ),
        ("ALPACA_IEX_EQUITY_QUOTES", "read_only_equity_quote", "equity_quote.v1", "1"),
        (
            "ALPACA_INDICATIVE_OPTION_SNAPSHOTS",
            "read_only_option_chain",
            "option_chain_snapshot.v1",
            "1",
        ),
        (
            "ALPACA_OPRA_OPTION_SNAPSHOTS",
            "read_only_option_chain",
            "option_chain_snapshot.v1",
            "1",
        ),
        (
            "HOST_DERIVED_VERTICAL_PACKAGES",
            "derived_package_quote",
            "package_quote.v1",
            "1",
        ),
    }
)


def _reject(reason: ExpressionReason, path: str, detail: str) -> ExpressionRejected:
    return ExpressionRejected(reason, path, detail)


def validate_feed(feed: FeedIdentity, path: str) -> None:
    """Require one declared read-only feed identity from the frozen allow-list."""

    if feed.identity_key() not in PINNED_FEEDS:
        raise _reject(
            ExpressionReason.UNKNOWN_FEED,
            path,
            f"feed identity {feed.identity_key()} is not pinned",
        )


def validate_executable_data(
    data_class: str, path: str, *, allows_indicative: bool = False
) -> None:
    """Reject indicative observations before they can become fill evidence.

    ``allows_indicative`` is threaded only from an explicitly flagged promoted
    policy (the owner-approved delayed-demo lane); the default keeps the frozen
    behaviour everywhere else.
    """

    if data_class == EXECUTABLE_DATA:
        return
    if allows_indicative and data_class == INDICATIVE_DATA:
        return
    raise _reject(
        ExpressionReason.INDICATIVE_ONLY,
        path,
        "indicative observations are never executable-fill evidence",
    )


def _observation_age(
    observed_at: datetime,
    *,
    snapshot: ExpressionMarketSnapshot,
    path: str,
) -> timedelta:
    age = snapshot.observation_clock_at - observed_at
    if age < timedelta():
        raise _reject(
            ExpressionReason.TIME_INCONSISTENT,
            path,
            "observation postdates the snapshot clock",
        )
    return age


def validate_quote(
    quote: TwoSidedQuote,
    *,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    path: str,
) -> None:
    """Require a fresh, two-sided, sized, bounded-spread quote."""

    age = _observation_age(quote.observed_at, snapshot=snapshot, path=path)
    if age > timedelta(milliseconds=policy.quote_max_age_ms):
        raise _reject(
            ExpressionReason.STALE_QUOTE,
            path,
            f"quote age {age} exceeds the frozen bound",
        )
    if quote.bid <= 0 or quote.ask <= 0:
        raise _reject(ExpressionReason.NO_QUOTE, path, "quote has no two-sided market")
    if quote.crossed:
        raise _reject(ExpressionReason.CROSSED_QUOTE, path, "quote is crossed")
    if quote.bid_size < policy.min_quote_size or quote.ask_size < policy.min_quote_size:
        raise _reject(
            ExpressionReason.INSUFFICIENT_SIZE,
            path,
            "quote size is below the frozen minimum",
        )
    spread_bps = quote.spread / quote.ask * Decimal(10000)
    if spread_bps > policy.spread_max_bps:
        raise _reject(
            ExpressionReason.SPREAD_TOO_WIDE,
            path,
            f"spread {spread_bps}bps exceeds the frozen bound",
        )


def validate_cross_leg_skew(
    quotes: Sequence[tuple[TwoSidedQuote, str]],
    *,
    policy: PromotedExpressionPolicy,
) -> None:
    """Require one atomic vertical's leg quotes to share a bounded observation skew."""

    if len(quotes) < 2:
        return
    times = sorted(quote.observed_at for quote, _ in quotes)
    skew = times[-1] - times[0]
    if skew > timedelta(milliseconds=policy.cross_leg_skew_max_ms):
        raise _reject(
            ExpressionReason.ASYNCHRONOUS_QUOTES,
            "snapshot.skew",
            f"cross-leg skew {skew} exceeds the frozen bound",
        )


def validate_package(
    package: PackageObservation,
    *,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    path: str,
) -> None:
    """Require a fresh, pinned, executable atomic-package market."""

    validate_feed(package.feed, f"{path}.feed")
    validate_executable_data(
        package.data_class,
        f"{path}.data_class",
        allows_indicative=policy.allows_indicative_data,
    )
    age = _observation_age(package.observed_at, snapshot=snapshot, path=path)
    if age > timedelta(milliseconds=policy.quote_max_age_ms):
        raise _reject(
            ExpressionReason.STALE_QUOTE,
            path,
            f"package age {age} exceeds the frozen bound",
        )
    if package.net_bid <= 0 or package.net_ask <= 0:
        raise _reject(ExpressionReason.PACKAGE_UNAVAILABLE, path, "package has no two-sided market")
    if package.crossed:
        raise _reject(ExpressionReason.CROSSED_QUOTE, path, "package quote is crossed")
    if package.size < policy.min_quote_size:
        raise _reject(
            ExpressionReason.INSUFFICIENT_SIZE,
            path,
            "package size is below the frozen minimum",
        )
    spread_bps = (package.net_ask - package.net_bid) / package.net_ask * Decimal(10000)
    if spread_bps > policy.spread_max_bps:
        raise _reject(
            ExpressionReason.SPREAD_TOO_WIDE,
            path,
            f"package spread {spread_bps}bps exceeds the frozen bound",
        )


def validate_borrow_locate(
    borrow_locate: BorrowLocateEvidence | None,
    *,
    snapshot: ExpressionMarketSnapshot,
    policy: PromotedExpressionPolicy,
    path: str = "borrow_locate",
) -> None:
    """Require pre-clock, fresh explicit borrow evidence for a short share expression."""

    if borrow_locate is None:
        raise _reject(
            ExpressionReason.BORROW_LOCATE_MISSING,
            path,
            "short shares require explicit borrow/locate evidence",
        )
    age = _observation_age(borrow_locate.observed_at, snapshot=snapshot, path=path)
    if age > timedelta(milliseconds=policy.quote_max_age_ms):
        raise _reject(
            ExpressionReason.STALE_QUOTE,
            path,
            f"borrow/locate age {age} exceeds the frozen bound",
        )
