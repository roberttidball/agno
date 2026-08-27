"""
FXMacroData — FX reference-rate and central-bank data provider (REST API).

Daily FX reference rates published by central banks and the BIS, together with
the press releases behind them. Rates are end-of-day reference fixings rather
than venue prices, so they suit reporting, bookkeeping and macro research
rather than execution. Needs `FXMACRODATA_API_KEY`
(https://fxmacrodata.com/subscribe). No extra dependency: uses `httpx`.

Symbols are currency pairs, written `EUR/USD`, `EURUSD` or `EUR-USD`, across
the eighteen currencies the service publishes: AUD, BRL, CAD, CHF, CNH, CNY,
DKK, EUR, GBP, ILS, JPY, NGN, NOK, NZD, PEN, SEK, THB, USD. `get_news` also
accepts a bare currency code (`USD`) and otherwise reads the base currency of
the pair.

Served: `search_symbols`, `get_quote`, `get_price_history`, `get_news`. The
company-level capabilities (profiles, metrics, financials, insider trades,
earnings, SEC filings) have no counterpart in an FX dataset, so `FinanceTools`
simply does not register those tools when this provider is selected.

Endpoints follow the OpenAPI spec published at
https://api.fxmacrodata.com/openapi.json (fetched 2026-08-27).
"""

from datetime import date, datetime, timedelta, timezone
from os import getenv
from typing import Any, Dict, List, Optional, Tuple

import httpx

from agno.tools.finance.base import (
    GET_NEWS,
    GET_PRICE_HISTORY,
    GET_QUOTE,
    INTERVALS,
    PERIODS,
    SEARCH_SYMBOLS,
    FinanceProvider,
    FinanceProviderError,
    NewsItem,
    PriceBar,
    PriceHistory,
    ProviderStatus,
    Quote,
    SymbolMatch,
)
from agno.utils.log import log_error, log_warning

_ENV_KEY = "FXMACRODATA_API_KEY"
_DEFAULT_BASE_URL = "https://api.fxmacrodata.com/v1"

# The published currency universe. Held locally so an unsupported pair costs no
# HTTP request and produces one clear message instead of a 404.
_CURRENCIES: Tuple[str, ...] = (
    "AUD",
    "BRL",
    "CAD",
    "CHF",
    "CNH",
    "CNY",
    "DKK",
    "EUR",
    "GBP",
    "ILS",
    "JPY",
    "NGN",
    "NOK",
    "NZD",
    "PEN",
    "SEK",
    "THB",
    "USD",
)
_CURRENCY_SET = frozenset(_CURRENCIES)

# Only daily reference rates are published, so `1d` is the sole interval.
_SUPPORTED_INTERVAL = "1d"

_PAGE_LIMIT = 100  # API maximum rows per request
_MAX_PAGES = 30  # hard stop against runaway paging (3000 daily bars ~ 11 years)
_NEWS_MAX = 100  # API maximum per request

# `get_price_history` period -> look-back window in calendar days. Short windows
# are widened so weekends and holidays never yield an empty range; `_TRIM_BARS`
# then keeps only the last N bars for those periods.
_PERIOD_DAYS: Dict[str, Optional[int]] = {
    "1d": 7,
    "5d": 12,
    "1mo": 31,
    "3mo": 92,
    "6mo": 183,
    "1y": 366,
    "2y": 731,
    "5y": 1827,
    "ytd": None,  # computed from Jan 1
    "max": 365 * 40,
}
_TRIM_BARS: Dict[str, int] = {"1d": 1, "5d": 5}
# Fallback window for a period with no entry above.
_DEFAULT_LOOKBACK_DAYS = 31


