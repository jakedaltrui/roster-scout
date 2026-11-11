"""Fixture-to-PostgreSQL publication coverage for NCAA D1, D2, and D3."""

import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import delete, event, func, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

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

        empty = (
            '<html><body><table id="stat_grid"><thead><tr>'
            '<th>Player</th><th>AB</th><th>H</th>'
            '</tr></thead><tbody></tbody></table></body></html>'
        )
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
        from roster_scout.jobs import (
            collect_ncaa_sections,
            reconcile_stale_report_jobs,
            run_report_job,
        )
        from roster_scout.models import ReportJob, ReportSection, ReportSnapshot, SourceRecord

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
            started_at = session.get(ReportJob, job.id).started_at

        reconciled = reconcile_stale_report_jobs(
            self.Session,
            now=started_at + timedelta(hours=1),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(reconciled.reopened, 1)

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
        from roster_scout.jobs import (
            collect_ncaa_sections,
            reconcile_stale_report_jobs,
            run_report_job,
        )
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


class FinalAttemptPublicationTests(CanonicalPublishTests):
    def test_complete_final_attempt_evidence_publishes_original_source_rows(self):
        from roster_scout.jobs import (
            collect_ncaa_sections,
            reconcile_stale_report_jobs,
            run_report_job,
        )
        from roster_scout.models import (
            BattingFact,
            PitchingFact,
            ReportJob,
            SourceRecord,
        )

        job = self.seed(1)
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            stored.max_attempts = 1
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
            original = {
                source.id: (
                    source.section,
                    source.row_ordinal,
                    source.payload_sha256,
                    source.collected_at,
                    source.raw_values,
                )
                for source in session.scalars(select(SourceRecord))
            }
            self.assertTrue(original)
            started_at = session.get(ReportJob, job.id).started_at

        reconciled = reconcile_stale_report_jobs(
            self.Session,
            now=started_at + timedelta(hours=1),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(reconciled.reopened, 1)

        collector = Mock(
            side_effect=AssertionError("evidence replay collected live data")
        )
        snapshot = run_report_job(self.Session, job.id, collector=collector)
        self.assertEqual(snapshot.status, "ready")
        collector.assert_not_called()

        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.attempt_count, 1)
            replayed = {
                source.id: (
                    source.section,
                    source.row_ordinal,
                    source.payload_sha256,
                    source.collected_at,
                    source.raw_values,
                )
                for source in session.scalars(select(SourceRecord))
            }
            self.assertEqual(replayed, original)
            source_ids = set(original)
            facts = list(session.scalars(select(BattingFact))) + list(
                session.scalars(select(PitchingFact))
            )
            self.assertTrue(facts)
            self.assertTrue(all(fact.source_record_id in source_ids for fact in facts))


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
            "parser_version": rs.NCAA_PARSER_VERSION,
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
        jobs = self.seed_jobs()
        rendezvous = threading.Barrier(2)

        def synchronize_player_insert(_conn, _cursor, statement, *_args):
            if statement.lstrip().startswith("INSERT INTO players"):
                rendezvous.wait(timeout=10)

        event.listen(self.engine, "before_cursor_execute", synchronize_player_insert)
        try:
            snapshots = self.run_jobs(jobs, self.sections())
        finally:
            event.remove(self.engine, "before_cursor_execute", synchronize_player_insert)
        self._assert_shared_rows(jobs, snapshots)

    def test_concurrent_metric_definition_creation_converges(self):
        from roster_scout.models import Player

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
        synchronized_connections = set()
        synchronized_connections_lock = threading.Lock()

        def synchronize_definition_insert(conn, _cursor, statement, *_args):
            if not statement.lstrip().startswith("INSERT INTO metric_definitions"):
                return
            with synchronized_connections_lock:
                if id(conn) in synchronized_connections:
                    return
                synchronized_connections.add(id(conn))
            rendezvous.wait(timeout=10)

        event.listen(self.engine, "before_cursor_execute", synchronize_definition_insert)
        try:
            snapshots = self.run_jobs(jobs, self.sections())
        finally:
            event.remove(self.engine, "before_cursor_execute", synchronize_definition_insert)
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


class PublicationIntegrityTests(CanonicalPublishTests):
    def build_draft(self, division=1, *, include_canonical=True):
        from roster_scout.jobs import (
            _new_or_existing_snapshot,
            _persist_canonical,
            _persist_sections,
        )
        from roster_scout.models import ReportJob
        from roster_scout.canonical import to_collected_sections

        job = self.seed(division)
        with patch.object(rs, "_fetch_ncaa_pages", return_value={
            "batting": fixture(f"d{division}_batting.html"),
            "pitching": fixture(f"d{division}_pitching.html"),
        }):
            collection = rs._collect_ncaa_team(TEAMS[division], 2026)
        sections = to_collected_sections(collection)
        with self.Session.begin() as session:
            stored_job = session.get(ReportJob, job.id)
            snapshot = _new_or_existing_snapshot(session, stored_job)
            _persist_sections(session, stored_job, snapshot, sections)
            snapshot.source_collected_at = collection.metadata.collected_at
            snapshot_id = snapshot.id
        if include_canonical:
            with self.Session.begin() as session:
                stored_job = session.get(ReportJob, job.id)
                snapshot = session.get(type(snapshot), snapshot_id)
                _persist_canonical(session, stored_job, snapshot, sections)
        return job.id, snapshot_id

    def assert_draft_unpublished(self, snapshot_id):
        from roster_scout.models import ReportSnapshot

        with self.Session() as session:
            snapshot = session.get(ReportSnapshot, snapshot_id)
            self.assertEqual(snapshot.status, "draft")
            self.assertIsNone(snapshot.published_at)

    def assert_publication_rejected(self, snapshot_id):
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot

        with self.assertRaises(SnapshotPublicationError):
            with self.Session.begin() as session:
                publish_snapshot(session, snapshot_id)
        self.assert_draft_unpublished(snapshot_id)

    def test_section_flags_and_source_rows_without_typed_facts_do_not_publish(self):
        _job_id, snapshot_id = self.build_draft(include_canonical=False)
        self.assert_publication_rejected(snapshot_id)

    def test_section_row_count_must_equal_source_and_typed_fact_counts(self):
        from roster_scout.models import ReportSection

        _job_id, snapshot_id = self.build_draft()
        with self.Session.begin() as session:
            section = session.scalar(
                select(ReportSection).where(
                    ReportSection.snapshot_id == snapshot_id,
                    ReportSection.section == "batting",
                )
            )
            section.row_count += 1
        self.assert_publication_rejected(snapshot_id)

    def test_missing_source_and_fact_row_do_not_publish(self):
        from roster_scout.models import BattingFact, SourceRecord

        _job_id, snapshot_id = self.build_draft()
        with self.Session.begin() as session:
            fact = session.scalar(
                select(BattingFact).where(BattingFact.snapshot_id == snapshot_id).limit(1)
            )
            source_id = fact.source_record_id
            session.delete(fact)
            session.flush()
            session.execute(delete(SourceRecord).where(SourceRecord.id == source_id))
        self.assert_publication_rejected(snapshot_id)

    def test_duplicate_source_mapping_is_rejected_even_when_fact_count_matches(self):
        from roster_scout.models import BattingFact

        _job_id, snapshot_id = self.build_draft()
        with self.Session.begin() as session:
            facts = list(
                session.scalars(
                    select(BattingFact)
                    .where(BattingFact.snapshot_id == snapshot_id)
                    .order_by(BattingFact.id)
                    .limit(2)
                )
            )
            self.assertEqual(len(facts), 2)
            first, replaced = facts
            session.delete(replaced)
            session.add(BattingFact(
                organization_id=first.organization_id,
                snapshot_id=first.snapshot_id,
                player_team_season_id=first.player_team_season_id,
                source_record_id=first.source_record_id,
                at_bats=first.at_bats,
                hits=first.hits,
                walks=first.walks,
                strikeouts=first.strikeouts,
            ))
        self.assert_publication_rejected(snapshot_id)

    def test_required_fact_links_are_database_non_null(self):
        from roster_scout.models import BattingFact

        _job_id, snapshot_id = self.build_draft()
        with self.assertRaises(IntegrityError):
            with self.Session.begin() as session:
                fact_id = session.scalar(
                    select(BattingFact.id)
                    .where(BattingFact.snapshot_id == snapshot_id)
                    .limit(1)
                )
                session.execute(
                    update(BattingFact)
                    .where(BattingFact.id == fact_id)
                    .values(source_record_id=None)
                )
        self.assert_draft_unpublished(snapshot_id)

    def test_cross_snapshot_fact_links_are_rejected_by_named_ownership_constraint(self):
        from roster_scout.models import BattingFact, ReportSnapshot

        _job_id, snapshot_id = self.build_draft()
        with self.Session.begin() as session:
            original = session.get(ReportSnapshot, snapshot_id)
            other = ReportSnapshot(
                organization_id=original.organization_id,
                report_id=original.report_id,
                schema_version=original.schema_version,
            )
            session.add(other)
            session.flush()
            other_id = other.id
        with self.assertRaises(IntegrityError) as raised:
            with self.Session.begin() as session:
                fact_id = session.scalar(
                    select(BattingFact.id)
                    .where(BattingFact.snapshot_id == snapshot_id)
                    .limit(1)
                )
                session.execute(
                    update(BattingFact)
                    .where(BattingFact.id == fact_id)
                    .values(snapshot_id=other_id)
                )
        self.assertIn("fk_batting_facts_", str(raised.exception))
        self.assert_draft_unpublished(snapshot_id)

    def test_verified_empty_core_sections_remain_publishable(self):
        from roster_scout.jobs import CollectedSection, run_report_job

        job = self.seed(2)
        sections = tuple(
            CollectedSection(
                name=name,
                critical=True,
                rows=(),
                verified_empty=True,
                source_url=f"https://stats.ncaa.org/{name}",
                source_team_id="689",
                source_season_id="17040",
                source_season_team_id="615087",
                source_stat_category_id=category,
                collected_at=datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
                parser_version=rs.NCAA_PARSER_VERSION,
                schema_version="schema-1",
            )
            for name, category in (
                ("batting", SEASON["batting_id"]),
                ("pitching", SEASON["pitching_id"]),
            )
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: sections,
        )
        self.assertEqual(snapshot.status, "ready")

    def test_d1_d2_d3_paths_publish_with_exact_traceability(self):
        from roster_scout.models import BattingFact, PitchingFact, ReportSection, SourceRecord

        for division in (1, 2, 3):
            with self.subTest(division=division):
                clean_database(self.database_url)
                _job, snapshot = self.publish(division)
                self.assertEqual(snapshot.status, "ready")
                with self.Session() as session:
                    for section_name, fact_model in (
                        ("batting", BattingFact),
                        ("pitching", PitchingFact),
                    ):
                        row_count = session.scalar(
                            select(ReportSection.row_count).where(
                                ReportSection.snapshot_id == snapshot.id,
                                ReportSection.section == section_name,
                            )
                        )
                        source_count = session.scalar(
                            select(func.count()).select_from(SourceRecord).where(
                                SourceRecord.snapshot_id == snapshot.id,
                                SourceRecord.section == section_name,
                            )
                        )
                        fact_count = session.scalar(
                            select(func.count()).select_from(fact_model).where(
                                fact_model.snapshot_id == snapshot.id
                            )
                        )
                        self.assertEqual(row_count, source_count)
                        self.assertEqual(row_count, fact_count)

    def test_revision_0004_is_reversible_and_preserves_published_facts(self):
        from alembic import command
        from tests.db_support import alembic_config
        from roster_scout.models import BattingFact, PitchingFact

        _job, snapshot = self.publish(1)
        with self.Session() as session:
            before = (
                session.scalar(select(func.count()).select_from(BattingFact)),
                session.scalar(select(func.count()).select_from(PitchingFact)),
            )
        command.downgrade(alembic_config(self.database_url), "0003")
        command.upgrade(alembic_config(self.database_url), "head")
        with self.Session() as session:
            after = (
                session.scalar(select(func.count()).select_from(BattingFact)),
                session.scalar(select(func.count()).select_from(PitchingFact)),
            )
        self.assertEqual(before, after)
        columns = {
            table: {column["name"]: column for column in inspect(self.engine).get_columns(table)}
            for table in ("batting_facts", "pitching_facts")
        }
        for table in columns.values():
            self.assertFalse(table["player_team_season_id"]["nullable"])
            self.assertFalse(table["source_record_id"]["nullable"])


class UnresolvedIdentityPersistenceTests(CanonicalPublishTests):
    @staticmethod
    def ambiguous_sections():
        from roster_scout.jobs import CollectedSection

        collected_at = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
        common = {
            "critical": True,
            "verified_empty": False,
            "source_team_id": TEAMS[2]["team_id"],
            "source_season_id": TEAMS[2]["season_id"],
            "source_season_team_id": TEAMS[2]["season_team_id"],
            "collected_at": collected_at,
            "parser_version": rs.NCAA_PARSER_VERSION,
            "schema_version": "schema-1",
        }
        return (
            CollectedSection(
                name="batting",
                rows=(
                    {
                        "Player": "Ambiguous Player",
                        "Jersey": "1",
                        "Pos": "OF",
                        "AB": "10",
                        "H": "3",
                    },
                    {
                        "Player": "Ambiguous Player",
                        "Jersey": "2",
                        "Pos": "C",
                        "AB": "8",
                        "H": "2",
                    },
                ),
                source_url="https://stats.ncaa.org/batting",
                source_stat_category_id=SEASON["batting_id"],
                **common,
            ),
            CollectedSection(
                name="pitching",
                rows=({
                    "Player": "Ambiguous Player",
                    "IP": "2.0",
                    "H": "1",
                    "ER": "0",
                },),
                source_url="https://stats.ncaa.org/pitching",
                source_stat_category_id=SEASON["pitching_id"],
                **common,
            ),
        )

    @staticmethod
    def distinguishable_sections():
        from roster_scout.jobs import CollectedSection

        collected_at = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
        common = {
            "critical": True,
            "source_team_id": TEAMS[2]["team_id"],
            "source_season_id": TEAMS[2]["season_id"],
            "source_season_team_id": TEAMS[2]["season_team_id"],
            "collected_at": collected_at,
            "parser_version": rs.NCAA_PARSER_VERSION,
            "schema_version": "schema-1",
        }
        return (
            CollectedSection(
                name="batting",
                rows=(
                    {"Player": "Known Teammate", "Jersey": "1", "AB": "4", "H": "1"},
                    {"Player": "Known Teammate", "Jersey": "2", "AB": "4", "H": "2"},
                ),
                source_url="https://stats.ncaa.org/batting",
                source_stat_category_id=SEASON["batting_id"],
                **common,
            ),
            CollectedSection(
                name="pitching",
                rows=(),
                verified_empty=True,
                source_url="https://stats.ncaa.org/pitching",
                source_stat_category_id=SEASON["pitching_id"],
                **common,
            ),
        )

    def test_ambiguous_rows_publish_as_isolated_unresolved_identities(self):
        from roster_scout.jobs import run_report_job
        from roster_scout.models import (
            BattingFact,
            PitchingFact,
            PlayerTeamSeason,
            SourceRecord,
        )

        job = self.seed(2)
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: self.ambiguous_sections(),
        )
        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            identities = list(session.scalars(
                select(PlayerTeamSeason)
                .where(PlayerTeamSeason.snapshot_id == snapshot.id)
                .order_by(PlayerTeamSeason.identity_key)
            ))
            self.assertEqual(len(identities), 3)
            self.assertEqual(
                {identity.identity_status for identity in identities},
                {"unresolved"},
            )
            self.assertTrue(all(
                identity.identity_key.startswith(
                    f"unresolved:{TEAMS[2]['season_team_id']}:"
                )
                for identity in identities
            ))
            sources = list(session.scalars(
                select(SourceRecord).where(SourceRecord.snapshot_id == snapshot.id)
            ))
            facts = [
                *session.scalars(
                    select(BattingFact).where(BattingFact.snapshot_id == snapshot.id)
                ),
                *session.scalars(
                    select(PitchingFact).where(PitchingFact.snapshot_id == snapshot.id)
                ),
            ]
            self.assertEqual(len(sources), 3)
            self.assertEqual(len(facts), 3)
            self.assertEqual(
                {source.player_team_season_id for source in sources},
                {identity.id for identity in identities},
            )
            self.assertEqual(
                {fact.player_team_season_id for fact in facts},
                {identity.id for identity in identities},
            )
            for fact in facts:
                source = next(item for item in sources if item.id == fact.source_record_id)
                self.assertEqual(
                    source.player_team_season_id,
                    fact.player_team_season_id,
                )

    def test_source_first_recovery_preserves_unresolved_keys_without_duplicates(self):
        from roster_scout.jobs import reconcile_stale_report_jobs, run_report_job
        from roster_scout.models import (
            BattingFact,
            PitchingFact,
            Player,
            PlayerTeamSeason,
            ReportJob,
        )

        job = self.seed(2)
        with patch(
            "roster_scout.jobs._persist_canonical", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_report_job(
                    self.Session,
                    job.id,
                    collector=lambda *args, **kwargs: self.ambiguous_sections(),
                )
        with self.Session() as session:
            started_at = session.get(ReportJob, job.id).started_at
        reconciled = reconcile_stale_report_jobs(
            self.Session,
            now=started_at + timedelta(hours=1),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(reconciled.reopened, 1)

        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: self.fail(
                "same-job unresolved evidence was collected twice"
            ),
        )
        with self.Session() as session:
            keys = tuple(session.scalars(
                select(PlayerTeamSeason.identity_key)
                .where(PlayerTeamSeason.snapshot_id == snapshot.id)
                .order_by(PlayerTeamSeason.identity_key)
            ))
            counts = (
                session.scalar(select(func.count()).select_from(Player)),
                session.scalar(select(func.count()).select_from(PlayerTeamSeason)),
                session.scalar(select(func.count()).select_from(BattingFact)),
                session.scalar(select(func.count()).select_from(PitchingFact)),
            )
        replay = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: self.fail("terminal job recollected"),
        )
        self.assertEqual(replay.id, snapshot.id)
        with self.Session() as session:
            self.assertEqual(
                tuple(session.scalars(
                    select(PlayerTeamSeason.identity_key)
                    .where(PlayerTeamSeason.snapshot_id == snapshot.id)
                    .order_by(PlayerTeamSeason.identity_key)
                )),
                keys,
            )
            self.assertEqual(
                (
                    session.scalar(select(func.count()).select_from(Player)),
                    session.scalar(select(func.count()).select_from(PlayerTeamSeason)),
                    session.scalar(select(func.count()).select_from(BattingFact)),
                    session.scalar(select(func.count()).select_from(PitchingFact)),
                ),
                counts,
            )

    def test_distinguishable_teammates_remain_fallback(self):
        from roster_scout.jobs import run_report_job
        from roster_scout.models import PlayerTeamSeason

        job = self.seed(2)
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: self.distinguishable_sections(),
        )
        with self.Session() as session:
            identities = list(session.scalars(
                select(PlayerTeamSeason).where(
                    PlayerTeamSeason.snapshot_id == snapshot.id
                )
            ))
            self.assertEqual(len(identities), 2)
            self.assertEqual(
                {identity.identity_status for identity in identities},
                {"fallback"},
            )
            self.assertEqual(len({item.identity_key for item in identities}), 2)

    def test_authoritative_conflict_remains_terminal(self):
        from roster_scout.jobs import CollectedSection, DeterministicJobError, run_report_job
        from roster_scout.models import ReportJob, ReportSnapshot

        batting, pitching = self.ambiguous_sections()
        sections = (
            CollectedSection(
                **{
                    **batting.__dict__,
                    "rows": ({"Player": "First Name", "AB": "1", "H": "1", "_player_id": "42"},),
                }
            ),
            CollectedSection(
                **{
                    **pitching.__dict__,
                    "rows": ({"Player": "Different Name", "IP": "1.0", "_player_id": "42"},),
                }
            ),
        )
        job = self.seed(2)
        with self.assertRaises(DeterministicJobError) as raised:
            run_report_job(
                self.Session,
                job.id,
                collector=lambda *args, **kwargs: sections,
            )
        self.assertEqual(raised.exception.category, "identity_conflict")
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            snapshot = session.get(ReportSnapshot, stored.result_snapshot_id)
            self.assertEqual(stored.status, "failed")
            self.assertEqual(snapshot.status, "draft")


