"""
Unit tests for Verifier and Conflict Resolution.
"""
import pytest

from app.models.company import CompanyIdentity
from app.models.evidence import Evidence, SourceType
from app.services.verifier import Verifier


def test_conflict_detection_on_mismatched_org_number():
    verifier = Verifier()
    identity = CompanyIdentity(
        company_number="923609016",
        name="EQUINOR ASA",
        status="Active",
        city="STAVANGER",
        country="Norway"
    )

    facts = {"company_name": "EQUINOR ASA", "company_number": "923609016"}
    evidence_list = [
        Evidence(
            field="company_number",
            value="923609016",
            source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Official registry number 923609016",
            is_verified=True
        ),
        # Malformed / erroneous article that mixed up org numbers with another company
        Evidence(
            field="additional_info",
            value="Some wrong data",
            source_url="https://news.example.com/article",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.8,
            explanation="Article mistakenly references organization number 984851006",
            is_verified=True
        )
    ]

    resolved_facts, verified_ev, conflicts, warnings, confidence = verifier.verify_and_resolve(
        identity=identity,
        facts=facts,
        evidence_list=evidence_list
    )

    # Erroneous evidence was flagged
    assert len(conflicts) == 1
    assert conflicts[0].severity == "high"
    assert "984851006" in str(conflicts[0].conflicting_value)
    assert verified_ev[1].is_verified is False
    assert verified_ev[1].confidence <= 0.3
    # Confidence is penalized for high-severity conflict
    assert confidence < 0.95


def test_conflict_resolution_prioritizes_official_registry():
    verifier = Verifier()
    identity = CompanyIdentity(
        company_number="923609016",
        name="EQUINOR ASA",
        status="Active",
        city="STAVANGER",
        country="Norway"
    )

    facts = {"city": "Oslo"}  # e.g., web source claimed Oslo
    evidence_list = [
        Evidence(
            field="city",
            value="Oslo",
            source_url="https://random-blog.com/equinor",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.7,
            explanation="Blog claimed offices in Oslo",
            is_verified=True
        )
    ]

    resolved_facts, verified_ev, conflicts, warnings, confidence = verifier.verify_and_resolve(
        identity=identity,
        facts=facts,
        evidence_list=evidence_list
    )

    # Verifier overrules blog with official registry city
    assert resolved_facts["city"] == "STAVANGER"
    assert len(conflicts) == 1
    assert conflicts[0].primary_value == "STAVANGER"
    assert conflicts[0].conflicting_value == "Oslo"


def test_identical_values_from_multiple_sources_are_not_a_conflict():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    evidence = [
        Evidence(
            field="ceo",
            value="Alex Example",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered CEO"
        ),
        Evidence(
            field="CEO",
            value=" Alex   Example ",
            source_url="https://company.example/about",
            source_type=SourceType.COMPANY_WEBSITE,
            confidence=0.9,
            explanation="Current CEO"
        )
    ]

    facts, _, conflicts, _, _ = Verifier().verify_and_resolve(identity, {}, evidence)

    assert not conflicts
    assert facts["ceo"] == "Alex Example"


@pytest.mark.parametrize(
    "second_claim",
    [
        "Anders Opedal – President and Chief Executive Officer",
        "Anders Opedal (President & CEO)",
        "ANDERS OPEDAL, CEO",
    ],
)
def test_same_ceo_with_appended_role_title_is_not_a_conflict(second_claim):
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    evidence = [
        Evidence(
            field="ceo",
            value="Anders Opedal",
            source_url="https://registry.example/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered CEO: Anders Opedal",
        ),
        Evidence(
            field="ceo",
            value=second_claim,
            source_url="https://news.example/company",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.9,
            explanation=f"Reported CEO: {second_claim}",
        ),
    ]

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, evidence
    )

    assert not conflicts
    assert facts["ceo"] == "Anders Opedal"
    assert len(verified) == 2


def test_same_ceo_with_omitted_middle_name_is_not_a_conflict():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    evidence = [
        Evidence(
            field="ceo",
            value="Kjerstin Elisabeth Braathen",
            source_url="https://registry.example/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered CEO: Kjerstin Elisabeth Braathen",
        ),
        Evidence(
            field="ceo",
            value="Kjerstin Braathen (CEO)",
            source_url="https://news.example/company",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.9,
            explanation="Reported CEO: Kjerstin Braathen (CEO)",
        ),
    ]

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, evidence
    )

    assert not conflicts
    assert facts["ceo"] == "Kjerstin Elisabeth Braathen"
    assert len(verified) == 2


