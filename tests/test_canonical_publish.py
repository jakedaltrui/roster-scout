"""Fixture-to-PostgreSQL publication coverage for NCAA D1, D2, and D3."""

import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, func, select

import roster_scraper as rs
from tests.db_support import clean_database, require_test_database_url, upgrade_database


FIXTURES = Path(__file__).parent / "fixtures"
SEASON = {"season": "2026", "id": "17040", "batting_id": "15867", "pitching_id": "15868"}
TEAMS = {
    1: {"team_id": "234", "team_name": "D One", "division": "1", "year": "2026", "season_id": "17040", "season_team_id": "614606"},
    2: {"team_id": "689", "team_name": "D Two", "division": "2", "year": "2026", "season_id": "17040", "season_team_id": "615087"},
    3: {"team_id": "633", "team_name": "D Three", "division": "3", "year": "2026", "season_id": "17040", "season_team_id": "615349"},
}


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class CanonicalPublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        upgrade_database(cls.database_url)
        from roster_scout.database import create_session_factory, get_engine

        cls.engine = get_engine(cls.database_url)
        cls.Session = create_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        clean_database(self.database_url)
        rs._NCAA_SEASON_ROWS = [dict(SEASON)]

    def tearDown(self):
        rs._NCAA_TEAM_ROWS = None
        rs._NCAA_SEASON_ROWS = None

    def seed(self, division):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import Organization, Report, Season, Team, TeamSeason

        source = TEAMS[division]
        rs._NCAA_TEAM_ROWS = [dict(source)]
        with self.Session.begin() as session:
            organization = Organization(name="Axis Baseball")
            team = Team(source="ncaa", source_team_id=source["team_id"], name=source["team_name"])
            season = Season(source="ncaa", source_season_id=source["season_id"], year=2026)
            session.add_all([organization, team, season])
            session.flush()
            team_season = TeamSeason(
                team_id=team.id,
                season_id=season.id,
                source_season_team_id=source["season_team_id"],
                division=source["division"],
                batting_stat_category_id=SEASON["batting_id"],
                pitching_stat_category_id=SEASON["pitching_id"],
            )
            session.add(team_season)
            session.flush()
            report = Report(
                organization_id=organization.id,
                team_season_id=team_season.id,
                name=f"{source['team_name']} 2026",
            )
            session.add(report)
            session.flush()
            organization_id, report_id = organization.id, report.id
        return accept_report_job(
            self.Session, organization_id, report_id, uuid.uuid4().hex, dispatch=None
        )

    def publish(self, division, pages=None):
        from roster_scout.jobs import collect_ncaa_sections, run_report_job

        job = self.seed(division)
        pages = pages or {
            "batting": fixture(f"d{division}_batting.html"),
            "pitching": fixture(f"d{division}_pitching.html"),
        }
        with patch.object(rs, "_fetch_ncaa_pages", return_value=pages):
            snapshot = run_report_job(
                self.Session,
                job.id,
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )
        return job, snapshot

    def test_all_divisions_publish_one_provenance_rich_canonical_contract(self):
        from roster_scout.models import (
            BattingFact,
            MetricDefinition,
            PitchingFact,
            PlayerTeamSeason,
            ReportSection,
            ReportSnapshot,
            SourceRecord,
        )

        schemas = []
        for division in (1, 2, 3):
            with self.subTest(division=division):
                clean_database(self.database_url)
                _, snapshot = self.publish(division)
                self.assertEqual(snapshot.status, "ready")
                with self.Session() as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(ReportSnapshot)), 1)
                    sections = list(session.scalars(select(ReportSection)))
                    self.assertEqual({item.section for item in sections}, {"batting", "pitching"})
                    sources = list(session.scalars(select(SourceRecord)))
                    self.assertTrue(sources)
                    self.assertTrue(all(record.source_url.startswith("https://stats.ncaa.org/") for record in sources))
                    self.assertTrue(all(record.source_player_id for record in sources))
                    self.assertTrue(all(record.raw_values.get("player_url", "").startswith("https://stats.ncaa.org/players/") for record in sources))
                    self.assertTrue(all(record.collected_at and record.parser_version and record.schema_version for record in sources))
                    self.assertTrue(all(len(record.payload_sha256) == 64 for record in sources))
                    batting = list(session.scalars(select(BattingFact)))
                    pitching = list(session.scalars(select(PitchingFact)))
                    self.assertTrue(batting)
                    self.assertTrue(pitching)
                    self.assertTrue(all(fact.source_record_id for fact in batting + pitching))
                    self.assertTrue(session.scalars(select(MetricDefinition)).all())
                    schemas.append((
                        tuple(column.name for column in BattingFact.__table__.columns),
                        tuple(column.name for column in PitchingFact.__table__.columns),
                    ))
                    if division == 1:
                        two_way = list(session.scalars(
                            select(PlayerTeamSeason).where(PlayerTeamSeason.display_name == "D One Two Way")
                        ))
                        self.assertEqual(len(two_way), 1)
                        self.assertEqual(
                            session.scalar(select(func.count()).select_from(BattingFact).where(BattingFact.player_team_season_id == two_way[0].id)),
                            1,
                        )
                        self.assertEqual(
                            session.scalar(select(func.count()).select_from(PitchingFact).where(PitchingFact.player_team_season_id == two_way[0].id)),
                            1,
                        )
        self.assertEqual(len(set(schemas)), 1)

    def test_verified_empty_core_publishes_but_missing_core_does_not(self):
        from roster_scout.jobs import RetryableJobError, collect_ncaa_sections, run_report_job
        from roster_scout.models import ReportSnapshot

        empty = '<html><body><table id="stat_grid"><thead><tr><th>Player</th></tr></thead><tbody></tbody></table></body></html>'
        _, snapshot = self.publish(2, {
            "batting": empty,
            "pitching": fixture("d2_pitching.html"),
        })
        self.assertEqual(snapshot.status, "ready")

        clean_database(self.database_url)
        job = self.seed(2)
        with patch.object(rs, "_fetch_ncaa_pages", return_value={"batting": fixture("d2_batting.html")}):
            with self.assertRaises(RetryableJobError):
                run_report_job(
                    self.Session,
                    job.id,
                    collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                        self.Session, job_id, cached_sections=cached_sections
                    ),
                )
        with self.Session() as session:
            self.assertIsNone(session.scalar(
                select(ReportSnapshot).where(ReportSnapshot.status.in_(("ready", "partial")))
            ))

    def test_duplicate_delivery_replays_snapshot_without_collecting(self):
        from roster_scout.jobs import run_report_job

        job, snapshot = self.publish(3)
        replay = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: self.fail("terminal job collected twice"),
        )
        self.assertEqual(replay.id, snapshot.id)

    def test_worker_loss_after_source_write_reuses_only_same_job_evidence(self):
        from roster_scout.jobs import collect_ncaa_sections, run_report_job
        from roster_scout.models import ReportSection, ReportSnapshot, SourceRecord

        job = self.seed(1)
        pages = {
            "batting": fixture("d1_batting.html"),
            "pitching": fixture("d1_pitching.html"),
        }
        with patch.object(rs, "_fetch_ncaa_pages", return_value=pages), patch(
            "roster_scout.jobs._persist_canonical", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_report_job(
                    self.Session,
                    job.id,
                    collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                        self.Session, job_id, cached_sections=cached_sections
                    ),
                )
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ReportSection)), 2)
            self.assertGreater(session.scalar(select(func.count()).select_from(SourceRecord)), 0)
            self.assertIsNone(session.scalar(
                select(ReportSnapshot).where(ReportSnapshot.status.in_(("ready", "partial")))
            ))

        with patch.object(rs, "_fetch_ncaa_pages", side_effect=AssertionError("same-job cache missed")):
            replay = run_report_job(
                self.Session,
                job.id,
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )
        self.assertEqual(replay.status, "ready")

    def test_new_refresh_forces_live_collection_and_does_not_reuse_old_rows(self):
        from roster_scout.jobs import collect_ncaa_sections, run_report_job
        from roster_scout.models import ReportJob, ReportSnapshot

        first_job, first_snapshot = self.publish(2)
        with self.Session() as session:
            stored = session.get(ReportJob, first_job.id)
            organization_id, report_id = stored.organization_id, stored.report_id
        from roster_scout.jobs import accept_report_job
        next_job = accept_report_job(
            self.Session, organization_id, report_id, "next-refresh", dispatch=None
        )
        with patch.object(rs, "_fetch_ncaa_pages", return_value={
            "batting": fixture("d2_batting.html"),
            "pitching": fixture("d2_pitching.html"),
        }):
            live_collection = rs._collect_ncaa_team(TEAMS[2], 2026)
        with patch.object(rs, "collect_ncaa_by_id", return_value=live_collection) as collect:
            second = run_report_job(
                self.Session,
                next_job.id,
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )
        collect.assert_called_once_with("615087", division="2", year=2026, force_refresh=True)
        self.assertNotEqual(second.id, first_snapshot.id)
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ReportSnapshot)), 2)


class SharedRecordPublishCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        upgrade_database(cls.database_url)
        from roster_scout.database import create_session_factory, get_engine

        cls.engine = get_engine(cls.database_url)
        cls.Session = create_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        clean_database(self.database_url)

    def seed_jobs(self, count=2):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import Organization, Report, Season, Team, TeamSeason

        with self.Session.begin() as session:
            team = Team(source="ncaa", source_team_id="shared-team", name="Shared Team")
            season = Season(source="ncaa", source_season_id="shared-season", year=2026)
            session.add_all([team, season])
            session.flush()
            team_season = TeamSeason(
                team_id=team.id,
                season_id=season.id,
                source_season_team_id="shared-team-season",
                division="1",
                batting_stat_category_id="batting-category",
                pitching_stat_category_id="pitching-category",
            )
            session.add(team_season)
            session.flush()
            job_inputs = []
            for index in range(count):
                organization = Organization(name=f"Organization {index}")
                session.add(organization)
                session.flush()
                report = Report(
                    organization_id=organization.id,
                    team_season_id=team_season.id,
                    name=f"Shared Team 2026 ({index})",
                )
                session.add(report)
                session.flush()
                job_inputs.append((organization.id, report.id))
        return [
            accept_report_job(
                self.Session,
                organization_id,
                report_id,
                f"shared-{index}-{uuid.uuid4().hex}",
                dispatch=None,
            )
            for index, (organization_id, report_id) in enumerate(job_inputs)
        ]

    @staticmethod
    def sections(display_name="Alex Smith", player_id="shared-player"):
        from roster_scout.jobs import CollectedSection

        collected_at = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
        common = {
            "critical": True,
            "verified_empty": False,
            "source_url": "https://stats.ncaa.org/shared",
            "source_team_id": "shared-team",
            "source_season_id": "shared-season",
            "source_season_team_id": "shared-team-season",
            "collected_at": collected_at,
            "parser_version": "parser-1",
            "schema_version": "schema-1",
        }
        return (
            CollectedSection(
                name="batting",
                rows=({
                    "Player": display_name,
                    "AB": "10",
                    "H": "4",
                    "BB": "1",
                    "K": "2",
                    "AVG": ".400",
                    "_player_id": player_id,
                },),
                source_stat_category_id="batting-category",
                **common,
            ),
            CollectedSection(
                name="pitching",
                rows=({
                    "Player": display_name,
                    "IP": "3.0",
                    "H": "2",
                    "ER": "1",
                    "BB": "1",
                    "SO": "4",
                    "ERA": "3.00",
                    "_player_id": player_id,
                },),
                source_stat_category_id="pitching-category",
                **common,
            ),
        )

    def run_jobs(self, jobs, sections):
        from roster_scout.jobs import run_report_job

        start = threading.Barrier(len(jobs))

        def publish(job):
            start.wait(timeout=10)
            return run_report_job(
                self.Session,
                job.id,
                collector=lambda *args, **kwargs: sections,
            )

        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            return list(executor.map(publish, jobs))


