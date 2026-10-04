"""
Company Verification and Conflict Resolution Service for Signalpost.
Validates extracted facts against authoritative identity, flags conflicting data,
applies source hierarchy, and calculates calibrated confidence scores.
"""
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
import re
from urllib.parse import urlparse

from app.models.company import CompanyIdentity
from app.models.evidence import ConflictItem, Evidence, SourceType


class Verifier:
    """
    Verification layer that guards against identity mismatch, conflicting claims,
    and hallucinated associations.
    """

    def verify_and_resolve(
        self,
        identity: CompanyIdentity,
        facts: Dict[str, Any],
        evidence_list: List[Evidence]
    ) -> Tuple[Dict[str, Any], List[Evidence], List[ConflictItem], List[str], float]:
        """
        Cross-checks all facts and evidence against authoritative identity.
        Returns:
            (resolved_facts, verified_evidence, conflicts, warnings, overall_confidence)
        """
        conflicts: List[ConflictItem] = []
        warnings: List[str] = []
        verified_evidence: List[Evidence] = []
        resolved_claims: Dict[str, Any] = {}

        norm_primary_name = self._normalize_company_name(identity.name)
        primary_domain = self._extract_domain(identity.official_website) if identity.official_website else None

        # 1. Verify each evidence record against primary identity
        for ev in evidence_list:
            explanation_lower = ev.explanation.lower()

            # Check if evidence mentions a different 9-digit Norwegian org number
            other_org_matches = set(re.findall(r"\b\d{9}\b", ev.explanation))
            if other_org_matches:
                other_org_matches.discard(identity.company_number)
                if other_org_matches:
                    conflicting_org = next(iter(other_org_matches))
                    ev.is_verified = False
                    ev.confidence = min(ev.confidence, 0.3)
                    conflicts.append(ConflictItem(
                        field=ev.field,
                        primary_value=identity.company_number,
                        conflicting_value=conflicting_org,
                        primary_source=f"Enhetsregisteret ({identity.company_number})",
                        conflicting_source=ev.source_url,
                        resolution=f"Rejected or downranked claim: Source references organization number {conflicting_org} rather than primary {identity.company_number}.",
                        severity="high"
                    ))
                    warnings.append(
                        f"Evidence on '{ev.field}' references mismatched org number {conflicting_org}."
                    )

            # Check domain consistency for company website claims
            if ev.source_type == SourceType.COMPANY_WEBSITE and primary_domain:
                ev_domain = self._extract_domain(ev.source_url)
                if (
                    ev_domain
                    and self._classify_website_relationship(
                        identity,
                        ev.source_url,
                        identity.official_website
                    ) == "unrelated"
                ):
                    ev.confidence = max(0.4, ev.confidence - 0.3)
                    warnings.append(
                        f"Website evidence from domain '{ev_domain}' differs from registered domain '{primary_domain}'."
                    )

            # Check if source text mentions competing city/address
            if ev.field in ("registered_address", "city") and ev.source_type != SourceType.OFFICIAL_REGISTRY:
                val_str = str(ev.value).lower()
                if identity.city and identity.city.lower() not in val_str:
                    conflicts.append(ConflictItem(
                        field="city",
                        primary_value=identity.city,
                        conflicting_value=str(ev.value),
                        primary_source="Brønnøysundregistrene (Enhetsregisteret)",
                        conflicting_source=ev.source_url,
                        resolution=f"Preserved official registered city '{identity.city}'. External source mentions '{ev.value}'.",
                        severity="warning"
                    ))
                    ev.confidence = max(0.5, ev.confidence - 0.3)

            verified_evidence.append(ev)

        # Compare only fields whose values are expected to be unique.
        comparable_evidence: Dict[str, List[Evidence]] = {}
        single_value_fields = {
            "company_name",
            "company_number",
            "status",
            "organization_type",
            "registered_address",
            "website",
            "founding_date",
            "industry",
            "business_purpose",
            "ceo",
            "board_chair",
            "headquarters_city",
        }
        multi_value_fields = {
            "board_member",
            "leadership_mentions",
            "key_products_or_services",
            "citations",
        }
        accumulated_values: Dict[str, List[Any]] = {}
        for ev in verified_evidence:
            if not ev.is_verified:
                continue
            normalized_field = self._normalize_field(ev.field, ev)
            if normalized_field == "ceo" and not self._is_ceo_claim(ev):
                normalized_field = "leadership_mentions"
            if normalized_field in multi_value_fields:
                values = accumulated_values.setdefault(normalized_field, [])
                incoming_values = ev.value if isinstance(ev.value, list) else [ev.value]
                for value in incoming_values:
                    if not any(
                        self._normalize_value(value, normalized_field)
                        == self._normalize_value(existing, normalized_field)
                        for existing in values
                    ):
                        values.append(value)
                if normalized_field == "leadership_mentions" and self._is_ceo_claim(ev):
                    comparable_evidence.setdefault("ceo", []).append(ev)
                continue
            if normalized_field not in single_value_fields:
                continue
            comparable_evidence.setdefault(normalized_field, []).append(ev)

        for field, values in accumulated_values.items():
            existing_values = facts.get(field)
            combined = list(existing_values) if isinstance(existing_values, list) else (
                [existing_values] if existing_values is not None else []
            )
            for value in values:
                if not any(
                    self._normalize_value(value, field)
                    == self._normalize_value(existing, field)
                    for existing in combined
                ):
                    combined.append(value)
            resolved_claims[field] = combined

        for field, claims in comparable_evidence.items():
            claims = self._deduplicate_conflict_evidence(claims)
            distinct_values: Dict[str, Any] = {}
            for ev in claims:
                distinct_values.setdefault(self._normalize_value(ev.value, field), ev.value)
            if field == "website" and len(distinct_values) > 1:
                registered_website = identity.official_website
                website_reference = registered_website or max(
                    claims,
                    key=lambda ev: (self._source_priority(ev), ev.confidence)
                ).value
                relationships = [
                    self._classify_website_relationship(
                        identity, str(ev.value), website_reference
                    )
                    for ev in claims
                ]
                if all(relationship != "unrelated" for relationship in relationships):
                    resolved_claims[field] = registered_website or website_reference
                    continue
            if len(distinct_values) < 2:
                best = max(claims, key=lambda ev: (
                    self._source_priority(ev),
                    ev.confidence
                ))
                resolved_claims[field] = best.value
                continue

            candidates = list(distinct_values.items())
            time_sensitive = field in {"ceo", "board_chair", "board_member", "leadership"}
            all_have_publication_dates = all(
                self._parse_publication_date(ev.publication_date) is not None
                for ev in claims
            )

            def evidence_rank(item: Tuple[str, Any]) -> Tuple[Any, ...]:
                normalized_value = item[0]
                matching = [
                    ev for ev in claims
                    if self._normalize_value(ev.value, field) == normalized_value
                ]
                best = max(matching, key=lambda ev: (
                    self._source_priority(ev),
                    ev.confidence
                ))
                if time_sensitive and all_have_publication_dates:
                    latest_date = max(self._parse_publication_date(ev.publication_date) for ev in matching)
                    return (latest_date, self._source_priority(best), best.confidence)
                return (self._source_priority(best), best.confidence)

            selected_normalized, selected_value = max(candidates, key=evidence_rank)
            selected_evidence = [
                ev for ev in claims
                if self._normalize_value(ev.value, field) == selected_normalized
            ]
            competing_values = [value for norm, value in candidates if norm != selected_normalized]
            has_dates_for_resolution = time_sensitive and all_have_publication_dates
            primary = max(selected_evidence, key=lambda ev: (
                self._parse_publication_date(ev.publication_date)
                if has_dates_for_resolution else date.min,
                self._source_priority(ev),
                ev.confidence
            ))
            resolved_claims[field] = selected_value
            if time_sensitive and not has_dates_for_resolution:
                resolution = (
                    "Selected the strongest available source by authority and confidence; "
                    "the conflict remains unresolved because publication dates are unavailable."
                )
            elif has_dates_for_resolution:
                resolution = (
                    "Selected the value supported by the newest source publication date; "
                    "competing evidence is preserved."
                )
            else:
                resolution = (
                    "Selected the value supported by the strongest available source; "
                    "competing evidence is preserved."
                )

            conflicts.append(ConflictItem(
                field=field,
                primary_value=selected_value,
                conflicting_value=competing_values[0],
                alternatives=[value for _, value in candidates],
                evidence=claims,
                primary_source=primary.source_url,
                conflicting_source=next(
                    ev.source_url for ev in claims
                    if self._normalize_value(ev.value, field) != selected_normalized
                ),
                resolution=resolution,
                severity="warning"
            ))
            warnings.append(f"Conflicting evidence found for '{field}'.")

        # 2. Source Hierarchy: Ensure official registry always takes precedence
        resolved_facts = dict(facts)
        resolved_facts["company_name"] = identity.name
        resolved_facts["company_number"] = identity.company_number
        resolved_facts["status"] = identity.status
        resolved_facts.update(resolved_claims)
        if identity.registered_address:
            resolved_facts["registered_address"] = identity.registered_address
        if identity.city:
            resolved_facts["city"] = identity.city
        if identity.official_website:
            resolved_facts["website"] = identity.official_website

        # 3. Calculate calibrated overall confidence
        confidence = 0.95  # Start with high confidence due to official registry
        if conflicts:
            for c in conflicts:
                if c.severity == "high":
                    confidence -= 0.15
                else:
                    confidence -= 0.05

        if len(verified_evidence) <= 1:
            confidence -= 0.10

        overall_confidence = max(0.1, min(1.0, round(confidence, 2)))

        return resolved_facts, verified_evidence, conflicts, warnings, overall_confidence

    def _normalize_company_name(self, name: str) -> str:
        """Strip legal suffixes for fuzzy matching."""
        n = name.upper()
        for suffix in [" ASA", " AS", " ENK", " DA", " ANS", " NUF"]:
            if n.endswith(suffix):
                n = n[:-len(suffix)].strip()
        return n.lower()

    def _extract_domain(self, url: str) -> Optional[str]:
        """Extract clean domain from URL."""
        if not url:
            return None
        try:
            parsed = urlparse(url if "://" in url else f"https://{url}")
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return None

    def _classify_website_relationship(
        self,
        identity: CompanyIdentity,
        candidate: str,
        registered: Optional[str],
    ) -> str:
        """Classify an observed site against the registered company website."""
        candidate_normalized = self._normalize_value(candidate, "website")
        registered_normalized = self._normalize_value(registered or "", "website")
        if candidate_normalized == registered_normalized:
            return "official_registered_website"

        candidate_host = self._website_host(candidate)
        registered_host = self._website_host(registered or "")
        if not candidate_host or not registered_host:
            return "unrelated"
        if candidate_host == registered_host:
            return "alternate_official_website"

        candidate_base = self._domain_base_label(candidate_host)
        registered_base = self._domain_base_label(registered_host)
        if candidate_base == registered_base:
            return "corporate_or_local_domain"

        company_tokens = {
            token for token in re.findall(r"[a-z0-9]+", identity.name.casefold())
            if len(token) >= 4 and token not in {
                "company", "group", "gruppen", "holding", "holdings",
                "international", "global", "corporate",
            }
        }
        candidate_labels = set(re.findall(r"[a-z0-9]+", candidate_host))
        registered_labels = set(re.findall(r"[a-z0-9]+", registered_host))
        known_domain_modifiers = {
            "group", "gruppen", "holding", "holdings", "international",
            "global", "corporate", "company", "official", "investor",
            "investors", "relations", "ir", "www",
        }
        shared_company_tokens = (
            candidate_labels & registered_labels & company_tokens
        )
        candidate_unrecognized = candidate_labels - company_tokens - known_domain_modifiers
        registered_unrecognized = registered_labels - company_tokens - known_domain_modifiers
        if shared_company_tokens and not candidate_unrecognized and not registered_unrecognized:
            return "alternate_official_website"
        return "unrelated"

    def _website_host(self, url: str) -> Optional[str]:
        if not url:
            return None
        try:
            parsed = urlparse(url if "://" in url else f"//{url}", scheme="")
            host = (parsed.hostname or "").casefold().rstrip(".")
            if host.startswith("www."):
                host = host[4:]
            return host or None
        except ValueError:
            return None

    def _domain_base_label(self, host: str) -> str:
        labels = host.split(".")
        if (
            len(labels) >= 3
            and len(labels[-1]) == 2
            and labels[-2] in {"co", "com", "org", "net", "gov", "ac", "edu"}
        ):
            return labels[-3]
        return labels[-2] if len(labels) > 1 else labels[0]

    def _normalize_field(self, field: str, evidence: Optional[Evidence] = None) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_")
        aliases = {
            "chief_executive": "ceo",
            "chief_executive_officer": "ceo",
            "daglig_leder": "ceo",
            "ceo_name": "ceo",
            "board_chairman": "board_chair",
            "board_chairperson": "board_chair",
        }
        return aliases.get(normalized, normalized)

    def _is_ceo_claim(self, evidence: Evidence) -> bool:
        """Require an explicit CEO role for leadership mentions and reject other named roles."""
        field = re.sub(r"[^a-z0-9]+", "_", evidence.field.lower()).strip("_")
        direct_ceo_field = field in {
            "ceo",
            "chief_executive",
            "chief_executive_officer",
            "ceo_name",
            "daglig_leder",
        }
        ceo_role = r"\b(?:CEO|chief executive(?: officer)?|daglig leder|administrerende direktør)\b"
        other_role = (
            r"\b(?:chair|chairman|chairwoman|chairperson|board chair(?:man|woman|person)?|"
            r"styrets leder|styreleder|"
            r"board member|styremedlem)\b"
        )
        value = evidence.value if isinstance(evidence.value, str) else ""
        if re.search(other_role, value, re.I):
            return False
        context = f"{evidence.source_title or ''} {evidence.explanation} {value}"
        explicit_ceo = re.search(ceo_role, context, re.I)
        explicit_other_role = re.search(other_role, context, re.I)
        if explicit_other_role:
            return False
        if explicit_ceo:
            return True
        return direct_ceo_field

    def _deduplicate_conflict_evidence(self, claims: List[Evidence]) -> List[Evidence]:
        seen = set()
        unique_claims = []
        for evidence in claims:
            key = (
                evidence.source_url,
                evidence.field,
                self._normalize_value(evidence.value),
                evidence.explanation,
            )
            if key not in seen:
                seen.add(key)
                unique_claims.append(evidence)
        return unique_claims

    def _normalize_value(self, value: Any, field: Optional[str] = None) -> str:
        if isinstance(value, str):
            normalized = re.sub(r"\s+", " ", value).strip()
            if field == "website":
                parsed = urlparse(
                    normalized if "://" in normalized else f"//{normalized}",
                    scheme=""
                )
                host = parsed.netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                path = parsed.path.rstrip("/")
                return f"{host}{path}?{parsed.query}#{parsed.fragment}".rstrip("?#")
            if field == "ceo":
                return self._normalize_person_name(normalized)
            return normalized.casefold()
        return repr(value)

    def _normalize_person_name(self, value: str) -> str:
        """Normalize a CEO claim to first and last name for comparison only."""
        normalized = value.casefold()
        role_labels = (
            r"\bchief executive(?: officer)?\b",
            r"\bboard chair(?:man|woman|person)?\b",
            r"\bboard member\b",
            r"\bpresident\b",
            r"\bceo\b",
            r"\bchairman\b",
            r"\bchairwoman\b",
            r"\bchair\b",
            r"\bchairperson\b",
            r"\bstyrets leder\b",
            r"\bstyreleder\b",
            r"\bstyremedlem\b",
            r"\bdaglig leder\b",
            r"\badministrerende direktør\b",
        )
        for role_label in role_labels:
            normalized = re.sub(role_label, " ", normalized, flags=re.IGNORECASE)
        normalized = re.sub(
            r"^\s*(?:mr|mrs|ms|miss|dr|prof|sir|dame)\.?\s+",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
        tokens = [
            token for token in normalized.split()
            if token not in {"and", "the", "of", "is"}
        ]
        if len(tokens) > 2:
            tokens = [tokens[0], tokens[-1]]
        return " ".join(tokens)

    def _source_priority(self, evidence: Evidence) -> int:
        return {
            SourceType.OFFICIAL_REGISTRY: 4,
            SourceType.COMPANY_WEBSITE: 3,
            SourceType.NEWS_REPORT: 2,
            SourceType.PUBLIC_SEARCH: 1,
            SourceType.OTHER: 0,
        }[evidence.source_type]

    def _parse_publication_date(self, value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
