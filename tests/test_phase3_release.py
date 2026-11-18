"""Phase 3 operator diagnostics and external-refresh safety contracts."""

from __future__ import annotations

import json
import sys
import uuid
import unittest
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from alembic import command
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError

from tests.db_support import (
    alembic_config,
    clean_database,
    downgrade_database,
    require_test_database_url,
    upgrade_database,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE3_SNAPSHOT_TABLES = {
    "fielding_facts",
    "season_conference_evidence",
    "games",
    "statistic_scopes",
    "statistic_scope_games",
    "scoped_batting_facts",
    "scoped_pitching_facts",
    "scoped_fielding_facts",
    "career_season_facts",
    "career_aggregates",
    "batting_game_log_facts",
    "pitching_game_log_facts",
    "fielding_game_log_facts",
    "request_ledgers",
}


class ReleaseSubprocessEnvironmentTests(unittest.TestCase):
    """The release gate never forwards parent credentials to child processes."""

    SAFE_RUNTIME = {
        "HOME": "/safe/home",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/safe/bin",
        "TMPDIR": "/safe/tmp",
    }
    DATABASE_URL = (
        "postgresql+psycopg://release:disposable@127.0.0.1/roster_scout_test"
    )
    SECRET_ENVIRONMENT = {
        "BROWSERLESS_TOKEN": "browserless-secret",
        "WORKOS_API_KEY": "workos-secret",
        "GOOGLE_CLIENT_SECRET": "google-secret",
        "AWS_SECRET_ACCESS_KEY": "storage-secret",
        "ROSTER_SCOUT_ARTIFACT_SECRET_ACCESS_KEY": "artifact-secret",
        "FLY_API_TOKEN": "fly-secret",
        "ROSTER_SCOUT_SESSION_SECRET": "session-secret",
        "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": "signing-secret",
        "HTTPS_PROXY": "https://proxy-secret.invalid",
        "NO_PROXY": "secret.internal",
        "ARBITRARY_PARENT_VALUE": "arbitrary-secret",
    }

    def _capture_release_environments(self):
        from scripts import validate_phase3_release as validator

        parent_environment = {
            **self.SAFE_RUNTIME,
            "DATABASE_URL": self.DATABASE_URL,
            "PHASE2_DESTRUCTIVE_DATABASE": "local-disposable",
            **self.SECRET_ENVIRONMENT,
        }
        captured = {}

        def capture(label, _command, *, environment):
            captured[label] = environment

        with patch.dict("os.environ", parent_environment, clear=True), patch.object(
            validator, "_validate_database"
        ), patch.object(validator, "_validate_required_paths"), patch.object(
            validator, "_run", side_effect=capture
        ):
            self.assertEqual(validator.main(), 0)
        return validator, captured

    def test_every_release_child_omits_parent_secrets_and_arbitrary_values(self):
        validator, captured = self._capture_release_environments()

        self.assertEqual(
            set(captured),
            {label for label, _command in validator.release_command_manifest(sys.executable)},
        )
        for label, environment in captured.items():
            with self.subTest(label=label):
                self.assertTrue(self.SECRET_ENVIRONMENT.keys().isdisjoint(environment))
                self.assertEqual(
                    {name: environment.get(name) for name in self.SAFE_RUNTIME},
                    self.SAFE_RUNTIME,
                )
                self.assertLessEqual(
                    set(environment),
                    {
                        *self.SAFE_RUNTIME,
                        "DATABASE_URL",
                        "PHASE2_DESTRUCTIVE_DATABASE",
                    },
                )

    def test_database_and_destructive_sentinel_reach_only_required_children(self):
        validator, captured = self._capture_release_environments()

        for label, environment in captured.items():
            with self.subTest(label=label):
                if label in validator.DATABASE_COMMAND_LABELS:
                    self.assertEqual(environment.get("DATABASE_URL"), self.DATABASE_URL)
                else:
                    self.assertNotIn("DATABASE_URL", environment)
                if label in validator.DESTRUCTIVE_SENTINEL_COMMAND_LABELS:
                    self.assertEqual(
                        environment.get("PHASE2_DESTRUCTIVE_DATABASE"),
                        "local-disposable",
                    )
                else:
                    self.assertNotIn("PHASE2_DESTRUCTIVE_DATABASE", environment)


class MigrationLifecycleTests(unittest.TestCase):
    """Revision 0025 is reversible and preserves published Phase 1/2 evidence."""

    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        cls.engine = create_engine(cls.database_url)

    @classmethod
    def tearDownClass(cls):
        upgrade_database(cls.database_url)
        cls.engine.dispose()

    def setUp(self):
        upgrade_database(self.database_url)
        clean_database(self.database_url)

    def _seed_published_0024_snapshot(self):
        ids = {name: uuid.uuid4() for name in (
            "organization", "team", "season", "team_season", "report",
            "snapshot", "player", "identity", "batting_source",
            "pitching_source", "batting_fact", "pitching_fact", "definition",
            "metric", "artifact", "batting_section", "pitching_section",
        )}
        collected_at = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
        published_at = datetime(2026, 11, 16, 9, 5, tzinfo=UTC)
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO organizations (id, name) VALUES (:id, 'Preserved Org')"
            ), {"id": ids["organization"]})
            connection.execute(text(
                "INSERT INTO teams (id, source, source_team_id, name) "
                "VALUES (:id, 'ncaa', 'legacy-team', 'Preserved Team')"
            ), {"id": ids["team"]})
            connection.execute(text(
                "INSERT INTO seasons (id, source, source_season_id, year) "
                "VALUES (:id, 'ncaa', 'legacy-season', 2026)"
            ), {"id": ids["season"]})
            connection.execute(text(
                "INSERT INTO team_seasons "
                "(id, team_id, season_id, source_season_team_id, division, "
                " batting_stat_category_id, pitching_stat_category_id) "
                "VALUES (:id, :team, :season, 'legacy-team-season', 'd2', "
                " 'legacy-batting', 'legacy-pitching')"
            ), {"id": ids["team_season"], "team": ids["team"], "season": ids["season"]})
            connection.execute(text(
                "INSERT INTO reports (id, organization_id, team_season_id, name) "
                "VALUES (:id, :organization, :team_season, 'Preserved Report')"
            ), {"id": ids["report"], "organization": ids["organization"], "team_season": ids["team_season"]})
            connection.execute(text(
                "INSERT INTO report_snapshots "
                "(id, organization_id, report_id, status, schema_version) "
                "VALUES (:id, :organization, :report, 'draft', 'phase2-schema')"
            ), {"id": ids["snapshot"], "organization": ids["organization"], "report": ids["report"]})
            connection.execute(text(
                "INSERT INTO players (id, source, source_player_id, display_name) "
                "VALUES (:id, 'ncaa', 'legacy-player', 'Preserved Player')"
            ), {"id": ids["player"]})
            connection.execute(text(
                "INSERT INTO player_team_seasons "
                "(id, organization_id, snapshot_id, player_id, team_season_id, "
                " identity_status, identity_key, display_name) "
                "VALUES (:id, :organization, :snapshot, :player, :team_season, "
                " 'authoritative', 'ncaa:legacy-player', 'Preserved Player')"
            ), {"id": ids["identity"], "organization": ids["organization"], "snapshot": ids["snapshot"], "player": ids["player"], "team_season": ids["team_season"]})
            for section, source_key, ordinal, digest in (
                ("batting", "batting_source", 0, "a" * 64),
                ("pitching", "pitching_source", 1, "b" * 64),
            ):
                connection.execute(text(
                    "INSERT INTO source_records "
                    "(id, organization_id, snapshot_id, player_team_season_id, "
                    " section, row_ordinal, source_url, source_team_id, "
                    " source_season_id, source_season_team_id, "
                    " source_stat_category_id, source_player_id, collected_at, "
                    " parser_version, schema_version, payload_sha256, raw_values) "
                    "VALUES (:id, :organization, :snapshot, :identity, :section, "
                    " :ordinal, :url, 'legacy-team', 'legacy-season', "
                    " 'legacy-team-season', :category, 'legacy-player', :collected, "
                    " 'parser-2', 'phase2-schema', :digest, CAST(:raw AS jsonb))"
                ), {
                    "id": ids[source_key], "organization": ids["organization"],
                    "snapshot": ids["snapshot"], "identity": ids["identity"],
                    "section": section, "ordinal": ordinal,
                    "url": f"https://stats.ncaa.org/{section}",
                    "category": f"legacy-{section}", "collected": collected_at,
                    "digest": digest, "raw": json.dumps({"Player": "Preserved Player", "section": section}),
                })
            connection.execute(text(
                "INSERT INTO batting_facts "
                "(id, organization_id, snapshot_id, player_team_season_id, "
                " source_record_id, at_bats, hits, walks, strikeouts) "
                "VALUES (:id, :organization, :snapshot, :identity, :source, 4, 2, 1, 1)"
            ), {"id": ids["batting_fact"], "organization": ids["organization"], "snapshot": ids["snapshot"], "identity": ids["identity"], "source": ids["batting_source"]})
            connection.execute(text(
                "INSERT INTO pitching_facts "
                "(id, organization_id, snapshot_id, player_team_season_id, "
                " source_record_id, innings_outs, hits_allowed, earned_runs, walks, strikeouts) "
                "VALUES (:id, :organization, :snapshot, :identity, :source, 9, 2, 1, 1, 3)"
            ), {"id": ids["pitching_fact"], "organization": ids["organization"], "snapshot": ids["snapshot"], "identity": ids["identity"], "source": ids["pitching_source"]})
            for section, section_key in (("batting", "batting_section"), ("pitching", "pitching_section")):
                connection.execute(text(
                    "INSERT INTO report_sections "
                    "(id, organization_id, snapshot_id, section, critical, status, "
                    " row_count, verified_empty, collected_at) "
                    "VALUES (:id, :organization, :snapshot, :section, true, "
                    " 'complete', 1, false, :collected)"
                ), {"id": ids[section_key], "organization": ids["organization"], "snapshot": ids["snapshot"], "section": section, "collected": collected_at})
            connection.execute(text(
                "INSERT INTO metric_definitions (id, code, version, formula) "
                "VALUES (:id, 'avg', 'legacy-1', 'H / AB')"
            ), {"id": ids["definition"]})
            connection.execute(text(
                "INSERT INTO metric_values "
                "(id, organization_id, snapshot_id, metric_definition_id, "
                " player_team_season_id, value) "
                "VALUES (:id, :organization, :snapshot, :definition, :identity, 0.50000000)"
            ), {"id": ids["metric"], "organization": ids["organization"], "snapshot": ids["snapshot"], "definition": ids["definition"], "identity": ids["identity"]})
            connection.execute(text(
                "INSERT INTO report_artifacts "
                "(id, organization_id, snapshot_id, artifact_type, status, "
                " schema_version, sha256, object_key) "
                "VALUES (:id, :organization, :snapshot, 'xlsx', 'ready', "
                " 'phase2-schema', :digest, 'immutable/preserved.xlsx')"
            ), {"id": ids["artifact"], "organization": ids["organization"], "snapshot": ids["snapshot"], "digest": "c" * 64})
            connection.execute(text(
                "UPDATE report_snapshots SET status = 'ready', published_at = :published "
                "WHERE id = :snapshot"
            ), {"published": published_at, "snapshot": ids["snapshot"]})
        return ids

    def _published_projection(self, ids):
        with self.engine.connect() as connection:
            return {
                "snapshot": tuple(connection.execute(text(
                    "SELECT id, organization_id, report_id, status, schema_version, published_at "
                    "FROM report_snapshots WHERE id = :id"
                ), {"id": ids["snapshot"]}).one()),
                "sources": [tuple(row) for row in connection.execute(text(
                    "SELECT id, section, row_ordinal, payload_sha256, raw_values::text "
                    "FROM source_records WHERE snapshot_id = :id ORDER BY section"
                ), {"id": ids["snapshot"]})],
                "facts": [tuple(row) for row in connection.execute(text(
                    "SELECT 'batting', id, at_bats, hits, walks, strikeouts FROM batting_facts "
                    "WHERE snapshot_id = :id UNION ALL "
                    "SELECT 'pitching', id, innings_outs, hits_allowed, walks, strikeouts "
                    "FROM pitching_facts WHERE snapshot_id = :id ORDER BY 1"
                ), {"id": ids["snapshot"]})],
                "artifact": tuple(connection.execute(text(
                    "SELECT id, status, schema_version, sha256, object_key "
                    "FROM report_artifacts WHERE snapshot_id = :id"
                ), {"id": ids["snapshot"]}).one()),
            }

    def test_upgrade_0024_preserves_published_snapshot(self):
        command.downgrade(alembic_config(self.database_url), "0024")
        ids = self._seed_published_0024_snapshot()
        before = self._published_projection(ids)
        command.upgrade(alembic_config(self.database_url), "0025")
        self.assertEqual(self._published_projection(ids), before)

        migration_source = (
            PROJECT_ROOT / "migrations" / "versions" /
            "0025_detailed_ncaa_intelligence.py"
        ).read_text()
        self.assertIn(
            "PHASE3_SCHEMA_FINALIZED = True",
            migration_source,
            "revision 0025 has not declared its final audited schema boundary",
        )

    def test_0025_downgrade_reupgrade_and_fresh_head(self):
        command.downgrade(alembic_config(self.database_url), "0024")
        command.upgrade(alembic_config(self.database_url), "0025")
        command.downgrade(alembic_config(self.database_url), "0024")
        command.upgrade(alembic_config(self.database_url), "head")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT version_num FROM alembic_version")),
                "0028",
            )
        downgrade_database(self.database_url)
        upgrade_database(self.database_url)
        self.assertEqual(set(inspect(self.engine).get_table_names()) & PHASE3_SNAPSHOT_TABLES, PHASE3_SNAPSHOT_TABLES)

    def test_0028_adds_reversible_two_stage_build_contract(self):
        command.downgrade(alembic_config(self.database_url), "0027")
        before = inspect(self.engine)
        self.assertNotIn(
            "build_profile",
            {column["name"] for column in before.get_columns("report_jobs")},
        )

        command.upgrade(alembic_config(self.database_url), "0028")
        current = inspect(self.engine)
        self.assertIn(
            "build_profile",
            {column["name"] for column in current.get_columns("report_jobs")},
        )
        job_checks = {
            constraint["name"]: constraint.get("sqltext", "")
            for constraint in current.get_check_constraints("report_jobs")
        }
        snapshot_checks = {
            constraint["name"]: constraint.get("sqltext", "")
            for constraint in current.get_check_constraints("report_snapshots")
        }
        self.assertIn("ck_report_jobs_build_profile", job_checks)
        self.assertIn("quick", job_checks["ck_report_jobs_build_profile"])
        self.assertIn(
            "quick-season-v1",
            snapshot_checks["ck_report_snapshots_collection_contract_version"],
        )
        ids = {name: uuid.uuid4() for name in (
            "organization", "team", "season", "team_season", "report",
            "snapshot", "job",
        )}
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO organizations (id, name) VALUES (:id, 'Quick Org')"
            ), {"id": ids["organization"]})
            connection.execute(text(
                "INSERT INTO teams (id, source, source_team_id, name) "
                "VALUES (:id, 'ncaa', 'quick-team', 'Quick Team')"
            ), {"id": ids["team"]})
            connection.execute(text(
                "INSERT INTO seasons (id, source, source_season_id, year) "
                "VALUES (:id, 'ncaa', 'quick-season', 2026)"
            ), {"id": ids["season"]})
            connection.execute(text(
                "INSERT INTO team_seasons "
                "(id, team_id, season_id, source_season_team_id, division) "
                "VALUES (:team_season, :team, :season, "
                "'quick-team-season', 'd1')"
            ), ids)
            connection.execute(text(
                "INSERT INTO reports (id, organization_id, team_season_id, name) "
                "VALUES (:report, :organization, :team_season, 'Quick Report')"
            ), ids)
            connection.execute(text(
                "INSERT INTO report_snapshots "
                "(id, organization_id, report_id, status, schema_version, "
                " collection_contract_version, published_at) "
                "VALUES (:snapshot, :organization, :report, 'ready', 'test', "
                " 'quick-season-v1', now())"
            ), ids)
            connection.execute(text(
                "INSERT INTO report_jobs "
                "(id, organization_id, report_id, result_snapshot_id, "
                " idempotency_key, build_profile, status) "
                "VALUES (:job, :organization, :report, :snapshot, "
                " 'quick-migration', 'quick', 'ready')"
            ), ids)

        command.downgrade(alembic_config(self.database_url), "0027")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text(
                    "SELECT collection_contract_version FROM report_snapshots "
                    "WHERE id = :snapshot"
                ), ids),
                "legacy-core-v1",
            )
        self.assertNotIn(
            "build_profile",
            {column["name"] for column in inspect(self.engine).get_columns("report_jobs")},
        )
        command.upgrade(alembic_config(self.database_url), "0028")


