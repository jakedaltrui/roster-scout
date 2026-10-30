#!/usr/bin/env python3
"""
Baseball Roster & Stats Scraper
Scrapes and normalizes NCAA college baseball team data for workbook export.

Supports:
  - NCAA stats.ncaa.org (primary — search by team name, all D1/D2/D3)
  - Sidearm Sports (uses internal bio+stats API — covers most NCAA schools)
  - Generic HTML table fallback for other platforms

Usage:
    python3 roster_scraper.py <team_url> [options]
"""

import re
import json
import argparse
import csv
import hashlib
import os
import tempfile
import threading
import time
import uuid
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

NCAA_BASE = "https://stats.ncaa.org"
NCAA_TEAM_LOOKUP_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/baseballr-data/"
    "main/ncaa/teams_info/ncaa_team_lookup.csv"
)
NCAA_SEASON_LOOKUP_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/baseballr-data/"
    "main/ncaa/seasons_info/ncaa_season_id_lu.csv"
)
NCAA_BLOCK_MARKERS = (
    "access denied", "reference&#32;#", "reference #",
    "pardon our interruption", "bm-verify", "akamai_validation",
    "triggerinterstitialchallenge", "/_sec/verify", "request_quota_reached",
)
NCAA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_NCAA_CACHE_VERSION = 2
_NCAA_CACHE_LOCK = threading.Lock()
_NCAA_BROWSER_LOCK = threading.Lock()
_NCAA_TEAM_ROWS = None
_NCAA_SEASON_ROWS = None

CURRENT_YEAR = datetime.now().year
CLASS_TO_GRAD = {
    "fr": CURRENT_YEAR + 3, "so": CURRENT_YEAR + 2,
    "jr": CURRENT_YEAR + 1, "sr": CURRENT_YEAR,
}

# ── URL helpers ────────────────────────────────────────────────────────────────

def get_roster_url(url: str) -> str:
    url = url.rstrip("/")
    if "/roster" in url:
        return url
    if "/sports/baseball" in url:
        base = url.split("/sports/baseball")[0]
        return f"{base}/sports/baseball/roster"
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/sports/baseball/roster"


def get_stats_url(roster_url: str) -> str:
    return roster_url.replace("/roster", "/stats")


def get_base_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def get_team_label(url: str) -> str:
    """Return a clean school name from og:site_name, falling back to domain parsing."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:site_name")
        if og and og.get("content", "").strip():
            name = og["content"].strip()
            # Strip "Athletics" / "Athletic Department"
            name = re.sub(r"\s*\bAthletics?\b.*", "", name, flags=re.IGNORECASE).strip()
            # If it starts with an all-caps abbreviation (UNO, UTRGV, SIUE, LSU …)
            # use just that — drop the mascot word that follows
            words = name.split()
            if words and re.match(r"^[A-Z]{2,8}$", words[0]):
                return words[0]
            return name
    except Exception:
        pass
    host = urlparse(url).netloc.replace("www.", "")
    return host.split(".")[0].title()

# ── Stat calculators ──────────────────────────────────────────────────────────

def _int(val) -> int:
    try:
        return int(val or 0)
    except (ValueError, TypeError):
        return 0


def _fmt(val: float, decimals: int = 3) -> str:
    """Format a rate stat, e.g. 0.321 → '.321'"""
    if val is None:
        return ""
    s = f"{val:.{decimals}f}"
    return s.lstrip("0") or "0"  # .321 not 0.321


def calc_avg(h, ab) -> str:
    ab = _int(ab)
    return _fmt(_int(h) / ab) if ab > 0 else ""


def calc_obp(h, bb, hbp, ab, sf) -> str:
    num = _int(h) + _int(bb) + _int(hbp)
    den = _int(ab) + _int(bb) + _int(hbp) + _int(sf)
    return _fmt(num / den) if den > 0 else ""


def calc_slg(h, doubles, triples, hr, ab) -> str:
    ab = _int(ab)
    if ab == 0:
        return ""
    singles = _int(h) - _int(doubles) - _int(triples) - _int(hr)
    tb = singles + 2 * _int(doubles) + 3 * _int(triples) + 4 * _int(hr)
    return _fmt(tb / ab)


def class_to_grad_year(class_str: str) -> str:
    s = class_str.strip().lower()
    # Strip "redshirt" prefix so "Redshirt Junior" → "junior"
    s = re.sub(r"^redshirt\s*", "", s).strip()
    mapping = {
        "fr": CURRENT_YEAR + 3, "freshman": CURRENT_YEAR + 3,
        "so": CURRENT_YEAR + 2, "sophomore": CURRENT_YEAR + 2,
        "jr": CURRENT_YEAR + 1, "junior": CURRENT_YEAR + 1,
        "sr": CURRENT_YEAR,     "senior": CURRENT_YEAR,
        "graduate": CURRENT_YEAR, "graduate student": CURRENT_YEAR, "gr": CURRENT_YEAR,
    }
    year = mapping.get(s) or mapping.get(s[:2])
    return str(year) if year else class_str

# ── Sidearm Sports scraper ────────────────────────────────────────────────────

def _get_sidearm_rp_ids(roster_url: str) -> list[tuple[str, str]]:
    """
    Parse the roster page JSON-LD to get (name, rp_id) pairs.
    Returns list of (name, rp_id).
    """
    resp = requests.get(roster_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    players = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue

        if isinstance(data, list):
            data = {"item": data}
        items = data.get("item", [])
        for item in items:
            if item.get("@type") == "Person":
                name = item.get("name", "")
                url  = item.get("url", "")
                m = re.search(r"rp_id=(\d+)", url)
                if name and m:
                    players.append((name, m.group(1)))

    return players


def _parse_ip(ip_str) -> float:
    """Convert Sidearm innings-pitched string '7.2' (7 and 2/3 IP) to a float."""
    try:
        parts = str(ip_str or "0").split(".")
        full = int(parts[0])
        outs = int(parts[1]) if len(parts) > 1 else 0
        return full + outs / 3
    except Exception:
        return 0.0


def calc_era(er, ip_str) -> str:
    ip = _parse_ip(ip_str)
    return f"{_int(er) / ip * 9:.2f}" if ip > 0 else ""


def calc_whip(bb, h, ip_str) -> str:
    ip = _parse_ip(ip_str)
    return f"{(_int(bb) + _int(h)) / ip:.2f}" if ip > 0 else ""


# Positions considered pitching vs hitting
_PITCHER_POSITIONS = {"rhp", "lhp", "p", "sp", "rp", "cp", "hp",
                      "lhsp", "rhsp", "lhrp", "rhrp"}
_HITTER_POSITIONS  = {"c", "1b", "2b", "3b", "ss", "of", "lf", "cf", "rf",
                      "dh", "inf", "utl", "util", "ph", "pr", "if", "mid"}


def _is_pitcher_pos(pos: str) -> bool:
    return any(p in _PITCHER_POSITIONS for p in re.split(r"[/,\s]+", pos.lower()) if p)


def _is_hitter_pos(pos: str) -> bool:
    return any(p in _HITTER_POSITIONS for p in re.split(r"[/,\s]+", pos.lower()) if p)


def _fetch_sidearm_player(base_url: str, rp_id: str, sport: str, year: str):
    """Call the Sidearm internal API for one player's bio + hitting + pitching stats."""
    try:
        resp = requests.get(
            f"{base_url}/services/roster_bio_stats.ashx",
            params={"rp_id": rp_id, "sport": sport, "year": year},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    bio        = data.get("bio") or {}
    stats      = data.get("stats") or {}
    game_stats = stats.get("game_by_game_stats") or []

    # Pull season-total hitting and pitching rows (footer stats)
    hitting  = {}
    pitching = {}
    for row in game_stats:
        if not row.get("is_a_footer_stat"):
            continue
        if row.get("hitting")  and not hitting:
            hitting  = row["hitting"]
        if row.get("pitching") and not pitching:
            pitching = row["pitching"]

    class_raw = bio.get("academic_class", "")

    # ── Hitting fields ────────────────────────────────────────────────────────
    h   = hitting.get("hits", "0")
    ab  = hitting.get("at_bats", "0")
    bb  = hitting.get("walks", "0")
    hbp = hitting.get("hit_by_pitch", "0")
    sf  = hitting.get("sacrifice_flies", "0")
    dbl = hitting.get("doubles", "0")
    tri = hitting.get("triples", "0")
    hr  = hitting.get("home_runs", "0")
    has_hitting = hitting and _int(ab) > 0

    # ── Pitching fields ───────────────────────────────────────────────────────
    ip      = pitching.get("innings_pitched", "0")
    p_h     = pitching.get("hits_allowed", "0")
    p_bb    = pitching.get("walks_allowed", "0")
    er      = pitching.get("earned_runs_allowed", "0")
    has_pitching = pitching and _parse_ip(ip) > 0

    pos = bio.get("position", "")
    if not pos or pos.lower() == "p":
        throws = bio.get("throws", "").strip().upper()
        if throws == "R":
            pos = "RHP"
        elif throws == "L":
            pos = "LHP"

    bio_fields = {
        "Name":        f"{bio.get('first_name', '')} {bio.get('last_name', '')}".strip(),
        "#":           bio.get("jersey_number", ""),
        "Position":    pos,
        "Class":       class_raw,
        "Grad Year":   class_to_grad_year(class_raw),
        "Hometown":    bio.get("hometown", ""),
        "High School": bio.get("highschool", ""),
    }

    return {
        **bio_fields,
        # Hitting
        "AVG":  calc_avg(h, ab)              if has_hitting else "",
        "OBP":  calc_obp(h, bb, hbp, ab, sf) if has_hitting else "",
        "SLG":  calc_slg(h, dbl, tri, hr, ab) if has_hitting else "",
        "H":    h   if has_hitting else "",
        "2B":   dbl if has_hitting else "",
        "HR":   hr  if has_hitting else "",
        "RBI":  hitting.get("runs_batted_in", "") if has_hitting else "",
        "AB":   ab  if has_hitting else "",
        "R":    hitting.get("runs_scored", "")   if has_hitting else "",
        "BB":   bb  if has_hitting else "",
        "H_SO": hitting.get("strikeouts", "")    if has_hitting else "",
        "SB":   hitting.get("stolen_bases", "")  if has_hitting else "",
        # Pitching
        "ERA":  calc_era(er, ip)            if has_pitching else "",
        "WHIP": calc_whip(p_bb, p_h, ip)   if has_pitching else "",
        "W":    pitching.get("wins", "")    if has_pitching else "",
        "L":    pitching.get("losses", "")  if has_pitching else "",
        "SV":   pitching.get("saves", "")   if has_pitching else "",
        "IP":   ip                          if has_pitching else "",
        "P_H":  p_h                         if has_pitching else "",
        "ER":   er                          if has_pitching else "",
        "P_BB": p_bb                        if has_pitching else "",
        "P_SO": pitching.get("strikeouts", "") if has_pitching else "",
        # Flags for classification
        "_has_hitting":  has_hitting,
        "_has_pitching": has_pitching,
        "_pos":          bio.get("position", ""),
    }


def scrape_sidearm(roster_url: str) -> list[dict]:
    """
    Full Sidearm Sports scrape: roster page → per-player bio+stats API.
    Fetches all players concurrently.
    """
    base  = get_base_url(roster_url)
    year  = str(CURRENT_YEAR)
    sport = "baseball"

    id_pairs = _get_sidearm_rp_ids(roster_url)
    if not id_pairs:
        return []

    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_fetch_sidearm_player, base, rp_id, sport, year): (name, rp_id)
            for name, rp_id in id_pairs
        }
        for future in as_completed(futures):
            name, rp_id = futures[future]
            player = future.result()
            if player:
                results[rp_id] = player

    # Return in original roster order
    ordered = []
    for name, rp_id in id_pairs:
        p = results.get(rp_id)
        if p:
            ordered.append(p)
        else:
            ordered.append({"Name": name, "#": "", "Position": "", "Height": "",
                            "Weight": "", "Class": "", "Grad Year": "", "Hometown": "",
                            "High School": "", "AVG": "", "OBP": "", "SLG": "",
                            "H": "", "2B": "", "HR": "", "RBI": "",
                            "AB": "", "R": "", "BB": "", "SO": "", "SB": ""})
    return ordered

