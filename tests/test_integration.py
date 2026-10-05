"""
Integration tests for Tavily, Groq, citation verification, and defensive fallback.
Verifies:
1. Tavily results reach LLMService.
2. Groq extraction is called when LLM_PROVIDER=groq.
3. Extracted fields reach the API response.
4. Citations preserve original source URLs and reject invented URLs.
5. An LLM failure does not crash the whole application.
"""
import json
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.company import CompanyIdentity, CompanyProfile
from app.services.extractor import InformationExtractor
from app.services.llm_service import (
    LLMExtractionResult,
    LLMProviderError,
    LLMService,
    MockLLMProvider,
    OpenAICompatibleProvider,
    format_extraction_result,
    _prepare_documents_for_extraction,
)
from app.services.researcher import ResearcherService
from app.services.search_service import SearchResult, TavilySearchProvider

client = TestClient(app)


import asyncio


def test_tavily_results_reach_llm_service():
    """Verify that results discovered by Tavily are formatted and passed into LLMService.extract_facts."""
    async def _test():
        mock_llm = AsyncMock()
        mock_llm.extract_facts.return_value = LLMExtractionResult(
            company_summary="Test company summary.",
            industry_focus="Technology",
            citations=[]
        )

        extractor = InformationExtractor(llm_service=mock_llm)

        registry_data = {
            "identity": CompanyIdentity(
                company_number="923609016",
                name="EQUINOR ASA",
                status="Active"
            ),
            "evidence_list": []
        }

        tavily_results = [
            SearchResult(
                title="Tavily Article 1",
                url="https://example.com/tavily-doc-1",
                snippet="Snippet from Tavily source 1",
                provider="tavily"
            ),
            SearchResult(
                title="Tavily Article 2",
                url="https://example.com/tavily-doc-2",
                snippet="Snippet from Tavily source 2",
                provider="tavily"
            )
        ]

        await extractor.extract_company_facts(registry_data, tavily_results)

        # Verify LLM was called with the exact documents from Tavily
        assert mock_llm.extract_facts.called
        call_args = mock_llm.extract_facts.call_args[1]
        passed_docs = call_args["documents"]
        assert len(passed_docs) == 2
        assert passed_docs[0]["url"] == "https://example.com/tavily-doc-1"
        assert passed_docs[1]["url"] == "https://example.com/tavily-doc-2"
        assert passed_docs[0]["snippet"] == "Snippet from Tavily source 1"

    asyncio.run(_test())


def test_groq_provider_initialized_when_configured():
    """Verify Groq uses OpenAICompatibleProvider pointing to Groq's endpoint."""
    with patch("app.services.llm_service.get_settings") as mock_settings:
        mock_settings.return_value.LLM_PROVIDER = "groq"
        mock_settings.return_value.LLM_API_KEY = "gsk_test_mock_key"
        mock_settings.return_value.LLM_MODEL = "openai/gpt-oss-120b"
        mock_settings.return_value.LLM_BASE_URL = "https://api.groq.com/openai/v1"
        mock_settings.return_value.LLM_TEMPERATURE = 0.0

        service = LLMService()
        assert isinstance(service.provider, OpenAICompatibleProvider)
        assert service.provider.base_url == "https://api.groq.com/openai/v1"
        assert service.provider.model == "openai/gpt-oss-120b"


def test_llm_result_marks_configured_provider_usage():
    async def _test():
        with patch("app.services.llm_service.get_settings") as mock_settings:
            mock_settings.return_value.LLM_PROVIDER = "groq"
            mock_settings.return_value.LLM_API_KEY = "test-key"
            mock_settings.return_value.LLM_MODEL = "test-model"
            mock_settings.return_value.LLM_BASE_URL = "https://api.groq.com/openai/v1"
            mock_settings.return_value.LLM_TEMPERATURE = 0.0
            service = LLMService()

        service.provider = AsyncMock()
        service.provider.extract_facts.return_value = LLMExtractionResult(company_summary="Evidence-based.")

        result = await service.extract_facts("Test company", "923609016", [])

        assert result.configured_provider == "groq"
        assert result.provider_used == "groq"
        assert result.fallback_used is False
        assert result.fallback_reason is None
        assert "provider_used" not in result.model_dump()

    asyncio.run(_test())


