"""Fail-closed contracts for detailed NCAA fielding evidence."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


FIXTURES = Path(__file__).parent / "fixtures" / "ncaa_detail"


def fixture(division: str) -> str:
    return (FIXTURES / division / "fielding.html").read_text(encoding="utf-8")


def fielding_api():
    return importlib.import_module("roster_scout.ncaa_detail")


class FieldingTests(unittest.TestCase):
    source_url = (
        "https://stats.ncaa.org/teams/615087/season_to_date_stats"
        "?year_stat_category_id=15869"
    )

    def parse(self, html: str, *, category_id: str = "15869"):
        return fielding_api().parse_fielding_page(
            html,
            source_url=self.source_url,
            stat_category_id=category_id,
        )

    def assert_failure(self, html: str, category: str):
        api = fielding_api()
        with self.assertRaises(api.FieldingParseError) as caught:
            self.parse(html)
        self.assertEqual(caught.exception.category, category)
        self.assertNotIn(self.source_url, str(caught.exception))

    def test_fielding_vertical_contract(self):
        """RED sentinel: imports compile and only the fielding module is absent."""
        self.assertIsNotNone(
            importlib.util.find_spec("roster_scout.ncaa_detail"),
            "NCAA fielding vertical slice is not implemented",
        )

    def test_d1_d2_d3_share_canonical_fields_and_preserve_source_identity(self):
        expected = {
            "games",
            "games_started",
            "putouts",
            "assists",
            "errors",
            "total_chances",
            "double_plays",
            "fielding_percentage",
            "passed_balls",
            "stolen_base_attempts",
            "caught_stealing_by",
        }
        for division, player_id in (("d1", "11001"), ("d2", "22001"), ("d3", "33001")):
            with self.subTest(division=division):
                section = self.parse(fixture(division))
                self.assertEqual(section.name, "fielding")
                self.assertFalse(section.verified_empty)
                self.assertEqual(section.source_url, self.source_url)
                self.assertEqual(section.stat_category_id, "15869")
                self.assertEqual(len(section.html_sha256), 64)
                self.assertEqual(section.rows[0].source_player_id, player_id)
                self.assertEqual(
                    section.rows[0].player_url,
                    f"https://stats.ncaa.org/players/{player_id}",
                )
                self.assertEqual(set(section.rows[0].canonical_values), expected)

    def test_optional_catcher_columns_are_nullable_and_sourced_zero_is_preserved(self):
        d2 = self.parse(fixture("d2")).rows[0].canonical_values
        self.assertIsNone(d2["passed_balls"])
        self.assertIsNone(d2["stolen_base_attempts"])
        self.assertIsNone(d2["caught_stealing_by"])

        d3 = self.parse(fixture("d3")).rows[0].canonical_values
        self.assertEqual(d3["passed_balls"], 0)
        self.assertEqual(d3["stolen_base_attempts"], 0)
        self.assertEqual(d3["caught_stealing_by"], 0)
        self.assertEqual(d3["fielding_percentage"], Decimal("1.000"))

    def test_rejects_missing_duplicate_or_renamed_required_headers(self):
        valid = fixture("d2")
        cases = (
            valid.replace("<th>TC</th>", ""),
            valid.replace("<th>PO</th>", "<th>PO</th><th>PO</th>"),
            valid.replace("<th>FPCT</th>", "<th>Fielding %</th>"),
        )
        for html in cases:
            with self.subTest(html=html[:80]):
                self.assert_failure(html, "schema_validation")

    def test_rejects_negative_counts_invalid_rates_and_mismatched_rows(self):
        valid = fixture("d2")
        cases = (
            valid.replace("<td>22</td>", "<td>-1</td>", 1),
            valid.replace("<td>.963</td>", "<td>1.001</td>", 1),
            valid.replace("<td>80</td><td>7</td>", "<td>80</td>", 1),
        )
        for html in cases:
            with self.subTest(html=html[-160:]):
                self.assert_failure(html, "schema_validation")

    def test_missing_blocked_or_bad_player_link_is_not_verified_empty(self):
        cases = (
            "<html><body>Access Denied</body></html>",
            "<html><body><p>No table here</p></body></html>",
            fixture("d2").replace("/players/22001", "https://example.test/players/22001"),
        )
        for html in cases:
            with self.subTest(html=html[:80]):
                self.assert_failure(html, "source_blocked" if "stat_grid" not in html else "schema_validation")

    def test_verified_empty_requires_a_valid_fielding_grid(self):
        empty = fixture("d2").replace(
            '<tr>\n          <td><a href="/players/22001">D Two Infielder</a></td>\n'
            '          <td>22</td><td>21</td><td>31</td><td>46</td><td>3</td>\n'
            '          <td>80</td><td>7</td><td>.963</td>\n        </tr>',
            "",
        )
        section = self.parse(empty)
        self.assertTrue(section.verified_empty)
        self.assertEqual(section.rows, ())

    def test_outbound_url_is_fixed_host_and_numeric_id_only(self):
        build = fielding_api().build_fielding_url
        self.assertEqual(build("615087", "15869"), self.source_url)
        for season_team_id, category_id in (
            ("615087@example.test", "15869"),
            ("615087", "15869&next=https://example.test"),
            ("https://example.test", "15869"),
            ("615087", ""),
        ):
            with self.subTest(season_team_id=season_team_id, category_id=category_id):
                with self.assertRaises(ValueError):
                    build(season_team_id, category_id)

    def test_shared_collector_fetches_discovered_fielding_id_on_fixed_endpoint(self):
        import roster_scraper as rs

        core_fixtures = Path(__file__).parent / "fixtures"
        pages = {
            "batting": (core_fixtures / "d1_batting.html").read_text(encoding="utf-8"),
            "pitching": (core_fixtures / "d1_pitching.html").read_text(encoding="utf-8"),
            "fielding": fixture("d1"),
        }
        season = {
            "season": "2026",
            "id": "17040",
            "batting_id": "15867",
            "pitching_id": "15868",
            "fielding_id": "15869",
        }
        team = {
            "team_id": "234",
            "team_name": "Florida St.",
            "division": "1",
            "year": "2026",
            "season_id": "17040",
            "season_team_id": "614606",
        }
        with patch.object(rs, "_find_ncaa_season", return_value=season), patch.object(
            rs, "_fetch_ncaa_pages", return_value=pages
        ) as fetch:
            collection = rs._collect_ncaa_team(team, 2026)

        urls = fetch.call_args.args[0]
        self.assertEqual(
            urls["fielding"],
            "https://stats.ncaa.org/teams/614606/season_to_date_stats"
            "?year_stat_category_id=15869",
        )
        self.assertEqual(
            {section.name for section in collection.sections},
            {"batting", "pitching", "fielding"},
        )
        self.assertEqual(collection.season.fielding_category_id, "15869")

    def test_canonical_fielding_fact_is_frozen_and_shares_authoritative_player(self):
        from roster_scraper import NcaaCollection, NcaaCollectionMetadata, NcaaSeason, NcaaTeam
        from roster_scout.canonical import canonicalize_ncaa_collection

        section = self.parse(fixture("d1"))
        collection = NcaaCollection(
            team=NcaaTeam("234", "Florida St.", "614606", "d1"),
            season=NcaaSeason("17040", 2026, "15867", "15868", "15869"),
            metadata=NcaaCollectionMetadata(section.collected_at),
            sections=(section.as_ncaa_section(),),
            players=(),
            team_label="Florida St.",
        )
        canonical = canonicalize_ncaa_collection(collection)
        fact = next(item for item in canonical.facts if item.section == "fielding")
        player = next(item for item in canonical.players if item.source_player_id == "11001")
        self.assertEqual(fact.identity_key, player.identity_key)
        self.assertEqual(fact.detailed_values["putouts"], 121)
        with self.assertRaises((AttributeError, TypeError)):
            replace(fact, section="batting").detailed_values["putouts"] = 0

    def test_fielding_model_uses_composite_owner_and_source_identity_constraints(self):
        from roster_scout.models import FieldingFact

        names = {constraint.name for constraint in FieldingFact.__table__.constraints}
        self.assertIn("fk_fielding_facts_snapshot_organization", names)
        self.assertIn("fk_fielding_facts_player_snapshot_organization", names)
        self.assertIn("fk_fielding_facts_source_identity", names)
        for column in (
            "games", "games_started", "putouts", "assists", "errors",
            "total_chances", "double_plays", "passed_balls",
            "stolen_base_attempts", "caught_stealing_by",
        ):
            self.assertIn(f"ck_fielding_facts_{column}_nonnegative", names)

    def test_fixtures_are_sanitized(self):
        forbidden = ("token=", "cookie", "authorization", "password", "customer")
        for division in ("d1", "d2", "d3"):
            text = fixture(division).casefold()
            with self.subTest(division=division):
                self.assertFalse(any(value in text for value in forbidden))


class FixtureCaptureSafetyTests(unittest.TestCase):
    """The live fixture utility must stay inside a narrow source/file boundary."""

    @staticmethod
    def capture_api():
        return importlib.import_module("scripts.capture_phase3_ncaa_fixtures")

    def test_output_is_confined_to_the_requested_division_fixture_root(self):
        api = self.capture_api()
        root = Path(__file__).parent / "fixtures" / "ncaa_detail"
        accepted = api.validate_capture_request(
            season_id="17040",
            season_team_id="614606",
            division="1",
            output_dir=root / "d1",
            limits=api.CaptureLimits(),
        )
        self.assertEqual(accepted.output_dir, (root / "d1").resolve())
        for output_dir in (
            root / "d2",
            root / "d1" / ".." / "..",
            Path(tempfile.gettempdir()) / "ncaa-detail",
        ):
            with self.subTest(output_dir=output_dir), self.assertRaises(ValueError):
                api.validate_capture_request(
                    season_id="17040",
                    season_team_id="614606",
                    division="1",
                    output_dir=output_dir,
                    limits=api.CaptureLimits(),
                )

    def test_source_urls_are_fixed_host_numeric_paths(self):
        api = self.capture_api()
        self.assertEqual(
            api.build_ncaa_url("614606", "15867"),
            "https://stats.ncaa.org/teams/614606/season_to_date_stats"
            "?year_stat_category_id=15867",
        )
        for team_id, category_id in (
            ("614606@example.test", "15867"),
            ("614606", "15867&token=secret"),
            ("https://example.test", "15867"),
            ("614606", ""),
        ):
            with self.subTest(team_id=team_id, category_id=category_id):
                with self.assertRaises(ValueError):
                    api.build_ncaa_url(team_id, category_id)

    def test_control_sanitizer_removes_secrets_scripts_and_unrelated_people(self):
        api = self.capture_api()
        raw = """
        <html><head><script>fetch('wss://production.browserless.io?token=top-secret')</script></head>
        <body data-cookie="session=secret">
          <p>coach@example.edu</p>
          <select name="split_id" onchange="steal()">
            <option value="991">vs. Left-Handed Pitchers</option>
            <option value="992">Bases Empty</option>
          </select>
          <a href="/teams/614606/season_to_date_stats?year_stat_category_id=15867&amp;split_id=991&amp;token=secret">
            Vs LHP
          </a>
          <div>Unrelated Player Name</div>
        </body></html>
        """
        sanitized = api.sanitize_split_controls(
            raw,
            season_id="17040",
            season_team_id="614606",
            division="1",
            stat_type="batting",
            source_path="/teams/614606/season_to_date_stats",
        )
        lowered = sanitized.casefold()
        for forbidden in (
            "top-secret", "token=", "cookie", "script", "onchange",
            "browserless", "coach@example.edu", "unrelated player name",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)
        self.assertIn("vs_lhp", sanitized)
        self.assertIn("991", sanitized)
        self.assertIn("bases_empty", sanitized)

    def test_limits_are_rejected_before_external_capture_is_called(self):
        api = self.capture_api()
        root = Path(__file__).parent / "fixtures" / "ncaa_detail" / "d1"
        with patch("roster_scraper._fetch_ncaa_pages") as fetch:
            with self.assertRaises(ValueError):
                api.capture_controls(
                    season_id="17040",
                    season_team_id="614606",
                    division="1",
                    output_dir=root,
                    limits=api.CaptureLimits(max_pages=0),
                )
        fetch.assert_not_called()

    def test_manifest_is_allowlisted_sorted_and_contains_no_raw_source(self):
        api = self.capture_api()
        manifest = api.build_fixture_manifest(
            division="1",
            season_id="17040",
            season_team_id="614606",
            captured_at="2026-11-16T00:00:00Z",
            controls_sha256="a" * 64,
            observations=[{
                "stat_type": "batting",
                "canonical_label": "vs_lhp",
                "status": "candidate",
                "identifier": "991",
                "source_path": "/teams/614606/season_to_date_stats",
                "raw_html": "must-not-survive",
                "token": "must-not-survive",
            }],
        )
        encoded = json.dumps(manifest, sort_keys=True)
        self.assertNotIn("raw_html", encoded)
        self.assertNotIn("must-not-survive", encoded)
        self.assertEqual(
            set(manifest),
            {
                "captured_at", "controls_sha256", "division", "observations",
                "sanitizer_version", "season_id", "season_team_id",
            },
        )
        self.assertEqual(
            set(manifest["observations"][0]),
            {"canonical_label", "identifier", "source_path", "stat_type", "status"},
        )

    def test_preflight_without_configuration_has_one_fixed_safe_failure(self):
        script = Path(__file__).parents[1] / "scripts" / "capture_phase3_ncaa_fixtures.py"
        env = dict(os.environ)
        env.pop("BROWSERLESS_TOKEN", None)
        env["NCAA_BROWSER_PROVIDER"] = "browserless"
        result = subprocess.run(
            [sys.executable, str(script), "--preflight"],
            cwd=Path(__file__).parents[1],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "phase3_browserless_unavailable")


class FieldingPublicationTests(
    __import__("tests.test_report_jobs", fromlist=["PostgreSQLJobTestCase"])
    .PostgreSQLJobTestCase
):
    """PostgreSQL contracts for evidence-first fielding publication."""

    @staticmethod
    def _sections(*, fielding_status="complete"):
        from datetime import UTC, datetime
        from roster_scout.jobs import CollectedSection, PARSER_VERSION

        collected_at = datetime.now(UTC)
        core = tuple(
            CollectedSection(
                name=name,
                critical=True,
                rows=(),
                verified_empty=True,
                source_url=f"https://stats.ncaa.org/{name}",
                source_team_id="234",
                source_season_id="17040",
                source_season_team_id="614606",
                source_stat_category_id=category,
                collected_at=collected_at,
                parser_version=PARSER_VERSION,
                schema_version="schema-1",
            )
            for name, category in (("batting", "15867"), ("pitching", "15868"))
        )
        if fielding_status != "complete":
            return (*core, CollectedSection.supplemental_failure(
                "fielding",
                status=fielding_status,
                category="source_blocked",
                collected_at=collected_at,
                source_url=(
                    "https://stats.ncaa.org/teams/614606/season_to_date_stats"
                    "?year_stat_category_id=15869"
                ),
                source_team_id="234",
                source_season_id="17040",
                source_season_team_id="614606",
                source_stat_category_id="15869",
            ))

        parsed = fielding_api().parse_fielding_page(
            fixture("d1"),
            source_url=(
                "https://stats.ncaa.org/teams/614606/season_to_date_stats"
                "?year_stat_category_id=15869"
            ),
            stat_category_id="15869",
            collected_at=collected_at,
        )
        return (*core, CollectedSection(
            name="fielding",
            critical=False,
            rows=tuple({
                **dict(row.raw_values),
                "_player_id": row.source_player_id,
                "_player_url": row.player_url,
                "_payload_sha256": row.payload_sha256,
            } for row in parsed.rows),
            source_url=parsed.source_url,
            source_team_id="234",
            source_season_id="17040",
            source_season_team_id="614606",
            source_stat_category_id=parsed.stat_category_id,
            collected_at=parsed.collected_at,
            parser_version=PARSER_VERSION,
            schema_version="schema-1",
        ))

    def test_complete_fielding_persists_exact_evidence_and_publishes_ready(self):
        from sqlalchemy import func, select
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import FieldingFact, ReportSection, SourceRecord

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "fielding-complete", dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self._sections(),
        )
        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            section = session.scalar(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section == "fielding",
            ))
            self.assertEqual((section.status, section.row_count), ("complete", 2))
            self.assertEqual(session.scalar(
                select(func.count()).select_from(SourceRecord).where(
                    SourceRecord.snapshot_id == snapshot.id,
                    SourceRecord.section == "fielding",
                )
            ), 2)
            facts = list(session.scalars(select(FieldingFact).where(
                FieldingFact.snapshot_id == snapshot.id
            )))
            self.assertEqual(len(facts), 2)
            self.assertEqual(facts[0].putouts, 121)
            self.assertIsNone(facts[1].passed_balls)

    def test_unavailable_fielding_publishes_truthful_partial(self):
        from sqlalchemy import func, select
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import FieldingFact, ReportSection, SourceRecord

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "fielding-unavailable", dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self._sections(
                fielding_status="unavailable"
            ),
        )
        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            section = session.scalar(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section == "fielding",
            ))
            self.assertEqual(section.status, "unavailable")
            self.assertEqual(section.failure_category, "source_blocked")
            self.assertIsNotNone(section.collected_at)
            self.assertEqual(section.row_count, 0)
            self.assertFalse(section.verified_empty)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(SourceRecord).where(
                    SourceRecord.snapshot_id == snapshot.id,
                    SourceRecord.section == "fielding",
                )
            ), 0)
            self.assertEqual(session.scalar(
                select(func.count()).select_from(FieldingFact).where(
                    FieldingFact.snapshot_id == snapshot.id
                )
            ), 0)

    def test_publication_rejects_fielding_row_count_mismatch(self):
        from roster_scout.jobs import (
            _new_or_existing_snapshot,
            _persist_canonical,
            _persist_sections,
            accept_report_job,
        )
        from roster_scout.models import ReportJob, ReportSection
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot
        from sqlalchemy import select

        organization_id, report_id = self.fixture()
        accepted = accept_report_job(
            self.Session, organization_id, report_id, "fielding-mismatch", dispatch=None
        )
        sections = self._sections()
        with self.Session.begin() as session:
            job = session.get(ReportJob, accepted.id)
            snapshot = _new_or_existing_snapshot(session, job)
            _persist_sections(session, job, snapshot, sections)
            _persist_canonical(session, job, snapshot, sections)
            snapshot_id = snapshot.id
            fielding = session.scalar(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section == "fielding",
            ))
            fielding.row_count += 1
            session.flush()
            with self.assertRaisesRegex(SnapshotPublicationError, "row count"):
                publish_snapshot(session, snapshot_id)

    def test_fielding_fact_rejects_cross_snapshot_identity_and_source(self):
        import uuid
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import FieldingFact, ReportSnapshot

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "fielding-owner", dispatch=None
        )
        published = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: self._sections(),
        )
        with self.Session.begin() as session:
            source_fact = session.scalar(select(FieldingFact).where(
                FieldingFact.snapshot_id == published.id
            ).limit(1))
            other = ReportSnapshot(
                organization_id=organization_id,
                report_id=report_id,
                schema_version="schema-1",
            )
            session.add(other)
            session.flush()
            with self.assertRaises(IntegrityError), session.begin_nested():
                session.add(FieldingFact(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    snapshot_id=other.id,
                    player_team_season_id=source_fact.player_team_season_id,
                    source_record_id=source_fact.source_record_id,
                    games=source_fact.games,
                ))
                session.flush()


if __name__ == "__main__":
    unittest.main()