class IdlessCorroborationPublicationTests(CanonicalPublishTests):
    @staticmethod
    def sections(batting_support=None, pitching_support=None):
        from roster_scout.jobs import CollectedSection

        batting_support = batting_support or {}
        pitching_support = pitching_support or {}
        collected_at = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
        common = {
            "critical": True,
            "verified_empty": False,
            "source_team_id": TEAMS[2]["team_id"],
            "source_season_id": TEAMS[2]["season_id"],
            "source_season_team_id": TEAMS[2]["season_team_id"],
            "collected_at": collected_at,
            "parser_version": "parser-2+ncaa-core-v2",
            "schema_version": "schema-1",
        }
        return (
            CollectedSection(
                name="batting",
                rows=({
                    "Player": "Same Name",
                    "AB": "10",
                    "H": "3",
                    **batting_support,
                },),
                source_url="https://stats.ncaa.org/batting",
                source_stat_category_id=SEASON["batting_id"],
                **common,
            ),
            CollectedSection(
                name="pitching",
                rows=({
                    "Player": "Same Name",
                    "IP": "2.0",
                    "H": "1",
                    "ER": "0",
                    **pitching_support,
                },),
                source_url="https://stats.ncaa.org/pitching",
                source_stat_category_id=SEASON["pitching_id"],
                **common,
            ),
        )

    def assert_isolated_unresolved_publication(self, sections):
        from roster_scout.jobs import run_report_job
        from roster_scout.models import (
            BattingFact,
            PitchingFact,
            Player,
            PlayerTeamSeason,
            SourceRecord,
        )

        job = self.seed(2)
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: sections,
        )
        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            identities = list(session.scalars(
                select(PlayerTeamSeason)
                .where(PlayerTeamSeason.snapshot_id == snapshot.id)
                .order_by(PlayerTeamSeason.identity_key)
            ))
            players = list(session.scalars(select(Player)))
            sources = list(session.scalars(
                select(SourceRecord)
                .where(SourceRecord.snapshot_id == snapshot.id)
                .order_by(SourceRecord.section)
            ))
            facts = [
                *session.scalars(
                    select(BattingFact).where(BattingFact.snapshot_id == snapshot.id)
                ),
                *session.scalars(
                    select(PitchingFact).where(PitchingFact.snapshot_id == snapshot.id)
                ),
            ]

            self.assertEqual(len(identities), 2)
            self.assertEqual(len(players), 2)
            self.assertEqual(len(sources), 2)
            self.assertEqual(len(facts), 2)
            self.assertEqual(
                {identity.identity_status for identity in identities},
                {"unresolved"},
            )
            self.assertEqual(len({identity.id for identity in identities}), 2)
            self.assertEqual(len({identity.player_id for identity in identities}), 2)
            self.assertEqual(len({identity.identity_key for identity in identities}), 2)
            self.assertTrue(all(
                identity.identity_key.startswith(
                    f"unresolved:{TEAMS[2]['season_team_id']}:"
                )
                for identity in identities
            ))
            self.assertEqual(
                {source.player_team_season_id for source in sources},
                {identity.id for identity in identities},
            )
            self.assertEqual(
                {fact.player_team_season_id for fact in facts},
                {identity.id for identity in identities},
            )
            sources_by_id = {source.id: source for source in sources}
            for fact in facts:
                source = sources_by_id[fact.source_record_id]
                self.assertEqual(
                    source.player_team_season_id,
                    fact.player_team_season_id,
                )
                expected_section = (
                    "batting" if isinstance(fact, BattingFact) else "pitching"
                )
                self.assertEqual(source.section, expected_section)

    def test_name_only_pair_publishes_as_two_unresolved_identities(self):
        self.assert_isolated_unresolved_publication(self.sections())

    def test_one_sided_support_publishes_as_two_unresolved_identities(self):
        self.assert_isolated_unresolved_publication(
            self.sections(batting_support={"Jersey": "7"})
        )

    def test_shared_support_publishes_one_two_way_identity(self):
        from roster_scout.jobs import run_report_job
        from roster_scout.models import (
            BattingFact,
            PitchingFact,
            Player,
            PlayerTeamSeason,
            SourceRecord,
        )

        job = self.seed(2)
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: self.sections(
                batting_support={"Jersey": "7"},
                pitching_support={"Jersey": "7"},
            ),
        )
        with self.Session() as session:
            identities = list(session.scalars(
                select(PlayerTeamSeason).where(
                    PlayerTeamSeason.snapshot_id == snapshot.id
                )
            ))
            sources = list(session.scalars(
                select(SourceRecord).where(SourceRecord.snapshot_id == snapshot.id)
            ))
            batting = list(session.scalars(
                select(BattingFact).where(BattingFact.snapshot_id == snapshot.id)
            ))
            pitching = list(session.scalars(
                select(PitchingFact).where(PitchingFact.snapshot_id == snapshot.id)
            ))

            self.assertEqual(len(identities), 1)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(Player)),
                1,
            )
            self.assertEqual(identities[0].identity_status, "fallback")
            self.assertEqual(len(sources), 2)
            self.assertEqual(len(batting), 1)
            self.assertEqual(len(pitching), 1)
            self.assertEqual(
                {source.player_team_season_id for source in sources},
                {identities[0].id},
            )
            self.assertEqual(
                {
                    batting[0].player_team_season_id,
                    pitching[0].player_team_season_id,
                },
                {identities[0].id},
            )


