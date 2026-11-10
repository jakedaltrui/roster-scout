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


class CanonicalIdentityResolutionTests(unittest.TestCase):
    def test_idless_identity_decision_table(self):
        cases = (
            (
                "unique single row",
                [{"Player": "Unique", "Jersey": "1"}],
                [],
                ("fallback",),
            ),
            (
                "reciprocal unique pair",
                [{"Player": "Two Way", "Jersey": "7"}],
                [{"Player": "Two Way", "Jersey": "7", "IP": "1.0"}],
                ("fallback",),
            ),
            (
                "distinguishable teammates",
                [
                    {"Player": "Same Name", "Jersey": "1", "Pos": "OF"},
                    {"Player": "Same Name", "Jersey": "2", "Pos": "C"},
                ],
                [],
                ("fallback", "fallback"),
            ),
            (
                "duplicate support signature",
                [
                    {"Player": "Same Name", "Jersey": "1", "Pos": "OF"},
                    {"Player": "Same Name", "Jersey": "1", "Pos": "OF"},
                ],
                [],
                ("unresolved", "unresolved"),
            ),
            (
                "missing support",
                [{"Player": "Same Name"}, {"Player": "Same Name"}],
                [],
                ("unresolved", "unresolved"),
            ),
            (
                "one batting name to many pitching rows",
                [{"Player": "Same Name"}],
                [
                    {"Player": "Same Name", "Jersey": "1", "IP": "1.0"},
                    {"Player": "Same Name", "Jersey": "2", "IP": "1.0"},
                ],
                ("unresolved", "unresolved", "unresolved"),
            ),
            (
                "many batting rows to one pitching name",
                [
                    {"Player": "Same Name", "Jersey": "1"},
                    {"Player": "Same Name", "Jersey": "2"},
                ],
                [{"Player": "Same Name", "IP": "1.0"}],
                ("unresolved", "unresolved", "unresolved"),
            ),
            (
                "conflicting support identifies separate teammates",
                [{"Player": "Same Name", "Jersey": "1", "Pos": "OF"}],
                [{"Player": "Same Name", "Jersey": "2", "Pos": "P", "IP": "1.0"}],
                ("fallback", "fallback"),
            ),
        )
        for label, batting, pitching, expected_statuses in cases:
            with self.subTest(label=label):
                canonical = canonicalize_ncaa_collection(collection(batting, pitching))
                self.assertEqual(
                    tuple(player.identity_status for player in canonical.players),
                    expected_statuses,
                )
                self.assertEqual(
                    len({player.identity_key for player in canonical.players}),
                    len(canonical.players),
                )
                for player in canonical.players:
                    self.assertTrue(
                        player.identity_key.startswith(f"{player.identity_status}:")
                    )

    def test_identity_keys_are_stable_and_team_season_scoped(self):
        batting = [
            {"Player": "Same Name", "Jersey": "1"},
            {"Player": "Same Name", "Jersey": "2"},
        ]
        pitching = [{"Player": "Same Name", "IP": "1.0"}]
        first = canonicalize_ncaa_collection(collection(batting, pitching, "team-a"))
        repeated = canonicalize_ncaa_collection(collection(batting, pitching, "team-a"))
        other_team = canonicalize_ncaa_collection(collection(batting, pitching, "team-b"))

        self.assertEqual(
            tuple(player.identity_key for player in first.players),
            tuple(player.identity_key for player in repeated.players),
        )
        self.assertTrue(
            set(player.identity_key for player in first.players).isdisjoint(
                player.identity_key for player in other_team.players
            )
        )

    def test_authoritative_id_remains_authoritative(self):
        canonical = canonicalize_ncaa_collection(collection(
            [{"Player": "Known Player", "_player_id": "42"}],
            [{"Player": "Known Player", "_player_id": "42", "IP": "1.0"}],
        ))
        self.assertEqual(len(canonical.players), 1)
        self.assertEqual(canonical.players[0].identity_status, "authoritative")
        self.assertEqual(canonical.players[0].identity_key, "ncaa:42")


