"""Google Sheets OAuth boundary contracts."""

from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

from roster_scout.google_sheets import (
    DRIVE_FILE_SCOPE,
    GoogleSheetsOAuthClient,
)
from roster_scout.workbooks import workbook_download_name
from datetime import date


class GoogleSheetsOAuthClientTests(unittest.TestCase):
    def _client(self, http=None):
        return GoogleSheetsOAuthClient(
            "client-id.apps.googleusercontent.com",
            "client-secret",
            "https://roster-scout.example/integrations/google/callback",
            http=http or Mock(),
        )

    def test_authorization_uses_exact_callback_state_and_drive_file_only(self):
        url = self._client().authorization_url(state="signed-state")
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.netloc, "accounts.google.com")
        self.assertEqual(query["state"], ["signed-state"])
        self.assertEqual(query["scope"], [DRIVE_FILE_SCOPE])
        self.assertEqual(query["access_type"], ["online"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://roster-scout.example/integrations/google/callback"],
        )
        self.assertNotIn("drive", query["scope"][0].removesuffix("drive.file"))

    def test_exchange_and_upload_convert_xlsx_without_refresh_token_storage(self):
        token_response = Mock()
        token_response.content = b'{"access_token":"access-token"}'
        token_response.json.return_value = {"access_token": "access-token"}
        upload_response = Mock()
        upload_response.content = b'{"id":"sheet_id-123456789"}'
        upload_response.json.return_value = {"id": "sheet_id-123456789"}
        http = Mock()
        http.post.side_effect = [token_response, upload_response]
        client = self._client(http)

        access_token = client.exchange_code("authorization-code")
        destination = client.import_workbook(
            access_token=access_token,
            workbook_name="Abilene Christian Stats 11-15-26.xlsx",
            payload=b"PK-workbook",
        )

        self.assertEqual(access_token, "access-token")
        self.assertEqual(
            destination,
            "https://docs.google.com/spreadsheets/d/sheet_id-123456789/edit",
        )
        token_call, upload_call = http.post.call_args_list
        self.assertEqual(token_call.kwargs["data"]["grant_type"], "authorization_code")
        self.assertNotIn("refresh_token", token_call.kwargs["data"])
        self.assertEqual(upload_call.kwargs["params"]["uploadType"], "multipart")
        metadata = json.loads(upload_call.kwargs["files"]["metadata"][1])
        self.assertEqual(metadata["name"], "Abilene Christian Stats 11-15-26")
        self.assertEqual(
            metadata["mimeType"], "application/vnd.google-apps.spreadsheet"
        )
        self.assertEqual(
            upload_call.kwargs["headers"], {"Authorization": "Bearer access-token"}
        )

    def test_environment_configuration_is_optional_but_never_partial(self):
        self.assertFalse(GoogleSheetsOAuthClient.from_environ({}).configured)
        with self.assertRaises(RuntimeError):
            GoogleSheetsOAuthClient.from_environ(
                {"GOOGLE_OAUTH_CLIENT_ID": "client-id"}
            )

    def test_workbook_name_uses_the_approved_shared_export_label(self):
        self.assertEqual(
            workbook_download_name(
                "Abilene Christian",
                generated_on=date(2026, 11, 15),
            ),
            "Team Stats 11-15-26.xlsx",
        )


if __name__ == "__main__":
    unittest.main()
