"""Canonical Roster Scout workbook rendering shared by every report flow."""

from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime
from functools import lru_cache

import requests
import xlsxwriter
from PIL import Image, ImageDraw
from xlsxwriter.utility import xl_pixel_width

from roster_scraper import HITTER_HEADERS, PITCHER_HEADERS


INTEGER_COLUMNS = {
    "#", "AB", "H", "2B", "HR", "RBI", "R", "BB", "SO", "SB",
    "W", "L", "SV", "ER",
}
THREE_DECIMAL_COLUMNS = {"AVG", "OBP", "SLG"}
TWO_DECIMAL_COLUMNS = {"ERA", "WHIP"}
DEFAULT_PALETTE = {
    "primary": "#1E3A5F",
    "primary_text": "#FFFFFF",
    "accent": "#C9A227",
    "band": "#F2F5F8",
    "border": "#D9E0E7",
}


def workbook_download_name(
    team_name: str,
    *,
    generated_on: date | None = None,
) -> str:
    """Return one dated, readable filename shared by every download flow."""
    readable = " ".join(str(team_name or "").split())
    safe_name = re.sub(r"[^A-Za-z0-9]+", " ", readable).strip() or "Opponent"
    report_date = generated_on or datetime.now(UTC).date()
    return f"{safe_name} Stats {report_date.strftime('%m-%d-%y')}.xlsx"


def excel_value(header: str, value):
    """Convert stat strings to useful Excel number types where semantics allow."""
    if value is None or value == "":
        return ""
    text = str(value).strip()
    try:
        if header in INTEGER_COLUMNS and re.fullmatch(r"-?\d+", text):
            return int(text)
        if header in THREE_DECIMAL_COLUMNS | TWO_DECIMAL_COLUMNS:
            return float(text)
    except ValueError:
        pass
    return text


@lru_cache(maxsize=1024)
def load_ncaa_logo_png(team_id: str) -> bytes:
    """Load and normalize the same official NCAA logo used by the homepage."""
    if not team_id.isascii() or not team_id.isdigit() or len(team_id) > 6:
        raise ValueError("invalid NCAA team id")
    url = f"https://web2.ncaa.org/ncaa_style/img/All_Logos/sm/{team_id}.gif"
    response = requests.get(url, timeout=8, stream=True)
    content = bytearray()
    try:
        response.raise_for_status()
        for chunk in response.iter_content(16384):
            content.extend(chunk)
            if len(content) > 256_000:
                raise ValueError("Logo is too large")
    finally:
        response.close()
    image = Image.open(io.BytesIO(content))
    if image.width * image.height > 1_000_000:
        raise ValueError("Logo dimensions are too large")
    image = image.convert("RGBA")
    for x in range(image.width):
        for y in (0, image.height - 1):
            if min(image.getpixel((x, y))[:3]) >= 245:
                ImageDraw.floodfill(image, (x, y), (255, 255, 255, 0), thresh=25)
    for y in range(image.height):
        for x in (0, image.width - 1):
            pixel = image.getpixel((x, y))
            if min(pixel[:3]) >= 245 and pixel[3]:
                ImageDraw.floodfill(image, (x, y), (255, 255, 255, 0), thresh=25)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _mix_rgb(first, second, second_weight):
    return tuple(
        round(a * (1 - second_weight) + b * second_weight)
        for a, b in zip(first, second)
    )


def _relative_luminance(rgb):
    channels = []
    for channel in rgb:
        value = channel / 255
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first, second):
    light, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


