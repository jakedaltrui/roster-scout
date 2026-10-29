import io
import unittest
import zipfile
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

    def assert_excel_download(self, response, team_name):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn(team_name, response.headers["Content-Disposition"])
        self.assertEqual(response.headers["X-Roster-Scout-Players"], "1")
        self.assertTrue(response.data.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            self.assertIn('name="Hitters"', workbook_xml)
            self.assertIn('name="Pitchers"', workbook_xml)

    def test_home_page_describes_direct_excel_download(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Download Roster Workbook", response.data)
        self.assertIn(b'id="team-combobox"', response.data)
        self.assertIn(b'role="listbox"', response.data)
        self.assertNotIn(b"email-input", response.data)
        self.assertNotIn(b"Google Sheets", response.data)

    def test_health_check_does_not_touch_external_services(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_team_catalog_endpoint_returns_exact_selector_ids(self):
        catalog = [{
            "name": "Tampa", "team_id": "689", "season_team_id": "615087",
            "division": "d2", "year": 2026,
        }]
        with patch.object(app_module, "list_ncaa_teams", return_value=catalog):
            response = self.client.get("/api/teams?division=d2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["teams"], catalog)

    def test_d2_selector_id_downloads_workbook_without_fuzzy_matching(self):
        with patch.object(
            app_module,
            "scrape_ncaa_by_id",
            return_value=([SAMPLE_PLAYER], "Tampa"),
        ) as scrape:
            response = self.client.post("/scrape", json={
                "team": "Tampa", "division": "d2", "season_team_id": "615087",
            })
        self.assert_excel_download(response, "Tampa")
        scrape.assert_called_once_with("615087", division="d2")

    def test_d1_uses_existing_fallback_when_ncaa_is_blocked(self):
        with patch.object(
            app_module,
            "scrape_ncaa_by_name",
            side_effect=NcaaAccessError("blocked"),
        ), patch.object(
            app_module,
            "scrape_by_team_name",
            return_value=([SAMPLE_PLAYER], "Vanderbilt"),
        ) as fallback:
            response = self.client.post("/scrape", json={
                "team": "Vanderbilt", "division": "d1",
            })
        self.assert_excel_download(response, "Vanderbilt")
        fallback.assert_called_once_with("Vanderbilt")

    def test_d3_failure_is_returned_without_d1_fallback(self):
        with patch.object(
            app_module,
            "scrape_ncaa_by_name",
            side_effect=NcaaAccessError("verification required"),
        ), patch.object(app_module, "scrape_by_team_name") as fallback:
            response = self.client.post("/scrape", json={
                "team": "Scranton", "division": "d3",
            })
        self.assertEqual(response.status_code, 500)
        self.assertIn("verification required", response.get_json()["error"])
        fallback.assert_not_called()

    def test_missing_team_is_rejected(self):
        response = self.client.post("/scrape", json={"division": "d2"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("team name", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
