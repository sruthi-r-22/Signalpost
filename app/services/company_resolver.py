"""
Company Resolver Service for Signalpost.
Authoritatively resolves Norwegian companies using Enhetsregisteret (Brønnøysundregistrene).
Implements validation, Modulo 11 checksum verification, and registry data extraction.
"""
from typing import Any, Dict, List, Optional, Tuple
import httpx
from app.config import get_settings
from app.models.company import CompanyIdentity, FinancialInfo, ManagementPerson
from app.models.evidence import Evidence, SourceType


class CompanyResolverError(Exception):
    """Base exception for resolver errors."""
    pass


class InvalidCompanyNumberError(CompanyResolverError):
    """Raised when company number does not match Norwegian org nr standards."""
    pass


class CompanyNotFoundError(CompanyResolverError):
    """Raised when company number is not found in official registry."""
    pass


def validate_norwegian_org_number(org_nr_raw: str, enforce_checksum: bool = True) -> str:
    """
    Validates a Norwegian 9-digit organization number.
    Uses Modulo 11 check digit verification according to Norwegian standard.
    """
    if org_nr_raw is None or not str(org_nr_raw).strip():
        raise InvalidCompanyNumberError("Company number cannot be empty.")

    # Strip spaces, hyphens, and periods
    cleaned = "".join(ch for ch in str(org_nr_raw).strip() if ch.isdigit())

    if len(cleaned) != 9:
        raise InvalidCompanyNumberError(
            f"Invalid organization number '{org_nr_raw}'. Norwegian company numbers must be exactly 9 digits."
        )

    if enforce_checksum:
        weights = [3, 2, 7, 6, 5, 4, 3, 2]
        weighted_sum = sum(int(cleaned[i]) * weights[i] for i in range(8))
        remainder = weighted_sum % 11

        if remainder == 0:
            expected_check = 0
        elif remainder == 1:
            raise InvalidCompanyNumberError(
                f"Invalid organization number '{cleaned}'. Check digit calculation resulted in invalid remainder 1."
            )
        else:
            expected_check = 11 - remainder

        actual_check = int(cleaned[8])
        if actual_check != expected_check:
            raise InvalidCompanyNumberError(
                f"Checksum failed for '{cleaned}'. Expected check digit {expected_check}, found {actual_check}."
            )

    return cleaned


