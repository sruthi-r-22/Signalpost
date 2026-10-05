"""
Isolated LLM Service for Signalpost.
Provides structured fact extraction using OpenAI, Gemini, Groq, Ollama, or Mock deterministic providers.
Strictly constrains LLM output to supplied evidence without hallucinating sources.
"""
from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)
MAX_TRANSIENT_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.0
MAX_RETRY_WAIT_SECONDS = 30.0


class LLMProviderError(RuntimeError):
    """Provider API response error with its HTTP status for retry classification."""

    def __init__(
        self,
        provider: str,
        status_code: int,
        message: str,
        headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(f"{provider} API error ({status_code}): {message[:200]}")
        self.status_code = status_code
        self.raw_message = message
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}


def _is_retryable_provider_error(error: Exception) -> bool:
    if isinstance(error, LLMProviderError):
        return error.status_code in (408, 429) or 500 <= error.status_code <= 599
    return isinstance(error, (httpx.TimeoutException, httpx.TransportError))


def _parse_retry_after(value: str) -> Optional[float]:
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _parse_token_reset(value: str) -> Optional[float]:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?)?\s*", value, re.IGNORECASE)
    if not match:
        return None
    return max(0.0, float(match.group(1)))


def _retry_delay(error: Exception, attempt: int) -> float:
    delay = RETRY_BACKOFF_SECONDS * (2 ** attempt)
    if isinstance(error, LLMProviderError) and error.status_code == 429:
        delay = (
            _parse_retry_after(error.headers["retry-after"])
            if "retry-after" in error.headers
            else None
        )
        if delay is None and "x-ratelimit-reset-tokens" in error.headers:
            delay = _parse_token_reset(error.headers["x-ratelimit-reset-tokens"])
        if delay is None:
            delay = RETRY_BACKOFF_SECONDS * (2 ** attempt)
    return min(delay, MAX_RETRY_WAIT_SECONDS)


