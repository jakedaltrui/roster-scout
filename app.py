#!/usr/bin/env python3
"""Axis Baseball Roster Scout web app.

Accept a team and division, collect the roster and season statistics, and
return a formatted Excel workbook containing Hitters and Pitchers worksheets.
"""

import io
import os
import re
from datetime import date

import xlsxwriter
from flask import Flask, jsonify, render_template, request, send_file

from roster_scraper import (
    HITTER_HEADERS,
    PITCHER_HEADERS,
    NcaaError,
    list_ncaa_teams,
    scrape_by_team_name,
    scrape_juco_by_name,
    scrape_ncaa_by_id,
    scrape_ncaa_by_name,
    split_roster,
)


app = Flask(__name__)

_INTEGER_COLUMNS = {
    "#", "AB", "H", "2B", "HR", "RBI", "R", "BB", "SO", "SB",
    "W", "L", "SV", "ER",
}
_THREE_DECIMAL_COLUMNS = {"AVG", "OBP", "SLG"}
_TWO_DECIMAL_COLUMNS = {"ERA", "WHIP"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/api/teams")
def teams():
    division = (request.args.get("division") or "").strip().lower()
    try:
        catalog = list_ncaa_teams(division)
        year = catalog[0]["year"] if catalog else None
        return jsonify({"division": division, "year": year, "teams": catalog})
    except NcaaError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/scrape", methods=["POST"])
def scrape():
    body = request.get_json(silent=True) or {}
    team = (body.get("team") or "").strip()
    division = (body.get("division") or "").strip().lower()
    season_team_id = str(body.get("season_team_id") or "").strip()

    if not team and not season_team_id:
        return jsonify({"error": "Please enter a team name."}), 400

    try:
        if division == "juco":
            rows, team_label = scrape_juco_by_name(team)
        elif division in {"d1", "d2", "d3"}:
            try:
                if season_team_id:
                    rows, team_label = scrape_ncaa_by_id(
                        season_team_id,
                        division=division,
                    )
                else:
                    rows, team_label = scrape_ncaa_by_name(team, division=division)
            except NcaaError as ncaa_exc:
                if division != "d1":
                    raise
                try:
                    rows, team_label = scrape_by_team_name(team)
                except ValueError:
                    raise ncaa_exc
        else:
            # Backwards-compatible auto-detection for clients that omit a division.
            try:
                rows, team_label = scrape_ncaa_by_name(team)
            except NcaaError as ncaa_exc:
                try:
                    rows, team_label = scrape_by_team_name(team)
                except ValueError:
                    try:
                        rows, team_label = scrape_juco_by_name(team)
                    except ValueError:
                        raise ncaa_exc

        hitters, pitchers = split_roster(rows)
        workbook_bytes = _create_workbook(hitters, pitchers)
        filename = f"{_safe_filename(team_label)}_Baseball_{date.today().isoformat()}.xlsx"

        response = send_file(
            workbook_bytes,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            max_age=0,
        )
        response.headers["X-Roster-Scout-Players"] = str(len(rows))
        response.headers["X-Roster-Scout-Hitters"] = str(len(hitters))
        response.headers["X-Roster-Scout-Pitchers"] = str(len(pitchers))
        return response

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _safe_filename(value: str) -> str:
    """Return a readable filename component without path or shell characters."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return cleaned or "Roster"


def _excel_value(header: str, value):
    """Convert stat strings to useful Excel number types where semantics allow."""
    if value is None or value == "":
        return ""
    text = str(value).strip()
    try:
        if header in _INTEGER_COLUMNS and re.fullmatch(r"-?\d+", text):
            return int(text)
        if header in _THREE_DECIMAL_COLUMNS | _TWO_DECIMAL_COLUMNS:
            return float(text)
    except ValueError:
        pass
    # IP intentionally remains text: 7.2 means seven and two-thirds innings.
    return text


def _write_worksheet(workbook, title: str, rows: list[dict], headers: list[str]):
    worksheet = workbook.add_worksheet(title)
    worksheet.hide_gridlines(2)
    worksheet.freeze_panes(1, 0)

    header_format = workbook.add_format({
        "bold": True,
        "font_color": "#FFFFFF",
        "bg_color": "#1E3A5F",
        "align": "center",
        "valign": "vcenter",
    })
    text_format = workbook.add_format({"valign": "vcenter"})
    integer_format = workbook.add_format({"num_format": "0", "valign": "vcenter"})
    three_decimal_format = workbook.add_format({"num_format": "0.000", "valign": "vcenter"})
    two_decimal_format = workbook.add_format({"num_format": "0.00", "valign": "vcenter"})

    worksheet.set_row(0, 24)
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, header_format)

    for row_index, player in enumerate(rows, start=1):
        for col_index, header in enumerate(headers):
            value = _excel_value(header, player.get(header, ""))
            cell_format = text_format
            if header in _INTEGER_COLUMNS and isinstance(value, int):
                cell_format = integer_format
            elif header in _THREE_DECIMAL_COLUMNS and isinstance(value, float):
                cell_format = three_decimal_format
            elif header in _TWO_DECIMAL_COLUMNS and isinstance(value, float):
                cell_format = two_decimal_format
            worksheet.write(row_index, col_index, value, cell_format)

    if rows:
        worksheet.add_table(0, 0, len(rows), len(headers) - 1, {
            "columns": [{"header": header} for header in headers],
            "style": "Table Style Medium 2",
        })
    else:
        worksheet.autofilter(0, 0, 0, len(headers) - 1)

    for col_index, header in enumerate(headers):
        content_width = max(
            [len(header)] + [len(str(player.get(header, "") or "")) for player in rows]
        )
        if header == "Name":
            width = min(max(content_width + 2, 20), 34)
        elif header in {"Position", "Class"}:
            width = min(max(content_width + 2, 10), 18)
        else:
            width = min(max(content_width + 2, 7), 12)
        worksheet.set_column(col_index, col_index, width)


def _create_workbook(hitters: list[dict], pitchers: list[dict]) -> io.BytesIO:
    """Build the two-tab Roster Scout workbook entirely in memory."""
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties({
        "title": "Axis Baseball Roster Scout",
        "subject": "Baseball roster and season statistics",
        "author": "Axis Baseball",
        "company": "Axis Baseball",
    })
    _write_worksheet(workbook, "Hitters", hitters, HITTER_HEADERS)
    _write_worksheet(workbook, "Pitchers", pitchers, PITCHER_HEADERS)
    workbook.close()
    output.seek(0)
    return output


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, port=port)
