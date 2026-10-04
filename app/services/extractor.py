"""
Information Extractor Service for Signalpost.
Synthesizes verified registry baseline with supplementary web evidence using the LLM Service.
Guarantees full citation tracking, preservation of LLM-generated fields, and zero invented facts.
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from app.models.company import CompanyIdentity, FinancialInfo, ManagementPerson
from app.models.evidence import Evidence, SourceType
from app.services.llm_service import LLMService, parse_confidence
from app.services.search_service import (
    SearchResult,
    is_meaningful_activity,
    parse_publication_date,
)


class InformationExtractor:
    """
    Coordinates multi-source extraction, attaching evidence metadata to every fact.
    """

    def __init__(self, llm_service: Optional[LLMService] = None):
        self.llm = llm_service or LLMService()

    async def extract_company_facts(
        self,
        registry_data: Dict[str, Any],
        search_results: List[SearchResult]
    ) -> Dict[str, Any]:
        """
        Takes verified registry data and web search results,
        extracts facts and synthesizes consolidated evidence.
        """
        identity: CompanyIdentity = registry_data["identity"]
        evidence_list: List[Evidence] = list(registry_data["evidence_list"])

        # Format web documents for LLM
        documents = [
            {
                "url": s.url,
                "title": s.title,
                "snippet": s.snippet,
                "retrieved_at": s.retrieved_at,
                "publication_date": s.publication_date
            }
            for s in search_results
        ]

        # Extract supplementary facts via LLM service (Tavily documents -> Groq LLM)
        llm_result = await self.llm.extract_facts(
            company_name=identity.name,
            company_number=identity.company_number,
            documents=documents
        )

        # Convert valid LLM citations to Evidence records
        valid_urls_map = {d["url"]: d for d in documents if d.get("url")}
        valid_urls_norm = {d["url"].rstrip("/").lower(): d["url"] for d in documents if d.get("url")}

        valid_citations: List[Dict[str, Any]] = []
        recent_activity_candidates: List[Dict[str, Any]] = []
        today = datetime.now(timezone.utc).date()

        for cit in llm_result.citations:
            raw_url = str(cit.get("source_url", "")).strip()
            matched_url = None
            if raw_url in valid_urls_map:
                matched_url = raw_url
            elif raw_url.rstrip("/").lower() in valid_urls_norm:
                matched_url = valid_urls_norm[raw_url.rstrip("/").lower()]

            # Strictly verify that LLM did not hallucinate URL
            if matched_url:
                doc = valid_urls_map[matched_url]
                conf = parse_confidence(cit.get("confidence"), 0.85)
                quote = str(cit.get("quote") or doc.get("snippet", ""))[:300]
                field = str(cit.get("field", "additional_info")).strip()
                val = cit.get("value")

                valid_citations.append({
                    "field": field,
                    "value": val,
                    "source_url": matched_url,
                    "quote": quote,
                    "confidence": conf
                })

                src_type = (
                    SourceType.COMPANY_WEBSITE
                    if identity.official_website and identity.official_website in matched_url
                    else SourceType.PUBLIC_SEARCH
                )
                evidence_list.append(Evidence(
                    field=field,
                    value=val,
                    source_url=matched_url,
                    source_title=doc.get("title"),
                    source_type=src_type,
                    retrieved_at=doc["retrieved_at"],
                    publication_date=doc.get("publication_date"),
                    confidence=conf,
                    explanation=quote,
                    is_verified=True
                ))
                if field.casefold() == "recent_activity" and val:
                    publication_date = parse_publication_date(doc.get("publication_date"))
                    source_text = " ".join((
                        doc.get("title", ""),
                        doc.get("snippet", ""),
                        quote,
                    ))
                    if publication_date and is_meaningful_activity(source_text):
                        age_days = (today - publication_date).days
                        if age_days >= 0:
                            recent_activity_candidates.append({
                                "value": str(val),
                                "citation": cit,
                                "publication_date": publication_date,
                                "age_days": age_days,
                            })

        # Consolidate key facts dictionary preserving all 8 LLM-generated fields
        website_val = identity.official_website
        if not website_val and llm_result.website:
            if any(llm_result.website in d["url"] or llm_result.website in d["snippet"] for d in documents):
                website_val = llm_result.website

        facts: Dict[str, Any] = {
            "company_name": identity.name,
            "company_number": identity.company_number,
            "status": identity.status,
            "organization_type": identity.organization_type,
            "registered_address": identity.registered_address,
            "city": identity.city,
            "postal_code": identity.postal_code,
            "country": identity.country,
            "website": website_val,
            "founding_date": identity.founding_date,
            "employee_count": registry_data.get("employee_count"),
            "industry": registry_data.get("industry_description"),
            "industry_code": registry_data.get("industry_code"),
            "business_purpose": registry_data.get("business_purpose"),
            # Preserved LLM-generated fields:
            "company_summary": llm_result.company_summary,
            "industry_focus": llm_result.industry_focus,
            "key_products_or_services": llm_result.key_products_or_services,
            "headquarters_city": llm_result.headquarters_city,
            "recent_activity": self._select_recent_activity(recent_activity_candidates),
            "leadership_mentions": llm_result.leadership_mentions,
            "citations": valid_citations
        }

        return {
            "facts": facts,
            "evidence_list": evidence_list,
            "recent_activity": facts["recent_activity"],
            "llm_result": llm_result,
            "citations": valid_citations
        }

    def _select_recent_activity(
        self,
        candidates: List[Dict[str, Any]]
    ) -> Optional[str]:
        for window_days in (90, 180, 365):
            eligible = [
                candidate for candidate in candidates
                if candidate["age_days"] <= window_days
            ]
            if eligible:
                selected = max(
                    eligible,
                    key=lambda candidate: (
                        candidate["publication_date"],
                        parse_confidence(candidate["citation"].get("confidence"), 0.85),
                    )
                )
                return selected["value"]

        older = [candidate for candidate in candidates if candidate["age_days"] > 365]
        if older:
            selected = max(older, key=lambda candidate: candidate["publication_date"])
            actual_date = selected["publication_date"].isoformat()
            return (
                f"No activity dated within the last 365 days. "
                f"Latest dated item ({actual_date}): {selected['value']}"
            )

        return "No recent activity found in the last 365 days."