def parse_confidence(val: Any, default: float = 0.85) -> float:
    """Parse and normalize confidence score to a float between 0.0 and 1.0."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return max(0.0, min(1.0, float(val)))
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("high", "very high", "certain", "strong"):
            return 0.90
        elif v in ("medium", "moderate"):
            return 0.75
        elif v in ("low", "weak", "uncertain"):
            return 0.50
        try:
            parsed = float(v)
            return max(0.0, min(1.0, parsed))
        except ValueError:
            return default
    return default


class LLMExtractionResult(BaseModel):
    company_summary: Optional[str] = Field(default=None, description="Concise summary of operations based solely on evidence")
    industry_focus: Optional[str] = Field(default=None, description="Primary industry or business focus")
    key_products_or_services: Optional[List[str]] = Field(default=None, description="Products or services mentioned in text")
    headquarters_city: Optional[str] = Field(default=None, description="City where HQ is located if stated")
    website: Optional[str] = Field(default=None, description="Official website URL if stated in text")
    recent_activity: Optional[str] = Field(default=None, description="Recent operational news or milestones mentioned")
    leadership_mentions: Optional[List[str]] = Field(default=None, description="Names of executives or leaders found in text")
    citations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of fact-to-source citations: {field, value, source_url, quote, confidence}"
    )
    configured_provider: str = Field(default="unknown", exclude=True)
    provider_used: str = Field(default="unknown", exclude=True)
    fallback_used: bool = Field(default=False, exclude=True)
    fallback_reason: Optional[str] = Field(default=None, exclude=True)


def _repair_and_parse_json(text: str) -> dict:
    """
    Parse JSON from model response with deterministic repair for minor formatting issues:
    1. Strips markdown code blocks.
    2. Strips leading/trailing conversational text.
    3. Cleans trailing commas before } or ].
    4. Balances missing closing braces/brackets if lightly truncated.
    """
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
        clean = clean.strip()

    # Try direct parse first
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Extract outermost JSON object if surrounded by text
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            clean = candidate

    # Remove trailing commas before closing braces/brackets
    cleaned = re.sub(r",\s*([\]\}])", r"\1", clean)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # If missing trailing brackets/braces, attempt balanced closure
    open_braces = cleaned.count("{") - cleaned.count("}")
    open_brackets = cleaned.count("[") - cleaned.count("]")
    if open_braces > 0 or open_brackets > 0:
        patch = cleaned + ("]" * max(0, open_brackets)) + ("}" * max(0, open_braces))
        try:
            return json.loads(patch)
        except json.JSONDecodeError:
            pass

    # Re-raise standard JSONDecodeError if unrepairable
    return json.loads(clean)


def format_extraction_result(parsed: Dict[str, Any], documents: List[Dict[str, str]]) -> LLMExtractionResult:
    """
    Sanitizes, validates, and normalizes raw JSON dictionary returned by LLMs.
    Guarantees:
    - Never allows invented source URLs: source_url must match a supplied document URL.
    - Every citation contains: field, value, source_url, quote, confidence.
    """
    valid_urls_map = {d["url"]: d for d in documents if d.get("url")}
    valid_urls_norm = {d["url"].rstrip("/").lower(): d["url"] for d in documents if d.get("url")}

    raw_citations = parsed.get("citations", [])
    clean_citations: List[Dict[str, Any]] = []

    if isinstance(raw_citations, list):
        for cit in raw_citations:
            if not isinstance(cit, dict):
                continue
            raw_url = str(cit.get("source_url", "")).strip()
            matched_url = None
            if raw_url in valid_urls_map:
                matched_url = raw_url
            elif raw_url.rstrip("/").lower() in valid_urls_norm:
                matched_url = valid_urls_norm[raw_url.rstrip("/").lower()]

            if matched_url:
                doc = valid_urls_map[matched_url]
                conf = parse_confidence(cit.get("confidence"), 0.85)
                quote = str(cit.get("quote") or doc.get("snippet", ""))[:300]
                field = str(cit.get("field", "summary")).strip()
                val = cit.get("value")

                clean_citations.append({
                    "field": field,
                    "value": val,
                    "source_url": matched_url,
                    "quote": quote,
                    "confidence": conf
                })

    prods = parsed.get("key_products_or_services")
    if isinstance(prods, str):
        prods = [p.strip() for p in prods.split(",") if p.strip()]
    elif not isinstance(prods, list):
        prods = None

    leaders = parsed.get("leadership_mentions")
    if isinstance(leaders, str):
        leaders = [l.strip() for l in leaders.split(",") if l.strip()]
    elif not isinstance(leaders, list):
        leaders = None

    return LLMExtractionResult(
        company_summary=parsed.get("company_summary"),
        industry_focus=parsed.get("industry_focus"),
        key_products_or_services=prods,
        headquarters_city=parsed.get("headquarters_city"),
        website=parsed.get("website"),
        recent_activity=parsed.get("recent_activity"),
        leadership_mentions=leaders,
        citations=clean_citations
    )


class BaseLLMProvider(ABC):
    @abstractmethod
    async def extract_facts(
        self,
        company_name: str,
        company_number: str,
        documents: List[Dict[str, str]]
    ) -> LLMExtractionResult:
        """Extract structured facts from supplied documents."""
        pass


class MockLLMProvider(BaseLLMProvider):
    """
    Deterministic zero-cost LLM provider.
    Analyzes document text with keyword extraction, regex, and exact matching.
    Guarantees zero hallucinations and zero API spend during testing.
    """

    async def extract_facts(
        self,
        company_name: str,
        company_number: str,
        documents: List[Dict[str, str]]
    ) -> LLMExtractionResult:
        if not documents:
            return LLMExtractionResult()

        citations: List[Dict[str, Any]] = []
        summary = None
        recent_activity = None
        leadership: List[str] = []
        website = None
        industry_focus = None
        headquarters_city = None
        key_products: List[str] = []

        for doc in documents:
            url = doc.get("url", "")
            title = doc.get("title", "")
            snippet = doc.get("snippet", "")
            full_text = f"{title}. {snippet}"

            # Look for website
            if ("www." in full_text or "http" in full_text) and not website:
                url_match = re.search(r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", full_text)
                if url_match:
                    website = url_match.group(0)
                    citations.append({
                        "field": "website",
                        "value": website,
                        "source_url": url,
                        "quote": snippet[:100],
                        "confidence": 0.85
                    })

            # Check for business overview / summary
            if company_name.lower() in full_text.lower() and not summary:
                summary = f"{company_name} is an enterprise identified in public sources with active business operations."
                citations.append({
                    "field": "company_summary",
                    "value": summary,
                    "source_url": url,
                    "quote": snippet[:150],
                    "confidence": 0.85
                })

            # Check for recent news or activity
            if any(k in full_text.lower() for k in ["news", "report", "update", "e24", "market", "commercial"]):
                if not recent_activity:
                    recent_activity = "Recent commercial coverage identified in business records and industry publications."
                    citations.append({
                        "field": "recent_activity",
                        "value": recent_activity,
                        "source_url": url,
                        "quote": snippet[:120],
                        "confidence": 0.80
                    })

        return LLMExtractionResult(
            company_summary=summary,
            industry_focus=industry_focus,
            key_products_or_services=key_products if key_products else None,
            headquarters_city=headquarters_city,
            website=website,
            recent_activity=recent_activity,
            leadership_mentions=leadership if leadership else None,
            citations=citations
        )


# ---------------------------------------------------------------------------
# Source budget and content compression helpers
# ---------------------------------------------------------------------------

# Maximum number of source documents to include in one LLM prompt.
# Chosen to cover all key fields while keeping token cost manageable.
# The pipeline may pass more documents; extras are ranked and pruned here.
_SOURCE_BUDGET = 10

# Per-document content character cap.  A sentence-boundary search within
# the window below is preferred; if none is found the hard cap is used.
# 450 chars ≈ 3–4 sentences, enough for any single factual claim.
_CONTENT_SOFT_WINDOW = 450
_CONTENT_HARD_CAP = 500

# Authoritative / high-priority URL patterns (Norwegian public sources).
_HIGH_PRIORITY_DOMAINS = re.compile(
    r"brreg\.no|data\.brreg\.no|proff\.no|ravninfo\.no"
    r"|finanstilsynet\.no|skatteetaten\.no|lovdata\.no",
    re.IGNORECASE,
)
_NEWS_DOMAINS = re.compile(
    r"e24\.no|dn\.no|aftenposten\.no|nrk\.no|reuters\.com"
    r"|bloomberg\.com|ft\.com|businesswire\.com|prnewswire\.com",
    re.IGNORECASE,
)


def _source_priority_score(doc: Dict[str, Any], company_name: str) -> int:
    """
    Score a document for inclusion priority (higher = more important).
    Does NOT discard any document — only ranks them so the budget
    prefers the most authoritative and diverse sources.
    """
    url = (doc.get("url") or "").lower()
    title = (doc.get("title") or "").lower()
    snippet = (doc.get("snippet") or "").lower()
    name_lower = company_name.lower()

    score = 0

    # Authoritative Norwegian registry sources
    if _HIGH_PRIORITY_DOMAINS.search(url):
        score += 40

    # Official company website / investor-relations / newsroom
    if any(tok in url for tok in ("investor", "ir.", "newsroom", "press", "about")):
        score += 20

    # Named company in title (likely directly about this company)
    if name_lower and any(tok in title for tok in name_lower.split() if len(tok) > 3):
        score += 15

    # Reputable business news sources
    if _NEWS_DOMAINS.search(url):
        score += 10

    # Has a publication date (dated sources are more reliably recent)
    if doc.get("publication_date"):
        score += 8

    # Has non-trivial content
    content_len = len(snippet)
    if content_len > 200:
        score += 5
    if content_len > 400:
        score += 3

    return score


def _compress_content(text: str) -> str:
    """
    Return the most evidence-dense portion of a content string within the
    character budget. Tries to end cleanly at a sentence or word boundary
    close to the budget rather than mid-sentence or mid-word.
    Never makes an LLM call.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) <= _CONTENT_SOFT_WINDOW:
        return text

    # Search for sentence boundaries within the hard cap
    window = text[:_CONTENT_HARD_CAP]
    sentence_matches = list(re.finditer(r"[.!?](?:\s+|$)", window))
    # Pick the latest sentence boundary that preserves a substantial portion of content
    for m in reversed(sentence_matches):
        if m.end() >= 250:
            return text[: m.end()].strip()

    # If no sentence boundary >= 250 chars, break cleanly at a word boundary
    last_space = window.rfind(" ")
    if last_space >= 250:
        return window[:last_space].strip()

    # Fallback to hard cap
    return window.strip()


