"""
Unit tests for Evidence Validation, Database Persistence, and Audit Tracking.
"""
import os
import tempfile
import pytest
from app.database.db import Database
from app.models.company import (
    CompanyHistoryEntry,
    CompanyIdentity,
    CompanyProfile,
    ResearchRun
)
from app.models.evidence import Evidence, SourceType


def test_evidence_model_validation():
    # Valid evidence
    ev = Evidence(
        field="website",
        value="https://www.equinor.com",
        source_url="https://data.brreg.no/enheter/923609016",
        explanation="Registered in Enhetsregisteret",
        confidence=0.99
    )
    assert ev.field == "website"

    # Invalid URL must raise ValueError
    with pytest.raises(ValueError, match="Invalid source_url"):
        Evidence(
            field="website",
            value="fake",
            source_url="ftp://invalid.com",
            explanation="Bad protocol"
        )


def test_db_persistence_and_duplicate_evidence_handling(tmp_path):
    # Use isolated temporary database file from pytest tmp_path fixture
    db_path = str(tmp_path / "test_signalpost.db")
    db = Database(db_path=db_path)
    assert db.check_health() is True

    identity = CompanyIdentity(
        company_number="923609016",
        name="EQUINOR ASA",
        status="Active",
        city="STAVANGER"
    )

    ev1 = Evidence(
        field="company_name",
        value="EQUINOR ASA",
        source_url="https://data.brreg.no/enhet",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Official register name"
    )

    # Exact duplicate
    ev2 = Evidence(
        field="company_name",
        value="EQUINOR ASA",
        source_url="https://data.brreg.no/enhet",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Official register name"
    )

    profile = CompanyProfile(
        company_number="923609016",
        identity=identity,
        facts={"company_name": "EQUINOR ASA", "city": "STAVANGER"},
        evidence_list=[ev1, ev2]
    )

    run = ResearchRun(
        run_id="run-1",
        company_number="923609016",
        sources_count=1,
        evidence_count=2
    )

    db.save_company_profile(profile, run)

    # Retrieve
    saved_profile = db.get_company_profile("923609016")
    assert saved_profile is not None
    assert saved_profile.identity.name == "EQUINOR ASA"

    # Check evidence deduplication in database table
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM evidence WHERE company_number = '923609016'")
        ev_count = cursor.fetchone()[0]
        # Deduplication should ensure only 1 row is stored for identical (field, source_url, value)
        assert ev_count == 1

    # Test history log
    history_entry = CompanyHistoryEntry(
        company_number="923609016",
        run_id="run-2",
        field_name="city",
        old_value="STAVANGER",
        new_value="OSLO",
        change_type="modified"
    )
    db.save_company_profile(profile, run, [history_entry])
    history_list = db.get_history_for_company("923609016")
    assert len(history_list) == 1
    assert history_list[0].field_name == "city"
    assert history_list[0].change_type == "modified"