class DivisionParityTests(unittest.TestCase):
    """Representative D1/D2/D3 fixtures expose one canonical detailed schema."""

    def test_representatives_share_fields_scopes_history_and_status_vocabulary(self):
        from roster_scout import ncaa_detail

        fixture_root = PROJECT_ROOT / "tests" / "fixtures" / "ncaa_detail"
        representatives = {
            "d1": ("9698671", "614606"),
            "d2": ("11219748", "615087"),
            "d3": ("9697775", "615349"),
        }
        field_sets = []
        career_type_sets = []
        for division, (player_id, season_team_id) in representatives.items():
            fielding = ncaa_detail.parse_fielding_page(
                (fixture_root / division / "fielding.html").read_text(),
                source_url=(
                    f"https://stats.ncaa.org/teams/{season_team_id}/season_to_date_stats"
                    "?year_stat_category_id=15869"
                ),
                stat_category_id="15869",
            )
            detail = ncaa_detail.parse_player_detail_fixture(
                (fixture_root / division / "player_detail.html").read_text(),
                expected_player_id=player_id,
                expected_season_id="17040",
                expected_season_team_id=season_team_id,
                expected_division=division,
            )
            field_sets.append(set(fielding.rows[0].canonical_values))
            career_type_sets.append({row.stat_type for row in detail.career_seasons})
        self.assertTrue(all(item == field_sets[0] for item in field_sets[1:]))
        self.assertEqual(career_type_sets, [{"batting", "pitching", "fielding"}] * 3)


