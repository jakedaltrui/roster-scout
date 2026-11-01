import hashlib
import unittest
from datetime import datetime, timezone

import roster_scraper as rs
from roster_scout.canonical import (
    IdentityConflictError,
    canonicalize_ncaa_collection,
    innings_to_outs,
)


def collection(batting, pitching, season_team_id="615087"):
    metadata = rs.NcaaCollectionMetadata(datetime(2026, 3, 1, tzinfo=timezone.utc))
    sections = []
    for name, rows, category in (("batting", batting, "100"), ("pitching", pitching, "200")):
        converted = []
        for ordinal, row in enumerate(rows):
            raw = {key: value for key, value in row.items() if not key.startswith("_")}
            converted.append(rs.NcaaSourceRow(
                name, ordinal, f"https://stats.ncaa.org/{name}",
                row.get("_player_id") or None, row.get("_player_url") or None,
                raw, hashlib.sha256(name.encode()).hexdigest(),
            ))
        sections.append(rs.NcaaSection(
            name, f"https://stats.ncaa.org/{name}", category,
            hashlib.sha256(name.encode()).hexdigest(), tuple(converted), not rows,
        ))
    return rs.NcaaCollection(
        rs.NcaaTeam("99", "Test", season_team_id, "d1"),
        rs.NcaaSeason("2026", 2026, "100", "200"), metadata,
        tuple(sections), (), "Test",
    )


class CanonicalDataTests(unittest.TestCase):
    def test_innings_are_outs_and_missing_is_not_zero(self):
        self.assertEqual(innings_to_outs("7.2"), 23)
        self.assertIsNone(innings_to_outs(""))
        canonical = canonicalize_ncaa_collection(collection([], [
            {"Player": "Pitcher", "IP": "7.2", "_player_id": "7"},
            {"Player": "Blank", "IP": "", "_player_id": "8"},
        ]))
        pitching = [fact for fact in canonical.facts if fact.section == "pitching"]
        self.assertEqual(pitching[0].values["innings_outs"], 23)
        self.assertEqual(pitching[0].values["innings_raw"], "7.2")
        self.assertIsNone(pitching[1].values["innings_outs"])

    def test_authoritative_identity_and_player_url_survive(self):
        canonical = canonicalize_ncaa_collection(collection([
            {"Player": "Two Way", "AB": "10", "H": "3", "_player_id": "42", "_player_url": "https://stats.ncaa.org/players/42"},
        ], [
            {"Player": "Two Way", "IP": "2.0", "_player_id": "42", "_player_url": "https://stats.ncaa.org/players/42"},
        ]))
        self.assertEqual(len(canonical.players), 1)
        self.assertEqual(canonical.players[0].source_player_id, "42")
        self.assertTrue(all(record.player_url.endswith("/players/42") for record in canonical.source_records))
        self.assertEqual({fact.section for fact in canonical.facts}, {"batting", "pitching"})

    def test_unique_idless_rows_merge_only_within_team_season(self):
        one = canonicalize_ncaa_collection(collection(
            [{"Player": "No Id", "Jersey": "12", "AB": "5"}],
            [{"Player": "No Id", "Jersey": "12", "IP": "1.0"}],
        ))
        other = canonicalize_ncaa_collection(collection(
            [{"Player": "No Id", "Jersey": "12", "AB": "5"}], [], "other-team",
        ))
        self.assertEqual(len(one.players), 1)
        self.assertNotEqual(one.players[0].identity_key, other.players[0].identity_key)

    def test_ambiguous_same_name_teammates_remain_separate(self):
        canonical = canonicalize_ncaa_collection(collection([
            {"Player": "Same Name", "Jersey": "1", "Pos": "OF"},
            {"Player": "Same Name", "Jersey": "2", "Pos": "C"},
        ], [
            {"Player": "Same Name", "IP": "1.0"},
        ]))
        self.assertEqual(len(canonical.players), 3)
        self.assertEqual(len({player.identity_key for player in canonical.players}), 3)

    def test_conflicting_names_for_one_ncaa_id_fail(self):
        with self.assertRaisesRegex(IdentityConflictError, "NCAA player ID 42"):
            canonicalize_ncaa_collection(collection(
                [{"Player": "First Player", "_player_id": "42"}],
                [{"Player": "Different Player", "_player_id": "42"}],
            ))

    def test_missing_numeric_values_and_metric_lineage(self):
        canonical = canonicalize_ncaa_collection(collection([
            {"Player": "Calculated", "AB": "4", "H": "1", "BB": "-"},
            {"Player": "Published", "AB": "4", "H": "2", "BA": ".500"},
        ], []))
        facts = {fact.identity_key: fact for fact in canonical.facts}
        calculated_player = next(p for p in canonical.players if p.display_name == "Calculated")
        self.assertIsNone(facts[calculated_player.identity_key].values["walks"])
        self.assertEqual(
            {metric.provenance for metric in canonical.metrics},
            {"calculated", "source-provided"},
        )
        self.assertTrue(all(metric.definition_version == "ncaa-1.0" for metric in canonical.metrics))


if __name__ == "__main__":
    unittest.main()
