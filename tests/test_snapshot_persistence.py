"""PostgreSQL invariants for canonical snapshot persistence."""

import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from alembic import command
from sqlalchemy import event, inspect, select, text
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

FACT_COUNT_CHECKS = {
    "batting_facts": {
        "ck_batting_facts_at_bats_nonnegative",
        "ck_batting_facts_hits_nonnegative",
        "ck_batting_facts_walks_nonnegative",
        "ck_batting_facts_strikeouts_nonnegative",
    },
    "pitching_facts": {
        "ck_pitching_facts_innings_outs_nonnegative",
        "ck_pitching_facts_hits_allowed_nonnegative",
        "ck_pitching_facts_earned_runs_nonnegative",
        "ck_pitching_facts_walks_nonnegative",
        "ck_pitching_facts_strikeouts_nonnegative",
    },
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    def test_all_count_columns_have_named_nonnegative_checks(self):
        from roster_scout.models import BattingFact, PitchingFact

        model_tables = {
            "batting_facts": BattingFact.__table__,
            "pitching_facts": PitchingFact.__table__,
        }
        inspector = inspect(self.engine)
        for table_name, expected_names in FACT_COUNT_CHECKS.items():
            with self.subTest(table=table_name, layer="database"):
                database_names = {
                    check["name"]
                    for check in inspector.get_check_constraints(table_name)
                    if check.get("name")
                }
                self.assertEqual(expected_names - database_names, set())
            with self.subTest(table=table_name, layer="orm"):
                model_names = {
                    constraint.name
                    for constraint in model_tables[table_name].constraints
                    if constraint.name
                }
                self.assertEqual(expected_names - model_names, set())

    def test_database_rejects_negative_counts(self):
        cases = {
            "batting_facts": ("at_bats", "hits", "walks", "strikeouts"),
            "pitching_facts": (
                "innings_outs",
                "hits_allowed",
                "earned_runs",
                "walks",
                "strikeouts",
            ),
        }
        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            organization_id = organization.id
            snapshot_id = snapshot.id
        for table_name, columns in cases.items():
            for column_name in columns:
                with self.subTest(table=table_name, column=column_name):
                    statement = text(
                        f"INSERT INTO {table_name} "
                        f"(id, organization_id, snapshot_id, {column_name}) "
                        f"VALUES (:id, :organization_id, :snapshot_id, -1)"
                    )
                    with self.Session.begin() as session:
                        with self.assertRaises(DBAPIError):
                            session.execute(statement, {
                                "id": uuid.uuid4(),
                                "organization_id": organization_id,
                                "snapshot_id": snapshot_id,
                            })

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
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                player_id=player.id,
                team_season_id=team_season.id,
                identity_status="authoritative",
                identity_key="ncaa:123",
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

    def test_authoritative_and_fallback_identity_uniqueness(self):
        from roster_scout.models import Player, PlayerTeamSeason
        with self.Session.begin() as session:
            session.add_all([
                Player(source="ncaa", source_player_id="duplicate", display_name="One"),
                Player(source="ncaa", source_player_id="duplicate", display_name="Two"),
            ])
            with self.assertRaises(IntegrityError):
                session.flush()
        with self.Session.begin() as session:
            organization, team_season, _, snapshot = self.fixture(session)
            players = [
                Player(source="ncaa", display_name="Same Name"),
                Player(source="ncaa", display_name="Same Name"),
            ]
            session.add_all(players)
            session.flush()
            session.add_all([
                PlayerTeamSeason(
                    organization_id=organization.id,
                    snapshot_id=snapshot.id,
                    player_id=player.id,
                    team_season_id=team_season.id,
                    identity_status="fallback",
                    identity_key="fallback:same-name:12:ss",
                    display_name="Same Name",
                )
                for player in players
            ])
            with self.assertRaises(IntegrityError):
                session.flush()


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

    def test_published_rows_cannot_be_reassigned_to_a_draft_snapshot(self):
        from roster_scout.models import BattingFact, ReportSection, ReportSnapshot
        from roster_scout.snapshots import publish_snapshot

        with self.Session.begin() as session:
            organization, _, report, published = self.fixture(session)
            draft = ReportSnapshot(
                organization_id=organization.id,
                report_id=report.id,
                schema_version="1",
            )
            fact = BattingFact(
                organization_id=organization.id,
                snapshot_id=published.id,
                at_bats=1,
                hits=0,
            )
            critical_section = ReportSection(
                organization_id=organization.id,
                snapshot_id=published.id,
                section="fielding",
                critical=True,
                status="complete",
                row_count=0,
                verified_empty=True,
            )
            session.add_all([draft, fact, critical_section])
            self.add_core_sections(session, organization.id, published.id)
            session.flush()
            published_id = published.id
            draft_id = draft.id
            fact_id = fact.id
            section_id = critical_section.id
        with self.Session.begin() as session:
            publish_snapshot(session, published_id)

        for table_name, identifier in (
            ("batting_facts", fact_id),
            ("report_sections", section_id),
        ):
            with self.subTest(table=table_name):
                with self.Session.begin() as session:
                    with self.assertRaises(DBAPIError):
                        session.execute(
                            text(
                                f"UPDATE {table_name} "
                                "SET snapshot_id = :draft_id WHERE id = :id"
                            ),
                            {"draft_id": draft_id, "id": identifier},
                        )

    def test_published_metric_definition_is_append_only(self):
        from roster_scout.models import MetricDefinition, MetricValue
        from roster_scout.snapshots import publish_snapshot

        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            definition = MetricDefinition(code="ops", version="1", formula="obp + slg")
            session.add(definition)
            session.flush()
            session.add(MetricValue(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                metric_definition_id=definition.id,
                value=1,
            ))
            self.add_core_sections(session, organization.id, snapshot.id)
            session.flush()
            definition_id = definition.id
            snapshot_id = snapshot.id
        with self.Session.begin() as session:
            publish_snapshot(session, snapshot_id)

        for statement in (
            text("UPDATE metric_definitions SET formula = 'changed' WHERE id = :id"),
            text("DELETE FROM metric_definitions WHERE id = :id"),
        ):
            with self.Session.begin() as session:
                with self.assertRaises(DBAPIError):
                    session.execute(statement, {"id": definition_id})

        with self.Session.begin() as session:
            session.add(MetricDefinition(code="ops", version="2", formula="new formula"))

    def test_published_snapshot_metadata_and_source_rows_are_immutable(self):
        from roster_scout.models import SourceRecord
        from roster_scout.snapshots import publish_snapshot
        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            source = SourceRecord(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                section="batting",
                row_ordinal=0,
                source_url="https://stats.ncaa.org/example",
                source_team_id="689",
                source_season_id="17040",
                source_season_team_id="615087",
                source_stat_category_id="15867",
                collected_at=datetime.now(UTC),
                parser_version="1",
                schema_version="1",
                payload_sha256="b" * 64,
                raw_values={"H": "1"},
            )
            session.add(source)
            self.add_core_sections(session, organization.id, snapshot.id)
            session.flush()
            snapshot_id = snapshot.id
            source_id = source.id
        with self.Session.begin() as session:
            publish_snapshot(session, snapshot_id)
        statements = (
            (text("UPDATE report_snapshots SET schema_version = '2' WHERE id = :id"), snapshot_id),
            (text("DELETE FROM report_snapshots WHERE id = :id"), snapshot_id),
            (text("UPDATE source_records SET raw_values = '{}'::jsonb WHERE id = :id"), source_id),
            (text("DELETE FROM source_records WHERE id = :id"), source_id),
        )
        for statement, identifier in statements:
            with self.Session.begin() as session:
                with self.assertRaises(DBAPIError):
                    session.execute(statement, {"id": identifier})

    def test_predecessor_from_another_report_is_rejected(self):
        from roster_scout.models import Report, ReportSnapshot, Team, TeamSeason
        with self.Session.begin() as session:
            organization, team_season, _, first = self.fixture(session)
            other_team = Team(
                source="ncaa", source_team_id=str(uuid.uuid4()), name="Other Team"
            )
            session.add(other_team)
            session.flush()
            other_team_season = TeamSeason(
                team_id=other_team.id,
                season_id=team_season.season_id,
                source_season_team_id=str(uuid.uuid4()),
                division="2",
            )
            session.add(other_team_season)
            session.flush()
            other_report = Report(
                organization_id=organization.id,
                team_season_id=other_team_season.id,
                name="Other",
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

    def test_failure_evidence_requires_failed_or_blocked_job(self):
        from roster_scout.models import FailureEvidence, ReportJob
        with self.Session.begin() as session:
            organization, _, report, _ = self.fixture(session)
            job = ReportJob(
                organization_id=organization.id,
                report_id=report.id,
                idempotency_key="pending-job",
                status="queued",
            )
            session.add(job)
            session.flush()
            session.add(FailureEvidence(
                organization_id=organization.id,
                job_id=job.id,
                compressed_html=b"gzip",
                sha256="c" * 64,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            ))
            with self.assertRaises(DBAPIError):
                session.flush()


class MigrationTests(PostgreSQLSnapshotTestCase):
    def test_0003_downgrade_and_reupgrade(self):
        clean_database(self.database_url)
        command.downgrade(alembic_config(self.database_url), "0002")
        command.upgrade(alembic_config(self.database_url), "0003")
        command.downgrade(alembic_config(self.database_url), "0002")
        command.upgrade(alembic_config(self.database_url), "head")
        self.assertIn(
            "ck_batting_facts_walks_nonnegative",
            {
                check["name"]
                for check in inspect(self.engine).get_check_constraints("batting_facts")
            },
        )

    def test_0001_boundary_is_frozen_and_complete(self):
        clean_database(self.database_url)
        downgrade_database(self.database_url)
        self.addCleanup(upgrade_database, self.database_url)
        command.upgrade(alembic_config(self.database_url), "0001")

        inspector = inspect(self.engine)
        self.assertEqual(REQUIRED_TABLES - set(inspector.get_table_names()), set())
        self.assertNotIn(
            "failure_message",
            {column["name"] for column in inspector.get_columns("report_jobs")},
        )
        self.assertNotIn(
            "job_id",
            {column["name"] for column in inspector.get_columns("report_sections")},
        )
        self.assertNotIn(
            "section",
            {column["name"] for column in inspector.get_columns("failure_evidence")},
        )

        index_names = {
            index["name"]
            for index in inspector.get_indexes("players")
            if index.get("name")
        }
        self.assertIn("uq_players_authoritative_source_id", index_names)

        check_names = {
            check["name"]
            for table in REQUIRED_TABLES
            for check in inspector.get_check_constraints(table)
            if check.get("name")
        }
        self.assertIn("ck_report_snapshots_status", check_names)
        self.assertIn("ck_batting_facts_at_bats_nonnegative", check_names)
        self.assertIn("ck_failure_evidence_sha256_length", check_names)

        foreign_key_names = {
            foreign_key["name"]
            for table in REQUIRED_TABLES
            for foreign_key in inspector.get_foreign_keys(table)
            if foreign_key.get("name")
        }
        self.assertIn("fk_report_snapshots_report_organization", foreign_key_names)
        self.assertIn("fk_batting_facts_source_snapshot_organization", foreign_key_names)
        self.assertIn("fk_failure_evidence_job_organization", foreign_key_names)

        with self.engine.connect() as connection:
            trigger_names = set(connection.scalars(text("""
                SELECT trigger_name
                FROM information_schema.triggers
                WHERE trigger_schema = current_schema()
            """)))
            function_names = set(connection.scalars(text("""
                SELECT routine_name
                FROM information_schema.routines
                WHERE routine_schema = current_schema()
                  AND routine_type = 'FUNCTION'
            """)))
        self.assertIn("trg_report_snapshots_immutable", trigger_names)
        self.assertIn("trg_batting_facts_immutable", trigger_names)
        self.assertIn("trg_failure_evidence_failed_jobs_only", trigger_names)
        self.assertIn("reject_published_snapshot_mutation", function_names)
        self.assertIn("reject_published_snapshot_child_mutation", function_names)
        self.assertIn("reject_nonfailure_evidence", function_names)

        command.upgrade(alembic_config(self.database_url), "head")
        downgrade_database(self.database_url)
        upgrade_database(self.database_url)
        self.assertIn("report_snapshots", inspect(self.engine).get_table_names())

    def test_0001_revision_does_not_depend_on_runtime_metadata(self):
        source = (
            PROJECT_ROOT
            / "migrations"
            / "versions"
            / "0001_canonical_snapshot_foundation.py"
        ).read_text()
        for forbidden in ("roster_scout.models", "Base", "create_all", "drop_all"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

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
            for section in ("batting", "pitching"):
                from roster_scout.models import ReportSection
                session.add(ReportSection(
                    organization_id=organization.id,
                    snapshot_id=snapshot.id,
                    section=section,
                    critical=True,
                    status="complete",
                    row_count=0,
                    verified_empty=True,
                ))

        def injected_failure(session, *_):
            if any(
                isinstance(item, ReportSnapshot) and item.status == "ready"
                for item in session.dirty
            ):
                raise RuntimeError("injected publication failure")

        with self.assertRaisesRegex(RuntimeError, "injected publication failure"):
            with self.Session.begin() as session:
                event.listen(session, "before_flush", injected_failure)
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