def test_distinct_named_ceos_remain_a_conflict_with_title_suffix():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    evidence = [
        Evidence(
            field="ceo",
            value="Eirik Lie",
            source_url="https://registry.example/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered CEO: Eirik Lie",
        ),
        Evidence(
            field="ceo",
            value="Geir Håøy (President & CEO)",
            source_url="https://news.example/company",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.9,
            explanation="Geir Håøy (President & CEO)",
        ),
    ]

    _, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, evidence
    )

    assert len(conflicts) == 1
    assert conflicts[0].alternatives == [
        "Eirik Lie",
        "Geir Håøy (President & CEO)",
    ]
    assert len(verified) == 2


def test_different_first_and_last_names_remain_a_ceo_conflict():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    evidence = [
        Evidence(
            field="ceo",
            value="John Smith",
            source_url="https://registry.example/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered CEO: John Smith",
        ),
        Evidence(
            field="ceo",
            value="Jane Smith",
            source_url="https://news.example/company",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.9,
            explanation="Reported CEO: Jane Smith",
        ),
    ]

    _, _, conflicts, _, _ = Verifier().verify_and_resolve(identity, {}, evidence)

    assert len(conflicts) == 1
    assert conflicts[0].alternatives == ["John Smith", "Jane Smith"]


def test_chairman_and_ceo_mentions_only_compare_ceo_person():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    chairman = Evidence(
        field="leadership_mentions",
        value="Eivind Reiten (Chairman)",
        source_url="https://company.example/board",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="Eivind Reiten (Chairman)",
    )
    ceo = Evidence(
        field="leadership_mentions",
        value="Geir Håøy (CEO)",
        source_url="https://company.example/leadership",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="Geir Håøy (CEO)",
    )

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [chairman, ceo]
    )

    assert not conflicts
    assert "Eivind Reiten (Chairman)" in facts["leadership_mentions"]
    assert "Geir Håøy (CEO)" in facts["leadership_mentions"]
    assert len(verified) == 2


def test_equivalent_president_ceo_mention_does_not_conflict_with_registry():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    registry_ceo = Evidence(
        field="ceo",
        value="Eivind Kallevik",
        source_url="https://registry.example/company",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO: Eivind Kallevik",
    )
    public_ceo = Evidence(
        field="leadership_mentions",
        value="Eivind Kallevik (President & CEO)",
        source_url="https://company.example/leadership",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="Eivind Kallevik (President & CEO)",
    )

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [registry_ceo, public_ceo]
    )

    assert not conflicts
    assert facts["ceo"] == "Eivind Kallevik"
    assert "Eivind Kallevik (President & CEO)" in facts["leadership_mentions"]
    assert len(verified) == 2


def test_chair_title_is_not_a_ceo_alternative():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    registry_ceo = Evidence(
        field="ceo",
        value="Eivind Kallevik",
        source_url="https://registry.example/company",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO: Eivind Kallevik",
    )
    chair_mention = Evidence(
        field="leadership_mentions",
        value="Rune Bjerke (Chair)",
        source_url="https://company.example/leadership",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="Rune Bjerke (Chair)",
    )

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [registry_ceo, chair_mention]
    )

    assert not conflicts
    assert facts["ceo"] == "Eivind Kallevik"
    assert "Rune Bjerke (Chair)" in facts["leadership_mentions"]
    assert len(verified) == 2


def test_chair_and_matching_president_ceo_mention_create_no_conflict():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    registry_ceo = Evidence(
        field="ceo",
        value="Eivind Kallevik",
        source_url="https://registry.example/company",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO: Eivind Kallevik",
    )
    mentions = [
        Evidence(
            field="leadership_mentions",
            value="Rune Bjerke (Chair)",
            source_url="https://company.example/board",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.9,
            explanation="Rune Bjerke (Chair)",
        ),
        Evidence(
            field="leadership_mentions",
            value="Eivind Kallevik (President & CEO)",
            source_url="https://company.example/leadership",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.9,
            explanation="Eivind Kallevik (President & CEO)",
        ),
    ]

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [registry_ceo, *mentions]
    )

    assert not conflicts
    assert facts["ceo"] == "Eivind Kallevik"
    assert set(facts["leadership_mentions"]) == {
        "Rune Bjerke (Chair)",
        "Eivind Kallevik (President & CEO)",
    }
    assert len(verified) == 3


