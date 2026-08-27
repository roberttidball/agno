"""FXMacroData: request shaping and normalization against OpenAPI-shaped payloads (httpx mocked)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest

from agno.tools.finance import FinanceProviderError, FinanceTools, FXMacroData

BASE = "https://api.fxmacrodata.com/v1"


def _response(status_code: int = 200, payload=None, text: str = "") -> httpx.Response:
    request = httpx.Request("GET", f"{BASE}/x")
    if payload is not None:
        return httpx.Response(status_code, json=payload, request=request)
    return httpx.Response(status_code, text=text, request=request)


def _forex_payload(rows, source="Official central-bank reference rates", pagination=None):
    payload = {"base": "EUR", "quote": "USD", "source": source, "data": rows}
    if pagination is not None:
        payload["pagination"] = pagination
    return payload


def _bar(observed, value):
    return {"date": observed, "val": value, "observation_datetime_iso": f"{observed}T00:00:00Z"}


@pytest.fixture
def provider() -> FXMacroData:
    return FXMacroData(api_key="test-key", timeout=5)


@pytest.fixture
def client():
    with patch("agno.tools.finance.providers.fxmacrodata.httpx.Client") as client_cls:
        yield client_cls.return_value.__enter__.return_value


# ---------------------------------------------------------------------------
# Construction / status
# ---------------------------------------------------------------------------


def test_reads_key_from_env_and_reports_status():
    with patch.dict("os.environ", {"FXMACRODATA_API_KEY": "env-key"}):
        p = FXMacroData()
    assert p.api_key == "env-key"
    assert p.status().ok is True


def test_declares_only_the_capabilities_it_serves(provider):
    assert provider.capabilities == {"search_symbols", "get_quote", "get_price_history", "get_news"}
    # Company-level capabilities have no FX counterpart and must stay unsupported.
    for capability in ("get_financials", "get_insider_trades", "get_sec_filings", "get_earnings"):
        assert not provider.supports(capability)


def test_missing_key_is_soft_until_a_call(client):
    import os

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("FXMACRODATA_API_KEY", None)
        p = FXMacroData()
    assert p.status().ok is False
    with pytest.raises(FinanceProviderError, match="FXMACRODATA_API_KEY not configured"):
        p.get_quote("EUR/USD")
    client.get.assert_not_called()


# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["EUR/USD", "eurusd", "EUR-USD", "EUR_USD", " eur/usd "])
def test_accepts_the_common_pair_spellings(provider, client, symbol):
    client.get.return_value = _response(payload=_forex_payload([_bar("2026-08-26", 1.1723)]))
    assert provider.get_quote(symbol).symbol == "EUR/USD"


def test_rejects_unsupported_currency_without_a_request(provider, client):
    with pytest.raises(FinanceProviderError, match="does not publish IDR"):
        provider.get_quote("IDR/USD")
    client.get.assert_not_called()


def test_rejects_a_non_pair_symbol_without_a_request(provider, client):
    with pytest.raises(FinanceProviderError, match="expects a currency pair"):
        provider.get_quote("NVDA")
    with pytest.raises(FinanceProviderError, match="not a currency pair"):
        provider.get_quote("USD/USD")
    client.get.assert_not_called()


def test_search_symbols_filters_and_caps(provider):
    matches = provider.search_symbols("EURUSD", limit=5)
    assert [m.symbol for m in matches] == ["EUR/USD"]
    assert matches[0].type == "currency"
    assert len(provider.search_symbols("", limit=3)) == 3
    # A leg match counts on either side of the pair.
    assert all("JPY" in m.symbol for m in provider.search_symbols("JPY", limit=4))


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


def test_quote_request_and_normalization(provider, client):
    client.get.return_value = _response(
        payload=_forex_payload([_bar("2026-08-26", 1.1723), _bar("2026-08-25", 1.1700)])
    )

    quote = provider.get_quote("EUR/USD")

    url, kwargs = client.get.call_args[0][0], client.get.call_args[1]
    assert url == f"{BASE}/forex/EUR/USD"
    assert kwargs["params"] == {"limit": 2}
    assert kwargs["headers"]["X-API-Key"] == "test-key"

    assert quote.symbol == "EUR/USD"
    assert quote.price == 1.1723
    assert quote.currency == "USD"
    assert quote.previous_close == 1.17
    assert quote.change == pytest.approx(0.0023)
    assert quote.change_percent == pytest.approx(0.1965, rel=1e-3)
    assert quote.as_of == "2026-08-26T00:00:00Z"
    assert quote.exchange == "Official central-bank reference rates"


def test_quote_without_a_previous_row_has_no_change(provider, client):
    client.get.return_value = _response(payload=_forex_payload([_bar("2026-08-26", 1.1723)]))
    quote = provider.get_quote("EUR/USD")
    assert quote.price == 1.1723
    assert quote.previous_close is None and quote.change is None and quote.change_percent is None


def test_empty_data_is_an_error_not_a_null_quote(provider, client):
    client.get.return_value = _response(payload=_forex_payload([]))
    with pytest.raises(FinanceProviderError, match="no recent rate for EUR/USD"):
        provider.get_quote("EUR/USD")


def test_api_key_never_reaches_the_url(provider, client):
    client.get.return_value = _response(payload=_forex_payload([_bar("2026-08-26", 1.1723)]))
    provider.get_quote("EUR/USD")
    url, kwargs = client.get.call_args[0][0], client.get.call_args[1]
    assert "test-key" not in url
    assert "test-key" not in str(kwargs.get("params"))


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------


def test_history_window_and_ascending_close_only_bars(provider, client):
    client.get.return_value = _response(
        payload=_forex_payload(
            # API returns most-recent-first; bars must come back ascending.
            [_bar("2026-08-26", 1.1723), _bar("2026-08-25", 1.1700), _bar("2026-08-24", 1.1688)],
            pagination={"has_more": False},
        )
    )

    history = provider.get_price_history("EUR/USD", period="1mo", interval="1d")

    params = client.get.call_args[1]["params"]
    expected_start = (datetime.now(timezone.utc).date() - timedelta(days=31)).isoformat()
    assert params["start_date"] == expected_start
    assert params["limit"] == 100 and params["page"] == 1

    assert history.symbol == "EUR/USD" and history.currency == "USD"
    assert [b.date for b in history.bars] == ["2026-08-24", "2026-08-25", "2026-08-26"]
    assert [b.close for b in history.bars] == [1.1688, 1.17, 1.1723]
    # A reference rate is one daily fixing, so OHLC and volume stay unset.
    assert all(b.open is None and b.high is None and b.low is None and b.volume is None for b in history.bars)


def test_ytd_window_starts_on_january_first(provider, client):
    client.get.return_value = _response(
        payload=_forex_payload([_bar("2026-08-26", 1.1723)], pagination={"has_more": False})
    )
    provider.get_price_history("EUR/USD", period="ytd")
    assert client.get.call_args[1]["params"]["start_date"] == f"{datetime.now(timezone.utc).year}-01-01"


def test_short_periods_are_trimmed_to_the_last_bars(provider, client):
    client.get.return_value = _response(
        payload=_forex_payload(
            [_bar("2026-08-26", 1.1723), _bar("2026-08-25", 1.1700), _bar("2026-08-24", 1.1688)],
            pagination={"has_more": False},
        )
    )
    history = provider.get_price_history("EUR/USD", period="1d")
    assert [b.date for b in history.bars] == ["2026-08-26"]


def test_history_follows_pagination(provider, client):
    client.get.side_effect = [
        _response(payload=_forex_payload([_bar("2026-08-26", 1.1723)], pagination={"has_more": True})),
        _response(payload=_forex_payload([_bar("2026-08-25", 1.1700)], pagination={"has_more": False})),
    ]
    history = provider.get_price_history("EUR/USD", period="1y")
    assert [b.date for b in history.bars] == ["2026-08-25", "2026-08-26"]
    assert [call[1]["params"]["page"] for call in client.get.call_args_list] == [1, 2]


def test_non_daily_interval_is_rejected(provider, client):
    with pytest.raises(FinanceProviderError, match="daily reference rates"):
        provider.get_price_history("EUR/USD", period="1y", interval="1wk")
    client.get.assert_not_called()


def test_unknown_period_is_rejected(provider, client):
    with pytest.raises(FinanceProviderError, match="period must be one of"):
        provider.get_price_history("EUR/USD", period="7y")
    client.get.assert_not_called()


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def test_news_uses_the_base_currency_of_a_pair(provider, client):
    client.get.return_value = _response(
        payload={
            "currency": "EUR",
            "source": "European Central Bank",
            "data": [
                {
                    "title": "Monetary policy decisions",
                    "url": "https://www.ecb.europa.eu/press/pr/date/2026/html/example.en.html",
                    "date": "2026-08-25",
                    "summary": "The Governing Council decided to keep the three key ECB interest rates unchanged.",
                }
            ],
        }
    )

    items = provider.get_news("EUR/USD", limit=5)

    assert client.get.call_args[0][0] == f"{BASE}/press-releases/EUR"
    assert client.get.call_args[1]["params"] == {"limit": 5}
    assert len(items) == 1
    assert items[0].title == "Monetary policy decisions"
    assert items[0].source == "European Central Bank"
    assert items[0].published_at == "2026-08-25"


def test_news_accepts_a_bare_currency_code(provider, client):
    client.get.return_value = _response(payload={"currency": "USD", "source": "Federal Reserve", "data": []})
    assert provider.get_news("USD") == []
    assert client.get.call_args[0][0] == f"{BASE}/press-releases/USD"


def test_news_limit_is_capped_to_the_api_maximum(provider, client):
    client.get.return_value = _response(payload={"source": "Federal Reserve", "data": []})
    provider.get_news("USD", limit=5000)
    assert client.get.call_args[1]["params"] == {"limit": 100}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_code, hint",
    [(401, "invalid API key"), (404, "no data for this request"), (429, "rate limited")],
)
def test_http_errors_are_mapped_to_agent_safe_messages(provider, client, status_code, hint):
    client.get.return_value = _response(status_code=status_code, payload={"detail": "upstream said no"})
    with pytest.raises(FinanceProviderError) as excinfo:
        provider.get_quote("EUR/USD")
    message = str(excinfo.value)
    assert f"HTTP {status_code}" in message and hint in message and "upstream said no" in message
    assert "test-key" not in message


def test_non_json_body_is_reported_cleanly(provider, client):
    client.get.return_value = _response(text="<html>gateway</html>")
    with pytest.raises(FinanceProviderError, match="response was not JSON"):
        provider.get_quote("EUR/USD")


def test_transport_failure_is_wrapped(provider, client):
    client.get.side_effect = httpx.ConnectError("boom")
    with pytest.raises(FinanceProviderError, match="Request to fxmacrodata.com failed"):
        provider.get_quote("EUR/USD")


# ---------------------------------------------------------------------------
# Toolkit integration
# ---------------------------------------------------------------------------


def test_toolkit_registers_only_the_supported_tools(provider):
    tools = FinanceTools(provider=provider)
    names = {getattr(f, "name", getattr(f, "__name__", "")) for f in tools.functions.values()}
    assert {"get_quote", "get_price_history", "get_news", "search_symbols"} <= names
    assert not names & {"get_financials", "get_insider_trades", "get_sec_filings", "get_earnings"}


def test_resolves_from_the_provider_registry(client):
    client.get.return_value = _response(payload=_forex_payload([_bar("2026-08-26", 1.1723)]))
    with patch.dict("os.environ", {"FXMACRODATA_API_KEY": "test-key"}):
        tools = FinanceTools(provider="fxmacrodata")
    payload = json.loads(tools.get_quote("EUR/USD"))
    assert payload["symbol"] == "EUR/USD"
    assert payload["provider"] == "fxmacrodata"


def test_through_toolkit_error_payload(provider, client):
    client.get.return_value = _response(status_code=404, payload={"detail": "nope"})
    tools = FinanceTools(provider=provider)
    payload = json.loads(tools.get_quote("EUR/USD"))
    assert payload["symbol"] == "EUR/USD" and payload["provider"] == "fxmacrodata"
    assert "HTTP 404" in payload["error"]
