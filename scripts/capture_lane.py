"""Live capture lane for the #68 armed PAPER session (host-run, read-only data plane).

Produces the host-captured evidence/market byte pairs that ``PaperMcpFeedEvent``
carries into the production composition: ex-ante candidate discovery (EDGAR
full-text search over the registered sources), universe screening against the
frozen selection rules, the candidate-manifest freeze, historical prefetch
(sessions calendar, raw daily bars with split detection, prior-window trades,
point-in-time security master, XBRL quarter history), release capture with
dateline-derived publication time, the reaction-window capture with the
GATE_A_EQUITY_ENTITLEMENT_RECEIPT self-check, and fixture-shaped serialization
consumed by deterministic replay doors.

Honest boundaries enforced here (all fail-closed, never guessed):

- every record carries its true retrieval instant; nothing is backdated;
- the reaction-window capture refuses to run unless the live SIP entitlement
  probe passes (the Basic plan's 15-minute recency restriction makes the frozen
  09:35:15 ET evidence cutoff unmeetable - see the entitlement blocker issue);
- publication time comes only from the publisher (release dateline); SEC
  acceptance time corroborates cohort classification but never substitutes for
  ``published_at``;
- AMC events without a dateline time are ineligible (DATE-precision midnight is
  admissible only for BMO, where same-day pre-open publication is corroborated
  by the acceptance instant);
- split detection via raw-vs-adjusted bar divergence fails the event closed
  when no adjustment-record source is entitled;
- guidance present but unparseable fails the event closed; absent guidance is
  the contract value NOT_GIVEN;
- sector mapping uses only the committed unambiguous SIC ranges; anything else
  is SECTOR_MAPPING_UNAVAILABLE.

No credential or host string is committed: Alpaca hosts and credentials arrive
per-invocation (``--data-host``, ``--trading-host``, ``--credentials-env``).

Subcommands: discover, screen, freeze-manifest, prefetch, capture-release,
capture-window, serialize, entitlement-receipt.  Run them in order; each writes
one JSON artifact and exits non-zero on any fail-closed refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ringdown_market.strategy.contracts import (  # noqa: E402
    canonical_json_bytes,
    parse_candidate_manifest,
    sha256_bytes,
)
from ringdown_market.strategy.policy import (  # noqa: E402
    strategy_policy_sha256,
    strategy_policy_v3_sha256,
)

ET = ZoneInfo("America/New_York")
EDGAR_FTS_HOST = "https://efts.sec.gov"
EDGAR_DATA_HOST = "https://data.sec.gov"
EDGAR_WWW_HOST = "https://www.sec.gov"
EDGAR_UA = {"User-Agent": "esscher-capture-lane research@tempest.local"}
CANDIDATE_ID = "EARNINGS_RESIDUAL_CONTINUATION_V1"
CANDIDATE_ID_V3 = "EARNINGS_RESIDUAL_CONTINUATION_V3"
SELECTION_RULE_ID = "live-earnings-universe-v1"
SELECTION_RULE_ID_V3 = "live-earnings-universe-v1-delayed-demo"
# The V3 delayed-demo evidence cutoff: window end (09:35:00 ET) + 16 minutes.
# The Basic data plan serves SIP data only once it is older than fifteen
# minutes, so the delayed lane captures the identical 09:30:00-09:35:00 signal
# window at 09:50:05-09:51:00 ET as legal historical data.
DELAYED_EVIDENCE_DEADLINE = timedelta(minutes=16)
LIVE_INPUTS_SCHEMA = "esscher.live_snapshot_inputs"
PRODUCER_BUILD_SHA256 = sha256_bytes(
    canonical_json_bytes({"schema": "esscher.capture_lane_producer", "version": 1})
)
MARKET_KEYS = ("daily_bars", "prior_window_trades", "reaction_quotes", "reaction_trades")

# Unambiguous SIC major-group -> point-in-time GICS sector names (the exact keys
# of the frozen sector_proxy_by_point_in_time_gics map).  Anything outside these
# ranges fails closed as SECTOR_MAPPING_UNAVAILABLE; the mapping is never
# guessed.  Ranges follow the standard SIC division structure published by the
# SEC (https://www.sec.gov/info/edgar/siccodes.htm).
SIC_GICS_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, ""),  # agriculture: ambiguous across staples/materials -> closed
    (1000, 1199, "MATERIALS"),  # metal mining
    (1300, 1399, "ENERGY"),  # oil and gas extraction
    (2000, 2099, "CONSUMER_STAPLES"),  # food and kindred products
    (2080, 2085, "CONSUMER_STAPLES"),  # beverages (inside 2000 range, same class)
    (2200, 2399, "CONSUMER_DISCRETIONARY"),  # textiles and apparel
    (2500, 2599, "CONSUMER_DISCRETIONARY"),  # furniture and fixtures
    (2600, 2699, "MATERIALS"),  # paper and allied products
    (2830, 2839, "HEALTH_CARE"),  # drugs and pharmaceuticals
    (2900, 2999, "ENERGY"),  # petroleum refining
    (3300, 3399, "MATERIALS"),  # primary metal industries
    (3550, 3599, "INDUSTRIALS"),  # industrial machinery (ex 3570s below)
    (3570, 3579, "INFORMATION_TECHNOLOGY"),  # computer and office equipment
    (3670, 3679, "INFORMATION_TECHNOLOGY"),  # electronic components
    (3710, 3719, "CONSUMER_DISCRETIONARY"),  # motor vehicles
    (3720, 3729, "INDUSTRIALS"),  # aircraft
    (3820, 3829, "INFORMATION_TECHNOLOGY"),  # measuring instruments (semi/test)
    (3840, 3849, "HEALTH_CARE"),  # medical instruments
    (4011, 4013, "INDUSTRIALS"),  # railroads
    (4400, 4699, "INDUSTRIALS"),  # water/motor-freight/pipeline transport
    (4500, 4599, "INDUSTRIALS"),  # air transportation
    (4812, 4813, "COMMUNICATION_SERVICES"),  # telecommunications
    (4900, 4999, "UTILITIES"),  # electric, gas, sanitary services
    (5271, 5271, "CONSUMER_DISCRETIONARY"),  # filling stations (retail)
    (5411, 5412, "CONSUMER_STAPLES"),  # grocery stores
    (5600, 5699, "CONSUMER_DISCRETIONARY"),  # apparel retail
    (5731, 5734, "CONSUMER_DISCRETIONARY"),  # electronics/computer retail
    (6000, 6799, "FINANCIALS"),  # finance, insurance, real estate finance
    (6500, 6553, "REAL_ESTATE"),  # real estate (narrower range wins below)
    (7370, 7379, "INFORMATION_TECHNOLOGY"),  # software and data processing
    (7500, 7549, "INDUSTRIALS"),  # auto repair/services
    (7800, 7849, "COMMUNICATION_SERVICES"),  # motion pictures and entertainment
    (8000, 8099, "HEALTH_CARE"),  # health services
    (8731, 8734, "HEALTH_CARE"),  # engineering labs / testing laboratories
)

# Narrower overrides applied after the range scan (most specific first).
SIC_GICS_OVERRIDES: tuple[tuple[int, int, str], ...] = (
    (6500, 6553, "REAL_ESTATE"),
    (2830, 2839, "HEALTH_CARE"),
    (3570, 3579, "INFORMATION_TECHNOLOGY"),
)

SECTOR_ETFS = {
    "COMMUNICATION_SERVICES": "XLC",
    "CONSUMER_DISCRETIONARY": "XLY",
    "CONSUMER_STAPLES": "XLP",
    "ENERGY": "XLE",
    "FINANCIALS": "XLF",
    "HEALTH_CARE": "XLV",
    "INDUSTRIALS": "XLI",
    "INFORMATION_TECHNOLOGY": "XLK",
    "MATERIALS": "XLB",
    "REAL_ESTATE": "XLRE",
    "UTILITIES": "XLU",
}

PRICE_FLOOR = Decimal("10.00")
MINIMUM_LIQUIDITY_SESSIONS = 18
LIQUIDITY_SESSION_COUNT = 20
MINIMUM_MEDIAN_DOLLAR_VOLUME = Decimal("50000000")
MINIMUM_ACTIVE_OPTION_CONTRACTS = 20
MINIMUM_EPS_QUARTERS = 12
MINIMUM_REVENUE_QUARTERS = 5


class CaptureLaneRejected(RuntimeError):
    """One typed fail-closed refusal; the reason code leads the message."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class Fetcher(Protocol):
    def __call__(self, url: str, *, headers: Mapping[str, str] | None = None) -> bytes: ...


@dataclass(slots=True)
class LiveFetcher:
    """The only network surface; host-owned, injected, never committed hosts."""

    alpaca_headers: Mapping[str, str]
    throttle_seconds: float = 0.35

    def __call__(self, url: str, *, headers: Mapping[str, str] | None = None) -> bytes:
        request = urllib.request.Request(url, headers=dict(headers or self.alpaca_headers))
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read()[:200].decode("utf-8", "replace")
            raise CaptureLaneRejected(
                f"HTTP_{error.code}", f"{url.split('?')[0]}?вЂ¦ -> {detail}"
            ) from None
        finally:
            time.sleep(self.throttle_seconds)
        return payload


def _json(payload: bytes) -> object:
    return json.loads(payload.decode("utf-8"), parse_float=str)


def _iso(value: datetime) -> str:
    aware = value.astimezone(UTC)
    spec = "microseconds" if aware.microsecond else "seconds"
    return aware.isoformat(timespec=spec).replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_credentials(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    key_id = values.get("APCA_API_KEY_ID", "")
    secret = values.get("APCA_API_SECRET_KEY", "")
    if not key_id or not secret:
        raise CaptureLaneRejected(
            "HOST_CONFIGURATION_MISSING", "credentials env lacks the APCA pair"
        )
    return {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}


def _write_json(path: Path, payload: object) -> str:
    raw = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw + b"\n")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def _fts_hits(fetcher: Fetcher, query: str, forms: str, start: date, end: date) -> list[dict]:
    url = (
        f"{EDGAR_FTS_HOST}/LATEST/search-index?q={urllib.parse.quote(query)}"
        f"&forms={forms}&startdt={start.isoformat()}&enddt={end.isoformat()}"
    )
    payload = _json(fetcher(url, headers=EDGAR_UA))
    if not isinstance(payload, dict):
        raise CaptureLaneRejected(
            "DISCOVERY_SOURCE_INVALID", "EDGAR full-text response is not an object"
        )
    hits = payload.get("hits", {}).get("hits", [])
    out = []
    for hit in hits:
        source = hit.get("_source", {})
        names = source.get("display_names") or [""]
        match = re.search(r"\(([A-Z.]{1,6})\)", str(names[0]))
        if not match:
            continue
        out.append(
            {
                "ticker": match.group(1),
                "issuer": str(names[0]).split("(")[0].strip(),
                "cik": str((source.get("ciks") or [""])[0]),
                "accession": str(source.get("adsh", "")),
                "file_date": str(source.get("file_date", "")),
                "items": [str(item) for item in (source.get("items") or [])],
            }
        )
    return out


