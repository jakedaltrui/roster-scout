"""Native dashboard metric and leader projection contracts."""

from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace


class NativeDashboardProjectionTests(unittest.TestCase):
    def test_dashboard_readiness_never_promotes_quick_season_snapshot(self):
        from roster_scout.native_dashboard import _dashboard_readiness

        sections = {
            "batting": {"status": "complete"},
            "pitching": {"status": "complete"},
        }
        result = _dashboard_readiness("quick", sections)

        self.assertEqual(result["state"], "partial")
        statuses = {item["code"]: item["status"] for item in result["sections"]}
        self.assertEqual(statuses["fielding"], "unavailable")

    def test_dashboard_ready_requires_every_coach_facing_section(self):
        from roster_scout.native_dashboard import (
            _DASHBOARD_SECTION_NAMES,
            _dashboard_readiness,
        )

        sections = {
            name: {"status": "complete"} for name in _DASHBOARD_SECTION_NAMES
        }
        self.assertEqual(
            _dashboard_readiness("detailed", sections)["state"],
            "ready",
        )
        sections["game_logs:fielding"] = {"status": "unavailable"}
        self.assertEqual(
            _dashboard_readiness("detailed", sections)["state"],
            "partial",
        )

    def test_team_aggregation_fields_never_include_derived_metric_codes(self):
        from roster_scout.metrics import METRIC_DEFINITIONS
        from roster_scout.native_dashboard import _SEASON_FIELDS

        for stat_type, fields in _SEASON_FIELDS.items():
            with self.subTest(stat_type=stat_type):
                self.assertFalse(set(fields).intersection(METRIC_DEFINITIONS))

    def test_team_rates_are_derived_only_from_counts(self):
        from roster_scout.native_dashboard import (
            _fielding_percentage,
            _metric,
            _xbh_rate,
        )

        batting = {
            "at_bats": 100,
            "hits": 30,
            "doubles": 6,
            "triples": 2,
            "home_runs": 4,
            "walks": 12,
            "hit_by_pitch": 2,
            "sacrifice_flies": 3,
            "sacrifice_hits": 1,
            "strikeouts": 20,
            "plate_appearances": 118,
        }
        self.assertAlmostEqual(_metric("ops", batting), 0.8961, places=4)
        self.assertAlmostEqual(_metric("batting_k_pct", batting), 0.1695, places=4)
        self.assertEqual(_xbh_rate(batting), 0.12)
        self.assertEqual(
            _fielding_percentage({"putouts": 80, "assists": 15, "total_chances": 100}),
            0.95,
        )

    def test_scoped_dashboard_restores_published_sample_denominators(self):
        from roster_scout.native_dashboard import _scoped_counts

        row = SimpleNamespace(
            counts_json={
                "at_bats": 39,
                "hits": 15,
                "walks": 3,
                "hit_by_pitch": 3,
                "sacrifice_flies": 1,
                "sacrifice_hits": 0,
            },
            sample_json={"ab": 39, "pa": 46},
        )

        counts = _scoped_counts(row, "batting")

        self.assertEqual(counts["plate_appearances"], 46)
        self.assertEqual(counts["at_bats"], 39)

    def test_split_cards_show_only_metrics_supported_by_published_counts(self):
        from roster_scout.native_dashboard import _split_card_values

        batting = _split_card_values(
            "batting", {"hits": 30, "at_bats": 100}
        )
        self.assertEqual(
            (batting["primaryLabel"], batting["primaryValue"]),
            ("AVG", 0.3),
        )
        self.assertEqual(
            (batting["secondaryLabel"], batting["secondaryValue"]),
            ("AB", 100),
        )

        pitching = _split_card_values(
            "pitching", {"hits_allowed": 27, "opponent_at_bats": 90}
        )
        self.assertEqual(
            (pitching["primaryLabel"], pitching["primaryValue"]),
            ("Opp AVG", 0.3),
        )
        self.assertEqual(
            (pitching["secondaryLabel"], pitching["secondaryValue"]),
            ("OAB", 90),
        )

    def test_leader_sorting_is_deterministic_and_keeps_missing_metrics_out(self):
        from roster_scout.native_dashboard import (
            _hitting_leaders,
            _pitching_leaders,
        )

        first, second, missing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        players = {
            first: {"name": "Alpha", "position": "OF"},
            second: {"name": "Bravo", "position": "1B"},
            missing: {"name": "Missing", "position": "C"},
        }
        base = {
            "at_bats": 20,
            "doubles": 1,
            "triples": 0,
            "home_runs": 1,
            "walks": 2,
            "hit_by_pitch": 0,
            "sacrifice_flies": 1,
            "sacrifice_hits": 0,
            "strikeouts": 5,
            "plate_appearances": 23,
        }
        hitters = _hitting_leaders(
            (
                (first, {**base, "hits": 8}),
                (second, {**base, "hits": 5}),
                (missing, {**base, "hits": None}),
            ),
            players,
        )
        self.assertEqual([item["name"] for item in hitters], ["Alpha", "Bravo"])

        pitching = {
            "innings_outs": 27,
            "hits_allowed": 6,
            "walks": 3,
            "strikeouts": 12,
        }
        pitchers = _pitching_leaders(
            (
                (first, {**pitching, "earned_runs": 2}),
                (second, {**pitching, "earned_runs": 4}),
            ),
            players,
        )
        self.assertEqual([item["name"] for item in pitchers], ["Alpha", "Bravo"])

    def test_fielding_and_player_views_share_the_selected_scope_counts(self):
        from roster_scout.native_dashboard import _fielding_leaders, _player_cards

        player_id = uuid.uuid4()
        players = {
            player_id: {"name": "Casey", "position": "SS", "jersey": "4"}
        }
        batting = {
            "at_bats": 20, "hits": 7, "doubles": 2, "triples": 0,
            "home_runs": 1, "walks": 3, "hit_by_pitch": 0,
            "sacrifice_flies": 1, "sacrifice_hits": 0, "strikeouts": 4,
            "plate_appearances": 24,
        }
        fielding = {
            "games": 8, "putouts": 12, "assists": 18, "errors": 1,
            "total_chances": 31,
        }
        rows = {
            "batting": (((player_id, batting),), "complete", None),
            "pitching": ((), "complete", None),
            "fielding": (((player_id, fielding),), "complete", None),
        }

        cards = _player_cards(rows, players)
        leaders = _fielding_leaders(rows["fielding"][0], players)

        self.assertEqual(cards[0]["batting"]["plateAppearances"], 24)
        self.assertEqual(cards[0]["fielding"]["totalChances"], 31)
        self.assertEqual(leaders[0]["name"], "Casey")
        self.assertEqual(leaders[0]["percentage"], 0.9677)


if __name__ == "__main__":
    unittest.main()