def _prepare_documents_for_extraction(
    documents: List[Dict[str, Any]],
    company_name: str,
) -> List[Dict[str, Any]]:
    """
    Apply source budget and content compression before sending to the LLM.

    Steps:
    1. Rank documents by priority score (authoritative > news > other).
    2. Select up to _SOURCE_BUDGET documents, preserving domain diversity
       (no more than 2 documents from the same domain, unless budget allows).
    3. Compress each document's content with sentence-boundary-aware truncation.
    4. Return a new list of dicts — original documents are NOT mutated.

    Quality guarantees:
    - All citation URLs remain intact (only content is compressed, not URLs).
    - Authoritative sources (brreg, proff) are always included if present.
    - Source ordering is preserved within rank tier to maintain context.
    """
    if not documents:
        return []

    # Score and sort by descending priority
    scored = sorted(
        enumerate(documents),
        key=lambda item: _source_priority_score(item[1], company_name),
        reverse=True,
    )

    selected_indices: List[int] = []
    domain_counts: Dict[str, int] = {}
    # Allow at most 2 docs per domain; authoritative domains get 3 slots
    domain_limit = 2
    auth_domain_limit = 3

    for original_idx, doc in scored:
        if len(selected_indices) >= _SOURCE_BUDGET:
            break
        url = (doc.get("url") or "").lower()
        try:
            domain = urlparse(url).netloc or url
        except Exception:
            domain = url
        # Strip www. prefix for deduplication
        domain = domain.lstrip("www.")

        is_auth = bool(_HIGH_PRIORITY_DOMAINS.search(url))
        limit = auth_domain_limit if is_auth else domain_limit
        if domain_counts.get(domain, 0) < limit:
            selected_indices.append(original_idx)
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

    # If budget not filled, include remaining documents while still respecting domain limits
    if len(selected_indices) < _SOURCE_BUDGET:
        seen = set(selected_indices)
        for original_idx, doc in scored:
            if len(selected_indices) >= _SOURCE_BUDGET:
                break
            if original_idx not in seen:
                url = (doc.get("url") or "").lower()
                try:
                    domain = urlparse(url).netloc or url
                except Exception:
                    domain = url
                domain = domain.lstrip("www.")
                is_auth = bool(_HIGH_PRIORITY_DOMAINS.search(url))
                limit = auth_domain_limit if is_auth else domain_limit
                if domain_counts.get(domain, 0) < limit:
                    selected_indices.append(original_idx)
                    domain_counts[domain] = domain_counts.get(domain, 0) + 1
                    seen.add(original_idx)

    # Restore original document order within the selected set
    selected_indices_sorted = sorted(selected_indices)
    result = []
    for idx in selected_indices_sorted:
        doc = documents[idx]
        compressed_content = _compress_content(doc.get("snippet") or "")
        result.append({
            "url": doc.get("url"),
            "title": doc.get("title"),
            "snippet": compressed_content,
            "retrieved_at": doc.get("retrieved_at"),
            "publication_date": doc.get("publication_date"),
        })

    return result