def _classify_cohort(
    accepted_utc: datetime, session_date: date, prior_session: date | None
) -> str | None:
    """BMO/AMC from the acceptance instant against the OFFICIAL calendar.

    Corroboration only - never a published_at source.  BMO: accepted on the
    session date before the open.  AMC: accepted after the prior full regular
    session's close and before this session's open (midweek AMC filings react
    in their own next session, never a later one).
    """

    accepted_et = accepted_utc.astimezone(ET)
    open_et = datetime.combine(session_date, datetime.min.time(), tzinfo=ET).replace(
        hour=9, minute=30
    )
    if accepted_et.date() == session_date and accepted_et < open_et:
        return "BMO"
    if prior_session is not None:
        prior_close_et = datetime.combine(prior_session, datetime.min.time(), tzinfo=ET).replace(
            hour=16
        )
        if prior_close_et <= accepted_et < open_et:
            return "AMC"
    return None


def _fts_hits(fetcher: Fetcher, query: str, forms: str, start: date, end: date) -> list[dict]:
    url = (
        f"{EDGAR_FTS_HOST}/LATEST/search-index?q={urllib.parse.quote(query)}"
        f"&forms={forms}&startdt={start.isoformat()}&enddt={end.isoformat()}"
    )
    payload = _json(fetcher(url, headers=EDGAR_UA))
    if not isinstance(payload, dict):
        raise CaptureLaneRejected(
            "DISCOVERY_SOURCE_INVALID", "EDGAR full-text response is not an object"
        )
    hits = payload.get("hits", {}).get("hits", [])
    out = []
    for hit in hits:
        source = hit.get("_source", {})
        names = source.get("display_names") or [""]
        match = re.search(r"\(([A-Z.]{1,6})\)", str(names[0]))
        if not match:
            continue
        hit_id = str(hit.get("_id", ""))
        document = hit_id.split(":", 1)[1] if ":" in hit_id else None
        out.append(
            {
                "ticker": match.group(1),
                "issuer": str(names[0]).split("(")[0].strip(),
                "cik": str((source.get("ciks") or [""])[0]),
                "accession": str(source.get("adsh", "")),
                "file_date": str(source.get("file_date", "")),
                "items": [str(item) for item in (source.get("items") or [])],
                "primary_document": document,
            }
        )
    return out


BMO_CONTEXT_MARKERS = (
    re.compile(r"before\s+(?:the\s+)?market\s+open", re.IGNORECASE),
    re.compile(r"prior\s+to\s+(?:the\s+)?(?:market\s+open|the\s+open)", re.IGNORECASE),
    re.compile(r"before\s+the\s+opening\s+bell", re.IGNORECASE),
    re.compile(r"pre-?market", re.IGNORECASE),
    re.compile(r"\bBMO\b"),
    re.compile(r"\d{1,2}:\d{2}\s*a\.?\s?m\.?\s*(?:ET|EST|EDT|Eastern|CT|Central)", re.IGNORECASE),
)
EARNINGS_CONTEXT_MARKERS = (
    re.compile(r"earnings", re.IGNORECASE),
    re.compile(r"financial\s+results", re.IGNORECASE),
    re.compile(r"fiscal\s+\w+\s+results", re.IGNORECASE),
    re.compile(r"conference\s+call", re.IGNORECASE),
)


def _preannounce_hits(fetcher: Fetcher, session_date: date) -> list[dict]:
    """Enumerate pre-announced BMO earnings events for session_date.

    The universe freeze (prior session 16:15 ET) precedes the Tuesday-morning
    Item 2.02 filings, so BMO candidates must be enumerable from issuer
    pre-announcements (the registered ISSUER_IR_EARNINGS_ANNOUNCEMENT source):
    recent filings whose text names the session date.  Each candidate document
    is fetched and classified locally: an earnings context plus a before-open
    timing marker within +-400 characters of the date mention.  Only what was
    publicly enumerable at freeze time enters the manifest; the matched context
    snippet is recorded as the enumeration provenance.  Everything else is
    honestly outside the universe.
    """

    month_names = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    month = month_names[session_date.month - 1]
    date_phrases = (
        f"{month} {session_date.day}, {session_date.year}",
        f"{month} {session_date.day:02d}, {session_date.year}",
        f"{month[:3]} {session_date.day}, {session_date.year}",
        f"{session_date.month}/{session_date.day}/{session_date.year}",
    )
    start = session_date - timedelta(days=30)
    end = session_date - timedelta(days=1)
    out: dict[str, dict] = {}
    seen_pairs: set[tuple[str, str]] = set()
    for phrase in dict.fromkeys(date_phrases):
        try:
            hits = _fts_hits(fetcher, f'"{phrase}"', "8-K", start, end)
        except (urllib.error.HTTPError, CaptureLaneRejected):
            continue
        for hit in hits:
            key = (hit["ticker"], hit["accession"])
            if key in seen_pairs or hit["ticker"] in out:
                seen_pairs.add(key)
                continue
            seen_pairs.add(key)
            document = hit.get("primary_document")
            cik = hit["cik"].zfill(10)
            if not document or not cik:
                continue
            accn = hit["accession"].replace("-", "")
            archive_base = f"{EDGAR_WWW_HOST}/Archives/edgar/data/{cik.lstrip('0') or cik}"
            try:
                raw = fetcher(f"{archive_base}/{accn}/{document}", headers=EDGAR_UA)
            except (urllib.error.HTTPError, CaptureLaneRejected, OSError):
                continue
            text = _strip_html(raw)
            index = text.find(phrase)
            if index < 0:
                lowered = text.lower()
                index = lowered.find(phrase.lower())
            if index < 0:
                continue
            context = text[max(0, index - 400) : index + 400]
            timing = next(
                (pattern.pattern for pattern in BMO_CONTEXT_MARKERS if pattern.search(context)),
                None,
            )
            earnings = next(
                (
                    pattern.pattern
                    for pattern in EARNINGS_CONTEXT_MARKERS
                    if pattern.search(context)
                ),
                None,
            )
            if timing is None or earnings is None:
                continue
            hit["discovery_mode"] = "PREANNOUNCEMENT"
            hit["preannounce_phrase"] = phrase
            hit["preannounce_timing_marker"] = timing
            hit["preannounce_context"] = re.sub(r"\s+", " ", context)[:500]
            out[hit["ticker"]] = hit
    return list(out.values())


def cmd_discover(args: argparse.Namespace) -> int:
    fetcher = _fetcher(args)
    session_date = date.fromisoformat(args.session_date)
    since = date.fromisoformat(args.since) if args.since else session_date - timedelta(days=7)

    hits = _fts_hits(fetcher, '"Item 2.02"', "8-K", since, session_date)
    for hit in hits:
        hit["discovery_mode"] = "ITEM_202_FILING"
    if args.mode in ("both", "preannounce"):
        existing = {h["ticker"] for h in hits}
        for hit in _preannounce_hits(fetcher, session_date):
            if hit["ticker"] not in existing:
                hits.append(hit)
    trading_host = args.trading_host.rstrip("/")
    calendar = _calendar_sessions(
        fetcher, trading_host, session_date - timedelta(days=14), session_date
    )
    prior_session: date | None = None
    for row in calendar:
        day = date.fromisoformat(str(row["date"]))
        full_regular = str(row["open"]) == "09:30" and str(row["close"]) == "16:00"
        if day < session_date and full_regular:
            prior_session = day
    events = []
    seen: set[str] = set()
    for hit in hits:
        ticker = hit["ticker"]
        if ticker in seen or not hit["cik"]:
            continue
        seen.add(ticker)
        try:
            asset = _json(fetcher(f"{trading_host}/v2/assets/{urllib.parse.quote(ticker)}"))
        except (urllib.error.HTTPError, CaptureLaneRejected):
            events.append({"ticker": ticker, "screen": "ASSET_QUERY_FAILED", **hit})
            continue
        if not isinstance(asset, dict):
            continue
        submissions = _json(
            fetcher(
                f"{EDGAR_DATA_HOST}/submissions/CIK{hit['cik'].zfill(10)}.json", headers=EDGAR_UA
            )
        )
        accepted = None
        items = None
        recent = (
            submissions.get("filings", {}).get("recent", {})
            if isinstance(submissions, dict)
            else {}
        )
        for index, accession in enumerate(recent.get("accessionNumber", [])):
            if accession == hit["accession"]:
                accepted = str(recent["acceptanceDateTime"][index])
                items = str(recent.get("items", [""] * len(recent["accessionNumber"]))[index])
                break
        entry = {
            **hit,
            "items": items or ",".join(hit["items"]),
            "accepted_at": accepted,
            "exchange": asset.get("exchange"),
            "asset_class": asset.get("class"),
            "asset_status": asset.get("status"),
            "tradable": asset.get("tradable"),
            "asset_name": asset.get("name"),
            "sic": None,
        }
        if isinstance(submissions, dict):
            entry["sic"] = submissions.get("sic")
            entry["sec_name"] = submissions.get("name")
        entry["cohort"] = (
            "BMO"
            if hit.get("discovery_mode") == "PREANNOUNCEMENT"
            else (
                _classify_cohort(_parse_iso(accepted), session_date, prior_session)
                if accepted
                else None
            )
        )
        entry["prior_session"] = prior_session.isoformat() if prior_session else None
        events.append(entry)
    payload = {
        "schema": "esscher.capture_discovery",
        "schema_version": 1,
        "session_date": session_date.isoformat(),
        "since": since.isoformat(),
        "generated_at": _iso(datetime.now(UTC)),
        "claims": ["NO_CREDENTIALS", "PAPER_ONLY", "SOURCE_GROUNDED"],
        "events": sorted(
            events, key=lambda item: (str(item.get("ticker")), str(item.get("accession")))
        ),
    }
    digest = _write_json(Path(args.out), payload)
    eligible_preview = [
        e
        for e in payload["events"]
        if e.get("exchange") == "NYSE"
        and e.get("asset_class") == "us_equity"
        and e.get("asset_status") == "active"
        and e.get("tradable")
        and e.get("cohort")
    ]
    print(
        f"discovery: {len(payload['events'])} filings, "
        f"{len(eligible_preview)} NYSE-primary with cohort"
    )
    for event in eligible_preview:
        print(
            f"  {event['ticker']:6s} {event['cohort']} "
            f"accepted={event['accepted_at']} sic={event['sic']}"
        )
    print(f"written {args.out} sha256={digest}")
    return 0


