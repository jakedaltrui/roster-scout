"""Deterministic release acceptance for the detailed two-sheet workbook."""

from __future__ import annotations

import io
import unittest
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from hashlib import sha256

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
        "Last 10 G": 10,
        "Last 10 PA": 44,
        "Last 10 AB": 38,
        "Last 10 AVG": "0.368",
        "Last 10 OPS": "1.101",
        "Last 10 K%": "0.114",
        "Last 10 Status": "Complete",
        "Conference G": 8,
        "Conference Status": "Partial",
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
        "Conference Status": "Failed",
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
                hitter_table = archive.read("xl/tables/table1.xml").decode("utf-8")
                pitcher_table = archive.read("xl/tables/table2.xml").decode("utf-8")
                schemas.append((
                    workbook_xml.count("<sheet "),
                    hitter_table.split("tableColumns", 1)[1],
                    pitcher_table.split("tableColumns", 1)[1],
                ))
                values.append((
                    _cell(archive, 1, "W5"),
                    _cell(archive, 1, "AI5"),
                    _cell(archive, 1, "AP5"),
                    _cell(archive, 2, "U5"),
                    _cell(archive, 2, "AO5"),
                ))

        self.assertEqual(schemas[0], schemas[1])
        self.assertEqual(schemas[1], schemas[2])
        self.assertEqual(values[0], values[1])
        self.assertEqual(values[1], values[2])
        self.assertEqual(values[0][0], ("1.043", "number"))
        self.assertEqual(values[0][1], ("Complete", "string"))
        self.assertEqual(values[0][2], ("Partial", "string"))
        self.assertEqual(values[0][3], ("10.51", "number"))
        self.assertEqual(values[0][4], ("Failed", "string"))

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
            tables = (
                archive.read("xl/tables/table1.xml").decode("utf-8"),
                archive.read("xl/tables/table2.xml").decode("utf-8"),
            )

        self.assertEqual(workbook_xml.count("<sheet "), 2)
        self.assertEqual(len(HITTER_COLUMNS), 42)
        self.assertEqual(len(PITCHER_COLUMNS), 41)
        for label in (
            "Player",
            "Season",
            "Derived Metrics",
            "Last 10 Games",
            "Conference Games",
        ):
            self.assertIn(f">{label}<", strings)
        self.assertIn("=SAFE TEXT", strings)
        self.assertNotIn(b"<f>", worksheets)
        self.assertNotIn(b"<hyperlinks", worksheets)
        self.assertIn('ref="A2:AP3"', tables[0])
        self.assertIn('ref="A2:AO3"', tables[1])


if __name__ == "__main__":
    unittest.main()