class Phase3PostgreSQLTestCase(unittest.TestCase):
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

    def fixture(self, *, name="Diagnostic Organization"):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import Organization, Report, Season, Team, TeamSeason

        with self.Session.begin() as session:
            organization = Organization(name=name)
            team = Team(source="ncaa", source_team_id=str(uuid.uuid4()), name="Team")
            season = Season(source="ncaa", source_season_id=str(uuid.uuid4()), year=2026)
            session.add_all((organization, team, season))
            session.flush()
            team_season = TeamSeason(
                team_id=team.id,
                season_id=season.id,
                source_season_team_id=str(uuid.uuid4()),
                division="2",
                batting_stat_category_id="batting",
                pitching_stat_category_id="pitching",
            )
            session.add(team_season)
            session.flush()
            report = Report(
                organization_id=organization.id,
                team_season_id=team_season.id,
                name="Customer Report Name",
            )
            session.add(report)
            session.flush()
            organization_id, report_id = organization.id, report.id
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            f"diagnostic-{uuid.uuid4()}",
            dispatch=None,
        )
        return organization_id, report_id, job.id


class Phase3SchemaSecurityTests(Phase3PostgreSQLTestCase):
    def test_every_snapshot_family_has_composite_tenant_fk_and_immutability_trigger(self):
        inspector = inspect(self.engine)
        with self.engine.connect() as connection:
            triggers = set(connection.execute(text(
                "SELECT event_object_table, trigger_name "
                "FROM information_schema.triggers "
                "WHERE trigger_schema = current_schema()"
            )))
        for table_name in sorted(PHASE3_SNAPSHOT_TABLES):
            with self.subTest(table=table_name):
                foreign_keys = inspector.get_foreign_keys(table_name)
                self.assertTrue(any(
                    set(foreign_key["constrained_columns"])
                    >= {"snapshot_id", "organization_id"}
                    and foreign_key["referred_table"] == "report_snapshots"
                    for foreign_key in foreign_keys
                ))
                self.assertIn(
                    (table_name, f"trg_{table_name}_immutable"),
                    triggers,
                )
        self.assertNotIn(
            ("external_refresh_diagnostics", "trg_external_refresh_diagnostics_immutable"),
            triggers,
        )

    def test_new_family_manifest_covers_field_scope_game_history_metric_and_ledger(self):
        expected_groups = {
            "fielding": {"fielding_facts"},
            "scope_membership": {"statistic_scopes", "statistic_scope_games"},
            "scoped_facts": {
                "scoped_batting_facts", "scoped_pitching_facts",
                "scoped_fielding_facts",
            },
            "game": {"games"},
            "career_log": {
                "career_season_facts", "career_aggregates",
                "batting_game_log_facts", "pitching_game_log_facts",
                "fielding_game_log_facts",
            },
            "ledger": {"request_ledgers"},
        }
        for group, tables in expected_groups.items():
            with self.subTest(group=group):
                self.assertEqual(tables - PHASE3_SNAPSHOT_TABLES, set())
        self.assertIn("metric_constant_sets", inspect(self.engine).get_table_names())


