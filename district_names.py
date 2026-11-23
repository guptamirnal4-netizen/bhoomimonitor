"""
district_names.py
--------------------
Bridges the live Bhoomi portal's district dropdown text to schema.py's
curated DISTRICTS vocabulary (used by the existing MonitorLogger/
dashboard).

Confirmed live (Sec. 16 shakedown): the portal's district dropdown has
32 entries with inconsistent casing ("BELAGAVI", "Bagalkote", "Bidar",
...); schema.py's DISTRICTS has 31, Title-Case, curated independently.
The two will not always line up 1:1 - Karnataka's official district
list has changed over time (Vijayanagara split off from Ballari in
2021), and the portal's own list doesn't necessarily track that.

A district with no mapping is NOT a crawl failure - it just means that
district's activity won't appear in the fetch_events dashboard until
schema.py's DISTRICTS is deliberately extended. The actual data
pipeline (land_schema.py/persistence.py) never depends on this mapping;
only the observability wrapper in crawler.py does.
"""

from schema import DISTRICTS

_LOOKUP = {d.upper(): d for d in DISTRICTS}


def normalize_for_dashboard(raw_district_name: str) -> str | None:
    """Case-insensitive lookup of `raw_district_name` (as it appears in
    the live portal's dropdown) against schema.py's canonical spelling.
    Returns None if unmapped - callers must treat that as "skip
    dashboard tracking for this district", never as a reason to abort
    the crawl."""
    return _LOOKUP.get((raw_district_name or "").strip().upper())
