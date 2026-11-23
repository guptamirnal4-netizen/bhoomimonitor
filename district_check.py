"""
district_check.py
--------------------
The lightweight, per-district reachability check the 31-district
monitor runs every 30 minutes: select the district on the Service2/ RTC
form and confirm its taluk list populates. This is deliberately NOT a
full RTC record fetch (rtc_verifier.py/rtc_crawler.py already do that,
used by health_monitor.py for the 2 districts with a validated survey/
hissa) - it needs no known-good land record, so it can run for all 31
districts without per-district discovery work first, at a fraction of
the request cost (1 GET + 1 POST vs. a 5-6 step full RTC fetch).

What "up" actually means here: the portal responded to a district
selection with a non-empty taluk list - i.e. "Bhoomi's data for this
district's administrative hierarchy is being served right now". It is
NOT evidence about any specific survey/parcel's data - that distinction
is preserved throughout (service="RTC-Reachability", check_performed
field), never conflated with the deeper per-record checks.

CHECK_STATUS decision rule (Sec. "these must never be treated as the
same thing"):
  - Timeout / connection failure / HTTP 5xx talking to the portal =
    real evidence the portal is unreachable -> "down" (not check_error;
    matches health_monitor.py's existing treatment of the same
    exceptions for the deeper checks).
  - District selected successfully but its taluk list comes back EMPTY
    = an anomaly (every real district has taluks) -> "down".
  - An unhandled exception in OUR OWN code (a bug, a district_registry
    mapping pointing at a value the live dropdown no longer has, etc.)
    -> "check_error" - this is NOT evidence the portal is down, it's
    evidence something on our side needs fixing, and reporting it as
    "down" would misattribute the fault.
"""

import logging

from bhoomi_session import BhoomiSession, BhoomiRequestFailed, BhoomiSessionExpired
from district_registry import portal_text

log = logging.getLogger("bhoomi.district_check")

BASE_URL = "https://landrecords.karnataka.gov.in/Service2/"
DISTRICT_SELECT_ID = "ctl00_MainContent_ddlCDistrict"
DISTRICT_SELECT_FIELD = "ctl00$MainContent$ddlCDistrict"
TALUK_SELECT_ID = "ctl00_MainContent_ddlCTaluk"

PLACEHOLDER_VALUES = {"", "0", "Select District", "Select Taluk"}


def _options(soup, select_id):
    return [
        (v, t) for v, t in BhoomiSession.select_options(soup, select_id)
        if v not in PLACEHOLDER_VALUES and t not in PLACEHOLDER_VALUES
    ]


def check_district_reachability(canonical_district: str, session: BhoomiSession | None = None) -> dict:
    """Runs the lightweight check for one district. Never raises for a
    portal-side problem (returns status="down" instead) - only a
    genuine bug in this function's own logic surfaces as status=
    "check_error". Returns:
        {"status": "up"|"down"|"check_error", "district_code": str|None,
         "taluk_count": int, "reason": str|None, "error_message": str|None,
         "latency_ms": int}
    """
    import time
    own_session = session is None
    if own_session:
        session = BhoomiSession(BASE_URL)
    started = time.monotonic()

    try:
        target_text = portal_text(canonical_district)
    except KeyError as exc:
        # Our own registry doesn't know this district - a bug on our
        # side, not evidence the portal is down.
        return {"status": "check_error", "district_code": None, "taluk_count": 0,
                "reason": None, "error_message": f"No district_registry mapping: {exc}",
                "latency_ms": int((time.monotonic() - started) * 1000)}

    try:
        soup = session.get_page("")  # Service2/'s form posts back to "./" - same as rtc_crawler.PATH
        options = _options(soup, DISTRICT_SELECT_ID)
        match = next(((v, t) for v, t in options if t.strip().lower() == target_text.strip().lower()), None)
        if match is None:
            return {
                "status": "check_error", "district_code": None, "taluk_count": 0,
                "reason": None,
                "error_message": f"{target_text!r} not found in live district dropdown "
                                  f"(district_registry.py mapping may be stale)",
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        district_value, _ = match

        new_soup, _ = session.postback("", soup, {DISTRICT_SELECT_FIELD: district_value},
                                        event_target=DISTRICT_SELECT_FIELD)
        taluks = _options(new_soup, TALUK_SELECT_ID)
        latency_ms = int((time.monotonic() - started) * 1000)

        if not taluks:
            return {
                "status": "down", "district_code": district_value, "taluk_count": 0,
                "reason": "District selected but its taluk list came back empty - "
                          "every real district should have at least one.",
                "error_message": None, "latency_ms": latency_ms,
            }
        return {
            "status": "up", "district_code": district_value, "taluk_count": len(taluks),
            "reason": None, "error_message": None, "latency_ms": latency_ms,
        }

    except BhoomiSessionExpired as exc:
        return {"status": "down", "district_code": None, "taluk_count": 0,
                "reason": "Session expired mid-check", "error_message": str(exc),
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except BhoomiRequestFailed as exc:
        return {"status": "down", "district_code": None, "taluk_count": 0,
                "reason": "Request to the portal failed after retries (timeout/connection/5xx)",
                "error_message": str(exc), "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 - a genuine bug in this function, not portal evidence
        log.exception("[DISTRICT_CHECK] unexpected error checking %s", canonical_district)
        return {"status": "check_error", "district_code": None, "taluk_count": 0,
                "reason": None, "error_message": f"Unexpected error in check logic: {exc}",
                "latency_ms": int((time.monotonic() - started) * 1000)}