# ---------------------------------------------------------------------------
# screening
# ---------------------------------------------------------------------------


def _sector_for_sic(sic: object) -> str | None:
    try:
        value = int(str(sic))
    except (TypeError, ValueError):
        return None
    for low, high, sector in SIC_GICS_OVERRIDES:
        if low <= value <= high and sector:
            return sector
    for low, high, sector in SIC_GICS_RANGES:
        if low <= value <= high:
            return sector or None
    return None


def _bars_page(
    fetcher: Fetcher, data_host: str, symbol: str, start: date, end: date, adjustment: str
) -> list[dict]:
    bars: list[dict] = []
    token: str | None = None
    while True:
        url = (
            f"{data_host}/v2/stocks/{urllib.parse.quote(symbol)}/bars?timeframe=1Day"
            f"&start={start.isoformat()}&end={end.isoformat()}&adjustment={adjustment}&limit=10000"
        )
        if token:
            url += f"&page_token={urllib.parse.quote(token)}"
        payload = _json(fetcher(url))
        if not isinstance(payload, dict):
            raise CaptureLaneRejected(
                "MARKET_SOURCE_INVALID", f"{symbol} bars response is not an object"
            )
        page = payload.get("bars") or []
        if isinstance(page, dict):
            page = page.get(symbol, [])
        bars.extend(page)
        token = payload.get("next_page_token")
        if not token:
            return bars


def _session_dates_from_bars(bars: Sequence[Mapping[str, object]]) -> list[date]:
    out = []
    for bar in bars:
        stamp = str(bar.get("t", ""))
        if stamp:
            out.append(_parse_iso(stamp).astimezone(ET).date())
    return sorted(set(out))


