"""Fail-closed contracts for detailed NCAA fielding evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import requests

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

    def test_live_fielding_headers_and_empty_spacer_rows_are_supported(self):
        live = """
        <table id="stat_grid">
          <thead><tr>
            <th>#</th><th>Player</th><th>Yr</th><th>Pos</th><th>Ht</th><th>B/T</th>
            <th>GP</th><th>GS</th><th>PO</th><th>A</th><th>TC</th><th>E</th>
            <th>FldPct</th><th>CI</th><th>PB</th><th>SBA</th><th>CSB</th>
            <th>IDP</th><th>TP</th><th>SBAPct</th>
          </tr></thead>
          <tbody>
            <tr>
              <td>12</td><td><a href="/players/11001">D One Catcher</a></td>
              <td>Jr</td><td>C</td><td>6-0</td><td>R/R</td>
              <td>40</td><td>39</td><td>121</td><td>14</td><td>137</td><td>2</td>
              <td>.985</td><td>0</td><td>1</td><td>8</td><td>4</td>
              <td>12</td><td>0</td><td>.750</td>
            </tr>
            <tr><td></td></tr>
          </tbody>
        </table>
        """

        section = self.parse(live)

        self.assertEqual(section.rows[0].canonical_values["games"], 40)
        self.assertEqual(section.rows[0].canonical_values["double_plays"], 12)
        self.assertEqual(
            section.rows[0].canonical_values["fielding_percentage"],
            Decimal(".985"),
        )

    def test_valid_fielding_grid_wins_over_unrelated_access_denied_copy(self):
        html = fixture("d1") + (
            '<template id="generic-error-copy">Access Denied</template>'
        )

        section = self.parse(html)

        self.assertFalse(section.verified_empty)
        self.assertEqual(section.rows[0].source_player_id, "11001")

    def test_live_player_links_may_repeat_the_exact_fielding_category(self):
        html = fixture("d1").replace(
            'href="/players/11001"',
            'href="/players/11001?year_stat_category_id=15869"',
        )

        section = self.parse(html)

        self.assertEqual(section.rows[0].source_player_id, "11001")
        for unsafe in (
            "year_stat_category_id=15870",
            "year_stat_category_id=15869&amp;next=https://example.com",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(fielding_api().FieldingParseError):
                    self.parse(html.replace("year_stat_category_id=15869", unsafe))

    def test_live_opponent_totals_row_is_not_treated_as_a_player(self):
        html = fixture("d1").replace(
            "</tbody>",
            "<tr><td>Opponent Totals</td><td>40</td><td>40</td>"
            "<td>100</td><td>20</td><td>5</td><td>125</td><td>0</td>"
            "<td>.960</td><td>0</td><td>0</td><td>0</td></tr></tbody>",
        )

        section = self.parse(html)

        self.assertEqual(len(section.rows), len(self.parse(fixture("d1")).rows))

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

    def test_verified_empty_requires_source_authored_empty_marker(self):
        header_only = fixture("d2").replace(
            '<tr>\n          <td><a href="/players/22001">D Two Infielder</a></td>\n'
            '          <td>22</td><td>21</td><td>31</td><td>46</td><td>3</td>\n'
            '          <td>80</td><td>7</td><td>.963</td>\n        </tr>',
            "",
        )
        self.assert_failure(header_only, "schema_validation")

        explicit_empty = header_only.replace(
            "<tbody>",
            '<tbody><tr><td colspan="9">No statistics available</td></tr>',
        )
        section = self.parse(explicit_empty)
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


class FixtureCaptureSafetyTests(unittest.TestCase):
    """The live fixture utility must stay inside a narrow source/file boundary."""

    @staticmethod
    def capture_api():
        return importlib.import_module("scripts.capture_phase3_ncaa_fixtures")

    def test_output_is_confined_to_the_requested_division_fixture_root(self):
        api = self.capture_api()
        root = Path(__file__).parent / "fixtures" / "ncaa_detail"
        accepted = api.validate_capture_request(
            season_id="17040",
            season_team_id="614606",
            division="1",
            output_dir=root / "d1",
            limits=api.CaptureLimits(),
        )
        self.assertEqual(accepted.output_dir, (root / "d1").resolve())
        for output_dir in (
            root / "d2",
            root / "d1" / ".." / "..",
            Path(tempfile.gettempdir()) / "ncaa-detail",
        ):
            with self.subTest(output_dir=output_dir), self.assertRaises(ValueError):
                api.validate_capture_request(
                    season_id="17040",
                    season_team_id="614606",
                    division="1",
                    output_dir=output_dir,
                    limits=api.CaptureLimits(),
                )

    def test_source_urls_are_fixed_host_numeric_paths(self):
        api = self.capture_api()
        self.assertEqual(
            api.build_ncaa_url("614606", "15867"),
            "https://stats.ncaa.org/teams/614606/season_to_date_stats"
            "?year_stat_category_id=15867",
        )
        for team_id, category_id in (
            ("614606@example.test", "15867"),
            ("614606", "15867&token=secret"),
            ("https://example.test", "15867"),
            ("614606", ""),
        ):
            with self.subTest(team_id=team_id, category_id=category_id):
                with self.assertRaises(ValueError):
                    api.build_ncaa_url(team_id, category_id)

    @staticmethod
    def _schedule_catalog():
        return ({
            "team_id": "234",
            "season_team_id": "614606",
            "conference_id": "821",
            "division": "1",
            "year": "2026",
        }, {
            "team_id": "235",
            "season_team_id": "614607",
            "conference_id": "821",
            "division": "1",
            "year": "2026",
        })

    @staticmethod
    def _raw_schedule(*, total="1", result="W 4-2", opponent="vs. Opponent", extra=""):
        return f"""
        <table data-total-games="{total}">
          <thead><tr><th>Date</th><th>Opponent</th><th>Result</th></tr></thead>
          <tbody>
            <tr data-venue="neutral"><td>03/01/2026</td>
              <td><a href="/teams/614607">{opponent}</a></td>
              <td><a href="/contests/99">{result}</a></td></tr>
            {extra}
          </tbody>
        </table>
        """

    def test_schedule_capture_proves_completeness_and_preserves_neutral_site(self):
        api = self.capture_api()
        sanitized = api.sanitize_schedule(
            self._raw_schedule(),
            season_id="17040",
            team_id="234",
            season_team_id="614606",
            division="1",
            catalog=self._schedule_catalog(),
            captured_at="2026-11-16T00:00:00Z",
        )
        self.assertIn('data-schedule-complete="true"', sanitized)
        self.assertIn('data-venue="neutral"', sanitized)

        truncated = api.sanitize_schedule(
            self._raw_schedule(total="2"),
            season_id="17040",
            team_id="234",
            season_team_id="614606",
            division="1",
            catalog=self._schedule_catalog(),
            captured_at="2026-11-16T00:00:00Z",
        )
        self.assertIn('data-schedule-complete="false"', truncated)

        with_spacer = self._raw_schedule(
            extra='<tr><td></td></tr>'
        )
        sanitized = api.sanitize_schedule(
            with_spacer,
            season_id="17040",
            team_id="234",
            season_team_id="614606",
            division="1",
            catalog=self._schedule_catalog(),
            captured_at="2026-11-16T00:00:00Z",
        )
        self.assertEqual(sanitized.count("<game "), 1)

    def test_schedule_capture_rejects_pagination_unknown_results_and_malformed_rows(self):
        api = self.capture_api()
        for control in (
            '<a rel="next" href="?page=2">Next</a>',
            '<nav class="pagination"><span class="next"><a href="?page=2">Next</a></span></nav>',
            '<nav class="pagination"><a class="next" href="?page=2">Next</a></nav>',
            '<nav class="pagination"><a data-page="not-a-page">Next</a></nav>',
        ):
            with self.subTest(control=control):
                sanitized = api.sanitize_schedule(
                    self._raw_schedule() + control,
                    season_id="17040",
                    team_id="234",
                    season_team_id="614606",
                    division="1",
                    catalog=self._schedule_catalog(),
                    captured_at="2026-11-16T00:00:00Z",
                )
                self.assertIn('data-schedule-complete="false"', sanitized)
        for control in (
            '<nav class="pagination"><span class="next disabled"><a href="?page=2">Next</a></span></nav>',
            '<nav class="pagination"><span class="next"><a aria-disabled="true" href="?page=2">Next</a></span></nav>',
        ):
            with self.subTest(control=control):
                sanitized = api.sanitize_schedule(
                    self._raw_schedule() + control,
                    season_id="17040",
                    team_id="234",
                    season_team_id="614606",
                    division="1",
                    catalog=self._schedule_catalog(),
                    captured_at="2026-11-16T00:00:00Z",
                )
                self.assertIn('data-schedule-complete="true"', sanitized)
        for raw in (
            self._raw_schedule(result="mystery result"),
            self._raw_schedule(extra="<tr><td>03/02/2026</td></tr>"),
        ):
            with self.subTest(raw=raw[-80:]), self.assertRaises(api.CaptureUnavailable):
                api.sanitize_schedule(
                    raw,
                    season_id="17040",
                    team_id="234",
                    season_team_id="614606",
                    division="1",
                    catalog=self._schedule_catalog(),
                    captured_at="2026-11-16T00:00:00Z",
                )

    def test_control_sanitizer_removes_secrets_scripts_and_unrelated_people(self):
        api = self.capture_api()
        raw = """
        <html><head><script>fetch('wss://production.browserless.io?token=top-secret')</script></head>
        <body data-cookie="session=secret">
          <p>coach@example.edu</p>
          <select name="split_id" onchange="steal()">
            <option value="991">vs. Left-Handed Pitchers</option>
            <option value="992">Bases Empty</option>
          </select>
          <a href="/teams/614606/season_to_date_stats?year_stat_category_id=15867&amp;split_id=991&amp;token=secret">
            Vs LHP
          </a>
          <div>Unrelated Player Name</div>
        </body></html>
        """
        sanitized = api.sanitize_split_controls(
            raw,
            season_id="17040",
            season_team_id="614606",
            division="1",
            stat_type="batting",
            source_path="/teams/614606/season_to_date_stats",
        )
        lowered = sanitized.casefold()
        for forbidden in (
            "top-secret", "token=", "cookie", "script", "onchange",
            "browserless", "coach@example.edu", "unrelated player name",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)
        self.assertIn("vs_lhp", sanitized)
        self.assertIn("991", sanitized)
        self.assertIn("bases_empty", sanitized)

    def test_limits_are_rejected_before_external_capture_is_called(self):
        api = self.capture_api()
        root = Path(__file__).parent / "fixtures" / "ncaa_detail" / "d1"
        with patch("roster_scraper._fetch_ncaa_pages") as fetch:
            with self.assertRaises(ValueError):
                api.capture_controls(
                    season_id="17040",
                    season_team_id="614606",
                    division="1",
                    output_dir=root,
                    limits=api.CaptureLimits(max_pages=0),
                )
        fetch.assert_not_called()

    def test_schedule_transport_uses_the_smaller_navigation_or_wall_timeout(self):
        api = self.capture_api()
        reserved = []
        deadline = time.monotonic() + 2
        with patch.dict(
            os.environ,
            {
                "BROWSERLESS_TOKEN": "test-only-token",
                "NCAA_BROWSER_PROVIDER": "browserless",
            },
            clear=False,
        ), patch.object(
            api.requests,
            "post",
            side_effect=requests.Timeout("fixture timeout"),
        ) as post:
            with self.assertRaises(api.CaptureUnavailable):
                api._bounded_schedule_fetch(
                    "https://stats.ncaa.org/team/234/17040",
                    api.CaptureLimits(max_pages=1, timeout_seconds=5),
                    request_reserver=reserved.append,
                    deadline_monotonic=deadline,
                )

        self.assertEqual(reserved, ["schedule"])
        self.assertLessEqual(post.call_args.kwargs["timeout"], 2)

    def test_terms_retry_reserves_the_followup_navigation(self):
        import roster_scraper as scraper

        checkbox = Mock()
        checkbox.count.return_value = 1
        button = Mock()
        button.count.return_value = 1
        button.is_enabled.return_value = True
        page = Mock()
        page.url = "https://stats.ncaa.org/stats_terms"
        page.locator.side_effect = lambda selector: {
            "#terms_accepted": checkbox,
            "#stats-access-button": button,
        }[selector]
        reserved = []
        target = "https://stats.ncaa.org/teams/614606/season_to_date_stats"

        with patch.dict(
            os.environ, {"NCAA_TERMS_DWELL_MILLISECONDS": "0"}, clear=False
        ):
            scraper._accept_ncaa_terms(
                page,
                target,
                request_reserver=lambda: reserved.append("batting"),
                timeout_seconds=5,
            )

        self.assertEqual(reserved, ["batting"])
        page.goto.assert_called_once_with(
            target, wait_until="domcontentloaded", timeout=5000
        )

    def test_player_transport_accepts_preloaded_first_url(self):
        api = self.capture_api()
        player_id = "9698671"
        urls = {
            key: api.build_player_url(player_id, category)
            for key, category in {
                "batting": "15867",
                "pitching": "15868",
                "fielding": "15869",
            }.items()
        }
        page = Mock()
        page.url = urls["batting"]
        page.content.side_effect = lambda: f"<html>{page.url}</html>"
        page.goto.side_effect = lambda url, **_kwargs: setattr(page, "url", url)
        browser = Mock()
        browser.contexts = [Mock(pages=[page])]
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = Mock()
        starter.start.return_value = playwright
        response = Mock(status_code=200)
        response.json.return_value = {
            "browserWSEndpoint": "wss://example.test/browser"
        }
        reserved = []

        with patch.dict(os.environ, {
            "BROWSERLESS_TOKEN": "test-only-token",
            "NCAA_BROWSER_PROVIDER": "browserless",
        }, clear=False), patch.object(
            api.requests, "post", return_value=response
        ), patch(
            "patchright.sync_api.sync_playwright", return_value=starter
        ):
            pages = api._bounded_player_fetch(
                urls,
                player_id,
                api.CaptureLimits(max_pages=3, timeout_seconds=5),
                request_reserver=reserved.append,
                deadline_monotonic=time.monotonic() + 30,
            )

        self.assertEqual(set(pages), set(urls))
        self.assertEqual(
            reserved,
            ["history:batting", "history:pitching", "history:fielding"],
        )
        self.assertEqual(page.goto.call_count, 2)

    def test_player_batch_reuses_one_terms_session_across_players(self):
        api = self.capture_api()
        players = ("9698671", "9679575")
        player_urls = {
            player_id: {
                "batting": api.build_player_url(player_id, "15867")
            }
            for player_id in players
        }
        page = Mock()
        page.url = player_urls[players[0]]["batting"]
        page.content.side_effect = lambda: f"<html>{page.url}</html>"
        page.goto.side_effect = lambda url, **_kwargs: setattr(page, "url", url)
        browser = Mock()
        browser.contexts = [Mock(pages=[page])]
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = Mock()
        starter.start.return_value = playwright
        response = Mock(status_code=200)
        response.json.return_value = {
            "browserWSEndpoint": "wss://example.test/browser"
        }
        post = Mock(return_value=response)
        reserved = []

        with patch.dict(os.environ, {
            "BROWSERLESS_TOKEN": "test-only-token",
            "NCAA_BROWSER_PROVIDER": "browserless",
        }, clear=False), patch.object(
            api.requests, "post", post
        ), patch(
            "patchright.sync_api.sync_playwright", return_value=starter
        ):
            pages = api._bounded_player_batch_fetch(
                player_urls,
                api.CaptureLimits(max_pages=1, timeout_seconds=5),
                request_reserver=lambda player_id, stat_type: reserved.append(
                    (player_id, stat_type)
                ),
                deadline_monotonic=time.monotonic() + 30,
            )

        self.assertEqual(set(pages), set(players))
        self.assertEqual(
            reserved,
            [(players[0], "batting"), (players[1], "batting")],
        )
        self.assertEqual(page.goto.call_count, 1)
        self.assertEqual(post.call_count, 1)

    def test_terms_dwell_stops_at_absolute_build_deadline(self):
        import roster_scraper as scraper
        from roster_scout.collection_control import CollectionDeadlineExceeded

        clock = [100.0]
        checkbox = Mock()
        checkbox.count.return_value = 1
        button = Mock()
        button.count.return_value = 1
        page = Mock()
        page.url = "https://stats.ncaa.org/stats_terms"
        page.locator.side_effect = lambda selector: {
            "#terms_accepted": checkbox,
            "#stats-access-button": button,
        }[selector]
        page.wait_for_timeout.side_effect = (
            lambda milliseconds: clock.__setitem__(
                0, clock[0] + milliseconds / 1000
            )
        )

        with patch.dict(
            os.environ, {"NCAA_TERMS_DWELL_MILLISECONDS": "8000"}, clear=False
        ), patch.object(scraper.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(CollectionDeadlineExceeded):
                scraper._accept_ncaa_terms(
                    page,
                    "https://stats.ncaa.org/teams/614606/season_to_date_stats",
                    timeout_seconds=45,
                    deadline_monotonic=103.0,
                )

        self.assertEqual(clock[0], 103.0)
        checkbox.check.assert_not_called()
        page.goto.assert_not_called()

    def test_terms_locator_action_receives_exact_remaining_timeout(self):
        import roster_scraper as scraper
        from roster_scout.collection_control import CollectionDeadlineExceeded

        clock = [200.0]
        configured = []
        page = Mock()
        page.url = "https://stats.ncaa.org/stats_terms"
        page.set_default_timeout.side_effect = configured.append
        checkbox = Mock()
        button = Mock()

        def blocked_count():
            clock[0] += configured[-1] / 1000
            return 1

        checkbox.count.side_effect = blocked_count
        button.count.return_value = 1
        page.locator.side_effect = lambda selector: {
            "#terms_accepted": checkbox,
            "#stats-access-button": button,
        }[selector]

        with patch.object(scraper.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(CollectionDeadlineExceeded):
                scraper._accept_ncaa_terms(
                    page,
                    "https://stats.ncaa.org/teams/614606/season_to_date_stats",
                    timeout_seconds=45,
                    deadline_monotonic=203.0,
                )

        self.assertEqual(configured, [3000, 3000, 3000])
        self.assertEqual(clock[0], 203.0)
        button.count.assert_not_called()
        checkbox.check.assert_not_called()

    def test_schedule_boundaries_preserve_deadline_exhaustion(self):
        api = self.capture_api()
        from roster_scout.collection_control import CollectionDeadlineExceeded

        source_url = "https://stats.ncaa.org/team/234/17040"
        for stage in ("post", "cdp", "content"):
            with self.subTest(stage=stage):
                clock = [100.0]
                response = Mock(status_code=200)
                response.json.return_value = {
                    "browserWSEndpoint": "wss://example.test/browser"
                }
                page = Mock()
                page.url = source_url
                page.content.return_value = self._raw_schedule()
                browser = Mock()
                browser.contexts = [Mock(pages=[page])]
                playwright = Mock()
                playwright.chromium.connect_over_cdp.return_value = browser
                starter = Mock()
                starter.start.return_value = playwright

                def expire(*_args, error_type=RuntimeError, **_kwargs):
                    clock[0] = 105.0
                    raise error_type("blocked boundary")

                if stage == "post":
                    post = Mock(
                        side_effect=lambda *_a, **_k: expire(
                            error_type=requests.Timeout
                        )
                    )
                else:
                    post = Mock(return_value=response)
                if stage == "cdp":
                    playwright.chromium.connect_over_cdp.side_effect = expire
                if stage == "content":
                    page.content.side_effect = expire

                with patch.dict(os.environ, {
                    "BROWSERLESS_TOKEN": "test-only-token",
                    "NCAA_BROWSER_PROVIDER": "browserless",
                }, clear=False), patch.object(
                    api.time, "monotonic", side_effect=lambda: clock[0]
                ), patch.object(
                    api.requests, "post", post
                ), patch(
                    "patchright.sync_api.sync_playwright",
                    return_value=starter,
                ):
                    with self.assertRaises(CollectionDeadlineExceeded):
                        api._bounded_schedule_fetch(
                            source_url,
                            api.CaptureLimits(max_pages=1, timeout_seconds=30),
                            deadline_monotonic=105.0,
                        )

                self.assertEqual(clock[0], 105.0)

    def test_player_boundaries_preserve_deadline_exhaustion(self):
        api = self.capture_api()
        from roster_scout.collection_control import CollectionDeadlineExceeded

        player_id = "9698671"
        urls = {
            key: api.build_player_url(player_id, category)
            for key, category in {
                "batting": "15867",
                "pitching": "15868",
                "fielding": "15869",
            }.items()
        }
        for stage in ("post", "cdp", "navigation", "selector", "content"):
            with self.subTest(stage=stage):
                clock = [200.0]
                response = Mock(status_code=200)
                response.json.return_value = {
                    "browserWSEndpoint": "wss://example.test/browser"
                }
                page = Mock()
                page.url = urls["batting"]
                page.content.return_value = "<html>player detail</html>"
                page.goto.side_effect = (
                    lambda url, **_kwargs: setattr(page, "url", url)
                )
                browser = Mock()
                browser.contexts = [Mock(pages=[page])]
                playwright = Mock()
                playwright.chromium.connect_over_cdp.return_value = browser
                starter = Mock()
                starter.start.return_value = playwright

                def expire(*_args, error_type=RuntimeError, **_kwargs):
                    clock[0] = 205.0
                    raise error_type("blocked boundary")

                if stage == "post":
                    post = Mock(
                        side_effect=lambda *_a, **_k: expire(
                            error_type=requests.Timeout
                        )
                    )
                else:
                    post = Mock(return_value=response)
                if stage == "cdp":
                    playwright.chromium.connect_over_cdp.side_effect = expire
                if stage == "navigation":
                    page.goto.side_effect = expire
                if stage == "selector":
                    page.wait_for_selector.side_effect = expire
                if stage == "content":
                    page.content.side_effect = expire

                with patch.dict(os.environ, {
                    "BROWSERLESS_TOKEN": "test-only-token",
                    "NCAA_BROWSER_PROVIDER": "browserless",
                }, clear=False), patch.object(
                    api.time, "monotonic", side_effect=lambda: clock[0]
                ), patch.object(
                    api.requests, "post", post
                ), patch(
                    "patchright.sync_api.sync_playwright",
                    return_value=starter,
                ):
                    with self.assertRaises(CollectionDeadlineExceeded):
                        api._bounded_player_fetch(
                            urls,
                            player_id,
                            api.CaptureLimits(max_pages=3, timeout_seconds=30),
                            deadline_monotonic=205.0,
                        )

                self.assertEqual(clock[0], 205.0)

    def test_terms_navigation_boundaries_preserve_deadline_exhaustion(self):
        import roster_scraper as scraper
        from roster_scout.collection_control import CollectionDeadlineExceeded

        target = "https://stats.ncaa.org/teams/614606/season_to_date_stats"
        for stage in ("locator", "load_state", "navigation"):
            with self.subTest(stage=stage):
                clock = [300.0]
                checkbox = Mock()
                checkbox.count.return_value = 1
                button = Mock()
                button.count.return_value = 1
                button.is_enabled.return_value = True
                page = Mock()
                page.url = "https://stats.ncaa.org/stats_terms"
                page.locator.side_effect = lambda selector: {
                    "#terms_accepted": checkbox,
                    "#stats-access-button": button,
                }[selector]

                def expire(*_args, **_kwargs):
                    clock[0] = 305.0
                    raise RuntimeError("blocked boundary")

                if stage == "locator":
                    page.locator.side_effect = expire
                elif stage == "load_state":
                    page.wait_for_load_state.side_effect = expire
                else:
                    page.goto.side_effect = expire

                with patch.dict(
                    os.environ, {"NCAA_TERMS_DWELL_MILLISECONDS": "0"}, clear=False
                ), patch.object(
                    scraper.time, "monotonic", side_effect=lambda: clock[0]
                ):
                    with self.assertRaises(CollectionDeadlineExceeded):
                        scraper._accept_ncaa_terms(
                            page,
                            target,
                            timeout_seconds=30,
                            deadline_monotonic=305.0,
                        )

                self.assertEqual(clock[0], 305.0)

    def test_split_control_boundaries_preserve_deadline_exhaustion(self):
        import roster_scraper as scraper
        from roster_scout.collection_control import CollectionDeadlineExceeded

        batting = """
        <table id="stat_grid"><thead><tr><th>Player</th><th>AB</th><th>H</th>
        </tr></thead><tbody></tbody></table>
        <button class="sport_stat_menu_option_4" data-sport-stat-id="4"
          data-split-id="4">
          vs. Left-Handed Pitchers
        </button>
        """
        pitching = """
        <table id="stat_grid"><thead><tr><th>Player</th><th>IP</th><th>ERA</th>
        </tr></thead><tbody></tbody></table>
        """
        team = {
            "season_id": "17040", "season_team_id": "614606", "division": "1"
        }
        season = {"id": "17040", "batting_id": "15867", "pitching_id": "15868"}

        for stage in (
            "locator", "count", "attribute", "label", "click", "content"
        ):
            with self.subTest(stage=stage):
                clock = [400.0]
                control = Mock()
                control.count.return_value = 1
                control.get_attribute.return_value = "4"
                control.inner_text.return_value = "vs. Left-Handed Pitchers"
                page = Mock()
                page.locator.return_value = control
                page.content.return_value = batting

                def expire(*_args, **_kwargs):
                    clock[0] = 405.0
                    raise RuntimeError("blocked boundary")

                target = {
                    "locator": page.locator,
                    "count": control.count,
                    "attribute": control.get_attribute,
                    "label": control.inner_text,
                    "click": control.evaluate,
                    "content": page.content,
                }[stage]
                target.side_effect = expire
                fetch_calls = [0]

                def transport(urls, *, page_consumer=None, **_kwargs):
                    fetch_calls[0] += 1
                    if page_consumer is None:
                        return {"batting": batting, "pitching": pitching}
                    key = next(iter(urls))
                    page_consumer(
                        key=key,
                        page=page,
                        source_url=urls[key],
                        html=batting,
                    )
                    return {key: batting}

                with patch.object(
                    scraper.time, "monotonic", side_effect=lambda: clock[0]
                ), patch.object(
                    scraper, "_find_ncaa_team_by_season_id", return_value=team
                ), patch.object(
                    scraper, "_find_ncaa_season", return_value=season
                ), patch.object(
                    scraper, "_fetch_ncaa_pages", side_effect=transport
                ):
                    with self.assertRaises(CollectionDeadlineExceeded):
                        scraper.collect_ncaa_split_results(
                            "614606",
                            division=1,
                            year=2026,
                            navigation_timeout_seconds=30,
                            deadline_monotonic=405.0,
                        )

                self.assertEqual(fetch_calls[0], 3)
                self.assertEqual(clock[0], 405.0)

    def test_manifest_is_allowlisted_sorted_and_contains_no_raw_source(self):
        api = self.capture_api()
        manifest = api.build_fixture_manifest(
            division="1",
            season_id="17040",
            season_team_id="614606",
            captured_at="2026-11-16T00:00:00Z",
            controls_sha256="a" * 64,
            control_sources=[
                {
                    "normalized_headers": ["player", "ab", "h"],
                    "source_path": "/teams/614606/season_to_date_stats",
                    "source_stat_category_id": "15867",
                    "stat_type": "batting",
                },
                {
                    "normalized_headers": ["player", "ip", "era"],
                    "source_path": "/teams/614606/season_to_date_stats",
                    "source_stat_category_id": "15868",
                    "stat_type": "pitching",
                },
            ],
            observations=[{
                "stat_type": "batting",
                "canonical_label": "vs_lhp",
                "status": "candidate",
                "identifier": "991",
                "source_path": "/teams/614606/season_to_date_stats",
                "source_stat_category_id": "15867",
                "raw_html": "must-not-survive",
                "token": "must-not-survive",
            }],
        )
        encoded = json.dumps(manifest, sort_keys=True)
        self.assertNotIn("raw_html", encoded)
        self.assertNotIn("must-not-survive", encoded)
        self.assertEqual(
            set(manifest),
            {
                "captured_at", "control_contract_version", "control_sources",
                "controls_sha256", "division", "observations", "sanitizer_version",
                "season_id", "season_team_id",
            },
        )
        self.assertEqual(
            set(manifest["observations"][0]),
            {
                "canonical_label", "identifier", "source_path",
                "source_stat_category_id", "stat_type", "status",
            },
        )

    def test_preflight_without_configuration_has_one_fixed_safe_failure(self):
        script = Path(__file__).parents[1] / "scripts" / "capture_phase3_ncaa_fixtures.py"
        env = dict(os.environ)
        env.pop("BROWSERLESS_TOKEN", None)
        env["NCAA_BROWSER_PROVIDER"] = "browserless"
        env["PHASE3_ALLOW_FLY_REMOTE"] = "0"
        result = subprocess.run(
            [sys.executable, str(script), "--preflight"],
            cwd=Path(__file__).parents[1],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "phase3_browserless_unavailable")


class SplitDiscoveryTests(unittest.TestCase):
    def test_live_counts_accept_only_well_formed_thousands_separators(self):
        api = fielding_api()
        self.assertEqual(api._parse_split_count("1,353"), 1353)
        self.assertEqual(api._parse_split_count("388"), 388)
        for value in ("13,53", "1 353", "1,353.0"):
            with self.subTest(value=value), self.assertRaises(api.SplitContractError):
                api._parse_split_count(value)

    """Live controls remain candidates until their exact source contract matches."""

    REPRESENTATIVES = {
        "d1": "614606",
        "d2": "615087",
        "d3": "615349",
    }
    REQUIRED_HEADERS = {
        "batting": {"player", "ab", "h"},
        "pitching": {"player", "ip", "era"},
    }

    @staticmethod
    def _load(division: str):
        fixture_dir = FIXTURES / division
        controls = (fixture_dir / "split_controls.html").read_text(encoding="utf-8")
        manifest = json.loads(
            (fixture_dir / "fixture_manifest.json").read_text(encoding="utf-8")
        )
        return manifest, controls

    def _validate_contract(self, manifest: dict, controls: str, division: str) -> None:
        api = FixtureCaptureSafetyTests.capture_api()
        self.assertEqual(manifest["control_contract_version"], api.CONTROL_CONTRACT_VERSION)
        self.assertEqual(manifest["sanitizer_version"], api.SANITIZER_VERSION)
        self.assertEqual(manifest["division"], division)
        self.assertEqual(manifest["season_id"], "17040")
        self.assertEqual(manifest["season_team_id"], self.REPRESENTATIVES[division])
        self.assertRegex(manifest["captured_at"], r"^2026-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(
            manifest["controls_sha256"],
            hashlib.sha256(controls.encode("utf-8")).hexdigest(),
        )

        sources = {item["stat_type"]: item for item in manifest["control_sources"]}
        self.assertEqual(set(sources), set(self.REQUIRED_HEADERS))
        for stat_type, required in self.REQUIRED_HEADERS.items():
            source = sources[stat_type]
            self.assertTrue(source["source_stat_category_id"].isdigit())
            self.assertEqual(
                source["source_path"],
                f"/teams/{self.REPRESENTATIVES[division]}/season_to_date_stats",
            )
            headers = source["normalized_headers"]
            self.assertTrue(required <= set(headers))
            self.assertTrue(all(headers.count(value) == 1 for value in required))

        expected = {
            (stat_type, label)
            for stat_type, labels in api.CANONICAL_LABELS.items()
            for label in labels
        }
        observations = {
            (item["stat_type"], item["canonical_label"]): item
            for item in manifest["observations"]
        }
        self.assertEqual(set(observations), expected)
        self.assertEqual(len(manifest["observations"]), len(expected))

        soup = __import__("bs4", fromlist=["BeautifulSoup"]).BeautifulSoup(
            controls, "html.parser"
        )
        root = soup.find("split-controls")
        self.assertIsNotNone(root)
        self.assertEqual(root.get("data-sanitizer-version"), api.SANITIZER_VERSION)
        sections = {item.get("data-stat-type"): item for item in root.find_all("section")}
        self.assertEqual(set(sections), set(self.REQUIRED_HEADERS))
        for stat_type, section in sections.items():
            self.assertEqual(section.get("data-season-id"), "17040")
            self.assertEqual(section.get("data-season-team-id"), self.REPRESENTATIVES[division])
            self.assertEqual(
                section.get("data-source-stat-category-id"),
                sources[stat_type]["source_stat_category_id"],
            )
            controls_by_label = {
                item.get("data-canonical-label"): item
                for item in section.find_all("control", recursive=False)
            }
            self.assertEqual(set(controls_by_label), set(api.CANONICAL_LABELS[stat_type]))
            for label, control in controls_by_label.items():
                observed = observations[(stat_type, label)]
                self.assertEqual(control.get("data-status"), observed["status"])
                self.assertEqual(control.get("data-identifier"), observed["identifier"])
                self.assertEqual(
                    observed["source_stat_category_id"],
                    sources[stat_type]["source_stat_category_id"],
                )
                if observed["status"] == "candidate":
                    self.assertRegex(observed["identifier"], r"^\d+$")
                else:
                    self.assertIn(
                        observed["status"],
                        {"source_not_published", "source_identifier_not_found"},
                    )
                    self.assertIsNone(observed["identifier"])

    def test_current_d1_d2_d3_control_contracts(self):
        for division in self.REPRESENTATIVES:
            with self.subTest(division=division):
                self._validate_contract(*self._load(division), division)

    def test_live_discovery_ignores_noisy_numeric_attributes_and_rejects_ambiguity(self):
        api = fielding_api()
        html = """
        <table id="stat_grid"><thead><tr><th>Player</th><th>AB</th><th>H</th></tr></thead></table>
        <button tabindex="7" data-stat-seq="123" aria-rowindex="9">Vs LHP</button>
        """
        scopes = api.discover_live_split_controls(
            html,
            season_id="17040",
            season_team_id="614606",
            stat_type="batting",
            source_stat_category_id="15867",
        )
        self.assertEqual(
            next(item for item in scopes if item.scope_code == "vs_lhp").source_identifier,
            "123",
        )

        live_html = """
        <table id="stat_grid"><thead><tr><th>Player</th><th>AB</th><th>H</th></tr></thead></table>
        <a class="sport_stat_menu_option_4" data-sport-stat-id="4"
           href="javascript:toggleSituationalStat(4);">Hits-AB vs Left Handed Pitchers</a>
        """
        live_scopes = api.discover_live_split_controls(
            live_html,
            season_id="17040",
            season_team_id="614606",
            stat_type="batting",
            source_stat_category_id="15867",
        )
        self.assertEqual(
            next(item for item in live_scopes if item.scope_code == "vs_lhp").source_identifier,
            "4",
        )
        unsafe_javascript = live_html.replace(
            "javascript:toggleSituationalStat(4);",
            "javascript:alert(document.cookie)",
        )
        unsafe_scopes = api.discover_live_split_controls(
            unsafe_javascript,
            season_id="17040",
            season_team_id="614606",
            stat_type="batting",
            source_stat_category_id="15867",
        )
        self.assertIsNone(
            next(
                item for item in unsafe_scopes if item.scope_code == "vs_lhp"
            ).source_identifier
        )
        two_out_html = """
        <table id="stat_grid"><thead><tr><th>Player</th><th>AB</th><th>H</th></tr></thead></table>
        <a data-sport-stat-id="1" href="javascript:toggleSituationalStat(1);">Hits-AB with 2 outs</a>
        <a data-sport-stat-id="7" href="javascript:toggleSituationalStat(7);">Succ-Opp to drive in a runner from 3rd &lt; 2 outs.</a>
        """
        two_out_scopes = api.discover_live_split_controls(
            two_out_html,
            season_id="17040",
            season_team_id="614606",
            stat_type="batting",
            source_stat_category_id="15867",
        )
        self.assertEqual(
            next(
                item for item in two_out_scopes
                if item.scope_code == "two_outs"
            ).source_identifier,
            "1",
        )
        ambiguous = html.replace(
            'data-stat-seq="123"',
            'data-stat-seq="123" data-split-id="124"',
        )
        with self.assertRaises(api.SplitContractError):
            api.discover_live_split_controls(
                ambiguous,
                season_id="17040",
                season_team_id="614606",
                stat_type="batting",
                source_stat_category_id="15867",
            )

    def test_capture_discovery_uses_only_documented_identifier_attributes(self):
        api = FixtureCaptureSafetyTests.capture_api()
        html = (
            '<button tabindex="7" data-stat-seq="123" aria-rowindex="9">'
            'Vs LHP</button>'
        )
        observations = api.discover_split_controls(
            html,
            season_id="17040",
            season_team_id="614606",
            stat_type="batting",
        )
        self.assertEqual(
            next(item for item in observations if item["canonical_label"] == "vs_lhp")["identifier"],
            "123",
        )
        with self.assertRaises(api.CaptureUnavailable):
            api.discover_split_controls(
                html.replace(
                    'data-stat-seq="123"',
                    'data-stat-seq="123" data-split-id="124"',
                ),
                season_id="17040",
                season_team_id="614606",
                stat_type="batting",
            )

    def test_rejects_stale_wrong_type_wrong_season_and_wrong_header_candidates(self):
        manifest, controls = self._load("d1")
        mutations = []

        stale = json.loads(json.dumps(manifest))
        stale["observations"][0]["identifier"] = "999999"
        mutations.append(("stale_identifier", stale, controls))

        wrong_type = json.loads(json.dumps(manifest))
        wrong_type["observations"][0]["stat_type"] = "pitching"
        mutations.append(("wrong_type", wrong_type, controls))

        wrong_season = json.loads(json.dumps(manifest))
        wrong_season["season_id"] = "16999"
        mutations.append(("wrong_season", wrong_season, controls))

        wrong_header = json.loads(json.dumps(manifest))
        wrong_header["control_sources"][0]["normalized_headers"] = [
            "athlete" if value == "player" else value
            for value in wrong_header["control_sources"][0]["normalized_headers"]
        ]
        mutations.append(("wrong_header", wrong_header, controls))

        for label, changed_manifest, changed_controls in mutations:
            with self.subTest(label=label), self.assertRaises(AssertionError):
                self._validate_contract(changed_manifest, changed_controls, "d1")

    def test_production_does_not_hard_code_discovered_split_ids(self):
        root = Path(__file__).parents[1]
        production = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                root / "roster_scraper.py",
                root / "roster_scout" / "ncaa_detail.py",
                root / "scripts" / "capture_phase3_ncaa_fixtures.py",
            )
        )
        for label in {
            item
            for labels in FixtureCaptureSafetyTests.capture_api().CANONICAL_LABELS.values()
            for item in labels
        }:
            with self.subTest(label=label):
                self.assertNotRegex(
                    production,
                    rf"[\"']{label}[\"']\s*:\s*[\"']?\d+",
                )

    def test_certified_result_grid_contract(self):
        api = FixtureCaptureSafetyTests.capture_api()
        representative_labels = {"batting": "vs_lhp", "pitching": "vs_lhb"}
        for division, team_id in self.REPRESENTATIVES.items():
            manifest, _controls = self._load(division)
            observations = {
                (item["stat_type"], item["canonical_label"]): item
                for item in manifest["observations"]
            }
            for stat_type, label in representative_labels.items():
                candidate = observations[(stat_type, label)]
                fixture_path = (
                    FIXTURES / division / f"{stat_type}_split_result.html"
                )
                with self.subTest(division=division, stat_type=stat_type):
                    text = fixture_path.read_text(encoding="utf-8")
                    certified = api.certify_result_fixture(
                        text,
                        expected_season_id="17040",
                        expected_season_team_id=team_id,
                        expected_division=division,
                        expected_stat_type=stat_type,
                        expected_canonical_label=label,
                        expected_identifier=candidate["identifier"],
                        expected_source_stat_category_id=(
                            candidate["source_stat_category_id"]
                        ),
                    )
                    self.assertIn(certified["status"], {"complete", "unavailable"})
                    if certified["status"] == "complete":
                        self.assertGreater(certified["row_count"], 0)
                        self.assertGreater(len(certified["normalized_headers"]), 1)
                        self.assertTrue(certified["player_links_valid"])
                    else:
                        self.assertIn(
                            certified["reason"],
                            {"source_not_published", "source_identifier_not_found"},
                        )

        valid = (FIXTURES / "d1" / "batting_split_result.html").read_text(
            encoding="utf-8"
        )
        expected = {
            "expected_season_id": "17040",
            "expected_season_team_id": "614606",
            "expected_division": "d1",
            "expected_stat_type": "batting",
            "expected_canonical_label": "vs_lhp",
            "expected_identifier": "4",
            "expected_source_stat_category_id": "15867",
        }
        mutations = {
            "wrong_season": valid.replace('data-season-id="17040"', 'data-season-id="16999"', 1),
            "wrong_type": valid.replace('data-stat-type="batting"', 'data-stat-type="pitching"', 1),
            "wrong_label": valid.replace('data-canonical-label="vs_lhp"', 'data-canonical-label="vs_rhp"', 1),
            "wrong_header": valid.replace("<th>Player</th>", "<th>Athlete</th>", 1),
            "malformed_row": valid.replace("</td>", "", 1),
            "invalid_player_link": valid.replace('/players/', 'https://example.test/players/', 1),
        }
        for label, changed in mutations.items():
            with self.subTest(rejection=label), self.assertRaises(ValueError):
                api.certify_result_fixture(changed, **expected)


class _SplitContractMixin:
    REPRESENTATIVES = {
        "d1": "614606",
        "d2": "615087",
        "d3": "615349",
    }
    CATEGORY_IDS = {"batting": "15867", "pitching": "15868"}
    SOURCE_LABELS = {
        "batting": {
            "vs_lhp": "vs_lhp",
            "vs_rhp": "vs_rhp",
            "runners_on": "runners_on",
            "bases_empty": "bases_empty",
            "bases_loaded": "bases_loaded",
            "risp": "runners_in_scoring_position",
            "two_outs": "two_outs",
        },
        "pitching": {
            "vs_lhb": "vs_lhb",
            "vs_rhb": "vs_rhb",
            "runners_on": "runners_on",
            "bases_empty": "bases_empty",
            "bases_loaded": "bases_loaded",
            "risp": "runners_in_scoring_position",
            "two_outs": "two_outs",
        },
    }

    def manifest(self, division="d1"):
        return json.loads(
            (FIXTURES / division / "fixture_manifest.json").read_text(encoding="utf-8")
        )

    def result_fixture(self, stat_type, division="d1"):
        return (
            FIXTURES / division / f"{stat_type}_split_result.html"
        ).read_text(encoding="utf-8")

    def discovery(self, stat_type, division="d1"):
        api = fielding_api()
        controls = (FIXTURES / division / "split_controls.html").read_text(
            encoding="utf-8"
        )
        return api.discover_split_controls(
            controls,
            manifest=self.manifest(division),
            expected_season_id="17040",
            expected_season_team_id=self.REPRESENTATIVES[division],
            expected_stat_type=stat_type,
            expected_source_stat_category_id=self.CATEGORY_IDS[stat_type],
        )

    def parse_result(self, stat_type, division="d1"):
        api = fielding_api()
        discovered = {
            scope.scope_code: scope for scope in self.discovery(stat_type, division)
        }
        representative = "vs_lhp" if stat_type == "batting" else "vs_lhb"
        return api.parse_split_result(
            self.result_fixture(stat_type, division),
            discovered_scope=discovered[representative],
        )


class BattingSplitTests(_SplitContractMixin, unittest.TestCase):
    CODES = (
        "vs_lhp", "vs_rhp", "runners_on", "bases_empty",
        "bases_loaded", "risp", "two_outs",
    )

    def test_scoped_split_persistence_contract(self):
        """RED sentinel: import/compile succeeds before the split API exists."""
        self.assertTrue(
            hasattr(fielding_api(), "parse_split_result"),
            "scoped split persistence is not implemented",
        )

    def test_enumerates_exactly_seven_canonical_batting_codes(self):
        api = fielding_api()
        self.assertEqual(api.split_codes("batting"), self.CODES)
        self.assertEqual(
            {scope.scope_code for scope in self.discovery("batting")},
            set(self.CODES),
        )

    def test_d1_d2_d3_certified_results_preserve_exact_batting_evidence(self):
        for division, team_id in self.REPRESENTATIVES.items():
            with self.subTest(division=division):
                result = self.parse_result("batting", division)
                fixture_text = self.result_fixture("batting", division)
                self.assertEqual(result.scope_code, "vs_lhp")
                self.assertEqual(result.stat_type, "batting")
                self.assertEqual(result.status, "complete")
                self.assertEqual(result.source_season_id, "17040")
                self.assertEqual(result.source_season_team_id, team_id)
                self.assertEqual(result.source_identifier, "4")
                self.assertEqual(result.source_stat_category_id, "15867")
                self.assertEqual(
                    result.fixture_sha256,
                    hashlib.sha256(fixture_text.encode("utf-8")).hexdigest(),
                )
                self.assertGreater(len(result.rows), 0)
                self.assertTrue(all(row.at_bats is not None for row in result.rows))
                self.assertTrue(all(row.hits is not None for row in result.rows))
                self.assertTrue(all(row.source_player_id for row in result.rows))

    def test_sourced_zero_is_distinct_from_missing_split(self):
        result = self.parse_result("batting", "d1")
        zeros = [row for row in result.rows if row.hits == 0]
        self.assertGreater(len(zeros), 0)
        self.assertTrue(all(row.at_bats > 0 for row in zeros))


class PitchingSplitTests(_SplitContractMixin, unittest.TestCase):
    CODES = (
        "vs_lhb", "vs_rhb", "runners_on", "bases_empty",
        "bases_loaded", "risp", "two_outs",
    )

    def test_enumerates_exactly_seven_canonical_pitching_codes(self):
        api = fielding_api()
        self.assertEqual(api.split_codes("pitching"), self.CODES)
        self.assertEqual(
            {scope.scope_code for scope in self.discovery("pitching")},
            set(self.CODES),
        )

    def test_d1_d2_d3_certified_results_preserve_exact_pitching_evidence(self):
        for division, team_id in self.REPRESENTATIVES.items():
            with self.subTest(division=division):
                result = self.parse_result("pitching", division)
                self.assertEqual(result.scope_code, "vs_lhb")
                self.assertEqual(result.stat_type, "pitching")
                self.assertEqual(result.status, "complete")
                self.assertEqual(result.source_season_team_id, team_id)
                self.assertEqual(result.source_identifier, "19")
                self.assertEqual(result.source_stat_category_id, "15868")
                self.assertGreater(len(result.rows), 0)
                self.assertTrue(all(row.opponent_at_bats is not None for row in result.rows))
                self.assertTrue(all(row.hits_allowed is not None for row in result.rows))

    def test_one_source_player_can_retain_typed_batting_and_pitching_rows(self):
        batting = self.parse_result("batting", "d1")
        pitching = self.parse_result("pitching", "d1")
        batting_ids = {row.source_player_id for row in batting.rows}
        pitching_ids = {row.source_player_id for row in pitching.rows}
        self.assertIn("9698671", batting_ids & pitching_ids)
        self.assertNotEqual(type(batting.rows[0]), type(pitching.rows[0]))


class SplitProvenanceTests(_SplitContractMixin, unittest.TestCase):
    @staticmethod
    def _live_duplicate_grid(second_counts):
        return f"""
        <button data-stat-seq="123">Vs LHP</button>
        <table id="stat_grid">
          <thead><tr><th>Player</th><th>H</th><th>AB</th></tr></thead>
          <tbody>
            <tr id="player_42"><td><a href="/players/42">Player</a></td></tr>
            <tr class="sport_stat_123"><td>2</td><td>5</td></tr>
            <tr class="sport_stat_123"><td>{second_counts[0]}</td><td>{second_counts[1]}</td></tr>
          </tbody>
        </table>
        """

    def test_live_split_duplicates_dedupe_only_when_counts_match(self):
        api = fielding_api()
        identical = self._live_duplicate_grid((2, 5))
        scope = next(item for item in api.discover_live_split_controls(
            identical,
            season_id="17040",
            season_team_id="614606",
            stat_type="batting",
            source_stat_category_id="15867",
        ) if item.scope_code == "vs_lhp")
        result = api.parse_live_split_result(identical, discovered_scope=scope)
        self.assertEqual(len(result.rows), 1)
        with self.assertRaises(api.SplitContractError):
            api.parse_live_split_result(
                self._live_duplicate_grid((3, 5)), discovered_scope=scope
            )

    def test_live_split_omits_published_team_totals(self):
        api = fielding_api()
        html = """
        <button data-stat-seq="123">Vs LHP</button>
        <table id="stat_grid">
          <thead><tr><th>Player</th><th>H</th><th>AB</th></tr></thead>
          <tbody>
            <tr id="player_42"><td><a href="/players/42">Player</a></td></tr>
            <tr class="sport_stat_123"><td>2</td><td>5</td></tr>
            <tr id="player_426889744"><td>Totals</td></tr>
            <tr class="sport_stat_123"><td>388</td><td>1,353</td></tr>
          </tbody>
        </table>
        """
        scope = next(item for item in api.discover_live_split_controls(
            html,
            season_id="17040",
            season_team_id="614606",
            stat_type="batting",
            source_stat_category_id="15867",
        ) if item.scope_code == "vs_lhp")
        result = api.parse_live_split_result(html, discovered_scope=scope)
        self.assertEqual(
            [(row.source_player_id, row.hits, row.at_bats) for row in result.rows],
            [("42", 2, 5)],
        )

    def test_live_split_uses_stable_player_id_from_exact_category_link(self):
        api = fielding_api()
        html = """
        <button data-stat-seq="123">Vs LHP</button>
        <table id="stat_grid">
          <thead><tr><th>Player</th><th>H</th><th>AB</th></tr></thead>
          <tbody>
            <tr id="player_426889718"><td><a href="/players/11216290?year_stat_category_id=15867">Player</a></td></tr>
            <tr class="sport_stat_123"><td>4</td><td>26</td></tr>
          </tbody>
        </table>
        """
        scope = next(item for item in api.discover_live_split_controls(
            html,
            season_id="17040",
            season_team_id="614606",
            stat_type="batting",
            source_stat_category_id="15867",
        ) if item.scope_code == "vs_lhp")
        result = api.parse_live_split_result(html, discovered_scope=scope)
        self.assertEqual(result.rows[0].source_player_id, "11216290")

        unsafe = html.replace(
            "year_stat_category_id=15867",
            "year_stat_category_id=15867&amp;redirect=https://example.com",
        )
        with self.assertRaises(api.SplitContractError):
            api.parse_live_split_result(unsafe, discovered_scope=scope)

    def test_capture_split_duplicates_reject_conflicting_evidence(self):
        api = FixtureCaptureSafetyTests.capture_api()
        identical = self._live_duplicate_grid((2, 5))
        self.assertEqual(len(api._result_rows(identical, "123", "batting")), 1)
        with self.assertRaises(api.CaptureUnavailable):
            api._result_rows(
                self._live_duplicate_grid((3, 5)), "123", "batting"
            )

    def test_stale_identifier_is_rejected_before_result_rows_are_returned(self):
        api = fielding_api()
        scope = next(
            item for item in self.discovery("batting") if item.scope_code == "vs_lhp"
        )
        stale = replace(scope, source_identifier="999999")
        with self.assertRaises(api.SplitContractError) as caught:
            api.parse_split_result(
                self.result_fixture("batting"),
                discovered_scope=stale,
            )
        self.assertEqual(caught.exception.category, "source_identity_mismatch")
        self.assertNotIn("999999", str(caught.exception))

    def test_scope_and_typed_fact_models_have_composite_owner_constraints(self):
        from roster_scout.models import (
            ScopedBattingFact,
            ScopedPitchingFact,
            StatisticScope,
        )

        scope_constraints = {
            constraint.name for constraint in StatisticScope.__table__.constraints
        }
        self.assertIn("fk_statistic_scopes_snapshot_organization", scope_constraints)
        self.assertIn("uq_statistic_scopes_snapshot_type_code", scope_constraints)
        self.assertIn("ck_statistic_scopes_status", scope_constraints)
        for model, prefix in (
            (ScopedBattingFact, "scoped_batting_facts"),
            (ScopedPitchingFact, "scoped_pitching_facts"),
        ):
            names = {constraint.name for constraint in model.__table__.constraints}
            self.assertIn(f"fk_{prefix}_scope_snapshot_organization", names)
            self.assertIn(f"fk_{prefix}_player_snapshot_organization", names)
            self.assertIn(f"fk_{prefix}_source_identity", names)

    def test_available_rows_carry_player_source_and_sample_context(self):
        for stat_type in ("batting", "pitching"):
            with self.subTest(stat_type=stat_type):
                result = self.parse_result(stat_type)
                for row in result.rows:
                    self.assertRegex(row.source_player_url, r"^https://stats\.ncaa\.org/players/\d+$")
                    self.assertEqual(row.source_identifier, result.source_identifier)
                    self.assertEqual(row.sample["at_bats"], row.at_bats)
                    self.assertNotIn(None, (row.source_player_id, row.source_payload_sha256))


class SplitAvailabilityTests(_SplitContractMixin, unittest.TestCase):
    def unavailable_fixture(self):
        return (
            '<split-result data-canonical-label="bases_loaded" '
            'data-captured-at="2026-11-16T07:39:58Z" data-division="d1" '
            'data-identifier="16" data-result-contract-version="ncaa-split-result-v1" '
            'data-season-id="17040" data-season-team-id="614606" '
            'data-source-path="/teams/614606/season_to_date_stats" '
            'data-source-payload-sha256="' + "a" * 64 + '" '
            'data-source-stat-category-id="15867" data-stat-type="batting" '
            'data-status="unavailable" data-reason="source_not_published"></split-result>'
        )

    def test_unavailable_scope_has_no_rows_or_synthetic_metric_zero(self):
        api = fielding_api()
        scope = next(
            item
            for item in self.discovery("batting")
            if item.scope_code == "bases_loaded"
        )
        result = api.parse_split_result(
            self.unavailable_fixture(),
            discovered_scope=scope,
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "source_not_published")
        self.assertEqual(result.rows, ())
        self.assertEqual(result.metric_results, ())

    def test_absent_grid_is_failed_not_verified_empty_or_zero(self):
        api = fielding_api()
        scope = next(
            item for item in self.discovery("pitching") if item.scope_code == "vs_lhb"
        )
        with self.assertRaises(api.SplitContractError) as caught:
            api.parse_split_result(
                "<html><body>Access Denied</body></html>",
                discovered_scope=scope,
            )
        self.assertEqual(caught.exception.category, "source_blocked")

    def test_status_vocabulary_is_complete_partial_unavailable_failed(self):
        from roster_scout.models import SCOPE_STATUSES

        self.assertEqual(
            tuple(SCOPE_STATUSES),
            ("complete", "partial", "unavailable", "failed"),
        )


class SplitPublicationTests(
    _SplitContractMixin,
    __import__("tests.test_report_jobs", fromlist=["PostgreSQLJobTestCase"])
    .PostgreSQLJobTestCase,
):
    """Evidence-first split persistence and truthful partial publication."""

    def collected_sections(self, *, all_available=False):
        from roster_scout.jobs import (
            CollectedSection,
            PARSER_VERSION,
            SCHEMA_VERSION,
            to_collected_split_sections,
        )

        collected_at = datetime(2026, 11, 16, 7, 42, tzinfo=UTC)
        shared = {
            "Player": "Two Way Player",
            "_player_id": "9698671",
            "_player_url": "https://stats.ncaa.org/players/9698671",
        }
        core = (
            CollectedSection(
                name="batting",
                critical=True,
                rows=({
                    **shared,
                    "AB": "10", "H": "4", "BB": "1", "K": "2",
                    "HBP": "0", "SF": "0", "SH": "0", "2B": "1",
                    "3B": "0", "HR": "0", "TB": "5",
                },),
                source_url="https://stats.ncaa.org/batting",
                source_team_id="234",
                source_season_id="17040",
                source_season_team_id="614606",
                source_stat_category_id="15867",
                collected_at=collected_at,
                parser_version=PARSER_VERSION,
                schema_version=SCHEMA_VERSION,
            ),
            CollectedSection(
                name="pitching",
                critical=True,
                rows=({
                    **shared,
                    "IP": "3.0", "H": "2", "R": "1", "ER": "1",
                    "BB": "1", "SO": "4", "HR": "0", "HBP": "0",
                    "BF": "12", "AB": "10", "SF": "0", "ERA": "3.00",
                },),
                source_url="https://stats.ncaa.org/pitching",
                source_team_id="234",
                source_season_id="17040",
                source_season_team_id="614606",
                source_stat_category_id="15868",
                collected_at=collected_at,
                parser_version=PARSER_VERSION,
                schema_version=SCHEMA_VERSION,
            ),
        )
        split_results = []
        for stat_type, representative in (("batting", "vs_lhp"), ("pitching", "vs_lhb")):
            parsed = self.parse_result(stat_type, "d1")
            row = next(item for item in parsed.rows if item.source_player_id == "9698671")
            metric = next(
                item for item in parsed.metric_results
                if item.source_player_id == "9698671"
            )
            for scope in self.discovery(stat_type, "d1"):
                if scope.scope_code == representative or all_available:
                    split_results.append(replace(
                        parsed,
                        scope_code=scope.scope_code,
                        display_label=scope.display_label,
                        source_identifier=scope.source_identifier,
                        source_url=scope.source_url,
                        source_payload_sha256=hashlib.sha256(
                            f"{stat_type}:{scope.scope_code}:complete".encode()
                        ).hexdigest(),
                        rows=(row,),
                        metric_results=(metric,),
                    ))
                else:
                    split_results.append(replace(
                        parsed,
                        scope_code=scope.scope_code,
                        display_label=scope.display_label,
                        source_identifier=scope.source_identifier,
                        source_url=scope.source_url,
                        source_payload_sha256=hashlib.sha256(
                            f"{stat_type}:{scope.scope_code}:unavailable".encode()
                        ).hexdigest(),
                        status="unavailable",
                        reason="source_not_published",
                        rows=(),
                        metric_results=(),
                    ))
        return (*core, *to_collected_split_sections(
            split_results,
            source_team_id="234",
        ))

    def test_fully_available_build_publishes_fourteen_traceable_scopes(self):
        from sqlalchemy import func, select
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import (
            MetricValue,
            ScopedBattingFact,
            ScopedPitchingFact,
            SourceRecord,
            StatisticScope,
        )

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "split-complete", dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self.collected_sections(
                all_available=True
            ),
        )
        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            self.assertEqual(session.scalar(
                select(func.count()).select_from(StatisticScope).where(
                    StatisticScope.snapshot_id == snapshot.id,
                    StatisticScope.status == "complete",
                )
            ), 14)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(ScopedBattingFact)
            ), 7)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(ScopedPitchingFact)
            ), 7)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(SourceRecord).where(
                    SourceRecord.section.in_(("splits:batting", "splits:pitching"))
                )
            ), 14)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(MetricValue).where(
                    MetricValue.statistic_scope_id.is_not(None)
                )
            ), 14)

    def test_mixed_split_availability_publishes_partial_with_exact_lineage(self):
        from sqlalchemy import func, select
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import (
            MetricValue,
            ScopedBattingFact,
            ScopedPitchingFact,
            SourceRecord,
            StatisticScope,
        )

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "split-mixed", dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self.collected_sections(),
        )
        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            scopes = list(session.scalars(
                select(StatisticScope).where(StatisticScope.snapshot_id == snapshot.id)
            ))
            self.assertEqual(len(scopes), 14)
            self.assertEqual(
                {(scope.stat_type, scope.scope_code) for scope in scopes},
                {
                    (stat_type, code)
                    for stat_type, codes in (
                        ("batting", BattingSplitTests.CODES),
                        ("pitching", PitchingSplitTests.CODES),
                    )
                    for code in codes
                },
            )
            self.assertEqual(sum(scope.status == "complete" for scope in scopes), 2)
            self.assertEqual(sum(scope.status == "unavailable" for scope in scopes), 12)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ScopedBattingFact)), 1
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ScopedPitchingFact)), 1
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MetricValue).where(
                    MetricValue.statistic_scope_id.is_not(None)
                )),
                2,
            )
            split_sources = list(session.scalars(select(SourceRecord).where(
                SourceRecord.section.in_(("splits:batting", "splits:pitching"))
            )))
            self.assertEqual(len(split_sources), 2)
            self.assertTrue(all(row.player_team_season_id for row in split_sources))

    def test_unavailable_scopes_persist_no_facts_or_metrics(self):
        from sqlalchemy import func, select
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import MetricValue, ScopedBattingFact, StatisticScope

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "split-no-zero", dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self.collected_sections(),
        )
        with self.Session() as session:
            unavailable = session.scalar(select(StatisticScope).where(
                StatisticScope.snapshot_id == snapshot.id,
                StatisticScope.stat_type == "batting",
                StatisticScope.scope_code == "bases_loaded",
            ))
            self.assertEqual(unavailable.status, "unavailable")
            self.assertEqual(unavailable.availability_reason, "source_not_published")
            self.assertEqual(session.scalar(
                select(func.count()).select_from(ScopedBattingFact).where(
                    ScopedBattingFact.statistic_scope_id == unavailable.id
                )
            ), 0)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(MetricValue).where(
                    MetricValue.statistic_scope_id == unavailable.id
                )
            ), 0)

    def test_published_scope_and_typed_facts_are_immutable_and_owner_bound(self):
        import uuid
        from sqlalchemy import select, text
        from sqlalchemy.exc import DBAPIError, IntegrityError
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import (
            ReportSnapshot,
            ScopedBattingFact,
            StatisticScope,
        )

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "split-owner", dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self.collected_sections(
                all_available=True
            ),
        )
        with self.Session() as session:
            scope = session.scalar(select(StatisticScope).where(
                StatisticScope.snapshot_id == snapshot.id,
                StatisticScope.stat_type == "batting",
            ).limit(1))
            fact = session.scalar(select(ScopedBattingFact).where(
                ScopedBattingFact.statistic_scope_id == scope.id
            ))
            scope_id, fact_id = scope.id, fact.id
            fact_values = {
                "player_team_season_id": fact.player_team_season_id,
                "source_record_id": fact.source_record_id,
                "at_bats": fact.at_bats,
                "hits": fact.hits,
                "sample_json": fact.sample_json,
            }
        for statement, identifier in (
            ("UPDATE statistic_scopes SET display_label = 'Changed' WHERE id = :id", scope_id),
            ("DELETE FROM scoped_batting_facts WHERE id = :id", fact_id),
        ):
            with self.Session.begin() as session, self.assertRaises(DBAPIError):
                session.execute(text(statement), {"id": identifier})

        with self.Session.begin() as session:
            other = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(other)
            session.flush()
            with self.assertRaises(IntegrityError), session.begin_nested():
                session.add(ScopedBattingFact(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    snapshot_id=other.id,
                    statistic_scope_id=scope_id,
                    **fact_values,
                ))
                session.flush()


class FieldingPublicationTests(
    __import__("tests.test_report_jobs", fromlist=["PostgreSQLJobTestCase"])
    .PostgreSQLJobTestCase
):
    """PostgreSQL contracts for evidence-first fielding publication."""

    @staticmethod
    def _sections(*, fielding_status="complete"):
        from datetime import UTC, datetime
        from roster_scout.jobs import CollectedSection, PARSER_VERSION

        collected_at = datetime.now(UTC)
        core = tuple(
            CollectedSection(
                name=name,
                critical=True,
                rows=(),
                verified_empty=True,
                source_url=f"https://stats.ncaa.org/{name}",
                source_team_id="234",
                source_season_id="17040",
                source_season_team_id="614606",
                source_stat_category_id=category,
                collected_at=collected_at,
                parser_version=PARSER_VERSION,
                schema_version="schema-1",
            )
            for name, category in (("batting", "15867"), ("pitching", "15868"))
        )
        if fielding_status != "complete":
            return (*core, CollectedSection.supplemental_failure(
                "fielding",
                status=fielding_status,
                category="source_blocked",
                collected_at=collected_at,
                source_url=(
                    "https://stats.ncaa.org/teams/614606/season_to_date_stats"
                    "?year_stat_category_id=15869"
                ),
                source_team_id="234",
                source_season_id="17040",
                source_season_team_id="614606",
                source_stat_category_id="15869",
            ))

        parsed = fielding_api().parse_fielding_page(
            fixture("d1"),
            source_url=(
                "https://stats.ncaa.org/teams/614606/season_to_date_stats"
                "?year_stat_category_id=15869"
            ),
            stat_category_id="15869",
            collected_at=collected_at,
        )
        return (*core, CollectedSection(
            name="fielding",
            critical=False,
            rows=tuple({
                **dict(row.raw_values),
                "_player_id": row.source_player_id,
                "_player_url": row.player_url,
                "_payload_sha256": row.payload_sha256,
            } for row in parsed.rows),
            source_url=parsed.source_url,
            source_team_id="234",
            source_season_id="17040",
            source_season_team_id="614606",
            source_stat_category_id=parsed.stat_category_id,
            collected_at=parsed.collected_at,
            parser_version=PARSER_VERSION,
            schema_version="schema-1",
        ))

    def test_complete_fielding_persists_exact_evidence_and_publishes_ready(self):
        from sqlalchemy import func, select
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import FieldingFact, ReportSection, SourceRecord

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "fielding-complete", dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self._sections(),
        )
        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            section = session.scalar(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section == "fielding",
            ))
            self.assertEqual((section.status, section.row_count), ("complete", 2))
            self.assertEqual(session.scalar(
                select(func.count()).select_from(SourceRecord).where(
                    SourceRecord.snapshot_id == snapshot.id,
                    SourceRecord.section == "fielding",
                )
            ), 2)
            facts = list(session.scalars(select(FieldingFact).where(
                FieldingFact.snapshot_id == snapshot.id
            )))
            self.assertEqual(len(facts), 2)
            self.assertEqual(facts[0].putouts, 121)
            self.assertIsNone(facts[1].passed_balls)

    def test_live_fielding_header_aliases_publish_complete_typed_counts(self):
        from dataclasses import replace
        from decimal import Decimal
        from sqlalchemy import select
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import FieldingFact

        sections = list(self._sections())
        fielding = sections[-1]
        live_rows = []
        for row in fielding.rows:
            live = dict(row)
            live["GP"] = live.pop("G")
            live["FldPct"] = live.pop("FPCT")
            live["IDP"] = live.pop("DP")
            live_rows.append(live)
        sections[-1] = replace(fielding, rows=tuple(live_rows))

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id,
            "fielding-live-aliases", dispatch=None,
        )
        snapshot = run_report_job(
            self.Session, job.id,
            collector=lambda *_args, **_kwargs: tuple(sections),
        )
        with self.Session() as session:
            facts = list(session.scalars(
                select(FieldingFact)
                .where(FieldingFact.snapshot_id == snapshot.id)
                .order_by(FieldingFact.games.desc())
            ))
            self.assertEqual(facts[0].games, 18)
            self.assertEqual(facts[0].double_plays, 3)
            self.assertEqual(facts[0].fielding_percentage, Decimal("0.98500"))

    def test_unavailable_fielding_publishes_truthful_partial(self):
        from sqlalchemy import func, select
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import FieldingFact, ReportSection, SourceRecord

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "fielding-unavailable", dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self._sections(
                fielding_status="unavailable"
            ),
        )
        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            section = session.scalar(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section == "fielding",
            ))
            self.assertEqual(section.status, "unavailable")
            self.assertEqual(section.failure_category, "source_blocked")
            self.assertIsNotNone(section.collected_at)
            self.assertEqual(section.row_count, 0)
            self.assertFalse(section.verified_empty)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(SourceRecord).where(
                    SourceRecord.snapshot_id == snapshot.id,
                    SourceRecord.section == "fielding",
                )
            ), 0)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(FieldingFact).where(
                    FieldingFact.snapshot_id == snapshot.id
                )
            ), 0)

    def test_publication_rejects_fielding_row_count_mismatch(self):
        from roster_scout.jobs import (
            _new_or_existing_snapshot,
            _persist_canonical,
            _persist_sections,
            accept_report_job,
        )
        from roster_scout.models import ReportJob, ReportSection
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot
        from sqlalchemy import select

        organization_id, report_id = self.fixture()
        accepted = accept_report_job(
            self.Session, organization_id, report_id, "fielding-mismatch", dispatch=None
        )
        sections = self._sections()
        with self.Session.begin() as session:
            job = session.get(ReportJob, accepted.id)
            snapshot = _new_or_existing_snapshot(session, job)
            _persist_sections(session, job, snapshot, sections)
            _persist_canonical(session, job, snapshot, sections)
            snapshot_id = snapshot.id
            fielding = session.scalar(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section == "fielding",
            ))
            fielding.row_count += 1
            session.flush()
            with self.assertRaisesRegex(SnapshotPublicationError, "row count"):
                publish_snapshot(session, snapshot_id)

    def test_fielding_fact_rejects_cross_snapshot_identity_and_source(self):
        import uuid
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import FieldingFact, ReportSnapshot

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "fielding-owner", dispatch=None
        )
        published = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self._sections(),
        )
        with self.Session.begin() as session:
            source_fact = session.scalar(select(FieldingFact).where(
                FieldingFact.snapshot_id == published.id
            ).limit(1))
            other = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(other)
            session.flush()
            with self.assertRaises(IntegrityError), session.begin_nested():
                session.add(FieldingFact(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    snapshot_id=other.id,
                    player_team_season_id=source_fact.player_team_season_id,
                    source_record_id=source_fact.source_record_id,
                    games=source_fact.games,
                ))
                session.flush()


if __name__ == "__main__":
    unittest.main()
