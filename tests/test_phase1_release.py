"""Goal-level release evidence for the complete Phase 1 operator story."""

import unittest

from tests.db_support import clean_database, require_test_database_url, upgrade_database


class Phase1ReleaseTests(unittest.TestCase):
    """Exercise the durable D1/D2/D3 slice through public service boundaries."""

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

    def test_representative_d1_happy_path(self):
        """A reconciled durable D1 job publishes one auditable ready snapshot."""
        release = self._publish_division_via_reconciled_dispatch(1)

        self.assertEqual(release["job_status"], "ready")
        self.assertEqual(release["snapshot_status"], "ready")
        self.assertEqual(release["snapshot_count"], 1)
        self.assertEqual(release["sections"], {"batting", "pitching"})
        self.assertGreater(release["source_count"], 0)
        self.assertEqual(release["source_count"], release["fact_count"])
        self.assertGreater(release["identity_count"], 0)
        self.assertGreater(release["metric_definition_count"], 0)


if __name__ == "__main__":
    unittest.main()