def cmd_screen(args: argparse.Namespace) -> int:
    fetcher = _fetcher(args)
    data_host = args.data_host.rstrip("/")
    discovery = json.loads(Path(args.discovery).read_text(encoding="utf-8"))
    session_date = date.fromisoformat(discovery["session_date"])
    # liquidity lookback ends at the last completed session before session_date
    end = session_date - timedelta(days=1)
    start = end - timedelta(days=45)
    results = []
    for event in discovery["events"]:
        ticker = str(event.get("ticker", ""))
        record = {"ticker": ticker, "reasons": []}
        if event.get("exchange") != "NYSE":
            record["reasons"].append("PRIMARY_EXCHANGE_NOT_XNYS")
        if (
            event.get("asset_class") != "us_equity"
            or event.get("asset_status") != "active"
            or not event.get("tradable")
        ):
            record["reasons"].append("NOT_ACTIVE_TRADABLE_COMMON")
        if not event.get("cohort"):
            record["reasons"].append("TIMING_BUCKET_UNKNOWN")
        sector = _sector_for_sic(event.get("sic"))
        record["sector"] = sector
        if sector is None:
            record["reasons"].append("SECTOR_MAPPING_UNAVAILABLE")
        if not record["reasons"]:
            bars = _bars_page(fetcher, data_host, ticker, start, end, "split")
            raw = _bars_page(fetcher, data_host, ticker, start, end, "raw")
            adjusted_sorted = sorted(bars, key=lambda item: str(item.get("t")))
            raw_sorted = sorted(raw, key=lambda item: str(item.get("t")))
            if len(adjusted_sorted) != len(raw_sorted) or any(
                str(a.get("c")) != str(b.get("c"))
                for a, b in zip(adjusted_sorted, raw_sorted, strict=False)
            ):
                record["reasons"].append("ADJUSTMENT_SOURCE_UNAVAILABLE")
            closes = [Decimal(str(bar["c"])) for bar in raw_sorted]
            volumes = [int(bar["v"]) for bar in raw_sorted]
            if len(closes) < MINIMUM_LIQUIDITY_SESSIONS:
                record["reasons"].append("INSUFFICIENT_LIQUIDITY_SESSIONS")
            else:
                record["prior_regular_close"] = str(closes[-1])
                if closes[-1] < PRICE_FLOOR:
                    record["reasons"].append("PRICE_BELOW_MINIMUM")
                dollar_volumes = [
                    Decimal(str(c)) * Decimal(v)
                    for c, v in zip(
                        closes[-LIQUIDITY_SESSION_COUNT:],
                        volumes[-LIQUIDITY_SESSION_COUNT:],
                        strict=True,
                    )
                ]
                dollar_volumes.sort()
                count = len(dollar_volumes)
                median = (
                    dollar_volumes[count // 2]
                    if count % 2
                    else (dollar_volumes[count // 2 - 1] + dollar_volumes[count // 2]) / 2
                )
                record["median_dollar_volume"] = str(median)
                if count < MINIMUM_LIQUIDITY_SESSIONS or median < MINIMUM_MEDIAN_DOLLAR_VOLUME:
                    record["reasons"].append("INSUFFICIENT_MEDIAN_DOLLAR_VOLUME")
            # optionability via entitled option snapshots (>=20 active contracts)
            try:
                snapshot = _json(
                    fetcher(
                        f"{data_host}/v1beta1/options/snapshots/{urllib.parse.quote(ticker)}?limit=1000"
                    )
                )
                names = list(snapshot.get("snapshots", {})) if isinstance(snapshot, dict) else []
                record["option_contract_count"] = len(names)
                if len(names) < MINIMUM_ACTIVE_OPTION_CONTRACTS:
                    record["reasons"].append("NOT_OPTIONABLE_AT_FREEZE")
            except urllib.error.HTTPError:
                record["reasons"].append("OPTION_ENTITLEMENT_UNAVAILABLE")
            # XBRL EPS depth (12 quarters incl. the just-reported one once filed;
            # at freeze time the current quarter comes from the release, so the
            # history must carry >=11 prior quarters)
            eps_points = _xbrl_quarter_points(
                fetcher, str(event.get("cik", "")), "EarningsPerShareDiluted"
            )
            rev_points = _xbrl_quarter_points(fetcher, str(event.get("cik", "")), "Revenue")
            if not eps_points:
                eps_points = _xbrl_quarter_points(
                    fetcher, str(event.get("cik", "")), "EarningsPerShareBasicAndDiluted"
                )
            if not rev_points:
                rev_points = _xbrl_quarter_points(
                    fetcher,
                    str(event.get("cik", "")),
                    "RevenueFromContractWithCustomerExcludingAssessedTax",
                )
            record["xbrl_eps_quarters"] = len(eps_points)
            record["xbrl_revenue_quarters"] = len(rev_points)
            if len(eps_points) < MINIMUM_EPS_QUARTERS - 1:
                record["reasons"].append("FEATURE_SOURCE_UNAVAILABLE_EPS_HISTORY")
            if len(rev_points) < MINIMUM_REVENUE_QUARTERS - 1:
                record["reasons"].append("FEATURE_SOURCE_UNAVAILABLE_REVENUE_HISTORY")
        record["eligible"] = not record["reasons"]
        record["accession"] = event.get("accession")
        record["cik"] = event.get("cik")
        record["cohort"] = event.get("cohort")
        record["issuer"] = event.get("sec_name") or event.get("issuer")
        record["accepted_at"] = event.get("accepted_at")
        results.append(record)
    payload = {
        "schema": "esscher.capture_screening",
        "schema_version": 1,
        "session_date": session_date.isoformat(),
        "generated_at": _iso(datetime.now(UTC)),
        "results": sorted(results, key=lambda item: str(item.get("ticker"))),
    }
    digest = _write_json(Path(args.out), payload)
    eligible = [r for r in results if r["eligible"]]
    print(
        f"screening: {len(results)} events, {len(eligible)} eligible: "
        f"{[r['ticker'] for r in eligible]}"
    )
    for record in results:
        if not record["eligible"]:
            print(f"  {record['ticker']:6s} INELIGIBLE {record['reasons']}")
    print(f"written {args.out} sha256={digest}")
    return 0


def _xbrl_quarter_points(fetcher: Fetcher, cik: str, concept: str) -> list[dict]:
    if not cik:
        return []
    payload = _json(
        fetcher(
            f"{EDGAR_DATA_HOST}/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json",
            headers=EDGAR_UA,
        )
    )
    if not isinstance(payload, dict):
        return []
    units = payload.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {})
    points = []
    for unit_key, entries in units.items():
        if unit_key not in ("USD", "USD/shares"):
            continue
        for entry in entries:
            start = entry.get("start")
            end = entry.get("end")
            if not start or not end:
                continue
            span = (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
            if not 75 <= span <= 115:
                continue
            points.append(
                {
                    "unit": unit_key,
                    **{
                        k: entry.get(k)
                        for k in ("start", "end", "val", "fy", "fp", "form", "filed", "accn")
                    },
                }
            )
    by_end: dict[str, tuple[int, dict]] = {}
    for point in points:
        priority = 0 if point.get("form") == "8-K" else (1 if point.get("form") == "10-Q" else 2)
        existing = by_end.get(str(point["end"]))
        if existing is None or priority < existing[0]:
            by_end[str(point["end"])] = (priority, point)
    return sorted((value[1] for value in by_end.values()), key=lambda item: str(item["end"]))


def _fetcher(args: argparse.Namespace) -> Fetcher:
    headers = _load_credentials(Path(args.credentials_env))
    return LiveFetcher(alpaca_headers=headers)


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------


def cmd_freeze_manifest(args: argparse.Namespace) -> int:
    discovery = json.loads(Path(args.discovery).read_text(encoding="utf-8"))
    screening = json.loads(Path(args.screening).read_text(encoding="utf-8"))
    session_date = date.fromisoformat(discovery["session_date"])
    by_ticker = {str(r["ticker"]): r for r in screening["results"]}
    if args.frozen_at:
        frozen_at = _parse_iso(args.frozen_at)
    else:
        frozen_at = datetime.combine(session_date, datetime.min.time(), tzinfo=ET).replace(
            hour=16, minute=15
        ) - timedelta(days=1)
        frozen_at = frozen_at.astimezone(UTC)
    records = []
    for event in discovery["events"]:
        ticker = str(event.get("ticker", ""))
        screen = by_ticker.get(ticker)
        if screen is None:
            continue
        cik = str(event.get("cik", "")).zfill(10)
        cohort = str(event.get("cohort") or "")
        if cohort == "BMO":
            scheduled = datetime.combine(session_date, datetime.min.time(), tzinfo=ET).replace(
                hour=9
            )
        elif cohort == "AMC":
            scheduled = datetime.combine(session_date, datetime.min.time(), tzinfo=ET).replace(
                hour=16, minute=5
            ) - timedelta(days=1)
        else:
            scheduled = datetime.combine(session_date, datetime.min.time(), tzinfo=ET).replace(
                hour=9
            )
        reasons = tuple(sorted(set(screen.get("reasons") or [])))
        eligible = screen.get("eligible") is True
        records.append(
            {
                "cohort_id": cohort or "BMO",
                "eligibility": "ELIGIBLE" if eligible else "INELIGIBLE",
                "event_id": f"{ticker}-{session_date:%Y%m%d}-EARNINGS",
                "issuer": str(screen.get("issuer") or event.get("issuer") or ticker),
                "reason_codes": [] if eligible else list(reasons),
                "scheduled_at": _iso(scheduled.astimezone(UTC)),
                "security_id": f"CIK-{cik}",
                "ticker": ticker,
            }
        )
    if not records:
        raise CaptureLaneRejected(
            "EMPTY_UNIVERSE", "no discovered events survived to manifest records"
        )
    records.sort(key=lambda item: str(item["event_id"]))
    policy_version = int(getattr(args, "policy_version", 1) or 1)
    if policy_version == 3:
        policy_sha = strategy_policy_v3_sha256()
        candidate_id = CANDIDATE_ID_V3
        selection_rule_id = SELECTION_RULE_ID_V3
        manifest_id = f"live-earnings-candidates-delayed-demo-{session_date.isoformat()}"
    elif policy_version == 1:
        policy_sha = strategy_policy_sha256()
        candidate_id = CANDIDATE_ID
        selection_rule_id = SELECTION_RULE_ID
        manifest_id = f"live-earnings-candidates-{session_date.isoformat()}"
    else:
        raise CaptureLaneRejected("UNSUPPORTED_POLICY_VERSION", f"{policy_version}")
    manifest = {
        "candidate_id": candidate_id,
        "frozen_at": _iso(frozen_at),
        "manifest_id": manifest_id,
        "policy_sha256": policy_sha,
        "producer_build_sha256": PRODUCER_BUILD_SHA256,
        "records": records,
        "schema": "esscher.candidate_manifest",
        "schema_version": 1,
        "selection_rule_id": selection_rule_id,
    }
    raw = canonical_json_bytes(manifest)
    parse_candidate_manifest(raw)  # fail closed unless the frozen contract accepts it
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw + b"\n")
    eligible_count = sum(1 for r in records if r["eligibility"] == "ELIGIBLE")
    print(
        f"manifest frozen_at={manifest['frozen_at']} records={len(records)} "
        f"eligible={eligible_count}"
    )
    print(f"written {out} sha256={hashlib.sha256(raw).hexdigest()}")
    return 0


# ---------------------------------------------------------------------------
# prefetch (historical; legal on the Basic plan - everything is >15 min old)
# ---------------------------------------------------------------------------


def _calendar_sessions(fetcher: Fetcher, trading_host: str, start: date, end: date) -> list[dict]:
    payload = _json(
        fetcher(f"{trading_host}/v2/calendar?start={start.isoformat()}&end={end.isoformat()}")
    )
    if not isinstance(payload, list):
        raise CaptureLaneRejected("CALENDAR_SOURCE_INVALID", "calendar response is not a list")
    return payload


def _session_record(row: Mapping[str, object], retrieved_at: str) -> dict:
    day = date.fromisoformat(str(row["date"]))
    open_et = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(
        hour=int(str(row["open"])[:2]), minute=int(str(row["open"])[3:5])
    )
    close_et = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(
        hour=int(str(row["close"])[:2]), minute=int(str(row["close"])[3:5])
    )
    full_regular = str(row["open"]) == "09:30" and str(row["close"]) == "16:00"
    record = {
        "close_at": _iso(close_et.astimezone(UTC)),
        "exchange_mic": "XNYS",
        "full_regular": full_regular,
        "open_at": _iso(open_et.astimezone(UTC)),
        "session_date": day.isoformat(),
        "session_id": f"XNYS-{day.isoformat()}",
    }
    record["provenance"] = {
        "content_sha256": _sha_text(canonical_json_bytes(dict(row)).decode("utf-8")),
        "entitlement": "PUBLIC",
        "limitations": [],
        "published_at": None,
        "published_at_precision": "DATE",
        "publisher": "NYSE_OFFICIAL_CALENDAR",
        "redistribution_status": "REDISTRIBUTABLE",
        "retrieved_at": retrieved_at,
        "source_class": "OFFICIAL_EXCHANGE_CALENDAR",
    }
    return record


def _trades_page(
    fetcher: Fetcher, data_host: str, symbol: str, start: datetime, end: datetime
) -> tuple[list[dict], int]:
    out: list[dict] = []
    token: str | None = None
    pages = 0
    while True:
        url = (
            f"{data_host}/v2/stocks/{urllib.parse.quote(symbol)}/trades"
            f"?start={_iso(start)}&end={_iso(end)}&limit=10000&sort=asc"
        )
        if token:
            url += f"&page_token={urllib.parse.quote(token)}"
        payload = _json(fetcher(url))
        if not isinstance(payload, dict):
            raise CaptureLaneRejected(
                "MARKET_SOURCE_INVALID", f"{symbol} trades response is not an object"
            )
        out.extend(payload.get("trades") or [])
        pages += 1
        token = payload.get("next_page_token")
        if not token:
            return out, pages


def _quotes_page(
    fetcher: Fetcher, data_host: str, symbol: str, start: datetime, end: datetime
) -> tuple[list[dict], int]:
    out: list[dict] = []
    token: str | None = None
    pages = 0
    while True:
        url = (
            f"{data_host}/v2/stocks/{urllib.parse.quote(symbol)}/quotes"
            f"?start={_iso(start)}&end={_iso(end)}&limit=10000&sort=asc"
        )
        if token:
            url += f"&page_token={urllib.parse.quote(token)}"
        payload = _json(fetcher(url))
        if not isinstance(payload, dict):
            raise CaptureLaneRejected(
                "MARKET_SOURCE_INVALID", f"{symbol} quotes response is not an object"
            )
        out.extend(payload.get("quotes") or [])
        pages += 1
        token = payload.get("next_page_token")
        if not token:
            return out, pages


def _trade_record(raw: Mapping[str, object], symbol: str, session_id: str) -> dict | None:
    conditions = [str(c) for c in (raw.get("c") or [])]
    sale_condition = "OPENING_AUCTION" if "O" in conditions else "REGULAR_CONTINUOUS"
    observed = _parse_iso(str(raw["t"]))
    price = Decimal(str(raw["p"]))
    size = int(raw["s"])  # type: ignore[arg-type]
    if price <= 0 or size <= 0:
        # Not a valid print under the frozen Trade contract (corrections,
        # zero-size records); filtered counts are disclosed in the artifact.
        return None
    # microsecond rounding for the frozen loader; nanoseconds are preserved in
    # the raw archive.  Exact-microsecond collisions fail the window gate
    # closed (DUPLICATE_OBSERVATION) by design - the tape is never altered.
    return {
        "observed_at": observed.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "price": str(price),
        "sale_condition": sale_condition,
        "session_id": session_id,
        "size": size,
        "symbol": symbol,
    }


def _consolidate_same_microsecond(
    records: list[dict],
) -> tuple[list[dict], dict[str, object]]:
    """Merge REGULAR_CONTINUOUS prints sharing one microsecond timestamp.

    The SIP tape carries nanosecond stamps; the frozen Trade model resolves to
    microseconds, so distinct prints can collide after rounding (birthday odds
    make this routine for liquid symbols).  Merging preserves window volume and
    VWAP EXACTLY (size sums, size-weighted price).  If a differing-price merge
    lands on the first or last eligible timestamp, the boundary price is
    ambiguous and the capture fails closed instead of choosing.
    """

    eligible_idx = [k for k, r in enumerate(records) if r["sale_condition"] == "REGULAR_CONTINUOUS"]
    first_ts = records[eligible_idx[0]]["observed_at"] if eligible_idx else None
    last_ts = records[eligible_idx[-1]]["observed_at"] if eligible_idx else None
    result: list[dict] = []
    groups = 0
    prints = 0
    boundary_ambiguous = False
    index = 0
    total = len(records)
    while index < total:
        record = records[index]
        if record["sale_condition"] != "REGULAR_CONTINUOUS":
            result.append(record)
            index += 1
            continue
        end = index
        while (
            end + 1 < total
            and records[end + 1]["sale_condition"] == "REGULAR_CONTINUOUS"
            and records[end + 1]["observed_at"] == record["observed_at"]
        ):
            end += 1
        group = records[index : end + 1]
        if len(group) == 1:
            result.append(group[0])
        else:
            groups += 1
            prints += len(group)
            merged_size = sum(int(item["size"]) for item in group)
            notional = sum(
                (Decimal(item["price"]) * Decimal(item["size"]) for item in group), Decimal(0)
            )
            prices = {str(item["price"]) for item in group}
            if len(prices) > 1 and record["observed_at"] in (first_ts, last_ts):
                boundary_ambiguous = True
            merged = dict(group[0])
            merged["price"] = format((notional / Decimal(merged_size)).normalize(), "f")
            merged["size"] = merged_size
            result.append(merged)
        index = end + 1
    stats = {
        "boundary_ambiguous": boundary_ambiguous,
        "merged_groups": groups,
        "merged_prints": prints,
    }
    return result, stats


def _quote_record(raw: Mapping[str, object], symbol: str, session_id: str) -> dict | None:
    observed = _parse_iso(str(raw["t"]))
    bid = Decimal(str(raw["bp"]))
    ask = Decimal(str(raw["ap"]))
    if bid <= 0 or ask <= 0 or ask < bid:
        # One-sided, zero-sided, or crossed SIP quote records are not valid
        # two-sided samples under the frozen QuoteSample contract; filtered
        # counts are disclosed in the artifact.
        return None
    return {
        "ask": str(ask),
        "bid": str(bid),
        "observed_at": observed.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "session_id": session_id,
        "symbol": symbol,
    }


def cmd_prefetch(args: argparse.Namespace) -> int:
    fetcher = _fetcher(args)
    data_host = args.data_host.rstrip("/")
    trading_host = args.trading_host.rstrip("/")
    manifest = json.loads(Path(args.manifest).read_bytes())
    session_date = date.fromisoformat(args.session_date)
    retrieved_at = _iso(datetime.now(UTC))
    eligible = [r for r in manifest["records"] if r["eligibility"] == "ELIGIBLE"]
    symbols = {str(r["ticker"]) for r in eligible}
    sectors = {str(r["ticker"]): _sector_for_sic(_sic_for(fetcher, r)) for r in eligible}
    symbols.add("SPY")
    symbols.update(etf for etf in (SECTOR_ETFS.get(s or "") for s in sectors.values()) if etf)

    calendar = _calendar_sessions(
        fetcher, trading_host, session_date - timedelta(days=560), session_date
    )
    sessions = [_session_record(row, retrieved_at) for row in calendar]
    full_regular = [s for s in sessions if s["full_regular"]]

    bars: dict[str, list[dict]] = {}
    for symbol in sorted(symbols):
        start = session_date - timedelta(days=560)
        end = session_date - timedelta(days=1)
        raw_bars = _bars_page(fetcher, data_host, symbol, start, end, "raw")
        bars[symbol] = [
            {
                "close": str(bar["c"]),
                "session_date": _parse_iso(str(bar["t"])).astimezone(ET).date().isoformat(),
                "session_id": f"XNYS-{_parse_iso(str(bar['t'])).astimezone(ET).date().isoformat()}",
                "symbol": symbol,
                "valid": True,
                "volume": int(bar["v"]),
            }
            for bar in raw_bars
        ]

    prior_sessions = sorted(
        (s for s in full_regular if date.fromisoformat(s["session_date"]) < session_date),
        key=lambda s: s["session_date"],
    )[-LIQUIDITY_SESSION_COUNT:]
    prior_window_trades: dict[str, list[dict]] = {}
    for session in prior_sessions:
        day = date.fromisoformat(session["session_date"])
        start = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=30)
        end = start + timedelta(minutes=5)
        merged: list[dict] = []
        for record in eligible:
            ticker = str(record["ticker"])
            raw_trades, _pages = _trades_page(
                fetcher, data_host, ticker, start.astimezone(UTC), end.astimezone(UTC)
            )
            records = [_trade_record(t, ticker, session["session_id"]) for t in raw_trades]
            valid = [record for record in records if record is not None]
            # Prior-window features consume only per-session volume sums, which
            # same-microsecond consolidation preserves exactly.
            consolidated, _stats = _consolidate_same_microsecond(valid)
            merged.extend(consolidated)
        prior_window_trades[session["session_id"]] = merged

    history: dict[str, dict] = {}
    for record in eligible:
        ticker = str(record["ticker"])
        cik = str(record["security_id"]).removeprefix("CIK-")
        eps = _xbrl_quarter_points(fetcher, cik, "EarningsPerShareDiluted") or _xbrl_quarter_points(
            fetcher, cik, "EarningsPerShareBasicAndDiluted"
        )
        rev = _xbrl_quarter_points(fetcher, cik, "Revenue") or _xbrl_quarter_points(
            fetcher, cik, "RevenueFromContractWithCustomerExcludingAssessedTax"
        )
        history[ticker] = {"eps": eps, "revenue": rev, "sector": sectors.get(ticker)}

    payload = {
        "schema": "esscher.capture_prefetch",
        "schema_version": 1,
        "session_date": session_date.isoformat(),
        "retrieved_at": retrieved_at,
        "sessions": sessions,
        "daily_bars": bars,
        "prior_window_trades": prior_window_trades,
        "quarter_history": history,
        "market_publisher": "ALPACA_SIP_EQUITY_FEED",
        "market_redistribution": "NON_REDISTRIBUTABLE",
    }
    digest = _write_json(Path(args.out), payload)
    history_summary = {k: (len(v["eps"]), len(v["revenue"])) for k, v in history.items()}
    print(
        f"prefetch: sessions={len(sessions)} symbols={sorted(symbols)} "
        f"prior_sessions={len(prior_window_trades)} history={history_summary}"
    )
    print(f"written {args.out} sha256={digest}")
    return 0


def _sic_for(fetcher: Fetcher, record: Mapping[str, object]) -> object:
    cik = str(record["security_id"]).removeprefix("CIK-")
    payload = _json(
        fetcher(f"{EDGAR_DATA_HOST}/submissions/CIK{cik.zfill(10)}.json", headers=EDGAR_UA)
    )
    return payload.get("sic") if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# release capture (event-day, pre-open): dateline publication time + current
# quarter + guidance detection from the primary EX-99
# ---------------------------------------------------------------------------

DATELINE_PATTERNS = (
    re.compile(
        r"([A-Z][A-Za-z .,'&-]{2,40}?),\s+([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})"
        r"(?:\s+\((?:[A-Z]+\s*(?:NEWSWIRE|WIRE|PR)\w*)\))?\s*(?:--|вЂ”|вЂ“|-|/)?\s*"
        r"(\d{1,2}:\d{2}\s*(?:[AP]\.?\s?[MP]\.?\s*(?:ET|EST|EDT|Eastern Time)?)?)?"
    ),
    re.compile(
        r"([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})\s+(\d{1,2}:\d{2}\s*[AP]\.?\s?[MP]\.?\s*(?:ET|EST|EDT))"
    ),
    # Generic fallback for dash-separated datelines ("GALWAY, Ireland -
    # September 1, 2026 - ..."): bare month/day/year, optional trailing time.
    # Restricted to the dateline zone; city-prefixed patterns take precedence.
    re.compile(
        r"([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})"
        r"(?:\s*(?:--|\u2014|\u2013|-|/|\()\s*)?"
        r"(\d{1,2}:\d{2}\s*(?:[AP]\.?\s?[MP]\.?\s*(?:ET|EST|EDT|Eastern Time)?)?)?"
    ),
)
MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sept": 9,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

EPS_LABELS = (
    "gaap diluted eps",
    "diluted earnings per share",
    "earnings per diluted share",
    "diluted eps",
    "net earnings per diluted share",
    "per diluted share",
)
REVENUE_LABELS = (
    "total revenue",
    "net revenue",
    "revenues",
    "net sales",
    "total net sales",
    "revenue",
)
GUIDANCE_MARKERS = ("guidance", "financial outlook", "outlook")


def _strip_html(raw: bytes) -> str:
    text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "replace"))
    text = text.replace("&#160;", " ").replace("&#8217;", "'").replace("&#8211;", "-")
    text = text.replace("&#8226;", "*").replace("&#38;", "&").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text)
    return text