def test_llm_provider_retries_429_then_succeeds():
    async def _test():
        service = _configured_groq_llm_service()
        service.provider = AsyncMock()
        service.provider.extract_facts.side_effect = [
            LLMProviderError("LLM", 429, "rate limit"),
            LLMExtractionResult(company_summary="Recovered using Groq."),
        ]

        with patch("app.services.llm_service.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await service.extract_facts("Test company", "923609016", [])

        assert service.provider.extract_facts.await_count == 2
        sleep.assert_awaited_once_with(1.0)
        assert result.provider_used == "groq"
        assert result.fallback_used is False

    asyncio.run(_test())


@pytest.mark.parametrize(
    ("headers", "expected_delay"),
    [
        ({"Retry-After": "7.5"}, 7.5),
        ({"x-ratelimit-reset-tokens": "4.25s"}, 4.25),
        ({"Retry-After": "900"}, 30.0),
    ],
)
def test_429_retry_uses_bounded_server_reset_delay(headers, expected_delay):
    async def _test():
        service = _configured_groq_llm_service()
        service.provider = AsyncMock()
        service.provider.extract_facts.side_effect = [
            LLMProviderError("LLM", 429, "rate limit", headers),
            LLMExtractionResult(company_summary="Recovered using Groq."),
        ]

        with patch("app.services.llm_service.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await service.extract_facts("Test company", "923609016", [])

        sleep.assert_awaited_once_with(expected_delay)

    asyncio.run(_test())


def test_repeated_429_retries_then_falls_back_to_mock():
    async def _test():
        service = _configured_groq_llm_service()
        service.provider = AsyncMock()
        service.provider.extract_facts.side_effect = LLMProviderError("LLM", 429, "rate limit")

        with patch("app.services.llm_service.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await service.extract_facts(
                "TEST COMPANY",
                "923609016",
                [{"url": "https://example.com/test", "title": "Test", "snippet": "TEST COMPANY"}],
            )

        assert service.provider.extract_facts.await_count == 3
        assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0]
        assert isinstance(result, LLMExtractionResult)
        assert result.provider_used == "mock"
        assert result.fallback_used is True
        assert "429" in result.fallback_reason

    asyncio.run(_test())


def test_llm_provider_does_not_retry_authentication_error():
    async def _test():
        service = _configured_groq_llm_service()
        service.provider = AsyncMock()
        service.provider.extract_facts.side_effect = LLMProviderError("LLM", 401, "unauthorized")

        with patch("app.services.llm_service.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await service.extract_facts(
                "TEST COMPANY",
                "923609016",
                [{"url": "https://example.com/test", "title": "Test", "snippet": "TEST COMPANY"}],
            )

        assert service.provider.extract_facts.await_count == 1
        sleep.assert_not_awaited()
        assert result.provider_used == "mock"
        assert result.fallback_used is True
        assert "401" in result.fallback_reason

    asyncio.run(_test())


def _configured_groq_llm_service():
    with patch("app.services.llm_service.get_settings") as mock_settings:
        mock_settings.return_value.LLM_PROVIDER = "groq"
        mock_settings.return_value.LLM_API_KEY = "test-key"
        mock_settings.return_value.LLM_MODEL = "test-model"
        mock_settings.return_value.LLM_BASE_URL = "https://api.groq.com/openai/v1"
        mock_settings.return_value.LLM_TEMPERATURE = 0.0
        return LLMService()


def test_citations_preserve_original_source_urls_and_reject_invented_urls():
    """Verify that source URLs must match supplied documents and invented URLs are dropped."""
    documents = [
        {"url": "https://allowed-source.com/page1", "title": "Doc 1", "snippet": "Text from doc 1"},
        {"url": "https://allowed-source.com/page2", "title": "Doc 2", "snippet": "Text from doc 2"}
    ]

    raw_llm_json = {
        "company_summary": "Summary text",
        "citations": [
            {
                "field": "company_summary",
                "value": "Summary text",
                "source_url": "https://allowed-source.com/page1",
                "quote": "Text from doc 1",
                "confidence": "high"
            },
            {
                "field": "fake_field",
                "value": "Invented fact",
                "source_url": "https://fake-hallucinated-url.com/invented",
                "quote": "Fake quote",
                "confidence": 0.99
            }
        ]
    }

    result = format_extraction_result(raw_llm_json, documents)

    # The invented URL must be rejected
    assert len(result.citations) == 1
    valid_cit = result.citations[0]
    assert valid_cit["source_url"] == "https://allowed-source.com/page1"
    assert valid_cit["field"] == "company_summary"
    assert valid_cit["value"] == "Summary text"
    assert valid_cit["quote"] == "Text from doc 1"
    assert valid_cit["confidence"] == 0.90  # "high" converted to float 0.90


def test_llm_failure_does_not_crash_application():
    """Verify defensive fallback: If the live LLM raises an error, fallback to MockLLMProvider."""
    async def _test():
        with patch("app.services.llm_service.get_settings") as mock_settings:
            mock_settings.return_value.LLM_PROVIDER = "groq"
            mock_settings.return_value.LLM_API_KEY = "test-key"
            mock_settings.return_value.LLM_MODEL = "test-model"
            mock_settings.return_value.LLM_BASE_URL = "https://api.groq.com/openai/v1"
            mock_settings.return_value.LLM_TEMPERATURE = 0.0
            service = LLMService()
        # Force provider to fail
        service.provider = AsyncMock()
        service.provider.extract_facts.side_effect = RuntimeError("Groq API rate limit or network error")

        documents = [
            {
                "url": "https://example.com/test",
                "title": "Test Title",
                "snippet": "TEST COMPANY is described in this test snippet."
            }
        ]

        # Should not raise exception
        res = await service.extract_facts("TEST COMPANY", "923609016", documents)
        assert isinstance(res, LLMExtractionResult)
        # Mock fallback extracted summary without crashing
        assert res.company_summary is not None
        assert res.configured_provider == service.provider_name
        assert res.provider_used == "mock"
        assert res.fallback_used is True
        assert "RuntimeError" in res.fallback_reason

    asyncio.run(_test())


def test_extracted_fields_reach_api_response():
    """Verify that all 8 LLM-generated fields are preserved in the final API response."""
    profile = CompanyProfile(
        company_number="123456789",
        identity=CompanyIdentity(
            company_number="123456789",
            name="UNSEEN COMPANY AS",
            status="Active"
        ),
        company_summary="Mock company summary",
        industry_focus="Mock industry",
        key_products_or_services=["Mock service"],
        headquarters_city="Mock City",
        website="https://company.example",
        recent_activity="Mock recent activity",
        leadership_mentions=["Mock Leader"],
        citations=[],
        facts={
            "company_summary": "Mock company summary",
            "industry_focus": "Mock industry",
            "key_products_or_services": ["Mock service"],
            "headquarters_city": "Mock City",
            "website": "https://company.example",
            "recent_activity": "Mock recent activity",
            "leadership_mentions": ["Mock Leader"],
            "citations": []
        }
    )
    with patch(
        "app.api.routes.ResearcherService.research_company",
        new=AsyncMock(return_value=profile)
    ):
        response = client.post("/api/research", json={"company_number": "123456789"})

    assert response.status_code == 200
    data = response.json()

    # Verify all 8 fields exist in the response
    assert "company_summary" in data
    assert "industry_focus" in data
    assert "key_products_or_services" in data
    assert "headquarters_city" in data
    assert "website" in data
    assert "recent_activity" in data
    assert "leadership_mentions" in data
    assert "citations" in data

    # Verify citations structure if present
    for cit in data["citations"]:
        assert "field" in cit
        assert "value" in cit
        assert "source_url" in cit
        assert "quote" in cit
        assert "confidence" in cit
        assert isinstance(cit["confidence"], (int, float))

    # Also verify they exist inside facts dict
    facts = data["facts"]
    assert "company_summary" in facts
    assert "industry_focus" in facts
    assert "key_products_or_services" in facts
    assert "headquarters_city" in facts
    assert "website" in facts
    assert "recent_activity" in facts
    assert "leadership_mentions" in facts
    assert "citations" in facts


def test_source_budgeting_preserves_authoritative_sources():
    """Verify that authoritative registry sources are prioritized and kept within source budget."""
    docs = [
        {"url": f"https://random-blog-{i}.com/post", "title": f"Blog {i}", "snippet": f"Random snippet {i}"}
        for i in range(15)
    ]
    # Inject authoritative sources
    docs.append({
        "url": "https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
        "title": "Brønnøysundregistrene Enhet",
        "snippet": "EQUINOR ASA official registry entry with status Active",
    })
    docs.append({
        "url": "https://proff.no/selskap/equinor-asa/923609016",
        "title": "Proff Equinor ASA",
        "snippet": "Proff company accounts and key figures for Equinor ASA",
    })

    prepared = _prepare_documents_for_extraction(docs, "EQUINOR ASA")

    # Budget cap is respected (<= 10)
    assert len(prepared) <= 10
    urls = [d["url"] for d in prepared]
    # Authoritative sources must be present
    assert "https://data.brreg.no/enhetsregisteret/api/enheter/923609016" in urls
    assert "https://proff.no/selskap/equinor-asa/923609016" in urls


def test_source_budgeting_preserves_source_diversity():
    """Verify that domain diversity limits prevent a single low-quality domain from crowding out others."""
    # 8 docs from the same domain
    same_domain_docs = [
        {"url": f"https://spam-news.com/article-{i}", "title": f"Spam {i}", "snippet": f"Content {i}"}
        for i in range(8)
    ]
    # Diverse docs from different domains
    other_docs = [
        {"url": "https://e24.no/equinor-deal", "title": "E24 Equinor Contract", "snippet": "Equinor wins contract"},
        {"url": "https://dn.no/equinor-results", "title": "DN Equinor Q3", "snippet": "Equinor reports quarterly earnings"},
        {"url": "https://equinor.com/about", "title": "Equinor Official", "snippet": "Official company overview"},
    ]
    all_docs = same_domain_docs + other_docs

    prepared = _prepare_documents_for_extraction(all_docs, "Equinor")

    spam_urls = [d["url"] for d in prepared if "spam-news.com" in d["url"]]
    # Domain limit of 2 must be respected for non-authoritative domains
    assert len(spam_urls) <= 2
    # Other domains must have been selected
    prepared_urls = [d["url"] for d in prepared]
    assert "https://e24.no/equinor-deal" in prepared_urls
    assert "https://dn.no/equinor-results" in prepared_urls
    assert "https://equinor.com/about" in prepared_urls


def test_citation_urls_remain_available_to_extraction_after_source_budget():
    """Verify that all original document URLs remain valid targets for citation validation."""
    original_docs = [
        {"url": f"https://source-{i}.com/page", "title": f"Doc {i}", "snippet": f"Content {i}"}
        for i in range(12)
    ]
    # Even if an LLM references a source that was in the original set, format_extraction_result validates it
    raw_llm_json = {
        "company_summary": "Extracted summary",
        "citations": [
            {
                "field": "company_summary",
                "value": "Extracted summary",
                "source_url": "https://source-11.com/page",
                "quote": "Content 11",
                "confidence": 0.9,
            }
        ],
    }
    result = format_extraction_result(raw_llm_json, original_docs)
    assert len(result.citations) == 1
    assert result.citations[0]["source_url"] == "https://source-11.com/page"


def test_extraction_prompt_requests_all_existing_fields_and_no_url_duplication():
    """Verify that OpenAICompatibleProvider sends all 8 schema fields, sets max_tokens, and omits URL array in system prompt."""
    async def _test():
        provider = OpenAICompatibleProvider(
            api_key="mock-groq-key",
            model="openai/gpt-oss-120b",
            base_url="https://api.groq.com/openai/v1",
        )
        captured_payload = {}

        class MockResponse:
            status_code = 200
            headers = {}

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"company_summary": "Test", "citations": []}'
                            }
                        }
                    ]
                }

        async def mock_post(client_self, url, *args, **kwargs):
            nonlocal captured_payload
            captured_payload = kwargs.get("json") or (args[1] if len(args) > 1 else {})
            return MockResponse()

        docs = [
            {"url": "https://example.com/source-1", "title": "Doc 1", "snippet": "Snippet 1"},
            {"url": "https://example.com/source-2", "title": "Doc 2", "snippet": "Snippet 2"},
        ]

        with patch("httpx.AsyncClient.post", new=mock_post):
            await provider.extract_facts("Test Company", "123456789", docs)

        system_msg = captured_payload["messages"][0]["content"]
        user_msg = captured_payload["messages"][1]["content"]

        # No duplicated array of URLs in system prompt
        assert "['https://example.com/source-1'" not in system_msg
        assert "https://example.com/source-1" not in system_msg
        # Rule 5 is concise
        assert "Use only URLs that appear in the Source Documents below" in system_msg

        # User prompt requests all 8 schema keys
        required_keys = [
            "company_summary",
            "industry_focus",
            "key_products_or_services",
            "headquarters_city",
            "website",
            "recent_activity",
            "leadership_mentions",
            "citations",
        ]
        for key in required_keys:
            assert key in user_msg

        # max_tokens cap is configured with sufficient headroom (1500)
        assert captured_payload.get("max_tokens") == 1500
        # Prompt contains concise citation guidance
        assert "under 25 words" in user_msg

    asyncio.run(_test())


