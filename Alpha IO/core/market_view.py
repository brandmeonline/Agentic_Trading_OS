"""
Market view — sector heatmap, breadth, and the cross-asset strip.

The Tier 1 build from ``docs/MARKET_DATA_PROVIDERS.md``: the three terminal
panels that had no data source, built at zero marginal cost on sources the
system already has or can reach for free.

The trick is that none of these panels needs what it appears to need:

- **Sector heatmap** does not need GICS membership. The eleven SPDR sector ETFs
  *are* the sectors, and they are ordinary equities.
- **Cross-asset strip** does not need an index feed. UUP, VIXY and USO proxy the
  dollar, volatility and crude; the 10-year comes from FRED, which is the actual
  Treasury series rather than a proxy for it.
- **Breadth** does not need hundreds of requests. Alpaca's snapshot endpoint
  takes comma-separated symbols with cursor pagination, so a 500-name universe
  is one or two calls inside the free tier's 200/min.

**Nothing here fabricates.** Every reading carries `available` and, when false,
a reason. A terminal panel showing invented breadth is worse than no panel, and
the whole point of the review that started this work was that the reference
architecture could not tell the two apart.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

#: The eleven SPDR select sector funds. These *are* the heatmap.
SECTOR_ETFS: Tuple[Tuple[str, str], ...] = (
    ("XLK", "Technology"),
    ("XLF", "Financials"),
    ("XLV", "Health Care"),
    ("XLY", "Cons. Discretionary"),
    ("XLP", "Cons. Staples"),
    ("XLE", "Energy"),
    ("XLI", "Industrials"),
    ("XLB", "Materials"),
    ("XLU", "Utilities"),
    ("XLRE", "Real Estate"),
    ("XLC", "Comm. Services"),
)

#: Cross-asset proxies. A proxy is labelled as one; UUP is not DXY.
CROSS_ASSET_PROXIES: Tuple[Tuple[str, str, str], ...] = (
    ("SPY", "S&P 500", "direct"),
    ("QQQ", "Nasdaq 100", "direct"),
    ("IWM", "Russell 2000", "direct"),
    ("UUP", "US Dollar", "proxy for DXY"),
    ("VIXY", "Volatility", "proxy for VIX"),
    ("USO", "Crude Oil", "proxy for WTI"),
    ("GLD", "Gold", "direct"),
)

#: A small, liquid default universe for breadth. Replaceable by the operator;
#: breadth is only as meaningful as the universe it is measured over, and this
#: one is deliberately labelled rather than presented as "the market".
DEFAULT_BREADTH_UNIVERSE: Tuple[str, ...] = tuple(symbol for symbol, _ in SECTOR_ETFS) + (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V",
    "UNH", "XOM", "MA", "COST", "HD", "PG", "JNJ", "ABBV", "WMT", "MRK",
    "KO", "PEP", "BAC", "CVX", "AMD", "CRM", "NFLX", "ADBE", "LIN", "TMO",
)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

#: FRED series worth putting on the strip. Real data, not a proxy.
FRED_SERIES: Tuple[Tuple[str, str, str], ...] = (
    ("DGS10", "US 10Y", "%"),
    ("DGS2", "US 2Y", "%"),
)


# =============================================================================
# Readings
# =============================================================================

@dataclass
class Quote:
    """One symbol's latest price and its move from the prior close."""
    symbol: str
    price: float
    prev_close: Optional[float] = None

    @property
    def change_pct(self) -> Optional[float]:
        if not self.prev_close:
            return None
        return (self.price - self.prev_close) / self.prev_close * 100.0

    def to_dict(self) -> Dict[str, Any]:
        change = self.change_pct
        return {
            "symbol": self.symbol,
            "price": round(self.price, 4),
            "prev_close": round(self.prev_close, 4) if self.prev_close else None,
            "change_pct": round(change, 3) if change is not None else None,
        }


@dataclass
class Tile:
    """A labelled quote for the heatmap or the strip."""
    symbol: str
    label: str
    quote: Optional[Quote] = None
    note: str = ""

    @property
    def change_pct(self) -> Optional[float]:
        return self.quote.change_pct if self.quote else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "label": self.label,
            "note": self.note,
            "quote": self.quote.to_dict() if self.quote else None,
            "change_pct": self.change_pct,
        }


@dataclass
class Breadth:
    """Advance/decline over a named universe."""
    universe_size: int
    advancing: int
    declining: int
    unchanged: int
    covered: int

    @property
    def ratio(self) -> Optional[float]:
        """Advancers per decliner. None when nothing declined."""
        if self.declining == 0:
            return None
        return self.advancing / self.declining

    @property
    def net(self) -> int:
        return self.advancing - self.declining

    @property
    def pct_advancing(self) -> Optional[float]:
        if self.covered == 0:
            return None
        return self.advancing / self.covered * 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "universe_size": self.universe_size,
            "covered": self.covered,
            "advancing": self.advancing,
            "declining": self.declining,
            "unchanged": self.unchanged,
            "net": self.net,
            "ratio": round(self.ratio, 3) if self.ratio is not None else None,
            "pct_advancing": round(self.pct_advancing, 2) if self.pct_advancing is not None else None,
        }