class OpenAICompatibleProvider(BaseLLMProvider):

    """
    Provider supporting OpenAI, Groq, OpenRouter, and local Ollama.
    """

    def __init__(self, api_key: str, model: str, base_url: str, temperature: float = 0.0):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature

    async def extract_facts(
        self,
        company_name: str,
        company_number: str,
        documents: List[Dict[str, str]]
    ) -> LLMExtractionResult:
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured.")

        # Apply source budget and content compression before building the prompt.
        # This selects the highest-value documents (up to _SOURCE_BUDGET), preserves
        # domain diversity, and compresses verbose content to sentence boundaries.
        # The original documents list is passed to format_extraction_result for URL
        # validation so that all supplied URLs remain valid citation targets.
        prepared_docs = _prepare_documents_for_extraction(documents, company_name)

        system_prompt = (
            "You are a strict data extraction agent for Norwegian corporate intelligence.\n"
            "CRITICAL RULES:\n"
            "1. ONLY extract information directly supported by the provided Source Documents.\n"
            "2. DO NOT invent, assume, or hallucinate facts or URLs.\n"
            "3. If a field is not explicitly present in the sources, output null.\n"
            "4. Every item in 'citations' MUST cite the exact 'source_url' from which the fact was extracted.\n"
            "5. Use only URLs that appear in the Source Documents below. Do not invent URLs.\n"
            "6. You MUST return ONLY valid JSON matching the schema."
        )

        docs_formatted = "\n\n".join([
            f"--- Source [{i+1}] ---\nURL: {d.get('url')}\nTitle: {d.get('title')}\nContent: {d.get('snippet')}"
            for i, d in enumerate(prepared_docs)
        ])

        user_prompt = (
            f"Company to research: {company_name} (Org Nr: {company_number})\n\n"
            f"SOURCE DOCUMENTS:\n{docs_formatted}\n\n"
            "Extract structured company facts into JSON with keys:\n"
            "- company_summary (string or null)\n"
            "- industry_focus (string or null)\n"
            "- key_products_or_services (list of strings or null)\n"
            "- headquarters_city (string or null)\n"
            "- website (string or null)\n"
            "- recent_activity (string or null)\n"
            "- leadership_mentions (list of strings or null)\n"
            "- citations (list of objects: {field, value, source_url, quote, confidence})\n\n"
            "Keep each citation 'quote' concise (a brief factual excerpt under 25 words). "
            "Do not quote entire paragraphs."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            # Safe completion headroom: 1500 tokens prevents mid-JSON truncation on
            # companies with 8-15 citations.  Groq charges only for actual generated
            # tokens (which stop naturally at ~450-750 tokens with concise quotes).
            "max_tokens": 1500,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)

            # If Groq returns 400 with a JSON validation or generation error (e.g. proxy grammar issue),
            # retry ONCE with prompt-guided JSON without the proxy-level response_format constraint.
            if resp.status_code == 400 and any(
                err in resp.text.lower() for err in ("failed to validate json", "failed to generate json")
            ):
                logger.warning(
                    "[OpenAICompatibleProvider] Groq structured JSON rejected (HTTP 400); "
                    "retrying once with prompt-guided JSON..."
                )
                retry_payload = dict(payload)
                retry_payload.pop("response_format", None)
                retry_payload["messages"] = [
                    {
                        "role": "system",
                        "content": system_prompt + "\nReturn ONLY a raw JSON object. No markdown fences, no conversational text.",
                    },
                    {"role": "user", "content": user_prompt},
                ]
                retry_resp = await client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=retry_payload
                )
                if retry_resp.status_code == 200:
                    resp = retry_resp
                else:
                    raise LLMProviderError("LLM", retry_resp.status_code, retry_resp.text, retry_resp.headers)

            if resp.status_code != 200:
                raise LLMProviderError("LLM", resp.status_code, resp.text, resp.headers)
            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            parsed = _repair_and_parse_json(raw_content)
            return format_extraction_result(parsed, documents)


