import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import roster_scraper as rs


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


TEAM_ROWS = [
    {"team_id": "234", "team_name": "Florida St.", "division": "1", "year": "2026", "season_id": "17040", "season_team_id": "614606"},
    {"team_id": "689", "team_name": "Tampa", "division": "2", "year": "2026", "season_id": "17040", "season_team_id": "615087"},
    {"team_id": "633", "team_name": "Scranton", "division": "3", "year": "2026", "season_id": "17040", "season_team_id": "615349"},
]
SEASON_ROWS = [{"season": "2026", "id": "17040", "batting_id": "15867", "pitching_id": "15868"}]


class NcaaWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "ROSTER_SCOUT_CACHE_DIR": self.temp_dir.name,
            "NCAA_REQUEST_DELAY_SECONDS": "0",
        })
        self.env.start()
        rs._NCAA_TEAM_ROWS = list(TEAM_ROWS)
        rs._NCAA_SEASON_ROWS = list(SEASON_ROWS)

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()
        rs._NCAA_TEAM_ROWS = None
        rs._NCAA_SEASON_ROWS = None

    def test_resolves_representative_team_from_each_division(self):
        self.assertEqual(rs._find_ncaa_team("Florida State", "d1", 2026)["team_id"], "234")
        self.assertEqual(rs._find_ncaa_team("Tampa", "d2", 2026)["team_id"], "689")
        self.assertEqual(rs._find_ncaa_team("Scranton", "d3", 2026)["season_team_id"], "615349")

    def test_parses_and_normalizes_all_three_divisions(self):
        for division in (1, 2, 3):
            batting = rs._parse_ncaa_stat_table(fixture(f"d{division}_batting.html"))
            pitching = rs._parse_ncaa_stat_table(fixture(f"d{division}_pitching.html"))
            players = rs._normalize_ncaa_players(batting, pitching)
            hitters, pitchers = rs.split_roster(players)
            self.assertGreaterEqual(len(hitters), 1, f"D{division} hitter missing")
            self.assertGreaterEqual(len(pitchers), 1, f"D{division} pitcher missing")
            self.assertIn("AVG", hitters[0])
            self.assertIn("ERA", pitchers[0])

    def test_merges_two_way_player_by_ncaa_player_id(self):
        batting = rs._parse_ncaa_stat_table(fixture("d1_batting.html"))
        pitching = rs._parse_ncaa_stat_table(fixture("d1_pitching.html"))
        players = rs._normalize_ncaa_players(batting, pitching)
        two_way = next(player for player in players if player["Name"] == "D One Two Way")
        self.assertTrue(two_way["_has_hitting"])
        self.assertTrue(two_way["_has_pitching"])
        hitters, pitchers = rs.split_roster(players)
        self.assertIn("D One Two Way", {row["Name"] for row in hitters})
        self.assertIn("D One Two Way", {row["Name"] for row in pitchers})

    def test_identifies_ncaa_aggregate_rows(self):
        for name in ("Total", "Totals", "Team Totals", "Opponent Totals"):
            self.assertTrue(rs._is_ncaa_aggregate_name(name))
        self.assertFalse(rs._is_ncaa_aggregate_name("Tommy Totalson"))

    def test_successful_result_is_cached_and_skips_repeat_fetch(self):
        pages = {
            "batting": fixture("d3_batting.html"),
            "pitching": fixture("d3_pitching.html"),
        }
        with patch.object(rs, "_fetch_ncaa_pages", return_value=pages) as fetch:
            first, label = rs.scrape_ncaa_by_name("Scranton", "d3", year=2026)
            second, second_label = rs.scrape_ncaa_by_name("Scranton", "d3", year=2026)
        self.assertEqual(label, "Scranton")
        self.assertEqual(second_label, label)
        self.assertEqual(second, first)
        self.assertEqual(fetch.call_count, 1)
        cached = list(Path(self.temp_dir.name).glob("result-*.json"))
        self.assertEqual(len(cached), 1)
        self.assertEqual(json.loads(cached[0].read_text())["team_id"], "633")

    def test_direct_http_is_attempted_before_browser_for_blocked_page(self):
        urls = {"batting": "bat-url", "pitching": "pit-url"}
        d1_batting = fixture("d1_batting.html")
        d1_pitching = fixture("d1_pitching.html")

        def direct(url, session):
            if url == "bat-url":
                return d1_batting
            raise rs.NcaaAccessError("blocked")

        with patch.object(rs, "_fetch_ncaa_http", side_effect=direct) as http_fetch, patch.object(
            rs, "_fetch_ncaa_browser_pages", return_value={"pitching": d1_pitching}
        ) as browser_fetch:
            pages = rs._fetch_ncaa_pages(urls)

        self.assertEqual(http_fetch.call_count, 2)
        browser_fetch.assert_called_once_with({"pitching": "pit-url"})
        self.assertEqual(pages["batting"], d1_batting)
        self.assertEqual(pages["pitching"], d1_pitching)

    def test_terms_page_is_accepted_before_retrying_stats_url(self):
        target = "https://stats.ncaa.org/teams/615236/season_to_date_stats"
        checkbox = MagicMock()
        checkbox.count.return_value = 1
        button = MagicMock()
        button.count.return_value = 1
        page = MagicMock()
        page.url = "https://stats.ncaa.org/stats_terms"
        page.locator.side_effect = lambda selector: {
            "#terms_accepted": checkbox,
            "#stats-access-button": button,
        }[selector]

        with patch.dict(os.environ, {"NCAA_TERMS_DWELL_MILLISECONDS": "0"}):
            rs._accept_ncaa_terms(page, target)

        checkbox.check.assert_called_once_with()
        button.click.assert_called_once_with()
        page.goto.assert_called_once_with(
            target, wait_until="domcontentloaded", timeout=60000
        )

    def test_failures_are_not_cached(self):
        with patch.object(rs, "_fetch_ncaa_pages", side_effect=rs.NcaaAccessError("blocked")):
            with self.assertRaises(rs.NcaaAccessError):
                rs.scrape_ncaa_by_name("Tampa", "d2", year=2026)
        self.assertEqual(list(Path(self.temp_dir.name).glob("result-*.json")), [])


if __name__ == "__main__":
    unittest.main()
