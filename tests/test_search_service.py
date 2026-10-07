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
