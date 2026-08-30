"""Deterministic fake adapters backed by the frozen synthetic fixture.

These adapters contain no network, subprocess, broker, or MCP path. They
replay one frozen local fixture so every test and dry capture is reproducible.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from ringdown_market.sourcedata.interfaces import (
    CorporateAction,
    DailyBar,
    GuidanceStatement,
    IssuerRelease,
    MacroRelease,
    MacroRevision,
    MacroScheduleEntry,
    QuarterFact,
    QuoteSample,
    SecurityMasterRecord,
    SessionRecord,
    SourceProvenance,
    Trade,
)
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.strategy.contracts import candidate_manifest_bytes
from ringdown_market.strategy.models import (
    CandidateManifest,
    CandidateRecord,
    EligibilityState,
)
from ringdown_market.strategy.policy import load_strategy_policy

FIXTURE_PATH = (
    Path(__file__).parents[3]
    / "tests"
    / "fixtures"
    / "sourcedata"
    / "synthetic_snapshot_inputs_v1.json"
)
MACRO_FIXTURE_PATH = (
    Path(__file__).parents[3]
    / "tests"
    / "fixtures"
    / "sourcedata"
    / "synthetic_macro_snapshot_inputs_v1.json"
)


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _provenance(payload: Mapping[str, object]) -> SourceProvenance:
    published_at = payload["published_at"]
    return SourceProvenance(
        source_class=str(payload["source_class"]),
        publisher=str(payload["publisher"]),
        content_sha256=str(payload["content_sha256"]),
        published_at=None if published_at is None else _timestamp(str(published_at)),
        published_at_precision=str(payload["published_at_precision"]),
        retrieved_at=_timestamp(str(payload["retrieved_at"])),
        entitlement=str(payload["entitlement"]),
        redistribution_status=str(payload["redistribution_status"]),
        limitations=tuple(str(item) for item in payload["limitations"]),  # type: ignore[union-attr]
    )


def _quarter(payload: Mapping[str, object]) -> QuarterFact:
    revenue = payload["revenue"]
    eps = payload["eps_diluted"]
    return QuarterFact(
        fiscal_period=str(payload["fiscal_period"]),
        revenue=None if revenue is None else Decimal(str(revenue)),
        eps_diluted=None if eps is None else Decimal(str(eps)),
    )


def _guidance(payload: Mapping[str, object] | None) -> GuidanceStatement | None:
    if payload is None:
        return None
    bounds = {}
    for field in ("revenue_low", "revenue_high", "eps_low", "eps_high"):
        value = payload[field]
        bounds[field] = None if value is None else Decimal(str(value))
    return GuidanceStatement(
        fiscal_period=str(payload["fiscal_period"]),
        withdrawn=bool(payload["withdrawn"]),
        revenue_low=bounds["revenue_low"],
        revenue_high=bounds["revenue_high"],
        eps_low=bounds["eps_low"],
        eps_high=bounds["eps_high"],
    )


def load_fixture(path: Path | None = None) -> dict[str, object]:
    """Load the frozen synthetic fixture exactly once per path."""

    fixture_path = path or FIXTURE_PATH
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "fixture",
            "fixture root must be an object",
        )
    return payload


def build_candidate_manifest(fixture: Mapping[str, object]) -> bytes:
    """Serialize the frozen fixture manifest to canonical bytes."""

    payload = fixture["candidate_manifest"]
    assert isinstance(payload, dict)
    policy = load_strategy_policy()
    records = tuple(
        CandidateRecord(
            event_id=str(record["event_id"]),
            issuer=str(record["issuer"]),
            security_id=str(record["security_id"]),
            ticker=str(record["ticker"]),
            cohort_id=str(record["cohort_id"]),
            scheduled_at=_timestamp(str(record["scheduled_at"])),
            eligibility=EligibilityState(str(record["eligibility"])),
            reason_codes=tuple(str(code) for code in record["reason_codes"]),  # type: ignore[union-attr]
        )
        for record in sorted(
            payload["records"],  # type: ignore[arg-type]
            key=lambda item: str(item["event_id"]),
        )
    )
    manifest = CandidateManifest(
        manifest_id=str(payload["manifest_id"]),
        candidate_id=str(payload["candidate_id"]),
        policy_sha256=policy.sha256,
        selection_rule_id=str(payload["selection_rule_id"]),
        producer_build_sha256=str(payload["producer_build_sha256"]),
        frozen_at=_timestamp(str(payload["frozen_at"])),
        records=records,
    )
    return candidate_manifest_bytes(manifest)


class FixtureEvidenceSource:
    """Read-only evidence adapter replaying the frozen fixture."""

    def __init__(self, fixture: Mapping[str, object]) -> None:
        self._sessions = tuple(
            SessionRecord(
                exchange_mic=str(record["exchange_mic"]),
                session_id=str(record["session_id"]),
                session_date=_date(str(record["session_date"])),
                open_at=_timestamp(str(record["open_at"])),
                close_at=_timestamp(str(record["close_at"])),
                full_regular=bool(record["full_regular"]),
                provenance=_provenance(record["provenance"]),  # type: ignore[arg-type]
            )
            for record in fixture["sessions"]  # type: ignore[union-attr]
        )
        master_payload = fixture["security_master"]
        assert isinstance(master_payload, dict)
        self._master = SecurityMasterRecord(
            ticker=str(master_payload["ticker"]),
            security_id=str(master_payload["security_id"]),
            issuer=str(master_payload["issuer"]),
            primary_exchange_mic=str(master_payload["primary_exchange_mic"]),
            security_type=str(master_payload["security_type"]),
            sector=str(master_payload["sector"]),
            active_at_freeze=bool(master_payload["active_at_freeze"]),
            listed_option_exists=bool(master_payload["listed_option_exists"]),
            prior_regular_close=Decimal(str(master_payload["prior_regular_close"])),
            asof=_timestamp(str(master_payload["asof"])),
            provenance=_provenance(master_payload["provenance"]),  # type: ignore[arg-type]
        )
        release_payload = fixture["issuer_release"]
        assert isinstance(release_payload, dict)
        self._release = IssuerRelease(
            event_id=str(release_payload["event_id"]),
            ticker=str(release_payload["ticker"]),
            provenance=_provenance(release_payload["provenance"]),  # type: ignore[arg-type]
            report_fiscal_period=str(release_payload["report_fiscal_period"]),
            current_quarter=_quarter(release_payload["current_quarter"]),  # type: ignore[arg-type]
            quarter_history=tuple(
                _quarter(quarter)
                for quarter in release_payload["quarter_history"]  # type: ignore[union-attr]
            ),
            current_guidance=_guidance(release_payload["current_guidance"]),  # type: ignore[arg-type]
            prior_guidance=_guidance(release_payload["prior_guidance"]),  # type: ignore[arg-type]
        )
        self._actions = tuple(
            CorporateAction(
                ticker=str(action["ticker"]),
                action_type=str(action["action_type"]),
                ex_date=_date(str(action["ex_date"])),
                ratio_numerator=action["ratio_numerator"],  # type: ignore[arg-type]
                ratio_denominator=action["ratio_denominator"],  # type: ignore[arg-type]
                symbol_from=action["symbol_from"],  # type: ignore[arg-type]
                symbol_to=action["symbol_to"],  # type: ignore[arg-type]
                provenance=_provenance(action["provenance"]),  # type: ignore[arg-type]
            )
            for action in fixture["corporate_actions"]  # type: ignore[union-attr]
        )

    def sessions(self, exchange_mic: str, start: date, end: date) -> Sequence[SessionRecord]:
        return tuple(
            session
            for session in self._sessions
            if session.exchange_mic == exchange_mic and start <= session.session_date <= end
        )

    def security_master(self, ticker: str, asof: datetime) -> SecurityMasterRecord:
        if ticker != self._master.ticker or asof < self._master.asof:
            raise CollectorRejected(
                CollectorReason.UNSUPPORTED_INPUT,
                f"security_master.{ticker}",
                "fixture security master covers only the frozen event",
            )
        return self._master

    def issuer_release(self, event_id: str) -> IssuerRelease | None:
        if event_id != self._release.event_id:
            return None
        return self._release

    def sec_filing(self, event_id: str) -> IssuerRelease | None:
        return None

    def corporate_actions(self, ticker: str, start: date, end: date) -> Sequence[CorporateAction]:
        return tuple(
            action
            for action in self._actions
            if action.ticker == ticker and start <= action.ex_date <= end
        )


class FixtureMarketDataSource:
    """Read-only market-data adapter replaying the frozen fixture."""

    def __init__(self, fixture: Mapping[str, object]) -> None:
        self._bars: dict[str, tuple[DailyBar, ...]] = {}
        bars_payload = fixture["daily_bars"]
        assert isinstance(bars_payload, dict)
        for symbol, bars in bars_payload.items():
            self._bars[symbol] = tuple(
                DailyBar(
                    symbol=str(bar["symbol"]),
                    session_id=str(bar["session_id"]),
                    session_date=_date(str(bar["session_date"])),
                    close=Decimal(str(bar["close"])),
                    volume=int(bar["volume"]),  # type: ignore[arg-type]
                    valid=bool(bar["valid"]),
                )
                for bar in bars  # type: ignore[union-attr]
            )
        self._trades: dict[str, tuple[Trade, ...]] = {}
        reaction_payload = fixture["reaction_trades"]
        assert isinstance(reaction_payload, dict)
        for symbol, trades in reaction_payload.items():
            self._trades.setdefault(symbol, ())
            self._trades[symbol] = self._trades[symbol] + tuple(
                Trade(
                    symbol=str(trade["symbol"]),
                    session_id=str(trade["session_id"]),
                    observed_at=_timestamp(str(trade["observed_at"])),
                    price=Decimal(str(trade["price"])),
                    size=int(trade["size"]),  # type: ignore[arg-type]
                    sale_condition=str(trade["sale_condition"]),
                )
                for trade in trades  # type: ignore[union-attr]
            )
        prior_payload = fixture.get("prior_window_trades", {})
        assert isinstance(prior_payload, dict)
        for session_id, trades in prior_payload.items():
            symbol = "KR"
            self._trades.setdefault(symbol, ())
            self._trades[symbol] = self._trades[symbol] + tuple(
                Trade(
                    symbol=str(trade["symbol"]),
                    session_id=str(session_id),
                    observed_at=_timestamp(str(trade["observed_at"])),
                    price=Decimal(str(trade["price"])),
                    size=int(trade["size"]),  # type: ignore[arg-type]
                    sale_condition=str(trade["sale_condition"]),
                )
                for trade in trades  # type: ignore[union-attr]
            )
        self._quotes: dict[str, tuple[QuoteSample, ...]] = {}
        quotes_payload = fixture["reaction_quotes"]
        assert isinstance(quotes_payload, dict)
        for symbol, quotes in quotes_payload.items():
            self._quotes[symbol] = tuple(
                QuoteSample(
                    symbol=str(quote["symbol"]),
                    session_id=str(quote["session_id"]),
                    observed_at=_timestamp(str(quote["observed_at"])),
                    bid=Decimal(str(quote["bid"])),
                    ask=Decimal(str(quote["ask"])),
                )
                for quote in quotes  # type: ignore[union-attr]
            )

    def daily_bars(self, symbol: str, start: date, end: date) -> Sequence[DailyBar]:
        return tuple(bar for bar in self._bars.get(symbol, ()) if start <= bar.session_date <= end)

    def window_trades(self, symbol: str, session_id: str) -> Sequence[Trade]:
        return tuple(
            trade for trade in self._trades.get(symbol, ()) if trade.session_id == session_id
        )

    def window_quotes(self, symbol: str, session_id: str) -> Sequence[QuoteSample]:
        return tuple(
            quote for quote in self._quotes.get(symbol, ()) if quote.session_id == session_id
        )


def load_macro_fixture(path: Path | None = None) -> dict[str, object]:
    """Load the frozen synthetic macro fixture exactly once per path."""

    fixture_path = path or MACRO_FIXTURE_PATH
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "macro_fixture",
            "fixture root must be an object",
        )
    return payload


def build_macro_candidate_manifest(fixture: Mapping[str, object]) -> bytes:
    """Serialize the frozen macro manifest to canonical bytes."""

    payload = fixture["candidate_manifest"]
    assert isinstance(payload, dict)
    policy = load_strategy_policy()
    records = tuple(
        CandidateRecord(
            event_id=str(record["event_id"]),
            issuer=str(record["issuer"]),
            security_id=str(record["security_id"]),
            ticker=str(record["ticker"]),
            cohort_id=str(record["cohort_id"]),
            scheduled_at=_timestamp(str(record["scheduled_at"])),
            eligibility=EligibilityState(str(record["eligibility"])),
            reason_codes=tuple(str(code) for code in record["reason_codes"]),  # type: ignore[union-attr]
        )
        for record in sorted(
            payload["records"],  # type: ignore[arg-type]
            key=lambda item: str(item["event_id"]),
        )
    )
    manifest = CandidateManifest(
        manifest_id=str(payload["manifest_id"]),
        candidate_id=str(payload["candidate_id"]),
        policy_sha256=policy.sha256,
        selection_rule_id=str(payload["selection_rule_id"]),
        producer_build_sha256=str(payload["producer_build_sha256"]),
        frozen_at=_timestamp(str(payload["frozen_at"])),
        records=records,
    )
    return candidate_manifest_bytes(manifest)


class FixtureMacroEvidenceSource:
    """Read-only calendar adapter replaying the frozen macro fixture sessions."""

    def __init__(self, fixture: Mapping[str, object]) -> None:
        self._sessions = tuple(
            SessionRecord(
                exchange_mic=str(record["exchange_mic"]),
                session_id=str(record["session_id"]),
                session_date=_date(str(record["session_date"])),
                open_at=_timestamp(str(record["open_at"])),
                close_at=_timestamp(str(record["close_at"])),
                full_regular=bool(record["full_regular"]),
                provenance=_provenance(record["provenance"]),  # type: ignore[arg-type]
            )
            for record in fixture["sessions"]  # type: ignore[union-attr]
        )

    def sessions(self, exchange_mic: str, start: date, end: date) -> Sequence[SessionRecord]:
        return tuple(
            session
            for session in self._sessions
            if session.exchange_mic == exchange_mic and start <= session.session_date <= end
        )


class FixtureMacroReleaseSource:
    """Read-only official macro release adapter replaying the frozen fixture."""

    def __init__(self, fixture: Mapping[str, object]) -> None:
        self._schedule = tuple(
            MacroScheduleEntry(
                release_family=str(entry["release_family"]),
                reference_period=str(entry["reference_period"]),
                scheduled_at=_timestamp(str(entry["scheduled_at"])),
                provenance=_provenance(entry["provenance"]),  # type: ignore[arg-type]
            )
            for entry in fixture["release_schedule"]  # type: ignore[union-attr]
        )
        release_payload = fixture["release"]
        assert isinstance(release_payload, dict)
        self._release = MacroRelease(
            release_family=str(release_payload["release_family"]),
            reference_period=str(release_payload["reference_period"]),
            vintage_index=int(release_payload["vintage_index"]),  # type: ignore[arg-type]
            published_at=_timestamp(str(release_payload["published_at"])),
            fields={
                str(field_id): Decimal(str(value))
                for field_id, value in release_payload["fields"].items()  # type: ignore[union-attr]
            },
            provenance=_provenance(release_payload["provenance"]),  # type: ignore[arg-type]
        )
        self._revisions = tuple(
            MacroRevision(
                release_family=str(revision["release_family"]),
                revised_reference_period=str(revision["revised_reference_period"]),
                field_id=str(revision["field_id"]),
                initial_value=Decimal(str(revision["initial_value"])),
                revised_value=Decimal(str(revision["revised_value"])),
                published_at=_timestamp(str(revision["published_at"])),
                provenance=_provenance(revision["provenance"]),  # type: ignore[arg-type]
            )
            for revision in fixture["revisions"]  # type: ignore[union-attr]
        )

    def release_schedule(self, release_family: str) -> Sequence[MacroScheduleEntry]:
        return tuple(entry for entry in self._schedule if entry.release_family == release_family)

    def release(
        self, release_family: str, reference_period: str, vintage_index: int
    ) -> MacroRelease | None:
        release = self._release
        if (
            release.release_family == release_family
            and release.reference_period == reference_period
            and release.vintage_index == vintage_index
        ):
            return release
        return None

    def revisions(self, release_family: str, published_before: datetime) -> Sequence[MacroRevision]:
        return tuple(
            revision
            for revision in self._revisions
            if revision.release_family == release_family
            and revision.published_at <= published_before
        )


class FixtureMacroMarketDataSource:
    """Read-only SPY market adapter replaying the frozen macro fixture."""

    def __init__(self, fixture: Mapping[str, object]) -> None:
        self._bars = tuple(
            DailyBar(
                symbol=str(bar["symbol"]),
                session_id=str(bar["session_id"]),
                session_date=_date(str(bar["session_date"])),
                close=Decimal(str(bar["close"])),
                volume=int(bar["volume"]),  # type: ignore[arg-type]
                valid=bool(bar["valid"]),
            )
            for bar in fixture["spy_daily_bars"]  # type: ignore[union-attr]
        )
        trades: list[Trade] = []
        reaction_trades = fixture["spy_reaction_trades"]
        assert isinstance(reaction_trades, list)
        trades.extend(self._trade(trade) for trade in reaction_trades)
        prior_trades = fixture["spy_prior_window_trades"]
        assert isinstance(prior_trades, dict)
        for session_trades in prior_trades.values():
            trades.extend(self._trade(trade) for trade in session_trades)  # type: ignore[union-attr]
        self._trades = tuple(trades)
        quotes: list[QuoteSample] = []
        anchor_quotes = fixture["spy_anchor_quotes"]
        assert isinstance(anchor_quotes, list)
        quotes.extend(self._quote(quote) for quote in anchor_quotes)
        window_quotes = fixture["spy_window_quotes"]
        assert isinstance(window_quotes, list)
        quotes.extend(self._quote(quote) for quote in window_quotes)
        self._quotes = tuple(quotes)

    @staticmethod
    def _trade(trade: Mapping[str, object]) -> Trade:
        return Trade(
            symbol=str(trade["symbol"]),
            session_id=str(trade["session_id"]),
            observed_at=_timestamp(str(trade["observed_at"])),
            price=Decimal(str(trade["price"])),
            size=int(trade["size"]),  # type: ignore[arg-type]
            sale_condition=str(trade["sale_condition"]),
        )

    @staticmethod
    def _quote(quote: Mapping[str, object]) -> QuoteSample:
        return QuoteSample(
            symbol=str(quote["symbol"]),
            session_id=str(quote["session_id"]),
            observed_at=_timestamp(str(quote["observed_at"])),
            bid=Decimal(str(quote["bid"])),
            ask=Decimal(str(quote["ask"])),
        )

    def daily_bars(self, symbol: str, start: date, end: date) -> Sequence[DailyBar]:
        return tuple(bar for bar in self._bars if start <= bar.session_date <= end)

    def window_trades(self, symbol: str, session_id: str) -> Sequence[Trade]:
        return tuple(trade for trade in self._trades if trade.session_id == session_id)

    def window_quotes(self, symbol: str, session_id: str) -> Sequence[QuoteSample]:
        return tuple(quote for quote in self._quotes if quote.session_id == session_id)
