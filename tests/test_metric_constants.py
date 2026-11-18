import unittest

from roster_scout.metric_constants import (
    FIP_VERSION,
    PITCHING_SOURCE_PAYLOAD_SHA256,
    SOURCE_PAYLOAD_SHA256,
    SOURCE_VERSION,
    VERSION,
    fip_constants,
    woba_constants,
)


class WobaConstantSourceTests(unittest.TestCase):
    def test_2026_has_one_documented_set_per_ncaa_division(self):
        rows = [woba_constants(2026, division) for division in ("d1", "2", 3)]

        self.assertTrue(all(row is not None for row in rows))
        self.assertEqual([row.division for row in rows], ["1", "2", "3"])
        self.assertEqual(rows[0].values["walk"], "0.77939")
        self.assertEqual(rows[1].values["home_run"], "1.848397")
        self.assertEqual(rows[2].values["league_woba"], "0.400786")

    def test_source_version_and_complete_csv_checksum_are_pinned(self):
        self.assertEqual(SOURCE_VERSION, "1.4.2")
        self.assertEqual(
            SOURCE_PAYLOAD_SHA256,
            "eb7e4af63cb60455904b6377b361e50f57628b779d52e53ff86fdab26e86e6bb",
        )
        self.assertEqual(VERSION, "ncaa-bbstats-1.4.2-eb7e4af63cb6")
        self.assertEqual(
            PITCHING_SOURCE_PAYLOAD_SHA256,
            "ef3dfcbcd9e7963c6a68740b6965738608fc3d5488bd489bfdc1e0c212e1cc7a",
        )
        self.assertEqual(FIP_VERSION, "ncaa-bbstats-1.4.2-ef3dfcbcd9e7")

    def test_2026_has_one_documented_fip_constant_per_division(self):
        rows = [fip_constants(2026, division) for division in (1, "d2", "3")]

        self.assertEqual([row.values["constant"] for row in rows], [
            "4.2076", "4.7217", "4.8316",
        ])

    def test_unknown_season_or_division_never_fabricates_weights(self):
        self.assertIsNone(woba_constants(2027, "d1"))
        self.assertIsNone(fip_constants(2027, "d1"))
        with self.assertRaises(ValueError):
            woba_constants(2026, "naia")


if __name__ == "__main__":
    unittest.main()
