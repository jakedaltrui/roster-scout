import unittest
from unittest.mock import patch

import app as app_module
from roster_scraper import NcaaAccessError


SAMPLE_PLAYER = {
    "Name": "Test Player", "#": "1", "Position": "SS", "Class": "Jr",
    "AVG": ".300", "OBP": ".400", "SLG": ".500", "AB": "100", "H": "30",
    "2B": "5", "HR": "3", "RBI": "20", "R": "25", "BB": "10",
    "H_SO": "12", "SB": "4", "ERA": "", "WHIP": "", "W": "", "L": "",
    "SV": "", "IP": "", "P_H": "", "ER": "", "P_BB": "", "P_SO": "",
    "_has_hitting": True, "_has_pitching": False, "_pos": "SS",
}


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def test_d2_request_uses_ncaa_workflow_and_preserves_sheet_output(self):
        with patch.object(app_module, "scrape_ncaa_by_name", return_value=([SAMPLE_PLAYER], "Tampa")) as scrape, patch.object(
            app_module, "_create_and_share", return_value="https://docs.google.com/spreadsheets/d/test"
        ):
            response = self.client.post("/scrape", json={
                "team": "Tampa", "email": "scout@example.com", "division": "d2"
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        scrape.assert_called_once_with("Tampa", division="d2")

    def test_d1_uses_existing_fallback_when_ncaa_is_blocked(self):
        with patch.object(app_module, "scrape_ncaa_by_name", side_effect=NcaaAccessError("blocked")), patch.object(
            app_module, "scrape_by_team_name", return_value=([SAMPLE_PLAYER], "Vanderbilt")
        ) as fallback, patch.object(
            app_module, "_create_and_share", return_value="https://docs.google.com/spreadsheets/d/test"
        ):
            response = self.client.post("/scrape", json={
                "team": "Vanderbilt", "email": "scout@example.com", "division": "d1"
            })
        self.assertEqual(response.status_code, 200)
        fallback.assert_called_once_with("Vanderbilt")

    def test_d3_failure_is_returned_without_d1_fallback(self):
        with patch.object(app_module, "scrape_ncaa_by_name", side_effect=NcaaAccessError("verification required")), patch.object(
            app_module, "scrape_by_team_name"
        ) as fallback:
            response = self.client.post("/scrape", json={
                "team": "Scranton", "email": "scout@example.com", "division": "d3"
            })
        self.assertEqual(response.status_code, 500)
        self.assertIn("verification required", response.get_json()["error"])
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
