"""Transfer-aware NCAA career and current-season game-log contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import re
import unittest


FIXTURES = Path(__file__).parent / "fixtures" / "ncaa_detail"
REPRESENTATIVES = {
    "d1": ("9698671", "614606"),
    "d2": ("11219748", "615087"),
    "d3": ("9697775", "615349"),
}


def history_api():
    from roster_scout import ncaa_detail

    return ncaa_detail


def fixture(division: str) -> str:
    return (FIXTURES / division / "player_detail.html").read_text(encoding="utf-8")


def parse(division: str):
    player_id, season_team_id = REPRESENTATIVES[division]
    return history_api().parse_player_detail_fixture(
        fixture(division),
        expected_player_id=player_id,
        expected_season_id="17040",
        expected_season_team_id=season_team_id,
        expected_division=division,
    )


class CareerSeasonTests(unittest.TestCase):
    def test_stable_player_history_contract(self):
        """Named RED sentinel: imports succeed before the missing contract fails."""
        api = history_api()
        self.assertTrue(
            hasattr(api, "parse_player_detail_fixture"),
            "stable-player history contract is not implemented",
        )

    def test_d1_d2_d3_keep_every_career_row_typed_and_source_keyed(self):
        for division, (player_id, _team_id) in REPRESENTATIVES.items():
            with self.subTest(division=division):
                detail = parse(division)
                self.assertEqual(detail.source_player_id, player_id)
                self.assertEqual(detail.active_season_id, "17040")
                self.assertEqual(detail.division, division)
                self.assertEqual(
                    {row.stat_type for row in detail.career_seasons},
                    {"batting", "pitching", "fielding"},
                )
                self.assertTrue(all(row.row_id for row in detail.career_seasons))
                self.assertTrue(all(row.source_season_team_id for row in detail.career_seasons))
                self.assertTrue(all(row.source_stat_category_id for row in detail.career_seasons))
                self.assertTrue(all(row.source_path == f"/players/{player_id}" for row in detail.career_seasons))

    def test_transfer_rows_remain_distinct_team_seasons_under_one_player(self):
        html = fixture("d1").replace(
            'data-school-label="Florida St." data-season-label="2022-23" '
            'data-source-season-team-id="548146"',
            'data-division="d2" data-school-label="Transfer School" '
            'data-season-label="2022-23" data-source-season-id="15000" '
            'data-source-season-team-id="548146"',
            1,
        )
        detail = history_api().parse_player_detail_fixture(
            html,
            expected_player_id="9698671",
            expected_season_id="17040",
            expected_season_team_id="614606",
            expected_division="d1",
        )
        transferred = next(
            row for row in detail.career_seasons
            if row.stat_type == "batting" and row.source_season_team_id == "548146"
        )
        current = next(
            row for row in detail.career_seasons
            if row.stat_type == "batting" and row.source_season_team_id == "614606"
        )
        self.assertEqual(detail.source_player_id, transferred.source_player_id)
        self.assertEqual(detail.source_player_id, current.source_player_id)
        self.assertEqual((transferred.school_name, transferred.division), ("Transfer School", "d2"))
        self.assertNotEqual(transferred.source_season_team_id, current.source_season_team_id)

    def test_player_url_is_fixed_host_and_numeric_only(self):
        build = history_api().build_player_detail_url
        self.assertEqual(
            build("9698671", "15867"),
            "https://stats.ncaa.org/players/9698671?year_stat_category_id=15867",
        )
        for player_id, category_id in (
            ("9698671@example.test", "15867"),
            ("9698671", "15867&next=https://example.test"),
            ("", "15867"),
        ):
            with self.subTest(player_id=player_id, category_id=category_id):
                with self.assertRaises(ValueError):
                    build(player_id, category_id)

    def test_role_specific_player_contract_accepts_only_requested_sections(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(fixture("d1"), "html.parser")
        for stat_type in ("pitching", "fielding"):
            soup.find("stat-section", {"data-stat-type": stat_type}).decompose()
        detail = history_api().parse_player_detail_fixture(
            str(soup),
            expected_player_id="9698671",
            expected_season_id="17040",
            expected_season_team_id="614606",
            expected_division="d1",
        )

        self.assertTrue(detail.game_logs)
        self.assertEqual(
            {row.stat_type for row in (*detail.career_seasons, *detail.game_logs)},
            {"batting"},
        )

    def test_career_models_repeat_snapshot_player_and_source_ownership(self):
        from roster_scout.models import CareerAggregate, CareerSeasonFact

        season_names = {item.name for item in CareerSeasonFact.__table__.constraints}
        aggregate_names = {item.name for item in CareerAggregate.__table__.constraints}
        self.assertIn("fk_career_season_facts_snapshot_organization", season_names)
        self.assertIn("fk_career_season_facts_player_snapshot_organization", season_names)
        self.assertIn("fk_career_season_facts_source_identity", season_names)
        self.assertIn("fk_career_aggregates_snapshot_organization", aggregate_names)
        self.assertIn("fk_career_aggregates_player_snapshot_organization", aggregate_names)


class CareerAggregateTests(unittest.TestCase):
    def rows(self):
        api = history_api()
        base = next(row for row in parse("d2").career_seasons if row.stat_type == "batting")
        return (
            replace(
                base,
                row_id="season-a",
                season_label="2024-25",
                source_season_id="16000",
                source_season_team_id="600001",
                division="d2",
                counts=MappingProxyType({"hits": 10, "at_bats": 20}),
                rates=MappingProxyType({"batting_average": Decimal(".500")}),
            ),
            replace(
                base,
                row_id="season-b",
                season_label="2025-26",
                source_season_id="17040",
                source_season_team_id="615087",
                division="d2",
                counts=MappingProxyType({"hits": 1, "at_bats": 10}),
                rates=MappingProxyType({"batting_average": Decimal(".100")}),
            ),
        )

    def test_aggregate_sums_counts_then_uses_shared_metric_registry(self):
        aggregate = history_api().aggregate_career_seasons(
            self.rows(), stat_type="batting", metric_codes=("avg",)
        )
        self.assertEqual(aggregate.status, "complete")
        self.assertEqual(aggregate.included_row_ids, ("season-a", "season-b"))
        self.assertEqual(aggregate.counts, {"at_bats": 30, "hits": 11})
        self.assertEqual(aggregate.metrics["avg"].value, Decimal(11) / Decimal(30))
        self.assertNotEqual(aggregate.metrics["avg"].value, Decimal(".300"))

    def test_missing_or_incompatible_rows_are_excluded_with_exact_reasons(self):
        valid, second = self.rows()
        missing_team = replace(second, row_id="missing-team", source_season_team_id=None)
        missing_count = replace(
            second,
            row_id="missing-count",
            counts=MappingProxyType({"hits": None, "at_bats": 10}),
        )
        wrong_type = replace(second, row_id="wrong-type", stat_type="pitching")
        aggregate = history_api().aggregate_career_seasons(
            (valid, missing_team, missing_count, wrong_type),
            stat_type="batting",
            metric_codes=("avg",),
        )
        self.assertEqual(aggregate.included_row_ids, ("season-a",))
        self.assertEqual(
            aggregate.excluded_rows,
            {
                "missing-team": "missing_team_identity",
                "missing-count": "missing_required_count",
                "wrong-type": "incompatible_stat_type",
            },
        )


class GameLogTests(unittest.TestCase):
    def test_published_game_log_blanks_are_sourced_zeroes(self):
        detail = parse("d3")
        row = next(
            item for item in detail.game_logs
            if item.stat_type == "batting" and item.source_game_id == "6537130"
        )
        self.assertEqual(row.counts["at_bats"], 4)
        self.assertEqual(row.counts["hits"], 1)
        self.assertEqual(row.counts["home_runs"], 0)
        self.assertEqual(row.counts["walks"], 0)
        self.assertEqual(row.counts["hit_by_pitch"], 0)
        self.assertEqual(row.counts["sacrifice_flies"], 0)

        career = next(
            item for item in detail.career_seasons
            if item.stat_type == "batting" and item.season_label == "2022-23"
        )
        self.assertIsNone(career.counts["home_runs"])

    def test_only_active_season_logs_are_emitted_and_remain_typed(self):
        for division in REPRESENTATIVES:
            with self.subTest(division=division):
                detail = parse(division)
                self.assertTrue(detail.game_logs)
                self.assertTrue(all(row.source_season_id == "17040" for row in detail.game_logs))
                self.assertEqual(
                    {row.stat_type for row in detail.game_logs},
                    {"batting", "pitching", "fielding"},
                )
                self.assertTrue(all(isinstance(row.game_date, date) for row in detail.game_logs))
                self.assertTrue(all(row.opponent_label for row in detail.game_logs))

    def test_exact_contest_ids_link_to_schedule_and_unknown_ids_stay_partial(self):
        api = history_api()
        detail = parse("d1")
        schedule = api.parse_schedule_fixture(
            (FIXTURES / "d1" / "schedule.html").read_text(encoding="utf-8"),
            expected_season_id="17040",
            expected_team_id="234",
            expected_season_team_id="614606",
            expected_division="d1",
        )
        source_ids = {game.source_game_id for game in schedule.games if game.source_game_id}
        exact = next(row for row in detail.game_logs if row.source_game_id in source_ids)
        linked = api.link_current_season_game_logs((exact,), schedule.games)
        self.assertEqual(linked[0].link_status, "linked")
        self.assertEqual(linked[0].source_game_id, exact.source_game_id)

        missing = replace(exact, row_id="missing-game", source_game_id="999999999")
        unavailable = api.link_current_season_game_logs((missing,), schedule.games)
        self.assertEqual(unavailable[0].link_status, "unmatched")
        self.assertIsNone(unavailable[0].game_identity)

    def test_game_log_models_use_existing_composite_game_identity(self):
        from roster_scout.models import (
            BattingGameLogFact,
            FieldingGameLogFact,
            PitchingGameLogFact,
        )

        for model, prefix in (
            (BattingGameLogFact, "batting_game_log_facts"),
            (PitchingGameLogFact, "pitching_game_log_facts"),
            (FieldingGameLogFact, "fielding_game_log_facts"),
        ):
            with self.subTest(model=model.__name__):
                names = {item.name for item in model.__table__.constraints}
                self.assertIn(f"fk_{prefix}_game_owner", names)
                self.assertIn(f"fk_{prefix}_player_snapshot_organization", names)
                self.assertIn(f"fk_{prefix}_source_identity", names)
                game_fk = next(
                    item for item in model.__table__.foreign_key_constraints
                    if item.name == f"fk_{prefix}_game_owner"
                )
                self.assertEqual(
                    tuple(column.name for column in game_fk.columns),
                    ("game_id", "snapshot_id", "organization_id"),
                )


class HistoryCompletenessTests(unittest.TestCase):
    def test_idless_roster_players_are_explicitly_unavailable(self):
        status = history_api().history_availability(None)
        self.assertEqual(status.status, "unavailable")
        self.assertEqual(status.reason, "missing_stable_player_id")
        self.assertEqual(status.completed, 0)

    def test_wrong_player_or_source_season_identity_fails_closed(self):
        api = history_api()
        for wrong_player, wrong_season in (("1", "17040"), ("9698671", "1")):
            with self.subTest(player=wrong_player, season=wrong_season):
                with self.assertRaises(api.PlayerDetailContractError):
                    api.parse_player_detail_fixture(
                        fixture("d1"),
                        expected_player_id=wrong_player,
                        expected_season_id=wrong_season,
                        expected_season_team_id="614606",
                        expected_division="d1",
                    )

    def test_missing_historical_team_or_division_is_disclosed_not_guessed(self):
        historical = [
            row for row in parse("d1").career_seasons
            if row.stat_type == "batting"
            and row.source_season_team_id != "614606"
        ]
        self.assertTrue(historical)
        self.assertTrue(all(row.division is None for row in historical))
        aggregate = history_api().aggregate_career_seasons(
            historical, stat_type="batting", metric_codes=("avg",)
        )
        self.assertEqual(aggregate.status, "unavailable")
        self.assertTrue(all(
            reason in {"missing_division", "missing_season_identity"}
            for reason in aggregate.excluded_rows.values()
        ))


class CollectionBudgetTests(unittest.TestCase):
    def test_player_history_requests_only_categories_with_season_activity(self):
        from roster_scout.jobs import _active_player_history_types

        sections = (
            SimpleNamespace(name="batting", rows=(
                {"_player_id": "10", "AB": "189", "GP": "56"},
                {"_player_id": "20", "AB": "", "GP": "15"},
                {"_player_id": "30", "AB": "1", "GP": "19"},
            )),
            SimpleNamespace(name="pitching", rows=(
                {"_player_id": "20", "IP": "77.0", "App": "15"},
                {"_player_id": "30", "IP": "2.0", "App": "2"},
            )),
            SimpleNamespace(name="fielding", rows=(
                {"_player_id": "10", "TC": "203"},
                {"_player_id": "20", "TC": "19"},
                {"_player_id": "30", "TC": "2"},
                {"_player_id": "40", "TC": "0", "GP": "5"},
            )),
        )

        player_types, unavailable = _active_player_history_types(sections)

        self.assertEqual(unavailable, 0)
        self.assertEqual(player_types, {
            "10": ("batting", "fielding"),
            "20": ("pitching", "fielding"),
            "30": ("batting", "pitching", "fielding"),
        })

    def test_shared_phase3_request_ledger_contract(self):
        """Named RED sentinel: imports/preflight succeed before one missing contract fails."""
        from roster_scout import models

        self.assertTrue(
            hasattr(models, "RequestLedger"),
            "shared Phase 3 request ledger is not implemented",
        )

    def test_collection_limits_have_bounded_defaults_and_deterministic_overrides(self):
        from roster_scout.jobs import load_phase3_collection_limits

        defaults = load_phase3_collection_limits({})
        self.assertEqual(defaults.navigation_concurrency, 2)
        self.assertEqual(defaults.live_page_budget, 200)
        self.assertEqual(defaults.navigation_timeout_seconds, 45)
        self.assertEqual(defaults.history_wall_time_seconds, 10 * 60)
        self.assertEqual(defaults.wall_time_seconds, 18 * 60)

        overridden = load_phase3_collection_limits({
            "PHASE3_NAVIGATION_CONCURRENCY": "1",
            "PHASE3_LIVE_PAGE_BUDGET": "12",
            "PHASE3_NAVIGATION_TIMEOUT_SECONDS": "8",
            "PHASE3_HISTORY_WALL_TIME_SECONDS": "60",
            "PHASE3_BUILD_WALL_TIME_SECONDS": "90",
        })
        self.assertEqual(
            (
                overridden.navigation_concurrency,
                overridden.live_page_budget,
                overridden.navigation_timeout_seconds,
                overridden.history_wall_time_seconds,
                overridden.wall_time_seconds,
            ),
            (1, 12, 8, 60, 90),
        )

        invalid = (
            ("PHASE3_NAVIGATION_CONCURRENCY", "0"),
            ("PHASE3_NAVIGATION_CONCURRENCY", "3"),
            ("PHASE3_LIVE_PAGE_BUDGET", "0"),
            ("PHASE3_LIVE_PAGE_BUDGET", "501"),
            ("PHASE3_NAVIGATION_TIMEOUT_SECONDS", "4"),
            ("PHASE3_NAVIGATION_TIMEOUT_SECONDS", "121"),
            ("PHASE3_HISTORY_WALL_TIME_SECONDS", "29"),
            ("PHASE3_HISTORY_WALL_TIME_SECONDS", "601"),
            ("PHASE3_BUILD_WALL_TIME_SECONDS", "59"),
            ("PHASE3_BUILD_WALL_TIME_SECONDS", "1801"),
            ("PHASE3_LIVE_PAGE_BUDGET", "email@example.edu"),
        )
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    load_phase3_collection_limits({key: value})

    def test_bounded_navigation_map_uses_parallelism_and_preserves_order(self):
        from threading import Barrier, Lock
        from roster_scout.jobs import _map_with_bounded_concurrency

        barrier = Barrier(2)
        lock = Lock()
        active = 0
        maximum_active = 0

        def worker(value):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            barrier.wait(timeout=2)
            with lock:
                active -= 1
            return value * 10

        result = _map_with_bounded_concurrency(
            (2, 1), worker, max_workers=2
        )

        self.assertEqual(result, (20, 10))
        self.assertEqual(maximum_active, 2)

    def test_player_history_has_a_smaller_deadline_than_the_dashboard_build(self):
        from roster_scout.jobs import _bounded_history_deadline

        self.assertEqual(
            _bounded_history_deadline(1_000, 180, now=100),
            280,
        )
        self.assertEqual(
            _bounded_history_deadline(200, 180, now=100),
            200,
        )

    def test_connection_loss_at_collection_wall_becomes_classified_timeout(self):
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError

        from roster_scout.collection_control import CollectionDeadlineExceeded
        from roster_scout.jobs import _reserve_until_deadline

        def disconnected():
            raise OperationalError("UPDATE report_jobs", {}, Exception("closed"))

        with patch("roster_scout.jobs.time.monotonic", return_value=10.0):
            with self.assertRaises(CollectionDeadlineExceeded):
                _reserve_until_deadline(disconnected, deadline_monotonic=10.0)

    def test_advisory_heartbeat_keeps_long_collection_connection_active(self):
        from types import SimpleNamespace
        from threading import Event
        from unittest.mock import Mock

        from roster_scout.database import session_advisory_heartbeat

        pinged = Event()
        connection = Mock()
        connection.exec_driver_sql.side_effect = lambda statement: pinged.set()
        claim = SimpleNamespace(connection=connection)

        with session_advisory_heartbeat(claim, interval_seconds=0.01):
            self.assertTrue(pinged.wait(timeout=0.5))

        connection.exec_driver_sql.assert_called_with("SELECT 1")
        connection.commit.assert_called()

    def test_unavailable_split_scopes_require_category_identities(self):
        from datetime import UTC, datetime

        from roster_scout.jobs import unavailable_collected_split_sections

        with self.assertRaises(ValueError):
            unavailable_collected_split_sections(
                source_team_id="267",
                source_season_id="17040",
                source_season_team_id="614825",
                category_ids={"batting": None, "pitching": "15868"},
                status="unavailable",
                reason="source_unavailable",
                collected_at=datetime.now(UTC),
            )

    def test_one_shared_budget_accounts_for_planned_cache_and_live_outcomes(self):
        from roster_scout.jobs import CollectionBudget, load_phase3_collection_limits

        budget = CollectionBudget(load_phase3_collection_limits({
            "PHASE3_LIVE_PAGE_BUDGET": "2",
        }))
        first = budget.plan("fielding", "ncaa:fielding:123")
        second = budget.plan("player_history", "ncaa:player:456:batting")
        third = budget.plan("game_logs", "ncaa:player:456:pitching")
        budget.settle(first, outcome="cache_hit")
        budget.settle(second, outcome="live_complete")
        budget.settle(third, outcome="live_complete")

        self.assertEqual(budget.cache_count, 1)
        self.assertEqual(budget.live_count, 2)
        self.assertEqual(budget.remaining, 0)
        with self.assertRaisesRegex(ValueError, "request_budget_exhausted"):
            budget.settle(
                budget.plan("splits", "ncaa:split:789"),
                outcome="live_complete",
            )

        for unsafe in (
            "https://stats.ncaa.org/path?token=secret",
            "player@example.edu",
            "authorization: bearer secret",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    budget.plan("fielding", unsafe)

    def test_shared_player_page_reserves_once_and_settles_every_family(self):
        from roster_scout.jobs import CollectionBudget, load_phase3_collection_limits

        budget = CollectionBudget(load_phase3_collection_limits({
            "PHASE3_LIVE_PAGE_BUDGET": "1",
        }))
        entries = tuple(
            budget.plan(f"history:{stat_type}", f"ncaa:player:456:{stat_type}")
            for stat_type in ("batting", "pitching", "fielding")
        )
        self.assertTrue(budget.reserve_live(entries, page_count=1))
        self.assertEqual(budget.remaining, 0)
        for entry in entries:
            budget.settle(entry, outcome="live_complete")
        self.assertEqual(budget.live_count, 1)
        self.assertEqual(budget.unsettled, frozenset())

    def test_concurrent_reservations_cannot_overbook_one_live_slot(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from roster_scout.jobs import CollectionBudget, load_phase3_collection_limits

        budget = CollectionBudget(load_phase3_collection_limits({
            "PHASE3_LIVE_PAGE_BUDGET": "1",
        }))
        entries = tuple(
            budget.plan("splits", f"ncaa:split:{index}")
            for index in range(2)
        )
        barrier = Barrier(3)

        def reserve(entry):
            barrier.wait()
            return budget.reserve_live((entry,), page_count=1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reserve, entry) for entry in entries]
            barrier.wait()
            reserved = [future.result() for future in futures]

        self.assertEqual(sorted(reserved), [False, True])
        for entry, succeeded in zip(entries, reserved):
            budget.settle(
                entry,
                outcome="live_complete" if succeeded else "budget_exhausted",
            )
        self.assertEqual(budget.live_count, 1)
        self.assertEqual(budget.unsettled, frozenset())

    def test_one_request_per_stable_player_type_and_no_per_season_refetch(self):
        from roster_scraper import plan_ncaa_player_detail_requests

        plan = plan_ncaa_player_detail_requests(
            ("9698671", "9698671", None),
            {"batting": "15867", "pitching": "15868", "fielding": "15869"},
            request_budget=3,
        )
        self.assertEqual(len(plan.scheduled), 3)
        self.assertEqual({item.source_player_id for item in plan.scheduled}, {"9698671"})
        self.assertEqual({item.stat_type for item in plan.scheduled}, {"batting", "pitching", "fielding"})
        self.assertEqual(plan.remaining, ())
        self.assertEqual(plan.unavailable_player_count, 1)

    def test_same_build_completed_keys_are_reused_and_budget_exhaustion_is_exact(self):
        from roster_scraper import plan_ncaa_player_detail_requests

        completed = frozenset({("9698671", "batting")})
        plan = plan_ncaa_player_detail_requests(
            ("9698671", "11219748"),
            {"batting": "15867", "pitching": "15868", "fielding": "15869"},
            request_budget=2,
            completed_keys=completed,
        )
        self.assertEqual(plan.reused_keys, completed)
        self.assertEqual(len(plan.scheduled), 2)
        self.assertEqual(len(plan.remaining), 3)
        self.assertEqual(plan.status, "partial")
        self.assertEqual(plan.reason, "request_budget_exhausted")


class SnapshotDrillPathTests(unittest.TestCase):
    def test_one_stable_identity_reaches_typed_history_and_logs(self):
        detail = parse("d1")
        self.assertEqual(detail.identity_key, "ncaa:9698671")
        self.assertTrue(all(row.identity_key == detail.identity_key for row in detail.career_seasons))
        self.assertTrue(all(row.identity_key == detail.identity_key for row in detail.game_logs))
        self.assertEqual(
            {row.stat_type for row in (*detail.career_seasons, *detail.game_logs)},
            {"batting", "pitching", "fielding"},
        )

    def test_canonical_history_keeps_one_identity_and_exact_game_links(self):
        from roster_scout.canonical import canonicalize_player_history

        api = history_api()
        detail = parse("d1")
        schedule = api.parse_schedule_fixture(
            (FIXTURES / "d1" / "schedule.html").read_text(encoding="utf-8"),
            expected_season_id="17040",
            expected_team_id="234",
            expected_season_team_id="614606",
            expected_division="d1",
        )
        history = canonicalize_player_history(detail, schedule.games)
        self.assertEqual(history.identity_key, detail.identity_key)
        self.assertEqual(
            {item.stat_type for item in history.career_aggregates},
            {"batting", "pitching", "fielding"},
        )
        self.assertTrue(any(item.link_status == "linked" for item in history.game_logs))
        self.assertTrue(all(
            item.game_identity is None or item.game_identity == item.source_game_id
            for item in history.game_logs
        ))

    def test_fixtures_are_minimal_sanitized_player_evidence(self):
        forbidden = (
            "token=", "cookie", "authorization", "browserless", "password",
            "<script", "mailto:",
        )
        for division in REPRESENTATIVES:
            text = fixture(division)
            lowered = text.casefold()
            with self.subTest(division=division):
                self.assertFalse(any(value in lowered for value in forbidden))
                self.assertIsNone(re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text))
                self.assertEqual(lowered.count("<player-detail "), 1)
                self.assertNotIn("https://", lowered)


if __name__ == "__main__":
    unittest.main()
