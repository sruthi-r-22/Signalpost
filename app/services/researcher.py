"""
Researcher Pipeline Orchestrator for Signalpost.
Coordinates Resolution -> Source Discovery -> Information Extraction ->
Verification -> Conflict Resolution -> Profile Assembly -> Persistence.
Supports Refresh with change tracking and historical audit trails.
"""
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from app.database.db import Database, get_db
from app.models.company import (
    CompanyHistoryEntry,
    CompanyIdentity,
    CompanyProfile,
    ResearchRun
)
from app.models.evidence import ConflictItem, Evidence
from app.services.company_resolver import CompanyResolver, validate_norwegian_org_number
from app.services.extractor import InformationExtractor
from app.services.search_service import SearchService
from app.services.verifier import Verifier


def _llm_usage_metadata(extraction_data: Dict[str, Any]) -> Dict[str, Any]:
    result = extraction_data.get("llm_result")
    return {
        "configured_provider": getattr(result, "configured_provider", "unknown"),
        "provider_used": getattr(result, "provider_used", "unknown"),
        "fallback_used": getattr(result, "fallback_used", False),
        "fallback_reason": getattr(result, "fallback_reason", None),
    }


class ResearcherService:
    """
    Main agent pipeline orchestrating all research phases.
    """

    def __init__(
        self,
        resolver: Optional[CompanyResolver] = None,
        search_service: Optional[SearchService] = None,
        extractor: Optional[InformationExtractor] = None,
        verifier: Optional[Verifier] = None,
        db: Optional[Database] = None
    ):
        self.resolver = resolver or CompanyResolver()
        self.search_service = search_service or SearchService()
        self.extractor = extractor or InformationExtractor()
        self.verifier = verifier or Verifier()
        self.db = db or get_db()

    async def research_company(self, company_number: str) -> CompanyProfile:
        """
        Executes complete research pipeline for a company number.
        """
        start_time = time.time()
        org_nr = validate_norwegian_org_number(company_number)
        run_id = str(uuid.uuid4())

        try:
            # 1. Company Resolver: Official authoritative registry lookup
            registry_data = await self.resolver.resolve_company(org_nr)
            identity: CompanyIdentity = registry_data["identity"]

            # 2. Source Discovery: Search for public information
            search_results = await self.search_service.discover_company_sources(
                company_number=org_nr,
                company_name=identity.name,
                website=identity.official_website
            )

            # 3. Fact Extraction: LLM + Registry extraction
            extraction_data = await self.extractor.extract_company_facts(
                registry_data=registry_data,
                search_results=search_results
            )

            raw_facts = extraction_data["facts"]
            raw_evidence = extraction_data["evidence_list"]

            # 4. Identity Verification & Conflict Resolution
            resolved_facts, verified_evidence, conflicts, warnings, confidence = (
                self.verifier.verify_and_resolve(
                    identity=identity,
                    facts=raw_facts,
                    evidence_list=raw_evidence
                )
            )

            duration = round(time.time() - start_time, 2)
            now = datetime.now(timezone.utc).isoformat()

            # 5. Build structured profile
            profile = CompanyProfile(
                company_number=org_nr,
                identity=identity,
                industry_code=registry_data.get("industry_code"),
                industry_description=registry_data.get("industry_description"),
                business_purpose=registry_data.get("business_purpose"),
                employee_count=registry_data.get("employee_count"),
                financials=registry_data.get("financials"),
                management=registry_data.get("management", []),
                recent_activity=resolved_facts.get("recent_activity"),
                company_summary=resolved_facts.get("company_summary"),
                industry_focus=resolved_facts.get("industry_focus"),
                key_products_or_services=resolved_facts.get("key_products_or_services"),
                headquarters_city=resolved_facts.get("headquarters_city"),
                website=resolved_facts.get("website") or identity.official_website,
                leadership_mentions=resolved_facts.get("leadership_mentions"),
                citations=resolved_facts.get("citations", []),
                facts=resolved_facts,
                evidence_list=verified_evidence,
                conflicts=conflicts,
                warnings=warnings,
                overall_confidence=confidence,
                research_run_id=run_id,
                researched_at=now,
                last_refreshed_at=None
            )
            profile._llm_usage = _llm_usage_metadata(extraction_data)

            # 6. Record Research Run
            run = ResearchRun(
                run_id=run_id,
                company_number=org_nr,
                run_type="initial",
                status="success",
                sources_count=len(search_results) + 1,  # +1 for Brreg
                evidence_count=len(verified_evidence),
                conflicts_count=len(conflicts),
                duration_seconds=duration,
                created_at=now
            )

            # Initial history records
            history_entries: List[CompanyHistoryEntry] = []
            for field, val in resolved_facts.items():
                if val is not None:
                    history_entries.append(CompanyHistoryEntry(
                        company_number=org_nr,
                        run_id=run_id,
                        field_name=field,
                        old_value=None,
                        new_value=val,
                        change_type="added",
                        changed_at=now
                    ))

            # 7. Persist to Database
            self.db.save_company_profile(profile, run, history_entries)

            return profile

        except Exception as e:
            duration = round(time.time() - start_time, 2)
            now = datetime.now(timezone.utc).isoformat()
            failed_run = ResearchRun(
                run_id=run_id,
                company_number=org_nr,
                run_type="initial",
                status="failed",
                duration_seconds=duration,
                created_at=now,
                error_message=str(e)
            )
            # Try to log failure in database
            try:
                with self.db._get_connection() as conn:
                    conn.execute("""
                        INSERT INTO research_runs (
                            run_id, company_number, run_type, status,
                            sources_count, evidence_count, conflicts_count,
                            duration_seconds, created_at, error_message
                        ) VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
                    """, (failed_run.run_id, failed_run.company_number, failed_run.run_type,
                          failed_run.status, failed_run.duration_seconds, failed_run.created_at,
                          failed_run.error_message))
            except Exception:
                pass
            raise

    async def refresh_company(self, company_number: str) -> Tuple[CompanyProfile, List[CompanyHistoryEntry]]:
        """
        Re-researches an existing company, identifies changed fields,
        preserves historical evidence, and records audit trail.
        """
        start_time = time.time()
        org_nr = validate_norwegian_org_number(company_number)
        run_id = str(uuid.uuid4())

        # Retrieve prior stored profile
        existing_profile = self.db.get_company_profile(org_nr)

        # Execute fresh research pipeline
        registry_data = await self.resolver.resolve_company(org_nr)
        identity: CompanyIdentity = registry_data["identity"]

        search_results = await self.search_service.discover_company_sources(
            company_number=org_nr,
            company_name=identity.name,
            website=identity.official_website
        )

        extraction_data = await self.extractor.extract_company_facts(
            registry_data=registry_data,
            search_results=search_results
        )

        resolved_facts, verified_evidence, conflicts, warnings, confidence = (
            self.verifier.verify_and_resolve(
                identity=identity,
                facts=extraction_data["facts"],
                evidence_list=extraction_data["evidence_list"]
            )
        )

        now = datetime.now(timezone.utc).isoformat()
        duration = round(time.time() - start_time, 2)

        # Merge evidence lists: preserve prior unique evidence, add new
        combined_evidence = list(verified_evidence)
        if existing_profile:
            seen_keys = {(e.field, e.source_url, str(e.value)) for e in combined_evidence}
            for old_ev in existing_profile.evidence_list:
                key = (old_ev.field, old_ev.source_url, str(old_ev.value))
                if key not in seen_keys:
                    combined_evidence.append(old_ev)
                    seen_keys.add(key)

        # Identify changed fields
        history_entries: List[CompanyHistoryEntry] = []
        old_facts = existing_profile.facts if existing_profile else {}

        all_fields = set(old_facts.keys()).union(resolved_facts.keys())
        for field in sorted(all_fields):
            old_val = old_facts.get(field)
            new_val = resolved_facts.get(field)

            if old_val != new_val:
                change_type = "added" if old_val is None else ("removed" if new_val is None else "modified")
                history_entries.append(CompanyHistoryEntry(
                    company_number=org_nr,
                    run_id=run_id,
                    field_name=field,
                    old_value=old_val,
                    new_value=new_val,
                    change_type=change_type,
                    changed_at=now
                ))
            else:
                if new_val is not None:
                    history_entries.append(CompanyHistoryEntry(
                        company_number=org_nr,
                        run_id=run_id,
                        field_name=field,
                        old_value=old_val,
                        new_value=new_val,
                        change_type="verified_unchanged",
                        changed_at=now
                    ))

        # Build refreshed profile
        refreshed_profile = CompanyProfile(
            company_number=org_nr,
            identity=identity,
            industry_code=registry_data.get("industry_code"),
            industry_description=registry_data.get("industry_description"),
            business_purpose=registry_data.get("business_purpose"),
            employee_count=registry_data.get("employee_count"),
            financials=registry_data.get("financials"),
            management=registry_data.get("management", []),
            recent_activity=resolved_facts.get("recent_activity"),
            company_summary=resolved_facts.get("company_summary"),
            industry_focus=resolved_facts.get("industry_focus"),
            key_products_or_services=resolved_facts.get("key_products_or_services"),
            headquarters_city=resolved_facts.get("headquarters_city"),
            website=resolved_facts.get("website") or identity.official_website,
            leadership_mentions=resolved_facts.get("leadership_mentions"),
            citations=resolved_facts.get("citations", []),
            facts=resolved_facts,
            evidence_list=combined_evidence,
            conflicts=conflicts,
            warnings=warnings,
            overall_confidence=confidence,
            research_run_id=run_id,
            researched_at=existing_profile.researched_at if existing_profile else now,
            last_refreshed_at=now
        )
        refreshed_profile._llm_usage = _llm_usage_metadata(extraction_data)

        run = ResearchRun(
            run_id=run_id,
            company_number=org_nr,
            run_type="refresh",
            status="success",
            sources_count=len(search_results) + 1,
            evidence_count=len(combined_evidence),
            conflicts_count=len(conflicts),
            duration_seconds=duration,
            created_at=now
        )

        self.db.save_company_profile(refreshed_profile, run, history_entries)

        return refreshed_profile, history_entries