def _parse_dateline(text: str) -> tuple[date, datetime | None] | None:
    for index, pattern in enumerate(DATELINE_PATTERNS):
        zone = text[:6000] if index < 2 else text[:3000]
        match = pattern.search(zone)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 5:
            month_name, day, year, time_part = groups[1], groups[2], groups[3], groups[4]
        else:
            month_name, day, year, time_part = groups[0], groups[1], groups[2], groups[3]
        month = MONTHS.get(str(month_name))
        if not month:
            continue
        day_date = date(int(year), month, int(day))
        published = None
        if time_part:
            time_match = re.search(r"(\d{1,2}):(\d{2})\s*([AP])", str(time_part))
            if time_match:
                hour = int(time_match.group(1)) % 12
                if time_match.group(3).upper() == "P":
                    hour += 12
                published = datetime.combine(day_date, datetime.min.time(), tzinfo=ET).replace(
                    hour=hour, minute=int(time_match.group(2))
                )
        return day_date, (published.astimezone(UTC) if published else None)
    return None


SCALES = {
    "thousand": Decimal(1_000),
    "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
}
_VALUE_RE = r"\$?\s?\(?\s?(-?\d[\d,]*(?:\.\d{1,4})?)\s?\)?\s*(thousand|million|billion)?"


def _find_quarter_value(text: str, labels: Sequence[str]) -> str | None:
    """Earliest labelled ACTUAL monetary value across all labels, scale-expanded.

    Guidance-style ranges ("$X to $Y") are skipped; among all label hits the
    earliest text position wins (release headlines/highlights precede tables
    and prose), so an unrelated later label cannot shadow the reported actual.
    """

    lowered = text.lower()
    best: tuple[int, str] | None = None
    for label in labels:
        start = 0
        while True:
            index = lowered.find(label, start)
            if index < 0:
                break
            start = index + len(label)
            tail = text[index + len(label) : index + len(label) + 400]
            match = re.search(_VALUE_RE, tail, re.IGNORECASE)
            if not match:
                continue
            after = tail[match.end() : match.end() + 40]
            if re.match(r"\s*(?:to|-|\u2013)\s*\$?\s*[\d.,]+", after):
                continue  # a guidance-style range, not a reported actual
            raw_value = match.group(1).replace(",", "")
            scale = SCALES.get((match.group(2) or "").lower(), Decimal(1))
            value = Decimal(raw_value) * scale
            if "(" in tail[: match.start() + len(match.group(1)) + 2]:
                value = -abs(value)
            candidate = (index, format(value.normalize(), "f"))
            if best is None or candidate[0] < best[0]:
                best = candidate
    return best[1] if best else None


