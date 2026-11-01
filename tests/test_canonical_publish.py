"""Fixture-to-PostgreSQL publication coverage for NCAA D1, D2, and D3."""

import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

import roster_scraper as rs
from tests.db_support import clean_database, require_test_database_url, upgrade_database


FIXTURES = Path(__file__).parent / "fixtures"
SEASON = {"season": "2026", "id": "17040", "batting_id": "15867", "pitching_id": "15868"}
TEAMS = {
    1: {"team_id": "234", "team_name": "D One", "division": "1", "year": "2026", "season_id": "17040", "season_team_id": "614606"},
    2: {"team_id": "689", "team_name": "D Two", "division": "2", "year": "2026", "season_id": "17040", "season_team_id": "615087"},
    3: {"team_id": "633", "team_name": "D Three", "division": "3", "year": "2026", "season_id": "17040", "season_team_id": "615349"},
}


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class CanonicalPublishTests(unittest.TestCase):
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
        rs._NCAA_SEASON_ROWS = [dict(SEASON)]

    def tearDown(self):
        rs._NCAA_TEAM_ROWS = None
        rs._NCAA_SEASON_ROWS = None

    def seed(self, division):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import Organization, Report, Season, Team, TeamSeason

        source = TEAMS[division]
        rs._NCAA_TEAM_ROWS = [dict(source)]
        with self.Session.begin() as session:
            organization = Organization(name="Axis Baseball")
            team = Team(source="ncaa", source_team_id=source["team_id"], name=source["team_name"])
            season = Season(source="ncaa", source_season_id=source["season_id"], year=2026)
            session.add_all([organization, team, season])
            session.flush()
            team_season = TeamSeason(
                team_id=team.id,
                season_id=season.id,
                source_season_team_id=source["season_team_id"],
                division=source["division"],
                batting_stat_category_id=SEASON["batting_id"],
                pitching_stat_category_id=SEASON["pitching_id"],
            )
            session.add(team_season)
            session.flush()
            report = Report(
                organization_id=organization.id,
                team_season_id=team_season.id,
                name=f"{source['team_name']} 2026",
            )
            session.add(report)
            session.flush()
            organization_id, report_id = organization.id, report.id
        return accept_report_job(
            self.Session, organization_id, report_id, uuid.uuid4().hex, dispatch=None
        )

    def publish(self, division, pages=None):
        from roster_scout.jobs import collect_ncaa_sections, run_report_job

        job = self.seed(division)
        pages = pages or {
            "batting": fixture(f"d{division}_batting.html"),
            "pitching": fixture(f"d{division}_pitching.html"),
        }
        with patch.object(rs, "_fetch_ncaa_pages", return_value=pages):
            snapshot = run_report_job(
                self.Session,
                job.id,
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )
        return job, snapshot

    def test_all_divisions_publish_one_provenance_rich_canonical_contract(self):
        from roster_scout.models import (
            BattingFact,
            MetricDefinition,
            PitchingFact,
            PlayerTeamSeason,
            ReportSection,
            ReportSnapshot,
            SourceRecord,
        )

        schemas = []
        for division in (1, 2, 3):
            with self.subTest(division=division):
                clean_database(self.database_url)
                _, snapshot = self.publish(division)
                self.assertEqual(snapshot.status, "ready")
                with self.Session() as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(ReportSnapshot)), 1)
                    sections = list(session.scalars(select(ReportSection)))
                    self.assertEqual({item.section for item in sections}, {"batting", "pitching"})
                    sources = list(session.scalars(select(SourceRecord)))
                    self.assertTrue(sources)
                    self.assertTrue(all(record.source_url.startswith("https://stats.ncaa.org/") for record in sources))
                    self.assertTrue(all(record.source_player_id for record in sources))
                    self.assertTrue(all(record.raw_values.get("player_url", "").startswith("https://stats.ncaa.org/players/") for record in sources))
                    self.assertTrue(all(record.collected_at and record.parser_version and record.schema_version for record in sources))
                    self.assertTrue(all(len(record.payload_sha256) == 64 for record in sources))
                    batting = list(session.scalars(select(BattingFact)))
                    pitching = list(session.scalars(select(PitchingFact)))
                    self.assertTrue(batting)
                    self.assertTrue(pitching)
                    self.assertTrue(all(fact.source_record_id for fact in batting + pitching))
                    self.assertTrue(session.scalars(select(MetricDefinition)).all())
                    schemas.append((
                        tuple(column.name for column in BattingFact.__table__.columns),
                        tuple(column.name for column in PitchingFact.__table__.columns),
                    ))
                    if division == 1:
                        two_way = list(session.scalars(
                            select(PlayerTeamSeason).where(PlayerTeamSeason.display_name == "D One Two Way")
                        ))
                        self.assertEqual(len(two_way), 1)
                        self.assertEqual(
                            session.scalar(select(func.count()).select_from(BattingFact).where(BattingFact.player_team_season_id == two_way[0].id)),
                            1,
                        )
                        self.assertEqual(
                            session.scalar(select(func.count()).select_from(PitchingFact).where(PitchingFact.player_team_season_id == two_way[0].id)),
                            1,
                        )
        self.assertEqual(len(set(schemas)), 1)

    def test_verified_empty_core_publishes_but_missing_core_does_not(self):
        from roster_scout.jobs import RetryableJobError, collect_ncaa_sections, run_report_job
        from roster_scout.models import ReportSnapshot

        empty = '<html><body><table id="stat_grid"><thead><tr><th>Player</th></tr></thead><tbody></tbody></table></body></html>'
        _, snapshot = self.publish(2, {
            "batting": empty,
            "pitching": fixture("d2_pitching.html"),
        })
        self.assertEqual(snapshot.status, "ready")

        clean_database(self.database_url)
        job = self.seed(2)
        with patch.object(rs, "_fetch_ncaa_pages", return_value={"batting": fixture("d2_batting.html")}):
            with self.assertRaises(RetryableJobError):
                run_report_job(
                    self.Session,
                    job.id,
                    collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                        self.Session, job_id, cached_sections=cached_sections
                    ),
                )
        with self.Session() as session:
            self.assertIsNone(session.scalar(
                select(ReportSnapshot).where(ReportSnapshot.status.in_(("ready", "partial")))
            ))

    def test_duplicate_delivery_replays_snapshot_without_collecting(self):
        from roster_scout.jobs import run_report_job

        job, snapshot = self.publish(3)
        replay = run_report_job(
            self.Session,
            job.id,
            collector=lambda *args, **kwargs: self.fail("terminal job collected twice"),
        )
        self.assertEqual(replay.id, snapshot.id)

    def test_worker_loss_after_source_write_reuses_only_same_job_evidence(self):
        from roster_scout.jobs import collect_ncaa_sections, run_report_job
        from roster_scout.models import ReportSection, ReportSnapshot, SourceRecord

        job = self.seed(1)
        pages = {
            "batting": fixture("d1_batting.html"),
            "pitching": fixture("d1_pitching.html"),
        }
        with patch.object(rs, "_fetch_ncaa_pages", return_value=pages), patch(
            "roster_scout.jobs._persist_canonical", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_report_job(
                    self.Session,
                    job.id,
                    collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                        self.Session, job_id, cached_sections=cached_sections
                    ),
                )
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ReportSection)), 2)
            self.assertGreater(session.scalar(select(func.count()).select_from(SourceRecord)), 0)
            self.assertIsNone(session.scalar(
                select(ReportSnapshot).where(ReportSnapshot.status.in_(("ready", "partial")))
            ))

        with patch.object(rs, "_fetch_ncaa_pages", side_effect=AssertionError("same-job cache missed")):
            replay = run_report_job(
                self.Session,
                job.id,
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )
        self.assertEqual(replay.status, "ready")

    def test_new_refresh_forces_live_collection_and_does_not_reuse_old_rows(self):
        from roster_scout.jobs import collect_ncaa_sections, run_report_job
        from roster_scout.models import ReportJob, ReportSnapshot

        first_job, first_snapshot = self.publish(2)
        with self.Session() as session:
            stored = session.get(ReportJob, first_job.id)
            organization_id, report_id = stored.organization_id, stored.report_id
        from roster_scout.jobs import accept_report_job
        next_job = accept_report_job(
            self.Session, organization_id, report_id, "next-refresh", dispatch=None
        )
        with patch.object(rs, "_fetch_ncaa_pages", return_value={
            "batting": fixture("d2_batting.html"),
            "pitching": fixture("d2_pitching.html"),
        }):
            live_collection = rs._collect_ncaa_team(TEAMS[2], 2026)
        with patch.object(rs, "collect_ncaa_by_id", return_value=live_collection) as collect:
            second = run_report_job(
                self.Session,
                next_job.id,
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )
        collect.assert_called_once_with("615087", division="2", year=2026, force_refresh=True)
        self.assertNotEqual(second.id, first_snapshot.id)
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ReportSnapshot)), 2)


if __name__ == "__main__":
    unittest.main()