class ConcurrentSharedRecordTests(SharedRecordPublishCase):
    def _assert_shared_rows(self, jobs, snapshots):
        from roster_scout.models import MetricDefinition, Player, ReportJob

        self.assertEqual([snapshot.status for snapshot in snapshots], ["ready", "ready"])
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Player)), 1)
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(MetricDefinition).where(
                        MetricDefinition.code == "AVG"
                    )
                ),
                1,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(MetricDefinition).where(
                        MetricDefinition.code == "ERA"
                    )
                ),
                1,
            )
            stored_jobs = [session.get(ReportJob, job.id) for job in jobs]
            self.assertEqual([job.failure_category for job in stored_jobs], [None, None])

    def test_concurrent_player_creation_converges_without_unique_key_failure(self):
        from roster_scout.models import Player

        jobs = self.seed_jobs()
        rendezvous = threading.Barrier(2)

        def synchronize_legacy_player_flush(session, *_args):
            if any(isinstance(item, Player) for item in session.new):
                rendezvous.wait(timeout=10)

        event.listen(self.Session.class_, "before_flush", synchronize_legacy_player_flush)
        try:
            snapshots = self.run_jobs(jobs, self.sections())
        finally:
            event.remove(self.Session.class_, "before_flush", synchronize_legacy_player_flush)
        self._assert_shared_rows(jobs, snapshots)

    def test_concurrent_metric_definition_creation_converges(self):
        from roster_scout.models import MetricDefinition, Player

        with self.Session.begin() as session:
            session.add(
                Player(
                    source="ncaa",
                    source_player_id="shared-player",
                    display_name="Alex Smith",
                )
            )
        jobs = self.seed_jobs()
        rendezvous = threading.Barrier(2)
        synchronized_sessions = set()
        synchronized_sessions_lock = threading.Lock()

        def synchronize_legacy_definition_flush(session, *_args):
            if not any(isinstance(item, MetricDefinition) for item in session.new):
                return
            with synchronized_sessions_lock:
                if id(session) in synchronized_sessions:
                    return
                synchronized_sessions.add(id(session))
            rendezvous.wait(timeout=10)

        event.listen(
            self.Session.class_, "before_flush", synchronize_legacy_definition_flush
        )
        try:
            snapshots = self.run_jobs(jobs, self.sections())
        finally:
            event.remove(
                self.Session.class_, "before_flush", synchronize_legacy_definition_flush
            )
        self._assert_shared_rows(jobs, snapshots)


