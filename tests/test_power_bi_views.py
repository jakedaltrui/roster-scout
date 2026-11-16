from __future__ import annotations

import unittest
import uuid

from sqlalchemy import create_engine, inspect, text

from tests.db_support import require_test_database_url, upgrade_database


class PowerBIAnalyticsViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        upgrade_database(cls.database_url)
        cls.engine = create_engine(cls.database_url)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_every_view_repeats_exact_snapshot_security_key(self):
        from importlib import import_module

        migration = import_module(
            "migrations.versions.0026_power_bi_analytics_views"
        )
        inspector = inspect(self.engine)
        names = set(inspector.get_view_names())
        self.assertEqual(set(migration.VIEW_NAMES), names & set(migration.VIEW_NAMES))
        for name in migration.VIEW_NAMES:
            with self.subTest(view=name):
                columns = {column["name"] for column in inspector.get_columns(name)}
                self.assertTrue(
                    {"organization_id", "snapshot_id", "security_key"}.issubset(columns)
                )
                definition = inspector.get_view_definition(name).lower()
                self.assertIn("organization_id", definition)
                self.assertIn("snapshot_id", definition)
                self.assertIn("security_key", definition)

    def test_snapshot_view_exposes_only_published_ready_or_partial_rows(self):
        definition = inspect(self.engine).get_view_definition("pbi_snapshots").lower()
        self.assertIn("published_at is not null", definition)
        self.assertIn("'ready'", definition)
        self.assertIn("'partial'", definition)
        self.assertNotIn("raw_values", definition)

    def test_scoped_views_expose_typed_inputs_for_advanced_metrics(self):
        inspector = inspect(self.engine)
        batting_columns = {
            column["name"]
            for column in inspector.get_columns("pbi_scoped_batting")
        }
        pitching_columns = {
            column["name"]
            for column in inspector.get_columns("pbi_scoped_pitching")
        }
        self.assertTrue({
            "plate_appearances", "walks", "strikeouts", "total_bases",
        }.issubset(batting_columns))
        self.assertTrue({
            "innings_outs", "earned_runs", "strikeouts", "batters_faced",
        }.issubset(pitching_columns))

    def test_security_key_matches_embed_effective_identity(self):
        from roster_scout.power_bi import snapshot_security_key

        organization_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        self.assertEqual(
            snapshot_security_key(organization_id, snapshot_id),
            f"{organization_id}|{snapshot_id}",
        )

    def test_power_bi_views_are_not_granted_to_public(self):
        with self.engine.connect() as connection:
            granted = connection.execute(text("""
                SELECT table_name, privilege_type
                FROM information_schema.role_table_grants
                WHERE grantee = 'PUBLIC' AND table_name LIKE 'pbi_%'
            """)).all()
        self.assertEqual(granted, [])

    def test_dashboard_audit_contract_is_database_enforced(self):
        from roster_scout.audit import AUDIT_ACTIONS, AUDIT_TARGET_TYPES

        self.assertIn("dashboard.embed_token", AUDIT_ACTIONS)
        self.assertIn("dashboard", AUDIT_TARGET_TYPES)
        with self.engine.connect() as connection:
            constraints = dict(connection.execute(text("""
                SELECT conname, pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname IN (
                  'ck_security_audit_events_action',
                  'ck_security_audit_events_target_type'
                )
            """)).all())
        self.assertIn("dashboard.embed_token", constraints["ck_security_audit_events_action"])
        self.assertIn("dashboard", constraints["ck_security_audit_events_target_type"])


if __name__ == "__main__":
    unittest.main()