class CompanyResolver:
    """
    Resolves official company information directly from Norwegian Enhetsregisteret.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None):
        settings = get_settings()
        self.base_url = (base_url or settings.BRREG_API_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.HTTP_TIMEOUT_SECONDS

    async def resolve_company(self, company_number: str) -> Dict[str, Any]:
        """
        Fetch company registration and role details.
        Returns a dictionary containing identity, attributes, management, and initial evidence list.
        """
        org_nr = validate_norwegian_org_number(company_number)
        url = f"{self.base_url}/enheter/{org_nr}"
        roles_url = f"{url}/roller"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url, headers={"Accept": "application/json"})
            except httpx.RequestError as exc:
                raise CompanyResolverError(
                    f"Network error contacting Norwegian registry for {org_nr}: {str(exc)}"
                ) from exc

            if response.status_code == 404:
                raise CompanyNotFoundError(
                    f"Company with organization number {org_nr} not found in Norwegian Enhetsregisteret."
                )
            if response.status_code != 200:
                raise CompanyResolverError(
                    f"Norwegian registry returned unexpected status code {response.status_code}: {response.text[:200]}"
                )

            data = response.json()

            # Attempt to fetch roles (CEO, board members)
            roles_data = None
            try:
                roles_resp = await client.get(roles_url, headers={"Accept": "application/json"})
                if roles_resp.status_code == 200:
                    roles_data = roles_resp.json()
            except Exception:
                # Roles are optional enrichment; non-fatal if unavailable
                pass

        return self._parse_registry_response(org_nr, data, roles_data, url, roles_url)

    def _parse_registry_response(
        self,
        org_nr: str,
        data: Dict[str, Any],
        roles_data: Optional[Dict[str, Any]],
        source_url: str,
        roles_source_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Extract typed models and baseline evidence from Enhetsregisteret JSON."""
        name = data.get("navn", f"Company {org_nr}").strip()
        org_form = data.get("organisasjonsform", {})
        org_type_desc = org_form.get("beskrivelse")
        org_type_code = org_form.get("kode")

        # Determine status
        is_bankrupt = data.get("konkurs", False)
        is_liquidating = data.get("underAvvikling", False)
        is_forced_liquidation = data.get("underTvangsavviklingEllerTvangsopplosning", False)
        is_deleted = data.get("slettedato") is not None

        if is_bankrupt:
            status = "Bankrupt"
        elif is_liquidating or is_forced_liquidation:
            status = "In Liquidation"
        elif is_deleted:
            status = "Dissolved"
        else:
            status = "Active"

        # Address parsing
        biz_addr = data.get("forretningsadresse") or data.get("postadresse") or {}
        address_lines = biz_addr.get("adresse", [])
        street_address = ", ".join(address_lines) if address_lines else None
        postal_code = biz_addr.get("postnummer")
        city = biz_addr.get("poststed") or biz_addr.get("kommune")
        country = biz_addr.get("land", "Norway")

        # Website
        raw_website = data.get("hjemmeside")
        official_website = None
        if raw_website:
            raw_website = raw_website.strip()
            if not raw_website.startswith(("http://", "https://")):
                official_website = f"https://{raw_website}"
            else:
                official_website = raw_website

        # Dates
        founding_date = data.get("stiftelsesdato")
        registration_date = data.get("registreringsdatoEnhetsregisteret")

        # Identity
        identity = CompanyIdentity(
            company_number=org_nr,
            name=name,
            organization_type=org_type_desc,
            organization_type_code=org_type_code,
            status=status,
            registered_address=street_address,
            postal_code=postal_code,
            city=city,
            country=country,
            official_website=official_website,
            founding_date=founding_date,
            registration_date=registration_date
        )

        # Industry (NACE)
        nace1 = data.get("naeringskode1", {})
        industry_code = nace1.get("kode")
        industry_desc = nace1.get("beskrivelse")

        # Business purpose / activity
        purpose_parts = data.get("vedtektsfestetFormaal") or data.get("aktivitet") or []
        business_purpose = " ".join(purpose_parts).strip() if purpose_parts else None

        # Employees
        employee_count = data.get("antallAnsatte")

        # Financials
        kapital = data.get("kapital", {})
        share_capital = kapital.get("belop")
        shares_count = kapital.get("antallAksjer")
        currency = kapital.get("valuta", "NOK")
        latest_accounts = data.get("sisteInnsendteAarsregnskap")

        financials = None
        if share_capital is not None or latest_accounts is not None:
            financials = FinancialInfo(
                latest_accounts_year=str(latest_accounts) if latest_accounts else None,
                share_capital=float(share_capital) if share_capital is not None else None,
                currency=currency,
                shares_count=int(shares_count) if shares_count is not None else None
            )

        # Management extraction from roles
        management: List[ManagementPerson] = []
        if roles_data:
            for group in roles_data.get("rollegrupper", []):
                group_type = group.get("type", {}).get("kode")
                group_name = group.get("type", {}).get("beskrivelse", group_type)
                for role_item in group.get("roller", []):
                    person = role_item.get("person", {})
                    first = person.get("navn", {}).get("fornavn", "")
                    last = person.get("navn", {}).get("etternavn", "")
                    full_name = f"{first} {last}".strip()
                    if full_name:
                        role_label = group_name
                        if group_type == "DAGL":
                            role_label = "Daglig leder / CEO"
                        elif group_type == "STYR":
                            role_desc = role_item.get("type", {}).get("beskrivelse")
                            if role_desc and "leder" in role_desc.lower():
                                role_label = "Styrets leder / Board Chair"
                            else:
                                role_label = "Styremedlem / Board Member"
                        management.append(ManagementPerson(role=role_label, name=full_name))

        # Build baseline authoritative evidence items
        evidence_list: List[Evidence] = []

        def add_evidence(field: str, val: Any, explanation: str):
            if val is not None:
                evidence_list.append(Evidence(
                    field=field,
                    value=val,
                    source_url=source_url,
                    source_title=f"Brønnøysundregistrene (Enhetsregisteret) - {org_nr}",
                    source_type=SourceType.OFFICIAL_REGISTRY,
                    confidence=1.0,
                    explanation=explanation,
                    is_verified=True
                ))

        add_evidence("company_name", name, f"Official company name registered in Enhetsregisteret: {name}")
        add_evidence("company_number", org_nr, f"Authoritative Norwegian organization number: {org_nr}")
        add_evidence("status", status, f"Official registration status in Brønnøysundregistrene: {status}")
        if org_type_desc:
            add_evidence("organization_type", org_type_desc, f"Registered legal form: {org_type_desc} ({org_type_code})")
        if street_address:
            add_evidence("registered_address", f"{street_address}, {postal_code or ''} {city or ''}".strip(), "Official business address from register")
        if official_website:
            add_evidence("website", official_website, "Official website URL registered in Enhetsregisteret")
        if founding_date:
            add_evidence("founding_date", founding_date, f"Registered foundation date: {founding_date}")
        if employee_count is not None:
            add_evidence("employee_count", employee_count, f"Registered employee count reported to Enhetsregisteret: {employee_count}")
        if industry_desc:
            add_evidence("industry", f"{industry_code} - {industry_desc}" if industry_code else industry_desc, "Official NACE primary industry classification")
        if business_purpose:
            add_evidence("business_purpose", business_purpose, "Registered statutory purpose in articles of association")

        for person in management:
            field = "ceo" if "CEO" in person.role else (
                "board_chair" if "Chair" in person.role else "board_member"
            )
            evidence_list.append(Evidence(
                field=field,
                value=person.name,
                source_url=roles_source_url or source_url,
                source_title=f"Brønnøysundregistrene (Enhetsregisteret) - {org_nr}",
                source_type=SourceType.OFFICIAL_REGISTRY,
                confidence=1.0,
                explanation=f"Officially registered as {person.role}: {person.name}",
                is_verified=True
            ))

        return {
            "identity": identity,
            "industry_code": industry_code,
            "industry_description": industry_desc,
            "business_purpose": business_purpose,
            "employee_count": employee_count,
            "financials": financials,
            "management": management,
            "evidence_list": evidence_list
        }
