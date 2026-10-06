"""
Search and Retrieval Service for Signalpost.
Provides a pluggable abstraction for web search providers (Mock, DuckDuckGo, Tavily, SerpAPI).
Handles URL deduplication, rate limits, and retrieval timestamping.
"""
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx
from pydantic import BaseModel, Field

from app.config import get_settings


class SearchResult(BaseModel):
    title: str = Field(..., description="Page or snippet title")
    url: str = Field(..., description="Canonical target URL")
    snippet: str = Field(..., description="Retrieved snippet or summary text")
    retrieved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 retrieval timestamp"
    )
    publication_date: Optional[str] = Field(default=None, description="Original publication date if known")
    provider: str = Field(default="search", description="Provider used for retrieval")


def parse_publication_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def is_meaningful_activity(text: str) -> bool:
    activity_terms = (
        r"\bannounc\w*|\bpress release\b|\bnews release\b|\bcontract\w*|\bdeal\w*|"
        r"\bagreement\w*|\bacquir\w*|\binvest\w*|\bappoint\w*|\bleadership change\b|"
        r"\blaunch\w*|\bintroduc\w*|\bunveil\w*|\bquarterly results\b|"
        r"\bfinancial results\b|\bannual results\b|\brevenue\b|\bearnings\b|"
        r"\bdividend\b|\bstrateg\w*|\border\w*|\baward\w*|\bpartnership\w*|"
        r"\bexpan\w*|\bdivest\w*|\bmerger\b"
    )
    return bool(re.search(activity_terms, text, re.IGNORECASE))


class SearchProviderError(Exception):
    """Raised when search provider encounters an error or is unconfigured."""
    pass


class BaseSearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """Execute a search query and return a list of SearchResults."""
        pass


class MockSearchProvider(BaseSearchProvider):
    """
    Deterministic zero-cost search provider for testing and offline development.
    Generates realistic, verifiable public search results based on query context.
    """

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        now = datetime.now(timezone.utc).isoformat()
        results = []

        # Extract org nr if present in query
        org_match = re.search(r"\b\d{9}\b", query)
        org_nr = org_match.group(0) if org_match else "999999999"

        # Extract potential company name
        clean_q = query.replace(org_nr, "").strip()
        company_name = clean_q.split()[0] if clean_q else f"Company {org_nr}"

        results.append(SearchResult(
            title=f"{company_name} - Official Corporate Information & Overview",
            url=f"https://www.{company_name.lower().replace(' ', '')}.com/about",
            snippet=f"{company_name} (Org nr {org_nr}) is a registered Norwegian enterprise operating in its sector with operations and corporate office in Norway.",
            retrieved_at=now,
            provider="mock"
        ))

        results.append(SearchResult(
            title=f"{company_name} in Proff Forvalt Enterprise Registry",
            url=f"https://proff.no/selskap/{company_name.lower().replace(' ', '-')}/{org_nr}",
            snippet=f"Proff.no company overview for {company_name} with organization number {org_nr}. Official accounts, board members, and contact info.",
            retrieved_at=now,
            provider="mock"
        ))

        results.append(SearchResult(
            title=f"E24 / Dagens Næringsliv Business Update: {company_name}",
            url=f"https://e24.no/tema/{company_name.lower().replace(' ', '-')}",
            snippet=f"Latest business coverage and market reports for {company_name}. Ongoing commercial activities and strategic developments.",
            retrieved_at=now,
            provider="mock"
        ))

        return results[:max_results]


class DuckDuckGoSearchProvider(BaseSearchProvider):
    """
    Public web search provider using DuckDuckGo Instant Answer / HTML endpoint.
    Requires no API key.
    """

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        data = {"q": query}

        results: List[SearchResult] = []
        now = datetime.now(timezone.utc).isoformat()

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.post(url, data=data, headers=headers)
                if resp.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    links = soup.find_all("div", class_="result")

                    for item in links[:max_results]:
                        title_el = item.find("a", class_="result__a")
                        snippet_el = item.find("a", class_="result__snippet")
                        if title_el:
                            title = title_el.get_text(strip=True)
                            href = title_el.get("href", "")
                            # DuckDuckGo wraps URLs in /l/?uddg=...
                            if "/l/?uddg=" in href:
                                actual_url = href.split("/l/?uddg=")[1].split("&")[0]
                                import urllib.parse
                                actual_url = urllib.parse.unquote(actual_url)
                            else:
                                actual_url = href

                            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                            if actual_url.startswith("http"):
                                results.append(SearchResult(
                                    title=title,
                                    url=actual_url,
                                    snippet=snippet,
                                    retrieved_at=now,
                                    provider="duckduckgo"
                                ))
        except Exception:
            # Fallback to mock if network blocks DDG scraping
            mock = MockSearchProvider()
            return await mock.search(query, max_results)

        if not results:
            mock = MockSearchProvider()
            return await mock.search(query, max_results)

        return results[:max_results]


