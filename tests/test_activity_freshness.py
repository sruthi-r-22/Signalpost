"""
Focused tests for dated recent-activity selection and search-window expansion.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.models.company import CompanyIdentity
from app.services.extractor import InformationExtractor
from app.services.llm_service import LLMExtractionResult
from app.services.search_service import SearchResult, SearchService


def _run_extraction(sources):
    today = datetime.now(timezone.utc).date()
    documents = []
    citations = []
    for index, source in enumerate(sources):
        url = f"https://news.example/item-{index}"
        documents.append(SearchResult(
            title=source["title"],
            url=url,
            snippet=source["snippet"],
            publication_date=source.get("publication_date"),
            retrieved_at=source.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
        ))
        citations.append({
            "field": "recent_activity",
            "value": source["value"],
            "source_url": url,
            "quote": source["snippet"],
            "confidence": source.get("confidence", 0.9),
        })

    llm = AsyncMock()
    llm.extract_facts.return_value = LLMExtractionResult(
        recent_activity="Unsupported generic activity claim",
        citations=citations,
    )
    registry_data = {
        "identity": CompanyIdentity(
            company_number="123456789",
            name="UNSEEN COMPANY AS",
            status="Active",
        ),
        "evidence_list": [],
    }
    extraction = asyncio.run(
        InformationExtractor(llm_service=llm).extract_company_facts(
            registry_data, documents
        )
    )
    return extraction, today


def _announcement(title, value, days_old, snippet=None, **kwargs):
    published = datetime.now(timezone.utc).date() - timedelta(days=days_old)
    return {
        "title": title,
        "snippet": snippet or title,
        "value": value,
        "publication_date": published.isoformat(),
        **kwargs,
    }


def test_30_day_activity_is_preferred_over_300_day_activity():
    extracted, _ = _run_extraction([
        _announcement("Company announces new contract", "New contract signed", 300),
        _announcement("Company announces strategic investment", "Investment announced", 30),
    ])

    assert extracted["facts"]["recent_activity"] == "Investment announced"


def test_300_day_activity_is_preferred_over_500_day_activity():
    extracted, _ = _run_extraction([
        _announcement("Company announces acquisition", "Acquisition announced", 500),
        _announcement("Company announces major contract", "Major contract signed", 300),
    ])

    assert extracted["facts"]["recent_activity"] == "Major contract signed"


def test_recent_announcement_is_selected_instead_of_old_annual_report():
    extracted, today = _run_extraction([
        {
            "title": "Annual Report 2024",
            "snippet": "Published its Annual Report 2024.",
            "value": "Published its Annual Report 2024",
            "publication_date": "2024-04-01",
        },
        _announcement(
            "Company announces a new customer contract",
            "New customer contract announced",
            12,
        ),
    ])

    assert extracted["facts"]["recent_activity"] == "New customer contract announced"
    evidence = [
        item for item in extracted["evidence_list"]
        if item.field == "recent_activity"
        and item.value == "New customer contract announced"
    ]
    assert len(evidence) == 1
    assert evidence[0].source_url == "https://news.example/item-1"
    assert evidence[0].source_title == "Company announces a new customer contract"
    assert evidence[0].explanation == "Company announces a new customer contract"
    assert evidence[0].confidence == 0.9
    assert evidence[0].publication_date == (today - timedelta(days=12)).isoformat()


def test_retrieval_timestamp_is_not_used_as_publication_date():
    extracted, _ = _run_extraction([{
        "title": "Company announces a contract",
        "snippet": "A material contract was announced.",
        "value": "Material contract announced",
        "publication_date": None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }])

    assert extracted["facts"]["recent_activity"] == (
        "No recent activity found in the last 365 days."
    )
    activity_evidence = [
        evidence for evidence in extracted["evidence_list"]
        if evidence.field == "recent_activity"
    ]
    assert len(activity_evidence) == 1
    assert activity_evidence[0].publication_date is None


def test_undated_or_non_activity_source_does_not_create_fabricated_activity():
    extracted, _ = _run_extraction([{
        "title": "Annual Report 2024",
        "snippet": "Published its Annual Report 2024.",
        "value": "Published its Annual Report 2024",
        "publication_date": "2024-04-01",
    }])

    activity = extracted["facts"]["recent_activity"]
    assert activity == "No recent activity found in the last 365 days."
    assert "Unsupported generic activity claim" not in activity


def test_search_expands_activity_window_until_dated_activity_is_found():
    today = datetime.now(timezone.utc).date()

    class ActivityProvider:
        def __init__(self):
            self.queries = []

        async def search(self, query, max_results=5):
            self.queries.append(query)
            if "after:" not in query:
                return []
            if query.endswith((today - timedelta(days=365)).isoformat()):
                return [SearchResult(
                    title="Company announces a contract",
                    url="https://news.example/contract",
                    snippet="A major contract was announced.",
                    publication_date=(today - timedelta(days=300)).isoformat(),
                )]
            return []

    provider = ActivityProvider()
    service = object.__new__(SearchService)
    service.provider = provider

    results = asyncio.run(service.discover_company_sources(
        company_number="123456789",
        company_name="UNSEEN COMPANY AS",
    ))

    activity_queries = [query for query in provider.queries if "after:" in query]
    assert len(activity_queries) == 3
    assert activity_queries[-1].endswith(
        (today - timedelta(days=365)).isoformat()
    )
    assert any(result.url == "https://news.example/contract" for result in results)
