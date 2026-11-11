"""Regression coverage for duplicate collections and remote browser cleanup."""
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import roster_scraper as rs
from usage_limits import BrowserCapacityExceeded


class NcaaSameTeamCollectionLockTests(unittest.TestCase):
    def test_concurrent_team_requests_collect_once(self):
        team = dict(team_name='Example', season_team_id='123', team_id='1', division=1)
        entered, release = threading.Event(), threading.Event()

        def collect(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return {section: (Path(__file__).parent / 'fixtures' / f'd3_{section}.html').read_text() for section in ('batting', 'pitching')}

        with tempfile.TemporaryDirectory() as cache, patch.dict(os.environ, {
            'ROSTER_SCOUT_CACHE_DIR': cache,
        }), patch.object(rs, '_find_ncaa_season', return_value={'id': '17040', 'batting_id': '1', 'pitching_id': '2'}), patch.object(rs, '_fetch_ncaa_pages', side_effect=collect) as fetch:
            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(rs._scrape_ncaa_team, team, 2026)
                self.assertTrue(entered.wait(3))
                second = pool.submit(rs._scrape_ncaa_team, team, 2026)
                time.sleep(.15)
                release.set()
                self.assertEqual(first.result(), second.result())
            self.assertEqual(fetch.call_count, 1)

    def test_force_refresh_bypasses_current_contract_cache(self):
        team = dict(team_name='Example', season_team_id='123', team_id='1', division=1)
        collection = SimpleNamespace(players=({'Name': 'Fresh Player', 'AB': '12'},))

        with tempfile.TemporaryDirectory() as cache, patch.dict(os.environ, {
            'ROSTER_SCOUT_CACHE_DIR': cache,
        }), patch.object(rs, '_collect_ncaa_team', return_value=collection) as collect:
            rs._scrape_ncaa_team(team, 2026)
            rs._scrape_ncaa_team(team, 2026, force_refresh=True)

        self.assertEqual(collect.call_count, 2)

    def test_result_cache_and_team_lock_namespaces_share_contract_identity(self):
        team = dict(team_name='Example', season_team_id='123', team_id='1', division=1)
        identities = (
            ('parser-a', 'headers-a', 'schema-a'),
            ('parser-b', 'headers-a', 'schema-a'),
            ('parser-a', 'headers-b', 'schema-a'),
            ('parser-a', 'headers-a', 'schema-b'),
        )

        with tempfile.TemporaryDirectory() as cache, patch.dict(os.environ, {
            'ROSTER_SCOUT_CACHE_DIR': cache,
        }):
            for parser, headers, schema in identities:
                with patch.multiple(
                    rs,
                    NCAA_PARSER_VERSION=parser,
                    NCAA_CORE_HEADER_CONTRACT_VERSION=headers,
                    NCAA_SCHEMA_VERSION=schema,
                ):
                    key = rs._ncaa_result_cache_key(team, 2026)
                    rs._write_json_cache(key, {'players': [{'Name': parser}]})
                    with rs._ncaa_team_lock(team, 2026):
                        pass

            self.assertEqual(len(list(Path(cache).glob('result-*.json'))), 4)
            self.assertEqual(len(list(Path(cache).glob('team-*.lock'))), 4)


class BrowserlessLifecycleTests(unittest.TestCase):
    def test_upstream_429_becomes_capacity_error_without_retry(self):
        with patch.dict(os.environ, {'BROWSERLESS_TOKEN': 'test'}), patch(
            'requests.post', return_value=SimpleNamespace(status_code=429)
        ) as post:
            with self.assertRaises(BrowserCapacityExceeded):
                rs._fetch_ncaa_browserless_pages({'batting': 'https://stats.ncaa.org/test'})
            self.assertEqual(post.call_count, 1)

    def test_remote_browser_terminated_even_when_collection_fails(self):
        browser, playwright = MagicMock(), MagicMock()
        browser.contexts = []
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = MagicMock()
        starter.start.return_value = playwright
        response = MagicMock(status_code=200)
        response.json.return_value = {'browserWSEndpoint': 'wss://example.test/browser'}
        with patch.dict(os.environ, {'BROWSERLESS_TOKEN': 'test'}), patch(
            'requests.post', return_value=response
        ), patch('patchright.sync_api.sync_playwright', return_value=starter):
            with self.assertRaises(rs.NcaaAccessError):
                rs._fetch_ncaa_browserless_pages({'batting': 'https://stats.ncaa.org/test'})
        browser.new_browser_cdp_session.return_value.send.assert_called_once_with('Browser.close')
        browser.close.assert_called_once()
        playwright.stop.assert_called_once()