class TavilySearchProvider(BaseSearchProvider):
    """
    Search provider using Tavily Search API.
    """

    def __init__(self, api_key: str, timeout: float = 15):
        if not api_key:
            raise SearchProviderError("Tavily API key is missing. Set SEARCH_API_KEY in .env.")
        self.api_key = api_key
        self.timeout = timeout

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": False,
            "max_results": max_results
        }
        timeout = httpx.Timeout(
            self.timeout,
            connect=min(self.timeout, 5.0),
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    raise SearchProviderError(f"Tavily search failed with code {resp.status_code}: {resp.text}")
                data = resp.json()
                now = datetime.now(timezone.utc).isoformat()
                results = []
                for r in data.get("results", []):
                    results.append(SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("content", ""),
                        retrieved_at=now,
                        publication_date=r.get("published_date"),
                        provider="tavily"
                    ))
                return results
        except httpx.TimeoutException as exc:
            raise SearchProviderError(
                f"Tavily search timed out after {self.timeout} seconds."
            ) from exc


class SerpApiSearchProvider(BaseSearchProvider):
    """
    Search provider using SerpAPI.
    """

    def __init__(self, api_key: str, timeout: int = 15):
        if not api_key:
            raise SearchProviderError("SerpAPI key is missing. Set SEARCH_API_KEY in .env.")
        self.api_key = api_key
        self.timeout = timeout

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        url = "https://serpapi.com/search.json"
        params = {
            "api_key": self.api_key,
            "q": query,
            "engine": "google",
            "gl": "no",
            "hl": "no",
            "num": max_results
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                raise SearchProviderError(f"SerpAPI search failed with code {resp.status_code}: {resp.text}")
            data = resp.json()
            now = datetime.now(timezone.utc).isoformat()
            results = []
            for r in data.get("organic_results", []):
                results.append(SearchResult(
                    title=r.get("title", ""),
                    url=r.get("link", ""),
                    snippet=r.get("snippet", ""),
                    retrieved_at=now,
                    provider="serpapi"
                ))
            return results


class SearchService:
    """
    Unified search orchestrator.
    Handles provider selection, multi-query discovery, deduplication, and domain tracking.
    """

    def __init__(self, provider: Optional[str] = None, api_key: Optional[str] = None):
        settings = get_settings()
        self.provider_name = (provider or settings.SEARCH_PROVIDER).lower()
        self.api_key = api_key or settings.SEARCH_API_KEY
        self.provider = self._init_provider()

    def _init_provider(self) -> BaseSearchProvider:
        if self.provider_name == "tavily":
            return TavilySearchProvider(self.api_key)
        elif self.provider_name == "serpapi":
            return SerpApiSearchProvider(self.api_key)
        elif self.provider_name == "duckduckgo":
            return DuckDuckGoSearchProvider()
        else:
            return MockSearchProvider()

    async def discover_company_sources(
        self,
        company_number: str,
        company_name: str,
        website: Optional[str] = None
    ) -> List[SearchResult]:
        """
        Executes targeted search queries:
        1. Query by company number (exact identifier)
        2. Query by official company name + Norway
        3. Query for official domain / business overview
        Deduplicates results by canonical URL.
        """
        queries = [
            f'"{company_number}" Norway',
            f'"{company_name}" Norway org nr',
        ]
        if website:
            parsed = urlparse(website)
            domain = parsed.netloc or parsed.path
            queries.append(f'site:{domain} OR "{company_name}" overview')
        else:
            queries.append(f'"{company_name}" official website')

        all_results: List[SearchResult] = []
        seen_urls = set()

        for q in queries:
            try:
                results = await self.provider.search(q, max_results=3)
                for res in results:
                    canonical = res.url.rstrip("/").lower()
                    if canonical not in seen_urls and res.url.startswith("http"):
                        seen_urls.add(canonical)
                        all_results.append(res)
            except Exception as e:
                # Search failure should not crash pipeline; fallback or log
                continue

        today = datetime.now(timezone.utc).date()
        for window_days in (90, 180, 365):
            cutoff = today - timedelta(days=window_days)
            query = (
                f'"{company_name}" company announcement news financial update contract deal '
                f'acquisition investment leadership launch strategy after:{cutoff.isoformat()}'
            )
            try:
                activity_results = await self.provider.search(query, max_results=5)
            except Exception:
                continue

            found_dated_activity = False
            for result in activity_results:
                canonical = result.url.rstrip("/").lower()
                if canonical not in seen_urls and result.url.startswith("http"):
                    seen_urls.add(canonical)
                    all_results.append(result)

                published = parse_publication_date(result.publication_date)
                age_days = (today - published).days if published else None
                if (
                    published
                    and age_days is not None
                    and 0 <= age_days <= window_days
                    and is_meaningful_activity(f"{result.title} {result.snippet}")
                ):
                    found_dated_activity = True
            if found_dated_activity:
                break

        return all_results
