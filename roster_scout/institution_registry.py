"""Deterministic school identity and safe domain-routing helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "institution-domain-map.json"
)


@dataclass(frozen=True)
class InstitutionIdentity:
    domain: str
    school_name: str
    logo_url: str | None
    division: str
    stable_team_id: str | None
    season_team_id: str


@lru_cache(maxsize=1)
def _registry() -> tuple[dict[str, dict], frozenset[str]]:
    try:
        payload = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        domains = payload["domains"]
        ambiguous = payload["ambiguous_domains"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}, frozenset()
    if not isinstance(domains, dict) or not isinstance(ambiguous, dict):
        return {}, frozenset()
    return domains, frozenset(ambiguous)


def identity_for_domain(domain: str | None) -> InstitutionIdentity | None:
    """Return a school only when the institutional root has one exact match."""
    if not isinstance(domain, str):
        return None
    canonical = domain.casefold().strip().strip(".")
    labels = canonical.split(".")
    if len(labels) < 2 or labels[-1] != "edu":
        return None
    root_domain = ".".join(labels[-2:])
    domains, _ambiguous = _registry()
    record = domains.get(root_domain)
    if not isinstance(record, dict):
        return None
    school_name = record.get("school_name")
    season_team_id = record.get("season_team_id")
    division = record.get("division")
    if not all(isinstance(value, str) and value for value in (
        school_name,
        season_team_id,
        division,
    )):
        return None
    logo_url = record.get("logo_url")
    if not isinstance(logo_url, str) or not (
        logo_url.startswith("https://www.ncaa.com/")
        or logo_url.startswith("/static/logos/")
    ):
        logo_url = None
    stable_team_id = record.get("stable_team_id")
    return InstitutionIdentity(
        domain=root_domain,
        school_name=school_name,
        logo_url=logo_url,
        division=division,
        stable_team_id=(
            stable_team_id if isinstance(stable_team_id, str) else None
        ),
        season_team_id=season_team_id,
    )


def identity_for_verified_email(email: str | None) -> InstitutionIdentity | None:
    if not isinstance(email, str) or "@" not in email:
        return None
    return identity_for_domain(email.rsplit("@", 1)[1])


def organization_routing_domain(verified_domain: str, root_domain: str) -> str:
    """Keep shared university-system roots isolated by exact email domain.

    Ordinary athletics subdomains safely fold into their one-school .edu root.
    When more than one NCAA baseball program uses that root, folding would risk
    cross-campus report access, so the exact verified domain remains the tenant
    boundary and personalization stays neutral.
    """
    _domains, ambiguous = _registry()
    return verified_domain if root_domain in ambiguous else root_domain


def ambiguous_root_domains() -> frozenset[str]:
    return _registry()[1]