@dataclass
class Panel:
    """Any panel's payload plus whether it can be trusted."""
    name: str
    available: bool
    reason: str = ""
    as_of: Optional[datetime] = None
    tiles: List[Tile] = field(default_factory=list)
    breadth: Optional[Breadth] = None
    staleness: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "reason": self.reason,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "staleness": self.staleness,
            "tiles": [t.to_dict() for t in self.tiles],
            "breadth": self.breadth.to_dict() if self.breadth else None,
        }


# =============================================================================
# Sources
# =============================================================================

class QuoteSource:
    """Anything that can return latest quotes for a batch of symbols."""

    #: How stale this source's data is, as a label the UI can show honestly.
    staleness = "unknown"

    def quotes(self, symbols: Sequence[str]) -> Dict[str, Quote]:
        raise NotImplementedError

    def describe(self) -> str:
        return type(self).__name__


class AlpacaQuoteSource(QuoteSource):
    """Multi-symbol snapshots from Alpaca.

    One request covers the whole batch — ``symbols`` is comma-separated and the
    endpoint paginates — which is what keeps a 500-name breadth universe inside
    the free tier's 200 requests per minute.
    """

    #: On the Basic plan, real-time is IEX-only and SIP is available at a
    #: 15-minute lag. Both are labelled rather than presented as live.
    staleness = "iex-realtime-or-15min-sip"

    def __init__(self, client: Any, batch_size: int = 200) -> None:
        self.client = client
        self.batch_size = max(1, batch_size)
        self.last_error: Optional[str] = None

    def quotes(self, symbols: Sequence[str]) -> Dict[str, Quote]:
        out: Dict[str, Quote] = {}
        self.last_error = None

        for batch in _chunk(symbols, self.batch_size):
            try:
                payload = self.client._request(
                    "GET",
                    "/v2/stocks/snapshots",
                    params={"symbols": ",".join(batch)},
                    base_url="https://data.alpaca.markets",
                )
            except Exception as exc:  # noqa: BLE001 - a dead batch must not kill the panel
                self.last_error = f"{type(exc).__name__}: {exc}"
                continue
            out.update(parse_snapshots(payload))
        return out

    def describe(self) -> str:
        return "alpaca-snapshots"


def parse_snapshots(payload: Any) -> Dict[str, Quote]:
    """Turn an Alpaca snapshot payload into quotes.

    Accepts both the bare ``{SYMBOL: {...}}`` shape and the newer
    ``{"snapshots": {SYMBOL: {...}}}`` wrapper, because which one is returned
    depends on the endpoint version and silently returning nothing for the wrong
    one would look exactly like a quiet market.
    """
    if not isinstance(payload, dict):
        return {}
    body = payload.get("snapshots") if isinstance(payload.get("snapshots"), dict) else payload

    quotes: Dict[str, Quote] = {}
    for symbol, snapshot in body.items():
        if not isinstance(snapshot, dict):
            continue
        price = _first_number(
            (snapshot.get("latestTrade") or {}).get("p"),
            (snapshot.get("dailyBar") or {}).get("c"),
            (snapshot.get("minuteBar") or {}).get("c"),
        )
        if price is None:
            continue
        prev = _first_number(
            (snapshot.get("prevDailyBar") or {}).get("c"),
            (snapshot.get("dailyBar") or {}).get("o"),
        )
        quotes[symbol] = Quote(symbol=symbol, price=price, prev_close=prev)
    return quotes


