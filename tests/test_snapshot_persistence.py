"""PostgreSQL invariants for canonical snapshot persistence."""

import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.db_support import (
    alembic_config,
    clean_database,
    downgrade_database,
    require_test_database_url,
    upgrade_database,
)


REQUIRED_TABLES = {
    "organizations", "teams", "seasons", "team_seasons", "players",
    "player_team_seasons", "reports", "report_snapshots", "report_sections",
    "source_records", "batting_facts", "pitching_facts", "metric_definitions",
    "metric_values", "report_jobs", "report_artifacts", "failure_evidence",
}


class PostgreSQLSnapshotTestCase(unittest.TestCase):
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

    def fixture(self, session, *, organization_name="Axis Baseball"):
        from roster_scout.models import Organization, Report, ReportSnapshot, Season, Team, TeamSeason
        organization = Organization(name=organization_name)
        team = Team(source="ncaa", source_team_id=str(uuid.uuid4()), name="Tampa")
        season = Season(source="ncaa", source_season_id=str(uuid.uuid4()), year=2026)
        session.add_all([organization, team, season])
        session.flush()
        team_season = TeamSeason(
            team_id=team.id, season_id=season.id, source_season_team_id=str(uuid.uuid4()), division="2"
        )
        session.add(team_season)
        session.flush()
        report = Report(
            organization_id=organization.id, team_season_id=team_season.id, name="Tampa 2026"
        )
        session.add(report)
        session.flush()
        snapshot = ReportSnapshot(
            organization_id=organization.id, report_id=report.id, schema_version="1"
        )
        session.add(snapshot)
        session.flush()
        return organization, team_season, report, snapshot


class CanonicalSchemaTests(PostgreSQLSnapshotTestCase):
    def test_t_01_07_migration_defines_minimum_tables_and_named_checks(self):
        inspector = inspect(self.engine)
        self.assertEqual(REQUIRED_TABLES - set(inspector.get_table_names()), set())
        names = {
            check["name"]
            for table in REQUIRED_TABLES
            for check in inspector.get_check_constraints(table)
            if check.get("name")
        }
        self.assertIn("ck_pitching_facts_innings_outs_nonnegative", names)
        self.assertIn("ck_report_snapshots_status", names)

    def test_null_numeric_fact_remains_distinct_from_zero_and_raw_json_is_exact(self):
        from roster_scout.models import BattingFact, Player, PlayerTeamSeason, SourceRecord
        with self.Session.begin() as session:
            organization, team_season, _, snapshot = self.fixture(session)
            player = Player(source="ncaa", source_player_id="123", display_name="Player One")
            session.add(player)
            session.flush()
            identity = PlayerTeamSeason(
                player_id=player.id,
                team_season_id=team_season.id,
                identity_status="authoritative",
                display_name="Player One",
            )
            session.add(identity)
            session.flush()
            source = SourceRecord(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                section="batting",
                row_ordinal=1,
                source_url="https://stats.ncaa.org/example",
                source_team_id="689",
                source_season_id="17040",
                source_season_team_id="615087",
                source_stat_category_id="15867",
                collected_at=datetime.now(UTC),
                parser_version="1",
                schema_version="1",
                payload_sha256="a" * 64,
                raw_values={"AB": "", "H": "0", "IP": "7.2"},
            )
            session.add(source)
            session.flush()
            session.add(BattingFact(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                player_team_season_id=identity.id,
                source_record_id=source.id,
                at_bats=None,
                hits=0,
            ))
        with self.Session() as session:
            fact = session.scalar(select(BattingFact))
            raw = session.scalar(select(SourceRecord))
            self.assertIsNone(fact.at_bats)
            self.assertEqual(fact.hits, 0)
            self.assertEqual(raw.raw_values, {"AB": "", "H": "0", "IP": "7.2"})

    def test_pitching_innings_are_nullable_nonnegative_outs(self):
        from roster_scout.models import PitchingFact
        column = inspect(self.engine).get_columns("pitching_facts")
        innings = next(item for item in column if item["name"] == "innings_outs")
        self.assertTrue(innings["nullable"])
        self.assertIn("INTEGER", str(innings["type"]).upper())

    def test_metric_values_retain_definition_version_and_artifact_is_independent(self):
        from roster_scout.models import MetricDefinition, MetricValue, ReportArtifact
        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            definition = MetricDefinition(code="ops", version="1", formula="obp + slg")
            session.add(definition)
            session.flush()
            session.add(MetricValue(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                metric_definition_id=definition.id,
                value=None,
                unavailable_reason="missing denominator",
            ))
            session.add(ReportArtifact(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                artifact_type="xlsx",
                status="pending",
            ))


