"""PostgreSQL invariants for canonical snapshot persistence."""

import os
import threading
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from alembic import command
from sqlalchemy import create_engine, event, func, inspect, select, text
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

SNAPSHOT_CHILD_TABLES = (
    "player_team_seasons",
    "report_sections",
    "source_records",
    "batting_facts",
    "pitching_facts",
    "metric_values",
)

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

    def traceable_fact_links(
        self,
        session,
        organization,
        team_season,
        snapshot,
        *,
        sections=("batting", "pitching"),
    ):
        from roster_scout.models import Player, PlayerTeamSeason, SourceRecord

        player = Player(
            source="ncaa",
            source_player_id=str(uuid.uuid4()),
            display_name="Traceable Player",
        )
        session.add(player)
        session.flush()
        identity = PlayerTeamSeason(
            organization_id=organization.id,
            snapshot_id=snapshot.id,
            player_id=player.id,
            team_season_id=team_season.id,
            identity_status="authoritative",
            identity_key=f"ncaa:{player.source_player_id}",
            display_name=player.display_name,
        )
        session.add(identity)
        session.flush()
        sources = {}
        for ordinal, section in enumerate(sections):
            source = SourceRecord(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                player_team_season_id=identity.id,
                section=section,
                row_ordinal=ordinal,
                source_url=f"https://stats.ncaa.org/{section}",
                source_team_id="689",
                source_season_id="17040",
                source_season_team_id="615087",
                source_stat_category_id=section,
                collected_at=datetime.now(UTC),
                parser_version="1",
                schema_version="1",
                payload_sha256=section[0] * 64,
                raw_values={"Player": player.display_name},
            )
            session.add(source)
            session.flush()
            sources[section] = source
        return identity, sources


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
            organization, team_season, _, snapshot = self.fixture(session)
            identity, sources = self.traceable_fact_links(
                session, organization, team_season, snapshot
            )
            organization_id = organization.id
            snapshot_id = snapshot.id
            identity_id = identity.id
            source_ids = {name: source.id for name, source in sources.items()}
        for table_name, columns in cases.items():
            for column_name in columns:
                with self.subTest(table=table_name, column=column_name):
                    section = table_name.removesuffix("_facts")
                    statement = text(
                        f"INSERT INTO {table_name} "
                        f"(id, organization_id, snapshot_id, player_team_season_id, "
                        f"source_record_id, {column_name}) "
                        f"VALUES (:id, :organization_id, :snapshot_id, :identity_id, "
                        f":source_record_id, -1)"
                    )
                    with self.Session.begin() as session:
                        with self.assertRaises(DBAPIError):
                            session.execute(statement, {
                                "id": uuid.uuid4(),
                                "organization_id": organization_id,
                                "snapshot_id": snapshot_id,
                                "identity_id": identity_id,
                                "source_record_id": source_ids[section],
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
                player_team_season_id=identity.id,
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
    def add_core_sections(self, session, organization_id, snapshot_id, *, batting_rows=0):
        from roster_scout.models import ReportSection
        for section in ("batting", "pitching"):
            row_count = batting_rows if section == "batting" else 0
            session.add(ReportSection(
                organization_id=organization_id,
                snapshot_id=snapshot_id,
                section=section,
                critical=True,
                status="complete",
                row_count=row_count,
                verified_empty=row_count == 0,
                collected_at=datetime.now(UTC),
            ))

    def add_traceable_batting_fact(self, session, organization, team_season, snapshot):
        from roster_scout.models import BattingFact

        identity, sources = self.traceable_fact_links(
            session,
            organization,
            team_season,
            snapshot,
            sections=("batting",),
        )
        fact = BattingFact(
            organization_id=organization.id,
            snapshot_id=snapshot.id,
            player_team_season_id=identity.id,
            source_record_id=sources["batting"].id,
            at_bats=1,
            hits=0,
        )
        session.add(fact)
        session.flush()
        return fact, sources["batting"]

    def test_t_01_01_cross_organization_fact_is_rejected(self):
        from roster_scout.models import BattingFact, Organization
        with self.Session.begin() as session:
            organization, team_season, _, snapshot = self.fixture(session)
            fact, _source = self.add_traceable_batting_fact(
                session, organization, team_season, snapshot
            )
            other = Organization(name="Other Org")
            session.add(other)
            session.flush()
            session.add(BattingFact(
                organization_id=other.id,
                snapshot_id=snapshot.id,
                player_team_season_id=fact.player_team_season_id,
                source_record_id=fact.source_record_id,
                at_bats=0,
                hits=0,
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
            organization, team_season, _, snapshot = self.fixture(session)
            fact, _source = self.add_traceable_batting_fact(
                session, organization, team_season, snapshot
            )
            self.add_core_sections(
                session, organization.id, snapshot.id, batting_rows=1
            )
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
            organization, team_season, report, published = self.fixture(session)
            draft = ReportSnapshot(
                organization_id=organization.id,
                report_id=report.id,
                schema_version="1",
            )
            fact, _source = self.add_traceable_batting_fact(
                session, organization, team_season, published
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
            session.add_all([draft, critical_section])
            self.add_core_sections(
                session, organization.id, published.id, batting_rows=1
            )
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
        from roster_scout.snapshots import publish_snapshot
        with self.Session.begin() as session:
            organization, team_season, _, snapshot = self.fixture(session)
            _fact, source = self.add_traceable_batting_fact(
                session, organization, team_season, snapshot
            )
            self.add_core_sections(
                session, organization.id, snapshot.id, batting_rows=1
            )
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


class SnapshotMutationSerializationTests(PostgreSQLSnapshotTestCase):
    def function_definition(self, function_name):
        with self.engine.connect() as connection:
            return connection.scalar(
                text(
                    """
                    SELECT pg_get_functiondef(procedure.oid)
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = current_schema()
                      AND procedure.proname = :function_name
                    """
                ),
                {"function_name": function_name},
            )

    def test_snapshot_lock_helper_and_every_child_trigger_share_one_protocol(self):
        lock_definition = self.function_definition("lock_snapshot_mutation")
        trigger_definition = self.function_definition(
            "reject_published_snapshot_child_mutation"
        )

        self.assertIsNotNone(lock_definition)
        self.assertIn("pg_advisory_xact_lock", lock_definition)
        self.assertIn("hashtextextended", lock_definition)
        self.assertIsNotNone(trigger_definition)
        self.assertIn("lock_snapshot_mutation", trigger_definition)
        self.assertIn("ORDER BY snapshot_id", trigger_definition)

        with self.engine.connect() as connection:
            trigger_tables = set(
                connection.scalars(
                    text(
                        """
                        SELECT relation.relname
                        FROM pg_trigger AS trigger
                        JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                        JOIN pg_proc AS procedure ON procedure.oid = trigger.tgfoid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = current_schema()
                          AND NOT trigger.tgisinternal
                          AND procedure.proname =
                              'reject_published_snapshot_child_mutation'
                        """
                    )
                )
            )
        self.assertEqual(trigger_tables, set(SNAPSHOT_CHILD_TABLES))

    def test_publisher_acquires_snapshot_lock_before_parent_row_lock(self):
        source = (PROJECT_ROOT / "roster_scout" / "snapshots.py").read_text()
        lock_position = source.index("lock_snapshot_mutation")
        row_lock_position = source.index("with_for_update")
        self.assertLess(lock_position, row_lock_position)

    def test_metric_paths_lock_definitions_before_snapshots_and_enumeration(self):
        lock_definition = self.function_definition(
            "lock_metric_definition_mutation"
        )
        child_definition = self.function_definition(
            "reject_published_snapshot_child_mutation"
        )
        metric_definition = self.function_definition(
            "reject_published_metric_definition_mutation"
        )

        self.assertIsNotNone(lock_definition)
        self.assertIn("pg_advisory_xact_lock", lock_definition)
        for definition in (child_definition, metric_definition):
            definition_lock = definition.index("lock_metric_definition_mutation")
            snapshot_lock = definition.index("lock_snapshot_mutation")
            self.assertLess(definition_lock, snapshot_lock)
            self.assertIn("ORDER BY metric_definition_id", definition)
            self.assertIn("ORDER BY snapshot_id", definition)


class SnapshotMutationRaceTests(PostgreSQLSnapshotTestCase):
    WAIT_SECONDS = 5
    BLOCKED_ASSERTION_SECONDS = 0.25

    def draft_ids(self):
        from roster_scout.models import ReportSection

        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            for section in ("batting", "pitching"):
                session.add(
                    ReportSection(
                        organization_id=organization.id,
                        snapshot_id=snapshot.id,
                        section=section,
                        critical=True,
                        status="complete",
                        row_count=0,
                        verified_empty=True,
                        collected_at=datetime.now(UTC),
                    )
                )
            session.flush()
            return organization.id, snapshot.id

    def independent_sessions(self):
        from roster_scout.database import create_session_factory

        engine = create_engine(self.database_url)
        self.addCleanup(engine.dispose)
        return create_session_factory(engine)

    def start_worker(self, target, *release_events):
        errors = []
        done = threading.Event()

        def run():
            try:
                target()
            except BaseException as error:  # surfaced in the owning test thread
                errors.append(error)
            finally:
                done.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()

        def stop_worker():
            for release_event in release_events:
                release_event.set()
            worker.join(self.WAIT_SECONDS)

        self.addCleanup(stop_worker)
        return worker, done, errors

    def insert_source_record(self, session, organization_id, snapshot_id):
        session.execute(
            text(
                """
                INSERT INTO source_records (
                    id, organization_id, snapshot_id, section, row_ordinal,
                    source_url, source_team_id, source_season_id,
                    source_season_team_id, source_stat_category_id,
                    collected_at, parser_version, schema_version,
                    payload_sha256, raw_values
                ) VALUES (
                    :id, :organization_id, :snapshot_id, 'batting', 0,
                    'https://stats.ncaa.org/batting', '689', '17040',
                    '615087', '15867', now(), '1', '1', :payload_sha256,
                    CAST('{}' AS jsonb)
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "organization_id": organization_id,
                "snapshot_id": snapshot_id,
                "payload_sha256": "a" * 64,
            },
        )

    def assert_worker_finished(self, worker, done):
        self.assertTrue(done.wait(self.WAIT_SECONDS))
        worker.join(self.WAIT_SECONDS)
        self.assertFalse(worker.is_alive())

    def test_child_mutation_commits_before_waiting_publication_validates_it(self):
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot

        organization_id, snapshot_id = self.draft_ids()
        MutationSession = self.independent_sessions()
        PublisherSession = self.independent_sessions()
        mutation_holds_lock = threading.Event()
        release_mutation = threading.Event()
        publisher_outcome = {}

        def mutate():
            with MutationSession.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                self.insert_source_record(session, organization_id, snapshot_id)
                mutation_holds_lock.set()
                if not release_mutation.wait(self.WAIT_SECONDS):
                    raise AssertionError("mutation release event was not signaled")

        def publish():
            try:
                with PublisherSession.begin() as session:
                    session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    publish_snapshot(session, snapshot_id)
            except SnapshotPublicationError as error:
                publisher_outcome["error"] = error

        mutation_worker, _, mutation_errors = self.start_worker(
            mutate, release_mutation
        )
        self.assertTrue(mutation_holds_lock.wait(self.WAIT_SECONDS))
        publisher_worker, publisher_done, publisher_errors = self.start_worker(
            publish, release_mutation
        )
        self.assertFalse(publisher_done.wait(self.BLOCKED_ASSERTION_SECONDS))

        release_mutation.set()
        mutation_worker.join(self.WAIT_SECONDS)
        self.assertFalse(mutation_worker.is_alive())
        self.assert_worker_finished(publisher_worker, publisher_done)
        self.assertEqual(mutation_errors, [])
        self.assertEqual(publisher_errors, [])
        self.assertIn("row count", str(publisher_outcome["error"]))

        from roster_scout.models import ReportSnapshot, SourceRecord

        with self.Session() as session:
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "draft")
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(SourceRecord)
                    .where(SourceRecord.snapshot_id == snapshot_id)
                ),
                1,
            )

    def test_publication_commits_before_waiting_child_mutation_is_rejected(self):
        from roster_scout.snapshots import publish_snapshot

        organization_id, snapshot_id = self.draft_ids()
        PublisherSession = self.independent_sessions()
        MutationSession = self.independent_sessions()
        publication_holds_lock = threading.Event()
        release_publication = threading.Event()

        def publish():
            with PublisherSession.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                publish_snapshot(session, snapshot_id)
                publication_holds_lock.set()
                if not release_publication.wait(self.WAIT_SECONDS):
                    raise AssertionError("publication release event was not signaled")

        def mutate():
            with MutationSession.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                self.insert_source_record(session, organization_id, snapshot_id)

        publisher_worker, _, publisher_errors = self.start_worker(
            publish, release_publication
        )
        self.assertTrue(publication_holds_lock.wait(self.WAIT_SECONDS))
        mutation_worker, mutation_done, mutation_errors = self.start_worker(
            mutate, release_publication
        )
        self.assertFalse(mutation_done.wait(self.BLOCKED_ASSERTION_SECONDS))

        release_publication.set()
        publisher_worker.join(self.WAIT_SECONDS)
        self.assertFalse(publisher_worker.is_alive())
        self.assert_worker_finished(mutation_worker, mutation_done)
        self.assertEqual(publisher_errors, [])
        self.assertEqual(len(mutation_errors), 1)
        self.assertIsInstance(mutation_errors[0], DBAPIError)

        from roster_scout.models import ReportSnapshot, SourceRecord

        with self.Session() as session:
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "ready")
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(SourceRecord)
                    .where(SourceRecord.snapshot_id == snapshot_id)
                ),
                0,
            )

    def test_definition_mutation_commits_before_reference_and_publication(self):
        from roster_scout.models import MetricDefinition, MetricValue
        from roster_scout.snapshots import publish_snapshot

        organization_id, snapshot_id = self.draft_ids()
        with self.Session.begin() as session:
            definition = MetricDefinition(code="ops", version="1", formula="old")
            session.add(definition)
            session.flush()
            definition_id = definition.id

        DefinitionSession = self.independent_sessions()
        PublisherSession = self.independent_sessions()
        definition_holds_lock = threading.Event()
        release_definition = threading.Event()

        def mutate_definition():
            with DefinitionSession.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                session.execute(
                    text(
                        "UPDATE metric_definitions SET formula = 'changed' "
                        "WHERE id = :definition_id"
                    ),
                    {"definition_id": definition_id},
                )
                definition_holds_lock.set()
                if not release_definition.wait(self.WAIT_SECONDS):
                    raise AssertionError("definition release event was not signaled")

        def reference_and_publish():
            with PublisherSession.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                session.add(
                    MetricValue(
                        organization_id=organization_id,
                        snapshot_id=snapshot_id,
                        metric_definition_id=definition_id,
                        value=1,
                    )
                )
                session.flush()
                publish_snapshot(session, snapshot_id)

        definition_worker, _, definition_errors = self.start_worker(
            mutate_definition, release_definition
        )
        self.assertTrue(definition_holds_lock.wait(self.WAIT_SECONDS))
        publisher_worker, publisher_done, publisher_errors = self.start_worker(
            reference_and_publish, release_definition
        )
        self.assertFalse(publisher_done.wait(self.BLOCKED_ASSERTION_SECONDS))

        release_definition.set()
        definition_worker.join(self.WAIT_SECONDS)
        self.assertFalse(definition_worker.is_alive())
        self.assert_worker_finished(publisher_worker, publisher_done)
        self.assertEqual(definition_errors, [])
        self.assertEqual(publisher_errors, [])

        from roster_scout.models import ReportSnapshot

        with self.Session() as session:
            self.assertEqual(session.get(MetricDefinition, definition_id).formula, "changed")
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "ready")
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MetricValue)), 1
            )

    def test_reference_and_publication_commit_before_definition_mutation_rechecks(self):
        from roster_scout.models import MetricDefinition, MetricValue, ReportSnapshot
        from roster_scout.snapshots import publish_snapshot

        organization_id, snapshot_id = self.draft_ids()
        with self.Session.begin() as session:
            definition = MetricDefinition(code="ops", version="1", formula="old")
            session.add(definition)
            session.flush()
            definition_id = definition.id

        PublisherSession = self.independent_sessions()
        DefinitionSession = self.independent_sessions()
        publication_holds_locks = threading.Event()
        release_publication = threading.Event()

        def reference_and_publish():
            with PublisherSession.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                session.add(
                    MetricValue(
                        organization_id=organization_id,
                        snapshot_id=snapshot_id,
                        metric_definition_id=definition_id,
                        value=1,
                    )
                )
                session.flush()
                publish_snapshot(session, snapshot_id)
                publication_holds_locks.set()
                if not release_publication.wait(self.WAIT_SECONDS):
                    raise AssertionError("publication release event was not signaled")

        def mutate_definition():
            with DefinitionSession.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                session.execute(
                    text(
                        "UPDATE metric_definitions SET formula = 'changed' "
                        "WHERE id = :definition_id"
                    ),
                    {"definition_id": definition_id},
                )

        publisher_worker, _, publisher_errors = self.start_worker(
            reference_and_publish, release_publication
        )
        self.assertTrue(publication_holds_locks.wait(self.WAIT_SECONDS))
        definition_worker, definition_done, definition_errors = self.start_worker(
            mutate_definition, release_publication
        )
        self.assertFalse(definition_done.wait(self.BLOCKED_ASSERTION_SECONDS))

        release_publication.set()
        publisher_worker.join(self.WAIT_SECONDS)
        self.assertFalse(publisher_worker.is_alive())
        self.assert_worker_finished(definition_worker, definition_done)
        self.assertEqual(publisher_errors, [])
        self.assertEqual(len(definition_errors), 1)
        self.assertIsInstance(definition_errors[0], DBAPIError)

        with self.Session() as session:
            self.assertEqual(session.get(MetricDefinition, definition_id).formula, "old")
            self.assertEqual(session.get(ReportSnapshot, snapshot_id).status, "ready")
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MetricValue)), 1
            )


class SourceIdentityConstraintTests(PostgreSQLSnapshotTestCase):
    def add_identity(self, session, organization, team_season, snapshot, label):
        from roster_scout.models import Player, PlayerTeamSeason

        player = Player(
            source="ncaa",
            source_player_id=f"player-{label}",
            display_name=f"Player {label}",
        )
        session.add(player)
        session.flush()
        identity = PlayerTeamSeason(
            organization_id=organization.id,
            snapshot_id=snapshot.id,
            player_id=player.id,
            team_season_id=team_season.id,
            identity_status="authoritative",
            identity_key=f"ncaa:player-{label}",
            display_name=player.display_name,
        )
        session.add(identity)
        session.flush()
        return identity

    def add_source(
        self,
        session,
        organization,
        snapshot,
        identity,
        *,
        ordinal,
    ):
        from roster_scout.models import SourceRecord

        source = SourceRecord(
            organization_id=organization.id,
            snapshot_id=snapshot.id,
            player_team_season_id=identity.id if identity is not None else None,
            section="batting",
            row_ordinal=ordinal,
            source_url=f"https://stats.ncaa.org/batting/{ordinal}",
            source_team_id="689",
            source_season_id="17040",
            source_season_team_id="615087",
            source_stat_category_id="15867",
            source_player_id=(f"player-{ordinal}" if identity is not None else None),
            collected_at=datetime.now(UTC),
            parser_version="1",
            schema_version="1",
            payload_sha256=f"{ordinal:x}" * 64,
            raw_values={"Player": f"Player {ordinal}"},
        )
        session.add(source)
        session.flush()
        return source

    def test_source_identity_constraints_are_named_and_drafts_may_be_unbound(self):
        from roster_scout.models import BattingFact, SourceRecord

        columns = {
            column["name"]: column
            for column in inspect(self.engine).get_columns("source_records")
        }
        self.assertIn("player_team_season_id", columns)
        self.assertTrue(columns["player_team_season_id"]["nullable"])
        self.assertTrue(SourceRecord.__table__.c.player_team_season_id.nullable)

        database_source_fks = {
            constraint["name"]
            for constraint in inspect(self.engine).get_foreign_keys("source_records")
        }
        database_fact_fks = {
            constraint["name"]
            for constraint in inspect(self.engine).get_foreign_keys("batting_facts")
        }
        model_source_constraints = {
            constraint.name for constraint in SourceRecord.__table__.constraints
        }
        model_fact_constraints = {
            constraint.name for constraint in BattingFact.__table__.constraints
        }
        self.assertIn(
            "fk_source_records_player_snapshot_organization", database_source_fks
        )
        self.assertIn("fk_batting_facts_source_identity", database_fact_fks)
        self.assertIn(
            "fk_source_records_player_snapshot_organization",
            model_source_constraints,
        )
        self.assertIn("fk_batting_facts_source_identity", model_fact_constraints)

        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            draft_source = self.add_source(
                session,
                organization,
                snapshot,
                None,
                ordinal=15,
            )
            self.assertIsNone(draft_source.player_team_season_id)

    def test_fact_rejects_swapped_source_identity_within_one_snapshot(self):
        from roster_scout.models import BattingFact

        with self.Session.begin() as session:
            organization, team_season, _, snapshot = self.fixture(session)
            identity_a = self.add_identity(
                session, organization, team_season, snapshot, "a"
            )
            identity_b = self.add_identity(
                session, organization, team_season, snapshot, "b"
            )
            source_a = self.add_source(
                session, organization, snapshot, identity_a, ordinal=1
            )
            session.add(
                BattingFact(
                    organization_id=organization.id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=identity_a.id,
                    source_record_id=source_a.id,
                    at_bats=1,
                    hits=1,
                )
            )
            session.flush()

            with self.assertRaisesRegex(
                IntegrityError, "fk_batting_facts_source_identity"
            ):
                session.execute(
                    text(
                        """
                        INSERT INTO batting_facts (
                            id, organization_id, snapshot_id,
                            player_team_season_id, source_record_id, at_bats, hits
                        ) VALUES (
                            :id, :organization_id, :snapshot_id,
                            :identity_id, :source_id, 1, 1
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "organization_id": organization.id,
                        "snapshot_id": snapshot.id,
                        "identity_id": identity_b.id,
                        "source_id": source_a.id,
                    },
                )

    def test_source_identity_rejects_cross_snapshot_and_organization_links(self):
        from roster_scout.models import Organization, ReportSnapshot

        with self.Session.begin() as session:
            organization, team_season, report, snapshot = self.fixture(session)
            identity = self.add_identity(
                session, organization, team_season, snapshot, "owner"
            )
            other_snapshot = ReportSnapshot(
                organization_id=organization.id,
                report_id=report.id,
                schema_version="1",
            )
            session.add(other_snapshot)
            session.flush()
            with self.assertRaisesRegex(
                IntegrityError, "fk_source_records_player_snapshot_organization"
            ):
                self.add_source(
                    session,
                    organization,
                    other_snapshot,
                    identity,
                    ordinal=2,
                )

        with self.Session.begin() as session:
            organization, team_season, _, snapshot = self.fixture(session)
            identity = self.add_identity(
                session, organization, team_season, snapshot, "tenant"
            )
            other_organization = Organization(name="Other Organization")
            session.add(other_organization)
            session.flush()
            with self.assertRaisesRegex(
                IntegrityError,
                "fk_source_records_(?:player_)?snapshot_organization",
            ):
                self.add_source(
                    session,
                    other_organization,
                    snapshot,
                    identity,
                    ordinal=3,
                )


class AuthoritativeSourcePlayerConstraintTests(PostgreSQLSnapshotTestCase):
    """The source NCAA ID must name the Player behind its assigned identity."""

    TRIGGER_NAMES = {
        "trg_source_records_authoritative_source_player",
        "trg_players_authoritative_source_player",
        "trg_player_team_seasons_authoritative_source_player",
    }

    def add_authoritative_link(
        self,
        session,
        organization,
        team_season,
        snapshot,
        *,
        source_player_id="42",
        row_ordinal=0,
    ):
        from roster_scout.models import Player, PlayerTeamSeason, SourceRecord

        player = Player(
            source="ncaa",
            source_player_id=source_player_id,
            display_name=f"Player {source_player_id}",
        )
        session.add(player)
        session.flush()
        identity = PlayerTeamSeason(
            organization_id=organization.id,
            snapshot_id=snapshot.id,
            player_id=player.id,
            team_season_id=team_season.id,
            identity_status="authoritative",
            identity_key=f"ncaa:{source_player_id}",
            display_name=player.display_name,
        )
        session.add(identity)
        session.flush()
        source = SourceRecord(
            organization_id=organization.id,
            snapshot_id=snapshot.id,
            player_team_season_id=identity.id,
            section="batting",
            row_ordinal=row_ordinal,
            source_url="https://stats.ncaa.org/authoritative",
            source_team_id="689",
            source_season_id="17040",
            source_season_team_id="615087",
            source_stat_category_id="15867",
            source_player_id=source_player_id,
            collected_at=datetime.now(UTC),
            parser_version="parser-2+ncaa-core-v2",
            schema_version="ncaa-core-v2",
            payload_sha256=f"{row_ordinal + 1:x}" * 64,
            raw_values={"Player": player.display_name},
        )
        session.add(source)
        session.flush()
        return player, identity, source

    def mark_snapshot_published(self, session, snapshot):
        snapshot.status = "ready"
        snapshot.published_at = datetime.now(UTC)
        session.flush()

    def assert_authoritative_mismatch(
        self,
        statement,
        parameters,
        *,
        disabled_triggers=(),
    ):
        with self.engine.begin() as connection:
            for table_name, trigger_name in disabled_triggers:
                connection.execute(
                    text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}")
                )
            try:
                savepoint = connection.begin_nested()
                try:
                    with self.assertRaisesRegex(
                        DBAPIError,
                        "authoritative source player mismatch",
                    ):
                        connection.execute(text(statement), parameters)
                finally:
                    savepoint.rollback()
            finally:
                for table_name, trigger_name in reversed(disabled_triggers):
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}")
                    )

    def test_named_contract_and_null_unbound_draft_remain_legal(self):
        from roster_scout.models import (
            AUTHORITATIVE_SOURCE_PLAYER_TRIGGER_NAMES,
            SourceRecord,
        )

        with self.engine.connect() as connection:
            trigger_names = set(
                connection.scalars(
                    text(
                        """
                        SELECT trigger_name
                        FROM information_schema.triggers
                        WHERE trigger_schema = current_schema()
                        """
                    )
                )
            )
        self.assertEqual(
            self.TRIGGER_NAMES - trigger_names,
            set(),
        )
        self.assertEqual(
            set(AUTHORITATIVE_SOURCE_PLAYER_TRIGGER_NAMES),
            self.TRIGGER_NAMES,
        )

        with self.Session.begin() as session:
            organization, _, _, snapshot = self.fixture(session)
            session.add(
                SourceRecord(
                    organization_id=organization.id,
                    snapshot_id=snapshot.id,
                    player_team_season_id=None,
                    section="batting",
                    row_ordinal=0,
                    source_url="https://stats.ncaa.org/draft",
                    source_team_id="689",
                    source_season_id="17040",
                    source_season_team_id="615087",
                    source_stat_category_id="15867",
                    source_player_id="42",
                    collected_at=datetime.now(UTC),
                    parser_version="parser-2+ncaa-core-v2",
                    schema_version="ncaa-core-v2",
                    payload_sha256="d" * 64,
                    raw_values={"Player": "Draft Player"},
                )
            )

    def test_source_record_insert_and_update_reject_mismatched_player_id(self):
        with self.Session.begin() as session:
            organization, team_season, _, snapshot = self.fixture(session)
            player, identity, source = self.add_authoritative_link(
                session,
                organization,
                team_season,
                snapshot,
                source_player_id="99",
            )
            ids = {
                "organization_id": organization.id,
                "snapshot_id": snapshot.id,
                "identity_id": identity.id,
                "source_id": source.id,
                "new_source_id": uuid.uuid4(),
                "collected_at": datetime.now(UTC),
            }

        self.assert_authoritative_mismatch(
            """
            INSERT INTO source_records (
                id, organization_id, snapshot_id, player_team_season_id,
                section, row_ordinal, source_url, source_team_id,
                source_season_id, source_season_team_id,
                source_stat_category_id, source_player_id, collected_at,
                parser_version, schema_version, payload_sha256, raw_values
            ) VALUES (
                :new_source_id, :organization_id, :snapshot_id, :identity_id,
                'pitching', 1, 'https://stats.ncaa.org/mismatch', '689',
                '17040', '615087', '15868', '42', :collected_at,
                'parser-2+ncaa-core-v2', 'ncaa-core-v2', :sha, '{}'::jsonb
            )
            """,
            {**ids, "sha": "e" * 64},
        )
        self.assert_authoritative_mismatch(
            "UPDATE source_records SET source_player_id = '42' WHERE id = :source_id",
            ids,
        )

    def test_player_source_and_id_mutations_reject_draft_and_published_links(self):
        for published in (False, True):
            with self.subTest(published=published):
                clean_database(self.database_url)
                with self.Session.begin() as session:
                    organization, team_season, _, snapshot = self.fixture(session)
                    player, _, _ = self.add_authoritative_link(
                        session,
                        organization,
                        team_season,
                        snapshot,
                    )
                    if published:
                        self.mark_snapshot_published(session, snapshot)
                    player_id = player.id

                self.assert_authoritative_mismatch(
                    "UPDATE players SET source = 'other' WHERE id = :player_id",
                    {"player_id": player_id},
                )
                self.assert_authoritative_mismatch(
                    "UPDATE players SET source_player_id = '99' WHERE id = :player_id",
                    {"player_id": player_id},
                )

    def test_identity_player_reassignment_rejects_draft_and_published_links(self):
        from roster_scout.models import Player

        for published in (False, True):
            with self.subTest(published=published):
                clean_database(self.database_url)
                with self.Session.begin() as session:
                    organization, team_season, _, snapshot = self.fixture(session)
                    _, identity, _ = self.add_authoritative_link(
                        session,
                        organization,
                        team_season,
                        snapshot,
                    )
                    replacement = Player(
                        source="ncaa",
                        source_player_id="99",
                        display_name="Replacement Player",
                    )
                    session.add(replacement)
                    session.flush()
                    if published:
                        self.mark_snapshot_published(session, snapshot)
                    identity_id = identity.id
                    replacement_id = replacement.id

                self.assert_authoritative_mismatch(
                    """
                    UPDATE player_team_seasons
                    SET player_id = :replacement_id
                    WHERE id = :identity_id
                    """,
                    {
                        "replacement_id": replacement_id,
                        "identity_id": identity_id,
                    },
                    disabled_triggers=(
                        (
                            "player_team_seasons",
                            "trg_player_team_seasons_immutable",
                        ),
                    ) if published else (),
                )


class MigrationTests(PostgreSQLSnapshotTestCase):
    def function_definition(self, function_name):
        with self.engine.connect() as connection:
            return connection.scalar(
                text(
                    """
                    SELECT pg_get_functiondef(procedure.oid)
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = current_schema()
                      AND procedure.proname = :function_name
                    """
                ),
                {"function_name": function_name},
            )

    def add_source_identity_fixture(self, session):
        from roster_scout.models import (
            BattingFact,
            Player,
            PlayerTeamSeason,
            SourceRecord,
        )

        organization, team_season, _, snapshot = self.fixture(session)
        players = [
            Player(
                source="ncaa",
                source_player_id=f"migration-player-{index}",
                display_name=f"Migration Player {index}",
            )
            for index in (1, 2)
        ]
        session.add_all(players)
        session.flush()
        identities = [
            PlayerTeamSeason(
                organization_id=organization.id,
                snapshot_id=snapshot.id,
                player_id=player.id,
                team_season_id=team_season.id,
                identity_status="authoritative",
                identity_key=f"ncaa:{player.source_player_id}",
                display_name=player.display_name,
            )
            for player in players
        ]
        session.add_all(identities)
        session.flush()
        source = SourceRecord(
            organization_id=organization.id,
            snapshot_id=snapshot.id,
            player_team_season_id=identities[0].id,
            section="batting",
            row_ordinal=0,
            source_url="https://stats.ncaa.org/migration",
            source_team_id="689",
            source_season_id="17040",
            source_season_team_id="615087",
            source_stat_category_id="15867",
            source_player_id=players[0].source_player_id,
            collected_at=datetime.now(UTC),
            parser_version="1",
            schema_version="1",
            payload_sha256="b" * 64,
            raw_values={"Player": players[0].display_name},
        )
        session.add(source)
        session.flush()
        fact = BattingFact(
            organization_id=organization.id,
            snapshot_id=snapshot.id,
            player_team_season_id=identities[0].id,
            source_record_id=source.id,
            at_bats=1,
            hits=1,
        )
        session.add(fact)
        session.flush()
        return organization, snapshot, identities, source

    def test_0008_downgrade_reupgrade_backfill_and_fresh_install(self):
        from roster_scout.models import SourceRecord

        with self.Session.begin() as session:
            _, _, identities, source = self.add_source_identity_fixture(session)
            source_id = source.id
            expected_identity_id = identities[0].id

        command.downgrade(alembic_config(self.database_url), "0007")
        self.assertNotIn(
            "player_team_season_id",
            {
                column["name"]
                for column in inspect(self.engine).get_columns("source_records")
            },
        )
        self.assertIn(
            "fk_batting_facts_source_snapshot_organization",
            {
                constraint["name"]
                for constraint in inspect(self.engine).get_foreign_keys(
                    "batting_facts"
                )
            },
        )

        command.upgrade(alembic_config(self.database_url), "head")
        with self.Session() as session:
            self.assertEqual(
                session.get(SourceRecord, source_id).player_team_season_id,
                expected_identity_id,
            )

        downgrade_database(self.database_url)
        upgrade_database(self.database_url)
        self.assertIn(
            "player_team_season_id",
            {
                column["name"]
                for column in inspect(self.engine).get_columns("source_records")
            },
        )

    def test_0008_rejects_conflicting_existing_source_identity_mappings(self):
        with self.Session.begin() as session:
            organization, snapshot, identities, source = (
                self.add_source_identity_fixture(session)
            )
            organization_id = organization.id
            snapshot_id = snapshot.id
            source_id = source.id
            expected_identity_id = identities[0].id
            conflicting_identity_id = identities[1].id

        command.downgrade(alembic_config(self.database_url), "0007")
        conflicting_fact_id = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO pitching_facts (
                        id, organization_id, snapshot_id,
                        player_team_season_id, source_record_id, innings_outs
                    ) VALUES (
                        :id, :organization_id, :snapshot_id,
                        :identity_id, :source_id, 3
                    )
                    """
                ),
                {
                    "id": conflicting_fact_id,
                    "organization_id": organization_id,
                    "snapshot_id": snapshot_id,
                    "identity_id": conflicting_identity_id,
                    "source_id": source_id,
                },
            )

        try:
            with self.assertRaisesRegex(DBAPIError, "multiple player identities"):
                command.upgrade(alembic_config(self.database_url), "0008")
        finally:
            with self.engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM pitching_facts WHERE id = :id"),
                    {"id": conflicting_fact_id},
                )
            command.upgrade(alembic_config(self.database_url), "head")

        with self.Session() as session:
            from roster_scout.models import SourceRecord

            self.assertEqual(
                session.get(SourceRecord, source_id).player_team_season_id,
                expected_identity_id,
            )

    def test_0010_rejects_existing_authoritative_player_mismatch(self):
        with self.Session.begin() as session:
            _, _, identities, source = self.add_source_identity_fixture(session)
            expected_source_player_id = source.source_player_id
            player_id = identities[0].player_id

        command.downgrade(alembic_config(self.database_url), "0009")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE players
                    SET source_player_id = 'migration-player-mismatch'
                    WHERE id = :player_id
                    """
                ),
                {
                    "player_id": player_id,
                },
            )

        try:
            with self.assertRaisesRegex(
                DBAPIError,
                "authoritative source player mismatch",
            ):
                command.upgrade(alembic_config(self.database_url), "0010")
        finally:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE players
                        SET source_player_id = :source_player_id
                        WHERE id = :player_id
                        """
                    ),
                    {
                        "player_id": player_id,
                        "source_player_id": expected_source_player_id,
                    },
                )
            command.upgrade(alembic_config(self.database_url), "head")

    def test_0010_downgrade_reupgrade_and_fresh_install(self):
        trigger_names = AuthoritativeSourcePlayerConstraintTests.TRIGGER_NAMES

        def installed_triggers():
            with self.engine.connect() as connection:
                return set(
                    connection.scalars(
                        text(
                            """
                            SELECT trigger_name
                            FROM information_schema.triggers
                            WHERE trigger_schema = current_schema()
                            """
                        )
                    )
                )

        self.assertEqual(trigger_names - installed_triggers(), set())
        command.downgrade(alembic_config(self.database_url), "0009")
        self.assertEqual(trigger_names & installed_triggers(), set())
        command.upgrade(alembic_config(self.database_url), "head")
        self.assertEqual(trigger_names - installed_triggers(), set())

        downgrade_database(self.database_url)
        upgrade_database(self.database_url)
        self.assertEqual(trigger_names - installed_triggers(), set())

    def test_0006_downgrade_reupgrade_and_fresh_install(self):
        clean_database(self.database_url)
        command.downgrade(alembic_config(self.database_url), "0005")
        self.addCleanup(command.upgrade, alembic_config(self.database_url), "head")

        self.assertIsNone(self.function_definition("lock_snapshot_mutation"))
        command.upgrade(alembic_config(self.database_url), "0006")
        self.assertIsNotNone(self.function_definition("lock_snapshot_mutation"))

        command.downgrade(alembic_config(self.database_url), "0005")
        self.assertIsNone(self.function_definition("lock_snapshot_mutation"))
        restored_child = self.function_definition(
            "reject_published_snapshot_child_mutation"
        )
        restored_definition = self.function_definition(
            "reject_published_metric_definition_mutation"
        )
        self.assertIn("old_status", restored_child)
        self.assertIn("new_status", restored_child)
        self.assertNotIn("lock_snapshot_mutation", restored_child)
        self.assertNotIn("lock_metric_definition_mutation", restored_definition)

        command.upgrade(alembic_config(self.database_url), "head")
        self.assertIsNotNone(self.function_definition("lock_snapshot_mutation"))
        downgrade_database(self.database_url)
        upgrade_database(self.database_url)
        self.assertIsNotNone(self.function_definition("lock_snapshot_mutation"))

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
