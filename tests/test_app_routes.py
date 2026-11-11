import base64
import io
import json
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

    def test_template_uses_retry_safe_download_state_machine(self):
        module = self.source.index("static', filename='download_tracking.js")
        safe_storage = self.source.index("createSafeStorage", module)
        classifier = self.source.index("classifyAcknowledgementResponse", module)
        tracker = self.source.index("createDownloadTracker", module)
        submission = self.source.index("runDownloadSubmission", tracker)
        request_field = self.source.index("download_event_id: downloadEventId")
        acknowledgement = self.source.index("/api/downloads/ack")

        self.assertLess(module, safe_storage)
        self.assertLess(safe_storage, classifier)
        self.assertLess(safe_storage, tracker)
        self.assertLess(tracker, submission)
        self.assertLess(submission, request_field)
        self.assertGreater(acknowledgement, module)
        self.assertIn("window.sessionStorage", self.source)
        self.assertIn("createSafeStorage", self.source)
        self.assertIn("try {", self.source)
        self.assertNotIn("storage: sessionStorage", self.source)
        self.assertNotIn("let downloadEventId = null;", self.source)
        self.assertNotIn("downloadEventId = crypto.randomUUID();", self.source)
        self.assertNotIn("downloadEventId = null;", self.source)

    def test_template_classifies_acknowledgement_status_and_definitive_retry_copy(self):
        self.assertIn(
            "classifyAcknowledgementResponse(response.status",
            self.source,
        )
        self.assertIn("definitiveAcknowledgementFailure", self.source)
        self.assertIn(
            "Your previous download confirmation was rejected. Please submit again.",
            self.source,
        )
        self.assertNotIn("if (!response.ok) return { acknowledged: false };", self.source)

    def test_success_copy_claims_start_not_filesystem_save(self):
        self.assertIn("Your Stat Sheet download has started.", self.source)
        self.assertNotIn("Your Stat Sheet has downloaded.", self.source)
        self.assertNotRegex(
            self.source,
            r"(?i)(?:download|stat sheet).{0,30}(?:saved|save complete)",
        )

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
        self.assertNotIn("navigator.sendBeacon", self.source)


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
            "ROSTER_SCOUT_PUBLIC_ORIGIN",
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
            ({
                "FLY_APP_NAME": "roster-scout",
                "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://roster-scout.fly.dev",
            }, "missing"),
            ({
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(31),
            }, "short local configured secret"),
            ({
                "WEB_CONCURRENCY": "2",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(31),
                "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://reports.example.test",
            }, "short"),
            ({
                "FLY_APP_NAME": "roster-scout",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": "not+urlsafe!",
                "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://roster-scout.fly.dev",
            }, "malformed"),
            ({
                "FLY_APP_NAME": "roster-scout",
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": "+" * 44,
                "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://roster-scout.fly.dev",
            }, "standard alphabet"),
            ({"WEB_CONCURRENCY": "0"}, "non-positive"),
            ({"WEB_CONCURRENCY": "many"}, "non-integer"),
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
                "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://roster-scout.fly.dev",
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
                "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://reports.example.test",
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
                "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://reports.example.test",
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
                "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://reports.example.test",
                "ACK_EVENT_ID": event_id,
                "ACK_PROOF": proof,
            },
        )
        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        self.assertEqual(rejected.stdout.strip(), "False")