def test_token_optimization_does_not_make_extra_llm_calls():
    """Verify that source selection and content compression do NOT trigger any secondary LLM calls."""
    async def _test():
        provider = OpenAICompatibleProvider(
            api_key="mock-groq-key",
            model="openai/gpt-oss-120b",
            base_url="https://api.groq.com/openai/v1",
        )
        call_count = 0

        class MockResponse:
            status_code = 200
            headers = {}

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"company_summary": "Test", "citations": []}'
                            }
                        }
                    ]
                }

        async def mock_post(client_self, url, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MockResponse()

        docs = [
            {"url": f"https://example.com/doc-{i}", "title": f"Doc {i}", "snippet": f"Content {i}" * 50}
            for i in range(12)
        ]

        with patch("httpx.AsyncClient.post", new=mock_post):
            await provider.extract_facts("Test Company", "123456789", docs)

        # EXACTLY one LLM call was made
        assert call_count == 1

    asyncio.run(_test())


def test_groq_http_400_json_validate_failed_triggers_retry_and_succeeds():
    """When Groq proxy rejects structured output with HTTP 400 JSON validation error, retry without response_format succeeds."""
    async def _test():
        provider = OpenAICompatibleProvider(
            api_key="mock-groq-key",
            model="openai/gpt-oss-120b",
            base_url="https://api.groq.com/openai/v1",
        )
        call_payloads = []

        class Mock400Response:
            status_code = 400
            text = '{"error":{"message":"Failed to validate JSON. Please adjust your prompt. See \'failed_generation\' for more details.","type":"invalid_request_error","code":"json_validate_failed"}}'
            headers = {}

        class Mock200Response:
            status_code = 200
            headers = {}

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"company_summary": "Recovered summary", "industry_focus": "Tech", "citations": [{"field": "company_summary", "value": "Recovered summary", "source_url": "https://example.com/source-1", "quote": "Recovered", "confidence": 0.9}]}'
                            }
                        }
                    ]
                }

        call_idx = 0
        async def mock_post(client_self, url, *args, **kwargs):
            nonlocal call_idx
            call_payloads.append(kwargs.get("json", {}))
            call_idx += 1
            if call_idx == 1:
                return Mock400Response()
            return Mock200Response()

        docs = [{"url": "https://example.com/source-1", "title": "Doc 1", "snippet": "Snippet 1"}]

        with patch("httpx.AsyncClient.post", new=mock_post):
            result = await provider.extract_facts("Test Company", "123456789", docs)

        # Exactly 2 calls were made: 1 initial (failed) + 1 retry
        assert len(call_payloads) == 2
        # Initial call had response_format
        assert "response_format" in call_payloads[0]
        # Retry omitted response_format to avoid Groq grammar failure
        assert "response_format" not in call_payloads[1]
        # Result was successfully parsed and validated
        assert result.company_summary == "Recovered summary"
        assert result.industry_focus == "Tech"
        assert len(result.citations) == 1
        assert result.citations[0]["source_url"] == "https://example.com/source-1"

    asyncio.run(_test())