def test_distinct_explicit_ceo_roles_still_conflict():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
    )
    registry_ceo = Evidence(
        field="ceo",
        value="Eivind Kallevik",
        source_url="https://registry.example/company",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO: Eivind Kallevik",
    )
    public_ceo = Evidence(
        field="leadership_mentions",
        value="Rune Bjerke (President & CEO)",
        source_url="https://news.example/company",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="Rune Bjerke (President & CEO)",
    )

    _, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [registry_ceo, public_ceo]
    )

    assert len(conflicts) == 1
    assert conflicts[0].field == "ceo"
    assert conflicts[0].alternatives == [
        "Eivind Kallevik",
        "Rune Bjerke (President & CEO)",
    ]
    assert len(verified) == 2


def test_equivalent_website_url_formats_are_not_a_conflict():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
        official_website="https://www.kongsberg.com/"
    )
    evidence = [
        Evidence(
            field="website",
            value="https://www.kongsberg.com/",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Officially registered website"
        ),
        Evidence(
            field="website",
            value="www.kongsberg.com",
            source_url="https://www.kongsberg.com/",
            source_type=SourceType.COMPANY_WEBSITE,
            confidence=0.9,
            explanation="Company website"
        )
    ]

    facts, verified, conflicts, _, confidence = Verifier().verify_and_resolve(
        identity, {}, evidence
    )

    assert not conflicts
    assert len(verified) == 2
    assert facts["website"] == "https://www.kongsberg.com/"
    assert confidence == 0.95


def test_website_scheme_case_and_trailing_slash_are_normalized():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
        official_website="https://www.example.com/",
    )
    evidence = [
        Evidence(
            field="website",
            value="https://www.example.com/",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered company website",
        ),
        Evidence(
            field="website",
            value="HTTP://WWW.EXAMPLE.COM",
            source_url="https://example.com/about",
            source_type=SourceType.COMPANY_WEBSITE,
            confidence=0.9,
            explanation="Company website",
        ),
    ]

    _, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, evidence
    )

    assert not conflicts
    assert len(verified) == 2


def test_company_country_and_corporate_domains_are_related_not_conflicting():
    identity = CompanyIdentity(
        company_number="123456789",
        name="TELENOR ASA",
        status="Active",
        official_website="https://www.telenor.no/",
    )
    evidence = [
        Evidence(
            field="website",
            value="https://www.telenor.no/",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered Norwegian website",
        ),
        Evidence(
            field="website",
            value="https://www.telenor.com",
            source_url="https://www.telenor.com/about",
            source_type=SourceType.COMPANY_WEBSITE,
            confidence=0.9,
            explanation="Corporate group website",
        ),
    ]
    verifier = Verifier()

    facts, verified, conflicts, _, _ = verifier.verify_and_resolve(
        identity, {}, evidence
    )

    assert not conflicts
    assert facts["website"] == "https://www.telenor.no/"
    assert len(verified) == 2
    assert verifier._classify_website_relationship(
        identity, "https://www.telenor.com", identity.official_website
    ) == "corporate_or_local_domain"


def test_website_www_and_non_www_domains_are_related():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
        official_website="https://example.com",
    )
    evidence = [
        Evidence(
            field="website",
            value="https://example.com",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered company website",
        ),
        Evidence(
            field="website",
            value="https://www.example.com/",
            source_url="https://www.example.com/about",
            source_type=SourceType.COMPANY_WEBSITE,
            confidence=0.9,
            explanation="Company website",
        ),
    ]

    _, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, evidence
    )

    assert not conflicts
    assert len(verified) == 2


def test_unrelated_website_is_a_conflict_and_evidence_is_preserved():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active",
        official_website="https://example.com",
    )
    evidence = [
        Evidence(
            field="website",
            value="https://example.com",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered company website",
        ),
        Evidence(
            field="website",
            value="https://unrelated.example",
            source_url="https://unrelated.example/company",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.9,
            explanation="Candidate company website",
        ),
    ]

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, evidence
    )

    assert len(conflicts) == 1
    assert conflicts[0].field == "website"
    assert conflicts[0].primary_value == "https://example.com"
    assert conflicts[0].conflicting_value == "https://unrelated.example"
    assert facts["website"] == "https://example.com"
    assert {ev.id for ev in verified} == {ev.id for ev in evidence}
    assert {ev.id for ev in conflicts[0].evidence} == {
        ev.id for ev in evidence
    }