class MetricConstantSetTests(Phase3PostgreSQLTestCase):
    def test_database_starts_empty_and_missing_constants_are_explicit(self):
        from roster_scout.metrics import calculate_metric
        from roster_scout.models import MetricConstantSet

        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MetricConstantSet)),
                0,
            )
        woba = calculate_metric("woba", {
            "at_bats": 10,
            "walks": 2,
            "hit_by_pitch": 1,
            "sacrifice_flies": 1,
            "singles": 3,
            "doubles": 1,
            "triples": 0,
            "home_runs": 1,
        })
        fip = calculate_metric("fip", {
            "innings_outs": 18,
            "home_runs_allowed": 1,
            "walks": 1,
            "hit_batters": 0,
            "strikeouts": 6,
        })
        for result in (woba, fip):
            self.assertEqual(result.status, "unavailable_constant")
            self.assertEqual(result.reason_code, "verified_ncaa_constant_required")
            self.assertIsNone(result.value)

    def test_metric_values_use_exact_composite_constant_definition_linkage(self):
        foreign_keys = inspect(self.engine).get_foreign_keys("metric_values")
        linkage = next(
            item
            for item in foreign_keys
            if item["name"] == "fk_metric_values_constant_definition"
        )
        self.assertEqual(
            linkage["constrained_columns"],
            ["constant_set_id", "metric_definition_id"],
        )
        self.assertEqual(
            linkage["referred_columns"],
            ["id", "metric_definition_id"],
        )

    def test_verified_constant_is_immutable_and_scope_is_unique(self):
        from roster_scout.models import (
            MetricConstantSet,
            MetricDefinition,
            Report,
            TeamSeason,
        )

        _organization_id, report_id, _job_id = self.fixture()
        now = datetime.now(UTC)
        with self.Session.begin() as session:
            report = session.get(Report, report_id)
            team_season = session.get(TeamSeason, report.team_season_id)
            definition = MetricDefinition(code="fip", version="release-1", formula="formula")
            session.add(definition)
            session.flush()
            constant = MetricConstantSet(
                metric_definition_id=definition.id,
                metric_code="fip",
                version="ncaa-2026-d2-v1",
                season_id=team_season.season_id,
                division=team_season.division,
                constant_type="additive_constant",
                values_json={"constant": "3.2"},
                methodology="Audited NCAA season/division methodology",
                source_url="https://stats.ncaa.org/constants",
                source_identifier="fip:2026:d2:v1",
                collected_at=now,
                payload_sha256="e" * 64,
                verification_status="verified",
                verified_at=now,
            )
            session.add(constant)
            session.flush()
            constant_id = constant.id
        with self.Session.begin() as session:
            with self.assertRaises(DBAPIError):
                session.execute(text(
                    "UPDATE metric_constant_sets SET methodology = 'changed' "
                    "WHERE id = :constant_id"
                ), {"constant_id": constant_id})