def test_groq_http_400_retry_failure_raises_and_falls_back():
    """If the retry after HTTP 400 also fails, LLMProviderError is raised so LLMService can fall back cleanly."""
    async def _test():
        provider = OpenAICompatibleProvider(
            api_key="mock-groq-key",
            model="openai/gpt-oss-120b",
            base_url="https://api.groq.com/openai/v1",
        )

        class Mock400Response:
            status_code = 400
            text = '{"error":{"message":"Failed to validate JSON. Please adjust your prompt."}}'
            headers = {}

        async def mock_post(client_self, url, *args, **kwargs):
            return Mock400Response()

        docs = [{"url": "https://example.com/source-1", "title": "Doc 1", "snippet": "Snippet 1"}]

        with patch("httpx.AsyncClient.post", new=mock_post):
            with pytest.raises(LLMProviderError) as exc_info:
                await provider.extract_facts("Test Company", "123456789", docs)
            assert exc_info.value.status_code == 400

    asyncio.run(_test())


def test_groq_http_400_retry_unparseable_output_raises():
    """If the retry returns garbage that cannot be repaired into JSON, JSONDecodeError is raised."""
    async def _test():
        provider = OpenAICompatibleProvider(
            api_key="mock-groq-key",
            model="openai/gpt-oss-120b",
            base_url="https://api.groq.com/openai/v1",
        )

        class Mock400Response:
            status_code = 400
            text = '{"error":{"message":"Failed to validate JSON."}}'
            headers = {}

        class Mock200GarbageResponse:
            status_code = 200
            headers = {}

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "I am unable to answer in JSON format."
                            }
                        }
                    ]
                }

        call_idx = 0
        async def mock_post(client_self, url, *args, **kwargs):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return Mock400Response()
            return Mock200GarbageResponse()

        docs = [{"url": "https://example.com/source-1", "title": "Doc 1", "snippet": "Snippet 1"}]

        with patch("httpx.AsyncClient.post", new=mock_post):
            with pytest.raises(json.JSONDecodeError):
                await provider.extract_facts("Test Company", "123456789", docs)

    asyncio.run(_test())


