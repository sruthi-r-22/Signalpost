"""
Integration tests for FastAPI endpoints.
Uses FastAPI TestClient to test health, research, get, refresh, and history.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


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
    response = client.post("/api/research", json={"company_number": "320000009"})
    assert response.status_code in (404, 502)


def test_research_valid_company_flow():
    # Equinor ASA (923609016)
    org_nr = "923609016"

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
