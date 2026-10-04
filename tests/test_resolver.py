"""
Unit tests for Company Resolver and Registry Parsing.
"""
import pytest
from app.services.company_resolver import CompanyResolver, CompanyNotFoundError


def test_parse_registry_response_active():
    resolver = CompanyResolver()
    raw_brreg_data = {
        "organisasjonsnummer": "923609016",
        "navn": "EQUINOR ASA",
        "organisasjonsform": {
            "kode": "ASA",
            "beskrivelse": "Allmennaksjeselskap"
        },
        "konkurs": False,
        "underAvvikling": False,
        "antallAnsatte": 21000,
        "forretningsadresse": {
            "adresse": ["Forusbeen 50"],
            "postnummer": "4035",
            "poststed": "STAVANGER",
            "land": "Norge"
        },
        "hjemmeside": "www.equinor.com",
        "stiftelsesdato": "1972-09-18",
        "registreringsdatoEnhetsregisteret": "1995-03-12",
        "naeringskode1": {
            "kode": "06.100",
            "beskrivelse": "Utvinning av råolje"
        },
        "vedtektsfestetFormaal": ["Energiutvikling og drift."]
    }
    raw_roles_data = {
        "rollegrupper": [
            {
                "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
                "roller": [
                    {"person": {"navn": {"fornavn": "Anders", "etternavn": "Opedal"}}}
                ]
            }
        ]
    }

    parsed = resolver._parse_registry_response(
        org_nr="923609016",
        data=raw_brreg_data,
        roles_data=raw_roles_data,
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016"
    )

    identity = parsed["identity"]
    assert identity.name == "EQUINOR ASA"
    assert identity.company_number == "923609016"
    assert identity.status == "Active"
    assert identity.organization_type == "Allmennaksjeselskap"
    assert identity.city == "STAVANGER"
    assert identity.official_website == "https://www.equinor.com"
    assert parsed["employee_count"] == 21000
    assert parsed["industry_code"] == "06.100"

    # Management
    assert len(parsed["management"]) == 1
    assert parsed["management"][0].name == "Anders Opedal"
    assert "CEO" in parsed["management"][0].role
    ceo_evidence = [ev for ev in parsed["evidence_list"] if ev.field == "ceo"]
    assert len(ceo_evidence) == 1
    assert ceo_evidence[0].value == "Anders Opedal"

    # Authoritative evidence count
    assert len(parsed["evidence_list"]) >= 8
    first_ev = parsed["evidence_list"][0]
    assert first_ev.confidence == 1.0
    assert first_ev.source_type.value == "official_registry"


def test_parse_registry_response_bankrupt_status():
    resolver = CompanyResolver()
    raw_brreg_data = {
        "organisasjonsnummer": "999999999",
        "navn": "DEBT CORP AS",
        "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
        "konkurs": True,
        "underAvvikling": False
    }
    parsed = resolver._parse_registry_response("999999999", raw_brreg_data, None, "http://brreg.no")
    assert parsed["identity"].status == "Bankrupt"