def test_multiple_board_members_are_accumulated_without_conflict():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    names = ["Morten Henriksen", "Merete Hverven", "Marianne Wiinholt"]
    evidence = [
        Evidence(
            field="board_member",
            value=name,
            source_url=f"https://registry.example/board/{index}",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation=f"Registered board member: {name}"
        )
        for index, name in enumerate(names)
    ]

    facts, verified, conflicts, _, confidence = Verifier().verify_and_resolve(
        identity, {}, evidence
    )

    assert not conflicts
    assert facts["board_member"] == names
    assert len(verified) == 3
    assert confidence == 0.95


def test_kongsberg_ceo_values_remain_a_conflict():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    registry_claim = Evidence(
        field="ceo",
        value="Eirik Lie",
        source_url="https://data.brreg.no/company",
        source_title="Brønnøysundregistrene",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO: Eirik Lie"
    )
    web_claim = Evidence(
        field="CEO",
        value="Geir Håøy",
        source_url="https://wikipedia.org/company",
        source_title="Wikipedia",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="CEO: Geir Håøy"
    )

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [registry_claim, web_claim]
    )

    assert len(conflicts) == 1
    assert conflicts[0].field == "ceo"
    assert conflicts[0].alternatives == ["Eirik Lie", "Geir Håøy"]
    assert {ev.id for ev in conflicts[0].evidence} == {
        registry_claim.id,
        web_claim.id
    }
    assert len(verified) == 2
    assert facts["ceo"] == "Eirik Lie"


def test_explicit_ceo_role_in_leadership_evidence_is_compared_as_ceo():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    registry_claim = Evidence(
        field="ceo",
        value="Registry Executive",
        source_url="https://data.brreg.no/company",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO"
    )
    public_claims = [
        Evidence(
            field="leadership_mentions",
            value=name,
            source_url=f"https://company.example/{index}",
            source_title=f"{name} (CEO)",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.9,
            explanation=f"{name} (CEO)"
        )
        for index, name in enumerate(("Eirik Lie", "Geir Håøy"))
    ]

    _, _, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [registry_claim, *public_claims]
    )

    assert len(conflicts) == 1
    assert conflicts[0].field == "ceo"
    assert set(conflicts[0].alternatives) == {
        "Registry Executive",
        "Eirik Lie",
        "Geir Håøy",
    }


def test_chairman_mention_is_preserved_but_not_compared_as_ceo():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    registry_claim = Evidence(
        field="ceo",
        value="Eirik Lie",
        source_url="https://data.brreg.no/company",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO"
    )
    ceo_mention = Evidence(
        field="leadership_mentions",
        value="Geir Håøy",
        source_url="https://wikipedia.org/company",
        source_title="Geir Håøy (CEO)",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="Geir Håøy (CEO)"
    )
    chairman_mention = Evidence(
        field="leadership_mentions",
        value="Eivind Reiten (Chairman)",
        source_url="https://company.example/board",
        source_title="Eivind Reiten (Chairman)",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="Eivind Reiten (Chairman)"
    )

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [registry_claim, ceo_mention, chairman_mention]
    )

    assert len(conflicts) == 1
    assert conflicts[0].field == "ceo"
    assert conflicts[0].alternatives == ["Eirik Lie", "Geir Håøy"]
    assert "Eivind Reiten (Chairman)" in facts["leadership_mentions"]
    assert {ev.id for ev in verified} == {
        registry_claim.id,
        ceo_mention.id,
        chairman_mention.id,
    }


def test_duplicate_ceo_conflict_evidence_is_deduplicated():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    registry_claim = Evidence(
        field="ceo",
        value="Eirik Lie",
        source_url="https://data.brreg.no/company",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO: Eirik Lie"
    )
    wikipedia_claim = Evidence(
        field="ceo",
        value="Geir Håøy",
        source_url="https://wikipedia.org/company",
        source_title="Wikipedia",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.9,
        explanation="Geir Håøy is the CEO"
    )
    duplicate_wikipedia_claim = wikipedia_claim.model_copy()

    _, _, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {},
        [registry_claim, wikipedia_claim, duplicate_wikipedia_claim]
    )

    assert len(conflicts) == 1
    conflict_wikipedia_evidence = [
        ev for ev in conflicts[0].evidence
        if ev.source_url == wikipedia_claim.source_url
    ]
    assert len(conflict_wikipedia_evidence) == 1
    assert len(conflicts[0].evidence) == 2


