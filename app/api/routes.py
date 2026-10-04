"""
FastAPI Route Handlers for Signalpost.
Defines endpoints for company research, retrieval, refresh, and historical audit logs.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.database.db import Database, get_db
from app.models.company import CompanyHistoryEntry, CompanyProfile, ResearchRun
from app.services.company_resolver import (
    CompanyNotFoundError,
    CompanyResolverError,
    InvalidCompanyNumberError
)
from app.services.researcher import ResearcherService

router = APIRouter()


class ResearchRequest(BaseModel):
    company_number: str = Field(..., description="Norwegian 9-digit company organization number")


class RefreshResponse(BaseModel):
    profile: CompanyProfile
    changes: List[CompanyHistoryEntry]


class HistoryResponse(BaseModel):
    company_number: str
    runs: List[ResearchRun]
    history: List[CompanyHistoryEntry]


def get_researcher(db: Database = Depends(get_db)) -> ResearcherService:
    return ResearcherService(db=db)


@router.post(
    "/research",
    response_model=CompanyProfile,
    summary="Research a Norwegian company",
    description="Resolves identity, retrieves public sources, extracts facts with evidence, and builds structured profile."
)
async def research_company_endpoint(
    payload: ResearchRequest,
    researcher: ResearcherService = Depends(get_researcher)
) -> CompanyProfile:
    try:
        profile = await researcher.research_company(payload.company_number)
        return profile
    except InvalidCompanyNumberError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except CompanyNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except CompanyResolverError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Research failed: {str(e)}")


@router.get(
    "/company/{company_number}",
    response_model=CompanyProfile,
    summary="Get stored company profile",
    description="Retrieves persisted company profile from the database."
)
async def get_company_endpoint(
    company_number: str,
    db: Database = Depends(get_db)
) -> CompanyProfile:
    clean_nr = "".join(ch for ch in company_number if ch.isdigit())
    profile = db.get_company_profile(clean_nr)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company with number '{company_number}' has not been researched yet. Use POST /api/research."
        )
    return profile


@router.post(
    "/company/{company_number}/refresh",
    response_model=RefreshResponse,
    summary="Refresh company research",
    description="Re-runs research pipeline, detects changes, preserves previous evidence, and records audit trail."
)
async def refresh_company_endpoint(
    company_number: str,
    researcher: ResearcherService = Depends(get_researcher)
) -> RefreshResponse:
    try:
        profile, changes = await researcher.refresh_company(company_number)
        return RefreshResponse(profile=profile, changes=changes)
    except InvalidCompanyNumberError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except CompanyNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except CompanyResolverError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Refresh failed: {str(e)}")


@router.get(
    "/company/{company_number}/history",
    response_model=HistoryResponse,
    summary="Get company research history and change audit",
    description="Lists past research runs and field-by-field modifications over time."
)
async def get_company_history_endpoint(
    company_number: str,
    db: Database = Depends(get_db)
) -> HistoryResponse:
    clean_nr = "".join(ch for ch in company_number if ch.isdigit())
    runs = db.get_research_runs(clean_nr)
    history = db.get_history_for_company(clean_nr)
    if not runs and not history:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No research history found for company number '{company_number}'."
        )
    return HistoryResponse(
        company_number=clean_nr,
        runs=runs,
        history=history
    )


@router.get(
    "/companies",
    response_model=List[Dict[str, Any]],
    summary="List all researched companies",
    description="Returns a list of all companies previously researched."
)
async def list_companies_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    db: Database = Depends(get_db)
) -> List[Dict[str, Any]]:
    return db.get_all_companies(limit=limit)
