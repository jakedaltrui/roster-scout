#!/usr/bin/env python3
"""
Baseball Roster & Stats Scraper
Scrapes NCAA college baseball team data and exports to Google Sheets.

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
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── Constants ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

NCAA_BASE = "https://stats.ncaa.org"

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

    bio_fields = {
        "Name":        f"{bio.get('first_name', '')} {bio.get('last_name', '')}".strip(),
        "#":           bio.get("jersey_number", ""),
        "Position":    bio.get("position", ""),
        "Height":      bio.get("height", ""),
        "Weight":      bio.get("weight", ""),
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


# ── Legacy NCAA stubs (kept for reference; stats.ncaa.org is WAF-blocked) ─────

def search_ncaa_team(name: str) -> list[dict]:
    """
    Stub — stats.ncaa.org blocks programmatic access.
    Use scrape_by_team_name() which routes to d1baseball.com instead.
    """
    resp = requests.get(
        f"{NCAA_BASE}/teams/search",
        params={"q": name, "sport_code": "MBA"},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    teams = []
    seen_ids = set()
    for a in soup.find_all("a", href=re.compile(r"/team/\d+")):
        href = a.get("href", "")
        m = re.search(r"/team/(\d+)", href)
        if not m:
            continue
        org_id = m.group(1)
        if org_id in seen_ids:
            continue
        seen_ids.add(org_id)
        label = a.get_text(strip=True)
        if label and not label.isdigit():
            teams.append({"name": label, "org_id": org_id})

    return teams


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
    "Name", "#", "Position", "Height", "Weight", "Class",
    "AVG", "OBP", "SLG", "AB", "H", "2B", "HR", "RBI", "R", "BB", "H_SO", "SB",
]
PITCHER_COLS = [
    "Name", "#", "Position", "Height", "Weight", "Class",
    "ERA", "WHIP", "W", "L", "SV", "IP", "P_H", "ER", "P_BB", "P_SO",
]
# Friendly display names for the header row
HITTER_HEADERS  = HITTER_COLS[:6]  + ["AVG","OBP","SLG","AB","H","2B","HR","RBI","R","BB","SO","SB"]
PITCHER_HEADERS = PITCHER_COLS[:6] + ["ERA","WHIP","W","L","SV","IP","H","ER","BB","SO"]


def split_roster(players: list[dict]) -> tuple:
    """
    Split a roster into (hitters, pitchers) lists.

    Rules:
    - Pitcher position (RHP/LHP/P/etc.) → pitchers sheet
    - Hitter position (C/INF/OF/etc.)   → hitters sheet
    - Has pitching stats (IP > 0)       → pitchers sheet (regardless of position)
    - Has hitting stats (AB > 0)        → hitters sheet (regardless of position)
    - Two-way / ambiguous               → both sheets
    """
    hitters  = []
    pitchers = []

    for p in players:
        pos          = p.get("_pos", p.get("Position", ""))
        has_hitting  = p.get("_has_hitting",  bool(p.get("AVG") or p.get("H")))
        has_pitching = p.get("_has_pitching", bool(p.get("ERA") or p.get("IP")))
        is_p_pos     = _is_pitcher_pos(pos)
        is_h_pos     = _is_hitter_pos(pos)

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