class AuthoritativeIdentityTests(SharedRecordPublishCase):
    def test_materially_conflicting_name_fails_closed_and_preserves_source_row(self):
        from roster_scout.jobs import DeterministicJobError, run_report_job
        from roster_scout.models import ReportJob, ReportSnapshot, SourceRecord

        first, conflicting = self.seed_jobs()
        first_snapshot = run_report_job(
            self.Session,
            first.id,
            collector=lambda *args, **kwargs: self.sections("Alex Smith"),
        )
        self.assertEqual(first_snapshot.status, "ready")

        with self.assertRaises(DeterministicJobError) as raised:
            run_report_job(
                self.Session,
                conflicting.id,
                collector=lambda *args, **kwargs: self.sections("Jordan Jones"),
            )
        self.assertEqual(raised.exception.category, "identity_conflict")
        with self.Session() as session:
            stored_job = session.get(ReportJob, conflicting.id)
            self.assertEqual(stored_job.status, "failed")
            self.assertEqual(stored_job.failure_category, "identity_conflict")
            snapshot = session.get(ReportSnapshot, stored_job.result_snapshot_id)
            self.assertEqual(snapshot.status, "draft")
            self.assertIsNone(snapshot.published_at)
            sources = list(
                session.scalars(
                    select(SourceRecord).where(SourceRecord.snapshot_id == snapshot.id)
                )
            )
            self.assertEqual(len(sources), 2)
            self.assertEqual(
                {source.raw_values["Player"] for source in sources}, {"Jordan Jones"}
            )

    def test_case_and_whitespace_equivalent_name_reuses_authoritative_player(self):
        from roster_scout.jobs import run_report_job
        from roster_scout.models import Player

        first, benign = self.seed_jobs()
        run_report_job(
            self.Session,
            first.id,
            collector=lambda *args, **kwargs: self.sections("Alex Smith"),
        )
        snapshot = run_report_job(
            self.Session,
            benign.id,
            collector=lambda *args, **kwargs: self.sections("  ALEX   smith  "),
        )
        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Player)), 1)


if __name__ == "__main__":
    unittest.main()
