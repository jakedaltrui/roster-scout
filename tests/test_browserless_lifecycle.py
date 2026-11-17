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
from roster_scout.collection_control import (
    CollectionBudgetExceeded,
    CollectionDeadlineExceeded,
)
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
    @staticmethod
    def _session(first_url):
        page = MagicMock()
        page.url = first_url
        page.content.return_value = (
            "<table id='stat_grid'><thead><tr><th>Player</th></tr></thead>"
            "<tbody></tbody></table>"
        )
        browser, playwright = MagicMock(), MagicMock()
        browser.contexts = [SimpleNamespace(pages=[page])]
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = MagicMock()
        starter.start.return_value = playwright
        response = MagicMock(status_code=200)
        response.json.return_value = {
            'browserWSEndpoint': 'wss://example.test/browser'
        }
        return page, response, starter

    def test_public_transport_preserves_collection_control_signals(self):
        urls = {
            'batting': 'https://stats.ncaa.org/teams/1/batting',
            'pitching': 'https://stats.ncaa.org/teams/1/pitching',
        }
        for signal in (
            CollectionBudgetExceeded('fixture budget'),
            CollectionDeadlineExceeded('fixture deadline'),
        ):
            page, response, starter = self._session(urls['batting'])
            calls = []

            def reserve(key):
                calls.append(key)
                if len(calls) == 2:
                    raise signal

            with self.subTest(signal=type(signal).__name__), patch.dict(
                os.environ,
                {
                    'NCAA_BROWSER_PROVIDER': 'browserless',
                    'BROWSERLESS_TOKEN': 'test',
                },
            ), patch(
                'requests.post', return_value=response
            ), patch(
                'patchright.sync_api.sync_playwright', return_value=starter
            ):
                with self.assertRaises(type(signal)):
                    rs._fetch_ncaa_pages(
                        urls,
                        request_reserver=reserve,
                        navigation_timeout_seconds=5,
                        deadline_monotonic=time.monotonic() + 30,
                    )
            self.assertEqual(calls, ['batting', 'pitching'])

    def test_session_post_crossing_deadline_never_starts_cdp(self):
        clock = [300.0]
        response = MagicMock(status_code=200)
        response.json.return_value = {
            'browserWSEndpoint': 'wss://example.test/browser'
        }
        starter = MagicMock()

        def blocked_post(*_args, **_kwargs):
            clock[0] += 3.1
            return response

        with patch.dict(os.environ, {'BROWSERLESS_TOKEN': 'test'}), patch.object(
            rs.time, 'monotonic', side_effect=lambda: clock[0]
        ), patch(
            'requests.post', side_effect=blocked_post
        ), patch(
            'patchright.sync_api.sync_playwright', return_value=starter
        ):
            with self.assertRaises(CollectionDeadlineExceeded):
                rs._fetch_ncaa_browserless_pages(
                    {'batting': 'https://stats.ncaa.org/test'},
                    deadline_monotonic=303.0,
                    navigation_timeout_seconds=45,
                )

        starter.assert_not_called()
        self.assertEqual(clock[0], 303.1)

    def test_direct_http_retry_sleep_stops_at_build_deadline(self):
        clock = [400.0]
        session = MagicMock()
        session.get.return_value = SimpleNamespace(status_code=429)

        def advance(seconds):
            clock[0] += seconds

        with patch.object(
            rs.time, 'monotonic', side_effect=lambda: clock[0]
        ), patch.object(rs.time, 'sleep', side_effect=advance):
            with self.assertRaises(CollectionDeadlineExceeded):
                rs._request_ncaa_http(
                    'https://stats.ncaa.org/test',
                    session,
                    timeout_seconds=45,
                    deadline_monotonic=402.5,
                )

        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(clock[0], 402.5)

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

    def test_expired_remote_session_cleanup_does_not_replace_source_failure(self):
        browser, playwright = MagicMock(), MagicMock()
        browser.contexts = []
        browser.new_browser_cdp_session.return_value.send.side_effect = RuntimeError(
            'remote session expired'
        )
        browser.close.side_effect = RuntimeError('client already closed')
        playwright.stop.side_effect = RuntimeError('driver already stopped')
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = MagicMock()
        starter.start.return_value = playwright
        response = MagicMock(status_code=200)
        response.json.return_value = {
            'browserWSEndpoint': 'wss://example.test/browser'
        }

        with patch.dict(os.environ, {'BROWSERLESS_TOKEN': 'test'}), patch(
            'requests.post', return_value=response
        ), patch('patchright.sync_api.sync_playwright', return_value=starter):
            with self.assertRaises(rs.NcaaAccessError) as raised:
                rs._fetch_ncaa_browserless_pages({
                    'batting': 'https://stats.ncaa.org/test'
                })

        self.assertEqual(
            str(raised.exception),
            'Browserless returned a session without a page.',
        )
        browser.new_browser_cdp_session.return_value.send.assert_called_once_with(
            'Browser.close'
        )
        browser.close.assert_called_once()
        playwright.stop.assert_called_once()

    def test_expired_remote_session_cleanup_does_not_discard_collected_page(self):
        source_url = 'https://stats.ncaa.org/teams/1/season_to_date_stats'
        page, response, starter = self._session(source_url)
        browser = starter.start.return_value.chromium.connect_over_cdp.return_value
        browser.new_browser_cdp_session.return_value.send.side_effect = RuntimeError(
            'remote session expired'
        )
        browser.close.side_effect = RuntimeError('client already closed')
        starter.start.return_value.stop.side_effect = RuntimeError(
            'driver already stopped'
        )

        with patch.dict(os.environ, {'BROWSERLESS_TOKEN': 'test'}), patch(
            'requests.post', return_value=response
        ), patch(
            'patchright.sync_api.sync_playwright', return_value=starter
        ), patch.object(rs, '_accept_ncaa_terms'):
            rendered = rs._fetch_ncaa_browserless_pages({'batting': source_url})

        self.assertEqual(rendered, {'batting': page.content.return_value})
        browser.new_browser_cdp_session.return_value.send.assert_called_once_with(
            'Browser.close'
        )
        browser.close.assert_called_once()
        starter.start.return_value.stop.assert_called_once()