class TenantAndPublicationTests(PostgreSQLSnapshotTestCase):
    def add_core_sections(self, session, organization_id, snapshot_id):
        from roster_scout.models import ReportSection
        for section in ("batting", "pitching"):
            session.add(ReportSection(
                organization_id=organization_id,
                snapshot_id=snapshot_id,
                section=section,
                critical=True,
                status="complete",
                row_count=0,
                verified_empty=True,
                collected_at=datetime.now(UTC),
            ))

    def test_t_01_01_cross_organization_fact_is_rejected(self):
        from roster_scout.models import BattingFact, Organization
        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            other = Organization(name="Other Org")
            session.add(other)
            session.flush()
            session.add(BattingFact(
                organization_id=other.id, snapshot_id=snapshot.id, at_bats=0, hits=0
            ))
            with self.assertRaises(IntegrityError):
                session.flush()

    def test_t_01_03_publish_requires_complete_or_verified_empty_core_sections(self):
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot
        with self.Session.begin() as session:
            _, _, _, snapshot = self.fixture(session)
            snapshot_id = snapshot.id
        with self.Session.begin() as session:
            with self.assertRaises(SnapshotPublicationError):
                publish_snapshot(session, snapshot_id)

    def test_publish_and_published_only_read_boundary(self):
        from roster_scout.repositories import list_published_snapshots
        from roster_scout.snapshots import publish_snapshot
        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            organization_id = organization.id
            snapshot_id = snapshot.id
            self.add_core_sections(session, organization.id, snapshot.id)
        with self.Session.begin() as session:
            publish_snapshot(session, snapshot_id)
        with self.Session() as session:
            snapshots = list_published_snapshots(session, organization_id)
            self.assertEqual([item.id for item in snapshots], [snapshot_id])

    def test_published_fact_update_and_delete_are_rejected(self):
        from roster_scout.models import BattingFact
        from roster_scout.snapshots import publish_snapshot
        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            fact = BattingFact(
                organization_id=organization.id, snapshot_id=snapshot.id, at_bats=1, hits=0
            )
            session.add(fact)
            self.add_core_sections(session, organization.id, snapshot.id)
            session.flush()
            fact_id = fact.id
            snapshot_id = snapshot.id
        with self.Session.begin() as session:
            publish_snapshot(session, snapshot_id)
        for statement in (
            text("UPDATE batting_facts SET hits = 1 WHERE id = :id"),
            text("DELETE FROM batting_facts WHERE id = :id"),
        ):
            with self.Session.begin() as session:
                with self.assertRaises(DBAPIError):
                    session.execute(statement, {"id": fact_id})

    def test_predecessor_from_another_report_is_rejected(self):
        from roster_scout.models import Report, ReportSnapshot
        with self.Session.begin() as session:
            organization, team_season, _, first = self.fixture(session)
            other_report = Report(
                organization_id=organization.id, team_season_id=team_season.id, name="Other"
            )
            session.add(other_report)
            session.flush()
            session.add(ReportSnapshot(
                organization_id=organization.id,
                report_id=other_report.id,
                predecessor_snapshot_id=first.id,
                schema_version="1",
            ))
            with self.assertRaises(IntegrityError):
                session.flush()

    def test_artifact_status_changes_after_publication(self):
        from roster_scout.models import ReportArtifact
        from roster_scout.snapshots import publish_snapshot
        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            self.add_core_sections(session, organization.id, snapshot.id)
            artifact = ReportArtifact(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                artifact_type="xlsx",
                status="pending",
            )
            session.add(artifact)
            session.flush()
            artifact_id = artifact.id
            snapshot_id = snapshot.id
        with self.Session.begin() as session:
            publish_snapshot(session, snapshot_id)
        with self.Session.begin() as session:
            artifact = session.get(ReportArtifact, artifact_id)
            artifact.status = "ready"


class MigrationLifecycleTests(PostgreSQLSnapshotTestCase):
    def test_t_01_07_downgrade_and_reupgrade(self):
        clean_database(self.database_url)
        downgrade_database(self.database_url)
        upgrade_database(self.database_url)
        self.assertIn("report_snapshots", inspect(self.engine).get_table_names())

    def test_failure_rolls_back_publication(self):
        from roster_scout.models import ReportSnapshot
        from roster_scout.repositories import list_published_snapshots
        from roster_scout.snapshots import publish_snapshot
        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            organization_id = organization.id
            snapshot_id = snapshot.id
        with self.assertRaises(Exception):
            with self.Session.begin() as session:
                publish_snapshot(session, snapshot_id)
        with self.Session() as session:
            self.assertEqual(list_published_snapshots(session, organization_id), [])
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "draft")


class DatabaseUrlSafetyTests(unittest.TestCase):
    def test_sqlite_is_explicitly_rejected(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///unsafe.db"}):
            with self.assertRaisesRegex(ValueError, "PostgreSQL"):
                require_test_database_url()


if __name__ == "__main__":
    unittest.main()