GUIDANCE_EPS_CURRENT = (
    re.compile(
        r"(?:diluted\s+)?(?:EPS|earnings\s+per\s+(?:diluted\s+)?share)"
        r"(?:\s+guidance)?\s*(?:of|to|in)?\s*(?:the\s+)?(?:new\s+)?range\s+of\s+"
        r"\$([\d.,]+)\s*(?:to|-|\u2013)\s*\$?([\d.,]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![Nn]on-)(?<![Nn]on )GAAP\s+(?:diluted\s+)?EPS(?:\s+guidance)?"
        r"\s*(?:of|to|in\s+the\s+range\s+of)\s+\$([\d.,]+)\s*(?:to|-|\u2013)\s*\$?([\d.,]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:diluted\s+)?EPS\s+(?:of|to)\s+\$([\d.,]+)\s*(?:to|-|\u2013)\s*\$?([\d.,]+)",
        re.IGNORECASE,
    ),
)
GUIDANCE_EPS_PRIOR = (
    re.compile(
        r"versus\s+(?:the\s+)?prior\s+\$([\d.,]+)\s*(?:to|-|\u2013)\s*\$?([\d.,]+)", re.IGNORECASE
    ),
    re.compile(
        r"(?:from|versus)\s+(?:the\s+)?prior\s+guidance\s+of\s+\$([\d.,]+)\s*(?:to|-|\u2013)\s*\$?([\d.,]+)",
        re.IGNORECASE,
    ),
)
GUIDANCE_REVENUE_RANGE = re.compile(
    r"(?:revenue|net\s+sales)(?:\s+guidance)?\s*(?:of|to|in\s+the\s+range\s+of)\s+"
    r"\$([\d.,]+)\s*(thousand|million|billion)?\s*(?:to|-|\u2013)\s*\$?([\d.,]+)\s*(thousand|million|billion)?",
    re.IGNORECASE,
)
GUIDANCE_WITHDRAWN = re.compile(
    r"withdraw\w*|suspend\w*|not\s+(?:providing|issuing|giving)|declined\s+to\s+(?:provide|issue)",
    re.IGNORECASE,
)
GUIDANCE_FY_LABEL = re.compile(r"(?:FY|fiscal)\s*(?:year\s*)?'?(20\d{2}|\d{2})\b", re.IGNORECASE)


def _decimal_or_none(raw: object) -> Decimal | None:
    try:
        cleaned = str(raw).replace(",", "").strip().strip(".")
        if not cleaned:
            return None
        return Decimal(cleaned)
    except Exception:
        return None


def _guidance_ranges(patterns: Sequence[re.Pattern], text: str) -> list[tuple[Decimal, Decimal]]:
    found = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            low = _decimal_or_none(match.group(1))
            high = _decimal_or_none(match.group(2))
            if low is None or high is None or low > high:
                continue
            found.append((low, high))
    unique = sorted(set(found))
    return unique


def _extract_guidance(text: str) -> dict[str, object]:
    """Conservative guidance extraction; ambiguity raises (fail closed)."""

    detected = any(marker in text.lower() for marker in GUIDANCE_MARKERS)
    result: dict[str, object] = {"detected": detected, "current": None, "prior": None}
    if not detected:
        return result
    withdrawn_match = None
    for marker in GUIDANCE_MARKERS:
        index = text.lower().find(marker)
        if index >= 0:
            zone = text[max(0, index - 250) : index + 250]
            if GUIDANCE_WITHDRAWN.search(zone):
                withdrawn_match = True
                break
    if withdrawn_match:
        result["current"] = {"withdrawn": True, "fiscal_period": None}
        return result
    eps_ranges = _guidance_ranges(GUIDANCE_EPS_CURRENT, text)
    if len(eps_ranges) > 1:
        raise CaptureLaneRejected(
            "GUIDANCE_EXTRACTION_AMBIGUOUS", f"multiple EPS guidance ranges: {eps_ranges}"
        )
    prior_ranges = _guidance_ranges(GUIDANCE_EPS_PRIOR, text)
    if len(prior_ranges) > 1:
        raise CaptureLaneRejected(
            "GUIDANCE_EXTRACTION_AMBIGUOUS", f"multiple prior EPS ranges: {prior_ranges}"
        )
    revenue_matches = list(GUIDANCE_REVENUE_RANGE.finditer(text))
    revenue_pairs = set()
    for match in revenue_matches:
        low_raw = _decimal_or_none(match.group(1))
        high_raw = _decimal_or_none(match.group(3))
        if low_raw is None or high_raw is None:
            continue
        low = low_raw * SCALES.get((match.group(2) or "").lower(), Decimal(1))
        high = high_raw * SCALES.get((match.group(4) or "").lower(), Decimal(1))
        if low <= high:
            revenue_pairs.add((low, high))
    if len(revenue_pairs) > 1:
        raise CaptureLaneRejected(
            "GUIDANCE_EXTRACTION_AMBIGUOUS",
            f"multiple revenue guidance ranges: {sorted(revenue_pairs)}",
        )
    if not eps_ranges and not revenue_pairs:
        raise CaptureLaneRejected(
            "GUIDANCE_EXTRACTION_UNRESOLVED",
            "guidance keywords present but no conservative range pattern matched",
        )
    period = None
    fy = GUIDANCE_FY_LABEL.search(text)
    if fy:
        digits = fy.group(1)
        period = f"FY{digits if len(digits) == 4 else '20' + digits}"
    current = {
        "withdrawn": False,
        "fiscal_period": period,
        "eps_low": str(eps_ranges[0][0]) if eps_ranges else None,
        "eps_high": str(eps_ranges[0][1]) if eps_ranges else None,
        "revenue_low": str(sorted(revenue_pairs)[0][0]) if revenue_pairs else None,
        "revenue_high": str(sorted(revenue_pairs)[0][1]) if revenue_pairs else None,
    }
    result["current"] = current
    if prior_ranges:
        result["prior"] = {
            "withdrawn": False,
            "fiscal_period": period,
            "eps_low": str(prior_ranges[0][0]),
            "eps_high": str(prior_ranges[0][1]),
            "revenue_low": None,
            "revenue_high": None,
        }
    return result


def cmd_capture_release(args: argparse.Namespace) -> int:
    fetcher = _fetcher(args)
    manifest = json.loads(Path(args.manifest).read_bytes())
    session_date = date.fromisoformat(args.session_date)
    retrieved_at = datetime.now(UTC)
    hits = _fts_hits(fetcher, '"Item 2.02"', "8-K", session_date, session_date)
    by_ticker = {h["ticker"]: h for h in hits}
    releases = {}
    for record in manifest["records"]:
        if record["eligibility"] != "ELIGIBLE":
            continue
        ticker = str(record["ticker"])
        hit = by_ticker.get(ticker)
        entry: dict[str, object] = {
            "event_id": record["event_id"],
            "ticker": ticker,
            "cohort": record["cohort_id"],
        }
        if hit is None:
            entry["status"] = "RELEASE_NOT_YET_FILED"
            releases[ticker] = entry
            continue
        cik = str(record["security_id"]).removeprefix("CIK-").zfill(10)
        accn = str(hit["accession"]).replace("-", "")
        index_payload = _json(
            fetcher(
                f"{EDGAR_WWW_HOST}/Archives/edgar/data/{cik.lstrip('0') or cik}/{accn}/index.json",
                headers=EDGAR_UA,
            )
        )
        names = (
            [str(item["name"]) for item in index_payload["directory"]["item"]]
            if isinstance(index_payload, dict)
            else []
        )
        exhibit = next(
            (
                n
                for n in names
                if re.search(r"(ex|exhibit).*99", n, re.I) and n.lower().endswith(".htm")
            ),
            None,
        )
        if exhibit is None:
            entry["status"] = "EXHIBIT_MISSING"
            releases[ticker] = entry
            continue
        raw = fetcher(
            f"{EDGAR_WWW_HOST}/Archives/edgar/data/{cik.lstrip('0') or cik}/{accn}/{exhibit}",
            headers=EDGAR_UA,
        )
        text = _strip_html(raw)
        dateline = _parse_dateline(text)
        entry["accession"] = hit["accession"]
        entry["exhibit"] = exhibit
        entry["file_date"] = hit.get("file_date")
        # Acceptance instant: corroboration only (never a published_at source).
        accepted_at = None
        submissions = _json(
            fetcher(f"{EDGAR_DATA_HOST}/submissions/CIK{cik}.json", headers=EDGAR_UA)
        )
        recent = (
            submissions.get("filings", {}).get("recent", {})
            if isinstance(submissions, dict)
            else {}
        )
        for index, accession in enumerate(recent.get("accessionNumber", [])):
            if accession == hit["accession"]:
                accepted_at = str(recent["acceptanceDateTime"][index])
                break
        entry["accepted_at"] = accepted_at
        entry["content_sha256"] = hashlib.sha256(raw).hexdigest()
        entry["text_head"] = text[:400]
        if dateline is None:
            entry["status"] = "PUBLICATION_TIME_UNPARSEABLE"
            releases[ticker] = entry
            continue
        dateline_date, published = dateline
        cohort = str(record["cohort_id"])
        if cohort == "BMO":
            if dateline_date != session_date:
                entry["status"] = "DATELINE_DATE_MISMATCH"
                releases[ticker] = entry
                continue
            if published is None:
                # DATE-precision midnight-ET lower bound is admissible only with
                # pre-open acceptance corroboration; an acceptance at/after the
                # open means same-morning publication is unproven -> fail closed.
                if accepted_at is None:
                    entry["status"] = "PUBLICATION_TIME_UNCORROBORATED"
                    releases[ticker] = entry
                    continue
                open_et = datetime.combine(session_date, datetime.min.time(), tzinfo=ET).replace(
                    hour=9, minute=30
                )
                if _parse_iso(accepted_at) >= open_et.astimezone(UTC):
                    entry["status"] = "PUBLICATION_TIME_UNCORROBORATED"
                    releases[ticker] = entry
                    continue
                published = datetime.combine(session_date, datetime.min.time(), tzinfo=ET)
                entry["published_at_precision"] = "DATE"
            else:
                entry["published_at_precision"] = "SECOND"
            entry["status"] = "CAPTURED"
            entry["published_at"] = _iso(published.astimezone(UTC))
        else:
            if published is None:
                entry["status"] = "PUBLICATION_TIME_UNPARSEABLE_AMC_REQUIRES_TIME"
                releases[ticker] = entry
                continue
            entry["status"] = "CAPTURED"
            entry["published_at"] = _iso(published)
            entry["published_at_precision"] = "SECOND"
        entry["retrieved_at"] = _iso(retrieved_at)
        entry["eps_current"] = _find_quarter_value(text, EPS_LABELS)
        entry["revenue_current"] = _find_quarter_value(text, REVENUE_LABELS)
        try:
            entry["guidance"] = _extract_guidance(text)
        except CaptureLaneRejected as rejection:
            entry["guidance"] = None
            entry["status"] = rejection.reason
            entry["detail"] = rejection.detail
            releases[ticker] = entry
            continue
        releases[ticker] = entry
    payload = {
        "schema": "esscher.capture_release",
        "schema_version": 1,
        "session_date": session_date.isoformat(),
        "retrieved_at": _iso(retrieved_at),
        "releases": releases,
    }
    digest = _write_json(Path(args.out), payload)
    for ticker, entry in sorted(releases.items()):
        print(
            f"  {ticker:6s} {entry.get('status')} published={entry.get('published_at')} "
            f"eps={entry.get('eps_current')} rev={entry.get('revenue_current')}"
        )
    print(f"written {args.out} sha256={digest}")
    return 0


