"""Durable immutable workbook artifact storage and recovery contracts."""

import io
import tempfile
import unittest
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import ClientError
from sqlalchemy import select

from tests.test_report_jobs import PostgreSQLJobTestCase as _PostgreSQLJobTestCase


class ArtifactStorageUnitTests(unittest.TestCase):
    def test_saved_source_rows_use_the_homepage_normalization_and_split(self):
        from roster_scout.artifacts import _workbook_rows_from_source_records

        batting = [{
            "Player": "Taylor Two-Way",
            "Jersey": "12",
            "Pos": "OF/RHP",
            "Yr": "Jr",
            "BA": ".321",
            "OBPct": ".411",
            "SlgPct": ".544",
            "AB": "81",
            "H": "26",
            "2B": "7",
            "HR": "5",
            "RBI": "22",
            "R": "19",
            "BB": "14",
            "K": "17",
            "SB": "8",
            "_player_id": "9001",
        }]
        pitching = [{
            "Player": "Taylor Two-Way",
            "Jersey": "12",
            "Pos": "OF/RHP",
            "Yr": "Jr",
            "ERA": "2.45",
            "WHIP": "1.10",
            "W": "4",
            "L": "1",
            "SV": "2",
            "IP": "33.0",
            "H": "28",
            "ER": "9",
            "BB": "8",
            "SO": "41",
            "_player_id": "9001",
        }]

        records = [
            SimpleNamespace(
                section="batting",
                source_player_id="9001",
                raw_values=batting[0],
            ),
            SimpleNamespace(
                section="pitching",
                source_player_id="9001",
                raw_values=pitching[0],
            ),
        ]
        hitters, pitchers = _workbook_rows_from_source_records(records)

        self.assertEqual(hitters[0]["Name"], "Taylor Two-Way")
        self.assertEqual(hitters[0]["AVG"], ".321")
        self.assertEqual(hitters[0]["HR"], "5")
        self.assertEqual(pitchers[0]["Name"], "Taylor Two-Way")
        self.assertEqual(pitchers[0]["ERA"], "2.45")
        self.assertEqual(pitchers[0]["SO"], "41")

    def test_object_keys_are_tenant_scoped_and_reject_traversal(self):
        from roster_scout.artifacts import (
            ArtifactStorageError,
            artifact_object_key,
            _validated_object_key,
        )

        organization_id = uuid.uuid4()
        report_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        key = artifact_object_key(organization_id, report_id, snapshot_id)
        self.assertEqual(
            key,
            f"organizations/{organization_id}/reports/{report_id}/snapshots/"
            f"{snapshot_id}/workbooks/roster-scout-coach-facing-two-sheet-xlsx-v5.xlsx",
        )
        with self.assertRaisesRegex(ArtifactStorageError, "invalid artifact key"):
            _validated_object_key(f"../{key}")

    def test_filesystem_store_is_create_only_and_integrity_checked(self):
        from roster_scout.artifacts import ArtifactStorageError, FilesystemArtifactStore

        key = (
            f"organizations/{uuid.uuid4()}/reports/{uuid.uuid4()}/snapshots/"
            f"{uuid.uuid4()}/workbooks/roster-scout-basic-xlsx-v1.xlsx"
        )
        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemArtifactStore(directory)
            original = b"immutable-workbook"
            digest = sha256(original).hexdigest()
            store.write_immutable(key, original, digest)
            store.write_immutable(key, original, digest)
            self.assertEqual(store.read(key), original)

            replacement = b"different-workbook"
            with self.assertRaisesRegex(ArtifactStorageError, "integrity"):
                store.write_immutable(
                    key,
                    replacement,
                    sha256(replacement).hexdigest(),
                )
            self.assertEqual(store.read(key), original)
            store.delete(key)
            store.delete(key)
            with self.assertRaisesRegex(ArtifactStorageError, "unavailable"):
                store.read(key)

    def test_s3_store_uses_conditional_private_encrypted_write(self):
        from roster_scout.artifacts import S3ArtifactStore

        client = Mock()
        key = (
            f"organizations/{uuid.uuid4()}/reports/{uuid.uuid4()}/snapshots/"
            f"{uuid.uuid4()}/workbooks/roster-scout-basic-xlsx-v1.xlsx"
        )
        payload = b"xlsx"
        digest = sha256(payload).hexdigest()
        S3ArtifactStore(bucket="private-artifacts", client=client).write_immutable(
            key, payload, digest
        )
        parameters = client.put_object.call_args.kwargs
        self.assertEqual(parameters["Bucket"], "private-artifacts")
        self.assertEqual(parameters["IfNoneMatch"], "*")
        self.assertEqual(parameters["ServerSideEncryption"], "AES256")
        self.assertEqual(parameters["Metadata"], {"sha256": digest})
        store = S3ArtifactStore(bucket="private-artifacts", client=client)
        store.delete(key)
        client.delete_object.assert_called_once_with(
            Bucket="private-artifacts",
            Key=key,
        )

    def test_s3_existing_object_must_match_digest_and_size(self):
        from roster_scout.artifacts import ArtifactStorageError, S3ArtifactStore

        client = Mock()
        client.put_object.side_effect = ClientError(
            {
                "Error": {"Code": "PreconditionFailed"},
                "ResponseMetadata": {"HTTPStatusCode": 412},
            },
            "PutObject",
        )
        key = (
            f"organizations/{uuid.uuid4()}/reports/{uuid.uuid4()}/snapshots/"
            f"{uuid.uuid4()}/workbooks/roster-scout-basic-xlsx-v1.xlsx"
        )
        payload = b"xlsx"
        digest = sha256(payload).hexdigest()
        client.head_object.return_value = {
            "Metadata": {"sha256": digest},
            "ContentLength": len(payload),
        }
        store = S3ArtifactStore(bucket="private-artifacts", client=client)
        store.write_immutable(key, payload, digest)

        client.head_object.return_value["Metadata"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ArtifactStorageError, "immutable artifact conflict"):
            store.write_immutable(key, payload, digest)

    def test_workbook_treats_source_formulas_and_urls_as_plain_text(self):
        from roster_scout.artifacts import SnapshotWorkbookSource, build_snapshot_workbook

        source = SnapshotWorkbookSource(
            organization_id=uuid.uuid4(),
            report_id=uuid.uuid4(),
            snapshot_id=uuid.uuid4(),
            source_team_id="test-team",
            team_name="Formula Safety",
            season_year=2026,
            published_at=datetime(2026, 11, 14, 16, 0, tzinfo=UTC),
            hitters=({"Name": "=1+1", "Position": "https://example.invalid"},),
            pitchers=(),
        )
        payload = build_snapshot_workbook(source)
        self.assertEqual(payload, build_snapshot_workbook(source))
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            worksheet_xml = b"".join(
                archive.read(name)
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet")
            )
            shared_strings = archive.read("xl/sharedStrings.xml")
        self.assertNotIn(b"<f>", worksheet_xml)
        self.assertNotIn(b"<hyperlinks", worksheet_xml)
        self.assertIn(b"=1+1", shared_strings)
        self.assertIn(b"https://example.invalid", shared_strings)

    def test_saved_report_uses_the_exact_homepage_workbook_renderer(self):
        import app as app_module
        from roster_scout.artifacts import SnapshotWorkbookSource, build_snapshot_workbook
        from roster_scout.workbooks import workbook_branding

        published_at = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
        hitters = ({"Name": "Alex Example", "AVG": ".321", "HR": "7"},)
        pitchers = ({"Name": "Pat Pitcher", "ERA": "2.45", "SO": "31"},)
        source = SnapshotWorkbookSource(
            organization_id=uuid.uuid4(),
            report_id=uuid.uuid4(),
            snapshot_id=uuid.uuid4(),
            source_team_id="test-team",
            team_name="Parity University",
            season_year=2026,
            published_at=published_at,
            source_collected_at=datetime(2026, 11, 14, 15, 58, tzinfo=UTC),
            snapshot_status="partial",
            hitters=hitters,
            pitchers=pitchers,
        )
        branding = workbook_branding(
            school_name=source.team_name,
            generated_on=published_at.date(),
            team_id=source.source_team_id,
        )

        payload = build_snapshot_workbook(source)
        self.assertEqual(
            payload,
            app_module._create_workbook(
                list(hitters), list(pitchers), branding=branding
            ).getvalue(),
        )
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            shared_strings = archive.read("xl/sharedStrings.xml")
        self.assertIn(b"NOVEMBER 14, 2026", shared_strings)
        for removed in (b"SEASON", b"PUBLISHED", b"SOURCE", b"SNAPSHOT"):
            self.assertNotIn(removed, shared_strings)


