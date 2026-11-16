"""Exact last-10 and season-conference scope contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import importlib
import importlib.util
from pathlib import Path
import re
import inspect
import unittest

from bs4 import BeautifulSoup


FIXTURES = Path(__file__).parent / "fixtures" / "ncaa_detail"


def scopes_api():
    return importlib.import_module("roster_scout.scopes")


def game(
    source_game_id: str,
    day: int,
    *,
    status: str = "completed",
    ordinal: int = 1,
    report_conference_id: str | None = "821",
    opponent_conference_id: str | None = "821",
    evidence_season_id: str | None = "17040",
    opponent_team_id: str | None = None,
):
    api = scopes_api()
    game_date = date(2026, 4, 1) + timedelta(days=day)
    return api.AuditedGame(
        source_game_id=source_game_id,
        fixture_fallback_key=None,
        season_id="17040",
        game_date=game_date,
        completed_at=datetime.combine(game_date, datetime.min.time(), tzinfo=UTC),
        status=status,
        opponent_team_id=opponent_team_id or f"team-{source_game_id}",
        report_team_conference_id=report_conference_id,
        opponent_conference_id=opponent_conference_id,
        conference_evidence_season_id=evidence_season_id,
        doubleheader_ordinal=ordinal,
        result_checksum=(source_game_id * 64)[:64],
        venue="home",
    )


class ScheduleFixtureTests(unittest.TestCase):
    def test_d1_d2_d3_fixtures_retain_exact_schedule_and_conference_evidence(self):
        for division, team_id, season_team_id in (
            ("d1", "234", "614606"),
            ("d2", "689", "615087"),
            ("d3", "633", "615349"),
        ):
            with self.subTest(division=division):
                text = (FIXTURES / division / "schedule.html").read_text(encoding="utf-8")
                self.assertNotRegex(text.casefold(), r"token=|cookie|authorization|browserless|@")
                soup = BeautifulSoup(text, "html.parser")
                root = soup.find("schedule-evidence")
                self.assertIsNotNone(root)
                self.assertEqual(root["data-schedule-contract-version"], "ncaa-schedule-v1")
                self.assertEqual(root["data-season-id"], "17040")
                self.assertEqual(root["data-team-id"], team_id)
                self.assertEqual(root["data-season-team-id"], season_team_id)
                self.assertEqual(root["data-schedule-complete"], "true")
                self.assertRegex(root["data-source-payload-sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(root["data-captured-at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
                games = root.find_all("game", recursive=False)
                self.assertGreater(len(games), 10)
                self.assertTrue(any(item["data-status"] == "completed" for item in games))
                self.assertTrue(any(item["data-status"] == "canceled" for item in games))
                for item in games:
                    self.assertRegex(item["data-date"], r"^2026-\d\d-\d\d$")
                    self.assertIn(item["data-status"], {
                        "completed", "canceled", "postponed", "no_contest", "scheduled"
                    })
                    self.assertIn(item["data-conference-evidence"], {"present", "source_absent"})
                    self.assertIn(item["data-opponent-identity"], {"present", "source_absent"})
                    self.assertRegex(item["data-result-sha256"], r"^[0-9a-f]{64}$")
                    if item["data-status"] == "completed":
                        self.assertRegex(item["data-source-game-id"], r"^\d+$")
                    if item["data-conference-evidence"] == "present":
                        self.assertRegex(item["data-opponent-conference-id"], r"^\d+$")

    def test_sanitized_fixture_parses_to_audited_games_without_name_inference(self):
        from roster_scout.ncaa_detail import parse_schedule_fixture

        parsed = parse_schedule_fixture(
            (FIXTURES / "d1" / "schedule.html").read_text(encoding="utf-8"),
            expected_season_id="17040",
            expected_team_id="234",
            expected_season_team_id="614606",
            expected_division="d1",
        )
        self.assertTrue(parsed.schedule_complete)
        self.assertEqual(parsed.team_conference_id, "821")
        audited = parsed.audited_games()
        self.assertEqual(len(audited), len(parsed.games))
        last_ten = scopes_api().select_last_ten_completed_games(
            audited, schedule_complete=parsed.schedule_complete
        )
        self.assertEqual(last_ten.actual_game_count, 10)
        self.assertEqual(last_ten.games[-1].source_game_id, "6582312")


class CanonicalScopeModelTests(unittest.TestCase):
    def test_game_scope_and_fielding_models_repeat_composite_ownership(self):
        from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
        from roster_scout.models import (
            Game, ScopedFieldingFact, SeasonConferenceEvidence,
            StatisticScope, StatisticScopeGame,
        )

        expected_constraints = {
            Game: {
                "uq_games_snapshot_owner", "fk_games_source_owner",
                "fk_games_report_conference_owner", "fk_games_opponent_conference_owner",
                "ck_games_identity_contract",
            },
            SeasonConferenceEvidence: {
                "uq_season_conference_evidence_snapshot_owner",
                "fk_season_conference_evidence_source_owner",
            },
            StatisticScope: {
                "uq_statistic_scopes_snapshot_owner",
                "fk_statistic_scopes_schedule_source_owner",
            },
            StatisticScopeGame: {
                "fk_statistic_scope_games_scope_owner",
                "fk_statistic_scope_games_game_owner",
            },
            ScopedFieldingFact: {
                "fk_scoped_fielding_facts_scope_snapshot_organization",
                "fk_scoped_fielding_facts_source_identity",
            },
        }
        for model, required in expected_constraints.items():
            with self.subTest(model=model.__name__):
                names = {
                    item.name for item in model.__table__.constraints
                    if isinstance(item, (CheckConstraint, ForeignKeyConstraint, UniqueConstraint))
                }
                names.update(
                    item.name for item in model.__table__.indexes if isinstance(item, Index)
                )
                self.assertTrue(required <= names, required - names)

    def test_game_identity_has_disjoint_source_and_fixture_only_unique_indexes(self):
        from roster_scout.models import Game

        indexes = {item.name: item for item in Game.__table__.indexes}
        self.assertEqual(
            str(indexes["uq_games_snapshot_source_identity"].dialect_options["postgresql"]["where"]),
            "source_game_id IS NOT NULL",
        )
        self.assertIn(
            "identity_kind = 'fixture_fallback'",
            str(indexes["uq_games_snapshot_fixture_identity"].dialect_options["postgresql"]["where"]),
        )

    def test_conference_selector_contains_no_name_or_current_affiliation_fallback(self):
        source = inspect.getsource(scopes_api().select_conference_games).casefold()
        self.assertNotIn("conference_name", source)
        self.assertNotIn("school_name", source)
        self.assertNotIn("current_conference", source)
        self.assertIn("conference_evidence_season_id", source)


class LastTenScopeTests(unittest.TestCase):
    def test_exact_last_ten_membership_contract(self):
        """RED sentinel: fail exactly once until the scoped selector exists."""
        self.assertIsNotNone(
            importlib.util.find_spec("roster_scout.scopes"),
            "exact last-10 scope selection is not implemented",
        )

    def test_selects_ten_latest_completed_games_in_deterministic_order(self):
        api = scopes_api()
        games = [game(str(index), index) for index in range(12)]
        result = api.select_last_ten_completed_games(tuple(reversed(games)), schedule_complete=True)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.actual_game_count, 10)
        self.assertEqual([item.source_game_id for item in result.games], [str(i) for i in range(2, 12)])
        self.assertEqual(result.start_date, games[2].game_date)
        self.assertEqual(result.end_date, games[-1].game_date)

    def test_sort_key_is_completed_time_then_doubleheader_ordinal_then_source_id(self):
        api = scopes_api()
        same_time = datetime(2026, 5, 1, tzinfo=UTC)
        games = tuple(
            replace(game(source_id, 30, ordinal=ordinal), completed_at=same_time)
            for source_id, ordinal in (("30", 2), ("20", 1), ("10", 1))
        )
        ordered = api.completed_game_sort_key
        self.assertEqual(
            [item.source_game_id for item in sorted(games, key=ordered)],
            ["10", "20", "30"],
        )

    def test_both_doubleheader_games_count_and_noncompleted_rows_do_not(self):
        api = scopes_api()
        games = (
            game("101", 1, ordinal=1),
            game("102", 1, ordinal=2),
            game("103", 2, status="canceled"),
            game("104", 3, status="postponed"),
            game("105", 4, status="no_contest"),
        )
        result = api.select_last_ten_completed_games(games, schedule_complete=True)
        self.assertEqual([item.source_game_id for item in result.games], ["101", "102"])
        self.assertEqual(result.actual_game_count, 2)

    def test_fewer_than_ten_is_complete_only_with_complete_schedule_evidence(self):
        api = scopes_api()
        games = tuple(game(str(index), index) for index in range(4))
        complete = api.select_last_ten_completed_games(games, schedule_complete=True)
        incomplete = api.select_last_ten_completed_games(games, schedule_complete=False)
        self.assertEqual((complete.status, complete.actual_game_count), ("complete", 4))
        self.assertEqual((incomplete.status, incomplete.reason), (
            "partial", "schedule_evidence_incomplete"
        ))
        self.assertNotEqual(incomplete.status, "complete")

    def test_production_selection_rejects_fixture_fallback_identity(self):
        api = scopes_api()
        fallback = replace(
            game("fixture", 1),
            source_game_id=None,
            fixture_fallback_key="2026-04-02:team-x:home:1:checksum",
        )
        result = api.select_last_ten_completed_games((fallback,), schedule_complete=True)
        self.assertEqual((result.status, result.reason), (
            "unavailable", "source_game_identity_missing"
        ))
        fixture_result = api.select_last_ten_completed_games(
            (fallback,), schedule_complete=True, allow_fixture_fallback=True
        )
        self.assertEqual(fixture_result.status, "complete")


class ConferenceScopeTests(unittest.TestCase):
    def test_conference_membership_uses_same_season_exact_ids(self):
        api = scopes_api()
        conference = game("1", 1, opponent_conference_id="821")
        nonconference = game("2", 2, opponent_conference_id="911")
        result = api.select_conference_games(
            (conference, nonconference), report_season_id="17040", schedule_complete=True
        )
        self.assertEqual(result.status, "complete")
        self.assertEqual([item.source_game_id for item in result.games], ["1"])

    def test_realignment_never_uses_current_affiliation(self):
        api = scopes_api()
        historical = game("1", 1, report_conference_id="821", opponent_conference_id="911")
        result = api.select_conference_games(
            (historical,), report_season_id="17040", schedule_complete=True
        )
        self.assertEqual(result.games, ())
        self.assertEqual(result.status, "complete")

    def test_missing_or_wrong_season_conference_evidence_is_not_zero(self):
        api = scopes_api()
        cases = (
            game("1", 1, opponent_conference_id=None),
            game("2", 2, report_conference_id=None),
            game("3", 3, evidence_season_id="16900"),
        )
        for audited in cases:
            with self.subTest(source_game_id=audited.source_game_id):
                result = api.select_conference_games(
                    (audited,), report_season_id="17040", schedule_complete=True
                )
                self.assertIn(result.status, {"partial", "unavailable"})
                self.assertEqual(result.reason, "conference_evidence_incomplete")
                self.assertEqual(result.games, ())


class ScopedAggregationTests(unittest.TestCase):
    def test_counts_are_summed_before_shared_metrics_and_samples_are_retained(self):
        api = scopes_api()
        facts = (
            api.ScopedCountFact("f1", "g1", "p1", "batting", {
                "hits": 1, "at_bats": 2, "walks": 1, "hit_by_pitch": 0,
                "sacrifice_flies": 0, "sacrifice_hits": 0,
            }),
            api.ScopedCountFact("f2", "g2", "p1", "batting", {
                "hits": 2, "at_bats": 3, "walks": 0, "hit_by_pitch": 0,
                "sacrifice_flies": 0, "sacrifice_hits": 0,
            }),
        )
        result = api.aggregate_scope_facts(
            facts, game_ids=("g1", "g2"), metric_codes=("avg", "pa")
        )
        self.assertEqual(result.counts["hits"], 3)
        self.assertEqual(result.counts["at_bats"], 5)
        self.assertEqual(result.metrics["avg"].value, Decimal("0.6"))
        self.assertEqual(result.metrics["avg"].sample["ab"], 5)
        self.assertEqual(result.metrics["pa"].sample["pa"], 6)
        self.assertEqual(result.contributing_fact_ids, ("f1", "f2"))
        self.assertEqual(result.game_count, 2)

    def test_pitching_and_fielding_samples_preserve_outs_bf_and_opportunities(self):
        api = scopes_api()
        pitching = api.aggregate_scope_facts((
            api.ScopedCountFact("p1", "g1", "pitcher", "pitching", {
                "earned_runs": 1, "innings_outs": 9, "batters_faced": 12,
            }),
        ), game_ids=("g1",), metric_codes=("era",))
        fielding = api.aggregate_scope_facts((
            api.ScopedCountFact("d1", "g1", "fielder", "fielding", {
                "putouts": 2, "assists": 1, "errors": 0, "total_chances": 3,
            }),
        ), game_ids=("g1",), metric_codes=())
        self.assertEqual(pitching.metrics["era"].sample["outs"], 9)
        self.assertEqual(pitching.metrics["era"].sample["bf"], 12)
        self.assertEqual(fielding.sample["opportunities"], 3)
        self.assertEqual(fielding.sample["game_count"], 1)

    def test_ambiguous_or_disconnected_game_log_fact_makes_scope_partial(self):
        api = scopes_api()
        ambiguous = api.ScopedCountFact(
            "f1", None, "p1", "batting", {"hits": 1, "at_bats": 2},
            link_status="ambiguous",
        )
        result = api.aggregate_scope_facts(
            (ambiguous,), game_ids=("g1",), metric_codes=("avg",)
        )
        self.assertEqual((result.status, result.reason), (
            "partial", "ambiguous_game_link"
        ))
        self.assertEqual(result.metrics, {})


if __name__ == "__main__":
    unittest.main()
