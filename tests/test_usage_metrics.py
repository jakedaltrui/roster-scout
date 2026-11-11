"""PostgreSQL coverage for privacy-conscious product usage metrics."""

import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch
import json

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tests.db_support import (
    alembic_config,
    clean_database,
    require_test_database_url,
    upgrade_database,
)


class UsageEventSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        upgrade_database(cls.database_url)
        cls.engine = create_engine(cls.database_url)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        clean_database(self.database_url)

    def _organization_id(self):
        organization_id = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, name) "
                    "VALUES (:id, 'Usage Metrics Test')"
                ),
                {"id": organization_id},
            )
        return organization_id

    def _insert_event(self, **overrides):
        values = {
            "id": uuid.uuid4(),
            "event_type": "workbook_download_served",
            "event_key": str(uuid.uuid4()),
            "occurred_at": datetime.now(UTC),
            "actor_account_id": None,
            "organization_id": None,
        }
        values.update(overrides)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO product_usage_events "
                    "(id, event_type, event_key, occurred_at, "
                    "actor_account_id, organization_id) "
                    "VALUES (:id, :event_type, :event_key, :occurred_at, "
                    ":actor_account_id, :organization_id)"
                ),
                values,
            )
        return values

    def test_schema_is_minimal_named_and_privacy_safe(self):
        inspector = inspect(self.engine)
        columns = {item["name"] for item in inspector.get_columns("product_usage_events")}
        self.assertEqual(
            columns,
            {
                "id",
                "event_type",
                "event_key",
                "occurred_at",
                "actor_account_id",
                "organization_id",
            },
        )
        forbidden = {
            "ip",
            "ip_address",
            "email",
            "cookie",
            "cookie_id",
            "user_agent",
            "metadata",
            "fingerprint",
            "pseudonymous_id",
        }
        self.assertFalse(columns & forbidden)
        checks = {
            item["name"]
            for item in inspector.get_check_constraints("product_usage_events")
        }
        self.assertIn("ck_product_usage_events_type", checks)
        self.assertIn("ck_product_usage_events_actor_scope", checks)
        self.assertIn("ck_product_usage_events_authenticated_scope", checks)
        unique_names = {
            item["name"]
            for item in inspector.get_unique_constraints("product_usage_events")
        }
        self.assertIn("uq_product_usage_events_type_key", unique_names)
        foreign_keys = {
            item["name"]
            for item in inspector.get_foreign_keys("product_usage_events")
        }
        self.assertIn("fk_product_usage_events_organization", foreign_keys)

    def test_valid_anonymous_download_and_authenticated_use(self):
        self._insert_event()
        organization_id = self._organization_id()
        self._insert_event(
            event_type="authenticated_use",
            actor_account_id=uuid.uuid4(),
            organization_id=organization_id,
        )
        with self.engine.connect() as connection:
            count = connection.scalar(text("SELECT count(*) FROM product_usage_events"))
        self.assertEqual(count, 2)

    def test_database_rejects_unsupported_types_duplicates_and_bad_scope(self):
        organization_id = self._organization_id()
        event_key = str(uuid.uuid4())
        self._insert_event(event_key=event_key)
        invalid_cases = (
            {"event_type": "page_view"},
            {"event_key": event_key},
            {"actor_account_id": uuid.uuid4()},
            {"organization_id": organization_id},
            {"event_type": "authenticated_use"},
            {
                "event_type": "authenticated_use",
                "actor_account_id": uuid.uuid4(),
            },
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(DBAPIError):
                    self._insert_event(**overrides)

    def test_database_rejects_unknown_organization(self):
        with self.assertRaises(DBAPIError):
            self._insert_event(
                event_type="authenticated_use",
                actor_account_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
            )

    def test_migration_downgrade_and_reupgrade_are_reversible(self):
        config = alembic_config(self.database_url)
        command.downgrade(config, "0011")
        try:
            self.assertNotIn(
                "product_usage_events",
                inspect(self.engine).get_table_names(),
            )
        finally:
            command.upgrade(config, "head")
        self.assertIn("product_usage_events", inspect(self.engine).get_table_names())

    def test_migration_fresh_install_creates_usage_table(self):
        config = alembic_config(self.database_url)
        command.downgrade(config, "base")
        try:
            self.assertNotIn(
                "product_usage_events",
                inspect(self.engine).get_table_names(),
            )
        finally:
            command.upgrade(config, "head")
        self.assertIn("product_usage_events", inspect(self.engine).get_table_names())

    def test_orm_model_matches_usage_event_contract(self):
        from roster_scout.models import ProductUsageEvent

        self.assertEqual(ProductUsageEvent.__tablename__, "product_usage_events")
        self.assertEqual(
            set(ProductUsageEvent.__table__.columns.keys()),
            {
                "id",
                "event_type",
                "event_key",
                "occurred_at",
                "actor_account_id",
                "organization_id",
            },
        )
class UsageServiceTests(unittest.TestCase):
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

    def _organization(self, name):
        from roster_scout.models import Organization

        with self.Session.begin() as session:
            organization = Organization(name=name)
            session.add(organization)
            session.flush()
            return organization.id

    def test_duplicate_and_concurrent_download_recording_is_idempotent(self):
        from roster_scout.usage import record_workbook_download_served

        event_key = str(uuid.uuid4())

        def record_once():
            with self.Session.begin() as session:
                return record_workbook_download_served(
                    session,
                    event_key=event_key,
                ).id

        with ThreadPoolExecutor(max_workers=2) as pool:
            identifiers = list(pool.map(lambda _value: record_once(), range(2)))
        self.assertEqual(identifiers[0], identifiers[1])
        with self.Session.begin() as session:
            self.assertEqual(record_once(), identifiers[0])

        with self.Session() as session:
            count = session.scalar(
                text(
                    "SELECT count(*) FROM product_usage_events "
                    "WHERE event_type = 'workbook_download_served'"
                )
            )
        self.assertEqual(count, 1)

    def test_windowed_metrics_count_downloads_and_distinct_nonnull_actors(self):
        from roster_scout.usage import (
            query_usage_metrics,
            record_authenticated_report_request_accepted,
            record_workbook_download_served,
        )

        organization_id = self._organization("Window Org")
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = start + timedelta(days=1)
        actor_one = uuid.uuid4()
        actor_two = uuid.uuid4()
        with self.Session.begin() as session:
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                occurred_at=start,
            )
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                occurred_at=end - timedelta(microseconds=1),
            )
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                occurred_at=end,
            )
            for actor in (actor_one, actor_one, actor_two):
                record_authenticated_report_request_accepted(
                    session,
                    event_key=str(uuid.uuid4()),
                    actor_account_id=actor,
                    organization_id=organization_id,
                    occurred_at=start + timedelta(hours=1),
                )
        with self.Session() as session:
            metrics = query_usage_metrics(session, start=start, end=end)
        self.assertEqual(metrics.successful_workbook_downloads, 2)
        self.assertEqual(metrics.unique_authenticated_users, 2)
        self.assertEqual(metrics.window_start, start)
        self.assertEqual(metrics.window_end, end)

    def test_organization_filter_is_scoped_while_global_query_includes_all(self):
        from roster_scout.usage import (
            query_usage_metrics,
            record_authenticated_report_request_accepted,
            record_workbook_download_served,
        )

        first_org = self._organization("First Org")
        second_org = self._organization("Second Org")
        start = datetime(2026, 2, 1, tzinfo=UTC)
        end = start + timedelta(days=1)
        with self.Session.begin() as session:
            for organization_id in (first_org, second_org):
                actor_id = uuid.uuid4()
                record_authenticated_report_request_accepted(
                    session,
                    event_key=str(uuid.uuid4()),
                    actor_account_id=actor_id,
                    organization_id=organization_id,
                    occurred_at=start,
                )
                record_workbook_download_served(
                    session,
                    event_key=str(uuid.uuid4()),
                    actor_account_id=actor_id,
                    organization_id=organization_id,
                    occurred_at=start,
                )
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                occurred_at=start,
            )
        with self.Session() as session:
            scoped = query_usage_metrics(
                session,
                start=start,
                end=end,
                organization_id=first_org,
            )
            global_metrics = query_usage_metrics(session, start=start, end=end)
        self.assertEqual(scoped.successful_workbook_downloads, 1)
        self.assertEqual(scoped.unique_authenticated_users, 1)
        self.assertEqual(global_metrics.successful_workbook_downloads, 3)
        self.assertEqual(global_metrics.unique_authenticated_users, 2)

    def test_actor_bearing_download_does_not_count_as_authenticated_user(self):
        from roster_scout.usage import (
            query_usage_metrics,
            record_authenticated_report_request_accepted,
            record_workbook_download_served,
        )

        organization_id = self._organization("Download Actor Org")
        actor_id = uuid.uuid4()
        start = datetime(2026, 2, 2, tzinfo=UTC)
        end = start + timedelta(days=1)
        with self.Session.begin() as session:
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                actor_account_id=actor_id,
                organization_id=organization_id,
                occurred_at=start,
            )
        with self.Session() as session:
            download_only = query_usage_metrics(session, start=start, end=end)
        self.assertEqual(download_only.successful_workbook_downloads, 1)
        self.assertEqual(download_only.unique_authenticated_users, 0)

        with self.Session.begin() as session:
            record_authenticated_report_request_accepted(
                session,
                event_key=str(uuid.uuid4()),
                actor_account_id=actor_id,
                organization_id=organization_id,
                occurred_at=start + timedelta(hours=1),
            )
        with self.Session() as session:
            after_authenticated_use = query_usage_metrics(
                session,
                start=start,
                end=end,
            )
        self.assertEqual(after_authenticated_use.successful_workbook_downloads, 1)
        self.assertEqual(after_authenticated_use.unique_authenticated_users, 1)

    def test_organization_scoped_unique_users_require_authenticated_use(self):
        from roster_scout.usage import (
            query_usage_metrics,
            record_authenticated_report_request_accepted,
            record_workbook_download_served,
        )

        first_org = self._organization("Scoped Auth First")
        second_org = self._organization("Scoped Auth Second")
        first_actor = uuid.uuid4()
        second_actor = uuid.uuid4()
        download_only_actor = uuid.uuid4()
        start = datetime(2026, 2, 3, tzinfo=UTC)
        end = start + timedelta(days=1)
        with self.Session.begin() as session:
            for actor_id, organization_id in (
                (first_actor, first_org),
                (download_only_actor, first_org),
                (second_actor, second_org),
            ):
                record_workbook_download_served(
                    session,
                    event_key=str(uuid.uuid4()),
                    actor_account_id=actor_id,
                    organization_id=organization_id,
                    occurred_at=start,
                )
            for _index in range(2):
                record_authenticated_report_request_accepted(
                    session,
                    event_key=str(uuid.uuid4()),
                    actor_account_id=first_actor,
                    organization_id=first_org,
                    occurred_at=start + timedelta(hours=1),
                )
            record_authenticated_report_request_accepted(
                session,
                event_key=str(uuid.uuid4()),
                actor_account_id=second_actor,
                organization_id=second_org,
                occurred_at=start + timedelta(hours=1),
            )

        with self.Session() as session:
            first_metrics = query_usage_metrics(
                session,
                start=start,
                end=end,
                organization_id=first_org,
            )
            global_metrics = query_usage_metrics(session, start=start, end=end)
        self.assertEqual(first_metrics.successful_workbook_downloads, 2)
        self.assertEqual(first_metrics.unique_authenticated_users, 1)
        self.assertEqual(global_metrics.successful_workbook_downloads, 3)
        self.assertEqual(global_metrics.unique_authenticated_users, 2)

    def test_null_actor_never_counts_as_unique_user(self):
        from roster_scout.usage import query_usage_metrics, record_workbook_download_served

        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = start + timedelta(days=1)
        with self.Session.begin() as session:
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                occurred_at=start,
            )
        with self.Session() as session:
            metrics = query_usage_metrics(session, start=start, end=end)
        self.assertEqual(metrics.successful_workbook_downloads, 1)
        self.assertEqual(metrics.unique_authenticated_users, 0)

    def test_invalid_inputs_and_nonqualifying_actions_have_no_event_path(self):
        import roster_scout.usage as usage

        self.assertFalse(hasattr(usage, "record_page_view"))
        self.assertFalse(hasattr(usage, "record_login"))
        self.assertFalse(hasattr(usage, "record_rejected_request"))
        with self.Session.begin() as session:
            with self.assertRaises(ValueError):
                usage.record_usage_event(
                    session,
                    event_type="page_view",
                    event_key=str(uuid.uuid4()),
                )
            with self.assertRaises(ValueError):
                usage.record_authenticated_report_request_accepted(
                    session,
                    event_key=str(uuid.uuid4()),
                    actor_account_id=None,
                    organization_id=None,
                )
            with self.assertRaises(ValueError):
                usage.query_usage_metrics(
                    session,
                    start=datetime(2026, 1, 2, tzinfo=UTC),
                    end=datetime(2026, 1, 1, tzinfo=UTC),
                )


