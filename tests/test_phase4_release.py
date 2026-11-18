"""Deterministic release acceptance for the detailed two-sheet workbook."""

from __future__ import annotations

import io
import unittest
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from hashlib import sha256

from PIL import Image

from roster_scout.workbooks import HITTER_COLUMNS, PITCHER_COLUMNS, create_workbook


XML_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _sample_rows():
    hitter = {
        "_player_id": "100",
        "_player_url": "https://stats.ncaa.org/players/100",
        "Name": "Complete Two Way",
        "#": "12",
        "Position": "OF/RHP",
        "Class": "Jr",
        "G": 32,
        "GS": 31,
        "PA": 140,
        "AB": 119,
        "H": 41,
        "2B": 8,
        "3B": 2,
        "HR": 7,
        "RBI": 34,
        "R": 29,
        "BB": 16,
        "SO": 20,
        "SB": 9,
        "AVG": "0.345",
        "OBP": "0.421",
        "SLG": "0.622",
        "OPS": "1.043",
        "ISO": "0.277",
        "BB%": "0.114",
        "K%": "0.143",
        "BABIP": "0.382",
        "wOBA": "0.401",
        "Last 10 G": 10,
        "Last 10 PA": 44,
        "Last 10 AB": 38,
        "Last 10 AVG": "0.368",
        "Last 10 OPS": "1.101",
        "Last 10 K%": "0.114",
        "Last 10 Status": "Complete",
        "Conference G": 8,
        "Conference PA": 35,
        "Conference AB": 30,
        "Conference AVG": "0.367",
        "Conference OPS": "1.090",
        "Conference K%": "0.114",
    }
    pitcher = {
        "_player_id": "100",
        "_player_url": "https://stats.ncaa.org/players/100",
        "Name": "Complete Two Way",
        "#": "12",
        "Position": "OF/RHP",
        "Class": "Jr",
        "G": 12,
        "GS": 7,
        "IP": "43.2",
        "W": 5,
        "L": 2,
        "SV": 1,
        "H": 37,
        "ER": 14,
        "BB": 12,
        "SO": 51,
        "HR": 3,
        "BF": 183,
        "ERA": "2.89",
        "WHIP": "1.12",
        "K/9": "10.51",
        "BB/9": "2.47",
        "H/9": "7.63",
        "HR/9": "0.62",
        "K/BB": "4.25",
        "K%": "0.279",
        "BB%": "0.066",
        "BABIP": "0.311",
        "Last 10 Status": "Unavailable",
        "Conference G": 7,
        "Conference IP": "21.0",
        "Conference ERA": "2.57",
        "Conference WHIP": "1.05",
        "Conference K/9": "9.88",
    }
    return hitter, pitcher


def _shared_strings(archive):
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.itertext()) for node in root.findall("s:si", XML_NS)]


def _cell(archive, sheet_number, reference):
    strings = _shared_strings(archive)
    root = ET.fromstring(archive.read(f"xl/worksheets/sheet{sheet_number}.xml"))
    cell = root.find(f".//s:c[@r='{reference}']", XML_NS)
    if cell is None:
        return None, None
    value = cell.find("s:v", XML_NS)
    raw = value.text if value is not None else ""
    if cell.get("t") == "s":
        return strings[int(raw)], "string"
    return raw, "number"


