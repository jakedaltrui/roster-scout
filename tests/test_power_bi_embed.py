from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest
import uuid
from unittest.mock import Mock

import requests


class _Response:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("provider body must never be surfaced")

    def json(self):
        return self.payload


class PowerBIProviderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
        self.ids = [str(uuid.uuid4()) for _ in range(7)]

    def _environment(self, **overrides):
        tenant, client, workspace, report, dataset, _org, _snapshot = self.ids
        values = {
            "POWER_BI_MODE": "live",
            "POWER_BI_TENANT_ID": tenant,
            "POWER_BI_CLIENT_ID": client,
            "POWER_BI_CLIENT_SECRET": "test-secret-never-log",
            "POWER_BI_WORKSPACE_ID": workspace,
            "POWER_BI_REPORT_ID": report,
            "POWER_BI_SEMANTIC_MODEL_ID": dataset,
            "POWER_BI_EMBED_URL": (
                f"https://app.powerbi.com/reportEmbed?reportId={report}"
            ),
            "POWER_BI_RLS_ROLE": "RosterScoutSnapshotReader",
            "POWER_BI_CAPACITY_VERIFIED": "true",
        }
        values.update(overrides)
        return values

    def test_live_payload_uses_exact_snapshot_effective_identity(self):
        from roster_scout.power_bi import PowerBIClient, PowerBISettings

        tenant, client, workspace, report, dataset, organization, snapshot = self.ids
        account = uuid.uuid4()
        expires = self.now + timedelta(minutes=59)
        http = Mock()
        http.post.side_effect = [
            _Response({"access_token": "application-token"}),
            _Response({"token": "embed-token", "expiration": expires.isoformat()}),
        ]
        provider = PowerBIClient(
            PowerBISettings.from_environ(self._environment()),
            http=http,
            now=lambda: self.now,
        )

        result = provider.generate_embed_configuration(
            account_id=account,
            organization_id=organization,
            snapshot_id=snapshot,
        )

        self.assertEqual(result.report_id, report)
        self.assertEqual(result.access_token, "embed-token")
        token_call = http.post.call_args_list[1]
        self.assertEqual(
            token_call.args[0],
            "https://api.powerbi.com/v1.0/myorg/GenerateToken",
        )
        self.assertEqual(token_call.kwargs["timeout"], (3.05, 12))
        body = token_call.kwargs["json"]
        self.assertEqual(body["reports"], [{"id": report, "allowEdit": False}])
        self.assertEqual(body["datasets"], [{"id": dataset}])
        self.assertEqual(body["targetWorkspaces"], [{"id": workspace}])
        self.assertEqual(body["lifetimeInMinutes"], 60)
        self.assertEqual(body["identities"], [{
            "username": str(account),
            "roles": ["RosterScoutSnapshotReader"],
            "datasets": [dataset],
            "customData": f"{organization}|{snapshot}",
        }])

    def test_production_fake_and_unverified_capacity_fail_closed(self):
        from roster_scout.power_bi import PowerBIClient, PowerBIFailure, PowerBISettings

        with self.assertRaisesRegex(PowerBIFailure, "configuration"):
            PowerBISettings.from_environ({
                "POWER_BI_MODE": "fake",
                "APP_ENV": "production",
            })
        provider = PowerBIClient(
            PowerBISettings.from_environ(
                self._environment(POWER_BI_CAPACITY_VERIFIED="false")
            ),
            http=Mock(),
            now=lambda: self.now,
        )
        with self.assertRaisesRegex(PowerBIFailure, "capacity_unverified"):
            provider.generate_embed_configuration(
                account_id=uuid.uuid4(),
                organization_id=self.ids[-2],
                snapshot_id=self.ids[-1],
            )
        provider._http.post.assert_not_called()

    def test_configuration_rejects_untrusted_embed_host_role_and_partial_ids(self):
        from roster_scout.power_bi import PowerBIFailure, PowerBISettings

        for overrides in (
            {"POWER_BI_EMBED_URL": "https://evil.example/reportEmbed"},
            {"POWER_BI_RLS_ROLE": "Admin"},
            {"POWER_BI_REPORT_ID": "not-a-uuid"},
            {"POWER_BI_CLIENT_SECRET": ""},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(PowerBIFailure):
                    PowerBISettings.from_environ(self._environment(**overrides))

    def test_provider_failures_are_fixed_and_do_not_expose_body_or_secret(self):
        from roster_scout.power_bi import PowerBIClient, PowerBIFailure, PowerBISettings

        http = Mock()
        http.post.return_value = _Response({"error": "secret provider detail"}, 500)
        provider = PowerBIClient(
            PowerBISettings.from_environ(self._environment()),
            http=http,
            now=lambda: self.now,
        )
        with self.assertRaises(PowerBIFailure) as raised:
            provider.generate_embed_configuration(
                account_id=uuid.uuid4(),
                organization_id=self.ids[-2],
                snapshot_id=self.ids[-1],
            )
        self.assertEqual(raised.exception.category, "provider_unavailable")
        self.assertNotIn("secret", str(raised.exception))

    def test_local_fake_is_deterministic_and_explicit(self):
        from roster_scout.power_bi import PowerBIClient, PowerBISettings

        provider = PowerBIClient(
            PowerBISettings.from_environ({"POWER_BI_MODE": "fake"}),
            now=lambda: self.now,
        )
        result = provider.generate_embed_configuration(
            account_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            snapshot_id=uuid.uuid4(),
        )
        self.assertTrue(result.synthetic)
        self.assertEqual(result.expires_at, self.now + timedelta(minutes=30))
        self.assertEqual(result.as_dict()["synthetic"], True)

    def test_refresh_start_and_poll_use_only_fixed_dataset_authority(self):
        from roster_scout.power_bi import PowerBIClient, PowerBISettings

        _tenant, _client, workspace, _report, dataset, _organization, _snapshot = self.ids
        refresh_id = str(uuid.uuid4())
        http = Mock()
        http.post.side_effect = [
            _Response({"access_token": "application-token"}),
            _Response({}, 202, {"x-ms-request-id": refresh_id}),
            _Response({"access_token": "application-token"}),
        ]
        http.get.return_value = _Response({"status": "Completed"})
        provider = PowerBIClient(
            PowerBISettings.from_environ(self._environment()),
            http=http,
            now=lambda: self.now,
        )

        started = provider.start_refresh()
        completed = provider.refresh_status(started.correlation_id)

        refresh_call = http.post.call_args_list[1]
        self.assertEqual(
            refresh_call.args[0],
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace}/datasets/{dataset}/refreshes",
        )
        self.assertEqual(refresh_call.kwargs["json"], {"notifyOption": "NoNotification"})
        self.assertEqual(started.status, "pending")
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.provider_status, "Completed")

    def test_refresh_rejects_invalid_correlation_and_unknown_provider_status(self):
        from roster_scout.power_bi import PowerBIClient, PowerBIFailure, PowerBISettings

        provider = PowerBIClient(
            PowerBISettings.from_environ(self._environment()),
            http=Mock(),
            now=lambda: self.now,
        )
        with self.assertRaisesRegex(PowerBIFailure, "provider_response"):
            provider.refresh_status("../another-dataset")
        provider._http.post.return_value = _Response({"access_token": "token"})
        provider._http.get.return_value = _Response({"status": "Surprise"})
        with self.assertRaisesRegex(PowerBIFailure, "provider_response"):
            provider.refresh_status(str(uuid.uuid4()))


if __name__ == "__main__":
    unittest.main()