# ---------------------------------------------------------------------------
# entitlement receipt + reaction-window capture
# ---------------------------------------------------------------------------


def cmd_entitlement_receipt(args: argparse.Namespace) -> int:
    fetcher = _fetcher(args)
    data_host = args.data_host.rstrip("/")
    now = datetime.now(UTC)
    probe_url = (
        f"{data_host}/v2/stocks/SPY/trades?start={_iso(now - timedelta(minutes=6))}"
        f"&end={_iso(now - timedelta(minutes=1))}&limit=100"
    )
    verdict, reason, count = "PASSED", None, None
    try:
        payload = _json(fetcher(probe_url))
        count = len(payload.get("trades") or []) if isinstance(payload, dict) else None
        if count is None:
            verdict, reason = "FAILED", "PROBE_RESPONSE_INVALID"
    except urllib.error.HTTPError as error:
        verdict, reason = "FAILED", f"HTTP_{error.code}_RECENT_SIP_BLOCKED"
    receipt = {
        "schema": "esscher.gate_a_equity_entitlement_receipt",
        "schema_version": 1,
        "probed_at": _iso(now),
        "verdict": verdict,
        "reason": reason,
        "recent_trade_count": count,
        "data_host_sha256": _sha_text(data_host),
        "claims": ["NO_CREDENTIALS", "READ_ONLY_PROBE"],
    }
    digest = _write_json(Path(args.out), receipt)
    print(f"entitlement receipt: {verdict} recent_trades={count} sha256={digest}")
    if verdict != "PASSED":
        print(
            "REFUSAL: the frozen 09:35:15 ET evidence cutoff cannot be met on this data "
            "plan (recent SIP queries are blocked; the IEX-only real-time feed fails the "
            "frozen density gates). See the entitlement blocker issue.",
            file=sys.stderr,
        )
        return 3
    return 0


def cmd_capture_window(args: argparse.Namespace) -> int:
    rehearsal = bool(getattr(args, "rehearsal_historical", False))
    delayed = bool(getattr(args, "delayed_demo", False))
    if rehearsal and delayed:
        raise CaptureLaneRejected(
            "UNSUPPORTED_INPUT", "rehearsal-historical and delayed-demo are exclusive modes"
        )
    receipt = (
        json.loads(Path(args.entitlement_receipt).read_bytes())
        if args.entitlement_receipt
        else None
    )
    if not rehearsal and not delayed and (receipt is None or receipt.get("verdict") != "PASSED"):
        raise CaptureLaneRejected(
            "ENTITLEMENT_UNVERIFIED", "refusing window capture without a PASSED receipt"
        )
    fetcher = _fetcher(args)
    data_host = args.data_host.rstrip("/")
    prefetch = json.loads(Path(args.prefetch).read_bytes())
    session_date = date.fromisoformat(args.session_date)
    symbols = sorted(prefetch["daily_bars"].keys())
    start = datetime.combine(session_date, datetime.min.time(), tzinfo=ET).replace(
        hour=9, minute=30
    )
    end = start + timedelta(minutes=5)
    session_id = f"XNYS-{session_date.isoformat()}"
    captured_at = datetime.now(UTC)
    if delayed:
        # The V3 delayed-demo lane captures the identical signal window as
        # fifteen-minute-old HISTORICAL data (legal on the Basic plan); the
        # frozen V3 evidence cutoff is window end + 16 minutes (09:51:00 ET).
        deadline = end.astimezone(UTC) + DELAYED_EVIDENCE_DEADLINE
        if captured_at < end.astimezone(UTC) + timedelta(minutes=15, seconds=5):
            raise CaptureLaneRejected(
                "DELAYED_CAPTURE_TOO_EARLY",
                "delayed capture must start after the data plan's 15-minute recency "
                "boundary clears the window end",
            )
        if captured_at > deadline:
            raise CaptureLaneRejected(
                "EVIDENCE_CUTOFF_MISSED",
                f"capture {_iso(captured_at)} past the V3 delayed cutoff {_iso(deadline)}",
            )
    elif not rehearsal:
        deadline = end.astimezone(UTC) + timedelta(seconds=15)
        if captured_at > deadline:
            raise CaptureLaneRejected(
                "EVIDENCE_CUTOFF_MISSED",
                f"capture {_iso(captured_at)} past the frozen cutoff {_iso(deadline)}",
            )
    trades: dict[str, list[dict]] = {}
    quotes: dict[str, list[dict]] = {}
    pages: dict[str, dict[str, int]] = {}
    filtered: dict[str, dict[str, int]] = {}
    consolidation_stats: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        raw_trades, trade_pages = _trades_page(
            fetcher, data_host, symbol, start.astimezone(UTC), end.astimezone(UTC)
        )
        trade_records = [_trade_record(t, symbol, session_id) for t in raw_trades]
        valid_trades = [record for record in trade_records if record is not None]
        consolidated, consolidation = _consolidate_same_microsecond(valid_trades)
        if consolidation["boundary_ambiguous"]:
            raise CaptureLaneRejected(
                "WINDOW_BOUNDARY_PRICE_AMBIGUOUS",
                f"{symbol}: a differing-price same-microsecond merge lands on a window boundary",
            )
        trades[symbol] = consolidated
        raw_quotes, quote_pages = _quotes_page(
            fetcher, data_host, symbol, start.astimezone(UTC), end.astimezone(UTC)
        )
        quote_records = [_quote_record(q, symbol, session_id) for q in raw_quotes]
        quotes[symbol] = [record for record in quote_records if record is not None]
        pages[symbol] = {"quotes": quote_pages, "trades": trade_pages}
        filtered[symbol] = {
            "quotes": len(quote_records) - len(quotes[symbol]),
            "trades": len(trade_records) - len(valid_trades),
        }
        consolidation_stats[symbol] = consolidation
    payload = {
        "schema": "esscher.capture_window",
        "schema_version": 1,
        "session_id": session_id,
        "captured_at": _iso(captured_at),
        "window_start": _iso(start.astimezone(UTC)),
        "window_end": _iso(end.astimezone(UTC)),
        "reaction_trades": trades,
        "reaction_quotes": quotes,
        "pages": pages,
        "filtered_invalid_records": filtered,
        "same_microsecond_consolidation": consolidation_stats,
        "counts": {s: (len(trades[s]), len(quotes[s])) for s in symbols},
        # A historical rehearsal capture is pipeline validation over past data
        # (legal on the Basic plan); it is NOT a live-session capture and is
        # labelled so downstream artifacts can never launder the distinction.
        "rehearsal_historical": rehearsal,
        "claims": (
            ["REHEARSAL_HISTORICAL_NOT_LIVE_SESSION"]
            if rehearsal
            else (
                ["DELAYED_EXECUTION_DEMO", "LIVE_SESSION_CAPTURE"]
                if delayed
                else ["LIVE_SESSION_CAPTURE"]
            )
        ),
    }
    digest = _write_json(Path(args.out), payload)
    print(f"window capture at {_iso(captured_at)}: {payload['counts']} rehearsal={rehearsal}")
    print(f"written {args.out} sha256={digest}")
    return 0


# ---------------------------------------------------------------------------
# serialization into the fixture-shaped evidence/market byte pair
# ---------------------------------------------------------------------------


