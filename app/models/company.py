"""
Company and Profile Data Models for Signalpost.
Defines structured schemas for company identity, facts, profiles, and history.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, PrivateAttr
from app.models.evidence import ConflictItem, Evidence


class ManagementPerson(BaseModel):
    role: str = Field(..., description="Role in company (e.g. CEO / Daglig leder, Board Chair)")
    name: str = Field(..., description="Full name of person")


class FinancialInfo(BaseModel):
    latest_accounts_year: Optional[str] = Field(default=None, description="Year of latest submitted annual accounts")
    share_capital: Optional[float] = Field(default=None, description="Share capital amount")
    currency: Optional[str] = Field(default="NOK", description="Currency code (NOK)")
    shares_count: Optional[int] = Field(default=None, description="Total number of shares")


class CompanyIdentity(BaseModel):
    company_number: str = Field(..., description="Norwegian 9-digit organization number")
    name: str = Field(..., description="Official registered company name")
    organization_type: Optional[str] = Field(default=None, description="Legal form description (e.g. Aksjeselskap)")
    organization_type_code: Optional[str] = Field(default=None, description="Legal form code (e.g. AS, ASA, ENK)")
    status: str = Field(default="Active", description="Company status (Active, In liquidation, Bankrupt, Dissolved)")
    registered_address: Optional[str] = Field(default=None, description="Registered street address")
    postal_code: Optional[str] = Field(default=None, description="Postal code")
    city: Optional[str] = Field(default=None, description="City / Municipality")
    country: str = Field(default="Norway", description="Country")
    official_website: Optional[str] = Field(default=None, description="Official company website URL")
    founding_date: Optional[str] = Field(default=None, description="Date company was founded (YYYY-MM-DD)")
    registration_date: Optional[str] = Field(default=None, description="Date registered in Enhetsregisteret")


class CompanyProfile(BaseModel):
    _llm_usage: Optional[Dict[str, Any]] = PrivateAttr(default=None)

    company_number: str = Field(..., description="Primary Norwegian organization number")
    identity: CompanyIdentity = Field(..., description="Verified primary identity")
    industry_code: Optional[str] = Field(default=None, description="Official NACE / Næringskode (e.g. 06.100)")
    industry_description: Optional[str] = Field(default=None, description="NACE industry description")
    business_purpose: Optional[str] = Field(default=None, description="Registered statutory purpose or activity")
    employee_count: Optional[int] = Field(default=None, description="Registered number of employees")
    financials: Optional[FinancialInfo] = Field(default=None, description="Financial capital and accounting info")
    management: List[ManagementPerson] = Field(default_factory=list, description="Key executives and board members")
    recent_activity: Optional[str] = Field(default=None, description="Verified recent activity or operational notes")
    company_summary: Optional[str] = Field(default=None, description="LLM-extracted summary of corporate operations")
    industry_focus: Optional[str] = Field(default=None, description="LLM-extracted industry or business focus")
    key_products_or_services: Optional[List[str]] = Field(default=None, description="Products or services extracted by LLM")
    headquarters_city: Optional[str] = Field(default=None, description="Headquarters city extracted from public records")
    website: Optional[str] = Field(default=None, description="Company website URL")
    leadership_mentions: Optional[List[str]] = Field(default=None, description="Key leaders/executives mentioned in public sources")
    citations: List[Dict[str, Any]] = Field(default_factory=list, description="Structured citations supporting extracted facts")
    facts: Dict[str, Any] = Field(default_factory=dict, description="Consolidated dictionary of extracted key facts")
    evidence_list: List[Evidence] = Field(default_factory=list, description="Attributed evidence citations for all facts")
    conflicts: List[ConflictItem] = Field(default_factory=list, description="Resolved or flagged conflicts across sources")
    warnings: List[str] = Field(default_factory=list, description="System or verification warnings")
    overall_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Overall profile confidence score")
    research_run_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="ID of the research run")
    researched_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Timestamp when research was completed"
    )
    last_refreshed_at: Optional[str] = Field(default=None, description="Timestamp of latest refresh run if any")


class ResearchRun(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    company_number: str
    run_type: str = Field(default="initial", description="'initial' or 'refresh'")
    status: str = Field(default="success", description="'success', 'partial', or 'failed'")
    sources_count: int = 0
    evidence_count: int = 0
    conflicts_count: int = 0
    duration_seconds: float = 0.0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error_message: Optional[str] = None


class CompanyHistoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    company_number: str
    run_id: str
    field_name: str
    old_value: Any
    new_value: Any
    change_type: str = Field(..., description="'added', 'modified', or 'verified_unchanged'")
    changed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
