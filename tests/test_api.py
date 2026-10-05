"""
Integration tests for FastAPI endpoints.
Uses FastAPI TestClient to test health, research, get, refresh, and history.
"""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.services.company_resolver import CompanyNotFoundError, CompanyResolver
from app.services.llm_service import LLMExtractionResult
from app.services.search_service import MockSearchProvider

client = TestClient(app)


def _equinor_registry_response():
    resolver = CompanyResolver()
    return resolver._parse_registry_response(
        org_nr="923609016",
        data={
            "organisasjonsnummer": "923609016",
            "navn": "EQUINOR ASA",
            "organisasjonsform": {
                "kode": "ASA",
                "beskrivelse": "Allmennaksjeselskap",
            },
            "forretningsadresse": {
                "adresse": ["Forusbeen 50"],
                "postnummer": "4035",
                "poststed": "Stavanger",
                "land": "Norway",
            },
            "hjemmeside": "https://www.equinor.com",
            "stiftelsesdato": "1972-06-14",
            "registreringsdatoEnhetsregisteret": "1981-09-01",
            "naeringskode1": {
                "kode": "06.100",
                "beskrivelse": "Extraction of crude petroleum",
            },
            "antallAnsatte": 23000,
            "aktivitet": ["Exploration and production of oil and gas"],
        },
        roles_data=None,
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
    )


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "signalpost"
    assert data["database"] == "connected"


def test_research_invalid_company_number():
    response = client.post("/api/research", json={"company_number": "12345"})
    assert response.status_code == 400
    assert "9 digits" in response.json()["detail"]


def test_research_non_existent_company():
    # Valid Modulo 11 check digit (3*3+2*2+7*0+6*0+5*0+4*0+3*0+2*0 = 13, 13%11=2, 11-2=9 -> 320000009)
    # but does not exist in registry
    with patch(
        "app.services.researcher.CompanyResolver.resolve_company",
        new=AsyncMock(side_effect=CompanyNotFoundError("Company not found")),
    ):
        response = client.post("/api/research", json={"company_number": "320000009"})
    assert response.status_code == 404


def test_research_valid_company_flow():
    # Equinor ASA (923609016)
    org_nr = "923609016"

    with (
        patch(
            "app.services.researcher.CompanyResolver.resolve_company",
            new=AsyncMock(return_value=_equinor_registry_response()),
        ),
        patch(
            "app.services.researcher.SearchService._init_provider",
            return_value=MockSearchProvider(),
        ),
        patch(
            "app.services.extractor.LLMService.extract_facts",
            new=AsyncMock(return_value=LLMExtractionResult()),
        ),
    ):
        # 1. Research
        res = client.post("/api/research", json={"company_number": org_nr})
        assert res.status_code == 200
        profile = res.json()
        assert profile["company_number"] == org_nr
        assert "EQUINOR" in profile["identity"]["name"]
        assert profile["identity"]["status"] == "Active"
        assert len(profile["evidence_list"]) > 0

        # 2. Retrieve existing
        get_res = client.get(f"/api/company/{org_nr}")
        assert get_res.status_code == 200
        assert get_res.json()["company_number"] == org_nr

        # 3. Refresh
        refresh_res = client.post(f"/api/company/{org_nr}/refresh")
        assert refresh_res.status_code == 200
        refresh_data = refresh_res.json()
        assert "profile" in refresh_data
        assert "changes" in refresh_data
        assert len(refresh_data["changes"]) > 0

        # 4. History
        hist_res = client.get(f"/api/company/{org_nr}/history")
        assert hist_res.status_code == 200
        hist_data = hist_res.json()
        assert len(hist_data["runs"]) >= 2  # initial + refresh
        assert len(hist_data["history"]) > 0

        # 5. List companies
        list_res = client.get("/api/companies")
        assert list_res.status_code == 200
        assert any(c["company_number"] == org_nr for c in list_res.json())