class DownloadAcknowledgementPublicOriginTests(unittest.TestCase):
    @staticmethod
    def _secret():
        return base64.urlsafe_b64encode(b"o" * 32).decode().rstrip("=")

    def _run_app_process(self, code="import app; print('ready')", *, updates=None):
        environment = dict(os.environ)
        for name in (
            "FLY_APP_NAME",
            "WEB_CONCURRENCY",
            "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET",
            "ROSTER_SCOUT_PUBLIC_ORIGIN",
            "BROWSER_ORIGIN",
            "REJECTED_ORIGIN",
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

    def test_shared_runtime_requires_an_absolute_bare_public_origin(self):
        invalid_origins = (
            None,
            "reports.example.test",
            "https://user:secret@reports.example.test",
            "https://reports.example.test/path",
            "https://reports.example.test?preview=1",
            "https://reports.example.test#fragment",
            "https://reports.example.test:not-a-port",
        )
        runtimes = (
            {"FLY_APP_NAME": "roster-scout"},
            {"WEB_CONCURRENCY": "2"},
        )
        for runtime in runtimes:
            for origin in invalid_origins:
                environment = {
                    **runtime,
                    "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(),
                }
                if origin is not None:
                    environment["ROSTER_SCOUT_PUBLIC_ORIGIN"] = origin
                with self.subTest(runtime=runtime, origin=origin):
                    completed = self._run_app_process(updates=environment)
                    self.assertNotEqual(completed.returncode, 0)

    def test_fly_requires_https_but_multiworker_accepts_valid_http_or_port(self):
        fly_http = self._run_app_process(updates={
            "FLY_APP_NAME": "roster-scout",
            "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(),
            "ROSTER_SCOUT_PUBLIC_ORIGIN": "http://roster-scout.fly.dev",
        })
        self.assertNotEqual(fly_http.returncode, 0)

        for origin in (
            "http://reports.internal.test",
            "https://reports.example.test:8443/",
        ):
            with self.subTest(origin=origin):
                completed = self._run_app_process(updates={
                    "WEB_CONCURRENCY": "2",
                    "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(),
                    "ROSTER_SCOUT_PUBLIC_ORIGIN": origin,
                })
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_shared_runtime_matches_browser_origin_default_port_rules(self):
        route_code = """
import os
import uuid
import app
app._record_workbook_download = lambda event_id: 'recorded'
client = app.app.test_client()
def status(origin):
    event_id = str(uuid.uuid4())
    proof = app._issue_download_ack_proof(event_id)
    response = client.post(
        '/api/downloads/ack',
        json={
            'download_event_id': event_id,
            'acknowledgement_proof': proof,
        },
        headers={'Origin': origin, 'Sec-Fetch-Site': 'same-origin'},
    )
    return response.status_code
print(status(os.environ['BROWSER_ORIGIN']))
print(status(os.environ['REJECTED_ORIGIN']))
"""
        cases = (
            (
                {"FLY_APP_NAME": "roster-scout"},
                "https://reports.example.test:443/",
                "https://reports.example.test",
                "https://reports.example.test:443",
            ),
            (
                {"WEB_CONCURRENCY": "2"},
                "http://reports.internal.test:80/",
                "http://reports.internal.test",
                "http://reports.internal.test:80",
            ),
            (
                {"WEB_CONCURRENCY": "2"},
                "https://reports.example.test:8443/",
                "https://reports.example.test:8443",
                "https://reports.example.test",
            ),
        )
        for runtime, configured, browser, rejected in cases:
            environment = {
                **runtime,
                "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(),
                "ROSTER_SCOUT_PUBLIC_ORIGIN": configured,
            }
            with self.subTest(configured=configured, check="startup"):
                completed = self._run_app_process(
                    "import app; print(app._DOWNLOAD_ACK_PUBLIC_ORIGIN)",
                    updates=environment,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout.strip(), browser)
            with self.subTest(configured=configured, check="route"):
                completed = self._run_app_process(
                    route_code,
                    updates={
                        **environment,
                        "BROWSER_ORIGIN": browser,
                        "REJECTED_ORIGIN": rejected,
                    },
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout.splitlines(), ["200", "403"])

    def test_non_fly_single_process_preserves_request_origin_fallback(self):
        code = """
import uuid
import app
app._record_workbook_download = lambda event_id: 'recorded'
event_id = str(uuid.uuid4())
proof = app._issue_download_ack_proof(event_id)
response = app.app.test_client().post(
    '/api/downloads/ack',
    json={'download_event_id': event_id, 'acknowledgement_proof': proof},
    headers={'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'},
)
print(response.status_code)
"""
        completed = self._run_app_process(code)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "200")

    def test_fly_uses_public_https_origin_for_internal_http_requests(self):
        code = """
import uuid
import app
app._record_workbook_download = lambda event_id: 'recorded'
client = app.app.test_client()
def status(origin, headers=None):
    event_id = str(uuid.uuid4())
    proof = app._issue_download_ack_proof(event_id)
    response = client.post(
        '/api/downloads/ack',
        base_url='http://internal-roster-scout:5000',
        json={'download_event_id': event_id, 'acknowledgement_proof': proof},
        headers={
            'Origin': origin,
            'Sec-Fetch-Site': 'same-origin',
            **(headers or {}),
        },
    )
    return response.status_code
print(status('https://roster-scout.fly.dev', {
    'Host': 'attacker.example',
    'X-Forwarded-Host': 'attacker.example',
    'X-Forwarded-Proto': 'http',
}))
print(status('http://internal-roster-scout:5000'))
print(status('https://attacker.example', {
    'Host': 'roster-scout.fly.dev',
    'X-Forwarded-Host': 'roster-scout.fly.dev',
    'X-Forwarded-Proto': 'https',
}))
"""
        completed = self._run_app_process(code, updates={
            "FLY_APP_NAME": "roster-scout",
            "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET": self._secret(),
            "ROSTER_SCOUT_PUBLIC_ORIGIN": "https://roster-scout.fly.dev",
        })
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines(), ["200", "403", "403"])


