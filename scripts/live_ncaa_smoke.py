#!/usr/bin/env python3
"""Live, credential-free NCAA smoke test for one team in each division."""

import json
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roster_scraper import scrape_ncaa_by_name, split_roster


TEAMS = [("d1", "Florida State"), ("d2", "Tampa"), ("d3", "Scranton")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--division", choices=("d1", "d2", "d3"))
    parser.add_argument("--team")
    args = parser.parse_args()
    teams = [(args.division, args.team)] if args.division and args.team else TEAMS

    results = []
    for division, team in teams:
        started = time.monotonic()
        try:
            players, label = scrape_ncaa_by_name(team, division=division, force_refresh=True)
            hitters, pitchers = split_roster(players)
            results.append({
                "division": division,
                "team": label,
                "status": "ok",
                "players": len(players),
                "hitters": len(hitters),
                "pitchers": len(pitchers),
                "seconds": round(time.monotonic() - started, 3),
            })
        except Exception as exc:
            results.append({
                "division": division,
                "team": team,
                "status": "error",
                "error": str(exc),
                "seconds": round(time.monotonic() - started, 3),
            })
    print(json.dumps(results, indent=2))
    return 0 if all(result["status"] == "ok" for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
