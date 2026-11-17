# NCAA school index

`ncaa-schools-index.json` is a snapshot of `https://www.ncaa.com/json/schools`,
the data source for NCAA.com's schools index, retrieved October 29, 2026.
It contains the index's short name, full name where present, and logo slug. Selector entries
are enriched only when their stats-catalog name matches one unique index name;
unmatched entries retain their original stats name and logo fallback.

`ncaa-school-aliases.json` contains reviewed matches between stats names and
NCAA index slugs. Display names omit "University" and "College";
the D1 Hawaii and Columbia entries use those names directly.

`institution-domain-map.json` is the deterministic school personalization
registry for the 2026 baseball catalog. Stable NCAA organization IDs join the
selector to the official NCAA Membership Directory; four directory omissions
use their official institution homepages. A root shared by multiple baseball
programs is listed under `ambiguous_domains`, never personalized, and routed by
the exact verified email domain to avoid cross-campus report access. Rebuild it
with `uv run python scripts/build_institution_domain_map.py`.

`ncaa-bbstats-batting-weights-2026.csv` pins the Division I, II, and III 2026
college-calibrated wOBA rows published by `ncaa_bbStats` 1.4.2. The complete
source CSV SHA-256 is
`eb7e4af63cb60455904b6377b361e50f57628b779d52e53ff86fdab26e86e6bb`;
the runtime stores that checksum, source URL, package version, exact season,
and division with every verified constant set. The upstream methodology fits
NCAA team counting statistics by weighted least squares, shrinks season values
toward the division pool, and enforces ordered hit values. `ncaa_bbStats` is
distributed under the MIT License.

`ncaa-bbstats-pitching-constants-2026.csv` pins the matching 2026 Division I,
II, and III FIP additive constants. Its complete upstream CSV SHA-256 is
`ef3dfcbcd9e7963c6a68740b6965738608fc3d5488bd489bfdc1e0c212e1cc7a`.
The package derives each constant from the exact division-season league totals
for ERA, innings, home runs, walks, hit batters, and strikeouts.
