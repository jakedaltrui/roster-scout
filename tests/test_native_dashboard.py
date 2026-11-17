"""Native dashboard metric and leader projection contracts."""

from __future__ import annotations

import unittest
import uuid


class NativeDashboardProjectionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
