"""Power BI route isolation and cohesive dashboard UI contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock

from sqlalchemy import select

from tests import test_report_library as _report_library


ROOT = Path(__file__).resolve().parents[1]


class PowerBIEmbedRouteTests(unittest.TestCase):
    setUpClass = _report_library.SharedLibraryTests.__dict__["setUpClass"]
    tearDownClass = _report_library.SharedLibraryTests.__dict__["tearDownClass"]
    _organization = _report_library.SharedLibraryTests._organization
    _client = _report_library.SharedLibraryTests._client
    _report = _report_library.SharedLibraryTests._report
    _snapshot = _report_library.SharedLibraryTests._snapshot

    def setUp(self):
        _report_library.SharedLibraryTests.setUp(self)
        self.original_power_bi = self.app_module.app.extensions.get(
            "roster_scout_power_bi"
        )

    def tearDown(self):
        if self.original_power_bi is None:
            self.app_module.app.extensions.pop("roster_scout_power_bi", None)
        else:
            self.app_module.app.extensions["roster_scout_power_bi"] = (
                self.original_power_bi
            )
        _report_library.SharedLibraryTests.tearDown(self)

    def _provider(self):
        provider = Mock()
        provider.settings = SimpleNamespace(mode="live")
        provider.configured = True
        return provider

    def _completed_diagnostic(self, organization_id, report_id, snapshot_id):
        from roster_scout.models import ExternalRefreshDiagnostic

        with self.Session.begin() as session:
            session.add(ExternalRefreshDiagnostic(
                organization_id=organization_id,
                report_id=report_id,
                snapshot_id=snapshot_id,
                provider="power_bi",
                status="completed",
                attempt_number=1,
                correlation_id=f"route-{uuid.uuid4()}",
                requested_at=self.now,
                started_at=self.now,
                completed_at=self.now,
                updated_at=self.now,
            ))

    def test_embed_token_is_exact_snapshot_bound_and_never_cached(self):
        from roster_scout.power_bi import EmbedConfiguration

        organization_id, accounts = self._organization(
            "Embed University", ("coach@embed.edu", "admin")
        )
        report_id = self._report(organization_id, "Embed Owls", 9501)
        snapshot_id = self._snapshot(
            organization_id, report_id, "ready", self.now
        )
        self._completed_diagnostic(organization_id, report_id, snapshot_id)
        provider = self._provider()
        provider.generate_embed_configuration.return_value = EmbedConfiguration(
            report_id=str(uuid.uuid4()),
            embed_url=(
                "https://app.powerbi.com/reportEmbed?reportId="
                "00000000-0000-0000-0000-000000000005"
            ),
            access_token="short-lived-test-token",
            expires_at=self.now + timedelta(minutes=30),
        )
        self.app_module.app.extensions["roster_scout_power_bi"] = provider

        response = self._client(accounts[0], organization_id).get(
            f"/reports/{report_id}/snapshots/{snapshot_id}/embed"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        provider.generate_embed_configuration.assert_called_once_with(
            account_id=accounts[0],
            organization_id=organization_id,
            snapshot_id=snapshot_id,
        )
        serialized = response.get_data(as_text=True)
        self.assertNotIn(str(organization_id), serialized)

    def test_foreign_and_missing_snapshot_are_indistinguishable_and_issue_no_token(self):
        organization_id, accounts = self._organization(
            "Visible University", ("coach@visible.edu", "admin")
        )
        foreign_id, _foreign_accounts = self._organization(
            "Private University", ("coach@private.edu", "admin")
        )
        foreign_report = self._report(foreign_id, "Private Bears", 9502)
        foreign_snapshot = self._snapshot(
            foreign_id, foreign_report, "ready", self.now
        )
        provider = self._provider()
        self.app_module.app.extensions["roster_scout_power_bi"] = provider
        client = self._client(accounts[0], organization_id)

        foreign = client.get(
            f"/reports/{foreign_report}/snapshots/{foreign_snapshot}/embed"
        )
        missing = client.get(
            f"/reports/{uuid.uuid4()}/snapshots/{uuid.uuid4()}/embed"
        )

        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(foreign.get_json(), missing.get_json())
        provider.generate_embed_configuration.assert_not_called()
        provider.start_refresh.assert_not_called()

    def test_status_starts_and_completes_refresh_without_mutating_snapshot(self):
        from roster_scout.models import ExternalRefreshDiagnostic, ReportSnapshot
        from roster_scout.power_bi import RefreshConfiguration

        organization_id, accounts = self._organization(
            "Refresh University", ("coach@refresh.edu", "admin")
        )
        report_id = self._report(organization_id, "Refresh Hawks", 9503)
        snapshot_id = self._snapshot(
            organization_id, report_id, "ready", self.now
        )
        correlation = str(uuid.uuid4())
        provider = self._provider()
        provider.start_refresh.return_value = RefreshConfiguration(
            correlation, "pending", "Unknown"
        )
        provider.refresh_status.return_value = RefreshConfiguration(
            correlation, "completed", "Completed"
        )
        self.app_module.app.extensions["roster_scout_power_bi"] = provider

        response = self._client(accounts[0], organization_id).get(
            f"/reports/{report_id}/snapshots/{snapshot_id}/dashboard-status"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ready"})
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        provider.start_refresh.assert_called_once_with()
        provider.refresh_status.assert_called_once_with(correlation)
        with self.Session() as session:
            snapshot = session.get(ReportSnapshot, snapshot_id)
            diagnostic = session.scalar(select(ExternalRefreshDiagnostic).where(
                ExternalRefreshDiagnostic.snapshot_id == snapshot_id
            ))
            self.assertEqual(snapshot.status, "ready")
            self.assertEqual(snapshot.published_at, self.now)
            self.assertEqual(diagnostic.status, "completed")


class PowerBIUIContractTests(unittest.TestCase):
    def test_workbook_actions_remain_before_dashboard_and_all_states_are_truthful(self):
        template = (ROOT / "templates/report_detail.html").read_text()
        self.assertLess(
            template.index("Current workbook"),
            template.index("Opponent dashboard"),
        )
        for phrase in (
            "Interactive analysis is being prepared",
            "The dashboard needs another try",
            "Interactive analysis is not connected yet",
            "Your saved workbook is unaffected",
        ):
            self.assertIn(phrase, template)
        self.assertIn('data-status-url="{{ url_for(', template)
        self.assertIn('aria-label="Opponent analysis pages"', template)

    def test_embed_script_is_ephemeral_keyboard_accessible_and_snapshot_scoped(self):
        script = (ROOT / "static/power_bi_embed.js").read_text()
        for forbidden in ("localStorage", "sessionStorage", "document.cookie"):
            self.assertNotIn(forbidden, script)
        for required in (
            'cache: "no-store"',
            'credentials: "same-origin"',
            "setAccessToken",
            "ArrowLeft",
            "ArrowRight",
            "data-dashboard-scope",
            'column: "scope_code"',
            'last_10: "last_10"',
            'conference: "conference"',
        ):
            self.assertIn(required, script)
        self.assertIn("Development preview · Illustrative values only", script)

    def test_dashboard_styles_are_responsive_and_respect_reduced_motion(self):
        css = (ROOT / "static/product.css").read_text()
        self.assertIn(".opponent-dashboard", css)
        self.assertIn(".dashboard-state[hidden] { display: none; }", css)
        self.assertIn("@media (max-width: 767px)", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":root[data-theme=\"dark\"]", css)


if __name__ == "__main__":
    unittest.main()
