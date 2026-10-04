"""
Unit tests for Norwegian Company Number Validation.
Tests 9-digit format, sanitization, and Modulo 11 checksum verification.
"""
import pytest
from app.services.company_resolver import (
    validate_norwegian_org_number,
    InvalidCompanyNumberError
)


def test_valid_norwegian_company_numbers():
    # Valid real Norwegian company numbers
    valid_numbers = [
        "923609016",  # Equinor ASA
        "984851006",  # DNB Bank ASA
        "943753709",  # Kongsberg Gruppen ASA
        "982463718",  # Telenor ASA
        "986228608",  # Yara International ASA
    ]
    for org in valid_numbers:
        assert validate_norwegian_org_number(org) == org


def test_sanitization_of_formatted_input():
    # Handles spaces, hyphens, and whitespace
    assert validate_norwegian_org_number(" 923 609 016 ") == "923609016"
    assert validate_norwegian_org_number("984-851-006") == "984851006"


def test_invalid_lengths():
    with pytest.raises(InvalidCompanyNumberError, match="must be exactly 9 digits"):
        validate_norwegian_org_number("12345678")  # 8 digits

    with pytest.raises(InvalidCompanyNumberError, match="must be exactly 9 digits"):
        validate_norwegian_org_number("1234567890")  # 10 digits


def test_invalid_characters_and_empty():
    with pytest.raises(InvalidCompanyNumberError, match="cannot be empty"):
        validate_norwegian_org_number("")

    with pytest.raises(InvalidCompanyNumberError, match="cannot be empty"):
        validate_norwegian_org_number("   ")


def test_modulo11_checksum_failure():
    # 123456789 has invalid check digit
    with pytest.raises(InvalidCompanyNumberError, match="Checksum failed"):
        validate_norwegian_org_number("123456789")

    # 923609017 has check digit 7 instead of 6
    with pytest.raises(InvalidCompanyNumberError, match="Checksum failed"):
        validate_norwegian_org_number("923609017")
