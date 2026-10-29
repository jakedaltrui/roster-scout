#!/usr/bin/env python3
"""Validate every current-season NCAA team selector record in one pass."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roster_scraper import validate_ncaa_team_catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    args = parser.parse_args()
    report = validate_ncaa_team_catalog(year=args.year)
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