class ArtifactContractTests(unittest.TestCase):
    """Product contract for the detailed, still-two-sheet workbook."""

    @staticmethod
    def _detailed_rows():
        hitter = {
            "_player_id": "9001",
            "_player_url": "https://stats.ncaa.org/players/9001",
            "Name": "Taylor Two-Way",
            "#": "12",
            "Position": "OF/RHP",
            "Class": "Jr",
            "G": "32",
            "GS": "31",
            "PA": "140",
            "AB": "119",
            "H": "41",
            "2B": "8",
            "3B": "2",
            "HR": "7",
            "RBI": "34",
            "R": "29",
            "BB": "16",
            "SO": "20",
            "SB": "9",
            "AVG": ".345",
            "OBP": ".421",
            "SLG": ".622",
            "OPS": "1.043",
            "ISO": ".277",
            "BB%": ".114",
            "K%": ".143",
            "BABIP": ".382",
            "wOBA": "",
            "Last 10 G": "10",
            "Last 10 PA": "44",
            "Last 10 AB": "38",
            "Last 10 AVG": ".368",
            "Last 10 OPS": "1.101",
            "Last 10 K%": ".114",
            "Last 10 Status": "Complete",
            "Conference Status": "Unavailable",
        }
        pitcher = {
            "_player_id": "9001",
            "_player_url": "https://stats.ncaa.org/players/9001",
            "Name": "Taylor Two-Way",
            "#": "12",
            "Position": "OF/RHP",
            "Class": "Jr",
            "G": "12",
            "GS": "7",
            "IP": "43.2",
            "W": "5",
            "L": "2",
            "SV": "1",
            "H": "37",
            "ER": "14",
            "BB": "12",
            "SO": "51",
            "HR": "3",
            "BF": "183",
            "ERA": "2.89",
            "WHIP": "1.12",
            "K/9": "10.51",
            "BB/9": "2.47",
            "H/9": "7.63",
            "HR/9": ".62",
            "K/BB": "4.25",
            "K%": ".279",
            "BB%": ".066",
            "BABIP": ".311",
            "FIP": "",
            "Last 10 Status": "Partial",
            "Conference Status": "Unavailable",
        }
        return hitter, pitcher

    def test_grouped_workbook_has_only_two_sheets_and_populated_header_groups(self):
        from roster_scout.workbooks import create_workbook

        hitter, pitcher = self._detailed_rows()
        payload = create_workbook(
            [hitter],
            [pitcher],
            branding={
                "school_name": "Parity University",
                "generated_on": datetime(2026, 11, 16, tzinfo=UTC).date(),
            },
        ).getvalue()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            hitters_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            pitchers_xml = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
            shared_strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
            archive_names = set(archive.namelist())

        self.assertEqual(workbook_xml.count("<sheet "), 2)
        self.assertIn('name="Hitters"', workbook_xml)
        self.assertIn('name="Pitchers"', workbook_xml)
        for label in (
            "Player",
            "Season Stats",
            "Last 10",
        ):
            self.assertIn(f">{label}<", shared_strings)
        for excluded in (
            "NCAA Player ID",
            "Source URL",
            "Derived Metrics",
            "Conference Status",
            "Fielding",
            "Career",
            "Game Logs",
            "Methodology",
            "Status",
            "Conference Stats",
            "wOBA",
            "FIP",
        ):
            self.assertNotIn(f">{excluded}<", shared_strings)
        self.assertIn('xSplit="4"', hitters_xml)
        self.assertIn('ySplit="4"', hitters_xml)
        self.assertIn('xSplit="4"', pitchers_xml)
        self.assertIn('ySplit="4"', pitchers_xml)
        self.assertIn("<autoFilter", hitters_xml)
        self.assertIn("<autoFilter", pitchers_xml)
        self.assertFalse(any(name.startswith("xl/tables/") for name in archive_names))

    def test_grouped_workbook_omits_audit_ids_and_preserves_scope_semantics(self):
        from roster_scout.workbooks import create_workbook

        hitter, pitcher = self._detailed_rows()
        hitter["Name"] = "=1+1"
        hitter["Conference G"] = ""
        payload = create_workbook([hitter], [pitcher]).getvalue()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            worksheets = b"".join(
                archive.read(name)
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet")
            )
            shared_strings = archive.read("xl/sharedStrings.xml")

        self.assertNotIn(b"<f>", worksheets)
        self.assertNotIn(b"<hyperlinks", worksheets)
        self.assertNotIn(b"9001", shared_strings)
        self.assertNotIn(b"https://stats.ncaa.org/players/9001", shared_strings)
        self.assertIn(b"=1+1", shared_strings)
        self.assertNotIn(b"Complete", shared_strings)
        self.assertNotIn(b"Partial", shared_strings)
        self.assertNotIn(b"Conference Status", shared_strings)
        self.assertNotIn(b"Conference G</t></si><si><t>0", shared_strings)

    def test_grouped_workbook_omits_wholly_empty_columns_but_keeps_zeroes(self):
        from roster_scout.workbooks import HITTER_COLUMNS, populated_columns

        columns = populated_columns(
            [{"Name": "Zero Value", "PA": 0, "wOBA": "", "Last 10 G": None}],
            HITTER_COLUMNS,
        )
        headers = [column.header for column in columns]

        self.assertIn("PA", headers)
        self.assertNotIn("wOBA", headers)
        self.assertNotIn("Games", headers)

    def test_two_way_player_keeps_identity_on_both_sheets(self):
        from roster_scraper import split_roster

        hitter, pitcher = self._detailed_rows()
        combined = {
            **hitter,
            "P_H": pitcher["H"],
            "P_BB": pitcher["BB"],
            "P_SO": pitcher["SO"],
            "_has_hitting": True,
            "_has_pitching": True,
            "_pos": "OF/RHP",
        }
        hitters, pitchers = split_roster([combined])

        self.assertEqual(hitters[0]["_player_id"], "9001")
        self.assertEqual(pitchers[0]["_player_id"], "9001")
        self.assertEqual(
            hitters[0]["_player_url"], "https://stats.ncaa.org/players/9001"
        )
        self.assertEqual(
            pitchers[0]["_player_url"], "https://stats.ncaa.org/players/9001"
        )

    def test_canonical_projection_uses_exact_snapshot_facts_metrics_and_scopes(self):
        from roster_scout.artifacts import _project_snapshot_workbook_rows

        player_id = uuid.uuid4()
        batting_source_id = uuid.uuid4()
        pitching_source_id = uuid.uuid4()
        last_ten_batting_id = uuid.uuid4()
        conference_batting_id = uuid.uuid4()
        last_ten_pitching_id = uuid.uuid4()
        conference_pitching_id = uuid.uuid4()
        player = SimpleNamespace(
            id=player_id,
            display_name="Taylor Canonical",
            jersey_number="12",
            position="OF/RHP",
        )
        batting_source = SimpleNamespace(
            id=batting_source_id,
            player_team_season_id=player_id,
            section="batting",
            source_player_id="9001",
            source_url="https://stats.ncaa.org/teams/701",
            raw_values={
                "Yr": "Jr",
                "player_url": "https://stats.ncaa.org/players/9001",
            },
        )
        pitching_source = SimpleNamespace(
            id=pitching_source_id,
            player_team_season_id=player_id,
            section="pitching",
            source_player_id="9001",
            source_url="https://stats.ncaa.org/teams/701",
            raw_values={"W": "5", "L": "2", "SV": "1"},
        )
        batting = SimpleNamespace(
            player_team_season_id=player_id,
            source_record_id=batting_source_id,
            games=32,
            games_started=31,
            plate_appearances=None,
            at_bats=119,
            runs=29,
            hits=41,
            doubles=8,
            triples=2,
            home_runs=7,
            runs_batted_in=34,
            walks=16,
            hit_by_pitch=3,
            strikeouts=20,
            sacrifice_flies=2,
            sacrifice_hits=0,
            stolen_bases=9,
        )
        pitching = SimpleNamespace(
            player_team_season_id=player_id,
            source_record_id=pitching_source_id,
            appearances=12,
            games_started=7,
            innings_outs=131,
            hits_allowed=37,
            earned_runs=14,
            walks=12,
            strikeouts=51,
            home_runs_allowed=3,
            hit_batters=2,
            batters_faced=183,
            opponent_at_bats=166,
            opponent_sacrifice_flies=2,
        )

        def scope(scope_id, code, stat_type, status, games):
            return SimpleNamespace(
                id=scope_id,
                scope_code=code,
                stat_type=stat_type,
                status=status,
                actual_game_count=games,
            )

        scopes = (
            scope(last_ten_batting_id, "last_10", "batting", "complete", 10),
            scope(conference_batting_id, "conference", "batting", "unavailable", None),
            scope(last_ten_pitching_id, "last_10", "pitching", "partial", 8),
            scope(conference_pitching_id, "conference", "pitching", "failed", None),
        )
        scoped_batting = (SimpleNamespace(
            statistic_scope_id=last_ten_batting_id,
            player_team_season_id=player_id,
            counts_json={
                "plate_appearances": 44,
                "at_bats": 38,
                "hits": 14,
                "doubles": 3,
                "triples": 0,
                "home_runs": 2,
                "walks": 5,
                "hit_by_pitch": 1,
                "strikeouts": 5,
                "sacrifice_flies": 0,
                "sacrifice_hits": 0,
            },
        ),)

        hitters, pitchers = _project_snapshot_workbook_rows(
            (player,),
            (batting_source, pitching_source),
            (batting,),
            (pitching,),
            (),
            scopes,
            scoped_batting,
            (),
        )

        self.assertEqual(hitters[0]["_player_id"], "9001")
        self.assertEqual(
            hitters[0]["_player_url"], "https://stats.ncaa.org/players/9001"
        )
        self.assertEqual(hitters[0]["G"], 32)
        self.assertEqual(hitters[0]["PA"], 140)
        self.assertEqual(hitters[0]["OPS"], "1.050")
        self.assertEqual(hitters[0]["Last 10 G"], 10)
        self.assertEqual(hitters[0]["Last 10 AVG"], "0.368")
        self.assertEqual(hitters[0]["Last 10 Status"], "Complete")
        self.assertEqual(hitters[0]["Conference Status"], "Unavailable")
        self.assertEqual(pitchers[0]["IP"], "43.2")
        self.assertEqual(pitchers[0]["K/9"], "10.51")
        self.assertEqual(pitchers[0]["Last 10 Status"], "Partial")
        self.assertEqual(pitchers[0]["Conference Status"], "Failed")
        self.assertNotIn("Last 10 ERA", pitchers[0])

    def test_canonical_projection_hides_pitcher_only_placeholder_from_hitters(self):
        from roster_scout.artifacts import _project_snapshot_workbook_rows

        player_id = uuid.uuid4()
        source_id = uuid.uuid4()
        player = SimpleNamespace(
            id=player_id,
            display_name="Pitcher Only",
            jersey_number="18",
            position="UT",
        )
        source = SimpleNamespace(
            id=source_id,
            player_team_season_id=player_id,
            section="batting",
            source_player_id="18",
            source_url="https://stats.ncaa.org/teams/701",
            raw_values={"Yr": "So", "Pos": "RHP"},
        )
        batting = SimpleNamespace(
            player_team_season_id=player_id,
            source_record_id=source_id,
            games=15,
            games_started=0,
            plate_appearances=0,
            at_bats=0,
            runs=0,
            hits=0,
            doubles=0,
            triples=0,
            home_runs=0,
            runs_batted_in=0,
            walks=0,
            hit_by_pitch=0,
            strikeouts=0,
            sacrifice_flies=0,
            sacrifice_hits=0,
            stolen_bases=0,
        )

        hitters, pitchers = _project_snapshot_workbook_rows(
            (player,), (source,), (batting,), (), (), (), (), ()
        )

        self.assertEqual(hitters, [])
        self.assertEqual(pitchers, [])

    def test_canonical_projection_marks_undefined_rates_instead_of_leaving_blanks(self):
        from roster_scout.artifacts import _project_snapshot_workbook_rows

        player_id = uuid.uuid4()
        source_id = uuid.uuid4()
        player = SimpleNamespace(
            id=player_id,
            display_name="Pinch Runner",
            jersey_number="3",
            position="INF",
        )
        source = SimpleNamespace(
            id=source_id,
            player_team_season_id=player_id,
            section="batting",
            source_player_id="123",
            source_url="https://stats.ncaa.org/teams/701",
            raw_values={"Yr": "Sr"},
        )
        batting = SimpleNamespace(
            player_team_season_id=player_id,
            source_record_id=source_id,
            games=18,
            games_started=9,
            plate_appearances=0,
            at_bats=0,
            runs=1,
            hits=0,
            doubles=0,
            triples=0,
            home_runs=0,
            runs_batted_in=0,
            walks=0,
            hit_by_pitch=0,
            strikeouts=0,
            sacrifice_flies=0,
            sacrifice_hits=0,
            stolen_bases=0,
        )

        hitters, _ = _project_snapshot_workbook_rows(
            (player,), (source,), (batting,), (), (), (), (), ()
        )

        self.assertEqual(hitters[0]["PA"], 0)
        self.assertEqual(hitters[0]["AVG"], "—")
        self.assertEqual(hitters[0]["wOBA"], "—")

    def test_artifact_schema_bump_accepts_exact_historical_keys(self):
        from roster_scout.artifacts import (
            ARTIFACT_SCHEMA_VERSION,
            _validated_object_key,
            artifact_object_key,
        )

        organization_id = uuid.uuid4()
        report_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        current = artifact_object_key(organization_id, report_id, snapshot_id)
        historical_v1 = (
            f"organizations/{organization_id}/reports/{report_id}/snapshots/"
            f"{snapshot_id}/workbooks/roster-scout-basic-xlsx-v1.xlsx"
        )
        historical_v2 = (
            f"organizations/{organization_id}/reports/{report_id}/snapshots/"
            f"{snapshot_id}/workbooks/roster-scout-detailed-two-sheet-xlsx-v2.xlsx"
        )

        historical_v3 = (
            f"organizations/{organization_id}/reports/{report_id}/snapshots/"
            f"{snapshot_id}/workbooks/roster-scout-coach-facing-two-sheet-xlsx-v3.xlsx"
        )

        historical_v4 = (
            f"organizations/{organization_id}/reports/{report_id}/snapshots/"
            f"{snapshot_id}/workbooks/roster-scout-coach-facing-two-sheet-xlsx-v4.xlsx"
        )

        self.assertEqual(ARTIFACT_SCHEMA_VERSION, "coach-facing-two-sheet-xlsx-v5")
        self.assertIn(ARTIFACT_SCHEMA_VERSION, current)
        self.assertEqual(_validated_object_key(historical_v1), historical_v1)
        self.assertEqual(_validated_object_key(historical_v2), historical_v2)
        self.assertEqual(_validated_object_key(historical_v3), historical_v3)
        self.assertEqual(_validated_object_key(historical_v4), historical_v4)