class FredSource:
    """Treasury and macro series from FRED. Free key, 120 requests/minute.

    For rates this is not a fallback — it is the authoritative series, and
    better than any ETF proxy for the same number.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        opener: Optional[Callable[[str, float], bytes]] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("FRED_API_KEY", "").strip() or None
        self.timeout = timeout
        self._opener = opener or _http_get
        self.last_error: Optional[str] = None

    @property
    def configured(self) -> bool:
        return self.api_key is not None

    def latest(self, series_id: str) -> Optional[float]:
        """Most recent non-missing observation, or None."""
        if not self.configured:
            self.last_error = "FRED_API_KEY is not set"
            return None

        query = urllib.parse.urlencode({
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,
        })
        try:
            raw = self._opener(f"{FRED_BASE}?{query}", self.timeout)
            payload = json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001 - a missing rate is not a crash
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

        for observation in payload.get("observations", []):
            value = observation.get("value")
            # FRED marks missing observations with a literal ".".
            if value in (None, "", "."):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        self.last_error = f"no usable observation for {series_id}"
        return None


# =============================================================================
# The view
# =============================================================================

class MarketView:
    """Builds the three panels, degrading honestly when a source is missing."""

    def __init__(
        self,
        quote_source: Optional[QuoteSource] = None,
        fred: Optional[FredSource] = None,
        breadth_universe: Sequence[str] = DEFAULT_BREADTH_UNIVERSE,
        universe_name: str = "liquid-41",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.quote_source = quote_source
        self.fred = fred
        self.breadth_universe = list(breadth_universe)
        self.universe_name = universe_name
        self._clock = clock

    def _unavailable(self, name: str) -> Panel:
        return Panel(
            name=name,
            available=False,
            reason="No market data source configured (see docs/MARKET_DATA_PROVIDERS.md).",
        )

    def sector_heatmap(self) -> Panel:
        """The eleven sector ETFs, by percentage move."""
        if self.quote_source is None:
            return self._unavailable("sector_heatmap")

        symbols = [symbol for symbol, _ in SECTOR_ETFS]
        quotes = self.quote_source.quotes(symbols)
        tiles = [
            Tile(symbol=symbol, label=label, quote=quotes.get(symbol))
            for symbol, label in SECTOR_ETFS
        ]
        covered = sum(1 for tile in tiles if tile.change_pct is not None)
        tiles.sort(key=lambda t: (t.change_pct is None, -(t.change_pct or 0.0)))

        return Panel(
            name="sector_heatmap",
            available=covered > 0,
            reason="" if covered else "No sector quotes returned.",
            as_of=self._clock(),
            tiles=tiles,
            staleness=self.quote_source.staleness,
        )

    def cross_asset(self) -> Panel:
        """Index, dollar, volatility, crude and gold, plus real rates."""
        tiles: List[Tile] = []
        quotes: Dict[str, Quote] = {}

        if self.quote_source is not None:
            quotes = self.quote_source.quotes([s for s, _, _ in CROSS_ASSET_PROXIES])
            tiles.extend(
                Tile(symbol=symbol, label=label, quote=quotes.get(symbol), note=note)
                for symbol, label, note in CROSS_ASSET_PROXIES
            )

        if self.fred is not None and self.fred.configured:
            for series_id, label, unit in FRED_SERIES:
                value = self.fred.latest(series_id)
                if value is None:
                    continue
                tiles.append(Tile(
                    symbol=series_id,
                    label=label,
                    quote=Quote(symbol=series_id, price=value),
                    note=f"FRED, {unit}, daily",
                ))

        covered = sum(1 for tile in tiles if tile.quote is not None)
        if not tiles:
            return self._unavailable("cross_asset")

        return Panel(
            name="cross_asset",
            available=covered > 0,
            reason="" if covered else "No cross-asset quotes returned.",
            as_of=self._clock(),
            tiles=tiles,
            staleness=self.quote_source.staleness if self.quote_source else "fred-daily",
        )

    def breadth(self, universe: Optional[Sequence[str]] = None) -> Panel:
        """Advance/decline across the configured universe.

        Reports ``covered`` alongside ``universe_size`` so a partial response
        reads as partial rather than as a market where half the names were flat.
        """
        if self.quote_source is None:
            return self._unavailable("breadth")

        symbols = list(universe if universe is not None else self.breadth_universe)
        quotes = self.quote_source.quotes(symbols)

        advancing = declining = unchanged = covered = 0
        for symbol in symbols:
            quote = quotes.get(symbol)
            change = quote.change_pct if quote else None
            if change is None:
                continue
            covered += 1
            if change > 0:
                advancing += 1
            elif change < 0:
                declining += 1
            else:
                unchanged += 1

        reading = Breadth(
            universe_size=len(symbols),
            advancing=advancing,
            declining=declining,
            unchanged=unchanged,
            covered=covered,
        )
        return Panel(
            name="breadth",
            available=covered > 0,
            reason="" if covered else "No quotes returned for the breadth universe.",
            as_of=self._clock(),
            breadth=reading,
            staleness=self.quote_source.staleness,
        )

    def all_panels(self) -> Dict[str, Any]:
        """Everything, in one call, for the terminal."""
        return {
            "sector_heatmap": self.sector_heatmap().to_dict(),
            "breadth": self.breadth().to_dict(),
            "cross_asset": self.cross_asset().to_dict(),
            "universe_name": self.universe_name,
            "source": self.quote_source.describe() if self.quote_source else None,
            "rates_source": "fred" if (self.fred and self.fred.configured) else None,
        }


# =============================================================================
# Wiring
# =============================================================================

def build_market_view(**kwargs) -> MarketView:
    """Assemble a MarketView from the environment.

    Returns a view with no quote source when Alpaca is unconfigured, rather than
    raising — an unconfigured terminal should show "not configured", not a stack
    trace.
    """
    quote_source = None
    try:
        from core.alpaca_connector import AlpacaClient, AlpacaConfig

        key = os.getenv("ALPACA_API_KEY", "").strip()
        secret = os.getenv("ALPACA_API_SECRET", "").strip()
        if key and secret:
            quote_source = AlpacaQuoteSource(
                AlpacaClient(AlpacaConfig(api_key=key, api_secret=secret))
            )
    except Exception:  # noqa: BLE001 - absence of a source is a state, not an error
        quote_source = None

    fred = FredSource()
    return MarketView(quote_source=quote_source, fred=fred if fred.configured else None, **kwargs)


# =============================================================================
# Helpers
# =============================================================================

def _chunk(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start:start + size])


def _first_number(*candidates: Any) -> Optional[float]:
    """First candidate that is a usable positive number."""
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _http_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "AgenticTradingOS/1.0"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()
