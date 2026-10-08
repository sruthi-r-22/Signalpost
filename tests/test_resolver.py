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
            },
            {
                "type": {"kode": "STYR", "beskrivelse": "Styre"},
                "roller": [
                    {
                        "type": {"beskrivelse": "Styreleder"},
                        "person": {"navn": {"fornavn": "Chair", "etternavn": "Person"}}
                    },
                    {
                        "type": {"beskrivelse": "Styremedlem"},
                        "person": {"navn": {"fornavn": "Board", "etternavn": "Member"}}
                    }
                ]
            }
        ]
    }
    registry_url = "https://data.brreg.no/enhetsregisteret/api/enheter/923609016"
    roles_url = f"{registry_url}/roller"

    parsed = resolver._parse_registry_response(
        org_nr="923609016",
        data=raw_brreg_data,
        roles_data=raw_roles_data,
        source_url=registry_url,
        roles_source_url=roles_url
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
    assert len(parsed["management"]) == 3
    assert parsed["management"][0].name == "Anders Opedal"
    assert "CEO" in parsed["management"][0].role
    leadership_evidence = {
        ev.field: ev for ev in parsed["evidence_list"]
        if ev.field in {"ceo", "board_chair", "board_member"}
    }
    assert set(leadership_evidence) == {"ceo", "board_chair", "board_member"}
    assert all(ev.source_url == roles_url for ev in leadership_evidence.values())
    assert leadership_evidence["ceo"].value == "Anders Opedal"
    assert leadership_evidence["board_chair"].value == "Chair Person"
    assert leadership_evidence["board_member"].value == "Board Member"

    ordinary_evidence = [
        ev for ev in parsed["evidence_list"]
        if ev.field in {"company_name", "registered_address", "employee_count", "industry"}
    ]
    assert ordinary_evidence
    assert all(ev.source_url == registry_url for ev in ordinary_evidence)

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
