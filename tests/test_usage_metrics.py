"""PostgreSQL coverage for privacy-conscious product usage metrics."""

import unittest
import uuid
from datetime import UTC, datetime

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


if __name__ == "__main__":
    unittest.main()