def test_different_values_are_flagged_and_all_evidence_is_preserved():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    registry_evidence = Evidence(
        field="ceo",
        value="Alex Example",
        source_url="https://data.brreg.no/company",
        source_title="Official Registry",
        source_type=SourceType.OFFICIAL_REGISTRY,
        confidence=1.0,
        explanation="Registered CEO: Alex Example"
    )
    web_evidence = Evidence(
        field="chief executive officer",
        value="Jordan Example",
        source_url="https://news.example/article",
        source_title="Company leadership update",
        source_type=SourceType.PUBLIC_SEARCH,
        confidence=0.85,
        explanation="Jordan Example is identified as CEO"
    )

    facts, verified, conflicts, _, _ = Verifier().verify_and_resolve(
        identity, {}, [registry_evidence, web_evidence]
    )

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.field == "ceo"
    assert conflict.primary_value == "Alex Example"
    assert conflict.alternatives == ["Alex Example", "Jordan Example"]
    assert {ev.id for ev in conflict.evidence} == {registry_evidence.id, web_evidence.id}
    assert {ev.id for ev in verified} == {registry_evidence.id, web_evidence.id}
    assert facts["ceo"] == "Alex Example"
    assert "unresolved" in conflict.resolution


def test_authoritative_source_is_preferred_over_higher_confidence_web_claim():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    evidence = [
        Evidence(
            field="ceo",
            value="Registry Name",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=0.7,
            explanation="Official registry record"
        ),
        Evidence(
            field="ceo",
            value="Web Name",
            source_url="https://random.example/company",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=1.0,
            explanation="Web profile"
        )
    ]

    facts, _, conflicts, _, _ = Verifier().verify_and_resolve(identity, {}, evidence)

    assert len(conflicts) == 1
    assert facts["ceo"] == "Registry Name"


def test_newer_publication_date_can_prefer_a_fresher_leadership_claim():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    evidence = [
        Evidence(
            field="ceo",
            value="Former CEO",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            publication_date="2023-01-01",
            confidence=1.0,
            explanation="Older registry record"
        ),
        Evidence(
            field="ceo",
            value="Current CEO",
            source_url="https://company.example/news",
            source_type=SourceType.COMPANY_WEBSITE,
            publication_date="2025-01-01",
            confidence=0.8,
            explanation="Newer leadership announcement"
        )
    ]

    facts, _, conflicts, _, _ = Verifier().verify_and_resolve(identity, {}, evidence)

    assert len(conflicts) == 1
    assert facts["ceo"] == "Current CEO"
    assert "newest source publication date" in conflicts[0].resolution


def test_leadership_citation_with_explicit_ceo_role_conflicts_with_registry_ceo():
    identity = CompanyIdentity(
        company_number="123456789",
        name="UNSEEN COMPANY AS",
        status="Active"
    )
    evidence = [
        Evidence(
            field="ceo",
            value="Registry Executive",
            source_url="https://data.brreg.no/company",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered Daglig leder / CEO"
        ),
        Evidence(
            field="leadership_mentions",
            value="Web Executive",
            source_url="https://company.example/about",
            source_type=SourceType.COMPANY_WEBSITE,
            confidence=0.9,
            explanation="Web Executive is the company's CEO"
        )
    ]

    _, _, conflicts, _, _ = Verifier().verify_and_resolve(identity, {}, evidence)

    assert len(conflicts) == 1
    assert conflicts[0].field == "ceo"
    assert conflicts[0].alternatives == ["Registry Executive", "Web Executive"]


def test_unknown_companies_use_the_same_generic_conflict_resolution():
    identity = CompanyIdentity(
        company_number="111222333",
        name="A COMPANY NEVER SEEN BEFORE",
        status="Active"
    )
    evidence = [
        Evidence(
            field="ceo",
            value="One Person",
            source_url="https://registry.example/record",
            source_type=SourceType.OFFICIAL_REGISTRY,
            confidence=1.0,
            explanation="Registered executive"
        ),
        Evidence(
            field="ceo",
            value="Another Person",
            source_url="https://web.example/profile",
            source_type=SourceType.PUBLIC_SEARCH,
            confidence=0.8,
            explanation="Reported executive"
        )
    ]

    facts, _, conflicts, _, _ = Verifier().verify_and_resolve(identity, {}, evidence)

    assert facts["company_name"] == "A COMPANY NEVER SEEN BEFORE"
    assert len(conflicts) == 1
    assert conflicts[0].alternatives == ["One Person", "Another Person"]