class WorkbookDownloadAcknowledgementTests(unittest.TestCase):
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
        app_module._usage_session_factory.cache_clear()
        self.addCleanup(app_module._usage_session_factory.cache_clear)

    def _events(self):
        with self.Session() as session:
            return list(session.scalars(select(ProductUsageEvent)))

    def _proof(self, event_id, *, issued_at=None):
        if issued_at is None:
            return app_module._issue_download_ack_proof(event_id)
        with patch("itsdangerous.timed.time.time", return_value=issued_at):
            return app_module._issue_download_ack_proof(event_id)

    def _ack(self, event_id, proof, *, origin="http://localhost", **kwargs):
        headers = {
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
            **kwargs.pop("headers", {}),
        }
        return self.client.post(
            "/api/downloads/ack",
            json={
                "download_event_id": event_id,
                "acknowledgement_proof": proof,
            },
            headers=headers,
            **kwargs,
        )

    def test_valid_ack_and_lost_response_retry_record_one_event(self):
        event_id = str(uuid.uuid4())
        proof = self._proof(event_id)

        first_response = self._ack(event_id, proof)
        retry_response = self._ack(event_id, proof)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(
            first_response.get_json(),
            {"acknowledged": True, "persisted": True},
        )
        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(retry_response.get_json(), first_response.get_json())
        events = self._events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_key, event_id)
        self.assertIsNone(events[0].actor_account_id)
        self.assertIsNone(events[0].organization_id)

    def test_cross_origin_malformed_and_forged_acknowledgements_are_rejected(self):
        event_id = str(uuid.uuid4())
        proof = self._proof(event_id)
        other_id = str(uuid.uuid4())
        cases = (
            ("missing origin", {}, {
                "download_event_id": event_id,
                "acknowledgement_proof": proof,
            }),
            ("cross origin", {"Origin": "https://evil.test"}, {
                "download_event_id": event_id,
                "acknowledgement_proof": proof,
            }),
            ("cross site", {
                "Origin": "http://localhost",
                "Sec-Fetch-Site": "cross-site",
            }, {
                "download_event_id": event_id,
                "acknowledgement_proof": proof,
            }),
            ("malformed uuid", {"Origin": "http://localhost"}, {
                "download_event_id": "not-a-uuid",
                "acknowledgement_proof": proof,
            }),
            ("mismatched uuid", {"Origin": "http://localhost"}, {
                "download_event_id": other_id,
                "acknowledgement_proof": proof,
            }),
            ("tampered proof", {"Origin": "http://localhost"}, {
                "download_event_id": event_id,
                "acknowledgement_proof": proof + "x",
            }),
            ("extra field", {"Origin": "http://localhost"}, {
                "download_event_id": event_id,
                "acknowledgement_proof": proof,
                "actor": "attacker@example.test",
            }),
        )
        for label, headers, payload in cases:
            with self.subTest(label=label):
                response = self.client.post(
                    "/api/downloads/ack",
                    json=payload,
                    headers=headers,
                )
                self.assertIn(response.status_code, (400, 403))
        self.assertEqual(self._events(), [])

    def test_acknowledgement_body_and_proof_are_bounded(self):
        event_id = str(uuid.uuid4())
        oversized_body = json.dumps({
            "download_event_id": event_id,
            "acknowledgement_proof": "x" * 1100,
        })
        response = self.client.post(
            "/api/downloads/ack",
            data=oversized_body,
            content_type="application/json",
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 413)

        response = self._ack(event_id, "x" * 513)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._events(), [])

    def test_proof_expiry_boundary_is_exactly_15_minutes(self):
        issued_at = 2_000_000_000
        for age, expected_status in ((899, 200), (900, 200), (901, 400)):
            event_id = str(uuid.uuid4())
            proof = self._proof(event_id, issued_at=issued_at)
            with self.subTest(age=age), patch(
                "itsdangerous.timed.time.time",
                return_value=issued_at + age,
            ):
                response = self._ack(event_id, proof)
            self.assertEqual(response.status_code, expected_status)
        self.assertEqual(len(self._events()), 2)

    def test_native_no_database_ack_is_accepted_with_sanitized_drop(self):
        environment = dict(os.environ)
        for name in ("DATABASE_URL", "FLY_APP_NAME", "WEB_CONCURRENCY"):
            environment.pop(name, None)
        environment["ROSTER_SCOUT_DOWNLOAD_ACK_SECRET"] = (
            base64.urlsafe_b64encode(b"n" * 32).decode().rstrip("=")
        )
        code = """
import json, uuid
import app
event_id = str(uuid.uuid4())
proof = app._issue_download_ack_proof(event_id)
response = app.app.test_client().post(
    '/api/downloads/ack',
    json={'download_event_id': event_id, 'acknowledgement_proof': proof},
    headers={'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'},
)
print(response.status_code)
print(json.dumps(response.get_json(), sort_keys=True))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.splitlines(),
            ["202", '{"acknowledged": true, "persisted": false}'],
        )
        self.assertIn(
            "usage_event_dropped route=download_ack reason=database_unavailable",
            completed.stderr,
        )
        for sensitive_value in ("postgresql", "Traceback", "ACK_PROOF"):
            self.assertNotIn(sensitive_value, completed.stderr)


class NativeWorkbookDatabaseOptionalTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        app_module._usage_session_factory.cache_clear()
        self.addCleanup(app_module._usage_session_factory.cache_clear)

    def test_native_workbook_succeeds_without_persisting_before_ack(self):
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
            ), patch.object(
                app_module,
                "scrape_ncaa_by_id",
                return_value=([SAMPLE_PLAYER], sensitive_team),
            ), patch.object(app_module.app.logger, "warning") as warning:
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
            response.headers["X-Roster-Scout-Download-Event-ID"],
            event_key,
        )
        self.assertTrue(response.headers["X-Roster-Scout-Download-Ack-Proof"])
        warning.assert_not_called()


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
