import io
import os
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
from PIL import Image

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
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        env = patch.dict(os.environ, {"ROSTER_SCOUT_CACHE_DIR": self.temp_dir.name})
        env.start()
        self.addCleanup(env.stop)
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
        self.assertIn(b"Download Roster Stat Sheet", response.data)
        self.assertIn(b'id="team-combobox"', response.data)
        self.assertIn(b'role="listbox"', response.data)
        self.assertNotIn(b"email-input", response.data)
        self.assertNotIn(b"Google Sheets", response.data)
        self.assertNotIn(b"JUCO", response.data)
        self.assertNotIn(b"NJCAA", response.data)
        self.assertNotIn(b'href="/demo"', response.data)
        self.assertNotIn(b"20-second product demo", response.data)

    def test_demo_page_is_unlisted_and_serves_seekable_video(self):
        response = self.client.get("/demo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn(b"20-second product demo", response.data)
        self.assertIn(b"/static/demo/roster-scout-demo.mp4", response.data)

        video = self.client.get(
            "/static/demo/roster-scout-demo.mp4",
            headers={"Range": "bytes=0-99"},
        )
        self.addCleanup(video.close)
        self.assertEqual(video.status_code, 206)
        self.assertEqual(len(video.data), 100)

    def test_workbook_cells_are_left_aligned_including_numeric_values(self):
        workbook = app_module._create_workbook([SAMPLE_PLAYER], [SAMPLE_PLAYER])
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(workbook) as archive:
            styles = ET.fromstring(archive.read("xl/styles.xml"))
            formats = styles.find("s:cellXfs", ns)
            for sheet in ("sheet1.xml", "sheet2.xml"):
                root = ET.fromstring(archive.read(f"xl/worksheets/{sheet}"))
                cells = root.findall(".//s:sheetData/s:row/s:c", ns)
                self.assertTrue(cells)
                for cell in cells:
                    fmt = formats[int(cell.get("s", "0"))]
                    alignment = fmt.find("s:alignment", ns)
                    self.assertIsNotNone(alignment, cell.get("r"))
                    self.assertEqual(alignment.get("horizontal"), "left", cell.get("r"))

    def test_ncaa_workbook_includes_school_branding_and_logo(self):
        logo = Image.new("RGBA", (40, 40), (0, 77, 46, 255))
        for x in range(20, 40):
            for y in range(40):
                logo.putpixel((x, y), (244, 185, 33, 255))
        logo_bytes = io.BytesIO()
        logo.save(logo_bytes, format="PNG")
        branding = {
            "school_name": "Belhaven",
            "generated_on": app_module.date(2026, 10, 30),
            "logo_png": logo_bytes.getvalue(),
            "palette": app_module._logo_palette(logo_bytes.getvalue()),
        }

        workbook = app_module._create_workbook([SAMPLE_PLAYER], [SAMPLE_PLAYER], branding=branding)
        with zipfile.ZipFile(workbook) as archive:
            hitters_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            drawing_xml = archive.read("xl/drawings/drawing1.xml").decode("utf-8")
            shared_strings_xml = archive.read("xl/sharedStrings.xml").decode("utf-8")
            table_xml = archive.read("xl/tables/table1.xml").decode("utf-8")
            self.assertIn("Belhaven", shared_strings_xml)
            self.assertIn("OCTOBER 30, 2026", shared_strings_xml)
            self.assertNotIn("D III", shared_strings_xml)
            self.assertNotIn("Hitters, 1 players", shared_strings_xml)
            self.assertIn("tabColor", hitters_xml)
            self.assertIn('ySplit="3"', hitters_xml)
            self.assertIn('mergeCell ref="A1:A2"', hitters_xml)
            self.assertIn('mergeCell ref="B1:D1"', hitters_xml)
            self.assertIn('mergeCell ref="B2:D2"', hitters_xml)
            self.assertIn('ref="A3:P4"', table_xml)
            self.assertIn("<xdr:col>0</xdr:col>", drawing_xml)
            self.assertIn("<xdr:row>0</xdr:row>", drawing_xml)
            self.assertEqual(drawing_xml.count("<xdr:twoCellAnchor"), 1)
            self.assertTrue(any(name.startswith("xl/media/") for name in archive.namelist()))

    def test_ncaa_branding_uses_the_site_display_name(self):
        branding = app_module._ncaa_workbook_branding(
            {"display_name": "Mississippi State University"},
            "Mississippi St.",
        )
        self.assertEqual(branding["school_name"], "Mississippi State University")
        self.assertNotIn("division", branding)

    def test_long_school_names_expand_the_header_lockup_without_shrinking_text(self):
        widths = [20, 7, 10, 10, 7, 7, 7, 7]
        last_col = app_module._lockup_last_column(
            widths,
            "Rose-Hulman Institute of Technology",
            "OCTOBER 30, 2026",
        )
        available_width = sum(
            app_module._column_width_pixels(width)
            for width in widths[1:last_col + 1]
        ) - 24
        required_title_width = round(
            app_module.xl_pixel_width("Rose-Hulman Institute of Technology") * 1.6
        )
        self.assertGreater(last_col, 3)
        self.assertGreaterEqual(available_width, required_title_width)

    def test_health_check_does_not_touch_external_services(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_responses_include_baseline_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_team_catalog_endpoint_returns_exact_selector_ids(self):
        catalog = [{
            "name": "Tampa", "team_id": "689", "season_team_id": "615087",
            "division": "d2", "year": 2026,
        }]
        with patch.object(app_module, "list_ncaa_teams", return_value=catalog):
            response = self.client.get("/api/teams?division=d2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["teams"], catalog)

    def test_team_logo_removes_outer_white_but_keeps_white_inside_mark(self):
        image = Image.new("RGB", (7, 7), "white")
        for x in range(1, 6):
            for y in range(1, 6):
                image.putpixel((x, y), (0, 0, 0))
        image.putpixel((3, 3), (255, 255, 255))
        source = io.BytesIO()
        image.save(source, format="GIF")
        response = MagicMock()
        response.iter_content.return_value = [source.getvalue()]
        app_module._ncaa_logo_png.cache_clear()
        self.addCleanup(app_module._ncaa_logo_png.cache_clear)
        with patch.object(app_module.requests, "get", return_value=response):
            result = self.client.get("/api/team-logo/234")
        self.assertEqual(result.status_code, 200)
        processed = Image.open(io.BytesIO(result.data)).convert("RGBA")
        self.assertEqual(processed.getpixel((0, 0))[3], 0)
        self.assertEqual(processed.getpixel((3, 3)), (255, 255, 255, 255))

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

    def test_d1_ncaa_failure_is_returned_without_external_fallback(self):
        with patch.object(
            app_module,
            "scrape_ncaa_by_name",
            side_effect=NcaaAccessError("blocked"),
        ):
            response = self.client.post("/scrape", json={
                "team": "Vanderbilt", "division": "d1",
            })
        self.assertEqual(response.status_code, 502)
        self.assertIn("blocked", response.get_json()["error"])

    def test_d3_ncaa_failure_is_returned(self):
        with patch.object(
            app_module,
            "scrape_ncaa_by_name",
            side_effect=NcaaAccessError("verification required"),
        ):
            response = self.client.post("/scrape", json={
                "team": "Scranton", "division": "d3",
            })
        self.assertEqual(response.status_code, 502)
        self.assertIn("verification required", response.get_json()["error"])

    def test_missing_team_is_rejected(self):
        response = self.client.post("/scrape", json={"division": "d2"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("team name", response.get_json()["error"])

    def test_scrape_requires_a_bounded_json_object(self):
        not_json = self.client.post("/scrape", data="team=Tampa")
        self.assertEqual(not_json.status_code, 415)
        array_body = self.client.post("/scrape", json=["Tampa"])
        self.assertEqual(array_body.status_code, 400)
        oversized = self.client.post("/scrape", json={"team": "x" * 17_000})
        self.assertEqual(oversized.status_code, 413)
        long_team = self.client.post("/scrape", json={"team": "x" * 121})
        self.assertEqual(long_team.status_code, 400)
        invalid_id = self.client.post("/scrape", json={
            "team": "Tampa", "division": "d2", "season_team_id": "../../etc",
        })
        self.assertEqual(invalid_id.status_code, 400)

    def test_unexpected_errors_do_not_leak_exception_text(self):
        with self.assertLogs(app_module.app.logger, level="ERROR") as logs, patch.object(
            app_module,
            "scrape_ncaa_by_id",
            side_effect=RuntimeError("secret internal detail"),
        ):
            response = self.client.post("/scrape", json={
                "team": "Tampa", "division": "d2", "season_team_id": "615087",
            })
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret internal detail", response.get_json()["error"])
        self.assertIn("Unexpected workbook generation failure", logs.output[0])

    def test_workbook_treats_formula_and_url_like_values_as_plain_text(self):
        player = {**SAMPLE_PLAYER, "Name": "=1+1", "Position": "https://example.com"}
        workbook = app_module._create_workbook([player], [])
        with zipfile.ZipFile(workbook) as archive:
            sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            shared_strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
        self.assertNotIn("<f>", sheet_xml)
        self.assertNotIn("<hyperlink", sheet_xml)
        self.assertIn("=1+1", shared_strings)
        self.assertIn("https://example.com", shared_strings)

    def test_rate_limit_is_per_client_and_returns_retry_header(self):
        with patch.dict(os.environ, {"SCRAPE_RATE_LIMIT": "1", "SCRAPE_RATE_WINDOW_SECONDS": "60"}), patch.object(
            app_module, "scrape_ncaa_by_id", return_value=([SAMPLE_PLAYER], "Tampa")
        ) as scrape:
            payload = {"team": "Tampa", "division": "d2", "season_team_id": "615087"}
            self.assertEqual(self.client.post("/scrape", json=payload).status_code, 200)
            limited = self.client.post("/scrape", json=payload)
            self.assertEqual(limited.status_code, 429)
            self.assertGreater(int(limited.headers["Retry-After"]), 0)
            self.assertEqual(self.client.post("/scrape", json=payload, environ_base={"REMOTE_ADDR": "192.0.2.2"}).status_code, 200)
            self.assertEqual(scrape.call_count, 2)

    def test_browser_capacity_returns_503(self):
        from usage_limits import BrowserCapacityExceeded
        with patch.object(
            app_module,
            "scrape_ncaa_by_name",
            side_effect=BrowserCapacityExceeded("busy"),
        ):
            response = self.client.post("/scrape", json={"team": "Tampa", "division": "d1"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "30")


if __name__ == "__main__":
    unittest.main()