class GeminiProvider(BaseLLMProvider):
    """
    Provider using Google Gemini REST API directly.
    """

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model

    async def extract_facts(
        self,
        company_name: str,
        company_number: str,
        documents: List[Dict[str, str]]
    ) -> LLMExtractionResult:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY / LLM_API_KEY is not configured.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        docs_formatted = "\n\n".join([
            f"--- Source [{i+1}] ---\nURL: {d.get('url')}\nTitle: {d.get('title')}\nContent: {d.get('snippet')}"
            for i, d in enumerate(documents)
        ])

        prompt = (
            "Extract company facts from the supplied sources for Norwegian company: "
            f"{company_name} (Org Nr: {company_number}).\n\n"
            f"SOURCES:\n{docs_formatted}\n\n"
            "Return JSON adhering strictly to: company_summary, industry_focus, key_products_or_services, "
            "headquarters_city, website, recent_activity, leadership_mentions, and citations."
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.0
            }
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise LLMProviderError("Gemini", resp.status_code, resp.text, resp.headers)
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            clean_content = text.strip()
            if clean_content.startswith("```"):
                clean_content = re.sub(r"^```(?:json)?\s*", "", clean_content)
                clean_content = re.sub(r"\s*```$", "", clean_content)
            parsed = json.loads(clean_content)
            return format_extraction_result(parsed, documents)