# ── Generic HTML fallback ─────────────────────────────────────────────────────

def _scrape_html_roster(url: str) -> list[dict]:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    players = []

    for table in soup.find_all("table"):
        thead = table.find("thead")
        if not thead:
            continue
        cols = [th.get_text(strip=True).lower() for th in thead.find_all("th")]
        if not any(h in cols for h in ["name", "player", "last name"]):
            continue

        for row in table.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 2:
                continue
            d = dict(zip(cols, cells))
            name_key = next((k for k in d if "name" in k or "player" in k), cols[0])
            name = d.get(name_key, "").strip()
            if not name:
                continue
            pos_key  = next((k for k in d if "pos" in k), "")
            yr_key   = next((k for k in d if any(w in k for w in ["year","class","yr","eligibility"])), "")
            num_key  = next((k for k in d if any(w in k for w in ["no","num","#","jersey"])), "")
            class_raw = d.get(yr_key, "")
            players.append({
                "Name": name, "#": d.get(num_key, ""), "Position": d.get(pos_key, ""),
                "Height": "", "Weight": "", "Class": class_raw,
                "Grad Year": class_to_grad_year(class_raw),
                "Hometown": "", "High School": "",
                "AVG": "", "OBP": "", "SLG": "", "H": "", "2B": "", "HR": "",
                "RBI": "", "AB": "", "R": "", "BB": "", "SO": "", "SB": "",
            })
        if players:
            break

    return players

# ── NJCAA / JUCO scraper ─────────────────────────────────────────────────────
# Source: njcaastats.prestosports.com (all 3 JUCO divisions, ~400 programs).
# Each school has its own PrestoSports subdomain; we derive it from the slug
# and hit the school's /players endpoint with ?ajax=true to get stats.

NJCAA_BASE   = "https://njcaastats.prestosports.com"
PRESTO_BASE  = "https://{sub}.prestosports.com"  # per-school base
_NJCAA_TEAM_CACHE: dict = {}  # name_lower -> {name, slug, div, team_id}

# Academic year for the current season (spring YEAR = YEAR-1/YEAR in PrestoSports)
_JUCO_SEASON = f"{CURRENT_YEAR - 1}-{str(CURRENT_YEAR)[2:]}"  # "2025-26"