def logo_palette(logo_png: bytes | None) -> dict[str, str]:
    """Extract the homepage workbook palette from a visible school logo."""
    if not logo_png:
        return dict(DEFAULT_PALETTE)
    try:
        image = Image.open(io.BytesIO(logo_png)).convert("RGBA")
        image.thumbnail((160, 160), Image.Resampling.LANCZOS)
        pixels = (
            image.get_flattened_data()
            if hasattr(image, "get_flattened_data")
            else image.getdata()
        )
        opaque = [
            pixel[:3]
            for pixel in pixels
            if pixel[3] >= 96 and not all(channel >= 245 for channel in pixel[:3])
        ]
        if not opaque:
            return dict(DEFAULT_PALETTE)
        sample = Image.new("RGB", (len(opaque), 1))
        sample.putdata(opaque)
        quantized = sample.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette()
        candidates = []
        for count, index in quantized.getcolors() or []:
            rgb = tuple(palette[index * 3:index * 3 + 3])
            brightness = sum(rgb) / (3 * 255)
            saturation = (max(rgb) - min(rgb)) / 255
            if brightness > 0.96:
                continue
            candidates.append((count * (0.65 + 1.8 * saturation), count, rgb))
        if not candidates:
            return dict(DEFAULT_PALETTE)
        candidates.sort(reverse=True)
        primary = candidates[0][2]
        accent = next(
            (
                rgb
                for _, _, rgb in candidates[1:]
                if sum((a - b) ** 2 for a, b in zip(primary, rgb)) ** 0.5 >= 70
            ),
            _mix_rgb(primary, (255, 255, 255), 0.38),
        )
        white = (255, 255, 255)
        dark_text = (24, 32, 42)
        primary_text = (
            white
            if _contrast_ratio(primary, white) >= _contrast_ratio(primary, dark_text)
            else dark_text
        )
        return {
            "primary": _rgb_to_hex(primary),
            "primary_text": _rgb_to_hex(primary_text),
            "accent": _rgb_to_hex(accent),
            "band": _rgb_to_hex(_mix_rgb(primary, white, 0.93)),
            "border": _rgb_to_hex(_mix_rgb(primary, white, 0.82)),
        }
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_PALETTE)


def workbook_branding(
    *, school_name: str, generated_on: date, team_id: str | None = None
) -> dict:
    """Resolve deterministic school branding for a canonical NCAA identity."""
    logo_png = None
    if team_id:
        try:
            logo_png = load_ncaa_logo_png(str(team_id))
        except (requests.RequestException, OSError, ValueError):
            pass
    return {
        "school_name": school_name,
        "generated_on": generated_on,
        "logo_png": logo_png,
        "palette": logo_palette(logo_png),
    }


def _column_width_pixels(width: float) -> int:
    return round(width * 7 + 5)


def _lockup_last_column(column_widths, school_name, date_text, text_start_col=1):
    required_width = max(
        round(xl_pixel_width(school_name) * 1.6),
        round(xl_pixel_width(date_text) * 1.08),
    ) + 24
    accumulated_width = 0
    last_col = min(max(3, text_start_col), len(column_widths) - 1)
    for col_index in range(text_start_col, len(column_widths)):
        accumulated_width += _column_width_pixels(column_widths[col_index])
        if col_index >= last_col and accumulated_width >= required_width:
            return col_index
    return len(column_widths) - 1