class UsageReportCliTests(unittest.TestCase):
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

    def _run(self, arguments, *, environ=None):
        from scripts import report_usage

        stdout = StringIO()
        stderr = StringIO()
        status = report_usage.main(
            arguments,
            environ={
                "DATABASE_URL": self.database_url,
                **(environ or {}),
            },
            stdout=stdout,
            stderr=stderr,
        )
        return status, stdout.getvalue(), stderr.getvalue()

    def test_global_and_tenant_reports_return_only_aggregate_contract(self):
        from roster_scout.models import Organization
        from roster_scout.usage import (
            record_authenticated_report_request_accepted,
            record_workbook_download_served,
        )

        start = datetime(2026, 4, 1, tzinfo=UTC)
        end = start + timedelta(days=1)
        with self.Session.begin() as session:
            organization = Organization(name="CLI Org")
            session.add(organization)
            session.flush()
            organization_id = organization.id
            actor_id = uuid.uuid4()
            record_authenticated_report_request_accepted(
                session,
                event_key=str(uuid.uuid4()),
                actor_account_id=actor_id,
                organization_id=organization_id,
                occurred_at=start,
            )
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                actor_account_id=actor_id,
                organization_id=organization_id,
                occurred_at=start,
            )
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                occurred_at=start,
            )
        arguments = [
            "--from",
            "2026-04-01T00:00:00Z",
            "--to",
            "2026-04-02T00:00:00Z",
        ]
        status, stdout, stderr = self._run(arguments)
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            {
                "window_start": "2026-04-01T00:00:00Z",
                "window_end": "2026-04-02T00:00:00Z",
                "unique_authenticated_users": 1,
                "successful_workbook_downloads": 2,
            },
        )
        status, stdout, stderr = self._run(
            arguments + ["--organization-id", str(organization_id)]
        )
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            {
                "window_start": "2026-04-01T00:00:00Z",
                "window_end": "2026-04-02T00:00:00Z",
                "organization_id": str(organization_id),
                "unique_authenticated_users": 1,
                "successful_workbook_downloads": 1,
            },
        )

    def test_pre_auth_downloads_report_zero_unique_users(self):
        from roster_scout.usage import record_workbook_download_served

        start = datetime(2026, 5, 1, tzinfo=UTC)
        with self.Session.begin() as session:
            record_workbook_download_served(
                session,
                event_key=str(uuid.uuid4()),
                occurred_at=start,
            )
        status, stdout, stderr = self._run(
            [
                "--from",
                "2026-05-01T00:00:00Z",
                "--to",
                "2026-05-02T00:00:00Z",
            ]
        )
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["successful_workbook_downloads"], 1)
        self.assertEqual(payload["unique_authenticated_users"], 0)

    def test_invalid_arguments_and_runtime_errors_are_sanitized(self):
        status, stdout, stderr = self._run(
            ["--from", "email@example.com", "--to", "not-a-time"]
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "usage report invalid arguments\n")

        from scripts import report_usage

        with patch.object(
            report_usage,
            "_runtime_dependencies",
            side_effect=RuntimeError(
                "postgresql://private-user:secret@private-host/customer"
            ),
        ):
            status, stdout, stderr = self._run(
                [
                    "--from",
                    "2026-05-01T00:00:00Z",
                    "--to",
                    "2026-05-02T00:00:00Z",
                ]
            )
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "usage report unavailable\n")
        self.assertNotIn("private-user", stderr)
        self.assertNotIn("secret", stderr)

    def test_help_defines_phase_two_contract_and_forbids_fallback_ids(self):
        from scripts.report_usage import build_parser

        help_text = " ".join(build_parser().format_help().split())
        for required in (
            "authorized report/job request is accepted",
            "accepted ReportJob UUID",
            "canonical account and organization UUIDs",
            "page views do not qualify",
            "Email, IP, cookie, user-agent",
            "pseudonymous IDs are forbidden",
        ):
            self.assertIn(required, help_text)


if __name__ == "__main__":
    unittest.main()