class DivisionWorkbookAcceptanceTests(unittest.TestCase):
    def test_d1_d2_d3_share_one_exact_schema_and_value_contract(self):
        hitter, pitcher = _sample_rows()
        schemas = []
        values = []
        for division in ("D1", "D2", "D3"):
            payload = create_workbook(
                [hitter],
                [pitcher],
                branding={
                    "school_name": f"Representative {division}",
                    "generated_on": date(2026, 11, 16),
                    "subtitle": f"2026 SEASON · {division} · READY SNAPSHOT",
                },
            ).getvalue()
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
                hitter_sheet = archive.read(
                    "xl/worksheets/sheet1.xml"
                ).decode("utf-8")
                pitcher_sheet = archive.read(
                    "xl/worksheets/sheet2.xml"
                ).decode("utf-8")
                schemas.append((
                    workbook_xml.count("<sheet "),
                    hitter_sheet.split("<sheetData", 1)[0],
                    pitcher_sheet.split("<sheetData", 1)[0],
                ))
                values.append((
                    _cell(archive, 1, "U5"),
                    _cell(archive, 1, "Z5"),
                    _cell(archive, 1, "AG5"),
                    _cell(archive, 2, "S5"),
                    _cell(archive, 2, "AE5"),
                ))

        self.assertEqual(schemas[0], schemas[1])
        self.assertEqual(schemas[1], schemas[2])
        self.assertEqual(values[0], values[1])
        self.assertEqual(values[1], values[2])
        self.assertEqual(values[0][0], ("1.043", "number"))
        self.assertEqual(values[0][1], ("0.401", "number"))
        self.assertEqual(values[0][2], ("8", "number"))
        self.assertEqual(values[0][3], ("10.51", "number"))
        self.assertEqual(values[0][4][1], "number")
        self.assertAlmostEqual(float(values[0][4][0]), 9.88, places=2)

    def test_workbook_structure_typing_safety_and_bytes_are_deterministic(self):
        hitter, pitcher = _sample_rows()
        hitter["Name"] = "=SAFE TEXT"
        first = create_workbook([hitter], [pitcher]).getvalue()
        second = create_workbook([hitter], [pitcher]).getvalue()
        self.assertEqual(sha256(first).digest(), sha256(second).digest())

        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
            worksheets = b"".join(
                archive.read(name)
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet")
            )
            archive_names = set(archive.namelist())

        self.assertEqual(workbook_xml.count("<sheet "), 2)
        self.assertEqual(len(HITTER_COLUMNS), 38)
        self.assertEqual(len(PITCHER_COLUMNS), 37)
        for label in (
            "Player",
            "Season Stats",
            "Last 10",
            "Conference Stats",
        ):
            self.assertIn(f">{label}<", strings)
        for removed in (
            "NCAA Player ID",
            "Source URL",
            "Derived Metrics",
            "Conference Status",
            "Status",
        ):
            self.assertNotIn(f">{removed}<", strings)
        self.assertIn("=SAFE TEXT", strings)
        self.assertIn(">Games<", strings)
        self.assertNotIn(b"<f>", worksheets)
        self.assertNotIn(b"<hyperlinks", worksheets)
        self.assertIn(b"<autoFilter", worksheets)
        self.assertFalse(any(name.startswith("xl/tables/") for name in archive_names))

    def test_school_logo_is_embedded_in_both_sheets(self):
        hitter, pitcher = _sample_rows()
        logo = io.BytesIO()
        Image.new("RGBA", (32, 32), (0, 71, 171, 255)).save(logo, format="PNG")
        payload = create_workbook(
            [hitter],
            [pitcher],
            branding={
                "school_name": "Logo University",
                "generated_on": date(2026, 11, 16),
                "logo_png": logo.getvalue(),
            },
        ).getvalue()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
        self.assertIn("xl/media/image1.png", names)
        self.assertIn("xl/drawings/drawing1.xml", names)
        self.assertIn("xl/drawings/drawing2.xml", names)

    def test_long_school_name_and_title_rows_stay_inside_frozen_panes(self):
        hitter, pitcher = _sample_rows()
        logo = io.BytesIO()
        Image.new("RGBA", (32, 32), (0, 71, 171, 255)).save(logo, format="PNG")
        payload = create_workbook(
            [hitter],
            [pitcher],
            branding={
                "school_name": "Rose-Hulman Institute of Technology",
                "generated_on": date(2026, 11, 16),
                "logo_png": logo.getvalue(),
            },
        ).getvalue()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn('xSplit="4"', sheet)
        self.assertIn('ySplit="4"', sheet)
        self.assertIn('mergeCell ref="B1:D1"', sheet)


if __name__ == "__main__":
    unittest.main()
