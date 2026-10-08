import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.services.search_service import (
    DuckDuckGoSearchProvider,
    MockSearchProvider,
    SearchProviderError,
    SearchService,
    TavilySearchProvider,
)


class _TimeoutClient:
    instances = []

    def __init__(self, *, timeout):
        self.timeout = timeout
        self.post_calls = 0
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def post(self, url, *, json):
        self.post_calls += 1
        raise httpx.ConnectTimeout("simulated TLS connection timeout")


class _Response:
    def __init__(self, status_code, text="", data=None):
        self.status_code = status_code
        self.text = text
        self._data = data or {}

    def json(self):
        return self._data


class _FallbackClient:
    calls = []
    ddg_response = None
    tavily_status = 432

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "api.tavily.com" in url:
            return _Response(self.tavily_status, "simulated Tavily response")
        return self.ddg_response


def test_tavily_with_api_key_uses_tavily():
    with patch("app.services.search_service.get_settings") as get_settings:
        get_settings.return_value.SEARCH_PROVIDER = "tavily"
        get_settings.return_value.SEARCH_API_KEY = "test-key"

        service = SearchService()

    assert isinstance(service.provider, TavilySearchProvider)


@pytest.mark.parametrize("api_key", ["", "   "])
def test_tavily_without_api_key_uses_duckduckgo_not_mock(api_key):
    with patch("app.services.search_service.get_settings") as get_settings:
        get_settings.return_value.SEARCH_PROVIDER = "tavily"
        get_settings.return_value.SEARCH_API_KEY = api_key

        service = SearchService()

    assert isinstance(service.provider, DuckDuckGoSearchProvider)
    assert not isinstance(service.provider, MockSearchProvider)


def test_explicit_duckduckgo_provider_still_uses_duckduckgo():
    with patch("app.services.search_service.get_settings") as get_settings:
        get_settings.return_value.SEARCH_PROVIDER = "duckduckgo"
        get_settings.return_value.SEARCH_API_KEY = ""

        service = SearchService()

    assert isinstance(service.provider, DuckDuckGoSearchProvider)


def test_tavily_timeout_is_bounded_and_raised_as_search_provider_error():
    _TimeoutClient.instances.clear()
    provider = TavilySearchProvider("test-key")

    with patch("app.services.search_service.httpx.AsyncClient", _TimeoutClient):
        with pytest.raises(SearchProviderError, match="Tavily search timed out") as exc_info:
            asyncio.run(provider.search("test query"))

    timeout = _TimeoutClient.instances[0].timeout
    assert timeout.connect == 5.0
    assert timeout.read == 15.0
    assert timeout.write == 15.0
    assert timeout.pool == 15.0
    assert isinstance(exc_info.value.__cause__, httpx.TimeoutException)


def test_search_service_safely_skips_timed_out_tavily_queries():
    _TimeoutClient.instances.clear()
    service = object.__new__(SearchService)
    service.provider = TavilySearchProvider("test-key")

    with patch("app.services.search_service.httpx.AsyncClient", _TimeoutClient):
        results = asyncio.run(
            service.discover_company_sources("123456789", "Test Company")
        )

    assert results == []
    assert sum(client.post_calls for client in _TimeoutClient.instances) == 6


def test_tavily_http_432_retries_query_with_duckduckgo_without_changing_provider():
    _FallbackClient.calls.clear()
    _FallbackClient.tavily_status = 432
    _FallbackClient.ddg_response = _Response(
        200,
        """
        <div class="result">
          <a class="result__a" href="https://example.com/company">Company result</a>
          <a class="result__snippet">A real search result.</a>
        </div>
        """
    )
    service = object.__new__(SearchService)
    service.provider_name = "tavily"
    service.provider = TavilySearchProvider("test-key")

    with patch("app.services.search_service.httpx.AsyncClient", _FallbackClient):
        results = asyncio.run(service._search_with_fallback("company query", max_results=3))

    assert [result.url for result in results] == ["https://example.com/company"]
    assert results[0].provider == "duckduckgo"
    assert service.provider_name == "tavily"
    assert isinstance(service.provider, TavilySearchProvider)
    assert [call[0] for call in _FallbackClient.calls] == [
        "https://api.tavily.com/search",
        "https://html.duckduckgo.com/html/",
    ]
    assert _FallbackClient.calls[0][1]["json"]["query"] == "company query"
    assert _FallbackClient.calls[1][1]["data"]["q"] == "company query"


@pytest.mark.parametrize(
    "ddg_response",
    [
        _Response(503, "simulated DuckDuckGo failure"),
        _Response(200, "<html><body>No search results</body></html>"),
    ],
)
def test_duckduckgo_failure_or_empty_results_returns_no_fabricated_sources(ddg_response):
    _FallbackClient.calls.clear()
    _FallbackClient.tavily_status = 432
    _FallbackClient.ddg_response = ddg_response
    service = object.__new__(SearchService)
    service.provider_name = "tavily"
    service.provider = TavilySearchProvider("test-key")

    with patch("app.services.search_service.httpx.AsyncClient", _FallbackClient):
        results = asyncio.run(service._search_with_fallback("company query", max_results=3))

    assert results == []
    assert len(_FallbackClient.calls) == 2


def test_other_tavily_http_errors_do_not_fall_back_to_duckduckgo():
    _FallbackClient.calls.clear()
    _FallbackClient.tavily_status = 403
    service = object.__new__(SearchService)
    service.provider_name = "tavily"
    service.provider = TavilySearchProvider("test-key")

    with patch("app.services.search_service.httpx.AsyncClient", _FallbackClient):
        with pytest.raises(SearchProviderError, match="code 403") as exc_info:
            asyncio.run(service._search_with_fallback("company query", max_results=3))

    assert exc_info.value.status_code == 403
    assert len(_FallbackClient.calls) == 1