def _write_worksheet(workbook, title, rows, headers, branding=None):
    worksheet = workbook.add_worksheet(title)
    worksheet.hide_gridlines(2)
    branding = branding or {}
    palette = branding.get("palette") or DEFAULT_PALETTE
    school_name = branding.get("school_name", "Axis Baseball")
    generated_on = branding.get("generated_on")
    logo_png = branding.get("logo_png")
    first_header_row = 2 if branding else 0
    first_data_row = first_header_row + 1
    last_col = len(headers) - 1
    worksheet.freeze_panes(first_data_row, 0)
    worksheet.set_tab_color(palette["primary"])
    worksheet.set_zoom(90)

    header_format = workbook.add_format({
        "bold": True, "font_name": "Arial", "font_size": 10,
        "font_color": palette["primary_text"], "bg_color": palette["primary"],
        "align": "left", "valign": "vcenter", "bottom": 2,
        "bottom_color": palette["accent"],
    })
    title_format = workbook.add_format({
        "bold": True, "font_name": "Arial", "font_size": 16,
        "font_color": palette["primary"], "align": "left", "valign": "bottom",
    })
    subtitle_format = workbook.add_format({
        "bold": True, "font_name": "Arial", "font_size": 10.5,
        "font_color": palette["primary"], "align": "left", "valign": "top",
    })
    lockup_format = workbook.add_format({
        "bg_color": "#FFFFFF", "align": "left", "valign": "vcenter",
    })

    def body_format(number_format=None, banded=False, bold=False):
        properties = {
            "font_name": "Arial", "font_size": 10, "font_color": "#20262E",
            "align": "left", "valign": "vcenter", "bottom": 1,
            "bottom_color": palette["border"],
        }
        if number_format:
            properties["num_format"] = number_format
        if banded:
            properties["bg_color"] = palette["band"]
        if bold:
            properties["bold"] = True
        return workbook.add_format(properties)

    formats = {}
    for banded in (False, True):
        for kind, number_format in (
            ("text", None), ("integer", "0"), ("three", "0.000"), ("two", "0.00")
        ):
            formats[(kind, banded, False)] = body_format(number_format, banded)
            formats[(kind, banded, True)] = body_format(number_format, banded, True)

    column_widths = []
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
        column_widths.append(width)
        worksheet.set_column(col_index, col_index, width)

    if branding:
        title_row_height = 36
        subtitle_row_height = 30
        worksheet.set_row(0, title_row_height)
        worksheet.set_row(1, subtitle_row_height)
        if isinstance(generated_on, date):
            date_text = (
                f"{generated_on.strftime('%B')} {generated_on.day}, "
                f"{generated_on.year}"
            ).upper()
        else:
            date_text = str(generated_on or date.today().isoformat()).upper()
        if logo_png:
            with Image.open(io.BytesIO(logo_png)) as logo:
                width, height = logo.size
            scale = min(82 / max(width, 1), 72 / max(height, 1))
            logo_width = width * scale
            logo_height = height * scale
            first_column_pixels = _column_width_pixels(column_widths[0])
            first_two_rows_pixels = round((title_row_height + subtitle_row_height) * 4 / 3)
            logo_x_offset = max(0, round((first_column_pixels - logo_width) / 2))
            logo_y_offset = max(0, round((first_two_rows_pixels - logo_height) / 2))
            lockup_last_col = _lockup_last_column(column_widths, school_name, date_text)
            worksheet.merge_range(0, 0, 1, 0, "", lockup_format)
            worksheet.merge_range(0, 1, 0, lockup_last_col, school_name, title_format)
            worksheet.merge_range(1, 1, 1, lockup_last_col, date_text, subtitle_format)
            worksheet.insert_image(0, 0, "school-logo.png", {
                "image_data": io.BytesIO(logo_png), "x_offset": logo_x_offset,
                "y_offset": logo_y_offset, "x_scale": scale, "y_scale": scale,
                "object_position": 1, "description": f"{school_name} logo",
            })
        else:
            worksheet.merge_range(0, 0, 0, last_col, school_name, title_format)
            worksheet.merge_range(1, 0, 1, last_col, date_text, subtitle_format)

    worksheet.set_row(first_header_row, 26)
    for col, header in enumerate(headers):
        worksheet.write(first_header_row, col, header, header_format)
    for row_index, player in enumerate(rows, start=first_data_row):
        worksheet.set_row(row_index, 21)
        banded = (row_index - first_data_row) % 2 == 1
        for col_index, header in enumerate(headers):
            value = excel_value(header, player.get(header, ""))
            kind = "text"
            if header in INTEGER_COLUMNS and isinstance(value, int):
                kind = "integer"
            elif header in THREE_DECIMAL_COLUMNS and isinstance(value, float):
                kind = "three"
            elif header in TWO_DECIMAL_COLUMNS and isinstance(value, float):
                kind = "two"
            worksheet.write(
                row_index, col_index, value,
                formats[(kind, banded, header == "Name")],
            )
    if rows:
        worksheet.add_table(
            first_header_row, 0, first_header_row + len(rows), last_col,
            {"columns": [
                {"header": header, "header_format": header_format}
                for header in headers
            ], "style": None, "banded_rows": False, "name": f"{title}Table"},
        )
    else:
        worksheet.autofilter(first_header_row, 0, first_header_row, last_col)
    last_row = first_header_row + max(len(rows), 1)
    worksheet.set_landscape()
    worksheet.fit_to_pages(1, 0)
    worksheet.repeat_rows(first_header_row, first_header_row)
    worksheet.set_margins(left=0.35, right=0.35, top=0.55, bottom=0.55)
    worksheet.print_area(0, 0, last_row, last_col)
    worksheet.set_footer(f"&L{school_name} Baseball&C{title}&RPage &P of &N")


def create_workbook(hitters, pitchers, branding=None) -> io.BytesIO:
    """Build the canonical two-tab workbook used by all product entry points."""
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {
        "in_memory": True,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    })
    school_name = (branding or {}).get("school_name", "Axis Baseball")
    workbook.set_properties({
        "title": f"{school_name} Baseball Roster Scout",
        "subject": "Baseball roster and season statistics",
        "author": "Axis Baseball",
        "company": "Axis Baseball",
    })
    _write_worksheet(workbook, "Hitters", hitters, HITTER_HEADERS, branding)
    _write_worksheet(workbook, "Pitchers", pitchers, PITCHER_HEADERS, branding)
    workbook.close()
    output.seek(0)
    return output
