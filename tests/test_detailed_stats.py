"""Golden contracts for auditable detailed NCAA season metrics."""

from __future__ import annotations

from decimal import Decimal
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFINITION_VERSION = "ncaa-detailed-1.0"


def metric_api():
    return importlib.import_module("roster_scout.metrics")


def assert_available(testcase, result, expected):
    testcase.assertEqual(result.status, "available")
    testcase.assertIsNone(result.reason_code)
    testcase.assertEqual(result.value, Decimal(expected))
    testcase.assertEqual(result.definition_version, DEFINITION_VERSION)


class HitterMetricTests(unittest.TestCase):
    counts = {
        "at_bats": 10,
        "hits": 4,
        "doubles": 1,
        "triples": 1,
        "home_runs": 1,
        "walks": 2,
        "hit_by_pitch": 1,
        "strikeouts": 2,
        "sacrifice_flies": 1,
        "sacrifice_hits": 1,
    }

    def test_detailed_metric_registry_contract(self):
        """RED sentinel: production imports work and only the registry is absent."""
        self.assertIsNotNone(
            importlib.util.find_spec("roster_scout.metrics"),
            "detailed metric registry is not implemented",
        )

    def test_hitter_formula_table_uses_exact_decimal_counts(self):
        calculate_metric = metric_api().calculate_metric
        cases = {
            "pa": "15",
            "avg": ".4",
            "obp": ".5",
            "slg": "1",
            "ops": "1.5",
            "iso": ".6",
            "batting_bb_pct": str(Decimal(2) / Decimal(15)),
            "batting_k_pct": str(Decimal(2) / Decimal(15)),
            "babip": ".375",
            "singles": "1",
            "total_bases": "10",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                result = calculate_metric(code, self.counts)
                assert_available(self, result, expected)
                self.assertEqual(result.constant_set_version, None)
                self.assertEqual(result.sample["pa"], 15)
                self.assertEqual(result.sample["ab"], 10)
                self.assertIsNone(result.sample["bf"])
                self.assertIsNone(result.sample["outs"])
                self.assertTrue(result.inputs)

    def test_count_first_aggregation_never_averages_source_rates(self):
        metrics = metric_api()
        aggregated = metrics.aggregate_compatible_counts(
            (
                {"at_bats": 1, "hits": 1},
                {"at_bats": 9, "hits": 0},
            )
        )
        result = metrics.calculate_metric("avg", aggregated)
        assert_available(self, result, ".1")
        self.assertNotEqual(result.value, Decimal(".5"))


class PitcherMetricTests(unittest.TestCase):
    counts = {
        "innings_outs": 21,
        "hits_allowed": 6,
        "earned_runs": 2,
        "walks": 3,
        "strikeouts": 9,
        "home_runs_allowed": 1,
        "hit_batters": 1,
        "batters_faced": 30,
        "opponent_at_bats": 26,
        "opponent_sacrifice_flies": 1,
    }

    def test_pitcher_formula_table_uses_outs_not_decimal_innings(self):
        calculate_metric = metric_api().calculate_metric
        cases = {
            "era": str(Decimal(54) / Decimal(21)),
            "whip": str(Decimal(27) / Decimal(21)),
            "k_per_9": str(Decimal(243) / Decimal(21)),
            "bb_per_9": str(Decimal(81) / Decimal(21)),
            "h_per_9": str(Decimal(162) / Decimal(21)),
            "hr_per_9": str(Decimal(27) / Decimal(21)),
            "k_bb": "3",
            "pitching_k_pct": ".3",
            "pitching_bb_pct": ".1",
            "babip_allowed": str(Decimal(5) / Decimal(17)),
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                result = calculate_metric(code, self.counts)
                assert_available(self, result, expected)
                self.assertEqual(result.sample["bf"], 30)
                self.assertEqual(result.sample["outs"], 21)
                self.assertIsNone(result.sample["pa"])
                self.assertIsNone(result.sample["ab"])

    def test_negative_or_impossible_counts_are_incompatible(self):
        calculate_metric = metric_api().calculate_metric
        cases = (
            ("era", {**self.counts, "earned_runs": -1}),
            ("babip_allowed", {**self.counts, "home_runs_allowed": 7}),
        )
        for code, counts in cases:
            with self.subTest(code=code):
                result = calculate_metric(code, counts)
                self.assertIsNone(result.value)
                self.assertEqual(result.status, "incompatible_input")
                self.assertEqual(result.reason_code, "incompatible_input")


class DenominatorSemanticsTests(unittest.TestCase):
    def test_zero_missing_undefined_and_incompatible_are_distinct(self):
        calculate_metric = metric_api().calculate_metric
        cases = (
            (
                "available zero",
                "avg",
                {"hits": 0, "at_bats": 4},
                "available",
                Decimal("0"),
            ),
            (
                "undefined denominator",
                "avg",
                {"hits": 0, "at_bats": 0},
                "undefined_denominator",
                None,
            ),
            (
                "missing input",
                "avg",
                {"hits": None, "at_bats": 4},
                "missing_input",
                None,
            ),
            (
                "incompatible input",
                "singles",
                {"hits": 1, "doubles": 1, "triples": 1, "home_runs": 0},
                "incompatible_input",
                None,
            ),
        )
        observed = {}
        for label, code, counts, status, value in cases:
            with self.subTest(label=label):
                result = calculate_metric(code, counts)
                observed[label] = result
                self.assertEqual(result.status, status)
                self.assertEqual(result.value, value)
                self.assertEqual(result.inputs, counts)
        self.assertEqual(observed["available zero"].value, Decimal("0"))
        self.assertIsNone(observed["missing input"].value)
        self.assertNotEqual(
            observed["available zero"].value,
            observed["missing input"].value,
        )

    def test_nonpositive_derived_denominator_is_not_a_zero_rate(self):
        result = metric_api().calculate_metric(
            "babip",
            {
                "hits": 1,
                "home_runs": 0,
                "at_bats": 1,
                "strikeouts": 1,
                "sacrifice_flies": 0,
            },
        )
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "undefined_denominator")


class ConstantMetricTests(unittest.TestCase):
    def test_woba(self):
        result = metric_api().calculate_metric(
            "woba",
            {
                "at_bats": 10,
                "hits": 4,
                "doubles": 1,
                "triples": 0,
                "home_runs": 1,
                "walks": 2,
                "hit_by_pitch": 0,
                "sacrifice_flies": 1,
            },
            season_id="2026",
            division="d1",
        )
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "unavailable_constant")
        self.assertEqual(result.reason_code, "verified_ncaa_constant_required")
        self.assertIsNone(result.constant_set_version)
        self.assertNotIn("mlb", repr(result).casefold())

    def test_fip(self):
        result = metric_api().calculate_metric(
            "fip",
            {
                "home_runs_allowed": 1,
                "walks": 2,
                "hit_batters": 1,
                "strikeouts": 7,
                "innings_outs": 18,
            },
            season_id="2026",
            division="d2",
        )
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "unavailable_constant")
        self.assertEqual(result.reason_code, "verified_ncaa_constant_required")
        self.assertIsNone(result.constant_set_version)
        self.assertNotIn("mlb", repr(result).casefold())

    def test_unverified_or_wrong_scope_constants_are_never_used(self):
        calculate_metric = metric_api().calculate_metric
        base = {
            "home_runs_allowed": 1,
            "walks": 2,
            "hit_batters": 1,
            "strikeouts": 7,
            "innings_outs": 18,
        }
        constant = {
            "metric_code": "fip",
            "version": "ncaa-2026-d1-v1",
            "season_id": "2026",
            "division": "d1",
            "verification_status": "pending",
            "values": {"constant": Decimal("3.2")},
        }
        for label, candidate in (
            ("pending", constant),
            ("wrong division", {**constant, "verification_status": "verified"}),
        ):
            with self.subTest(label=label):
                result = calculate_metric(
                    "fip",
                    base,
                    season_id="2026",
                    division="d2",
                    constant_set=candidate,
                )
                self.assertEqual(result.status, "unavailable_constant")
                self.assertIsNone(result.value)

    def test_missing_constant_precedes_incomplete_count_inputs(self):
        for code in ("woba", "fip"):
            with self.subTest(code=code):
                result = metric_api().calculate_metric(
                    code,
                    {},
                    season_id="2026",
                    division="d3",
                )
                self.assertEqual(result.status, "unavailable_constant")
                self.assertEqual(
                    result.reason_code, "verified_ncaa_constant_required"
                )