class SourceIdentityBindingTests(CanonicalPublishTests):
    def build_source_draft(self, division=1):
        from roster_scout.canonical import to_collected_sections
        from roster_scout.jobs import _new_or_existing_snapshot, _persist_sections
        from roster_scout.models import ReportJob

        job = self.seed(division)
        with patch.object(
            rs,
            "_fetch_ncaa_pages",
            return_value={
                "batting": fixture(f"d{division}_batting.html"),
                "pitching": fixture(f"d{division}_pitching.html"),
            },
        ):
            collection = rs._collect_ncaa_team(TEAMS[division], 2026)
        sections = to_collected_sections(collection)
        with self.Session.begin() as session:
            stored_job = session.get(ReportJob, job.id)
            snapshot = _new_or_existing_snapshot(session, stored_job)
            _persist_sections(session, stored_job, snapshot, sections)
            snapshot.source_collected_at = collection.metadata.collected_at
            snapshot_id = snapshot.id
        return job.id, snapshot_id, sections

    def build_canonical_draft(self, division=1):
        from roster_scout.jobs import _persist_canonical
        from roster_scout.models import ReportJob, ReportSnapshot

        job_id, snapshot_id, sections = self.build_source_draft(division)
        with self.Session.begin() as session:
            _persist_canonical(
                session,
                session.get(ReportJob, job_id),
                session.get(ReportSnapshot, snapshot_id),
                sections,
            )
        return job_id, snapshot_id

    def test_all_published_sources_bind_to_their_fact_identity(self):
        from roster_scout.models import (
            BattingFact,
            PitchingFact,
            PlayerTeamSeason,
            SourceRecord,
        )

        for division in (1, 2, 3):
            with self.subTest(division=division):
                clean_database(self.database_url)
                _job, snapshot = self.publish(division)
                with self.Session() as session:
                    sources = {
                        source.id: source
                        for source in session.scalars(
                            select(SourceRecord).where(
                                SourceRecord.snapshot_id == snapshot.id
                            )
                        )
                    }
                    facts = [
                        *session.scalars(
                            select(BattingFact).where(
                                BattingFact.snapshot_id == snapshot.id
                            )
                        ),
                        *session.scalars(
                            select(PitchingFact).where(
                                PitchingFact.snapshot_id == snapshot.id
                            )
                        ),
                    ]
                    self.assertTrue(facts)
                    self.assertTrue(
                        all(source.player_team_season_id for source in sources.values())
                    )
                    for fact in facts:
                        self.assertEqual(
                            sources[fact.source_record_id].player_team_season_id,
                            fact.player_team_season_id,
                        )

                    if division == 1:
                        two_way = session.scalar(
                            select(PlayerTeamSeason).where(
                                PlayerTeamSeason.snapshot_id == snapshot.id,
                                PlayerTeamSeason.display_name == "D One Two Way",
                            )
                        )
                        self.assertIsNotNone(two_way)
                        two_way_source_ids = {
                            fact.source_record_id
                            for fact in facts
                            if fact.player_team_season_id == two_way.id
                        }
                        self.assertEqual(len(two_way_source_ids), 2)
                        self.assertEqual(
                            {
                                sources[source_id].player_team_season_id
                                for source_id in two_way_source_ids
                            },
                            {two_way.id},
                        )

    def test_swapped_fact_identity_is_rejected_by_orm_and_direct_sql(self):
        from roster_scout.models import BattingFact

        _job_id, snapshot_id = self.build_canonical_draft()
        with self.assertRaises(IntegrityError) as orm_error:
            with self.Session.begin() as session:
                facts = list(
                    session.scalars(
                        select(BattingFact)
                        .where(BattingFact.snapshot_id == snapshot_id)
                        .order_by(BattingFact.id)
                        .limit(2)
                    )
                )
                self.assertEqual(len(facts), 2)
                facts[0].player_team_season_id = facts[1].player_team_season_id
                session.flush()
        self.assertIn("fk_batting_facts_source_identity", str(orm_error.exception))

        with self.assertRaises(IntegrityError) as sql_error:
            with self.Session.begin() as session:
                facts = list(
                    session.execute(
                        text(
                            """
                            SELECT id, player_team_season_id
                            FROM batting_facts
                            WHERE snapshot_id = :snapshot_id
                            ORDER BY id
                            LIMIT 2
                            """
                        ),
                        {"snapshot_id": snapshot_id},
                    )
                )
                self.assertEqual(len(facts), 2)
                session.execute(
                    text(
                        """
                        UPDATE batting_facts
                        SET player_team_season_id = :swapped_identity_id
                        WHERE id = :fact_id
                        """
                    ),
                    {
                        "fact_id": facts[0].id,
                        "swapped_identity_id": facts[1].player_team_season_id,
                    },
                )
        self.assertIn("fk_batting_facts_source_identity", str(sql_error.exception))

    def test_one_source_cannot_be_assigned_to_two_canonical_identities(self):
        from roster_scout.canonical import (
            CanonicalCollection,
            CanonicalFact,
            CanonicalPlayer,
        )
        from roster_scout.jobs import DeterministicJobError, _persist_canonical
        from roster_scout.models import ReportJob, ReportSnapshot

        job_id, snapshot_id, sections = self.build_source_draft()
        players = tuple(
            CanonicalPlayer(
                identity_key=f"ncaa:conflict-{label}",
                identity_status="authoritative",
                source_player_id=f"conflict-{label}",
                display_name=f"Conflict {label}",
                jersey_number=None,
                position=None,
                batting_source=("batting", 0),
                pitching_source=None,
            )
            for label in ("a", "b")
        )
        canonical = CanonicalCollection(
            source_records=(),
            players=players,
            facts=tuple(
                CanonicalFact(
                    section="batting",
                    identity_key=player.identity_key,
                    source_record=("batting", 0),
                    values={
                        "at_bats": 1,
                        "hits": 1,
                        "walks": 0,
                        "strikeouts": 0,
                    },
                )
                for player in players
            ),
            metrics=(),
        )
        with patch(
            "roster_scout.canonical.canonicalize_ncaa_collection",
            return_value=canonical,
        ):
            with self.assertRaises(DeterministicJobError) as raised:
                with self.Session.begin() as session:
                    _persist_canonical(
                        session,
                        session.get(ReportJob, job_id),
                        session.get(ReportSnapshot, snapshot_id),
                        sections,
                    )
        self.assertEqual(raised.exception.category, "schema_validation")

    def test_source_first_draft_stays_unbound_and_cannot_publish(self):
        from roster_scout.models import SourceRecord
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot

        _job_id, snapshot_id, _sections = self.build_source_draft()
        with self.Session() as session:
            sources = list(
                session.scalars(
                    select(SourceRecord).where(SourceRecord.snapshot_id == snapshot_id)
                )
            )
            self.assertTrue(sources)
            self.assertTrue(
                all(source.player_team_season_id is None for source in sources)
            )
        with self.assertRaises(SnapshotPublicationError):
            with self.Session.begin() as session:
                publish_snapshot(session, snapshot_id)


