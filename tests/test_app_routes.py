import base64
import io
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
from PIL import Image
from sqlalchemy import select

import app as app_module
from roster_scout.evidence import EvidencePurgeReadiness
from roster_scout.models import ProductUsageEvent
from roster_scraper import NcaaAccessError
from tests.db_support import clean_database, require_test_database_url, upgrade_database


SAMPLE_PLAYER = {
    "Name": "Test Player", "#": "1", "Position": "SS", "Class": "Jr",
    "AVG": ".300", "OBP": ".400", "SLG": ".500", "AB": "100", "H": "30",
    "2B": "5", "HR": "3", "RBI": "20", "R": "25", "BB": "10",
    "H_SO": "12", "SB": "4", "ERA": "", "WHIP": "", "W": "", "L": "",
    "SV": "", "IP": "", "P_H": "", "ER": "", "P_BB": "", "P_SO": "",
    "_has_hitting": True, "_has_pitching": False, "_pos": "SS",
}


class DownloadEventContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (
            os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
        )
        with open(cls.template, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_one_uuid_is_reused_for_one_inflight_submission(self):
        guard = self.source.index("if (scrapeInFlight) return;")
        generation = self.source.index("downloadEventId = crypto.randomUUID();")
        request_field = self.source.index("download_event_id: downloadEventId")
        terminal_clear = self.source.index("downloadEventId = null;", request_field)

        self.assertLess(guard, generation)
        self.assertLess(generation, request_field)
        self.assertLess(request_field, terminal_clear)
        self.assertEqual(
            self.source.count("downloadEventId = crypto.randomUUID();"),
            1,
        )
        self.assertIn("let downloadEventId = null;", self.source)

    def test_download_event_id_is_request_only_and_nonidentifying(self):
        self.assertNotRegex(
            self.source,
            r"localStorage\.(?:getItem|setItem)\([^)]*downloadEventId",
        )
        self.assertNotRegex(
            self.source,
            r"document\.cookie[^;\n]*downloadEventId",
        )
        self.assertNotRegex(
            self.source.lower(),
            r"(?:fingerprint|useragent|user_agent)[^;\n]*downloadeventid",
        )
        self.assertNotIn('name="download_event_id"', self.source)


class WorkbookPreparationReceiptTests(unittest.TestCase):
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
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        factory = getattr(app_module, "_usage_session_factory", None)
        if factory is not None and hasattr(factory, "cache_clear"):
            factory.cache_clear()
            self.addCleanup(factory.cache_clear)

    def _download(self, event_key=None):
        payload = {
            "team": "Tampa",
            "division": "d2",
            "season_team_id": "615087",
        }
        if event_key is not None:
            payload["download_event_id"] = event_key
        with patch.object(
            app_module,
            "check_request_limit",
            return_value=None,
        ), patch.object(
            app_module,
            "scrape_ncaa_by_id",
            return_value=([SAMPLE_PLAYER], "Tampa"),
        ):
            return self.client.post("/scrape", json=payload)

    def _events(self):
        with self.Session() as session:
            return list(
                session.scalars(
                    select(ProductUsageEvent).order_by(ProductUsageEvent.occurred_at)
                )
            )

    def assert_excel_download(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("Tampa", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            self.assertIn('name="Hitters"', workbook_xml)
            self.assertIn('name="Pitchers"', workbook_xml)

    def test_successful_workbook_returns_receipt_without_recording_event(self):
        first_key = str(uuid.uuid4())
        second_key = str(uuid.uuid4())
        for response, expected_key in (
            (self._download(first_key), first_key),
            (self._download(first_key), first_key),
            (self._download(second_key), second_key),
        ):
            self.assert_excel_download(response)
            self.assertEqual(
                response.headers["X-Roster-Scout-Download-Event-ID"],
                expected_key,
            )
            self.assertTrue(response.headers["X-Roster-Scout-Download-Ack-Proof"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")

        self.assertEqual(self._events(), [])

    def test_omitted_key_gets_one_request_local_receipt(self):
        response = self._download()
        self.assert_excel_download(response)
        event_key = response.headers["X-Roster-Scout-Download-Event-ID"]
        self.assertEqual(str(uuid.UUID(event_key)), event_key)
        self.assertTrue(response.headers["X-Roster-Scout-Download-Ack-Proof"])
        self.assertEqual(self._events(), [])

    def test_malformed_supplied_key_is_rejected_before_collection(self):
        with patch.object(app_module, "scrape_ncaa_by_id") as scrape:
            response = self.client.post("/scrape", json={
                "team": "Tampa",
                "division": "d2",
                "season_team_id": "615087",
                "download_event_id": "not-a-uuid",
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._events(), [])
        self.assertNotIn("X-Roster-Scout-Download-Event-ID", response.headers)
        self.assertNotIn("X-Roster-Scout-Download-Ack-Proof", response.headers)
        scrape.assert_not_called()

    def test_failed_workbook_has_no_receipt_and_no_event(self):
        with patch.object(
            app_module,
            "check_request_limit",
            return_value=None,
        ), patch.object(
            app_module,
            "scrape_ncaa_by_id",
            side_effect=ValueError("upstream failed"),
        ):
            response = self.client.post("/scrape", json={
                "team": "Tampa",
                "division": "d2",
                "season_team_id": "615087",
                "download_event_id": str(uuid.uuid4()),
            })

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("X-Roster-Scout-Download-Event-ID", response.headers)
        self.assertNotIn("X-Roster-Scout-Download-Ack-Proof", response.headers)
        self.assertEqual(self._events(), [])


class DownloadAcknowledgementSecretConfigurationTests(unittest.TestCase):
    @staticmethod
    def _secret(byte_count, byte=b"s"):
        return base64.urlsafe_b64encode(byte * byte_count).decode().rstrip("=")

    def _run_app_process(self, code, *, updates=None):
        environment = dict(os.environ)
        for name in (
            "FLY_APP_NAME",
            "WEB_CONCURRENCY",
            "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET",
            "ACK_EVENT_ID",
            "ACK_PROOF",
        ):
            environment.pop(name, None)
        environment.update(updates or {})
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_shared_runtime_rejects_missing_short_and_malformed_secrets(self):
        cases = (
            ({"FLY_APP_NAME": "roster-scout"}, "missing"),
            ({
                "WEB_CONCURRENCY": "2",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(31),
            }, "short"),
            ({
                "FLY_APP_NAME": "roster-scout",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": "not+urlsafe!",
            }, "malformed"),
            ({"WEB_CONCURRENCY": "0"}, "non-positive"),
        )
        for environment, label in cases:
            with self.subTest(label=label):
                completed = self._run_app_process("import app", updates=environment)
                self.assertNotEqual(completed.returncode, 0)

    def test_valid_32_byte_shared_secret_starts(self):
        completed = self._run_app_process(
            "import app; print('ready')",
            updates={
                "FLY_APP_NAME": "roster-scout",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(32),
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ready")

    def test_clean_processes_with_same_secret_interoperate(self):
        event_id = str(uuid.uuid4())
        shared_secret = self._secret(32, b"a")
        issued = self._run_app_process(
            "import os, app; print(app._issue_download_ack_proof(os.environ['ACK_EVENT_ID']))",
            updates={
                "WEB_CONCURRENCY": "2",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": shared_secret,
                "ACK_EVENT_ID": event_id,
            },
        )
        self.assertEqual(issued.returncode, 0, issued.stderr)
        proof = issued.stdout.strip()
        verified = self._run_app_process(
            "import os, app; print(app._verify_download_ack_proof(os.environ['ACK_PROOF'], os.environ['ACK_EVENT_ID']))",
            updates={
                "WEB_CONCURRENCY": "2",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": shared_secret,
                "ACK_EVENT_ID": event_id,
                "ACK_PROOF": proof,
            },
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(verified.stdout.strip(), "True")
        rejected = self._run_app_process(
            "import os, app; print(app._verify_download_ack_proof(os.environ['ACK_PROOF'], os.environ['ACK_EVENT_ID']))",
            updates={
                "WEB_CONCURRENCY": "2",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(32, b"b"),
                "ACK_EVENT_ID": event_id,
                "ACK_PROOF": proof,
            },
        )
        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        self.assertEqual(rejected.stdout.strip(), "False")


class NativeWorkbookDatabaseOptionalTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        app_module._usage_session_factory.cache_clear()
        self.addCleanup(app_module._usage_session_factory.cache_clear)

    def test_native_workbook_succeeds_without_database_and_logs_sanitized_drop(self):
        event_key = str(uuid.uuid4())
        sensitive_team = "Sensitive Opponent"
        sensitive_ip = "203.0.113.44"
        environment = dict(os.environ)
        environment.pop("DATABASE_URL", None)
        with tempfile.TemporaryDirectory() as cache_dir:
            environment["ROSTER_SCOUT_CACHE_DIR"] = cache_dir
            with patch.dict(
                os.environ,
                environment,
                clear=True,
            ), self.assertLogs(
                app_module.app.logger,
                level="WARNING",
            ) as logs, patch.object(
                app_module,
                "scrape_ncaa_by_id",
                return_value=([SAMPLE_PLAYER], sensitive_team),
            ):
                response = self.client.post(
                    "/scrape",
                    json={
                        "team": sensitive_team,
                        "division": "d2",
                        "season_team_id": "615087",
                        "download_event_id": event_key,
                    },
                    environ_base={"REMOTE_ADDR": sensitive_ip},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        self.assertIn('name="Hitters"', workbook_xml)
        self.assertIn('name="Pitchers"', workbook_xml)
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            ["usage_event_dropped route=legacy_scrape reason=database_unavailable"],
        )
        rendered_logs = "\n".join(logs.output)
        for sensitive_value in (
            sensitive_team,
            sensitive_ip,
            event_key,
            "DATABASE_URL",
            "required for persistence",
            "Traceback",
        ):
            self.assertNotIn(sensitive_value, rendered_logs)


class AppReadinessTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        factory = getattr(app_module, "_evidence_purge_session_factory", None)
        if factory is not None and hasattr(factory, "cache_clear"):
            factory.cache_clear()
            self.addCleanup(factory.cache_clear)

    @staticmethod
    def _readiness(*, ready, status, expired_bytes_count=0):
        return EvidencePurgeReadiness(
            ready=ready,
            status=status,
            last_success_at=None,
            last_purged_count=0,
            expired_bytes_count=expired_bytes_count,
        )

    def _request_with_readiness(self, readiness):
        session_factory = object()
        with patch.object(
            app_module,
            "get_engine",
            return_value=object(),
            create=True,
        ) as get_engine, patch.object(
            app_module,
            "create_session_factory",
            return_value=session_factory,
            create=True,
        ) as create_factory, patch.object(
            app_module,
            "get_evidence_purge_readiness",
            return_value=readiness,
            create=True,
        ) as get_readiness:
            response = self.client.get("/readyz")
        get_engine.assert_called_once_with()
        create_factory.assert_called_once()
        get_readiness.assert_called_once_with(session_factory)
        return response

    def test_readyz_returns_200_only_for_fresh_persisted_purge_health(self):
        response = self._request_with_readiness(
            self._readiness(ready=True, status="fresh")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"status": "ready", "evidence_purge": "fresh"},
        )

    def test_readyz_fails_closed_before_first_success(self):
        response = self._request_with_readiness(
            self._readiness(ready=False, status="never_succeeded")
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"status": "not_ready", "evidence_purge": "never_succeeded"},
        )

    def test_readyz_fails_closed_after_freshness_expires(self):
        response = self._request_with_readiness(
            self._readiness(ready=False, status="stale")
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"status": "not_ready", "evidence_purge": "stale"},
        )

    def test_readyz_fails_closed_while_expired_bytes_remain(self):
        response = self._request_with_readiness(
            self._readiness(
                ready=False,
                status="expired_bytes_present",
                expired_bytes_count=1,
            )
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"status": "not_ready", "evidence_purge": "expired_bytes_present"},
        )

    def test_readyz_sanitizes_database_and_configuration_failures(self):
        secret = (
            "postgresql://operator:secret@example.test/customer "
            "<html>blocked</html> https://ncaa.test/player?token=secret "
            "checksum-deadbeef"
        )
        with patch.object(
            app_module,
            "get_engine",
            side_effect=RuntimeError(secret),
            create=True,
        ), patch.object(app_module.app, "logger") as logger:
            response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"status": "not_ready", "evidence_purge": "unavailable"},
        )
        self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertNotIn("customer", response.get_data(as_text=True))
        logger.warning.assert_not_called()
        logger.error.assert_not_called()
        logger.exception.assert_not_called()

    def test_readyz_normalizes_database_unavailable_projection(self):
        response = self._request_with_readiness(
            self._readiness(ready=False, status="database_unavailable")
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"status": "not_ready", "evidence_purge": "unavailable"},
        )

    def test_readyz_rejects_invalid_retention_before_purge_dependencies(self):
        invalid_values = ("29", "", "not-an-integer", "0", "-1")
        for value in invalid_values:
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"FAILURE_EVIDENCE_RETENTION_DAYS": value},
            ), patch.object(
                app_module,
                "_evidence_purge_session_factory",
            ) as session_factory:
                response = self.client.get("/readyz")

            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.get_json(),
                {"status": "not_ready", "evidence_purge": "unavailable"},
            )
            session_factory.assert_not_called()
            payload = response.get_data(as_text=True)
            self.assertNotIn(value or "blank", payload)
            self.assertNotIn("configuration", payload)

    def test_readyz_accepts_retention_boundaries(self):
        readiness = self._readiness(ready=True, status="fresh")
        for value in ("1", "28"):
            app_module._evidence_purge_session_factory.cache_clear()
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"FAILURE_EVIDENCE_RETENTION_DAYS": value},
            ):
                response = self._request_with_readiness(readiness)
            self.assertEqual(response.status_code, 200)

    def test_healthz_remains_connection_free_with_invalid_retention(self):
        with patch.dict(
            os.environ,
            {"FAILURE_EVIDENCE_RETENTION_DAYS": "29"},
        ), patch.object(
            app_module,
            "_evidence_purge_session_factory",
        ) as session_factory:
            response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        session_factory.assert_not_called()


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        env = patch.dict(os.environ, {"ROSTER_SCOUT_CACHE_DIR": self.temp_dir.name})
        env.start()
        self.addCleanup(env.stop)
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        usage = patch.object(app_module, "_record_workbook_download")
        usage.start()
        self.addCleanup(usage.stop)

    def assert_excel_download(self, response, team_name):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn(team_name, response.headers["Content-Disposition"])
        self.assertEqual(response.headers["X-Roster-Scout-Players"], "1")
        self.assertEqual(response.headers["X-Roster-Scout-Hitters"], "1")
        self.assertEqual(response.headers["X-Roster-Scout-Pitchers"], "0")
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
        with patch.object(app_module, "get_engine", create=True) as get_engine:
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        get_engine.assert_not_called()

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

    def test_all_ncaa_divisions_share_the_same_failure_contract(self):
        for division in ("d1", "d2", "d3"):
            with self.subTest(division=division), patch.object(
                app_module,
                "scrape_ncaa_by_name",
                side_effect=NcaaAccessError("source unavailable"),
            ):
                response = self.client.post("/scrape", json={
                    "team": "Test Team", "division": division,
                })
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.get_json(), {"error": "source unavailable"})

    def test_phase_one_adds_no_unauthenticated_job_routes(self):
        routes = {rule.rule for rule in app_module.app.url_map.iter_rules()}
        self.assertNotIn("/jobs", routes)
        self.assertFalse(any(route.startswith("/api/jobs") for route in routes))
        self.assertFalse(any("report-job" in route for route in routes))

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
