"""
Evidence and Conflict Data Models for Signalpost.
Tracks facts, source citations, verification, and conflict detection.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid
from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    OFFICIAL_REGISTRY = "official_registry"
    COMPANY_WEBSITE = "company_website"
    PUBLIC_SEARCH = "public_search"
    NEWS_REPORT = "news_report"
    OTHER = "other"


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    field: str = Field(..., description="The profile field this evidence supports")
    value: Any = Field(..., description="The value extracted from this source")
    source_url: str = Field(..., description="The full URL where evidence was found")
    source_title: Optional[str] = Field(default=None, description="Title of the source document or webpage")
    source_type: SourceType = Field(default=SourceType.PUBLIC_SEARCH, description="Category of source")
    retrieved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 timestamp of retrieval"
    )
    publication_date: Optional[str] = Field(default=None, description="Original publication date if known")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0")
    explanation: str = Field(..., description="Brief snippet, quote, or rationale justifying this fact")
    is_verified: bool = Field(default=True, description="Whether this evidence verified against primary identity")

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"Invalid source_url: {v}. Must begin with http:// or https://")
        return v


class ConflictItem(BaseModel):
    field: str = Field(..., description="Field where contradictory data was found")
    primary_value: Any = Field(..., description="Chosen/authoritative value")
    conflicting_value: Any = Field(..., description="Contradicting value from another source")
    alternatives: list[Any] = Field(default_factory=list, description="All distinct values reported for this field")
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence supporting each competing value")
    primary_source: Optional[str] = Field(default=None, description="Source URL of primary value")
    conflicting_source: Optional[str] = Field(default=None, description="Source URL of conflicting value")
    resolution: str = Field(..., description="Explanation of how the conflict was resolved")
    severity: str = Field(default="warning", description="Severity level: 'info', 'warning', or 'high'")
