import json
import unittest
from pathlib import Path

from roster_scout.institution_registry import (
    ambiguous_root_domains,
    identity_for_domain,
    identity_for_verified_email,
    organization_routing_domain,
)


class InstitutionRegistryTests(unittest.TestCase):
    def test_unique_school_root_personalizes_all_of_its_subdomains(self):
        identity = identity_for_verified_email("coach@athletics.lsu.edu")

        self.assertIsNotNone(identity)
        self.assertEqual(identity.domain, "lsu.edu")
        self.assertEqual(identity.school_name, "Louisiana State")
        self.assertEqual(
            identity.logo_url,
            "https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/lsu.svg",
        )
        self.assertEqual(
            organization_routing_domain("athletics.lsu.edu", "lsu.edu"),
            "lsu.edu",
        )

    def test_shared_university_system_root_never_guesses_a_school(self):
        self.assertIn("psu.edu", ambiguous_root_domains())
        self.assertIsNone(identity_for_domain("psu.edu"))
        self.assertIsNone(identity_for_domain("athletics.psu.edu"))
        self.assertEqual(
            organization_routing_domain("athletics.psu.edu", "psu.edu"),
            "athletics.psu.edu",
        )

    def test_unknown_edu_remains_neutral_and_keeps_ordinary_root_routing(self):
        self.assertIsNone(identity_for_verified_email("coach@athletics.unknown.edu"))
        self.assertEqual(
            organization_routing_domain("athletics.unknown.edu", "unknown.edu"),
            "unknown.edu",
        )

    def test_registry_is_complete_unique_and_uses_only_approved_logo_sources(self):
        path = Path(__file__).resolve().parents[1] / "data" / "institution-domain-map.json"
        payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["season"], 2026)
        self.assertEqual(payload["schools_reviewed"], 942)
        self.assertTrue(set(payload["domains"]).isdisjoint(payload["ambiguous_domains"]))
        reviewed = len(payload["domains"]) + sum(
            len(schools) for schools in payload["ambiguous_domains"].values()
        )
        self.assertEqual(reviewed, payload["schools_reviewed"])
        for domain, school in payload["domains"].items():
            self.assertTrue(domain.endswith(".edu"), domain)
            self.assertEqual(school["domain"], domain)
            self.assertTrue(school["school_name"])
            self.assertTrue(school["evidence_url"].startswith("https://"))
            self.assertTrue(
                school["logo_url"].startswith("https://www.ncaa.com/")
                or school["logo_url"].startswith("/static/logos/"),
                school["logo_url"],
            )


if __name__ == "__main__":
    unittest.main()