class FXMacroData(FinanceProvider):
    """FXMacroData data provider.

    Args:
        api_key: API key. Falls back to the `FXMACRODATA_API_KEY` env var.
        base_url: API base URL.
        timeout: Per-request HTTP timeout in seconds.
    """

    id = "fxmacrodata"
    name = "FXMacroData"
    capabilities = frozenset(
        {
            SEARCH_SYMBOLS,
            GET_QUOTE,
            GET_PRICE_HISTORY,
            GET_NEWS,
        }
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: Optional[float] = 30.0,
    ) -> None:
        self.api_key: Optional[str] = api_key or getenv(_ENV_KEY)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else 30.0
        if not self.api_key:
            log_error(f"{_ENV_KEY} not set. Please set the {_ENV_KEY} environment variable.")

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def status(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(ok=False, detail=f"{_ENV_KEY} not set")
        return ProviderStatus(ok=True, detail=self.base_url)

    async def astatus(self) -> ProviderStatus:
        return self.status()

    # ------------------------------------------------------------------
    # Symbols
    # ------------------------------------------------------------------

    @staticmethod
    def _split_pair(symbol: str) -> Tuple[str, str]:
        """`EUR/USD`, `EURUSD`, `EUR-USD` and `EUR_USD` -> `("EUR", "USD")`."""
        raw = (symbol or "").strip().upper()
        base = quote = ""
        for separator in ("/", "-", "_", ":"):
            if separator in raw:
                base, _, quote = raw.partition(separator)
                break
        else:
            if len(raw) != 6:
                raise FinanceProviderError(f"FXMacroData expects a currency pair such as EUR/USD, got {symbol!r}")
            base, quote = raw[:3], raw[3:]

        base, quote = base.strip(), quote.strip()
        unknown = [code for code in (base, quote) if code not in _CURRENCY_SET]
        if unknown:
            raise FinanceProviderError(
                f"FXMacroData does not publish {', '.join(unknown)}; supported currencies are {', '.join(_CURRENCIES)}"
            )
        if base == quote:
            raise FinanceProviderError(f"{base}/{quote} is not a currency pair")
        return base, quote

    @classmethod
    def _currency_of(cls, symbol: str) -> str:
        """Currency code for a news lookup: a bare code, or a pair's base."""
        raw = (symbol or "").strip().upper()
        if raw in _CURRENCY_SET:
            return raw
        return cls._split_pair(raw)[0]

    def search_symbols(self, query: str, limit: int = 5) -> List[SymbolMatch]:
        needle = (query or "").strip().upper().replace("/", "").replace("-", "").replace("_", "")
        capped = max(1, limit)
        matches: List[SymbolMatch] = []
        for base in _CURRENCIES:
            for quote in _CURRENCIES:
                if base == quote:
                    continue
                if needle and needle not in f"{base}{quote}":
                    continue
                matches.append(
                    SymbolMatch(symbol=f"{base}/{quote}", name=f"{base}/{quote} reference rate", type="currency")
                )
                if len(matches) >= capped:
                    return matches
        return matches

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise FinanceProviderError(f"{_ENV_KEY} not configured")
        # Header transport keeps the key out of URLs and access logs.
        return {"X-API-Key": self.api_key, "Accept": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _params(params: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in params.items() if v is not None}

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = self._headers()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(self._url(path), headers=headers, params=self._params(params or {}))
        except httpx.RequestError as e:
            raise FinanceProviderError(f"Request to fxmacrodata.com failed: {e}") from e
        return self._parse(response, path)

    async def _aget(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self._url(path), headers=headers, params=self._params(params or {}))
        except httpx.RequestError as e:
            raise FinanceProviderError(f"Request to fxmacrodata.com failed: {e}") from e
        return self._parse(response, path)

    @staticmethod
    def _parse(response: httpx.Response, path: str) -> Dict[str, Any]:
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = str(body.get("detail") or body.get("message") or body.get("error") or "")
            except ValueError:
                detail = response.text[:200]
            hint = {
                400: "bad request",
                401: "invalid API key",
                403: "plan does not cover this request",
                404: "no data for this request",
                422: "unsupported currency or parameter",
                429: "rate limited",
            }.get(response.status_code, "request failed")
            message = f"fxmacrodata.com {path}: HTTP {response.status_code} ({hint})"
            if detail:
                message = f"{message}: {detail}"
            log_error(message)
            raise FinanceProviderError(message)
        try:
            data = response.json()
        except ValueError as e:
            raise FinanceProviderError(f"fxmacrodata.com {path}: response was not JSON") from e
        if not isinstance(data, dict):
            raise FinanceProviderError(f"fxmacrodata.com {path}: unexpected response shape")
        return data

    # ------------------------------------------------------------------
    # Shared row helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def _as_of(row: Dict[str, Any]) -> Optional[str]:
        value = row.get("observation_datetime_iso") or row.get("date")
        return str(value) if value is not None else None

    @staticmethod
    def _value(row: Dict[str, Any]) -> Optional[float]:
        raw = row.get("val")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Quote
    # ------------------------------------------------------------------

    def _quote_from(self, base: str, quote: str, payload: Dict[str, Any]) -> Quote:
        rows = self._rows(payload)
        if not rows:
            raise FinanceProviderError(f"fxmacrodata.com has no recent rate for {base}/{quote}")

        # Rows are ordered most-recent-first.
        price = self._value(rows[0])
        previous = self._value(rows[1]) if len(rows) > 1 else None
        change = change_percent = None
        if price is not None and previous:
            change = price - previous
            change_percent = change / previous * 100

        source = payload.get("source")
        return Quote(
            symbol=f"{base}/{quote}",
            price=price,
            currency=quote,
            name=f"{base}/{quote} reference rate",
            change=change,
            change_percent=change_percent,
            previous_close=previous,
            exchange=str(source) if source else None,
            as_of=self._as_of(rows[0]),
        )

    def get_quote(self, symbol: str) -> Quote:
        base, quote = self._split_pair(symbol)
        payload = self._get(f"forex/{base}/{quote}", {"limit": 2})
        return self._quote_from(base, quote, payload)

    async def aget_quote(self, symbol: str) -> Quote:
        base, quote = self._split_pair(symbol)
        payload = await self._aget(f"forex/{base}/{quote}", {"limit": 2})
        return self._quote_from(base, quote, payload)

    # ------------------------------------------------------------------
    # Price history
    # ------------------------------------------------------------------

    @staticmethod
    def _start_date(period: str) -> str:
        today = datetime.now(timezone.utc).date()
        if period == "ytd":
            return date(today.year, 1, 1).isoformat()
        days = _PERIOD_DAYS.get(period)
        if days is None:
            days = _DEFAULT_LOOKBACK_DAYS
        return (today - timedelta(days=days)).isoformat()

    def _history_request(self, symbol: str, period: str, interval: str) -> Tuple[str, str, Dict[str, Any]]:
        base, quote = self._split_pair(symbol)
        if period not in PERIODS:
            raise FinanceProviderError(f"period must be one of {', '.join(PERIODS)}")
        if interval not in INTERVALS:
            raise FinanceProviderError(f"interval must be one of {', '.join(INTERVALS)}")
        if interval != _SUPPORTED_INTERVAL:
            raise FinanceProviderError(
                f"FXMacroData publishes daily reference rates; interval must be {_SUPPORTED_INTERVAL}"
            )
        params: Dict[str, Any] = {"start_date": self._start_date(period), "limit": _PAGE_LIMIT}
        return base, quote, params

    def _history_from(
        self, base: str, quote: str, period: str, interval: str, pages: List[Dict[str, Any]]
    ) -> PriceHistory:
        rows: List[Dict[str, Any]] = []
        for payload in pages:
            rows.extend(self._rows(payload))

        bars: List[PriceBar] = []
        for row in rows:
            observed = row.get("date") or row.get("observation_datetime_iso")
            value = self._value(row)
            if observed is None or value is None:
                continue
            # A reference rate is a single daily fixing, so only `close` is populated.
            bars.append(PriceBar(date=str(observed), close=value))

        bars.sort(key=lambda bar: bar.date)
        trim = _TRIM_BARS.get(period)
        if trim:
            bars = bars[-trim:]

        return PriceHistory(symbol=f"{base}/{quote}", period=period, interval=interval, currency=quote, bars=bars)

    @classmethod
    def _has_more(cls, payload: Dict[str, Any], page: int) -> bool:
        if page >= _MAX_PAGES:
            log_warning(f"fxmacrodata.com forex: stopped after {_MAX_PAGES} pages; result is truncated")
            return False
        pagination = payload.get("pagination")
        if isinstance(pagination, dict) and "has_more" in pagination:
            return bool(pagination["has_more"])
        # Without pagination metadata, a full page implies there may be another.
        return len(cls._rows(payload)) >= _PAGE_LIMIT

    def get_price_history(self, symbol: str, period: str = "1mo", interval: str = "1d") -> PriceHistory:
        base, quote, params = self._history_request(symbol, period, interval)
        pages: List[Dict[str, Any]] = []
        page = 1
        while True:
            payload = self._get(f"forex/{base}/{quote}", {**params, "page": page})
            pages.append(payload)
            if not self._has_more(payload, page):
                break
            page += 1
        return self._history_from(base, quote, period, interval, pages)

    async def aget_price_history(self, symbol: str, period: str = "1mo", interval: str = "1d") -> PriceHistory:
        base, quote, params = self._history_request(symbol, period, interval)
        pages: List[Dict[str, Any]] = []
        page = 1
        while True:
            payload = await self._aget(f"forex/{base}/{quote}", {**params, "page": page})
            pages.append(payload)
            if not self._has_more(payload, page):
                break
            page += 1
        return self._history_from(base, quote, period, interval, pages)

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------

    def _news_from(self, payload: Dict[str, Any], limit: int) -> List[NewsItem]:
        source = payload.get("source")
        items: List[NewsItem] = []
        for row in self._rows(payload)[:limit]:
            items.append(
                NewsItem(
                    title=row.get("title"),
                    url=row.get("url"),
                    source=str(source) if source else None,
                    published_at=row.get("date"),
                    summary=row.get("summary"),
                )
            )
        return items

    def get_news(self, symbol: str, limit: int = 10) -> List[NewsItem]:
        currency = self._currency_of(symbol)
        capped = min(max(1, limit), _NEWS_MAX)
        payload = self._get(f"press-releases/{currency}", {"limit": capped})
        return self._news_from(payload, capped)

    async def aget_news(self, symbol: str, limit: int = 10) -> List[NewsItem]:
        currency = self._currency_of(symbol)
        capped = min(max(1, limit), _NEWS_MAX)
        payload = await self._aget(f"press-releases/{currency}", {"limit": capped})
        return self._news_from(payload, capped)
