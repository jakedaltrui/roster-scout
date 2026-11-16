#!/usr/bin/env python3
"""Deterministic source and isolation gate for the Roster Scout PBIP project."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "powerbi" / "RosterScout.pbip"
REPORT = ROOT / "powerbi" / "RosterScout.Report"
MODEL = ROOT / "powerbi" / "RosterScout.SemanticModel" / "definition"
MIGRATION = ROOT / "migrations" / "versions" / "0026_power_bi_analytics_views.py"
EXPECTED_PAGES = (
    "Overview", "Roster", "Hitting", "Pitching", "Fielding", "Splits",
    "Player Detail", "Data Quality",
)
EXPECTED_TABLES = (
    "Snapshot", "Player", "Batting", "Pitching", "Fielding", "Scope",
    "Metrics", "Scoped Batting", "Scoped Pitching", "Scoped Fielding",
    "Career Season", "Game Log", "Data Quality", "Source",
)
EXPECTED_VIEWS = {
    "pbi_snapshots", "pbi_players", "pbi_batting", "pbi_pitching",
    "pbi_fielding", "pbi_metrics", "pbi_scopes", "pbi_scoped_batting",
    "pbi_scoped_pitching", "pbi_scoped_fielding", "pbi_career_seasons",
    "pbi_game_logs", "pbi_data_quality", "pbi_sources",
}
EXPECTED_MEASURES = {
    "AVG", "OBP", "SLG", "OPS", "ISO", "BB%", "K%", "BABIP",
    "ERA", "WHIP", "K/9", "BB/9", "H/9", "HR/9", "K/BB",
    "Fielding %", "wOBA", "FIP",
    "Dashboard OPS", "Dashboard ISO", "Dashboard BB%", "Dashboard K%",
    "Dashboard BABIP", "Dashboard ERA", "Dashboard WHIP", "Dashboard K/9",
    "Dashboard BB/9", "Dashboard H/9", "Dashboard HR/9", "Dashboard K/BB",
    "Dashboard Fielding %",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def load_migration():
    spec = importlib.util.spec_from_file_location("power_bi_views_0026", MIGRATION)
    if spec is None or spec.loader is None:
        fail("could not load Power BI migration")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_report() -> None:
    metadata = json.loads((REPORT / "definition/pages/pages.json").read_text())
    pages = []
    for page_id in metadata["pageOrder"]:
        page_dir = REPORT / "definition/pages" / page_id
        page = json.loads((page_dir / "page.json").read_text())
        pages.append(page["displayName"])
        visuals = list((page_dir / "visuals").glob("*/visual.json"))
        if not visuals:
            fail(f"Power BI page has no visual: {page['displayName']}")
        for visual in visuals:
            json.loads(visual.read_text())
    if tuple(pages) != EXPECTED_PAGES:
        fail(f"unexpected Power BI page order: {pages}")


def validate_model() -> None:
    table_files = {path.stem: path for path in (MODEL / "tables").glob("*.tmdl")}
    if set(table_files) != set(EXPECTED_TABLES):
        fail(f"unexpected semantic-model tables: {sorted(table_files)}")
    role = (MODEL / "roles/RosterScoutSnapshotReader.tmdl").read_text()
    if "CUSTOMDATA()" not in role or "modelPermission: read" not in role:
        fail("Power BI RLS role is missing CUSTOMDATA read isolation")
    for table in EXPECTED_TABLES:
        escaped = re.escape(table)
        permission = re.compile(
            rf"^\s*tablePermission\s+(?:'{escaped}'|{escaped})\s*=\s*"
            rf"(?:'{escaped}'|{escaped})\[security_key\]\s*=\s*CUSTOMDATA\(\)\s*$",
            re.M,
        )
        if not permission.search(role):
            fail(f"RLS is not independently applied to {table}")
        contents = table_files[table].read_text()
        if "column security_key" not in contents:
            fail(f"semantic-model table lacks security_key: {table}")
        if "CONFIGURE_IN_POWER_BI_SERVICE" in contents:
            fail(f"placeholder leaked into table definition: {table}")
    all_model = "\n".join(path.read_text() for path in MODEL.rglob("*.tmdl"))
    measures = set(re.findall(r"^\s*measure\s+(?:'([^']+)'|([^=\s]+))\s*=", all_model, re.M))
    normalized = {left or right for left, right in measures}
    missing = EXPECTED_MEASURES - normalized
    if missing:
        fail(f"semantic model is missing measures: {sorted(missing)}")
    for relationship in (
        "Player_ScopedBatting", "Player_ScopedPitching", "Player_ScopedFielding",
        "Scope_ScopedBatting", "Scope_ScopedPitching", "Scope_ScopedFielding",
    ):
        if f"relationship {relationship}" not in all_model:
            fail(f"semantic model is missing relationship: {relationship}")


def validate_views() -> None:
    migration = load_migration()
    if set(migration.VIEW_SQL) != EXPECTED_VIEWS:
        fail("Power BI view set changed without updating the release contract")
    for name, sql in migration.VIEW_SQL.items():
        if "security_key" not in sql:
            fail(f"analytics view lacks security_key: {name}")
        if name != "pbi_snapshots" and "JOIN pbi_snapshots" not in sql:
            fail(f"analytics view can bypass published-snapshot boundary: {name}")
    source_sql = migration.VIEW_SQL["pbi_sources"]
    if "raw_values" in source_sql or "response_body" in source_sql:
        fail("raw source payloads must not enter Power BI")


def validate_no_secrets() -> None:
    forbidden = (
        "POWER_BI_CLIENT_SECRET=", "client_secret\"", "password=",
        "postgresql://", "postgres://",
    )
    for path in (ROOT / "powerbi").rglob("*"):
        if path.is_file():
            contents = path.read_text(errors="ignore").lower()
            for token in forbidden:
                if token.lower() in contents:
                    fail(f"possible credential in source-controlled Power BI project: {path}")


def validate_pbir_schema() -> None:
    result = subprocess.run(
        [
            "npx", "--yes", "@microsoft/powerbi-report-authoring-cli@latest",
            "validate", str(PROJECT), "--format", "text",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        fail("Microsoft PBIR validation failed")


def main() -> int:
    validate_report()
    validate_model()
    validate_views()
    validate_no_secrets()
    validate_pbir_schema()
    print("Power BI project gate passed: 8 pages, 14 RLS-bound tables, 14 tenant views.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"Power BI project gate failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