def _load_njcaa_teams() -> dict:
    """
    Scrape the NJCAA teams page and return a dict of name_lower →
    {name, slug, div, team_id}.  Cached for the process lifetime.
    """
    global _NJCAA_TEAM_CACHE
    if _NJCAA_TEAM_CACHE:
        return _NJCAA_TEAM_CACHE

    resp = requests.get(
        f"{NJCAA_BASE}/sports/bsb/teams-page",
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    teams = {}
    for table in soup.find_all("table"):
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            links = [a.get("href", "") for a in row.find_all("a", href=True)]
            # Slug from the first link that contains /teams/
            slug_link = next((l for l in links if "/teams/" in l), None)
            if not slug_link:
                continue
            sm = re.search(r"/teams/([^?/]+)", slug_link)
            if not sm:
                continue
            slug = sm.group(1)
            dm   = re.search(r"/div(\d)/", slug_link)
            div  = dm.group(1) if dm else "1"
            # team_id from schedule link
            sched = next((l for l in links if "teamId=" in l), "")
            tm    = re.search(r"teamId=([a-z0-9]+)", sched)
            team_id = tm.group(1) if tm else ""
            # Name is in cell[1] text, appears as "Long NameShort Name" — keep long name
            raw = cells[1].get_text(strip=True)
            # Find where short version starts (capital after lowercase, no space)
            m = re.search(r"([a-z])([A-Z])", raw)
            name = raw[:m.start() + 1] if m else raw

            entry = {"name": name, "slug": slug, "div": div, "team_id": team_id}
            teams[name.lower()] = entry

    _NJCAA_TEAM_CACHE = teams
    return teams


_COLLEGE_SUFFIXES = re.compile(
    r"\s+(community\s+college|junior\s+college|state\s+college|technical\s+college"
    r"|community\s+college|college|university|cc|jc|state)$",
    re.IGNORECASE,
)

def _strip_suffix(name: str) -> str:
    """Strip common college-type suffixes so 'Jones Community College' → 'jones'."""
    s = name.strip().lower()
    prev = None
    while s != prev:
        prev = s
        s = _COLLEGE_SUFFIXES.sub("", s).strip()
    return s


def _find_njcaa_team(team_name: str) -> dict:
    """Fuzzy-match a user query to an NJCAA team. Returns team dict."""
    from difflib import get_close_matches

    teams = _load_njcaa_teams()
    query = team_name.strip().lower()

    # 1. Exact match
    if query in teams:
        return teams[query]

    # 2. Starts-with match
    prefix = [v for k, v in teams.items() if k.startswith(query)]
    if len(prefix) == 1:
        return prefix[0]
    if prefix:
        return min(prefix, key=lambda x: len(x["name"]))

    # 3. Strip suffixes and match on core name — runs before full fuzzy
    #    so "Jones Community College" → "jones" matches "Jones College" → "jones"
    #    rather than fuzzy-matching "Owens Community College" on shared suffix words.
    query_core = _strip_suffix(query)
    core_map   = {_strip_suffix(k): v for k, v in teams.items()}

    if query_core in core_map:
        return core_map[query_core]

    core_keys = list(core_map.keys())
    close_core = get_close_matches(query_core, core_keys, n=1, cutoff=0.7)
    if close_core:
        return core_map[close_core[0]]

    # 4. Full-name fuzzy as last resort
    names = list(teams.keys())
    close = get_close_matches(query, names, n=1, cutoff=0.6)
    if close:
        return teams[close[0]]

    raise ValueError(
        f"No JUCO baseball team found matching '{team_name}'. "
        "Try the school name (e.g. 'Chipola', 'Jones', 'Crowder')."
    )


def _find_presto_subdomain(slug: str, team_id: str) -> str:
    """
    Derive a school's PrestoSports subdomain from its NJCAA slug.
    Tries several patterns, returns the first that resolves.
    """
    # Generate candidate subdomains by stripping common suffixes
    candidates = [slug]
    for suffix in ("communitycollege", "community", "college", "state", "technical", "junior"):
        stripped = slug.replace(suffix, "").strip("-")
        if stripped and stripped not in candidates:
            candidates.append(stripped)

    # Also try splitting on common word boundaries (e.g. "allencountycc" -> "allencounty")
    cc_strip = re.sub(r"(community|junior|technical|state)?(college|cc)$", "", slug)
    if cc_strip and cc_strip not in candidates:
        candidates.append(cc_strip)

    for sub in candidates:
        url = f"https://{sub}.prestosports.com/sports/bsb/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=6, allow_redirects=True)
            if r.status_code == 200 and "prestosports" in r.url:
                return sub
        except Exception:
            continue

    raise ValueError(f"Could not find PrestoSports site for JUCO team (slug={slug})")


def _parse_juco_roster_page(base: str) -> list[dict]:
    """
    Scrape the PrestoSports roster page for a JUCO school.
    Returns list of player dicts with bio info and bio_href for stats lookup.
    """
    url = f"{base}/sports/bsb/{_JUCO_SEASON}/roster"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    players = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        num  = cells[0].get_text(strip=True)
        a    = cells[1].find("a")
        if not a:
            continue
        name     = re.sub(r"\s+", " ", a.get_text()).strip()
        bio_href = a.get("href", "")
        pos = re.sub(r"Pos\.\s*:", "", cells[2].get_text(strip=True)).strip()
        cl  = re.sub(r"Cl\.\s*:",  "", cells[4].get_text(strip=True)).strip() if len(cells) > 4 else ""
        ht  = re.sub(r"Ht\.\s*:",  "", cells[5].get_text(strip=True)).strip() if len(cells) > 5 else ""
        wt  = re.sub(r"Wt\.\s*:",  "", cells[6].get_text(strip=True)).strip() if len(cells) > 6 else ""

        # Hometown / high school are "/" separated in cell 7
        hometown = high_school = ""
        if len(cells) > 7:
            raw = re.sub(r"Hometown/High School.*?:", "", cells[7].get_text(separator=" ", strip=True))
            parts = [p.strip() for p in re.split(r"\s*/\s*", raw) if p.strip()]
            hometown   = parts[0] if parts else ""
            high_school = parts[1] if len(parts) > 1 else ""

        players.append({
            "Name": name, "#": num, "Position": pos,
            "Height": ht, "Weight": wt,
            "Class": cl, "Grad Year": class_to_grad_year(cl) if cl else "",
            "Hometown": hometown, "High School": high_school,
            "bio_href": bio_href,
        })

    return players


def _fetch_juco_bio_stats(base: str, bio_href: str) -> dict:
    """
    Fetch an individual player's PrestoSports bio page and return
    season-total hitting and pitching stats from the summary tables.
    """
    if not bio_href:
        return {}
    try:
        resp = requests.get(f"{base}{bio_href}", headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        return {}

    soup   = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")

    hitting  = {}
    pitching = {}

    for t in tables:
        thead = t.find("thead")
        if not thead:
            continue
        cols = [th.get_text(strip=True).lower() for th in thead.find_all("th")]

        if "ab" in cols and "avg" in cols and not hitting:
            for tr in t.find_all("tr"):
                th_el = tr.find("th")
                if th_el and th_el.get_text(strip=True).lower() == "total":
                    cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                    hitting = dict(zip(cols[1:], cells))
                    break

        if "era" in cols and "ip" in cols and not pitching:
            for tr in t.find_all("tr"):
                th_el = tr.find("th")
                if th_el and th_el.get_text(strip=True).lower() == "total":
                    cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                    pitching = dict(zip(cols[1:], cells))
                    break

    return {"hitting": hitting, "pitching": pitching}


def _scrape_juco_via_njcaastats(slug: str, div: str, team_id: str) -> list[dict]:
    """
    Fallback for JUCO schools without their own PrestoSports subdomain.
    Hits njcaastats.prestosports.com AJAX endpoint directly (requires ~30s timeout).
    Returns partial player dicts (no height/weight/hometown).
    """
    base     = "https://njcaastats.prestosports.com"
    div_path = f"/div{div}" if div != "1" else ""
    players_path = f"/sports/bsb/{_JUCO_SEASON}{div_path}/players"
    requri       = f"/sports/bsb/{_JUCO_SEASON}{div_path}/teams/{slug}"

    def _fetch(pos: str, sort: str, min_q: str) -> list[dict]:
        url = (
            f"{base}{players_path}"
            f"?teamId={team_id}&view=lineup&sort={sort}&pos={pos}&r=0"
            f"&ajax=true&requri={requri}&tmpl=stats-bios-template&dec=&min={min_q}"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                return []
        except Exception:
            return []
        soup  = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            return []
        thead = table.find("thead")
        if not thead:
            return []
        cols = [th.get_text(strip=True).lower() for th in thead.find_all("th")]
        rows = []
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 4:
                continue
            d    = dict(zip(cols, cells))
            name = re.sub(r"\s+", " ", d.get("name", cells[1] if len(cells) > 1 else "")).strip()
            if not name or name.lower() in ("totals", "total", "team"):
                continue
            d["name"] = name
            rows.append(d)
        return rows

    with ThreadPoolExecutor(max_workers=2) as pool:
        bat_fut = pool.submit(_fetch, "h", "avg", "par")
        pit_fut = pool.submit(_fetch, "p", "era", "bf")
        bat_rows = bat_fut.result()
        pit_rows = pit_fut.result()

    def _mk(d: dict, is_bat: bool) -> dict:
        yr  = d.get("yr", d.get("cl", ""))
        pos = d.get("pos", "")
        num = d.get("#", "")
        if is_bat:
            ab  = d.get("ab","0"); h   = d.get("h","0")
            dbl = d.get("2b","0"); tri = d.get("3b","0"); hr = d.get("hr","0")
            rbi = d.get("rbi","0"); r  = d.get("r","0")
            bb  = d.get("bb","0"); so  = d.get("k", d.get("so","0"))
            sb  = d.get("sb","0"); hbp = d.get("hbp","0")
            avg = d.get("avg",""); obp = d.get("obp",""); slg = d.get("slg","")
            has = _int(ab) > 0
            return {
                "Name": d["name"], "#": num, "Position": pos,
                "Height":"","Weight":"","Class": yr,
                "Grad Year": class_to_grad_year(yr) if yr else "",
                "Hometown":"","High School":"",
                "AVG": avg or (calc_avg(h,ab) if has else ""),
                "OBP": obp or (calc_obp(h,bb,hbp,ab,"0") if has else ""),
                "SLG": slg or (calc_slg(h,dbl,tri,hr,ab) if has else ""),
                "AB":ab if has else "","H":h if has else "",
                "2B":dbl if has else "","HR":hr if has else "",
                "RBI":rbi if has else "","R":r if has else "",
                "BB":bb if has else "","H_SO":so if has else "","SB":sb if has else "",
                "ERA":"","WHIP":"","W":"","L":"","SV":"",
                "IP":"","P_H":"","ER":"","P_BB":"","P_SO":"",
                "_has_hitting": has, "_has_pitching": False, "_pos": pos,
            }
        else:
            ip   = d.get("ip","0"); era  = d.get("era","")
            w    = d.get("w","");   l    = d.get("l",""); sv = d.get("sv","")
            p_h  = d.get("h","0"); er   = d.get("er","0")
            p_bb = d.get("bb","0"); p_so = d.get("k", d.get("so","0"))
            has  = _parse_ip(ip) > 0
            return {
                "Name": d["name"], "#": num, "Position": pos,
                "Height":"","Weight":"","Class": yr,
                "Grad Year": class_to_grad_year(yr) if yr else "",
                "Hometown":"","High School":"",
                "AVG":"","OBP":"","SLG":"","AB":"","H":"","2B":"","HR":"",
                "RBI":"","R":"","BB":"","H_SO":"","SB":"",
                "ERA": era or (calc_era(er,ip) if has else ""),
                "WHIP": calc_whip(p_bb,p_h,ip) if has else "",
                "W":w if has else "","L":l if has else "","SV":sv if has else "",
                "IP":ip if has else "","P_H":p_h if has else "",
                "ER":er if has else "","P_BB":p_bb if has else "","P_SO":p_so if has else "",
                "_has_hitting": False, "_has_pitching": has, "_pos": pos,
            }

    bat_by_name = {r["name"].lower(): _mk(r, True)  for r in bat_rows}
    pit_by_name = {r["name"].lower(): _mk(r, False) for r in pit_rows}
    all_names   = list(bat_by_name) + [n for n in pit_by_name if n not in bat_by_name]

    players = []
    for norm in all_names:
        b = bat_by_name.get(norm, {})
        p = pit_by_name.get(norm, {})
        players.append({
            "Name":      b.get("Name") or p.get("Name", norm),
            "#":         b.get("#","") or p.get("#",""),
            "Position":  b.get("Position") or p.get("Position",""),
            "Height":"","Weight":"",
            "Class":     b.get("Class") or p.get("Class",""),
            "Grad Year": b.get("Grad Year") or p.get("Grad Year",""),
            "Hometown":"","High School":"",
            "AVG":b.get("AVG",""),"OBP":b.get("OBP",""),"SLG":b.get("SLG",""),
            "AB":b.get("AB",""),"H":b.get("H",""),"2B":b.get("2B",""),
            "HR":b.get("HR",""),"RBI":b.get("RBI",""),"R":b.get("R",""),
            "BB":b.get("BB",""),"H_SO":b.get("H_SO",""),"SB":b.get("SB",""),
            "ERA":p.get("ERA",""),"WHIP":p.get("WHIP",""),
            "W":p.get("W",""),"L":p.get("L",""),"SV":p.get("SV",""),
            "IP":p.get("IP",""),"P_H":p.get("P_H",""),"ER":p.get("ER",""),
            "P_BB":p.get("P_BB",""),"P_SO":p.get("P_SO",""),
            "_has_hitting":  bool(b),
            "_has_pitching": bool(p),
            "_pos": b.get("_pos") or p.get("_pos",""),
        })

    return players


def scrape_juco_by_name(team_name: str) -> tuple:
    """
    Look up a JUCO team by name, scrape the full roster from the PrestoSports
    roster page, then fetch each player's bio page for season-total stats.
    Returns (players, team_label).
    """
    team_info = _find_njcaa_team(team_name)
    slug    = team_info["slug"]
    div     = team_info["div"]
    team_id = team_info["team_id"]
    label   = f"{team_info['name']} (JUCO D{div})"

    try:
        sub  = _find_presto_subdomain(slug, team_id)
        base = f"https://{sub}.prestosports.com"
    except ValueError:
        # No dedicated subdomain — fall back to njcaastats.prestosports.com
        players = _scrape_juco_via_njcaastats(slug, div, team_id)
        if not players:
            raise ValueError(
                f"Found '{label}' but no current-season stats are available yet."
            )
        return players, label

    roster = _parse_juco_roster_page(base)
    if not roster:
        raise ValueError(
            f"Found '{label}' but could not load the roster page."
        )

    # Fetch each player's bio page in parallel
    bio_stats: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_fetch_juco_bio_stats, base, p["bio_href"]): p["Name"]
            for p in roster if p.get("bio_href")
        }
        for future in as_completed(futures):
            name = futures[future]
            result = future.result()
            if result:
                bio_stats[name] = result

    any_stats = any(
        bool(v.get("hitting") or v.get("pitching"))
        for v in bio_stats.values()
    )
    if not any_stats:
        raise ValueError(
            f"Found '{label}' but no current-season stats are available yet."
        )

    players = []
    for p in roster:
        stats    = bio_stats.get(p["Name"], {})
        hitting  = stats.get("hitting",  {})
        pitching = stats.get("pitching", {})

        ab  = hitting.get("ab",  "0"); h   = hitting.get("h",  "0")
        dbl = hitting.get("2b",  "0"); tri = hitting.get("3b", "0")
        hr  = hitting.get("hr",  "0"); rbi = hitting.get("rbi","0")
        r   = hitting.get("r",   "0"); bb  = hitting.get("bb", "0")
        so  = hitting.get("k",   hitting.get("so", "0"))
        sb  = hitting.get("sb",  "0"); hbp = hitting.get("hbp","0")
        avg = hitting.get("avg", "");  obp = hitting.get("obp","")
        slg = hitting.get("slg", "")
        has_hitting = _int(ab) > 0

        ip   = pitching.get("ip",  "0"); era  = pitching.get("era", "")
        w    = pitching.get("w",   "");  l    = pitching.get("l",   "")
        sv   = pitching.get("sv",  "");  p_h  = pitching.get("h",  "0")
        er   = pitching.get("er",  "0"); p_bb = pitching.get("bb", "0")
        p_so = pitching.get("k",   pitching.get("so", "0"))
        has_pitching = _parse_ip(ip) > 0

        players.append({
            "Name":      p["Name"],
            "#":         p["#"],
            "Position":  p["Position"],
            "Height":    p["Height"],
            "Weight":    p["Weight"],
            "Class":     p["Class"],
            "Grad Year": p["Grad Year"],
            "Hometown":  p["Hometown"],
            "High School": p["High School"],
            "AVG":  avg or (calc_avg(h, ab)              if has_hitting else ""),
            "OBP":  obp or (calc_obp(h, bb, hbp, ab, "0") if has_hitting else ""),
            "SLG":  slg or (calc_slg(h, dbl, tri, hr, ab) if has_hitting else ""),
            "AB":  ab  if has_hitting else "", "H":   h   if has_hitting else "",
            "2B":  dbl if has_hitting else "", "HR":  hr  if has_hitting else "",
            "RBI": rbi if has_hitting else "", "R":   r   if has_hitting else "",
            "BB":  bb  if has_hitting else "", "H_SO": so if has_hitting else "",
            "SB":  sb  if has_hitting else "",
            "ERA":  era or (calc_era(er, ip)         if has_pitching else ""),
            "WHIP": calc_whip(p_bb, p_h, ip)         if has_pitching else "",
            "W": w if has_pitching else "", "L": l   if has_pitching else "",
            "SV": sv if has_pitching else "", "IP": ip if has_pitching else "",
            "P_H": p_h if has_pitching else "", "ER": er if has_pitching else "",
            "P_BB": p_bb if has_pitching else "", "P_SO": p_so if has_pitching else "",
            "_has_hitting":  has_hitting,
            "_has_pitching": has_pitching,
            "_pos": p["Position"],
        })

    return players, label


# ── D1Baseball.com scraper ────────────────────────────────────────────────────
# stats.ncaa.org is WAF-blocked; d1baseball.com covers all 308 D1 programs
# with current-season stats and a clean public URL structure.

D1BB_BASE = "https://d1baseball.com"
_D1BB_TEAM_CACHE: dict = {}  # slug -> full name, loaded once per process


def _load_d1bb_teams() -> dict:
    """
    Fetch the D1Baseball team-leaderboards page to get slug → name mapping
    for all 308+ D1 programs. Result is cached for the life of the process.
    """
    global _D1BB_TEAM_CACHE
    if _D1BB_TEAM_CACHE:
        return _D1BB_TEAM_CACHE

    resp = requests.get(
        f"{D1BB_BASE}/team-leaderboards/",
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    teams = {}
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/team/" not in href:
            continue
        m = re.search(r"/team/([^/]+)/?$", href)
        if not m:
            continue
        slug = m.group(1)
        name = a.get_text(strip=True)
        if name and not name.isdigit():
            teams[slug] = name

    _D1BB_TEAM_CACHE = teams
    return teams


def _find_d1bb_slug(team_name: str):
    """
    Find the best D1Baseball slug for a user-supplied team name.
    Returns (slug, canonical_name) or raises ValueError.
    """
    from difflib import get_close_matches

    teams = _load_d1bb_teams()
    query = team_name.strip().lower()

    # 1. Exact name match (case-insensitive)
    for slug, name in teams.items():
        if name.lower() == query:
            return slug, name

    # 2. Slug exact match
    query_nospace = re.sub(r"\s+", "", query)
    if query_nospace in teams:
        return query_nospace, teams[query_nospace]

    # 3. Name starts-with match
    prefix_matches = [(s, n) for s, n in teams.items() if n.lower().startswith(query)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if prefix_matches:
        return min(prefix_matches, key=lambda x: len(x[1]))

    # 4. Fuzzy name match
    names = list(teams.values())
    close = get_close_matches(team_name, names, n=1, cutoff=0.6)
    if close:
        for slug, name in teams.items():
            if name == close[0]:
                return slug, name

    raise ValueError(
        f"No D1 baseball team found matching '{team_name}'. "
        "Try the full school name (e.g. 'Auburn', 'Louisiana State', 'Vanderbilt')."
    )


def _scrape_d1bb_hitting(slug: str) -> list[dict]:
    """Scrape per-player hitting stats from d1baseball.com."""
    resp = requests.get(
        f"{D1BB_BASE}/team/{slug}/{CURRENT_YEAR}/stats/",
        headers=HEADERS,
        timeout=20,
    )
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    for table in soup.find_all("table"):
        thead = table.find("thead")
        if not thead:
            continue
        cols = [th.get_text(strip=True) for th in thead.find_all("th")]
        # Hitting table: must have AB (pitching table also has BA but not AB)
        if "AB" not in cols:
            continue

        rows = []
        for tr in table.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 5:
                continue
            d = dict(zip(cols, cells))
            name = d.get("Player", "").strip()
            if not name or name.lower() in ("totals", "total", "team"):
                continue

            yr  = d.get("Class", "")
            pos = d.get("POS", "")
            ab  = d.get("AB", "0")
            h   = d.get("H", "0")
            dbl = d.get("2B", "0")
            tri = d.get("3B", "0")
            hr  = d.get("HR", "0")
            rbi = d.get("RBI", "0")
            r   = d.get("R", "0")
            bb  = d.get("BB", "0")
            so  = d.get("K", d.get("SO", "0"))
            sb  = d.get("SB", "0")
            hbp = d.get("HBP", "0")
            avg = d.get("BA", "")
            obp = d.get("OBP", "")
            slg = d.get("SLG", "")

            rows.append({
                "Name":       name,
                "#":          "",
                "Position":   pos,
                "Height":     "",
                "Weight":     "",
                "Class":      yr,
                "Grad Year":  class_to_grad_year(yr) if yr else "",
                "Hometown":   "",
                "High School": "",
                "AVG":  avg or calc_avg(h, ab),
                "OBP":  obp or calc_obp(h, bb, hbp, ab, "0"),
                "SLG":  slg or calc_slg(h, dbl, tri, hr, ab),
                "AB":   ab, "H": h, "2B": dbl, "HR": hr,
                "RBI":  rbi, "R": r, "BB": bb, "H_SO": so, "SB": sb,
                "ERA": "", "WHIP": "", "W": "", "L": "", "SV": "",
                "IP": "", "P_H": "", "ER": "", "P_BB": "", "P_SO": "",
                "_has_hitting":  _int(ab) > 0,
                "_has_pitching": False,
                "_pos":          pos,
            })

        if rows:
            return rows

    return []


def _scrape_d1bb_pitching(slug: str) -> list[dict]:
    """Scrape per-player pitching stats from d1baseball.com."""
    resp = requests.get(
        f"{D1BB_BASE}/team/{slug}/{CURRENT_YEAR}/stats/",
        headers=HEADERS,
        timeout=20,
    )
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    for table in soup.find_all("table"):
        thead = table.find("thead")
        if not thead:
            continue
        cols = [th.get_text(strip=True) for th in thead.find_all("th")]
        if "ERA" not in cols or "IP" not in cols:
            continue

        rows = []
        for tr in table.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 5:
                continue
            d = dict(zip(cols, cells))
            name = d.get("Player", "").strip()
            if not name or name.lower() in ("totals", "total", "team"):
                continue

            yr   = d.get("Class", "")
            ip   = d.get("IP", "0")
            era  = d.get("ERA", "")
            w    = d.get("W", "")
            l    = d.get("L", "")
            sv   = d.get("SV", "")
            p_h  = d.get("H", "0")
            er   = d.get("ER", "0")
            p_bb = d.get("BB", "0")
            p_so = d.get("K", d.get("SO", "0"))

            rows.append({
                "Name":       name,
                "#":          "",
                "Position":   "",
                "Height":     "",
                "Weight":     "",
                "Class":      yr,
                "Grad Year":  class_to_grad_year(yr) if yr else "",
                "Hometown":   "",
                "High School": "",
                "ERA":  era or calc_era(er, ip),
                "WHIP": calc_whip(p_bb, p_h, ip),
                "W": w, "L": l, "SV": sv,
                "IP": ip, "P_H": p_h, "ER": er, "P_BB": p_bb, "P_SO": p_so,
                "AVG": "", "OBP": "", "SLG": "", "AB": "", "H": "",
                "2B": "", "HR": "", "RBI": "", "R": "", "BB": "",
                "H_SO": "", "SB": "",
                "_has_hitting":  False,
                "_has_pitching": _parse_ip(ip) > 0,
                "_pos":          "",
            })

        if rows:
            return rows

    return []


# ── NCAA stats.ncaa.org workflow ──────────────────────────────────────────────
# This follows baseballr's open-source NCAA model in Python: use the maintained
# SportsDataverse lookup tables for franchise/season IDs, request the modern
# season-team stat grids, and fall back to a verified browser render only when
# NCAA's Akamai edge blocks the normal HTTP path.


class NcaaError(ValueError):
    """Base class for user-facing NCAA workflow failures."""


class NcaaTeamNotFound(NcaaError):
    """Raised when a school cannot be resolved unambiguously."""


class NcaaAccessError(NcaaError):
    """Raised when NCAA blocks or withholds a requested stats page."""


def _ncaa_cache_dir() -> Path:
    configured = os.environ.get("ROSTER_SCOUT_CACHE_DIR")
    path = Path(configured) if configured else Path(__file__).resolve().parent / ".cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _NCAA_CACHE_LOCK:
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        )
        try:
            with handle:
                handle.write(content)
            os.replace(handle.name, path)
        except Exception:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise


def _cache_fresh(path: Path, ttl_seconds: int) -> bool:
    try:
        return path.is_file() and time.time() - path.stat().st_mtime < ttl_seconds
    except OSError:
        return False


def _read_json_cache(key: str, ttl_seconds: int):
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    path = _ncaa_cache_dir() / f"result-{digest}.json"
    if not _cache_fresh(path, ttl_seconds):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("cache_version") != _NCAA_CACHE_VERSION:
        return None
    return payload


def _write_json_cache(key: str, payload: dict) -> None:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    path = _ncaa_cache_dir() / f"result-{digest}.json"
    body = dict(payload)
    body["cache_version"] = _NCAA_CACHE_VERSION
    body["cached_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write(path, json.dumps(body, separators=(",", ":")))


def _load_csv_rows(url: str, filename: str, required_columns: set[str]) -> list[dict]:
    ttl = int(os.environ.get("NCAA_LOOKUP_CACHE_TTL_SECONDS", "86400"))
    path = _ncaa_cache_dir() / filename
    text = None

    if _cache_fresh(path, ttl):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = None

    if text is None:
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            text = response.text
            header = next(csv.reader(text.splitlines()), [])
            if not required_columns.issubset(set(header)):
                raise NcaaAccessError(f"Unexpected NCAA lookup schema from {url}")
            _atomic_write(path, text)
        except Exception as exc:
            # A stale lookup is safer than failing every request during a brief
            # GitHub outage. It is only used for IDs and is revalidated later.
            if path.is_file():
                text = path.read_text(encoding="utf-8")
            else:
                raise NcaaAccessError(f"Could not load NCAA team metadata: {exc}") from exc

    reader = csv.DictReader(text.splitlines())
    if not required_columns.issubset(set(reader.fieldnames or [])):
        raise NcaaAccessError("Cached NCAA lookup has an unexpected schema.")
    return list(reader)


def _load_ncaa_team_rows() -> list[dict]:
    global _NCAA_TEAM_ROWS
    if _NCAA_TEAM_ROWS is None:
        _NCAA_TEAM_ROWS = _load_csv_rows(
            NCAA_TEAM_LOOKUP_URL,
            "ncaa-team-lookup.csv",
            {"team_id", "team_name", "division", "year", "season_id", "season_team_id"},
        )
    return _NCAA_TEAM_ROWS


def _load_ncaa_season_rows() -> list[dict]:
    global _NCAA_SEASON_ROWS
    if _NCAA_SEASON_ROWS is None:
        _NCAA_SEASON_ROWS = _load_csv_rows(
            NCAA_SEASON_LOOKUP_URL,
            "ncaa-season-lookup.csv",
            {"season", "id", "batting_id", "pitching_id"},
        )
    return _NCAA_SEASON_ROWS


def _current_ncaa_year() -> int:
    override = os.environ.get("NCAA_SEASON_YEAR")
    if override:
        try:
            return int(override)
        except ValueError as exc:
            raise NcaaError("NCAA_SEASON_YEAR must be a four-digit year.") from exc
    now = datetime.now()
    return now.year if now.month >= 2 else now.year - 1


def _parse_division(division) -> int | None:
    if division in (None, "", "ncaa"):
        return None
    value = str(division).lower().strip().replace("ncaa", "").replace("d", "")
    if value not in {"1", "2", "3"}:
        raise NcaaError("NCAA division must be D1, D2, or D3.")
    return int(value)


def _normalize_school_name(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"\b(university|college|the)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _find_ncaa_team(team_name: str, division=None, year: int | None = None) -> dict:
    year = year or _current_ncaa_year()
    division_num = _parse_division(division)
    rows = [
        row for row in _load_ncaa_team_rows()
        if row.get("year") == str(year)
        and row.get("team_id")
        and row.get("season_team_id")
        and (division_num is None or row.get("division") == str(division_num))
    ]
    if not rows:
        suffix = f" in D{division_num}" if division_num else ""
        raise NcaaTeamNotFound(f"No NCAA baseball teams are available for {year}{suffix}.")

    query = _normalize_school_name(team_name)
    exact = [row for row in rows if _normalize_school_name(row["team_name"]) == query]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        divisions = ", ".join(sorted({f"D{row['division']}" for row in exact}))
        raise NcaaTeamNotFound(
            f"'{team_name}' matches more than one NCAA team ({divisions}). Choose a division."
        )

    ranked = [
        (
            SequenceMatcher(None, query, _normalize_school_name(row["team_name"])).ratio(),
            row,
        )
        for row in rows
    ]
    ranked.sort(key=lambda item: item[0])
    score, best = ranked[-1]
    if score < 0.64:
        suffix = f" in D{division_num}" if division_num else ""
        raise NcaaTeamNotFound(
            f"No NCAA baseball team found matching '{team_name}'{suffix}. Try the full school name."
        )
    if len(ranked) > 1 and ranked[-2][0] >= score - 0.025:
        candidates = [ranked[-1][1]["team_name"], ranked[-2][1]["team_name"]]
        raise NcaaTeamNotFound(
            f"'{team_name}' is ambiguous. Try one of: {', '.join(candidates)}."
        )
    return best


def _school_display_name(name: str) -> str:
    """Keep the school's NCAA name without 'university' or 'college'."""
    institution = r"(?:University|Colleges?|Univ\.)"
    name = re.sub(rf"\b{institution}\s+(?:of|at)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(rf"\b{institution}(?=\W|$)", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+([,-])", r"\1", name)
    return re.sub(r"\s+", " ", name).strip(" ,-")


def _ncaa_school_index_by_name() -> dict:
    """Match NCAA.com's index by exact, unique stats display name only."""
    path = Path(__file__).resolve().parent / "data" / "ncaa-schools-index.json"
    try:
        schools = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    matches = {}
    for school in schools:
        name = (school.get("name") or "").strip().casefold()
        slug = school.get("slug") or ""
        long_name = (school.get("long_name") or school.get("name") or "").strip()
        if not name or not long_name or not re.fullmatch(r"[a-z0-9-]+", slug):
            continue
        if name in matches:
            matches[name] = None
        else:
            matches[name] = {"display_name": _school_display_name(long_name), "logo_url": (
                f"https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/{slug}.svg"
            )}
    return matches


def _ncaa_approved_school_aliases(division: str) -> dict:
    """Resolve reviewed stats names against the NCAA index by its slug."""
    data_dir = Path(__file__).resolve().parent / "data"
    try:
        aliases = json.loads((data_dir / "ncaa-school-aliases.json").read_text(encoding="utf-8"))
        schools = json.loads((data_dir / "ncaa-schools-index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    by_slug = {school.get("slug"): school for school in schools}
    result = {}
    for stats_name, slug in aliases.get(division, {}).items():
        school = by_slug.get(slug)
        if not school or not re.fullmatch(r"[a-z0-9-]+", slug):
            continue
        result[stats_name.casefold()] = {
            "display_name": _school_display_name((school.get("long_name") or school.get("name") or "").strip()),
            "logo_url": f"https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/{slug}.svg",
        }
    return result


def list_ncaa_teams(division, year: int | None = None) -> list[dict]:
    """Return the deterministic selector catalog for one NCAA division."""
    year = year or _current_ncaa_year()
    division_num = _parse_division(division)
    if division_num is None:
        raise NcaaError("Choose D1, D2, or D3 to list NCAA teams.")
    school_index = _ncaa_school_index_by_name()
    school_index.update(_ncaa_approved_school_aliases(f"d{division_num}"))
    teams = [
        {
            "name": row["team_name"],
            "team_id": row.get("team_id", ""),
            "season_team_id": row["season_team_id"],
            "division": f"d{division_num}",
            "year": year,
            "display_name": _school_display_name(row["team_name"]),
            **(school_index.get(row["team_name"].strip().casefold()) or {}),
        }
        for row in _load_ncaa_team_rows()
        if row.get("year") == str(year)
        and row.get("division") == str(division_num)
        and row.get("team_name")
        and row.get("season_team_id", "").isdigit()
    ]
    if division_num == 2:
        display_overrides = {
            "AUM": "Auburn-Montgomery", "Cal St. San Marcos": "Cal State-San Marcos",
            "Okla. Baptist": "Oklahoma Baptist", "Georgia College": "Georgia College",
            "Chico St.": "Chico State", "UVA Wise": "UVA Wise", "Wheeling": "Wheeling",
        }
        for team in teams:
            if team["name"] in display_overrides:
                team["display_name"] = display_overrides[team["name"]]
            if team["name"] in {"Wayne St. (MI)", "Wayne St. (NE)"}:
                team["disambiguation"] = "Michigan" if "(MI)" in team["name"] else "Nebraska"
    if division_num == 3:
        display_overrides = {
            "SUNY Brockport": "SUNY Brockport",
            "SUNY Cobleskill": "SUNY Cobleskill",
            "Cortland": "SUNY Cortland",
            "Fredonia": "SUNY Fredonia",
            "Old Westbury": "SUNY Old Westbury",
            "Oswego St.": "SUNY Oswego",
            "Plattsburgh St.": "SUNY Plattsburgh",
            "Purchase": "SUNY Purchase",
            "Sewanee": "Sewanee",
            "TCNJ": "TCNJ",
            "Russell Sage": "Russell Sage",
            "NYU": "NYU",
            "Illinois Col.": "Illinois College",
            "Washington Col.": "Washington College",
            "Penn College": "Penn College",
        }
        for team in teams:
            if team["name"] in display_overrides:
                team["display_name"] = display_overrides[team["name"]]
            if team["name"] in {"North Central (IL)", "North Central (MN)"}:
                team["disambiguation"] = "Illinois" if "(IL)" in team["name"] else "Minnesota"
            if team["name"] == "JWU Charlotte":
                team["display_name"] = "Johnson and Wales"
                team["disambiguation"] = "Charlotte"
                team["logo_url"] = "https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/johnson-wales-ri.svg"
            elif team["name"] == "JWU (Providence)":
                team["disambiguation"] = "Providence"
    approved_logos = {
        ("d2", "Jamestown"): "/static/logos/jamestown.png",
        ("d2", "Point Park"): "/static/logos/point-park.png",
        ("d3", "Regent"): "/static/logos/regent.png",
        ("d3", "SUNY Brockport"): "/static/logos/brockport.svg",
    }
    for team in teams:
        logo = approved_logos.get((team["division"], team["name"]))
        if logo:
            team["logo_url"] = logo
    if division_num == 1:
        display_overrides = {
            "Hawaii": "Hawaii",
            "Columbia": "Columbia",
            "Indiana": "Indiana",
            "Maryland": "Maryland",
            "SIUE": "SIU Edwardsville",
            "UIW": "Incarnate Word",
            "Pacific": "Pacific",
            "Tennessee Tech": "Tennessee Tech",
            "Saint Mary's (CA)": "St. Mary's",
            "St. Thomas (MN)": "St. Thomas",
            "St. John's (NY)": "St. John's",
            "SFA": "Stephen F. Austin",
            "UT Martin": "UT Martin",
            "Navy": "Navy",
            "UT Arlington": "UT Arlington",
            "UTSA": "UTSA",
            "Milwaukee": "Milwaukee",
            "Boston College": "Boston College",
        }
        for team in teams:
            if team["name"] in display_overrides:
                team["display_name"] = display_overrides[team["name"]]
    teams.sort(key=lambda team: team.get("display_name", team["name"]).casefold())
    return teams


def _find_ncaa_team_by_season_id(
    season_team_id: str,
    division=None,
    year: int | None = None,
) -> dict:
    """Resolve an exact current-season NCAA team ID with no fuzzy matching."""
    year = year or _current_ncaa_year()
    division_num = _parse_division(division)
    identifier = str(season_team_id or "").strip()
    if not identifier.isdigit():
        raise NcaaTeamNotFound("Choose a team from the NCAA team selector.")
    matches = [
        row for row in _load_ncaa_team_rows()
        if row.get("year") == str(year)
        and row.get("season_team_id") == identifier
        and (division_num is None or row.get("division") == str(division_num))
    ]
    if len(matches) != 1:
        suffix = f" in D{division_num}" if division_num else ""
        raise NcaaTeamNotFound(
            f"NCAA team ID {identifier} is not available for {year}{suffix}."
        )
    return matches[0]


def validate_ncaa_team_catalog(year: int | None = None) -> dict:
    """Validate the complete current-season selector catalog in one local pass."""
    year = year or _current_ncaa_year()
    rows = [row for row in _load_ncaa_team_rows() if row.get("year") == str(year)]
    season = _find_ncaa_season(year)
    errors = []
    warnings = []
    required = ("team_name", "division", "year", "season_id", "season_team_id")
    valid_rows = []

    for index, row in enumerate(rows, start=2):
        missing = [field for field in required if not str(row.get(field, "")).strip()]
        if missing:
            errors.append({"row": index, "type": "missing_fields", "fields": missing})
            continue
        if row["division"] not in {"1", "2", "3"}:
            errors.append({"row": index, "type": "invalid_division", "value": row["division"]})
            continue
        if not row["season_team_id"].isdigit():
            errors.append({"row": index, "type": "invalid_season_team_id", "value": row["season_team_id"]})
            continue
        if row["season_id"] != season.get("id"):
            errors.append({"row": index, "type": "season_id_mismatch", "value": row["season_id"]})
            continue
        valid_rows.append(row)

    def duplicates(key_func):
        grouped = {}
        for row in valid_rows:
            grouped.setdefault(key_func(row), []).append(row)
        return {key: values for key, values in grouped.items() if len(values) > 1}

    for identifier, duplicate_rows in duplicates(lambda row: row["season_team_id"]).items():
        errors.append({
            "type": "duplicate_season_team_id",
            "value": identifier,
            "teams": [row["team_name"] for row in duplicate_rows],
        })
    for selector_key, duplicate_rows in duplicates(
        lambda row: (row["division"], _normalize_school_name(row["team_name"]))
    ).items():
        errors.append({
            "type": "duplicate_selector_name",
            "value": selector_key[1],
            "division": f"d{selector_key[0]}",
            "teams": [row["team_name"] for row in duplicate_rows],
        })

    missing_stable_ids = [
        row for row in valid_rows
        if not row.get("team_id", "").isdigit()
    ]
    if missing_stable_ids:
        warnings.append({
            "type": "missing_stable_team_id",
            "count": len(missing_stable_ids),
            "note": "These teams remain selectable by their unique season_team_id.",
        })

    counts = {
        f"d{division}": sum(row["division"] == division for row in valid_rows)
        for division in ("1", "2", "3")
    }
    return {
        "valid": not errors,
        "year": year,
        "rows": len(rows),
        "selectable": len(valid_rows),
        "counts": counts,
        "errors": errors,
        "warnings": warnings,
    }


def search_ncaa_team(name: str, division=None, year: int | None = None) -> list[dict]:
    """Return the best NCAA lookup match in the historical public shape."""
    team = _find_ncaa_team(name, division=division, year=year)
    return [{
        "name": team["team_name"],
        "org_id": team["team_id"],
        "season_team_id": team["season_team_id"],
        "division": team["division"],
        "year": team["year"],
    }]


def _find_ncaa_season(year: int) -> dict:
    for row in _load_ncaa_season_rows():
        if row.get("season") == str(year):
            return row
    raise NcaaAccessError(f"NCAA stat category IDs are unavailable for {year}.")


def _is_ncaa_blocked(html: str) -> bool:
    lowered = (html or "").lower()
    return any(marker in lowered for marker in NCAA_BLOCK_MARKERS)


def _has_ncaa_stat_grid(html: str) -> bool:
    if not html or _is_ncaa_blocked(html):
        return False
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one("#stat_grid") is not None


def _request_ncaa_http(url: str, session: requests.Session):
    last_response = None
    timeout = int(os.environ.get("NCAA_HTTP_TIMEOUT_SECONDS", "60"))
    for attempt in range(3):
        try:
            response = session.get(
                url, headers=NCAA_HEADERS, timeout=timeout, allow_redirects=True
            )
        except requests.RequestException as exc:
            raise NcaaAccessError(
                "NCAA request could not reach the configured network path."
            ) from exc
        last_response = response
        if response.status_code not in (429, 503):
            break
        time.sleep(2 ** attempt)
    return last_response


def _accept_ncaa_terms_http(
    session: requests.Session,
    terms_response=None,
) -> None:
    """Accept NCAA terms while preserving Site Unblocker and NCAA sessions."""
    timeout = int(os.environ.get("NCAA_HTTP_TIMEOUT_SECONDS", "60"))
    terms_url = f"{NCAA_BASE}/stats_terms"
    response = terms_response
    form = None
    if response is not None and response.status_code == 200:
        form = BeautifulSoup(response.text, "html.parser").select_one(
            "form[action='/stats_terms']"
        )

    if form is None:
        try:
            response = session.get(
                terms_url,
                headers=NCAA_HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise NcaaAccessError("NCAA terms page could not be reached.") from exc

        if response.status_code != 200:
            raise NcaaAccessError(
                f"NCAA terms page returned HTTP {response.status_code}."
            )

        form = BeautifulSoup(response.text, "html.parser").select_one(
            "form[action='/stats_terms']"
        )
    if form is None:
        raise NcaaAccessError("NCAA returned an unrecognized terms page.")

    required = ("authenticity_token", "nonce", "terms_accepted")
    data = {}
    utf8 = form.select_one("input[name='utf8']")
    if utf8 is not None:
        data["utf8"] = utf8.get("value", "✓")
    for name in required:
        field = form.select_one(f"[name='{name}']")
        value = field.get("value") if field is not None else None
        if not value:
            raise NcaaAccessError(
                f"NCAA terms page did not include {name}."
            )
        data[name] = value

    headers = {
        **NCAA_HEADERS,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": NCAA_BASE,
        "Referer": response.url,
        # Site Unblocker may otherwise replace request headers with its own
        # browser profile. NCAA's CSRF-protected form requires these exact
        # headers and the response cookie from the terms GET.
        "X-SU-Force-Headers": "1",
        "X-SU-Force-Cookies": "1",
    }
    accepted = None
    for attempt in range(3):
        try:
            accepted = session.post(
                urljoin(response.url, form.get("action")),
                data=data,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise NcaaAccessError("NCAA terms acceptance failed.") from exc
        if accepted.status_code != 550:
            break
        time.sleep(2 ** attempt)
    if accepted.status_code not in (200, 302, 303):
        raise NcaaAccessError(
            f"NCAA terms acceptance returned HTTP {accepted.status_code}."
        )


def _fetch_ncaa_http(url: str, session: requests.Session) -> str:
    last_response = _request_ncaa_http(url, session)

    needs_terms_flow = (
        os.environ.get("NCAA_SITE_UNBLOCKER", "0") == "1"
        and not getattr(session, "_ncaa_terms_accepted", False)
        and last_response is not None
        and (
            last_response.status_code == 403
            or _is_ncaa_blocked(last_response.text)
            or (
                last_response.status_code == 200
                and not _has_ncaa_stat_grid(last_response.text)
            )
        )
    )
    if needs_terms_flow:
        _accept_ncaa_terms_http(session, last_response)
        session._ncaa_terms_accepted = True
        last_response = _request_ncaa_http(url, session)

    delay = float(os.environ.get("NCAA_REQUEST_DELAY_SECONDS", "1"))
    if delay > 0:
        time.sleep(delay)

    if last_response is None:
        raise NcaaAccessError("NCAA did not return a response.")
    if last_response.status_code == 403 or _is_ncaa_blocked(last_response.text):
        raise NcaaAccessError("NCAA returned an Akamai verification page.")
    if last_response.status_code != 200:
        raise NcaaAccessError(f"NCAA returned HTTP {last_response.status_code}.")
    if not _has_ncaa_stat_grid(last_response.text):
        raise NcaaAccessError("NCAA returned a page without the expected stats table.")
    return last_response.text


def _playwright_launch_options() -> dict:
    options = {
        "headless": os.environ.get("NCAA_BROWSER_HEADLESS", "1") != "0",
        "args": ["--disable-dev-shm-usage"],
    }
    executable = os.environ.get("NCAA_BROWSER_EXECUTABLE_PATH")
    channel = os.environ.get("NCAA_BROWSER_CHANNEL")
    proxy = os.environ.get("NCAA_BROWSER_PROXY")
    if executable:
        options["executable_path"] = executable
    elif channel:
        options["channel"] = channel
    if proxy:
        options["proxy"] = _playwright_proxy_options(proxy)
        if not _proxy_tls_verification_enabled():
            # Managed unblockers may terminate TLS with their own certificate.
            # This exception is scoped to the dedicated proxied NCAA context.
            options["ignore_https_errors"] = True
            options["args"].append("--ignore-certificate-errors")
    return options


def _proxy_tls_verification_enabled() -> bool:
    return os.environ.get("NCAA_PROXY_TLS_VERIFY", "1") != "0"


def _playwright_proxy_options(proxy_url: str) -> dict:
    """Convert a credentialed proxy URL into Patchright's proxy shape."""
    if "://" not in proxy_url and proxy_url.count(":") >= 3:
        host, port, username, password = proxy_url.split(":", 3)
        if not host or not port or not username or not password:
            raise NcaaAccessError("NCAA_BROWSER_PROXY is not a valid proxy URL.")
        return {
            "server": f"http://{host}:{port}",
            "username": unquote(username),
            "password": unquote(password),
        }

    candidate = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
    parsed = urlparse(candidate)
    if not parsed.hostname:
        raise NcaaAccessError("NCAA_BROWSER_PROXY is not a valid proxy URL.")

    server = f"{parsed.scheme or 'http'}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    options = {"server": server}
    if parsed.username is not None:
        options["username"] = unquote(parsed.username)
    if parsed.password is not None:
        options["password"] = unquote(parsed.password)
    return options


def _requests_proxy_url(proxy_url: str) -> str:
    """Return a requests-compatible URL without logging proxy credentials."""
    options = _playwright_proxy_options(proxy_url)
    username = options.get("username")
    password = options.get("password")
    if username is None and password is None:
        return options["server"]
    return (
        f"{options['server'].split('://', 1)[0]}://"
        f"{quote(username or '', safe='')}:{quote(password or '', safe='')}@"
        f"{options['server'].split('://', 1)[1]}"
    )


def _accept_ncaa_terms(page, target_url: str) -> None:
    """Accept NCAA's stats terms screen and retry the requested stats page."""
    if not page.url.rstrip("/").endswith("/stats_terms"):
        return

    checkbox = page.locator("#terms_accepted")
    button = page.locator("#stats-access-button")
    if checkbox.count() != 1 or button.count() != 1:
        raise NcaaAccessError("NCAA returned an unrecognized terms verification page.")

    # NCAA rejects immediate synthetic submissions. A short dwell before and
    # after the real browser input matches the site's intended interaction and
    # preserves the accepted state in the dedicated browser profile.
    dwell_ms = int(os.environ.get("NCAA_TERMS_DWELL_MILLISECONDS", "8000"))
    page.wait_for_timeout(max(dwell_ms, 0))
    checkbox.check()
    page.wait_for_timeout(2000)
    if button.is_enabled():
        button.click()
    else:
        # Site Unblocker's rendered response can arrive after NCAA's
        # DOMContentLoaded listener would normally enable the button. The form
        # is otherwise complete and the checked field carries NCAA's signed
        # value, so submit that native form directly.
        form = page.locator("form[action='/stats_terms']")
        if form.count() != 1:
            raise NcaaAccessError("NCAA returned an unrecognized terms form.")
        form.evaluate("form => form.submit()")
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    page.goto(target_url, wait_until="domcontentloaded", timeout=60000)


def _fetch_ncaa_browser_pages(urls: dict[str, str]) -> dict[str, str]:
    # A persistent Chromium profile cannot be opened by two processes at once.
    # Gunicorn uses one process for this app, so this lock safely serializes the
    # rare browser fallback while normal HTTP and cache hits remain concurrent.
    with _NCAA_BROWSER_LOCK:
        return _fetch_ncaa_browser_pages_locked(urls)


def _fetch_ncaa_browser_pages_locked(urls: dict[str, str]) -> dict[str, str]:
    try:
        from patchright.sync_api import sync_playwright
    except ImportError as exc:
        raise NcaaAccessError(
            "NCAA requires browser verification, but Patchright is not installed."
        ) from exc

    timeout_seconds = int(os.environ.get("NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS", "45"))
    # A persistent profile retains Akamai cookies and browser-managed state that
    # Playwright's storage_state export does not cover. It also gives a headed
    # local run a durable, one-time human verification path without touching the
    # user's normal Chrome profile.
    configured_profile = os.environ.get("NCAA_BROWSER_PROFILE_DIR")
    profile_path = (
        Path(configured_profile)
        if configured_profile
        else _ncaa_cache_dir() / "ncaa-browser-profile"
    )
    profile_path.mkdir(parents=True, exist_ok=True)
    headless = os.environ.get("NCAA_BROWSER_HEADLESS", "1") != "0"
    rendered = {}

    try:
        with sync_playwright() as playwright:
            context_options = _playwright_launch_options()
            context_options.update({
                "locale": "en-US",
                "timezone_id": "America/Chicago",
                "no_viewport": True,
            })
            context = playwright.chromium.launch_persistent_context(
                str(profile_path), **context_options
            )
            page = context.pages[0] if context.pages else context.new_page()
            preloaded_url = None

            if (
                urls
                and os.environ.get("NCAA_BROWSER_PREFLIGHT_TERMS", "0") == "1"
            ):
                first_url = next(iter(urls.values()))
                page.goto(
                    f"{NCAA_BASE}/stats_terms",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                _accept_ncaa_terms(page, first_url)
                preloaded_url = first_url

            for key, url in urls.items():
                if url != preloaded_url:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                preloaded_url = None
                _accept_ncaa_terms(page, url)
                deadline = time.monotonic() + timeout_seconds
                html = ""
                while time.monotonic() < deadline:
                    try:
                        html = page.content()
                    except Exception:
                        # Akamai uses timed meta/JavaScript redirects. Content
                        # is temporarily unavailable while Chrome commits the
                        # next document, which is a normal verification state.
                        page.wait_for_timeout(1000)
                        continue
                    if _has_ncaa_stat_grid(html):
                        break
                    page.wait_for_timeout(1000)
                if not _has_ncaa_stat_grid(html):
                    if os.environ.get("NCAA_BROWSER_PROXY"):
                        guidance = (
                            "NCAA verification did not complete through the "
                            "configured proxy."
                        )
                    elif headless:
                        guidance = (
                            "Run locally with NCAA_BROWSER_HEADLESS=0 and complete "
                            "the NCAA prompt in the opened browser."
                        )
                    else:
                        guidance = (
                            "NCAA rejected this browser build. On macOS, run "
                            "scripts/run_local.sh so Roster Scout can use installed Chrome."
                        )
                    raise NcaaAccessError(
                        "NCAA browser verification did not produce the expected stats table. "
                        + guidance
                    )
                rendered[key] = html

            context.close()
    except NcaaAccessError:
        raise
    except Exception as exc:
        if os.environ.get("NCAA_BROWSER_PROXY"):
            raise NcaaAccessError(
                "NCAA browser proxy connection failed. Check the proxy endpoint "
                "and credentials."
            ) from exc
        raise NcaaAccessError(f"NCAA browser fallback failed: {exc}") from exc

    return rendered


def _fetch_ncaa_browserless_pages(urls: dict[str, str]) -> dict[str, str]:
    """Fetch NCAA grids inside one Browserless residential browser session."""
    token = os.environ.get("BROWSERLESS_TOKEN")
    if not token:
        raise NcaaAccessError("Browserless is configured without an API token.")
    if not urls:
        return {}

    try:
        from patchright.sync_api import sync_playwright
    except ImportError as exc:
        raise NcaaAccessError(
            "Browserless requires Patchright, but it is not installed."
        ) from exc

    first_url = next(iter(urls.values()))
    endpoint = os.environ.get(
        "BROWSERLESS_UNBLOCK_ENDPOINT",
        "https://production-sfo.browserless.io/chromium/unblock",
    )
    timeout_ms = int(os.environ.get("BROWSERLESS_TIMEOUT_MILLISECONDS", "120000"))
    query = urlencode({
        "token": token,
        "proxy": os.environ.get("BROWSERLESS_PROXY", "residential"),
        "proxyCountry": os.environ.get("BROWSERLESS_PROXY_COUNTRY", "us"),
        "proxySticky": "true",
        "timeout": str(timeout_ms),
        "emulationOs": os.environ.get("BROWSERLESS_EMULATION_OS", "windows"),
    })
    try:
        response = requests.post(
            f"{endpoint}?{query}",
            json={
                "url": first_url,
                "browserWSEndpoint": True,
                "cookies": True,
                "content": True,
                "screenshot": False,
                "ttl": 60000,
            },
            timeout=(timeout_ms / 1000) + 30,
        )
    except requests.RequestException as exc:
        raise NcaaAccessError("Browserless could not start the NCAA session.") from exc
    if response.status_code != 200:
        raise NcaaAccessError(
            f"Browserless session creation returned HTTP {response.status_code}."
        )
    try:
        payload = response.json()
        ws_endpoint = payload["browserWSEndpoint"]
    except (KeyError, TypeError, ValueError) as exc:
        raise NcaaAccessError("Browserless returned an invalid session response.") from exc

    rendered = {}
    browser = None
    playwright = None
    try:
        playwright = sync_playwright().start()
        authenticated_ws = (
            ws_endpoint
            + ("&" if "?" in ws_endpoint else "?")
            + "token="
            + quote(token, safe="")
        )
        browser = playwright.chromium.connect_over_cdp(
            authenticated_ws,
            timeout=30000,
        )
        pages = [
            page
            for context in browser.contexts
            for page in context.pages
        ]
        if not pages:
            raise NcaaAccessError("Browserless returned a session without a page.")
        page = next(
            (candidate for candidate in pages if "stats.ncaa.org" in candidate.url),
            pages[0],
        )

        _accept_ncaa_terms(page, first_url)
        verify_seconds = int(
            os.environ.get("NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS", "45")
        )
        for key, url in urls.items():
            if page.url != url:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            _accept_ncaa_terms(page, url)
            deadline = time.monotonic() + verify_seconds
            html = ""
            while time.monotonic() < deadline:
                html = page.content()
                if _has_ncaa_stat_grid(html):
                    break
                page.wait_for_timeout(1000)
            if not _has_ncaa_stat_grid(html):
                raise NcaaAccessError(
                    "Browserless did not produce the expected NCAA stats table."
                )
            rendered[key] = html
    except NcaaAccessError:
        raise
    except Exception as exc:
        # Do not include Browserless exception text: WebSocket failures can
        # contain the authenticated endpoint.
        raise NcaaAccessError("Browserless NCAA browser session failed.") from exc
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
    return rendered


def _fetch_ncaa_pages(urls: dict[str, str]) -> dict[str, str]:
    if os.environ.get("NCAA_BROWSER_PROVIDER") == "browserless":
        from usage_limits import browserless_slot
        with browserless_slot():
            return _fetch_ncaa_browserless_pages(urls)

    direct = {}
    blocked = {}
    session = requests.Session()
    http_proxy = os.environ.get("NCAA_HTTP_PROXY") or os.environ.get(
        "NCAA_BROWSER_PROXY"
    )
    if http_proxy:
        normalized_proxy = _requests_proxy_url(http_proxy)
        session.proxies.update({"http": normalized_proxy, "https": normalized_proxy})
        session.verify = _proxy_tls_verification_enabled()
    if os.environ.get("NCAA_SITE_UNBLOCKER", "0") == "1":
        session.headers.update({
            "X-SU-Session-Id": uuid.uuid4().hex,
            "X-SU-Force-Cookies": "1",
        })
    for key, url in urls.items():
        try:
            direct[key] = _fetch_ncaa_http(url, session)
        except NcaaAccessError:
            blocked[key] = url

    if blocked:
        if os.environ.get("NCAA_BROWSER_FALLBACK", "1") == "0":
            raise NcaaAccessError(
                "NCAA blocked the direct request and browser fallback is disabled."
            )
        direct.update(_fetch_ncaa_browser_pages(blocked))
    return direct


def _unique_headers(headers: list[str]) -> list[str]:
    seen = {}
    output = []
    for index, header in enumerate(headers):
        header = header.strip() or f"column_{index + 1}"
        count = seen.get(header, 0) + 1
        seen[header] = count
        output.append(header if count == 1 else f"{header}_{count}")
    return output


def _is_ncaa_aggregate_name(name: str) -> bool:
    """Identify NCAA summary rows that appear inside the player table body."""
    normalized = re.sub(r"\s+", " ", str(name or "").strip().lower())
    return bool(re.fullmatch(r"(?:(?:team|opponent)\s+)?totals?", normalized))


def _parse_ncaa_stat_table(html: str) -> list[dict]:
    if not _has_ncaa_stat_grid(html):
        raise NcaaAccessError("NCAA stats table is missing or blocked.")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#stat_grid")
    header_rows = table.select("thead tr")
    header_row = header_rows[-1] if header_rows else table.find("tr")
    headers = _unique_headers([
        cell.get_text(" ", strip=True)
        for cell in header_row.find_all(["th", "td"], recursive=False)
    ])
    rows = []
    body_rows = table.select("tbody tr") or table.find_all("tr")[1:]
    for tr in body_rows:
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) != len(headers):
            continue
        values = [cell.get_text(" ", strip=True) for cell in cells]
        row = dict(zip(headers, values))
        player_key = next((key for key in row if key.lower() == "player"), None)
        if not player_key:
            continue
        name = re.sub(r"^x\s+", "", row[player_key], flags=re.IGNORECASE).strip()
        if not name or name.lower() == "team" or _is_ncaa_aggregate_name(name):
            continue
        row[player_key] = name
        player_link = tr.find("a", href=re.compile(r"/players/\d+"))
        if player_link:
            match = re.search(r"/players/(\d+)", player_link.get("href", ""))
            row["_player_id"] = match.group(1) if match else ""
        else:
            row["_player_id"] = ""
        rows.append(row)
    return rows


def _stat(row: dict, *keys: str) -> str:
    lowered = {key.lower(): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, "", "-"):
            return str(value).strip()
    return ""


def _rate(value: str) -> str:
    if not value:
        return ""
    try:
        return _fmt(float(value))
    except (TypeError, ValueError):
        return value


def _normalize_ncaa_players(batting: list[dict], pitching: list[dict]) -> list[dict]:
    def player_key(row: dict) -> str:
        player_id = row.get("_player_id")
        if player_id:
            return f"id:{player_id}"
        return f"name:{_normalize_school_name(_stat(row, 'Player'))}"

    bat_by_key = {player_key(row): row for row in batting}
    pit_by_key = {player_key(row): row for row in pitching}
    keys = list(bat_by_key) + [key for key in pit_by_key if key not in bat_by_key]
    players = []

    for key in keys:
        bat = bat_by_key.get(key, {})
        pit = pit_by_key.get(key, {})
        common = bat or pit
        name = _stat(common, "Player") or _stat(pit, "Player")
        jersey = _stat(common, "Jersey", "#") or _stat(pit, "Jersey", "#")
        position = _stat(common, "Pos", "Position") or _stat(pit, "Pos", "Position")
        year = _stat(common, "Yr", "Class") or _stat(pit, "Yr", "Class")
        ab = _stat(bat, "AB")
        hits = _stat(bat, "H")
        doubles = _stat(bat, "2B")
        triples = _stat(bat, "3B")
        home_runs = _stat(bat, "HR")
        walks = _stat(bat, "BB")
        hbp = _stat(bat, "HBP")
        sf = _stat(bat, "SF")
        ip = _stat(pit, "IP")
        pitching_hits = _stat(pit, "H")
        earned_runs = _stat(pit, "ER")
        pitching_walks = _stat(pit, "BB")

        players.append({
            "Name": name,
            "#": jersey,
            "Position": position,
            "Height": _stat(common, "Ht", "Height") or _stat(pit, "Ht", "Height"),
            "Weight": "",
            "Class": year,
            "Grad Year": class_to_grad_year(year) if year else "",
            "Hometown": "",
            "High School": "",
            "AVG": _rate(_stat(bat, "BA", "AVG")) or calc_avg(hits, ab),
            "OBP": _rate(_stat(bat, "OBPct", "OBP")) or calc_obp(hits, walks, hbp, ab, sf),
            "SLG": _rate(_stat(bat, "SlgPct", "SLG")) or calc_slg(hits, doubles, triples, home_runs, ab),
            "AB": ab,
            "H": hits,
            "2B": doubles,
            "HR": home_runs,
            "RBI": _stat(bat, "RBI"),
            "R": _stat(bat, "R"),
            "BB": walks,
            "H_SO": _stat(bat, "K", "SO"),
            "SB": _stat(bat, "SB"),
            "ERA": _stat(pit, "ERA") or calc_era(earned_runs, ip),
            "WHIP": _stat(pit, "WHIP") or calc_whip(pitching_walks, pitching_hits, ip),
            "W": _stat(pit, "W"),
            "L": _stat(pit, "L"),
            "SV": _stat(pit, "SV"),
            "IP": ip,
            "P_H": pitching_hits,
            "ER": earned_runs,
            "P_BB": pitching_walks,
            "P_SO": _stat(pit, "SO", "K"),
            "_has_hitting": bool(bat),
            "_has_pitching": bool(pit),
            "_batting_activity": any(_int(_stat(bat, key)) > 0 for key in (
                "AB", "H", "R", "RBI", "BB", "HBP", "SF", "SH", "SB", "K", "SO",
            )),
            "_pos": position,
        })
    return players


def _scrape_ncaa_team(team: dict, year: int, force_refresh: bool = False) -> tuple:
    """Scrape one already-resolved NCAA catalog row."""
    label = team["team_name"]
    season_team_id = team["season_team_id"]
    cache_key = f"ncaa:{year}:{season_team_id}"
    ttl = int(os.environ.get("NCAA_RESULT_CACHE_TTL_SECONDS", "21600"))

    if not force_refresh:
        cached = _read_json_cache(cache_key, ttl)
        if cached and cached.get("players"):
            cached_players = [
                player for player in cached["players"]
                if not _is_ncaa_aggregate_name(player.get("Name", ""))
            ]
            if cached_players:
                if len(cached_players) != len(cached["players"]):
                    _write_json_cache(cache_key, {**cached, "players": cached_players})
                return cached_players, cached.get("team_label", label)

    season = _find_ncaa_season(year)
    base_url = f"{NCAA_BASE}/teams/{season_team_id}/season_to_date_stats"
    urls = {
        "batting": f"{base_url}?year_stat_category_id={season['batting_id']}",
        "pitching": f"{base_url}?year_stat_category_id={season['pitching_id']}",
    }
    pages = _fetch_ncaa_pages(urls)
    batting = _parse_ncaa_stat_table(pages["batting"])
    pitching = _parse_ncaa_stat_table(pages["pitching"])
    if not batting and not pitching:
        raise NcaaAccessError(
            f"Found '{label}' but NCAA has no batting or pitching rows for {year}."
        )

    players = _normalize_ncaa_players(batting, pitching)
    if not players:
        raise NcaaAccessError(f"NCAA returned stats for '{label}', but no players could be parsed.")

    _write_json_cache(cache_key, {
        "team_label": label,
        "team_id": team["team_id"],
        "season_team_id": season_team_id,
        "division": team["division"],
        "year": year,
        "players": players,
    })
    return players, label


def scrape_ncaa_by_name(
    team_name: str,
    division=None,
    year: int | None = None,
    force_refresh: bool = False,
) -> tuple:
    """Resolve a name and scrape NCAA D1/D2/D3 stats (compatibility path)."""
    year = year or _current_ncaa_year()
    team = _find_ncaa_team(team_name, division=division, year=year)
    return _scrape_ncaa_team(team, year, force_refresh=force_refresh)


def scrape_ncaa_by_id(
    season_team_id: str,
    division=None,
    year: int | None = None,
    force_refresh: bool = False,
) -> tuple:
    """Scrape an exact selector ID without invoking fuzzy name matching."""
    year = year or _current_ncaa_year()
    team = _find_ncaa_team_by_season_id(
        season_team_id,
        division=division,
        year=year,
    )
    return _scrape_ncaa_team(team, year, force_refresh=force_refresh)


def scrape_by_team_name(team_name: str) -> tuple:
    """
    Primary entry point: look up D1 team by name, scrape current season stats
    from d1baseball.com. Returns (players, team_label).
    """
    slug, label = _find_d1bb_slug(team_name)

    # Fetch hitting and pitching concurrently (same page, but two parse passes)
    with ThreadPoolExecutor(max_workers=2) as pool:
        bat_fut = pool.submit(_scrape_d1bb_hitting, slug)
        pit_fut = pool.submit(_scrape_d1bb_pitching, slug)
        bat_rows = bat_fut.result()
        pit_rows = pit_fut.result()

    if not bat_rows and not pit_rows:
        raise ValueError(
            f"Found '{label}' but no current-season stats are posted yet on d1baseball.com. "
            "Stats typically appear after the first week of games."
        )

    # Merge by player name (handles two-way players appearing in both tables)
    bat_by_name = {p["Name"].lower(): p for p in bat_rows}
    pit_by_name = {p["Name"].lower(): p for p in pit_rows}
    all_names   = list(bat_by_name) + [n for n in pit_by_name if n not in bat_by_name]

    players = []
    for norm in all_names:
        b = bat_by_name.get(norm, {})
        p = pit_by_name.get(norm, {})
        players.append({
            "Name":       b.get("Name") or p.get("Name", norm),
            "#":          "",
            "Position":   b.get("Position") or p.get("Position", ""),
            "Height":     "",
            "Weight":     "",
            "Class":      b.get("Class") or p.get("Class", ""),
            "Grad Year":  b.get("Grad Year") or p.get("Grad Year", ""),
            "Hometown":   "",
            "High School": "",
            # Hitting
            "AVG": b.get("AVG", ""), "OBP": b.get("OBP", ""),
            "SLG": b.get("SLG", ""), "AB":  b.get("AB", ""),
            "H":   b.get("H", ""),   "2B":  b.get("2B", ""),
            "HR":  b.get("HR", ""),  "RBI": b.get("RBI", ""),
            "R":   b.get("R", ""),   "BB":  b.get("BB", ""),
            "H_SO": b.get("H_SO", ""), "SB": b.get("SB", ""),
            # Pitching
            "ERA":  p.get("ERA", ""), "WHIP": p.get("WHIP", ""),
            "W":    p.get("W", ""),   "L":    p.get("L", ""),
            "SV":   p.get("SV", ""),  "IP":   p.get("IP", ""),
            "P_H":  p.get("P_H", ""), "ER":   p.get("ER", ""),
            "P_BB": p.get("P_BB", ""), "P_SO": p.get("P_SO", ""),
            "_has_hitting":  bool(b),
            "_has_pitching": bool(p),
            "_pos":          b.get("_pos") or p.get("_pos", ""),
        })

    return players, label


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_roster(url: str) -> list[dict]:
    """
    Scrape roster. Detects Sidearm Sports and uses its internal API for
    full bio + stats. Falls back to HTML table parsing for other platforms.
    Returns fully-populated player dicts (stats already included).
    """
    # Try Sidearm first (most NCAA schools)
    players = scrape_sidearm(url)
    if players:
        return players

    # Generic HTML fallback
    return _scrape_html_roster(url)


def scrape_stats(url: str) -> dict:
    """
    For Sidearm sites, stats are already embedded in scrape_roster() results.
    This returns empty for Sidearm; merge() handles that gracefully.
    Kept for non-Sidearm HTML table sites.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return {}

    for table in soup.find_all("table"):
        text = table.get_text().lower()
        if "avg" in text and ("rbi" in text or "ab" in text):
            thead = table.find("thead")
            if not thead:
                continue
            cols = [th.get_text(strip=True).lower() for th in thead.find_all("th")]
            stats = {}
            for row in table.select("tbody tr"):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) < 3:
                    continue
                d = dict(zip(cols, cells))
                name_key = next((k for k in d if "name" in k or "player" in k), cols[0] if cols else None)
                if not name_key:
                    continue
                name = d.get(name_key, "").strip()
                if name and name.lower() not in ("totals", "total", "team"):
                    stats[name] = d
            if stats:
                return stats
    return {}


def merge(roster: list[dict], stats: dict) -> list[dict]:
    """
    For Sidearm rosters, stats are already populated — pass through as-is.
    For HTML rosters, attempt to merge from the stats dict.
    """
    if not stats:
        # Sidearm path: roster already has stats
        return roster

    # HTML fallback path: try to inject stats by name match
    def _norm(s):
        return re.sub(r"\s+", " ", s.lower().strip())

    ALIASES = {
        "AVG": ["avg", "ba"], "OBP": ["obp", "ob%"], "SLG": ["slg", "slg%"],
        "H": ["h", "hits"], "2B": ["2b"], "HR": ["hr"], "RBI": ["rbi"],
        "AB": ["ab"], "R": ["r", "runs"], "BB": ["bb"], "SO": ["so"], "SB": ["sb"],
    }

    def _find(d, aliases):
        for k in d:
            if k in aliases:
                return d[k]
        return ""

    merged = []
    for p in roster:
        norm = _norm(p["Name"])
        s = {}
        for sname, sdata in stats.items():
            if _norm(sname) == norm:
                s = sdata
                break
        if not s:
            last = norm.split()[-1] if norm.split() else ""
            for sname, sdata in stats.items():
                if last and last in _norm(sname).split():
                    s = sdata
                    break
        row = dict(p)
        for col, aliases in ALIASES.items():
            if not row.get(col):
                row[col] = _find(s, aliases)
        merged.append(row)

    return merged


# ── Hitter / Pitcher split ────────────────────────────────────────────────────

# Columns to include in each sheet (bio fields shared, stats differ)
HITTER_COLS = [
    "Name", "#", "Position", "Class",
    "AVG", "OBP", "SLG", "AB", "H", "2B", "HR", "RBI", "R", "BB", "H_SO", "SB",
]
PITCHER_COLS = [
    "Name", "#", "Position", "Class",
    "ERA", "WHIP", "W", "L", "SV", "IP", "P_H", "ER", "P_BB", "P_SO",
]
# Friendly display names for the header row
HITTER_HEADERS  = HITTER_COLS[:4]  + ["AVG","OBP","SLG","AB","H","2B","HR","RBI","R","BB","SO","SB"]
PITCHER_HEADERS = PITCHER_COLS[:4] + ["ERA","WHIP","W","L","SV","IP","H","ER","BB","SO"]


def split_roster(players: list[dict]) -> tuple:
    """
    Split a roster into (hitters, pitchers) lists.

    Rules:
    - Pitcher position (RHP/LHP/P/etc.) → pitchers sheet
    - Hitter position (C/INF/OF/etc.)   → hitters sheet
    - Has pitching stats (IP > 0)       → pitchers sheet (regardless of position)
    - Pitchers with batting activity or a hitter position → both sheets
    - Unclassified players without pitching evidence → hitters sheet
    """
    hitters  = []
    pitchers = []

    for p in players:
        pos          = p.get("_pos", p.get("Position", ""))
        has_hitting  = p.get("_has_hitting",  bool(p.get("AVG") or p.get("H")))
        has_pitching = p.get("_has_pitching", bool(p.get("ERA") or p.get("IP")))
        is_p_pos     = _is_pitcher_pos(pos)
        is_h_pos     = _is_hitter_pos(pos)

        # NCAA batting tables include zero-stat rows for pitchers.  Cached
        # results can retain the old row-presence flag, so check the actual
        # batting activity whenever the player is also classified as a pitcher.
        if is_p_pos or has_pitching:
            has_hitting = p.get("_batting_activity", False) or any(_int(p.get(key)) > 0 for key in (
                "AB", "H", "2B", "HR", "RBI", "R", "BB", "H_SO", "SB",
            ))

        # A position player with pitching stats → both
        # A pitcher with batting stats → both
        # No position info → decide by stats; if neither, use hitters as default
        goes_hitting  = is_h_pos or has_hitting or (not is_p_pos and not has_pitching)
        goes_pitching = is_p_pos or has_pitching

        # Build clean output rows (drop internal flags)
        def _row(cols):
            return {h: p.get(k, "") for k, h in zip(cols, cols)}

        if goes_hitting:
            row = {h: p.get(k, "") for k, h in zip(HITTER_COLS, HITTER_HEADERS)}
            hitters.append(row)
        if goes_pitching:
            row = {h: p.get(k, "") for k, h in zip(PITCHER_COLS, PITCHER_HEADERS)}
            pitchers.append(row)

    hitters.sort(key=lambda p: _int(p.get("AB") or 0), reverse=True)
    pitchers.sort(key=lambda p: _parse_ip(p.get("IP") or 0), reverse=True)
    return hitters, pitchers
