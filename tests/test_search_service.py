import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.services.search_service import (
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
