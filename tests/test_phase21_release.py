"""Deterministic corrective Phase 2.1 customer journey."""

from __future__ import annotations

import re
import unittest
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from sqlalchemy import select

from tests.db_support import clean_database, require_test_database_url, upgrade_database


PLAYER = {
    "Name": "Release Player",
    "#": "7",
    "Position": "SS",
    "Class": "Jr",
    "AVG": ".321",
    "OBP": ".410",
    "SLG": ".550",
    "AB": "100",
    "H": "32",
    "2B": "8",
    "HR": "5",
    "RBI": "24",
    "R": "28",
    "BB": "14",
    "H_SO": "18",
    "SB": "6",
    "ERA": "",
    "WHIP": "",
    "W": "",
    "L": "",
    "SV": "",
    "IP": "",
    "P_H": "",
    "ER": "",
    "P_BB": "",
    "P_SO": "",
    "_has_hitting": True,
    "_has_pitching": False,
    "_pos": "SS",
}

CATALOG = [{
    "name": "State University",
    "display_name": "State University",
    "team_id": "12345",
    "season_team_id": "67890",
    "division": "d1",
    "year": 2026,
}]


class Phase21CustomerJourneyTests(unittest.TestCase):
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
        import app as app_module

        self.app_module = app_module
        self.original_config = dict(app_module.app.config)
        self.original_extensions = {
            name: app_module.app.extensions.get(name)
            for name in (
                "roster_scout_auth_session_factory",
                "roster_scout_report_session_factory",
                "roster_scout_identity_provider",
                "roster_scout_report_dispatch",
            )
        }
        app_module.app.config.update(
            TESTING=True,
            ROSTER_SCOUT_TEST_AUTH_ENFORCEMENT=True,
            WTF_CSRF_ENABLED=True,
            WTF_CSRF_SSL_STRICT=False,
        )
        app_module.app.extensions["roster_scout_auth_session_factory"] = self.Session
        app_module.app.extensions["roster_scout_report_session_factory"] = self.Session

    def tearDown(self):
        self.app_module.app.config.clear()
        self.app_module.app.config.update(self.original_config)
        for name, value in self.original_extensions.items():
            if value is None:
                self.app_module.app.extensions.pop(name, None)
            else:
                self.app_module.app.extensions[name] = value

    def _sign_in(self, email, label, *, return_to="/reports", client=None):
        from roster_scout.identity import FakeIdentityProvider, VerifiedIdentity

        code = f"code-{label}"
        identity = VerifiedIdentity(
            issuer="https://identity.example.test",
            subject=f"subject-{label}",
            provider_user_id=f"provider-{label}",
            email=email,
            email_verified=True,
            linking_approved=False,
            sealed_session=f"sealed-{label}",
        )
        self.app_module.app.extensions["roster_scout_identity_provider"] = (
            FakeIdentityProvider(identities={code: identity})
        )
        client = client or self.app_module.app.test_client()
        started = client.get("/auth/start", query_string={"return_to": return_to})
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        callback = client.get(
            "/auth/callback",
            query_string={"state": state, "code": code},
        )
        return client, callback

    @staticmethod
    def _csrf(html):
        match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        if match is None:
            raise AssertionError("signed-in homepage did not expose a CSRF token")
        return match.group(1)

    def test_public_select_sign_in_resume_download_and_shared_portal(self):
        from roster_scout.models import (
            Organization,
            OrganizationDomain,
            OrganizationMembership,
            ProductUsageEvent,
            Report,
        )

        client = self.app_module.app.test_client()
        homepage = client.get("/")
        self.assertEqual(homepage.status_code, 200)
        self.assertIn(b">Sign in<", homepage.data)
        with patch.object(self.app_module, "list_ncaa_teams", return_value=CATALOG):
            catalog = client.get("/api/teams?division=d1")
        self.assertEqual(catalog.get_json()["teams"], CATALOG)

        event_id = str(uuid.uuid4())
        payload = {
            "team": "State University",
            "division": "d1",
            "season_team_id": "67890",
            "team_id": "12345",
            "display_name": "State University",
            "download_event_id": event_id,
        }
        anonymous = client.post("/scrape", json=payload)
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous.get_json(), {"error": "authentication_required"})

        client, callback = self._sign_in(
            "coach@school.edu",
            "coach",
            return_to="/?resume=download",
            client=client,
        )
        self.assertEqual(callback.headers["Location"], "/?resume=download")
        resumed = client.get(callback.headers["Location"])
        self.assertEqual(resumed.status_code, 200)
        self.assertIn(b">Saved reports<", resumed.data)
        csrf = self._csrf(resumed.get_data(as_text=True))

        with (
            patch(
                "roster_scout.report_routes.list_ncaa_teams",
                return_value=CATALOG,
            ),
            patch.object(self.app_module, "check_request_limit", return_value=0),
            patch.object(
                self.app_module,
                "scrape_ncaa_by_id",
                return_value=([PLAYER], "State University"),
            ),
            patch.object(self.app_module, "_ncaa_workbook_branding", return_value=None),
            patch.object(self.app_module, "drain_report_dispatch_outbox"),
        ):
            workbook = client.post(
                "/scrape",
                json=payload,
                headers={"X-CSRFToken": csrf},
            )
        self.assertEqual(workbook.status_code, 200)
        self.assertTrue(workbook.data.startswith(b"PK"))
        report_url = workbook.headers["X-Roster-Scout-Report-URL"]
        self.assertRegex(report_url, r"^/reports/[0-9a-f-]{36}$")

        acknowledged = client.post(
            "/api/downloads/ack",
            json={
                "download_event_id": workbook.headers[
                    "X-Roster-Scout-Download-Event-ID"
                ],
                "acknowledgement_proof": workbook.headers[
                    "X-Roster-Scout-Download-Ack-Proof"
                ],
            },
            headers={
                "Origin": "http://localhost",
                "Sec-Fetch-Site": "same-origin",
                "X-CSRFToken": csrf,
            },
        )
        self.assertEqual(acknowledged.status_code, 200)

        library = client.get("/reports")
        self.assertIn(b"State University", library.data)
        self.assertIn(b">New report<", library.data)
        self.assertNotIn(b">Members<", library.data)

        colleague, _ = self._sign_in("assistant@athletics.school.edu", "assistant")
        colleague_library = colleague.get("/reports")
        self.assertIn(b"State University", colleague_library.data)

        outsider, _ = self._sign_in("coach@other.edu", "outsider")
        outsider_denial = outsider.get(report_url)
        self.assertEqual(outsider_denial.status_code, 404)
        self.assertNotIn(b"State University", outsider_denial.data)

        with self.Session() as session:
            domains = list(session.scalars(select(OrganizationDomain)))
            organizations = list(session.scalars(select(Organization)))
            memberships = list(session.scalars(select(OrganizationMembership)))
            reports = list(session.scalars(select(Report)))
            usage = list(session.scalars(select(ProductUsageEvent)))
        school_route = next(item for item in domains if item.domain == "school.edu")
        school_members = [
            item for item in memberships
            if item.organization_id == school_route.organization_id
        ]
        self.assertEqual(len(school_members), 2)
        self.assertEqual(len(organizations), 2)
        self.assertEqual(len(reports), 1)
        self.assertEqual(
            {item.event_type for item in usage},
            {"authenticated_use", "workbook_download_served"},
        )


if __name__ == "__main__":
    unittest.main()
