import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

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

    def test_selector_catalog_and_exact_id_resolution(self):
        d2 = rs.list_ncaa_teams("d2", year=2026)
        self.assertEqual([team["name"] for team in d2], ["Tampa"])
        self.assertEqual(d2[0]["season_team_id"], "615087")
        self.assertEqual(d2[0]["display_name"], "Tampa")
        self.assertTrue(d2[0]["logo_url"].endswith("/bgl/tampa.svg"))
        resolved = rs._find_ncaa_team_by_season_id("615087", "d2", year=2026)
        self.assertEqual(resolved["team_name"], "Tampa")
        with self.assertRaises(rs.NcaaTeamNotFound):
            rs._find_ncaa_team_by_season_id("615087", "d3", year=2026)

    def test_approved_d1_aliases_use_index_logos_and_shortened_names(self):
        aliases = rs._ncaa_approved_school_aliases("d1")
        self.assertEqual(len(aliases), 47)
        self.assertEqual(aliases["a&m-corpus christi"]["display_name"], "Texas A&M-Corpus Christi")
        self.assertEqual(aliases["uconn"]["display_name"], "Connecticut")
        self.assertEqual(aliases["ualbany"]["display_name"], "Albany")
        self.assertIn("am-corpus-chris.svg", aliases["a&m-corpus christi"]["logo_url"])

    def test_school_names_drop_college_and_use_hawaii_columbia_overrides(self):
        self.assertEqual(rs._school_display_name("American International College"), "American International")
        self.assertEqual(rs._school_display_name("College of Charleston"), "Charleston")
        self.assertEqual(rs._school_display_name("Univ. of Colorado-Colorado Springs"), "Colorado-Colorado Springs")
        rs._NCAA_TEAM_ROWS += [
            {"team_id": "111", "team_name": name, "division": "1", "year": "2026", "season_id": "17040", "season_team_id": ident}
            for name, ident in (("Hawaii", "614618"), ("Columbia", "614586"), ("Indiana", "614627"), ("Maryland", "614654"), ("SIUE", "614744"), ("UIW", "614745"), ("Saint Mary's (CA)", "614746"), ("UTSA", "614747"))
        ]
        teams = {team["name"]: team["display_name"] for team in rs.list_ncaa_teams("d1", 2026)}
        self.assertEqual(teams["Hawaii"], "Hawaii")
        self.assertEqual(teams["Columbia"], "Columbia")
        self.assertEqual(teams["Indiana"], "Indiana")
        self.assertEqual(teams["Maryland"], "Maryland")
        self.assertEqual(teams["SIUE"], "SIU Edwardsville")
        self.assertEqual(teams["UIW"], "Incarnate Word")
        self.assertEqual(teams["Saint Mary's (CA)"], "St. Mary's")
        self.assertEqual(teams["UTSA"], "UTSA")

    def test_reviewed_d2_and_d3_aliases_resolve_to_index_schools(self):
        d2 = rs._ncaa_approved_school_aliases("d2")
        d3 = rs._ncaa_approved_school_aliases("d3")
        self.assertEqual(d2["csusb"]["display_name"], "Cal State-San Bernardino")
        self.assertEqual(d2["aum"]["display_name"], "Auburn-Montgomery")
        self.assertTrue(d2["cal st. san marcos"]["logo_url"].endswith("/cal-st-san-marcos.svg"))
        self.assertTrue(d2["okla. baptist"]["logo_url"].endswith("/okla-baptist.svg"))
        self.assertEqual(d3["cwru"]["display_name"], "Case Western Reserve")
        self.assertTrue(d3["rit"]["logo_url"].endswith("/rochester-inst.svg"))

    def test_johnson_and_wales_campuses_remain_distinguishable(self):
        rs._NCAA_TEAM_ROWS += [
            {"team_id": "", "team_name": name, "division": "3", "year": "2026", "season_id": "17040", "season_team_id": ident}
            for name, ident in (("JWU Charlotte", "615586"), ("JWU (Providence)", "615587"))
        ]
        teams = {t["name"]: t for t in rs.list_ncaa_teams("d3", 2026)}
        self.assertEqual(teams["JWU Charlotte"]["display_name"], "Johnson and Wales")
        self.assertEqual(teams["JWU Charlotte"]["disambiguation"], "Charlotte")
        self.assertEqual(teams["JWU (Providence)"]["disambiguation"], "Providence")
        self.assertEqual(teams["JWU Charlotte"]["logo_url"], teams["JWU (Providence)"]["logo_url"])

    def test_approved_school_logos_are_used_without_stable_team_ids(self):
        rs._NCAA_TEAM_ROWS += [
            {"team_id": "", "team_name": name, "division": division, "year": "2026", "season_id": "17040", "season_team_id": ident}
            for name, division, ident in (("Jamestown", "2", "999901"), ("Point Park", "2", "999902"), ("Regent", "3", "999903"), ("SUNY Brockport", "3", "999904"))
        ]
        teams = {t["name"]: t for d in ("d2", "d3") for t in rs.list_ncaa_teams(d, 2026)}
        for name, filename in (("Jamestown", "jamestown.png"), ("Point Park", "point-park.png"), ("Regent", "regent.png"), ("SUNY Brockport", "brockport.svg")):
            self.assertEqual(teams[name]["logo_url"], f"/static/logos/{filename}")
            self.assertTrue((Path(__file__).parent.parent / "static" / "logos" / filename).is_file())

    def test_suny_names_and_duplicate_schools_are_clear(self):
        rs._NCAA_TEAM_ROWS += [
            {"team_id": "", "team_name": name, "division": division, "year": "2026", "season_id": "17040", "season_team_id": str(990000 + i)}
            for i, (name, division) in enumerate((
                ("SUNY Brockport", "3"), ("Cortland", "3"), ("Oswego St.", "3"),
                ("Wayne St. (MI)", "2"), ("Wayne St. (NE)", "2"),
                ("North Central (IL)", "3"), ("North Central (MN)", "3"),
            ))
        ]
        teams = {t["name"]: t for d in ("d2", "d3") for t in rs.list_ncaa_teams(d, 2026)}
        self.assertEqual(teams["SUNY Brockport"]["display_name"], "SUNY Brockport")
        self.assertEqual(teams["Cortland"]["display_name"], "SUNY Cortland")
        self.assertEqual(teams["Oswego St."]["display_name"], "SUNY Oswego")
        self.assertEqual(teams["Wayne St. (MI)"]["disambiguation"], "Michigan")
        self.assertEqual(teams["Wayne St. (NE)"]["disambiguation"], "Nebraska")
        self.assertEqual(teams["North Central (MN)"]["disambiguation"], "Minnesota")

    def test_validates_entire_catalog_in_one_pass(self):
        report = rs.validate_ncaa_team_catalog(year=2026)
        self.assertTrue(report["valid"])
        self.assertEqual(report["rows"], 3)
        self.assertEqual(report["selectable"], 3)
        self.assertEqual(report["counts"], {"d1": 1, "d2": 1, "d3": 1})
        self.assertEqual(report["errors"], [])

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

    def test_pitcher_zero_batting_row_is_excluded_but_two_way_players_remain(self):
        batting = [
            {"Player": "Pitcher Only", "_player_id": "1", "AB": "0", "BB": "0"},
            {"Player": "Two Way", "_player_id": "2", "AB": "12"},
            {"Player": "Walk Only", "_player_id": "3", "AB": "0", "HBP": "1"},
            {"Player": "Hitter", "_player_id": "4", "AB": "30"},
        ]
        pitching = [{"Player": name, "_player_id": ident, "IP": "3.0"} for name, ident in (
            ("Pitcher Only", "1"), ("Two Way", "2"), ("Walk Only", "3"),
        )]
        players = rs._normalize_ncaa_players(batting, pitching)
        hitters, pitchers = rs.split_roster(players)
        self.assertEqual({p["Name"] for p in hitters}, {"Two Way", "Walk Only", "Hitter"})
        self.assertEqual({p["Name"] for p in pitchers}, {"Pitcher Only", "Two Way", "Walk Only"})

        # Older cached records contain the original row-presence flag only.
        for player in players:
            player.pop("_batting_activity")
        hitters, _ = rs.split_roster(players)
        self.assertNotIn("Pitcher Only", {p["Name"] for p in hitters})

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

    def test_browserless_provider_owns_the_complete_ncaa_session(self):
        urls = {"batting": "bat-url", "pitching": "pit-url"}
        expected = {"batting": "bat-html", "pitching": "pit-html"}
        with patch.dict(os.environ, {
            "NCAA_BROWSER_PROVIDER": "browserless",
            "BROWSERLESS_TOKEN": "secret-token",
        }):
            with patch.object(
                rs, "_fetch_ncaa_browserless_pages", return_value=expected
            ) as browserless, patch.object(rs, "_fetch_ncaa_http") as direct:
                pages = rs._fetch_ncaa_pages(urls)

        self.assertEqual(pages, expected)
        browserless.assert_called_once_with(urls)
        direct.assert_not_called()

    def test_browserless_capacity_is_released_after_error(self):
        from usage_limits import BrowserCapacityExceeded, browserless_slot
        with browserless_slot(), browserless_slot():
            with self.assertRaises(BrowserCapacityExceeded):
                with browserless_slot():
                    pass
        with self.assertRaisesRegex(RuntimeError, "failure"):
            with browserless_slot():
                raise RuntimeError("failure")
        with browserless_slot(), browserless_slot():
            pass

    def test_proxy_url_is_used_for_http_and_browser_with_decoded_credentials(self):
        proxy_url = "http://user%2Dsession:secret%40word@proxy.example:7000"
        with patch.dict(os.environ, {"NCAA_BROWSER_PROXY": proxy_url}):
            options = rs._playwright_launch_options()
            with patch.object(rs, "_fetch_ncaa_http", return_value="ok") as fetch:
                pages = rs._fetch_ncaa_pages({"batting": "bat-url"})

        self.assertEqual(options["proxy"], {
            "server": "http://proxy.example:7000",
            "username": "user-session",
            "password": "secret@word",
        })
        self.assertEqual(pages, {"batting": "ok"})
        session = fetch.call_args.args[1]
        self.assertEqual(
            session.proxies["https"],
            "http://user-session:secret%40word@proxy.example:7000",
        )

    def test_proxy_without_scheme_defaults_to_http(self):
        self.assertEqual(
            rs._playwright_proxy_options("proxy.example:7000"),
            {"server": "http://proxy.example:7000"},
        )

    def test_decodo_colon_format_is_supported_without_echoing_credentials(self):
        proxy = "us.example.com:10001:user-name:secret-password"
        self.assertEqual(rs._playwright_proxy_options(proxy), {
            "server": "http://us.example.com:10001",
            "username": "user-name",
            "password": "secret-password",
        })
        self.assertEqual(
            rs._requests_proxy_url(proxy),
            "http://user-name:secret-password@us.example.com:10001",
        )

        session = MagicMock()
        session.get.side_effect = requests.exceptions.InvalidURL(proxy)
        with self.assertRaises(rs.NcaaAccessError) as raised:
            rs._fetch_ncaa_http("https://stats.ncaa.org/test", session)
        self.assertNotIn("secret-password", str(raised.exception))

    def test_proxy_tls_bypass_is_scoped_to_configured_proxy(self):
        proxy = "unblock.example:60000:user:password"
        with patch.dict(os.environ, {
            "NCAA_BROWSER_PROXY": proxy,
            "NCAA_PROXY_TLS_VERIFY": "0",
        }):
            options = rs._playwright_launch_options()
            with patch.object(rs, "_fetch_ncaa_http", return_value="ok") as fetch:
                rs._fetch_ncaa_pages({"batting": "bat-url"})

        self.assertTrue(options["ignore_https_errors"])
        self.assertIn("--ignore-certificate-errors", options["args"])
        self.assertFalse(fetch.call_args.args[1].verify)

        with patch.dict(os.environ, {
            "NCAA_PROXY_TLS_VERIFY": "0",
        }, clear=True):
            self.assertNotIn("ignore_https_errors", rs._playwright_launch_options())

    def test_site_unblocker_uses_one_random_session_and_forced_cookies(self):
        with patch.dict(os.environ, {"NCAA_SITE_UNBLOCKER": "1"}):
            with patch.object(rs, "_fetch_ncaa_http", return_value="ok") as fetch:
                rs._fetch_ncaa_pages({"batting": "bat-url", "pitching": "pit-url"})

        first_session = fetch.call_args_list[0].args[1]
        second_session = fetch.call_args_list[1].args[1]
        self.assertIs(first_session, second_session)
        self.assertEqual(first_session.headers["X-SU-Force-Cookies"], "1")
        self.assertRegex(first_session.headers["X-SU-Session-Id"], r"^[0-9a-f]{32}$")

    def test_site_unblocker_accepts_terms_then_retries_original_stats_url(self):
        target = "https://stats.ncaa.org/teams/615087/season_to_date_stats"
        blocked = MagicMock(
            status_code=200,
            text="<html><meta http-equiv='refresh' content='bm-verify'></html>",
        )
        terms = MagicMock(
            status_code=200,
            url="https://stats.ncaa.org/stats_terms",
            text="""
                <form action="/stats_terms" method="post">
                  <input name="utf8" value="✓">
                  <input name="authenticity_token" value="token-now">
                  <input name="nonce" value="nonce-now">
                  <input name="terms_accepted" value="terms-now">
                </form>
            """,
        )
        accepted = MagicMock(status_code=200)
        stats = MagicMock(status_code=200, text=fixture("d2_batting.html"))
        session = MagicMock()
        session._ncaa_terms_accepted = False
        session.get.side_effect = [blocked, terms, stats]
        session.post.return_value = accepted

        with patch.dict(os.environ, {
            "NCAA_SITE_UNBLOCKER": "1",
            "NCAA_REQUEST_DELAY_SECONDS": "0",
        }):
            html = rs._fetch_ncaa_http(target, session)

        self.assertTrue(rs._has_ncaa_stat_grid(html))
        self.assertEqual(session.get.call_args_list[0].args[0], target)
        self.assertEqual(session.get.call_args_list[1].args[0], rs.NCAA_BASE + "/stats_terms")
        self.assertEqual(session.get.call_args_list[2].args[0], target)
        post = session.post.call_args
        self.assertEqual(post.args[0], rs.NCAA_BASE + "/stats_terms")
        self.assertEqual(post.kwargs["headers"]["X-SU-Force-Headers"], "1")
        self.assertEqual(post.kwargs["headers"]["X-SU-Force-Cookies"], "1")
        self.assertFalse(post.kwargs["allow_redirects"])
        self.assertEqual(post.kwargs["data"], {
            "utf8": "✓",
            "authenticity_token": "token-now",
            "nonce": "nonce-now",
            "terms_accepted": "terms-now",
        })

    def test_site_unblocker_retries_faulted_terms_post(self):
        terms = MagicMock(
            status_code=200,
            url=rs.NCAA_BASE + "/stats_terms",
            text="""
                <form action="/stats_terms" method="post">
                  <input name="authenticity_token" value="token-now">
                  <input name="nonce" value="nonce-now">
                  <input name="terms_accepted" value="terms-now">
                </form>
            """,
        )
        faulted = MagicMock(status_code=550)
        accepted = MagicMock(status_code=200)
        session = MagicMock()
        session.get.return_value = terms
        session.post.side_effect = [faulted, accepted]

        with patch.dict(os.environ, {"NCAA_HTTP_TIMEOUT_SECONDS": "1"}):
            with patch.object(rs.time, "sleep") as sleep:
                rs._accept_ncaa_terms_http(session)

        self.assertEqual(session.post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_site_unblocker_accepts_ncaa_terms_redirect(self):
        terms = MagicMock(
            status_code=200,
            url=rs.NCAA_BASE + "/stats_terms",
            text="""
                <form action="/stats_terms" method="post">
                  <input name="authenticity_token" value="token-now">
                  <input name="nonce" value="nonce-now">
                  <input name="terms_accepted" value="terms-now">
                </form>
            """,
        )
        session = MagicMock()
        session.get.return_value = terms
        session.post.return_value = MagicMock(status_code=302)

        rs._accept_ncaa_terms_http(session)

        self.assertEqual(session.post.call_count, 1)

    def test_site_unblocker_posts_terms_from_initial_stats_response(self):
        target = "https://stats.ncaa.org/teams/615087/season_to_date_stats"
        terms = MagicMock(
            status_code=200,
            url=target,
            text="""
                <form action="/stats_terms" method="post">
                  <input name="authenticity_token" value="token-now">
                  <input name="nonce" value="nonce-now">
                  <input name="terms_accepted" value="terms-now">
                </form>
            """,
        )
        accepted = MagicMock(status_code=200)
        stats = MagicMock(status_code=200, text=fixture("d2_batting.html"))
        session = MagicMock()
        session._ncaa_terms_accepted = False
        session.get.side_effect = [terms, stats]
        session.post.return_value = accepted

        with patch.dict(os.environ, {
            "NCAA_SITE_UNBLOCKER": "1",
            "NCAA_REQUEST_DELAY_SECONDS": "0",
        }):
            html = rs._fetch_ncaa_http(target, session)

        self.assertTrue(rs._has_ncaa_stat_grid(html))
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args_list[0].args[0], target)
        self.assertEqual(session.get.call_args_list[1].args[0], target)
        self.assertEqual(session.post.call_args.args[0], rs.NCAA_BASE + "/stats_terms")

    def test_terms_page_is_accepted_before_retrying_stats_url(self):
        target = "https://stats.ncaa.org/teams/615236/season_to_date_stats"
        checkbox = MagicMock()
        checkbox.count.return_value = 1
        button = MagicMock()
        button.count.return_value = 1
        button.is_enabled.return_value = True
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

    def test_terms_form_submits_when_unblocker_leaves_button_disabled(self):
        target = "https://stats.ncaa.org/teams/615087/season_to_date_stats"
        checkbox = MagicMock()
        checkbox.count.return_value = 1
        button = MagicMock()
        button.count.return_value = 1
        button.is_enabled.return_value = False
        form = MagicMock()
        form.count.return_value = 1
        page = MagicMock()
        page.url = "https://stats.ncaa.org/stats_terms"
        page.locator.side_effect = lambda selector: {
            "#terms_accepted": checkbox,
            "#stats-access-button": button,
            "form[action='/stats_terms']": form,
        }[selector]

        with patch.dict(os.environ, {"NCAA_TERMS_DWELL_MILLISECONDS": "0"}):
            rs._accept_ncaa_terms(page, target)

        checkbox.check.assert_called_once_with()
        button.click.assert_not_called()
        form.evaluate.assert_called_once_with("form => form.submit()")
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
