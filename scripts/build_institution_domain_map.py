#!/usr/bin/env python3
"""Build the reviewed NCAA school-domain personalization registry.

The NCAA Membership Directory is joined to Roster Scout's current baseball
catalog by stable NCAA organization ID. A short override table covers current
programs whose stable ID is absent from, or no longer present in, the directory
feed. Root domains shared by multiple baseball programs are kept explicitly
ambiguous and are never used for school-name or logo personalization.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from roster_scraper import list_ncaa_teams


DIRECTORY_URL = "https://web3.ncaa.org/directory/api/directory/memberList?type=12"
DIRECTORY_DETAIL_URL = "https://web3.ncaa.org/directory/orgDetail?id={org_id}"

# Current programs with no usable stable team ID in the baseball lookup are
# tied to an exact NCAA directory organization after manual review.
DIRECTORY_ID_OVERRIDES = {
    ("d1", "Mercyhurst"): "408",
    ("d1", "New Haven"): "470",
    ("d1", "West Ga."): "766",
    ("d2", "UAH"): "10",
    ("d2", "UCCS"): "9754",
    ("d2", "Ferrum"): "225",
    ("d2", "Georgian Court"): "9004",
    ("d2", "Holy Family"): "1126",
    ("d2", "Jamestown"): "30267",
    ("d2", "Middle Georgia"): "30272",
    ("d2", "Point Park"): "30268",
    ("d2", "Saint Anselm"): "593",
    ("d2", "Texas-Dallas"): "23121",
    ("d2", "UT Permian Basin"): "30088",
    ("d3", "Gustavus Adolphus"): "266",
    ("d3", "Illinois Tech"): "30172",
    ("d3", "JWU Charlotte"): "30245",
    ("d3", "Lyon"): "22989",
    ("d3", "UMSV"): "451",
    ("d3", "Notre Dame (MD)"): "8600",
    ("d3", "Penn St. Brandywine"): "30189",
    ("d3", "Regent"): "30264",
    ("d3", "Saint Joseph (CT)"): "8687",
    ("d3", "SUNY Cobleskill"): "30083",
    ("d3", "UW-River Falls"): "801",
}

# These four schools are absent from the current membership-directory feed.
# Their canonical .edu domains are sourced from their official institution
# homepages. The NCAA logo remains tied to the exact current baseball catalog.
OFFICIAL_SITE_OVERRIDES = {
    ("d3", "Anna Maria"): ("annamaria.edu", "https://annamaria.edu/"),
    ("d3", "New Jersey City"): ("njcu.edu", "https://www.njcu.edu/"),
    ("d3", "Rosemont"): ("rosemont.edu", "https://www.rosemont.edu/"),
    ("d3", "Valley Forge"): ("uvf.edu", "https://www.uvf.edu/"),
}

NCAA_LOGO_OVERRIDES = {
    ("d2", "Vanguard"): (
        "https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/vanguard.svg"
    ),
    ("d3", "MUW"): (
        "https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/muw.svg"
    ),
}


def _root_edu_domain(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
        candidate = f"https://{candidate}"
    hostname = (urlparse(candidate).hostname or "").casefold().strip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    labels = hostname.split(".")
    if len(labels) < 2 or labels[-1] != "edu":
        return None
    return ".".join(labels[-2:])


def _directory_records(path: Path | None) -> list[dict]:
    if path is not None:
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        request = Request(
            DIRECTORY_URL,
            headers={"User-Agent": "Roster Scout domain registry builder"},
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed URL
            payload = json.load(response)
    if not isinstance(payload, list):
        raise RuntimeError("NCAA membership directory returned an unexpected schema")
    return payload


def build_registry(*, year: int, directory_path: Path | None = None) -> dict:
    directory = {
        str(record.get("orgId")): record
        for record in _directory_records(directory_path)
        if record.get("orgId") is not None
    }
    schools = []
    for division in ("d1", "d2", "d3"):
        for team in list_ncaa_teams(division, year):
            key = (division, team["name"])
            stable_team_id = str(team.get("team_id") or "").strip()
            org_id = DIRECTORY_ID_OVERRIDES.get(key, stable_team_id)
            record = directory.get(org_id)
            if key in OFFICIAL_SITE_OVERRIDES:
                domain, evidence_url = OFFICIAL_SITE_OVERRIDES[key]
                directory_org_id = None
            elif record is not None:
                domain = _root_edu_domain(record.get("webSiteUrl"))
                evidence_url = DIRECTORY_DETAIL_URL.format(org_id=org_id)
                directory_org_id = org_id
            else:
                raise RuntimeError(
                    f"No authoritative domain evidence for {division} {team['name']}"
                )
            if not domain:
                raise RuntimeError(
                    f"Official source does not expose a .edu domain for {division} {team['name']}"
                )
            schools.append({
                "domain": domain,
                "school_name": team["display_name"],
                "ncaa_catalog_name": team["name"],
                "division": division,
                "stable_team_id": stable_team_id or None,
                "season_team_id": str(team["season_team_id"]),
                "logo_url": NCAA_LOGO_OVERRIDES.get(key, team.get("logo_url")),
                "evidence_url": evidence_url,
                "directory_org_id": directory_org_id,
            })

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for school in schools:
        by_domain[school["domain"]].append(school)

    domains = {}
    ambiguous_domains = {}
    for domain in sorted(by_domain):
        matches = sorted(
            by_domain[domain],
            key=lambda item: (item["division"], item["school_name"].casefold()),
        )
        if len(matches) == 1:
            domains[domain] = matches[0]
        else:
            ambiguous_domains[domain] = [
                {
                    "school_name": item["school_name"],
                    "ncaa_catalog_name": item["ncaa_catalog_name"],
                    "division": item["division"],
                    "evidence_url": item["evidence_url"],
                }
                for item in matches
            ]

    return {
        "schema_version": 1,
        "season": year,
        "source_url": DIRECTORY_URL,
        "schools_reviewed": len(schools),
        "domains": domains,
        "ambiguous_domains": ambiguous_domains,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--directory-json", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/institution-domain-map.json"),
    )
    args = parser.parse_args()
    registry = build_registry(year=args.year, directory_path=args.directory_json)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(registry['domains'])} deterministic domains; "
        f"{len(registry['ambiguous_domains'])} shared roots remain neutral."
    )


if __name__ == "__main__":
    main()