class CanonicalIdentityTests(unittest.TestCase):
    def test_name_only_pair_remains_two_unresolved_identities(self):
        canonical = canonicalize_ncaa_collection(collection(
            [{"Player": "Same Name"}],
            [{"Player": "Same Name", "IP": "1.0"}],
        ))

        self.assertEqual(len(canonical.players), 2)
        self.assertEqual(
            tuple(player.identity_status for player in canonical.players),
            ("unresolved", "unresolved"),
        )
        self.assertEqual(
            len({player.identity_key for player in canonical.players}),
            2,
        )

    def test_one_sided_support_does_not_corroborate_a_merge(self):
        cases = (
            (
                [{"Player": "Same Name", "Jersey": "7"}],
                [{"Player": "Same Name", "IP": "1.0"}],
            ),
            (
                [{"Player": "Same Name"}],
                [{"Player": "Same Name", "Pos": "P", "IP": "1.0"}],
            ),
        )
        for batting, pitching in cases:
            with self.subTest(batting=batting, pitching=pitching):
                canonical = canonicalize_ncaa_collection(collection(batting, pitching))
                self.assertEqual(len(canonical.players), 2)
                self.assertEqual(
                    tuple(player.identity_status for player in canonical.players),
                    ("unresolved", "unresolved"),
                )
                self.assertEqual(
                    len({player.identity_key for player in canonical.players}),
                    2,
                )

    def test_shared_nonempty_jersey_or_position_corroborates_a_merge(self):
        cases = (
            (
                [{"Player": "Two Way", "Jersey": "7"}],
                [{"Player": "Two Way", "Jersey": "7", "IP": "1.0"}],
            ),
            (
                [{"Player": "Two Way", "Pos": "RHP"}],
                [{"Player": "Two Way", "Pos": "  rhp ", "IP": "1.0"}],
            ),
        )
        for batting, pitching in cases:
            with self.subTest(batting=batting, pitching=pitching):
                canonical = canonicalize_ncaa_collection(collection(batting, pitching))
                self.assertEqual(len(canonical.players), 1)
                self.assertEqual(canonical.players[0].identity_status, "fallback")

    def test_conflicting_nonempty_support_keeps_teammates_separate(self):
        canonical = canonicalize_ncaa_collection(collection(
            [{"Player": "Same Name", "Jersey": "7", "Pos": "OF"}],
            [{"Player": "Same Name", "Jersey": "8", "Pos": "P", "IP": "1.0"}],
        ))

        self.assertEqual(len(canonical.players), 2)
        self.assertEqual(
            tuple(player.identity_status for player in canonical.players),
            ("fallback", "fallback"),
        )
        self.assertEqual(
            len({player.identity_key for player in canonical.players}),
            2,
        )


class CanonicalDataTests(unittest.TestCase):
    def test_negative_count_values_are_rejected(self):
        batting_cases = (
            ("AB", "at-bats"),
            ("H", "hits"),
            ("BB", "walks"),
            ("K", "strikeouts"),
        )
        for source_key, label in batting_cases:
            with self.subTest(section="batting", field=label):
                with self.assertRaisesRegex(ValueError, "cannot be negative"):
                    canonicalize_ncaa_collection(collection([
                        {"Player": "Negative", source_key: "-1", "_player_id": "1"},
                    ], []))

        pitching_cases = (
            ("H", "hits allowed"),
            ("ER", "earned runs"),
            ("BB", "walks"),
            ("SO", "strikeouts"),
        )
        for source_key, label in pitching_cases:
            with self.subTest(section="pitching", field=label):
                with self.assertRaisesRegex(ValueError, "cannot be negative"):
                    canonicalize_ncaa_collection(collection([], [
                        {"Player": "Negative", source_key: "-1", "_player_id": "1"},
                    ]))

        with self.assertRaisesRegex(ValueError, "Invalid baseball innings"):
            canonicalize_ncaa_collection(collection([], [
                {"Player": "Negative", "IP": "-1.0", "_player_id": "1"},
            ]))

    def test_zero_counts_remain_distinct_from_missing_counts(self):
        canonical = canonicalize_ncaa_collection(collection([
            {
                "Player": "Zero",
                "AB": "0",
                "H": "0",
                "BB": "0",
                "K": "0",
                "_player_id": "1",
            },
            {"Player": "Missing", "_player_id": "2"},
        ], []))
        facts = {player.display_name: fact.values for player, fact in zip(
            canonical.players, canonical.facts, strict=True
        )}
        self.assertEqual(
            facts["Zero"],
            {"at_bats": 0, "hits": 0, "walks": 0, "strikeouts": 0},
        )
        self.assertEqual(
            facts["Missing"],
            {"at_bats": None, "hits": None, "walks": None, "strikeouts": None},
        )

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
        self.assertEqual(
            {player.identity_status for player in canonical.players},
            {"unresolved"},
        )

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