class ArtifactLifecycleTests(_PostgreSQLJobTestCase):
    def _published_artifact(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportArtifact
        from tests.test_report_jobs import PublicationTests

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "durable-artifact",
            dispatch=None,
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=Mock(return_value=PublicationTests.successful_sections()),
        )
        with self.Session() as session:
            artifact = session.scalar(
                select(ReportArtifact).where(
                    ReportArtifact.snapshot_id == snapshot.id
                )
            )
            return snapshot.id, artifact.id

    def test_published_snapshot_builds_one_exact_immutable_workbook(self):
        from roster_scout.artifacts import (
            FilesystemArtifactStore,
            build_claimed_snapshot_artifact,
            claim_artifact_build,
        )
        from roster_scout.models import ReportArtifact

        _snapshot_id, artifact_id = self._published_artifact()
        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemArtifactStore(directory)
            attempt = claim_artifact_build(self.Session, artifact_id)
            self.assertEqual(attempt, 1)
            self.assertEqual(
                build_claimed_snapshot_artifact(
                    self.Session,
                    artifact_id,
                    attempt,
                    store=store,
                ),
                "ready",
            )
            with self.Session() as session:
                artifact = session.get(ReportArtifact, artifact_id)
                payload = store.read(artifact.object_key)
                self.assertEqual(artifact.status, "ready")
                self.assertEqual(artifact.sha256, sha256(payload).hexdigest())
                self.assertEqual(artifact.attempt_count, 1)
                self.assertTrue(payload.startswith(b"PK"))
            self.assertIsNone(claim_artifact_build(self.Session, artifact_id))

    def test_detailed_snapshot_source_projects_canonical_rows_not_legacy_rates(self):
        from roster_scout.artifacts import load_snapshot_workbook_source
        from roster_scout.jobs import accept_report_job, run_report_job
        from tests.test_report_jobs import FullSeasonMetricPublicationTests

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "detailed-workbook-source",
            dispatch=None,
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=Mock(
                return_value=FullSeasonMetricPublicationTests.detailed_sections()
            ),
        )

        with self.Session() as session:
            source = load_snapshot_workbook_source(session, snapshot.id)

        hitter = next(row for row in source.hitters if row["_player_id"] == "100")
        pitcher = next(row for row in source.pitchers if row["_player_id"] == "100")
        self.assertEqual(hitter["_player_url"], "https://stats.ncaa.org/players/100")
        self.assertEqual(hitter["G"], 12)
        self.assertEqual(hitter["OPS"], "1.500")
        self.assertEqual(hitter["Last 10 Status"], "Unavailable")
        self.assertEqual(hitter["Conference Status"], "Unavailable")
        self.assertEqual(pitcher["IP"], "7.0")
        self.assertEqual(pitcher["K/9"], "11.57")
        self.assertEqual(source.source_collected_at.isoformat(), "2026-04-01T18:30:00+00:00")

    def test_dispatch_failure_releases_claim_for_bounded_retry(self):
        from roster_scout.artifacts import dispatch_pending_artifacts
        from roster_scout.models import ReportArtifact

        _snapshot_id, artifact_id = self._published_artifact()
        now = datetime.now(UTC)
        first = dispatch_pending_artifacts(
            self.Session,
            dispatch=Mock(side_effect=ConnectionError("broker secret")),
            now=now,
        )
        self.assertEqual((first.claimed, first.published, first.released), (1, 0, 1))
        with self.Session() as session:
            failed = session.get(ReportArtifact, artifact_id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.failure_category, "queue")
            self.assertEqual(failed.attempt_count, 1)

        too_soon = dispatch_pending_artifacts(
            self.Session,
            dispatch=Mock(),
            now=now + timedelta(seconds=29),
        )
        self.assertEqual(too_soon.claimed, 0)
        dispatch = Mock()
        retry = dispatch_pending_artifacts(
            self.Session,
            dispatch=dispatch,
            now=now + timedelta(seconds=30),
        )
        self.assertEqual((retry.claimed, retry.published), (1, 1))
        dispatch.assert_called_once_with(str(artifact_id), 2)

        with self.Session.begin() as session:
            exhausted = session.get(ReportArtifact, artifact_id)
            exhausted.status = "failed"
            exhausted.attempt_count = 3
            exhausted.last_attempt_at = now - timedelta(hours=1)
        final = dispatch_pending_artifacts(
            self.Session,
            dispatch=Mock(),
            now=now + timedelta(hours=1),
        )
        self.assertEqual(final.claimed, 0)


if __name__ == "__main__":
    unittest.main()