def cmd_serialize(args: argparse.Namespace) -> int:
    prefetch = json.loads(Path(args.prefetch).read_bytes())
    releases = json.loads(Path(args.release).read_bytes())["releases"]
    window = json.loads(Path(args.window).read_bytes())
    manifest = json.loads(Path(args.manifest).read_bytes())
    rehearsal = bool(window.get("rehearsal_historical"))
    window_claims = [str(c) for c in (window.get("claims") or [])]
    delayed = "DELAYED_EXECUTION_DEMO" in window_claims
    if not rehearsal and not delayed:
        entitlement = json.loads(Path(args.entitlement_receipt).read_bytes())
        if entitlement.get("verdict") != "PASSED":
            raise CaptureLaneRejected(
                "ENTITLEMENT_UNVERIFIED", "serialized captures require a PASSED receipt"
            )
    event_id = args.event_id
    record = next((r for r in manifest["records"] if r["event_id"] == event_id), None)
    if record is None or record["eligibility"] != "ELIGIBLE":
        raise CaptureLaneRejected(
            "EVENT_NOT_ELIGIBLE", f"{event_id} is not an eligible frozen record"
        )
    ticker = str(record["ticker"])
    release = releases.get(ticker) or {}
    if release.get("status") != "CAPTURED":
        raise CaptureLaneRejected("RELEASE_NOT_CAPTURED", f"{ticker}: {release.get('status')}")
    history = prefetch["quarter_history"].get(ticker) or {}
    sector = history.get("sector")
    if sector not in SECTOR_ETFS:
        raise CaptureLaneRejected("SECTOR_MAPPING_UNAVAILABLE", f"{ticker}: {sector}")
    symbols = (ticker, "SPY", SECTOR_ETFS[str(sector)])
    for symbol in symbols:
        if symbol not in prefetch["daily_bars"] or symbol not in window["reaction_trades"]:
            raise CaptureLaneRejected("CAPTURE_INCOMPLETE", f"missing capture for {symbol}")

    eps_points = history.get("eps") or []
    rev_points = history.get("revenue") or []
    if release.get("eps_current") is None or release.get("revenue_current") is None:
        raise CaptureLaneRejected(
            "FEATURE_SOURCE_UNAVAILABLE_RELEASE_PARSE", f"{ticker} current-quarter parse"
        )
    fiscal_period = session_period_label(args.session_date)
    quarter_history = _quarter_history(eps_points, rev_points)
    current_quarter = {
        "eps_diluted": str(release["eps_current"]),
        "fiscal_period": fiscal_period,
        "revenue": str(release["revenue_current"]),
    }
    guidance = release.get("guidance")
    if guidance is None:
        raise CaptureLaneRejected("GUIDANCE_EXTRACTION_UNRESOLVED", f"{ticker}: extraction refused")
    current_guidance = _guidance_statement(guidance.get("current"), fiscal_period)
    prior_guidance = _guidance_statement(guidance.get("prior"), fiscal_period)
    issuer_release = {
        "current_guidance": current_guidance,
        "current_quarter": current_quarter,
        "event_id": event_id,
        "prior_guidance": prior_guidance,
        "provenance": {
            "content_sha256": str(release["content_sha256"]),
            "entitlement": "PUBLIC",
            "limitations": [],
            "published_at": release["published_at"],
            "published_at_precision": release["published_at_precision"],
            "publisher": "ISSUER_INVESTOR_RELATIONS_VIA_SEC_EDGAR",
            "redistribution_status": "REDISTRIBUTABLE",
            "retrieved_at": release["retrieved_at"],
            "source_class": "ISSUER_INVESTOR_RELATIONS",
        },
        "quarter_history": quarter_history,
        "report_fiscal_period": fiscal_period,
        "ticker": ticker,
    }

    cik = str(record["security_id"]).removeprefix("CIK-")
    closes = prefetch["daily_bars"][ticker]
    prior_close = closes[-1]["close"] if closes else None
    if prior_close is None:
        raise CaptureLaneRejected("PRIOR_CLOSE_MISSING", ticker)
    security_master = {
        "active_at_freeze": True,
        "asof": str(manifest["frozen_at"]),
        "issuer": str(record["issuer"]),
        "listed_option_exists": True,
        "primary_exchange_mic": "XNYS",
        "prior_regular_close": str(prior_close),
        "provenance": {
            "content_sha256": _sha_text(f"{ticker}|{cik}|{prior_close}|{manifest['frozen_at']}"),
            "entitlement": "ENTITLED",
            "limitations": ["LICENSED_REFERENCE_DATA"],
            "published_at": None,
            "published_at_precision": "DATE",
            "publisher": "ALPACA_REFERENCE",
            "redistribution_status": "NON_REDISTRIBUTABLE",
            "retrieved_at": str(prefetch["retrieved_at"]),
            "source_class": "POINT_IN_TIME_SECURITY_MASTER",
        },
        "sector": str(sector),
        "security_id": str(record["security_id"]),
        "security_type": "US_COMMON_STOCK",
        "ticker": ticker,
    }

    trade_pages = sum(int(p.get("trades", 1)) for p in (window.get("pages") or {}).values())
    quote_pages = sum(int(p.get("quotes", 1)) for p in (window.get("pages") or {}).values())
    blob = {
        "candidate_manifest": manifest,
        "capture_at": str(window["captured_at"]),
        "claim_labels": (
            ["DELAYED_EXECUTION_DEMO", "NO_BROKER_EXECUTION", "NOT_THE_VALIDATED_LANE"]
            if delayed
            else ["NO_BROKER_EXECUTION"]
        ),
        "corporate_actions": [],
        "daily_bars": {s: prefetch["daily_bars"][s] for s in symbols},
        "data_class": "POINT_IN_TIME_LIVE_CAPTURE",
        "event_id": event_id,
        "issuer_release": issuer_release,
        "market_entitlement": "ENTITLED",
        "market_publisher": str(prefetch["market_publisher"]),
        "market_redistribution": str(prefetch["market_redistribution"]),
        "prior_window_trades": {
            sid: [t for t in session_trades if t["symbol"] == ticker]
            for sid, session_trades in prefetch["prior_window_trades"].items()
        },
        "reaction_quotes": {s: window["reaction_quotes"][s] for s in symbols},
        "reaction_trades": {s: window["reaction_trades"][s] for s in symbols},
        "retrieval_pages": {
            "market-quotes": [quote_pages, quote_pages],
            "market-trades": [trade_pages, trade_pages],
        },
        "schema": LIVE_INPUTS_SCHEMA,
        "schema_version": 1,
        "security_master": security_master,
        "sessions": prefetch["sessions"],
    }
    market = {key: blob[key] for key in MARKET_KEYS}
    evidence = {key: value for key, value in blob.items() if key not in MARKET_KEYS}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    market_raw = canonical_json_bytes(market)
    evidence_raw = canonical_json_bytes(evidence)
    (out_dir / "market_window.json").write_bytes(market_raw + b"\n")
    (out_dir / "evidence_manifest.json").write_bytes(evidence_raw + b"\n")
    identity = {
        "schema": "esscher.capture_identity",
        "schema_version": 1,
        "event_id": event_id,
        "evidence_manifest_sha256": hashlib.sha256(evidence_raw).hexdigest(),
        "market_window_sha256": hashlib.sha256(market_raw).hexdigest(),
        "capture_at": blob["capture_at"],
        "window_id": args.window_id,
        "candidate_id": "EARNINGS_RESIDUAL_CONTINUATION_V2",
        "market_publisher": blob["market_publisher"],
        "market_entitlement": blob["market_entitlement"],
        "market_redistribution": blob["market_redistribution"],
        "claims": (
            ["REHEARSAL_HISTORICAL_NOT_LIVE_SESSION"]
            if rehearsal
            else (window_claims or ["LIVE_SESSION_CAPTURE"])
        ),
    }
    identity_digest = _write_json(out_dir / "capture_identity.json", identity)
    print(
        f"serialized {event_id}: evidence={identity['evidence_manifest_sha256'][:16]}... "
        f"market={identity['market_window_sha256'][:16]}..."
    )
    print(f"capture identity sha256={identity_digest}")
    return 0


def session_period_label(session_date_text: str) -> str:
    day = date.fromisoformat(session_date_text)
    return f"{day.year}{day.month:02d}{day.day:02d}-REPORT"


def _guidance_statement(raw: object, fallback_period: str) -> dict | None:
    """Project the extracted guidance onto the frozen GuidanceStatement shape."""

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CaptureLaneRejected(
            "GUIDANCE_EXTRACTION_UNRESOLVED", "guidance entry is not a mapping"
        )
    if raw.get("withdrawn"):
        return {
            "eps_high": None,
            "eps_low": None,
            "fiscal_period": str(raw.get("fiscal_period") or fallback_period),
            "revenue_high": None,
            "revenue_low": None,
            "withdrawn": True,
        }
    return {
        "eps_high": raw.get("eps_high"),
        "eps_low": raw.get("eps_low"),
        "fiscal_period": str(raw.get("fiscal_period") or fallback_period),
        "revenue_high": raw.get("revenue_high"),
        "revenue_low": raw.get("revenue_low"),
        "withdrawn": False,
    }


def _quarter_history(
    eps_points: Sequence[Mapping[str, object]], rev_points: Sequence[Mapping[str, object]]
) -> list[dict]:
    revenue_by_end = {str(p["end"]): p for p in rev_points}
    history = []
    for point in eps_points[-(MINIMUM_EPS_QUARTERS - 1) :]:
        end = str(point["end"])
        revenue = revenue_by_end.get(end)
        history.append(
            {
                "eps_diluted": str(point["val"]),
                "fiscal_period": f"FY{point.get('fy')}-{point.get('fp')}-{end}",
                "revenue": str(revenue["val"]) if revenue else None,
            }
        )
    return history


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, *, credentials: bool = True) -> None:
        if credentials:
            p.add_argument("--credentials-env", required=True)
        p.add_argument("--data-host", required=True)
        p.add_argument("--trading-host", required=True)

    d = sub.add_parser("discover")
    common(d)
    d.add_argument("--session-date", required=True)
    d.add_argument("--since")
    d.add_argument(
        "--mode",
        choices=("filed", "preannounce", "both"),
        default="both",
        help=(
            "filed: Item 2.02 filings already on EDGAR (AMC + same-morning BMO); "
            "preannounce: issuer pre-announcements enumerable before the ex-ante "
            "universe freeze (Tuesday-morning BMO); both: the union"
        ),
    )
    d.add_argument("--out", required=True)

    s = sub.add_parser("screen")
    common(s)
    s.add_argument("--discovery", required=True)
    s.add_argument("--out", required=True)

    f = sub.add_parser("freeze-manifest")
    f.add_argument("--discovery", required=True)
    f.add_argument("--screening", required=True)
    f.add_argument("--frozen-at")
    f.add_argument(
        "--policy-version",
        type=int,
        choices=(1, 3),
        default=1,
        help="3 = the owner-approved delayed-capture demo generation (#68/#101)",
    )
    f.add_argument("--out", required=True)

    p = sub.add_parser("prefetch")
    common(p)
    p.add_argument("--manifest", required=True)
    p.add_argument("--session-date", required=True)
    p.add_argument("--out", required=True)

    r = sub.add_parser("capture-release")
    r.add_argument("--credentials-env", required=True)
    r.add_argument("--data-host", required=True)
    r.add_argument("--trading-host", required=True)
    r.add_argument("--manifest", required=True)
    r.add_argument("--session-date", required=True)
    r.add_argument("--out", required=True)

    e = sub.add_parser("entitlement-receipt")
    common(e)
    e.add_argument("--out", required=True)

    w = sub.add_parser("capture-window")
    common(w)
    w.add_argument("--prefetch", required=True)
    w.add_argument("--entitlement-receipt", required=False)
    w.add_argument("--session-date", required=True)
    w.add_argument(
        "--rehearsal-historical",
        action="store_true",
        help="post-hoc pipeline validation over past data; never a live-session capture",
    )
    w.add_argument(
        "--delayed-demo",
        action="store_true",
        help=(
            "V3 delayed-capture demo lane: capture the identical 09:30-09:35 signal "
            "window at 09:50:05-09:51:00 ET as legal historical Basic-plan data"
        ),
    )
    w.add_argument("--out", required=True)

    z = sub.add_parser("serialize")
    z.add_argument("--prefetch", required=True)
    z.add_argument("--release", required=True)
    z.add_argument("--window", required=True)
    z.add_argument("--manifest", required=True)
    z.add_argument("--entitlement-receipt", required=False)
    z.add_argument("--event-id", required=True)
    z.add_argument("--window-id", default="SCAN_1000_ET")
    z.add_argument("--session-date", required=True)
    z.add_argument("--out", required=True)
    return parser


_COMMANDS = {
    "discover": cmd_discover,
    "screen": cmd_screen,
    "freeze-manifest": cmd_freeze_manifest,
    "prefetch": cmd_prefetch,
    "capture-release": cmd_capture_release,
    "entitlement-receipt": cmd_entitlement_receipt,
    "capture-window": cmd_capture_window,
    "serialize": cmd_serialize,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except CaptureLaneRejected as rejection:
        print(f"FAIL-CLOSED {rejection.reason}: {rejection.detail}", file=sys.stderr)
        return 3
    except urllib.error.HTTPError as error:
        print(f"FAIL-CLOSED HTTP_{error.code}: {error.reason}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