class AuthoritativeIdentityPublicationSerializationTests(
    SourceIdentityBindingTests
):
    """Publication joins the same ordered authoritative lock protocol."""

    WAIT_SECONDS = 5
    BLOCKED_ASSERTION_SECONDS = 0.25

    def run_worker(self, target):
        done = threading.Event()
        errors = []

        def run():
            try:
                target()
            except BaseException as error:  # surfaced in the owning test thread
                errors.append(error)
            finally:
                done.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        return worker, done, errors

    def assert_worker_finished(self, worker, done):
        self.assertTrue(done.wait(self.WAIT_SECONDS))
        worker.join(self.WAIT_SECONDS)
        self.assertFalse(worker.is_alive())

    def wait_for_advisory_lock(self, backend_pid):
        deadline = time.monotonic() + self.WAIT_SECONDS
        while time.monotonic() < deadline:
            with self.engine.connect() as connection:
                wait_state = connection.execute(
                    text(
                        "SELECT wait_event_type, wait_event "
                        "FROM pg_stat_activity WHERE pid = :pid"
                    ),
                    {"pid": backend_pid},
                ).one_or_none()
            if wait_state == ("Lock", "advisory"):
                return
            time.sleep(0.01)
        self.fail("source insertion did not block on the snapshot advisory lock")

    def add_unreferenced_identity(self, snapshot_id):
        from roster_scout.models import Player, PlayerTeamSeason

        with self.Session.begin() as session:
            exemplar = session.scalar(
                select(PlayerTeamSeason)
                .where(PlayerTeamSeason.snapshot_id == snapshot_id)
                .limit(1)
            )
            player = Player(
                source="ncaa",
                source_player_id=f"unreferenced-{uuid.uuid4()}",
                display_name="Unreferenced Publication Player",
            )
            session.add(player)
            session.flush()
            identity = PlayerTeamSeason(
                organization_id=exemplar.organization_id,
                snapshot_id=snapshot_id,
                player_id=player.id,
                team_season_id=exemplar.team_season_id,
                identity_status="fallback",
                identity_key=f"unreferenced:{player.id}",
                display_name=player.display_name,
            )
            session.add(identity)
            session.flush()
            return player.id

    def test_publisher_uses_snapshot_global_tenant_identity_lock_order(self):
        source = (Path(__file__).parent.parent / "roster_scout" / "snapshots.py").read_text()
        snapshot_lock = source.index("lock_snapshot_mutation")
        global_player_lock = source.index(
            "lock_authoritative_global_player_mutation"
        )
        tenant_player_lock = source.index("lock_authoritative_player_mutation")
        identity_lock = source.index(
            "lock_authoritative_player_team_season_mutation"
        )
        mismatch_query = source.index("authoritative_mismatch_count")
        self.assertLess(snapshot_lock, global_player_lock)
        self.assertLess(global_player_lock, tenant_player_lock)
        self.assertLess(tenant_player_lock, identity_lock)
        self.assertLess(identity_lock, mismatch_query)

    def test_source_insert_key_share_does_not_deadlock_publication(self):
        from roster_scout.models import ReportSnapshot, SourceRecord
        from roster_scout.snapshots import publish_snapshot

        _job_id, snapshot_id = self.build_canonical_draft()
        with self.Session() as session:
            source_id = session.scalar(
                select(SourceRecord.id)
                .where(SourceRecord.snapshot_id == snapshot_id)
                .order_by(SourceRecord.id)
                .limit(1)
            )

        publisher_has_advisory_lock = threading.Event()
        child_has_key_share = threading.Event()
        child_pid = []

        def publish():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                session.execute(
                    text(
                        "SELECT lock_snapshot_mutation("
                        "CAST(:snapshot_id AS uuid))"
                    ),
                    {"snapshot_id": snapshot_id},
                )
                publisher_has_advisory_lock.set()
                self.assertTrue(child_has_key_share.wait(self.WAIT_SECONDS))
                publish_snapshot(session, snapshot_id)

        def insert_source():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                child_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
                self.assertTrue(
                    publisher_has_advisory_lock.wait(self.WAIT_SECONDS)
                )
                # PostgreSQL foreign-key checks use this same parent-row mode.
                # Taking it explicitly creates a deterministic barrier before
                # the insert's mutation trigger waits on the advisory lock.
                session.execute(
                    select(ReportSnapshot.id)
                    .where(ReportSnapshot.id == snapshot_id)
                    .with_for_update(read=True, key_share=True)
                )
                child_has_key_share.set()
                session.execute(
                    text(
                        """
                        INSERT INTO source_records (
                            id, organization_id, snapshot_id,
                            player_team_season_id, section, row_ordinal,
                            source_url, source_team_id, source_season_id,
                            source_season_team_id, source_stat_category_id,
                            source_player_id, collected_at, parser_version,
                            schema_version, payload_sha256, raw_values
                        )
                        SELECT
                            :new_id, organization_id, snapshot_id,
                            player_team_season_id, section, row_ordinal + 10000,
                            source_url, source_team_id, source_season_id,
                            source_season_team_id, source_stat_category_id,
                            source_player_id, collected_at, parser_version,
                            schema_version, payload_sha256, raw_values
                        FROM source_records
                        WHERE id = :source_id
                        """
                    ),
                    {"new_id": uuid.uuid4(), "source_id": source_id},
                )

        publication_worker, publication_done, publication_errors = (
            self.run_worker(publish)
        )
        self.assertTrue(publisher_has_advisory_lock.wait(self.WAIT_SECONDS))
        insertion_worker, insertion_done, insertion_errors = self.run_worker(
            insert_source
        )
        self.assertTrue(child_has_key_share.wait(self.WAIT_SECONDS))
        self.wait_for_advisory_lock(child_pid[0])

        self.assert_worker_finished(publication_worker, publication_done)
        self.assert_worker_finished(insertion_worker, insertion_done)
        self.assertEqual(publication_errors, [])
        self.assertEqual(len(insertion_errors), 1)
        self.assertIsInstance(insertion_errors[0], DBAPIError)
        self.assertNotIn(
            getattr(insertion_errors[0].orig, "sqlstate", None),
            {"40P01", "55P03"},
        )
        with self.Session() as session:
            self.assertEqual(
                session.get(ReportSnapshot, snapshot_id).status,
                "ready",
            )

    def test_source_mutation_commits_before_waiting_publication_rechecks(self):
        from roster_scout.models import ReportSnapshot, SourceRecord
        from roster_scout.snapshots import publish_snapshot

        _job_id, snapshot_id = self.build_canonical_draft()
        with self.Session() as session:
            source_id = session.scalar(
                select(SourceRecord.id)
                .where(SourceRecord.snapshot_id == snapshot_id)
                .order_by(SourceRecord.id)
                .limit(1)
            )

        mutation_holds_lock = threading.Event()
        release_mutation = threading.Event()

        def mutate_source():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                session.execute(
                    text(
                        "UPDATE source_records "
                        "SET raw_values = raw_values || '{\"race\": true}'::jsonb "
                        "WHERE id = :source_id"
                    ),
                    {"source_id": source_id},
                )
                mutation_holds_lock.set()
                self.assertTrue(release_mutation.wait(self.WAIT_SECONDS))

        def publish():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                publish_snapshot(session, snapshot_id)

        mutation_worker, mutation_done, mutation_errors = self.run_worker(
            mutate_source
        )
        self.assertTrue(mutation_holds_lock.wait(self.WAIT_SECONDS))
        publication_worker, publication_done, publication_errors = self.run_worker(
            publish
        )
        self.assertFalse(
            publication_done.wait(self.BLOCKED_ASSERTION_SECONDS),
            "publication did not wait for the source mutation",
        )
        release_mutation.set()
        self.assert_worker_finished(mutation_worker, mutation_done)
        self.assert_worker_finished(publication_worker, publication_done)
        self.assertEqual(mutation_errors, [])
        self.assertEqual(publication_errors, [])
        with self.Session() as session:
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "ready")

    def test_publication_commits_before_waiting_source_mutation_is_rejected(self):
        from roster_scout.models import ReportSnapshot, SourceRecord
        from roster_scout.snapshots import publish_snapshot

        _job_id, snapshot_id = self.build_canonical_draft()
        with self.Session() as session:
            source_id = session.scalar(
                select(SourceRecord.id)
                .where(SourceRecord.snapshot_id == snapshot_id)
                .order_by(SourceRecord.id)
                .limit(1)
            )

        publication_holds_locks = threading.Event()
        release_publication = threading.Event()

        def publish():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                publish_snapshot(session, snapshot_id)
                publication_holds_locks.set()
                self.assertTrue(release_publication.wait(self.WAIT_SECONDS))

        def mutate_source():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                session.execute(
                    text(
                        "UPDATE source_records "
                        "SET raw_values = raw_values || '{\"race\": true}'::jsonb "
                        "WHERE id = :source_id"
                    ),
                    {"source_id": source_id},
                )

        publication_worker, _, publication_errors = self.run_worker(publish)
        self.assertTrue(publication_holds_locks.wait(self.WAIT_SECONDS))
        mutation_worker, mutation_done, mutation_errors = self.run_worker(
            mutate_source
        )
        self.assertFalse(
            mutation_done.wait(self.BLOCKED_ASSERTION_SECONDS),
            "source mutation did not wait for publication",
        )
        release_publication.set()
        publication_worker.join(self.WAIT_SECONDS)
        self.assertFalse(publication_worker.is_alive())
        self.assert_worker_finished(mutation_worker, mutation_done)
        self.assertEqual(publication_errors, [])
        self.assertEqual(len(mutation_errors), 1)
        self.assertIsInstance(mutation_errors[0], DBAPIError)
        self.assertNotIn(
            getattr(mutation_errors[0].orig, "sqlstate", None),
            {"40P01", "55P03"},
        )
        with self.Session() as session:
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "ready")

    def test_player_mutation_commits_before_waiting_publication_rechecks(self):
        from roster_scout.models import Player, ReportSnapshot
        from roster_scout.snapshots import publish_snapshot

        _job_id, snapshot_id = self.build_canonical_draft()
        player_id = self.add_unreferenced_identity(snapshot_id)
        mutation_holds_locks = threading.Event()
        release_mutation = threading.Event()

        def mutate_player():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                session.execute(
                    text(
                        "UPDATE players SET source_player_id = :new_id "
                        "WHERE id = :player_id"
                    ),
                    {"new_id": f"changed-{uuid.uuid4()}", "player_id": player_id},
                )
                mutation_holds_locks.set()
                self.assertTrue(release_mutation.wait(self.WAIT_SECONDS))

        def publish():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                publish_snapshot(session, snapshot_id)

        mutation_worker, mutation_done, mutation_errors = self.run_worker(
            mutate_player
        )
        self.assertTrue(mutation_holds_locks.wait(self.WAIT_SECONDS))
        publication_worker, publication_done, publication_errors = self.run_worker(
            publish
        )
        self.assertFalse(publication_done.wait(self.BLOCKED_ASSERTION_SECONDS))
        release_mutation.set()
        self.assert_worker_finished(mutation_worker, mutation_done)
        self.assert_worker_finished(publication_worker, publication_done)
        self.assertEqual(mutation_errors, [])
        self.assertEqual(publication_errors, [])
        with self.Session() as session:
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "ready")
            self.assertTrue(
                session.get(Player, player_id).source_player_id.startswith("changed-")
            )

    def test_publication_commits_before_waiting_player_mismatch_is_rejected(self):
        from roster_scout.models import Player, PlayerTeamSeason, ReportSnapshot
        from roster_scout.snapshots import publish_snapshot

        _job_id, snapshot_id = self.build_canonical_draft()
        with self.Session() as session:
            player_id = session.scalar(
                select(Player.id)
                .join(PlayerTeamSeason, PlayerTeamSeason.player_id == Player.id)
                .where(PlayerTeamSeason.snapshot_id == snapshot_id)
                .order_by(Player.id)
                .limit(1)
            )

        publication_holds_locks = threading.Event()
        release_publication = threading.Event()

        def publish():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                publish_snapshot(session, snapshot_id)
                publication_holds_locks.set()
                self.assertTrue(release_publication.wait(self.WAIT_SECONDS))

        def mutate_player():
            with self.Session.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                session.execute(
                    text(
                        "UPDATE players SET source_player_id = 'mismatch' "
                        "WHERE id = :player_id"
                    ),
                    {"player_id": player_id},
                )

        publication_worker, publication_done, publication_errors = self.run_worker(
            publish
        )
        self.assertTrue(publication_holds_locks.wait(self.WAIT_SECONDS))
        mutation_worker, mutation_done, mutation_errors = self.run_worker(
            mutate_player
        )
        self.assertFalse(mutation_done.wait(self.BLOCKED_ASSERTION_SECONDS))
        release_publication.set()
        self.assert_worker_finished(publication_worker, publication_done)
        self.assert_worker_finished(mutation_worker, mutation_done)
        self.assertEqual(publication_errors, [])
        self.assertEqual(len(mutation_errors), 1)
        self.assertIsInstance(mutation_errors[0], DBAPIError)
        self.assertNotIn(
            getattr(mutation_errors[0].orig, "sqlstate", None),
            {"40P01", "55P03"},
        )
        with self.Session() as session:
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "ready")


if __name__ == "__main__":
    unittest.main()
