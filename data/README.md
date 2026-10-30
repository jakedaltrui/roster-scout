# NCAA school index

`ncaa-schools-index.json` is a snapshot of `https://www.ncaa.com/json/schools`,
the data source for NCAA.com's schools index, retrieved October 29, 2026.
It contains the index's short name, full name where present, and logo slug. Selector entries
are enriched only when their stats-catalog name matches one unique index name;
unmatched entries retain their original stats name and logo fallback.

`ncaa-school-aliases.json` contains reviewed matches between stats names and
NCAA index slugs. Display names omit "University" and "College";
the D1 Hawaii and Columbia entries use those names directly.