class LLMService:
    """
    Configurable LLM Service manager with defensive fallback to deterministic extraction.
    """

    def __init__(self):
        settings = get_settings()
        self.provider_name = settings.LLM_PROVIDER.lower()
        self.api_key = settings.LLM_API_KEY
        self.model = settings.LLM_MODEL
        self.base_url = settings.LLM_BASE_URL
        self.temperature = settings.LLM_TEMPERATURE
        self.provider = self._init_provider()

    def _init_provider(self) -> BaseLLMProvider:
        if self.provider_name in ("openai", "groq", "openrouter", "ollama") and self.api_key:
            return OpenAICompatibleProvider(
                api_key=self.api_key,
                model=self.model,
                base_url=self.base_url,
                temperature=self.temperature
            )
        elif self.provider_name == "gemini" and self.api_key:
            return GeminiProvider(api_key=self.api_key, model=self.model)
        else:
            return MockLLMProvider()

    async def extract_facts(
        self,
        company_name: str,
        company_number: str,
        documents: List[Dict[str, str]]
    ) -> LLMExtractionResult:
        """
        Executes extraction. Tries configured LLM first. If live LLM fails or is
        unconfigured, logs a concise error without exposing API keys and falls back
        gracefully to deterministic MockLLMProvider so the application never crashes.
        """
        using_mock_provider = isinstance(self.provider, MockLLMProvider)
        result_metadata = {
            "configured_provider": self.provider_name,
            "provider_used": "mock" if using_mock_provider else self.provider_name,
            "fallback_used": using_mock_provider and self.provider_name != "mock",
        }
        if result_metadata["fallback_used"]:
            result_metadata["fallback_reason"] = (
                f"Configured provider '{self.provider_name}' is unavailable or not configured."
            )
        last_error: Optional[Exception] = None
        for attempt in range(MAX_TRANSIENT_RETRIES + 1):
            try:
                result = await self.provider.extract_facts(company_name, company_number, documents)
                for field, value in result_metadata.items():
                    setattr(result, field, value)
                return result
            except Exception as error:
                last_error = error
                if attempt < MAX_TRANSIENT_RETRIES and _is_retryable_provider_error(error):
                    delay = _retry_delay(error, attempt)
                    logger.warning(
                        "[LLMService] Provider '%s' transient failure (%s); retrying in %.1fs "
                        "(attempt %s/%s).",
                        self.provider_name,
                        f"{type(error).__name__}: {str(error)[:150]}",
                        delay,
                        attempt + 1,
                        MAX_TRANSIENT_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                break

        err_msg = f"{type(last_error).__name__}: {str(last_error)[:150]}"
        logger.warning(
            f"[LLMService] Provider '{self.provider_name}' failed ({err_msg}). Falling back to MockLLMProvider."
        )
        fallback = MockLLMProvider()
        result = await fallback.extract_facts(company_name, company_number, documents)
        result.configured_provider = self.provider_name
        result.provider_used = "mock"
        result.fallback_used = self.provider_name != "mock"
        result.fallback_reason = err_msg if result.fallback_used else None
        return result