class MethodologyPersistenceTests(unittest.TestCase):
    def test_metric_result_retains_recomputation_lineage(self):
        result = metric_api().calculate_metric(
            "avg", {"hits": 0, "at_bats": 4}
        )
        self.assertEqual(result.inputs, {"hits": 0, "at_bats": 4})
        self.assertEqual(
            result.sample,
            {"pa": None, "ab": 4, "bf": None, "outs": None},
        )
        self.assertEqual(result.definition_version, DEFINITION_VERSION)
        self.assertIsNone(result.constant_set_version)

    def test_metric_models_expose_status_inputs_sample_and_constant_provenance(self):
        from roster_scout.models import MetricConstantSet, MetricValue

        columns = set(MetricValue.__table__.c.keys())
        self.assertTrue(
            {
                "status",
                "reason_code",
                "inputs_json",
                "sample_json",
                "scope",
                "constant_set_id",
            }.issubset(columns)
        )
        constant_columns = set(MetricConstantSet.__table__.c.keys())
        self.assertTrue(
            {
                "metric_definition_id",
                "metric_code",
                "version",
                "season_id",
                "division",
                "constant_type",
                "values_json",
                "methodology",
                "source_url",
                "source_identifier",
                "collected_at",
                "payload_sha256",
                "verification_status",
                "verified_at",
                "verified_by_account_id",
            }.issubset(constant_columns)
        )

    def test_migration_seeds_no_guessed_constants(self):
        migration = (
            PROJECT_ROOT
            / "migrations"
            / "versions"
            / "0025_detailed_ncaa_intelligence.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("INSERT INTO METRIC_CONSTANT_SETS", migration.upper())

    def test_downgrade_backfill_temporarily_suspends_immutable_row_guard(self):
        migration = (
            PROJECT_ROOT
            / "migrations"
            / "versions"
            / "0025_detailed_ncaa_intelligence.py"
        ).read_text(encoding="utf-8")
        disable = migration.index(
            "ALTER TABLE metric_values DISABLE TRIGGER "
            "trg_metric_values_immutable"
        )
        backfill = migration.index(
            "SET unavailable_reason = COALESCE(unavailable_reason, reason_code)"
        )
        enable = migration.index(
            "ALTER TABLE metric_values ENABLE TRIGGER "
            "trg_metric_values_immutable"
        )
        self.assertLess(disable, backfill)
        self.assertLess(backfill, enable)

    def test_database_preflight_rejects_unsafe_targets_without_disclosure(self):
        script = PROJECT_ROOT / "scripts" / "prepare_phase3_test_database.py"
        environment = dict(os.environ)
        environment.update(
            {
                "DATABASE_URL": "postgresql+psycopg://private:secret@db.example/production",
                "PHASE2_DESTRUCTIVE_DATABASE": "local-disposable",
            }
        )
        completed = subprocess.run(
            [sys.executable, str(script), "--ensure"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        combined = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("phase3_test_database_unavailable", combined)
        self.assertNotIn("private", combined)
        self.assertNotIn("secret", combined)
        self.assertNotIn("db.example", combined)


if __name__ == "__main__":
    unittest.main()