class ExternalRefreshDiagnosticTests(Phase3PostgreSQLTestCase):
    def test_power_bi_lifecycle_is_tenant_owned_safe_and_controlled(self):
        from roster_scout.models import ExternalRefreshDiagnostic, ReportJob, ReportSnapshot

        organization_id, report_id, job_id = self.fixture()
        other_organization_id, _other_report_id, _ = self.fixture(name="Other Customer")
        now = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
        with self.Session.begin() as session:
            job = session.get(ReportJob, job_id)
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(snapshot)
            session.flush()
            job.result_snapshot_id = snapshot.id
            diagnostic = ExternalRefreshDiagnostic(
                organization_id=organization_id,
                report_id=report_id,
                snapshot_id=snapshot.id,
                provider="power_bi",
                status="pending",
                attempt_number=1,
                correlation_id=f"refresh-{uuid.uuid4()}",
                requested_at=now,
                updated_at=now,
            )
            session.add(diagnostic)
            session.flush()
            diagnostic.started_at = now + timedelta(seconds=1)
            diagnostic.completed_at = now + timedelta(seconds=2)
            diagnostic.updated_at = now + timedelta(seconds=2)
            diagnostic.status = "failed"
            diagnostic.safe_category = "provider_unavailable"
            diagnostic.safe_reason_code = "capacity_unavailable"

        with self.Session() as session:
            stored = session.scalar(select(ExternalRefreshDiagnostic))
            self.assertEqual(stored.provider, "power_bi")
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.safe_reason_code, "capacity_unavailable")
            stored.organization_id = other_organization_id
            with self.assertRaises(DBAPIError):
                session.flush()

    def test_completed_attempt_and_published_ledger_immutability(self):
        from roster_scout.models import (
            ExternalRefreshDiagnostic,
            ReportJob,
            ReportSnapshot,
            RequestLedger,
        )

        organization_id, report_id, job_id = self.fixture()
        now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
        with self.Session.begin() as session:
            job = session.get(ReportJob, job_id)
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(snapshot)
            session.flush()
            job.result_snapshot_id = snapshot.id
            ledger = RequestLedger(
                organization_id=organization_id,
                job_id=job.id,
                snapshot_id=snapshot.id,
                family="schedule",
                source_identity="ncaa:schedule:stable",
                request_kind="cache",
                outcome="cache_hit",
                started_at=now,
                completed_at=now,
                budget_remaining=150,
            )
            refresh = ExternalRefreshDiagnostic(
                organization_id=organization_id,
                report_id=report_id,
                snapshot_id=snapshot.id,
                provider="power_bi",
                status="pending",
                attempt_number=1,
                correlation_id=f"complete-{uuid.uuid4()}",
                requested_at=now,
                updated_at=now,
            )
            session.add_all((ledger, refresh))
            session.flush()
            refresh.status = "completed"
            refresh.started_at = now + timedelta(seconds=1)
            refresh.completed_at = now + timedelta(seconds=2)
            refresh.updated_at = now + timedelta(seconds=2)
            snapshot.status = "ready"
            snapshot.published_at = now + timedelta(seconds=3)
            ledger_id = ledger.id

        with self.Session() as session:
            stored_refresh = session.scalar(select(ExternalRefreshDiagnostic))
            self.assertEqual(stored_refresh.status, "completed")
            stored_ledger = session.get(RequestLedger, ledger_id)
            stored_ledger.safe_category = "source_unavailable"
            with self.assertRaises(DBAPIError):
                session.flush()

    def test_request_ledger_rejects_cross_tenant_job_snapshot_substitution(self):
        from roster_scout.models import ReportJob, ReportSnapshot, RequestLedger

        organization_id, report_id, job_id = self.fixture()
        other_organization_id, other_report_id, other_job_id = self.fixture(
            name="Other Tenant"
        )
        now = datetime.now(UTC)
        with self.Session.begin() as session:
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            other_snapshot = ReportSnapshot(
                organization_id=other_organization_id,
                report_id=other_report_id,
                schema_version="schema-1",
            )
            session.add_all((snapshot, other_snapshot))
            session.flush()
            session.get(ReportJob, job_id).result_snapshot_id = snapshot.id
            session.get(ReportJob, other_job_id).result_snapshot_id = other_snapshot.id
            session.add(RequestLedger(
                organization_id=organization_id,
                job_id=other_job_id,
                snapshot_id=snapshot.id,
                family="fielding",
                source_identity="ncaa:fielding:stable",
                request_kind="live",
                outcome="live_complete",
                started_at=now,
                completed_at=now,
                budget_remaining=149,
            ))
            with self.assertRaises(DBAPIError):
                session.flush()

    def test_attempt_and_correlation_identity_are_unique_and_terminal_is_one_way(self):
        from roster_scout.models import ExternalRefreshDiagnostic, ReportJob, ReportSnapshot

        organization_id, report_id, job_id = self.fixture()
        now = datetime.now(UTC)
        correlation = f"release-{uuid.uuid4()}"
        with self.Session.begin() as session:
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(snapshot)
            session.flush()
            session.get(ReportJob, job_id).result_snapshot_id = snapshot.id
            diagnostic = ExternalRefreshDiagnostic(
                organization_id=organization_id,
                report_id=report_id,
                snapshot_id=snapshot.id,
                provider="power_bi",
                status="pending",
                attempt_number=1,
                correlation_id=correlation,
                requested_at=now,
                updated_at=now,
            )
            session.add(diagnostic)
            session.flush()
            diagnostic.status = "failed"
            diagnostic.started_at = now
            diagnostic.completed_at = now
            diagnostic.updated_at = now
            diagnostic.safe_category = "provider_unavailable"
            diagnostic.safe_reason_code = "capacity_unavailable"
            session.flush()
            diagnostic_id = diagnostic.id
        with self.Session.begin() as session:
            stored = session.get(ExternalRefreshDiagnostic, diagnostic_id)
            stored.status = "completed"
            with self.assertRaises(DBAPIError):
                session.flush()


