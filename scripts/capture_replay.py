"""Deterministic replay adapters + CaptureSourceDoor over live-captured bytes.

The live capture lane (``scripts/capture_lane.py``) fetches once and serializes;
these adapters replay the serialized bytes as the frozen ``EvidenceSource`` /
``MarketDataSource`` protocols with zero I/O at call time, so the production
composition's double compile (probe + ``prepare_v2``) sees byte-identical
sources.  This module holds no network, credential, or host surface; the host
session runner imports it and injects it through ``PaperMcpHostDoors``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from esscher.sourcedata.compiler import CaptureConfiguration
from esscher.sourcedata.interfaces import (
    CorporateAction,
    DailyBar,
    GuidanceStatement,
    IssuerRelease,
    QuarterFact,
    QuoteSample,
    SecurityMasterRecord,
    SessionRecord,
    SourceProvenance,
    Trade,
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _day(value: str) -> date:
    return date.fromisoformat(value)


def _dec(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _provenance(payload: Mapping[str, object]) -> SourceProvenance:
    published_at = payload["published_at"]
    return SourceProvenance(
        source_class=str(payload["source_class"]),
        publisher=str(payload["publisher"]),
        content_sha256=str(payload["content_sha256"]),
        published_at=None if published_at is None else _ts(str(published_at)),
        published_at_precision=str(payload["published_at_precision"]),
        retrieved_at=_ts(str(payload["retrieved_at"])),
        entitlement=str(payload["entitlement"]),
        redistribution_status=str(payload["redistribution_status"]),
        limitations=tuple(str(item) for item in payload["limitations"]),
    )


def _quarter(payload: Mapping[str, object]) -> QuarterFact:
    return QuarterFact(
        fiscal_period=str(payload["fiscal_period"]),
        revenue=_dec(payload["revenue"]),
        eps_diluted=_dec(payload["eps_diluted"]),
    )


def _guidance(payload: Mapping[str, object] | None) -> GuidanceStatement | None:
    if payload is None:
        return None
    return GuidanceStatement(
        fiscal_period=str(payload["fiscal_period"]),
        withdrawn=bool(payload["withdrawn"]),
        revenue_low=_dec(payload["revenue_low"]),
        revenue_high=_dec(payload["revenue_high"]),
        eps_low=_dec(payload["eps_low"]),
        eps_high=_dec(payload["eps_high"]),
    )


class LiveReplayEvidenceSource:
    """EvidenceSource replaying one serialized live capture blob."""

    def __init__(self, blob: Mapping[str, object]) -> None:
        self._blob = blob
        self._sessions = tuple(
            SessionRecord(
                exchange_mic=str(s["exchange_mic"]),
                session_id=str(s["session_id"]),
                session_date=_day(str(s["session_date"])),
                open_at=_ts(str(s["open_at"])),
                close_at=_ts(str(s["close_at"])),
                full_regular=bool(s["full_regular"]),
                provenance=_provenance(s["provenance"]),
            )
            for s in blob["sessions"]
        )
        master = blob["security_master"]
        assert isinstance(master, Mapping)
        self._master = SecurityMasterRecord(
            ticker=str(master["ticker"]),
            security_id=str(master["security_id"]),
            issuer=str(master["issuer"]),
            primary_exchange_mic=str(master["primary_exchange_mic"]),
            security_type=str(master["security_type"]),
            sector=str(master["sector"]),
            active_at_freeze=bool(master["active_at_freeze"]),
            listed_option_exists=bool(master["listed_option_exists"]),
            prior_regular_close=Decimal(str(master["prior_regular_close"])),
            asof=_ts(str(master["asof"])),
            provenance=_provenance(master["provenance"]),
        )
        release = blob["issuer_release"]
        assert isinstance(release, Mapping)
        self._release = IssuerRelease(
            event_id=str(release["event_id"]),
            ticker=str(release["ticker"]),
            provenance=_provenance(release["provenance"]),
            report_fiscal_period=str(release["report_fiscal_period"]),
            current_quarter=_quarter(release["current_quarter"]),
            quarter_history=tuple(_quarter(q) for q in release["quarter_history"]),
            current_guidance=_guidance(release["current_guidance"]),
            prior_guidance=_guidance(release["prior_guidance"]),
        )
        self._actions = tuple(
            CorporateAction(
                ticker=str(a["ticker"]),
                action_type=str(a["action_type"]),
                ex_date=_day(str(a["ex_date"])),
                ratio_numerator=a.get("ratio_numerator"),
                ratio_denominator=a.get("ratio_denominator"),
                symbol_from=a.get("symbol_from"),
                symbol_to=a.get("symbol_to"),
                provenance=_provenance(a["provenance"]),
            )
            for a in blob["corporate_actions"]
        )

    def sessions(self, exchange_mic: str, start: date, end: date) -> Sequence[SessionRecord]:
        return tuple(
            s
            for s in self._sessions
            if s.exchange_mic == exchange_mic and start <= s.session_date <= end
        )

    def security_master(self, ticker: str, asof: datetime) -> SecurityMasterRecord:
        if ticker != self._master.ticker:
            raise ValueError(f"security master capture holds {self._master.ticker}, asked {ticker}")
        if asof < self._master.asof:
            raise ValueError("security master asked before its captured as-of instant")
        return self._master

    def issuer_release(self, event_id: str) -> IssuerRelease | None:
        return self._release if event_id == self._release.event_id else None

    def sec_filing(self, event_id: str) -> IssuerRelease | None:
        return None

    def corporate_actions(self, ticker: str, start: date, end: date) -> Sequence[CorporateAction]:
        return tuple(a for a in self._actions if a.ticker == ticker and start <= a.ex_date <= end)


class LiveReplayMarketDataSource:
    """MarketDataSource replaying one serialized live capture blob."""

    def __init__(self, blob: Mapping[str, object]) -> None:
        self._bars: dict[str, tuple[DailyBar, ...]] = {}
        for symbol, bars in (blob["daily_bars"] or {}).items():
            self._bars[str(symbol)] = tuple(
                DailyBar(
                    symbol=str(bar["symbol"]),
                    session_id=str(bar["session_id"]),
                    session_date=_day(str(bar["session_date"])),
                    close=Decimal(str(bar["close"])),
                    volume=int(bar["volume"]),
                    valid=bool(bar["valid"]),
                )
                for bar in bars
            )
        self._trades: dict[str, list[Trade]] = {}
        for symbol, trades in (blob["reaction_trades"] or {}).items():
            self._trades.setdefault(str(symbol), []).extend(
                Trade(
                    symbol=str(t["symbol"]),
                    session_id=str(t["session_id"]),
                    observed_at=_ts(str(t["observed_at"])),
                    price=Decimal(str(t["price"])),
                    size=int(t["size"]),
                    sale_condition=str(t["sale_condition"]),
                )
                for t in trades
            )
        for session_id, trades in (blob["prior_window_trades"] or {}).items():
            for t in trades:
                symbol = str(t["symbol"])
                self._trades.setdefault(symbol, []).append(
                    Trade(
                        symbol=symbol,
                        session_id=str(session_id),
                        observed_at=_ts(str(t["observed_at"])),
                        price=Decimal(str(t["price"])),
                        size=int(t["size"]),
                        sale_condition=str(t["sale_condition"]),
                    )
                )
        self._quotes: dict[str, tuple[QuoteSample, ...]] = {}
        for symbol, quotes in (blob["reaction_quotes"] or {}).items():
            self._quotes[str(symbol)] = tuple(
                QuoteSample(
                    symbol=str(q["symbol"]),
                    session_id=str(q["session_id"]),
                    observed_at=_ts(str(q["observed_at"])),
                    bid=Decimal(str(q["bid"])),
                    ask=Decimal(str(q["ask"])),
                )
                for q in quotes
            )

    def daily_bars(self, symbol: str, start: date, end: date) -> Sequence[DailyBar]:
        return tuple(bar for bar in self._bars.get(symbol, ()) if start <= bar.session_date <= end)

    def window_trades(self, symbol: str, session_id: str) -> Sequence[Trade]:
        return tuple(
            trade
            for trade in sorted(self._trades.get(symbol, ()), key=lambda t: t.observed_at)
            if trade.session_id == session_id
        )

    def window_quotes(self, symbol: str, session_id: str) -> Sequence[QuoteSample]:
        return tuple(
            quote
            for quote in sorted(self._quotes.get(symbol, ()), key=lambda q: q.observed_at)
            if quote.session_id == session_id
        )


class LiveCaptureDoors:
    """CaptureSourceDoor resolving each feed event's serialized capture bytes.

    The feed event carries the complete capture (evidence + market byte pair);
    the door replays it deterministically - zero I/O at call time, so the
    composition's double compile sees byte-identical sources.
    """

    def sources_for(self, event) -> tuple[CaptureConfiguration, object, object]:
        evidence = json.loads(bytes(event.evidence_manifest_bytes))
        market = json.loads(bytes(event.market_window_bytes))
        overlap = set(evidence) & set(market)
        if overlap:
            raise ValueError(f"capture blobs overlap: {sorted(overlap)}")
        blob = {**evidence, **market}
        event_id = str(blob["event_id"])
        pages = {
            str(key): (int(pair[0]), int(pair[1]))
            for key, pair in (blob.get("retrieval_pages") or {}).items()
        }
        from esscher.strategy.contracts import canonical_json_bytes

        capture = CaptureConfiguration(
            candidate_manifest_bytes=canonical_json_bytes(blob["candidate_manifest"]),
            event_id=event_id,
            capture_at=_ts(str(blob["capture_at"])),
            market_publisher=str(blob["market_publisher"]),
            market_entitlement=str(blob["market_entitlement"]),
            market_redistribution=str(blob["market_redistribution"]),
            retrieval_pages=pages,
        )
        return capture, LiveReplayEvidenceSource(blob), LiveReplayMarketDataSource(blob)
