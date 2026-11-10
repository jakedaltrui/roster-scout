"""Goal-level release evidence for the complete Phase 1 operator story."""

import gzip
import io
import unittest
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import func, select

import roster_scraper as scraper
from tests.db_support import clean_database, require_test_database_url, upgrade_database


FIXTURES = Path(__file__).parent / "fixtures"
SEASON = {
    "season": "2026",
    "id": "17040",
    "batting_id": "15867",
    "pitching_id": "15868",
}
TEAMS = {
    1: {
        "team_id": "234",
        "team_name": "D One",
        "division": "1",
        "year": "2026",
        "season_id": "17040",
        "season_team_id": "614606",
    },
    2: {
        "team_id": "689",
        "team_name": "D Two",
        "division": "2",
        "year": "2026",
        "season_id": "17040",
        "season_team_id": "615087",
    },
    3: {
        "team_id": "633",
        "team_name": "D Three",
        "division": "3",
        "year": "2026",
        "season_id": "17040",
        "season_team_id": "615349",
    },
}


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class Phase1ReleaseTests(unittest.TestCase):
    """Exercise the durable D1/D2/D3 slice through public service boundaries."""

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
        scraper._NCAA_SEASON_ROWS = [dict(SEASON)]

    def tearDown(self):
        scraper._NCAA_TEAM_ROWS = None
        scraper._NCAA_SEASON_ROWS = None

    def _seed_report(self, division, *, idempotency_key=None, dispatch=None):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import Organization, Report, Season, Team, TeamSeason

        source = TEAMS[division]
        scraper._NCAA_TEAM_ROWS = [dict(source)]
        with self.Session.begin() as session:
            organization = Organization(name=f"Release D{division}")
            team = Team(
                source="ncaa",
                source_team_id=source["team_id"],
                name=source["team_name"],
            )
            season = Season(
                source="ncaa",
                source_season_id=source["season_id"],
                year=int(source["year"]),
            )
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
            organization_id = organization.id
            report_id = report.id
        key = idempotency_key or f"release-d{division}-{uuid.uuid4().hex}"
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            key,
            dispatch=dispatch,
        )
        return job, organization_id, report_id, key

    def _release_state(self, job_id, organization_id, snapshot_id):
        from roster_scout.models import (
            BattingFact,
            MetricDefinition,
            MetricValue,
            PitchingFact,
            PlayerTeamSeason,
            Report,
            ReportJob,
            ReportSection,
            ReportSnapshot,
            Season,
            SourceRecord,
            Team,
            TeamSeason,
        )
        from roster_scout.repositories import get_published_snapshot

        with self.Session() as session:
            job = session.get(ReportJob, job_id)
            snapshot = get_published_snapshot(session, organization_id, snapshot_id)
            report = session.get(Report, job.report_id)
            team_season = session.get(TeamSeason, report.team_season_id)
            team = session.get(Team, team_season.team_id)
            season = session.get(Season, team_season.season_id)
            sections = list(session.scalars(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot_id
            )))
            sources = list(session.scalars(select(SourceRecord).where(
                SourceRecord.snapshot_id == snapshot_id
            )))
            identities = list(session.scalars(select(PlayerTeamSeason).where(
                PlayerTeamSeason.snapshot_id == snapshot_id
            )))
            batting = list(session.scalars(select(BattingFact).where(
                BattingFact.snapshot_id == snapshot_id
            )))
            pitching = list(session.scalars(select(PitchingFact).where(
                PitchingFact.snapshot_id == snapshot_id
            )))
            metric_values = list(session.scalars(select(MetricValue).where(
                MetricValue.snapshot_id == snapshot_id
            )))
            metric_definition_ids = set(session.scalars(select(MetricDefinition.id)))
            source_ids = {row.id for row in sources}
            identity_ids = {row.id for row in identities}
            facts = [*batting, *pitching]
            per_section = {
                section.section: {
                    "declared": section.row_count,
                    "sources": sum(row.section == section.section for row in sources),
                    "facts": len(batting) if section.section == "batting" else len(pitching),
                }
                for section in sections
            }
            return {
                "job_status": job.status,
                "snapshot_status": snapshot.status,
                "snapshot_count": session.scalar(
                    select(func.count()).select_from(ReportSnapshot)
                ),
                "snapshot_id": snapshot.id,
                "published_at": snapshot.published_at,
                "source_collected_at": snapshot.source_collected_at,
                "provenance": {
                    "organization_id": snapshot.organization_id,
                    "report_id": snapshot.report_id,
                    "team_id": team.source_team_id,
                    "season_id": season.source_season_id,
                    "season_team_id": team_season.source_season_team_id,
                    "division": team_season.division,
                    "batting_category_id": team_season.batting_stat_category_id,
                    "pitching_category_id": team_season.pitching_stat_category_id,
                },
                "sections": {section.section for section in sections},
                "sections_complete": all(
                    section.critical and section.status == "complete"
                    for section in sections
                ),
                "per_section": per_section,
                "source_count": len(sources),
                "fact_count": len(facts),
                "identity_count": len(identities),
                "metric_definition_count": session.scalar(
                    select(func.count()).select_from(MetricDefinition)
                ),
                "metric_value_count": len(metric_values),
                "all_fact_links_exact": all(
                    fact.source_record_id in source_ids
                    and fact.player_team_season_id in identity_ids
                    for fact in facts
                ),
                "all_metric_links_exact": all(
                    metric.metric_definition_id in metric_definition_ids
                    and (
                        metric.player_team_season_id is None
                        or metric.player_team_season_id in identity_ids
                    )
                    for metric in metric_values
                ),
                "all_sources_auditable": all(
                    row.source_team_id
                    and row.source_season_id
                    and row.source_season_team_id
                    and row.source_stat_category_id
                    and row.source_url.startswith("https://stats.ncaa.org/")
                    and row.collected_at
                    and row.parser_version
                    and row.schema_version
                    and len(row.payload_sha256) == 64
                    and isinstance(row.raw_values, dict)
                    for row in sources
                ),
                "source_fingerprints": {
                    row.id: (row.collected_at, row.payload_sha256, row.raw_values)
                    for row in sources
                },
            }

    def _publish_division_via_reconciled_dispatch(self, division):
        from roster_scout.jobs import (
            collect_ncaa_sections,
            drain_report_dispatch_outbox,
            reconcile_stale_report_jobs,
            run_report_job,
        )
        from roster_scout.models import ReportJobDispatch

        initially_dispatched = []

        def acknowledge_then_drop(job_id, generation):
            initially_dispatched.append((job_id, generation))
            return Mock(id="accepted-then-dropped")

        job, organization_id, _report_id, key = self._seed_report(
            division,
            idempotency_key=f"release-d{division}",
            dispatch=acknowledge_then_drop,
        )
        self.assertEqual(initially_dispatched, [(str(job.id), 1)])
        with self.Session() as session:
            intent = session.scalar(
                select(ReportJobDispatch).where(ReportJobDispatch.job_id == job.id)
            )
            redispatch_at = intent.redispatch_after + timedelta(microseconds=1)

        reconciled = reconcile_stale_report_jobs(
            self.Session,
            now=redispatch_at,
            queued_timeout=timedelta(minutes=5),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(reconciled.reopened, 1)
        redelivered = []
        drained = drain_report_dispatch_outbox(
            self.Session,
            dispatch=lambda job_id, generation: (
                redelivered.append((job_id, generation))
                or Mock(id="reconciled-delivery")
            ),
            now=redispatch_at,
            limit=1,
        )
        self.assertEqual(drained.published, 1)
        self.assertEqual(redelivered, [(str(job.id), 1)])

        pages = {
            "batting": fixture(f"d{division}_batting.html"),
            "pitching": fixture(f"d{division}_pitching.html"),
        }
        with patch.object(scraper, "_fetch_ncaa_pages", return_value=pages) as fetch:
            snapshot = run_report_job(
                self.Session,
                job.id,
                redelivered[0][1],
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )
        fetch.assert_called_once()
        release = self._release_state(job.id, organization_id, snapshot.id)
        release.update({
            "job_id": job.id,
            "organization_id": organization_id,
            "idempotency_key": key,
            "collector_calls": fetch.call_count,
        })
        return release

    def _assert_complete_release(self, division, release):
        source = TEAMS[division]
        self.assertEqual(release["job_status"], "ready")
        self.assertEqual(release["snapshot_status"], "ready")
        self.assertEqual(release["snapshot_count"], 1)
        self.assertEqual(release["sections"], {"batting", "pitching"})
        self.assertTrue(release["sections_complete"])
        self.assertEqual(release["provenance"]["organization_id"], release["organization_id"])
        self.assertEqual(release["provenance"]["team_id"], source["team_id"])
        self.assertEqual(release["provenance"]["season_id"], source["season_id"])
        self.assertEqual(release["provenance"]["season_team_id"], source["season_team_id"])
        self.assertEqual(release["provenance"]["division"], source["division"])
        self.assertEqual(release["provenance"]["batting_category_id"], SEASON["batting_id"])
        self.assertEqual(release["provenance"]["pitching_category_id"], SEASON["pitching_id"])
        self.assertEqual(set(release["per_section"]), {"batting", "pitching"})
        for counts in release["per_section"].values():
            self.assertGreater(counts["declared"], 0)
            self.assertEqual(counts["declared"], counts["sources"])
            self.assertEqual(counts["declared"], counts["facts"])
        self.assertGreater(release["source_count"], 0)
        self.assertEqual(release["source_count"], release["fact_count"])
        self.assertGreater(release["identity_count"], 0)
        self.assertGreater(release["metric_definition_count"], 0)
        self.assertGreater(release["metric_value_count"], 0)
        self.assertTrue(release["all_fact_links_exact"])
        self.assertTrue(release["all_metric_links_exact"])
        self.assertTrue(release["all_sources_auditable"])
        self.assertIsNotNone(release["published_at"])
        self.assertIsNotNone(release["source_collected_at"])
        self.assertEqual(release["collector_calls"], 1)

    def test_representative_d1_happy_path(self):
        """A reconciled durable D1 job publishes one auditable ready snapshot."""
        self._assert_complete_release(
            1, self._publish_division_via_reconciled_dispatch(1)
        )

    def test_d2_and_d3_share_the_complete_canonical_contract(self):
        for division in (2, 3):
            with self.subTest(division=division):
                clean_database(self.database_url)
                self._assert_complete_release(
                    division,
                    self._publish_division_via_reconciled_dispatch(division),
                )

    def test_duplicate_and_reopen_use_the_same_immutable_snapshot_without_collection(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportJob, ReportSnapshot
        from roster_scout.repositories import get_published_snapshot, list_published_snapshots

        release = self._publish_division_via_reconciled_dispatch(2)
        dispatch = Mock(side_effect=AssertionError("terminal job was dispatched again"))
        duplicate = accept_report_job(
            self.Session,
            release["organization_id"],
            self._report_id_for_job(release["job_id"]),
            release["idempotency_key"],
            dispatch=dispatch,
        )
        self.assertEqual(duplicate.id, release["job_id"])
        dispatch.assert_not_called()

        collector = Mock(side_effect=AssertionError("terminal job collected again"))
        replay = run_report_job(self.Session, duplicate.id, collector=collector)
        self.assertEqual(replay.id, release["snapshot_id"])
        collector.assert_not_called()

        with patch.object(
            scraper,
            "_fetch_ncaa_pages",
            side_effect=AssertionError("published reopen contacted NCAA"),
        ):
            with self.Session() as session:
                reopened = get_published_snapshot(
                    session, release["organization_id"], release["snapshot_id"]
                )
                library = list_published_snapshots(session, release["organization_id"])
                self.assertEqual(reopened.id, release["snapshot_id"])
                self.assertEqual([item.id for item in library], [release["snapshot_id"]])
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(ReportJob)), 1
                )
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(ReportSnapshot)), 1
                )

        after = self._release_state(
            release["job_id"], release["organization_id"], release["snapshot_id"]
        )
        self.assertEqual(after["published_at"], release["published_at"])
        self.assertEqual(after["source_collected_at"], release["source_collected_at"])
        self.assertEqual(after["source_fingerprints"], release["source_fingerprints"])

    def _report_id_for_job(self, job_id):
        from roster_scout.models import ReportJob

        with self.Session() as session:
            return session.get(ReportJob, job_id).report_id

    def test_malformed_and_blocked_core_fail_closed_with_bounded_purgeable_evidence(self):
        from roster_scout.evidence import purge_expired_evidence
        from roster_scout.jobs import (
            DeterministicJobError,
            collect_ncaa_sections,
            run_report_job,
        )
        from roster_scout.models import FailureEvidence, ReportJob, ReportSnapshot

        malformed = (
            "<html><table id='stat_grid'><thead><tr><th>Athlete</th></tr></thead>"
            f"<tbody><tr><td>{'SENSITIVE-HTML' * 6000}</td></tr></tbody></table></html>"
        )
        cases = {
            "malformed": (
                {"batting": malformed, "pitching": fixture("d3_pitching.html")},
                DeterministicJobError,
                "schema_validation",
                "schema_validation",
            ),
            "blocked": (
                scraper._NcaaFailureEvidenceError(
                    section="batting",
                    source_url=(
                        "https://proxy-user:proxy-password@stats.ncaa.org/blocked"
                        "?token=secret#private"
                    ),
                    html=malformed,
                    failure_category="source_blocked",
                ),
                DeterministicJobError,
                "retry_exhausted",
                "source_blocked",
            ),
        }
        for label, (failure, error_type, job_category, evidence_category) in cases.items():
            with self.subTest(case=label):
                clean_database(self.database_url)
                job, _organization_id, _report_id, _key = self._seed_report(3)
                with self.Session.begin() as session:
                    session.get(ReportJob, job.id).max_attempts = 1
                patch_value = (
                    patch.object(scraper, "_fetch_ncaa_pages", return_value=failure)
                    if isinstance(failure, dict)
                    else patch.object(scraper, "_fetch_ncaa_pages", side_effect=failure)
                )
                with patch_value:
                    with self.assertRaises(error_type) as raised:
                        run_report_job(
                            self.Session,
                            job.id,
                            collector=lambda job_id, cached_sections=frozenset(): (
                                collect_ncaa_sections(
                                    self.Session,
                                    job_id,
                                    cached_sections=cached_sections,
                                )
                            ),
                        )
                self.assertEqual(raised.exception.category, job_category)
                with self.Session() as session:
                    stored = session.get(ReportJob, job.id)
                    evidence = session.scalar(select(FailureEvidence))
                    self.assertEqual(stored.status, "failed")
                    self.assertEqual(stored.failure_category, job_category)
                    self.assertNotIn("SENSITIVE-HTML", stored.failure_message or "")
                    self.assertIsNone(session.scalar(select(ReportSnapshot).where(
                        ReportSnapshot.status.in_(("ready", "partial"))
                    )))
                    self.assertIsNotNone(evidence)
                    raw_html = gzip.decompress(evidence.compressed_html)
                    self.assertLessEqual(len(raw_html), scraper.FAILURE_EVIDENCE_MAX_BYTES)
                    self.assertNotIn("@", evidence.source_url or "")
                    self.assertNotIn("?", evidence.source_url or "")
                    self.assertNotIn("#", evidence.source_url or "")
                    evidence_id = evidence.id
                    checksum = evidence.sha256
                    expires_at = evidence.expires_at
                self.assertEqual(
                    purge_expired_evidence(self.Session, now=expires_at), 1
                )
                with self.Session() as session:
                    purged = session.get(FailureEvidence, evidence_id)
                    self.assertIsNone(purged.compressed_html)
                    self.assertEqual(purged.sha256, checksum)
                    self.assertEqual(purged.failure_category, evidence_category)

    def test_interrupted_source_first_write_recovers_without_second_collection(self):
        from roster_scout.jobs import (
            collect_ncaa_sections,
            reconcile_stale_report_jobs,
            run_report_job,
        )
        from roster_scout.models import ReportJob, ReportSnapshot, SourceRecord

        job, organization_id, _report_id, _key = self._seed_report(1)
        pages = {
            "batting": fixture("d1_batting.html"),
            "pitching": fixture("d1_pitching.html"),
        }
        with patch.object(
            scraper, "_fetch_ncaa_pages", return_value=pages
        ) as fetch, patch(
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
        fetch.assert_called_once()
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            original_sources = {
                row.id: (row.collected_at, row.payload_sha256, row.raw_values)
                for row in session.scalars(select(SourceRecord))
            }
            self.assertEqual(stored.attempt_count, 1)
            self.assertTrue(original_sources)
            self.assertIsNone(session.scalar(select(ReportSnapshot).where(
                ReportSnapshot.status.in_(("ready", "partial"))
            )))
            started_at = stored.started_at

        reconciled = reconcile_stale_report_jobs(
            self.Session,
            now=started_at + timedelta(hours=1),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(reconciled.reopened, 1)

        collector = Mock(side_effect=AssertionError("recovery collected twice"))
        snapshot = run_report_job(self.Session, job.id, collector=collector)
        self.assertEqual(snapshot.status, "ready")
        collector.assert_not_called()
        release = self._release_state(job.id, organization_id, snapshot.id)
        self.assertEqual(release["source_fingerprints"], original_sources)
        with self.Session() as session:
            self.assertEqual(session.get(ReportJob, job.id).attempt_count, 1)

    def test_existing_scrape_route_keeps_the_tuple_contract_and_two_sheet_workbook(self):
        import app as app_module

        app_module.app.config.update(TESTING=True)
        client = app_module.app.test_client()
        for division in (1, 2, 3):
            with self.subTest(division=division):
                batting = scraper._parse_ncaa_stat_table(
                    fixture(f"d{division}_batting.html"), section="batting"
                )
                pitching = scraper._parse_ncaa_stat_table(
                    fixture(f"d{division}_pitching.html"), section="pitching"
                )
                players = scraper._normalize_ncaa_players(batting, pitching)
                source = TEAMS[division]
                with patch.object(
                    app_module,
                    "scrape_ncaa_by_id",
                    return_value=(players, source["team_name"]),
                ) as scrape_by_id, patch.object(
                    app_module, "check_request_limit", return_value=None
                ):
                    response = client.post("/scrape", json={
                        "team": source["team_name"],
                        "display_name": source["team_name"],
                        "division": f"d{division}",
                        "season_team_id": source["season_team_id"],
                    })
                self.assertEqual(response.status_code, 200)
                scrape_by_id.assert_called_once_with(
                    source["season_team_id"], division=f"d{division}"
                )
                with zipfile.ZipFile(io.BytesIO(response.data)) as workbook:
                    workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
                self.assertEqual(workbook_xml.count("<sheet "), 2)
                self.assertIn('name="Hitters"', workbook_xml)
                self.assertIn('name="Pitchers"', workbook_xml)


if __name__ == "__main__":
    unittest.main()