class OperatorDiagnosticsTests(Phase3PostgreSQLTestCase):
    def _run(self, arguments, *, environ=None):
        from scripts import inspect_report_jobs

        stdout, stderr = StringIO(), StringIO()
        status = inspect_report_jobs.main(
            arguments,
            environ={"DATABASE_URL": self.database_url, **(environ or {})},
            stdout=stdout,
            stderr=stderr,
        )
        return status, stdout.getvalue(), stderr.getvalue()

    def test_invalid_inputs_and_database_failure_are_fixed_and_sanitized(self):
        status, stdout, stderr = self._run(["--job-id", "player@example.edu"])
        self.assertEqual((status, stdout, stderr), (2, "", "report job diagnostics invalid arguments\n"))

        from scripts import inspect_report_jobs

        with patch.object(
            inspect_report_jobs,
            "_runtime_dependencies",
            side_effect=RuntimeError(
                "postgresql://private-user:secret@remote/customer?token=unsafe"
            ),
        ):
            status, stdout, stderr = self._run(["--older-than-seconds", "60"])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "report job diagnostics unavailable\n")
        self.assertNotIn("private-user", stderr)
        self.assertNotIn("secret", stderr)

    def test_sorted_metadata_only_output_identifies_stuck_and_failed_power_bi(self):
        from roster_scout.models import (
            ExternalRefreshDiagnostic,
            ReportJob,
            ReportSection,
            ReportSnapshot,
            RequestLedger,
        )

        organization_id, report_id, job_id = self.fixture()
        now = datetime.now(UTC)
        correlation_id = f"opaque-{uuid.uuid4()}"
        with self.Session.begin() as session:
            job = session.get(ReportJob, job_id)
            job.status = "running"
            job.started_at = now - timedelta(hours=1)
            snapshot = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(snapshot)
            session.flush()
            job.result_snapshot_id = snapshot.id
            session.add(RequestLedger(
                organization_id=organization_id,
                job_id=job.id,
                snapshot_id=snapshot.id,
                family="player_history",
                source_identity="ncaa:player:123:batting",
                request_kind="live",
                outcome="capacity_exhausted",
                started_at=now - timedelta(minutes=5),
                completed_at=now - timedelta(minutes=4),
                safe_category="browser_capacity",
                budget_remaining=0,
            ))
            session.add(ReportSection(
                organization_id=organization_id,
                job_id=job.id,
                snapshot_id=snapshot.id,
                section="game_logs:fielding",
                critical=False,
                status="unavailable",
                row_count=0,
                verified_empty=False,
                collected_at=now - timedelta(minutes=4),
                failure_category="request_budget_exhausted",
                schema_version="schema-1",
                parser_version="parser-1",
            ))
            session.add(ExternalRefreshDiagnostic(
                organization_id=organization_id,
                report_id=report_id,
                snapshot_id=snapshot.id,
                provider="power_bi",
                status="failed",
                safe_category="provider_unavailable",
                safe_reason_code="capacity_unavailable",
                attempt_number=1,
                correlation_id=correlation_id,
                requested_at=now - timedelta(minutes=3),
                started_at=now - timedelta(minutes=2),
                completed_at=now - timedelta(minutes=1),
                updated_at=now - timedelta(minutes=1),
            ))

        before = None
        with self.Session() as session:
            before = session.scalar(select(func.count()).select_from(RequestLedger))
        status, stdout, stderr = self._run(["--job-id", str(job_id)])
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(stdout, json.dumps(json.loads(stdout), sort_keys=True) + "\n")
        payload = json.loads(stdout)
        self.assertEqual(payload["jobs"][0]["state_category"], "running_stuck")
        self.assertEqual(payload["jobs"][0]["capacity_category"], "browser_capacity")
        self.assertEqual(payload["jobs"][0]["request_budget"]["remaining"], 0)
        self.assertEqual(
            payload["jobs"][0]["sections"],
            [{
                "row_count": 0,
                "safe_reason": "request_budget_exhausted",
                "section": "game_logs:fielding",
                "status": "unavailable",
            }],
        )
        self.assertEqual(payload["jobs"][0]["external_refreshes"][0]["provider"], "power_bi")
        self.assertEqual(payload["jobs"][0]["external_refreshes"][0]["correlation_id"], correlation_id)
        lowered = stdout.casefold()
        for denied in (
            "customer report name", "diagnostic organization", "player",
            "@example", "token=", "private-user", "secret", "stats.ncaa.org",
        ):
            self.assertNotIn(denied, lowered)
        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(RequestLedger)), before
            )


if __name__ == "__main__":
    unittest.main()
