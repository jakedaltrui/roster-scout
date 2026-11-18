#!/usr/bin/env python3
"""Build a deterministic visual preview with the production workbook renderer."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from roster_scout.workbooks import create_workbook, workbook_branding


def _rows():
    hitters = []
    for index, name in enumerate((
        "Evan Rogers",
        "Dylan Biddick",
        "Kruise Newman",
        "Eli Tidwell",
        "Owen Smith",
        "Miles Curley",
        "Dathan Cummings",
        "Adrian Dominguez",
    ), 1):
        at_bats = 220 - index * 9
        hits = 74 - index * 4
        doubles = 15 - index // 2
        triples = index % 3
        home_runs = 14 - index
        walks = 20 + index * 2
        strikeouts = 32 + index * 4
        pa = at_bats + walks + 4
        avg = hits / at_bats
        slg = (hits - doubles - triples - home_runs + 2 * doubles + 3 * triples + 4 * home_runs) / at_bats
        obp = (hits + walks + 2) / (at_bats + walks + 4)
        preview_woba = (
            0.70 * (walks + 2)
            + 0.90 * (hits - doubles - triples - home_runs)
            + 1.25 * doubles
            + 1.60 * triples
            + 2.00 * home_runs
        ) / pa
        hitters.append({
            "_player_id": f"PREVIEW-474-H{index:02d}",
            "_player_url": f"https://stats.ncaa.org/players/preview-h{index:02d}",
            "Name": name,
            "#": index + 2,
            "Position": ("INF", "OF", "C", "UT")[index % 4],
            "Class": ("Jr", "Sr", "So")[index % 3],
            "G": 48 - index,
            "GS": 44 - index,
            "PA": pa,
            "AB": at_bats,
            "H": hits,
            "2B": doubles,
            "3B": triples,
            "HR": home_runs,
            "RBI": 52 - index * 2,
            "R": 45 - index,
            "BB": walks,
            "SO": strikeouts,
            "SB": index + 2,
            "AVG": f"{avg:.3f}",
            "OBP": f"{obp:.3f}",
            "SLG": f"{slg:.3f}",
            "OPS": f"{obp + slg:.3f}",
            "ISO": f"{slg - avg:.3f}",
            "BB%": f"{walks / pa:.3f}",
            "K%": f"{strikeouts / pa:.3f}",
            "BABIP": f"{(hits - home_runs) / (at_bats - strikeouts - home_runs + 2):.3f}",
            "wOBA": f"{preview_woba:.3f}",
            "Last 10 G": 10,
            "Last 10 PA": 39 + index,
            "Last 10 AB": 34 + index,
            "Last 10 AVG": f"{max(0.150, avg + (index - 4) * 0.008):.3f}",
            "Last 10 OPS": f"{max(0.450, obp + slg + (index - 4) * 0.014):.3f}",
            "Last 10 K%": f"{max(0.050, strikeouts / pa - (index - 4) * 0.006):.3f}",
            "Last 10 Status": "Complete",
            "Conference G": 18,
            "Conference PA": 74 + index,
            "Conference AB": 63 + index,
            "Conference AVG": f"{max(0.150, avg - (index - 4) * 0.006):.3f}",
            "Conference OPS": f"{max(0.450, obp + slg - (index - 4) * 0.010):.3f}",
            "Conference K%": f"{max(0.050, strikeouts / pa + (index - 4) * 0.004):.3f}",
            "Conference Status": "Complete",
        })

    pitchers = []
    for index, name in enumerate((
        "Riley Marcotte",
        "Bryson Goff",
        "William Andrade",
        "Luke Lycette",
        "Lane Alack",
        "Jack Garvey",
        "Reid Leonard",
        "Skylar Jones",
    ), 1):
        innings = 68 - index * 4
        strikeouts = 62 - index * 2
        walks = 18 + index
        hits = 48 + index * 2
        earned_runs = 19 + index * 2
        era = earned_runs * 9 / innings
        whip = (walks + hits) / innings
        pitchers.append({
            "_player_id": f"PREVIEW-474-P{index:02d}",
            "_player_url": f"https://stats.ncaa.org/players/preview-p{index:02d}",
            "Name": name,
            "#": index + 20,
            "Position": "RHP" if index % 2 else "LHP",
            "Class": ("Jr", "Sr", "So")[index % 3],
            "G": 18 + index,
            "GS": max(0, 12 - index),
            "IP": f"{innings}.0",
            "W": max(0, 7 - index // 2),
            "L": index // 3,
            "SV": max(0, index - 5),
            "H": hits,
            "ER": earned_runs,
            "BB": walks,
            "SO": strikeouts,
            "HR": 3 + index // 2,
            "BF": innings * 4 + 18,
            "ERA": f"{era:.2f}",
            "WHIP": f"{whip:.2f}",
            "K/9": f"{strikeouts * 9 / innings:.2f}",
            "BB/9": f"{walks * 9 / innings:.2f}",
            "H/9": f"{hits * 9 / innings:.2f}",
            "HR/9": f"{(3 + index // 2) * 9 / innings:.2f}",
            "K/BB": f"{strikeouts / walks:.2f}",
            "K%": f"{strikeouts / (innings * 4 + 18):.3f}",
            "BB%": f"{walks / (innings * 4 + 18):.3f}",
            "Last 10 G": 10,
            "Last 10 IP": f"{min(innings, 28)}.0",
            "Last 10 ERA": f"{max(0.50, era + (index - 4) * 0.18):.2f}",
            "Last 10 WHIP": f"{max(0.60, whip + (index - 4) * 0.03):.2f}",
            "Last 10 K/9": f"{max(1.0, strikeouts * 9 / innings - (index - 4) * 0.15):.2f}",
            "Last 10 Status": "Complete",
            "Conference G": 8 + index,
            "Conference IP": f"{min(innings, 31)}.0",
            "Conference ERA": f"{max(0.50, era - (index - 4) * 0.12):.2f}",
            "Conference WHIP": f"{max(0.60, whip - (index - 4) * 0.02):.2f}",
            "Conference K/9": f"{max(1.0, strikeouts * 9 / innings + (index - 4) * 0.10):.2f}",
            "Conference Status": "Complete",
        })
    return hitters, pitchers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--with-logo", action="store_true")
    arguments = parser.parse_args()
    output = Path(arguments.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    hitters, pitchers = _rows()
    branding = workbook_branding(
        school_name="University of New Orleans",
        generated_on=date(2026, 11, 16),
        team_id="474" if arguments.with_logo else None,
        subtitle="2026 SEASON · SIMULATED PREVIEW · DETAILED TWO-SHEET FORMAT",
    )
    payload = create_workbook(hitters, pitchers, branding=branding).getvalue()
    output.write_bytes(payload)
    print(output)


if __name__ == "__main__":
    main()
