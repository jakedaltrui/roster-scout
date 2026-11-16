"""Fail-closed contracts for detailed NCAA fielding evidence."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


FIXTURES = Path(__file__).parent / "fixtures" / "ncaa_detail"


def fixture(division: str) -> str:
    return (FIXTURES / division / "fielding.html").read_text(encoding="utf-8")


def fielding_api():
    return importlib.import_module("roster_scout.ncaa_detail")


class FieldingTests(unittest.TestCase):
    source_url = (
        "https://stats.ncaa.org/teams/615087/season_to_date_stats"
        "?year_stat_category_id=15869"
    )

    def parse(self, html: str, *, category_id: str = "15869"):
        return fielding_api().parse_fielding_page(
            html,
            source_url=self.source_url,
            stat_category_id=category_id,
        )

    def assert_failure(self, html: str, category: str):
        api = fielding_api()
        with self.assertRaises(api.FieldingParseError) as caught:
            self.parse(html)
        self.assertEqual(caught.exception.category, category)
        self.assertNotIn(self.source_url, str(caught.exception))

    def test_fielding_vertical_contract(self):
        """RED sentinel: imports compile and only the fielding module is absent."""
        self.assertIsNotNone(
            importlib.util.find_spec("roster_scout.ncaa_detail"),
            "NCAA fielding vertical slice is not implemented",
        )

    def test_d1_d2_d3_share_canonical_fields_and_preserve_source_identity(self):
        expected = {
            "games",
            "games_started",
            "putouts",
            "assists",
            "errors",
            "total_chances",
            "double_plays",
            "fielding_percentage",
            "passed_balls",
            "stolen_base_attempts",
            "caught_stealing_by",
        }
        for division, player_id in (("d1", "11001"), ("d2", "22001"), ("d3", "33001")):
            with self.subTest(division=division):
                section = self.parse(fixture(division))
                self.assertEqual(section.name, "fielding")
                self.assertFalse(section.verified_empty)
                self.assertEqual(section.source_url, self.source_url)
                self.assertEqual(section.stat_category_id, "15869")
                self.assertEqual(len(section.html_sha256), 64)
                self.assertEqual(section.rows[0].source_player_id, player_id)
                self.assertEqual(
                    section.rows[0].player_url,
                    f"https://stats.ncaa.org/players/{player_id}",
                )
                self.assertEqual(set(section.rows[0].canonical_values), expected)

    def test_optional_catcher_columns_are_nullable_and_sourced_zero_is_preserved(self):
        d2 = self.parse(fixture("d2")).rows[0].canonical_values
        self.assertIsNone(d2["passed_balls"])
        self.assertIsNone(d2["stolen_base_attempts"])
        self.assertIsNone(d2["caught_stealing_by"])

        d3 = self.parse(fixture("d3")).rows[0].canonical_values
        self.assertEqual(d3["passed_balls"], 0)
        self.assertEqual(d3["stolen_base_attempts"], 0)
        self.assertEqual(d3["caught_stealing_by"], 0)
        self.assertEqual(d3["fielding_percentage"], Decimal("1.000"))

    def test_rejects_missing_duplicate_or_renamed_required_headers(self):
        valid = fixture("d2")
        cases = (
            valid.replace("<th>TC</th>", ""),
            valid.replace("<th>PO</th>", "<th>PO</th><th>PO</th>"),
            valid.replace("<th>FPCT</th>", "<th>Fielding %</th>"),
        )
        for html in cases:
            with self.subTest(html=html[:80]):
                self.assert_failure(html, "schema_validation")

    def test_rejects_negative_counts_invalid_rates_and_mismatched_rows(self):
        valid = fixture("d2")
        cases = (
            valid.replace("<td>22</td>", "<td>-1</td>", 1),
            valid.replace("<td>.963</td>", "<td>1.001</td>", 1),
            valid.replace("<td>80</td><td>7</td>", "<td>80</td>", 1),
        )
        for html in cases:
            with self.subTest(html=html[-160:]):
                self.assert_failure(html, "schema_validation")

    def test_missing_blocked_or_bad_player_link_is_not_verified_empty(self):
        cases = (
            "<html><body>Access Denied</body></html>",
            "<html><body><p>No table here</p></body></html>",
            fixture("d2").replace("/players/22001", "https://example.test/players/22001"),
        )
        for html in cases:
            with self.subTest(html=html[:80]):
                self.assert_failure(html, "source_blocked" if "stat_grid" not in html else "schema_validation")

    def test_verified_empty_requires_a_valid_fielding_grid(self):
        empty = fixture("d2").replace(
            '<tr>\n          <td><a href="/players/22001">D Two Infielder</a></td>\n'
            '          <td>22</td><td>21</td><td>31</td><td>46</td><td>3</td>\n'
            '          <td>80</td><td>7</td><td>.963</td>\n        </tr>',
            "",
        )
        section = self.parse(empty)
        self.assertTrue(section.verified_empty)
        self.assertEqual(section.rows, ())

    def test_outbound_url_is_fixed_host_and_numeric_id_only(self):
        build = fielding_api().build_fielding_url
        self.assertEqual(build("615087", "15869"), self.source_url)
        for season_team_id, category_id in (
            ("615087@example.test", "15869"),
            ("615087", "15869&next=https://example.test"),
            ("https://example.test", "15869"),
            ("615087", ""),
        ):
            with self.subTest(season_team_id=season_team_id, category_id=category_id):
                with self.assertRaises(ValueError):
                    build(season_team_id, category_id)

    def test_shared_collector_fetches_discovered_fielding_id_on_fixed_endpoint(self):
        import roster_scraper as rs

        core_fixtures = Path(__file__).parent / "fixtures"
        pages = {
            "batting": (core_fixtures / "d1_batting.html").read_text(encoding="utf-8"),
            "pitching": (core_fixtures / "d1_pitching.html").read_text(encoding="utf-8"),
            "fielding": fixture("d1"),
        }
        season = {
            "season": "2026",
            "id": "17040",
            "batting_id": "15867",
            "pitching_id": "15868",
            "fielding_id": "15869",
        }
        team = {
            "team_id": "234",
            "team_name": "Florida St.",
            "division": "1",
            "year": "2026",
            "season_id": "17040",
            "season_team_id": "614606",
        }
        with patch.object(rs, "_find_ncaa_season", return_value=season), patch.object(
            rs, "_fetch_ncaa_pages", return_value=pages
        ) as fetch:
            collection = rs._collect_ncaa_team(team, 2026)

        urls = fetch.call_args.args[0]
        self.assertEqual(
            urls["fielding"],
            "https://stats.ncaa.org/teams/614606/season_to_date_stats"
            "?year_stat_category_id=15869",
        )
        self.assertEqual(
            {section.name for section in collection.sections},
            {"batting", "pitching", "fielding"},
        )
        self.assertEqual(collection.season.fielding_category_id, "15869")

    def test_canonical_fielding_fact_is_frozen_and_shares_authoritative_player(self):
        from roster_scraper import NcaaCollection, NcaaCollectionMetadata, NcaaSeason, NcaaTeam
        from roster_scout.canonical import canonicalize_ncaa_collection

        section = self.parse(fixture("d1"))
        collection = NcaaCollection(
            team=NcaaTeam("234", "Florida St.", "614606", "d1"),
            season=NcaaSeason("17040", 2026, "15867", "15868", "15869"),
            metadata=NcaaCollectionMetadata(section.collected_at),
            sections=(section.as_ncaa_section(),),
            players=(),
            team_label="Florida St.",
        )
        canonical = canonicalize_ncaa_collection(collection)
        fact = next(item for item in canonical.facts if item.section == "fielding")
        player = next(item for item in canonical.players if item.source_player_id == "11001")
        self.assertEqual(fact.identity_key, player.identity_key)
        self.assertEqual(fact.detailed_values["putouts"], 121)
        with self.assertRaises((AttributeError, TypeError)):
            replace(fact, section="batting").detailed_values["putouts"] = 0

    def test_fielding_model_uses_composite_owner_and_source_identity_constraints(self):
        from roster_scout.models import FieldingFact

        names = {constraint.name for constraint in FieldingFact.__table__.constraints}
        self.assertIn("fk_fielding_facts_snapshot_organization", names)
        self.assertIn("fk_fielding_facts_player_snapshot_organization", names)
        self.assertIn("fk_fielding_facts_source_identity", names)
        for column in (
            "games", "games_started", "putouts", "assists", "errors",
            "total_chances", "double_plays", "passed_balls",
            "stolen_base_attempts", "caught_stealing_by",
        ):
            self.assertIn(f"ck_fielding_facts_{column}_nonnegative", names)

    def test_fixtures_are_sanitized(self):
        forbidden = ("token=", "cookie", "authorization", "password", "customer")
        for division in ("d1", "d2", "d3"):
            text = fixture(division).casefold()
            with self.subTest(division=division):
                self.assertFalse(any(value in text for value in forbidden))


if __name__ == "__main__":
    unittest.main()
